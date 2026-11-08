"""PDF Page Organiser: reordering, rotating, duplicating, removing, splitting, and merging.

Provides structural operations that preserve searchable text, outlines, forms, and embedded
structures without rasterizing. Enforces password security, destination collision safety,
and digital signature modification warnings.
"""

import io
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from PIL import Image
import pypdf
import pypdfium2

from upload_ready.core.constants import MAX_DECODED_PIXELS, MAX_PDF_PAGES, bytes_to_human
from upload_ready.core.models import (
    CancellationToken,
    JobResult,
    OperationCancelledError,
    OperationType,
    RequirementCheck,
)
from upload_ready.core.pdf_ops import (
    InvalidPdfPasswordError,
    PdfPasswordRequiredError,
    detect_pdf_signatures,
    parse_page_range,
)
from upload_ready.core.safety import (
    ResourceLimitExceededError,
    SafetyValidationError,
    StagedOutput,
    resolve_collision_path,
    sanitize_filename,
    sniff_content_type,
    validate_destination_safety,
    validate_dimensions,
    validate_file_size,
    validate_pdf_page_count,
)


@dataclass
class OrganizedPage:
    """Descriptor for a single page arrangement in an organized document."""

    source_path: Path
    page_index: int  # 0-indexed in source PDF
    rotation_offset: int = 0  # Additional 0, 90, 180, 270 degrees
    source_label: str = ""  # Human-readable identifier e.g. "Doc A, Page 1"


def render_page_thumbnail(
    pdf_path: Path,
    page_index: int,
    max_dim: int = 200,
    password: Optional[str] = None,
) -> Image.Image:
    """
    Render a lightweight, bounded thumbnail for visual page arrangement in the UI.
    Guards against unbounded resolution by clamping render scale.
    """
    validate_file_size(pdf_path)
    try:
        doc = pypdfium2.PdfDocument(pdf_path, password=password)
    except Exception as exc:
        msg = str(exc).lower()
        if "password" in msg or "encrypted" in msg:
            raise PdfPasswordRequiredError("Document is password protected.")
        raise

    if page_index < 0 or page_index >= len(doc):
        raise IndexError(f"Page index {page_index} out of range for document with {len(doc)} pages.")

    page = doc.get_page(page_index)
    pw = page.get_width()
    ph = page.get_height()

    scale = min(max_dim / max(1.0, pw), max_dim / max(1.0, ph))
    pil_thumb = page.render(scale=scale).to_pil().convert("RGB")
    return pil_thumb


def _remap_outline_recursive(
    outline_items: List[Any],
    reader: pypdf.PdfReader,
    src_path: Path,
    pages: List[OrganizedPage],
    writer: pypdf.PdfWriter,
    current_parent: Optional[Any] = None,
    warnings: Optional[List[str]] = None,
) -> None:
    """
    Recursively preserve nested outline hierarchy and remap page destinations
    after page reordering, duplication, or extraction.
    Treatment for removed pages:
    - Omit the bookmark targeting the removed page and log an informative warning.
    - Promote any descendant bookmarks of the omitted parent to the parent's parent (or top-level).
    - If resolving the destination throws an unexpected error, record a descriptive warning.
    """
    idx = 0
    n = len(outline_items)
    while idx < n:
        item = outline_items[idx]
        if isinstance(item, pypdf.generic.Destination):
            children = []
            if idx + 1 < n and isinstance(outline_items[idx + 1], list):
                children = outline_items[idx + 1]
                idx += 1

            orig_page_num = None
            try:
                orig_page_num = reader.get_destination_page_number(item)
            except Exception as exc:
                if warnings is not None:
                    warnings.append(
                        f"Could not resolve destination for bookmark '{getattr(item, 'title', 'unknown')}' in '{src_path.name}': {exc}"
                    )

            if orig_page_num is not None:
                new_indices = [
                    i for i, p_desc in enumerate(pages)
                    if p_desc.source_path.resolve() == src_path.resolve() and p_desc.page_index == orig_page_num
                ]
                if new_indices:
                    new_item = writer.add_outline_item(
                        title=str(item.title),
                        page_number=new_indices[0],
                        parent=current_parent,
                    )
                    if children:
                        _remap_outline_recursive(
                            children,
                            reader,
                            src_path,
                            pages,
                            writer,
                            current_parent=new_item,
                            warnings=warnings,
                        )
                else:
                    if warnings is not None:
                        warnings.append(
                            f"Bookmark '{item.title}' in '{src_path.name}' points to removed page {orig_page_num + 1}; omitted."
                        )
                    if children:
                        _remap_outline_recursive(
                            children,
                            reader,
                            src_path,
                            pages,
                            writer,
                            current_parent=current_parent,
                            warnings=warnings,
                        )
            else:
                if children:
                    _remap_outline_recursive(
                        children,
                        reader,
                        src_path,
                        pages,
                        writer,
                        current_parent=current_parent,
                        warnings=warnings,
                    )
        elif isinstance(item, list):
            _remap_outline_recursive(
                item,
                reader,
                src_path,
                pages,
                writer,
                current_parent=current_parent,
                warnings=warnings,
            )
        else:
            title = getattr(item, "title", str(item))
            if warnings is not None:
                warnings.append(f"Unrecognized outline item type {type(item)} for '{title}' in '{src_path.name}'.")

        idx += 1


