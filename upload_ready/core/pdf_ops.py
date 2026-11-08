"""PDF operations: Images to PDF, PDF to Images, and PDF Size Reduction.

Includes password handling (local memory only, never persisted), signature safety warnings,
page range parsing, resource limits on rendered dimensions, cooperative cancellation,
multi-frame protection on input images, staged output writes, and honest reporting
distinguishing structural reduction from scanned page downsampling.
"""

import io
import math
import os
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from PIL import Image, ImageOps
import pypdf
import pypdfium2

from upload_ready.core.constants import (
    DEFAULT_MAX_CUMULATIVE_PIXELS,
    MAX_DECODED_PIXELS,
    MAX_PDF_PAGES,
    PDF_PAGE_SIZES,
    bytes_to_human,
)
from upload_ready.core.image_convert import flatten_alpha_on_background, prepare_color_profile_and_mode
from upload_ready.core.models import (
    CancellationToken,
    ImagesToPdfOptions,
    JobResult,
    OperationCancelledError,
    OperationType,
    PageOrientation,
    PdfReduceOptions,
    PdfToImagesOptions,
    RequirementCheck,
)
from upload_ready.core.safety import (
    MultiFileJobRollback,
    ResourceLimitExceededError,
    StagedOutput,
    TempJobWorkspace,
    check_single_frame_image,
    resolve_collision_path,
    sanitize_filename,
    sniff_content_type,
    validate_cumulative_image_dimensions,
    validate_cumulative_pdf_pages_dimensions,
    validate_destination_safety,
    validate_dimensions,
    validate_dpi,
    validate_file_size,
    validate_pdf_page_count,
)


class PdfPasswordRequiredError(Exception):
    """Raised when an encrypted PDF requires a decryption password."""
    pass


class InvalidPdfPasswordError(Exception):
    """Raised when the provided decryption password fails."""
    pass


def parse_page_range(range_str: str, total_pages: int) -> List[int]:
    """
    Parse page range string (e.g. 'all', '1, 3-5, 8') into sorted unique 0-indexed page indices.
    """
    clean = range_str.strip().lower()
    if not clean or clean == "all":
        return list(range(total_pages))

    pages = set()
    parts = clean.split(",")
    for part in parts:
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            sub = item.split("-")
            if len(sub) != 2:
                raise ValueError(f"Invalid page range token: '{item}'")
            start = int(sub[0].strip())
            end = int(sub[1].strip())
            if start < 1 or end < start:
                raise ValueError(f"Invalid range bounds: '{item}'")
            for p in range(start, min(end + 1, total_pages + 1)):
                pages.add(p - 1)
        else:
            p = int(item)
            if 1 <= p <= total_pages:
                pages.add(p - 1)
            else:
                raise ValueError(f"Page number {p} is outside document page count ({total_pages}).")

    result = sorted(list(pages))
    if not result:
        raise ValueError("Page range selection resulted in 0 pages.")
    return result


def detect_pdf_signatures(reader: pypdf.PdfReader) -> Tuple[bool, bool]:
    """
    Inspect PDF for digital signatures and distinguish signed documents from empty signature fields.
    Returns (is_signed, has_empty_signature_field).
    """
    is_signed = False
    has_empty = False
    try:
        # 1. Check SigFlags in Document Catalog (Bit 1 = SignaturesExist)
        root = reader.trailer.get("/Root")
        if root:
            root_dict = root.get_object() if hasattr(root, "get_object") else root
            if isinstance(root_dict, dict):
                sig_flags = root_dict.get("/SigFlags")
                if sig_flags and int(sig_flags) & 1:
                    is_signed = True

        # 2. Check interactive form fields (including nested trees via get_fields)
        fields = reader.get_fields()
        if fields:
            for name, f_dict in fields.items():
                fd = f_dict.get_object() if hasattr(f_dict, "get_object") else f_dict
                if isinstance(fd, dict):
                    if fd.get("/FT") == "/Sig":
                        v = fd.get("/V")
                        if v:
                            v_obj = v.get_object() if hasattr(v, "get_object") else v
                            if isinstance(v_obj, dict) and ("/ByteRange" in v_obj or "/Contents" in v_obj):
                                is_signed = True
                            elif "/ByteRange" in fd:
                                is_signed = True
                        else:
                            has_empty = True
                    elif "/ByteRange" in fd:
                        is_signed = True

        # 3. Check page annotations for signature widgets
        for page in reader.pages:
            annots = page.get("/Annots")
            if annots:
                annots_obj = annots.get_object() if hasattr(annots, "get_object") else annots
                if isinstance(annots_obj, list):
                    for a in annots_obj:
                        obj = a.get_object() if hasattr(a, "get_object") else a
                        if isinstance(obj, dict):
                            if obj.get("/Subtype") == "/Widget" and obj.get("/FT") == "/Sig":
                                v = obj.get("/V")
                                if v:
                                    is_signed = True
                                else:
                                    has_empty = True
    except Exception:
        pass
    return is_signed, has_empty


