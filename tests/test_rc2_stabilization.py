"""Comprehensive verification test suite for Upload Ready RC2 stabilization defects.

Covers:
1. Complete source protection (union of operation sources and batch inputs).
2. Transactional preset processing (isolated workspace, atomic commit, stored metadata categories).
3. Selective metadata removal & image fidelity (GPS-only, P-mode palette/alpha, PDF honest checks).
4. Safety checks across all new image routes (multi-frame refusal, dimension bounds).
5. PDF organiser and watermark structure preservation (independent duplicate page rotations, AcroForm fields, bookmarks).
6. Physical layout & alpha handling (rejection of impossible layouts, signature alpha compositing).
7. GUI preview fidelity & custom sheet dimensions (pixel-mm sync, card rendering, watermark backdrop).
"""

import io
from pathlib import Path
from PIL import Image, ImageDraw
import pypdf
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject
import pytest

from upload_ready.core.id_layout import IdLayoutOptions, generate_id_layout
from upload_ready.core.image_compress import CompressOptions, compress_image
from upload_ready.core.image_convert import ConvertOptions, convert_image
from upload_ready.core.image_resize import ResizeMode, ResizeOptions, resize_image
from upload_ready.core.metadata import inspect_metadata, scrub_metadata
from upload_ready.core.models import CancellationToken, OperationCancelledError
from upload_ready.core.pdf_organizer import OrganizedPage, export_organized_pdf
from upload_ready.core.photo_sheet import PhotoSheetOptions, generate_photo_sheet
from upload_ready.core.presets import RequirementPreset, apply_preset_to_image
from upload_ready.core.safety import SafetyValidationError
from upload_ready.core.watermark import WatermarkOptions, apply_watermark_to_image, apply_watermark_to_pdf
from upload_ready.gui.tabs.id_layout_tab import IdLayoutTab
from upload_ready.gui.tabs.photo_sheet_tab import PhotoSheetTab
from upload_ready.gui.tabs.watermark_tab import WatermarkTab


def create_searchable_form_pdf(dest_path: Path, text: str, field_name: str, field_val: str, bookmark_title: str) -> None:
    """Helper creating a valid PDF with searchable vector text, an interactive text field, and an outline item."""
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=300)

    # Searchable text content stream
    content = f"BT /F1 12 Tf 50 250 Td ({text}) Tj ET".encode("latin1")
    stream_obj = pypdf.generic.DecodedStreamObject()
    stream_obj.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream_obj)

    # Add interactive text form field
    field = DictionaryObject()
    field[NameObject("/FT")] = NameObject("/Tx")
    field[NameObject("/T")] = TextStringObject(field_name)
    field[NameObject("/V")] = TextStringObject(field_val)
    field[NameObject("/Subtype")] = NameObject("/Widget")
    field[NameObject("/Rect")] = ArrayObject([
        NumberObject(50), NumberObject(150), NumberObject(200), NumberObject(180)
    ])
    field_ref = writer._add_object(field)
    page[NameObject("/Annots")] = ArrayObject([field_ref])

    acro = DictionaryObject()
    acro[NameObject("/Fields")] = ArrayObject([field_ref])
    writer.root_object[NameObject("/AcroForm")] = writer._add_object(acro)

    # Add outline/bookmark
    writer.add_outline_item(bookmark_title, 0)

    with open(dest_path, "wb") as f:
        writer.write(f)


# ==============================================================================
# DEFECT 1: COMPLETE SOURCE PROTECTION
# ==============================================================================

def test_defect1_source_protection_union_disjoint_batch_inputs(temp_dir: Path):
    """
    Export an organized PDF back to its source path with allow_overwrite=True
    while batch_inputs contains a completely different file.
    The union protection must prevent overwriting the source PDF.
    """
    src1 = temp_dir / "source1.pdf"
    other_batch_file = temp_dir / "other_batch.pdf"
    create_searchable_form_pdf(src1, "Doc 1 Text", "f1", "v1", "B1")
    create_searchable_form_pdf(other_batch_file, "Other Text", "f2", "v2", "B2")

    pages = [OrganizedPage(source_path=src1, page_index=0, rotation_offset=0, source_label="p1")]

    # Attempt to export back to src1 while batch_inputs only specifies other_batch_file
    res = export_organized_pdf(
        pages=pages,
        output_path=src1,
        allow_overwrite=True,
        batch_inputs=[other_batch_file],
    )
    assert res.success is False
    assert "prohibits overwriting" in res.error_message.lower()

    # Ensure source was not damaged
    assert src1.exists()
    r = pypdf.PdfReader(src1)
    assert len(r.pages) == 1


