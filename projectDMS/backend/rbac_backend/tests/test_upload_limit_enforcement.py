"""Gate 5 bullet 3: upload MIME, extension, size, and concurrency limits are validated.

MIME and extension were already covered - `test_upload_policy.py` pins the
advertised policy and `upload_streaming.validate_spooled_upload` refuses a
sniffed MIME outside the allowlist, exercised by `test_archive_upload_route.py`
and `test_contract_upload_security.py`. The size and bulk/concurrency halves of
the bullet had no permanent refusal test at all, which is what this module adds.

Three properties matter more than the arithmetic:

* **The limits under test are the configured ones.** Every boundary here is
  derived from `settings` at call time. A test that hard-codes 100 MB stays
  green after somebody sets `GENERAL_UPLOAD_MAX_FILE_SIZE_MB=100000` and proves
  nothing about the deployment.
* **Enforcement is server-side and streaming.** `spool_upload_file` counts the
  bytes it has actually read, so a client that lies about `Content-Length` - or
  omits it - is refused on the byte that crosses the limit, not on its own say
  so. The fake upload below deliberately advertises one byte and delivers many.
* **A refusal leaves nothing behind.** The spooled temporary file is removed
  when the limit trips; a refusal that leaks a spool file or a half-created
  document row is a worse outcome than accepting the upload.
"""

from __future__ import annotations

import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from rbac_backend.core.config import settings
from rbac_backend.services.upload_limits import UploadConcurrencyLimiter
from rbac_backend.services.upload_streaming import (
    UploadTooLargeError,
    read_file_within_limit,
    spool_upload_file,
    validate_spooled_upload,
)

RBAC_BACKEND = Path(__file__).resolve().parents[1]
ROUTERS = RBAC_BACKEND / "routers"
#: Trees where an upload can be read. F-A8T-1 was in `services/`, which the
#: first cut of the class guard did not look at.
UPLOAD_TREES = (ROUTERS, RBAC_BACKEND / "services", RBAC_BACKEND / "utils")


# --------------------------------------------------------------------------- #
# A fake upload that lies about its size, the way a hostile client would
# --------------------------------------------------------------------------- #


class _LyingUpload:
    """An `UploadFile`-shaped object whose declared size is not its real size.

    `spool_upload_file` only calls `seek` and `read`. Nothing it does may consult
    `size` or `headers`; those exist here so that an implementation which starts
    trusting them is caught by `test_declared_size_is_never_trusted`.
    """

    def __init__(self, filename: str, body: bytes, *, declared_size: int = 1) -> None:
        import io

        self.filename = filename
        self._body = body
        self._offset = 0
        #: The spooled handle a real `UploadFile` exposes. The synchronous call
        #: sites reach for this rather than the async wrapper, and F-A8T-1 was
        #: on one of them, so the fake has to carry it too.
        self.file = io.BytesIO(body)
        self.size = declared_size
        self.headers = {"content-length": str(declared_size)}
        self.content_type = "application/pdf"

    async def seek(self, offset: int) -> None:
        self._offset = offset

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._body[self._offset :]
            self._offset = len(self._body)
            return chunk
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def _pdf_body(length: int) -> bytes:
    """`length` bytes that sniff as a PDF, so only the size rule can refuse them."""
    head = b"%PDF-1.4\n"
    if length <= len(head):
        return head[:length]
    return head + b"0" * (length - len(head))


def _spool_files() -> set:
    return set(Path(tempfile.gettempdir()).glob("upload_spool_*"))


