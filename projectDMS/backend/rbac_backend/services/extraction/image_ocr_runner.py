"""Direct OCR for standalone PNG/JPEG inputs; no PDF page mapping.

Deliberately separate from OcrMyPdfRunner. That runner reads the input's page
count and maps source pages onto output indexes - contracts an image cannot
satisfy. Routing a PNG through it would either fail obscurely or "succeed"
with a mapping that means nothing.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ImageOcrRunnerError(RuntimeError):
    """Raised when a standalone image cannot be OCR'd."""


class TesseractImageOcrRunner:
    def __init__(self, *, timeout: int = 300) -> None:
        self.timeout = timeout

    async def run(self, source_image: Path, language: str) -> str:
        def _verify() -> None:
            from PIL import Image

            with Image.open(source_image) as image:
                image.verify()

        try:
            await asyncio.to_thread(_verify)
        except Exception as exc:
            raise ImageOcrRunnerError(
                f"Not a readable image: {exc}"[:500]
            ) from exc

        process = await asyncio.create_subprocess_exec(
            "tesseract",
            str(source_image),
            "stdout",
            "-l",
            language,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ImageOcrRunnerError("Standalone image OCR timed out") from exc

        if process.returncode != 0:
            raise ImageOcrRunnerError(
                stderr.decode("utf-8", errors="replace")[:500]
            )
        return stdout.decode("utf-8", errors="replace").strip()
