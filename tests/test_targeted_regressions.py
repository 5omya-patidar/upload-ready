"""Targeted regression test suite verifying fixes for all 14 independent audit findings.

Covers:
1. Same-file input/output destination rejection.
2. Staged atomic writes and destination preservation upon failure.
3. Multi-frame protection on all routes (resize, compress, images-to-pdf, convert).
4. Palette (P) mode pixel fidelity and transparency verification.
5. High-bit-depth (16-bit) fidelity without lossy downcasting.
6. Lossless WebP exact RGB preservation under transparent pixels.
7. Colour-profile compatibility and cross-space conversion.
8. Fit resize upscaling calculation (200x100 -> 100x200).
9. Sniffed format preservation in resize and compress (ignoring deceiving extensions).
10. Strict decimal (KB) vs binary (KiB) size units and non-positive size rejection.
11. PDF scanned mode JPEG quality impact on embedded stream size.
12. PDF structural reduction honest failure reporting when output grows.
13. Digital signature detection using supported pypdf APIs.
14. Password whitespace preservation and masking in dataclass repr.
15. File queue rotation index synchronization after row deletion.
"""

import io
import shutil
import zipfile
from pathlib import Path
import pytest
from PIL import Image, ImageCms
import pypdf

from upload_ready.core.constants import (
    DEFAULT_MAX_CUMULATIVE_PIXELS,
    MAX_DECODED_PIXELS,
    UnitStandard,
    bytes_to_human,
    parse_size_to_bytes,
)
from upload_ready.core.image_compress import (
    are_images_pixel_identical,
    compress_image,
    encode_candidate,
)
from upload_ready.core.image_convert import convert_image
from upload_ready.core.image_resize import (
    apply_resize,
    calculate_target_dimensions,
    resize_image,
)
from upload_ready.core.models import (
    CancellationToken,
    CompressOptions,
    ConvertOptions,
    ImagesToPdfOptions,
    PdfReduceOptions,
    PdfToImagesOptions,
    ResizeMode,
    ResizeOptions,
)
from upload_ready.core.pdf_ops import (
    check_pdf_has_signatures,
    detect_pdf_signatures,
    images_to_pdf,
    pdf_to_images,
    reduce_pdf_size,
)
from upload_ready.core.safety import (
    MultiFileJobRollback,
    ResourceLimitExceededError,
    SafetyValidationError,
    StagedOutput,
    check_single_frame_image,
    is_same_file,
    validate_cumulative_image_dimensions,
    validate_cumulative_pdf_pages_dimensions,
    validate_destination_safety,
    validate_dimensions,
)
from upload_ready.gui.file_queue import FileQueueWidget


# -----------------------------------------------------------------------------
# 1. Same-File Input/Output Destination Rejection & Staged Preservation
# -----------------------------------------------------------------------------

def test_same_file_destination_rejected_across_all_routes(tmp_path: Path):
    """Destination resolving to original input must be rejected, even if allow_overwrite=True."""
    test_img = tmp_path / "original.png"
    Image.new("RGB", (50, 50), (255, 0, 0)).save(test_img)
    orig_bytes = test_img.read_bytes()

    # 1. Convert
    res_conv = convert_image(
        test_img,
        tmp_path,
        ConvertOptions(target_format="PNG"),
        custom_stem="original",
        allow_overwrite=True,
    )
    assert res_conv.success is False
    assert "strictly prohibits overwriting input files" in res_conv.error_message
    assert test_img.read_bytes() == orig_bytes

    # 2. Resize
    res_res = resize_image(
        test_img,
        tmp_path,
        ResizeOptions(width=40, height=40),
        custom_stem="original",
        allow_overwrite=True,
    )
    assert res_res.success is False
    assert "strictly prohibits overwriting input files" in res_res.error_message
    assert test_img.read_bytes() == orig_bytes

    # 3. Compress
    res_comp = compress_image(
        test_img,
        tmp_path,
        CompressOptions(is_lossless=True),
        custom_stem="original",
        allow_overwrite=True,
    )
    assert res_comp.success is False
    assert "strictly prohibits overwriting input files" in res_comp.error_message
    assert test_img.read_bytes() == orig_bytes

    # 4. PDF Reduce
    test_pdf = tmp_path / "doc.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with open(test_pdf, "wb") as f:
        writer.write(f)
    pdf_bytes = test_pdf.read_bytes()

    res_red = reduce_pdf_size(
        test_pdf,
        test_pdf,
        PdfReduceOptions(),
        allow_overwrite=True,
    )
    assert res_red.success is False
    assert "strictly prohibits overwriting input files" in res_red.error_message
    assert test_pdf.read_bytes() == pdf_bytes


def test_staged_output_preserves_existing_destination_on_failure(tmp_path: Path):
    """When processing fails or is cancelled, pre-existing destination file must be preserved."""
    dest_file = tmp_path / "existing_output.png"
    sentinel_content = b"CRITICAL_USER_DATA_DO_NOT_DELETE"
    dest_file.write_bytes(sentinel_content)

    cancel_token = CancellationToken()
    staged = StagedOutput(dest_file, cancel_token=cancel_token)

    with staged as staged_path:
        staged_path.write_bytes(b"PARTIAL_UNFINISHED_DATA")
        # Cancel before commit
        cancel_token.cancel()

    # Attempt commit after cancellation
    with pytest.raises(Exception):
        staged.commit()

    # Pre-existing destination must remain untouched
    assert dest_file.read_bytes() == sentinel_content
    # Temporary staged file must be purged
    assert not staged.staged_path.exists()


# -----------------------------------------------------------------------------
# 2. Multi-Frame Protection Across All Routes
# -----------------------------------------------------------------------------

