"""Image resizing, dimension calculation, aspect ratio handling, and DPI management.

Provides Fit, Crop, and Pad modes using Lanczos resampling.
Guards against accidental upscaling and distinguishes between pixel resampling and DPI metadata.
Uses content-sniffed format rather than extension to preserve transparency.
Stages outputs to protect original files and avoid partial writes.
"""

import math
from pathlib import Path
from typing import Iterable, Optional, Tuple
from PIL import Image, ImageOps

from upload_ready.core.constants import (
    MAX_DECODED_PIXELS,
    SUPPORTED_IMAGE_OUTPUT_FORMATS,
)
from upload_ready.core.image_convert import flatten_alpha_on_background, prepare_color_profile_and_mode
from upload_ready.core.models import (
    CancellationToken,
    JobResult,
    OperationCancelledError,
    OperationType,
    RequirementCheck,
    ResizeMode,
    ResizeOptions,
)
from upload_ready.core.safety import (
    StagedOutput,
    check_single_frame_image,
    resolve_collision_path,
    sanitize_filename,
    sniff_content_type,
    validate_destination_safety,
    validate_dimensions,
    validate_dpi,
    validate_file_size,
)

Image.MAX_IMAGE_PIXELS = MAX_DECODED_PIXELS


def calculate_target_dimensions(
    orig_w: int,
    orig_h: int,
    target_w: Optional[int],
    target_h: Optional[int],
    aspect_ratio_lock: bool,
) -> Tuple[int, int]:
    """Calculate resolved (width, height) targets considering aspect ratio."""
    if not target_w and not target_h:
        return orig_w, orig_h

    if target_w and not target_h:
        if aspect_ratio_lock:
            ratio = orig_h / orig_w
            return target_w, max(1, round(target_w * ratio))
        return target_w, orig_h

    if target_h and not target_w:
        if aspect_ratio_lock:
            ratio = orig_w / orig_h
            return max(1, round(target_h * ratio)), target_h
        return orig_w, target_h

    assert target_w is not None and target_h is not None
    return target_w, target_h


