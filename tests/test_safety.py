"""Tests for file safety, validation, collision handling, and privacy boundary enforcement."""

from pathlib import Path
import pytest

from upload_ready.core.constants import UnitStandard, bytes_to_human, parse_size_to_bytes
from upload_ready.core.safety import (
    ResourceLimitExceededError,
    SafetyValidationError,
    TempJobWorkspace,
    anonymize_path_for_log,
    resolve_collision_path,
    sanitize_filename,
    sniff_content_type,
    validate_file_size,
)


def test_sniff_valid_formats(synthetic_rgb_png, synthetic_jpeg_with_exif, synthetic_multipage_pdf):
    assert sniff_content_type(synthetic_rgb_png) == "PNG"
    assert sniff_content_type(synthetic_jpeg_with_exif) == "JPEG"
    assert sniff_content_type(synthetic_multipage_pdf) == "PDF"


def test_sniff_corrupt_file_raises_validation_error(synthetic_corrupt_file):
    with pytest.raises(SafetyValidationError, match="Unsupported or unrecognized"):
        sniff_content_type(synthetic_corrupt_file)


def test_validate_file_size_empty_file(tmp_path: Path):
    empty = tmp_path / "empty.dat"
    empty.touch()
    with pytest.raises(SafetyValidationError, match="File is empty"):
        validate_file_size(empty)


def test_validate_file_size_exceeded(tmp_path: Path):
    dummy = tmp_path / "large.dat"
    with open(dummy, "wb") as f:
        f.seek(1024 * 1024)  # 1 MB
        f.write(b"X")
    with pytest.raises(ResourceLimitExceededError):
        validate_file_size(dummy, max_bytes=500 * 1024)  # 500 KB limit


def test_sanitize_filename_prevents_traversal():
    dangerous = "../../secret/malicious:file<name>?.png"
    clean = sanitize_filename(dangerous)
    assert "/" not in clean
    assert "\\" not in clean
    assert ":" not in clean
    assert "<" not in clean
    assert "?" not in clean
    assert ".." not in clean


def test_resolve_collision_path(tmp_path: Path):
    original = tmp_path / "document.pdf"
    original.touch()
    
    # Should resolve to document_1.pdf without overwriting
    candidate1 = resolve_collision_path(original, allow_overwrite=False)
    assert candidate1.name == "document_1.pdf"
    
    # If candidate 1 exists, should resolve to document_2.pdf
    candidate1.touch()
    candidate2 = resolve_collision_path(original, allow_overwrite=False)
    assert candidate2.name == "document_2.pdf"
    
    # If allow_overwrite is True, returns original path
    assert resolve_collision_path(original, allow_overwrite=True) == original


def test_temp_workspace_cleanup():
    ws = TempJobWorkspace()
    with ws as temp_path:
        assert temp_path.exists()
        test_file = temp_path / "scratch.tmp"
        test_file.write_text("temporary data")
        assert test_file.exists()
        
    # After exiting context, directory must be completely removed
    assert not temp_path.exists()


def test_privacy_log_anonymization():
    secret_path = Path("C:/Users/JaneDoe/SecretFolder/confidential_tax_return.pdf")
    anon = anonymize_path_for_log(secret_path)
    assert "JaneDoe" not in anon
    assert "confidential" not in anon
    assert "SecretFolder" not in anon
    assert ".pdf" in anon


def test_size_parsing_and_units():
    # Decimal standard
    assert parse_size_to_bytes("50KB", UnitStandard.DECIMAL) == 50000
    assert parse_size_to_bytes("2MB", UnitStandard.DECIMAL) == 2000000
    # Binary standard
    assert parse_size_to_bytes("50KiB") == 50 * 1024
    assert parse_size_to_bytes("2MiB") == 2 * 1024 * 1024
    
    # Formatting
    assert bytes_to_human(50000, UnitStandard.DECIMAL) == "50.0 KB"
    assert bytes_to_human(51200, UnitStandard.BINARY) == "50.0 KiB"