def test_multiframe_protection_on_all_routes(tmp_path: Path):
    """Multi-page TIFF or animated GIF must be rejected in resize, compress, images-to-pdf, and convert (without flag)."""
    # Create 2-page TIFF
    tiff_path = tmp_path / "multipage.tiff"
    p1 = Image.new("RGB", (40, 40), (255, 0, 0))
    p2 = Image.new("RGB", (40, 40), (0, 255, 0))
    p1.save(tiff_path, save_all=True, append_images=[p2])

    out_dir = tmp_path / "out"

    # 1. Resize refuses multi-frame
    r_res = resize_image(tiff_path, out_dir, ResizeOptions(width=30, height=30))
    assert r_res.success is False
    assert "Multi-frame input detected" in r_res.error_message

    # 2. Compress refuses multi-frame
    r_comp = compress_image(tiff_path, out_dir, CompressOptions(is_lossless=False, quality=80))
    assert r_comp.success is False
    assert "Multi-frame input detected" in r_comp.error_message

    # 3. Images to PDF refuses multi-frame
    r_i2p = images_to_pdf([tiff_path], out_dir / "out.pdf", ImagesToPdfOptions())
    assert r_i2p.success is False
    assert "Multi-frame input detected" in r_i2p.error_message

    # 4. Convert refuses multi-frame without flag
    r_conv_no = convert_image(tiff_path, out_dir, ConvertOptions(target_format="PNG", allow_first_frame_only=False))
    assert r_conv_no.success is False
    assert "Multi-frame input detected" in r_conv_no.error_message

    # 5. Convert succeeds with explicit allow_first_frame_only
    r_conv_yes = convert_image(tiff_path, out_dir, ConvertOptions(target_format="PNG", allow_first_frame_only=True))
    assert r_conv_yes.success is True
    assert any("Exported only the first frame" in w for w in r_conv_yes.warnings)


# -----------------------------------------------------------------------------
# 3. Complete Pixel-Fidelity Verification
# -----------------------------------------------------------------------------

def test_palette_mode_pixel_fidelity_catches_different_palettes():
    """Two P-mode images with identical raster indices but different RGB palettes must fail equality."""
    p1 = Image.new("P", (10, 10), 0)
    p1.putpalette([255, 0, 0] + [0] * 765)  # Index 0 is Red

    p2 = Image.new("P", (10, 10), 0)
    p2.putpalette([0, 0, 255] + [0] * 765)  # Index 0 is Blue

    assert are_images_pixel_identical(p1, p2) is False

    p3 = Image.new("P", (10, 10), 0)
    p3.putpalette([255, 0, 0] + [0] * 765)  # Index 0 is Red
    assert are_images_pixel_identical(p1, p3) is True


def test_palette_mode_pixel_fidelity_catches_transparency_difference():
    """Two P-mode images with different transparency metadata must fail equality."""
    p1 = Image.new("P", (10, 10), 0)
    p1.putpalette([255, 0, 0] + [0] * 765)
    p1.info["transparency"] = 0  # Index 0 is transparent

    p2 = Image.new("P", (10, 10), 0)
    p2.putpalette([255, 0, 0] + [0] * 765)
    # Index 0 is opaque

    assert are_images_pixel_identical(p1, p2) is False


def test_high_bit_depth_pixel_fidelity_not_truncated():
    """16-bit integer (I) images differing only in low-order bits must be detected as different."""
    im1 = Image.new("I", (10, 10), 1000)
    im2 = Image.new("I", (10, 10), 1001)  # Delta of 1 in 16-bit depth
    assert are_images_pixel_identical(im1, im2) is False

    im3 = Image.new("I", (10, 10), 1000)
    assert are_images_pixel_identical(im1, im3) is True


def test_lossless_webp_exact_rgb_beneath_transparent_pixels():
    """Lossless WebP must preserve RGB values beneath transparent alpha pixels using exact=True."""
    # Pixel with distinct RGB and A=0
    img = Image.new("RGBA", (4, 4), (200, 100, 50, 0))
    encoded = encode_candidate(img, "WEBP", quality=100, is_lossless=True)

    with Image.open(io.BytesIO(encoded)) as decoded:
        pixel = decoded.getpixel((0, 0))
        # Exact RGB should be preserved (200, 100, 50, 0), not zeroed to (0, 0, 0, 0)
        assert pixel == (200, 100, 50, 0)


# -----------------------------------------------------------------------------
# 4. Colour-Profile Compatibility
# -----------------------------------------------------------------------------

def test_lab_to_rgb_does_not_attach_mismatched_lab_profile(tmp_path: Path):
    """Converting a Lab image to RGB/PNG must NOT attach a mismatched Lab ICC profile to RGB pixels."""
    lab_img = Image.new("LAB", (20, 20), (100, 128, 128))
    lab_path = tmp_path / "synthetic_lab.tiff"
    lab_img.save(lab_path, format="TIFF")

    out_dir = tmp_path / "out"
    res = convert_image(lab_path, out_dir, ConvertOptions(target_format="PNG"))
    assert res.success is True

    with Image.open(res.output_path) as out_im:
        assert out_im.mode in ("RGB", "RGBA")
        icc = out_im.info.get("icc_profile")
        if icc:
            # If an ICC profile exists, its color space MUST be RGB, never Lab
            prof = ImageCms.getOpenProfile(io.BytesIO(icc))
            assert prof.profile.xcolor_space.strip().upper() == "RGB"


# -----------------------------------------------------------------------------
# 5. Fit Resize Upscale Calculation & Aspect Ratio
# -----------------------------------------------------------------------------

def test_fit_resize_downscale_within_large_box_not_rejected_as_upscale():
    """Resizing a 200x100 image to FIT inside 100x200 scales to 100x50 and is NOT upscaling."""
    img = Image.new("RGB", (200, 100), (128, 128, 128))
    # Both width and height provided, mode=FIT
    resized, dims, was_upscaled = apply_resize(
        img,
        target_w=100,
        target_h=200,
        mode=ResizeMode.FIT,
        aspect_ratio_lock=True,
        allow_upscale=False,  # Must not raise ValueError
    )
    assert dims == (100, 50)
    assert was_upscaled is False
    assert resized.size == (100, 50)


