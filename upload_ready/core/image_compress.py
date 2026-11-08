"""Image compression, quality tuning, target file-size search, and pixel fidelity verification.

Distinguishes between lossless and lossy operations with honest reporting.
Guards against accidental JPEG lossless claims and transparent-RGB truncation.
Ranks compliant target-size candidates by encoder quality preference.
Requires explicit consent for lossy palette reduction on PNG.
Stages outputs to prevent partial writes and protect original files.
"""

import io
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from PIL import Image, ImageChops, ImageCms, ImageOps

from upload_ready.core.constants import (
    MAX_DECODED_PIXELS,
    MAX_OPTIMIZER_ITERATIONS,
    SUPPORTED_IMAGE_OUTPUT_FORMATS,
    UnitStandard,
    bytes_to_human,
)
from upload_ready.core.image_convert import flatten_alpha_on_background, prepare_color_profile_and_mode
from upload_ready.core.models import (
    CancellationToken,
    CompressOptions,
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


def validate_native_icc_profile(
    icc_bytes: Optional[bytes],
    mode: str,
    warnings: Optional[list] = None,
) -> Optional[bytes]:
    """
    Validate embedded ICC profile bytes for native-mode compression paths.
    Returns valid compatible ICC bytes, or None if corrupted/invalid/incompatible.
    Does not convert or mutate native pixel arrays.
    """
    if not icc_bytes:
        return None
    try:
        prof = ImageCms.getOpenProfile(io.BytesIO(icc_bytes))
        cs = prof.profile.xcolor_space.strip().upper()
        target_cs = "RGB" if mode in ("RGB", "RGBA", "P", "PA", "RGBX") else (
            "GRAY" if mode in ("L", "LA", "I", "I;16", "I;16L", "I;16B", "F") else ("CMYK" if mode == "CMYK" else None)
        )
        if target_cs and cs != target_cs and cs != "RGB":
            if warnings is not None:
                warnings.append(
                    f"Input image contained an incompatible {cs} ICC profile for {mode} mode. "
                    "Dropped profile to avoid display corruption."
                )
            return None
        return icc_bytes
    except Exception as exc:
        if warnings is not None:
            warnings.append(
                f"Input image contained an invalid or corrupted ICC profile: {exc}. "
                "The profile was ignored and pixels were processed without color management."
            )
        return None


def are_images_pixel_identical(orig: Image.Image, candidate: Image.Image) -> bool:
    """
    Check that candidate decoded pixels match original decoded pixels 100% across all channels.
    Handles EXIF orientation normalization, Palette (P) mode color and transparency comparison,
    high-bit-depth modes (I;16, I, F) without lossy downcasting, and all channel band extrema.
    """
    # EXIF orientation normalization on both images
    t_orig = ImageOps.exif_transpose(orig)
    norm_orig = t_orig if t_orig is not None else orig
    t_cand = ImageOps.exif_transpose(candidate)
    norm_cand = t_cand if t_cand is not None else candidate

    if norm_orig.size != norm_cand.size:
        return False

    # Palette (P) mode: must compare decoded RGBA values to check both palette colors and transparency
    if norm_orig.mode == "P" or norm_cand.mode == "P":
        img1 = norm_orig.convert("RGBA")
        img2 = norm_cand.convert("RGBA")
        diff = ImageChops.difference(img1, img2)
        extrema = diff.getextrema()
        return all(band_max == 0 for _, band_max in extrema)

    # High-bit-depth modes: compare exact binary raster buffers without 8-bit downcasting
    if norm_orig.mode in ("I", "I;16", "I;16L", "I;16B", "F"):
        if norm_orig.mode != norm_cand.mode:
            return False
        return norm_orig.tobytes() == norm_cand.tobytes()

    # Genuine alpha check (avoids Lab 'A' channel confusion)
    orig_has_alpha = norm_orig.mode in ("RGBA", "LA", "PA") or ("transparency" in norm_orig.info)
    cand_has_alpha = norm_cand.mode in ("RGBA", "LA", "PA") or ("transparency" in norm_cand.info)

    if norm_orig.mode != norm_cand.mode:
        target_mode = "RGBA" if (orig_has_alpha or cand_has_alpha) else "RGB"
        img1 = norm_orig.convert(target_mode)
        img2 = norm_cand.convert(target_mode)
    else:
        img1 = norm_orig
        img2 = norm_cand

    diff = ImageChops.difference(img1, img2)
    extrema = diff.getextrema()

    if isinstance(extrema[0], tuple):
        return all(band_max == 0 for _, band_max in extrema)
    else:
        return extrema[1] == 0


def verify_lossless_pixels(orig: Image.Image, candidate_bytes: bytes, fmt: str) -> bool:
    """Verify that candidate decoded pixels match original decoded pixels across all channels."""
    with Image.open(io.BytesIO(candidate_bytes)) as decoded:
        return are_images_pixel_identical(orig, decoded)


def encode_candidate(
    img: Image.Image,
    fmt: str,
    quality: int,
    is_lossless: bool = False,
    quantize_colors: Optional[int] = None,
    icc_profile: Optional[bytes] = None,
    meta_kwargs: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Encode an image candidate in memory, preserving ICC profile and metadata if present."""
    buf = io.BytesIO()
    fmt_upper = fmt.upper()
    kwargs = {}
    if meta_kwargs:
        for k, v in meta_kwargs.items():
            if k not in ("format", "quality", "optimize", "lossless", "compress_level"):
                kwargs[k] = v

    if icc_profile:
        kwargs["icc_profile"] = icc_profile
    else:
        kwargs.pop("icc_profile", None)
        img.info.pop("icc_profile", None)

    if img.mode in ("RGBA", "LA", "PA"):
        kwargs.pop("transparency", None)
        img.info.pop("transparency", None)
    
    if fmt_upper in ("JPEG", "JPG"):
        rgb_img = img.convert("RGB")
        rgb_img.save(buf, format="JPEG", quality=quality, optimize=True, **kwargs)
    elif fmt_upper == "PNG":
        if is_lossless:
            img.save(buf, format="PNG", optimize=True, compress_level=9, **kwargs)
        elif quantize_colors is not None:
            quantized = img.quantize(colors=quantize_colors)
            quantized.save(buf, format="PNG", optimize=True, **kwargs)
        else:
            img.save(buf, format="PNG", optimize=True, **kwargs)
    elif fmt_upper == "WEBP":
        if is_lossless:
            # exact=True ensures RGB values under transparent alpha (A=0) are preserved
            img.save(buf, format="WEBP", lossless=True, exact=True, quality=100, method=6, **kwargs)
        else:
            img.save(buf, format="WEBP", lossless=False, quality=quality, method=6, **kwargs)
    else:
        raise ValueError(f"Unsupported format for encoding candidate: {fmt}")
        
    return buf.getvalue()


def search_target_size(
    source_img: Image.Image,
    fmt: str,
    max_bytes: Optional[int],
    min_bytes: Optional[int],
    unit_standard: UnitStandard = UnitStandard.DECIMAL,
    allow_palette_quantization: bool = False,
    icc_profile: Optional[bytes] = None,
    meta_kwargs: Optional[Dict[str, Any]] = None,
    cancel_token: Optional[CancellationToken] = None,
) -> Tuple[bytes, int, Optional[int], bool, str]:
    """
    Bounded binary search for target file size.
    Ranks compliant candidates by highest tested encoder quality preference.
    Describes unreached limits honestly without claiming unproven mathematical bounds.
    Returns: (best_bytes, best_size, best_quality, passed, report_note)
    """
    fmt_upper = "JPEG" if fmt.upper() in ("JPEG", "JPG") else fmt.upper()
    
    target_max = max_bytes if max_bytes is not None else float("inf")
    target_min = min_bytes if min_bytes is not None else 0
    
    if target_min > target_max:
        raise ValueError(f"Minimum target size ({target_min} B) cannot exceed maximum target size ({target_max} B).")

    if fmt_upper in ("JPEG", "WEBP"):
        low_q = 5
        high_q = 95
        best_compliant_bytes = None
        best_compliant_size = 0
        best_compliant_q = None

        closest_fallback_bytes = None
        closest_fallback_size = 0
        closest_fallback_q = None
        closest_fallback_diff = float("inf")
        
        left = low_q
        right = high_q
        
        for _ in range(MAX_OPTIMIZER_ITERATIONS):
            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("Target size search cancelled by user.")

            if left > right:
                break
            mid_q = (left + right) // 2
            cand = encode_candidate(source_img, fmt_upper, quality=mid_q, icc_profile=icc_profile, meta_kwargs=meta_kwargs)
            c_size = len(cand)
            
            is_compliant = (c_size <= target_max) and (c_size >= target_min)
            
            target_goal = target_max if target_max != float("inf") else (target_min * 1.2)
            diff = abs(c_size - target_goal)
            
            if diff < closest_fallback_diff or closest_fallback_bytes is None:
                closest_fallback_diff = diff
                closest_fallback_bytes = cand
                closest_fallback_size = c_size
                closest_fallback_q = mid_q

            if is_compliant:
                # Prioritize higher encoder quality among compliant candidates
                if best_compliant_bytes is None or mid_q > best_compliant_q:
                    best_compliant_bytes = cand
                    best_compliant_size = c_size
                    best_compliant_q = mid_q
                elif mid_q == best_compliant_q and c_size < best_compliant_size:
                    best_compliant_bytes = cand
                    best_compliant_size = c_size
                # Search for higher quality under target_max
                left = mid_q + 1
            else:
                if c_size > target_max:
                    right = mid_q - 1
                else:
                    left = mid_q + 1

        if best_compliant_bytes is not None:
            note = f"Found compliant encoder quality {best_compliant_q} with actual size {bytes_to_human(best_compliant_size, unit_standard)}"
            return best_compliant_bytes, best_compliant_size, best_compliant_q, True, note
        else:
            assert closest_fallback_bytes is not None
            if closest_fallback_size > target_max:
                note = (
                    f"Target size not reached with selected settings. Closest tested candidate was "
                    f"{bytes_to_human(closest_fallback_size, unit_standard)} at encoder quality {closest_fallback_q} "
                    f"(target max: {bytes_to_human(int(target_max), unit_standard)}). "
                    "Consider reducing pixel dimensions or switching to a more compressible format."
                )
            else:
                note = (
                    f"Target size not reached with selected settings. Closest tested candidate was "
                    f"{bytes_to_human(closest_fallback_size, unit_standard)} at encoder quality {closest_fallback_q} "
                    f"(target min: {bytes_to_human(target_min, unit_standard)}). "
                    "Upload Ready does not add dummy byte padding to artificially inflate file size."
                )
            return closest_fallback_bytes, closest_fallback_size, closest_fallback_q, False, note

    elif fmt_upper == "PNG":
        # PNG: Test lossless Deflate optimization first
        cand = encode_candidate(source_img, "PNG", quality=100, is_lossless=True, icc_profile=icc_profile, meta_kwargs=meta_kwargs)
        c_size = len(cand)
        if (c_size <= target_max) and (c_size >= target_min):
            return cand, c_size, None, True, f"Lossless PNG met target: {bytes_to_human(c_size, unit_standard)}"

        # If lossless exceeds target and user has not allowed palette quantization, reject with clear advice
        if not allow_palette_quantization:
            if c_size > target_max:
                note = (
                    f"Target size not reached with the selected settings. Lossless PNG size is "
                    f"{bytes_to_human(c_size, unit_standard)} (target max: {bytes_to_human(int(target_max), unit_standard)}). "
                    "Enable 'Allow lossy palette quantization' to permit color reduction, or convert to WebP/JPEG."
                )
            else:
                note = (
                    f"Target size not reached with the selected settings. Lossless PNG size is "
                    f"{bytes_to_human(c_size, unit_standard)} (below target min: {bytes_to_human(target_min, unit_standard)}). "
                    "Upload Ready does not add dummy byte padding to artificially inflate file size."
                )
            return cand, c_size, None, False, note

        # User opted into lossy palette quantization
        color_levels = [256, 128, 64, 32, 16]
        best_compliant_cand = None
        best_compliant_size = 0
        best_compliant_colors = None

        closest_fallback_cand = cand
        closest_fallback_size = c_size
        closest_fallback_colors = None
        target_reference = target_max if target_max != float("inf") else target_min
        closest_fallback_diff = abs(c_size - target_reference)
        
        for colors in color_levels:
            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("Target size search cancelled by user.")

            q_cand = encode_candidate(source_img, "PNG", quality=100, quantize_colors=colors, icc_profile=icc_profile, meta_kwargs=meta_kwargs)
            q_size = len(q_cand)
            diff = abs(q_size - target_reference)

            if diff < closest_fallback_diff:
                closest_fallback_diff = diff
                closest_fallback_cand = q_cand
                closest_fallback_size = q_size
                closest_fallback_colors = colors

            if (q_size <= target_max) and (q_size >= target_min):
                # Prioritize maximum retained colors
                if best_compliant_cand is None or colors > best_compliant_colors:
                    best_compliant_cand = q_cand
                    best_compliant_size = q_size
                    best_compliant_colors = colors

        if best_compliant_cand is not None:
            note = f"Compliant PNG reached via lossy palette quantization to {best_compliant_colors} colors ({bytes_to_human(best_compliant_size, unit_standard)})"
            return best_compliant_cand, best_compliant_size, None, True, note
        else:
            if closest_fallback_size > target_max:
                note = (
                    f"Target size not reached with selected settings. Closest tested candidate was "
                    f"{bytes_to_human(closest_fallback_size, unit_standard)} with {closest_fallback_colors} colors "
                    f"(target max: {bytes_to_human(int(target_max), unit_standard)}). "
                    "Consider converting to JPEG or WebP, or reducing pixel dimensions."
                )
            else:
                note = (
                    f"Target size not reached with selected settings. Image encoded size is {bytes_to_human(closest_fallback_size, unit_standard)} "
                    f"which is below target minimum ({bytes_to_human(target_min, unit_standard)}). "
                    "Upload Ready does not add dummy byte padding to artificially inflate file size."
                )
            return closest_fallback_cand, closest_fallback_size, None, False, note

    raise ValueError(f"Unsupported format: {fmt_upper}")


def compress_image(
    input_path: Path,
    output_dir: Path,
    options: CompressOptions,
    custom_stem: Optional[str] = None,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """Compress image with honest reporting and pixel-fidelity validation."""
    result = JobResult(
        operation=OperationType.COMPRESS,
        input_path=input_path,
    )
    
    try:
        input_size = validate_file_size(input_path)
        result.input_size_bytes = input_size
        detected_fmt = sniff_content_type(input_path)

        # Multi-frame validation: refuse multi-frame inputs in compression
        check_single_frame_image(input_path, allow_first_frame_only=False, operation_name="image compression")

        # Reject impossible range bounds
        if options.min_bytes is not None and options.max_bytes is not None and options.min_bytes > options.max_bytes:
            raise ValueError(f"Minimum size ({options.min_bytes} B) cannot exceed maximum target size ({options.max_bytes} B).")

        # Reject lossless mode on JPEG
        if detected_fmt == "JPEG" and options.is_lossless:
            raise ValueError(
                "Lossless optimization is not available for JPEG: True lossless compression is not supported for JPEG by "
                "Pillow re-encoding (DCT re-quantization alters pixel values). "
                "To compress losslessly, convert to PNG/WEBP, or provide quality/target-size parameters for lossy compression."
            )

        with Image.open(input_path) as img:
            # Enforce dimensions before any transform or buffer allocation
            validate_dimensions(img.width, img.height, context="Input image")

            transposed = ImageOps.exif_transpose(img)
            orig_img = transposed if transposed is not None else img
            
            orig_w, orig_h = orig_img.size
            result.input_dimensions = (orig_w, orig_h)

            # Output format matches detected content format
            fmt = "JPEG" if detected_fmt == "JPEG" else ("WEBP" if detected_fmt == "WEBP" else "PNG")
            ext = SUPPORTED_IMAGE_OUTPUT_FORMATS.get(fmt, ".png")

            stem = sanitize_filename(custom_stem or input_path.stem)
            target_filename = f"{stem}{ext}"
            destination = resolve_collision_path(output_dir / target_filename, allow_overwrite=allow_overwrite)

            # Prohibit overwriting any queued batch input
            protected_inputs = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))
            validate_destination_safety(destination, protected_inputs)

            is_high_bit_depth = orig_img.mode in ("I", "I;16", "I;16L", "I;16B", "F")
            if is_high_bit_depth:
                if options.is_lossless:
                    if fmt != "PNG":
                        raise ValueError(
                            f"Lossless compression for high-bit-depth image mode '{orig_img.mode}' is not supported by {fmt}. "
                            f"{fmt} only supports 8-bit precision. To preserve {orig_img.mode} pixel precision losslessly, use PNG."
                        )
                    # Preserve native high-bit-depth format for lossless PNG without downcasting
                    working_img = orig_img
                    out_icc = validate_native_icc_profile(
                        orig_img.info.get("icc_profile"), orig_img.mode, warnings=result.warnings
                    )
                else:
                    result.warnings.append(
                        f"High-bit-depth input ({orig_img.mode}) converted to 8-bit {fmt}. Precision reduced."
                    )
                    flattened = orig_img.convert("RGB")
                    working_img, out_icc = prepare_color_profile_and_mode(flattened, "RGB", warnings=result.warnings)
            elif orig_img.mode == "P" and fmt == "PNG" and options.is_lossless:
                # Retain P mode with palette transparency for lossless PNG compression
                working_img = orig_img
                out_icc = validate_native_icc_profile(
                    orig_img.info.get("icc_profile"), orig_img.mode, warnings=result.warnings
                )
            else:
                has_alpha = orig_img.mode in ("RGBA", "LA", "PA") or ("transparency" in orig_img.info)
                if fmt == "JPEG":
                    flattened = flatten_alpha_on_background(orig_img, (255, 255, 255))
                    working_img, out_icc = prepare_color_profile_and_mode(flattened, "RGB", warnings=result.warnings)
                else:
                    target_mode = "RGBA" if has_alpha else ("L" if orig_img.mode == "L" else "RGB")
                    working_img, out_icc = prepare_color_profile_and_mode(orig_img, target_mode, warnings=result.warnings)

            # The color management result (out_icc) is authoritative:
            if out_icc:
                working_img.info["icc_profile"] = out_icc
            else:
                working_img.info.pop("icc_profile", None)

            encoded_bytes: bytes
            actual_quality: Optional[int] = None
            size_search_passed: Optional[bool] = None
            size_note: str = ""

            has_target_size = (options.max_bytes is not None) or (options.min_bytes is not None)

            meta_kwargs: Dict[str, Any] = {}
            if options.preserve_metadata:
                from upload_ready.core.metadata import build_save_kwargs, extract_image_metadata, filter_image_metadata
                raw_meta = extract_image_metadata(img)
                raw_meta["icc_profile"] = out_icc
                filtered_meta = filter_image_metadata(
                    raw_meta,
                    categories=set(),
                    orientation_transposed=bool(transposed is not None),
                )
                meta_kwargs = build_save_kwargs(fmt, filtered_meta, warnings=result.warnings, image_mode=working_img.mode)
                if not out_icc:
                    meta_kwargs.pop("icc_profile", None)

            if working_img.mode in ("RGBA", "LA", "PA"):
                meta_kwargs.pop("transparency", None)
                working_img.info.pop("transparency", None)

            if has_target_size:
                if options.is_lossless:
                    # Lossless encoding with target size check
                    encoded_bytes = encode_candidate(working_img, fmt, quality=100, is_lossless=True, icc_profile=out_icc, meta_kwargs=meta_kwargs)
                    c_size = len(encoded_bytes)
                    target_max = options.max_bytes if options.max_bytes is not None else float("inf")
                    target_min = options.min_bytes if options.min_bytes is not None else 0
                    size_search_passed = (c_size <= target_max) and (c_size >= target_min)
                    if size_search_passed:
                        size_note = f"Lossless {fmt} met target size: {bytes_to_human(c_size, options.unit_standard)}"
                    else:
                        size_note = f"Target size not reached with selected settings: Lossless {fmt} size is {bytes_to_human(c_size, options.unit_standard)}"
                else:
                    # Bounded search for compliant candidate
                    (
                        encoded_bytes,
                        final_size,
                        actual_quality,
                        size_search_passed,
                        size_note,
                    ) = search_target_size(
                        working_img,
                        fmt,
                        max_bytes=options.max_bytes,
                        min_bytes=options.min_bytes,
                        unit_standard=options.unit_standard,
                        allow_palette_quantization=options.allow_palette_quantization,
                        icc_profile=out_icc,
                        meta_kwargs=meta_kwargs,
                        cancel_token=cancel_token,
                    )
            else:
                # Quality or lossless parameter
                q = options.quality if options.quality is not None else 80
                actual_quality = q if not options.is_lossless else None
                encoded_bytes = encode_candidate(
                    working_img,
                    fmt,
                    quality=q,
                    is_lossless=options.is_lossless,
                    icc_profile=out_icc,
                    meta_kwargs=meta_kwargs,
                )

            # Pixel fidelity verification if lossless requested: validate against original pixels
            if options.is_lossless:
                is_identical = verify_lossless_pixels(orig_img, encoded_bytes, fmt)
                if not is_identical:
                    raise RuntimeError("Lossless pixel check failed: decoded output does not match original pixels across all channels.")

            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("Compression cancelled before writing output.")

            # Stage output in temporary file and commit atomically
            staged = StagedOutput(destination, cancel_token=cancel_token)
            with staged as staged_path:
                with open(staged_path, "wb") as f_out:
                    f_out.write(encoded_bytes)
            staged.commit()

            result.output_path = destination
            result.output_dimensions = working_img.size
            result.output_size_bytes = destination.stat().st_size
            result.success = True

            # Report checks
            if options.is_lossless:
                result.checks.append(
                    RequirementCheck(
                        name="Pixel Fidelity",
                        target="100% Identical",
                        actual="Verified Identical Across All Channels",
                        passed=True,
                        details="Decoded output matches source pixels exactly without quantization",
                    )
                )
            elif actual_quality is not None:
                result.checks.append(
                    RequirementCheck(
                        name="Encoder Quality",
                        target=f"Q={options.quality or 'Target Search'}",
                        actual=f"Q={actual_quality}",
                        passed=True,
                        details="Lossy re-quantization applied",
                    )
                )

            if has_target_size:
                target_str = []
                if options.max_bytes:
                    target_str.append(f"<= {bytes_to_human(options.max_bytes, options.unit_standard)}")
                if options.min_bytes:
                    target_str.append(f">= {bytes_to_human(options.min_bytes, options.unit_standard)}")
                
                result.checks.append(
                    RequirementCheck(
                        name="Target File Size",
                        target=" and ".join(target_str),
                        actual=bytes_to_human(result.output_size_bytes, options.unit_standard),
                        passed=bool(size_search_passed),
                        details=size_note,
                    )
                )
                if not size_search_passed:
                    result.success = False
                    result.error_message = size_note
                    result.warnings.append(size_note)

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result