# --------------------------------------------------------------------------- #
# Size: the boundary, measured against the cap the caller supplies
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "delta, allowed",
    [
        pytest.param(-1, True, id="one-byte-below-max"),
        pytest.param(0, True, id="exactly-max"),
        pytest.param(1, False, id="one-byte-over-max"),
    ],
)
async def test_the_size_limit_is_enforced_at_its_boundary(delta: int, allowed: bool) -> None:
    """`<= max` is accepted, `max + 1` is refused. The boundary is stated, not guessed."""
    limit = 4096
    upload = _LyingUpload("boundary.pdf", _pdf_body(limit + delta))

    if allowed:
        spooled = await spool_upload_file(upload, max_size_bytes=limit)
        try:
            assert spooled.size == limit + delta
        finally:
            await spooled.cleanup()
        return

    with pytest.raises(UploadTooLargeError):
        await spool_upload_file(upload, max_size_bytes=limit)


def test_the_routers_pass_the_configured_limit_and_not_a_literal() -> None:
    """The boundary above is only meaningful if production derives its own cap.

    Read from the source rather than from a mock: a router that inlined
    `100 * 1024 * 1024` would keep every test in this module green while ignoring
    the deployed configuration.
    """
    for name in ("documents.py", "contracts.py"):
        source = (ROUTERS / name).read_text(encoding="utf-8")
        assert "spool_upload_file" in source, f"{name} no longer spools its uploads"
        assert "max_size_bytes=" in source, (
            f"{name} calls spool_upload_file without a size cap, so an upload of any "
            "size is buffered to disk before anything refuses it"
        )
    documents = (ROUTERS / "documents.py").read_text(encoding="utf-8")
    assert "settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB" in documents, (
        "the document upload cap is no longer read from configuration"
    )
    assert int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB) > 0, (
        "the configured general upload cap is not a positive number of megabytes"
    )


async def test_declared_size_is_never_trusted() -> None:
    """A forged `Content-Length` must not buy an oversize upload.

    The fake advertises one byte and delivers many. If the implementation ever
    short-circuits on `upload.size` or the header, this admits an oversize body.
    """
    limit = 2048
    upload = _LyingUpload("forged.pdf", _pdf_body(limit * 4), declared_size=1)

    with pytest.raises(UploadTooLargeError):
        await spool_upload_file(upload, max_size_bytes=limit)


async def test_a_refused_upload_leaves_no_spool_file_behind() -> None:
    """The refusal path must clean up, or a rejected upload still consumes disk."""
    limit = 1024
    upload = _LyingUpload("leak.pdf", _pdf_body(limit * 8))

    before = _spool_files()
    with pytest.raises(UploadTooLargeError):
        await spool_upload_file(upload, max_size_bytes=limit)
    leaked = _spool_files() - before

    assert not leaked, f"the refused upload left spool files behind: {leaked}"


async def test_the_limit_trips_before_the_whole_body_is_buffered() -> None:
    """Streaming, not "read it all and then measure".

    A limit checked after the read completes is not a limit; it is a delayed
    error that has already paid the memory and disk cost. Counting the bytes the
    fake was asked for proves the refusal happened during the stream.

    The body has to exceed one read chunk for the question to mean anything -
    `UPLOAD_STREAM_CHUNK_SIZE_MB` floors at 1 MB, so a 64 KB body arrives whole
    on the first read and would satisfy a purely post-hoc check.
    """
    chunk_bytes = max(1, int(getattr(settings, "UPLOAD_STREAM_CHUNK_SIZE_MB", 1))) * 1024 * 1024
    limit = 1024
    body = _pdf_body(chunk_bytes * 3)

    class _Counting(_LyingUpload):
        def __init__(self) -> None:
            super().__init__("counted.pdf", body)
            self.bytes_served = 0

        async def read(self, size: int = -1) -> bytes:
            chunk = await super().read(size)
            self.bytes_served += len(chunk)
            return chunk

    upload = _Counting()
    with pytest.raises(UploadTooLargeError):
        await spool_upload_file(upload, max_size_bytes=limit)

    assert upload.bytes_served < len(body), (
        "the whole body was read before the size limit refused it"
    )


# --------------------------------------------------------------------------- #
# The refusal has to look like a refusal (F-A8S-2)
# --------------------------------------------------------------------------- #


