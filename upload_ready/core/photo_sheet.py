"""Passport-photo and signature print sheet generator.

Prepares user-provided portrait or signature images for printing or upload:
- Physical sizing in millimetres or pixels with explicit DPI.
- Configurable grid (copies, margins, spacing, cut guides).
- Standard sheets (A4, Letter) or custom paper dimensions.
- Signature preparation: transparency preservation or explicit background flattening.
- PDF and image export.
Disclaimers: Does not generate or imitate signatures. Does not provide cryptographic signatures.
Users must independently verify the exact requirements of their receiving authority.
"""

import io
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageOps
import pypdf

from upload_ready.core.constants import MAX_DECODED_PIXELS, PDF_PAGE_SIZES, bytes_to_human
from upload_ready.core.image_convert import flatten_alpha_on_background
from upload_ready.core.models import (
    CancellationToken,
    JobResult,
    OperationCancelledError,
    OperationType,
    RequirementCheck,
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
    validate_dpi,
    validate_file_size,
)

# Sheet sizes in millimetres (width, height)
SHEET_SIZES_MM: dict = {
    "A4": (210.0, 297.0),
    "LETTER": (215.9, 279.4),
    "4X6": (101.6, 152.4),
}


@dataclass
class PhotoSheetOptions:
    """Configuration for physical photo/signature grid layout."""

    # Target photo dimensions in mm (or px)
    photo_width_mm: float = 35.0  # Standard 35mm
    photo_height_mm: float = 45.0  # Standard 45mm
    photo_width_px: Optional[int] = None
    photo_height_px: Optional[int] = None
    dpi: int = 300
    copies: int = 6
    margin_mm: float = 10.0
    spacing_mm: float = 5.0
    cut_guides: bool = True
    sheet_size: str = "A4"  # "A4", "LETTER", "4X6"
    custom_sheet_w_mm: Optional[float] = None
    custom_sheet_h_mm: Optional[float] = None
    allow_upscale: bool = False
    output_format: str = "PDF"  # "PDF", "PNG", "JPEG"
    bg_color: Tuple[int, int, int] = (255, 255, 255)
    is_signature: bool = False
    preserve_alpha: bool = True  # In signature mode for PNG
    crop_pos_x: float = 0.5      # 0.0 = Left, 0.5 = Center, 1.0 = Right
    crop_pos_y: float = 0.35     # 0.0 = Top/Head, 0.5 = Center, 1.0 = Bottom


def mm_to_pixels(mm: float, dpi: int) -> int:
    """Convert millimetres to pixels at target DPI."""
    return max(1, round(mm * (dpi / 25.4)))


