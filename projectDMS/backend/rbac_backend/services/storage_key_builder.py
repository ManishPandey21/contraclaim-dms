"""Canonical storage key construction for document binaries."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Optional


class StorageKeyBuilder:
    """Build deterministic object keys shared by local and S3 providers."""

    _safe_re = re.compile(r"[^A-Za-z0-9._-]+")

    @classmethod
    def safe_token(cls, value: Optional[str], fallback: str = "unknown") -> str:
        raw = str(value or "").strip()
        if not raw:
            raw = fallback
        token = cls._safe_re.sub("_", raw.replace("\\", "_").replace("/", "_"))
        token = token.strip("._-")
        return token or fallback

    @classmethod
    def extension_from_filename(cls, filename: Optional[str]) -> str:
        name = str(filename or "")
        suffix = PurePosixPath(name.replace("\\", "/")).suffix
        return suffix.lower() if suffix else ""

    @classmethod
    def safe_filename(cls, filename: Optional[str], fallback: str = "file") -> str:
        name = PurePosixPath(str(filename or fallback).replace("\\", "/")).name
        safe = cls.safe_token(name, fallback=fallback)
        return safe or fallback

    def build_document_key(
        self,
        *,
        organization: str,
        project: str,
        upload_type: str,
        letter_no: Optional[str],
        original_filename: str,
        upload_id: str,
        at: Optional[datetime] = None,
    ) -> str:
        now = at or datetime.utcnow()
        bucket = "outgoing" if str(upload_type or "").lower() == "outgoing" else "incoming"
        org = self.safe_token(organization, "ORG")
        proj = self.safe_token(project, "PROJ")
        ext = self.extension_from_filename(original_filename)
        if letter_no:
            base = self.safe_token(letter_no, "letter")
        else:
            stem = PurePosixPath(original_filename.replace("\\", "/")).stem
            base = self.safe_token(stem, "document")
        stored = f"{self.safe_token(upload_id, 'upload')}_{base}{ext}"
        return f"/{org}/{proj}/{bucket}/{now:%Y}/{now:%m}/{stored}"

    def build_contract_key(
        self,
        *,
        organization: str,
        project: Optional[str],
        safe_filename: str,
        upload_id: str,
    ) -> str:
        org = self.safe_token(organization, "ORG")
        proj = self.safe_token(project or "default", "PROJ")
        stored = f"{self.safe_token(upload_id, 'upload')}_{self.safe_filename(safe_filename)}"
        return f"/{org}/{proj}/contracts/{stored}"

    def build_enclosure_key(
        self,
        *,
        organization: str,
        project: str,
        parent_document_id: str,
        original_filename: str,
        upload_id: str,
        at: Optional[datetime] = None,
    ) -> str:
        now = at or datetime.utcnow()
        org = self.safe_token(organization, "ORG")
        proj = self.safe_token(project, "PROJ")
        stored = f"{self.safe_token(upload_id, 'upload')}_{self.safe_filename(original_filename)}"
        parent = self.safe_token(parent_document_id, "document")
        return f"/{org}/{proj}/enclosures/{now:%Y}/{now:%m}/{parent}/{stored}"