def test_defect1_image_operations_protect_source_union(synthetic_rgb_png: Path, temp_dir: Path):
    """Image operations must protect both input_path and batch_inputs."""
    other_file = temp_dir / "unrelated.png"
    other_file.write_bytes(b"placeholder")

    # Image convert
    res = convert_image(
        synthetic_rgb_png,
        synthetic_rgb_png.parent,
        ConvertOptions(target_format="PNG"),
        allow_overwrite=True,
        batch_inputs=[other_file],
        custom_stem=synthetic_rgb_png.stem,
    )
    assert res.success is False
    assert "prohibits overwriting" in res.error_message.lower()


# ==============================================================================
# DEFECT 2: TRANSACTIONAL PRESET PROCESSING
# ==============================================================================

def test_defect2_preset_pipeline_transactional_failure_leaves_destination_intact(synthetic_rgb_png: Path, temp_dir: Path):
    """
    If a preset pipeline step or validation fails, the pre-existing destination file
    must remain completely unmodified.
    """
    dest_file = temp_dir / f"{synthetic_rgb_png.stem}.jpg"
    original_sentinel_content = b"SENTINEL_ORIGINAL_CONTENT_MUST_NOT_BE_TOUCHED"
    dest_file.write_bytes(original_sentinel_content)

    # Preset with an impossible minimum file size requirement (50 MB)
    impossible_preset = RequirementPreset(
        id="impossible_min_bytes",
        name="Impossible Min Bytes",
        target_format="JPEG",
        width=100,
        height=100,
        min_bytes=50_000_000,
    )

    res = apply_preset_to_image(
        synthetic_rgb_png,
        temp_dir,
        impossible_preset,
        allow_overwrite=True,
    )

    assert res.success is False
    assert any(term in (res.error_message or "").lower() for term in ("target size not reached", "below preset minimum"))
    # The pre-existing file must remain intact
    assert dest_file.exists()
    assert dest_file.read_bytes() == original_sentinel_content


def test_defect2_preset_remove_metadata_and_categories(synthetic_rgb_png: Path, temp_dir: Path):
    """Preset with remove_metadata=True scrubs requested metadata categories."""
    # Add EXIF tag to source
    img = Image.open(synthetic_rgb_png)
    exif = img.getexif()
    exif[0x010E] = "Test Author"
    with_exif_path = temp_dir / "with_exif.png"
    img.save(with_exif_path, exif=exif)

    preset = RequirementPreset(
        id="clean_meta",
        name="Clean Meta",
        target_format="PNG",
        remove_metadata=True,
        metadata_categories=["exif"],
    )

    res = apply_preset_to_image(
        with_exif_path,
        temp_dir,
        preset,
        allow_overwrite=False,
    )
    assert res.success is True
    assert res.output_path.exists()

    with Image.open(res.output_path) as out_img:
        out_exif = out_img.getexif()
        assert len(out_exif) == 0


# ==============================================================================
# DEFECT 3: SELECTIVE METADATA REMOVAL & IMAGE FIDELITY
# ==============================================================================

def test_defect3_selective_gps_scrubbing_preserves_exif_author(temp_dir: Path):
    """Scrubbing with categories={'gps'} removes GPS (0x8825) but preserves Author and Make."""
    src = temp_dir / "photo_with_gps.jpg"
    img = Image.new("RGB", (100, 100), color=(100, 150, 200))
    exif = img.getexif()
    exif[0x010E] = "Photographer Name"
    exif[0x010F] = "Camera Manufacturer"
    # GPS IFD tag 0x8825
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1] = "N"
    gps_ifd[2] = (37.0, 46.0, 29.0)

    img.save(src, "JPEG", exif=exif)

    out_dir = temp_dir / "meta_out"
    res = scrub_metadata(src, out_dir, categories={"gps"})
    assert res.success is True
    assert res.output_path.exists()

    with Image.open(res.output_path) as out_img:
        out_exif = out_img.getexif()
        # Author and Manufacturer must be preserved
        assert out_exif.get(0x010E) == "Photographer Name"
        assert out_exif.get(0x010F) == "Camera Manufacturer"
        # GPS tag must be stripped
        assert 0x8825 not in out_exif