def test_the_size_refusal_is_a_413_and_not_a_server_error() -> None:
    """R-A8S measured this returning 500 through `DocumentController`.

    The size limit was a bare `ValueError`, and the two upload surfaces
    disagreed about what to do with it: `routers/contracts.py` runs under
    `handle_exceptions` (ValueError -> 422), while `DocumentController` has its
    own handler whose `except Exception` produced
    **500 "Document creation service temporarily unavailable"**.

    A policy refusal presented as a server error is worse than a wrong number.
    The client cannot distinguish it from an outage, so the UI shows an error
    state and the user retries the same oversize file forever; monitoring counts
    a deliberate decision as a 5xx.
    """
    error = UploadTooLargeError(5 * 1024 * 1024)
    assert error.status_code == 413
    assert "5MB" in str(error.detail)


async def test_the_document_upload_surface_answers_413_for_an_oversize_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driven through the real handler, because that is where the defect lived.

    Reading the source would not have found this: every clause involved was
    correct in isolation. Only running the path shows which `except` wins.
    """
    from fastapi import HTTPException

    from rbac_backend.routers.documents import DocumentController

    monkeypatch.setattr(settings, "GENERAL_UPLOAD_MAX_FILE_SIZE_MB", 1, raising=False)

    class _AllowEverything:
        async def authorize(self, *_args, **_kwargs) -> None:
            return None

    controller = DocumentController.__new__(DocumentController)
    controller.policy_service = _AllowEverything()

    chunk = max(1, int(getattr(settings, "UPLOAD_STREAM_CHUNK_SIZE_MB", 1))) * 1024 * 1024
    oversize = _LyingUpload("big.pdf", _pdf_body(chunk * 3))

    with pytest.raises(HTTPException) as refused:
        await controller.create_document(
            background_tasks=None,
            file=oversize,
            organization_id="org-gate5",
            project_id="proj-gate5",
            upload_type="document",
            letter_no="L-1",
            date_str="2026-09-09",
            current_user=SimpleNamespace(id="u-gate5", organization_id="org-gate5"),
        )

    assert refused.value.status_code == 413, (
        "an oversize document upload is being reported as "
        f"{refused.value.status_code}; it was 500 before R-A8S, which a client "
        "cannot tell apart from an outage"
    )


async def test_the_contract_upload_surface_answers_413_for_an_oversize_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second surface, which disagreed with the first: it answered 422.

    Both now inherit the status from the seam that makes the decision, so a
    third caller cannot invent a third answer.
    """
    from fastapi import HTTPException

    from rbac_backend.routers import contracts as contracts_router

    monkeypatch.setattr(settings, "CONTRACT_UPLOAD_MAX_FILE_SIZE_MB", 1, raising=False)

    class _AllowEverything:
        async def authorize(self, *_args, **_kwargs) -> None:
            return None

    class _NoSessions:
        async def validate_upload_session(self, *_args, **_kwargs):
            raise AssertionError("no upload_id was supplied")

    chunk = max(1, int(getattr(settings, "UPLOAD_STREAM_CHUNK_SIZE_MB", 1))) * 1024 * 1024
    oversize = _LyingUpload("big.pdf", _pdf_body(chunk * 3))

    with pytest.raises(HTTPException) as refused:
        await contracts_router.upload_contracts_multipart(
            files=[oversize],
            organization_id="org-gate5",
            project_id="proj-gate5",
            upload_ids=None,
            tags=None,
            contract_service=_NoSessions(),
            file_service=None,
            current_user=SimpleNamespace(id="u-gate5", organization_id="org-gate5"),
            policy=_AllowEverything(),
        )

    assert refused.value.status_code == 413, (
        "the contract upload surface answers "
        f"{refused.value.status_code} for an oversize file; it answered 422 before "
        "R-A8S while the document surface answered 500 for the same condition"
    )