def apply_resize(
    img: Image.Image,
    target_w: int,
    target_h: int,
    mode: ResizeMode,
    aspect_ratio_lock: bool = True,
    bg_color: Tuple[int, int, int] = (255, 255, 255),
    allow_upscale: bool = False,
) -> Tuple[Image.Image, Tuple[int, int], bool]:
    """
    Resize image using specified mode.
    Accurately determines whether pixel upscaling occurs based on the actual scale factor.
    Returns (result_image, (final_w, final_h), was_upscaled).
    """
    orig_w, orig_h = img.size

    # Validate output dimensions before allocating
    validate_dimensions(target_w, target_h, context="Target resize")

    # If aspect ratio is explicitly unlocked, stretch directly to target_w, target_h
    if not aspect_ratio_lock:
        needs_upscale = (target_w > orig_w) or (target_h > orig_h)
        if needs_upscale and not allow_upscale:
            raise ValueError(
                f"Requested resize to {target_w}x{target_h} requires upscaling from original "
                f"{orig_w}x{orig_h}. Enable 'Allow deliberate upscaling' to proceed. "
                "Note: Upscaling does not restore missing detail."
            )
        resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        return resized, (target_w, target_h), needs_upscale

    if mode == ResizeMode.FIT:
        # Scale proportionally to fit within target bounding box
        ratio = min(target_w / orig_w, target_h / orig_h)
        scaled_w = max(1, round(orig_w * ratio))
        scaled_h = max(1, round(orig_h * ratio))
        needs_upscale = (scaled_w > orig_w) or (scaled_h > orig_h)
        if needs_upscale and not allow_upscale:
            raise ValueError(
                f"Requested resize to fit inside {target_w}x{target_h} results in {scaled_w}x{scaled_h}, "
                f"which requires upscaling from original {orig_w}x{orig_h}. "
                "Enable 'Allow deliberate upscaling' to proceed. Note: Upscaling does not restore missing detail."
            )
        resized = img.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        return resized, (scaled_w, scaled_h), needs_upscale

    elif mode == ResizeMode.CROP:
        # Scale to fill bounding box, then center-crop
        ratio = max(target_w / orig_w, target_h / orig_h)
        scaled_w = max(1, round(orig_w * ratio))
        scaled_h = max(1, round(orig_h * ratio))
        needs_upscale = (scaled_w > orig_w) or (scaled_h > orig_h)
        if needs_upscale and not allow_upscale:
            raise ValueError(
                f"Requested resize with crop to {target_w}x{target_h} requires scaling content to {scaled_w}x{scaled_h}, "
                f"which requires upscaling from original {orig_w}x{orig_h}. "
                "Enable 'Allow deliberate upscaling' to proceed. Note: Upscaling does not restore missing detail."
            )

        # Bounded source-box resampling directly to target size:
        # Prevents allocating massive intermediate scaled bitmaps when aspect ratios diverge
        crop_src_w = target_w / ratio
        crop_src_h = target_h / ratio
        left = (orig_w - crop_src_w) / 2.0
        top = (orig_h - crop_src_h) / 2.0
        right = left + crop_src_w
        bottom = top + crop_src_h
        cropped = img.resize((target_w, target_h), resample=Image.Resampling.LANCZOS, box=(left, top, right, bottom))
        return cropped, (target_w, target_h), needs_upscale

    elif mode == ResizeMode.PAD:
        # Scale to fit inside bounding box, then pad with bg_color to exact target_w, target_h
        ratio = min(target_w / orig_w, target_h / orig_h)
        scaled_w = max(1, round(orig_w * ratio))
        scaled_h = max(1, round(orig_h * ratio))
        needs_upscale = (scaled_w > orig_w) or (scaled_h > orig_h)
        if needs_upscale and not allow_upscale:
            raise ValueError(
                f"Requested resize with padding to {target_w}x{target_h} requires scaling content to {scaled_w}x{scaled_h}, "
                f"which requires upscaling from original {orig_w}x{orig_h}. "
                "Enable 'Allow deliberate upscaling' to proceed. Note: Upscaling does not restore missing detail."
            )
        scaled = img.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        
        # Canvas creation
        canvas_mode = "RGBA" if img.mode == "RGBA" else "RGB"
        canvas_bg = bg_color + ((255,) if canvas_mode == "RGBA" else ())
        canvas = Image.new(canvas_mode, (target_w, target_h), canvas_bg)
        
        offset_x = (target_w - scaled_w) // 2
        offset_y = (target_h - scaled_h) // 2
        
        if scaled.mode == "RGBA" and canvas_mode == "RGBA":
            canvas.alpha_composite(scaled, (offset_x, offset_y))
        else:
            canvas.paste(scaled, (offset_x, offset_y))
            
        return canvas, (target_w, target_h), needs_upscale

    else:
        raise ValueError(f"Unknown resize mode: {mode}")


