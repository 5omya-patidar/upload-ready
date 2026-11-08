"""Comprehensive end-to-end tests exercising the desktop interface controls and workflows."""

import sys
import time
from pathlib import Path
from PIL import Image
import pypdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from upload_ready.core.models import OperationType, ResizeMode
from upload_ready.gui.main_window import MainWindow


def wait_for_worker(window: MainWindow, timeout_sec: float = 8.0) -> None:
    """Helper to pump the Qt event loop until background worker finishes."""
    start = time.time()
    while window._active_worker is not None and window._active_worker.isRunning():
        QApplication.processEvents()
        time.sleep(0.02)
        if (time.time() - start) > timeout_sec:
            raise TimeoutError("Background worker did not finish within timeout.")
    QApplication.processEvents()


def create_test_window(temp_dir: Path) -> MainWindow:
    """Instantiate a MainWindow configured for automated testing (non-blocking dialogs)."""
    win = MainWindow()
    win.auto_show_report_dialog = False
    win.txt_out_dir.setText(str(temp_dir))
    return win


def test_gui_convert_transparency_flow(qapp, synthetic_rgba_png, temp_dir):
    """Exercise format conversion of transparent PNG to JPEG with custom background color via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgba_png])
    
    # Select Convert tab
    win.tab_widget.setCurrentIndex(0)
    win.tab_convert.combo_format.setCurrentText("JPEG")
    win.tab_convert._bg_color = (0, 0, 255)  # Blue background
    win.tab_convert.slider_quality.setValue(90)

    # Trigger process button
    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".jpg"
    
    # Check that preview loaded result
    assert win.preview_panel.result_pane.meta_label.text() != ""
    win.deleteLater()


def test_gui_exact_resize_crop_pad(qapp, synthetic_rgb_png, temp_dir):
    """Exercise Resize tab with Crop and Pad mode via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])
    
    # Select Resize tab
    win.tab_widget.setCurrentIndex(1)
    win.tab_resize.spin_w.setValue(100)
    win.tab_resize.spin_h.setValue(100)
    # Mode: Pad
    win.tab_resize.combo_mode.setCurrentIndex(2)  # Pad
    win.tab_resize._bg_color = (255, 0, 0)       # Red canvas

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_dimensions == (100, 100)
    win.deleteLater()


def test_gui_compress_target_size(qapp, tmp_path: Path, temp_dir: Path):
    """Exercise Compress tab target size search and before/after metadata comparison."""
    # Create realistic JPEG
    img_path = tmp_path / "gui_test_photo.jpg"
    img = Image.new("RGB", (300, 300))
    for x in range(300):
        for y in range(300):
            img.putpixel((x, y), ((x * 2) % 256, (y * 3) % 256, 120))
    img.save(img_path, format="JPEG", quality=95)

    win = create_test_window(temp_dir)
    win.file_queue.add_files([img_path])

    # Select Compress tab
    win.tab_widget.setCurrentIndex(2)
    win.tab_compress.radio_target_max.setChecked(True)
    win.tab_compress.txt_max_size.setText("16 KB")

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_size_bytes <= 16000
    win.deleteLater()


def test_gui_images_to_pdf_reorder_and_rotate(qapp, synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    """Exercise Images to PDF with queue reordering and rotation."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png, synthetic_rgba_png])

    # Select second item and move it up
    win.file_queue.table.selectRow(1)
    win.file_queue._move_up()
    # Now order is synthetic_rgba_png, then synthetic_rgb_png
    assert win.file_queue.get_files()[0] == synthetic_rgba_png

    # Rotate first item 90 degrees
    win.file_queue.table.selectRow(0)
    win.file_queue._rotate_selected()
    assert win.file_queue.get_rotations().get(0) == 90

    # Select Images to PDF tab
    win.tab_widget.setCurrentIndex(3)
    win.tab_i2p.txt_filename.setText("gui_combined.pdf")

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert (temp_dir / "gui_combined.pdf").exists()
    win.deleteLater()


def test_gui_pdf_to_images_page_selection(qapp, synthetic_multipage_pdf, temp_dir):
    """Exercise PDF to Images tab rendering specific pages via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_multipage_pdf])

    # Select PDF to Images tab
    win.tab_widget.setCurrentIndex(4)
    win.tab_p2i.txt_pages.setText("1, 3")
    win.tab_p2i.combo_format.setCurrentText("PNG")
    win.tab_p2i.spin_dpi.setValue(100)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.details["rendered_count"] == 2
    win.deleteLater()