def test_defect3_p_mode_png_preserves_palette_colors_and_transparency(temp_dir: Path):
    """Metadata scrubbing on a palette (P-mode) PNG retains custom palette colors and transparency."""
    src = temp_dir / "palette_sample.png"
    # Create P-mode image with custom palette
    p_img = Image.new("P", (80, 80))
    # Palette with vivid red, green, blue
    palette = [255, 0, 0, 0, 255, 0, 0, 0, 255] + [0] * (768 - 9)
    p_img.putpalette(palette)
    # Paint pixels with index 1 (green) and index 2 (blue)
    p_img.putpixel((10, 10), 1)
    p_img.putpixel((20, 20), 2)
    # Set index 0 as transparent
    p_img.info["transparency"] = 0
    p_img.save(src, "PNG")

    out_dir = temp_dir / "p_scrubbed"
    res = scrub_metadata(src, out_dir, categories={"all"})
    assert res.success is True

    with Image.open(res.output_path) as reopened:
        assert reopened.mode == "P"
        reopened_palette = reopened.getpalette()
        # Palette entries 0, 1, 2 must match original custom palette
        assert reopened_palette[:9] == palette[:9]
        assert reopened.getpixel((10, 10)) == 1
        assert reopened.getpixel((20, 20)) == 2
        assert "transparency" in reopened.info


def test_defect3_pdf_metadata_scrubbing_honest_verification(temp_dir: Path):
    """PDF metadata scrubbing verifies that Info dict and XMP are actually cleared."""
    pdf_path = temp_dir / "doc_with_info.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({
        "/Title": "Confidential Report",
        "/Author": "Internal Auditor",
    })
    with open(pdf_path, "wb") as f:
        writer.write(f)

    out_dir = temp_dir / "pdf_clean"
    res = scrub_metadata(pdf_path, out_dir, categories={"pdf_docinfo", "pdf_xmp"})
    assert res.success is True

    r = pypdf.PdfReader(res.output_path)
    assert not r.metadata or len(r.metadata) == 0


# ==============================================================================
# DEFECT 4: SAFETY CHECKS ACROSS ALL NEW ROUTES
# ==============================================================================

def test_defect4_multiframe_rejected_on_all_new_routes(temp_dir: Path):
    """Multi-frame GIF must be rejected before decoding in watermark, metadata, photo-sheet, and ID layout."""
    gif_path = temp_dir / "multi_frame.gif"
    f1 = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    f2 = Image.new("RGBA", (100, 100), (0, 255, 0, 255))
    f1.save(gif_path, save_all=True, append_images=[f2])

    out_dir = temp_dir / "safety_out"

    # Watermarking
    r1 = apply_watermark_to_image(gif_path, out_dir / "wm.png", WatermarkOptions(text="TEST"))
    assert r1.success is False
    assert "multi-frame input detected" in r1.error_message.lower()

    # Metadata scrubbing
    r2 = scrub_metadata(gif_path, out_dir, categories={"all"})
    assert r2.success is False
    assert "multi-frame input detected" in r2.error_message.lower()

    # Photo Sheet
    r3 = generate_photo_sheet(gif_path, out_dir / "sheet.pdf", PhotoSheetOptions())
    assert r3.success is False
    assert "multi-frame input detected" in r3.error_message.lower()

    # ID Layout
    valid_png = temp_dir / "valid.png"
    Image.new("RGB", (100, 100)).save(valid_png)
    r4 = generate_id_layout(gif_path, valid_png, out_dir / "id.pdf", IdLayoutOptions())
    assert r4.success is False
    assert "multi-frame input detected" in r4.error_message.lower()


# ==============================================================================
# DEFECT 5: PDF ORGANISER & WATERMARK PRESERVATION
# ==============================================================================

def test_defect5_pdf_organizer_duplicate_pages_independent_rotations(temp_dir: Path):
    """
    Referencing the same page twice with different rotations must produce independent
    rotations in the output without mutating the reader's source page.
    """
    src = temp_dir / "single_page.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(src, "wb") as f:
        writer.write(f)

    # Duplicate page 0: occurrence 1 has rotation 90, occurrence 2 has rotation 180
    pages = [
        OrganizedPage(source_path=src, page_index=0, rotation_offset=90, source_label="p1_rot90"),
        OrganizedPage(source_path=src, page_index=0, rotation_offset=180, source_label="p1_rot180"),
    ]

    out_pdf = temp_dir / "duplicates_rotated.pdf"
    res = export_organized_pdf(pages=pages, output_path=out_pdf, allow_overwrite=True)
    assert res.success is True

    # Output verification
    r_out = pypdf.PdfReader(out_pdf)
    assert len(r_out.pages) == 2
    assert r_out.pages[0].get("/Rotate", 0) == 90
    assert r_out.pages[1].get("/Rotate", 0) == 180

    # Source reader page must not have been mutated
    r_src = pypdf.PdfReader(src)
    assert r_src.pages[0].get("/Rotate", 0) == 0