def test_the_size_limit_still_refuses_from_the_service_layer() -> None:
    """`upload_streaming` owns the decision; a call site must not have to repeat it."""
    source = (
        Path(__file__).resolve().parents[1] / "services" / "upload_streaming.py"
    ).read_text(encoding="utf-8")
    assert "raise UploadTooLargeError(max_size_bytes)" in source, (
        "the streaming size check no longer raises the typed refusal"
    )
    assert "HTTP_413_REQUEST_ENTITY_TOO_LARGE" in source


# --------------------------------------------------------------------------- #
# The in-memory upload paths (F-A8S-4)
# --------------------------------------------------------------------------- #
#
# Not every upload spools to disk. A CSV import, a profile photo and the deep
# planning text analyser want the whole body in memory, and five call sites did
# `await file.read()` to get it. Three of them - `bank_guarantees.py`,
# `key_dates.py`, `deep_planning.py` - had **no application-level size limit at
# all**, so the only bound was the gateway's `client_max_body_size 200m`. Two -
# `profiles.py`, `folder_structure.py` - had a limit and applied it *after* the
# read, which bounds what is stored and not what the process holds.
#
# Reviewing the Gate 5 bullet 3 tick is what found them: the bullet says "upload
# ... size ... limits are validated" and says nothing about only the spooled
# paths counting.


@pytest.mark.parametrize(
    "delta, allowed",
    [
        pytest.param(-1, True, id="one-byte-below-max"),
        pytest.param(0, True, id="exactly-max"),
        pytest.param(1, False, id="one-byte-over-max"),
    ],
)
async def test_the_in_memory_read_is_capped_at_its_boundary(delta: int, allowed: bool) -> None:
    from rbac_backend.services.upload_streaming import read_upload_within_limit

    limit = 4096
    upload = _LyingUpload("rows.csv", b"a" * (limit + delta))

    if allowed:
        assert len(await read_upload_within_limit(upload, limit)) == limit + delta
        return

    with pytest.raises(UploadTooLargeError):
        await read_upload_within_limit(upload, limit)


async def test_the_in_memory_read_refuses_before_buffering_the_whole_body() -> None:
    """The distinction the fix is about.

    `await file.read()` followed by `len(content) > cap` refuses the request and
    still held the body. Counting the bytes the fake was asked for proves this
    one does not.
    """
    from rbac_backend.services.upload_streaming import read_upload_within_limit

    chunk = max(1, int(getattr(settings, "UPLOAD_STREAM_CHUNK_SIZE_MB", 1))) * 1024 * 1024
    body = b"a" * (chunk * 3)

    class _Counting(_LyingUpload):
        def __init__(self) -> None:
            super().__init__("big.csv", body)
            self.bytes_served = 0

        async def read(self, size: int = -1) -> bytes:
            piece = await super().read(size)
            self.bytes_served += len(piece)
            return piece

    upload = _Counting()
    with pytest.raises(UploadTooLargeError):
        await read_upload_within_limit(upload, 1024)

    assert upload.bytes_served < len(body), (
        "the whole body was read before the cap refused it, which is the defect "
        "this helper replaced"
    )


