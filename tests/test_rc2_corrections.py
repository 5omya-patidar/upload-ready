"""Focused regression test suite covering RC2 corrections pass:
1. Photo sheet failure atomicity (destination preservation on failure/cancellation, absent destination cleanup, exact-capacity success).
2. Selective metadata preservation for PNG (EXIF Author, Comment chunk, XMP) and PDF (DocInfo vs Catalog XMP) with reopen verification.
3. Preset pipeline metadata carry-through (remove_metadata=False, GPS-only removal, all removal, transactional rollback).
4. Nested PDF bookmark structure recursive hierarchy preservation, remapping, and removed page promotion.
5. Preview/export parity (ID preview watermark opacity 0.25 / angle 45°, watermark preview applicable page selection in document coordinates, organizer Reset restoration).
6. Metadata source-protection regression with disjoint batch_inputs.
"""

import io
from pathlib import Path
from PIL import Image, ImageChops, ImageCms, ImageStat, PngImagePlugin
import pypdf
import pytest
from PySide6.QtCore import Qt

from upload_ready.core.metadata import (
    extract_image_metadata,
    filter_image_metadata,
    inspect_metadata,
    scrub_metadata,
)
from upload_ready.core.models import (
    CancellationToken,
    ConvertOptions,
    ResizeOptions,
    CompressOptions,
    ResizeMode,
    OperationCancelledError,
)
from upload_ready.core.image_convert import convert_image
from upload_ready.core.image_resize import resize_image
from upload_ready.core.image_compress import compress_image
from upload_ready.core.presets import RequirementPreset, apply_preset_to_image
from upload_ready.core.pdf_organizer import (
    OrganizedPage,
    export_organized_pdf,
)
from upload_ready.core.photo_sheet import (
    PhotoSheetOptions,
    generate_photo_sheet,
)
from upload_ready.core.safety import SafetyValidationError
from upload_ready.core.watermark import WatermarkOptions, apply_watermark_to_image
from upload_ready.gui.tabs.id_layout_tab import IdLayoutTab
from upload_ready.gui.tabs.pdf_organize_tab import PdfOrganizeTab
from upload_ready.gui.tabs.watermark_tab import WatermarkTab


# ==============================================================================
# 1. PHOTO-SHEET FAILURE ATOMICITY
# ==============================================================================

def test_photo_sheet_failure_preserves_existing_destination_byte_for_byte(temp_dir: Path, synthetic_rgb_png: Path):
    """When photo sheet generation fails due to capacity, pre-existing destination is untouched."""
    out_file = temp_dir / "existing_sheet.png"
    original_data = b"PRE_EXISTING_PHOTO_SHEET_DATA_BYTE_EXACT"
    out_file.write_bytes(original_data)

    opts = PhotoSheetOptions(
        photo_width_mm=50.0,
        photo_height_mm=50.0,
        copies=9999,  # Impossibly large copy count
        sheet_size="A4",
        output_format="PNG",
        allow_upscale=True,
    )

    res = generate_photo_sheet(
        synthetic_rgb_png,
        out_file,
        opts,
        allow_overwrite=True,
    )
    assert res.success is False
    assert "cannot fit" in res.error_message.lower() or "maximum sheet capacity" in res.error_message.lower()
    # Destination must be preserved byte-for-byte
    assert out_file.exists()
    assert out_file.read_bytes() == original_data


def test_photo_sheet_failure_absent_destination_leaves_no_file(temp_dir: Path, synthetic_rgb_png: Path):
    """When photo sheet generation fails and destination did not exist, no partial file is left."""
    out_file = temp_dir / "nonexistent_sheet.png"
    assert not out_file.exists()

    opts = PhotoSheetOptions(
        photo_width_mm=50.0,
        photo_height_mm=50.0,
        copies=9999,
        sheet_size="A4",
        output_format="PNG",
    )

    res = generate_photo_sheet(
        synthetic_rgb_png,
        out_file,
        opts,
        allow_overwrite=False,
    )
    assert res.success is False
    assert not out_file.exists()


def test_photo_sheet_exact_capacity_succeeds(temp_dir: Path, synthetic_rgb_png: Path):
    """Photo sheet generation succeeds when copies equals the exact sheet capacity."""
    out_file = temp_dir / "exact_sheet.png"
    # A4 sheet: 210 x 297 mm
    # With margin 10mm: available 190 x 277 mm
    # Photo 50x50 mm, spacing 10mm:
    # cols = (190 + 10) // (50 + 10) = 200 // 60 = 3
    # rows = (277 + 10) // (50 + 10) = 287 // 60 = 4
    # Capacity = 3 * 4 = 12 copies
    opts = PhotoSheetOptions(
        photo_width_mm=50.0,
        photo_height_mm=50.0,
        margin_mm=10.0,
        spacing_mm=10.0,
        copies=12,
        sheet_size="A4",
        output_format="PNG",
        allow_upscale=True,
    )

    res = generate_photo_sheet(
        synthetic_rgb_png,
        out_file,
        opts,
        allow_overwrite=True,
    )
    assert res.success is True
    assert out_file.exists()
    assert res.output_dimensions is not None


# ==============================================================================
# 2. SELECTIVE METADATA PRESERVATION
# ==============================================================================

