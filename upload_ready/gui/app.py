"""Application runner and styling configuration."""

import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from upload_ready.core.safety import TempJobWorkspace
from upload_ready.gui.main_window import MainWindow


def apply_theme(app: QApplication) -> None:
    """Apply clean, accessible modern dark theme."""
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Base, QColor(24, 24, 24))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(36, 36, 36))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(24, 24, 24))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
    app.setPalette(palette)


def run_gui() -> int:
    """Launch the Upload Ready desktop GUI."""
    # Clean any crashed workspaces on startup
    TempJobWorkspace.clean_orphaned_workspaces()

    app = QApplication(sys.argv)
    app.setApplicationName("Upload Ready")
    app.setOrganizationName("Upload Ready")
    apply_theme(app)

    window = MainWindow()
    window.show()

    return app.exec()

