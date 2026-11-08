"""Command-Line Interface (CLI) for Upload Ready.

Reuses the exact same core processing engines and models as the desktop application.
Provides deterministic exits codes, validation reports, and optional JSON output.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from upload_ready.core.constants import (
    UnitStandard,
    bytes_to_human,
    parse_size_to_bytes,
)
from upload_ready.core.image_compress import compress_image
from upload_ready.core.image_convert import convert_image
from upload_ready.core.image_resize import resize_image
from upload_ready.core.models import (
    CompressOptions,
    ConvertOptions,
    ImagesToPdfOptions,
    JobResult,
    PageOrientation,
    PdfReduceOptions,
    PdfToImagesOptions,
    ResizeMode,
    ResizeOptions,
)
import pypdf

from upload_ready.core.pdf_ops import (
    images_to_pdf,
    parse_page_range,
    pdf_to_images,
    reduce_pdf_size,
)
from upload_ready.core.id_layout import IdLayoutOptions, generate_id_layout
from upload_ready.core.metadata import inspect_metadata, scrub_metadata
from upload_ready.core.photo_sheet import PhotoSheetOptions, generate_photo_sheet
from upload_ready.core.presets import PresetManager, RequirementPreset, apply_preset_to_image
from upload_ready.core.pdf_organizer import (
    OrganizedPage,
    export_organized_pdf,
    merge_multiple_pdfs,
)
from upload_ready.core.watermark import (
    WatermarkOptions,
    apply_watermark_to_image,
    apply_watermark_to_pdf,
)
from upload_ready.core.safety import TempJobWorkspace


def parse_hex_color(hex_str: str) -> tuple[int, int, int]:
    """Parse #RRGGBB or RRGGBB string into RGB integer tuple."""
    clean = hex_str.strip().lstrip("#")
    if len(clean) != 6:
        raise ValueError(f"Invalid hex color format: '{hex_str}'. Expected 6-character hex like #FFFFFF.")
    return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))


def print_result_report(result: JobResult, json_output: bool = False) -> None:
    """Print structured job result report."""
    if json_output:
        data = {
            "operation": result.operation.value,
            "success": result.success,
            "input_file": result.input_path.name,
            "output_file": result.output_path.name if result.output_path else None,
            "input_size_bytes": result.input_size_bytes,
            "output_size_bytes": result.output_size_bytes,
            "input_dimensions": result.input_dimensions,
            "output_dimensions": result.output_dimensions,
            "warnings": result.warnings,
            "error": result.error_message,
            "checks": [
                {
                    "name": c.name,
                    "target": c.target,
                    "actual": c.actual,
                    "passed": c.passed,
                    "details": c.details,
                }
                for c in result.checks
            ],
        }
        print(json.dumps(data, indent=2))
        return

    status = "SUCCESS" if result.success else "FAILED"
    print(f"\n[{status}] {result.operation.value.upper()}: {result.input_path.name}")
    if result.output_path:
        print(f"  -> Saved: {result.output_path}")
        print(f"  -> Size: {bytes_to_human(result.input_size_bytes)} -> {bytes_to_human(result.output_size_bytes)}")
        if result.input_dimensions and result.output_dimensions:
            print(f"  -> Dims: {result.input_dimensions[0]}x{result.input_dimensions[1]} -> {result.output_dimensions[0]}x{result.output_dimensions[1]}")

    for w in result.warnings:
        print(f"  [WARN] {w}")

    for c in result.checks:
        c_status = "PASS" if c.passed else "FAIL"
        print(f"  [{c_status}] {c.name}: {c.actual} (target: {c.target})")
        if c.details:
            print(f"         {c.details}")

    if result.error_message:
        print(f"  [ERROR] {result.error_message}")


def run_convert(args: argparse.Namespace) -> int:
    out_dir = Path(args.output)
    bg_color = parse_hex_color(args.bg_color)
    fmt_raw = args.format.upper().strip()
    target_format = "JPEG" if fmt_raw in ("JPEG", "JPG") else fmt_raw
    options = ConvertOptions(
        target_format=target_format,
        bg_color=bg_color,
        quality=args.quality,
        lossless=args.lossless,
        allow_first_frame_only=args.allow_first_frame_only,
    )

    exit_code = 0
    all_inputs = [Path(f) for f in args.inputs]
    for input_file in args.inputs:
        path = Path(input_file)
        res = convert_image(path, out_dir, options, allow_overwrite=args.overwrite, batch_inputs=all_inputs)
        print_result_report(res, args.json)
        if not res.success:
            exit_code = 1
    return exit_code


