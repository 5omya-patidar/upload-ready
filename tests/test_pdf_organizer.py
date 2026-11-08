"""Tests for PDF Page Organiser (Feature 2)."""

from pathlib import Path
import pypdf
import pytest

from upload_ready.core.pdf_ops import PdfPasswordRequiredError
from upload_ready.core.pdf_organizer import (
    OrganizedPage,
    export_organized_pdf,
    merge_multiple_pdfs,
    render_page_thumbnail,
    split_pdf_by_ranges,
)


def test_render_page_thumbnail(synthetic_multipage_pdf: Path):
    thumb = render_page_thumbnail(synthetic_multipage_pdf, page_index=0, max_dim=150)
    assert thumb is not None
    assert max(thumb.size) <= 150
    assert thumb.mode == "RGB"


def test_export_organized_pdf_reorder_and_rotate(synthetic_multipage_pdf: Path, temp_dir: Path):
    out_pdf = temp_dir / "reordered.pdf"
    pages = [
        OrganizedPage(source_path=synthetic_multipage_pdf, page_index=1, rotation_offset=90, source_label="Page 2 rotated"),
        OrganizedPage(source_path=synthetic_multipage_pdf, page_index=0, rotation_offset=0, source_label="Page 1 normal"),
    ]

    res = export_organized_pdf(
        pages=pages,
        output_path=out_pdf,
        allow_overwrite=False,
    )
    assert res.success is True
    assert out_pdf.exists()

    r = pypdf.PdfReader(out_pdf)
    assert len(r.pages) == 2
    # Check rotation offset applied
    assert r.pages[0].rotation % 360 == 90
    assert r.pages[1].rotation % 360 == 0


def test_pdf_organizer_preserves_searchable_vector_text(synthetic_multipage_pdf: Path, temp_dir: Path):
    # Verify vector text is structurally retained and not rasterized into pixels
    out_pdf = temp_dir / "text_preserved.pdf"
    pages = [
        OrganizedPage(source_path=synthetic_multipage_pdf, page_index=0),
    ]
    res = export_organized_pdf(pages=pages, output_path=out_pdf)
    assert res.success is True

    # Check that text extraction still yields content from source
    r_orig = pypdf.PdfReader(synthetic_multipage_pdf)
    r_new = pypdf.PdfReader(out_pdf)
    orig_text = r_orig.pages[0].extract_text()
    new_text = r_new.pages[0].extract_text()
    assert orig_text == new_text


def test_split_pdf_by_ranges(synthetic_multipage_pdf: Path, temp_dir: Path):
    # Split into 2 files
    results = split_pdf_by_ranges(
        input_path=synthetic_multipage_pdf,
        output_dir=temp_dir,
        range_specs=["1", "2-3"],
    )
    assert len(results) == 2
    assert all(r.success for r in results)
    assert results[0].output_path.exists()
    assert results[1].output_path.exists()

    r1 = pypdf.PdfReader(results[0].output_path)
    r2 = pypdf.PdfReader(results[1].output_path)
    assert len(r1.pages) == 1
    assert len(r2.pages) == 2


def test_merge_multiple_pdfs(synthetic_multipage_pdf: Path, temp_dir: Path):
    out_merged = temp_dir / "merged_out.pdf"
    res = merge_multiple_pdfs(
        input_paths=[synthetic_multipage_pdf, synthetic_multipage_pdf],
        output_path=out_merged,
    )
    assert res.success is True
    assert out_merged.exists()

    r = pypdf.PdfReader(out_merged)
    # Original has 3 pages, merging 2 copies must yield 6 pages
    assert len(r.pages) == 6