def test_free_aspect_ratio_stretches_to_exact_dimensions():
    """When aspect ratio is unlocked, image resizes directly to target_w, target_h."""
    img = Image.new("RGB", (200, 100), (128, 128, 128))
    resized, dims, was_upscaled = apply_resize(
        img,
        target_w=100,
        target_h=80,
        mode=ResizeMode.FIT,
        aspect_ratio_lock=False,
        allow_upscale=False,
    )
    assert dims == (100, 80)
    assert resized.size == (100, 80)


# -----------------------------------------------------------------------------
# 6. Sniffed Format Preservation in Resize & Compress
# -----------------------------------------------------------------------------

def test_resize_preserves_sniffed_png_format_despite_jpg_extension(tmp_path: Path):
    """A PNG renamed to .jpg passed to resize must be saved as PNG, preserving alpha."""
    png_data = Image.new("RGBA", (60, 60), (255, 0, 0, 128))
    deceptive_path = tmp_path / "deceptive_image.jpg"
    png_data.save(deceptive_path, format="PNG")

    out_dir = tmp_path / "out"
    res = resize_image(deceptive_path, out_dir, ResizeOptions(width=40, height=40))
    assert res.success is True
    # Output must have .png extension and retain RGBA mode
    assert res.output_path.suffix.lower() == ".png"
    with Image.open(res.output_path) as out_im:
        assert out_im.format == "PNG"
        assert out_im.mode == "RGBA"


# -----------------------------------------------------------------------------
# 7. Size Targeting Units & Non-Positive Rejection
# -----------------------------------------------------------------------------

def test_size_parsing_strict_decimal_and_binary_units():
    """KB must be 1,000 bytes; KiB must be 1,024 bytes regardless of default."""
    assert parse_size_to_bytes("50 KB", UnitStandard.BINARY) == 50_000
    assert parse_size_to_bytes("50 KiB", UnitStandard.DECIMAL) == 51_200
    assert parse_size_to_bytes("1.5 MB", UnitStandard.BINARY) == 1_500_000
    assert parse_size_to_bytes("1.5 MiB", UnitStandard.DECIMAL) == int(1.5 * 1024 * 1024)

    with pytest.raises(ValueError, match="greater than 0"):
        parse_size_to_bytes("0 B")

    with pytest.raises(ValueError, match="greater than 0"):
        parse_size_to_bytes("-50 KB")


# -----------------------------------------------------------------------------
# 8. PDF Scanned Mode JPEG Quality & Structural Reduction Honesty
# -----------------------------------------------------------------------------

def test_pdf_scanned_mode_quality_changes_output_size(tmp_path: Path):
    """In scanned mode, image_quality=20 must produce a smaller PDF than image_quality=95."""
    # Create input PDF with gradient image
    input_pdf = tmp_path / "scanned_doc.pdf"
    grad = Image.radial_gradient("L").resize((250, 250)).convert("RGB")
    buf = io.BytesIO()
    grad.save(buf, format="PDF", resolution=72.0)
    buf.seek(0)
    with open(input_pdf, "wb") as f:
        f.write(buf.getvalue())

    out_q20 = tmp_path / "reduced_q20.pdf"
    out_q95 = tmp_path / "reduced_q95.pdf"

    res_q20 = reduce_pdf_size(
        input_pdf,
        out_q20,
        PdfReduceOptions(structural_only=False, downsample_scanned=True, target_dpi=100, image_quality=20),
    )
    res_q95 = reduce_pdf_size(
        input_pdf,
        out_q95,
        PdfReduceOptions(structural_only=False, downsample_scanned=True, target_dpi=100, image_quality=95),
    )

    assert res_q20.success is True
    assert res_q95.success is True
    assert out_q20.stat().st_size < out_q95.stat().st_size


def test_pdf_structural_reduction_honest_failure_when_output_grows(tmp_path: Path):
    """When structural reduction cannot shrink an already minimal PDF, reports passed=False on Size Reduction."""
    test_pdf = tmp_path / "minimal.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with open(test_pdf, "wb") as f:
        writer.write(f)

    out_pdf = tmp_path / "reduced.pdf"
    res = reduce_pdf_size(test_pdf, out_pdf, PdfReduceOptions(structural_only=True, remove_metadata=False))
    assert res.success is True

    # Check that RequirementCheck for Size Reduction reports passed=False if size was not reduced
    reduction_check = next(c for c in res.checks if c.name == "PDF Optimization")
    assert reduction_check.passed == (out_pdf.stat().st_size < test_pdf.stat().st_size)


def test_digital_signature_detection_via_catalog():
    """Detects digital signature flags in PDF catalog via supported APIs."""
    w = pypdf.PdfWriter()
    w.add_blank_page(width=100, height=100)
    w.root_object[pypdf.generic.NameObject("/SigFlags")] = pypdf.generic.NumberObject(1)

    buf = io.BytesIO()
    w.write(buf)
    buf.seek(0)

    reader = pypdf.PdfReader(buf)
    assert check_pdf_has_signatures(reader) is True


# -----------------------------------------------------------------------------
# 9. Password Whitespace & Dataclass Repr Masking
# -----------------------------------------------------------------------------

def test_password_whitespace_preserved_and_masked_in_repr():
    """Passwords must preserve whitespace exactly and never appear in dataclass repr."""
    raw_pwd = "  secret with spaces  "
    p2i_opts = PdfToImagesOptions(password=raw_pwd)
    red_opts = PdfReduceOptions(password=raw_pwd)

    assert p2i_opts.password == raw_pwd
    assert red_opts.password == raw_pwd

    # Must NOT appear in repr
    assert raw_pwd not in repr(p2i_opts)
    assert raw_pwd not in repr(red_opts)


# -----------------------------------------------------------------------------
# 10. File Queue Rotation Synchronization on Row Removal
# -----------------------------------------------------------------------------

