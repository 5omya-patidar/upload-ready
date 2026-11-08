"""Background processing worker with progress reporting and cancellation support."""

from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from PySide6.QtCore import QObject, QThread, Signal

from upload_ready.core.id_layout import IdLayoutOptions, generate_id_layout
from upload_ready.core.image_compress import compress_image
from upload_ready.core.image_convert import convert_image
from upload_ready.core.image_resize import resize_image
from upload_ready.core.metadata import scrub_metadata
from upload_ready.core.models import (
    CancellationToken,
    CompressOptions,
    ConvertOptions,
    ImagesToPdfOptions,
    JobResult,
    OperationCancelledError,
    OperationType,
    PageOrientation,
    PdfReduceOptions,
    PdfToImagesOptions,
    ResizeOptions,
)
from upload_ready.core.pdf_ops import images_to_pdf, pdf_to_images, reduce_pdf_size
from upload_ready.core.pdf_organizer import (
    OrganizedPage,
    export_organized_pdf,
    merge_multiple_pdfs,
)
from upload_ready.core.photo_sheet import PhotoSheetOptions, generate_photo_sheet
from upload_ready.core.presets import RequirementPreset, apply_preset_to_image
from upload_ready.core.safety import TempJobWorkspace, sniff_content_type
from upload_ready.core.watermark import (
    WatermarkOptions,
    apply_watermark_to_image,
    apply_watermark_to_pdf,
)


class BatchWorkerSignals(QObject):
    progress = Signal(int, int, str)       # (current_index, total_count, current_filename)
    item_finished = Signal(JobResult)     # Per-item result
    batch_finished = Signal(list)          # All results
    cancelled = Signal()
    error = Signal(str)