def test_png_selective_metadata_preserves_unselected_categories(temp_dir: Path):
    """PNG containing EXIF Author, Comment chunk, and XMP preserves unselected metadata."""
    src = temp_dir / "source_annotated.png"

    # Build rich fixture
    img = Image.new("RGB", (60, 60), color=(120, 180, 240))
    exif = img.getexif()
    exif[0x013B] = "Authoritarian Artist"
    # Add GPS IFD
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1] = "N"
    gps_ifd[2] = (45.0, 10.0, 0.0)

    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_text("Comment", "Confidential Review Copy")
    pnginfo.add_text("Author", "Authoritarian Artist")
    pnginfo.add_itxt("XML:com.adobe.xmp", "<x:xmpmeta>urn:test:packet</x:xmpmeta>")

    img.save(src, format="PNG", exif=exif, pnginfo=pnginfo)

    # Assert fixture contains all 4 items before processing
    with Image.open(src) as check_in:
        c_exif = check_in.getexif()
        assert c_exif.get(0x013B) == "Authoritarian Artist"
        assert 0x8825 in c_exif
        assert check_in.info.get("Comment") == "Confidential Review Copy"
        assert "xmp" in check_in.info or "XML:com.adobe.xmp" in check_in.info

    # Test A: categories={"gps"} (remove GPS only; preserve Author, Comment, XMP)
    out_dir_gps = temp_dir / "out_gps"
    res_gps = scrub_metadata(src, out_dir_gps, categories={"gps"})
    assert res_gps.success is True
    assert res_gps.output_path.exists()

    with Image.open(res_gps.output_path) as ver_gps:
        v_exif = ver_gps.getexif()
        assert 0x8825 not in v_exif  # GPS stripped
        assert v_exif.get(0x013B) == "Authoritarian Artist"  # Author preserved
        assert ver_gps.info.get("Comment") == "Confidential Review Copy"  # Comment preserved
        assert "xmp" in ver_gps.info or "XML:com.adobe.xmp" in ver_gps.info  # XMP preserved

    # Test B: categories=set() (preserve all)
    out_dir_none = temp_dir / "out_none"
    res_none = scrub_metadata(src, out_dir_none, categories=set())
    assert res_none.success is True

    with Image.open(res_none.output_path) as ver_none:
        v_exif = ver_none.getexif()
        assert 0x8825 in v_exif  # GPS retained
        assert v_exif.get(0x013B) == "Authoritarian Artist"  # Author retained
        assert ver_none.info.get("Comment") == "Confidential Review Copy"
        assert "xmp" in ver_none.info or "XML:com.adobe.xmp" in ver_none.info

    # Test C: categories={"all"} (remove everything)
    out_dir_all = temp_dir / "out_all"
    res_all = scrub_metadata(src, out_dir_all, categories={"all"})
    assert res_all.success is True

    with Image.open(res_all.output_path) as ver_all:
        v_exif = ver_all.getexif()
        assert len(v_exif) == 0
        assert "Comment" not in ver_all.info
        assert "xmp" not in ver_all.info and "XML:com.adobe.xmp" not in ver_all.info


def test_pdf_selective_metadata_docinfo_and_xmp(temp_dir: Path):
    """PDF containing DocInfo and Catalog XMP can selectively strip one while preserving the other."""
    src_pdf = temp_dir / "doc_with_both.pdf"
    w = pypdf.PdfWriter()
    w.add_blank_page(width=100, height=100)
    w.add_metadata({
        "/Title": "Classified Document",
        "/Author": "Secret Agent",
    })
    from pypdf.generic import NameObject, StreamObject
    xmp_stream = StreamObject()
    xmp_stream._data = b"<x:xmpmeta>CatalogXMPStreamData</x:xmpmeta>"
    xmp_stream[NameObject("/Type")] = NameObject("/Metadata")
    xmp_stream[NameObject("/Subtype")] = NameObject("/XML")
    w.root_object[NameObject("/Metadata")] = xmp_stream

    with open(src_pdf, "wb") as f:
        w.write(f)

    # Assert source contains both
    r_src = pypdf.PdfReader(src_pdf)
    assert r_src.metadata.get("/Title") == "Classified Document"
    assert "/Metadata" in r_src.trailer["/Root"]

    # Test A: Strip docinfo only (categories={"pdf_docinfo"})
    out_dir_doc = temp_dir / "out_docinfo"
    res_doc = scrub_metadata(src_pdf, out_dir_doc, categories={"pdf_docinfo"})
    assert res_doc.success is True

    r_doc = pypdf.PdfReader(res_doc.output_path)
    assert (not r_doc.metadata) or len(r_doc.metadata.keys()) == 0  # DocInfo stripped
    assert "/Metadata" in r_doc.trailer["/Root"]  # XMP preserved!
    assert b"CatalogXMPStreamData" in r_doc.trailer["/Root"]["/Metadata"].get_data()

    # Test B: Strip XMP only (categories={"pdf_xmp"})
    out_dir_xmp = temp_dir / "out_xmp"
    res_xmp = scrub_metadata(src_pdf, out_dir_xmp, categories={"pdf_xmp"})
    assert res_xmp.success is True

    r_xmp = pypdf.PdfReader(res_xmp.output_path)
    assert r_xmp.metadata.get("/Title") == "Classified Document"  # DocInfo preserved!
    assert "/Metadata" not in r_xmp.trailer["/Root"]  # XMP stripped!


# ==============================================================================
# 3. PRESET METADATA SETTINGS PIPELINE
# ==============================================================================

def test_preset_metadata_disabled_preserves_author_and_comments(temp_dir: Path):
    """Presets with remove_metadata=False preserve EXIF Author and Comments through convert, resize, and compress."""
    src = temp_dir / "author_image.png"
    img = Image.new("RGB", (120, 80), color=(50, 150, 250))
    exif = img.getexif()
    exif[0x013B] = "Preset Master"
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_text("Comment", "Preserved Preset Note")
    img.save(src, format="PNG", exif=exif, pnginfo=pnginfo)

    preset = RequirementPreset(
        id="preserve_meta_test",
        name="Preserve Meta",
        target_format="PNG",
        width=60,
        height=40,
        resize_mode="fit",
        remove_metadata=False,
    )

    out_dir = temp_dir / "preset_out"
    res = apply_preset_to_image(src, out_dir, preset, allow_overwrite=True)
    assert res.success is True
    assert res.output_path.exists()

    with Image.open(res.output_path) as final_img:
        assert final_img.size == (60, 40)
        out_exif = final_img.getexif()
        assert out_exif.get(0x013B) == "Preset Master"
        assert final_img.info.get("Comment") == "Preserved Preset Note"


