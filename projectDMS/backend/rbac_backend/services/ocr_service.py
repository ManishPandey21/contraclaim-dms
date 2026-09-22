# services/ocr_service.py

import asyncio
import importlib.metadata
import importlib.util
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config.document_processing_config import DocumentProcessingConfig
from ..utils import exceptions as processing_exceptions
from ..utils.pipeline_logging import configure_pipeline_logger
from .extraction.text_quality import assess_native_text_quality

logger = logging.getLogger(__name__)
configure_pipeline_logger(logger)

class DocumentProcessingError(Exception):
    """Custom exception for document processing errors"""
    pass


class TextLayerAssessment(str, Enum):
    """The legacy path's verdict on a PDF's native text layer.

    The two OCR verdicts stay apart because they need different OCRmyPDF runs.
    An empty layer is OCR'd as it always was. An unusable one - pdfminer
    ``(cid:N)`` placeholders, see ``extraction.text_quality`` - has to be
    *replaced*: unforced, OCRmyPDF refuses a PDF that already has a text layer
    (``PriorOcrFoundError``), and ``--skip-text``/``--redo-ocr`` both keep the
    unusable text.
    """

    TEXTUAL = "textual"
    OCR_REQUIRED_EMPTY = "ocr_required_empty"
    OCR_REQUIRED_UNUSABLE_TEXT = "ocr_required_unusable_text"


class UnusableTextLayerError(processing_exceptions.DocumentProcessingError):
    """A PDF's text is unusable and was not replaced by usable OCR text.

    Raised instead of falling back to the original, whose text is the very
    thing that could not be used. The message carries counts only - never the
    document's text.
    """


class OcrMyPdfPriorOcrFound(Exception):
    """OCRmyPDF exit 6: the input already has text and OCR was not forced."""


class OcrMyPdfEncrypted(Exception):
    """OCRmyPDF exit 8: the input is encrypted."""


class OcrMyPdfFailed(Exception):
    """Any other non-zero OCRmyPDF exit. Carries counts, never output text."""


#: OCRmyPDF's documented exit codes (ocrmypdf.ExitCode) this service acts on.
#: Mirrored rather than imported - importing OCRmyPDF to read an IntEnum would
#: reinstate the pdfminer patch this module exists to avoid. Pinned against the
#: installed package by test_ocrmypdf_out_of_process.py.
_EXIT_PRIOR_OCR = 6
_EXIT_ENCRYPTED = 8
_EXIT_NAMES = {
    0: "ok",
    1: "bad_args",
    2: "input_file",
    3: "missing_dependency",
    4: "invalid_output_pdf",
    5: "file_access_error",
    6: "already_done_ocr",
    7: "child_process_error",
    8: "encrypted_pdf",
    9: "invalid_config",
    10: "pdfa_conversion_failed",
    15: "other_error",
    130: "ctrl_c",
}


