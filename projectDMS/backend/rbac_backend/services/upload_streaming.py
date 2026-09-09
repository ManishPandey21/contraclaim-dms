"""Streaming upload helpers for bounded-memory file handling."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from fastapi import HTTPException, UploadFile, status

from ..core.config import settings
from ..models.document import FileValidationResult
from ..utils.file_validation import sniff_mime_from_bytes
from ..utils.validation import sanitize_filename


class UploadTooLargeError(HTTPException):
    """A refusal, raised as one, at the seam that discovers it.

    This used to be a bare `ValueError`, and the two production callers
    disagreed about what that meant. `routers/contracts.py` runs under
    `handle_exceptions`, which maps `ValueError` to 422; `DocumentController`
    has its own handler, whose `except Exception` turned the refusal into
    **500 "Document creation service temporarily unavailable"** - measured, not
    inferred. A client cannot tell that from a real outage, so the UI shows a
    server-error state and the user retries the same oversize file, and
    monitoring counts a policy decision as a 5xx.

    `UploadConcurrencyLimiter` already raises `HTTPException(429)` from this
    same layer for the same reason, and `contract_service` raises 413 for its
    own size rule. Raising the status here rather than at each call site is what
    stops the fourth caller re-introducing the disagreement: every existing
    handler in the upload paths re-raises `HTTPException` unchanged.
    """

    def __init__(self, max_size_bytes: int) -> None:
        super().__init__(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "Upload exceeds the maximum allowed file size "
                f"({max_size_bytes // (1024 * 1024)}MB)"
            ),
        )
        self.max_size_bytes = int(max_size_bytes)


async def read_upload_within_limit(
    upload: UploadFile,
    max_size_bytes: int,
) -> bytes:
    """Read a small upload into memory, refusing it on the byte that crosses the cap.

    For the paths that legitimately want the whole body in memory - a CSV import,
    a profile photo, a pasted text document - where spooling to disk buys
    nothing. What it does not do is `await upload.read()` and then measure, which
    is what five call sites used to do:

    * `bank_guarantees.py`, `key_dates.py` and `deep_planning.py` had **no
      application-level size limit at all**, so the only bound was the gateway's
      `client_max_body_size 200m`;
    * `profiles.py` and `folder_structure.py` had a limit and applied it *after*
      buffering, so a 200 MB body was already resident before the 5 MB rule
      refused it.

    A limit enforced after the read is not a limit on what the process holds. The
    refusal here is the same `UploadTooLargeError` the streaming path raises, so
    every upload surface answers 413 for the same condition.
    """
    limit = max(1, int(max_size_bytes))
    chunk_size = max(1, int(getattr(settings, "UPLOAD_STREAM_CHUNK_SIZE_MB", 1))) * 1024 * 1024
    chunks: list[bytes] = []
    total = 0

    await upload.seek(0)
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UploadTooLargeError(limit)
        chunks.append(chunk)
    try:
        await upload.seek(0)
    except Exception:
        pass
    return b"".join(chunks)


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
                raise UploadTooLargeError(max_size_bytes)
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