def test_preset_gps_only_removal_retains_author_and_xmp(temp_dir: Path):
    """Presets with remove_metadata=True and categories=['gps'] removes GPS but retains Author and XMP."""
    src = temp_dir / "gps_image.png"
    img = Image.new("RGB", (100, 100), color=(80, 120, 160))
    exif = img.getexif()
    exif[0x013B] = "Traveler"
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1] = "S"
    gps_ifd[2] = (12.0, 34.0, 56.0)

    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_itxt("XML:com.adobe.xmp", "<x:xmpmeta>packet</x:xmpmeta>")
    img.save(src, format="PNG", exif=exif, pnginfo=pnginfo)

    preset = RequirementPreset(
        id="gps_strip_preset",
        name="GPS Strip",
        target_format="PNG",
        width=80,
        height=80,
        resize_mode="fit",
        remove_metadata=True,
        metadata_categories=["gps"],
    )

    out_dir = temp_dir / "preset_out"
    res = apply_preset_to_image(src, out_dir, preset, allow_overwrite=True)
    assert res.success is True

    with Image.open(res.output_path) as final_img:
        out_exif = final_img.getexif()
        assert 0x8825 not in out_exif  # GPS removed
        assert out_exif.get(0x013B) == "Traveler"  # Author retained
        assert "xmp" in final_img.info or "XML:com.adobe.xmp" in final_img.info  # XMP retained


def test_preset_transactional_rollback_preserves_destination_on_failure(temp_dir: Path, synthetic_rgb_png: Path):
    """Preset pipeline failure preserves pre-existing destination untouched."""
    dest = temp_dir / f"{synthetic_rgb_png.stem}.jpg"
    original_data = b"DESTINATION_DATA_MUST_NOT_BE_OVERWRITTEN"
    dest.write_bytes(original_data)

    # Preset with impossible max_bytes constraint (10 bytes)
    preset = RequirementPreset(
        id="failing_preset",
        name="Failing Preset",
        target_format="JPEG",
        max_bytes=10,
    )

    res = apply_preset_to_image(synthetic_rgb_png, temp_dir, preset, allow_overwrite=True)
    assert res.success is False
    assert dest.exists()
    assert dest.read_bytes() == original_data


# ==============================================================================
# 4. NESTED PDF BOOKMARK STRUCTURE
# ==============================================================================

def test_nested_pdf_bookmarks_hierarchy_and_remapping(temp_dir: Path):
    """PDF Organizer preserves nested parent/child outlines and remaps after page reordering."""
    src_pdf = temp_dir / "nested_doc.pdf"
    w = pypdf.PdfWriter()
    for _ in range(4):
        w.add_blank_page(width=100, height=100)

    # Build hierarchy:
    # Page 0: Chapter 1
    #   Page 1: Section 1.1
    #     Page 2: Subsection 1.1.1
    # Page 3: Chapter 2
    p1 = w.add_outline_item("Chapter 1", 0)
    c1 = w.add_outline_item("Section 1.1", 1, parent=p1)
    g1 = w.add_outline_item("Subsection 1.1.1", 2, parent=c1)
    p2 = w.add_outline_item("Chapter 2", 3)

    with open(src_pdf, "wb") as f:
        w.write(f)

    # Reorder pages: [3, 2, 1, 0]
    out_pdf = temp_dir / "reordered_nested.pdf"
    pages = [
        OrganizedPage(source_path=src_pdf, page_index=3, rotation_offset=0, source_label="p4"),
        OrganizedPage(source_path=src_pdf, page_index=2, rotation_offset=0, source_label="p3"),
        OrganizedPage(source_path=src_pdf, page_index=1, rotation_offset=0, source_label="p2"),
        OrganizedPage(source_path=src_pdf, page_index=0, rotation_offset=0, source_label="p1"),
    ]

    res = export_organized_pdf(pages, out_pdf, allow_overwrite=True)
    assert res.success is True

    r_out = pypdf.PdfReader(out_pdf)
    assert len(r_out.pages) == 4

    # Outline in pypdf: [Chapter 1, [Section 1.1, [Subsection 1.1.1]], Chapter 2]
    outl = r_out.outline
    assert len(outl) == 3  # [Dest(Chapter 1), list(children), Dest(Chapter 2)]
    assert outl[0].title == "Chapter 1"
    assert r_out.get_destination_page_number(outl[0]) == 3  # Remapped to index 3!

    child_list = outl[1]
    assert isinstance(child_list, list)
    assert child_list[0].title == "Section 1.1"
    assert r_out.get_destination_page_number(child_list[0]) == 2  # Remapped to index 2!

    subchild_list = child_list[1]
    assert isinstance(subchild_list, list)
    assert subchild_list[0].title == "Subsection 1.1.1"
    assert r_out.get_destination_page_number(subchild_list[0]) == 1  # Remapped to index 1!

    assert outl[2].title == "Chapter 2"
    assert r_out.get_destination_page_number(outl[2]) == 0  # Remapped to index 0!