def _installed_ocrmypdf_version() -> Optional[str]:
    """Return OCRmyPDF's installed version, or None, without importing it."""
    if "ocrmypdf" not in sys.modules:
        try:
            if importlib.util.find_spec("ocrmypdf") is None:
                return None
        except (ImportError, ValueError):
            return None
    try:
        return importlib.metadata.version("ocrmypdf")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


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

    #: Floor, per-page budget and ceiling for one OCRmyPDF run. The floor is
    #: OcrMyPdfRunner's bound for a page batch; the per-page term keeps long
    #: scans, which the in-process call ran untimed, from newly failing.
    OCR_TIMEOUT_SECONDS = 900
    OCR_SECONDS_PER_PAGE = 6
    OCR_TIMEOUT_MAX_SECONDS = 7200

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._ocr_available = self._check_ocr_availability()

    def _check_ocr_availability(self) -> bool:
        """Check if OCR dependencies are available.

        Presence only - deliberately without importing OCRmyPDF. Importing it
        (16.10.4, ``ocrmypdf/pdfinfo/layout.py``) rewrites pdfminer's
        ``PDFSimpleFont.__init__`` and ``PSBaseParser.BUFSIZ`` for the whole
        process, and this check runs on every ``OCRService`` construction, so
        an import here patched every backend process that built a document
        controller. Both paths now run OCRmyPDF as a subprocess and need only
        the ``ocrmypdf`` executable, which ``REQUIRED_BINARIES`` checks, so no
        backend process imports it at all.
        """
        version = _installed_ocrmypdf_version()
        if version is None:
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
            version,
            shutil.which("tesseract"),
            shutil.which("gs"),
            shutil.which("qpdf"),
        )
        return True

    def assess_text_layer(
        self, pdf_path: Path, max_pages: Optional[int] = 5
    ) -> TextLayerAssessment:
        """Judge the first `max_pages` pages' native text, as the pagewise engine does.

        Each non-empty page goes through ``assess_native_text_quality``, the one
        policy both pipelines share. One unusable page makes the layer
        untrustworthy, however much readable text the other pages carry.

        Read with pdfplumber only. The PyPDF2 pass this replaced is not a
        dependency (absent from ``requirements.txt``, so it never ran in a
        deployed or CI interpreter), and its output cannot be judged by a
        policy defined on pdfminer's ``(cid:N)`` rendering.
        """
        try:
            import pdfplumber

            with pdfplumber.open(pdf_path) as pdf:
                texts = [page.extract_text() or "" for page in pdf.pages[:max_pages]]
        except Exception as e:
            logger.error(f"Error checking PDF text content: {e}")
            return TextLayerAssessment.OCR_REQUIRED_EMPTY

        qualities = [assess_native_text_quality(text) for text in texts if text.strip()]
        if any(quality.unusable for quality in qualities):
            return TextLayerAssessment.OCR_REQUIRED_UNUSABLE_TEXT
        if qualities:
            return TextLayerAssessment.TEXTUAL
        return TextLayerAssessment.OCR_REQUIRED_EMPTY

    def is_pdf_textual(self, pdf_path: Path, max_pages: int = 5) -> bool:
        """Check if PDF has a usable native text layer."""
        return self.assess_text_layer(pdf_path, max_pages) is TextLayerAssessment.TEXTUAL

    @staticmethod
    def _unusable_pages(pdf_path: Path) -> List[int]:
        """Every page, not just the inspected few, whose native text is unusable.

        The check every copy-the-original exit runs first. A page pdfplumber
        cannot read is skipped rather than ending the scan, so one malformed
        page cannot hide an unusable one after it; a file that cannot be
        opened at all yields nothing - pdfminer cannot produce placeholders
        from it either.
        """
        unusable_pages: List[int] = []
        try:
            import pdfplumber

            with pdfplumber.open(pdf_path) as pdf:
                for number, page in enumerate(pdf.pages, start=1):
                    try:
                        text = page.extract_text() or ""
                    except Exception:
                        continue
                    if assess_native_text_quality(text).unusable:
                        unusable_pages.append(number)
        except Exception as e:
            logger.warning(f"Could not scan {pdf_path.name} for unusable text: {type(e).__name__}")
        return unusable_pages

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

        run_dir: Optional[Path] = None
        dest_path: Optional[Path] = None
        sidecar_txt_path: Optional[Path] = None
        published = False
        try:
            if not input_path.exists():
                raise DocumentProcessingError(f"Input file not found: {input_path}")

            logger.info("[document_pipeline] OCR processing requested for %s", input_path.name)

            # Create destination directory
            dest_dir = Path(self.config.process_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)

            # Every run owns a directory of its own and writes nothing outside
            # it. Sharing one flat `process_dir/<name>` meant two documents of
            # the same file name wrote, replaced and deleted each other's
            # processed PDF and sidecar - and a failure in the middle of
            # publishing left the survivor holding the other document's text.
            self._sweep_abandoned_runs(dest_dir)
            run_dir = dest_dir / f"run-{uuid.uuid4().hex}"
            run_dir.mkdir(parents=True, exist_ok=True)
            dest_path = run_dir / input_path.name
            sidecar_txt_path = dest_path.with_suffix('.txt')

            raw_ocr_text = None

            # Check whether a text layer exists, and whether it is usable
            assessment = await self._assess_text_layer_async(input_path)

            if not self._ocr_available:
                # No sidecar runs on this branch, so nothing else reads the
                # pages past the first five - of a textual PDF or a scanned
                # one. Judge every page before the original is handed on as a
                # processed PDF.
                loop = asyncio.get_event_loop()
                unusable_pages = await loop.run_in_executor(None, self._unusable_pages, input_path)
                if assessment is TextLayerAssessment.OCR_REQUIRED_UNUSABLE_TEXT or unusable_pages:
                    raise UnusableTextLayerError(
                        "native text layer is unusable and OCR is not available to replace it"
                    )
                # Just copy the file
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._copy_file, input_path, dest_path)
                logger.info("[document_pipeline] OCR not available - copied %s without processing", input_path.name)
                published = True
                return dest_path, None

            if assessment is TextLayerAssessment.TEXTUAL:
                # Copy original file
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._copy_file, input_path, dest_path)
                logger.info("[document_pipeline] Text layer detected for %s - using original PDF", input_path.name)

                # Try to generate sidecar text for better embeddings
                try:
                    raw_ocr_text = await self._extract_sidecar_text(dest_path, sidecar_txt_path)
                except UnusableTextLayerError as e:
                    # The assessment inspects the first pages; the sidecar reads
                    # them all. An unusable later page makes the whole layer
                    # untrustworthy, so it is replaced rather than published.
                    logger.warning(
                        "[document_pipeline] %s: %s - replacing the text layer with OCR",
                        input_path.name,
                        e,
                    )
                    raw_ocr_text = await self._replace_unusable_text_layer(
                        input_path, dest_path, sidecar_txt_path
                    )
                except Exception as e:
                    logger.warning(f"Failed to extract sidecar text: {e}")
            elif assessment is TextLayerAssessment.OCR_REQUIRED_UNUSABLE_TEXT:
                logger.info(
                    "[document_pipeline] Replacing the unusable text layer of %s with OCR",
                    input_path.name,
                )
                raw_ocr_text = await self._replace_unusable_text_layer(
                    input_path, dest_path, sidecar_txt_path
                )
            else:
                # Perform OCR
                logger.info("[document_pipeline] Running OCR preprocessing for %s (no text layer detected)", input_path.name)
                raw_ocr_text = await self._run_ocr_with_sidecar(input_path, dest_path, sidecar_txt_path)

            published = True
            return dest_path, raw_ocr_text

        except UnusableTextLayerError as e:
            logger.error("[document_pipeline] %s not processed: %s", input_path.name, e)
            raise
        except DocumentProcessingError:
            raise
        except Exception as e:
            logger.error(f"PDF processing failed: {e}")
            raise DocumentProcessingError(f"PDF processing failed: {str(e)}")
        finally:
            # Only a failed run's own directory, and only while it is still
            # this run's: a published run's outputs are the document's.
            if not published:
                self._discard_run(input_path, run_dir)

    @staticmethod
    def _discard_run(input_path: Path, run_dir: Optional[Path]) -> None:
        """Remove a failed run's directory, so nothing in it passes for output.

        Everything it deletes was created by this run, inside a directory named
        for this run. The source is never inside one, but it is checked for
        rather than assumed.
        """
        if run_dir is None:
            return
        try:
            source = input_path.resolve()
            if source == run_dir.resolve() or run_dir.resolve() in source.parents:
                logger.warning("Refusing to remove %s: it holds the source file", run_dir)
                return
            shutil.rmtree(run_dir, ignore_errors=True)
        except OSError as exc:
            logger.warning("Could not remove failed run directory %s: %s", run_dir.name, exc)

    def _sweep_abandoned_runs(self, dest_dir: Path) -> None:
        """Delete run directories a killed worker left behind.

        A crash, an OOM kill or a redeploy mid-OCR skips the `finally` above,
        and `process_dir` is a backed-up volume, so abandoned runs would
        accumulate there for ever. Only directories older than one OCR timeout
        are swept, so a run in flight in another worker is never touched.
        """
        cutoff = time.time() - max(self.OCR_TIMEOUT_SECONDS * 2, 3600)
        try:
            candidates = [path for path in dest_dir.glob("run-*") if path.is_dir()]
        except OSError as exc:
            logger.warning("Could not list %s for abandoned runs: %s", dest_dir, exc)
            return
        for candidate in candidates:
            try:
                if candidate.stat().st_mtime < cutoff:
                    shutil.rmtree(candidate, ignore_errors=True)
                    logger.info("[document_pipeline] Swept abandoned run directory %s", candidate.name)
            except OSError as exc:
                logger.warning("Could not sweep %s: %s", candidate.name, exc)

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

    async def _assess_text_layer_async(self, pdf_path: Path) -> TextLayerAssessment:
        """Assess the PDF's text layer asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.assess_text_layer, pdf_path)

    def _ocr_timeout_for(self, input_path: Path, pages: Optional[Sequence[int]]) -> int:
        """Seconds to allow one OCR run, scaled by how many pages it rasterises.

        A flat bound is the wrong shape here: the in-process call this replaced
        had no timeout at all, so a flat 900s would fail long scans that used
        to finish. OcrMyPdfRunner's 900s covers a page *batch*, and this is the
        same budget per page with a floor and a hard ceiling.
        """
        page_count = len(pages) if pages else self._page_count(input_path)
        return int(
            min(
                self.OCR_TIMEOUT_MAX_SECONDS,
                max(self.OCR_TIMEOUT_SECONDS, 120 + self.OCR_SECONDS_PER_PAGE * page_count),
            )
        )

    @staticmethod
    def _page_count(pdf_path: Path) -> int:
        try:
            import pdfplumber

            with pdfplumber.open(pdf_path) as pdf:
                return len(pdf.pages)
        except Exception as e:
            logger.warning(f"Could not count pages of {pdf_path.name}: {type(e).__name__}")
            return 0

    def _ocrmypdf(
        self,
        input_path: Path,
        output_path: Path,
        *,
        force_ocr: bool,
        pages: Optional[Sequence[int]] = None,
    ) -> None:
        """Run OCRmyPDF in a child process, never in this one.

        ``import ocrmypdf`` rewrites pdfminer's ``PDFSimpleFont.__init__`` and
        ``PSBaseParser.BUFSIZ`` for the whole interpreter. Run in-process, the
        first legacy OCR in a worker made every later extraction in that
        worker - legacy and pagewise alike - read simple fonts without an
        ``/Encoding`` as ``(cid:N)``, so the same PDF was textual in a fresh
        worker and force-OCR'd in a warm one. The pagewise OcrMyPdfRunner
        already runs it as a subprocess; this does the same, with the same
        options the in-process call used.
        """
        executable = shutil.which("ocrmypdf")
        prefix = [executable] if executable else [sys.executable, "-m", "ocrmypdf"]
        command = [
            *prefix,
            "--language",
            self.config.ocr_language,
            "--rotate-pages",
            "--deskew",
            "--optimize",
            "1",
            "--jobs",
            str(min(2, os.cpu_count() or 3)),
        ]
        if pages:
            # Only the pages whose text is unusable, as OcrMyPdfRunner does.
            # Forcing the whole document would rasterise every good page too,
            # destroying native text that was never in question.
            command += ["--pages", ",".join(str(int(page)) for page in pages)]
        if force_ocr:
            # Forced only to replace an unusable text layer: forcing
            # rasterises the pages it covers, including any whose text is fine.
            command.append("--force-ocr")
        command += [str(input_path), str(output_path)]

        timeout = self._ocr_timeout_for(input_path, pages)
        try:
            # Bytes, not text: the streams are the customer's PDF as OCRmyPDF
            # read it, and decoding them under the host's locale can raise
            # before the exit code is ever looked at. Only their lengths are
            # used. The timeout is OcrMyPdfRunner's, so both paths bound an OCR
            # run the same way instead of hanging a worker thread for ever.
            result = self._run_ocrmypdf_process(command, timeout)
        except subprocess.TimeoutExpired as exc:
            raise OcrMyPdfFailed(f"exit=timeout after {timeout}s") from exc
        if result.returncode == 0:
            return
        # Exit code and stream lengths only: OCRmyPDF reads the customer's PDF,
        # so both streams can quote its text. The exit name is OCRmyPDF's own
        # constant, not document content, and without it a production failure
        # reads only as a number.
        detail = (
            f"exit={result.returncode} ({_EXIT_NAMES.get(result.returncode, 'unknown')}), "
            f"stderr_chars={len(result.stderr or '')}, "
            f"stdout_chars={len(result.stdout or '')}"
        )
        if result.returncode == _EXIT_PRIOR_OCR:
            raise OcrMyPdfPriorOcrFound(detail)
        if result.returncode == _EXIT_ENCRYPTED:
            raise OcrMyPdfEncrypted(detail)
        raise OcrMyPdfFailed(detail)

    @staticmethod
    def _run_ocrmypdf_process(command: List[str], timeout: int) -> Any:
        """Run `command`, and on timeout kill the whole process tree it started.

        OCRmyPDF forks ghostscript, tesseract and unpaper. Killing only the
        direct child leaves those running, so a timed-out document would keep
        burning both worker cores while its retry starts a second run.
        """
        popen_kwargs: Dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        if hasattr(os, "setsid"):
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **popen_kwargs)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # getpgid/killpg/SIGKILL are POSIX-only; production is Linux, and
            # elsewhere killing the child itself is all that is available.
            kill_group = getattr(os, "killpg", None)
            group_of = getattr(os, "getpgid", None)
            sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            if kill_group is not None and group_of is not None:
                try:
                    kill_group(group_of(process.pid), sigkill)
                except OSError:
                    process.kill()
            else:
                process.kill()
            process.communicate()
            raise
        return SimpleNamespace(returncode=process.returncode, stdout=stdout, stderr=stderr)

    async def _run_ocr_with_sidecar(self, input_path: Path, output_path: Path, sidecar_path: Path) -> Optional[str]:
        """Run OCR and generate sidecar text file"""
        try:
            loop = asyncio.get_event_loop()

            try:
                # Run OCR in executor
                await loop.run_in_executor(
                    None,
                    lambda: self._ocrmypdf(input_path, output_path, force_ocr=False),
                )

                logger.info("[document_pipeline] OCR processing completed for %s -> %s", input_path.name, output_path)

                # Generate sidecar text file
                return await self._extract_sidecar_text(output_path, sidecar_path, what="OCR output")

            except OcrMyPdfPriorOcrFound:
                logger.info("[document_pipeline] Prior OCR found for %s - using original file", input_path.name)
                await loop.run_in_executor(None, self._copy_file, input_path, output_path)
                try:
                    return await self._extract_sidecar_text(output_path, sidecar_path)
                except UnusableTextLayerError as e:
                    # The prior text is the unusable kind: keeping the original
                    # would publish it. Replace it instead.
                    logger.warning(
                        "[document_pipeline] %s: %s - replacing the text layer with OCR",
                        input_path.name,
                        e,
                    )
                    return await self._replace_unusable_text_layer(input_path, output_path, sidecar_path)

            except OcrMyPdfEncrypted as e:
                logger.warning(f"PDF is encrypted: {e}")
                raise processing_exceptions.DocumentProcessingError(
                    "Cannot process encrypted PDF"
                )

        except (UnusableTextLayerError, processing_exceptions.DocumentProcessingError):
            # Never the copy-the-original fallback below. An unusable original
            # is the thing that could not be used, and a refusal that fell
            # through to the fallback - as the encrypted one did - was reported
            # as a successful run of a document with no text at all.
            raise
        except Exception as e:
            logger.error(f"OCR processing failed: {e}")
            loop = asyncio.get_event_loop()
            # The copy below is only a fallback for an original with nothing
            # unusable in it. The decision inspected five pages, and OCRmyPDF
            # may have died before reaching a later page with bad text.
            if await loop.run_in_executor(None, self._unusable_pages, input_path):
                logger.warning(
                    "[document_pipeline] %s has an unusable text layer - replacing it with OCR "
                    "instead of copying the original",
                    input_path.name,
                )
                return await self._replace_unusable_text_layer(input_path, output_path, sidecar_path)
            # Fallback: copy original file
            try:
                await loop.run_in_executor(None, self._copy_file, input_path, output_path)
                logger.info("[document_pipeline] OCR fallback: copied original file %s to %s", input_path.name, output_path)
            except Exception as copy_error:
                logger.error(f"Failed to copy original file: {copy_error}")
                raise DocumentProcessingError(f"OCR failed and unable to copy original: {copy_error}")

            return None

    async def _replace_unusable_text_layer(
        self, input_path: Path, output_path: Path, sidecar_path: Path
    ) -> Optional[str]:
        """Force OCR over the unusable pages; fail rather than keep their text.

        Only the pages whose text is unusable are forced. Forcing the whole
        document would rasterise every good page as well, replacing native text
        that was never in question - on a long born-digital contract with one
        bad page that is both a fidelity loss and minutes of needless OCR.

        There is deliberately no copy-the-original fallback. Every failure -
        OCRmyPDF missing, refusing the text as prior OCR, any other OCR error,
        or OCR output that is itself unusable - raises ``UnusableTextLayerError``.
        Only exception type names are reported, never their messages.
        """
        unreplaced = "native text layer is unusable and could not be replaced"
        loop = asyncio.get_event_loop()
        pages = await loop.run_in_executor(None, self._unusable_pages, input_path)
        try:
            await loop.run_in_executor(
                None,
                lambda: self._ocrmypdf(
                    input_path, output_path, force_ocr=True, pages=pages or None
                ),
            )
        except OcrMyPdfPriorOcrFound as exc:
            raise UnusableTextLayerError(
                f"{unreplaced}: OCRmyPDF refused it as prior OCR"
            ) from exc
        except Exception as exc:
            raise UnusableTextLayerError(
                f"{unreplaced}: OCR failed ({type(exc).__name__})"
            ) from exc

        logger.info(
            "[document_pipeline] Forced OCR replaced the text layer of %s -> %s",
            input_path.name,
            output_path,
        )
        return await self._extract_sidecar_text(output_path, sidecar_path, what="OCR output")

    async def _extract_sidecar_text(
        self, pdf_path: Path, sidecar_path: Path, *, what: str = "native text layer"
    ) -> Optional[str]:
        """Extract text to sidecar file for better embeddings.

        Every page is judged by ``assess_native_text_quality``. A page of
        ``(cid:N)`` placeholders raises ``UnusableTextLayerError`` and nothing
        is written: its text is neither returned nor stripped of placeholders
        and passed off as the page. Dropping only that page would lose it
        silently; the caller decides whether OCR can replace it.
        """
        try:
            import pdfplumber

            loop = asyncio.get_event_loop()

            def extract_text():
                full_text = ""
                # Page numbers and describe()'s glyph counts only: this message
                # reaches logs and the document's error field.
                unusable_pages: List[int] = []
                first_unusable_reason = ""
                with pdfplumber.open(pdf_path) as pdf:
                    total_pages = len(pdf.pages)
                    for number, page in enumerate(pdf.pages, start=1):
                        text = page.extract_text() or ""
                        quality = assess_native_text_quality(text)
                        if quality.unusable:
                            unusable_pages.append(number)
                            first_unusable_reason = first_unusable_reason or quality.describe()
                        full_text += text + "\n"

                if unusable_pages:
                    raise UnusableTextLayerError(
                        f"{what} is unusable on {len(unusable_pages)} of {total_pages} "
                        f"page(s); page {unusable_pages[0]}: {first_unusable_reason}"
                    )

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

        except UnusableTextLayerError:
            # The quality verdict is this method's job, not an extraction
            # failure to be logged and swallowed.
            raise
        except Exception as e:
            logger.warning(f"Failed to extract sidecar text: {e}")
            # None sends the caller on with this PDF unverified. A read error
            # must not also discard the quality verdict on the pages that did
            # read, or on those after the one that failed.
            unusable_pages = await asyncio.get_event_loop().run_in_executor(
                None, self._unusable_pages, pdf_path
            )
            if unusable_pages:
                raise UnusableTextLayerError(
                    f"{what} is unusable on {len(unusable_pages)} page(s), first page "
                    f"{unusable_pages[0]} ({type(e).__name__} while reading)"
                ) from e
            return None
