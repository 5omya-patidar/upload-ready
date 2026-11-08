"""Settings panel for Image Format Conversion."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.models import ConvertOptions


class ConvertTab(QWidget):
    """Configuration controls for format conversion."""

    def __init__(self):
        super().__init__()
        self._bg_color = (255, 255, 255)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Target Format
        self.combo_format = QComboBox()
        self.combo_format.addItems(["JPEG", "PNG", "WEBP"])
        self.combo_format.currentTextChanged.connect(self._on_format_changed)
        form.addRow("Target Format:", self.combo_format)

        # Quality slider
        quality_layout = QHBoxLayout()
        self.slider_quality = QSlider(Qt.Orientation.Horizontal)
        self.slider_quality.setRange(1, 100)
        self.slider_quality.setValue(85)
        self.lbl_quality_val = QLabel("85")
        self.slider_quality.valueChanged.connect(lambda v: self.lbl_quality_val.setText(str(v)))
        quality_layout.addWidget(self.slider_quality)
        quality_layout.addWidget(self.lbl_quality_val)
        self.row_quality_label = QLabel("Encoder Quality:")
        form.addRow(self.row_quality_label, quality_layout)

        # Lossless Toggle (PNG / WebP)
        self.chk_lossless = QCheckBox("Lossless Compression")
        self.chk_lossless.setChecked(True)
        self.chk_lossless.toggled.connect(self._on_lossless_toggled)
        form.addRow("", self.chk_lossless)

        # Multi-frame first frame only authorization
        self.chk_allow_frame = QCheckBox("Allow first frame only for multi-frame inputs")
        self.chk_allow_frame.setChecked(False)
        self.chk_allow_frame.setToolTip("Refuses to drop frames from animated GIFs or multi-page TIFFs unless checked.")
        form.addRow("", self.chk_allow_frame)

        # Background color for transparent images when converting to JPEG
        bg_layout = QHBoxLayout()
        self.btn_bg_color = QPushButton()
        self.btn_bg_color.setFixedSize(40, 24)
        self._update_color_button()
        self.btn_bg_color.clicked.connect(self._choose_color)
        self.lbl_bg_desc = QLabel("White (#FFFFFF)")
        bg_layout.addWidget(self.btn_bg_color)
        bg_layout.addWidget(self.lbl_bg_desc)
        bg_layout.addStretch()
        self.row_bg_label = QLabel("Background Color:")
        form.addRow(self.row_bg_label, bg_layout)

        layout.addLayout(form)

        # Information notice
        self.lbl_notice = QLabel(
            "Note: Converting transparent images into JPEG automatically composites alpha over the selected background."
        )
        self.lbl_notice.setWordWrap(True)
        self.lbl_notice.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.lbl_notice)
        layout.addStretch()

        self._on_format_changed("JPEG")

    def _on_format_changed(self, fmt: str) -> None:
        is_jpeg = fmt == "JPEG"
        is_png = fmt == "PNG"
        is_webp = fmt == "WEBP"

        self.btn_bg_color.setEnabled(is_jpeg)
        self.lbl_bg_desc.setEnabled(is_jpeg)
        self.row_bg_label.setEnabled(is_jpeg)

        self.chk_lossless.setVisible(not is_jpeg)
        self.slider_quality.setEnabled(not self.chk_lossless.isChecked() or is_jpeg)
        self.lbl_quality_val.setEnabled(not self.chk_lossless.isChecked() or is_jpeg)

    def _on_lossless_toggled(self, checked: bool) -> None:
        fmt = self.combo_format.currentText()
        if fmt != "JPEG":
            self.slider_quality.setEnabled(not checked)
            self.lbl_quality_val.setEnabled(not checked)

    def _choose_color(self) -> None:
        color = QColorDialog.getColor()
        if color.isValid():
            self._bg_color = (color.red(), color.green(), color.blue())
            hex_str = f"#{color.red():02X}{color.green():02X}{color.blue():02X}"
            self.lbl_bg_desc.setText(hex_str)
            self._update_color_button()

    def _update_color_button(self) -> None:
        r, g, b = self._bg_color
        self.btn_bg_color.setStyleSheet(f"background-color: rgb({r}, {g}, {b}); border: 1px solid #666;")

    def get_options(self) -> ConvertOptions:
        fmt = self.combo_format.currentText()
        is_lossless = self.chk_lossless.isChecked() if fmt != "JPEG" else False
        return ConvertOptions(
            target_format=fmt,
            bg_color=self._bg_color,
            quality=self.slider_quality.value(),
            lossless=is_lossless,
            allow_first_frame_only=self.chk_allow_frame.isChecked(),
        )