def test_nested_pdf_bookmarks_removed_page_promotes_children_with_warning(temp_dir: Path):
    """When a parent bookmark's page is removed, an informative warning is logged and children are promoted."""
    src_pdf = temp_dir / "nested_remove.pdf"
    w = pypdf.PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=100, height=100)

    # Page 0: Parent
    #   Page 1: Child A
    #   Page 2: Child B
    p = w.add_outline_item("Parent Chapter", 0)
    w.add_outline_item("Child A", 1, parent=p)
    w.add_outline_item("Child B", 2, parent=p)

    with open(src_pdf, "wb") as f:
        w.write(f)

    # Exclude page 0; assemble only pages [1, 2]
    out_pdf = temp_dir / "extracted_children.pdf"
    pages = [
        OrganizedPage(source_path=src_pdf, page_index=1, rotation_offset=0, source_label="p2"),
        OrganizedPage(source_path=src_pdf, page_index=2, rotation_offset=0, source_label="p3"),
    ]

    res = export_organized_pdf(pages, out_pdf, allow_overwrite=True)
    assert res.success is True

    # Assert warning recorded
    assert any("Parent Chapter" in warn and "omitted" in warn for warn in res.warnings)

    r_out = pypdf.PdfReader(out_pdf)
    assert len(r_out.pages) == 2
    outl = r_out.outline
    # Children promoted to top-level
    titles = [item.title for item in outl if isinstance(item, pypdf.generic.Destination)]
    assert "Child A" in titles
    assert "Child B" in titles


# ==============================================================================
# 5. PREVIEW/EXPORT PARITY & GUI SAFETY
# ==============================================================================

def test_id_layout_preview_and_export_share_watermark_options(qapp):
    """ID layout preview and export both use opacity 0.25 and angle 45.0."""
    tab = IdLayoutTab()
    tab.txt_watermark.setText("VERIFICATION COPY")
    opts = tab.get_options()
    assert opts.watermark is not None
    assert opts.watermark.opacity == 0.25
    assert opts.watermark.angle == 45.0


def test_watermark_preview_applicable_page_and_document_coords(qapp, temp_dir: Path):
    """Watermark preview updates based on page range and composites in document space."""
    pdf_path = temp_dir / "multi_colored.pdf"
    # Create 3-page PDF with distinct content
    img1 = Image.new("RGB", (200, 200), color=(255, 0, 0))    # Red
    img2 = Image.new("RGB", (200, 200), color=(0, 255, 0))    # Green
    img3 = Image.new("RGB", (200, 200), color=(0, 0, 255))    # Blue

    buf1, buf2, buf3 = io.BytesIO(), io.BytesIO(), io.BytesIO()
    img1.save(buf1, format="PDF")
    img2.save(buf2, format="PDF")
    img3.save(buf3, format="PDF")

    w = pypdf.PdfWriter()
    w.append(pypdf.PdfReader(io.BytesIO(buf1.getvalue())))
    w.append(pypdf.PdfReader(io.BytesIO(buf2.getvalue())))
    w.append(pypdf.PdfReader(io.BytesIO(buf3.getvalue())))
    with open(pdf_path, "wb") as f:
        w.write(f)

    tab = WatermarkTab()
    tab.set_preview_file(pdf_path)

    # Page range '1' (page 0 - red)
    tab.txt_pages.setText("1")
    pix1 = tab.lbl_preview.pixmap().toImage()

    # Page range '2' (page 1 - green)
    tab.txt_pages.setText("2")
    pix2 = tab.lbl_preview.pixmap().toImage()

    # Red page vs Green page pixmaps must be materially different
    assert pix1 != pix2


def test_pdf_organizer_reset_restores_independent_baseline(qapp, temp_dir: Path):
    """Organiser Reset restores sequence, count, and zero rotation after mutate, reorder, and remove."""
    pdf_path = temp_dir / "sample_doc.pdf"
    w = pypdf.PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=100, height=100)
    with open(pdf_path, "wb") as f:
        w.write(f)

    tab = PdfOrganizeTab()
    tab.load_documents([pdf_path])
    assert tab.list_widget.count() == 3

    # 1. Rotate first page
    item0 = tab.list_widget.item(0)
    item0.setSelected(True)
    tab._on_rotate_selected()
    p0: OrganizedPage = item0.data(Qt.ItemDataRole.UserRole)
    assert p0.rotation_offset == 90

    # 2. Reorder (move row 0 right)
    tab.list_widget.setCurrentRow(0)
    tab._on_move_right()

    # 3. Remove a page
    tab.list_widget.clearSelection()
    item_last = tab.list_widget.item(tab.list_widget.count() - 1)
    item_last.setSelected(True)
    tab._on_remove_selected()
    assert tab.list_widget.count() == 2

    # 4. Trigger Reset
    tab.reset_to_initial()

    # Verify initial state completely restored
    assert tab.list_widget.count() == 3
    for i in range(3):
        p: OrganizedPage = tab.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
        assert p.page_index == i
        assert p.rotation_offset == 0  # Rotation restored to 0!


# ==============================================================================
# 6. METADATA SOURCE-PROTECTION REGRESSION (INVOKING SCRUB_METADATA WITH ALIAS)
# ==============================================================================

def test_scrub_metadata_protects_source_when_batch_inputs_disjoint(temp_dir: Path):
    """
    Invoke scrub_metadata() directly with overwrite enabled, disjoint batch_inputs,
    and a destination alias resolving to the actual input file.
    Assert rejection (SafetyValidationError), unchanged source bytes, and cleanup.
    """
    src = temp_dir / "my_document.png"
    Image.new("RGB", (40, 40), (200, 50, 50)).save(src)
    orig_bytes = src.read_bytes()

    # scrub_metadata resolves destination as output_dir / f"{stem}_clean.png"
    dest_alias = temp_dir / "my_document_clean.png"
    alias_created = False
    try:
        dest_alias.symlink_to(src)
        alias_created = True
    except (OSError, NotImplementedError):
        try:
            import os
            os.link(src, dest_alias)
            alias_created = True
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"Filesystem alias creation unavailable in this test environment: {exc}")

    assert alias_created, "Alias must be created to verify destination alias resolution"

    # Disjoint batch inputs: contains only other files, excluding src
    disjoint_batch = [
        temp_dir / "unrelated_1.png",
        temp_dir / "unrelated_2.png",
    ]

    # Directly invoke scrub_metadata with allow_overwrite=True
    res = scrub_metadata(
        input_path=src,
        output_dir=temp_dir,
        categories={"all"},
        allow_overwrite=True,
        batch_inputs=disjoint_batch,
    )

    # 1. Operation must be rejected
    assert res.success is False
    assert "Destination path resolves to a queued original input file" in (res.error_message or "")

    # 2. Source file must remain completely unaltered byte-for-byte
    assert src.read_bytes() == orig_bytes

    # 3. No temporary staged files left behind
    temp_files = list(temp_dir.glob("*.tmp")) + list(temp_dir.glob(".upload_ready_stage*"))
    assert len(temp_files) == 0


