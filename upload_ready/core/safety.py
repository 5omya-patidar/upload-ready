"""Safety, content validation, sanitization, resource protection, and temp management.

All untrusted inputs are validated here before being handed to decoding engines.
Logs are stripped of full paths, filenames, and sensitive payload fragments.
Destinations are validated against inputs to guarantee that original source files
are never overwritten or deleted, even if allow_overwrite is set.
Outputs are staged to prevent partial writes and preserve existing files on failure.
"""

import logging
import math
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union
from PIL import Image

from upload_ready.core.constants import (
    DEFAULT_MAX_CUMULATIVE_PIXELS,
    MAGIC_SIGNATURES,
    MAX_DECODED_PIXELS,
    MAX_INPUT_FILE_SIZE_BYTES,
)
from upload_ready.core.models import CancellationToken, OperationCancelledError

logger = logging.getLogger("upload_ready")

# Forbidden characters in filenames across Windows and POSIX
ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class SafetyValidationError(Exception):
    """Raised when an untrusted file fails safety checks or exceeds resource bounds."""
    pass


class ResourceLimitExceededError(SafetyValidationError):
    """Raised when input size or decoded dimension exceeds conservative memory bounds."""
    pass


def validate_file_size(file_path: Path, max_bytes: int = MAX_INPUT_FILE_SIZE_BYTES) -> int:
    """Ensure the file exists, is a regular file, and does not exceed maximum byte limits."""
    if not file_path.is_file():
        raise SafetyValidationError(f"File does not exist or is not a regular file")
    
    size = file_path.stat().st_size
    if size == 0:
        raise SafetyValidationError("File is empty (0 bytes)")
    if size > max_bytes:
        raise ResourceLimitExceededError(
            f"File size exceeds safety limit of {max_bytes // (1024 * 1024)} MB"
        )
    return size