def run_resize(args: argparse.Namespace) -> int:
    out_dir = Path(args.output)
    bg_color = parse_hex_color(args.bg_color)
    mode = ResizeMode(args.mode.lower())
    options = ResizeOptions(
        width=args.width,
        height=args.height,
        aspect_ratio_lock=not args.free_aspect,
        mode=mode,
        bg_color=bg_color,
        dpi=(args.dpi, args.dpi) if args.dpi else None,
        allow_upscale=args.allow_upscale,
    )

    exit_code = 0
    all_inputs = [Path(f) for f in args.inputs]
    for input_file in args.inputs:
        path = Path(input_file)
        res = resize_image(path, out_dir, options, allow_overwrite=args.overwrite, batch_inputs=all_inputs)
        print_result_report(res, args.json)
        if not res.success:
            exit_code = 1
    return exit_code


def run_compress(args: argparse.Namespace) -> int:
    out_dir = Path(args.output)
    unit_std = UnitStandard(args.unit.lower())
    
    max_b = parse_size_to_bytes(args.max_size, unit_std) if args.max_size else None
    min_b = parse_size_to_bytes(args.min_size, unit_std) if args.min_size else None

    options = CompressOptions(
        is_lossless=args.lossless,
        quality=args.quality,
        max_bytes=max_b,
        min_bytes=min_b,
        unit_standard=unit_std,
        allow_palette_quantization=args.allow_palette_quantization,
    )

    exit_code = 0
    all_inputs = [Path(f) for f in args.inputs]
    for input_file in args.inputs:
        path = Path(input_file)
        res = compress_image(path, out_dir, options, allow_overwrite=args.overwrite, batch_inputs=all_inputs)
        print_result_report(res, args.json)
        if not res.success:
            exit_code = 1
    return exit_code


def run_images_to_pdf(args: argparse.Namespace) -> int:
    out_path = Path(args.output)
    input_paths = [Path(p) for p in args.inputs]
    options = ImagesToPdfOptions(
        page_size=args.page_size.upper(),
        orientation=PageOrientation(args.orientation.lower()),
        margin_pt=args.margin,
    )
    res = images_to_pdf(input_paths, out_path, options, allow_overwrite=args.overwrite)
    print_result_report(res, args.json)
    return 0 if res.success else 1


