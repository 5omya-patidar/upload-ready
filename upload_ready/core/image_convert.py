"""Image format conversion with orientation correction, transparency handling, and safe staging.

Preserves original files and outputs real re-encoded data.
Rejects overwriting input files.
Transparent channels are composited cleanly over chosen backgrounds when targeting
formats that do not support transparency (such as JPEG).
Guards against silent truncation of multi-frame/animated documents.
Preserves color profiles only when compatible with the target color space.
"""

import io
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from PIL import Image, ImageCms, ImageOps

from upload_ready.core.constants import (
    MAX_DECODED_PIXELS,
    SUPPORTED_IMAGE_OUTPUT_FORMATS,
)
from upload_ready.core.models import (
    CancellationToken,
    ConvertOptions,
    JobResult,
    OperationCancelledError,
    OperationType,
    RequirementCheck,
)
from upload_ready.core.safety import (
    StagedOutput,
    check_single_frame_image,
    resolve_collision_path,
    sanitize_filename,
    sniff_content_type,
    validate_destination_safety,
    validate_dimensions,
    validate_file_size,
)

Image.MAX_IMAGE_PIXELS = MAX_DECODED_PIXELS


def flatten_alpha_on_background(image: Image.Image, bg_color: Tuple[int, int, int]) -> Image.Image:
    """Composite an alpha-containing image over a solid background color."""
    # Note: Lab mode has 'A' band which is NOT alpha; only check genuine alpha modes
    if image.mode in ("RGBA", "LA", "PA") or ("transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, bg_color + (255,))
        composited = Image.alpha_composite(background, rgba)
        return composited.convert("RGB")
    return image


def prepare_color_profile_and_mode(
    img: Image.Image,
    target_mode: str,
    warnings: Optional[List[str]] = None,
) -> Tuple[Image.Image, Optional[bytes]]:
    """
    Ensure color profile compatibility with the target pixel mode.
    If color spaces match (e.g. RGB profile for RGB/RGBA mode), the ICC profile is retained.
    If color spaces differ (e.g. Lab/CMYK input converting to RGB), attempts a color-managed
    sRGB transformation; if unsupported or invalid, converts pixels and drops the mismatched profile
    with an explicit warning to prevent corrupt display in standard viewers.
    """
    icc_bytes = img.info.get("icc_profile")
    in_cs = None
    in_profile = None

    if icc_bytes:
        try:
            in_profile = ImageCms.getOpenProfile(io.BytesIO(icc_bytes))
            in_cs = in_profile.profile.xcolor_space.strip().upper()
        except Exception as exc:
            if warnings is not None:
                warnings.append(
                    f"Input image contained an invalid or corrupted ICC profile: {exc}. "
                    "The profile was ignored and pixels were processed without color management."
                )
            icc_bytes = None
            in_profile = None

    target_cs = "RGB" if target_mode in ("RGB", "RGBA") else (
        "GRAY" if target_mode in ("L", "LA") else ("CMYK" if target_mode == "CMYK" else None)
    )

    # Handle cross-color-space conversion (e.g. Lab -> RGB, CMYK -> RGB)
    if in_cs and target_cs and in_cs != target_cs:
        if in_profile and target_cs == "RGB":
            try:
                srgb = ImageCms.createProfile("sRGB")
                transformed = ImageCms.profileToProfile(img, in_profile, srgb, outputMode=target_mode, inPlace=0)
                return transformed, ImageCms.ImageCmsProfile(srgb).tobytes()
            except Exception as exc:
                if warnings is not None:
                    warnings.append(
                        f"Color management transform from {in_cs} to {target_cs} failed ({exc}). "
                        "Falling back to direct pixel mode conversion without profile."
                    )
        elif warnings is not None:
            warnings.append(
                f"Color space conversion from {in_cs} to {target_cs} is not supported by color manager. "
                "Converted pixel mode directly without attaching incompatible profile."
            )
        # Fallback: convert mode directly without attaching incompatible profile
        return img.convert(target_mode), None

    if not icc_bytes and img.mode in ("CMYK", "LAB") and target_cs == "RGB":
        if warnings is not None:
            warnings.append(
                f"Input image was in {img.mode} mode without an embedded ICC profile. "
                "Standard unmanaged conversion applied; color appearance may shift."
            )

    # Matching or unprofiled image
    if img.mode != target_mode:
        converted = img.convert(target_mode)
    else:
        converted = img

    if icc_bytes and in_cs == target_cs:
        return converted, icc_bytes

    return converted, None


def convert_image(
    input_path: Path,
    output_dir: Path,
    options: ConvertOptions,
    custom_stem: Optional[str] = None,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """Convert an image from any supported input format to JPEG, PNG, or WebP."""
    result = JobResult(
        operation=OperationType.CONVERT,
        input_path=input_path,
    )
    
    try:
        # Validate input existence and content
        input_size = validate_file_size(input_path)
        result.input_size_bytes = input_size
        detected_format = sniff_content_type(input_path)
        
        # Normalize format aliases (JPG -> JPEG)
        fmt_raw = options.target_format.upper().strip()
        target_fmt = "JPEG" if fmt_raw in ("JPEG", "JPG") else fmt_raw
        if target_fmt not in SUPPORTED_IMAGE_OUTPUT_FORMATS:
            raise ValueError(f"Target format '{options.target_format}' is unsupported. Choose from JPEG, PNG, WEBP.")

        # Multi-frame validation prior to transformation
        frame_count = check_single_frame_image(
            input_path,
            allow_first_frame_only=options.allow_first_frame_only,
            operation_name="format conversion",
        )
        if frame_count > 1:
            result.warnings.append(
                f"Multi-frame input detected ({frame_count} frames). "
                "Exported only the first frame per explicit user authorization."
            )

        output_ext = SUPPORTED_IMAGE_OUTPUT_FORMATS[target_fmt]
        stem = sanitize_filename(custom_stem or input_path.stem)
        target_filename = f"{stem}{output_ext}"
        destination = resolve_collision_path(output_dir / target_filename, allow_overwrite=allow_overwrite)

        # Prohibit overwriting any queued batch input
        protected_inputs = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))
        validate_destination_safety(destination, protected_inputs)

        with Image.open(input_path) as img:
            # Enforce dimensions before any transform or buffer allocation
            validate_dimensions(img.width, img.height, context="Input image")

            # Respect EXIF orientation tag before processing
            transposed = ImageOps.exif_transpose(img)
            orig_img = transposed if transposed is not None else img
            
            orig_w, orig_h = orig_img.size
            result.input_dimensions = (orig_w, orig_h)

            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("Operation cancelled before processing image.")

            # Prepare image mode and save arguments
            has_alpha = orig_img.mode in ("RGBA", "LA", "PA") or ("transparency" in orig_img.info)

            if target_fmt == "JPEG":
                # JPEG does not support alpha; composite over user background
                flattened = flatten_alpha_on_background(orig_img, options.bg_color)
                processed, out_icc = prepare_color_profile_and_mode(flattened, "RGB", warnings=result.warnings)
                save_kwargs = {
                    "format": "JPEG",
                    "quality": options.quality,
                    "optimize": True,
                }
            elif target_fmt == "PNG":
                target_mode = "RGBA" if has_alpha else ("L" if orig_img.mode == "L" else "RGB")
                processed, out_icc = prepare_color_profile_and_mode(orig_img, target_mode, warnings=result.warnings)
                save_kwargs = {
                    "format": "PNG",
                    "optimize": options.lossless,
                }
            elif target_fmt == "WEBP":
                target_mode = "RGBA" if has_alpha else ("L" if orig_img.mode == "L" else "RGB")
                processed, out_icc = prepare_color_profile_and_mode(orig_img, target_mode, warnings=result.warnings)
                save_kwargs = {
                    "format": "WEBP",
                    "lossless": options.lossless,
                    "quality": options.quality if not options.lossless else 100,
                }
                # Preserve exact RGB values beneath transparent pixels in lossless WebP
                if options.lossless:
                    save_kwargs["exact"] = True
            else:
                raise ValueError(f"Unhandled target format: {target_fmt}")

            # The color management result (out_icc) is authoritative:
            if out_icc:
                save_kwargs["icc_profile"] = out_icc
                processed.info["icc_profile"] = out_icc
            else:
                save_kwargs.pop("icc_profile", None)
                processed.info.pop("icc_profile", None)

            # Carry forward source metadata if requested
            if options.preserve_metadata:
                from upload_ready.core.metadata import build_save_kwargs, extract_image_metadata, filter_image_metadata
                raw_meta = extract_image_metadata(img)
                # Color management result authoritatively replaces or clears rejected/corrupted source profile:
                raw_meta["icc_profile"] = out_icc
                filtered_meta = filter_image_metadata(
                    raw_meta,
                    categories=set(),
                    orientation_transposed=bool(transposed is not None),
                )
                meta_kwargs = build_save_kwargs(target_fmt, filtered_meta, warnings=result.warnings, image_mode=processed.mode)
                save_kwargs.update(meta_kwargs)
                if not out_icc:
                    save_kwargs.pop("icc_profile", None)

            if processed.mode in ("RGBA", "LA", "PA"):
                save_kwargs.pop("transparency", None)
                processed.info.pop("transparency", None)

            # Notice on lossy -> lossless conversion
            if detected_format == "JPEG" and target_fmt in ("PNG", "WEBP") and options.lossless:
                result.warnings.append(
                    "Converting a lossy JPEG into a lossless format preserves existing pixels, "
                    "but does not restore compression artifacts or lost original detail."
                )

            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("Operation cancelled before saving output.")

            # Stage output in temporary file and commit atomically
            staged = StagedOutput(destination, cancel_token=cancel_token)
            with staged as staged_path:
                processed.save(staged_path, **save_kwargs)
            staged.commit()
            
            result.output_path = destination
            result.output_dimensions = processed.size
            result.output_size_bytes = destination.stat().st_size
            result.success = True

            # Populate validation checks
            result.checks.append(
                RequirementCheck(
                    name="Format Encoding",
                    target=target_fmt,
                    actual=target_fmt,
                    passed=True,
                    details=f"Re-encoded from {detected_format} to {target_fmt}",
                )
            )
            result.checks.append(
                RequirementCheck(
                    name="Dimensions Preservation",
                    target=f"{result.input_dimensions[0]}x{result.input_dimensions[1]}",
                    actual=f"{result.output_dimensions[0]}x{result.output_dimensions[1]}",
                    passed=(result.input_dimensions == result.output_dimensions),
                    details="Dimensions preserved during format conversion",
                )
            )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result
