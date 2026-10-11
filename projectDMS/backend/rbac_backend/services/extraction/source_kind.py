"""Decide which extractor an upload belongs to, from its detected MIME type.

The detected MIME is authoritative, not the filename: a file named .pdf that
sniffs as PNG is an image, and routing it by name sends it to a reader that
cannot parse it. The filename parameter exists only for callers that want to
log it.

Anything unrecognised is UNSUPPORTED rather than assumed to be a PDF. The old
general path fed every admitted MIME into `OCRService.process_pdf`, so a PNG
upload entered a PDF reader and produced nothing - visibly succeeding while
extracting no text.
"""

from __future__ import annotations

from typing import Optional

from .models import SourceKind

PDF_MIMES = frozenset({"application/pdf"})
IMAGE_MIMES = frozenset({"image/png", "image/jpeg"})
TEXT_MIMES = frozenset({"text/plain"})
ARCHIVE_MIMES = frozenset({"application/zip", "application/vnd.rar"})


class SourceKindRouter:
    @staticmethod
    def route(mime: Optional[str], filename: Optional[str] = None) -> SourceKind:
        """Map a detected MIME type onto a SourceKind."""
        if not mime:
            return SourceKind.UNSUPPORTED

        normalized = mime.split(";", 1)[0].strip().lower()
        if normalized in PDF_MIMES:
            return SourceKind.PDF
        if normalized in IMAGE_MIMES:
            return SourceKind.IMAGE
        if normalized in TEXT_MIMES:
            return SourceKind.TEXT
        if normalized in ARCHIVE_MIMES:
            return SourceKind.ARCHIVE
        return SourceKind.UNSUPPORTED


route = SourceKindRouter.route
