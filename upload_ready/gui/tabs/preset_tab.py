"""Interactive Preset Management panel and dialog."""

import json
from pathlib import Path
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from upload_ready.core.constants import UnitStandard, parse_size_to_bytes
from upload_ready.core.presets import PresetManager, RequirementPreset


class PresetEditDialog(QDialog):
    """Interactive modal dialog for creating and editing declarative requirement presets."""

    def __init__(self, parent: Optional[QWidget] = None, preset: Optional[RequirementPreset] = None):
        super().__init__(parent)
        self.preset = preset
        self.setWindowTitle("Edit Requirement Preset" if preset else "Create New Requirement Preset")
        self.resize(500, 520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # General
        self.txt_name = QLineEdit(preset.name if preset else "New Preset")
        form.addRow("Preset Name:", self.txt_name)

        self.txt_desc = QLineEdit(preset.description if preset else "")
        form.addRow("Description:", self.txt_desc)

        # Target Format
        self.combo_format = QComboBox()
        self.combo_format.addItem("Keep Original / No Conversion", None)
        self.combo_format.addItem("JPEG", "JPEG")
        self.combo_format.addItem("PNG", "PNG")
        self.combo_format.addItem("WEBP", "WEBP")
        if preset and preset.target_format:
            idx = self.combo_format.findData(preset.target_format)
            if idx >= 0:
                self.combo_format.setCurrentIndex(idx)
        form.addRow("Target Format:", self.combo_format)

        # Dimensions & Resize Mode
        dim_layout = QHBoxLayout()
        self.spin_width = QSpinBox()
        self.spin_width.setRange(0, 50000)
        self.spin_width.setSpecialValueText("Original")
        self.spin_width.setValue(preset.width if (preset and preset.width) else 0)

        self.spin_height = QSpinBox()
        self.spin_height.setRange(0, 50000)
        self.spin_height.setSpecialValueText("Original")
        self.spin_height.setValue(preset.height if (preset and preset.height) else 0)

        dim_layout.addWidget(QLabel("W:"))
        dim_layout.addWidget(self.spin_width)
        dim_layout.addWidget(QLabel("H:"))
        dim_layout.addWidget(self.spin_height)
        form.addRow("Target Dimensions (px):", dim_layout)

        self.combo_mode = QComboBox()
        self.combo_mode.addItem("Fit Inside Bounds (fit)", "fit")
        self.combo_mode.addItem("Fill & Center Crop (crop)", "crop")
        self.combo_mode.addItem("Pad to Bounds (pad)", "pad")
        if preset:
            idx = self.combo_mode.findData(preset.resize_mode)
            if idx >= 0:
                self.combo_mode.setCurrentIndex(idx)
        form.addRow("Resize Mode:", self.combo_mode)

        self.chk_upscale = QCheckBox("Allow Deliberate Upscaling")
        self.chk_upscale.setChecked(preset.allow_upscale if preset else False)
        form.addRow("", self.chk_upscale)

        # Compression & Sizing
        self.chk_lossless = QCheckBox("Lossless Compression")
        self.chk_lossless.setChecked(preset.is_lossless if preset else False)
        form.addRow("", self.chk_lossless)

        quality_layout = QHBoxLayout()
        self.slider_quality = QSlider(Qt.Orientation.Horizontal)
        self.slider_quality.setRange(1, 100)
        self.slider_quality.setValue(preset.quality if (preset and preset.quality) else 85)
        self.lbl_quality = QLabel(str(self.slider_quality.value()))
        self.slider_quality.valueChanged.connect(lambda v: self.lbl_quality.setText(str(v)))
        quality_layout.addWidget(self.slider_quality)
        quality_layout.addWidget(self.lbl_quality)
        form.addRow("Quality Setting:", quality_layout)

        self.txt_max_size = QLineEdit()
        self.txt_max_size.setPlaceholderText("e.g. '200 KB' or '1.5 MB'")
        if preset and preset.max_bytes:
            from upload_ready.core.constants import bytes_to_human
            self.txt_max_size.setText(bytes_to_human(preset.max_bytes))
        form.addRow("Max File Size:", self.txt_max_size)

        self.txt_min_size = QLineEdit()
        self.txt_min_size.setPlaceholderText("e.g. '50 KB' (optional)")
        if preset and preset.min_bytes:
            from upload_ready.core.constants import bytes_to_human
            self.txt_min_size.setText(bytes_to_human(preset.min_bytes))
        form.addRow("Min File Size:", self.txt_min_size)

        # Metadata
        self.chk_metadata = QCheckBox("Remove EXIF / GPS / XMP Metadata")
        self.chk_metadata.setChecked(preset.remove_metadata if preset else False)
        form.addRow("", self.chk_metadata)

        layout.addLayout(form)

        # Dialog Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self._validate_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.result_preset: Optional[RequirementPreset] = None

    def _validate_and_accept(self) -> None:
        name = self.txt_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Invalid Name", "Preset must have a name.")
            return

        w = self.spin_width.value() if self.spin_width.value() > 0 else None
        h = self.spin_height.value() if self.spin_height.value() > 0 else None

        max_b = None
        if self.txt_max_size.text().strip():
            try:
                max_b = parse_size_to_bytes(self.txt_max_size.text().strip(), UnitStandard.DECIMAL)
            except Exception as e:
                QMessageBox.warning(self, "Invalid Max Size", f"Could not parse max file size: {e}")
                return

        min_b = None
        if self.txt_min_size.text().strip():
            try:
                min_b = parse_size_to_bytes(self.txt_min_size.text().strip(), UnitStandard.DECIMAL)
            except Exception as e:
                QMessageBox.warning(self, "Invalid Min Size", f"Could not parse min file size: {e}")
                return

        preset_id = self.preset.id if self.preset else name.lower().replace(" ", "_")

        self.result_preset = RequirementPreset(
            id=preset_id,
            name=name,
            description=self.txt_desc.text().strip(),
            target_format=self.combo_format.currentData(),
            width=w,
            height=h,
            resize_mode=self.combo_mode.currentData(),
            allow_upscale=self.chk_upscale.isChecked(),
            is_lossless=self.chk_lossless.isChecked(),
            quality=self.slider_quality.value(),
            max_bytes=max_b,
            min_bytes=min_b,
            remove_metadata=self.chk_metadata.isChecked(),
        )
        self.accept()


class PresetTab(QWidget):
    """Configuration controls for applying and managing declarative requirement presets."""

    def __init__(self):
        super().__init__()
        self.manager = PresetManager()

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Preset selection
        self.combo_presets = QComboBox()
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)
        form.addRow("Active Preset:", self.combo_presets)

        # Preset Management Toolbar
        btn_toolbar = QHBoxLayout()
        self.btn_new = QPushButton("➕ New Preset...")
        self.btn_new.clicked.connect(self._on_new_preset)
        btn_toolbar.addWidget(self.btn_new)

        self.btn_edit = QPushButton("✏️ Edit Preset...")
        self.btn_edit.clicked.connect(self._on_edit_preset)
        btn_toolbar.addWidget(self.btn_edit)

        self.btn_dup = QPushButton("📋 Duplicate...")
        self.btn_dup.clicked.connect(self._on_duplicate_preset)
        btn_toolbar.addWidget(self.btn_dup)

        self.btn_delete = QPushButton("🗑️ Delete")
        self.btn_delete.clicked.connect(self._on_delete_preset)
        btn_toolbar.addWidget(self.btn_delete)
        form.addRow("Manage Presets:", btn_toolbar)

        # Preset Details Display
        self.txt_details = QTextEdit()
        self.txt_details.setReadOnly(True)
        self.txt_details.setMaximumHeight(160)
        self.txt_details.setStyleSheet("font-family: monospace; font-size: 11px;")
        form.addRow("Preset Details:", self.txt_details)

        # Action Buttons (Import / Export)
        btn_layout = QHBoxLayout()
        self.btn_import = QPushButton("📥 Import Preset (JSON)...")
        self.btn_import.clicked.connect(self._on_import_preset)
        btn_layout.addWidget(self.btn_import)

        self.btn_export = QPushButton("📤 Export Preset (JSON)...")
        self.btn_export.clicked.connect(self._on_export_preset)
        btn_layout.addWidget(self.btn_export)
        form.addRow("Exchange:", btn_layout)

        layout.addLayout(form)
        layout.addStretch()

        self._populate_presets()

    def _populate_presets(self) -> None:
        self.combo_presets.clear()
        presets = self.manager.list_presets()
        for p in presets:
            self.combo_presets.addItem(p.name, p.id)
        if presets:
            self._on_preset_selected(0)

    def _on_preset_selected(self, index: int) -> None:
        preset_id = self.combo_presets.currentData()
        if not preset_id:
            self.txt_details.clear()
            return
        preset = self.manager.get_preset(preset_id)
        if preset:
            details_str = json.dumps(preset.to_dict(), indent=2)
            self.txt_details.setText(details_str)

    def _on_new_preset(self) -> None:
        dlg = PresetEditDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_preset:
            p = dlg.result_preset
            # Ensure unique id
            if self.manager.get_preset(p.id):
                p.id = f"{p.id}_{len(self.manager.list_presets()) + 1}"
            self.manager.add_or_update(p)
            self._populate_presets()
            idx = self.combo_presets.findData(p.id)
            if idx >= 0:
                self.combo_presets.setCurrentIndex(idx)
            QMessageBox.information(self, "Preset Created", f"Successfully created preset: {p.name}")

    def _on_edit_preset(self) -> None:
        preset_id = self.combo_presets.currentData()
        if not preset_id:
            return
        preset = self.manager.get_preset(preset_id)
        if not preset:
            return

        dlg = PresetEditDialog(self, preset=preset)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_preset:
            p = dlg.result_preset
            p.id = preset_id  # Preserve original ID
            self.manager.add_or_update(p)
            self._populate_presets()
            idx = self.combo_presets.findData(p.id)
            if idx >= 0:
                self.combo_presets.setCurrentIndex(idx)
            QMessageBox.information(self, "Preset Updated", f"Successfully updated preset: {p.name}")

    def _on_duplicate_preset(self) -> None:
        preset_id = self.combo_presets.currentData()
        if not preset_id:
            return
        preset = self.manager.get_preset(preset_id)
        if not preset:
            return
        new_name = f"{preset.name} (Copy)"
        dup = self.manager.duplicate(preset_id, new_name)
        if dup:
            self._populate_presets()
            idx = self.combo_presets.findData(dup.id)
            if idx >= 0:
                self.combo_presets.setCurrentIndex(idx)
            QMessageBox.information(self, "Preset Duplicated", f"Duplicated as: {dup.name}")

    def _on_delete_preset(self) -> None:
        preset_id = self.combo_presets.currentData()
        if not preset_id:
            return
        preset = self.manager.get_preset(preset_id)
        if not preset:
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete preset '{preset.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.manager.delete(preset_id)
            self._populate_presets()

    def _on_import_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Requirement Preset", "", "JSON Files (*.json)")
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8")
            preset = self.manager.import_preset_json(content)
            self._populate_presets()
            idx = self.combo_presets.findData(preset.id)
            if idx >= 0:
                self.combo_presets.setCurrentIndex(idx)
            QMessageBox.information(self, "Preset Imported", f"Successfully imported preset:\n{preset.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", f"Failed to import preset schema:\n{exc}")

    def _on_export_preset(self) -> None:
        preset_id = self.combo_presets.currentData()
        if not preset_id:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Requirement Preset", f"{preset_id}.json", "JSON Files (*.json)")
        if not path:
            return
        try:
            content = self.manager.export_preset_json(preset_id)
            Path(path).write_text(content, encoding="utf-8")
            QMessageBox.information(self, "Preset Exported", f"Preset exported to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Failed to export preset:\n{exc}")

    def get_options(self) -> RequirementPreset:
        preset_id = self.combo_presets.currentData()
        if not preset_id:
            raise ValueError("No preset selected.")
        preset = self.manager.get_preset(preset_id)
        if not preset:
            raise ValueError(f"Preset '{preset_id}' not found.")
        return preset
