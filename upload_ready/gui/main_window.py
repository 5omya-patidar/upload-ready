"""Main Application Window for Upload Ready."""

from pathlib import Path
from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.constants import bytes_to_human
from upload_ready.core.models import JobResult, OperationType
from upload_ready.gui.file_queue import FileQueueWidget
from upload_ready.gui.preview_widget import PreviewWidget
from upload_ready.gui.report_dialog import ValidationReportDialog
from upload_ready.gui.tabs import (
    CompressTab,
    ConvertTab,
    IdLayoutTab,
    ImagesToPdfTab,
    MetadataTab,
    PdfOrganizeTab,
    PdfReduceTab,
    PdfToImagesTab,
    PhotoSheetTab,
    PresetTab,
    ResizeTab,
    WatermarkTab,
)
from upload_ready.gui.worker import BatchProcessingWorker


class MainWindow(QMainWindow):
    """Main window implementing the 5-step local-only preparation workflow."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Upload Ready - Local-Only Image & PDF Preparation")
        self.resize(1180, 780)

        self._active_worker: Optional[BatchProcessingWorker] = None
        self._last_batch_results: List[JobResult] = []
        self._is_closing: bool = False
        self.auto_show_report_dialog: bool = True

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Top Main Splitter (Left: File Queue & Operations | Right: Live Preview)
        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Column: File Queue + Operation Tabs
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # Step 1: File Queue
        self.file_queue = FileQueueWidget()
        self.file_queue.file_selected.connect(self._on_file_selected_in_queue)
        self.file_queue.queue_changed.connect(self._on_queue_count_changed)
        left_layout.addWidget(self.file_queue, stretch=3)

        # Step 2 & 3: Operations & Requirements Tabs
        step2_header = QLabel("2. Select Operation & Requirements")
        step2_header.setStyleSheet("font-weight: bold; font-size: 14px; margin-top: 6px;")
        left_layout.addWidget(step2_header)

        self.tab_widget = QTabWidget()
        self.tab_convert = ConvertTab()
        self.tab_resize = ResizeTab()
        self.tab_compress = CompressTab()
        self.tab_i2p = ImagesToPdfTab()
        self.tab_p2i = PdfToImagesTab()
        self.tab_reduce = PdfReduceTab()
        self.tab_presets = PresetTab()
        self.tab_organize = PdfOrganizeTab()
        self.tab_metadata = MetadataTab()
        self.tab_photo_sheet = PhotoSheetTab()
        self.tab_watermark = WatermarkTab()
        self.tab_id_layout = IdLayoutTab()

        self.tab_widget.addTab(self.tab_convert, "Convert")
        self.tab_widget.addTab(self.tab_resize, "Resize")
        self.tab_widget.addTab(self.tab_compress, "Compress")
        self.tab_widget.addTab(self.tab_i2p, "Images to PDF")
        self.tab_widget.addTab(self.tab_p2i, "PDF to Images")
        self.tab_widget.addTab(self.tab_reduce, "PDF Reduce")
        self.tab_widget.addTab(self.tab_presets, "Presets")
        self.tab_widget.addTab(self.tab_organize, "PDF Organiser")
        self.tab_widget.addTab(self.tab_metadata, "Metadata")
        self.tab_widget.addTab(self.tab_photo_sheet, "Photo Sheet")
        self.tab_widget.addTab(self.tab_watermark, "Watermark")
        self.tab_widget.addTab(self.tab_id_layout, "ID Layout")
        left_layout.addWidget(self.tab_widget, stretch=4)

        top_splitter.addWidget(left_widget)

        # Right Column: Step 4 - Preview & Comparison
        self.preview_panel = PreviewWidget()
        top_splitter.addWidget(self.preview_panel)
        top_splitter.setSizes([560, 600])

        main_layout.addWidget(top_splitter, stretch=1)

        # Step 5: Save & Action Bar (Bottom Pane)
        action_frame = QFrame()
        action_frame.setFrameShape(QFrame.Shape.StyledPanel)
        action_frame.setStyleSheet("QFrame { background-color: #252526; border-radius: 6px; padding: 6px; }")
        action_layout = QVBoxLayout(action_frame)
        action_layout.setContentsMargins(10, 8, 10, 8)

        # Directory selection line
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("<b>Save to Folder:</b>"))
        default_save_dir = Path.home() / "Documents" / "UploadReady_Outputs"
        self.txt_out_dir = QLineEdit(str(default_save_dir))
        dir_layout.addWidget(self.txt_out_dir, stretch=1)

        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._on_browse_output_dir)
        dir_layout.addWidget(self.btn_browse)

        self.chk_overwrite = QCheckBox("Allow overwrite existing")
        self.chk_overwrite.setChecked(False)
        self.chk_overwrite.setToolTip("If unchecked, files are saved with non-colliding suffixes (_1, _2).")
        dir_layout.addWidget(self.chk_overwrite)
        action_layout.addLayout(dir_layout)

        # Controls & Progress line
        ctrl_layout = QHBoxLayout()

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        ctrl_layout.addWidget(self.progress_bar, stretch=1)

        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet("color: #aaa;")
        ctrl_layout.addWidget(self.lbl_status)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        ctrl_layout.addWidget(self.btn_cancel)

        self.btn_report = QPushButton("Validation Report")
        self.btn_report.setEnabled(False)
        self.btn_report.clicked.connect(self._show_validation_report)
        ctrl_layout.addWidget(self.btn_report)

        self.btn_process = QPushButton("Save & Process Batch")
        self.btn_process.setStyleSheet(
            "QPushButton { background-color: #0e639c; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #1177bb; }"
        )
        self.btn_process.clicked.connect(self._on_process_clicked)
        ctrl_layout.addWidget(self.btn_process)

        action_layout.addLayout(ctrl_layout)
        main_layout.addWidget(action_frame)

    def _on_browse_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.txt_out_dir.text())
        if folder:
            self.txt_out_dir.setText(folder)

    def _on_file_selected_in_queue(self, path: Path) -> None:
        self.preview_panel.load_original(path)
        self.tab_metadata.inspect_file(path)
        self.tab_photo_sheet.set_source_image(path)
        self.tab_watermark.set_preview_file(path)

    def _on_queue_count_changed(self, count: int) -> None:
        self.lbl_status.setText(f"{count} file(s) in queue")
        files = self.file_queue.get_files()
        if count == 0:
            self._last_batch_results.clear()
            self.preview_panel.clear()
            self.btn_report.setEnabled(False)
        else:
            self.tab_watermark.set_preview_file(files[0])
            self.tab_photo_sheet.set_source_image(files[0])
            pdf_files = [f for f in files if f.suffix.lower() == ".pdf"]
            if pdf_files and self.tab_organize.list_widget.count() == 0:
                self.tab_organize.load_documents(pdf_files)
            if len(files) >= 2 and not self.tab_id_layout.txt_front.text():
                self.tab_id_layout.set_front_back_from_queue(files)

    def _get_current_operation_and_options(self):
        idx = self.tab_widget.currentIndex()
        if idx == 0:
            return OperationType.CONVERT, self.tab_convert.get_options()
        elif idx == 1:
            return OperationType.RESIZE, self.tab_resize.get_options()
        elif idx == 2:
            return OperationType.COMPRESS, self.tab_compress.get_options()
        elif idx == 3:
            rotations = self.file_queue.get_rotations()
            return OperationType.IMAGES_TO_PDF, self.tab_i2p.get_options(rotations)
        elif idx == 4:
            return OperationType.PDF_TO_IMAGES, self.tab_p2i.get_options()
        elif idx == 5:
            return OperationType.PDF_REDUCE, self.tab_reduce.get_options()
        elif idx == 6:
            return OperationType.PRESET, self.tab_presets.get_options()
        elif idx == 7:
            return OperationType.PDF_ORGANIZE, self.tab_organize.get_options()
        elif idx == 8:
            return OperationType.METADATA, self.tab_metadata.get_options()
        elif idx == 9:
            return OperationType.PHOTO_SHEET, self.tab_photo_sheet.get_options()
        elif idx == 10:
            return OperationType.WATERMARK, self.tab_watermark.get_options()
        elif idx == 11:
            return OperationType.ID_LAYOUT, self.tab_id_layout.get_options()
        raise ValueError("Invalid tab index")

    def _on_process_clicked(self) -> None:
        idx = self.tab_widget.currentIndex()
        files = self.file_queue.get_files()
        if idx == 9:
            photo_src = self.tab_photo_sheet.get_source_image_path()
            if photo_src and photo_src.exists():
                files = [photo_src]
        elif not files:
            if idx == 7:
                pages = self.tab_organize.get_pages()
                if pages:
                    files = list(dict.fromkeys(p.source_path for p in pages))
            elif idx == 11:
                try:
                    f, b = self.tab_id_layout.get_front_and_back_paths()
                    files = [f, b]
                except Exception:
                    pass

        if not files:
            QMessageBox.warning(self, "No Files", "Please add files to the queue before processing.")
            return

        out_dir = Path(self.txt_out_dir.text().strip())
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Invalid Output Directory", f"Cannot access output directory:\n{e}")
            return

        try:
            operation, options = self._get_current_operation_and_options()
        except Exception as e:
            QMessageBox.warning(self, "Invalid Configuration", f"Invalid operation options:\n{e}")
            return

        allow_overwrite = self.chk_overwrite.isChecked()
        pdf_name = ""
        worker_passwords = {}
        if operation == OperationType.IMAGES_TO_PDF:
            pdf_name = self.tab_i2p.get_output_name()
        elif operation == OperationType.PDF_ORGANIZE:
            pdf_name = self.tab_organize.get_output_name()
            # Ensure all loaded organiser sources are included in protected files
            org_pages = self.tab_organize.get_pages()
            if org_pages:
                files = list(dict.fromkeys(files + [p.source_path for p in org_pages]))
            worker_passwords = self.tab_organize.get_passwords()
        elif operation == OperationType.ID_LAYOUT:
            ext = getattr(options, "output_format", "PDF").lower()
            pdf_name = f"id_layout.{ext}"
            try:
                f, b = self.tab_id_layout.get_front_and_back_paths()
                files = [f, b]
            except Exception:
                if len(files) >= 2:
                    files = files[:2]

        # UI state during processing: disable queue and tabs to prevent race conditions
        self.btn_process.setEnabled(False)
        self.tab_widget.setEnabled(False)
        self.file_queue.setEnabled(False)
        self.chk_overwrite.setEnabled(False)
        self.txt_out_dir.setEnabled(False)
        self.btn_browse.setEnabled(False)

        is_single_output = operation in (OperationType.IMAGES_TO_PDF, OperationType.PDF_ORGANIZE, OperationType.ID_LAYOUT)
        self.btn_cancel.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 1 if is_single_output else len(files))
        self.progress_bar.setValue(0)
        self.btn_report.setEnabled(False)
        self._last_batch_results.clear()

        self._active_worker = BatchProcessingWorker(
            operation=operation,
            input_paths=files,
            output_dir=out_dir,
            options=options,
            allow_overwrite=allow_overwrite,
            pdf_output_name=pdf_name,
            passwords=worker_passwords,
        )
        self._active_worker.signals.progress.connect(self._on_worker_progress)
        self._active_worker.signals.item_finished.connect(self._on_worker_item_finished)
        self._active_worker.signals.batch_finished.connect(self._on_worker_batch_finished)
        self._active_worker.signals.cancelled.connect(self._on_worker_cancelled)
        self._active_worker.finished.connect(self._on_worker_thread_finished)

        self._active_worker.start()

    def _on_cancel_clicked(self) -> None:
        if self._active_worker:
            self.lbl_status.setText("Cancelling operation...")
            self._active_worker.cancel()

    def _on_worker_progress(self, current: int, total: int, filename: str) -> None:
        self.progress_bar.setValue(current)
        self.lbl_status.setText(f"Processing ({current}/{total}): {filename}")

    def _on_worker_item_finished(self, result: JobResult) -> None:
        self._last_batch_results.append(result)
        # Update queue row status
        files = self.file_queue.get_files()
        try:
            idx = files.index(result.input_path)
            status_text = "Done" if result.success else "Failed"
            self.file_queue.set_file_status(idx, status_text, is_error=not result.success)
        except ValueError:
            pass

        # Update preview with result
        self.preview_panel.load_result(result)

    def _on_worker_batch_finished(self, results: List[JobResult]) -> None:
        passed = sum(1 for r in results if r.success)
        total = len(results)
        self.lbl_status.setText(f"Batch completed: {passed}/{total} successful")
        self.btn_report.setEnabled(True)
        if self.auto_show_report_dialog:
            self._show_validation_report()

    def _on_worker_cancelled(self) -> None:
        self.lbl_status.setText("Processing cancelled by user.")
        if self._last_batch_results:
            self.btn_report.setEnabled(True)

    def _on_worker_thread_finished(self) -> None:
        self.btn_process.setEnabled(True)
        self.tab_widget.setEnabled(True)
        self.file_queue.setEnabled(True)
        self.chk_overwrite.setEnabled(True)
        self.txt_out_dir.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.progress_bar.setVisible(False)
        self._active_worker = None

    def closeEvent(self, event) -> None:
        if self._is_closing:
            # Already cancelling and waiting for background thread to exit cleanly.
            # If the worker has already finished by now, accept closure; otherwise ignore repeated close events.
            worker = self._active_worker
            if worker is None or not worker.isRunning():
                event.accept()
            else:
                event.ignore()
            return

        worker = self._active_worker
        if worker and worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Operation in Progress",
                "A batch operation is currently running. Do you want to cancel and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

            # Re-check worker state after modal dialog returns:
            # During the modal event loop, the worker thread may have completed and cleared self._active_worker.
            worker = self._active_worker
            if worker is None or not worker.isRunning():
                event.accept()
                return

            self._is_closing = True
            self.lbl_status.setText("Cancellation in progress. Window will close automatically when the worker terminates.")
            self.setEnabled(False)

            # Connect finished signal BEFORE requesting cancellation to ensure completion is handled
            # even if the worker terminates immediately upon cancellation.
            worker.finished.connect(self.close)
            worker.cancel()

            # If the worker finished synchronously/immediately upon cancel, accept close immediately
            if not worker.isRunning():
                event.accept()
                return

            event.ignore()
        else:
            event.accept()

    def _show_validation_report(self) -> None:
        if self._last_batch_results:
            dlg = ValidationReportDialog(self._last_batch_results, parent=self)
            dlg.exec()