def generate_photo_sheet(
    input_path: Path,
    output_path: Path,
    options: PhotoSheetOptions,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Lay out portrait photo or signature copies onto a printable sheet with cut guides.
    Exports to PDF, PNG, or JPEG.
    """
    result = JobResult(
        operation=OperationType.RESIZE,
        input_path=input_path,
    )
    protected = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))
    dest_path = resolve_collision_path(output_path, allow_overwrite=allow_overwrite)

    try:
        validate_destination_safety(dest_path, protected)
        validate_file_size(input_path)
        validate_dpi(options.dpi)

        # Enforce multi-frame safety
        check_single_frame_image(input_path, operation_name="Photo Sheet")

        if options.photo_width_mm <= 0 or options.photo_height_mm <= 0:
            raise ValueError("Photo dimensions in mm must be positive numbers.")
        if options.copies <= 0:
            raise ValueError("Copies must be a positive integer.")

        # Determine sheet dimensions in mm
        sheet_key = options.sheet_size.upper()
        if sheet_key in SHEET_SIZES_MM:
            sheet_w_mm, sheet_h_mm = SHEET_SIZES_MM[sheet_key]
        elif options.custom_sheet_w_mm and options.custom_sheet_h_mm:
            sheet_w_mm = options.custom_sheet_w_mm
            sheet_h_mm = options.custom_sheet_h_mm
        else:
            sheet_w_mm, sheet_h_mm = SHEET_SIZES_MM["A4"]

        sheet_w_px = mm_to_pixels(sheet_w_mm, options.dpi)
        sheet_h_px = mm_to_pixels(sheet_h_mm, options.dpi)

        if options.photo_width_px and options.photo_height_px:
            photo_w_px = options.photo_width_px
            photo_h_px = options.photo_height_px
            options.photo_width_mm = round(photo_w_px * 25.4 / options.dpi, 2)
            options.photo_height_mm = round(photo_h_px * 25.4 / options.dpi, 2)
        else:
            photo_w_px = mm_to_pixels(options.photo_width_mm, options.dpi)
            photo_h_px = mm_to_pixels(options.photo_height_mm, options.dpi)

        margin_px = mm_to_pixels(options.margin_mm, options.dpi)
        spacing_px = mm_to_pixels(options.spacing_mm, options.dpi)

        validate_dimensions(sheet_w_px, sheet_h_px, context="Print sheet")
        validate_dimensions(photo_w_px, photo_h_px, context="Single photo cell")

        with Image.open(input_path) as raw_img:
            validate_dimensions(raw_img.width, raw_img.height, context="Photo Sheet Source Image")
            transposed = ImageOps.exif_transpose(raw_img)
            img = transposed if transposed is not None else raw_img.copy()

            orig_w, orig_h = img.size
            result.input_dimensions = (orig_w, orig_h)

            needs_upscale = (photo_w_px > orig_w) or (photo_h_px > orig_h)
            if needs_upscale and not options.allow_upscale:
                raise ValueError(
                    f"Rendering {options.photo_width_mm}x{options.photo_height_mm} mm at {options.dpi} DPI "
                    f"requires {photo_w_px}x{photo_h_px} px, which requires upscaling from source {orig_w}x{orig_h} px. "
                    "Enable 'Allow deliberate upscaling' to proceed."
                )

            # Crop or fit source into photo cell
            if options.is_signature:
                # For signatures: scale proportionally to fit inside the cell bounds
                ratio = min(photo_w_px / orig_w, photo_h_px / orig_h)
                sig_w = max(1, round(orig_w * ratio))
                sig_h = max(1, round(orig_h * ratio))
                scaled_sig = img.resize((sig_w, sig_h), Image.Resampling.LANCZOS)

                # Paste centered in photo cell
                cell_mode = "RGBA" if (options.preserve_alpha and img.mode == "RGBA") else "RGB"
                cell_bg = options.bg_color + ((0,) if cell_mode == "RGBA" else ())
                cell_img = Image.new(cell_mode, (photo_w_px, photo_h_px), cell_bg)
                pos_x = (photo_w_px - sig_w) // 2
                pos_y = (photo_h_px - sig_h) // 2
                if scaled_sig.mode == "RGBA" and cell_mode == "RGBA":
                    cell_img.alpha_composite(scaled_sig, (pos_x, pos_y))
                else:
                    cell_img.paste(scaled_sig, (pos_x, pos_y))
            else:
                # For portrait photos: interactive crop positioning to exact aspect ratio, then resize
                ratio = max(photo_w_px / orig_w, photo_h_px / orig_h)
                src_crop_w = photo_w_px / ratio
                src_crop_h = photo_h_px / ratio
                max_offset_x = max(0.0, orig_w - src_crop_w)
                max_offset_y = max(0.0, orig_h - src_crop_h)
                left = max_offset_x * max(0.0, min(1.0, options.crop_pos_x))
                top = max_offset_y * max(0.0, min(1.0, options.crop_pos_y))
                right = left + src_crop_w
                bottom = top + src_crop_h

                cell_img = img.resize(
                    (photo_w_px, photo_h_px),
                    resample=Image.Resampling.LANCZOS,
                    box=(left, top, right, bottom),
                )
                if cell_img.mode in ("RGBA", "LA") or ("transparency" in cell_img.info):
                    cell_img = flatten_alpha_on_background(cell_img, options.bg_color)
                elif cell_img.mode != "RGB":
                    cell_img = cell_img.convert("RGB")

            # Create sheet canvas
            sheet_format = options.output_format.upper()
            if options.is_signature and options.preserve_alpha and sheet_format == "PNG":
                sheet_mode = "RGBA"
                sheet_bg = (255, 255, 255, 0)
            else:
                sheet_mode = "RGB"
                sheet_bg = (255, 255, 255)

            sheet_img = Image.new(sheet_mode, (sheet_w_px, sheet_h_px), sheet_bg)
            draw = ImageDraw.Draw(sheet_img)

            # Calculate columns and rows that fit on sheet
            avail_w = sheet_w_px - 2 * margin_px
            avail_h = sheet_h_px - 2 * margin_px
            if photo_w_px > avail_w or photo_h_px > avail_h or avail_w <= 0 or avail_h <= 0:
                cols = 0
                rows = 0
            else:
                cols = (avail_w + spacing_px) // (photo_w_px + spacing_px)
                rows = (avail_h + spacing_px) // (photo_h_px + spacing_px)

            max_copies_fit = cols * rows
            if cols <= 0 or rows <= 0 or max_copies_fit <= 0:
                raise ValueError(
                    f"Cannot fit requested photo size ({options.photo_width_mm}x{options.photo_height_mm} mm) "
                    f"on {options.sheet_size} page with {options.margin_mm} mm margins. "
                    f"Photo requires {photo_w_px}x{photo_h_px} px, available space is {avail_w}x{avail_h} px."
                )

            if options.copies > max_copies_fit:
                raise ValueError(
                    f"Cannot fit requested {options.copies} copies on {options.sheet_size} page with "
                    f"{options.margin_mm} mm margins and {options.spacing_mm} mm spacing. "
                    f"Maximum sheet capacity is {max_copies_fit} copies (grid: {cols}x{rows})."
                )

            placed_count = 0
            guide_color = (180, 180, 180, 255) if sheet_img.mode == "RGBA" else (180, 180, 180)

            for r in range(rows):
                for c in range(cols):
                    if placed_count >= options.copies:
                        break

                    x = margin_px + c * (photo_w_px + spacing_px)
                    y = margin_px + r * (photo_h_px + spacing_px)

                    if cell_img.mode == "RGBA" and sheet_img.mode == "RGBA":
                        sheet_img.alpha_composite(cell_img, (x, y))
                    elif cell_img.mode == "RGBA":
                        sheet_img.paste(cell_img, (x, y), cell_img)
                    else:
                        sheet_img.paste(cell_img, (x, y))

                    # Draw cut guides around cell if enabled
                    if options.cut_guides:
                        guide_len = min(15, spacing_px // 2) if spacing_px > 0 else 10
                        # Top-left corner
                        draw.line([(x - guide_len, y), (x, y)], fill=guide_color, width=1)
                        draw.line([(x, y - guide_len), (x, y)], fill=guide_color, width=1)
                        # Top-right corner
                        draw.line([(x + photo_w_px, y), (x + photo_w_px + guide_len, y)], fill=guide_color, width=1)
                        draw.line([(x + photo_w_px, y - guide_len), (x + photo_w_px, y)], fill=guide_color, width=1)
                        # Bottom-left corner
                        draw.line([(x - guide_len, y + photo_h_px), (x, y + photo_h_px)], fill=guide_color, width=1)
                        draw.line([(x, y + photo_h_px), (x, y + photo_h_px + guide_len)], fill=guide_color, width=1)
                        # Bottom-right corner
                        draw.line([(x + photo_w_px, y + photo_h_px), (x + photo_w_px + guide_len, y + photo_h_px)], fill=guide_color, width=1)
                        draw.line([(x + photo_w_px, y + photo_h_px), (x + photo_w_px, y + photo_h_px + guide_len)], fill=guide_color, width=1)

                    placed_count += 1

            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("Sheet generation cancelled by user.")

            if placed_count < options.copies:
                raise ValueError(
                    f"Cannot fit requested {options.copies} copies on {options.sheet_size} page "
                    f"(only {placed_count} fit in grid)."
                )

            staged = StagedOutput(dest_path, cancel_token=cancel_token)
            with staged as staged_path:
                fmt_upper = options.output_format.upper()
                if fmt_upper == "PDF":
                    # Convert to PDF page at exact physical resolution
                    scale_res = float(options.dpi)
                    sheet_img.save(staged_path, format="PDF", resolution=scale_res)
                elif fmt_upper in ("JPEG", "JPG"):
                    sheet_img.save(staged_path, format="JPEG", quality=95, dpi=(options.dpi, options.dpi))
                else:
                    sheet_img.save(staged_path, format="PNG", dpi=(options.dpi, options.dpi))
            staged.commit()

            result.output_path = dest_path
            result.output_dimensions = (sheet_w_px, sheet_h_px)
            result.output_size_bytes = dest_path.stat().st_size
            result.success = True
            result.checks.append(
                RequirementCheck(
                    name="Print Sheet Generation",
                    target=f"{options.copies} copies on {options.sheet_size} ({options.photo_width_mm}x{options.photo_height_mm} mm)",
                    actual=f"{placed_count} copies placed",
                    passed=True,
                    details=f"DPI: {options.dpi}, Margins: {options.margin_mm} mm, Cut Guides: {options.cut_guides}",
                )
            )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result

