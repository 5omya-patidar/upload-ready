"""Settings panel for Image Resizing and Dimensions."""

from typing import Optional
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.models import ResizeMode, ResizeOptions


class ResizeTab(QWidget):
    """Configuration controls for image resizing."""

    def __init__(self):
        super().__init__()
        self._bg_color = (255, 255, 255)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Dimensions
        dim_layout = QHBoxLayout()
        self.spin_w = QSpinBox()
        self.spin_w.setRange(0, 50000)
        self.spin_w.setValue(1920)
        self.spin_w.setSpecialValueText("Auto")
        dim_layout.addWidget(QLabel("W:"))
        dim_layout.addWidget(self.spin_w)

        self.spin_h = QSpinBox()
        self.spin_h.setRange(0, 50000)
        self.spin_h.setValue(1080)
        self.spin_h.setSpecialValueText("Auto")
        dim_layout.addWidget(QLabel("H:"))
        dim_layout.addWidget(self.spin_h)
        dim_layout.addWidget(QLabel("px"))
        form.addRow("Target Dimensions:", dim_layout)

        # Aspect Ratio Lock
        self.chk_lock_aspect = QCheckBox("Lock Aspect Ratio")
        self.chk_lock_aspect.setChecked(True)
        form.addRow("", self.chk_lock_aspect)

        # Resize Mode
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("Fit (Scale proportionally inside bounding box)", ResizeMode.FIT)
        self.combo_mode.addItem("Crop (Scale to fill and center-crop excess)", ResizeMode.CROP)
        self.combo_mode.addItem("Pad (Fit inside and pad canvas with background color)", ResizeMode.PAD)
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Fitting Mode:", self.combo_mode)

        # Padding background color
        pad_bg_layout = QHBoxLayout()
        self.btn_pad_color = QPushButton()
        self.btn_pad_color.setFixedSize(40, 24)
        self._update_color_button()
        self.btn_pad_color.clicked.connect(self._choose_color)
        self.lbl_pad_desc = QLabel("White (#FFFFFF)")
        pad_bg_layout.addWidget(self.btn_pad_color)
        pad_bg_layout.addWidget(self.lbl_pad_desc)
        pad_bg_layout.addStretch()
        self.row_pad_label = QLabel("Padding Background:")
        form.addRow(self.row_pad_label, pad_bg_layout)

        # Allow deliberate upscaling
        self.chk_allow_upscale = QCheckBox("Allow deliberate upscaling")
        self.chk_allow_upscale.setChecked(False)
        self.chk_allow_upscale.setToolTip("Refuses enlargement unless explicitly authorized.")
        form.addRow("", self.chk_allow_upscale)

        # Optional DPI metadata
        dpi_layout = QHBoxLayout()
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(0, 2400)
        self.spin_dpi.setValue(0)
        self.spin_dpi.setSpecialValueText("Unchanged")
        dpi_layout.addWidget(self.spin_dpi)
        dpi_layout.addWidget(QLabel("DPI (metadata only, does not change pixel count)"))
        form.addRow("DPI Setting:", dpi_layout)

        layout.addLayout(form)

        # Explanatory notice
        self.lbl_notice = QLabel(
            "Note: Changing DPI metadata adjusts physical print scale headers without resampling pixels. "
            "Resizing changes actual pixel grid using Lanczos interpolation."
        )
        self.lbl_notice.setWordWrap(True)
        self.lbl_notice.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.lbl_notice)
        layout.addStretch()

        self._on_mode_changed()

    def _on_mode_changed(self) -> None:
        mode = self.combo_mode.currentData()
        is_pad = mode == ResizeMode.PAD
        self.btn_pad_color.setEnabled(is_pad)
        self.lbl_pad_desc.setEnabled(is_pad)
        self.row_pad_label.setEnabled(is_pad)

    def _choose_color(self) -> None:
        color = QColorDialog.getColor()
        if color.isValid():
            self._bg_color = (color.red(), color.green(), color.blue())
            hex_str = f"#{color.red():02X}{color.green():02X}{color.blue():02X}"
            self.lbl_pad_desc.setText(hex_str)
            self._update_color_button()

    def _update_color_button(self) -> None:
        r, g, b = self._bg_color
        self.btn_pad_color.setStyleSheet(f"background-color: rgb({r}, {g}, {b}); border: 1px solid #666;")

    def get_options(self) -> ResizeOptions:
        w = self.spin_w.value() if self.spin_w.value() > 0 else None
        h = self.spin_h.value() if self.spin_h.value() > 0 else None
        dpi_val = self.spin_dpi.value()
        dpi_tuple = (dpi_val, dpi_val) if dpi_val > 0 else None

        return ResizeOptions(
            width=w,
            height=h,
            aspect_ratio_lock=self.chk_lock_aspect.isChecked(),
            mode=self.combo_mode.currentData(),
            bg_color=self._bg_color,
            dpi=dpi_tuple,
            allow_upscale=self.chk_allow_upscale.isChecked(),
        )

