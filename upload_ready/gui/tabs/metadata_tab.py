"""Interactive Metadata Inspection and Selective Scrubbing panel."""

from pathlib import Path
from typing import Optional, Set
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.metadata import DetectedMetadata, inspect_metadata


class MetadataTab(QWidget):
    """
    Configuration and inspection panel for metadata:
    - Displays detected metadata tags in a structured inspection table
    - Allows users to selectively check/uncheck removal categories
    - Emphasizes explicit distinction between metadata removal and visual redaction
    """

    def __init__(self):
        super().__init__()
        self._current_file: Optional[Path] = None

        layout = QVBoxLayout(self)

        # Inspection Section
        inspect_group = QGroupBox("Detected Metadata in Selected File")
        inspect_layout = QVBoxLayout(inspect_group)

        file_bar = QHBoxLayout()
        self.lbl_inspect_target = QLabel("No file selected for inspection.")
        self.lbl_inspect_target.setStyleSheet("font-weight: bold; color: #4ec9b0;")
        file_bar.addWidget(self.lbl_inspect_target, stretch=1)

        self.btn_browse_inspect = QPushButton("📂 Inspect File...")
        self.btn_browse_inspect.clicked.connect(self._on_browse_file)
        file_bar.addWidget(self.btn_browse_inspect)
        inspect_layout.addLayout(file_bar)

        # Table of detected metadata tags
        self.table_meta = QTableWidget(0, 2)
        self.table_meta.setHorizontalHeaderLabels(["Metadata Category", "Detected Content / Status"])
        self.table_meta.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_meta.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_meta.setMaximumHeight(150)
        self.table_meta.setStyleSheet(
            "QTableWidget { background-color: #1e1e1e; border: 1px solid #333; gridline-color: #333; } "
            "QHeaderView::section { background-color: #2d2d2d; color: #eee; font-weight: bold; padding: 4px; border: 1px solid #333; }"
        )
        inspect_layout.addWidget(self.table_meta)
        layout.addWidget(inspect_group)

        # Removal Categories Form
        form = QFormLayout()

        self.chk_all = QCheckBox("Select All Removal Categories")
        self.chk_all.setChecked(True)
        self.chk_all.toggled.connect(self._on_select_all_toggled)
        form.addRow("Removal Options:", self.chk_all)

        self.chk_exif = QCheckBox("Camera & Exposure EXIF Tags (e.g. Make, Model, DateTime, FocalLength)")
        self.chk_exif.setChecked(True)
        form.addRow("", self.chk_exif)

        self.chk_gps = QCheckBox("GPS Location Coordinates (Sensitive Location Privacy)")
        self.chk_gps.setChecked(True)
        self.chk_gps.setStyleSheet("color: #e06c75; font-weight: bold;")
        form.addRow("", self.chk_gps)

        self.chk_xmp = QCheckBox("XMP Metadata Packets")
        self.chk_xmp.setChecked(True)
        form.addRow("", self.chk_xmp)

        self.chk_comments = QCheckBox("User Comments & Software Watermarks / Text Chunks")
        self.chk_comments.setChecked(True)
        form.addRow("", self.chk_comments)

        self.chk_pdf_docinfo = QCheckBox("PDF Document Information (Title, Author, Producer, CreationDate)")
        self.chk_pdf_docinfo.setChecked(True)
        form.addRow("", self.chk_pdf_docinfo)

        self.chk_pdf_xmp = QCheckBox("PDF Catalog XMP Metadata Stream")
        self.chk_pdf_xmp.setChecked(True)
        form.addRow("", self.chk_pdf_xmp)

        # PDF Password
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Decryption password if encrypted")
        form.addRow("PDF Password:", self.txt_password)

        # Explicit Redaction Notice
        info_lbl = QLabel(
            "<b>Important Distinction: Metadata Removal vs. Redaction:</b><br>"
            "• <b>Metadata removal</b> strips hidden data headers (EXIF, GPS, author info, comments).<br>"
            "• Metadata removal does <b>NOT</b> redact or obscure visible text, images, or faces.<br>"
            "• EXIF orientation is normalized before tag removal so visual orientation is preserved.<br>"
            "• Compatible ICC color profiles are retained to avoid visual color shifts."
        )
        info_lbl.setStyleSheet("color: #bbb; font-size: 11px; margin-top: 8px;")
        form.addRow("", info_lbl)

        layout.addLayout(form)
        layout.addStretch()

    def inspect_file(self, file_path: Path, password: Optional[str] = None) -> None:
        """Inspect and display metadata of the given file."""
        self._current_file = file_path
        self.lbl_inspect_target.setText(f"Inspected: {file_path.name}")
        self.table_meta.setRowCount(0)

        try:
            pw = password if password is not None else (self.txt_password.text() or None)
            meta = inspect_metadata(file_path, password=pw)

            rows = [
                ("File Format", meta.file_type),
                ("EXIF Metadata", f"{meta.exif_tag_count} tag(s) detected" if meta.has_exif else "None"),
                ("GPS Location Coordinates", "⚠ SENSITIVE: GPS tags found!" if meta.has_gps else "None detected"),
                ("XMP Data Packet", "Present" if meta.has_xmp else "None"),
                ("Comments / Software Tags", "Present" if meta.has_comments else "None"),
                ("ICC Color Profile", f"Present ({meta.icc_profile_desc or 'Embedded'})" if meta.has_icc_profile else "None"),
            ]
            if meta.file_type == "PDF":
                rows.append(("PDF Document Info", ", ".join(meta.doc_info_keys) if meta.doc_info_keys else "Empty"))

            self.table_meta.setRowCount(len(rows))
            for i, (cat, val) in enumerate(rows):
                cat_item = QTableWidgetItem(cat)
                val_item = QTableWidgetItem(val)
                if "SENSITIVE" in val:
                    val_item.setForeground(Qt.GlobalColor.red)
                self.table_meta.setItem(i, 0, cat_item)
                self.table_meta.setItem(i, 1, val_item)

        except Exception as exc:
            self.table_meta.setRowCount(1)
            self.table_meta.setItem(0, 0, QTableWidgetItem("Error"))
            self.table_meta.setItem(0, 1, QTableWidgetItem(str(exc)))

    def _on_browse_file(self) -> None:
        file, _ = QFileDialog.getOpenFileName(self, "Select Image or PDF to Inspect", "", "Images and PDFs (*.png *.jpg *.jpeg *.webp *.pdf)")
        if file:
            self.inspect_file(Path(file))

    def _on_select_all_toggled(self, checked: bool) -> None:
        self.chk_exif.setChecked(checked)
        self.chk_gps.setChecked(checked)
        self.chk_xmp.setChecked(checked)
        self.chk_comments.setChecked(checked)
        self.chk_pdf_docinfo.setChecked(checked)
        self.chk_pdf_xmp.setChecked(checked)

    def get_selected_categories(self) -> Set[str]:
        cats = set()
        if self.chk_exif.isChecked():
            cats.add("exif")
        if self.chk_gps.isChecked():
            cats.add("gps")
        if self.chk_xmp.isChecked():
            cats.add("xmp")
        if self.chk_comments.isChecked():
            cats.add("comments")
        if self.chk_pdf_docinfo.isChecked():
            cats.add("pdf_docinfo")
        if self.chk_pdf_xmp.isChecked():
            cats.add("pdf_xmp")
        return cats

    def get_options(self):
        class MetadataOptions:
            def __init__(self, categories: Set[str], password: str | None):
                self.categories = categories
                self.password = password

        return MetadataOptions(
            categories=self.get_selected_categories(),
            password=self.txt_password.text() or None,
        )
