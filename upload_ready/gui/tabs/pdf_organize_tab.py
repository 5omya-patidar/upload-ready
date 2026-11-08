"""Interactive PDF Page Organiser panel."""

import io
from pathlib import Path
from typing import Dict, List, Optional
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
import pypdf

from upload_ready.core.pdf_organizer import OrganizedPage, render_page_thumbnail


class PdfOrganizeTab(QWidget):
    """
    Visual, interactive PDF page organiser:
    - Page thumbnails and document/page identification
    - Visual reordering (move left/right)
    - 90-degree incremental rotation
    - Duplicating and removing pages
    - Extracting selected pages
    - Undo/Reset to original page sequence
    """

    def __init__(self):
        super().__init__()
        self._initial_pages: List[OrganizedPage] = []
        self._passwords: Dict[str, str] = {}

        layout = QVBoxLayout(self)

        # Header and Load Button
        top_bar = QHBoxLayout()
        self.lbl_info = QLabel("<b>Visual Page Organiser:</b> Reorder, rotate, duplicate, or remove pages.")
        top_bar.addWidget(self.lbl_info)
        top_bar.addStretch()

        self.btn_load_file = QPushButton("📂 Open PDF...")
        self.btn_load_file.clicked.connect(self._on_open_pdf)
        top_bar.addWidget(self.btn_load_file)

        self.btn_reload = QPushButton("🔄 Reset / Reload")
        self.btn_reload.setToolTip("Reset page arrangement back to the initial state")
        self.btn_reload.clicked.connect(self.reset_to_initial)
        top_bar.addWidget(self.btn_reload)
        layout.addLayout(top_bar)

        # Visual Page Thumbnail List
        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(100, 130))
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setSpacing(10)
        self.list_widget.setStyleSheet(
            "QListWidget { background-color: #1e1e1e; border: 1px solid #333; border-radius: 4px; padding: 6px; } "
            "QListWidget::item { background-color: #2d2d2d; border-radius: 4px; padding: 4px; color: #eee; } "
            "QListWidget::item:selected { background-color: #094771; border: 1px solid #007acc; }"
        )
        layout.addWidget(self.list_widget, stretch=1)

        # Action Toolbar (Reorder, Rotate, Duplicate, Remove, Extract)
        act_bar = QHBoxLayout()

        self.btn_move_left = QPushButton("◀ Move Left")
        self.btn_move_left.clicked.connect(self._on_move_left)
        act_bar.addWidget(self.btn_move_left)

        self.btn_move_right = QPushButton("Move Right ▶")
        self.btn_move_right.clicked.connect(self._on_move_right)
        act_bar.addWidget(self.btn_move_right)

        self.btn_rotate = QPushButton("↻ Rotate 90°")
        self.btn_rotate.setToolTip("Rotate selected pages 90 degrees clockwise")
        self.btn_rotate.clicked.connect(self._on_rotate_selected)
        act_bar.addWidget(self.btn_rotate)

        self.btn_duplicate = QPushButton("📋 Duplicate")
        self.btn_duplicate.setToolTip("Duplicate selected page")
        self.btn_duplicate.clicked.connect(self._on_duplicate_selected)
        act_bar.addWidget(self.btn_duplicate)

        self.btn_remove = QPushButton("🗑️ Remove")
        self.btn_remove.setToolTip("Remove selected page from output document")
        self.btn_remove.clicked.connect(self._on_remove_selected)
        act_bar.addWidget(self.btn_remove)

        self.btn_extract = QPushButton("✂️ Extract Selected")
        self.btn_extract.setToolTip("Keep only the selected pages and discard others")
        self.btn_extract.clicked.connect(self._on_extract_selected)
        act_bar.addWidget(self.btn_extract)

        layout.addLayout(act_bar)

        # Settings Form
        form = QFormLayout()

        self.txt_output_name = QLineEdit("organized_document.pdf")
        form.addRow("Output Filename:", self.txt_output_name)

        # Password
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Decryption password if encrypted")
        form.addRow("PDF Password:", self.txt_password)

        warning_lbl = QLabel(
            "<b>Honest Structure Preservation:</b> Preserves searchable text, bookmarks, form fields, and attachments.<br>"
            "Digital cryptographic signatures are invalidated if pages are rearranged."
        )
        warning_lbl.setStyleSheet("color: #bbb; font-size: 11px;")
        form.addRow("", warning_lbl)

        layout.addLayout(form)

    def load_documents(self, pdf_paths: List[Path], password: Optional[str] = None) -> None:
        """Load pages from one or more PDF documents into visual organiser."""
        self._initial_pages.clear()
        self.list_widget.clear()

        for pdf_p in pdf_paths:
            if not pdf_p.exists() or pdf_p.suffix.lower() != ".pdf":
                continue

            try:
                r = pypdf.PdfReader(pdf_p)
                if r.is_encrypted:
                    pw = password if password is not None else (self.txt_password.text() or None)
                    if not pw:
                        continue
                    if r.decrypt(pw) == 0:
                        continue
                    self._passwords[str(pdf_p.resolve())] = pw

                for i in range(len(r.pages)):
                    baseline_desc = OrganizedPage(
                        source_path=pdf_p,
                        page_index=i,
                        rotation_offset=0,
                        source_label=f"{pdf_p.name} (p.{i+1})",
                    )
                    self._initial_pages.append(baseline_desc)

                    ui_desc = OrganizedPage(
                        source_path=pdf_p,
                        page_index=i,
                        rotation_offset=0,
                        source_label=f"{pdf_p.name} (p.{i+1})",
                    )
                    self._add_page_item(ui_desc)

            except Exception as exc:
                QMessageBox.warning(self, "Load Error", f"Could not load {pdf_p.name}: {exc}")

        self.lbl_info.setText(f"<b>Visual Page Organiser:</b> {self.list_widget.count()} page(s) loaded.")

    def _on_open_pdf(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select PDF Documents", "", "PDF Files (*.pdf)")
        if files:
            paths = [Path(f) for f in files]
            self.load_documents(paths)

    def _add_page_item(self, page_desc: OrganizedPage) -> None:
        try:
            pw = self._passwords.get(str(page_desc.source_path.resolve()))
            pil_img = render_page_thumbnail(page_desc.source_path, page_desc.page_index, max_dim=100, password=pw)
            if page_desc.rotation_offset % 360 != 0:
                pil_img = pil_img.rotate(-page_desc.rotation_offset % 360, expand=True)

            # Convert PIL to QPixmap
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            buf.seek(0)
            qimg = QImage.fromData(buf.getvalue())
            pix = QPixmap.fromImage(qimg)
            icon = QIcon(pix)
        except Exception:
            icon = QIcon()

        label = f"{page_desc.source_label}\n({page_desc.rotation_offset % 360}°)"
        item = QListWidgetItem(icon, label)
        item.setData(Qt.ItemDataRole.UserRole, page_desc)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_widget.addItem(item)

    def _on_move_left(self) -> None:
        row = self.list_widget.currentRow()
        if row > 0:
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row - 1, item)
            self.list_widget.setCurrentRow(row - 1)

    def _on_move_right(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0 and row < self.list_widget.count() - 1:
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row + 1, item)
            self.list_widget.setCurrentRow(row + 1)

    def _on_rotate_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            page_desc: OrganizedPage = item.data(Qt.ItemDataRole.UserRole)
            page_desc.rotation_offset = (page_desc.rotation_offset + 90) % 360
            item.setData(Qt.ItemDataRole.UserRole, page_desc)
            # Update label and icon
            try:
                pw = self._passwords.get(str(page_desc.source_path.resolve()))
                pil_img = render_page_thumbnail(page_desc.source_path, page_desc.page_index, max_dim=100, password=pw)
                if page_desc.rotation_offset != 0:
                    pil_img = pil_img.rotate(-page_desc.rotation_offset, expand=True)
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                buf.seek(0)
                qimg = QImage.fromData(buf.getvalue())
                item.setIcon(QIcon(QPixmap.fromImage(qimg)))
            except Exception:
                pass
            item.setText(f"{page_desc.source_label}\n({page_desc.rotation_offset}°)")

    def _on_duplicate_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            orig_item = self.list_widget.item(row)
            orig_page: OrganizedPage = orig_item.data(Qt.ItemDataRole.UserRole)
            dup_page = OrganizedPage(
                source_path=orig_page.source_path,
                page_index=orig_page.page_index,
                rotation_offset=orig_page.rotation_offset,
                source_label=f"{orig_page.source_label} (copy)",
            )
            self._add_page_item(dup_page)
            # Move right after current row
            new_item = self.list_widget.takeItem(self.list_widget.count() - 1)
            self.list_widget.insertItem(row + 1, new_item)
            self.list_widget.setCurrentRow(row + 1)

    def _on_remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)
        self.lbl_info.setText(f"<b>Visual Page Organiser:</b> {self.list_widget.count()} page(s) remaining.")

    def _on_extract_selected(self) -> None:
        selected_pages = [item.data(Qt.ItemDataRole.UserRole) for item in self.list_widget.selectedItems()]
        if not selected_pages:
            QMessageBox.information(self, "No Selection", "Please select the pages you wish to extract.")
            return
        self.list_widget.clear()
        for p in selected_pages:
            self._add_page_item(p)
        self.lbl_info.setText(f"<b>Visual Page Organiser:</b> {self.list_widget.count()} extracted page(s).")

    def reset_to_initial(self) -> None:
        """Reset page sequence back to initial loaded state."""
        self.list_widget.clear()
        for p in self._initial_pages:
            copy_p = OrganizedPage(
                source_path=p.source_path,
                page_index=p.page_index,
                rotation_offset=0,
                source_label=p.source_label,
            )
            self._add_page_item(copy_p)
        self.lbl_info.setText(f"<b>Visual Page Organiser:</b> Reset to {len(self._initial_pages)} initial pages.")

    def get_pages(self) -> List[OrganizedPage]:
        pages = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            pages.append(item.data(Qt.ItemDataRole.UserRole))
        return pages

    def get_output_name(self) -> str:
        name = self.txt_output_name.text().strip() or "organized_document.pdf"
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        return name

    def get_passwords(self) -> Dict[str, str]:
        """Return passwords map for all documents, preserving exact whitespace."""
        pw_entered = self.txt_password.text()
        passwords = dict(self._passwords)
        if pw_entered:
            for p in self.get_pages():
                passwords[str(p.source_path.resolve())] = pw_entered
                passwords[p.source_path.name] = pw_entered
        return passwords

    def get_options(self):
        pages = self.get_pages()
        if not pages:
            raise ValueError("No pages available in organiser. Open a PDF first.")
        return pages