def run_pdf_to_images(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    out_dir = Path(args.output)
    options = PdfToImagesOptions(
        page_range=args.pages,
        target_format=args.format.upper(),
        dpi=args.dpi,
        password=args.password,
        as_zip=args.zip,
    )
    res = pdf_to_images(input_path, out_dir, options, allow_overwrite=args.overwrite)
    print_result_report(res, args.json)
    return 0 if res.success else 1


def run_pdf_reduce(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    out_path = Path(args.output)
    options = PdfReduceOptions(
        structural_only=not args.downsample,
        downsample_scanned=args.downsample,
        target_dpi=args.dpi,
        image_quality=args.quality,
        remove_metadata=not args.keep_metadata,
    )
    res = reduce_pdf_size(input_path, out_path, options, password=args.password, allow_overwrite=args.overwrite)
    print_result_report(res, args.json)
    return 0 if res.success else 1


def run_preset(args: argparse.Namespace) -> int:
    mgr = PresetManager()
    action = args.preset_action
    if action == "list":
        presets = mgr.list_presets()
        if args.json:
            out = [{"id": p.id, "name": p.name, "description": p.description} for p in presets]
            print(json.dumps(out, indent=2))
        else:
            print("Available Presets:")
            for p in presets:
                print(f"  - {p.id}: {p.name} ({p.description})")
        return 0
    elif action == "show":
        p = mgr.get_preset(args.name)
        if not p:
            print(f"[ERROR] Preset '{args.name}' not found.", file=sys.stderr)
            return 1
        print(f"Preset: {p.name} ({p.id})")
        print(f"  Description: {p.description}")
        print(f"  Format: {p.target_format}")
        print(f"  Dimensions: {p.width}x{p.height} ({p.resize_mode})")
        print(f"  Max Size: {p.max_bytes} bytes")
        return 0
    elif action == "apply":
        preset = mgr.get_preset(args.preset)
        if not preset:
            print(f"[ERROR] Preset '{args.preset}' not found.", file=sys.stderr)
            return 1
        input_path = Path(args.input)
        out_dir = Path(args.output)
        res = apply_preset_to_image(
            input_path=input_path,
            output_dir=out_dir,
            preset=preset,
            allow_upscale=args.allow_upscale,
            allow_overwrite=args.overwrite,
        )
        print_result_report(res, args.json)
        return 0 if res.success else 1
    return 1


def run_pdf_organize(args: argparse.Namespace) -> int:
    action = args.organize_action
    if action == "merge":
        inputs = [Path(p) for p in args.inputs]
        out_path = Path(args.output)
        res = merge_multiple_pdfs(inputs, out_path, allow_overwrite=args.overwrite)
        print_result_report(res, args.json)
        return 0 if res.success else 1
    elif action == "rotate":
        input_path = Path(args.input)
        out_path = Path(args.output)
        reader = pypdf.PdfReader(input_path)
        total = len(reader.pages)
        indices = set(parse_page_range(args.pages, total)) if args.pages != "all" else set(range(total))
        pages = []
        for i in range(total):
            rot = args.angle if i in indices else 0
            pages.append(OrganizedPage(source_path=input_path, page_index=i, rotation_offset=rot))
        res = export_organized_pdf(
            pages=pages,
            output_path=out_path,
            allow_overwrite=args.overwrite,
        )
        print_result_report(res, args.json)
        return 0 if res.success else 1
    return 1


def run_metadata(args: argparse.Namespace) -> int:
    action = args.meta_action
    if action == "inspect":
        input_path = Path(args.input)
        meta = inspect_metadata(input_path)
        if args.json:
            out = {
                "file": str(meta.file_path),
                "type": meta.file_type,
                "has_exif": meta.has_exif,
                "has_gps": meta.has_gps,
                "has_xmp": meta.has_xmp,
                "has_comments": meta.has_comments,
                "has_icc_profile": meta.has_icc_profile,
                "icc_profile_desc": meta.icc_profile_desc,
                "detected_tags": meta.detected_tags,
                "doc_info_keys": meta.doc_info_keys,
            }
            print(json.dumps(out, indent=2))
        else:
            print(f"Metadata for {input_path.name} ({meta.file_type}):")
            print(f"  EXIF: {meta.has_exif} (tags: {meta.exif_tag_count})")
            print(f"  GPS: {meta.has_gps}")
            print(f"  XMP: {meta.has_xmp}")
            print(f"  Comments: {meta.has_comments}")
            print(f"  ICC Profile: {meta.has_icc_profile} ({meta.icc_profile_desc or 'None'})")
            if meta.detected_tags:
                print(f"  Detected Tags: {', '.join(meta.detected_tags)}")
            if meta.doc_info_keys:
                print(f"  DocInfo Keys: {', '.join(meta.doc_info_keys)}")
        return 0
    elif action == "scrub":
        input_path = Path(args.input)
        out_dir = Path(args.output)
        cats = set(args.categories.split(",")) if hasattr(args, "categories") and args.categories else {"all"}
        res = scrub_metadata(input_path, out_dir, categories=cats, allow_overwrite=args.overwrite)
        print_result_report(res, args.json)
        return 0 if res.success else 1
    return 1


def run_photo_sheet(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    out_path = Path(args.output)
    options = PhotoSheetOptions(
        photo_width_mm=args.photo_width,
        photo_height_mm=args.photo_height,
        copies=args.copies,
        sheet_size=args.sheet_size.upper(),
        output_format=args.format.upper(),
        cut_guides=not args.no_cut_guides,
        allow_upscale=args.allow_upscale,
    )
    res = generate_photo_sheet(input_path, out_path, options, allow_overwrite=args.overwrite)
    print_result_report(res, args.json)
    return 0 if res.success else 1


def run_watermark(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    dest = Path(args.output)
    options = WatermarkOptions(
        text=args.text,
        opacity=args.opacity,
        angle=args.angle,
        font_size=args.font_size,
        color=parse_hex_color(args.color) if hasattr(args, "color") and args.color else (180, 0, 0),
        page_range=args.pages if hasattr(args, "pages") else "all",
    )
    is_pdf = input_path.suffix.lower() == ".pdf"
    if dest.is_dir() or not dest.suffix:
        ext = ".pdf" if is_pdf else input_path.suffix.lower()
        target_path = dest / f"{input_path.stem}_watermarked{ext}"
    else:
        target_path = dest

    if is_pdf:
        res = apply_watermark_to_pdf(input_path, target_path, options, allow_overwrite=args.overwrite)
    else:
        res = apply_watermark_to_image(input_path, target_path, options, allow_overwrite=args.overwrite)
    print_result_report(res, args.json)
    return 0 if res.success else 1


def run_id_layout(args: argparse.Namespace) -> int:
    front_path = Path(args.front)
    back_path = Path(args.back)
    out_path = Path(args.output)
    options = IdLayoutOptions(
        layout_type=args.layout.lower(),
        sheet_size=args.sheet_size.upper(),
        output_format=args.format.upper(),
    )
    res = generate_id_layout(front_path, back_path, out_path, options, allow_overwrite=args.overwrite)
    print_result_report(res, args.json)
    return 0 if res.success else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="upload-ready",
        description="Upload Ready - Local-only image & PDF preparation tool.",
    )
    
    # Common flags shared across main parser and all subcommands
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Format output as JSON")
    common.add_argument("--overwrite", action="store_true", help="Allow overwriting target file if it already exists")

    parser.add_argument("--json", action="store_true", help="Format output as JSON")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting target file if it already exists")

    subparsers = parser.add_subparsers(dest="subcommand", help="Operation to perform")

    # convert
    p_conv = subparsers.add_parser("convert", parents=[common], help="Convert images between JPEG, PNG, and WebP")
    p_conv.add_argument("inputs", nargs="+", help="Input image files")
    p_conv.add_argument("-f", "--format", required=True, choices=["jpg", "jpeg", "png", "webp"], help="Target format")
    p_conv.add_argument("-o", "--output", required=True, help="Destination directory")
    p_conv.add_argument("--bg-color", default="#FFFFFF", help="Background color for transparent to JPEG (#RRGGBB)")
    p_conv.add_argument("-q", "--quality", type=int, default=85, help="JPEG/WebP encoding quality (1-100)")
    p_conv.add_argument("--lossless", action="store_true", help="Lossless encoding (PNG/WebP)")
    p_conv.add_argument("--allow-first-frame-only", action="store_true", help="Authorize extracting only the first frame on multi-frame/animated inputs")

    # resize
    p_res = subparsers.add_parser("resize", parents=[common], help="Resize image dimensions")
    p_res.add_argument("inputs", nargs="+", help="Input image files")
    p_res.add_argument("-o", "--output", required=True, help="Destination directory")
    p_res.add_argument("-W", "--width", type=int, help="Target width in pixels")
    p_res.add_argument("-H", "--height", type=int, help="Target height in pixels")
    p_res.add_argument("--mode", choices=["fit", "crop", "pad"], default="fit", help="Resize mode")
    p_res.add_argument("--free-aspect", action="store_true", help="Unlock aspect ratio")
    p_res.add_argument("--allow-upscale", action="store_true", help="Explicitly authorize upscaling")
    p_res.add_argument("--dpi", type=int, help="Set DPI metadata")
    p_res.add_argument("--bg-color", default="#FFFFFF", help="Padding background color (#RRGGBB)")

    # compress
    p_comp = subparsers.add_parser("compress", parents=[common], help="Compress images or target specific file sizes")
    p_comp.add_argument("inputs", nargs="+", help="Input image files")
    p_comp.add_argument("-o", "--output", required=True, help="Destination directory")
    p_comp.add_argument("--lossless", action="store_true", help="Lossless optimization (zero pixel delta)")
    p_comp.add_argument("-q", "--quality", type=int, help="Manual lossy quality setting (1-100)")
    p_comp.add_argument("--max-size", help="Target maximum size (e.g. '50KB', '1.5MB')")
    p_comp.add_argument("--min-size", help="Target minimum size for range (e.g. '20KB')")
    p_comp.add_argument("--unit", choices=["decimal", "binary"], default="decimal", help="Unit standard")
    p_comp.add_argument("--allow-palette-quantization", action="store_true", help="Authorize lossy palette color reduction on PNG target sizing")

    # images-to-pdf
    p_i2p = subparsers.add_parser("images-to-pdf", parents=[common], help="Assemble images into a single PDF")
    p_i2p.add_argument("inputs", nargs="+", help="Input image files in page order")
    p_i2p.add_argument("-o", "--output", required=True, help="Destination PDF file path")
    p_i2p.add_argument("--page-size", default="A4", choices=["A4", "Letter", "Legal", "IMAGE_SIZE"], help="PDF page size")
    p_i2p.add_argument("--orientation", default="auto", choices=["auto", "portrait", "landscape"], help="Orientation")
    p_i2p.add_argument("--margin", type=float, default=0.0, help="Page margin in points (72 pt = 1 inch)")

    # pdf-to-images
    p_p2i = subparsers.add_parser("pdf-to-images", parents=[common], help="Render PDF pages to images")
    p_p2i.add_argument("input", help="Input PDF file")
    p_p2i.add_argument("-o", "--output", required=True, help="Destination directory")
    p_p2i.add_argument("--pages", default="all", help="Page selection ('all' or '1, 3-5')")
    p_p2i.add_argument("--format", default="PNG", choices=["png", "jpeg"], help="Output format")
    p_p2i.add_argument("--dpi", type=int, default=150, help="Rendering DPI (default: 150)")
    p_p2i.add_argument("--password", help="PDF decryption password (not stored)")
    p_p2i.add_argument("--zip", action="store_true", help="Bundle rendered images into a ZIP archive")

    # pdf-reduce
    p_red = subparsers.add_parser("pdf-reduce", parents=[common], help="Reduce PDF file size")
    p_red.add_argument("input", help="Input PDF file")
    p_red.add_argument("-o", "--output", required=True, help="Destination PDF file path")
    p_red.add_argument("--downsample", action="store_true", help="Rasterize and downsample pages (for scanned PDFs)")
    p_red.add_argument("--dpi", type=int, default=150, help="Downsampling DPI")
    p_red.add_argument("-q", "--quality", type=int, default=75, help="JPEG quality for downsampled images")
    p_red.add_argument("--keep-metadata", action="store_true", help="Preserve PDF metadata")
    p_red.add_argument("--password", help="Decryption password if encrypted")

    # preset
    p_preset = subparsers.add_parser("preset", parents=[common], help="Manage and apply presets")
    preset_sub = p_preset.add_subparsers(dest="preset_action", required=True)
    p_pr_list = preset_sub.add_parser("list", parents=[common], help="List available presets")
    p_pr_show = preset_sub.add_parser("show", parents=[common], help="Show preset details")
    p_pr_show.add_argument("name", help="Preset ID or name")
    p_pr_apply = preset_sub.add_parser("apply", parents=[common], help="Apply preset to input file")
    p_pr_apply.add_argument("input", help="Input file path")
    p_pr_apply.add_argument("--preset", required=True, help="Preset ID")
    p_pr_apply.add_argument("-o", "--output", required=True, help="Destination directory")
    p_pr_apply.add_argument("--allow-upscale", action="store_true", help="Authorize upscaling if input is smaller than preset")

    # pdf-organize
    p_org = subparsers.add_parser("pdf-organize", parents=[common], help="Merge, rotate, and organize PDF pages")
    org_sub = p_org.add_subparsers(dest="organize_action", required=True)
    p_org_merge = org_sub.add_parser("merge", parents=[common], help="Merge multiple PDFs")
    p_org_merge.add_argument("inputs", nargs="+", help="Input PDF files in merge order")
    p_org_merge.add_argument("-o", "--output", required=True, help="Destination merged PDF")
    p_org_rotate = org_sub.add_parser("rotate", parents=[common], help="Rotate pages in PDF")
    p_org_rotate.add_argument("input", help="Input PDF file")
    p_org_rotate.add_argument("-o", "--output", required=True, help="Destination PDF file")
    p_org_rotate.add_argument("--angle", type=int, choices=[90, 180, 270], default=90, help="Clockwise rotation angle")
    p_org_rotate.add_argument("--pages", default="all", help="Pages to rotate (e.g. 'all', '1, 3-5')")

    # metadata
    p_meta = subparsers.add_parser("metadata", parents=[common], help="Inspect or scrub metadata")
    meta_sub = p_meta.add_subparsers(dest="meta_action", required=True)
    p_meta_insp = meta_sub.add_parser("inspect", parents=[common], help="Inspect metadata")
    p_meta_insp.add_argument("input", help="Input file path")
    p_meta_scrub = meta_sub.add_parser("scrub", parents=[common], help="Scrub metadata")
    p_meta_scrub.add_argument("input", help="Input file path")
    p_meta_scrub.add_argument("-o", "--output", required=True, help="Destination directory")
    p_meta_scrub.add_argument("--categories", default="all", help="Categories to scrub comma-separated")

    # photo-sheet
    p_photo = subparsers.add_parser("photo-sheet", parents=[common], help="Generate passport-photo / signature sheet")
    p_photo.add_argument("input", help="Input photo/signature file")
    p_photo.add_argument("-o", "--output", required=True, help="Destination file (.pdf, .jpg, .png)")
    p_photo.add_argument("--photo-width", type=float, default=35.0, help="Photo width in mm")
    p_photo.add_argument("--photo-height", type=float, default=45.0, help="Photo height in mm")
    p_photo.add_argument("--copies", type=int, default=8, help="Number of copies")
    p_photo.add_argument("--sheet-size", default="A4", choices=["A4", "Letter", "4X6"], help="Sheet paper size")
    p_photo.add_argument("--format", default="pdf", choices=["pdf", "jpg", "jpeg", "png"], help="Output format")
    p_photo.add_argument("--allow-upscale", action="store_true", help="Authorize upscaling small images")
    p_photo.add_argument("--no-cut-guides", action="store_true", help="Disable corner cut guides")

    # watermark
    p_wm = subparsers.add_parser("watermark", parents=[common], help="Apply purpose watermark")
    p_wm.add_argument("input", help="Input image or PDF file")
    p_wm.add_argument("-o", "--output", required=True, help="Destination file or directory")
    p_wm.add_argument("--text", required=True, help="Watermark text")
    p_wm.add_argument("--opacity", type=float, default=0.3, help="Opacity between 0.0 and 1.0")
    p_wm.add_argument("--angle", type=int, default=-30, help="Rotation angle in degrees")
    p_wm.add_argument("--font-size", type=int, default=36, help="Font size in points")
    p_wm.add_argument("--color", default="#B40000", help="Hex color string (#RRGGBB)")
    p_wm.add_argument("--pages", default="all", help="PDF page range (e.g. 'all', '1, 3-5')")

    # id-layout
    p_id = subparsers.add_parser("id-layout", parents=[common], help="Generate ID card 2-sided sheet")
    p_id.add_argument("--front", required=True, help="Front card image path")
    p_id.add_argument("--back", required=True, help="Back card image path")
    p_id.add_argument("-o", "--output", required=True, help="Destination file (.pdf or .png)")
    p_id.add_argument("--layout", default="stacked", choices=["stacked", "side_by_side"], help="Layout arrangement")
    p_id.add_argument("--sheet-size", default="A4", choices=["A4", "Letter", "4X6"], help="Sheet paper size")
    p_id.add_argument("--format", default="pdf", choices=["pdf", "png", "jpg"], help="Output format")

    args = parser.parse_args(argv)

    # Clean any old orphaned workspaces on startup
    TempJobWorkspace.clean_orphaned_workspaces()

    if not args.subcommand:
        parser.print_help()
        return 1

    try:
        if args.subcommand == "convert":
            return run_convert(args)
        elif args.subcommand == "resize":
            return run_resize(args)
        elif args.subcommand == "compress":
            return run_compress(args)
        elif args.subcommand == "images-to-pdf":
            return run_images_to_pdf(args)
        elif args.subcommand == "pdf-to-images":
            return run_pdf_to_images(args)
        elif args.subcommand == "pdf-reduce":
            return run_pdf_reduce(args)
        elif args.subcommand == "preset":
            return run_preset(args)
        elif args.subcommand == "pdf-organize":
            return run_pdf_organize(args)
        elif args.subcommand == "metadata":
            return run_metadata(args)
        elif args.subcommand == "photo-sheet":
            return run_photo_sheet(args)
        elif args.subcommand == "watermark":
            return run_watermark(args)
        elif args.subcommand == "id-layout":
            return run_id_layout(args)
        else:
            parser.print_help()
            return 1
    except Exception as exc:
        print(f"[FATAL] Operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
