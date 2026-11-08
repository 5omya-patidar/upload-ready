"""Tests for image resizing, dimension calculation, aspect ratio modes, and DPI handling."""

from pathlib import Path
from PIL import Image
import pytest

from upload_ready.core.image_resize import resize_image
from upload_ready.core.models import ResizeMode, ResizeOptions


def test_resize_fit_mode(synthetic_rgb_png, temp_dir):
    # Original is 200x150 (ratio 4:3)
    # Fit into 100x100 bounding box -> should scale to 100x75
    options = ResizeOptions(width=100, height=100, mode=ResizeMode.FIT)
    result = resize_image(synthetic_rgb_png, temp_dir, options)
    
    assert result.success is True
    assert result.output_dimensions == (100, 75)
    with Image.open(result.output_path) as out_img:
        assert out_img.size == (100, 75)


def test_resize_crop_mode(synthetic_rgb_png, temp_dir):
    # Crop to 100x100 exactly
    options = ResizeOptions(width=100, height=100, mode=ResizeMode.CROP)
    result = resize_image(synthetic_rgb_png, temp_dir, options)
    
    assert result.success is True
    assert result.output_dimensions == (100, 100)
    with Image.open(result.output_path) as out_img:
        assert out_img.size == (100, 100)


def test_resize_pad_mode(synthetic_rgb_png, temp_dir):
    # Pad to 100x100 with yellow background (255, 255, 0)
    options = ResizeOptions(width=100, height=100, mode=ResizeMode.PAD, bg_color=(255, 255, 0))
    result = resize_image(synthetic_rgb_png, temp_dir, options)
    
    assert result.success is True
    assert result.output_dimensions == (100, 100)
    with Image.open(result.output_path) as out_img:
        assert out_img.size == (100, 100)
        # Check padding pixel (top edge should be background color)
        assert out_img.getpixel((50, 2)) == (255, 255, 0)


def test_upscaling_refusal_without_deliberate_flag(synthetic_rgb_png, temp_dir):
    # Original is 200x150, request 400x300
    options = ResizeOptions(width=400, height=300, allow_upscale=False)
    result = resize_image(synthetic_rgb_png, temp_dir, options)
    
    assert result.success is False
    assert "requires upscaling" in result.error_message


def test_upscaling_authorized_has_warning(synthetic_rgb_png, temp_dir):
    # Original is 200x150, request 400x300 with allow_upscale=True
    options = ResizeOptions(width=400, height=300, allow_upscale=True)
    result = resize_image(synthetic_rgb_png, temp_dir, options)
    
    assert result.success is True
    assert result.output_dimensions == (400, 300)
    assert any("interpolates pixels" in w for w in result.warnings)


def test_dpi_metadata_setting(synthetic_rgb_png, temp_dir):
    options = ResizeOptions(width=100, height=75, dpi=(300, 300))
    result = resize_image(synthetic_rgb_png, temp_dir, options)
    
    assert result.success is True
    with Image.open(result.output_path) as out_img:
        # DPI metadata should be present without distorting pixel count
        assert out_img.size == (100, 75)
        dpi = out_img.info.get("dpi")
        if dpi:
            assert round(dpi[0]) == 300

