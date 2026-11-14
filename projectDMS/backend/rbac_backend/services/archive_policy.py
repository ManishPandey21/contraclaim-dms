"""Intake rules for archive uploads.

Archives are stored as immutable FileObjects and nothing more. Antivirus and
SHA-256 duplicate detection still run - only extraction is skipped - so this
adds no new malware path and no new Python dependency, because nothing ever
reads inside the archive.

`is_archive` and `is_admitted` are deliberately separate questions. A RAR is an
archive whether or not this deployment accepts one; collapsing the two would
mean a rejected RAR stopped being recognised as an archive by the code paths
that need to know.
"""

from __future__ import annotations

from typing import Optional

from .extraction.source_kind import ARCHIVE_MIMES

ZIP_MIME = "application/zip"
RAR_MIME = "application/vnd.rar"


def _normalize(mime: Optional[str]) -> str:
    return (mime or "").split(";", 1)[0].strip().lower()


class ArchiveIntakePolicy:
    """Decides what an archive upload is exempt from."""

    def __init__(self, *, rar_enabled: bool) -> None:
        self.rar_enabled = bool(rar_enabled)

    @staticmethod
    def is_archive(mime: Optional[str]) -> bool:
        normalized = _normalize(mime)
        return bool(normalized) and normalized in ARCHIVE_MIMES

    def is_admitted(self, mime: Optional[str]) -> bool:
        normalized = _normalize(mime)
        if normalized == ZIP_MIME:
            return True
        return normalized == RAR_MIME and self.rar_enabled

    @classmethod
    def requires_letter_number(cls, mime: Optional[str]) -> bool:
        """Archives carry no letter number; every other type still must."""
        return not cls.is_archive(mime)

    @classmethod
    def creates_processing_job(cls, mime: Optional[str]) -> bool:
        """Archives are never queued for extraction, whatever the OCR flag says."""
        return not cls.is_archive(mime)
