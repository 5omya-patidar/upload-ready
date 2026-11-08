"""Tests for CLI commands and parity with core operations."""

import json
from pathlib import Path
from PIL import Image
from upload_ready.cli import main


def test_cli_convert(synthetic_rgb_png, temp_dir):
    code = main([
        "convert",
        str(synthetic_rgb_png),
        "-f", "webp",
        "-o", str(temp_dir),
        "--json"
    ])
    assert code == 0
    assert (temp_dir / f"{synthetic_rgb_png.stem}.webp").exists()


def test_cli_convert_multi_frame_refusal_and_override(tmp_path: Path, temp_dir: Path):
    gif_path = tmp_path / "cli_animated.gif"
    f1 = Image.new("RGB", (30, 30), (255, 0, 0))
    f2 = Image.new("RGB", (30, 30), (0, 0, 255))
    f1.save(gif_path, save_all=True, append_images=[f2])

    # 1. Attempt without --allow-first-frame-only -> must fail (non-zero exit)
    code_refuse = main([
        "convert",
        str(gif_path),
        "-f", "jpeg",
        "-o", str(temp_dir),
    ])
    assert code_refuse != 0

    # 2. Attempt with --allow-first-frame-only -> must succeed
    code_allow = main([
        "convert",
        str(gif_path),
        "-f", "jpeg",
        "-o", str(temp_dir),
        "--allow-first-frame-only",
        "--overwrite"
    ])
    assert code_allow == 0


def test_cli_resize(synthetic_rgb_png, temp_dir):
    code = main([
        "resize",
        str(synthetic_rgb_png),
        "--width", "80",
        "--height", "60",
        "--mode", "fit",
        "-o", str(temp_dir),
        "--overwrite"
    ])
    assert code == 0


def test_cli_compress(synthetic_rgb_png, temp_dir):
    code = main([
        "compress",
        str(synthetic_rgb_png),
        "--lossless",
        "-o", str(temp_dir),
        "--overwrite"
    ])
    assert code == 0


def test_cli_images_to_pdf(synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    out_pdf = temp_dir / "cli_combined.pdf"
    code = main([
        "images-to-pdf",
        str(synthetic_rgb_png),
        str(synthetic_rgba_png),
        "-o", str(out_pdf),
        "--page-size", "A4"
    ])
    assert code == 0
    assert out_pdf.exists()


def test_cli_pdf_to_images(synthetic_multipage_pdf, temp_dir):
    code = main([
        "pdf-to-images",
        str(synthetic_multipage_pdf),
        "-o", str(temp_dir),
        "--pages", "1-2",
        "--format", "png",
        "--dpi", "72"
    ])
    assert code == 0


def test_cli_pdf_reduce(synthetic_multipage_pdf, temp_dir):
    out_pdf = temp_dir / "cli_reduced.pdf"
    code = main([
        "pdf-reduce",
        str(synthetic_multipage_pdf),
        "-o", str(out_pdf)
    ])
    assert code == 0
    assert out_pdf.exists()


def test_cli_preset(synthetic_rgb_png, temp_dir):
    # preset list
    assert main(["preset", "list", "--json"]) == 0

    # preset show
    assert main(["preset", "show", "web_optimized_jpeg"]) == 0

    # preset apply
    code = main([
        "preset",
        "apply",
        str(synthetic_rgb_png),
        "--preset", "web_optimized_jpeg",
        "-o", str(temp_dir),
        "--allow-upscale",
        "--overwrite"
    ])
    assert code == 0


def test_cli_pdf_organize(synthetic_multipage_pdf, temp_dir):
    out_merge = temp_dir / "cli_merged.pdf"
    code_merge = main([
        "pdf-organize",
        "merge",
        str(synthetic_multipage_pdf),
        str(synthetic_multipage_pdf),
        "-o", str(out_merge),
        "--overwrite"
    ])
    assert code_merge == 0
    assert out_merge.exists()

    out_rot = temp_dir / "cli_rotated.pdf"
    code_rot = main([
        "pdf-organize",
        "rotate",
        str(synthetic_multipage_pdf),
        "-o", str(out_rot),
        "--angle", "90",
        "--pages", "1",
        "--overwrite"
    ])
    assert code_rot == 0
    assert out_rot.exists()


def test_cli_metadata(synthetic_rgb_png, temp_dir):
    # inspect
    assert main(["metadata", "inspect", str(synthetic_rgb_png), "--json"]) == 0

    # scrub
    code_scrub = main([
        "metadata",
        "scrub",
        str(synthetic_rgb_png),
        "-o", str(temp_dir),
        "--overwrite"
    ])
    assert code_scrub == 0


def test_cli_photo_sheet(synthetic_rgb_png, temp_dir):
    out_sheet = temp_dir / "cli_sheet.pdf"
    code = main([
        "photo-sheet",
        str(synthetic_rgb_png),
        "-o", str(out_sheet),
        "--photo-width", "35",
        "--photo-height", "45",
        "--copies", "4",
        "--sheet-size", "A4",
        "--format", "pdf",
        "--allow-upscale",
        "--overwrite"
    ])
    assert code == 0
    assert out_sheet.exists()


def test_cli_watermark(synthetic_rgb_png, temp_dir):
    code = main([
        "watermark",
        str(synthetic_rgb_png),
        "-o", str(temp_dir),
        "--text", "SPECIMEN",
        "--opacity", "0.3",
        "--overwrite"
    ])
    assert code == 0


def test_cli_id_layout(synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    out_id = temp_dir / "cli_id.pdf"
    code = main([
        "id-layout",
        "--front", str(synthetic_rgb_png),
        "--back", str(synthetic_rgba_png),
        "-o", str(out_id),
        "--layout", "stacked",
        "--sheet-size", "A4",
        "--format", "pdf",
        "--overwrite"
    ])
    assert code == 0
    assert out_id.exists()