def sniff_content_type(file_path: Path) -> str:
    """Inspect magic bytes at the beginning of the file to determine true content format."""
    validate_file_size(file_path)
    
    with open(file_path, "rb") as f:
        header = f.read(32)
        
    if header.startswith(MAGIC_SIGNATURES["PNG"]):
        return "PNG"
    if header.startswith(MAGIC_SIGNATURES["JPEG"]):
        return "JPEG"
    if header.startswith(MAGIC_SIGNATURES["PDF"]):
        return "PDF"
    if header.startswith(MAGIC_SIGNATURES["GIF"]):
        return "GIF"
    if header.startswith(MAGIC_SIGNATURES["BMP"]):
        return "BMP"
    if header.startswith(MAGIC_SIGNATURES["TIFF_LE"]) or header.startswith(MAGIC_SIGNATURES["TIFF_BE"]):
        return "TIFF"
    if len(header) >= 12 and header[0:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "WEBP"
        
    raise SafetyValidationError(
        "Unsupported or unrecognized file signature. File header did not match any supported format."
    )


def is_same_file(path1: Union[str, Path], path2: Union[str, Path]) -> bool:
    """
    Check if two paths refer to the same file system location.
    Handles exact string match, resolved absolute path matching, case insensitivity on Windows,
    and filesystem inode/stat sameness (os.path.samefile).
    """
    p1 = Path(path1)
    p2 = Path(path2)
    
    # Exact path or resolved match
    try:
        r1 = p1.resolve()
        r2 = p2.resolve()
        if r1 == r2:
            return True
        if os.name == "nt" and str(r1).lower() == str(r2).lower():
            return True
    except Exception:
        pass

    # Direct samefile check if both files exist
    try:
        if p1.exists() and p2.exists():
            return os.path.samefile(p1, p2)
    except (FileNotFoundError, OSError, ValueError):
        pass

    return False


def validate_destination_safety(destination: Path, inputs: Union[Path, Iterable[Path]]) -> None:
    """
    Reject destinations that identify any original input, including aliases and case variations.
    Original files must NEVER be overwritten as an output of their own processing or any batch item.
    """
    if isinstance(inputs, Path):
        input_list = [inputs]
    else:
        input_list = list(inputs)

    for inp in input_list:
        if is_same_file(destination, inp):
            raise SafetyValidationError(
                f"Destination path resolves to a queued original input file: {Path(inp).name}. "
                f"Upload Ready strictly prohibits overwriting input files to prevent data loss."
            )


def sanitize_filename(name: str, fallback_prefix: str = "output") -> str:
    """Sanitize a filename stem to avoid directory traversal or illegal file system characters."""
    # Strip any directory path components
    basename = Path(name).name
    # Replace illegal characters
    cleaned = ILLEGAL_FILENAME_CHARS.sub("_", basename).strip().strip(".")
    if not cleaned:
        cleaned = fallback_prefix
    return cleaned


def resolve_collision_path(target_path: Path, allow_overwrite: bool = False) -> Path:
    """Return non-colliding destination path by appending numeric suffix if file already exists."""
    if allow_overwrite or not target_path.exists():
        return target_path

    parent = target_path.parent
    stem = target_path.stem
    suffix = target_path.suffix
    
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


class StagedOutput:
    """
    Stages file writes to an application-owned temporary file in the destination directory.
    
    Ensures:
    1. Partial writes or crashes never leave a corrupt file at the final destination.
    2. Any existing destination file is left intact if processing fails or is cancelled.
    3. Cancellation is checked before committing completed outputs.
    4. Destination is never cleaned up merely because an error occurred.
    """
    def __init__(self, final_destination: Path, cancel_token: Optional[CancellationToken] = None):
        self.final_destination = final_destination
        self.cancel_token = cancel_token
        self.parent_dir = final_destination.parent
        self.parent_dir.mkdir(parents=True, exist_ok=True)
        self.staged_path = self.parent_dir / f".tmp_{final_destination.name}_{os.getpid()}_{int(time.time()*1000)}"
        self._committed = False

    def __enter__(self) -> Path:
        return self.staged_path

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.discard()

    def commit(self) -> Path:
        """
        Check cancellation and atomically replace the staged file to the final destination.
        """
        if self._committed:
            return self.final_destination

        if self.cancel_token and self.cancel_token.is_cancelled:
            self.discard()
            raise OperationCancelledError("Operation cancelled before output could be committed.")

        if not self.staged_path.exists():
            raise FileNotFoundError(f"Staged output file was not created: {self.staged_path}")

        try:
            # os.replace provides atomic overwrite on POSIX and safe overwrite on Windows (Python 3.3+)
            os.replace(self.staged_path, self.final_destination)
            self._committed = True
            return self.final_destination
        except Exception:
            self.discard()
            raise

    def discard(self) -> None:
        """Discard staged file without touching the final destination."""
        if not self._committed and self.staged_path.exists():
            try:
                self.staged_path.unlink()
            except Exception:
                pass

    def __del__(self):
        self.discard()


class MultiFileJobRollback:
    """
    Tracks created files and overwritten pre-existing files in multi-file operations (e.g. PDF-to-images).
    
    Provides job-level compensation policy:
    1. If a destination file already exists prior to the operation, a temporary backup copy is preserved.
       If the job aborts or fails mid-stream, the pre-existing file is restored from its backup.
    2. Newly created files (that did not exist before this run) are unlinked on abort or failure.
    3. On successful completion (mark_success), all temporary backups are purged.
    
    Note on failure atomicity: Upload Ready guarantees atomic file replacement at the single-file level
    via StagedOutput (os.replace). For multi-file jobs, MultiFileJobRollback provides best-effort
    application-level rollback, but does not claim crash-atomic whole-job transactions across sudden
    power loss or SIGKILL.
    """
    def __init__(self):
        self.created_files: List[Path] = []
        self._backups: Dict[Path, Path] = {}
        self._success = False

    def stage_commit(self, staged: "StagedOutput") -> Path:
        """Commit a staged file while recording backup or creation status for safe rollback."""
        dest = staged.final_destination
        if dest.exists() and dest not in self._backups:
            backup_path = staged.parent_dir / f".backup_{dest.name}_{os.getpid()}_{int(time.time()*1000)}"
            try:
                shutil.copy2(dest, backup_path)
            except Exception as exc:
                # Must abort: never perform destructive overwrite without the safety backup required by rollback policy
                staged.discard()
                raise SafetyValidationError(
                    f"Failed to create safety backup for pre-existing destination {dest.name}: {exc}. "
                    f"Aborting write to preserve existing destination."
                ) from exc
            self._backups[dest] = backup_path
        elif not dest.exists():
            self.created_files.append(dest)

        return staged.commit()

    def record_created(self, file_path: Path) -> None:
        """Registration for files created directly without stage_commit."""
        if file_path not in self.created_files:
            self.created_files.append(file_path)

    def mark_success(self) -> None:
        """Mark job successful and purge all temporary backups."""
        self._success = True
        for b in list(self._backups.values()):
            if b.exists():
                try:
                    b.unlink()
                except Exception:
                    pass
        self._backups.clear()

    def rollback(self) -> List[str]:
        """
        Roll back the multi-file job:
        - Restore pre-existing files that were overwritten from their backup copies.
        - Delete newly created files.
        - Purge backup copies if restored successfully; retain recoverable backups on error.
        Returns list of restoration error messages if any.
        """
        restoration_errors: List[str] = []
        if not self._success:
            # 1. Restore overwritten files
            for dest, backup in list(self._backups.items()):
                if backup.exists():
                    try:
                        os.replace(backup, dest)
                        del self._backups[dest]
                    except Exception as exc1:
                        try:
                            shutil.copy2(backup, dest)
                            backup.unlink()
                            del self._backups[dest]
                        except Exception as exc2:
                            err_msg = f"Failed to restore {dest.name} from backup {backup.name}: {exc2}"
                            logger.error(err_msg)
                            restoration_errors.append(err_msg)

            # 2. Delete newly created files
            for p in self.created_files:
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

            self.created_files.clear()
        return restoration_errors


def check_single_frame_image(
    image_path: Path,
    allow_first_frame_only: bool = False,
    operation_name: str = "this operation",
) -> int:
    """
    Pre-decode validation of multi-frame / animated images.
    Inspects image structure before decoding or transformation.
    Raises SafetyValidationError if multi-frame is detected and allow_first_frame_only is False.
    Returns total frame count.
    """
    with Image.open(image_path) as img:
        n_frames = getattr(img, "n_frames", 1)
        is_animated = getattr(img, "is_animated", False)

        if n_frames > 1 or is_animated:
            if not allow_first_frame_only:
                raise SafetyValidationError(
                    f"Multi-frame input detected ({n_frames} frames). "
                    f"Multi-frame processing is not supported for {operation_name} to prevent silent content loss. "
                    f"Use format conversion with explicit first-frame authorization if single-frame export is intended."
                )
        return n_frames


def validate_dimensions(
    width: Optional[float],
    height: Optional[float],
    max_pixels: int = MAX_DECODED_PIXELS,
    context: str = "Dimension",
) -> None:
    """
    Validate width and height bounds before memory allocation.
    Rejects non-positive, non-finite (NaN, Inf), or dimensions exceeding max_pixels.
    """
    if width is not None:
        if not math.isfinite(width) or width <= 0:
            raise SafetyValidationError(
                f"{context} width must be a positive finite number, got {width}"
            )
    if height is not None:
        if not math.isfinite(height) or height <= 0:
            raise SafetyValidationError(
                f"{context} height must be a positive finite number, got {height}"
            )
    if width is not None and height is not None:
        pixel_count = width * height
        if pixel_count > max_pixels:
            raise ResourceLimitExceededError(
                f"{context} dimensions {int(width)}x{int(height)} require {int(pixel_count):,} pixels, "
                f"which exceeds safety limit of {max_pixels:,} pixels."
            )


def validate_cumulative_image_dimensions(
    image_paths: List[Path],
    max_cumulative_pixels: int = DEFAULT_MAX_CUMULATIVE_PIXELS,
    context: str = "Multi-image job",
) -> int:
    """
    Inspect headers of input images and compute cumulative pixel volume before memory allocation.
    Rejects excessive cumulative workloads before opening or rasterizing full bitmaps.

    Limitation note:
    This check enforces an uncompressed decoded bitmap dimension limit
    (sum of width * height across all retained images). It bounds cumulative memory
    consumption from large image buffers, but is not an exact operating system
    process-level memory or RSS ceiling.

    Returns:
        total_pixels: Total cumulative pixel count.
    """
    total_pixels = 0
    for p in image_paths:
        validate_file_size(p)
        with Image.open(p) as img:
            w, h = img.size
            validate_dimensions(w, h, context=f"Image {p.name}")
            total_pixels += (w * h)
            if total_pixels > max_cumulative_pixels:
                raise ResourceLimitExceededError(
                    f"{context} exceeds cumulative safety budget of {max_cumulative_pixels:,} pixels "
                    f"(accumulated {total_pixels:,} pixels across input images). "
                    f"Reduce the number of images or input resolutions to stay within memory limits."
                )
    return total_pixels


def validate_cumulative_pdf_pages_dimensions(
    page_dimensions: Iterable[Tuple[float, float]],
    dpi: float,
    max_cumulative_pixels: int = DEFAULT_MAX_CUMULATIVE_PIXELS,
    context: str = "PDF rasterization job",
) -> int:
    """
    Inspect page point dimensions and compute cumulative raster pixel volume at target DPI
    before decoding or allocating retained page buffers.

    Limitation note:
    Enforces an uncompressed raster bitmap dimension budget. It bounds cumulative memory
    consumption from rasterized pages, but is not an exact OS process RSS ceiling.

    Returns:
        total_pixels: Total cumulative pixel count across all pages at target DPI.
    """
    scale = dpi / 72.0
    total_pixels = 0
    for idx, (pw, ph) in enumerate(page_dimensions):
        rw = math.ceil(pw * scale)
        rh = math.ceil(ph * scale)
        validate_dimensions(rw, rh, context=f"Page {idx + 1}")
        total_pixels += (rw * rh)
        if total_pixels > max_cumulative_pixels:
            raise ResourceLimitExceededError(
                f"{context} exceeds cumulative safety budget of {max_cumulative_pixels:,} pixels "
                f"(accumulated {total_pixels:,} pixels across pages). "
                f"Reduce the target DPI or process fewer pages to stay within memory limits."
            )
    return total_pixels


def validate_dpi(dpi: Optional[float]) -> None:
    """Validate DPI metadata values."""
    if dpi is not None:
        if not math.isfinite(dpi) or dpi <= 0 or dpi > 2400:
            raise SafetyValidationError(
                f"Invalid DPI value: {dpi}. DPI must be a finite number between 1 and 2400."
            )


def validate_pdf_page_count(page_count: int, max_pages: int = 10000) -> None:
    """Validate PDF page count to protect against resource exhaustion."""
    if page_count <= 0:
        raise SafetyValidationError("PDF document contains no pages.")
    if page_count > max_pages:
        raise ResourceLimitExceededError(
            f"PDF document contains {page_count} pages, which exceeds the safety limit of {max_pages} pages."
        )


class TempJobWorkspace:
    """Dedicated per-job temporary directory with automatic and crash cleanup."""
    
    APP_TEMP_SUBDIR = "upload_ready_workspace"
    
    def __init__(self, job_id: Optional[str] = None, prefix: Optional[str] = None):
        pfx = prefix or "job_"
        self.job_id = job_id or f"{pfx}{int(time.time() * 1000)}_{os.getpid()}"
        self.base_dir = Path(tempfile.gettempdir()) / self.APP_TEMP_SUBDIR
        self.job_dir = self.base_dir / self.job_id
        
    def __enter__(self) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        return self.job_dir

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def cleanup(self) -> None:
        """Safely delete only this job's temporary directory."""
        if self.job_dir.exists():
            try:
                shutil.rmtree(self.job_dir, ignore_errors=True)
            except Exception:
                pass

    @classmethod
    def clean_orphaned_workspaces(cls, max_age_seconds: int = 3600) -> int:
        """Remove abandoned temporary workspaces older than max_age_seconds from past runs."""
        base_dir = Path(tempfile.gettempdir()) / cls.APP_TEMP_SUBDIR
        if not base_dir.exists():
            return 0
            
        now = time.time()
        cleaned_count = 0
        try:
            for item in base_dir.iterdir():
                if item.is_dir() and item.name.startswith("job_"):
                    try:
                        mtime = item.stat().st_mtime
                        if (now - mtime) > max_age_seconds:
                            shutil.rmtree(item, ignore_errors=True)
                            cleaned_count += 1
                    except Exception:
                        continue
        except Exception:
            pass
        return cleaned_count


def anonymize_path_for_log(path: Path) -> str:
    """Return privacy-safe descriptor without leaking user directories, usernames, or filenames."""
    try:
        suffix = path.suffix.lower()
        return f"[file: *{suffix}]"
    except Exception:
        return "[file]"
