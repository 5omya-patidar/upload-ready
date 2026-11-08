"""Validation Report dialog showing requirement checks, pass/fail status, and warnings."""

from typing import List
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from upload_ready.core.constants import bytes_to_human
from upload_ready.core.models import JobResult


class ValidationReportDialog(QDialog):
    """Detailed modal report displaying every verified requirement and constraint."""

    def __init__(self, results: List[JobResult], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Validation & Compliance Report")
        self.resize(750, 520)

        layout = QVBoxLayout(self)

        # Header summary
        total_jobs = len(results)
        passed_jobs = sum(1 for r in results if r.success)
        failed_jobs = total_jobs - passed_jobs

        summary_text = (
            f"<b>Batch Completed:</b> {total_jobs} total item(s) | "
            f"<span style='color: #4CAF50;'><b>{passed_jobs} Passed</b></span> | "
            f"<span style='color: {'#F44336' if failed_jobs > 0 else '#888'};'><b>{failed_jobs} Failed/Unmet</b></span>"
        )
        lbl_summary = QLabel(summary_text)
        lbl_summary.setStyleSheet("font-size: 14px; margin-bottom: 8px;")
        layout.addWidget(lbl_summary)

        # Table of individual requirement checks
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["File", "Requirement", "Target", "Actual", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=2)

        # Warnings / Non-compliance details text area
        layout.addWidget(QLabel("<b>Detailed Constraints & Warnings:</b>"))
        self.txt_details = QTextEdit()
        self.txt_details.setReadOnly(True)
        self.txt_details.setStyleSheet("background: #1e1e1e; color: #ccc; font-family: monospace; font-size: 11px;")
        layout.addWidget(self.txt_details, stretch=1)

        # Standard close button
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._populate_report(results)

    def _populate_report(self, results: List[JobResult]) -> None:
        rows = []
        detail_lines = []

        for res in results:
            fname = res.input_path.name
            
            if res.error_message:
                rows.append((fname, "Processing", "Success", res.error_message, False))
                detail_lines.append(f"❌ [ERROR] {fname}: {res.error_message}")

            for chk in res.checks:
                rows.append((fname, chk.name, chk.target, chk.actual, chk.passed))
                if chk.details:
                    status_icon = "✓" if chk.passed else "⚠️"
                    detail_lines.append(f"{status_icon} [{fname}] {chk.name}: {chk.details}")

            for w in res.warnings:
                detail_lines.append(f"⚠️ [WARN] [{fname}] {w}")

        self.table.setRowCount(len(rows))
        for row_idx, (fname, req, target, actual, passed) in enumerate(rows):
            item_file = QTableWidgetItem(fname)
            item_req = QTableWidgetItem(req)
            item_target = QTableWidgetItem(target)
            item_actual = QTableWidgetItem(actual)

            status_str = "PASS" if passed else "FAIL"
            item_status = QTableWidgetItem(status_str)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if passed:
                item_status.setForeground(Qt.GlobalColor.green)
            else:
                item_status.setForeground(Qt.GlobalColor.red)

            self.table.setItem(row_idx, 0, item_file)
            self.table.setItem(row_idx, 1, item_req)
            self.table.setItem(row_idx, 2, item_target)
            self.table.setItem(row_idx, 3, item_actual)
            self.table.setItem(row_idx, 4, item_status)

        if not detail_lines:
            detail_lines.append("All operations verified with 0 warnings.")
        self.txt_details.setPlainText("\n".join(detail_lines))