class BatchProcessingWorker(QThread):
    """Executes file operations sequentially or in controlled batches off the main UI thread."""

    def __init__(
        self,
        operation: OperationType,
        input_paths: List[Path],
        output_dir: Path,
        options: object,
        allow_overwrite: bool = False,
        pdf_output_name: str = "combined.pdf",
        passwords: Optional[Dict[str, str]] = None,
    ):
        super().__init__()
        self.operation = operation
        self.input_paths = input_paths
        self.output_dir = output_dir
        self.options = options
        self.allow_overwrite = allow_overwrite
        self.pdf_output_name = pdf_output_name
        self.passwords = passwords or {}
        self.signals = BatchWorkerSignals()
        self.cancel_token = CancellationToken()

    def cancel(self) -> None:
        """Signal worker to abort execution cooperatively at safe checkpoints."""
        self.cancel_token.cancel()

    def run(self) -> None:
        results: List[JobResult] = []
        total = len(self.input_paths)

        # Ensure output directory exists within error handling
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            for p in self.input_paths:
                fail_res = JobResult(
                    operation=self.operation,
                    input_path=p,
                    success=False,
                    error_message=f"Could not create output directory: {exc}",
                )
                results.append(fail_res)
                self.signals.item_finished.emit(fail_res)
            self.signals.batch_finished.emit(results)
            return

        # Single combined operation: images_to_pdf
        if self.operation == OperationType.IMAGES_TO_PDF:
            if self.cancel_token.is_cancelled:
                self.signals.cancelled.emit()
                return
            self.signals.progress.emit(1, 1, self.pdf_output_name)
            out_pdf = self.output_dir / self.pdf_output_name
            res = images_to_pdf(
                self.input_paths,
                out_pdf,
                self.options,  # type: ignore
                allow_overwrite=self.allow_overwrite,
                cancel_token=self.cancel_token,
            )
            results.append(res)
            self.signals.item_finished.emit(res)
            if self.cancel_token.is_cancelled:
                self.signals.cancelled.emit()
            else:
                self.signals.batch_finished.emit(results)
            return

        # Single combined operation: PDF Organize
        if self.operation == OperationType.PDF_ORGANIZE:
            if self.cancel_token.is_cancelled:
                self.signals.cancelled.emit()
                return
            self.signals.progress.emit(1, 1, self.pdf_output_name)
            out_pdf = self.output_dir / self.pdf_output_name
            if isinstance(self.options, list):
                res = export_organized_pdf(
                    pages=self.options,
                    output_path=out_pdf,
                    passwords=self.passwords,
                    allow_overwrite=self.allow_overwrite,
                    cancel_token=self.cancel_token,
                    batch_inputs=self.input_paths,
                )
            else:
                res = merge_multiple_pdfs(
                    input_paths=self.input_paths,
                    output_path=out_pdf,
                    allow_overwrite=self.allow_overwrite,
                    cancel_token=self.cancel_token,
                    batch_inputs=self.input_paths,
                )
            results.append(res)
            self.signals.item_finished.emit(res)
            if self.cancel_token.is_cancelled:
                self.signals.cancelled.emit()
            else:
                self.signals.batch_finished.emit(results)
            return

        # Single combined operation: ID layout
        if self.operation == OperationType.ID_LAYOUT:
            if self.cancel_token.is_cancelled:
                self.signals.cancelled.emit()
                return
            self.signals.progress.emit(1, 1, self.pdf_output_name)
            if len(self.input_paths) < 2:
                fail_res = JobResult(
                    operation=self.operation,
                    input_path=self.input_paths[0] if self.input_paths else Path("none"),
                    success=False,
                    error_message="ID layout requires both front and back card images.",
                )
                results.append(fail_res)
                self.signals.item_finished.emit(fail_res)
                self.signals.batch_finished.emit(results)
                return

            ext = getattr(self.options, "output_format", "PDF").lower()
            out_file = self.output_dir / self.pdf_output_name
            res = generate_id_layout(
                front_path=self.input_paths[0],
                back_path=self.input_paths[1],
                output_path=out_file,
                options=self.options,  # type: ignore
                allow_overwrite=self.allow_overwrite,
                cancel_token=self.cancel_token,
                batch_inputs=self.input_paths,
            )
            results.append(res)
            self.signals.item_finished.emit(res)
            if self.cancel_token.is_cancelled:
                self.signals.cancelled.emit()
            else:
                self.signals.batch_finished.emit(results)
            return

        # Per-file operations
        for idx, path in enumerate(self.input_paths):
            if self.cancel_token.is_cancelled:
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(idx + 1, total, path.name)

            try:
                if self.operation == OperationType.CONVERT:
                    res = convert_image(
                        path,
                        self.output_dir,
                        self.options,  # type: ignore
                        allow_overwrite=self.allow_overwrite,
                        cancel_token=self.cancel_token,
                        batch_inputs=self.input_paths,
                    )
                elif self.operation == OperationType.RESIZE:
                    res = resize_image(
                        path,
                        self.output_dir,
                        self.options,  # type: ignore
                        allow_overwrite=self.allow_overwrite,
                        cancel_token=self.cancel_token,
                        batch_inputs=self.input_paths,
                    )
                elif self.operation == OperationType.COMPRESS:
                    res = compress_image(
                        path,
                        self.output_dir,
                        self.options,  # type: ignore
                        allow_overwrite=self.allow_overwrite,
                        cancel_token=self.cancel_token,
                        batch_inputs=self.input_paths,
                    )
                elif self.operation == OperationType.PDF_TO_IMAGES:
                    res = pdf_to_images(
                        path,
                        self.output_dir,
                        self.options,  # type: ignore
                        allow_overwrite=self.allow_overwrite,
                        cancel_token=self.cancel_token,
                        batch_inputs=self.input_paths,
                    )
                elif self.operation == OperationType.PDF_REDUCE:
                    out_pdf = self.output_dir / f"{path.stem}_reduced.pdf"
                    res = reduce_pdf_size(
                        path,
                        out_pdf,
                        self.options,  # type: ignore
                        allow_overwrite=self.allow_overwrite,
                        cancel_token=self.cancel_token,
                        batch_inputs=self.input_paths,
                    )
                elif self.operation == OperationType.PRESET:
                    res = apply_preset_to_image(
                        path,
                        self.output_dir,
                        self.options,  # type: ignore
                        allow_overwrite=self.allow_overwrite,
                        cancel_token=self.cancel_token,
                        batch_inputs=self.input_paths,
                    )
                elif self.operation == OperationType.METADATA:
                    categories = getattr(self.options, "categories", ["exif", "gps", "xmp", "comments", "pdf_docinfo", "pdf_xmp"])
                    pw = getattr(self.options, "password", None)
                    res = scrub_metadata(
                        path,
                        self.output_dir,
                        categories=set(categories),
                        password=pw,
                        allow_overwrite=self.allow_overwrite,
                        cancel_token=self.cancel_token,
                        batch_inputs=self.input_paths,
                    )
                elif self.operation == OperationType.WATERMARK:
                    c_type = sniff_content_type(path)
                    if c_type == "PDF":
                        out_pdf = self.output_dir / f"{path.stem}_watermarked.pdf"
                        res = apply_watermark_to_pdf(
                            path,
                            out_pdf,
                            self.options,  # type: ignore
                            allow_overwrite=self.allow_overwrite,
                            cancel_token=self.cancel_token,
                            batch_inputs=self.input_paths,
                        )
                    else:
                        res = apply_watermark_to_image(
                            path,
                            self.output_dir,
                            self.options,  # type: ignore
                            allow_overwrite=self.allow_overwrite,
                            cancel_token=self.cancel_token,
                            batch_inputs=self.input_paths,
                        )
                elif self.operation == OperationType.PHOTO_SHEET:
                    ext = getattr(self.options, "output_format", "PDF").lower()
                    out_file = self.output_dir / f"{path.stem}_sheet.{ext}"
                    res = generate_photo_sheet(
                        path,
                        out_file,
                        self.options,  # type: ignore
                        allow_overwrite=self.allow_overwrite,
                        cancel_token=self.cancel_token,
                        batch_inputs=self.input_paths,
                    )
                else:
                    raise ValueError(f"Unknown operation: {self.operation}")

                if self.cancel_token.is_cancelled:
                    self.signals.cancelled.emit()
                    return

                results.append(res)
                self.signals.item_finished.emit(res)

            except Exception as exc:
                if self.cancel_token.is_cancelled:
                    self.signals.cancelled.emit()
                    return
                fail_res = JobResult(
                    operation=self.operation,
                    input_path=path,
                    success=False,
                    error_message=str(exc),
                )
                results.append(fail_res)
                self.signals.item_finished.emit(fail_res)

        if self.cancel_token.is_cancelled:
            self.signals.cancelled.emit()
        else:
            self.signals.batch_finished.emit(results)