def check_pdf_has_signatures(reader: pypdf.PdfReader) -> bool:
    """Detect if the PDF contains a signed digital signature."""
    is_signed, _ = detect_pdf_signatures(reader)
    return is_signed


def images_to_pdf(
    input_paths: List[Path],
    output_path: Path,
    options: ImagesToPdfOptions,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
) -> JobResult:
    """
    Combine an ordered list of images into a single PDF document.
    Rejects multi-frame inputs to prevent silent frame loss.
    Stages output to protect original files.
    """
    result = JobResult(
        operation=OperationType.IMAGES_TO_PDF,
        input_path=input_paths[0] if input_paths else Path("empty"),
    )

    if not input_paths:
        result.error_message = "No input images provided for PDF creation."
        return result

    validate_pdf_page_count(len(input_paths), max_pages=MAX_PDF_PAGES)

    dest_path = resolve_collision_path(output_path, allow_overwrite=allow_overwrite)

    try:
        # Prohibit overwriting any input image
        validate_destination_safety(dest_path, input_paths)

        # Enforce cumulative bitmap memory budget before allocation
        max_cum_pixels = options.max_cumulative_pixels or DEFAULT_MAX_CUMULATIVE_PIXELS
        validate_cumulative_image_dimensions(
            input_paths,
            max_cumulative_pixels=max_cum_pixels,
            context="Images to PDF assembly",
        )

        total_in_bytes = sum(p.stat().st_size for p in input_paths)
        result.input_size_bytes = total_in_bytes

        writer = pypdf.PdfWriter()

        for idx, img_path in enumerate(input_paths):
            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("Images to PDF assembly cancelled by user.")

            validate_file_size(img_path)
            
            # Guard against multi-frame inputs in PDF assembly
            check_single_frame_image(
                img_path,
                allow_first_frame_only=False,
                operation_name="Images to PDF assembly",
            )

            with Image.open(img_path) as raw_img:
                transposed = ImageOps.exif_transpose(raw_img)
                img = transposed if transposed is not None else raw_img

                orig_w, orig_h = img.size
                validate_dimensions(orig_w, orig_h, context=f"Input image {img_path.name}")

                # Apply user-specified rotation
                rot = options.image_rotations.get(idx, 0) % 360
                if rot != 0:
                    img = img.rotate(-rot, expand=True)

                # Ensure RGB mode for clean PDF embedding
                if img.mode in ("RGBA", "LA", "PA") or ("transparency" in img.info):
                    img = flatten_alpha_on_background(img, (255, 255, 255))
                else:
                    img, _ = prepare_color_profile_and_mode(img, "RGB")

                # Handle Page Size mapping
                if options.page_size in PDF_PAGE_SIZES:
                    page_w_pt, page_h_pt = PDF_PAGE_SIZES[options.page_size]

                    # Orientation calculation
                    if options.orientation == PageOrientation.AUTO:
                        is_landscape = img.width > img.height
                        target_w = max(page_w_pt, page_h_pt) if is_landscape else min(page_w_pt, page_h_pt)
                        target_h = min(page_w_pt, page_h_pt) if is_landscape else max(page_w_pt, page_h_pt)
                    elif options.orientation == PageOrientation.LANDSCAPE:
                        target_w = max(page_w_pt, page_h_pt)
                        target_h = min(page_w_pt, page_h_pt)
                    else:
                        target_w = min(page_w_pt, page_h_pt)
                        target_h = max(page_w_pt, page_h_pt)

                    margin = options.margin_pt
                    avail_w = max(1.0, target_w - 2 * margin)
                    avail_h = max(1.0, target_h - 2 * margin)

                    # Scale image preserving aspect ratio to fit available area
                    scale = min(avail_w / img.width, avail_h / img.height)
                    draw_w = img.width * scale
                    draw_h = img.height * scale

                    # Convert PIL image to temporary PDF page
                    buf = io.BytesIO()
                    img.save(buf, format="PDF", resolution=72.0 / scale)
                    buf.seek(0)
                    page_reader = pypdf.PdfReader(buf)
                    writer.add_page(page_reader.pages[0])

                else:
                    # IMAGE_SIZE: Page matches natural image dimensions at 150 DPI
                    buf = io.BytesIO()
                    img.save(buf, format="PDF", resolution=150.0)
                    buf.seek(0)
                    page_reader = pypdf.PdfReader(buf)
                    writer.add_page(page_reader.pages[0])

        if cancel_token and cancel_token.is_cancelled:
            raise OperationCancelledError("Images to PDF assembly cancelled before saving.")

        # Stage output and commit atomically
        staged = StagedOutput(dest_path, cancel_token=cancel_token)
        with staged as staged_path:
            with open(staged_path, "wb") as f_out:
                writer.write(f_out)
        staged.commit()

        result.output_path = dest_path
        result.output_size_bytes = dest_path.stat().st_size
        result.success = True
        orient_str = options.orientation.value if isinstance(options.orientation, PageOrientation) else str(options.orientation)
        result.checks.append(
            RequirementCheck(
                name="PDF Page Assembly",
                target=f"{len(input_paths)} pages",
                actual=f"{len(writer.pages)} pages",
                passed=(len(writer.pages) == len(input_paths)),
                details=f"Page Size: {options.page_size}, Orientation: {orient_str}",
            )
        )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result


