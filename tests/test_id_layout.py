"""Tests for ID Front & Back single-page layout (Feature 6)."""

from pathlib import Path
from PIL import Image
import pypdf
import pytest

from upload_ready.core.id_layout import IdLayoutOptions, generate_id_layout
from upload_ready.core.watermark import WatermarkOptions


def test_id_layout_stacked_pdf(synthetic_rgb_png: Path, synthetic_rgba_png: Path, temp_dir: Path):
    out_pdf = temp_dir / "id_card_stacked.pdf"
    options = IdLayoutOptions(
        layout_type="stacked",
        sheet_size="A4",
        dpi=300,
        output_format="PDF",
    )
    res = generate_id_layout(
        front_path=synthetic_rgb_png,
        back_path=synthetic_rgba_png,
        output_path=out_pdf,
        options=options,
    )
    assert res.success is True
    assert out_pdf.exists()

    r = pypdf.PdfReader(out_pdf)
    assert len(r.pages) == 1


def test_id_layout_side_by_side_png_with_watermark(synthetic_rgb_png: Path, synthetic_rgba_png: Path, temp_dir: Path):
    out_png = temp_dir / "id_side_by_side.png"
    wm = WatermarkOptions(text="OFFICIAL USE ONLY", opacity=0.3)
    options = IdLayoutOptions(
        layout_type="side_by_side",
        sheet_size="A4",
        dpi=150,
        output_format="PNG",
        watermark=wm,
    )
    res = generate_id_layout(
        front_path=synthetic_rgb_png,
        back_path=synthetic_rgba_png,
        output_path=out_png,
        options=options,
    )
    assert res.success is True
    assert out_png.exists()

    with Image.open(out_png) as img:
        assert img.format == "PNG"


def test_id_layout_low_dpi_honest_warning(tmp_path: Path, temp_dir: Path):
    # Create very low resolution front/back images (e.g. 50x30 pixels)
    front = tmp_path / "low_res_front.png"
    back = tmp_path / "low_res_back.png"
    Image.new("RGB", (50, 30), color=(100, 100, 100)).save(front)
    Image.new("RGB", (50, 30), color=(100, 100, 100)).save(back)

    out_pdf = temp_dir / "low_res_id.pdf"
    options = IdLayoutOptions(
        dpi=300,
        card_width_mm=85.6,
        card_height_mm=54.0,
    )
    res = generate_id_layout(front, back, out_pdf, options)
    assert res.success is True
    # Should report warning about source DPI < 150 DPI
    warnings_str = " ".join(res.warnings)
    assert "DPI" in warnings_str