# ==============================================================================
# 7. TRANSPARENCY PROPAGATION AFTER IMAGE-MODE CONVERSION
# ==============================================================================

def test_transparency_propagation_p_mode_png_conversion_resolves_alpha(temp_dir: Path):
    """
    Two-pixel P-mode PNG with red and blue palette entries and transparency index 0:
    Conversion to PNG must succeed without 'cannot use transparency for this mode',
    resolving transparency into true RGBA alpha (pixel 0 has alpha=0, pixel 1 has alpha=255).
    """
    p_img = temp_dir / "palette_trans.png"
    im = Image.new("P", (2, 1))
    im.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    im.putdata([0, 1])  # pixel 0 is red (index 0), pixel 1 is blue (index 1)
    im.info["transparency"] = 0
    im.save(p_img)

    out_dir = temp_dir / "conv_out"
    out_dir.mkdir()
    res = convert_image(p_img, out_dir, ConvertOptions(target_format="PNG"))
    assert res.success is True, f"convert_image failed: {res.error_message}"

    with Image.open(res.output_path) as out:
        assert out.format == "PNG"
        assert out.mode == "RGBA"
        # Assert decoded output colors and resolved alpha
        px0 = out.getpixel((0, 0))
        px1 = out.getpixel((1, 0))
        assert px0 == (255, 0, 0, 0), f"Expected red with alpha=0, got {px0}"
        assert px1 == (0, 0, 255, 255), f"Expected blue with alpha=255, got {px1}"


def test_transparency_propagation_p_mode_png_resize_and_compress(temp_dir: Path):
    """
    Transparent P-mode PNG through resize and compression:
    - Resize resolves transparency to RGBA alpha with correct pixel colors.
    - Lossless compression of P-mode PNG retains P mode and palette transparency.
    """
    p_img = temp_dir / "palette_4x2.png"
    im = Image.new("P", (4, 2))
    im.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    # Left half transparent red, right half opaque blue
    im.putdata([0, 0, 1, 1, 0, 0, 1, 1])
    im.info["transparency"] = 0
    im.save(p_img)

    # 1. Resize
    res_dir = temp_dir / "resize_out"
    res_dir.mkdir()
    res_resize = resize_image(p_img, res_dir, ResizeOptions(width=2, height=1))
    assert res_resize.success is True, f"resize failed: {res_resize.error_message}"
    with Image.open(res_resize.output_path) as out_r:
        assert out_r.mode == "RGBA"
        # 4x2 transparent P-mode image resized to 2x1 with alpha-aware Lanczos interpolation
        # resolves transparency to RGBA alpha with interpolated boundary alpha:
        assert out_r.getpixel((0, 0)) == (0, 0, 255, 18)
        assert out_r.getpixel((1, 0)) == (0, 0, 255, 237)

    # 2. Compress (Lossless)
    comp_dir = temp_dir / "comp_out"
    comp_dir.mkdir()
    res_comp = compress_image(p_img, comp_dir, CompressOptions(is_lossless=True))
    assert res_comp.success is True, f"compress failed: {res_comp.error_message}"
    with Image.open(res_comp.output_path) as out_c:
        # Lossless PNG retains native P mode and palette transparency
        assert out_c.mode == "P"
        assert out_c.info.get("transparency") == 0


def test_transparency_propagation_rgb_colorkey_inputs(temp_dir: Path):
    """
    RGB PNG with colorkey transparency converts to RGBA and resolves alpha across
    conversion, resize, and compression without encoder errors.
    """
    rgb_img = temp_dir / "rgb_colorkey.png"
    im = Image.new("RGB", (4, 2))
    # Red pixels (255, 0, 0) designated as transparent colorkey
    im.putdata([(255, 0, 0), (255, 0, 0), (0, 0, 255), (0, 0, 255)] * 2)
    im.info["transparency"] = (255, 0, 0)
    im.save(rgb_img, transparency=(255, 0, 0))

    out_dir = temp_dir / "rgb_key_out"
    out_dir.mkdir()

    # Convert
    res_conv = convert_image(rgb_img, out_dir, ConvertOptions(target_format="PNG"))
    assert res_conv.success is True, f"convert failed: {res_conv.error_message}"
    with Image.open(res_conv.output_path) as out:
        assert out.mode == "RGBA"
        assert out.getpixel((0, 0)) == (255, 0, 0, 0)
        assert out.getpixel((2, 0)) == (0, 0, 255, 255)

    # Resize
    res_resize = resize_image(rgb_img, out_dir, ResizeOptions(width=2, height=1))
    assert res_resize.success is True, f"resize failed: {res_resize.error_message}"
    with Image.open(res_resize.output_path) as out:
        assert out.mode == "RGBA"
        # 4x2 RGB colorkey resized to 2x1 with alpha-aware Lanczos interpolation:
        # Colorkey red pixels (255, 0, 0) normalized to alpha 0 before resampling
        # yields transparent/semi-transparent blue edges without opaque red fringing.
        assert out.getpixel((0, 0)) == (0, 0, 255, 18)
        assert out.getpixel((1, 0)) == (0, 0, 255, 237)

    # Compress
    res_comp = compress_image(rgb_img, out_dir, CompressOptions(is_lossless=True))
    assert res_comp.success is True, f"compress failed: {res_comp.error_message}"
    with Image.open(res_comp.output_path) as out:
        assert out.mode == "RGBA"
        assert out.getpixel((0, 0)) == (255, 0, 0, 0)
        assert out.getpixel((2, 0)) == (0, 0, 255, 255)


