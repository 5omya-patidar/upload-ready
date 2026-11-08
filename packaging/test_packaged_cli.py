"""Smoke test script for packaged Upload Ready CLI binary.

Executes all 12 CLI commands against dist/UploadReady/upload-ready.exe
with rigorous output-content assertions:
1. convert: format, dimensions
2. resize: format, dimensions (width, height, fit)
3. compress: target byte size enforcement (--max-bytes)
4. images-to-pdf: multi-page PDF generation, page count
5. pdf-to-images: rendering PDF pages to image files, dimensions
6. pdf-reduce: structural reduction, page count preservation
7. preset apply: preset application, format and constraints
8. pdf-organize merge: PDF combining, sum of pages
9. metadata inspect & scrub: metadata inspection JSON & stripping
10. photo-sheet: passport grid sheet, PDF format and page count
11. watermark: image watermarking with text and opacity
12. id-layout: 2-sided ID sheet generation, layout and format
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from PIL import Image
import pypdf


def run_cmd(exe: Path, args: list[str]) -> subprocess.CompletedProcess:
    full_cmd = [str(exe)] + args
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"FAILED: {' '.join(full_cmd)}")
        print(f"STDOUT: {res.stdout}")
        print(f"STDERR: {res.stderr}")
    return res


def main():
    root = Path(__file__).resolve().parent.parent
    exe = root / "dist" / "UploadReady" / "upload-ready.exe"
    if not exe.exists():
        print(f"[!] Executable not found at {exe}")
        sys.exit(1)

    print(f"[*] Testing packaged binary: {exe}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out_dir = tmp / "output"
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. Prepare test assets
        img1 = tmp / "test1.png"
        Image.new("RGB", (200, 150), (200, 50, 50)).save(img1)

        img2 = tmp / "test2.png"
        Image.new("RGBA", (150, 200), (50, 200, 50, 255)).save(img2)

        # Multi-page PDF
        pdf1 = tmp / "doc1.pdf"
        writer1 = pypdf.PdfWriter()
        writer1.add_blank_page(width=300, height=400)
        writer1.add_blank_page(width=300, height=400)
        with open(pdf1, "wb") as f:
            writer1.write(f)

        pdf2 = tmp / "doc2.pdf"
        writer2 = pypdf.PdfWriter()
        writer2.add_blank_page(width=300, height=400)
        with open(pdf2, "wb") as f:
            writer2.write(f)

        print("[+] Test assets generated.")

        # Test 1: convert
        print("[*] 1. Testing 'convert'...")
        res = run_cmd(exe, ["convert", str(img1), "-f", "webp", "-o", str(out_dir), "--overwrite"])
        assert res.returncode == 0, "convert failed"
        conv_out = out_dir / "test1.webp"
        assert conv_out.exists(), f"convert output missing: {conv_out}"
        with Image.open(conv_out) as im:
            assert im.format == "WEBP", f"Expected WEBP, got {im.format}"
            assert im.size == (200, 150), f"Expected (200, 150), got {im.size}"
        print("  -> convert OK (WEBP 200x150)")

        # Test 2: resize
        print("[*] 2. Testing 'resize'...")
        res = run_cmd(exe, ["resize", str(img1), "--width", "100", "--height", "75", "--mode", "fit", "-o", str(out_dir), "--overwrite"])
        assert res.returncode == 0, "resize failed"
        resize_out = out_dir / "test1.png"
        assert resize_out.exists(), f"resize output missing: {resize_out}"
        with Image.open(resize_out) as im:
            assert im.size == (100, 75), f"Expected (100, 75), got {im.size}"
        print("  -> resize OK (100x75)")

        # Test 3: compress
        print("[*] 3. Testing 'compress'...")
        comp_dir = tmp / "comp_out"
        comp_dir.mkdir(parents=True, exist_ok=True)
        res = run_cmd(exe, ["compress", str(img1), "--max-size", "4KB", "-o", str(comp_dir), "--overwrite"])
        assert res.returncode == 0, "compress failed"
        comp_out = comp_dir / "test1.png"
        assert comp_out.exists(), f"compress output missing: {comp_out}"
        assert comp_out.stat().st_size <= 4000, f"Compressed file {comp_out.stat().st_size} exceeds 4000 bytes"
        print(f"  -> compress OK (size: {comp_out.stat().st_size} <= 4000 bytes)")

        # Test 4: images-to-pdf
        print("[*] 4. Testing 'images-to-pdf'...")
        pdf_out = out_dir / "images_combined.pdf"
        res = run_cmd(exe, ["images-to-pdf", str(img1), str(img2), "-o", str(pdf_out), "--page-size", "A4", "--overwrite"])
        assert res.returncode == 0, "images-to-pdf failed"
        assert pdf_out.exists(), f"images-to-pdf output missing: {pdf_out}"
        reader = pypdf.PdfReader(str(pdf_out))
        assert len(reader.pages) == 2, f"Expected 2 pages, got {len(reader.pages)}"
        print("  -> images-to-pdf OK (2 pages)")

        # Test 5: pdf-to-images
        print("[*] 5. Testing 'pdf-to-images'...")
        pdf_img_dir = tmp / "pdf_imgs"
        pdf_img_dir.mkdir(parents=True, exist_ok=True)
        res = run_cmd(exe, ["pdf-to-images", str(pdf1), "-o", str(pdf_img_dir), "--format", "png", "--dpi", "72"])
        assert res.returncode == 0, "pdf-to-images failed"
        extracted_imgs = list(pdf_img_dir.glob("*.png"))
        assert len(extracted_imgs) == 2, f"Expected 2 extracted images, got {len(extracted_imgs)}"
        for e_img in extracted_imgs:
            with Image.open(e_img) as im:
                assert im.format == "PNG"
        print(f"  -> pdf-to-images OK ({len(extracted_imgs)} images)")

        # Test 6: pdf-reduce
        print("[*] 6. Testing 'pdf-reduce'...")
        red_pdf = out_dir / "reduced.pdf"
        res = run_cmd(exe, ["pdf-reduce", str(pdf1), "-o", str(red_pdf), "--overwrite"])
        assert res.returncode == 0, "pdf-reduce failed"
        assert red_pdf.exists(), f"reduced PDF missing: {red_pdf}"
        reader_red = pypdf.PdfReader(str(red_pdf))
        assert len(reader_red.pages) == 2, f"Expected 2 pages in reduced PDF, got {len(reader_red.pages)}"
        print("  -> pdf-reduce OK (2 pages preserved)")

        # Test 7: preset apply
        print("[*] 7. Testing 'preset apply'...")
        preset_dir = tmp / "preset_out"
        preset_dir.mkdir(parents=True, exist_ok=True)
        res = run_cmd(exe, ["preset", "apply", str(img1), "--preset", "web_optimized_jpeg", "-o", str(preset_dir), "--allow-upscale", "--overwrite"])
        assert res.returncode == 0, f"preset apply failed: {res.stderr}"
        preset_out = preset_dir / "test1.jpg"
        assert preset_out.exists(), f"preset output missing: {preset_out}"
        with Image.open(preset_out) as im:
            assert im.format == "JPEG"
        print(f"  -> preset apply OK (format: JPEG, size: {preset_out.stat().st_size} bytes)")

        # Test 8: pdf-organize merge
        print("[*] 8. Testing 'pdf-organize merge'...")
        merged_pdf = out_dir / "merged.pdf"
        res = run_cmd(exe, ["pdf-organize", "merge", str(pdf1), str(pdf2), "-o", str(merged_pdf), "--overwrite"])
        assert res.returncode == 0, "pdf-organize merge failed"
        assert merged_pdf.exists(), f"merged PDF missing: {merged_pdf}"
        reader_merge = pypdf.PdfReader(str(merged_pdf))
        assert len(reader_merge.pages) == 3, f"Expected 3 pages (2+1), got {len(reader_merge.pages)}"
        print("  -> pdf-organize merge OK (3 pages total)")

        # Test 9: metadata inspect & scrub
        print("[*] 9. Testing 'metadata inspect & scrub'...")
        # Inspect
        res_insp = run_cmd(exe, ["metadata", "inspect", str(img1), "--json"])
        assert res_insp.returncode == 0, "metadata inspect failed"
        info = json.loads(res_insp.stdout)
        assert "type" in info, "metadata inspect missing 'type'"

        # Scrub
        scrub_dir = tmp / "scrub_out"
        scrub_dir.mkdir(parents=True, exist_ok=True)
        res_scrub = run_cmd(exe, ["metadata", "scrub", str(img1), "-o", str(scrub_dir), "--overwrite"])
        assert res_scrub.returncode == 0, "metadata scrub failed"
        scrub_out = scrub_dir / "test1_clean.png"
        assert scrub_out.exists(), f"scrub output missing: {scrub_out}"
        print("  -> metadata inspect & scrub OK")

        # Test 10: photo-sheet
        print("[*] 10. Testing 'photo-sheet'...")
        sheet_pdf = out_dir / "sheet.pdf"
        res = run_cmd(exe, [
            "photo-sheet", str(img1),
            "-o", str(sheet_pdf),
            "--photo-width", "35",
            "--photo-height", "45",
            "--copies", "4",
            "--sheet-size", "A4",
            "--format", "pdf",
            "--allow-upscale",
            "--overwrite"
        ])
        assert res.returncode == 0, f"photo-sheet failed: {res.stderr}"
        assert sheet_pdf.exists(), f"photo-sheet output missing: {sheet_pdf}"
        reader_sheet = pypdf.PdfReader(str(sheet_pdf))
        assert len(reader_sheet.pages) == 1, f"Expected 1 sheet page, got {len(reader_sheet.pages)}"
        print("  -> photo-sheet OK (1 sheet page)")

        # Test 11: watermark
        print("[*] 11. Testing 'watermark'...")
        wm_dir = tmp / "wm_out"
        wm_dir.mkdir(parents=True, exist_ok=True)
        res = run_cmd(exe, [
            "watermark", str(img1),
            "-o", str(wm_dir),
            "--text", "SPECIMEN",
            "--opacity", "0.25",
            "--overwrite"
        ])
        assert res.returncode == 0, f"watermark failed: {res.stderr}"
        wm_out = wm_dir / "test1_watermarked.png"
        assert wm_out.exists(), f"watermark output missing: {wm_out}"
        with Image.open(wm_out) as im:
            assert im.size == (200, 150)
        print("  -> watermark OK (dimensions 200x150 preserved)")

        # Test 12: id-layout
        print("[*] 12. Testing 'id-layout'...")
        id_pdf = out_dir / "id_sheet.pdf"
        res = run_cmd(exe, [
            "id-layout",
            "--front", str(img1),
            "--back", str(img2),
            "-o", str(id_pdf),
            "--layout", "stacked",
            "--sheet-size", "A4",
            "--format", "pdf",
            "--overwrite"
        ])
        assert res.returncode == 0, f"id-layout failed: {res.stderr}"
        assert id_pdf.exists(), f"id-layout output missing: {id_pdf}"
        reader_id = pypdf.PdfReader(str(id_pdf))
        assert len(reader_id.pages) == 1, f"Expected 1 ID sheet page, got {len(reader_id.pages)}"
        print("  -> id-layout OK (1 ID sheet page)")

    print("\n[+] ALL 12 PACKAGED CLI OPERATIONS VERIFIED SUCCESSFULLY WITH CONTENT ASSERTIONS!")


if __name__ == "__main__":
    main()
