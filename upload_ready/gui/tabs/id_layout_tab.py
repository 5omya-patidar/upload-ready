"""Interactive ID Front & Back Layout panel with slots, swapping, preview, and resolution check."""

import io
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageOps
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.id_layout import IdLayoutOptions
from upload_ready.core.photo_sheet import SHEET_SIZES_MM, mm_to_pixels
from upload_ready.core.watermark import WatermarkOptions, create_watermark_overlay_image


class IdLayoutTab(QWidget):
    """
    Configuration and interactive layout panel for ID Front & Back cards:
    - Clearly labelled Front and Back card slots with Browse buttons
    - Swap button to interchange Front and Back
    - Independent crop/fit mode and 90° rotation per card
    - Configurable card dimensions with quick CR80 preset button
    - Adjustable margins and spacing
    - Live resolution check and warning indicator (< 150 DPI)
    - Accurate live page preview
    """

    def __init__(self):
        super().__init__()
        self._front_path: Optional[Path] = None
        self._back_path: Optional[Path] = None
        self._thumb_cache: Dict[tuple, Image.Image] = {}

        layout = QHBoxLayout(self)

        # Left Column: Configuration Controls
        left_widget = QWidget()
        form = QFormLayout(left_widget)

        # Card Slots Section
        slots_group = QGroupBox("Card Source Slots")
        slots_layout = QFormLayout(slots_group)

        # Front Slot
        front_bar = QHBoxLayout()
        self.txt_front = QLineEdit()
        self.txt_front.setPlaceholderText("Select Front card image...")
        self.txt_front.textChanged.connect(self._on_front_path_changed)
        self.btn_browse_front = QPushButton("Browse...")
        self.btn_browse_front.clicked.connect(self._browse_front)
        front_bar.addWidget(self.txt_front, stretch=1)
        front_bar.addWidget(self.btn_browse_front)
        slots_layout.addRow("Front Card:", front_bar)

        # Front Controls
        front_ctrl_bar = QHBoxLayout()
        self.combo_front_rot = QComboBox()
        self.combo_front_rot.addItem("Rotate: 0°", 0)
        self.combo_front_rot.addItem("Rotate: 90°", 90)
        self.combo_front_rot.addItem("Rotate: 180°", 180)
        self.combo_front_rot.addItem("Rotate: 270°", 270)
        self.combo_front_rot.currentIndexChanged.connect(self._update_preview)
        front_ctrl_bar.addWidget(self.combo_front_rot)

        self.combo_front_crop = QComboBox()
        self.combo_front_crop.addItem("Proportional Fit", "fit")
        self.combo_front_crop.addItem("Center Fill & Crop", "crop")
        self.combo_front_crop.currentIndexChanged.connect(self._update_preview)
        front_ctrl_bar.addWidget(self.combo_front_crop)
        slots_layout.addRow("Front Options:", front_ctrl_bar)

        # Swap Button
        swap_bar = QHBoxLayout()
        self.btn_swap = QPushButton("⇄ Swap Front & Back")
        self.btn_swap.setStyleSheet("font-weight: bold; background-color: #2d2d2d; color: #4ec9b0; padding: 4px;")
        self.btn_swap.clicked.connect(self._swap_front_back)
        swap_bar.addWidget(self.btn_swap)
        slots_layout.addRow("", swap_bar)

        # Back Slot
        back_bar = QHBoxLayout()
        self.txt_back = QLineEdit()
        self.txt_back.setPlaceholderText("Select Back card image...")
        self.txt_back.textChanged.connect(self._on_back_path_changed)
        self.btn_browse_back = QPushButton("Browse...")
        self.btn_browse_back.clicked.connect(self._browse_back)
        back_bar.addWidget(self.txt_back, stretch=1)
        back_bar.addWidget(self.btn_browse_back)
        slots_layout.addRow("Back Card:", back_bar)

        # Back Controls
        back_ctrl_bar = QHBoxLayout()
        self.combo_back_rot = QComboBox()
        self.combo_back_rot.addItem("Rotate: 0°", 0)
        self.combo_back_rot.addItem("Rotate: 90°", 90)
        self.combo_back_rot.addItem("Rotate: 180°", 180)
        self.combo_back_rot.addItem("Rotate: 270°", 270)
        self.combo_back_rot.currentIndexChanged.connect(self._update_preview)
        back_ctrl_bar.addWidget(self.combo_back_rot)

        self.combo_back_crop = QComboBox()
        self.combo_back_crop.addItem("Proportional Fit", "fit")
        self.combo_back_crop.addItem("Center Fill & Crop", "crop")
        self.combo_back_crop.currentIndexChanged.connect(self._update_preview)
        back_ctrl_bar.addWidget(self.combo_back_crop)
        slots_layout.addRow("Back Options:", back_ctrl_bar)

        form.addRow(slots_group)

        # Card Dimensions & CR80 Preset
        card_dim_layout = QHBoxLayout()
        self.spin_card_w = QDoubleSpinBox()
        self.spin_card_w.setRange(20.0, 300.0)
        self.spin_card_w.setValue(85.6)
        self.spin_card_w.setSuffix(" mm")
        self.spin_card_w.valueChanged.connect(self._update_preview)

        self.spin_card_h = QDoubleSpinBox()
        self.spin_card_h.setRange(20.0, 300.0)
        self.spin_card_h.setValue(54.0)
        self.spin_card_h.setSuffix(" mm")
        self.spin_card_h.valueChanged.connect(self._update_preview)

        self.btn_cr80_preset = QPushButton("Standard CR80 (85.6×54mm)")
        self.btn_cr80_preset.clicked.connect(self._set_cr80_preset)

        card_dim_layout.addWidget(self.spin_card_w)
        card_dim_layout.addWidget(QLabel("×"))
        card_dim_layout.addWidget(self.spin_card_h)
        card_dim_layout.addWidget(self.btn_cr80_preset)
        form.addRow("Card Dimensions:", card_dim_layout)

        # Layout Arrangement (Stacked vs Side-by-side)
        self.combo_arrangement = QComboBox()
        self.combo_arrangement.addItem("Vertically Stacked (Top / Bottom)", "stacked")
        self.combo_arrangement.addItem("Side-by-Side (Left / Right)", "side_by_side")
        self.combo_arrangement.currentIndexChanged.connect(self._update_preview)
        form.addRow("Card Placement:", self.combo_arrangement)

        # Spacing and Margins
        space_bar = QHBoxLayout()
        self.spin_margin = QDoubleSpinBox()
        self.spin_margin.setRange(0.0, 100.0)
        self.spin_margin.setValue(10.0)
        self.spin_margin.setSuffix(" mm")
        self.spin_margin.valueChanged.connect(self._update_preview)

        self.spin_spacing = QDoubleSpinBox()
        self.spin_spacing.setRange(0.0, 100.0)
        self.spin_spacing.setValue(10.0)
        self.spin_spacing.setSuffix(" mm")
        self.spin_spacing.valueChanged.connect(self._update_preview)

        space_bar.addWidget(QLabel("Margin:"))
        space_bar.addWidget(self.spin_margin)
        space_bar.addWidget(QLabel("Spacing:"))
        space_bar.addWidget(self.spin_spacing)
        form.addRow("Margins & Gaps:", space_bar)

        # Paper Sheet Size, DPI, Format
        opt_bar = QHBoxLayout()
        self.combo_sheet = QComboBox()
        self.combo_sheet.addItems(["A4", "LETTER"])
        self.combo_sheet.currentIndexChanged.connect(self._update_preview)

        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(72, 1200)
        self.spin_dpi.setValue(300)
        self.spin_dpi.valueChanged.connect(self._update_preview)

        self.combo_format = QComboBox()
        self.combo_format.addItems(["PDF", "PNG", "JPEG"])

        opt_bar.addWidget(self.combo_sheet)
        opt_bar.addWidget(QLabel("DPI:"))
        opt_bar.addWidget(self.spin_dpi)
        opt_bar.addWidget(QLabel("Export:"))
        opt_bar.addWidget(self.combo_format)
        form.addRow("Print Options:", opt_bar)

        # Purpose Watermark Overlay
        self.txt_watermark = QLineEdit()
        self.txt_watermark.setPlaceholderText("e.g. FOR VERIFICATION ONLY (optional)")
        self.txt_watermark.textChanged.connect(self._update_preview)
        form.addRow("Purpose Watermark:", self.txt_watermark)

        # Live Resolution Check Label
        self.lbl_resolution_status = QLabel("Resolution status: Load card images to evaluate DPI.")
        self.lbl_resolution_status.setStyleSheet("color: #aaa; font-size: 11px;")
        form.addRow("Resolution Quality:", self.lbl_resolution_status)

        layout.addWidget(left_widget, stretch=3)

        # Right Column: Live Sheet Preview
        preview_group = QGroupBox("Live Sheet Layout Preview")
        preview_layout = QVBoxLayout(preview_group)

        self.lbl_preview = QLabel("Load Front and Back images to view sheet preview.")
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(220, 280)
        self.lbl_preview.setStyleSheet("background-color: #ffffff; border: 1px solid #444; border-radius: 4px;")
        preview_layout.addWidget(self.lbl_preview, stretch=1)

        layout.addWidget(preview_group, stretch=2)

    def _browse_front(self) -> None:
        file, _ = QFileDialog.getOpenFileName(self, "Select Front Card Image", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if file:
            self.txt_front.setText(file)

    def _browse_back(self) -> None:
        file, _ = QFileDialog.getOpenFileName(self, "Select Back Card Image", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if file:
            self.txt_back.setText(file)

    def _on_front_path_changed(self, text: str) -> None:
        p = Path(text.strip())
        self._front_path = p if p.exists() else None
        self._update_preview()

    def _on_back_path_changed(self, text: str) -> None:
        p = Path(text.strip())
        self._back_path = p if p.exists() else None
        self._update_preview()

    def _swap_front_back(self) -> None:
        f_text = self.txt_front.text()
        b_text = self.txt_back.text()
        self.txt_front.setText(b_text)
        self.txt_back.setText(f_text)

        # Swap rotations
        f_rot_idx = self.combo_front_rot.currentIndex()
        b_rot_idx = self.combo_back_rot.currentIndex()
        self.combo_front_rot.setCurrentIndex(b_rot_idx)
        self.combo_back_rot.setCurrentIndex(f_rot_idx)

    def _set_cr80_preset(self) -> None:
        self.spin_card_w.setValue(85.6)
        self.spin_card_h.setValue(54.0)

    def _update_preview(self) -> None:
        # Check DPI of source images against physical card dimensions
        card_w_mm = self.spin_card_w.value()
        card_h_mm = self.spin_card_h.value()
        warnings = []

        if self._front_path:
            try:
                with Image.open(self._front_path) as img:
                    dpi_w = img.width / (card_w_mm / 25.4)
                    dpi_h = img.height / (card_h_mm / 25.4)
                    eff_dpi = int(min(dpi_w, dpi_h))
                    if eff_dpi < 150:
                        warnings.append(f"Front: {eff_dpi} DPI (Low resolution <150 DPI)")
            except Exception:
                pass

        if self._back_path:
            try:
                with Image.open(self._back_path) as img:
                    dpi_w = img.width / (card_w_mm / 25.4)
                    dpi_h = img.height / (card_h_mm / 25.4)
                    eff_dpi = int(min(dpi_w, dpi_h))
                    if eff_dpi < 150:
                        warnings.append(f"Back: {eff_dpi} DPI (Low resolution <150 DPI)")
            except Exception:
                pass

        if warnings:
            self.lbl_resolution_status.setText("⚠ " + " | ".join(warnings))
            self.lbl_resolution_status.setStyleSheet("color: #e06c75; font-weight: bold;")
        elif self._front_path and self._back_path:
            self.lbl_resolution_status.setText("✓ High print quality (>=150 DPI)")
            self.lbl_resolution_status.setStyleSheet("color: #4ec9b0; font-weight: bold;")
        else:
            self.lbl_resolution_status.setText("Resolution status: Load card images to evaluate DPI.")
            self.lbl_resolution_status.setStyleSheet("color: #aaa;")

        def render_card_thumb(card_path: Optional[Path], rot_deg: int, crop_mode: str, tw: int, th: int, label: str) -> Image.Image:
            if not card_path or not card_path.exists():
                card_canvas = Image.new("RGBA", (tw, th), (245, 245, 245, 255))
                cdraw = ImageDraw.Draw(card_canvas)
                cdraw.text((max(4, tw // 2 - 20), max(4, th // 2 - 6)), label, fill=(120, 120, 120))
                cdraw.rectangle([0, 0, tw - 1, th - 1], outline=(150, 150, 150))
                return card_canvas

            cache_key = (str(card_path), card_path.stat().st_mtime, rot_deg, crop_mode, tw, th)
            if hasattr(self, "_thumb_cache") and cache_key in self._thumb_cache:
                return self._thumb_cache[cache_key].copy()

            card_canvas = Image.new("RGBA", (tw, th), (245, 245, 245, 255))
            try:
                from upload_ready.core.safety import validate_dimensions
                with Image.open(card_path) as raw:
                    # Enforce dimension bounds before decoding full pixel raster or intermediate copies
                    validate_dimensions(raw.width, raw.height, context="ID card preview")
                    max_dim = max(tw, th) * 2
                    if max(raw.width, raw.height) > max_dim and raw.format in ("JPEG", "MPO"):
                        s = max_dim / max(raw.width, raw.height)
                        raw.draft(None, (max(1, int(raw.width * s)), max(1, int(raw.height * s))))

                    trans = ImageOps.exif_transpose(raw)
                    src_img = trans if trans is not None else raw

                    if rot_deg % 360 != 0:
                        src_img = src_img.rotate(-rot_deg % 360, expand=True)

                    if crop_mode == "crop":
                        # Source crop box with bounded target allocation directly to (tw, th)
                        ratio = max(tw / max(1, src_img.width), th / max(1, src_img.height))
                        crop_src_w = tw / ratio
                        crop_src_h = th / ratio
                        left = (src_img.width - crop_src_w) / 2.0
                        top = (src_img.height - crop_src_h) / 2.0
                        c_img = src_img.resize(
                            (tw, th),
                            resample=Image.Resampling.BILINEAR,
                            box=(left, top, left + crop_src_w, top + crop_src_h),
                        )
                    else:
                        # Fit mode: retain proportions without distortion
                        c_img = src_img.copy()
                        c_img.thumbnail((tw, th), Image.Resampling.BILINEAR)

                    px = (tw - c_img.width) // 2
                    py = (th - c_img.height) // 2
                    if c_img.mode != "RGBA":
                        c_img = c_img.convert("RGBA")
                    card_canvas.alpha_composite(c_img, (px, py))
            except Exception:
                pass

            cdraw = ImageDraw.Draw(card_canvas)
            cdraw.rectangle([0, 0, tw - 1, th - 1], outline=(150, 150, 150))
            if hasattr(self, "_thumb_cache"):
                self._thumb_cache[cache_key] = card_canvas.copy()
            return card_canvas

        sheet_key = self.combo_sheet.currentText().upper()
        sheet_w_mm, sheet_h_mm = SHEET_SIZES_MM.get(sheet_key, (210.0, 297.0))

        sw = 240
        sh = max(100, round(sw * (sheet_h_mm / sheet_w_mm)))
        sheet_canvas = Image.new("RGBA", (sw, sh), (255, 255, 255, 255))

        scale = sw / sheet_w_mm
        cw = max(20, round(card_w_mm * scale))
        ch = max(15, round(card_h_mm * scale))
        margin_px = round(self.spin_margin.value() * scale)
        gap = max(2, round(self.spin_spacing.value() * scale))

        f_rot = int(self.combo_front_rot.currentData() or 0)
        b_rot = int(self.combo_back_rot.currentData() or 0)
        f_crop = str(self.combo_front_crop.currentData() or "fit")
        b_crop = str(self.combo_back_crop.currentData() or "fit")

        front_thumb = render_card_thumb(self._front_path, f_rot, f_crop, cw, ch, "FRONT")
        back_thumb = render_card_thumb(self._back_path, b_rot, b_crop, cw, ch, "BACK")

        layout_type = self.combo_arrangement.currentData()

        if layout_type == "side_by_side":
            total_cards_w = 2 * cw + gap
            avail_w = max(0, sw - 2 * margin_px)
            start_x = margin_px + max(0, (avail_w - total_cards_w) // 2)
            avail_h = max(0, sh - 2 * margin_px)
            start_y = margin_px + max(0, (avail_h - ch) // 2)

            sheet_canvas.alpha_composite(front_thumb, (start_x, start_y))
            bx = start_x + cw + gap
            sheet_canvas.alpha_composite(back_thumb, (bx, start_y))
        else:
            total_cards_h = 2 * ch + gap
            avail_w = max(0, sw - 2 * margin_px)
            start_x = margin_px + max(0, (avail_w - cw) // 2)
            avail_h = max(0, sh - 2 * margin_px)
            start_y = margin_px + max(0, (avail_h - total_cards_h) // 2)

            sheet_canvas.alpha_composite(front_thumb, (start_x, start_y))
            by = start_y + ch + gap
            sheet_canvas.alpha_composite(back_thumb, (start_x, by))

        # Purpose watermark preview overlay
        wm_text = self.txt_watermark.text().strip()
        if wm_text:
            wm_opt = WatermarkOptions(text=wm_text, opacity=0.25, angle=45.0)
            overlay = create_watermark_overlay_image(sw, sh, wm_opt)
            sheet_canvas = Image.alpha_composite(sheet_canvas, overlay)

        buf = io.BytesIO()
        sheet_canvas.save(buf, format="PNG")
        buf.seek(0)
        qimg = QImage.fromData(buf.getvalue())
        self.lbl_preview.setPixmap(QPixmap.fromImage(qimg))

    def set_front_back_from_queue(self, paths: list[Path]) -> None:
        """Helper to populate front and back slots from queued files."""
        if len(paths) >= 1 and paths[0].exists():
            self.txt_front.setText(str(paths[0]))
        if len(paths) >= 2 and paths[1].exists():
            self.txt_back.setText(str(paths[1]))

    def get_options(self) -> IdLayoutOptions:
        wm = None
        txt = self.txt_watermark.text().strip()
        if txt:
            wm = WatermarkOptions(text=txt, opacity=0.25, angle=45.0)

        return IdLayoutOptions(
            layout_type=self.combo_arrangement.currentData(),
            card_width_mm=self.spin_card_w.value(),
            card_height_mm=self.spin_card_h.value(),
            dpi=self.spin_dpi.value(),
            sheet_size=self.combo_sheet.currentText(),
            margin_mm=self.spin_margin.value(),
            spacing_mm=self.spin_spacing.value(),
            front_rotation=self.combo_front_rot.currentData(),
            back_rotation=self.combo_back_rot.currentData(),
            front_crop_mode=self.combo_front_crop.currentData(),
            back_crop_mode=self.combo_back_crop.currentData(),
            output_format=self.combo_format.currentText(),
            watermark=wm,
        )

    def get_front_and_back_paths(self) -> Tuple[Path, Path]:
        f = Path(self.txt_front.text().strip())
        b = Path(self.txt_back.text().strip())
        if not f.exists():
            raise ValueError(f"Front card image does not exist: {f}")
        if not b.exists():
            raise ValueError(f"Back card image does not exist: {b}")
        return f, b