def uncapped_upload_reads(source: str, label: str = "<source>") -> list:
    """Every place `source` reads an upload whole with no size limit.

    An AST walk rather than a substring search, because the property is "an
    upload is read whole with no limit" and not "this exact string appears".

    Three shapes, because R-A8T's review found the first cut saw only one:

    * ``await file.read()`` - the original;
    * ``file.file.read()`` - the *synchronous* spooled handle. This is F-A8T-1.
      `POST /api/documents/bulk-upload` takes two upload parameters, caps
      `files` and never measured `csv_file`, and
      `bulk_upload_service.read_csv_from_upload_file` read that one with
      `upload_file.file.read()` before decoding it. The guard that certified
      Gate 5 bullet 3 matched only `ast.Name.read()`, so the attribute chain was
      invisible to it;
    * the loop variable of ``for f in files:`` where `files` is a
      `List[UploadFile]` parameter - the shape a bulk endpoint naturally uses.

    A call with arguments is treated as bounded: it is asking for a chunk. That
    is deliberately generous - a chunk loop with no running total still passes -
    and it is the honest boundary of a static check. The runtime boundary tests
    above are what cover the counting.
    """
    import ast

    tree = ast.parse(source)
    watched = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in node.args.args + node.args.kwonlyargs:
                annotation = ast.unparse(argument.annotation) if argument.annotation else ""
                if "UploadFile" in annotation:
                    watched.add(argument.arg)
    if not watched:
        return []
    # A loop over a watched collection yields watched items.
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name):
            iterated = ast.unparse(node.iter)
            if any(name in iterated for name in watched):
                watched.add(node.target.id)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "read":
            continue
        if node.args:  # a chunk size was asked for
            continue
        target = func.value
        if isinstance(target, ast.Name) and target.id in watched:
            offenders.append(f"{label}:{node.lineno}: {target.id}.read()")
        elif (
            isinstance(target, ast.Attribute)
            and target.attr == "file"
            and isinstance(target.value, ast.Name)
            and target.value.id in watched
        ):
            offenders.append(f"{label}:{node.lineno}: {target.value.id}.file.read()")
    return offenders


def test_nothing_reads_an_upload_without_a_cap() -> None:
    """The class-level guard, over routers AND services."""
    offenders = []
    for tree_root in UPLOAD_TREES:
        for path in sorted(tree_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            offenders.extend(
                uncapped_upload_reads(
                    # utf-8-sig: four service modules carry a BOM, which Python
                    # strips on import and `ast.parse` refuses. Read the way the
                    # interpreter does, or the guard skips them with a crash.
                    path.read_text(encoding="utf-8-sig"),
                    str(path.relative_to(RBAC_BACKEND)),
                )
            )

    assert not offenders, (
        "these read an upload whole with no size limit, so the only bound on "
        "what the process holds is the gateway's client_max_body_size:\n  "
        + "\n  ".join(offenders)
        + "\nUse services.upload_streaming.read_upload_within_limit (async) or "
        "read_file_within_limit (the spooled handle) instead."
    )


UNCAPPED_SHAPES = [
    pytest.param(
        "async def r(file: UploadFile):\n    return await file.read()\n",
        id="the-original-shape",
    ),
    pytest.param(
        "def r(file: UploadFile):\n    return file.file.read()\n",
        id="the-spooled-handle-F-A8T-1",
    ),
    pytest.param(
        "async def r(files: List[UploadFile]):\n"
        "    for f in files:\n"
        "        await f.read()\n",
        id="a-loop-variable",
    ),
]


@pytest.mark.parametrize("snippet", UNCAPPED_SHAPES)
def test_each_uncapped_shape_is_caught(snippet: str) -> None:
    """Without these the widened guard could be green because it looks at nothing.

    The middle one is the defect the first cut of this guard could not see, and
    it was live in `services/bulk_upload_service.py` while Gate 5 bullet 3 was
    ticked.
    """
    assert uncapped_upload_reads(snippet), f"not caught: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param(
            "async def r(file: UploadFile):\n    return await file.read(1024)\n",
            id="a-chunked-read",
        ),
        pytest.param(
            "def r(handle):\n    return handle.read()\n",
            id="not-an-upload-at-all",
        ),
        pytest.param(
            "async def r(file: UploadFile):\n"
            "    return await read_upload_within_limit(file, 1024)\n",
            id="the-seam",
        ),
    ],
)
def test_the_guard_does_not_ban_a_bounded_read(snippet: str) -> None:
    assert uncapped_upload_reads(snippet) == []


