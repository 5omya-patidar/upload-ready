"""Constants, supported formats, unit definitions, and resource limits.

All limits and unit definitions are centralized here to prevent silent truncation
or unchecked memory expansion during untrusted file parsing.
"""

from enum import Enum
from typing import Dict, Tuple

# Resource safety limits for untrusted input
MAX_INPUT_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB
MAX_DECODED_PIXELS = 100_000_000  # 100 Megapixels per individual image (guards against decompression bombs)
DEFAULT_MAX_CUMULATIVE_PIXELS = 150_000_000  # 150 Megapixels cumulative budget for multi-page retained allocations
MAX_PDF_PAGES = 2000              # Maximum PDF page count to process
MAX_OPTIMIZER_ITERATIONS = 12     # Maximum steps for binary search target size optimizer

# Unit definitions: clear distinction between decimal and binary representations
class UnitStandard(str, Enum):
    DECIMAL = "decimal"  # 1 KB = 1,000 bytes, 1 MB = 1,000,000 bytes
    BINARY = "binary"    # 1 KiB = 1,024 bytes, 1 MiB = 1,048,576 bytes

def bytes_to_human(byte_count: int, unit_standard: UnitStandard = UnitStandard.DECIMAL) -> str:
    """Format bytes to human readable string with explicit units."""
    if byte_count < 0:
        return "0 B"
    
    if unit_standard == UnitStandard.BINARY:
        units = ["B", "KiB", "MiB", "GiB"]
        multiplier = 1024.0
    else:
        units = ["B", "KB", "MB", "GB"]
        multiplier = 1000.0

    size = float(byte_count)
    for unit in units[:-1]:
        if size < multiplier:
            if unit in ("B", "KiB", "KB"):
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= multiplier
    return f"{size:.2f} {units[-1]}"

def parse_size_to_bytes(size_str: str, default_standard: UnitStandard = UnitStandard.DECIMAL) -> int:
    """
    Parse user size string into integer bytes.
    Respects explicit units:
    - KB, MB, GB are strictly decimal (SI: 1 KB = 1,000 B).
    - KiB, MiB, GiB are strictly binary (IEC: 1 KiB = 1,024 B).
    - Ambiguous single-letter units (K, M, G) or unitless numbers use default_standard.
    Validates that the returned byte count is strictly positive.
    """
    clean = size_str.strip().upper()
    if not clean:
        raise ValueError("Size string cannot be empty")
    
    # Check explicit binary (IEC) units
    if clean.endswith("KIB"):
        bytes_val = int(float(clean[:-3].strip()) * 1024)
    elif clean.endswith("MIB"):
        bytes_val = int(float(clean[:-3].strip()) * 1024 * 1024)
    elif clean.endswith("GIB"):
        bytes_val = int(float(clean[:-3].strip()) * 1024 * 1024 * 1024)
    # Check explicit decimal (SI) units
    elif clean.endswith("KB"):
        bytes_val = int(float(clean[:-2].strip()) * 1000)
    elif clean.endswith("MB"):
        bytes_val = int(float(clean[:-2].strip()) * 1000 * 1000)
    elif clean.endswith("GB"):
        bytes_val = int(float(clean[:-2].strip()) * 1000 * 1000 * 1000)
    # Check single-letter or B suffixes
    elif clean.endswith("K"):
        multiplier = 1000 if default_standard == UnitStandard.DECIMAL else 1024
        bytes_val = int(float(clean[:-1].strip()) * multiplier)
    elif clean.endswith("M"):
        multiplier = 1000 * 1000 if default_standard == UnitStandard.DECIMAL else 1024 * 1024
        bytes_val = int(float(clean[:-1].strip()) * multiplier)
    elif clean.endswith("G"):
        multiplier = 1000 * 1000 * 1000 if default_standard == UnitStandard.DECIMAL else 1024 * 1024 * 1024
        bytes_val = int(float(clean[:-1].strip()) * multiplier)
    elif clean.endswith("B"):
        bytes_val = int(float(clean[:-1].strip()))
    else:
        # Pure numeric without suffix defaults to bytes
        bytes_val = int(float(clean))

    if bytes_val <= 0:
        raise ValueError(f"Size must be a positive number greater than 0, got {bytes_val} bytes.")

    return bytes_val

# Supported Image Formats
SUPPORTED_IMAGE_INPUT_FORMATS = {
    "JPEG": [".jpg", ".jpeg"],
    "PNG": [".png"],
    "WEBP": [".webp"],
    "GIF": [".gif"],
    "BMP": [".bmp"],
    "TIFF": [".tiff", ".tif"],
}

SUPPORTED_IMAGE_OUTPUT_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}

# PDF Page Dimensions in points (1 pt = 1/72 inch)
PDF_PAGE_SIZES: Dict[str, Tuple[float, float]] = {
    "A4": (595.28, 841.89),       # 210 x 297 mm
    "LETTER": (612.0, 792.0),     # 8.5 x 11 inches
    "LEGAL": (612.0, 1008.0),     # 8.5 x 14 inches
}

# Magic signatures for content inspection (not relying on file extensions)
MAGIC_SIGNATURES: Dict[str, bytes] = {
    "PNG": b"\x89PNG\r\n\x1a\n",
    "JPEG": b"\xff\xd8\xff",
    "GIF": b"GIF8",
    "BMP": b"BM",
    "TIFF_LE": b"II*\x00",
    "TIFF_BE": b"MM\x00*",
    "PDF": b"%PDF-",
}