def test_file_queue_rotations_reindexed_on_removal(qapp, tmp_path: Path):
    """Removing a row in FileQueueWidget must shift subsequent rotation entries correctly."""
    f0 = tmp_path / "f0.png"
    f1 = tmp_path / "f1.png"
    f2 = tmp_path / "f2.png"
    for f in (f0, f1, f2):
        Image.new("RGB", (10, 10)).save(f)

    widget = FileQueueWidget()
    widget.add_files([f0, f1, f2])

    # Rotate row 1 by 90° and row 2 by 180°
    widget._rotations[1] = 90
    widget._rotations[2] = 180

    # Select and remove row 0
    widget.table.selectRow(0)
    widget._remove_selected()

    # Remaining files are [f1, f2] at indices 0 and 1
    files = widget.get_files()
    assert files == [f1, f2]

    # Rotations must now be: index 0 (f1) -> 90°, index 1 (f2) -> 180°
    rotations = widget.get_rotations()
    assert rotations.get(0) == 90
    assert rotations.get(1) == 180
    assert 2 not in rotations


# -----------------------------------------------------------------------------
# 16. Colour Management and Reference Transformations
# -----------------------------------------------------------------------------

def test_tagged_cmyk_converts_to_rgb_with_cms_reference_and_srgb_profile(tmp_path: Path):
    """Converting CMYK image with embedded profile to RGB must match ImageCms transform and output sRGB profile."""
    cmyk_path = Path("C:/Windows/System32/spool/drivers/color/RSWOP.icm")
    if not cmyk_path.exists():
        pytest.skip("Windows RSWOP.icm profile not found on host.")

    cmyk_bytes = cmyk_path.read_bytes()
    img = Image.new("CMYK", (20, 20), (100, 50, 0, 10))
    img.info["icc_profile"] = cmyk_bytes
    img_path = tmp_path / "cmyk_test.jpg"
    img.save(img_path, format="JPEG", icc_profile=cmyk_bytes)

    out_dir = tmp_path / "cmyk_out"
    out_dir.mkdir()
    res = convert_image(img_path, out_dir, ConvertOptions(target_format="PNG"))
    assert res.success, f"CMYK conversion failed: {res.error_message}"
    assert res.output_path.exists()

    with Image.open(res.output_path) as out_img:
        assert out_img.mode == "RGB"
        # Reference transform
        in_prof = ImageCms.getOpenProfile(str(cmyk_path))
        srgb = ImageCms.createProfile("sRGB")
        expected_img = ImageCms.profileToProfile(img, in_prof, srgb, outputMode="RGB", inPlace=0)
        assert out_img.getpixel((0, 0)) == expected_img.getpixel((0, 0))
        assert "icc_profile" in out_img.info
        out_prof = ImageCms.getOpenProfile(io.BytesIO(out_img.info["icc_profile"]))
        assert out_prof.profile.xcolor_space.strip().upper() == "RGB"


def test_tagged_lab_converts_to_rgb_with_cms_reference(tmp_path: Path):
    """Converting Lab image with embedded profile must match ImageCms reference transform."""
    lab_prof = ImageCms.createProfile("LAB")
    lab_bytes = ImageCms.ImageCmsProfile(lab_prof).tobytes()

    img = Image.new("LAB", (15, 15), (150, 130, 120))
    img.info["icc_profile"] = lab_bytes
    img_path = tmp_path / "lab_test.tif"
    img.save(img_path, format="TIFF")

    out_dir = tmp_path / "lab_out"
    out_dir.mkdir()
    res = convert_image(img_path, out_dir, ConvertOptions(target_format="PNG"))
    assert res.success
    with Image.open(res.output_path) as out_img:
        assert out_img.mode == "RGB"
        in_prof = ImageCms.getOpenProfile(io.BytesIO(lab_bytes))
        srgb = ImageCms.createProfile("sRGB")
        expected_img = ImageCms.profileToProfile(img, in_prof, srgb, outputMode="RGB", inPlace=0)
        assert out_img.getpixel((0, 0)) == expected_img.getpixel((0, 0))


def test_corrupt_icc_profile_warns_and_processes_safely(tmp_path: Path):
    """Images with corrupted ICC profiles must emit a warning and complete without crashing."""
    img = Image.new("RGB", (20, 20), (100, 150, 200))
    img.info["icc_profile"] = b"CORRUPTED_ICC_HEADER_NOT_VALID"
    img_path = tmp_path / "corrupt_icc.png"
    img.save(img_path)

    out_dir = tmp_path / "icc_out"
    out_dir.mkdir()
    res = convert_image(img_path, out_dir, ConvertOptions(target_format="JPEG"))
    assert res.success
    assert any("invalid or corrupted ICC profile" in w for w in res.warnings)


# -----------------------------------------------------------------------------
# 17. Multi-File Rollback and Pre-Existing File Restoration
# -----------------------------------------------------------------------------

def test_multi_file_rollback_restores_preexisting_files_on_cancellation(tmp_path: Path, synthetic_multipage_pdf: Path):
    """When a multi-file job is cancelled mid-way, pre-existing destination files are restored from backup."""
    out_dir = tmp_path / "rollback_test"
    out_dir.mkdir()
    pre_existing_page1 = out_dir / "multipage_page_0001.png"
    pre_existing_page1.write_bytes(b"ORIGINAL_PRE_EXISTING_CONTENT_DO_NOT_DESTROY")

    class CancelAfterFirstPageToken(CancellationToken):
        def __init__(self, check_file: Path):
            super().__init__()
            self.check_file = check_file
            self._queried = 0

        @property
        def is_cancelled(self) -> bool:
            if self.check_file.exists():
                self._queried += 1
                if self._queried > 2:
                    return True
            return False

    token = CancelAfterFirstPageToken(pre_existing_page1)
    opts = PdfToImagesOptions(dpi=72)
    res = pdf_to_images(synthetic_multipage_pdf, out_dir, opts, allow_overwrite=True, cancel_token=token)
    assert not res.success
    assert "cancelled" in res.error_message.lower()

    # Pre-existing file content must be restored intact!
    assert pre_existing_page1.exists()
    assert pre_existing_page1.read_bytes() == b"ORIGINAL_PRE_EXISTING_CONTENT_DO_NOT_DESTROY"

    # Later pages must not be left behind
    page2 = out_dir / "multipage_page_0002.png"
    assert not page2.exists()