def export_organized_pdf(
    pages: List[OrganizedPage],
    output_path: Path,
    passwords: Optional[Dict[str, str]] = None,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Assemble an organized list of pages from one or more source PDFs into a new document.
    Preserves searchable vector text, annotations, forms, and outline items structurally.
    """
    result = JobResult(
        operation=OperationType.PDF_REDUCE,  # PDF structural operation
        input_path=pages[0].source_path if pages else Path("empty"),
    )

    if not pages:
        result.error_message = "No pages provided to export."
        return result

    validate_pdf_page_count(len(pages), max_pages=MAX_PDF_PAGES)

    passwords_map = passwords or {}
    all_source_paths = list({p.source_path.resolve() for p in pages})
    protected_inputs = list(dict.fromkeys(all_source_paths + (list(batch_inputs) if batch_inputs is not None else [])))

    dest_path = resolve_collision_path(output_path, allow_overwrite=allow_overwrite)

    try:
        # Strictly prohibit overwriting any source document
        validate_destination_safety(dest_path, protected_inputs)

        readers_cache: Dict[Path, pypdf.PdfReader] = {}
        for src in all_source_paths:
            validate_file_size(src)
            detected = sniff_content_type(src)
            if detected != "PDF":
                raise ValueError(f"Source file {src.name} is not a valid PDF.")

            r = pypdf.PdfReader(src)
            if r.is_encrypted:
                pw = passwords_map.get(str(src)) or passwords_map.get(src.name)
                if not pw:
                    raise PdfPasswordRequiredError(f"Document '{src.name}' is password protected.")
                if r.decrypt(pw) == 0:
                    raise InvalidPdfPasswordError(f"Invalid decryption password for '{src.name}'.")

            # Signature check
            is_signed, _ = detect_pdf_signatures(r)
            if is_signed:
                result.warnings.append(
                    f"Digital signature detected in '{src.name}': Rearranging or exporting pages invalidates cryptographic signatures."
                )

            readers_cache[src] = r

        writer = pypdf.PdfWriter()

        for idx, page_desc in enumerate(pages):
            if cancel_token and cancel_token.is_cancelled:
                raise OperationCancelledError("PDF export cancelled by user.")

            src_reader = readers_cache[page_desc.source_path.resolve()]
            if page_desc.page_index < 0 or page_desc.page_index >= len(src_reader.pages):
                raise IndexError(
                    f"Page index {page_desc.page_index + 1} exceeds page count of {page_desc.source_path.name}."
                )

            # Append the page into the writer; creates an isolated copy
            writer.append(src_reader, pages=[page_desc.page_index], import_outline=False)
            added_page = writer.pages[-1]

            # Apply incremental rotation to the appended page only (does not mutate reader page)
            rot = page_desc.rotation_offset % 360
            if rot != 0:
                added_page.rotate(rot)

        # Preserve attachments from all involved source documents
        for src_path, r in readers_cache.items():
            if hasattr(r, "attachments") and r.attachments:
                for att_name, att_data_list in r.attachments.items():
                    for att_data in att_data_list:
                        try:
                            writer.add_attachment(f"{src_path.stem}_{att_name}", att_data)
                        except Exception:
                            pass

        # Remap and preserve bookmarks (outlines) recursively
        for src_path, r in readers_cache.items():
            if hasattr(r, "outline") and r.outline:
                _remap_outline_recursive(
                    r.outline,
                    r,
                    src_path,
                    pages,
                    writer,
                    current_parent=None,
                    warnings=result.warnings,
                )

        # Detect dynamic XFA limitations
        for src_path, r in readers_cache.items():
            try:
                if "/Root" in r.trailer and "/AcroForm" in r.trailer["/Root"]:
                    acro = r.trailer["/Root"]["/AcroForm"]
                    if isinstance(acro, dict) and "/XFA" in acro:
                        result.warnings.append(
                            f"Document '{src_path.name}' contains dynamic XFA forms: Dynamic datasets cannot be maintained across page reorganization; static appearances are preserved."
                        )
            except Exception:
                pass

        if cancel_token and cancel_token.is_cancelled:
            raise OperationCancelledError("PDF export cancelled before writing output.")

        staged = StagedOutput(dest_path, cancel_token=cancel_token)
        with staged as staged_path:
            with open(staged_path, "wb") as f_out:
                writer.write(f_out)
        staged.commit()

        result.output_path = dest_path
        result.output_size_bytes = dest_path.stat().st_size
        result.success = True
        result.checks.append(
            RequirementCheck(
                name="PDF Page Organization",
                target=f"{len(pages)} pages",
                actual=f"{len(writer.pages)} pages assembled",
                passed=True,
                details=f"Assembled from {len(all_source_paths)} source documents",
            )
        )

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)

    return result


def split_pdf_by_ranges(
    input_path: Path,
    output_dir: Path,
    range_specs: List[str],
    password: Optional[str] = None,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> List[JobResult]:
    """
    Split a source PDF into multiple sub-documents according to page ranges (e.g. ['1-3', '4-5', '6']).
    """
    results: List[JobResult] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    protected = list(dict.fromkeys([input_path] + (list(batch_inputs) if batch_inputs is not None else [])))

    r = pypdf.PdfReader(input_path)
    if r.is_encrypted:
        if not password:
            raise PdfPasswordRequiredError("Document is password protected.")
        if r.decrypt(password) == 0:
            raise InvalidPdfPasswordError("Invalid password.")

    total_pages = len(r.pages)

    for idx, r_spec in enumerate(range_specs):
        if cancel_token and cancel_token.is_cancelled:
            break

        indices = parse_page_range(r_spec, total_pages)
        if not indices:
            continue

        pages = [
            OrganizedPage(
                source_path=input_path,
                page_index=i,
                source_label=f"Page {i + 1}",
            )
            for i in indices
        ]

        out_name = f"{input_path.stem}_part{idx + 1}_{r_spec.replace(' ', '').replace(',', '_')}.pdf"
        out_file = output_dir / out_name

        res = export_organized_pdf(
            pages=pages,
            output_path=out_file,
            passwords={str(input_path.resolve()): password or ""},
            allow_overwrite=allow_overwrite,
            cancel_token=cancel_token,
            batch_inputs=protected,
        )
        results.append(res)

    return results


def merge_multiple_pdfs(
    input_paths: List[Path],
    output_path: Path,
    passwords: Optional[Dict[str, str]] = None,
    allow_overwrite: bool = False,
    cancel_token: Optional[CancellationToken] = None,
    batch_inputs: Optional[Iterable[Path]] = None,
) -> JobResult:
    """
    Merge a sequence of PDF files in specified order into a single combined document.
    """
    pages: List[OrganizedPage] = []
    passwords_map = passwords or {}

    for src in input_paths:
        r = pypdf.PdfReader(src)
        if r.is_encrypted:
            pw = passwords_map.get(str(src)) or passwords_map.get(src.name)
            if not pw:
                raise PdfPasswordRequiredError(f"Document '{src.name}' is password protected.")
            if r.decrypt(pw) == 0:
                raise InvalidPdfPasswordError(f"Invalid decryption password for '{src.name}'.")

        for i in range(len(r.pages)):
            pages.append(
                OrganizedPage(
                    source_path=src,
                    page_index=i,
                    source_label=f"{src.name} p.{i+1}",
                )
            )

    return export_organized_pdf(
        pages=pages,
        output_path=output_path,
        passwords=passwords,
        allow_overwrite=allow_overwrite,
        cancel_token=cancel_token,
        batch_inputs=batch_inputs,
    )