def test_gui_pdf_reduce_structural(qapp, synthetic_multipage_pdf, temp_dir):
    """Exercise PDF Reduce tab structural mode via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_multipage_pdf])

    # Select PDF Reduce tab
    win.tab_widget.setCurrentIndex(5)
    win.tab_reduce.radio_structural.setChecked(True)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert (temp_dir / f"{synthetic_multipage_pdf.stem}_reduced.pdf").exists()
    win.deleteLater()


def test_gui_invalid_input_marks_failure(qapp, synthetic_corrupt_file, temp_dir):
    """Verify invalid corrupt input updates row status badge to 'Failed' with red color."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_corrupt_file])

    win.tab_widget.setCurrentIndex(0)
    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    assert win._last_batch_results[0].success is False
    # Check queue badge
    item = win.file_queue.table.item(0, 4)
    assert item.text() == "Failed"
    win.deleteLater()


def test_gui_cancellation_during_batch(qapp, synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    """Verify cancellation button halts worker and cleans up without hanging."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png, synthetic_rgba_png])

    win.tab_widget.setCurrentIndex(0)
    win.btn_process.click()
    
    # Immediately trigger cancellation
    win._on_cancel_clicked()
    wait_for_worker(win)

    assert win.lbl_status.text() == "Processing cancelled by user."
    assert win.btn_process.isEnabled() is True
    win.deleteLater()


def test_gui_filename_collision_handling(qapp, synthetic_rgb_png, temp_dir):
    """Verify processing same file twice without overwrite creates _1 non-colliding copy."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])
    win.chk_overwrite.setChecked(False)

    win.tab_widget.setCurrentIndex(0)
    win.tab_convert.combo_format.setCurrentText("PNG")

    # Pass 1
    win.btn_process.click()
    wait_for_worker(win)
    first_path = win._last_batch_results[0].output_path
    assert first_path.name == "synthetic_rgb.png"

    # Pass 2 (same destination)
    win.btn_process.click()
    wait_for_worker(win)
    second_path = win._last_batch_results[0].output_path
    assert second_path.name == "synthetic_rgb_1.png"
    assert first_path.exists() and second_path.exists()
    win.deleteLater()


def test_gui_password_with_surrounding_whitespace(qapp, tmp_path: Path, temp_dir: Path):
    """GUI must preserve password with leading and trailing whitespace intact and decrypt successfully."""
    # Create encrypted PDF with password containing surrounding whitespace
    pdf_path = tmp_path / "whitespace_enc.pdf"
    writer = pypdf.PdfWriter()
    p = writer.add_blank_page(100, 100)
    raw_pass = "  spaced_password  "
    writer.encrypt(user_password=raw_pass)
    with open(pdf_path, "wb") as f:
        writer.write(f)

    win = create_test_window(temp_dir)
    win.file_queue.add_files([pdf_path])

    # Select PDF to Images tab
    win.tab_widget.setCurrentIndex(4)
    win.tab_p2i.txt_password.setText(raw_pass)

    win.btn_process.click()
    wait_for_worker(win)

    assert win._last_batch_results is not None
    assert win._last_batch_results[0].success is True
    assert (temp_dir / "whitespace_enc_page_0001.png").exists()
    win.deleteLater()


def test_gui_repeated_batches_clears_previous_state(qapp, synthetic_rgb_png, temp_dir: Path):
    """Running repeated batches updates settings and replaces previous batch results."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Batch 1: Convert
    win.tab_widget.setCurrentIndex(0)
    win.tab_convert.combo_format.setCurrentText("JPEG")
    win.btn_process.click()
    wait_for_worker(win)
    assert win._last_batch_results[0].operation == OperationType.CONVERT

    # Batch 2: Resize
    win.tab_widget.setCurrentIndex(1)
    win.tab_resize.spin_w.setValue(50)
    win.tab_resize.spin_h.setValue(50)
    win.btn_process.click()
    wait_for_worker(win)
    assert win._last_batch_results[0].operation == OperationType.RESIZE
    assert win._last_batch_results[0].output_dimensions == (50, 38)
    win.deleteLater()


def test_gui_close_event_defers_safely_when_worker_is_busy(qapp, synthetic_rgb_png, temp_dir: Path, monkeypatch):
    """closeEvent must asynchronously defer destruction without blocking GUI thread, ignoring repeated close calls."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    prompt_count = 0
    def mock_question(*args, **kwargs):
        nonlocal prompt_count
        prompt_count += 1
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", mock_question)

    class SlowWorkerMock:
        def __init__(self):
            self._running = True
            self.cancelled = False
        def isRunning(self): return self._running
        def cancel(self): self.cancelled = True
        class FinishedSignal:
            def __init__(self): self.callbacks = []
            def connect(self, fn): self.callbacks.append(fn)
        finished = FinishedSignal()

    worker = SlowWorkerMock()
    win._active_worker = worker

    # 1. First close attempt: triggers prompt, requests cancellation, defers closing
    event1 = QCloseEvent()
    win.closeEvent(event1)
    assert event1.isAccepted() is False
    assert prompt_count == 1
    assert worker.cancelled is True
    assert win._is_closing is True
    assert "Cancellation in progress" in win.lbl_status.text()
    assert win.isEnabled() is False

    # 2. Repeated close attempt while still closing: immediately ignored without re-prompting
    event2 = QCloseEvent()
    win.closeEvent(event2)
    assert event2.isAccepted() is False
    assert prompt_count == 1  # No re-prompt!

    # 3. Worker finishes in background
    worker._running = False
    event3 = QCloseEvent()
    win.closeEvent(event3)
    assert event3.isAccepted() is True

    win._active_worker = None
    win.deleteLater()


def test_gui_presets_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise preset selection, creation, and execution through GUI controls."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Presets Tab (index 6)
    win.tab_widget.setCurrentIndex(6)
    assert win.tab_presets.combo_presets.count() >= 4

    # Select privacy_sanitized preset
    idx = win.tab_presets.combo_presets.findData("privacy_sanitized")
    assert idx >= 0
    win.tab_presets.combo_presets.setCurrentIndex(idx)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".webp"
    win.deleteLater()


def test_gui_pdf_organizer_workflow(qapp, synthetic_multipage_pdf, temp_dir):
    """Exercise PDF page organiser thumbnail loading, rotating, reordering, and export via GUI."""
    win = create_test_window(temp_dir)
    # Load document
    win.tab_organize.load_documents([synthetic_multipage_pdf])
    assert win.tab_organize.list_widget.count() == 3

    # Select second page, rotate 90
    win.tab_organize.list_widget.setCurrentRow(1)
    win.tab_organize._on_rotate_selected()
    p1 = win.tab_organize.list_widget.item(1).data(Qt.ItemDataRole.UserRole)
    assert p1.rotation_offset == 90

    # Move right
    win.tab_organize._on_move_right()
    assert win.tab_organize.list_widget.currentRow() == 2

    # Switch to tab and process
    win.tab_widget.setCurrentIndex(7)
    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.exists()
    r = pypdf.PdfReader(res.output_path)
    assert len(r.pages) == 3
    win.deleteLater()


def test_gui_metadata_workflow(qapp, synthetic_jpeg_with_exif, temp_dir):
    """Exercise metadata inspection and scrubbing via GUI controls."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_jpeg_with_exif])

    # Select Metadata Tab (index 8)
    win.tab_widget.setCurrentIndex(8)
    win.tab_metadata.inspect_file(synthetic_jpeg_with_exif)
    assert win.tab_metadata.table_meta.rowCount() >= 4

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    with Image.open(res.output_path) as img:
        # EXIF orientation normalized and stripped
        assert 0x0112 not in img.getexif()
    win.deleteLater()


def test_gui_photo_sheet_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise photo sheet interactive crop positioning and sheet generation via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Photo Sheet Tab (index 9)
    win.tab_widget.setCurrentIndex(9)
    win.tab_photo_sheet.set_source_image(synthetic_rgb_png)
    win.tab_photo_sheet.slider_crop_y.setValue(40)
    win.tab_photo_sheet.spin_copies.setValue(4)
    win.tab_photo_sheet.combo_format.setCurrentText("PNG")
    win.tab_photo_sheet.chk_allow_upscale.setChecked(True)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".png"
    win.deleteLater()


def test_gui_watermark_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise purpose watermark overlay settings and generation via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Watermark Tab (index 10)
    win.tab_widget.setCurrentIndex(10)
    win.tab_watermark.txt_text.setText("TENANCY VERIFIED")
    win.tab_watermark.slider_opacity.setValue(35)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.exists()
    win.deleteLater()


def test_gui_id_layout_workflow(qapp, synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    """Exercise ID front/back slots, swapping, and sheet generation via GUI."""
    win = create_test_window(temp_dir)

    # Select ID Layout Tab (index 11)
    win.tab_widget.setCurrentIndex(11)
    win.tab_id_layout.txt_front.setText(str(synthetic_rgb_png))
    win.tab_id_layout.txt_back.setText(str(synthetic_rgba_png))
    win.tab_id_layout._swap_front_back()
    assert win.tab_id_layout.txt_front.text() == str(synthetic_rgba_png)
    assert win.tab_id_layout.txt_back.text() == str(synthetic_rgb_png)

    win.tab_id_layout.combo_format.setCurrentText("PNG")

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".png"
    win.deleteLater()


def test_gui_presets_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise preset selection, creation, and execution through GUI controls."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Presets Tab (index 6)
    win.tab_widget.setCurrentIndex(6)
    assert win.tab_presets.combo_presets.count() >= 4

    # Select privacy_sanitized preset
    idx = win.tab_presets.combo_presets.findData("privacy_sanitized")
    assert idx >= 0
    win.tab_presets.combo_presets.setCurrentIndex(idx)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".webp"
    win.deleteLater()


def test_gui_pdf_organizer_workflow(qapp, synthetic_multipage_pdf, temp_dir):
    """Exercise PDF page organiser thumbnail loading, rotating, reordering, and export via GUI."""
    win = create_test_window(temp_dir)
    # Load document
    win.tab_organize.load_documents([synthetic_multipage_pdf])
    assert win.tab_organize.list_widget.count() == 3

    # Select second page, rotate 90
    win.tab_organize.list_widget.setCurrentRow(1)
    win.tab_organize._on_rotate_selected()
    p1 = win.tab_organize.list_widget.item(1).data(Qt.ItemDataRole.UserRole)
    assert p1.rotation_offset == 90

    # Move right
    win.tab_organize._on_move_right()
    assert win.tab_organize.list_widget.currentRow() == 2

    # Switch to tab and process
    win.tab_widget.setCurrentIndex(7)
    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.exists()
    r = pypdf.PdfReader(res.output_path)
    assert len(r.pages) == 3
    win.deleteLater()


def test_gui_metadata_workflow(qapp, synthetic_jpeg_with_exif, temp_dir):
    """Exercise metadata inspection and scrubbing via GUI controls."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_jpeg_with_exif])

    # Select Metadata Tab (index 8)
    win.tab_widget.setCurrentIndex(8)
    win.tab_metadata.inspect_file(synthetic_jpeg_with_exif)
    assert win.tab_metadata.table_meta.rowCount() >= 4

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    with Image.open(res.output_path) as img:
        # EXIF orientation normalized and stripped
        assert 0x0112 not in img.getexif()
    win.deleteLater()


def test_gui_photo_sheet_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise photo sheet interactive crop positioning and sheet generation via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Photo Sheet Tab (index 9)
    win.tab_widget.setCurrentIndex(9)
    win.tab_photo_sheet.set_source_image(synthetic_rgb_png)
    win.tab_photo_sheet.slider_crop_y.setValue(40)
    win.tab_photo_sheet.spin_copies.setValue(4)
    win.tab_photo_sheet.combo_format.setCurrentText("PNG")
    win.tab_photo_sheet.chk_allow_upscale.setChecked(True)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".png"
    win.deleteLater()


def test_gui_watermark_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise purpose watermark overlay settings and generation via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Watermark Tab (index 10)
    win.tab_widget.setCurrentIndex(10)
    win.tab_watermark.txt_text.setText("TENANCY VERIFIED")
    win.tab_watermark.slider_opacity.setValue(35)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.exists()
    win.deleteLater()


def test_gui_id_layout_workflow(qapp, synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    """Exercise ID front/back slots, swapping, and sheet generation via GUI."""
    win = create_test_window(temp_dir)

    # Select ID Layout Tab (index 11)
    win.tab_widget.setCurrentIndex(11)
    win.tab_id_layout.txt_front.setText(str(synthetic_rgb_png))
    win.tab_id_layout.txt_back.setText(str(synthetic_rgba_png))
    win.tab_id_layout._swap_front_back()
    assert win.tab_id_layout.txt_front.text() == str(synthetic_rgba_png)
    assert win.tab_id_layout.txt_back.text() == str(synthetic_rgb_png)

    win.tab_id_layout.combo_format.setCurrentText("PNG")

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".png"
    win.deleteLater()



def test_gui_close_event_worker_finishes_while_dialog_is_open(qapp, temp_dir: Path, monkeypatch):
    """If the worker thread finishes while the confirmation dialog is open, closeEvent must accept immediately without crashing."""
    win = create_test_window(temp_dir)

    class FinishingWorkerMock:
        def __init__(self):
            self._running = True
            self.cancelled = False
        def isRunning(self): return self._running
        def cancel(self): self.cancelled = True
        class FinishedSignal:
            def __init__(self): self.callbacks = []
            def connect(self, fn): self.callbacks.append(fn)
        finished = FinishedSignal()

    worker = FinishingWorkerMock()
    win._active_worker = worker

    def mock_question_finishes_worker(*args, **kwargs):
        # Simulate worker completing in the background during the modal dialog loop
        worker._running = False
        win._on_worker_thread_finished()
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", mock_question_finishes_worker)

    event = QCloseEvent()
    win.closeEvent(event)

    # Must accept immediately because worker finished during the dialog
    assert event.isAccepted() is True
    assert win._active_worker is None
    win.deleteLater()


def test_gui_close_event_worker_finishes_immediately_upon_cancellation(qapp, temp_dir: Path, monkeypatch):
    """If the worker thread terminates immediately during cancel(), closeEvent must accept immediately."""
    win = create_test_window(temp_dir)

    class ImmediateCancelWorkerMock:
        def __init__(self):
            self._running = True
            self.cancelled = False
        def isRunning(self): return self._running
        def cancel(self):
            self.cancelled = True
            self._running = False  # Terminates immediately on cancellation
        class FinishedSignal:
            def __init__(self): self.callbacks = []
            def connect(self, fn): self.callbacks.append(fn)
        finished = FinishedSignal()

    worker = ImmediateCancelWorkerMock()
    win._active_worker = worker

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    event = QCloseEvent()
    win.closeEvent(event)

    # Must accept immediately because worker finished upon cancellation
    assert event.isAccepted() is True
    assert worker.cancelled is True
    win._active_worker = None
    win.deleteLater()


def test_gui_presets_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise preset selection, creation, and execution through GUI controls."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Presets Tab (index 6)
    win.tab_widget.setCurrentIndex(6)
    assert win.tab_presets.combo_presets.count() >= 4

    # Select privacy_sanitized preset
    idx = win.tab_presets.combo_presets.findData("privacy_sanitized")
    assert idx >= 0
    win.tab_presets.combo_presets.setCurrentIndex(idx)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".webp"
    win.deleteLater()


def test_gui_pdf_organizer_workflow(qapp, synthetic_multipage_pdf, temp_dir):
    """Exercise PDF page organiser thumbnail loading, rotating, reordering, and export via GUI."""
    win = create_test_window(temp_dir)
    # Load document
    win.tab_organize.load_documents([synthetic_multipage_pdf])
    assert win.tab_organize.list_widget.count() == 3

    # Select second page, rotate 90
    win.tab_organize.list_widget.setCurrentRow(1)
    win.tab_organize._on_rotate_selected()
    p1 = win.tab_organize.list_widget.item(1).data(Qt.ItemDataRole.UserRole)
    assert p1.rotation_offset == 90

    # Move right
    win.tab_organize._on_move_right()
    assert win.tab_organize.list_widget.currentRow() == 2

    # Switch to tab and process
    win.tab_widget.setCurrentIndex(7)
    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.exists()
    r = pypdf.PdfReader(res.output_path)
    assert len(r.pages) == 3
    win.deleteLater()


def test_gui_metadata_workflow(qapp, synthetic_jpeg_with_exif, temp_dir):
    """Exercise metadata inspection and scrubbing via GUI controls."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_jpeg_with_exif])

    # Select Metadata Tab (index 8)
    win.tab_widget.setCurrentIndex(8)
    win.tab_metadata.inspect_file(synthetic_jpeg_with_exif)
    assert win.tab_metadata.table_meta.rowCount() >= 4

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    with Image.open(res.output_path) as img:
        # EXIF orientation normalized and stripped
        assert 0x0112 not in img.getexif()
    win.deleteLater()


def test_gui_photo_sheet_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise photo sheet interactive crop positioning and sheet generation via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Photo Sheet Tab (index 9)
    win.tab_widget.setCurrentIndex(9)
    win.tab_photo_sheet.set_source_image(synthetic_rgb_png)
    win.tab_photo_sheet.slider_crop_y.setValue(40)
    win.tab_photo_sheet.spin_copies.setValue(4)
    win.tab_photo_sheet.combo_format.setCurrentText("PNG")
    win.tab_photo_sheet.chk_allow_upscale.setChecked(True)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".png"
    win.deleteLater()


def test_gui_watermark_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise purpose watermark overlay settings and generation via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Watermark Tab (index 10)
    win.tab_widget.setCurrentIndex(10)
    win.tab_watermark.txt_text.setText("TENANCY VERIFIED")
    win.tab_watermark.slider_opacity.setValue(35)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.exists()
    win.deleteLater()


def test_gui_id_layout_workflow(qapp, synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    """Exercise ID front/back slots, swapping, and sheet generation via GUI."""
    win = create_test_window(temp_dir)

    # Select ID Layout Tab (index 11)
    win.tab_widget.setCurrentIndex(11)
    win.tab_id_layout.txt_front.setText(str(synthetic_rgb_png))
    win.tab_id_layout.txt_back.setText(str(synthetic_rgba_png))
    win.tab_id_layout._swap_front_back()
    assert win.tab_id_layout.txt_front.text() == str(synthetic_rgba_png)
    assert win.tab_id_layout.txt_back.text() == str(synthetic_rgb_png)

    win.tab_id_layout.combo_format.setCurrentText("PNG")

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".png"
    win.deleteLater()



def test_gui_close_event_repeated_close_attempts(qapp, temp_dir: Path, monkeypatch):
    """Repeated close attempts while cancellation is pending must be ignored without re-prompting, then accepted once finished."""
    win = create_test_window(temp_dir)

    prompt_count = 0
    def mock_question(*args, **kwargs):
        nonlocal prompt_count
        prompt_count += 1
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", mock_question)

    class AsyncWorkerMock:
        def __init__(self):
            self._running = True
            self.cancelled = False
        def isRunning(self): return self._running
        def cancel(self): self.cancelled = True
        class FinishedSignal:
            def __init__(self): self.callbacks = []
            def connect(self, fn): self.callbacks.append(fn)
        finished = FinishedSignal()

    worker = AsyncWorkerMock()
    win._active_worker = worker

    # 1. First close attempt: prompts user, sets _is_closing = True, ignores event
    event1 = QCloseEvent()
    win.closeEvent(event1)
    assert event1.isAccepted() is False
    assert prompt_count == 1
    assert win._is_closing is True

    # 2. Second close attempt: worker still busy -> ignored immediately without re-prompting
    event2 = QCloseEvent()
    win.closeEvent(event2)
    assert event2.isAccepted() is False
    assert prompt_count == 1

    # 3. Third close attempt: worker still busy -> ignored immediately without re-prompting
    event3 = QCloseEvent()
    win.closeEvent(event3)
    assert event3.isAccepted() is False
    assert prompt_count == 1

    # 4. Worker finishes in the background
    worker._running = False
    win._on_worker_thread_finished()

    # 5. Subsequent close attempt: worker is finished -> accepted
    event4 = QCloseEvent()
    win.closeEvent(event4)
    assert event4.isAccepted() is True
    assert prompt_count == 1

    win._active_worker = None
    win.deleteLater()


def test_gui_presets_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise preset selection, creation, and execution through GUI controls."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Presets Tab (index 6)
    win.tab_widget.setCurrentIndex(6)
    assert win.tab_presets.combo_presets.count() >= 4

    # Select privacy_sanitized preset
    idx = win.tab_presets.combo_presets.findData("privacy_sanitized")
    assert idx >= 0
    win.tab_presets.combo_presets.setCurrentIndex(idx)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".webp"
    win.deleteLater()


def test_gui_pdf_organizer_workflow(qapp, synthetic_multipage_pdf, temp_dir):
    """Exercise PDF page organiser thumbnail loading, rotating, reordering, and export via GUI."""
    win = create_test_window(temp_dir)
    # Load document
    win.tab_organize.load_documents([synthetic_multipage_pdf])
    assert win.tab_organize.list_widget.count() == 3

    # Select second page, rotate 90
    win.tab_organize.list_widget.setCurrentRow(1)
    win.tab_organize._on_rotate_selected()
    p1 = win.tab_organize.list_widget.item(1).data(Qt.ItemDataRole.UserRole)
    assert p1.rotation_offset == 90

    # Move right
    win.tab_organize._on_move_right()
    assert win.tab_organize.list_widget.currentRow() == 2

    # Switch to tab and process
    win.tab_widget.setCurrentIndex(7)
    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.exists()
    r = pypdf.PdfReader(res.output_path)
    assert len(r.pages) == 3
    win.deleteLater()


def test_gui_metadata_workflow(qapp, synthetic_jpeg_with_exif, temp_dir):
    """Exercise metadata inspection and scrubbing via GUI controls."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_jpeg_with_exif])

    # Select Metadata Tab (index 8)
    win.tab_widget.setCurrentIndex(8)
    win.tab_metadata.inspect_file(synthetic_jpeg_with_exif)
    assert win.tab_metadata.table_meta.rowCount() >= 4

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    with Image.open(res.output_path) as img:
        # EXIF orientation normalized and stripped
        assert 0x0112 not in img.getexif()
    win.deleteLater()


def test_gui_photo_sheet_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise photo sheet interactive crop positioning and sheet generation via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Photo Sheet Tab (index 9)
    win.tab_widget.setCurrentIndex(9)
    win.tab_photo_sheet.set_source_image(synthetic_rgb_png)
    win.tab_photo_sheet.slider_crop_y.setValue(40)
    win.tab_photo_sheet.spin_copies.setValue(4)
    win.tab_photo_sheet.combo_format.setCurrentText("PNG")
    win.tab_photo_sheet.chk_allow_upscale.setChecked(True)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".png"
    win.deleteLater()


def test_gui_watermark_workflow(qapp, synthetic_rgb_png, temp_dir):
    """Exercise purpose watermark overlay settings and generation via GUI."""
    win = create_test_window(temp_dir)
    win.file_queue.add_files([synthetic_rgb_png])

    # Select Watermark Tab (index 10)
    win.tab_widget.setCurrentIndex(10)
    win.tab_watermark.txt_text.setText("TENANCY VERIFIED")
    win.tab_watermark.slider_opacity.setValue(35)

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.exists()
    win.deleteLater()


def test_gui_id_layout_workflow(qapp, synthetic_rgb_png, synthetic_rgba_png, temp_dir):
    """Exercise ID front/back slots, swapping, and sheet generation via GUI."""
    win = create_test_window(temp_dir)

    # Select ID Layout Tab (index 11)
    win.tab_widget.setCurrentIndex(11)
    win.tab_id_layout.txt_front.setText(str(synthetic_rgb_png))
    win.tab_id_layout.txt_back.setText(str(synthetic_rgba_png))
    win.tab_id_layout._swap_front_back()
    assert win.tab_id_layout.txt_front.text() == str(synthetic_rgba_png)
    assert win.tab_id_layout.txt_back.text() == str(synthetic_rgb_png)

    win.tab_id_layout.combo_format.setCurrentText("PNG")

    win.btn_process.click()
    wait_for_worker(win)

    assert len(win._last_batch_results) == 1
    res = win._last_batch_results[0]
    assert res.success is True
    assert res.output_path.suffix.lower() == ".png"
    win.deleteLater()