def resize_image(
    input_path: Path,
    output_dir: Path,
    options: ResizeOptions,
    custom_stem: Optional[str] = None,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """Resize an image according to specified dimensions, mode, and constraints."""
    result = JobResult(
        operation=OperationType.RESIZE,
        input_path=input_path,
    )
    
    try:
        input_size = validate_file_size(input_path)
        result.input_size_bytes = input_size
        detected_fmt = sniff_content_type(input_path)

        # Multi-frame validation: refuse multi-frame inputs in resize
        check_single_frame_image(input_path, allow_first_frame_only=False, operation_name="image resizing")

        if options.dpi:
            validate_dpi(options.dpi[0])
            validate_dpi(options.dpi[1])

        with Image.open(input_path) as img:
            # Enforce dimensions before any transform or buffer allocation
            validate_dimensions(img.width, img.height, context="Input image")

            transposed = ImageOps.exif_transpose(img)
            orig_img = transposed if transposed is not None else img
            
            orig_w, orig_h = orig_img.size
            result.input_dimensions = (orig_w, orig_h)

            target_w, target_h = calculate_target_dimensions(
                orig_w, orig_h, options.width, options.height, options.aspect_ratio_lock
            )
            # Enforce target dimensions before allocating resized image or intermediate canvases
            validate_dimensions(target_w, target_h, context="Target resize dimensions")

            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("Resize operation cancelled by user.")

            mode = options.mode if isinstance(options.mode, ResizeMode) else ResizeMode(str(options.mode))

            # Normalize colour-key or palette transparency into an alpha working representation BEFORE resizing
            has_transparency = (
                ("transparency" in orig_img.info)
                or (orig_img.mode in ("PA", "P") and "transparency" in orig_img.info)
            )
            if has_transparency:
                working_input = orig_img.convert("RGBA" if orig_img.mode != "L" else "LA")
                working_input.info.pop("transparency", None)
            else:
                working_input = orig_img

            # Apply resizing on working representation
            resized_img, final_dims, was_upscaled = apply_resize(
                working_input,
                target_w,
                target_h,
                mode,
                aspect_ratio_lock=options.aspect_ratio_lock,
                bg_color=options.bg_color,
                allow_upscale=options.allow_upscale,
            )

            if was_upscaled:
                result.warnings.append(
                    "Image was upscaled beyond its original pixel dimensions. "
                    "Upscaling interpolates pixels and does not recover original fine detail."
                )

            # Determine destination extension and format from detected content, not filename extension
            fmt = "JPEG" if detected_fmt == "JPEG" else ("WEBP" if detected_fmt == "WEBP" else "PNG")
            ext = SUPPORTED_IMAGE_OUTPUT_FORMATS.get(fmt, ".png")
                
            stem = sanitize_filename(custom_stem or input_path.stem)
            target_filename = f"{stem}{ext}"
            destination = resolve_collision_path(output_dir / target_filename, allow_overwrite=allow_overwrite)

            # Prohibit overwriting any queued batch input
            protected_inputs = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))
            validate_destination_safety(destination, protected_inputs)

            # Prepare image mode and compatible ICC profile
            has_alpha = resized_img.mode in ("RGBA", "LA", "PA") or ("transparency" in resized_img.info)
            if fmt == "JPEG":
                flattened = flatten_alpha_on_background(resized_img, options.bg_color)
                processed, out_icc = prepare_color_profile_and_mode(flattened, "RGB", warnings=result.warnings)
            else:
                target_mode = "RGBA" if has_alpha else ("L" if resized_img.mode == "L" else "RGB")
                processed, out_icc = prepare_color_profile_and_mode(resized_img, target_mode, warnings=result.warnings)

            # The color management result (out_icc) is authoritative:
            save_kwargs = {}
            if options.dpi:
                save_kwargs["dpi"] = options.dpi
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
                raw_meta["icc_profile"] = out_icc
                filtered_meta = filter_image_metadata(
                    raw_meta,
                    categories=set(),
                    orientation_transposed=bool(transposed is not None),
                )
                meta_kwargs = build_save_kwargs(fmt, filtered_meta, warnings=result.warnings, image_mode=processed.mode)
                save_kwargs.update(meta_kwargs)
                if not out_icc:
                    save_kwargs.pop("icc_profile", None)

            if processed.mode in ("RGBA", "LA", "PA"):
                save_kwargs.pop("transparency", None)
                processed.info.pop("transparency", None)

            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("Operation cancelled before writing output.")
                
            # Stage output in temporary file and commit atomically
            staged = StagedOutput(destination, cancel_token=cancel_token)
            with staged as staged_path:
                processed.save(staged_path, format=fmt, **save_kwargs)
            staged.commit()

            result.output_path = destination
            result.output_dimensions = final_dims
            result.output_size_bytes = destination.stat().st_size
            result.success = True

            # Validation checks
            passed_dim_check = (
                (final_dims == (target_w, target_h))
                if mode in (ResizeMode.CROP, ResizeMode.PAD) or not options.aspect_ratio_lock
                else (final_dims[0] <= target_w and final_dims[1] <= target_h)
            )
            result.checks.append(
                RequirementCheck(
                    name="Target Dimensions",
                    target=f"{target_w}x{target_h} ({mode.value})",
                    actual=f"{final_dims[0]}x{final_dims[1]}",
                    passed=passed_dim_check,
                    details=f"Mode: {mode.value}, Aspect Ratio Locked: {options.aspect_ratio_lock}",
                )
            )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result
