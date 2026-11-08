"""ID Front & Back single-page layout generator.

Combines front and back images of an identity card onto one printable page:
- Side-by-side or vertically stacked layouts.
- Standard CR80 card dimensions (85.6 x 54.0 mm) or custom sizing.
- Independent rotation and aspect-ratio proportional scaling.
- Source resolution check: emits honest warning if source DPI < 150 DPI.
- PDF, PNG, and JPEG export.
- Optional purpose watermark integration.
- Safe staging and temporary cleanup.
"""

import io
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageOps
import pypdf

from upload_ready.core.constants import MAX_DECODED_PIXELS, bytes_to_human
from upload_ready.core.models import (
    CancellationToken,
    JobResult,
    OperationCancelledError,
    OperationType,
    RequirementCheck,
)
from upload_ready.core.photo_sheet import SHEET_SIZES_MM, mm_to_pixels
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
from upload_ready.core.watermark import WatermarkOptions, create_watermark_overlay_image


@dataclass
class IdLayoutOptions:
    """Configuration for front/back ID layout on a single sheet."""

    layout_type: str = "stacked"  # "side_by_side" or "stacked"
    card_width_mm: float = 85.6  # Standard ID-1 / CR80 card width
    card_height_mm: float = 54.0  # Standard ID-1 / CR80 card height
    dpi: int = 300
    sheet_size: str = "A4"  # "A4", "LETTER"
    margin_mm: float = 10.0
    spacing_mm: float = 10.0
    front_rotation: int = 0  # 0, 90, 180, 270
    back_rotation: int = 0  # 0, 90, 180, 270
    front_crop_mode: str = "fit"  # "fit" or "crop"
    back_crop_mode: str = "fit"   # "fit" or "crop"
    output_format: str = "PDF"  # "PDF", "PNG", "JPEG"
    bg_color: Tuple[int, int, int] = (255, 255, 255)
    watermark: Optional[WatermarkOptions] = None


