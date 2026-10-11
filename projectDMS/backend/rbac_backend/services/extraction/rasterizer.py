"""Render PDF pages and regions to PNG for the vision fallback.

pdfplumber's `to_image()` is backed by pypdfium2 (Apache-2.0), already present
as a transitive dependency. PyMuPDF is deliberately not used: the companion
design rejected adding an AGPL dependency, and this module was verified against
the real interpreter before it was written.

Region-first is the rule. Sending a crop rather than a whole page is
simultaneously a cost control, a data-minimisation control, and a precision
control - a narrower prompt reconstructs a table better than a wider one.

A standalone PNG/JPEG is treated as logical page 1 so the image path never has
to pretend to be a PDF.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

DEFAULT_DPI = 150

#: PDF user-space is 72 units per inch.
_PDF_UNITS_PER_INCH = 72.0

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


class RasterizationError(Exception):
    """Raised when a page or region cannot be rendered."""


class PageRasterizer:
    def __init__(self, *, dpi: int = DEFAULT_DPI) -> None:
        self.dpi = max(1, int(dpi))

    @property
    def _scale(self) -> float:
        return self.dpi / _PDF_UNITS_PER_INCH

    def render_page(self, source: Path, page_number: int) -> bytes:
        """Return the page as PNG bytes."""
        image = self._render(source, page_number)
        return self._to_png(image)

    def crop_region(
        self, source: Path, page_number: int, bbox: Tuple[float, float, float, float]
    ) -> bytes:
        """Return only the requested region as PNG bytes.

        `bbox` is in PDF user-space (x0, top, x1, bottom) for PDFs, and in
        pixels for standalone images. It is clamped to the page: a box running
        a little past the edge is a rounding artefact, not a reason to fail.
        """
        x0, top, x1, bottom = bbox
        if x1 <= x0 or bottom <= top:
            raise RasterizationError(f"Region has no area: {bbox}")

        image = self._render(source, page_number)
        scale = 1.0 if self._is_image(source) else self._scale

        left = max(0, int(x0 * scale))
        upper = max(0, int(top * scale))
        right = min(image.width, int(x1 * scale))
        lower = min(image.height, int(bottom * scale))

        if right <= left or lower <= upper:
            raise RasterizationError(
                f"Region {bbox} falls outside page {page_number}"
            )

        return self._to_png(image.crop((left, upper, right, lower)))

    @staticmethod
    def _is_image(source: Path) -> bool:
        return Path(source).suffix.lower() in _IMAGE_SUFFIXES

    def _render(self, source: Path, page_number: int):
        from PIL import Image

        path = Path(source)
        if not path.exists():
            raise RasterizationError(f"Source not found: {path}")

        if self._is_image(path):
            if page_number != 1:
                raise RasterizationError(
                    f"A standalone image has only page 1, asked for {page_number}"
                )
            try:
                with Image.open(path) as handle:
                    return handle.convert("RGB")
            except Exception as exc:
                raise RasterizationError(f"Could not read image: {exc}") from exc

        try:
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                if not 1 <= page_number <= len(pdf.pages):
                    raise RasterizationError(
                        f"Page {page_number} is outside this document "
                        f"(1-{len(pdf.pages)})"
                    )
                page_image = pdf.pages[page_number - 1].to_image(resolution=self.dpi)
                return page_image.original.convert("RGB")
        except RasterizationError:
            raise
        except Exception as exc:
            raise RasterizationError(
                f"Could not render page {page_number} of {path.name}: {exc}"
            ) from exc

    @staticmethod
    def _to_png(image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