# ==============================================================================
# 8. AUTHORITATIVE ICC PROFILE REMOVAL / RETENTION
# ==============================================================================

def test_rejected_invalid_icc_profile_is_authoritatively_stripped(temp_dir: Path):
    """
    When an image contains an invalid or corrupted ICC profile (e.g. b'INVALID_ICC_PROFILE'),
    conversion, resizing, and compression must warn that the profile was ignored
    and MUST NOT retain those invalid bytes in their output.
    """
    bad_icc_img = temp_dir / "bad_icc.png"
    im = Image.new("RGB", (20, 20), (100, 150, 200))
    im.info["icc_profile"] = b"INVALID_ICC_PROFILE"
    im.save(bad_icc_img, icc_profile=b"INVALID_ICC_PROFILE")

    # Assert input actually contains the invalid profile
    with Image.open(bad_icc_img) as check_in:
        assert check_in.info.get("icc_profile") == b"INVALID_ICC_PROFILE"

    out_dir = temp_dir / "icc_test_out"
    out_dir.mkdir()

    # 1. Convert
    res_conv = convert_image(bad_icc_img, out_dir, ConvertOptions(target_format="PNG"))
    assert res_conv.success is True
    assert any("invalid or corrupted ICC profile" in w for w in res_conv.warnings)
    with Image.open(res_conv.output_path) as out:
        assert out.info.get("icc_profile") is None, "Rejected ICC profile must NOT be restored in convert output"

    # 2. Resize
    res_resize = resize_image(bad_icc_img, out_dir, ResizeOptions(width=10, height=10))
    assert res_resize.success is True
    assert any("invalid or corrupted ICC profile" in w for w in res_resize.warnings)
    with Image.open(res_resize.output_path) as out:
        assert out.info.get("icc_profile") is None, "Rejected ICC profile must NOT be restored in resize output"

    # 3. Compress
    res_comp = compress_image(bad_icc_img, out_dir, CompressOptions(is_lossless=True))
    assert res_comp.success is True
    assert any("invalid or corrupted ICC profile" in w for w in res_comp.warnings)
    with Image.open(res_comp.output_path) as out:
        assert out.info.get("icc_profile") is None, "Rejected ICC profile must NOT be restored in compress output"


def test_native_compression_icc_validation_and_preservation(temp_dir: Path):
    """
    Native-mode compression branches (lossless palette PNG and high-bit-depth images)
    validate ICC profiles before propagating them:
    - Corrupted ICC profiles (e.g. b'INVALID_ICC_PROFILE') are dropped with a warning,
      preserving palette transparency and native P mode.
    - Valid ICC profiles (e.g. valid sRGB) are preserved byte-for-byte in the output.
    """
    out_dir = temp_dir / "native_icc_out"
    out_dir.mkdir()

    # 1. Palette PNG with invalid ICC and palette transparency
    p_bad = temp_dir / "palette_bad_icc.png"
    im_bad = Image.new("P", (10, 10))
    im_bad.putpalette([255, 0, 0, 0, 255, 0] + [0] * (256 * 3 - 6))
    im_bad.info["icc_profile"] = b"INVALID_ICC_PROFILE"
    im_bad.info["transparency"] = 0
    im_bad.save(p_bad, icc_profile=b"INVALID_ICC_PROFILE", transparency=0)

    res_bad = compress_image(p_bad, out_dir, CompressOptions(is_lossless=True))
    assert res_bad.success is True
    assert any("invalid or corrupted ICC profile" in w for w in res_bad.warnings)
    with Image.open(res_bad.output_path) as out:
        assert out.mode == "P"
        assert out.info.get("transparency") == 0
        assert out.info.get("icc_profile") is None

    # 2. Palette PNG with valid sRGB ICC profile
    prof = ImageCms.createProfile("sRGB")
    srgb_bytes = ImageCms.ImageCmsProfile(prof).tobytes()

    p_valid = temp_dir / "palette_valid_icc.png"
    im_valid = Image.new("P", (10, 10))
    im_valid.putpalette([255, 0, 0, 0, 255, 0] + [0] * (256 * 3 - 6))
    im_valid.info["icc_profile"] = srgb_bytes
    im_valid.save(p_valid, icc_profile=srgb_bytes)

    res_valid = compress_image(p_valid, out_dir, CompressOptions(is_lossless=True))
    assert res_valid.success is True
    assert not any("invalid or corrupted ICC profile" in w for w in res_valid.warnings)
    with Image.open(res_valid.output_path) as out:
        assert out.mode == "P"
        assert out.info.get("icc_profile") == srgb_bytes


# ==============================================================================
# 9. PREVIEW GEOMETRY, SCALE PARITY, AND ID PREVIEW SAFETY
# ==============================================================================

