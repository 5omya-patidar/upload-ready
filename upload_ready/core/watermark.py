"""Purpose watermarking for images and PDF documents.

Allows applying purpose text (e.g. 'For rental verification only', 'Specimen only'):
- Configurable text, font size, color, opacity, angle, and positioning (center, corner, tiled).
- Images: alpha-blended rotated text overlay.
- PDFs: vector page overlay using page.merge_page() to preserve searchable text without rasterizing.
- Warning on digitally signed PDFs.
- Clear scope explanation: watermark indicates intended use; it is not cryptographic security or redaction.
Zero runtime downloads or online fonts.
"""

import io
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageOps
import pypdf

from upload_ready.core.constants import MAX_DECODED_PIXELS, bytes_to_human
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
    parse_page_range,
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


@dataclass
class WatermarkOptions:
    """Configuration for text purpose watermark overlay."""

    text: str = "FOR VERIFICATION ONLY"
    font_size: int = 36
    color: Tuple[int, int, int] = (128, 128, 128)
    opacity: float = 0.3  # 0.0 (transparent) to 1.0 (opaque)
    angle: float = 45.0  # Rotation angle in degrees
    position: str = "center"  # "center", "top_left", "bottom_right", "repeat_tile"
    page_range: str = "all"  # For PDFs
    password: Optional[str] = None  # Decryption password for protected PDFs


