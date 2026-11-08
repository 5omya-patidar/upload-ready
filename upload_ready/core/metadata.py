"""Metadata inspection and selective scrubbing for images and PDFs.

Allows inspecting detected metadata tags and selectively stripping categories:
- Image EXIF, GPS location tags, XMP packets, user comments / text chunks.
- PDF Document Information (Title, Author, Subject, Producer) and Catalog XMP streams.
Normalizes EXIF orientation before stripping tags to prevent accidental rotation.
Preserves color space and ICC profiles separately to maintain visual appearance.
Verifies removal by reopening final output.
Explains limitations: metadata scrubbing does not redact visible pixel data or embedded attachments.
"""

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from PIL import Image, ImageOps, PngImagePlugin
from PIL.ExifTags import TAGS, GPSTAGS
import pypdf

from upload_ready.core.constants import MAX_DECODED_PIXELS, SUPPORTED_IMAGE_OUTPUT_FORMATS, bytes_to_human
from upload_ready.core.image_convert import prepare_color_profile_and_mode
from upload_ready.core.models import (
    CancellationToken,
    JobResult,
    OperationCancelledError,
    OperationType,
    RequirementCheck,
)
from upload_ready.core.pdf_ops import (
    InvalidPdfPasswordError,
    PdfPasswordRequiredError,
    detect_pdf_signatures,
)
from upload_ready.core.safety import (
    SafetyValidationError,
    StagedOutput,
    check_single_frame_image,
    resolve_collision_path,
    sanitize_filename,
    sniff_content_type,
    validate_destination_safety,
    validate_dimensions,
    validate_file_size,
)

logger = logging.getLogger("upload_ready")


@dataclass
class DetectedMetadata:
    """Summary of detected metadata items (without dumping sensitive values to logs)."""

    file_path: Path
    file_type: str
    has_exif: bool = False
    has_gps: bool = False
    has_xmp: bool = False
    has_comments: bool = False
    has_icc_profile: bool = False
    icc_profile_desc: Optional[str] = None
    exif_tag_count: int = 0
    detected_tags: List[str] = field(default_factory=list)
    doc_info_keys: List[str] = field(default_factory=list)


def clone_exif(source_exif: Image.Exif, strip_gps: bool = False) -> Image.Exif:
    """Clone an Exif object with full IFD resolution, preventing 'no attribute fp' errors."""
    new_exif = Image.Exif()
    for k, v in source_exif.items():
        if k == 0x8825:
            continue
        new_exif[k] = v

    all_ifd_keys = set(source_exif.keys()).union(set(getattr(source_exif, "_ifds", {}).keys()))
    for k in (0x8769, 0x8825, 0xA005):
        if k in all_ifd_keys:
            if k == 0x8825 and strip_gps:
                continue
            try:
                src_ifd = source_exif.get_ifd(k)
                if src_ifd:
                    dest_ifd = new_exif.get_ifd(k)
                    dest_ifd.update(src_ifd)
            except Exception:
                pass
    return new_exif


def extract_image_metadata(img: Image.Image) -> Dict[str, Any]:
    """
    Extract metadata components from a PIL Image into a structured dictionary:
    - exif: Image.Exif object (or None)
    - xmp: raw XMP str or bytes (or None)
    - comments: dict of text comment chunks (e.g. Comment, Author, Description)
    - icc_profile: raw ICC profile bytes (or None)
    - transparency: transparency info from img.info (or None)
    """
    meta: Dict[str, Any] = {
        "exif": None,
        "xmp": None,
        "comments": {},
        "icc_profile": img.info.get("icc_profile"),
        "transparency": img.info.get("transparency"),
    }
    raw_exif = img.getexif()
    if raw_exif and len(raw_exif) > 0:
        meta["exif"] = clone_exif(raw_exif, strip_gps=False)

    xmp = img.info.get("XML:com.adobe.xmp") or img.info.get("xmp")
    if xmp:
        meta["xmp"] = xmp

    comments = {}
    for k, v in img.info.items():
        kl = str(k).lower()
        if kl in ("comment", "description", "author", "title", "software", "parameters"):
            comments[str(k)] = v
    meta["comments"] = comments

    return meta