def generate_id_layout(
    front_path: Path,
    back_path: Path,
    output_path: Path,
    options: IdLayoutOptions,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Combine front and back card images into a single-page document.
    """
    result = JobResult(
        operation=OperationType.RESIZE,
        input_path=front_path,
    )
    protected = list(dict.fromkeys([front_path, back_path] + (list(batch_inputs) if batch_inputs is not None else [])))
    dest_path = resolve_collision_path(output_path, allow_overwrite=allow_overwrite)

    try:
        validate_destination_safety(dest_path, protected)
        validate_file_size(front_path)
        validate_file_size(back_path)
        validate_dpi(options.dpi)

        # Enforce multi-frame safety
        check_single_frame_image(front_path, operation_name="ID Card Layout")
        check_single_frame_image(back_path, operation_name="ID Card Layout")

        sheet_key = options.sheet_size.upper()
        sheet_w_mm, sheet_h_mm = SHEET_SIZES_MM.get(sheet_key, SHEET_SIZES_MM["A4"])

        sheet_w_px = mm_to_pixels(sheet_w_mm, options.dpi)
        sheet_h_px = mm_to_pixels(sheet_h_mm, options.dpi)
        card_w_px = mm_to_pixels(options.card_width_mm, options.dpi)
        card_h_px = mm_to_pixels(options.card_height_mm, options.dpi)
        margin_px = mm_to_pixels(options.margin_mm, options.dpi)
        spacing_px = mm_to_pixels(options.spacing_mm, options.dpi)

        validate_dimensions(sheet_w_px, sheet_h_px, context="ID Sheet")
        validate_dimensions(card_w_px, card_h_px, context="ID Card Slot")

        # Layout geometry validation: ensure cards and margins physically fit on the sheet
        if options.layout_type == "side_by_side":
            total_cards_w = 2 * card_w_px + spacing_px
            total_cards_h = card_h_px
        else:
            total_cards_w = card_w_px
            total_cards_h = 2 * card_h_px + spacing_px

        min_req_w = total_cards_w + 2 * margin_px
        min_req_h = total_cards_h + 2 * margin_px
        if min_req_w > sheet_w_px or min_req_h > sheet_h_px:
            raise ValueError(
                f"Cannot fit requested ID card layout on {options.sheet_size} page. "
                f"Required physical dimensions: {min_req_w}x{min_req_h} px "
                f"(including {options.margin_mm} mm margins and {options.spacing_mm} mm spacing), "
                f"sheet canvas size: {sheet_w_px}x{sheet_h_px} px."
            )

        # Process front and back images
        processed_cards: List[Image.Image] = []
        for p, rot, label in [(front_path, options.front_rotation, "Front"), (back_path, options.back_rotation, "Back")]:
            with Image.open(p) as raw_img:
                validate_dimensions(raw_img.width, raw_img.height, context=f"{label} Card Image")
                transposed = ImageOps.exif_transpose(raw_img)
                img = transposed if transposed is not None else raw_img.copy()

                rot_deg = rot % 360
                if rot_deg != 0:
                    img = img.rotate(-rot_deg, expand=True)

                ow, oh = img.size

                # Check effective DPI of source image
                eff_dpi_w = ow / (options.card_width_mm / 25.4)
                eff_dpi_h = oh / (options.card_height_mm / 25.4)
                min_eff_dpi = min(eff_dpi_w, eff_dpi_h)
                if min_eff_dpi < 150.0:
                    result.warnings.append(
                        f"{label} image resolution is low ({ow}x{oh} px, ~{int(min_eff_dpi)} DPI). "
                        "May appear pixelated or blurry when printed."
                    )

                crop_mode = options.front_crop_mode if label == "Front" else options.back_crop_mode
                if crop_mode == "crop":
                    # Fill card slot and center crop
                    ratio = max(card_w_px / ow, card_h_px / oh)
                    src_w = card_w_px / ratio
                    src_h = card_h_px / ratio
                    left = (ow - src_w) / 2.0
                    top = (oh - src_h) / 2.0
                    scaled = img.resize((card_w_px, card_h_px), Image.Resampling.LANCZOS, box=(left, top, left + src_w, top + src_h))
                    cw, ch = card_w_px, card_h_px
                else:
                    # Scale proportionally to fit within card_w_px x card_h_px without stretching
                    ratio = min(card_w_px / ow, card_h_px / oh)
                    cw = max(1, round(ow * ratio))
                    ch = max(1, round(oh * ratio))
                    scaled = img.resize((cw, ch), Image.Resampling.LANCZOS)

                if scaled.mode != "RGB":
                    scaled = scaled.convert("RGB")

                # Center on card slot
                slot = Image.new("RGB", (card_w_px, card_h_px), (255, 255, 255))
                slot.paste(scaled, ((card_w_px - cw) // 2, (card_h_px - ch) // 2))

                # Draw subtle border around card boundary
                slot_draw = ImageDraw.Draw(slot)
                slot_draw.rectangle([0, 0, card_w_px - 1, card_h_px - 1], outline=(200, 200, 200), width=1)

                processed_cards.append(slot)

        # Create sheet
        sheet_img = Image.new("RGB", (sheet_w_px, sheet_h_px), (255, 255, 255))
        sheet_draw = ImageDraw.Draw(sheet_img)

        front_card, back_card = processed_cards[0], processed_cards[1]

        avail_w = sheet_w_px - 2 * margin_px
        avail_h = sheet_h_px - 2 * margin_px

        if options.layout_type == "side_by_side":
            # Place side by side horizontally constrained within margins
            start_x = margin_px + (avail_w - total_cards_w) // 2
            start_y = margin_px + (avail_h - total_cards_h) // 2

            sheet_img.paste(front_card, (start_x, start_y))
            sheet_img.paste(back_card, (start_x + card_w_px + spacing_px, start_y))
        else:
            # Stack vertically (default) constrained within margins
            start_x = margin_px + (avail_w - total_cards_w) // 2
            start_y = margin_px + (avail_h - total_cards_h) // 2

            sheet_img.paste(front_card, (start_x, start_y))
            sheet_img.paste(back_card, (start_x, start_y + card_h_px + spacing_px))

        # Apply purpose watermark if requested
        if options.watermark and options.watermark.text:
            wm_overlay = create_watermark_overlay_image(sheet_w_px, sheet_h_px, options.watermark)
            sheet_rgba = sheet_img.convert("RGBA")
            sheet_img = Image.alpha_composite(sheet_rgba, wm_overlay).convert("RGB")

        if cancel_token and cancel_token.is_cancelled:
            raise OperationCancelledError("ID layout cancelled by user.")

        staged = StagedOutput(dest_path, cancel_token=cancel_token)
        with staged as staged_path:
            fmt_upper = options.output_format.upper()
            if fmt_upper == "PDF":
                sheet_img.save(staged_path, format="PDF", resolution=float(options.dpi))
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
                name="ID Layout Generation",
                target=f"Front & Back on {options.sheet_size} ({options.layout_type})",
                actual="Assembled successfully",
                passed=True,
                details=f"Card size: {options.card_width_mm}x{options.card_height_mm} mm @ {options.dpi} DPI",
            )
        )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result

