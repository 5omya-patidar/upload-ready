"""Settings panel for Image Compression and Target File Size."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.constants import UnitStandard, parse_size_to_bytes
from upload_ready.core.models import CompressOptions


class CompressTab(QWidget):
    """Configuration controls for compression and target size searching."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        # Mode Selection
        mode_box = QGroupBox("Compression Mode")
        mode_layout = QVBoxLayout(mode_box)

        self.radio_lossless = QRadioButton("Lossless Optimization (Zero pixel delta, strips metadata, optimizes tables)")
        self.radio_quality = QRadioButton("Manual Lossy Quality Control")
        self.radio_target_max = QRadioButton("Target Maximum File Size (<= Limit)")
        self.radio_target_range = QRadioButton("Target File Size Range (Min - Max)")

        self.radio_target_max.setChecked(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_lossless, 0)
        self.mode_group.addButton(self.radio_quality, 1)
        self.mode_group.addButton(self.radio_target_max, 2)
        self.mode_group.addButton(self.radio_target_range, 3)
        self.mode_group.idToggled.connect(self._on_mode_changed)

        mode_layout.addWidget(self.radio_lossless)
        mode_layout.addWidget(self.radio_quality)
        mode_layout.addWidget(self.radio_target_max)
        mode_layout.addWidget(self.radio_target_range)
        layout.addWidget(mode_box)

        # Settings Form
        self.form_widget = QWidget()
        form = QFormLayout(self.form_widget)

        # Unit standard
        self.combo_unit = QComboBox()
        self.combo_unit.addItem("Decimal (1 KB = 1,000 bytes)", UnitStandard.DECIMAL)
        self.combo_unit.addItem("Binary (1 KiB = 1,024 bytes)", UnitStandard.BINARY)
        form.addRow("Unit Standard:", self.combo_unit)

        # Target Max Size input
        self.txt_max_size = QLineEdit("50 KB")
        self.txt_max_size.setPlaceholderText("e.g. 50 KB, 1.5 MB, 200 KiB")
        self.lbl_max_size = QLabel("Maximum Size:")
        form.addRow(self.lbl_max_size, self.txt_max_size)

        # Target Min Size input (for range)
        self.txt_min_size = QLineEdit("20 KB")
        self.txt_min_size.setPlaceholderText("e.g. 20 KB, 100 KiB")
        self.lbl_min_size = QLabel("Minimum Size:")
        form.addRow(self.lbl_min_size, self.txt_min_size)

        # Allow lossy palette quantization for PNG
        self.chk_allow_quantization = QCheckBox("Allow lossy palette quantization for PNG target sizing")
        self.chk_allow_quantization.setChecked(False)
        self.chk_allow_quantization.setToolTip(
            "When unchecked, PNG targets will only use lossless Deflate. If target cannot be met, an honest limit report is returned."
        )
        form.addRow("", self.chk_allow_quantization)

        # Manual Quality Slider
        quality_layout = QHBoxLayout()
        self.slider_quality = QSlider(Qt.Orientation.Horizontal)
        self.slider_quality.setRange(1, 100)
        self.slider_quality.setValue(80)
        self.lbl_quality_val = QLabel("80")
        self.slider_quality.valueChanged.connect(lambda v: self.lbl_quality_val.setText(str(v)))
        quality_layout.addWidget(self.slider_quality)
        quality_layout.addWidget(self.lbl_quality_val)
        self.lbl_quality_row = QLabel("Encoder Setting:")
        form.addRow(self.lbl_quality_row, quality_layout)

        layout.addWidget(self.form_widget)

        # Notice on honest limits
        self.lbl_notice = QLabel(
            "Note: Target file size searches bounded encoder candidates generated directly from the original source. "
            "Upload Ready never adds dummy padding bytes to artificially satisfy a minimum size, and reports unmet constraints honestly."
        )
        self.lbl_notice.setWordWrap(True)
        self.lbl_notice.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.lbl_notice)
        layout.addStretch()

        self._update_visibility()

    def _on_mode_changed(self, btn_id: int, checked: bool) -> None:
        if checked:
            self._update_visibility()

    def _update_visibility(self) -> None:
        is_lossless = self.radio_lossless.isChecked()
        is_quality = self.radio_quality.isChecked()
        is_max = self.radio_target_max.isChecked()
        is_range = self.radio_target_range.isChecked()

        self.slider_quality.setVisible(is_quality)
        self.lbl_quality_val.setVisible(is_quality)
        self.lbl_quality_row.setVisible(is_quality)

        self.lbl_max_size.setVisible(is_max or is_range)
        self.txt_max_size.setVisible(is_max or is_range)

        self.lbl_min_size.setVisible(is_range)
        self.txt_min_size.setVisible(is_range)

        self.combo_unit.setVisible(is_max or is_range)
        self.chk_allow_quantization.setVisible(is_max or is_range)

    def get_options(self) -> CompressOptions:
        unit_std = self.combo_unit.currentData()
        allow_quant = self.chk_allow_quantization.isChecked()

        if self.radio_lossless.isChecked():
            return CompressOptions(is_lossless=True, unit_standard=unit_std)

        if self.radio_quality.isChecked():
            return CompressOptions(
                is_lossless=False,
                quality=self.slider_quality.value(),
                unit_standard=unit_std,
                allow_palette_quantization=allow_quant,
            )

        if self.radio_target_max.isChecked():
            max_bytes = parse_size_to_bytes(self.txt_max_size.text(), unit_std)
            return CompressOptions(
                is_lossless=False,
                max_bytes=max_bytes,
                unit_standard=unit_std,
                allow_palette_quantization=allow_quant,
            )

        if self.radio_target_range.isChecked():
            max_bytes = parse_size_to_bytes(self.txt_max_size.text(), unit_std)
            min_bytes = parse_size_to_bytes(self.txt_min_size.text(), unit_std)
            return CompressOptions(
                is_lossless=False,
                max_bytes=max_bytes,
                min_bytes=min_bytes,
                unit_standard=unit_std,
                allow_palette_quantization=allow_quant,
            )

        return CompressOptions()

