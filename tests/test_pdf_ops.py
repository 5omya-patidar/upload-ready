"""Tests for PDF creation, extraction, password handling, limits, and size reduction."""

import io
import zipfile
from pathlib import Path
from PIL import Image
import pypdf
import pytest

from upload_ready.core.models import (
    CancellationToken,
    ImagesToPdfOptions,
    PdfReduceOptions,
    PdfToImagesOptions,
)
from upload_ready.core.pdf_ops import (
    PdfPasswordRequiredError,
    images_to_pdf,
    parse_page_range,
    pdf_to_images,
    reduce_pdf_size,
)


def test_parse_page_range():
    assert parse_page_range("all", 5) == [0, 1, 2, 3, 4]
    assert parse_page_range("1", 5) == [0]
    assert parse_page_range("1, 3-4", 5) == [0, 2, 3]
    with pytest.raises(ValueError):
        parse_page_range("10", 5)


def test_images_to_pdf_ordering_and_page_count(synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    out_pdf = temp_dir / "combined.pdf"
    inputs = [synthetic_rgb_png, synthetic_rgba_png]
    options = ImagesToPdfOptions(page_size="A4", margin_pt=18.0)
    
    result = images_to_pdf(inputs, out_pdf, options)
    assert result.success is True
    assert out_pdf.exists()
    
    reader = pypdf.PdfReader(out_pdf)
    assert len(reader.pages) == 2


def test_images_to_pdf_cancellation_cleans_partial_file(synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    out_pdf = temp_dir / "cancelled.pdf"
    token = CancellationToken()
    token.cancel()  # Pre-cancelled
    options = ImagesToPdfOptions(page_size="A4")
    
    result = images_to_pdf([synthetic_rgb_png, synthetic_rgba_png], out_pdf, options, cancel_token=token)
    assert result.success is False
    assert not out_pdf.exists(), "Aborted PDF creation left a partial file on disk!"


def test_pdf_to_images_page_rendering(synthetic_multipage_pdf, temp_dir):
    # Render page 1 and 3 to PNG
    options = PdfToImagesOptions(page_range="1, 3", target_format="PNG", dpi=100)
    result = pdf_to_images(synthetic_multipage_pdf, temp_dir, options)
    
    assert result.success is True
    assert result.details["rendered_count"] == 2
    
    # Check rendered files
    files = list(temp_dir.glob("*.png"))
    assert len(files) == 2


def test_pdf_to_images_zip_export(synthetic_multipage_pdf, temp_dir):
    options = PdfToImagesOptions(page_range="all", target_format="PNG", dpi=72, as_zip=True)
    result = pdf_to_images(synthetic_multipage_pdf, temp_dir, options)
    
    assert result.success is True
    assert result.output_path.suffix.lower() == ".zip"
    
    with zipfile.ZipFile(result.output_path, "r") as zf:
        names = zf.namelist()
        assert len(names) == 3


def test_pdf_to_images_render_pixel_limit_exceeded(tmp_path: Path, temp_dir: Path):
    """Verify that rendering an oversized PDF page rejects before allocating excessive memory."""
    huge_pdf = tmp_path / "huge_page.pdf"
    writer = pypdf.PdfWriter()
    buf = io.BytesIO()
    # 6000x6000 pt page
    Image.new("RGB", (100, 100), (0, 0, 0)).save(buf, format="PDF", resolution=1.0)
    buf.seek(0)
    reader = pypdf.PdfReader(buf)
    page = reader.pages[0]
    page.mediabox.upper_right = (8000, 8000)  # 8000x8000 points
    writer.add_page(page)
    with open(huge_pdf, "wb") as f:
        writer.write(f)

    # Rendering at 300 DPI would yield (8000 * 300 / 72)^2 = 1.1 billion pixels (> 100M limit)
    options = PdfToImagesOptions(dpi=300)
    res = pdf_to_images(huge_pdf, temp_dir, options)
    assert res.success is False
    assert "exceeds safety limit" in res.error_message


def test_pdf_to_images_cancellation_cleans_partial_files(synthetic_multipage_pdf, temp_dir):
    """Verify cancelling multi-page render leaves 0 orphaned output files."""
    token = CancellationToken()
    token.cancel()
    options = PdfToImagesOptions(page_range="all", target_format="PNG")
    res = pdf_to_images(synthetic_multipage_pdf, temp_dir, options, cancel_token=token)
    
    assert res.success is False
    png_files = list(temp_dir.glob("*.png"))
    assert len(png_files) == 0, f"Found {len(png_files)} partial output files after cancellation!"


def test_encrypted_pdf_password_handling(synthetic_encrypted_pdf, temp_dir):
    # Attempt without password -> fails with clear password-required message
    options_no_pass = PdfToImagesOptions(password=None)
    res_no_pass = pdf_to_images(synthetic_encrypted_pdf, temp_dir, options_no_pass)
    assert res_no_pass.success is False
    assert "password protected" in res_no_pass.error_message.lower()
        
    # Attempt with correct password -> succeeds
    options_with_pass = PdfToImagesOptions(password="testpass123")
    res = pdf_to_images(synthetic_encrypted_pdf, temp_dir, options_with_pass)
    assert res.success is True


def test_pdf_size_reduction_structural(synthetic_multipage_pdf, temp_dir):
    out_pdf = temp_dir / "reduced.pdf"
    options = PdfReduceOptions(structural_only=True, remove_metadata=True)
    result = reduce_pdf_size(synthetic_multipage_pdf, out_pdf, options)
    
    assert result.success is True
    assert out_pdf.exists()
    reader = pypdf.PdfReader(out_pdf)
    assert len(reader.pages) == 3
