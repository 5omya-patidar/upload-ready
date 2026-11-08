"""Settings panel for rendering PDF pages to images."""

from typing import Optional
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.models import PdfToImagesOptions


class PdfToImagesTab(QWidget):
    """Configuration controls for rendering PDF pages into images."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Page Selection Range
        self.txt_pages = QLineEdit("all")
        self.txt_pages.setPlaceholderText("e.g. 'all' or '1, 3-5, 8'")
        form.addRow("Pages to Render:", self.txt_pages)

        # Output Format
        self.combo_format = QComboBox()
        self.combo_format.addItems(["PNG", "JPEG"])
        form.addRow("Image Format:", self.combo_format)

        # Resolution DPI
        dpi_layout = QHBoxLayout()
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(50, 1200)
        self.spin_dpi.setValue(150)
        dpi_layout.addWidget(self.spin_dpi)
        dpi_layout.addWidget(QLabel("DPI (72 = screen, 150 = balanced, 300 = print)"))
        form.addRow("Render Resolution:", dpi_layout)

        # Password (optional)
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Leave blank if unencrypted")
        form.addRow("Decryption Password:", self.txt_password)

        # ZIP packaging option
        self.chk_zip = QCheckBox("Bundle exported pages into a single ZIP archive")
        self.chk_zip.setChecked(False)
        form.addRow("", self.chk_zip)

        layout.addLayout(form)

        # Explanatory notice
        self.lbl_notice = QLabel(
            "Note: This operation renders entire PDF page canvases into raster images at the specified DPI. "
            "It is distinct from extracting embedded photos. Passwords are used strictly in memory and are never saved or logged."
        )
        self.lbl_notice.setWordWrap(True)
        self.lbl_notice.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.lbl_notice)
        layout.addStretch()

    def get_options(self) -> PdfToImagesOptions:
        pwd = self.txt_password.text()
        return PdfToImagesOptions(
            page_range=self.txt_pages.text().strip() or "all",
            target_format=self.combo_format.currentText(),
            dpi=self.spin_dpi.value(),
            password=pwd if pwd else None,
            as_zip=self.chk_zip.isChecked(),
        )

