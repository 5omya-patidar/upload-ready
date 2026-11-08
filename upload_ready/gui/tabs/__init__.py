"""UI configuration tabs for each supported operation."""

from upload_ready.gui.tabs.compress_tab import CompressTab
from upload_ready.gui.tabs.convert_tab import ConvertTab
from upload_ready.gui.tabs.id_layout_tab import IdLayoutTab
from upload_ready.gui.tabs.images_to_pdf_tab import ImagesToPdfTab
from upload_ready.gui.tabs.metadata_tab import MetadataTab
from upload_ready.gui.tabs.pdf_organize_tab import PdfOrganizeTab
from upload_ready.gui.tabs.pdf_reduce_tab import PdfReduceTab
from upload_ready.gui.tabs.pdf_to_images_tab import PdfToImagesTab
from upload_ready.gui.tabs.photo_sheet_tab import PhotoSheetTab
from upload_ready.gui.tabs.preset_tab import PresetTab
from upload_ready.gui.tabs.resize_tab import ResizeTab
from upload_ready.gui.tabs.watermark_tab import WatermarkTab

__all__ = [
    "ConvertTab",
    "ResizeTab",
    "CompressTab",
    "ImagesToPdfTab",
    "PdfToImagesTab",
    "PdfReduceTab",
    "PresetTab",
    "PdfOrganizeTab",
    "MetadataTab",
    "PhotoSheetTab",
    "WatermarkTab",
    "IdLayoutTab",
]
