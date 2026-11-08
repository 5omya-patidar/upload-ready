"""Tests for Purpose Watermarking (Feature 5)."""

from pathlib import Path
from PIL import Image
import pypdf
import pytest

from upload_ready.core.watermark import (
    WatermarkOptions,
    apply_watermark_to_image,
    apply_watermark_to_pdf,
    create_watermark_overlay_image,
)


def test_create_watermark_overlay_image():
    options = WatermarkOptions(
        text="SPECIMEN ONLY",
        font_size=30,
        opacity=0.5,
        angle=30.0,
        position="center",
    )
    overlay = create_watermark_overlay_image(400, 300, options)
    assert overlay.size == (400, 300)
    assert overlay.mode == "RGBA"
    # Transparent canvas with non-zero alpha pixels where text was drawn
    alpha_channel = overlay.split()[3]
    bbox = alpha_channel.getbbox()
    assert bbox is not None


def test_apply_watermark_to_image(synthetic_rgb_png: Path, temp_dir: Path):
    options = WatermarkOptions(
        text="FOR VERIFICATION ONLY",
        opacity=0.4,
        position="center",
    )
    res = apply_watermark_to_image(synthetic_rgb_png, temp_dir, options)
    assert res.success is True
    assert res.output_path is not None
    assert res.output_path.exists()

    with Image.open(res.output_path) as out_img:
        assert out_img.size == (200, 150)


def test_apply_watermark_to_pdf_preserves_vector_text(synthetic_multipage_pdf: Path, temp_dir: Path):
    out_pdf = temp_dir / "watermarked.pdf"
    options = WatermarkOptions(
        text="FOR TENANCY APPLICATION ONLY",
        opacity=0.3,
        position="center",
    )
    res = apply_watermark_to_pdf(synthetic_multipage_pdf, out_pdf, options)
    assert res.success is True
    assert out_pdf.exists()

    # Verify searchable text remains extractable
    r_orig = pypdf.PdfReader(synthetic_multipage_pdf)
    r_wm = pypdf.PdfReader(out_pdf)
    assert len(r_wm.pages) == len(r_orig.pages)

    orig_text = r_orig.pages[0].extract_text()
    wm_text = r_wm.pages[0].extract_text()
    assert orig_text in wm_text
