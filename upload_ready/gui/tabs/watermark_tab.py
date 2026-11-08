"""Interactive Purpose Watermarking panel with live preview and PDF page range selection."""

import io
from pathlib import Path
from typing import Optional
from PIL import Image, ImageOps
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.watermark import WatermarkOptions, create_watermark_overlay_image


class WatermarkTab(QWidget):
    """
    Configuration panel for purpose watermarks:
    - Text, font size, color, opacity, angle, and positioning
    - Repeated tiled patterns or corner/center placement
    - Live visual preview of the rendered watermark
    - Target PDF page selection
    """

    def __init__(self):
        super().__init__()
        self._color = (128, 128, 128)
        self._preview_file: Optional[Path] = None

        layout = QHBoxLayout(self)

        # Left Column: Controls
        left_widget = QWidget()
        form = QFormLayout(left_widget)

        # Watermark Text
        self.txt_text = QLineEdit("FOR VERIFICATION ONLY")
        self.txt_text.textChanged.connect(self._update_preview)
        form.addRow("Watermark Text:", self.txt_text)

        # Placement Position
        self.combo_position = QComboBox()
        self.combo_position.addItem("Center Overlay", "center")
        self.combo_position.addItem("Repeated Tiled Pattern", "repeat_tile")
        self.combo_position.addItem("Top-Left Corner", "top_left")
        self.combo_position.addItem("Bottom-Right Corner", "bottom_right")
        self.combo_position.currentIndexChanged.connect(self._update_preview)
        form.addRow("Placement:", self.combo_position)

        # Opacity Slider
        opacity_layout = QHBoxLayout()
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(5, 100)
        self.slider_opacity.setValue(30)
        self.lbl_opacity = QLabel("30%")
        self.slider_opacity.valueChanged.connect(self._on_opacity_changed)
        opacity_layout.addWidget(self.slider_opacity)
        opacity_layout.addWidget(self.lbl_opacity)
        form.addRow("Opacity:", opacity_layout)

        # Rotation Angle
        angle_layout = QHBoxLayout()
        self.spin_angle = QSpinBox()
        self.spin_angle.setRange(-180, 180)
        self.spin_angle.setValue(45)
        self.spin_angle.setSuffix("°")
        self.spin_angle.valueChanged.connect(self._update_preview)
        angle_layout.addWidget(self.spin_angle)
        form.addRow("Rotation Angle:", angle_layout)

        # Font Size
        self.spin_font_size = QSpinBox()
        self.spin_font_size.setRange(10, 120)
        self.spin_font_size.setValue(36)
        self.spin_font_size.setSuffix(" pt")
        self.spin_font_size.valueChanged.connect(self._update_preview)
        form.addRow("Font Size:", self.spin_font_size)

        # Text Color
        color_layout = QHBoxLayout()
        self.btn_color = QPushButton("Choose Color...")
        self.btn_color.clicked.connect(self._choose_color)
        self.color_sample = QLabel("       ")
        self._update_color_sample()
        color_layout.addWidget(self.btn_color)
        color_layout.addWidget(self.color_sample)
        form.addRow("Text Color:", color_layout)

        # PDF Target Pages
        self.txt_pages = QLineEdit("all")
        self.txt_pages.setPlaceholderText("e.g. 'all' or '1, 3-5'")
        self.txt_pages.textChanged.connect(self._update_preview)
        form.addRow("PDF Pages to Watermark:", self.txt_pages)

        # Decryption Password for PDFs
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Leave blank if unencrypted")
        self.txt_password.textChanged.connect(self._update_preview)
        form.addRow("PDF Password:", self.txt_password)

        # Disclaimer
        disclaimer = QLabel(
            "<b>Honest Purpose Watermarking:</b> Watermarks signal intended purpose (e.g. rental check).<br>"
            "Watermarking preserves searchable PDF text where supported, but does not constitute cryptographic signing."
        )
        disclaimer.setStyleSheet("color: #bbb; font-size: 11px; margin-top: 8px;")
        form.addRow("", disclaimer)

        layout.addWidget(left_widget, stretch=3)

        # Right Column: Live Watermark Overlay Preview
        preview_group = QGroupBox("Live Watermark Preview")
        preview_layout = QVBoxLayout(preview_group)

        self.lbl_preview = QLabel()
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(220, 260)
        self.lbl_preview.setStyleSheet("background-color: #ffffff; border: 1px solid #444; border-radius: 4px;")
        preview_layout.addWidget(self.lbl_preview, stretch=1)

        layout.addWidget(preview_group, stretch=2)

        self._update_preview()

    def _on_opacity_changed(self, value: int) -> None:
        self.lbl_opacity.setText(f"{value}%")
        self._update_preview()

    def _choose_color(self) -> None:
        c = QColorDialog.getColor(QColor(*self._color), self, "Select Watermark Color")
        if c.isValid():
            self._color = (c.red(), c.green(), c.blue())
            self._update_color_sample()
            self._update_preview()

    def _update_color_sample(self) -> None:
        r, g, b = self._color
        self.color_sample.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid #777; border-radius: 3px;")

    def set_preview_file(self, path: Path) -> None:
        """Set an actual image or PDF document to show under the live watermark overlay."""
        self._preview_file = path if path.exists() else None
        self._update_preview()

    def _update_preview(self) -> None:
        try:
            pw, ph = 300, 380
            canvas = Image.new("RGBA", (pw, ph), (250, 250, 250, 255))
            options = self.get_options()

            if self._preview_file and self._preview_file.exists():
                ext = self._preview_file.suffix.lower()
                base_img = None
                scale_factor = 1.0
                is_watermarked_page = True

                if ext == ".pdf":
                    try:
                        import pypdf
                        from upload_ready.core.pdf_organizer import render_page_thumbnail
                        from upload_ready.core.watermark import parse_page_range
                        pwd = self.txt_password.text() or None
                        r = pypdf.PdfReader(self._preview_file)
                        if r.is_encrypted and pwd:
                            r.decrypt(pwd)
                        total_pages = len(r.pages)
                        page_spec = self.txt_pages.text().strip() or "all"
                        try:
                            target_indices = parse_page_range(page_spec, total_pages)
                        except Exception:
                            target_indices = set(range(total_pages))
                        
                        # Preview the first page that is actually targeted, or page 0 if none
                        preview_idx = min(target_indices) if target_indices else 0
                        is_watermarked_page = (preview_idx in target_indices)

                        # Match PDF export document coordinates (150 DPI resolution)
                        page = r.pages[preview_idx]
                        pw_pt = float(page.mediabox.width)
                        ph_pt = float(page.mediabox.height)
                        doc_w = max(10, round(pw_pt * (150.0 / 72.0)))
                        doc_h = max(10, round(ph_pt * (150.0 / 72.0)))

                        raw_thumb = render_page_thumbnail(self._preview_file, preview_idx, max_dim=max(pw, ph), password=pwd)
                        if raw_thumb is not None:
                            base_img = raw_thumb.copy()
                            base_img.thumbnail((pw, ph), Image.Resampling.LANCZOS)
                            thumb_w, thumb_h = base_img.size
                            scale_factor = thumb_w / max(1, doc_w)
                    except Exception:
                        base_img = None
                else:
                    try:
                        from upload_ready.core.safety import validate_dimensions
                        with Image.open(self._preview_file) as raw:
                            validate_dimensions(raw.width, raw.height, context="Watermark preview")
                            orig_w, orig_h = raw.size
                            exif = raw.getexif()
                            orientation = exif.get(0x0112, 1) if exif else 1
                            doc_w, doc_h = (orig_h, orig_w) if orientation in (5, 6, 7, 8) else (orig_w, orig_h)

                            max_dim = 1200
                            if max(orig_w, orig_h) > max_dim and raw.format in ("JPEG", "MPO"):
                                s = max_dim / max(orig_w, orig_h)
                                raw.draft(None, (max(1, int(orig_w * s)), max(1, int(orig_h * s))))
                            trans = ImageOps.exif_transpose(raw)
                            src_img = trans if trans is not None else raw
                            base_img = src_img.copy()
                            base_img.thumbnail((pw, ph), Image.Resampling.LANCZOS)
                            thumb_w, thumb_h = base_img.size
                            scale_factor = thumb_w / max(1, doc_w)
                    except Exception:
                        base_img = None

                if base_img is not None:
                    if base_img.mode != "RGBA":
                        base_img = base_img.convert("RGBA")
                    thumb_w, thumb_h = base_img.size
                    if is_watermarked_page:
                        # Render watermark overlay in document coordinates scaled to thumbnail geometry
                        doc_overlay = create_watermark_overlay_image(thumb_w, thumb_h, options, scale_factor=scale_factor)
                        composited = Image.alpha_composite(base_img, doc_overlay)
                    else:
                        composited = base_img

                    bx = (pw - composited.width) // 2
                    by = (ph - composited.height) // 2
                    canvas.alpha_composite(composited, (bx, by))
                else:
                    overlay = create_watermark_overlay_image(pw, ph, options, scale_factor=1.0)
                    canvas = Image.alpha_composite(canvas, overlay)
            else:
                overlay = create_watermark_overlay_image(pw, ph, options, scale_factor=1.0)
                canvas = Image.alpha_composite(canvas, overlay)

            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            buf.seek(0)
            qimg = QImage.fromData(buf.getvalue())
            self.lbl_preview.setPixmap(QPixmap.fromImage(qimg))
        except Exception as exc:
            self.lbl_preview.setText(f"Preview error:\n{exc}")

    def get_options(self) -> WatermarkOptions:
        return WatermarkOptions(
            text=self.txt_text.text().strip() or "FOR VERIFICATION ONLY",
            font_size=self.spin_font_size.value(),
            color=self._color,
            opacity=self.slider_opacity.value() / 100.0,
            angle=float(self.spin_angle.value()),
            position=self.combo_position.currentData(),
            page_range=self.txt_pages.text().strip() or "all",
            password=self.txt_password.text() or None,
        )