# -----------------------------------------------------------------------------
# 18. PDF Preservation: Rich Structures and Signature Distinctions
# -----------------------------------------------------------------------------

def test_pdf_structural_reduction_preserves_text_outline_and_forms(tmp_path: Path):
    """Structural reduction must preserve outlines, forms, and metadata when remove_metadata=False."""
    w = pypdf.PdfWriter()
    p = w.add_blank_page(width=300, height=300)
    w.add_metadata({"/Title": "Preserved Title", "/Author": "Test Author"})
    w.add_outline_item("Chapter 1", 0)

    pdf_path = tmp_path / "rich_doc.pdf"
    with open(pdf_path, "wb") as f:
        w.write(f)

    out_keep = tmp_path / "rich_reduced_keep.pdf"
    res_keep = reduce_pdf_size(pdf_path, out_keep, PdfReduceOptions(remove_metadata=False), allow_overwrite=True)
    assert res_keep.success

    r_keep = pypdf.PdfReader(out_keep)
    assert len(r_keep.outline) == 1
    assert r_keep.metadata.get("/Title") == "Preserved Title"

    out_strip = tmp_path / "rich_reduced_strip.pdf"
    res_strip = reduce_pdf_size(pdf_path, out_strip, PdfReduceOptions(remove_metadata=True), allow_overwrite=True)
    assert res_strip.success

    r_strip = pypdf.PdfReader(out_strip)
    assert len(r_strip.outline) == 1
    assert r_strip.metadata.get("/Title") is None


def test_detect_pdf_signatures_distinguishes_signed_from_empty_field(tmp_path: Path):
    """detect_pdf_signatures must distinguish unsigned signature fields from signed documents."""
    # 1. Unsigned signature field
    w_unsigned = pypdf.PdfWriter()
    p_unsigned = w_unsigned.add_blank_page(width=200, height=200)
    sig_field = pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Widget"),
        pypdf.generic.NameObject("/FT"): pypdf.generic.NameObject("/Sig"),
        pypdf.generic.NameObject("/T"): pypdf.generic.create_string_object("SignatureField1"),
    })
    w_unsigned.add_annotation(page_number=0, annotation=sig_field)
    
    unsigned_pdf = tmp_path / "unsigned_sig.pdf"
    with open(unsigned_pdf, "wb") as f:
        w_unsigned.write(f)

    r_unsigned = pypdf.PdfReader(unsigned_pdf)
    is_signed, has_empty = detect_pdf_signatures(r_unsigned)
    assert not is_signed
    assert has_empty

    # 2. Signed document (with /ByteRange and /V dictionary)
    w_signed = pypdf.PdfWriter()
    p_signed = w_signed.add_blank_page(width=200, height=200)
    sig_v = pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/Sig"),
        pypdf.generic.NameObject("/ByteRange"): pypdf.generic.ArrayObject([
            pypdf.generic.NumberObject(0), pypdf.generic.NumberObject(100),
            pypdf.generic.NumberObject(200), pypdf.generic.NumberObject(100)
        ]),
    })
    signed_field = pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Widget"),
        pypdf.generic.NameObject("/FT"): pypdf.generic.NameObject("/Sig"),
        pypdf.generic.NameObject("/T"): pypdf.generic.create_string_object("SignatureField1"),
        pypdf.generic.NameObject("/V"): sig_v,
    })
    w_signed.add_annotation(page_number=0, annotation=signed_field)

    signed_pdf = tmp_path / "signed_sig.pdf"
    with open(signed_pdf, "wb") as f:
        w_signed.write(f)

    r_signed = pypdf.PdfReader(signed_pdf)
    is_signed_res, has_empty_res = detect_pdf_signatures(r_signed)
    assert is_signed_res
    assert not has_empty_res


# -----------------------------------------------------------------------------
# 19. Palette Semantic Equality with Differently Ordered Palette Tables
# -----------------------------------------------------------------------------

def test_palette_semantic_equality_with_swapped_indexes():
    """are_images_pixel_identical must recognize semantically equal pixels despite swapped palette indices."""
    # Image 1: index 0 = Red, index 1 = Green; pixel (0,0)=Red, (1,0)=Green
    p1 = Image.new("P", (2, 1))
    pal1 = [255, 0, 0,  0, 255, 0] + [0]*762
    p1.putpalette(pal1)
    p1.putpixel((0, 0), 0)
    p1.putpixel((1, 0), 1)

    # Image 2: index 0 = Green, index 1 = Red; pixel (0,0)=Red (idx 1), (1,0)=Green (idx 0)
    p2 = Image.new("P", (2, 1))
    pal2 = [0, 255, 0,  255, 0, 0] + [0]*762
    p2.putpalette(pal2)
    p2.putpixel((0, 0), 1)
    p2.putpixel((1, 0), 0)

    assert are_images_pixel_identical(p1, p2) is True

    # Image 3: different color (Blue instead of Green)
    p3 = Image.new("P", (2, 1))
    pal3 = [255, 0, 0,  0, 0, 255] + [0]*762
    p3.putpalette(pal3)
    p3.putpixel((0, 0), 0)
    p3.putpixel((1, 0), 1)

    assert are_images_pixel_identical(p1, p3) is False


# -----------------------------------------------------------------------------
# 20. Cumulative-Memory Limits for Retained-Page and Multi-Image Workflows
# -----------------------------------------------------------------------------