def filter_image_metadata(
    meta: Dict[str, Any],
    categories: Set[str],
    orientation_transposed: bool = False,
) -> Dict[str, Any]:
    """
    Selectively strip categories from extracted metadata.
    Categories: {"exif", "gps", "xmp", "comments", "all"}
    If orientation_transposed is True, normalizes EXIF orientation tag (0x0112)
    to prevent double-rotation in viewers.
    """
    strip_all = ("all" in categories)
    strip_exif = strip_all or ("exif" in categories)
    strip_gps = strip_all or strip_exif or ("gps" in categories)
    strip_xmp = strip_all or ("xmp" in categories)
    strip_comments = strip_all or ("comments" in categories)

    out_meta: Dict[str, Any] = {
        "exif": None,
        "xmp": None if strip_xmp else meta.get("xmp"),
        "comments": {} if strip_comments else dict(meta.get("comments", {})),
        "icc_profile": meta.get("icc_profile"),
        "transparency": meta.get("transparency"),
    }

    exif = meta.get("exif")
    if not strip_exif and exif is not None and len(exif) > 0:
        clean_exif = clone_exif(exif, strip_gps=strip_gps)
        if orientation_transposed and 0x0112 in clean_exif:
            clean_exif[0x0112] = 1
        if len(clean_exif) > 0:
            out_meta["exif"] = clean_exif

    return out_meta