def test_the_in_memory_helper_is_actually_used_by_the_paths_that_need_it() -> None:
    """Otherwise the guard above is satisfied by deleting the uploads."""
    for name in (
        "bank_guarantees.py",
        "key_dates.py",
        "deep_planning.py",
        "profiles.py",
        "folder_structure.py",
    ):
        source = (ROUTERS / name).read_text(encoding="utf-8")
        assert "read_upload_within_limit" in source, (
            f"{name} no longer bounds its in-memory upload read"
        )
    bulk = (RBAC_BACKEND / "services" / "bulk_upload_service.py").read_text(
        encoding="utf-8"
    )
    assert "read_file_within_limit" in bulk, (
        "bulk_upload_service no longer bounds the CSV read on "
        "POST /api/documents/bulk-upload (F-A8T-1)"
    )


# --------------------------------------------------------------------------- #
# The MIME half of the bullet: sniffed, never declared
# --------------------------------------------------------------------------- #

#: Real magic numbers. `sniff_mime_from_bytes` reads the container, so these are
#: headers rather than whole files.
GIF = b"GIF89a" + b"\x01\x00\x01\x00\x00\x00\x00"
WEBP = b"RIFF" + b"\x1a\x00\x00\x00" + b"WEBP" + b"VP8 "
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8
WINDOWS_EXECUTABLE = b"MZ\x90\x00" + b"\x00" * 16


@pytest.mark.parametrize(
    "body,expected",
    [
        pytest.param(GIF, "image/gif", id="gif"),
        pytest.param(WEBP, "image/webp", id="webp"),
        pytest.param(PNG, "image/png", id="png"),
        pytest.param(JPEG, "image/jpeg", id="jpeg"),
    ],
)
def test_every_allowed_profile_image_type_can_be_sniffed(body: bytes, expected: str) -> None:
    """A type an allowlist permits but the sniffer cannot recognise is a type
    that has to be taken on the client's word.

    `routers/profiles.py` allows jpeg, png, webp and gif, and the sniffer knew
    only the first two - so the route checked `file.content_type`, the header
    the client chose. GIF and WebP signatures were added in R-A8T for that
    reason, and neither type appears in any other allowlist in this backend, so
    recognising them widens nothing else.
    """
    from rbac_backend.utils.file_validation import sniff_mime_from_bytes

    assert sniff_mime_from_bytes(body, "photo") == expected


def test_the_profile_photo_route_does_not_trust_the_declared_content_type() -> None:
    """F-A8T-4. Extension trust is the classic upload bypass; a declared
    `Content-Type` is exactly as forgeable, and it was also the type written
    into the stored `data:` URL."""
    source = (ROUTERS / "profiles.py").read_text(encoding="utf-8")
    assert "sniff_mime_from_bytes" in source, (
        "profiles.py no longer sniffs the profile photo and is back to trusting "
        "the client's declared Content-Type"
    )
    assert "data:{sniffed};base64," in source, (
        "the stored data URL asserts a MIME type that was not measured from the "
        "bytes it encodes"
    )


def test_an_executable_is_refused_however_it_declares_itself() -> None:
    """The property the sniff exists for, at the seam that makes the decision."""
    from rbac_backend.utils.file_validation import sniff_mime_from_bytes

    sniffed = sniff_mime_from_bytes(WINDOWS_EXECUTABLE, "avatar.png")
    assert sniffed != "image/png"
    assert sniffed not in {"image/jpeg", "image/png", "image/webp", "image/gif"}


# --------------------------------------------------------------------------- #
# The synchronous seam: `read_file_within_limit`
# --------------------------------------------------------------------------- #


def _handle(body: bytes):
    import io

    return io.BytesIO(body)


def test_the_sync_seam_allows_a_body_at_the_limit() -> None:
    body = b"a" * 4096
    assert read_file_within_limit(_handle(body), 4096) == body


def test_the_sync_seam_refuses_one_byte_over() -> None:
    with pytest.raises(UploadTooLargeError) as excinfo:
        read_file_within_limit(_handle(b"a" * 4097), 4096)
    assert excinfo.value.status_code == 413


