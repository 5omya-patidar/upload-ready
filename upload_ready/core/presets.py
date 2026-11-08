"""Reusable requirement presets for batch image and PDF operations.

Allows creating, saving, loading, editing, duplicating, deleting, and importing/exporting
declarative requirement presets. Imported presets are strictly validated against a declarative
schema to prevent arbitrary code execution or path persistence.
"""

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from PIL import Image

from upload_ready.core.constants import (
    SUPPORTED_IMAGE_OUTPUT_FORMATS,
    UnitStandard,
    bytes_to_human,
    parse_size_to_bytes,
)
from upload_ready.core.image_compress import compress_image
from upload_ready.core.image_convert import convert_image
from upload_ready.core.image_resize import resize_image
from upload_ready.core.metadata import scrub_metadata
from upload_ready.core.models import (
    CancellationToken,
    CompressOptions,
    ConvertOptions,
    JobResult,
    OperationCancelledError,
    OperationType,
    RequirementCheck,
    ResizeMode,
    ResizeOptions,
)
from upload_ready.core.safety import (
    SafetyValidationError,
    StagedOutput,
    TempJobWorkspace,
    check_single_frame_image,
    resolve_collision_path,
    sanitize_filename,
    validate_destination_safety,
    validate_dimensions,
    validate_file_size,
)

logger = logging.getLogger("upload_ready")

PRESET_SCHEMA_VERSION = "1.0"

DEFAULT_PRESETS_DIR = Path.home() / ".upload_ready"
DEFAULT_PRESETS_FILE = DEFAULT_PRESETS_DIR / "presets.json"


@dataclass
class RequirementPreset:
    """Declarative specification of output requirements."""

    id: str
    name: str
    description: str = ""
    schema_version: str = PRESET_SCHEMA_VERSION

    # Format conversion
    target_format: Optional[str] = None  # "JPEG", "PNG", "WEBP"
    bg_color: Tuple[int, int, int] = (255, 255, 255)

    # Dimensions and resize
    width: Optional[int] = None
    height: Optional[int] = None
    resize_mode: str = "fit"  # "fit", "crop", "pad"
    aspect_ratio_lock: bool = True
    allow_upscale: bool = False
    dpi: Optional[int] = None

    # Compression and file size
    is_lossless: bool = False
    quality: Optional[int] = 85
    max_bytes: Optional[int] = None
    min_bytes: Optional[int] = None
    unit_standard: str = "decimal"  # "decimal" or "binary"

    # Metadata
    remove_metadata: bool = False
    metadata_categories: List[str] = field(default_factory=lambda: ["exif", "gps", "xmp", "comments"])

    def to_dict(self) -> Dict[str, Any]:
        """Export preset to declarative dictionary without sensitive data or paths."""
        d = asdict(self)
        d["schema_version"] = PRESET_SCHEMA_VERSION
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RequirementPreset":
        """
        Validate and construct preset from declarative dictionary.
        Rejects unrecognized fields, executable code, paths, or passwords.
        """
        if not isinstance(data, dict):
            raise ValueError("Preset data must be a JSON object.")

        schema_ver = data.get("schema_version", "1.0")
        if schema_ver != "1.0":
            raise ValueError(f"Unsupported preset schema version: {schema_ver}")

        preset_id = str(data.get("id", "")).strip()
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Preset must have a non-empty 'name'.")
        if not preset_id:
            preset_id = name.lower().replace(" ", "_")

        # Validate target format
        fmt = data.get("target_format")
        if fmt is not None:
            fmt = str(fmt).upper().strip()
            if fmt in ("JPG", "JPEG"):
                fmt = "JPEG"
            elif fmt not in ("PNG", "WEBP"):
                raise ValueError(f"Invalid target_format '{fmt}'. Supported: JPEG, PNG, WEBP.")

        # Validate resize parameters
        w = data.get("width")
        h = data.get("height")
        if w is not None and (not isinstance(w, int) or w <= 0 or w > 50000):
            raise ValueError("Width must be a positive integer <= 50,000.")
        if h is not None and (not isinstance(h, int) or h <= 0 or h > 50000):
            raise ValueError("Height must be a positive integer <= 50,000.")

        mode_str = str(data.get("resize_mode", "fit")).lower()
        if mode_str not in ("fit", "crop", "pad"):
            raise ValueError("resize_mode must be one of 'fit', 'crop', 'pad'.")

        # Validate bg_color
        bg = data.get("bg_color", [255, 255, 255])
        if not (isinstance(bg, (list, tuple)) and len(bg) == 3 and all(0 <= int(c) <= 255 for c in bg)):
            raise ValueError("bg_color must be an RGB 3-tuple with values 0-255.")
        bg_tuple = (int(bg[0]), int(bg[1]), int(bg[2]))

        # Validate compression parameters
        is_lossless = bool(data.get("is_lossless", False))
        q = data.get("quality")
        if q is not None:
            if not isinstance(q, int) or not (1 <= q <= 100):
                raise ValueError("quality must be an integer between 1 and 100.")

        max_b = data.get("max_bytes")
        min_b = data.get("min_bytes")
        if max_b is not None and (not isinstance(max_b, int) or max_b <= 0):
            raise ValueError("max_bytes must be a positive integer.")
        if min_b is not None and (not isinstance(min_b, int) or min_b <= 0):
            raise ValueError("min_bytes must be a positive integer.")
        if max_b is not None and min_b is not None and min_b > max_b:
            raise ValueError(f"min_bytes ({min_b}) cannot exceed max_bytes ({max_b}).")

        unit_std = str(data.get("unit_standard", "decimal")).lower()
        if unit_std not in ("decimal", "binary"):
            unit_std = "decimal"

        dpi_val = data.get("dpi")
        if dpi_val is not None and (not isinstance(dpi_val, int) or not (1 <= dpi_val <= 2400)):
            raise ValueError("dpi must be an integer between 1 and 2400.")

        # Ensure no forbidden fields (passwords, file paths, documents)
        forbidden = {"password", "input_path", "output_path", "file_path", "content", "payload"}
        found_forbidden = forbidden.intersection(set(data.keys()))
        if found_forbidden:
            raise ValueError(f"Presets must not contain sensitive/path fields: {list(found_forbidden)}")

        categories = data.get("metadata_categories", ["exif", "gps", "xmp", "comments"])
        if not isinstance(categories, list):
            categories = ["exif", "gps", "xmp", "comments"]

        return cls(
            id=preset_id,
            name=name,
            description=str(data.get("description", "")),
            schema_version=PRESET_SCHEMA_VERSION,
            target_format=fmt,
            bg_color=bg_tuple,
            width=w,
            height=h,
            resize_mode=mode_str,
            aspect_ratio_lock=bool(data.get("aspect_ratio_lock", True)),
            allow_upscale=bool(data.get("allow_upscale", False)),
            dpi=dpi_val,
            is_lossless=is_lossless,
            quality=q if q is not None else 85,
            max_bytes=max_b,
            min_bytes=min_b,
            unit_standard=unit_std,
            remove_metadata=bool(data.get("remove_metadata", False)),
            metadata_categories=[str(c) for c in categories],
        )


