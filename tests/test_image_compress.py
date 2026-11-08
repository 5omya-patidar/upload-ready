"""Tests for image compression, lossless pixel verification, and target size search."""

import io
from pathlib import Path
from PIL import Image, ImageCms

from upload_ready.core.constants import UnitStandard
from upload_ready.core.image_compress import (
    are_images_pixel_identical,
    compress_image,
    verify_lossless_pixels,
)
from upload_ready.core.models import CompressOptions


def test_rgba_different_rgb_identical_alpha_detected():
    """
    Regression test: verify that two RGBA images with identical alpha (255)
    but different RGB color values are correctly detected as NOT identical.
    (Guards against ImageChops.difference().getbbox() returning None on RGBA images).
    """
    img1 = Image.new("RGBA", (20, 20), (100, 150, 200, 255))
    img2 = Image.new("RGBA", (20, 20), (105, 150, 200, 255))  # Red channel differs by 5
    assert are_images_pixel_identical(img1, img2) is False

    # Also test with fully transparent pixels (alpha=0) having different RGB
    img3 = Image.new("RGBA", (20, 20), (10, 20, 30, 0))
    img4 = Image.new("RGBA", (20, 20), (99, 20, 30, 0))
    assert are_images_pixel_identical(img3, img4) is False


def test_lossless_modes_fidelity(tmp_path: Path):
    """Test lossless equality across RGB, RGBA, and Grayscale (L) inputs."""
    # RGB
    rgb = Image.new("RGB", (30, 30), (50, 100, 150))
    rgb_same = Image.new("RGB", (30, 30), (50, 100, 150))
    rgb_diff = Image.new("RGB", (30, 30), (50, 100, 151))
    assert are_images_pixel_identical(rgb, rgb_same) is True
    assert are_images_pixel_identical(rgb, rgb_diff) is False

    # RGBA
    rgba = Image.new("RGBA", (30, 30), (50, 100, 150, 200))
    rgba_same = Image.new("RGBA", (30, 30), (50, 100, 150, 200))
    rgba_diff = Image.new("RGBA", (30, 30), (50, 100, 150, 201))
    assert are_images_pixel_identical(rgba, rgba_same) is True
    assert are_images_pixel_identical(rgba, rgba_diff) is False

    # Grayscale (L)
    gray = Image.new("L", (30, 30), 128)
    gray_same = Image.new("L", (30, 30), 128)
    gray_diff = Image.new("L", (30, 30), 129)
    assert are_images_pixel_identical(gray, gray_same) is True
    assert are_images_pixel_identical(gray, gray_diff) is False


def test_lossless_png_preserves_every_pixel(synthetic_rgb_png, temp_dir):
    options = CompressOptions(is_lossless=True)
    result = compress_image(synthetic_rgb_png, temp_dir, options)
    
    assert result.success is True
    assert result.output_path.exists()
    
    # Strictly verify decoded pixels are identical across all channels
    with Image.open(synthetic_rgb_png) as orig_img, Image.open(result.output_path) as out_img:
        assert are_images_pixel_identical(orig_img, out_img) is True


def test_lossless_jpeg_refusal_honesty(synthetic_jpeg_with_exif, temp_dir):
    """JPEG re-encoding is lossy. Verify Upload Ready refuses to falsely label JPEG recompression lossless."""
    options = CompressOptions(is_lossless=True)
    result = compress_image(synthetic_jpeg_with_exif, temp_dir, options)
    
    assert result.success is False
    assert "Lossless optimization is not available for JPEG" in result.error_message


def test_icc_profile_preservation(tmp_path: Path, temp_dir: Path):
    """Verify embedded ICC color profiles are retained in encoded output."""
    img_path = tmp_path / "with_profile.png"
    srgb_profile = ImageCms.createProfile("sRGB")
    icc_bytes = ImageCms.ImageCmsProfile(srgb_profile).tobytes()
    
    img = Image.new("RGB", (40, 40), (200, 100, 50))
    img.save(img_path, format="PNG", icc_profile=icc_bytes)
    
    options = CompressOptions(is_lossless=True)
    result = compress_image(img_path, temp_dir, options)
    
    assert result.success is True
    with Image.open(result.output_path) as out_img:
        assert "icc_profile" in out_img.info
        assert len(out_img.info["icc_profile"]) > 0


def test_target_max_size_achievable(tmp_path: Path, temp_dir: Path):
    # Create realistic test image (400x300 gradient)
    img_path = tmp_path / "gradient.jpg"
    img = Image.new("RGB", (400, 300))
    for x in range(400):
        for y in range(300):
            img.putpixel((x, y), ((x * 3) % 256, (y * 4) % 256, ((x + y) * 2) % 256))
    img.save(img_path, format="JPEG", quality=95)

    options = CompressOptions(
        max_bytes=15000,
        unit_standard=UnitStandard.DECIMAL,
    )
    result = compress_image(img_path, temp_dir, options)
    
    assert result.success is True
    actual_size = result.output_path.stat().st_size
    assert actual_size <= 15000
    assert result.checks[0].passed is True


def test_target_size_range_min_max_achievable(tmp_path: Path, temp_dir: Path):
    # Create realistic test image (400x300 gradient)
    img_path = tmp_path / "gradient_range.jpg"
    img = Image.new("RGB", (400, 300))
    for x in range(400):
        for y in range(300):
            img.putpixel((x, y), ((x * 2) % 256, (y * 3) % 256, (x % 256)))
    img.save(img_path, format="JPEG", quality=95)

    # Set achievable range 8 KB to 20 KB
    options = CompressOptions(
        min_bytes=8000,
        max_bytes=20000,
        unit_standard=UnitStandard.DECIMAL,
    )
    result = compress_image(img_path, temp_dir, options)
    
    assert result.success is True
    actual_size = result.output_path.stat().st_size
    assert 8000 <= actual_size <= 20000
    assert result.checks[0].passed is True


def test_target_size_below_minimum_reports_unmet_honestly(synthetic_rgb_png, temp_dir):
    # synthetic_rgb_png is small (~600 B). Requesting minimum 50,000 B cannot be met without fake padding.
    options = CompressOptions(
        min_bytes=50000,
        max_bytes=60000,
        unit_standard=UnitStandard.DECIMAL,
    )
    result = compress_image(synthetic_rgb_png, temp_dir, options)
    
    # Must report unmet honestly
    assert result.success is False
    assert any("Target size not reached with the selected settings" in w for w in result.warnings)
    assert result.checks[0].passed is False


def test_impossible_target_range_raises_error(synthetic_rgb_png, temp_dir):
    # min_bytes > max_bytes
    options = CompressOptions(
        min_bytes=50000,
        max_bytes=10000,
    )
    result = compress_image(synthetic_rgb_png, temp_dir, options)
    assert result.success is False
    assert "cannot exceed maximum" in result.error_message
