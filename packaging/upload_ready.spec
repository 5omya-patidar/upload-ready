# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

datas = []
datas += collect_data_files('pypdfium2')
binaries = []
binaries += collect_dynamic_libs('pypdfium2')

a = Analysis(
    ['..\\upload_ready\\__main__.py'],
    pathex=['..'],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'upload_ready.core.constants',
        'upload_ready.core.safety',
        'upload_ready.core.models',
        'upload_ready.core.image_convert',
        'upload_ready.core.image_resize',
        'upload_ready.core.image_compress',
        'upload_ready.core.pdf_ops',
        'upload_ready.core.presets',
        'upload_ready.core.pdf_organizer',
        'upload_ready.core.metadata',
        'upload_ready.core.photo_sheet',
        'upload_ready.core.watermark',
        'upload_ready.core.id_layout',
        'upload_ready.cli',
        'upload_ready.gui.app',
        'upload_ready.gui.main_window',
        'upload_ready.gui.file_queue',
        'upload_ready.gui.preview_widget',
        'upload_ready.gui.report_dialog',
        'upload_ready.gui.worker',
        'upload_ready.gui.tabs.convert_tab',
        'upload_ready.gui.tabs.resize_tab',
        'upload_ready.gui.tabs.compress_tab',
        'upload_ready.gui.tabs.images_to_pdf_tab',
        'upload_ready.gui.tabs.pdf_to_images_tab',
        'upload_ready.gui.tabs.pdf_reduce_tab',
        'upload_ready.gui.tabs.preset_tab',
        'upload_ready.gui.tabs.pdf_organize_tab',
        'upload_ready.gui.tabs.metadata_tab',
        'upload_ready.gui.tabs.photo_sheet_tab',
        'upload_ready.gui.tabs.watermark_tab',
        'upload_ready.gui.tabs.id_layout_tab',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Desktop GUI executable (windowed, no console window)
exe_gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='UploadReady',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# CLI executable (terminal output, pipe-friendly)
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='upload-ready',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='UploadReady',
)
