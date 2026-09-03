# services/ocr_service.py

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

from ..config.document_processing_config import DocumentProcessingConfig
from ..utils.pipeline_logging import configure_pipeline_logger

logger = logging.getLogger(__name__)
configure_pipeline_logger(logger)

class DocumentProcessingError(Exception):
    """Custom exception for document processing errors"""
    pass

class OCRService:
    """Service for OCR operations with proper error handling"""

    REQUIRED_BINARIES = (
        "ocrmypdf",
        "tesseract",
        "gs",
        "qpdf",
        "unpaper",
        "pngquant",
        "pdfinfo",
        "pdftotext",
    )

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._ocr_available = self._check_ocr_availability()

    def _check_ocr_availability(self) -> bool:
        """Check if OCR dependencies are available"""
        try:
            import ocrmypdf
        except ImportError:
            logger.warning("OCRmyPDF not available - scanned PDFs may not be processed correctly")
            return False

        missing = [binary for binary in self.REQUIRED_BINARIES if shutil.which(binary) is None]
        if missing:
            logger.warning(
                "OCRmyPDF system dependencies missing from PATH: %s. PATH=%s",
                ", ".join(missing),
                os.environ.get("PATH", ""),
            )
            return False

        logger.info(
            "OCR dependencies available: ocrmypdf=%s, tesseract=%s, gs=%s, qpdf=%s",
            getattr(ocrmypdf, "__version__", "unknown"),
            shutil.which("tesseract"),
            shutil.which("gs"),
            shutil.which("qpdf"),
        )
        return True

    def is_pdf_textual(self, pdf_path: Path, max_pages: int = 5) -> bool:
        """Check if PDF has extractable text"""
        try:
            # Try PyPDF2 first
            try:
                import PyPDF2
                with open(pdf_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    pages_to_scan = min(len(reader.pages), max_pages)

                    for i in range(pages_to_scan):
                        text = reader.pages[i].extract_text() or ""
                        if text.strip():
                            return True
                    return False

            except Exception:
                pass

            # Fallback to pdfplumber (more reliable)
            try:
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    pages_to_scan = min(len(pdf.pages), max_pages)
                    for i in range(pages_to_scan):
                        text = pdf.pages[i].extract_text() or ""
                        if text.strip():
                            return True
                    return False

            except Exception:
                return False

        except Exception as e:
            logger.error(f"Error checking PDF text content: {e}")
            return False

    async def process_pdf(self, input_path: Path) -> Tuple[Path, Optional[str]]:
        """
        Process PDF with OCR if needed and return processed file path and raw OCR text.

        Args:
            input_path: Path to input PDF

        Returns:
            Tuple of (processed_file_path, raw_ocr_text)

        Raises:
            DocumentProcessingError: If processing fails
        """
        from ..utils.exceptions import DocumentProcessingError

        try:
            if not input_path.exists():
                raise DocumentProcessingError(f"Input file not found: {input_path}")

            logger.info("[document_pipeline] OCR processing requested for %s", input_path.name)

            # Create destination directory
            dest_dir = Path(self.config.process_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)

            dest_path = dest_dir / input_path.name
            sidecar_txt_path = dest_path.with_suffix('.txt')

            raw_ocr_text = None

            if not self._ocr_available:
                # Just copy the file
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._copy_file, input_path, dest_path)
                logger.info("[document_pipeline] OCR not available - copied %s without processing", input_path.name)
                return dest_path, None

            # Check if text layer exists
            has_text = await self._check_pdf_textual_async(input_path)

            if has_text:
                # Copy original file
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._copy_file, input_path, dest_path)
                logger.info("[document_pipeline] Text layer detected for %s - using original PDF", input_path.name)

                # Try to generate sidecar text for better embeddings
                try:
                    raw_ocr_text = await self._extract_sidecar_text(dest_path, sidecar_txt_path)
                except Exception as e:
                    logger.warning(f"Failed to extract sidecar text: {e}")
            else:
                # Perform OCR
                logger.info("[document_pipeline] Running OCR preprocessing for %s (no text layer detected)", input_path.name)
                raw_ocr_text = await self._run_ocr_with_sidecar(input_path, dest_path, sidecar_txt_path)

            return dest_path, raw_ocr_text

        except DocumentProcessingError:
            raise
        except Exception as e:
            logger.error(f"PDF processing failed: {e}")
            raise DocumentProcessingError(f"PDF processing failed: {str(e)}")

    async def process_pdf_pagewise(
        self,
        input_path: Path,
        *,
        store: Any,
        document_id: str,
        ocr_runner: Any = None,
        retry_pages: Optional[Sequence[int]] = None,
    ):
        """Extract a PDF page by page, OCR-ing only the pages that need it.

        Replaces the document-level `is_pdf_textual` decision for the general
        path. That decision inspected the first five pages and, on finding any
        text, skipped OCR for the entire document - so a scanned covering
        letter behind a textual body was indexed as empty.
        """
        from ..core.config import settings
        from .extraction.engine import PageExtractionEngine
        from .extraction.models import PageExtractionPolicy
        from .extraction.ocrmypdf_runner import OcrMyPdfRunner

        work_dir = Path(self.config.process_dir) / "page_batches" / document_id
        runner = ocr_runner or OcrMyPdfRunner(work_dir=work_dir)

        engine = PageExtractionEngine(
            policy=PageExtractionPolicy(
                ocr_enabled=self.config.ocr_enabled and self._ocr_available,
                min_text_chars_per_page=max(
                    0, int(self.config.contract_ocr_min_text_chars_per_page)
                ),
                batch_size=max(1, int(self.config.contract_ocr_batch_size)),
                max_ocr_pages_per_attempt=int(
                    getattr(settings, "DOCUMENT_OCR_MAX_PAGES_PER_ATTEMPT", 0)
                ),
                ocr_language=self.config.ocr_language,
            ),
            ocr_runner=runner,
            store=store,
        )
        return await engine.extract(input_path, retry_pages=retry_pages)

    async def process_document(self, input_path: Path, language: Optional[str] = None) -> Path:
        """
        Backwards-compatible wrapper used by older ingestion code.
        Runs OCR (if needed) and returns the processed PDF path.
        """
        if input_path.suffix.lower() != ".pdf":
            logger.info(
                "[document_pipeline] Skipping OCR for non-PDF contract input %s",
                input_path.name,
            )
            return input_path
        original_language = self.config.ocr_language
        if language and language != original_language:
            self.config.ocr_language = language
        try:
            processed_path, _ = await self.process_pdf(input_path)
            return processed_path
        finally:
            self.config.ocr_language = original_language

    @staticmethod
    def _copy_file(src: Path, dst: Path):
        """Copy file synchronously"""
        import shutil
        shutil.copy2(src, dst)

    async def _check_pdf_textual_async(self, pdf_path: Path) -> bool:
        """Check PDF text content asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.is_pdf_textual, pdf_path)

    async def _run_ocr_with_sidecar(self, input_path: Path, output_path: Path, sidecar_path: Path) -> Optional[str]:
        """Run OCR and generate sidecar text file"""
        try:
            import ocrmypdf
            from ocrmypdf.exceptions import PriorOcrFoundError, EncryptedPdfError

            loop = asyncio.get_event_loop()

            try:
                # Run OCR in executor
                await loop.run_in_executor(
                    None,
                    lambda: ocrmypdf.ocr(
                        input_file=str(input_path),
                        output_file=str(output_path),
                        language=self.config.ocr_language,
                        rotate_pages=True,
                        deskew=True,
                        optimize=1,
                        force_ocr=False,  # Force OCR for scanned documents
                        jobs=min(2, os.cpu_count() or 3)
                    )
                )

                logger.info("[document_pipeline] OCR processing completed for %s -> %s", input_path.name, output_path)

                # Generate sidecar text file
                return await self._extract_sidecar_text(output_path, sidecar_path)

            except PriorOcrFoundError:
                logger.info("[document_pipeline] Prior OCR found for %s - using original file", input_path.name)
                await loop.run_in_executor(None, self._copy_file, input_path, output_path)
                return await self._extract_sidecar_text(output_path, sidecar_path)

            except EncryptedPdfError as e:
                logger.warning(f"PDF is encrypted: {e}")
                raise DocumentProcessingError("Cannot process encrypted PDF")

        except Exception as e:
            logger.error(f"OCR processing failed: {e}")
            # Fallback: copy original file
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._copy_file, input_path, output_path)
                logger.info("[document_pipeline] OCR fallback: copied original file %s to %s", input_path.name, output_path)
            except Exception as copy_error:
                logger.error(f"Failed to copy original file: {copy_error}")
                raise DocumentProcessingError(f"OCR failed and unable to copy original: {copy_error}")

            return None

    async def _extract_sidecar_text(self, pdf_path: Path, sidecar_path: Path) -> Optional[str]:
        """Extract text to sidecar file for better embeddings"""
        try:
            import pdfplumber

            loop = asyncio.get_event_loop()

            def extract_text():
                full_text = ""
                with pdfplumber.open(pdf_path) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text() or ""
                        full_text += text + "\n"

                # Save to sidecar file
                sidecar_path.write_text(full_text, encoding='utf-8')
                return full_text.strip()

            raw_text = await loop.run_in_executor(None, extract_text)

            if raw_text:
                logger.info("[document_pipeline] Extracted %s characters of sidecar text for %s", len(raw_text), pdf_path.name)
                return raw_text
            else:
                logger.warning("No text could be extracted from PDF")
                return None

        except Exception as e:
            logger.warning(f"Failed to extract sidecar text: {e}")
            return None