def pdf_to_images(
    input_path: Path,
    output_dir: Path,
    options: PdfToImagesOptions,
    stem_prefix: Optional[str] = None,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Render PDF pages to PNG or JPEG images.
    Enforces maximum decoded-pixel limits on rendered pages using math.ceil bounds.
    When as_zip=True, uses job-owned temporary workspace to ensure pre-existing files
    in the user output directory are never overwritten or deleted as disposable intermediates.
    Tracks created files with MultiFileJobRollback to purge newly created outputs on abort.
    """
    result = JobResult(
        operation=OperationType.PDF_TO_IMAGES,
        input_path=input_path,
    )
    rollback_tracker = MultiFileJobRollback()
    protected_inputs = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))

    try:
        input_size = validate_file_size(input_path)
        result.input_size_bytes = input_size
        detected = sniff_content_type(input_path)
        if detected != "PDF":
            raise ValueError(f"Input is not a PDF file (detected {detected}).")

        # Check for encryption using pypdf
        pypdf_reader = pypdf.PdfReader(input_path)
        if pypdf_reader.is_encrypted:
            if not options.password:
                raise PdfPasswordRequiredError("Document is password protected. Please provide a decryption password.")
            if pypdf_reader.decrypt(options.password) == 0:
                raise InvalidPdfPasswordError("Invalid decryption password.")

        try:
            pdf = pypdfium2.PdfDocument(input_path, password=options.password)
        except Exception as e:
            msg = str(e).lower()
            if "password" in msg or "encrypted" in msg:
                raise PdfPasswordRequiredError("Document is password protected. Please provide a decryption password.")
            raise

        total_pages = len(pdf)
        validate_pdf_page_count(total_pages, max_pages=MAX_PDF_PAGES)
        validate_dpi(options.dpi)

        target_indices = parse_page_range(options.page_range, total_pages)
        fmt = "JPEG" if options.target_format.upper() in ("JPEG", "JPG") else "PNG"
        ext = ".jpg" if fmt == "JPEG" else ".png"

        stem = sanitize_filename(stem_prefix or input_path.stem)
        scale = options.dpi / 72.0
        output_dir.mkdir(parents=True, exist_ok=True)

        if options.as_zip:
            # Mode A: ZIP bundle export using isolated temporary workspace
            zip_name = f"{stem}_pages.zip"
            zip_dest = resolve_collision_path(output_dir / zip_name, allow_overwrite=allow_overwrite)
            validate_destination_safety(zip_dest, protected_inputs)

            with TempJobWorkspace() as temp_ws:
                temp_rendered_files: List[Path] = []
                for idx in target_indices:
                    if cancel_token and cancel_token.is_cancelled:
                        raise OperationCancelledError("PDF rendering cancelled by user.")

                    page = pdf.get_page(idx)
                    page_w = page.get_width()
                    page_h = page.get_height()

                    render_w = math.ceil(page_w * scale)
                    render_h = math.ceil(page_h * scale)
                    validate_dimensions(render_w, render_h, context=f"Page {idx + 1} rendering")

                    pil_image = page.render(scale=scale).to_pil()

                    if cancel_token and cancel_token.is_cancelled:
                        raise OperationCancelledError("PDF rendering cancelled after page generation.")

                    filename = f"{stem}_page_{idx + 1:04d}{ext}"
                    temp_file = temp_ws / filename
                    if fmt == "JPEG":
                        flatten_alpha_on_background(pil_image, (255, 255, 255)).save(
                            temp_file, format="JPEG", quality=90, optimize=True
                        )
                    else:
                        pil_image.save(temp_file, format="PNG", optimize=True)

                    temp_rendered_files.append(temp_file)

                if cancel_token and cancel_token.is_cancelled:
                    raise OperationCancelledError("PDF to images cancelled before ZIP creation.")

                staged_zip = StagedOutput(zip_dest, cancel_token=cancel_token)
                with staged_zip as staged_zip_path:
                    with zipfile.ZipFile(staged_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                        for f in temp_rendered_files:
                            zipf.write(f, arcname=f.name)
                rollback_tracker.stage_commit(staged_zip)

            result.output_path = zip_dest
            result.output_size_bytes = zip_dest.stat().st_size
        else:
            # Mode B: Individual page image files written directly to output directory
            rendered_files: List[Path] = []
            for idx in target_indices:
                if cancel_token and cancel_token.is_cancelled:
                    raise OperationCancelledError("PDF rendering cancelled by user.")

                page = pdf.get_page(idx)
                page_w = page.get_width()
                page_h = page.get_height()

                render_w = math.ceil(page_w * scale)
                render_h = math.ceil(page_h * scale)
                validate_dimensions(render_w, render_h, context=f"Page {idx + 1} rendering")

                pil_image = page.render(scale=scale).to_pil()

                if cancel_token and cancel_token.is_cancelled:
                    raise OperationCancelledError("PDF rendering cancelled after page generation.")

                filename = f"{stem}_page_{idx + 1:04d}{ext}"
                destination = resolve_collision_path(output_dir / filename, allow_overwrite=allow_overwrite)
                validate_destination_safety(destination, protected_inputs)

                staged = StagedOutput(destination, cancel_token=cancel_token)
                with staged as staged_path:
                    if fmt == "JPEG":
                        flatten_alpha_on_background(pil_image, (255, 255, 255)).save(
                            staged_path, format="JPEG", quality=90, optimize=True
                        )
                    else:
                        pil_image.save(staged_path, format="PNG", optimize=True)
                rollback_tracker.stage_commit(staged)
                rendered_files.append(destination)

            result.output_path = rendered_files[0] if rendered_files else output_dir
            result.output_size_bytes = sum(f.stat().st_size for f in rendered_files)

        rollback_tracker.mark_success()
        result.details["rendered_count"] = len(target_indices)
        result.success = True
        result.checks.append(
            RequirementCheck(
                name="Page Rendering",
                target=f"{len(target_indices)} pages at {options.dpi} DPI",
                actual=f"{len(target_indices)} rendered",
                passed=True,
                details=f"Rendered to {fmt} ({'ZIP bundle' if options.as_zip else 'Separate files'})",
            )
        )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)
        rollback_tracker.rollback()

    return result


def reduce_pdf_size(
    input_path: Path,
    output_path: Path,
    options: PdfReduceOptions,
    password: Optional[str] = None,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Reduce PDF file size via lossless structural optimization or scanned downsampling.
    Warns on digitally signed documents and preserves existing destinations if failed.
    """
    result = JobResult(
        operation=OperationType.PDF_REDUCE,
        input_path=input_path,
    )
    dest_path = resolve_collision_path(output_path, allow_overwrite=allow_overwrite)
    protected_inputs = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))

    try:
        # Strictly prohibit overwriting any queued original input
        validate_destination_safety(dest_path, protected_inputs)

        input_size = validate_file_size(input_path)
        result.input_size_bytes = input_size

        # Resolve password parameter from direct argument or options
        active_password = password if password is not None else options.password

        # Check for encryption and signatures
        reader = pypdf.PdfReader(input_path)
        if reader.is_encrypted:
            if not active_password:
                raise PdfPasswordRequiredError("Document is password protected. Decryption password required.")
            res = reader.decrypt(active_password)
            if res == 0:
                raise InvalidPdfPasswordError("Invalid decryption password.")

        # Signature check
        is_signed, has_empty = detect_pdf_signatures(reader)
        if is_signed:
            result.warnings.append(
                "Digital signature detected: Modifying, optimizing, or re-saving this PDF invalidates cryptographic signatures because file byte ranges are rewritten."
            )
        elif has_empty:
            result.warnings.append(
                "Unsigned signature field detected: Document contains form fields prepared for digital signing (retaining field structures does not constitute a valid digital signature)."
            )

        if options.downsample_scanned:
            validate_dpi(options.target_dpi)
            # Mode 2: Scanned document downsampling (rasterizes pages to lower DPI JPEG)
            result.warnings.append(
                "Scanned downsampling mode rasterizes all pages. Vector text and forms are converted into images."
            )
            doc = pypdfium2.PdfDocument(input_path, password=active_password)
            page_dims = [(doc.get_page(i).get_width(), doc.get_page(i).get_height()) for i in range(len(doc))]
            max_cum_pixels = options.max_cumulative_pixels or DEFAULT_MAX_CUMULATIVE_PIXELS
            validate_cumulative_pdf_pages_dimensions(
                page_dims,
                dpi=options.target_dpi,
                max_cumulative_pixels=max_cum_pixels,
                context="Scanned PDF downsampling",
            )
            scale = options.target_dpi / 72.0
            writer = pypdf.PdfWriter()

            for i in range(len(doc)):
                if cancel_token and cancel_token.is_cancelled:
                    raise OperationCancelledError("PDF downsampling cancelled by user.")

                page = doc.get_page(i)
                render_w = math.ceil(page.get_width() * scale)
                render_h = math.ceil(page.get_height() * scale)
                validate_dimensions(render_w, render_h, context=f"Page {i+1} scanned downsampling")

                rendered = page.render(scale=scale).to_pil().convert("RGB")
                
                # Directly encode into PDF page using requested image quality
                page_pdf_buf = io.BytesIO()
                rendered.save(
                    page_pdf_buf,
                    format="PDF",
                    quality=options.image_quality,
                    resolution=float(options.target_dpi),
                )
                page_pdf_buf.seek(0)
                
                p_reader = pypdf.PdfReader(page_pdf_buf)
                writer.add_page(p_reader.pages[0])

            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("PDF downsampling cancelled before writing output.")

            staged = StagedOutput(dest_path, cancel_token=cancel_token)
            with staged as staged_path:
                with open(staged_path, "wb") as f_out:
                    writer.write(f_out)
            staged.commit()

        else:
            # Mode 1: Lossless structural optimization
            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("PDF structural optimization cancelled by user.")

            writer = pypdf.PdfWriter()
            writer.append(reader)

            # Preserve embedded file attachments from reader
            if hasattr(reader, "attachments") and reader.attachments:
                for att_name, att_data_list in reader.attachments.items():
                    for att_data in att_data_list:
                        writer.add_attachment(att_name, att_data)

            # Compress streams and identical objects
            for page in writer.pages:
                page.compress_content_streams()
            writer.compress_identical_objects()

            if options.remove_metadata:
                writer.add_metadata({})
                # Strip XMP metadata stream from catalog if present
                try:
                    if "/Metadata" in writer.root_object:
                        del writer.root_object["/Metadata"]
                except Exception:
                    pass
            else:
                # Explicitly preserve document metadata if remove_metadata is False
                if reader.metadata:
                    writer.add_metadata(reader.metadata)
                # Explicitly preserve catalog XMP metadata stream if present
                try:
                    if "/Metadata" in reader.trailer["/Root"]:
                        writer.root_object[pypdf.generic.NameObject("/Metadata")] = reader.trailer["/Root"]["/Metadata"]
                except Exception:
                    pass

            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("PDF structural optimization cancelled before writing output.")

            staged = StagedOutput(dest_path, cancel_token=cancel_token)
            with staged as staged_path:
                with open(staged_path, "wb") as f_out:
                    writer.write(f_out)
            staged.commit()

        output_size = dest_path.stat().st_size
        result.output_path = dest_path
        result.output_size_bytes = output_size
        result.success = True

        passed_reduction = (output_size < input_size)

        if passed_reduction:
            savings = input_size - output_size
            pct_savings = (savings / input_size) * 100
            result.checks.append(
                RequirementCheck(
                    name="PDF Optimization",
                    target="Size Reduction",
                    actual=f"{bytes_to_human(output_size)} ({pct_savings:.1f}% reduction)",
                    passed=True,
                    details=f"Reduced by {bytes_to_human(savings)}. Mode: {'Scanned Downsampling' if options.downsample_scanned else 'Lossless Structural'}",
                )
            )
        else:
            pct_increase = ((output_size - input_size) / input_size) * 100 if input_size > 0 else 0.0
            result.warnings.append(
                f"Structural optimization did not achieve a size reduction for this document "
                f"({bytes_to_human(input_size)} -> {bytes_to_human(output_size)})."
            )
            result.checks.append(
                RequirementCheck(
                    name="PDF Optimization",
                    target="Size Reduction",
                    actual=f"{bytes_to_human(output_size)} (+{pct_increase:.1f}% change)",
                    passed=False,
                    details=f"Output file is not smaller than original input. Mode: {'Scanned Downsampling' if options.downsample_scanned else 'Lossless Structural'}",
                )
            )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result