def test_validate_cumulative_image_dimensions_bounds(tmp_path: Path):
    """validate_cumulative_image_dimensions must accurately sum pixel volume and enforce limits."""
    img1 = tmp_path / "img1.png"
    img2 = tmp_path / "img2.png"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(img1)  # 10,000 px
    Image.new("RGB", (150, 100), (0, 255, 0)).save(img2)  # 15,000 px
    # Total = 25,000 pixels

    # Within budget
    total = validate_cumulative_image_dimensions([img1, img2], max_cumulative_pixels=30_000)
    assert total == 25_000

    # Exceeding budget
    with pytest.raises(ResourceLimitExceededError) as exc_info:
        validate_cumulative_image_dimensions([img1, img2], max_cumulative_pixels=20_000)
    assert "exceeds cumulative safety budget of 20,000 pixels" in str(exc_info.value)
    assert "accumulated 25,000 pixels" in str(exc_info.value)


def test_images_to_pdf_enforces_cumulative_pixel_limit_and_preserves_files(tmp_path: Path):
    """images_to_pdf must reject jobs exceeding cumulative pixel budget and preserve output path."""
    img1 = tmp_path / "img1.png"
    img2 = tmp_path / "img2.png"
    Image.new("RGB", (100, 100)).save(img1)  # 10,000 px
    Image.new("RGB", (100, 100)).save(img2)  # 10,000 px
    # Total = 20,000 px

    out_pdf = tmp_path / "assembled.pdf"

    # Set threshold smaller than total (15,000 px)
    options = ImagesToPdfOptions(max_cumulative_pixels=15_000)
    result = images_to_pdf([img1, img2], out_pdf, options)

    assert result.success is False
    assert "exceeds cumulative safety budget" in result.error_message
    assert not out_pdf.exists(), "Destination file must not be created on cumulative memory rejection"


def test_pdf_reduce_scanned_downsampling_enforces_cumulative_budget(tmp_path: Path):
    """reduce_pdf_size scanned downsampling must reject jobs exceeding cumulative raster pixel budget."""
    # Create a 2-page PDF (each page 200x200 points)
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_blank_page(width=200, height=200)

    in_pdf = tmp_path / "multi_page.pdf"
    with open(in_pdf, "wb") as f:
        w.write(f)

    out_pdf = tmp_path / "downsampled.pdf"

    # At 150 DPI, scale = 150/72 = 2.0833 -> 417x417 = 173,889 pixels per page -> 347,778 total pixels
    # Provide a conservative threshold of 100,000 pixels
    options = PdfReduceOptions(
        downsample_scanned=True,
        target_dpi=150,
        max_cumulative_pixels=100_000,
    )
    result = reduce_pdf_size(in_pdf, out_pdf, options)

    assert result.success is False
    assert "exceeds cumulative safety budget" in result.error_message
    assert not out_pdf.exists(), "Destination file must not be created when cumulative limit is exceeded"


# -----------------------------------------------------------------------------
# 21. Digital Signature Honesty and Warning Distinction
# -----------------------------------------------------------------------------

def test_pdf_reduce_signature_warning_distinguishes_validity_and_fields(tmp_path: Path):
    """reduce_pdf_size must distinguish signed document warnings from unsigned form field warnings."""
    # 1. Signed PDF document
    w_signed = pypdf.PdfWriter()
    w_signed.add_blank_page(width=200, height=200)
    sig_v = pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/Sig"),
        pypdf.generic.NameObject("/ByteRange"): pypdf.generic.ArrayObject([
            pypdf.generic.NumberObject(0), pypdf.generic.NumberObject(100),
            pypdf.generic.NumberObject(200), pypdf.generic.NumberObject(100)
        ]),
    })
    signed_field = pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Widget"),
        pypdf.generic.NameObject("/FT"): pypdf.generic.NameObject("/Sig"),
        pypdf.generic.NameObject("/T"): pypdf.generic.create_string_object("SignatureField1"),
        pypdf.generic.NameObject("/V"): sig_v,
    })
    w_signed.add_annotation(page_number=0, annotation=signed_field)
    signed_pdf = tmp_path / "signed_doc.pdf"
    with open(signed_pdf, "wb") as f:
        w_signed.write(f)

    out_signed = tmp_path / "signed_reduced.pdf"
    res_signed = reduce_pdf_size(signed_pdf, out_signed, PdfReduceOptions(structural_only=True))
    assert res_signed.success
    assert any("invalidates cryptographic signatures" in w for w in res_signed.warnings)

    # 2. Unsigned signature form field
    w_unsigned = pypdf.PdfWriter()
    w_unsigned.add_blank_page(width=200, height=200)
    unsigned_field = pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Widget"),
        pypdf.generic.NameObject("/FT"): pypdf.generic.NameObject("/Sig"),
        pypdf.generic.NameObject("/T"): pypdf.generic.create_string_object("SignatureField2"),
    })
    w_unsigned.add_annotation(page_number=0, annotation=unsigned_field)
    unsigned_pdf = tmp_path / "unsigned_doc.pdf"
    with open(unsigned_pdf, "wb") as f:
        w_unsigned.write(f)

    out_unsigned = tmp_path / "unsigned_reduced.pdf"
    res_unsigned = reduce_pdf_size(unsigned_pdf, out_unsigned, PdfReduceOptions(structural_only=True))
    assert res_unsigned.success
    assert any("prepared for digital signing" in w for w in res_unsigned.warnings)
    assert not any("invalidates cryptographic signatures" in w for w in res_unsigned.warnings)


# -----------------------------------------------------------------------------
# 22. PDF-to-ZIP Export: Intermediate File Isolation and Pre-existing Protection
# -----------------------------------------------------------------------------

