"""GUI component and integration tests."""

from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

from upload_ready.core.models import OperationType, ResizeMode
from upload_ready.gui.file_queue import FileQueueWidget
from upload_ready.gui.main_window import MainWindow
from upload_ready.gui.report_dialog import ValidationReportDialog
from upload_ready.gui.tabs import (
    CompressTab,
    ConvertTab,
    ImagesToPdfTab,
    PdfReduceTab,
    PdfToImagesTab,
    ResizeTab,
)


def test_file_queue_operations(qapp, synthetic_rgb_png, synthetic_rgba_png):
    queue = FileQueueWidget()
    assert len(queue.get_files()) == 0

    # Add files
    queue.add_files([synthetic_rgb_png, synthetic_rgba_png])
    assert len(queue.get_files()) == 2
    assert queue.table.rowCount() == 2

    # Rotate first file
    queue.table.selectRow(0)
    queue._rotate_selected()
    assert queue.get_rotations().get(0) == 90

    # Move down
    queue._move_down()
    assert queue.get_files()[0] == synthetic_rgba_png
    assert queue.get_files()[1] == synthetic_rgb_png

    # Clear all
    queue.clear_all()
    assert len(queue.get_files()) == 0
    assert queue.table.rowCount() == 0
    queue.deleteLater()


def test_convert_tab_options(qapp):
    tab = ConvertTab()
    opts = tab.get_options()
    assert opts.target_format == "JPEG"
    assert opts.quality == 85
    assert opts.lossless is False
    tab.deleteLater()


def test_resize_tab_options(qapp):
    tab = ResizeTab()
    tab.spin_w.setValue(1280)
    tab.spin_h.setValue(720)
    opts = tab.get_options()
    assert opts.width == 1280
    assert opts.height == 720
    assert opts.mode == ResizeMode.FIT
    tab.deleteLater()


def test_compress_tab_options(qapp):
    tab = CompressTab()
    tab.radio_target_max.setChecked(True)
    tab.txt_max_size.setText("100 KB")
    opts = tab.get_options()
    assert opts.max_bytes == 100000
    assert opts.is_lossless is False
    tab.deleteLater()


def test_images_to_pdf_tab_options(qapp):
    tab = ImagesToPdfTab()
    tab.txt_filename.setText("my_presentation.pdf")
    opts = tab.get_options({0: 90})
    assert tab.get_output_name() == "my_presentation.pdf"
    assert opts.page_size == "A4"
    assert opts.image_rotations.get(0) == 90
    tab.deleteLater()


def test_pdf_to_images_tab_options(qapp):
    tab = PdfToImagesTab()
    tab.txt_pages.setText("1-3")
    tab.spin_dpi.setValue(200)
    opts = tab.get_options()
    assert opts.page_range == "1-3"
    assert opts.dpi == 200
    assert opts.target_format == "PNG"
    tab.deleteLater()


def test_pdf_reduce_tab_options(qapp):
    tab = PdfReduceTab()
    opts = tab.get_options()
    assert opts.structural_only is True
    assert opts.downsample_scanned is False
    tab.deleteLater()


def test_validation_report_dialog(qapp, synthetic_rgb_png):
    from upload_ready.core.image_convert import convert_image
    from upload_ready.core.models import ConvertOptions
    
    res = convert_image(
        synthetic_rgb_png,
        synthetic_rgb_png.parent / "out_report",
        ConvertOptions(target_format="WEBP"),
    )
    
    dlg = ValidationReportDialog([res])
    assert dlg.table.rowCount() >= 2
    assert "WEBP" in dlg.table.item(0, 2).text() or "WEBP" in dlg.table.item(0, 3).text()
    dlg.deleteLater()


def test_main_window_init(qapp):
    win = MainWindow()
    assert win.windowTitle().startswith("Upload Ready")
    assert win.tab_widget.count() == 12
    win.deleteLater()