def test_the_sync_seam_refuses_before_reading_the_whole_body() -> None:
    """The property that separates a limit from a measurement.

    `bulk_upload_service` used to `read()` the lot and never measure it at all;
    a fix that read the lot and *then* refused would bound what is stored and
    not what the process holds.
    """
    body = b"b" * (8 * 1024 * 1024)

    class _Counting:
        def __init__(self) -> None:
            self._offset = 0
            self.bytes_served = 0

        def seek(self, offset: int, whence: int = 0) -> int:
            self._offset = offset
            return offset

        def read(self, size: int = -1) -> bytes:
            piece = body[self._offset : self._offset + size] if size and size > 0 else body[self._offset :]
            self._offset += len(piece)
            self.bytes_served += len(piece)
            return piece

    handle = _Counting()
    with pytest.raises(UploadTooLargeError):
        read_file_within_limit(handle, 1024)
    assert handle.bytes_served < len(body), (
        "the whole body was read before the cap refused it"
    )


def test_the_sync_seam_rewinds_so_a_later_read_still_works() -> None:
    handle = _handle(b"header,value\n1,2\n")
    read_file_within_limit(handle, 1024)
    assert handle.read() == b"header,value\n1,2\n"


def test_the_bulk_csv_read_answers_413_rather_than_a_processing_failure() -> None:
    """F-A8T-1, end to end through the real service method.

    Two things are asserted, because the first fix would have been incomplete
    without the second: the CSV half of `POST /api/documents/bulk-upload` is now
    capped, **and** the refusal survives the method's own `except Exception`
    tail. That tail rewrote every failure as
    `ValueError("CSV processing failed: ...")`, which is F-A8S-2 again - a
    client cannot tell a policy refusal from an outage.
    """
    from rbac_backend.services.bulk_upload_service import BulkUploadService

    limit = max(1, int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB)) * 1024 * 1024
    oversize = _LyingUpload("metadata.csv", b"x" * (limit + 1))

    with pytest.raises(UploadTooLargeError) as excinfo:
        BulkUploadService().read_csv_from_upload_file(oversize)
    assert excinfo.value.status_code == 413


# --------------------------------------------------------------------------- #
# Bulk: count and total size, from the configured limits
# --------------------------------------------------------------------------- #


def _bulk_guard(file_count: int, total_bytes: int):
    """The route's own bulk rules, applied to a candidate request.

    `routers/documents.py` enforces these inline before the controller is
    reached. This mirrors the two conditions so the boundary can be exercised
    without constructing a full multipart request;
    `test_the_bulk_rules_are_the_routers_own` is what stops the mirror drifting
    from the original.
    """
    if file_count > int(settings.BULK_UPLOAD_MAX_FILES):
        return "count"
    if total_bytes > int(settings.BULK_UPLOAD_MAX_SIZE_MB) * 1024 * 1024:
        return "size"
    return None


def test_the_bulk_rules_are_the_routers_own() -> None:
    source = (ROUTERS / "documents.py").read_text(encoding="utf-8")
    assert "len(files) > settings.BULK_UPLOAD_MAX_FILES" in source, (
        "the bulk file-count guard is gone or no longer reads its configured limit"
    )
    assert "settings.BULK_UPLOAD_MAX_SIZE_MB * 1024 * 1024" in source, (
        "the bulk total-size guard is gone or no longer reads its configured limit"
    )
    assert "file.file.seek(0, 2)" in source, (
        "the bulk total-size guard no longer measures the real stream length; the "
        "defect it replaced measured ~0 bytes and enforced nothing"
    )


def test_a_bulk_request_at_the_configured_count_is_allowed() -> None:
    assert _bulk_guard(int(settings.BULK_UPLOAD_MAX_FILES), 0) is None


def test_a_bulk_request_over_the_configured_count_is_refused() -> None:
    assert _bulk_guard(int(settings.BULK_UPLOAD_MAX_FILES) + 1, 0) == "count"


