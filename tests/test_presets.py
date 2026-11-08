"""Tests for Reusable Requirement Presets (Feature 1)."""

import json
from pathlib import Path
from PIL import Image
import pytest

from upload_ready.core.presets import (
    PRESET_SCHEMA_VERSION,
    PresetManager,
    RequirementPreset,
    apply_preset_to_image,
)


def test_preset_serialization_roundtrip():
    preset = RequirementPreset(
        id="test_custom",
        name="Custom Web Preset",
        description="Testing preset",
        target_format="JPEG",
        width=1200,
        height=800,
        resize_mode="crop",
        is_lossless=False,
        quality=75,
        max_bytes=150000,
        remove_metadata=True,
    )
    d = preset.to_dict()
    assert d["schema_version"] == PRESET_SCHEMA_VERSION
    assert d["id"] == "test_custom"
    assert d["width"] == 1200

    loaded = RequirementPreset.from_dict(d)
    assert loaded.id == preset.id
    assert loaded.name == preset.name
    assert loaded.target_format == "JPEG"
    assert loaded.width == 1200
    assert loaded.height == 800
    assert loaded.max_bytes == 150000


def test_preset_schema_rejects_forbidden_fields():
    # Rejects sensitive / executable / path fields
    bad_data = {
        "id": "malicious",
        "name": "Malicious Preset",
        "password": "secret_password",
        "schema_version": "1.0",
    }
    with pytest.raises(ValueError, match="sensitive/path fields"):
        RequirementPreset.from_dict(bad_data)

    bad_path_data = {
        "id": "malicious2",
        "name": "Malicious Path",
        "file_path": "C:\\Windows\\System32\\cmd.exe",
        "schema_version": "1.0",
    }
    with pytest.raises(ValueError, match="sensitive/path fields"):
        RequirementPreset.from_dict(bad_path_data)


def test_preset_schema_validation_constraints():
    # Negative width
    with pytest.raises(ValueError, match="Width must be a positive integer"):
        RequirementPreset.from_dict({"name": "Bad", "width": -10})

    # Invalid format
    with pytest.raises(ValueError, match="Invalid target_format"):
        RequirementPreset.from_dict({"name": "Bad", "target_format": "TIFF"})

    # Min bytes > Max bytes
    with pytest.raises(ValueError, match="cannot exceed max_bytes"):
        RequirementPreset.from_dict({"name": "Bad", "min_bytes": 500, "max_bytes": 200})


def test_preset_manager_crud_and_persistence(tmp_path: Path):
    storage_file = tmp_path / "presets.json"
    manager = PresetManager(storage_file=storage_file)

    # Defaults should be loaded
    presets = manager.list_presets()
    assert len(presets) >= 4

    # Add custom preset
    custom = RequirementPreset(
        id="custom_1",
        name="Custom 1",
        target_format="PNG",
        is_lossless=True,
    )
    manager.add_or_update(custom)
    assert manager.get_preset("custom_1") is not None

    # Duplicate
    dup = manager.duplicate("custom_1", "Custom 1 Copy")
    assert dup is not None
    assert dup.name == "Custom 1 Copy"

    # Export & Import JSON
    json_str = manager.export_preset_json("custom_1")
    imported = manager.import_preset_json(json_str)
    assert imported is not None

    # Delete
    assert manager.delete("custom_1") is True
    assert manager.get_preset("custom_1") is None

    # Verify disk persistence
    manager2 = PresetManager(storage_file=storage_file)
    assert manager2.get_preset("custom_1") is None
    assert manager2.get_preset(dup.id) is not None


def test_apply_preset_to_image_pipeline(synthetic_rgb_png: Path, temp_dir: Path):
    preset = RequirementPreset(
        id="pipeline_test",
        name="Pipeline Test",
        target_format="JPEG",
        width=100,
        height=100,
        resize_mode="fit",
        quality=80,
    )

    res = apply_preset_to_image(
        synthetic_rgb_png,
        temp_dir,
        preset,
        allow_overwrite=False,
    )
    assert res.success is True
    assert res.output_path is not None
    assert res.output_path.suffix.lower() == ".jpg"
    with Image.open(res.output_path) as out_img:
        assert out_img.format == "JPEG"
        assert max(out_img.size) <= 100

    # Checks should contain target format
    check_names = [c.name for c in res.checks]
    assert "Preset Target Format" in check_names