def test_pdf_to_images_zip_export_preserves_preexisting_files(tmp_path: Path):
    """pdf_to_images with as_zip=True must never overwrite or delete pre-existing user files with page names."""
    # 1. Create a 2-page source PDF
    src_pdf = tmp_path / "sample.pdf"
    w = pypdf.PdfWriter()
    w.add_blank_page(width=100, height=100)
    w.add_blank_page(width=100, height=100)
    with open(src_pdf, "wb") as f:
        w.write(f)

    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 2. Pre-create an existing file with the exact name that would match a page intermediate
    preexisting_file = out_dir / "sample_page_0001.png"
    sentinel_content = b"CRITICAL_USER_DATA_DO_NOT_DELETE"
    preexisting_file.write_bytes(sentinel_content)

    # 3. Run PDF to images with as_zip=True and allow_overwrite=True
    options = PdfToImagesOptions(target_format="PNG", as_zip=True)
    res = pdf_to_images(src_pdf, out_dir, options, allow_overwrite=True)

    assert res.success is True
    assert res.output_path is not None
    assert res.output_path.exists()
    assert res.output_path.name == "sample_pages.zip"

    # 4. Verify the pre-existing user file was NOT overwritten or unlinked
    assert preexisting_file.exists(), "Pre-existing file must NOT be deleted after ZIP creation!"
    assert preexisting_file.read_bytes() == sentinel_content, "Pre-existing file content must remain byte-identical!"

    # 5. Verify the ZIP contains valid rendered images
    with zipfile.ZipFile(res.output_path, "r") as zf:
        namelist = zf.namelist()
        assert len(namelist) == 2
        assert "sample_page_0001.png" in namelist
        assert "sample_page_0002.png" in namelist


# -----------------------------------------------------------------------------
# 23. Whole-Batch Destination Collision Protection
# -----------------------------------------------------------------------------

def test_batch_destination_safety_protects_queued_inputs(tmp_path: Path):
    """Processing one batch item must never overwrite another queued original input file."""
    photo_png = tmp_path / "photo.png"
    photo_jpg = tmp_path / "photo.jpg"
    Image.new("RGB", (50, 50), (255, 0, 0)).save(photo_png)
    orig_jpg_bytes = b"ORIGINAL_PHOTO_JPG_CONTENT"
    photo_jpg.write_bytes(orig_jpg_bytes)

    batch_inputs = [photo_png, photo_jpg]

    res = convert_image(
        photo_png,
        tmp_path,
        ConvertOptions(target_format="JPEG"),
        allow_overwrite=True,
        batch_inputs=batch_inputs,
    )

    assert res.success is False
    assert "strictly prohibits overwriting input files" in (res.error_message or "")
    assert "queued original input file" in (res.error_message or "")
    assert photo_jpg.read_bytes() == orig_jpg_bytes


# -----------------------------------------------------------------------------
# 24. Abort When Backup Creation Fails in MultiFileJobRollback
# -----------------------------------------------------------------------------

def test_multifile_rollback_aborts_when_backup_fails(tmp_path: Path, monkeypatch):
    """MultiFileJobRollback.stage_commit must abort before committing if backup creation fails."""
    dest = tmp_path / "existing_dest.txt"
    dest.write_text("ORIGINAL_DEST_DATA")

    rollback_tracker = MultiFileJobRollback()
    staged = StagedOutput(dest)
    with staged as staged_path:
        staged_path.write_text("NEW_STAGED_DATA")

    def failing_copy2(*args, **kwargs):
        raise OSError("Simulated disk error during backup copy")

    monkeypatch.setattr(shutil, "copy2", failing_copy2)

    with pytest.raises(SafetyValidationError) as exc_info:
        rollback_tracker.stage_commit(staged)

    assert "Failed to create safety backup" in str(exc_info.value)
    assert dest.read_text() == "ORIGINAL_DEST_DATA"
    assert not staged.staged_path.exists()


# -----------------------------------------------------------------------------
# 25. High-Bit-Depth Lossless Validation & Precision Preservation
# -----------------------------------------------------------------------------

def test_compress_high_bit_depth_preserves_precision(tmp_path: Path):
    """compress_image with is_lossless=True must preserve 16-bit precision in PNG without downcasting to RGB."""
    img_path = tmp_path / "high_depth.png"
    img = Image.new("I;16", (2, 1))
    img.putpixel((0, 0), 1000)
    img.putpixel((1, 0), 40000)
    img.save(img_path)

    out_dir = tmp_path / "out"
    res = compress_image(img_path, out_dir, CompressOptions(is_lossless=True), allow_overwrite=True)

    assert res.success is True
    assert res.output_path is not None and res.output_path.exists()

    with Image.open(res.output_path) as decoded:
        assert decoded.mode == "I;16"
        assert decoded.getpixel((0, 0)) == 1000
        assert decoded.getpixel((1, 0)) == 40000


# -----------------------------------------------------------------------------
# 26. Crop Intermediate Dimension Bounded Protection
# -----------------------------------------------------------------------------

def test_resize_crop_bounded_resampling_without_massive_intermediate(tmp_path: Path, monkeypatch):
    """Crop resize on 10000x1 input to 1000x1000 target must use bounded box resampling without huge allocation."""
    img_path = tmp_path / "wide_stripe.png"
    Image.new("RGB", (10000, 1), (255, 0, 0)).save(img_path)

    # Intercept allocations / resize calls
    allocated_sizes = []
    orig_resize = Image.Image.resize

    def spy_resize(self, size, *args, **kwargs):
        allocated_sizes.append((size, kwargs.get("box")))
        return orig_resize(self, size, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "resize", spy_resize)

    out_dir = tmp_path / "out"
    options = ResizeOptions(width=1000, height=1000, mode=ResizeMode.CROP, allow_upscale=True)
    res = resize_image(img_path, out_dir, options, allow_overwrite=True)

    assert res.success is True
    assert res.output_dimensions == (1000, 1000)
    # Verify the dangerous 10,000,000x1,000 intermediate buffer was NEVER allocated!
    for size, box in allocated_sizes:
        assert size != (10_000_000, 1000), "Oversized crop intermediate (10M x 1k) must never be allocated!"
        assert size == (1000, 1000), "Direct bounded target size must be allocated!"
        assert box is not None, "Bounded source box must be provided for direct crop resampling!"


