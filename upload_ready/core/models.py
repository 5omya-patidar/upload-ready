"""Data models, option structures, and validation result schemas."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from upload_ready.core.constants import UnitStandard


class OperationType(str, Enum):
    CONVERT = "convert"
    RESIZE = "resize"
    COMPRESS = "compress"
    IMAGES_TO_PDF = "images_to_pdf"
    PDF_TO_IMAGES = "pdf_to_images"
    PDF_REDUCE = "pdf_reduce"
    PRESET = "preset"
    PDF_ORGANIZE = "pdf_organize"
    METADATA = "metadata"
    PHOTO_SHEET = "photo_sheet"
    WATERMARK = "watermark"
    ID_LAYOUT = "id_layout"


class ResizeMode(str, Enum):
    FIT = "fit"    # Scale image to fit inside box, preserving aspect ratio (no cropping, no padding)
    CROP = "crop"  # Fill box and center-crop any excess pixels
    PAD = "pad"    # Fit inside box and pad remaining area with background color


class PageOrientation(str, Enum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    AUTO = "auto"  # Orient page according to image dimensions


class OperationCancelledError(Exception):
    """Raised when an operation is cancelled cooperatively by the user."""
    pass


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self):
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise OperationCancelledError("Operation was cancelled by user.")


@dataclass
class RequirementCheck:
    """Individual assertion check reported in the honest validation summary."""
    name: str
    target: str
    actual: str
    passed: bool
    details: str = ""


@dataclass
class JobResult:
    """Outcome of a single conversion, resize, or compression task."""
    operation: OperationType
    input_path: Path
    output_path: Optional[Path] = None
    success: bool = False
    input_size_bytes: int = 0
    output_size_bytes: int = 0
    input_dimensions: Optional[Tuple[int, int]] = None
    output_dimensions: Optional[Tuple[int, int]] = None
    checks: List[RequirementCheck] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConvertOptions:
    target_format: str = "PNG"  # JPEG, PNG, WEBP
    bg_color: Tuple[int, int, int] = (255, 255, 255)  # Background when flattening alpha to JPEG
    quality: int = 85  # For JPEG/WEBP
    lossless: bool = True  # For PNG and WEBP lossless mode
    allow_first_frame_only: bool = False  # Explicit authorization to drop frames on multi-frame inputs
    preserve_metadata: bool = True  # Carry forward EXIF, XMP, and comments


@dataclass
class ResizeOptions:
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio_lock: bool = True
    mode: ResizeMode = ResizeMode.FIT
    bg_color: Tuple[int, int, int] = (255, 255, 255)
    dpi: Optional[Tuple[int, int]] = None  # Metadata DPI or physical calculation
    allow_upscale: bool = False            # Refuses silent upscaling unless explicitly allowed
    preserve_metadata: bool = True         # Carry forward EXIF, XMP, and comments

    def __post_init__(self):
        if self.width is not None and self.width <= 0:
            raise ValueError(f"Width must be a positive integer, got {self.width}")
        if self.height is not None and self.height <= 0:
            raise ValueError(f"Height must be a positive integer, got {self.height}")
        if self.dpi is not None:
            if self.dpi[0] <= 0 or self.dpi[1] <= 0 or self.dpi[0] > 2400 or self.dpi[1] > 2400:
                raise ValueError(f"DPI must be between 1 and 2400, got {self.dpi}")


@dataclass
class CompressOptions:
    is_lossless: bool = False
    quality: Optional[int] = 80             # Manual lossy quality (1-100)
    max_bytes: Optional[int] = None         # Maximum file-size targeting
    min_bytes: Optional[int] = None         # Minimum file-size targeting (for min-max range)
    unit_standard: UnitStandard = UnitStandard.DECIMAL
    allow_palette_quantization: bool = False  # Explicit opt-in required for lossy PNG palette reduction
    preserve_metadata: bool = True          # Carry forward EXIF, XMP, and comments

    def __post_init__(self):
        if self.quality is not None and not (1 <= self.quality <= 100):
            raise ValueError(f"Quality must be an integer between 1 and 100, got {self.quality}")
        if self.min_bytes is not None and self.min_bytes <= 0:
            raise ValueError(f"Minimum size must be greater than 0, got {self.min_bytes}")
        if self.max_bytes is not None and self.max_bytes <= 0:
            raise ValueError(f"Maximum size must be greater than 0, got {self.max_bytes}")


@dataclass
class ImagesToPdfOptions:
    page_size: str = "A4"                   # A4, LETTER, or IMAGE_SIZE
    orientation: PageOrientation = PageOrientation.AUTO
    margin_pt: float = 0.0                  # Margin in points (72 pt = 1 inch)
    image_rotations: Dict[int, int] = field(default_factory=dict)  # index -> angle (0, 90, 180, 270)
    max_cumulative_pixels: Optional[int] = None  # Cumulative budget limit for retained image bitmaps


@dataclass
class PdfToImagesOptions:
    page_range: str = "all"                 # "all" or "1, 3-5, 8"
    target_format: str = "PNG"              # PNG or JPEG
    dpi: int = 150                          # Rendering resolution
    password: Optional[str] = field(default=None, repr=False)  # Decryption password (masked from repr and logs)
    as_zip: bool = False                    # Bundle output images into a ZIP archive


@dataclass
class PdfReduceOptions:
    structural_only: bool = True            # Deduplicate streams, clean objects, strip metadata
    downsample_scanned: bool = False        # Downsample rasterized pages (for scanned documents)
    target_dpi: int = 150                   # Downsampling resolution
    image_quality: int = 75                 # JPEG recompression quality for scanned pages
    remove_metadata: bool = True
    password: Optional[str] = field(default=None, repr=False)  # Decryption password (masked from repr and logs)
    max_cumulative_pixels: Optional[int] = None  # Cumulative budget limit for rasterized pages

