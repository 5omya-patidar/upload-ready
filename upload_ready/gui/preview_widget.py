"""Interactive comparison and preview widget showing before and after states.

Renders both image formats (with EXIF orientation normalization) and PDF documents
(using pypdfium2 page rendering). Does not hide generated output files solely because
a target size requirement was unmet.
"""

from pathlib import Path
from typing import Optional
from PIL import Image, ImageOps
import pypdfium2
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.constants import UnitStandard, bytes_to_human
from upload_ready.core.models import JobResult
from upload_ready.core.safety import sniff_content_type, validate_dimensions


def pil_image_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    """Safely convert PIL Image to Qt QPixmap."""
    if pil_img.mode == "RGBA":
        data = pil_img.tobytes("raw", "RGBA")
        qim = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888)
    elif pil_img.mode == "RGB":
        data = pil_img.tobytes("raw", "RGB")
        qim = QImage(data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format.Format_RGB888)
    elif pil_img.mode == "L":
        data = pil_img.tobytes("raw", "L")
        qim = QImage(data, pil_img.width, pil_img.height, pil_img.width, QImage.Format.Format_Grayscale8)
    else:
        rgb = pil_img.convert("RGB")
        data = rgb.tobytes("raw", "RGB")
        qim = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qim)


class ImagePane(QFrame):
    """Single zoomable image viewing card with metadata header."""

    def __init__(self, title: str):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { background-color: #2b2b2b; border: 1px solid #3c3c3c; border-radius: 6px; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #e0e0e0;")
        layout.addWidget(self.title_label)

        # Metadata
        self.meta_label = QLabel("No file selected")
        self.meta_label.setStyleSheet("font-size: 11px; color: #a0a0a0; margin-bottom: 4px;")
        layout.addWidget(self.meta_label)

        # Scrollable image display
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setStyleSheet("QScrollArea { border: none; background-color: #1e1e1e; }")

        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.scroll.setWidget(self.img_label)
        layout.addWidget(self.scroll, stretch=1)

        self._original_pixmap: Optional[QPixmap] = None
        self._zoom_factor: float = 1.0

    def set_content(self, path: Optional[Path], metadata_text: str = "") -> None:
        """Load and display preview from image or PDF file path."""
        if not path or not path.exists():
            self.clear()
            return

        self.meta_label.setText(metadata_text)
        try:
            detected = ""
            try:
                detected = sniff_content_type(path)
            except Exception:
                pass

            if detected == "PDF" or path.suffix.lower() == ".pdf":
                # Render first page of PDF using pypdfium2 with pre-calculated bounds to prevent large allocation
                doc = pypdfium2.PdfDocument(path)
                page = doc.get_page(0)
                page_w = max(1.0, float(page.get_width()))
                page_h = max(1.0, float(page.get_height()))
                max_dim = max(page_w, page_h)
                scale = min(1.5, 1600.0 / max_dim)
                rendered = page.render(scale=scale).to_pil()
                self._original_pixmap = pil_image_to_qpixmap(rendered)
            else:
                with Image.open(path) as img:
                    # Enforce dimensions
                    validate_dimensions(img.width, img.height, context="Preview image")
                    w, h = img.width, img.height
                    max_preview_dim = 1600

                    # Use draft decoding for large JPEGs to avoid huge memory spikes on GUI thread
                    if max(w, h) > max_preview_dim and img.format in ("JPEG", "MPO"):
                        scale = max_preview_dim / max(w, h)
                        img.draft(None, (max(1, int(w * scale)), max(1, int(h * scale))))

                    # Normalize EXIF orientation
                    transposed = ImageOps.exif_transpose(img)
                    orig = transposed if transposed is not None else img

                    # Limit preview thumbnail size without full copy if large
                    if orig.width > max_preview_dim or orig.height > max_preview_dim:
                        orig.thumbnail((max_preview_dim, max_preview_dim), Image.Resampling.BILINEAR)

                    # Clamp extreme aspect ratios to prevent unbounded Qt texture allocations
                    if orig.width > 0 and orig.height > 0:
                        aspect = orig.width / orig.height
                        if aspect > 50.0:
                            orig = orig.resize((max_preview_dim, max(1, int(max_preview_dim / 50.0))), Image.Resampling.BILINEAR)
                        elif aspect < 0.02:
                            orig = orig.resize((max(1, int(max_preview_dim * 0.02)), max_preview_dim), Image.Resampling.BILINEAR)

                    self._original_pixmap = pil_image_to_qpixmap(orig)

            self.update_view()
        except Exception:
            self.clear("Unable to load preview")

    def set_pixmap_direct(self, pixmap: QPixmap, metadata_text: str) -> None:
        self.meta_label.setText(metadata_text)
        self._original_pixmap = pixmap
        self.update_view()

    def update_view(self) -> None:
        if self._original_pixmap and not self._original_pixmap.isNull():
            w = int(self._original_pixmap.width() * self._zoom_factor)
            h = int(self._original_pixmap.height() * self._zoom_factor)
            scaled = self._original_pixmap.scaled(
                w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self.img_label.setPixmap(scaled)
            self.img_label.resize(scaled.size())
        else:
            self.img_label.setText("No Preview")

    def zoom_in(self) -> None:
        self._zoom_factor = min(4.0, self._zoom_factor * 1.25)
        self.update_view()

    def zoom_out(self) -> None:
        self._zoom_factor = max(0.2, self._zoom_factor / 1.25)
        self.update_view()

    def zoom_reset(self) -> None:
        self._zoom_factor = 1.0
        self.update_view()

    def clear(self, message: str = "No file selected") -> None:
        self._original_pixmap = None
        self._zoom_factor = 1.0
        self.img_label.clear()
        self.img_label.setText(message)
        self.meta_label.setText("")


class PreviewWidget(QWidget):
    """Side-by-side before and after comparison view."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QHBoxLayout()
        header = QLabel("Preview & Comparison")
        header.setStyleSheet("font-weight: bold; font-size: 14px;")
        toolbar.addWidget(header)
        toolbar.addStretch()

        btn_zoom_in = QPushButton("+ Zoom In")
        btn_zoom_in.clicked.connect(self._zoom_in_both)
        toolbar.addWidget(btn_zoom_in)

        btn_zoom_out = QPushButton("- Zoom Out")
        btn_zoom_out.clicked.connect(self._zoom_out_both)
        toolbar.addWidget(btn_zoom_out)

        btn_zoom_reset = QPushButton("Reset Zoom")
        btn_zoom_reset.clicked.connect(self._zoom_reset_both)
        toolbar.addWidget(btn_zoom_reset)

        layout.addLayout(toolbar)

        # Splitter with Original and Result panes
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.original_pane = ImagePane("Original Input")
        self.result_pane = ImagePane("Processed Result")

        self.splitter.addWidget(self.original_pane)
        self.splitter.addWidget(self.result_pane)
        layout.addWidget(self.splitter, stretch=1)

    def _zoom_in_both(self):
        self.original_pane.zoom_in()
        self.result_pane.zoom_in()

    def _zoom_out_both(self):
        self.original_pane.zoom_out()
        self.result_pane.zoom_out()

    def _zoom_reset_both(self):
        self.original_pane.zoom_reset()
        self.result_pane.zoom_reset()

    def load_original(self, path: Path) -> None:
        if not path.exists():
            return
        size_bytes = path.stat().st_size
        size_human = bytes_to_human(size_bytes, UnitStandard.DECIMAL)
        try:
            detected = ""
            try:
                detected = sniff_content_type(path)
            except Exception:
                pass

            if detected == "PDF" or path.suffix.lower() == ".pdf":
                doc = pypdfium2.PdfDocument(path)
                meta = f"Format: PDF | Pages: {len(doc)} | File Size: {size_human}"
            else:
                with Image.open(path) as img:
                    transposed = ImageOps.exif_transpose(img)
                    im = transposed if transposed is not None else img
                    dims = f"{im.width}x{im.height}"
                    fmt = im.format or path.suffix.upper().lstrip(".")
                meta = f"Format: {fmt} | Dimensions: {dims} | File Size: {size_human}"
        except Exception:
            meta = f"File Size: {size_human}"
        self.original_pane.set_content(path, meta)

    def load_result(self, result: JobResult) -> None:
        # Do not hide output file if it exists, even if a requirement check failed
        has_file = result.output_path and result.output_path.exists()
        if not has_file:
            self.result_pane.clear(f"Processing failed: {result.error_message or 'No output produced'}")
            return

        size_human = bytes_to_human(result.output_size_bytes, UnitStandard.DECIMAL)
        diff_str = ""
        if result.input_size_bytes > 0:
            diff_bytes = result.output_size_bytes - result.input_size_bytes
            pct = (diff_bytes / result.input_size_bytes) * 100
            diff_str = f" ({pct:+.1f}%)"

        dims_str = f"{result.output_dimensions[0]}x{result.output_dimensions[1]}" if result.output_dimensions else "Doc"
        status_note = ""
        if not result.success:
            status_note = f" [Requirement unmet: {result.error_message}]"

        meta = f"Dimensions: {dims_str} | Size: {size_human}{diff_str}{status_note}"
        self.result_pane.set_content(result.output_path, meta)

    def clear(self) -> None:
        self.original_pane.clear()
        self.result_pane.clear()