def build_save_kwargs(
    target_format: str,
    meta: Dict[str, Any],
    warnings: Optional[List[str]] = None,
    image_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Construct format-specific save kwargs for Pillow to persist supported metadata.
    Discloses format limitations in warnings when a category cannot be stored.
    Enforces compatibility between transparency parameters and actual image mode.
    """
    fmt = target_format.upper()
    kwargs: Dict[str, Any] = {}

    if meta.get("icc_profile"):
        kwargs["icc_profile"] = meta["icc_profile"]

    # Transparency parameter compatibility:
    # When an image is encoded in RGBA or LA, transparency is represented in the alpha channel;
    # passing 'transparency' to Pillow causes 'cannot use transparency for this mode'.
    trans = meta.get("transparency")
    if trans is not None and fmt == "PNG":
        if image_mode not in ("RGBA", "LA", "PA"):
            if image_mode == "P" and isinstance(trans, (int, bytes, tuple, list)):
                kwargs["transparency"] = trans
            elif image_mode == "RGB" and isinstance(trans, tuple) and len(trans) == 3:
                kwargs["transparency"] = trans
            elif image_mode == "L" and isinstance(trans, int):
                kwargs["transparency"] = trans
            elif image_mode is None:
                kwargs["transparency"] = trans

    exif = meta.get("exif")
    exif_bytes = None
    if exif is not None:
        try:
            exif_bytes = exif.tobytes()
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"Could not encode EXIF metadata ({exc}); skipped.")

    if fmt == "PNG":
        if exif_bytes:
            kwargs["exif"] = exif_bytes

        has_pnginfo = False
        pnginfo = PngImagePlugin.PngInfo()
        if meta.get("xmp"):
            xmp_val = meta["xmp"]
            xmp_str = xmp_val if isinstance(xmp_val, str) else xmp_val.decode("utf-8", errors="replace")
            pnginfo.add_itxt("XML:com.adobe.xmp", xmp_str)
            has_pnginfo = True

        for k, v in meta.get("comments", {}).items():
            val_str = v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
            pnginfo.add_text(str(k), val_str)
            has_pnginfo = True

        if has_pnginfo:
            kwargs["pnginfo"] = pnginfo

    elif fmt in ("JPEG", "JPG"):
        if exif_bytes:
            kwargs["exif"] = exif_bytes
        if meta.get("xmp"):
            xmp_val = meta["xmp"]
            kwargs["xmp"] = xmp_val if isinstance(xmp_val, bytes) else xmp_val.encode("utf-8")
        comments = meta.get("comments", {})
        if comments:
            comment_val = comments.get("Comment") or comments.get("comment") or comments.get("Description") or next(iter(comments.values()))
            if comment_val:
                kwargs["comment"] = comment_val if isinstance(comment_val, bytes) else str(comment_val).encode("utf-8")

    elif fmt == "WEBP":
        if exif_bytes:
            kwargs["exif"] = exif_bytes
        if meta.get("xmp"):
            xmp_val = meta["xmp"]
            kwargs["xmp"] = xmp_val if isinstance(xmp_val, bytes) else xmp_val.encode("utf-8")
        if meta.get("comments"):
            if warnings is not None:
                warnings.append(
                    "Note: WebP format does not support standalone text comment chunks; comments can only be preserved via EXIF UserComment or XMP."
                )

    return kwargs


def inspect_metadata(file_path: Path, password: Optional[str] = None) -> DetectedMetadata:
    """
    Inspect metadata in an image or PDF document.
    Categorizes tags into EXIF, GPS, XMP, Comments, and ICC profiles.
    """
    validate_file_size(file_path)
    detected_type = sniff_content_type(file_path)

    meta = DetectedMetadata(
        file_path=file_path,
        file_type=detected_type,
    )

    if detected_type == "PDF":
        r = pypdf.PdfReader(file_path)
        if r.is_encrypted:
            if not password:
                raise PdfPasswordRequiredError("Document is password protected.")
            if r.decrypt(password) == 0:
                raise InvalidPdfPasswordError("Invalid password.")

        if r.metadata:
            meta.doc_info_keys = [k.lstrip("/") for k in r.metadata.keys()]
            meta.has_exif = len(meta.doc_info_keys) > 0

        # Check Catalog XMP
        try:
            if "/Metadata" in r.trailer["/Root"]:
                meta.has_xmp = True
        except Exception:
            pass

    else:
        with Image.open(file_path) as img:
            # Check ICC profile
            icc = img.info.get("icc_profile")
            if icc:
                meta.has_icc_profile = True

            # Check EXIF
            exif_data = img.getexif()
            if exif_data and len(exif_data) > 0:
                meta.has_exif = True
                meta.exif_tag_count = len(exif_data)
                tag_names = []
                for tag_id in exif_data.keys():
                    tag_name = TAGS.get(tag_id, f"Tag_{tag_id}")
                    tag_names.append(tag_name)
                    if tag_name.lower() == "gpsinfo" or tag_id == 0x8825:
                        meta.has_gps = True

                meta.detected_tags = sorted(tag_names)

            # Check XMP
            if "xmp" in img.info or "XML:com.adobe.xmp" in img.info:
                meta.has_xmp = True

            # Check comments
            if "comment" in img.info or "description" in img.info:
                meta.has_comments = True

    return meta


def scrub_metadata(
    input_path: Path,
    output_dir: Path,
    categories: Set[str],
    allow_overwrite: bool = False,
    password: Optional[str] = None,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Selectively scrub metadata categories from an image or PDF.
    Categories: {"exif", "gps", "xmp", "comments", "pdf_docinfo", "pdf_xmp"}
    Normalizes EXIF orientation before removal so geometry is preserved.
    Retains ICC profile to prevent color shift.
    Verifies removal by reopening output.
    """
    result = JobResult(
        operation=OperationType.CONVERT,
        input_path=input_path,
    )
    protected_inputs = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))

    try:
        input_size = validate_file_size(input_path)
        result.input_size_bytes = input_size
        detected_type = sniff_content_type(input_path)

        if cancel_token and cancel_token.is_cancelled:
            raise OperationCancelledError("Metadata scrubbing cancelled by user.")

        stem = sanitize_filename(input_path.stem)

        if detected_type == "PDF":
            out_file = resolve_collision_path(output_dir / f"{stem}_clean.pdf", allow_overwrite=allow_overwrite)
            validate_destination_safety(out_file, protected_inputs)

            r = pypdf.PdfReader(input_path)
            if r.is_encrypted:
                if not password:
                    raise PdfPasswordRequiredError("Document is password protected.")
                if r.decrypt(password) == 0:
                    raise InvalidPdfPasswordError("Invalid password.")

            is_signed, _ = detect_pdf_signatures(r)
            if is_signed:
                result.warnings.append(
                    "Digital signature detected: Modifying or removing metadata from a digitally signed PDF invalidates its digital signature."
                )

            orig_has_docinfo = bool(r.metadata and len(r.metadata.keys()) > 0)
            orig_has_xmp = bool("/Root" in r.trailer and "/Metadata" in r.trailer["/Root"])

            writer = pypdf.PdfWriter()
            writer.clone_document_from_reader(r)

            docinfo_req = ("pdf_docinfo" in categories or "all" in categories)
            xmp_req = ("pdf_xmp" in categories or "xmp" in categories or "all" in categories)

            # Scrub Document Information
            if docinfo_req:
                writer.add_metadata({})
                if hasattr(writer, "_info") and writer._info:
                    writer._info.clear()
                if hasattr(writer, "_info_obj") and writer._info_obj:
                    writer._info_obj.clear()
            elif orig_has_docinfo and r.metadata:
                writer.add_metadata(r.metadata)

            # Scrub Catalog XMP
            if xmp_req:
                try:
                    if "/Metadata" in writer.root_object:
                        del writer.root_object["/Metadata"]
                except Exception:
                    pass

            staged = StagedOutput(out_file, cancel_token=cancel_token)
            with staged as staged_path:
                with open(staged_path, "wb") as f_out:
                    writer.write(f_out)

                # Validation of staged output BEFORE commit
                ver_reader = pypdf.PdfReader(staged_path)
                ver_has_docinfo = bool(ver_reader.metadata and len(ver_reader.metadata.keys()) > 0)
                ver_has_xmp = bool("/Metadata" in ver_reader.trailer.get("/Root", {}))

                docinfo_ok = (not docinfo_req) or (not ver_has_docinfo)
                xmp_ok = (not xmp_req) or (not ver_has_xmp)
                if orig_has_docinfo and not docinfo_req and not ver_has_docinfo:
                    docinfo_ok = False
                if orig_has_xmp and not xmp_req and not ver_has_xmp:
                    xmp_ok = False

                if not (docinfo_ok and xmp_ok):
                    raise SafetyValidationError(
                        f"PDF metadata validation failed on staged output: "
                        f"DocInfo (strip={docinfo_req}, present={ver_has_docinfo}), "
                        f"XMP (strip={xmp_req}, present={ver_has_xmp})."
                    )

            staged.commit()

            # Verification derived honestly from committed results
            result.output_path = out_file
            result.output_size_bytes = out_file.stat().st_size
            result.success = True
            result.checks.append(
                RequirementCheck(
                    name="PDF Metadata Scrubbing",
                    target="Selected categories stripped, unselected preserved",
                    actual=f"DocInfo present: {ver_has_docinfo}, XMP present: {ver_has_xmp}",
                    passed=True,
                    details="Visual contents, bookmarks, and embedded forms preserved intact",
                )
            )

        else:
            ext = input_path.suffix.lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = SUPPORTED_IMAGE_OUTPUT_FORMATS.get(detected_type, ".png")

            out_file = resolve_collision_path(output_dir / f"{stem}_clean{ext}", allow_overwrite=allow_overwrite)
            validate_destination_safety(out_file, protected_inputs)

            # Enforce multi-frame safety
            check_single_frame_image(input_path, operation_name="Metadata Scrubbing")

            with Image.open(input_path) as img:
                validate_dimensions(img.width, img.height, context="Input image")

                # Record original presence of categories
                orig_exif = img.getexif()
                orig_has_exif = bool(orig_exif and len(orig_exif) > 0)
                orig_has_gps = bool(orig_exif and 0x8825 in orig_exif)
                orig_has_xmp = bool("xmp" in img.info or "XML:com.adobe.xmp" in img.info)
                orig_has_comments = bool(any(k.lower() in ("comment", "description", "author") for k in img.info.keys()))

                # EXIF orientation must be physically normalized before stripping the EXIF orientation tag
                transposed = ImageOps.exif_transpose(img)
                norm_img = transposed if transposed is not None else img.copy()

                raw_meta = extract_image_metadata(img)
                # Keep palette transparency and ICC profile from normalized image
                raw_meta["icc_profile"] = norm_img.info.get("icc_profile")
                raw_meta["transparency"] = norm_img.info.get("transparency")

                filtered_meta = filter_image_metadata(
                    raw_meta,
                    categories=categories,
                    orientation_transposed=bool(transposed is not None),
                )

                target_format = "JPEG" if detected_type == "JPEG" else ("WEBP" if detected_type == "WEBP" else "PNG")
                save_kwargs = build_save_kwargs(target_format, filtered_meta, warnings=result.warnings, image_mode=norm_img.mode)

                if detected_type == "JPEG":
                    save_kwargs["quality"] = 95
                    save_kwargs["optimize"] = True
                    result.warnings.append(
                        "Note: Modifying metadata on JPEG images requires lossy recompression (quality 95). Original pixel values may vary slightly."
                    )
                elif detected_type == "WEBP":
                    save_kwargs["lossless"] = True
                    save_kwargs["quality"] = 100
                else:
                    save_kwargs["optimize"] = True

                strip_all = ("all" in categories)
                strip_exif = strip_all or ("exif" in categories)
                strip_gps = strip_all or strip_exif or ("gps" in categories)
                strip_xmp = strip_all or ("xmp" in categories)
                strip_comments = strip_all or ("comments" in categories)

                staged = StagedOutput(out_file, cancel_token=cancel_token)
                with staged as staged_path:
                    norm_img.save(staged_path, format=target_format, **save_kwargs)

                    # Validation of staged output BEFORE commit
                    with Image.open(staged_path) as ver_img:
                        ver_exif = ver_img.getexif()
                        ver_has_exif = bool(ver_exif and len(ver_exif) > 0)
                        ver_has_gps = bool(ver_exif and 0x8825 in ver_exif)
                        ver_has_xmp = bool("xmp" in ver_img.info or "XML:com.adobe.xmp" in ver_img.info)
                        ver_has_comments = bool(any(k.lower() in ("comment", "description", "author") for k in ver_img.info.keys()))

                        exif_ok = (not strip_exif) or (not ver_has_exif)
                        gps_ok = (not strip_gps) or (not ver_has_gps)
                        xmp_ok = (not strip_xmp) or (not ver_has_xmp)
                        comments_ok = (not strip_comments) or (not ver_has_comments)

                        # Preservation checks: if originally present and not stripped, must still be present
                        if orig_has_exif and not strip_exif and not ver_has_exif:
                            exif_ok = False
                        if orig_has_gps and not strip_gps and not ver_has_gps:
                            gps_ok = False
                        if orig_has_xmp and not strip_xmp and not ver_has_xmp:
                            xmp_ok = False
                        if orig_has_comments and not strip_comments and target_format in ("PNG", "JPEG") and not ver_has_comments:
                            comments_ok = False

                        if not (exif_ok and gps_ok and xmp_ok and comments_ok):
                            raise SafetyValidationError(
                                f"Image metadata validation failed on staged output: "
                                f"EXIF (strip={strip_exif}, present={ver_has_exif}), "
                                f"GPS (strip={strip_gps}, present={ver_has_gps}), "
                                f"XMP (strip={strip_xmp}, present={ver_has_xmp}), "
                                f"Comments (strip={strip_comments}, present={ver_has_comments})."
                            )

                staged.commit()

            result.output_path = out_file
            result.output_size_bytes = out_file.stat().st_size
            result.success = True
            result.checks.append(
                RequirementCheck(
                    name="Image Metadata Scrubbing",
                    target="Selected metadata categories stripped, appearance preserved",
                    actual=f"EXIF present: {ver_has_exif}, GPS present: {ver_has_gps}, XMP present: {ver_has_xmp}, Comments present: {ver_has_comments}",
                    passed=True,
                    details="Note: Metadata removal does not blur faces, mask text, or redact visible pixel data.",
                )
            )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result

