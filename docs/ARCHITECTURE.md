# Architecture & Design

Upload Ready is structured with a strict separation between **core domain logic**, **desktop UI**, and **command-line interface (CLI)**.

---

## 1. Stack Selection & Justification

| Component | Technology | Rationale |
| :--- | :--- | :--- |
| **Runtime & Language** | Python 3.12 x64 | Mature ecosystem, robust C extension binding, cross-platform packaging with PyInstaller. |
| **UI Framework** | PySide6-Essentials (LGPLv3) | Native desktop styling, responsive `QThread` event model, drag-and-drop, zoomable graphics, lean memory profile (~60-90 MB vs 250MB+ Chromium). Stripped of heavy WebEngine/3D/Bluetooth modules. |
| **Image Engine** | Pillow (HPND/PIL License) | High-performance SIMD-accelerated imaging library with bundled libjpeg-turbo, libpng, and libwebp. Supports exact Lanczos resampling, EXIF transposition, and alpha compositing. |
| **PDF Rendering** | pypdfium2 (Apache 2.0 / BSD) | Google PDFium engine wrapper. Fast, faithful page rendering at arbitrary DPI without external poppler binary dependencies. |
| **PDF Structure** | pypdf (BSD 3-Clause) | Pure Python PDF library for stream deflation, object deduplication, metadata manipulation, and digital signature detection. |

---

## 2. Module Organization

```
upload_ready/
├── __init__.py          # Package metadata and version definition
├── __main__.py          # Unified entry point (dispatches to GUI or CLI)
├── cli.py               # Command-line interface reusing core models
├── core/                # Pure domain logic (ZERO UI dependencies)
│   ├── constants.py     # Limits, units (Decimal vs Binary), PDF page sizes
│   ├── safety.py        # Magic byte sniffing, traversal protection, temp workspaces
│   ├── models.py        # Typed dataclasses, RequirementCheck, JobResult
│   ├── image_convert.py # Format conversion, EXIF transposition, alpha flattening
│   ├── image_resize.py  # Fit, Crop, Pad modes, DPI metadata vs resampling
│   ├── image_compress.py# Lossless optimization, target size bounded search
│   └── pdf_ops.py       # Images to PDF, PDF to Images, structural reduce
└── gui/                 # Desktop Graphical Interface (PySide6)
    ├── app.py           # Application initialization and theme
    ├── main_window.py   # 5-step UI workflow controller
    ├── file_queue.py    # Drag-and-drop table, reordering, and item rotation
    ├── preview_widget.py# Side-by-side zoomable before/after comparison
    ├── report_dialog.py # Honest validation report modal
    ├── worker.py        # QThread worker with progress and cancellation
    └── tabs/            # Operation configuration panels
```

---

## 3. Threading and Concurrency Model

- **UI Responsiveness:** The Qt main thread handles exclusively user interactions, animations, and widget updates.
- **Worker Threading:** All image and PDF operations execute on a dedicated background thread (`BatchProcessingWorker` subclassing `QThread`).
- **Cancellation:** The worker checks an atomic `_is_cancelled` flag between operations. When the user clicks **Cancel**, ongoing batches stop immediately and temporary workspaces are purged without corrupting output destinations.
- **Signal Decoupling:** Communication between the processing thread and UI is strictly asynchronous using Qt Signals:
  - `progress(current, total, filename)`
  - `item_finished(JobResult)`
  - `batch_finished(list)`
  - `cancelled()`

---

## 4. Bounded Target File Size Algorithm

1. The target size optimizer in `upload_ready.core.image_compress.search_target_size` operates strictly on the **original decoded image**, never compounding lossy compression artifacts across iterations.
2. Binary search executes within bounded limits (`MAX_OPTIMIZER_ITERATIONS = 12`) on encoder quality parameters (5 to 95 for JPEG/WebP) or color quantization levels (for PNG).
3. Every candidate is encoded into memory (`io.BytesIO`) and its exact byte length is measured.
4. If a candidate satisfies the range `[min_bytes, max_bytes]`, it is accepted.
5. If constraints are mathematically unachievable (e.g. image entropy exceeds target size even at minimum quality, or image is smaller than target minimum), Upload Ready presents the closest candidate, reports the exact unmet constraint, and suggests relaxing constraints (e.g. reducing pixel dimensions).