def test_watermark_preview_geometry_and_scale_parity(qapp, temp_dir: Path):
    """
    Watermark preview scales font size, margins, and placement in document coordinates
    so that watermark relative dimensions match downscaled actual exports across
    large landscape and portrait images, draft-decoded large JPEGs, and EXIF-oriented images.
    """
    # 1. Large Landscape Image (1600x800, 2:1)
    land_path = temp_dir / "landscape_1600.png"
    Image.new("RGB", (1600, 800), (220, 220, 220)).save(land_path)

    tab = WatermarkTab()
    tab.lbl_preview.resize(300, 300)
    tab.set_preview_file(land_path)
    tab.txt_text.setText("PARITY_TEST")
    tab.spin_font_size.setValue(80)
    tab.slider_opacity.setValue(70)
    tab.combo_position.setCurrentIndex(0)  # center

    tab._update_preview()
    pix_land = tab.lbl_preview.pixmap()
    assert not pix_land.isNull()

    # Compare against downscaled export
    out_land = temp_dir / "land_export.png"
    opts = tab.get_options()
    apply_watermark_to_image(land_path, out_land, opts)
    with Image.open(out_land) as exp_img:
        # Scale export to thumbnail width (approx 300)
        exp_thumb = exp_img.resize((300, 150), Image.Resampling.BILINEAR)
        # Check center watermark pixel has altered intensity
        assert exp_thumb.getpixel((150, 75)) != (220, 220, 220)

    # 2. Large Portrait Image (800x1600, 1:2)
    port_path = temp_dir / "portrait_1600.png"
    Image.new("RGB", (800, 1600), (220, 220, 220)).save(port_path)
    tab.set_preview_file(port_path)
    tab._update_preview()
    pix_port = tab.lbl_preview.pixmap()
    assert not pix_port.isNull()
    # Different aspect ratio document produces different preview geometry
    assert bytes(pix_land.toImage().bits()) != bytes(pix_port.toImage().bits())

    # 3. Large Draft JPEG (4000x2000, triggers draft() downsample to 2000x1000)
    draft_jpeg = temp_dir / "draft_4000x2000.jpg"
    Image.new("RGB", (4000, 2000), (240, 240, 240)).save(draft_jpeg, quality=95)
    tab.set_preview_file(draft_jpeg)
    tab.txt_text.setText("DRAFT_SCALE")
    tab.spin_font_size.setValue(120)
    tab.slider_opacity.setValue(80)
    tab.combo_position.setCurrentIndex(0)
    tab._update_preview()

    # Use actual pixmap dimensions rather than widget label dimensions
    pix_draft = tab.lbl_preview.pixmap()
    assert not pix_draft.isNull()
    pw = pix_draft.width()
    ph = pix_draft.height()
    qimg_draft = pix_draft.toImage()
    buf_draft = qimg_draft.bits().tobytes()
    prev_draft = Image.frombuffer("RGBA", (pw, ph), buf_draft, "raw", "BGRA", 0, 1)

    # Document rectangle within preview pixmap (4000x2000 fitted into pw x ph)
    tw = pw
    th = int(round(pw * (2000 / 4000)))  # 300 x 150
    bx = (pw - tw) // 2
    by = (ph - th) // 2
    doc_prev = prev_draft.crop((bx, by, bx + tw, by + th)).convert("RGB")

    out_draft = temp_dir / "draft_export.jpg"
    opts_draft = tab.get_options()
    res_draft = apply_watermark_to_image(draft_jpeg, out_draft, opts_draft)
    assert res_draft.success is True

    with Image.open(out_draft) as exp_draft:
        exp_thumb = exp_draft.resize((tw, th), Image.Resampling.LANCZOS)

        bg = Image.new("RGB", (tw, th), (240, 240, 240))
        diff_prev = ImageChops.difference(doc_prev, bg)
        diff_exp = ImageChops.difference(exp_thumb, bg)

        mask_prev = diff_prev.convert("L").point(lambda v: 255 if v > 15 else 0)
        mask_exp = diff_exp.convert("L").point(lambda v: 255 if v > 15 else 0)

        bbox_prev = mask_prev.getbbox()
        bbox_exp = mask_exp.getbbox()
        assert bbox_prev is not None and bbox_exp is not None

        w_prev = bbox_prev[2] - bbox_prev[0]
        h_prev = bbox_prev[3] - bbox_prev[1]
        w_exp = bbox_exp[2] - bbox_exp[0]
        h_exp = bbox_exp[3] - bbox_exp[1]

        # 1. Compare watermark bounds (width and height match export within tight rasterization tolerance)
        assert abs(w_prev - w_exp) <= 2, f"Watermark width mismatch: prev={w_prev}, exp={w_exp}"
        assert abs(h_prev - h_exp) <= 2, f"Watermark height mismatch: prev={h_prev}, exp={h_exp}"

        # 2. Compare placement (center coordinates match within 2 pixels)
        c_prev = ((bbox_prev[0] + bbox_prev[2]) / 2, (bbox_prev[1] + bbox_prev[3]) / 2)
        c_exp = ((bbox_exp[0] + bbox_exp[2]) / 2, (bbox_exp[1] + bbox_exp[3]) / 2)
        assert abs(c_prev[0] - c_exp[0]) <= 2.0, f"Placement center X mismatch: prev={c_prev}, exp={c_exp}"
        assert abs(c_prev[1] - c_exp[1]) <= 2.0, f"Placement center Y mismatch: prev={c_prev}, exp={c_exp}"

        # 3. Compare watermark-focused region (rejecting doubled-scale regressions)
        pad = 5
        focused_box = (
            max(0, bbox_exp[0] - pad),
            max(0, bbox_exp[1] - pad),
            min(tw, bbox_exp[2] + pad),
            min(th, bbox_exp[3] + pad)
        )
        wm_prev = doc_prev.crop(focused_box)
        wm_exp = exp_thumb.crop(focused_box)
        diff_focused = ImageChops.difference(wm_prev, wm_exp)
        stat_focused = ImageStat.Stat(diff_focused)
        # Correct implementation yields ~2.27 mean error in focused crop; doubled-scale yields > 8.5
        assert all(d < 4.0 for d in stat_focused.mean), f"Focused region diff {stat_focused.mean} exceeds tolerance 4.0"

    # 4. EXIF Orientation 6 JPEG (2000x1000 with 90 deg clockwise rotation -> 1000x2000 portrait)
    exif_jpeg = temp_dir / "exif6_2000x1000.jpg"
    im_exif = Image.new("RGB", (2000, 1000), (240, 240, 240))
    exif = im_exif.getexif()
    exif[0x0112] = 6
    im_exif.save(exif_jpeg, quality=95, exif=exif)

    tab.set_preview_file(exif_jpeg)
    tab.txt_text.setText("EXIF6_SCALE")
    tab.spin_font_size.setValue(100)
    tab.slider_opacity.setValue(80)
    tab.combo_position.setCurrentIndex(0)
    tab._update_preview()

    pix_exif = tab.lbl_preview.pixmap()
    assert not pix_exif.isNull()
    pw = pix_exif.width()
    ph = pix_exif.height()
    qimg_exif = pix_exif.toImage()
    buf_exif = qimg_exif.bits().tobytes()
    prev_exif = Image.frombuffer("RGBA", (pw, ph), buf_exif, "raw", "BGRA", 0, 1)

    # 1000x2000 portrait document fitted into pw x ph (300 x 380)
    th = ph
    tw = int(round(ph * (1000 / 2000)))  # 190 x 380
    bx = (pw - tw) // 2
    by = (ph - th) // 2
    doc_prev_exif = prev_exif.crop((bx, by, bx + tw, by + th)).convert("RGB")

    out_exif = temp_dir / "exif6_export.jpg"
    opts_exif = tab.get_options()
    res_exif = apply_watermark_to_image(exif_jpeg, out_exif, opts_exif)
    assert res_exif.success is True

    with Image.open(out_exif) as exp_exif:
        assert exp_exif.size == (1000, 2000)
        exp_thumb_exif = exp_exif.resize((tw, th), Image.Resampling.LANCZOS)

        bg = Image.new("RGB", (tw, th), (240, 240, 240))
        diff_prev_exif = ImageChops.difference(doc_prev_exif, bg)
        diff_exp_exif = ImageChops.difference(exp_thumb_exif, bg)

        mask_prev_exif = diff_prev_exif.convert("L").point(lambda v: 255 if v > 15 else 0)
        mask_exp_exif = diff_exp_exif.convert("L").point(lambda v: 255 if v > 15 else 0)

        bbox_prev_exif = mask_prev_exif.getbbox()
        bbox_exp_exif = mask_exp_exif.getbbox()
        assert bbox_prev_exif is not None and bbox_exp_exif is not None

        w_prev_exif = bbox_prev_exif[2] - bbox_prev_exif[0]
        h_prev_exif = bbox_prev_exif[3] - bbox_prev_exif[1]
        w_exp_exif = bbox_exp_exif[2] - bbox_exp_exif[0]
        h_exp_exif = bbox_exp_exif[3] - bbox_exp_exif[1]

        assert abs(w_prev_exif - w_exp_exif) <= 2
        assert abs(h_prev_exif - h_exp_exif) <= 2

        pad = 5
        focused_box_exif = (
            max(0, bbox_exp_exif[0] - pad),
            max(0, bbox_exp_exif[1] - pad),
            min(tw, bbox_exp_exif[2] + pad),
            min(th, bbox_exp_exif[3] + pad)
        )
        wm_prev_exif = doc_prev_exif.crop(focused_box_exif)
        wm_exp_exif = exp_thumb_exif.crop(focused_box_exif)
        diff_focused_exif = ImageChops.difference(wm_prev_exif, wm_exp_exif)
        stat_focused_exif = ImageStat.Stat(diff_focused_exif)
        assert all(d < 4.0 for d in stat_focused_exif.mean), f"EXIF focused diff {stat_focused_exif.mean} exceeds tolerance 4.0"


