"""Tests for image conversion, EXIF orientation correction, transparency, and multi-frame guards."""

from pathlib import Path
from PIL import Image, ImageCms
import pytest

from upload_ready.core.image_convert import convert_image
from upload_ready.core.models import ConvertOptions


def test_convert_png_to_jpeg_with_alpha_flattening(synthetic_rgba_png, temp_dir):
    # Convert RGBA PNG to JPEG with custom green background (0, 255, 0)
    options = ConvertOptions(target_format="JPEG", bg_color=(0, 255, 0), quality=90)
    result = convert_image(synthetic_rgba_png, temp_dir, options)
    
    assert result.success is True
    assert result.output_path.suffix.lower() in (".jpg", ".jpeg")
    
    with Image.open(result.output_path) as out_img:
        assert out_img.format == "JPEG"
        assert out_img.mode == "RGB"
        # Check corner pixel that was transparent in source: should now be the green background
        # Allow +-2 tolerance for lossy DCT quantization in JPEG
        corner_pixel = out_img.getpixel((5, 5))
        assert all(abs(a - b) <= 2 for a, b in zip(corner_pixel, (0, 255, 0)))


def test_convert_png_to_webp_preserves_alpha(synthetic_rgba_png, temp_dir):
    options = ConvertOptions(target_format="WEBP", lossless=True)
    result = convert_image(synthetic_rgba_png, temp_dir, options)
    
    assert result.success is True
    assert result.output_path.suffix.lower() == ".webp"
    
    with Image.open(result.output_path) as out_img:
        assert out_img.format == "WEBP"
        assert out_img.mode == "RGBA"
        # Check transparent corner
        assert out_img.getpixel((5, 5))[3] == 0


def test_exif_orientation_correction(synthetic_jpeg_with_exif, temp_dir):
    # Input is 120x60 with EXIF tag 6 (rotated 90 CW)
    # Correct orientation should result in 60x120
    options = ConvertOptions(target_format="PNG", lossless=True)
    result = convert_image(synthetic_jpeg_with_exif, temp_dir, options)
    
    assert result.success is True
    with Image.open(result.output_path) as out_img:
        assert out_img.size == (60, 120)


def test_lossy_to_lossless_warning(synthetic_jpeg_with_exif, temp_dir):
    options = ConvertOptions(target_format="PNG", lossless=True)
    result = convert_image(synthetic_jpeg_with_exif, temp_dir, options)
    
    # Must warn user that converting JPEG to PNG does not restore lost detail
    assert any("does not restore" in w for w in result.warnings)


def test_multi_frame_input_refused_without_explicit_authorization(tmp_path: Path, temp_dir: Path):
    """Multi-frame / animated inputs must not silently drop frames without explicit permission."""
    gif_path = tmp_path / "animated.gif"
    frame1 = Image.new("RGB", (50, 50), (255, 0, 0))
    frame2 = Image.new("RGB", (50, 50), (0, 255, 0))
    frame1.save(gif_path, save_all=True, append_images=[frame2])

    # Attempt without explicit authorization -> must fail
    options_refuse = ConvertOptions(target_format="JPEG", allow_first_frame_only=False)
    res_refuse = convert_image(gif_path, temp_dir, options_refuse)
    assert res_refuse.success is False
    assert "Multi-frame input detected" in res_refuse.error_message

    # Attempt with explicit authorization -> succeeds with warning
    options_allow = ConvertOptions(target_format="JPEG", allow_first_frame_only=True)
    res_allow = convert_image(gif_path, temp_dir, options_allow)
    assert res_allow.success is True
    assert any("Exported only the first frame" in w for w in res_allow.warnings)


def test_convert_preserves_icc_profile(tmp_path: Path, temp_dir: Path):
    img_path = tmp_path / "profile_convert.png"
    srgb_profile = ImageCms.createProfile("sRGB")
    icc_bytes = ImageCms.ImageCmsProfile(srgb_profile).tobytes()
    
    img = Image.new("RGB", (30, 30), (120, 180, 240))
    img.save(img_path, format="PNG", icc_profile=icc_bytes)
    
    options = ConvertOptions(target_format="WEBP", lossless=True)
    res = convert_image(img_path, temp_dir, options)
    assert res.success is True
    with Image.open(res.output_path) as out:
        assert "icc_profile" in out.info


def test_convert_corrupt_file_fails_honestly(synthetic_corrupt_file, temp_dir):
    options = ConvertOptions(target_format="JPEG")
    result = convert_image(synthetic_corrupt_file, temp_dir, options)
    assert result.success is False
    assert result.error_message is not None
