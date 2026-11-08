# Upload Ready

> **Local-only, privacy-first desktop application for converting, resizing, compressing, and preparing images and PDFs.**

Upload Ready runs 100% offline on your computer. It performs all operations locally in memory and safe temporary workspaces without uploading your files, communicating with external servers, running background analytics, or checking for updates.

---

## Key Features

- **Image Format Conversion:** Convert between JPEG, PNG, and WebP. Automatically respects EXIF orientation flags. Preserves alpha channels when converting between PNG and WebP, and provides customizable solid background compositing when flattening transparent images to JPEG.
- **Multiple Images to PDF:** Reorder, rotate (90°, 180°, 270°), and combine images into single multi-page PDF documents. Choose from A4, Letter, Legal, or natural image-dimension pages, with configurable margins and proportion-preserving centering.
- **PDF to Images:** Faithfully rasterize multi-page PDFs to PNG or JPEG at customizable DPI (72, 150, 200, 300+). Select page ranges (`all`, `1, 3-5, 8`), decrypt password-protected PDFs locally in memory, and export as separate files or bundled into a ZIP archive.
- **Resizing & Dimensions:** Resample pixels using high-quality Lanczos interpolation. Choose between **Fit** (contain proportionally), **Crop** (fill and center-crop), or **Pad** (fit inside padded background canvas). Guard against accidental enlargement with deliberate upscaling authorization. Set DPI metadata headers without resampling pixels.
- **Compression & Target File Size:**
  - *Lossless Optimization:* Verified 0-pixel delta difference check; strips metadata and optimizes compression tables.
  - *Manual Lossy Quality:* Direct encoder quality control (1–100).
  - *Target File Size:* Bounded binary search on encoder parameters to meet maximum limits (e.g. `≤ 50 KB`) or ranges (`20 KB - 50 KB`). Generates candidates strictly from original decoded sources (never compounding lossy passes). Never injects dummy byte padding to fake minimum sizes.
- **PDF Size Reduction:** Lossless structural optimization (object deduplication, stream deflation, and metadata stripping). Optional downsampling mode for scanned paper documents.
- **Shared CLI & GUI Core:** Identical processing engines, validation assertions, and safety checks power both the graphical interface and the automation CLI.

---

## Honest Capabilities & Format Limits

Upload Ready provides clear, factual documentation without exaggerated marketing claims:

| Format / Feature | Status | Notes |
| :--- | :--- | :--- |
| **JPEG / JPG** | Fully Supported | Read and write. Lossy format; recompression is lossy. |
| **PNG** | Fully Supported | Read and write. Full transparency support. Lossless compression. |
| **WebP** | Fully Supported | Read and write. Supports both lossy and lossless modes with alpha channel. |
| **BMP / TIFF / GIF** | Input Supported | Converted to JPEG, PNG, or WebP. Animated GIF / multi-frame TIFF: only first frame encoded when saving to single-frame JPEG. |
| **AVIF / HEIC / SVG** | Not Supported | Not included in initial release; avoids unverified runtime dependencies. |
| **Digitally Signed PDFs** | Warning Provided | Modifying, optimizing, or re-encoding a digitally signed PDF invalidates its cryptographic signature because file byte ranges are rewritten. Upload Ready warns before modifying. Retaining signature form fields does not preserve cryptographic validity. |
| **Searchable vs Scanned PDFs** | Distinguishable | Structural reduction preserves searchable text; scanned mode rasterizes pages into images. |

---

## Installation & Running

### Option 1: Standalone Portable Package (Windows x64)

The standalone release requires **no Python, Node.js, or external tools**. It runs offline on its very first launch:

1. Download `UploadReady-v1.0.0-windows-x64-portable.zip` from the release assets.
2. Extract the archive to any local folder.
3. Run `UploadReady.exe`.

### Option 2: Running from Source

Requires Python 3.10+ (tested on Python 3.12 x64):

```powershell
# In the source directory:
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\activate

# Install locked dependencies
pip install -r requirements.txt

# Launch Desktop GUI
python -m upload_ready

# Or run CLI directly
python -m upload_ready.cli --help
```

---

## Command-Line Interface (CLI)

The CLI uses the exact same core processing engine and validation models:

### 1. Format Conversion
```bash
# Convert PNG to WebP
upload-ready convert input.png -f webp -o ./output

# Convert transparent PNG to JPEG with custom background color
upload-ready convert icon.png -f jpg -o ./output --bg-color "#FFFFFF" -q 90
```

### 2. Image Resizing
```bash
# Fit inside 1920x1080 maintaining aspect ratio
upload-ready resize photo.jpg --width 1920 --height 1080 --mode fit -o ./output

# Exact crop to 800x800
upload-ready resize banner.png --width 800 --height 800 --mode crop -o ./output
```

### 3. Compression & Target File Size
```bash
# Lossless optimization (zero pixel delta)
upload-ready compress image.png --lossless -o ./output

# Target maximum file size (<= 50 KB)
upload-ready compress scan.jpg --max-size 50KB -o ./output

# Target file size range (20 KB - 50 KB)
upload-ready compress photo.jpg --min-size 20KB --max-size 50KB -o ./output
```

### 4. Images to PDF
```bash
upload-ready images-to-pdf page1.jpg page2.png page3.jpg -o ./combined.pdf --page-size A4 --margin 18.0
```

### 5. PDF to Images
```bash
upload-ready pdf-to-images document.pdf -o ./rendered_pages --pages "1, 3-5" --format png --dpi 150
```

### 6. PDF Size Reduction
```bash
upload-ready pdf-reduce heavy_doc.pdf -o ./optimized_doc.pdf
```

---

## Running the Automated Test Suite

```powershell
pytest -v
```

The test suite exercises format validation, EXIF transposition, transparency preservation, pixel equality in lossless compression, target size bounds, PDF operations, safety limits, and CLI parity using **synthetic fixtures** without personal documents or IDs.

---

## License

This project is licensed under the **GNU General Public License v3.0 or later** (GPL-3.0-or-later). See [`LICENSE`](LICENSE) for details.