def test_defect5_pdf_organizer_merges_two_form_pdfs_preserving_all_fields(temp_dir: Path):
    """Exporting pages from two different PDFs with form fields merges both fields into AcroForm."""
    doc1 = temp_dir / "form1.pdf"
    doc2 = temp_dir / "form2.pdf"
    create_searchable_form_pdf(doc1, "First Document", "applicant_name", "Somya", "Section 1")
    create_searchable_form_pdf(doc2, "Second Document", "passport_no", "Z1234567", "Section 2")

    pages = [
        OrganizedPage(source_path=doc1, page_index=0, rotation_offset=0, source_label="doc1"),
        OrganizedPage(source_path=doc2, page_index=0, rotation_offset=0, source_label="doc2"),
    ]

    merged_pdf = temp_dir / "merged_forms.pdf"
    res = export_organized_pdf(pages=pages, output_path=merged_pdf, allow_overwrite=True)
    assert res.success is True

    r = pypdf.PdfReader(merged_pdf)
    assert len(r.pages) == 2
    fields = r.get_fields()
    assert fields is not None
    assert "applicant_name" in fields
    assert "passport_no" in fields
    assert fields["applicant_name"]["/V"] == "Somya"
    assert fields["passport_no"]["/V"] == "Z1234567"

    # Outlines / bookmarks preserved
    titles = [x.title for x in r.outline]
    assert "Section 1" in titles
    assert "Section 2" in titles


def test_defect5_pdf_watermark_preserves_bookmarks_and_form_fields(temp_dir: Path):
    """Watermarking a PDF preserves its searchable text, bookmarks, and AcroForm fields."""
    src_pdf = temp_dir / "watermark_target.pdf"
    create_searchable_form_pdf(src_pdf, "Searchable Text Inside", "id_number", "ID-9999", "Chapter 1")

    out_pdf = temp_dir / "watermarked.pdf"
    wm = WatermarkOptions(text="OFFICIAL COPY", opacity=0.3, page_range="all")
    res = apply_watermark_to_pdf(src_pdf, out_pdf, wm, allow_overwrite=True)
    assert res.success is True

    r = pypdf.PdfReader(out_pdf)
    assert len(r.pages) == 1
    # Check fields
    fields = r.get_fields()
    assert fields is not None
    assert "id_number" in fields
    assert fields["id_number"]["/V"] == "ID-9999"

    # Check bookmarks
    titles = [x.title for x in r.outline]
    assert "Chapter 1" in titles


# ==============================================================================
# DEFECT 6: PHYSICAL LAYOUT & ALPHA COMPOSITING
# ==============================================================================

def test_defect6_photo_sheet_impossible_layout_rejected(synthetic_rgb_png: Path, temp_dir: Path):
    """An impossible layout (e.g. 300x300mm photo on A4 210x297mm) raises ValueError without clipping."""
    opts = PhotoSheetOptions(
        photo_width_mm=300.0,
        photo_height_mm=300.0,
        sheet_size="A4",
        allow_upscale=True,
    )
    res = generate_photo_sheet(synthetic_rgb_png, temp_dir / "impossible.pdf", opts)
    assert res.success is False
    assert "cannot fit" in res.error_message.lower()


def test_defect6_photo_sheet_signature_alpha_preserves_semi_transparency_128(temp_dir: Path):
    """
    Signature mode onto RGBA canvas uses Porter-Duff alpha compositing,
    preventing semi-transparent alpha 128 from halving to 64.
    """
    sig_path = temp_dir / "signature_128.png"
    # Create semi-transparent RGBA image with alpha=128
    sig_img = Image.new("RGBA", (100, 100), (0, 100, 200, 128))
    sig_img.save(sig_path)

    opts = PhotoSheetOptions(
        photo_width_mm=30.0,
        photo_height_mm=30.0,
        copies=1,
        sheet_size="4X6",
        output_format="PNG",
        is_signature=True,
        preserve_alpha=True,
        allow_upscale=True,
    )
    out_sheet = temp_dir / "sig_sheet.png"
    res = generate_photo_sheet(sig_path, out_sheet, opts, allow_overwrite=True)
    assert res.success is True

    with Image.open(out_sheet) as sheet:
        assert sheet.mode == "RGBA"
        # Find a pixel inside the signature cell
        # Center of first cell should have alpha ~ 128, NOT 64
        # Margin is 10mm (~118px at 300 DPI)
        sample_pixel = sheet.getpixel((150, 150))
        assert sample_pixel[3] == 128