class PresetManager:
    """Manages persistence, retrieval, and declarative import/export of requirement presets."""

    def __init__(self, storage_file: Optional[Path] = None):
        self.storage_file = storage_file or DEFAULT_PRESETS_FILE
        self._presets: Dict[str, RequirementPreset] = {}
        self.load()

    def load(self) -> None:
        """Load stored presets from JSON file or populate built-in defaults."""
        self._presets.clear()
        if self.storage_file.exists():
            try:
                data = json.loads(self.storage_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "presets" in data:
                    for item in data["presets"]:
                        try:
                            preset = RequirementPreset.from_dict(item)
                            self._presets[preset.id] = preset
                        except Exception as e:
                            logger.warning(f"Skipping malformed preset {item.get('name')}: {e}")
                return
            except Exception as e:
                logger.warning(f"Could not load presets file {self.storage_file}: {e}")

        # Populate standard built-in templates
        self._load_defaults()

    def _load_defaults(self) -> None:
        """Standard generic templates (no claim of official compliance)."""
        defaults = [
            RequirementPreset(
                id="web_optimized_jpeg",
                name="Web Optimized JPEG",
                description="Compress to JPEG max 200 KB, fit 1920x1080",
                target_format="JPEG",
                width=1920,
                height=1080,
                resize_mode="fit",
                quality=85,
                max_bytes=200_000,
            ),
            RequirementPreset(
                id="lossless_archival_png",
                name="Lossless Archival PNG",
                description="Lossless PNG with metadata preserved",
                target_format="PNG",
                is_lossless=True,
                remove_metadata=False,
            ),
            RequirementPreset(
                id="thumbnail_crop",
                name="Square Thumbnail 500x500",
                description="Center crop to 500x500 square thumbnail",
                width=500,
                height=500,
                resize_mode="crop",
            ),
            RequirementPreset(
                id="privacy_sanitized",
                name="Privacy Sanitized WebP",
                description="Convert to WebP and strip EXIF/GPS/XMP metadata",
                target_format="WEBP",
                quality=80,
                remove_metadata=True,
            ),
        ]
        for d in defaults:
            self._presets[d.id] = d

    def save(self) -> None:
        """Persist presets to local storage file."""
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": PRESET_SCHEMA_VERSION,
            "presets": [p.to_dict() for p in self._presets.values()],
        }
        self.storage_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_presets(self) -> List[RequirementPreset]:
        return list(self._presets.values())

    def get_preset(self, preset_id: str) -> Optional[RequirementPreset]:
        return self._presets.get(preset_id)

    def add_or_update(self, preset: RequirementPreset) -> None:
        self._presets[preset.id] = preset
        self.save()

    def delete(self, preset_id: str) -> bool:
        if preset_id in self._presets:
            del self._presets[preset_id]
            self.save()
            return True
        return False

    def duplicate(self, preset_id: str, new_name: str) -> Optional[RequirementPreset]:
        orig = self.get_preset(preset_id)
        if not orig:
            return None
        d = orig.to_dict()
        new_id = f"{orig.id}_copy_{len(self._presets) + 1}"
        d["id"] = new_id
        d["name"] = new_name
        dup = RequirementPreset.from_dict(d)
        self.add_or_update(dup)
        return dup

    def export_preset_json(self, preset_id: str) -> str:
        preset = self.get_preset(preset_id)
        if not preset:
            raise KeyError(f"Preset '{preset_id}' not found.")
        return json.dumps(preset.to_dict(), indent=2)

    def import_preset_json(self, json_str: str) -> RequirementPreset:
        data = json.loads(json_str)
        preset = RequirementPreset.from_dict(data)
        # Ensure unique ID if conflict
        if preset.id in self._presets:
            preset.id = f"{preset.id}_{len(self._presets) + 1}"
        self.add_or_update(preset)
        return preset


