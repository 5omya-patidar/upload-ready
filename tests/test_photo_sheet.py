"""Tests for Passport Photo & Signature Sheet generation (Feature 4)."""

from pathlib import Path
from PIL import Image
import pypdf
import pytest

from upload_ready.core.photo_sheet import (
    PhotoSheetOptions,
    generate_photo_sheet,
    mm_to_pixels,
)
from upload_ready.core.safety import ResourceLimitExceededError


def test_mm_to_pixels():
    # 25.4 mm = 1 inch = 300 px at 300 DPI
    px = mm_to_pixels(25.4, 300)
    assert px == 300

    # 35 mm at 300 DPI: round(35 * 300 / 25.4) = 413 px
    px35 = mm_to_pixels(35.0, 300)
    assert px35 == 413


def test_generate_photo_sheet_pdf(synthetic_rgb_png: Path, temp_dir: Path):
    out_pdf = temp_dir / "passport_photos.pdf"
    options = PhotoSheetOptions(
        photo_width_mm=35.0,
        photo_height_mm=45.0,
        dpi=300,
        copies=6,
        sheet_size="A4",
        output_format="PDF",
        cut_guides=True,
        allow_upscale=True,
    )
    res = generate_photo_sheet(synthetic_rgb_png, out_pdf, options)
    assert res.success is True
    assert out_pdf.exists()

    r = pypdf.PdfReader(out_pdf)
    assert len(r.pages) == 1


def test_generate_photo_sheet_image_and_signature_alpha(synthetic_rgba_png: Path, temp_dir: Path):
    out_png = temp_dir / "signature_sheet.png"
    options = PhotoSheetOptions(
        photo_width_mm=50.0,
        photo_height_mm=25.0,
        dpi=150,
        copies=4,
        sheet_size="4X6",
        output_format="PNG",
        is_signature=True,
        preserve_alpha=True,
        cut_guides=True,
        allow_upscale=True,
    )
    res = generate_photo_sheet(synthetic_rgba_png, out_png, options)
    assert res.success is True
    assert out_png.exists()

    with Image.open(out_png) as img:
        assert img.format == "PNG"
        # In signature mode with preserve_alpha=True, RGBA mode should be preserved
        assert img.mode == "RGBA"


def test_photo_sheet_overflow_error(synthetic_rgb_png: Path, temp_dir: Path):
    out_pdf = temp_dir / "overflow.pdf"
    # Requesting 50 copies of 100x100mm photos cannot fit on A4
    options = PhotoSheetOptions(
        photo_width_mm=100.0,
        photo_height_mm=100.0,
        copies=50,
        sheet_size="A4",
        allow_upscale=True,
    )
    res = generate_photo_sheet(synthetic_rgb_png, out_pdf, options)
    assert res.success is False
    assert "Cannot fit" in (res.error_message or "")