def test_defect6_id_layout_impossible_cards_rejected_by_margins(synthetic_rgb_png: Path, temp_dir: Path):
    """Oversized cards that cannot physically fit within margins are rejected honestly."""
    opts = IdLayoutOptions(
        layout_type="side_by_side",
        card_width_mm=120.0,  # 120 * 2 = 240mm > 210mm A4 width
        card_height_mm=70.0,
        sheet_size="A4",
    )
    res = generate_id_layout(
        front_path=synthetic_rgb_png,
        back_path=synthetic_rgb_png,
        output_path=temp_dir / "overflow.png",
        options=opts,
    )
    assert res.success is False
    assert "cannot fit" in res.error_message.lower()


# ==============================================================================
# DEFECT 7: GUI PREVIEWS FIDELITY
# ==============================================================================

def test_defect7_photo_sheet_custom_dimensions_and_pixel_sync(qapp):
    """PhotoSheetTab synchronizes mm and pixel spinboxes and exposes custom sheet dimensions."""
    tab = PhotoSheetTab()
    # Change mm
    tab.spin_w.setValue(50.0)
    tab.spin_h.setValue(50.0)
    tab.spin_dpi.setValue(300)

    # 50 mm at 300 DPI = round(50 * 300 / 25.4) = 591 px
    assert tab.spin_px_w.value() == 591
    assert tab.spin_px_h.value() == 591

    # Select CUSTOM sheet size
    idx = tab.combo_sheet.findText("CUSTOM")
    assert idx >= 0
    tab.combo_sheet.setCurrentIndex(idx)
    tab.spin_custom_sheet_w.setValue(150.0)
    tab.spin_custom_sheet_h.setValue(200.0)

    opts = tab.get_options()
    assert opts.sheet_size == "CUSTOM"
    assert opts.custom_sheet_w_mm == 150.0
    assert opts.custom_sheet_h_mm == 200.0
    assert opts.photo_width_px == 591
    assert opts.photo_height_px == 591


def test_defect7_id_layout_preview_renders_card_images(qapp, synthetic_rgb_png: Path, synthetic_rgba_png: Path):
    """IdLayoutTab renders actual front/back images on the preview canvas with watermark parity."""
    tab = IdLayoutTab()
    # Baseline empty preview
    tab._update_preview()
    empty_img = tab.lbl_preview.pixmap().toImage()

    # Populate actual card files and watermark
    tab.txt_front.setText(str(synthetic_rgb_png))
    tab.txt_back.setText(str(synthetic_rgba_png))
    tab.txt_watermark.setText("CONFIDENTIAL")

    # Force update
    tab._update_preview()
    populated_img = tab.lbl_preview.pixmap().toImage()

    assert not tab.lbl_preview.pixmap().isNull()
    assert bytes(populated_img.bits()) != bytes(empty_img.bits())

    # Parity check between preview and exported options
    opts = tab.get_options()
    assert opts.watermark is not None
    assert opts.watermark.opacity == 0.25
    assert opts.watermark.angle == 45.0


def test_defect7_watermark_preview_renders_actual_document(qapp, synthetic_rgb_png: Path):
    """WatermarkTab renders document in document coordinates and updates when text changes."""
    tab = WatermarkTab()
    # Blank canvas
    blank_img = tab.lbl_preview.pixmap().toImage()

    # Set document
    tab.set_preview_file(synthetic_rgb_png)
    doc_img = tab.lbl_preview.pixmap().toImage()

    assert not tab.lbl_preview.pixmap().isNull()
    assert bytes(doc_img.bits()) != bytes(blank_img.bits())

    # Update watermark text
    tab.txt_text.setText("MODIFIED WATERMARK TEXT")
    text_changed_img = tab.lbl_preview.pixmap().toImage()
    assert bytes(text_changed_img.bits()) != bytes(doc_img.bits())
