"""File queue table supporting drag-and-drop, reordering, rotation, and status badges."""

from pathlib import Path
from typing import Dict, List, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.constants import bytes_to_human
from upload_ready.core.safety import sniff_content_type


class FileQueueWidget(QWidget):
    """File queue manager with drag-and-drop and reordering."""

    file_selected = Signal(Path)       # When user clicks a file in the queue to preview
    queue_changed = Signal(int)        # Emits number of items in the queue

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self._files: List[Path] = []
        self._rotations: Dict[int, int] = {}  # index -> rotation in degrees

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header and Actions
        header_layout = QHBoxLayout()
        title = QLabel("1. Add Files")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.btn_add_files = QPushButton("Choose Files...")
        self.btn_add_files.setToolTip("Add images or PDF files to the queue")
        self.btn_add_files.clicked.connect(self._on_choose_files_dialog)
        header_layout.addWidget(self.btn_add_files)

        self.btn_move_up = QPushButton("▲ Up")
        self.btn_move_up.setToolTip("Move selected file up (for PDF order)")
        self.btn_move_up.clicked.connect(self._move_up)
        header_layout.addWidget(self.btn_move_up)

        self.btn_move_down = QPushButton("▼ Down")
        self.btn_move_down.setToolTip("Move selected file down")
        self.btn_move_down.clicked.connect(self._move_down)
        header_layout.addWidget(self.btn_move_down)

        self.btn_rotate = QPushButton("↻ Rotate")
        self.btn_rotate.setToolTip("Rotate selected image 90° clockwise")
        self.btn_rotate.clicked.connect(self._rotate_selected)
        header_layout.addWidget(self.btn_rotate)

        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self._remove_selected)
        header_layout.addWidget(self.btn_remove)

        self.btn_clear = QPushButton("Clear All")
        self.btn_clear.clicked.connect(self.clear_all)
        header_layout.addWidget(self.btn_clear)

        layout.addLayout(header_layout)

        # Drag and drop hint label
        self.drop_hint = QLabel("Drag & drop files here, or click 'Choose Files...'")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setStyleSheet(
            "QLabel { border: 2px dashed #444; border-radius: 8px; padding: 12px; color: #888; background: #222; font-size: 12px; }"
        )
        layout.addWidget(self.drop_hint)

        # Table queue
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["#", "Filename", "Format", "Size", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table, stretch=1)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = []
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_file():
                paths.append(p)
        if paths:
            self.add_files(paths)

    def _on_choose_files_dialog(self) -> None:
        filters = "Supported Files (*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff *.pdf);;All Files (*.*)"
        file_strs, _ = QFileDialog.getOpenFileNames(self, "Choose files", "", filters)
        if file_strs:
            self.add_files([Path(f) for f in file_strs])

    def add_files(self, paths: List[Path]) -> None:
        for p in paths:
            if p not in self._files and p.is_file():
                self._files.append(p)
        self._refresh_table()
        self.queue_changed.emit(len(self._files))
        if self._files:
            self.drop_hint.setVisible(False)

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self._files))
        for row, path in enumerate(self._files):
            # 0: Order index
            rot = self._rotations.get(row, 0)
            rot_str = f" ({rot}°)" if rot else ""
            idx_item = QTableWidgetItem(f"{row + 1}{rot_str}")
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, idx_item)

            # 1: Filename
            name_item = QTableWidgetItem(path.name)
            self.table.setItem(row, 1, name_item)

            # 2: Format
            try:
                fmt = sniff_content_type(path)
            except Exception:
                fmt = path.suffix.upper().lstrip(".") or "UNKNOWN"
            fmt_item = QTableWidgetItem(fmt)
            fmt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, fmt_item)

            # 3: Size
            try:
                size_str = bytes_to_human(path.stat().st_size)
            except Exception:
                size_str = "-"
            size_item = QTableWidgetItem(size_str)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 3, size_item)

            # 4: Status badge
            status_item = QTableWidgetItem("Ready")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, status_item)

    def set_file_status(self, index: int, status_text: str, is_error: bool = False) -> None:
        if 0 <= index < self.table.rowCount():
            item = self.table.item(index, 4)
            if item:
                item.setText(status_text)
                if is_error:
                    item.setForeground(Qt.GlobalColor.red)
                else:
                    item.setForeground(Qt.GlobalColor.green)

    def _on_selection_changed(self) -> None:
        selected_rows = self.table.selectedIndexes()
        if selected_rows:
            row = selected_rows[0].row()
            if 0 <= row < len(self._files):
                self.file_selected.emit(self._files[row])

    def _move_up(self) -> None:
        row = self.table.currentRow()
        if row > 0:
            self._files[row], self._files[row - 1] = self._files[row - 1], self._files[row]
            # Swap rotations
            r1 = self._rotations.get(row, 0)
            r2 = self._rotations.get(row - 1, 0)
            self._rotations[row] = r2
            self._rotations[row - 1] = r1
            self._refresh_table()
            self.table.selectRow(row - 1)

    def _move_down(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._files) - 1:
            self._files[row], self._files[row + 1] = self._files[row + 1], self._files[row]
            # Swap rotations
            r1 = self._rotations.get(row, 0)
            r2 = self._rotations.get(row + 1, 0)
            self._rotations[row] = r2
            self._rotations[row + 1] = r1
            self._refresh_table()
            self.table.selectRow(row + 1)

    def _rotate_selected(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._files):
            current = self._rotations.get(row, 0)
            self._rotations[row] = (current + 90) % 360
            self._refresh_table()
            self.table.selectRow(row)

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._files):
            del self._files[row]
            # Reindex rotations so remaining files keep their respective rotations
            new_rotations = {}
            for idx, r in self._rotations.items():
                if idx < row:
                    new_rotations[idx] = r
                elif idx > row:
                    new_rotations[idx - 1] = r
            self._rotations = new_rotations
            self._refresh_table()
            self.queue_changed.emit(len(self._files))
            if not self._files:
                self.drop_hint.setVisible(True)

    def clear_all(self) -> None:
        self._files.clear()
        self._rotations.clear()
        self._refresh_table()
        self.drop_hint.setVisible(True)
        self.queue_changed.emit(0)

    def get_files(self) -> List[Path]:
        return list(self._files)

    def get_rotations(self) -> Dict[int, int]:
        return dict(self._rotations)

