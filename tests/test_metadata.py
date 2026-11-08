"""Tests for Metadata Inspection and Selective Scrubbing (Feature 3)."""

from pathlib import Path
from PIL import Image
import pypdf
import pytest

from upload_ready.core.metadata import (
    DetectedMetadata,
    inspect_metadata,
    scrub_metadata,
)


def test_inspect_and_scrub_image_exif(tmp_path: Path, temp_dir: Path):
    # Create image with EXIF orientation tag 6 (90 deg CCW)
    img_path = tmp_path / "photo_with_exif.jpg"
    img = Image.new("RGB", (100, 60), color=(200, 50, 50))
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation tag
    exif[0x010E] = "Test Camera Description"  # ImageDescription
    img.save(img_path, exif=exif)

    # 1. Inspect
    meta = inspect_metadata(img_path)
    assert meta.file_type == "JPEG"
    assert meta.has_exif is True
    assert meta.exif_tag_count >= 1

    # 2. Scrub metadata
    res = scrub_metadata(
        input_path=img_path,
        output_dir=temp_dir,
        categories={"exif"},
    )
    assert res.success is True
    assert res.output_path is not None
    assert res.output_path.exists()

    # 3. Verify removal by reopening
    with Image.open(res.output_path) as clean_img:
        clean_exif = clean_img.getexif()
        # EXIF description should be removed
        assert 0x010E not in clean_exif
        # Geometry should be normalized: tag 6 rotates 90 deg, so width and height are swapped (60, 100)
        assert clean_img.size == (60, 100)


def test_scrub_pdf_metadata(synthetic_multipage_pdf: Path, temp_dir: Path):
    res = scrub_metadata(
        input_path=synthetic_multipage_pdf,
        output_dir=temp_dir,
        categories={"pdf_docinfo", "pdf_xmp"},
    )
    assert res.success is True
    assert res.output_path is not None

    r = pypdf.PdfReader(res.output_path)
    # Check that DocInfo is empty or has no keys
    if r.metadata:
        assert len(r.metadata.keys()) == 0


def test_metadata_icc_profile_retention(tmp_path: Path, temp_dir: Path):
    from PIL import ImageCms
    profile = ImageCms.createProfile("sRGB")
    icc_bytes = ImageCms.ImageCmsProfile(profile).tobytes()
    
    img_path = tmp_path / "photo_with_icc.png"
    img = Image.new("RGB", (100, 100), color=(100, 150, 200))
    img.save(img_path, icc_profile=icc_bytes)

    res = scrub_metadata(
        input_path=img_path,
        output_dir=temp_dir,
        categories={"exif", "comments"},
    )
    assert res.success is True
    with Image.open(res.output_path) as out_img:
        assert "icc_profile" in out_img.info