# -----------------------------------------------------------------------------
# 27. Rich PDF Structural Reduction: Preserving Attachments, Outlines, Links, Annotations, Forms, and XMP
# -----------------------------------------------------------------------------

def test_pdf_structural_reduction_preserves_all_document_structures(tmp_path: Path):
    """Structural reduction must preserve text, bookmarks, link/text annotations, forms/values, attachments, and catalog XMP."""
    w = pypdf.PdfWriter()
    p = w.add_blank_page(width=300, height=300)

    # 1. Searchable Text (with standard font resource)
    font_dict = pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/Font"),
        pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Type1"),
        pypdf.generic.NameObject("/BaseFont"): pypdf.generic.NameObject("/Helvetica"),
    })
    res_dict = pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Font"): pypdf.generic.DictionaryObject({
            pypdf.generic.NameObject("/F1"): font_dict
        })
    })
    p[pypdf.generic.NameObject("/Resources")] = res_dict
    content_stream = pypdf.generic.DecodedStreamObject()
    content_stream.set_data(b"BT /F1 12 Tf 50 200 Td (Searchable Alpha Beta) Tj ET")
    p[pypdf.generic.NameObject("/Contents")] = content_stream

    # 2. Outline item
    w.add_outline_item("Chapter 1: Intro", 0)

    # 3. Form field and value
    w.add_annotation(0, pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Widget"),
        pypdf.generic.NameObject("/FT"): pypdf.generic.NameObject("/Tx"),
        pypdf.generic.NameObject("/T"): pypdf.generic.create_string_object("customer_name"),
        pypdf.generic.NameObject("/V"): pypdf.generic.create_string_object("Jane Doe"),
        pypdf.generic.NameObject("/Rect"): pypdf.generic.ArrayObject([
            pypdf.generic.NumberObject(10), pypdf.generic.NumberObject(10),
            pypdf.generic.NumberObject(100), pypdf.generic.NumberObject(30)
        ]),
    }))

    # 4. Link annotation
    w.add_annotation(0, pypdf.generic.DictionaryObject({
        pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Link"),
        pypdf.generic.NameObject("/Rect"): pypdf.generic.ArrayObject([
            pypdf.generic.NumberObject(50), pypdf.generic.NumberObject(50),
            pypdf.generic.NumberObject(150), pypdf.generic.NumberObject(70)
        ]),
        pypdf.generic.NameObject("/A"): pypdf.generic.DictionaryObject({
            pypdf.generic.NameObject("/S"): pypdf.generic.NameObject("/URI"),
            pypdf.generic.NameObject("/URI"): pypdf.generic.create_string_object("https://example.com/docs"),
        }),
    }))

    # 5. Attachment
    w.add_attachment("notes.txt", b"Embedded secret attachment content")

    # 6. Catalog XMP metadata
    xmp_stream = pypdf.generic.DecodedStreamObject()
    xmp_stream.set_data(b"<x:xmpmeta>CustomXMPContent</x:xmpmeta>")
    xmp_stream[pypdf.generic.NameObject("/Type")] = pypdf.generic.NameObject("/Metadata")
    xmp_stream[pypdf.generic.NameObject("/Subtype")] = pypdf.generic.NameObject("/XML")
    w._root_object[pypdf.generic.NameObject("/Metadata")] = xmp_stream

    # 7. Document information metadata
    w.add_metadata({"/Title": "Rich Structural Test Document", "/Author": "OpenSource Reviewer"})

    rich_pdf = tmp_path / "rich_doc.pdf"
    with open(rich_pdf, "wb") as f:
        w.write(f)

    # Pass 1: Structural reduction with remove_metadata=False
    out_keep = tmp_path / "reduced_keep.pdf"
    res_keep = reduce_pdf_size(rich_pdf, out_keep, PdfReduceOptions(remove_metadata=False), allow_overwrite=True)
    assert res_keep.success is True

    r_keep = pypdf.PdfReader(out_keep)
    assert "Searchable Alpha Beta" in r_keep.pages[0].extract_text()
    assert len(r_keep.outline) == 1
    annots = r_keep.pages[0].get("/Annots", [])
    widget_found = False
    link_found = False
    for a in annots:
        obj = a.get_object()
        if obj.get("/Subtype") == "/Widget" and obj.get("/T") == "customer_name" and obj.get("/V") == "Jane Doe":
            widget_found = True
        if obj.get("/Subtype") == "/Link" and "https://example.com/docs" in str(obj.get("/A", {}).get("/URI", "")):
            link_found = True
    assert widget_found, "Widget form field and value must be preserved!"
    assert link_found, "Link annotation URI must be preserved!"
    assert "notes.txt" in getattr(r_keep, "attachments", {}), "Embedded attachment must be preserved!"
    assert r_keep.attachments["notes.txt"][0] == b"Embedded secret attachment content"
    assert "/Metadata" in r_keep.trailer["/Root"], "Catalog XMP must be preserved when remove_metadata=False!"
    assert b"CustomXMPContent" in r_keep.trailer["/Root"]["/Metadata"].get_data()
    assert r_keep.metadata.get("/Title") == "Rich Structural Test Document"

    # Pass 2: Structural reduction with remove_metadata=True
    out_strip = tmp_path / "reduced_strip.pdf"
    res_strip = reduce_pdf_size(rich_pdf, out_strip, PdfReduceOptions(remove_metadata=True), allow_overwrite=True)
    assert res_strip.success is True

    r_strip = pypdf.PdfReader(out_strip)
    assert "Searchable Alpha Beta" in r_strip.pages[0].extract_text()
    assert len(r_strip.outline) == 1
    assert "notes.txt" in getattr(r_strip, "attachments", {})
    assert r_strip.metadata.get("/Title") is None
    assert "/Metadata" not in r_strip.trailer["/Root"]