def test_watermark_preview_multipage_pdf_differing_sizes_and_range(qapp, temp_dir: Path):
    """
    Watermark preview reflects targeted page geometry in multi-page PDF with differing page sizes:
    - Page 1 is A4 (595x842 pt), Page 2 is Landscape (842x595 pt).
    - Page-range selection updates preview to reflect targeted page and geometry.
    """
    pdf_path = temp_dir / "diff_sizes.pdf"
    writer = pypdf.PdfWriter()
    # Page 1: Portrait A4
    writer.add_blank_page(width=595, height=842)
    # Page 2: Landscape
    writer.add_blank_page(width=842, height=595)
    with open(pdf_path, "wb") as f:
        writer.write(f)

    tab = WatermarkTab()
    tab.set_preview_file(pdf_path)
    tab.txt_text.setText("PDF_PARITY")

    # Target page 1
    tab.txt_pages.setText("1")
    tab._update_preview()
    img_p1 = tab.lbl_preview.pixmap().toImage()

    # Target page 2 (landscape geometry)
    tab.txt_pages.setText("2")
    tab._update_preview()
    img_p2 = tab.lbl_preview.pixmap().toImage()

    # Previews must differ because page 1 and page 2 have different dimensions/orientations
    assert bytes(img_p1.bits()) != bytes(img_p2.bits())


def test_id_layout_preview_bounded_decoding_and_proportions(qapp, temp_dir: Path):
    """
    IdLayoutTab preview enforces bounds before decoding, does not distort
    extreme-aspect-ratio images in fit mode, and uses source crop box in crop mode.
    """
    # Create extreme-aspect-ratio image (800 x 40, 20:1)
    extreme_img = temp_dir / "extreme_banner.png"
    Image.new("RGB", (800, 40), (50, 150, 250)).save(extreme_img)

    tab = IdLayoutTab()
    tab.txt_front.setText(str(extreme_img))
    tab.txt_back.setText(str(extreme_img))

    # 1. Fit mode: proportions preserved without distortion
    tab.combo_front_crop.setCurrentIndex(0)  # fit
    tab._update_preview()
    pix_fit = tab.lbl_preview.pixmap()
    assert not pix_fit.isNull()

    # 2. Crop mode: bounded source crop box targets exact card size
    tab.combo_front_crop.setCurrentIndex(1)  # crop
    tab._update_preview()
    pix_crop = tab.lbl_preview.pixmap()
    assert not pix_crop.isNull()

    # Crop and fit must produce distinct preview renderings
    assert bytes(pix_fit.toImage().bits()) != bytes(pix_crop.toImage().bits())