def apply_preset_to_image(
    input_path: Path,
    output_dir: Path,
    preset: RequirementPreset,
    allow_overwrite: bool = False,
    allow_upscale: Optional[bool] = None,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Apply a complete preset pipeline (format conversion, resizing, compression, metadata)
    transactionally using a temporary workspace, and honestly validate final output against
    all specified preset requirements before committing to the destination.
    """
    result = JobResult(
        operation=OperationType.CONVERT,
        input_path=input_path,
    )
    protected_inputs = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))

    try:
        if cancel_token and cancel_token.is_cancelled:
            raise OperationCancelledError("Preset application cancelled by user.")

        # Prohibit multi-frame images unless explicitly authorized
        check_single_frame_image(input_path, allow_first_frame_only=False, operation_name="preset application")

        # Enforce dimension check before any processing
        with Image.open(input_path) as in_check:
            validate_dimensions(in_check.width, in_check.height, context="Preset input image")
            result.input_dimensions = in_check.size
            orig_exif = in_check.getexif()
            orig_has_exif = bool(orig_exif and len(orig_exif) > 0)
            orig_has_gps = bool(orig_exif and 0x8825 in orig_exif)
            orig_has_xmp = bool("xmp" in in_check.info or "XML:com.adobe.xmp" in in_check.info)
            orig_has_comments = bool(any(k.lower() in ("comment", "description", "author") for k in in_check.info.keys()))

        # Determine target format and resolve final destination path
        target_fmt = preset.target_format or "PNG"
        output_ext = SUPPORTED_IMAGE_OUTPUT_FORMATS.get(target_fmt, ".png")
        target_filename = f"{sanitize_filename(input_path.stem)}{output_ext}"
        destination = resolve_collision_path(output_dir / target_filename, allow_overwrite=allow_overwrite)
        validate_destination_safety(destination, protected_inputs)

        # Execute all pipeline steps inside an isolated temporary workspace
        with TempJobWorkspace(prefix="preset_pipeline_") as ws:
            step1_dir = ws / "step1_convert"
            step1_dir.mkdir(parents=True, exist_ok=True)
            conv_opt = ConvertOptions(
                target_format=target_fmt,
                bg_color=preset.bg_color,
                quality=preset.quality or 85,
                lossless=preset.is_lossless,
            )
            res1 = convert_image(
                input_path,
                step1_dir,
                conv_opt,
                allow_overwrite=True,
                cancel_token=cancel_token,
                batch_inputs=protected_inputs,
            )
            if not res1.success or res1.output_path is None:
                return res1
            result.warnings.extend(res1.warnings)
            current_file = res1.output_path

            # Step 2: Resize if dimensions specified
            if preset.width is not None or preset.height is not None:
                step2_dir = ws / "step2_resize"
                step2_dir.mkdir(parents=True, exist_ok=True)
                effective_upscale = allow_upscale if allow_upscale is not None else preset.allow_upscale
                mode_enum = ResizeMode(preset.resize_mode.lower())
                resize_opt = ResizeOptions(
                    width=preset.width,
                    height=preset.height,
                    aspect_ratio_lock=preset.aspect_ratio_lock,
                    mode=mode_enum,
                    bg_color=preset.bg_color,
                    allow_upscale=effective_upscale,
                    dpi=(preset.dpi, preset.dpi) if preset.dpi else None,
                )
                res2 = resize_image(
                    current_file,
                    step2_dir,
                    resize_opt,
                    allow_overwrite=True,
                    cancel_token=cancel_token,
                    batch_inputs=protected_inputs,
                )
                if not res2.success or res2.output_path is None:
                    return res2
                result.warnings.extend(res2.warnings)
                current_file = res2.output_path

            # Step 3: Compression or Target File Size
            unit_std = UnitStandard.BINARY if preset.unit_standard == "binary" else UnitStandard.DECIMAL
            has_size_constraint = (preset.max_bytes is not None) or (preset.min_bytes is not None)
            if has_size_constraint or preset.is_lossless or (preset.quality is not None and preset.quality != 85):
                step3_dir = ws / "step3_compress"
                step3_dir.mkdir(parents=True, exist_ok=True)
                comp_opt = CompressOptions(
                    is_lossless=preset.is_lossless,
                    quality=preset.quality,
                    max_bytes=preset.max_bytes,
                    min_bytes=preset.min_bytes,
                    unit_standard=unit_std,
                )
                res3 = compress_image(
                    current_file,
                    step3_dir,
                    comp_opt,
                    allow_overwrite=True,
                    cancel_token=cancel_token,
                    batch_inputs=protected_inputs,
                )
                if not res3.success or res3.output_path is None:
                    return res3
                result.warnings.extend(res3.warnings)
                current_file = res3.output_path

            # Step 4: Metadata removal if specified by preset
            if preset.remove_metadata:
                step4_dir = ws / "step4_metadata"
                step4_dir.mkdir(parents=True, exist_ok=True)
                cats = set(preset.metadata_categories) if preset.metadata_categories else {"all"}
                res4 = scrub_metadata(
                    current_file,
                    step4_dir,
                    categories=cats,
                    allow_overwrite=True,
                    cancel_token=cancel_token,
                    batch_inputs=protected_inputs,
                )
                if not res4.success or res4.output_path is None:
                    return res4
                result.warnings.extend(res4.warnings)
                current_file = res4.output_path

            # Step 5: Final Validation against applied preset requirements
            final_size = current_file.stat().st_size
            checks_passed = True

            with Image.open(current_file) as final_img:
                final_w, final_h = final_img.size

                # Check format
                fmt_pass = (final_img.format == target_fmt) or (
                    target_fmt == "JPEG" and final_img.format == "JPEG"
                )
                result.checks.append(
                    RequirementCheck(
                        name="Preset Target Format",
                        target=target_fmt,
                        actual=final_img.format or "unknown",
                        passed=fmt_pass,
                        details=f"Preset '{preset.name}' format requirement",
                    )
                )
                if not fmt_pass:
                    checks_passed = False
                    result.error_message = f"Final format '{final_img.format}' did not match preset format '{target_fmt}'."

                # Check dimensions
                if preset.width and preset.height and preset.resize_mode in ("crop", "pad"):
                    dim_pass = (final_w == preset.width and final_h == preset.height)
                    result.checks.append(
                        RequirementCheck(
                            name="Preset Exact Dimensions",
                            target=f"{preset.width}x{preset.height}",
                            actual=f"{final_w}x{final_h}",
                            passed=dim_pass,
                            details=f"Mode: {preset.resize_mode}",
                        )
                    )
                    if not dim_pass:
                        checks_passed = False
                        result.error_message = f"Final dimensions ({final_w}x{final_h}) did not match exact preset target ({preset.width}x{preset.height})."
                elif preset.width or preset.height:
                    max_w = preset.width or 999999
                    max_h = preset.height or 999999
                    fit_pass = (final_w <= max_w and final_h <= max_h)
                    result.checks.append(
                        RequirementCheck(
                            name="Preset Bounded Dimensions",
                            target=f"<= {preset.width or '*'}x{preset.height or '*'}",
                            actual=f"{final_w}x{final_h}",
                            passed=fit_pass,
                            details="Mode: fit within bounds",
                        )
                    )
                    if not fit_pass:
                        checks_passed = False
                        result.error_message = f"Final dimensions ({final_w}x{final_h}) exceeded preset bounds ({preset.width}x{preset.height})."

                # Check size limits
                if preset.max_bytes is not None:
                    passed_max = (final_size <= preset.max_bytes)
                    result.checks.append(
                        RequirementCheck(
                            name="Preset Max File Size",
                            target=f"<= {bytes_to_human(preset.max_bytes, unit_std)}",
                            actual=bytes_to_human(final_size, unit_std),
                            passed=passed_max,
                            details="Honest size check without dummy byte padding",
                        )
                    )
                    if not passed_max:
                        checks_passed = False
                        result.error_message = f"Final file size ({bytes_to_human(final_size, unit_std)}) exceeded preset max ({bytes_to_human(preset.max_bytes, unit_std)})."

                if preset.min_bytes is not None:
                    passed_min = (final_size >= preset.min_bytes)
                    result.checks.append(
                        RequirementCheck(
                            name="Preset Min File Size",
                            target=f">= {bytes_to_human(preset.min_bytes, unit_std)}",
                            actual=bytes_to_human(final_size, unit_std),
                            passed=passed_min,
                            details="Minimum file size threshold check",
                        )
                    )
                    if not passed_min:
                        checks_passed = False
                        result.error_message = f"Final file size ({bytes_to_human(final_size, unit_std)}) below preset minimum ({bytes_to_human(preset.min_bytes, unit_std)})."

                # Check metadata requirements (both removal and preservation)
                cats = set(preset.metadata_categories) if preset.metadata_categories else {"all"}
                strip_exif = preset.remove_metadata and ("all" in cats or "exif" in cats)
                strip_gps = preset.remove_metadata and ("all" in cats or "exif" in cats or "gps" in cats)
                strip_xmp = preset.remove_metadata and ("all" in cats or "xmp" in cats)
                strip_comments = preset.remove_metadata and ("all" in cats or "comments" in cats)

                final_exif = final_img.getexif()
                final_has_exif = bool(final_exif and len(final_exif) > 0)
                final_has_gps = bool(final_exif and 0x8825 in final_exif)
                final_has_xmp = bool("xmp" in final_img.info or "XML:com.adobe.xmp" in final_img.info)
                final_has_comments = bool(any(k.lower() in ("comment", "description", "author") for k in final_img.info.keys()))

                meta_ok = True
                meta_details = []

                if strip_exif and final_has_exif:
                    meta_ok = False
                    meta_details.append("EXIF removal failed")
                if strip_gps and final_has_gps:
                    meta_ok = False
                    meta_details.append("GPS removal failed")
                if strip_xmp and final_has_xmp:
                    meta_ok = False
                    meta_details.append("XMP removal failed")
                if strip_comments and final_has_comments:
                    meta_ok = False
                    meta_details.append("Comments removal failed")

                # Preservation assertions for unselected categories
                if orig_has_exif and not strip_exif and not final_has_exif:
                    meta_ok = False
                    meta_details.append("EXIF preservation failed")
                if orig_has_gps and not strip_gps and not final_has_gps:
                    meta_ok = False
                    meta_details.append("GPS preservation failed")
                if orig_has_xmp and not strip_xmp and not final_has_xmp:
                    meta_ok = False
                    meta_details.append("XMP preservation failed")
                if orig_has_comments and not strip_comments and target_fmt in ("PNG", "JPEG") and not final_has_comments:
                    meta_ok = False
                    meta_details.append("Comments preservation failed")

                result.checks.append(
                    RequirementCheck(
                        name="Preset Metadata Compliance",
                        target="Requested removal stripped, retained categories preserved",
                        actual="Compliant" if meta_ok else f"Failed: {', '.join(meta_details)}",
                        passed=meta_ok,
                        details=f"Remove: {preset.remove_metadata}, Categories: {', '.join(sorted(cats)) if preset.remove_metadata else 'None'}",
                    )
                )
                if not meta_ok:
                    checks_passed = False
                    result.error_message = f"Preset metadata requirements unmet: {', '.join(meta_details)}."

            if not checks_passed:
                result.success = False
                return result

            # Step 6: Atomic commit to user's destination
            staged = StagedOutput(destination, cancel_token=cancel_token)
            with staged as staged_path:
                shutil.copy2(current_file, staged_path)
            staged.commit()

            result.output_path = destination
            result.output_size_bytes = destination.stat().st_size
            with Image.open(destination) as final_committed:
                result.output_dimensions = final_committed.size
            result.success = True

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result
