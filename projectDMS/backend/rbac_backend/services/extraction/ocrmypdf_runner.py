"""Run OCRmyPDF over a batch of pages and return their text by page number.

The page mapping is explicit (see page_mapping) rather than inferred from the
output's shape. A mismatch raises instead of silently attributing one page's
text to another.

The input page count is read from the source BEFORE OCRmyPDF runs, so the
mapping has both counts and never has to guess one from the other.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Sequence

from .numeric_integrity import readable_page
from .page_mapping import PageMappingError, map_source_pages_to_output

logger = logging.getLogger(__name__)


class OcrRunnerError(Exception):
    """Raised when an OCR batch cannot be run or its output cannot be mapped."""


class OcrMyPdfRunner:
    def __init__(self, *, work_dir: Path, timeout: int = 900) -> None:
        self.work_dir = Path(work_dir)
        self.timeout = timeout

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        pages = sorted({int(page) for page in page_numbers if int(page) > 0})
        if not pages:
            return {}

        input_page_count = await asyncio.to_thread(self._input_page_count, source)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        page_range = self._format_page_range(pages)
        output = (
            self.work_dir / f"{source.stem}_pages_{page_range.replace('-', '_')}.pdf"
        )
        sidecar = output.with_suffix(".txt")

        command = self._build_command(
            source=source,
            output=output,
            sidecar=sidecar,
            page_numbers=pages,
            language=language,
        )
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if result.returncode != 0:
            # Exit code and lengths, not the streams themselves. OCRmyPDF reads
            # the customer's PDF, so both channels can quote its text. Same
            # class as the Marker stderr channel closed in R-A8S.
            raise OcrRunnerError(
                "OCRmyPDF batch failed "
                f"(exit={result.returncode}, "
                f"stderr_chars={len(result.stderr or '')}, "
                f"stdout_chars={len(result.stdout or '')})"
            )

        return await asyncio.to_thread(
            self._extract_mapped_pages,
            output_path=output,
            page_numbers=pages,
            input_page_count=input_page_count,
        )

    def _build_command(
        self,
        *,
        source: Path,
        output: Path,
        sidecar: Path,
        page_numbers: Sequence[int],
        language: str,
    ) -> List[str]:
        executable = shutil.which("ocrmypdf")
        prefix = [executable] if executable else [sys.executable, "-m", "ocrmypdf"]
        return [
            *prefix,
            "--pages",
            self._format_page_range(page_numbers),
            # Every page the engine sends either has no text layer or has one
            # it judged unusable (too thin, or dominated by (cid:N)
            # placeholders). Without this, one such page with any text makes
            # OCRmyPDF abort the whole batch with PriorOcrFoundError (exit 6),
            # taking its scanned neighbours down with it. --skip-text and
            # --redo-ocr would keep the unusable text instead of reading it.
            "--force-ocr",
            "--language",
            language,
            "--rotate-pages",
            "--deskew",
            "--optimize",
            "1",
            "--jobs",
            str(min(2, os.cpu_count() or 2)),
            "--sidecar",
            str(sidecar),
            str(source),
            str(output),
        ]

    @staticmethod
    def _format_page_range(page_numbers: Sequence[int]) -> str:
        ordered = sorted({int(page) for page in page_numbers if int(page) > 0})
        if not ordered:
            return ""
        if len(ordered) == 1:
            return str(ordered[0])
        return f"{ordered[0]}-{ordered[-1]}"

    @staticmethod
    def _input_page_count(source: Path) -> int:
        import pdfplumber

        with pdfplumber.open(source) as pdf:
            return len(pdf.pages)

    @staticmethod
    def _extract_mapped_pages(
        *, output_path: Path, page_numbers: Sequence[int], input_page_count: int
    ) -> Dict[int, str]:
        import pdfplumber

        with pdfplumber.open(output_path) as pdf:
            output_page_count = len(pdf.pages)
            try:
                mapping = map_source_pages_to_output(
                    page_numbers,
                    output_page_count=output_page_count,
                    input_page_count=input_page_count,
                )
            except PageMappingError as exc:
                raise OcrRunnerError(str(exc)) from exc

            extracted: Dict[int, str] = {}
            for page_number, index in mapping.items():
                if 0 <= index < output_page_count:
                    extracted[page_number] = readable_page(pdf.pages[index]).extract_text() or ""
            return extracted
