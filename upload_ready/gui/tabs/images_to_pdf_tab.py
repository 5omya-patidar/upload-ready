"""Settings panel for assembling multiple images into a single PDF."""

from typing import Dict
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.models import ImagesToPdfOptions, PageOrientation


class ImagesToPdfTab(QWidget):
    """Configuration controls for images to PDF conversion."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Output filename
        self.txt_filename = QLineEdit("combined_document.pdf")
        form.addRow("Output PDF Name:", self.txt_filename)

        # Page Size
        self.combo_page_size = QComboBox()
        self.combo_page_size.addItem("A4 (210 x 297 mm)", "A4")
        self.combo_page_size.addItem("Letter (8.5 x 11 in)", "LETTER")
        self.combo_page_size.addItem("Legal (8.5 x 14 in)", "LEGAL")
        self.combo_page_size.addItem("Fit Image Size (Match image aspect and DPI)", "IMAGE_SIZE")
        form.addRow("Page Size:", self.combo_page_size)

        # Page Orientation
        self.combo_orientation = QComboBox()
        self.combo_orientation.addItem("Auto (Match each image aspect)", PageOrientation.AUTO)
        self.combo_orientation.addItem("Portrait", PageOrientation.PORTRAIT)
        self.combo_orientation.addItem("Landscape", PageOrientation.LANDSCAPE)
        form.addRow("Orientation:", self.combo_orientation)

        # Margins
        margin_layout = QHBoxLayout()
        self.spin_margin = QDoubleSpinBox()
        self.spin_margin.setRange(0.0, 144.0)
        self.spin_margin.setValue(18.0)  # 0.25 inch
        self.spin_margin.setSingleStep(6.0)
        margin_layout.addWidget(self.spin_margin)
        margin_layout.addWidget(QLabel("points (72 pt = 1 inch, 18 pt = 0.25 in)"))
        form.addRow("Page Margins:", margin_layout)

        layout.addLayout(form)

        # Information notice
        self.lbl_notice = QLabel(
            "Note: Images are centered and scaled proportionally to fit the usable page area. "
            "Upload Ready never distorts or silently crops your images. Use the Up/Down and Rotate buttons "
            "in the file list to reorder or reorient pages."
        )
        self.lbl_notice.setWordWrap(True)
        self.lbl_notice.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.lbl_notice)
        layout.addStretch()

    def get_output_name(self) -> str:
        name = self.txt_filename.text().strip()
        if not name:
            name = "combined_document.pdf"
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        return name

    def get_options(self, rotations: Dict[int, int]) -> ImagesToPdfOptions:
        return ImagesToPdfOptions(
            page_size=self.combo_page_size.currentData(),
            orientation=self.combo_orientation.currentData(),
            margin_pt=self.spin_margin.value(),
            image_rotations=rotations,
        )

