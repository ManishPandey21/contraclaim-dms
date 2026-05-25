"""Streaming upload helpers for bounded-memory file handling."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from fastapi import UploadFile

from ..core.config import settings
from ..models.document import FileValidationResult
from ..utils.file_validation import sniff_mime_from_bytes
from ..utils.validation import sanitize_filename


@dataclass
class SpooledUpload:
    filename: str
    path: Path
    size: int
    sha256: str
    sample: bytes
    mime_type: str

    async def cleanup(self) -> None:
        path = self.path

        def _unlink() -> None:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _unlink)


async def spool_upload_file(
    upload: UploadFile,
    *,
    max_size_bytes: Optional[int] = None,
    sample_size: Optional[int] = None,
) -> SpooledUpload:
    if not upload.filename:
        raise ValueError("Upload filename is required")

    safe_filename = sanitize_filename(upload.filename)
    chunk_size = max(1, int(getattr(settings, "UPLOAD_STREAM_CHUNK_SIZE_MB", 1))) * 1024 * 1024
    max_sample = max(512, int(sample_size or settings.UPLOAD_VALIDATION_SAMPLE_BYTES))
    hasher = hashlib.sha256()
    total = 0
    sample = bytearray()
    tmp = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=Path(safe_filename).suffix,
        prefix="upload_spool_",
    )
    tmp_path = Path(tmp.name)
    try:
        await upload.seek(0)
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if max_size_bytes is not None and total > max_size_bytes:
                raise ValueError("Upload exceeds the maximum allowed file size")
            hasher.update(chunk)
            if len(sample) < max_sample:
                remaining = max_sample - len(sample)
                sample.extend(chunk[:remaining])
            tmp.write(chunk)
        tmp.flush()
    except Exception:
        tmp.close()
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            await upload.seek(0)
        except Exception:
            pass

    sample_bytes = bytes(sample)
    return SpooledUpload(
        filename=safe_filename,
        path=tmp_path,
        size=total,
        sha256=hasher.hexdigest(),
        sample=sample_bytes,
        mime_type=sniff_mime_from_bytes(sample_bytes, safe_filename),
    )


async def inspect_existing_file(
    path: Path,
    *,
    filename: str,
    sample_size: Optional[int] = None,
) -> SpooledUpload:
    source = Path(path).expanduser().resolve()
    safe_filename = sanitize_filename(filename)
    max_sample = max(512, int(sample_size or settings.UPLOAD_VALIDATION_SAMPLE_BYTES))
    chunk_size = max(1, int(getattr(settings, "UPLOAD_STREAM_CHUNK_SIZE_MB", 1))) * 1024 * 1024

    def _inspect() -> tuple[int, str, bytes]:
        hasher = hashlib.sha256()
        total = 0
        sample = bytearray()
        with source.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                hasher.update(chunk)
                if len(sample) < max_sample:
                    sample.extend(chunk[: max_sample - len(sample)])
        return total, hasher.hexdigest(), bytes(sample)

    loop = asyncio.get_running_loop()
    size, digest, sample = await loop.run_in_executor(None, _inspect)
    return SpooledUpload(
        filename=safe_filename,
        path=source,
        size=size,
        sha256=digest,
        sample=sample,
        mime_type=sniff_mime_from_bytes(sample, safe_filename),
    )


def validate_spooled_upload(
    spooled: SpooledUpload,
    allowed_mime_types: Optional[Sequence[str]] = None,
) -> FileValidationResult:
    file_type = os.path.splitext(spooled.filename)[1].lstrip(".")
    if spooled.size <= 0:
        return FileValidationResult(
            is_valid=False,
            filename=spooled.filename,
            file_size=spooled.size,
            file_type=file_type,
            mime_type=spooled.mime_type,
            error="File is empty",
        )
    if allowed_mime_types and spooled.mime_type not in set(allowed_mime_types):
        return FileValidationResult(
            is_valid=False,
            filename=spooled.filename,
            file_size=spooled.size,
            file_type=file_type,
            mime_type=spooled.mime_type,
            error=f"File type '{spooled.mime_type}' is not allowed",
        )
    executable_signatures = [b"\x4d\x5a", b"\x7fELF", b"\xfe\xed\xfa"]
    if any(spooled.sample.startswith(sig) for sig in executable_signatures):
        return FileValidationResult(
            is_valid=False,
            filename=spooled.filename,
            file_size=spooled.size,
            file_type=file_type,
            mime_type=spooled.mime_type,
            error="Executable files are not allowed",
        )
    return FileValidationResult(
        is_valid=True,
        filename=spooled.filename,
        file_size=spooled.size,
        file_type=file_type,
        mime_type=spooled.mime_type,
    )