def test_a_bulk_request_at_the_configured_total_size_is_allowed() -> None:
    exact = int(settings.BULK_UPLOAD_MAX_SIZE_MB) * 1024 * 1024
    assert _bulk_guard(1, exact) is None


def test_a_bulk_request_over_the_configured_total_size_is_refused() -> None:
    over = int(settings.BULK_UPLOAD_MAX_SIZE_MB) * 1024 * 1024 + 1
    assert _bulk_guard(1, over) == "size"


# --------------------------------------------------------------------------- #
# Concurrency: the configured per-user and per-organisation caps
# --------------------------------------------------------------------------- #


async def test_concurrent_uploads_are_refused_above_the_configured_limit() -> None:
    """The (N+1)th simultaneous upload gets 429, and releasing a slot restores capacity."""
    from fastapi import HTTPException

    limiter = UploadConcurrencyLimiter()
    limiter._local_counts.clear()
    limit = int(settings.UPLOAD_MAX_CONCURRENT_PER_USER)
    assert limit >= 1, "the per-user upload concurrency limit is not a positive number"

    held = []
    try:
        for _ in range(limit):
            slot = limiter.slot("user:gate5", limit)
            await slot.__aenter__()
            held.append(slot)

        with pytest.raises(HTTPException) as refused:
            async with limiter.slot("user:gate5", limit):
                pass
        assert refused.value.status_code == 429
    finally:
        for slot in reversed(held):
            await slot.__aexit__(None, None, None)
        limiter._local_counts.clear()

    async with limiter.slot("user:gate5", limit):
        pass  # capacity is restored once the held slots are released
    limiter._local_counts.clear()


async def test_the_per_org_limit_is_a_separate_bucket_from_the_per_user_limit() -> None:
    """A single user must not exhaust the org budget under its own key.

    Both routers acquire a user slot and an org slot, so the two keys have to be
    independent or the second acquisition is decorative.
    """
    limiter = UploadConcurrencyLimiter()
    limiter._local_counts.clear()
    try:
        async with limiter.slot("user:solo", 1):
            async with limiter.slot("org:solo", 1):
                pass
    finally:
        limiter._local_counts.clear()


def test_the_upload_routes_acquire_both_concurrency_slots() -> None:
    for name in ("documents.py", "contracts.py"):
        source = (ROUTERS / name).read_text(encoding="utf-8")
        assert "settings.UPLOAD_MAX_CONCURRENT_PER_USER" in source, (
            f"{name} no longer bounds concurrent uploads per user"
        )
        assert "settings.UPLOAD_MAX_CONCURRENT_PER_ORG" in source, (
            f"{name} no longer bounds concurrent uploads per organisation"
        )


# --------------------------------------------------------------------------- #
# MIME and extension, kept alongside so the bullet reads as one thing
# --------------------------------------------------------------------------- #


async def test_a_disallowed_mime_is_refused_by_sniffed_content_not_by_extension() -> None:
    """A `.pdf` name over executable bytes must not pass.

    Extension trust is the classic upload bypass; `validate_spooled_upload`
    sniffs instead, and refuses executables outright.
    """
    upload = _LyingUpload("innocent.pdf", b"MZ" + b"\x00" * 4096)
    spooled = await spool_upload_file(upload, max_size_bytes=1024 * 1024)
    try:
        result = validate_spooled_upload(spooled, list(settings.ALLOWED_DOCUMENT_MIMES))
        assert not result.is_valid
        assert result.error
    finally:
        await spooled.cleanup()


async def test_an_empty_upload_is_refused() -> None:
    upload = _LyingUpload("empty.pdf", b"")
    spooled = await spool_upload_file(upload, max_size_bytes=1024)
    try:
        result = validate_spooled_upload(spooled, list(settings.ALLOWED_DOCUMENT_MIMES))
        assert not result.is_valid
        assert result.error == "File is empty"
    finally:
        await spooled.cleanup()