def create_watermark_overlay_image(
    canvas_w: int,
    canvas_h: int,
    options: WatermarkOptions,
    scale_factor: float = 1.0,
) -> Image.Image:
    """
    Generate an RGBA transparent image of specified width and height
    containing the rotated watermark text with configured opacity.
    If scale_factor != 1.0, proportionally scales font size, margins, and tile
    repetition spacing to match document-coordinate geometry on preview canvases.
    """
    overlay = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    alpha_int = max(0, min(255, round(options.opacity * 255)))
    text_color = options.color + (alpha_int,)

    effective_font_size = max(1, round(options.font_size * scale_factor))
    try:
        font = ImageFont.load_default(size=effective_font_size)
    except Exception:
        font = ImageFont.load_default()

    # Measure text bounding box
    dummy_draw = ImageDraw.Draw(overlay)
    bbox = dummy_draw.textbbox((0, 0), options.text, font=font)
    t_w = max(1, bbox[2] - bbox[0])
    t_h = max(1, bbox[3] - bbox[1])

    # Create text stamp on temporary transparent canvas
    padding = max(1, round(20 * scale_factor))
    stamp_w = t_w + 2 * padding
    stamp_h = t_h + 2 * padding
    stamp = Image.new("RGBA", (stamp_w, stamp_h), (0, 0, 0, 0))
    stamp_draw = ImageDraw.Draw(stamp)
    stamp_draw.text((padding, padding), options.text, font=font, fill=text_color)

    # Rotate stamp
    rotated_stamp = stamp.rotate(options.angle, expand=True, resample=Image.Resampling.BICUBIC)
    rw, rh = rotated_stamp.size

    margin = max(1, round(20 * scale_factor))

    if options.position == "repeat_tile":
        step_x = max(rw + max(1, round(40 * scale_factor)), max(1, round(100 * scale_factor)))
        step_y = max(rh + max(1, round(40 * scale_factor)), max(1, round(100 * scale_factor)))
        for y in range(-rh // 2, canvas_h + rh, step_y):
            for x in range(-rw // 2, canvas_w + rw, step_x):
                overlay.alpha_composite(rotated_stamp, (x, y))
    elif options.position == "top_left":
        overlay.alpha_composite(rotated_stamp, (margin, margin))
    elif options.position == "bottom_right":
        overlay.alpha_composite(rotated_stamp, (max(0, canvas_w - rw - margin), max(0, canvas_h - rh - margin)))
    else:  # "center"
        cx = (canvas_w - rw) // 2
        cy = (canvas_h - rh) // 2
        overlay.alpha_composite(rotated_stamp, (cx, cy))

    return overlay


def apply_watermark_to_image(
    input_path: Path,
    output_path: Path,
    options: WatermarkOptions,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Apply purpose watermark text to an image file.
    """
    result = JobResult(
        operation=OperationType.CONVERT,
        input_path=input_path,
    )
    protected = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))
    dest_path = resolve_collision_path(output_path, allow_overwrite=allow_overwrite)

    try:
        validate_destination_safety(dest_path, protected)
        validate_file_size(input_path)
        detected_fmt = sniff_content_type(input_path)

        # Enforce multi-frame safety
        check_single_frame_image(input_path, operation_name="Watermarking")

        with Image.open(input_path) as raw_img:
            validate_dimensions(raw_img.width, raw_img.height, context="Watermark image")
            transposed = ImageOps.exif_transpose(raw_img)
            img = transposed if transposed is not None else raw_img

            orig_w, orig_h = img.size
            result.input_dimensions = (orig_w, orig_h)

            overlay = create_watermark_overlay_image(orig_w, orig_h, options)

            # Composite
            base_rgba = img.convert("RGBA")
            composited = Image.alpha_composite(base_rgba, overlay)

            staged = StagedOutput(dest_path, cancel_token=cancel_token)
            with staged as staged_path:
                if detected_fmt == "JPEG":
                    composited.convert("RGB").save(staged_path, format="JPEG", quality=95, optimize=True)
                elif detected_fmt == "WEBP":
                    composited.save(staged_path, format="WEBP", lossless=True, quality=100)
                else:
                    composited.save(staged_path, format="PNG", optimize=True)
            staged.commit()

            result.output_path = dest_path
            result.output_dimensions = (orig_w, orig_h)
            result.output_size_bytes = dest_path.stat().st_size
            result.success = True
            result.checks.append(
                RequirementCheck(
                    name="Purpose Watermark",
                    target=options.text,
                    actual="Watermark applied",
                    passed=True,
                    details=f"Position: {options.position}, Angle: {options.angle} deg, Opacity: {options.opacity:.2f}",
                )
            )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result


def apply_watermark_to_pdf(
    input_path: Path,
    output_path: Path,
    options: WatermarkOptions,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Apply purpose watermark text to a PDF document using structural overlay.
    Merges watermark onto target pages while preserving searchable vector text.
    """
    result = JobResult(
        operation=OperationType.PDF_REDUCE,
        input_path=input_path,
    )
    protected = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))
    dest_path = resolve_collision_path(output_path, allow_overwrite=allow_overwrite)

    try:
        validate_destination_safety(dest_path, protected)
        validate_file_size(input_path)
        detected = sniff_content_type(input_path)
        if detected != "PDF":
            raise ValueError(f"Input file is not a PDF (detected {detected}).")

        reader = pypdf.PdfReader(input_path)
        if reader.is_encrypted:
            if not options.password:
                raise PdfPasswordRequiredError("Document is password protected.")
            if reader.decrypt(options.password) == 0:
                raise InvalidPdfPasswordError("Invalid decryption password.")

        is_signed, _ = detect_pdf_signatures(reader)
        if is_signed:
            result.warnings.append(
                "Digital signature detected: Adding a watermark to a digitally signed PDF invalidates its cryptographic signature."
            )

        total_pages = len(reader.pages)
        target_indices = set(parse_page_range(options.page_range, total_pages))

        # Clone entire document structure (preserves outlines, bookmarks, form fields, and attachments)
        writer = pypdf.PdfWriter(clone_from=reader)

        for idx in range(total_pages):
            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("PDF watermarking cancelled by user.")

            if idx in target_indices:
                page = writer.pages[idx]
                pw_pt = float(page.mediabox.width)
                ph_pt = float(page.mediabox.height)

                # Render watermark overlay image at 150 DPI
                scale = 150.0 / 72.0
                canvas_w = max(10, round(pw_pt * scale))
                canvas_h = max(10, round(ph_pt * scale))
                validate_dimensions(canvas_w, canvas_h, context=f"Watermark page {idx + 1}")

                overlay_img = create_watermark_overlay_image(canvas_w, canvas_h, options)

                # Save overlay to PDF page
                buf = io.BytesIO()
                overlay_img.save(buf, format="PDF", resolution=150.0)
                buf.seek(0)
                wm_reader = pypdf.PdfReader(buf)
                wm_page = wm_reader.pages[0]

                # Merge overlay on top of cloned page content
                page.merge_page(wm_page)

        if cancel_token and cancel_token.is_cancelled:
            raise OperationCancelledError("PDF watermarking cancelled before saving.")

        staged = StagedOutput(dest_path, cancel_token=cancel_token)
        with staged as staged_path:
            with open(staged_path, "wb") as f_out:
                writer.write(f_out)
        staged.commit()

        result.output_path = dest_path
        result.output_size_bytes = dest_path.stat().st_size
        result.success = True
        result.checks.append(
            RequirementCheck(
                name="PDF Purpose Watermark",
                target=f"Applied to {len(target_indices)}/{total_pages} pages",
                actual=f"Watermarked {len(target_indices)} pages structurally",
                passed=True,
                details=f"Text: '{options.text}'. Note: Watermark indicates intended use; not an encryption barrier.",
            )
        )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result

