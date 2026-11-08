"""Settings panel for PDF size reduction and structural optimization."""

from typing import Optional
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.models import PdfReduceOptions


class PdfReduceTab(QWidget):
    """Configuration controls for PDF size reduction."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        # Mode Selection
        mode_box = QGroupBox("Optimization Strategy")
        mode_layout = QVBoxLayout(mode_box)

        self.radio_structural = QRadioButton(
            "Lossless Structural Optimization (Deduplicate objects, deflate streams, preserve vector text)"
        )
        self.radio_scanned = QRadioButton(
            "Scanned Document Downsampling (Rasterize pages at lower resolution for scanned paper docs)"
        )
        self.radio_structural.setChecked(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_structural, 0)
        self.mode_group.addButton(self.radio_scanned, 1)
        self.mode_group.idToggled.connect(self._on_mode_changed)

        mode_layout.addWidget(self.radio_structural)
        mode_layout.addWidget(self.radio_scanned)
        layout.addWidget(mode_box)

        # Settings Form
        form = QFormLayout()

        # Metadata removal
        self.chk_remove_meta = QCheckBox("Remove document metadata (Author, Producer, Timestamps)")
        self.chk_remove_meta.setChecked(True)
        form.addRow("", self.chk_remove_meta)

        # Scanned downsampling options
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(50, 300)
        self.spin_dpi.setValue(150)
        self.lbl_dpi = QLabel("Target Resolution (DPI):")
        form.addRow(self.lbl_dpi, self.spin_dpi)

        quality_layout = QHBoxLayout()
        self.slider_quality = QSlider()
        self.slider_quality.setRange(20, 95)
        self.slider_quality.setValue(75)
        self.lbl_quality_val = QLabel("75")
        self.slider_quality.valueChanged.connect(lambda v: self.lbl_quality_val.setText(str(v)))
        quality_layout.addWidget(self.slider_quality)
        quality_layout.addWidget(self.lbl_quality_val)
        self.lbl_quality = QLabel("Image Quality:")
        form.addRow(self.lbl_quality, quality_layout)

        # Decryption password (if encrypted)
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Leave blank if unencrypted")
        form.addRow("Decryption Password:", self.txt_password)

        layout.addLayout(form)

        # Digital signature warning box
        self.lbl_sig_warn = QLabel(
            "⚠️ Digital Signatures Notice: Structural rewriting or downsampling invalidates cryptographic "
            "digital signatures because file byte ranges are modified. Retaining signature form fields does not "
            "preserve cryptographic signature validity. Keep your original signed documents safe."
        )
        self.lbl_sig_warn.setWordWrap(True)
        self.lbl_sig_warn.setStyleSheet(
            "QLabel { background: #332b00; border: 1px solid #665500; border-radius: 4px; padding: 8px; color: #ffdd66; font-size: 11px; }"
        )
        layout.addWidget(self.lbl_sig_warn)

        # Scanned mode explanation
        self.lbl_scanned_notice = QLabel(
            "Note: Scanned downsampling rasterizes entire pages into compressed images. "
            "Use this mode only for scanned physical documents. It converts searchable text into raster pixels."
        )
        self.lbl_scanned_notice.setWordWrap(True)
        self.lbl_scanned_notice.setStyleSheet("color: #888; font-size: 11px; margin-top: 4px;")
        layout.addWidget(self.lbl_scanned_notice)

        layout.addStretch()
        self._update_visibility()

    def _on_mode_changed(self, btn_id: int, checked: bool) -> None:
        if checked:
            self._update_visibility()

    def _update_visibility(self) -> None:
        is_scanned = self.radio_scanned.isChecked()
        self.lbl_dpi.setVisible(is_scanned)
        self.spin_dpi.setVisible(is_scanned)
        self.lbl_quality.setVisible(is_scanned)
        self.slider_quality.setVisible(is_scanned)
        self.lbl_quality_val.setVisible(is_scanned)
        self.lbl_scanned_notice.setVisible(is_scanned)

    def get_password(self) -> Optional[str]:
        p = self.txt_password.text()
        return p if p else None

    def get_options(self) -> PdfReduceOptions:
        is_scanned = self.radio_scanned.isChecked()
        return PdfReduceOptions(
            structural_only=not is_scanned,
            downsample_scanned=is_scanned,
            target_dpi=self.spin_dpi.value(),
            image_quality=self.slider_quality.value(),
            remove_metadata=self.chk_remove_meta.isChecked(),
            password=self.get_password(),
        )

