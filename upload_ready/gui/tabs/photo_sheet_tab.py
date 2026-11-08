"""Interactive Passport Photo & Signature Sheet panel with crop positioning and preview."""

import io
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageOps
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.photo_sheet import PhotoSheetOptions, mm_to_pixels


class PhotoSheetTab(QWidget):
    """
    Configuration panel for physical photo and signature print sheets:
    - Interactive crop positioning (horizontal/vertical framing sliders)
    - Real-time crop box preview
    - Physical dimensions in mm with pixel conversion at target DPI
    - Configurable grid copies, margins, spacing, and cut guides
    - Honest disclaimer without claiming official passport compliance
    """

    def __init__(self):
        super().__init__()
        self._loaded_image_path: Optional[Path] = None
        self._syncing: bool = False

        layout = QHBoxLayout(self)

        # Left Column: Configuration Controls
        left_widget = QWidget()
        form = QFormLayout(left_widget)

        # Source Photo Loader
        load_layout = QHBoxLayout()
        self.btn_load_photo = QPushButton("📂 Load Portrait / Signature...")
        self.btn_load_photo.clicked.connect(self._on_load_photo)
        self.lbl_photo_name = QLabel("No photo loaded")
        self.lbl_photo_name.setStyleSheet("color: #888;")
        load_layout.addWidget(self.btn_load_photo)
        load_layout.addWidget(self.lbl_photo_name, stretch=1)
        form.addRow("Source Image:", load_layout)

        # Photo Dimensions
        dim_layout = QHBoxLayout()
        self.spin_w = QDoubleSpinBox()
        self.spin_w.setRange(10.0, 300.0)
        self.spin_w.setValue(35.0)
        self.spin_w.setSuffix(" mm")
        self.spin_w.valueChanged.connect(self._on_mm_changed)

        self.spin_h = QDoubleSpinBox()
        self.spin_h.setRange(10.0, 300.0)
        self.spin_h.setValue(45.0)
        self.spin_h.setSuffix(" mm")
        self.spin_h.valueChanged.connect(self._on_mm_changed)

        dim_layout.addWidget(self.spin_w)
        dim_layout.addWidget(QLabel("×"))
        dim_layout.addWidget(self.spin_h)
        form.addRow("Photo Size (mm):", dim_layout)

        # Direct Pixel Dimensions
        px_layout = QHBoxLayout()
        self.spin_px_w = QSpinBox()
        self.spin_px_w.setRange(10, 10000)
        self.spin_px_w.setValue(413)
        self.spin_px_w.setSuffix(" px")
        self.spin_px_w.valueChanged.connect(self._on_px_changed)

        self.spin_px_h = QSpinBox()
        self.spin_px_h.setRange(10, 10000)
        self.spin_px_h.setValue(531)
        self.spin_px_h.setSuffix(" px")
        self.spin_px_h.valueChanged.connect(self._on_px_changed)

        px_layout.addWidget(self.spin_px_w)
        px_layout.addWidget(QLabel("×"))
        px_layout.addWidget(self.spin_px_h)
        form.addRow("Photo Size (px):", px_layout)

        # Resolution DPI & Calculated Pixel Size
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(72, 1200)
        self.spin_dpi.setValue(300)
        self.spin_dpi.valueChanged.connect(self._on_dpi_changed)
        form.addRow("Printing DPI:", self.spin_dpi)

        self.lbl_pixel_calc = QLabel("Target: 413 × 531 px")
        self.lbl_pixel_calc.setStyleSheet("color: #4ec9b0; font-family: monospace;")
        form.addRow("Pixel Equivalent:", self.lbl_pixel_calc)

        # Interactive Crop Positioning Sliders
        crop_group = QGroupBox("Interactive Crop Positioning")
        crop_layout = QFormLayout(crop_group)

        self.slider_crop_y = QSlider(Qt.Orientation.Horizontal)
        self.slider_crop_y.setRange(0, 100)
        self.slider_crop_y.setValue(35)  # Default slightly towards head
        self.slider_crop_y.valueChanged.connect(self._update_preview)
        crop_layout.addRow("Vertical (Head ↔ Torso):", self.slider_crop_y)

        self.slider_crop_x = QSlider(Qt.Orientation.Horizontal)
        self.slider_crop_x.setRange(0, 100)
        self.slider_crop_x.setValue(50)  # Default center
        self.slider_crop_x.valueChanged.connect(self._update_preview)
        crop_layout.addRow("Horizontal (Left ↔ Right):", self.slider_crop_x)

        form.addRow(crop_group)

        # Number of copies
        self.spin_copies = QSpinBox()
        self.spin_copies.setRange(1, 40)
        self.spin_copies.setValue(6)
        form.addRow("Number of Copies:", self.spin_copies)

        # Margins & Spacing
        spacing_layout = QHBoxLayout()
        self.spin_margin = QDoubleSpinBox()
        self.spin_margin.setRange(0.0, 50.0)
        self.spin_margin.setValue(10.0)
        self.spin_margin.setSuffix(" mm")

        self.spin_spacing = QDoubleSpinBox()
        self.spin_spacing.setRange(0.0, 50.0)
        self.spin_spacing.setValue(5.0)
        self.spin_spacing.setSuffix(" mm")

        spacing_layout.addWidget(QLabel("Margin:"))
        spacing_layout.addWidget(self.spin_margin)
        spacing_layout.addWidget(QLabel("Gap:"))
        spacing_layout.addWidget(self.spin_spacing)
        form.addRow("Layout Spacing:", spacing_layout)

        # Sheet Paper Size & Export Format
        self.combo_sheet = QComboBox()
        self.combo_sheet.addItems(["A4", "LETTER", "4X6", "CUSTOM"])
        self.combo_sheet.currentIndexChanged.connect(self._on_sheet_size_changed)
        form.addRow("Paper Sheet Size:", self.combo_sheet)

        custom_sheet_layout = QHBoxLayout()
        self.spin_custom_sheet_w = QDoubleSpinBox()
        self.spin_custom_sheet_w.setRange(20.0, 1000.0)
        self.spin_custom_sheet_w.setValue(210.0)
        self.spin_custom_sheet_w.setSuffix(" mm")
        self.spin_custom_sheet_w.valueChanged.connect(self._update_preview)

        self.spin_custom_sheet_h = QDoubleSpinBox()
        self.spin_custom_sheet_h.setRange(20.0, 1000.0)
        self.spin_custom_sheet_h.setValue(297.0)
        self.spin_custom_sheet_h.setSuffix(" mm")
        self.spin_custom_sheet_h.valueChanged.connect(self._update_preview)

        custom_sheet_layout.addWidget(self.spin_custom_sheet_w)
        custom_sheet_layout.addWidget(QLabel("×"))
        custom_sheet_layout.addWidget(self.spin_custom_sheet_h)
        self.custom_sheet_widget = QWidget()
        self.custom_sheet_widget.setLayout(custom_sheet_layout)
        self.custom_sheet_widget.setVisible(False)
        form.addRow("Custom Sheet (mm):", self.custom_sheet_widget)

        self.combo_format = QComboBox()
        self.combo_format.addItems(["PDF", "PNG", "JPEG"])
        form.addRow("Export Format:", self.combo_format)

        # Cut Guides & Signature Mode
        self.chk_cut_guides = QCheckBox("Print Corner Cut Guides")
        self.chk_cut_guides.setChecked(True)
        form.addRow("", self.chk_cut_guides)

        self.chk_signature = QCheckBox("Signature Mode (Preserve Alpha Transparency in PNG)")
        self.chk_signature.setChecked(False)
        form.addRow("", self.chk_signature)

        self.chk_allow_upscale = QCheckBox("Allow Deliberate Upscaling")
        self.chk_allow_upscale.setChecked(False)
        form.addRow("", self.chk_allow_upscale)

        # Honest Disclaimer
        disclaimer = QLabel(
            "<b>Disclaimer:</b> Upload Ready assists with physical sheet layout and cut guides.<br>"
            "It does not certify official compliance with passport regulations of any government."
        )
        disclaimer.setStyleSheet("color: #bbb; font-size: 11px; margin-top: 6px;")
        form.addRow("", disclaimer)

        layout.addWidget(left_widget, stretch=3)

        # Right Column: Live Interactive Crop & Layout Preview
        preview_group = QGroupBox("Live Crop Framing Preview")
        preview_layout = QVBoxLayout(preview_group)

        self.lbl_preview = QLabel("Load an image to preview crop framing.")
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(220, 260)
        self.lbl_preview.setStyleSheet("background-color: #1a1a1a; border: 1px dashed #444; border-radius: 4px;")
        preview_layout.addWidget(self.lbl_preview, stretch=1)

        layout.addWidget(preview_group, stretch=2)

    def set_source_image(self, path: Path) -> None:
        """Load source photo for preview."""
        if path.exists():
            self._loaded_image_path = path
            self.lbl_photo_name.setText(path.name)
            self._update_preview()

    def _on_load_photo(self) -> None:
        file, _ = QFileDialog.getOpenFileName(self, "Select Portrait or Signature", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if file:
            self.set_source_image(Path(file))

    def get_source_image_path(self) -> Optional[Path]:
        return self._loaded_image_path

    def _on_mm_changed(self) -> None:
        if not self._syncing:
            self._syncing = True
            dpi = self.spin_dpi.value()
            self.spin_px_w.setValue(mm_to_pixels(self.spin_w.value(), dpi))
            self.spin_px_h.setValue(mm_to_pixels(self.spin_h.value(), dpi))
            self._syncing = False
        self._update_preview()

    def _on_px_changed(self) -> None:
        if not self._syncing:
            self._syncing = True
            dpi = self.spin_dpi.value()
            self.spin_w.setValue(round(self.spin_px_w.value() * 25.4 / dpi, 2))
            self.spin_h.setValue(round(self.spin_px_h.value() * 25.4 / dpi, 2))
            self._syncing = False
        self._update_preview()

    def _on_dpi_changed(self) -> None:
        if not self._syncing:
            self._syncing = True
            dpi = self.spin_dpi.value()
            self.spin_px_w.setValue(mm_to_pixels(self.spin_w.value(), dpi))
            self.spin_px_h.setValue(mm_to_pixels(self.spin_h.value(), dpi))
            self._syncing = False
        self._update_preview()

    def _on_sheet_size_changed(self) -> None:
        is_custom = (self.combo_sheet.currentText() == "CUSTOM")
        self.custom_sheet_widget.setVisible(is_custom)
        self._update_preview()

    def _update_preview(self) -> None:
        w_mm = self.spin_w.value()
        h_mm = self.spin_h.value()
        dpi = self.spin_dpi.value()

        px_w = mm_to_pixels(w_mm, dpi)
        px_h = mm_to_pixels(h_mm, dpi)
        self.lbl_pixel_calc.setText(f"Target: {px_w} × {px_h} px ({w_mm:.1f} × {h_mm:.1f} mm)")

        if not self._loaded_image_path or not self._loaded_image_path.exists():
            return

        try:
            with Image.open(self._loaded_image_path) as raw_img:
                img = ImageOps.exif_transpose(raw_img) or raw_img
                orig_w, orig_h = img.size

                # Calculate crop rectangle
                ratio = max(px_w / orig_w, px_h / orig_h)
                crop_w = px_w / ratio
                crop_h = px_h / ratio
                max_ox = max(0.0, orig_w - crop_w)
                max_oy = max(0.0, orig_h - crop_h)
                pos_x = self.slider_crop_x.value() / 100.0
                pos_y = self.slider_crop_y.value() / 100.0
                left = max_ox * pos_x
                top = max_oy * pos_y
                right = min(float(orig_w), left + crop_w)
                bottom = min(float(orig_h), top + crop_h)

                # Render preview with overlay
                preview_canvas = img.copy().convert("RGBA")
                draw = ImageDraw.Draw(preview_canvas)

                # Dim outside crop area
                dim_overlay = Image.new("RGBA", (orig_w, orig_h), (0, 0, 0, 110))
                dim_draw = ImageDraw.Draw(dim_overlay)
                dim_draw.rectangle([left, top, right, bottom], fill=(0, 0, 0, 0))
                preview_canvas = Image.alpha_composite(preview_canvas, dim_overlay)

                # Draw green crop border
                draw_final = ImageDraw.Draw(preview_canvas)
                draw_final.rectangle([left, top, right, bottom], outline=(78, 201, 176, 255), width=max(2, orig_w // 150))

                # Scale to fit preview label
                preview_canvas.thumbnail((240, 280), Image.Resampling.BILINEAR)
                buf = io.BytesIO()
                preview_canvas.save(buf, format="PNG")
                buf.seek(0)
                qimg = QImage.fromData(buf.getvalue())
                self.lbl_preview.setPixmap(QPixmap.fromImage(qimg))

        except Exception as e:
            self.lbl_preview.setText(f"Preview error:\n{e}")

    def get_options(self) -> PhotoSheetOptions:
        sheet_sz = self.combo_sheet.currentText()
        is_custom = (sheet_sz == "CUSTOM")
        return PhotoSheetOptions(
            photo_width_mm=self.spin_w.value(),
            photo_height_mm=self.spin_h.value(),
            photo_width_px=self.spin_px_w.value(),
            photo_height_px=self.spin_px_h.value(),
            dpi=self.spin_dpi.value(),
            copies=self.spin_copies.value(),
            margin_mm=self.spin_margin.value(),
            spacing_mm=self.spin_spacing.value(),
            sheet_size=sheet_sz,
            custom_sheet_w_mm=self.spin_custom_sheet_w.value() if is_custom else None,
            custom_sheet_h_mm=self.spin_custom_sheet_h.value() if is_custom else None,
            output_format=self.combo_format.currentText(),
            cut_guides=self.chk_cut_guides.isChecked(),
            is_signature=self.chk_signature.isChecked(),
            preserve_alpha=self.chk_signature.isChecked(),
            allow_upscale=self.chk_allow_upscale.isChecked(),
            crop_pos_x=self.slider_crop_x.value() / 100.0,
            crop_pos_y=self.slider_crop_y.value() / 100.0,
        )
