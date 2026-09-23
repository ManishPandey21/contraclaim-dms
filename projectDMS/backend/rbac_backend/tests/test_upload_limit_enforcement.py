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

import ast
import re
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

# R-A8U moved the static analyser into `upload_surface` so that the inventory in
# `test_upload_route_inventory.py` and this bullet's guard cannot drift apart:
# one answers "which routes must be bounded" and the other "is anything
# unbounded", and they have to agree about what an upload is.
from rbac_backend.tests.upload_surface import (
    RBAC_BACKEND,
    ROUTERS,
    scan_upload_trees,
    uncapped_upload_reads,
)


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
    from rbac_backend.tests.selection_fixtures import selection

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
            selection=selection(None, "org-gate5", "proj-gate5"),
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


def test_nothing_reads_an_upload_without_a_cap() -> None:
    """The class-level guard, over routers AND services.

    R-A8U replaced the guard's body with `upload_surface.uncapped_upload_reads`,
    which measures the property rather than three spellings of it. See that
    module for what F-A8T2-5 and F-A8T2-6 were, and what "bounded" now means.
    """
    offenders = scan_upload_trees()

    assert not offenders, (
        "these materialise an upload body with no defensible size limit, so the "
        "only bound on what the process holds is the gateway's "
        "client_max_body_size:\n  "
        + "\n  ".join(offenders)
        + "\nUse services.upload_streaming.read_upload_within_limit (async), "
        "read_file_within_limit (the spooled handle) or "
        "read_request_body_within_limit (a raw Request) instead."
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
    # ---- R-A8U: the shapes the review found the guard could not see --------- #
    pytest.param(
        "async def r(self, upload_file, name):\n    return await upload_file.read()\n",
        id="an-unannotated-parameter-F-A8T2-5",
    ),
    pytest.param(
        "async def r(self, csv_file):\n    return csv_file.file.read()\n",
        id="an-unannotated-spooled-handle-F-A8T2-5",
    ),
    pytest.param(
        "async def r(file: UploadFile):\n    return await file.read(-1)\n",
        id="read-minus-one-F-A8T2-6",
    ),
    pytest.param(
        "async def r(file: UploadFile):\n    return await file.read(None)\n",
        id="read-none-F-A8T2-6",
    ),
    pytest.param(
        "async def r(file: UploadFile):\n    return await file.read(size=-1)\n",
        id="read-keyword-minus-one",
    ),
    pytest.param(
        "async def r(file: UploadFile):\n"
        "    handle = file\n"
        "    return await handle.read()\n",
        id="a-simple-alias",
    ),
    pytest.param(
        "async def r(file: UploadFile):\n"
        "    a = file\n"
        "    b = a\n"
        "    c = b\n"
        "    return await c.read()\n",
        id="a-three-hop-alias",
    ),
    pytest.param(
        "async def r(file: UploadFile):\n"
        "    handle = file.file\n"
        "    return handle.read()\n",
        id="the-spooled-handle-through-an-alias",
    ),
    pytest.param(
        "async def r(files: List[UploadFile]):\n    return await files[0].read()\n",
        id="an-indexed-upload",
    ),
    pytest.param(
        "async def r(files: List[UploadFile]):\n"
        "    for index, f in enumerate(files):\n"
        "        await f.read()\n",
        id="an-enumerated-loop-variable",
    ),
    pytest.param(
        "async def r(files: List[UploadFile]):\n"
        "    bodies = [await f.read() for f in files]\n"
        "    return bodies\n",
        id="a-comprehension-variable",
    ),
    pytest.param(
        "async def r(request: Request):\n    return await request.body()\n",
        id="a-whole-request-body",
    ),
]


@pytest.mark.parametrize("snippet", UNCAPPED_SHAPES)
def test_each_uncapped_shape_is_caught(snippet: str) -> None:
    """Without these the widened guard could be green because it looks at nothing.

    The first three were R-A8T's. The rest are R-A8U's, one per form the
    independent review demonstrated the old guard walking past.
    """
    assert uncapped_upload_reads(snippet, cap_bytes=1024 * 1024), f"not caught: {snippet!r}"


def test_a_fixed_read_larger_than_the_configured_cap_is_caught() -> None:
    """A number is not a limit if it is bigger than the limit.

    `await file.read(1_000_000_000)` asked for a size, so the old guard's
    `if node.args: continue` accepted it. It reads a gigabyte.
    """
    snippet = "async def r(file: UploadFile):\n    return await file.read(1000000000)\n"
    found = uncapped_upload_reads(snippet, cap_bytes=8 * 1024 * 1024)
    assert found, "a fixed read far larger than the configured cap was accepted"
    assert "configured cap" in found[0], found


def test_the_cap_the_guard_compares_against_is_the_configured_one() -> None:
    """The same principle as `test_the_routers_pass_the_configured_limit`.

    A guard that hard-coded 100 MB would keep passing after somebody set
    `GENERAL_UPLOAD_MAX_FILE_SIZE_MB=100000`.
    """
    snippet = "async def r(file: UploadFile):\n    return await file.read(4096)\n"
    assert uncapped_upload_reads(snippet, cap_bytes=1024), (
        "a 4 KB read was accepted against a 1 KB cap"
    )
    assert uncapped_upload_reads(snippet, cap_bytes=8192) == [], (
        "a 4 KB read was refused against an 8 KB cap"
    )


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
        pytest.param(
            "async def r(file: UploadFile):\n"
            "    while True:\n"
            "        chunk = await file.read(chunk_size)\n"
            "        if not chunk:\n"
            "            break\n",
            id="a-streaming-bounded-loop",
        ),
        pytest.param(
            "async def r(file: UploadFile):\n"
            "    body = await read_upload_within_limit(file, cap)\n"
            "    return body.read\n",
            id="bytes-from-the-seam-are-not-tainted",
        ),
        pytest.param(
            "async def r(request: Request):\n"
            "    return await read_request_body_within_limit(request, 1024)\n",
            id="the-request-seam",
        ),
        pytest.param(
            "def r(path):\n"
            "    with open(path, 'rb') as handle:\n"
            "        return handle.read()\n",
            id="a-local-file-is-not-an-upload",
        ),
    ],
)
def test_the_guard_does_not_ban_a_bounded_read(snippet: str) -> None:
    """The false-positive boundary. A guard that banned the correct seam, or a
    plain local file read, would be routed around rather than obeyed."""
    assert uncapped_upload_reads(snippet, cap_bytes=1024 * 1024) == []


def test_removing_a_routes_size_guard_turns_the_class_guard_red() -> None:
    """The mutation control, run against the real router source.

    `documents.py` is read from disk, its `spool_upload_file(...)` call rewritten
    into the bare `await file.read()` it replaced, and the guard re-run over the
    mutated text. A guard that stayed green here would be certifying nothing -
    which is precisely what the pre-R-A8U guard did for `read(-1)` and for an
    unannotated parameter.
    """
    original = (ROUTERS / "documents.py").read_text(encoding="utf-8-sig")
    assert uncapped_upload_reads(original, "documents.py") == [], (
        "documents.py is already reported by the guard, so this control cannot "
        "tell a mutation from the baseline"
    )

    mutated = re.sub(
        r"await spool_upload_file\((\w+)[^)]*\)",
        r"await \1.read()",
        original,
        count=1,
    )
    assert mutated != original, (
        "the mutation did not apply, so this control proves nothing - the same "
        "way R-A8T Part 2's first `mongo_restore` control did not"
    )

    assert uncapped_upload_reads(mutated, "documents.py(mutated)"), (
        "removing the size guard from a real upload route left the class guard "
        "green"
    )


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
# The raw-request seam: `read_request_body_within_limit` (R-A8U)
# --------------------------------------------------------------------------- #
#
# `POST /api/billing/webhooks/{provider}` is intentionally unauthenticated - the
# payment provider calls it, not a logged-in user - and it did
# `raw_body = await request.body()`. That materialises the whole request before
# anything can object to its size, so an anonymous caller decided how much the
# process held, bounded only by the gateway's `client_max_body_size 200m`.
#
# The Gate 5 upload guard was green over it for the reason F-A8T2-5 names: the
# guard was built around `UploadFile` parameters and this route has none.


class _StreamingRequest:
    """A `Request`-shaped object that yields its body in transport-sized chunks.

    Like `_LyingUpload`, it advertises a size it does not honour, so an
    implementation that starts trusting `content-length` is caught here.
    """

    def __init__(self, body: bytes, *, chunk: int = 4096, declared_size: int = 1) -> None:
        self._body = body
        self._chunk = chunk
        self.headers = {"content-length": str(declared_size)}
        self.chunks_served = 0

    async def stream(self):
        for offset in range(0, len(self._body), self._chunk):
            piece = self._body[offset : offset + self._chunk]
            self.chunks_served += 1
            yield piece


@pytest.mark.parametrize(
    "delta, allowed",
    [
        pytest.param(-1, True, id="one-byte-below-max"),
        pytest.param(0, True, id="exactly-max"),
        pytest.param(1, False, id="one-byte-over-max"),
    ],
)
async def test_the_raw_request_read_is_capped_at_its_boundary(delta: int, allowed: bool) -> None:
    from rbac_backend.services.upload_streaming import read_request_body_within_limit

    limit = 4096
    request = _StreamingRequest(b"a" * (limit + delta), chunk=512)

    if allowed:
        assert len(await read_request_body_within_limit(request, limit)) == limit + delta
        return

    with pytest.raises(UploadTooLargeError):
        await read_request_body_within_limit(request, limit)


async def test_the_raw_request_read_refuses_before_buffering_the_whole_body() -> None:
    """The distinction the fix is about, measured the same way as the others."""
    from rbac_backend.services.upload_streaming import read_request_body_within_limit

    body = b"a" * (256 * 1024)
    request = _StreamingRequest(body, chunk=4096)

    with pytest.raises(UploadTooLargeError):
        await read_request_body_within_limit(request, 1024)

    assert request.chunks_served * 4096 < len(body), (
        "the whole body was streamed before the cap refused it"
    )


async def test_a_forged_content_length_buys_the_webhook_nothing() -> None:
    """The header says one byte; the transport delivers a megabyte."""
    from rbac_backend.services.upload_streaming import read_request_body_within_limit

    request = _StreamingRequest(b"a" * (1024 * 1024), chunk=8192, declared_size=1)
    with pytest.raises(UploadTooLargeError) as refused:
        await read_request_body_within_limit(request, 4096)
    assert refused.value.status_code == 413


async def test_the_bounded_body_is_byte_identical_to_what_request_body_returns() -> None:
    """A webhook signature is computed over the raw body.

    Stripe and Razorpay both HMAC the exact bytes they sent. A seam that
    normalised, re-encoded or dropped a chunk boundary would keep every size
    test green and make every real webhook fail its signature check - a
    self-inflicted outage on an endpoint no test in this repository signs.
    """
    from starlette.requests import Request  # noqa: PLC0415

    from rbac_backend.services.upload_streaming import (  # noqa: PLC0415
        read_request_body_within_limit,
    )

    payload = b'{"id":"evt_1","data":{"object":{"amount":1999}}}\n\x00\xff'

    def _make_request():
        chunks = [payload[:10], payload[10:30], payload[30:]]
        sent = iter(chunks)

        async def receive():
            try:
                return {"type": "http.request", "body": next(sent), "more_body": True}
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}

        scope = {"type": "http", "method": "POST", "headers": [], "path": "/x"}
        return Request(scope, receive)

    assert await _make_request().body() == payload, "the fake transport is wrong"
    assert await read_request_body_within_limit(_make_request(), len(payload) * 4) == payload


async def test_the_bounded_body_is_cached_so_a_later_read_still_works() -> None:
    """`Request.stream()` raises `RuntimeError("Stream consumed")` once it is
    exhausted. Caching the way `Request.body()` does keeps this a drop-in
    replacement, so a second reader gets the body rather than an error that
    looks nothing like the size limit that consumed the stream."""
    from starlette.requests import Request  # noqa: PLC0415

    from rbac_backend.services.upload_streaming import (  # noqa: PLC0415
        read_request_body_within_limit,
    )

    payload = b"signed-payload"
    sent = iter([payload])

    async def receive():
        try:
            return {"type": "http.request", "body": next(sent), "more_body": True}
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({"type": "http", "method": "POST", "headers": [], "path": "/x"}, receive)

    assert await read_request_body_within_limit(request, 1024) == payload
    assert await request.body() == payload


def test_the_webhook_route_reads_its_body_through_the_bounded_seam() -> None:
    """Read from the source, because the property is about the deployed route.

    A runtime test that constructed its own bounded read would stay green after
    somebody put `await request.body()` back.
    """
    source = (ROUTERS / "billing_webhooks.py").read_text(encoding="utf-8")
    assert "read_request_body_within_limit" in source, (
        "the billing webhook no longer bounds its request body"
    )
    assert "raw_body = await request.body()" not in source, (
        "the billing webhook is back to materialising the whole request body"
    )
    assert "settings.WEBHOOK_MAX_BODY_SIZE_KB" in source, (
        "the webhook body cap is no longer read from configuration"
    )
    assert int(settings.WEBHOOK_MAX_BODY_SIZE_KB) > 0, (
        "the configured webhook body cap is not a positive number of kilobytes"
    )


def test_the_unannotated_service_reads_are_bounded_at_their_seam() -> None:
    """F-A8T2-5's three live sites, pinned so they cannot regress.

    None of the three has a caller today. That is the reason they were not fixed
    inside R-A8T Part 2's spent window, and it is not a reason to leave them: an
    unreachable whole-body read is one caller away from a reachable one, and the
    guard that was supposed to see them could not.
    """
    services = RBAC_BACKEND / "services"
    file_service = (services / "file_service.py").read_text(encoding="utf-8")
    assert "data = await upload_file.read()" not in file_service
    assert "data = await chunk_file.read()" not in file_service
    assert file_service.count("read_upload_within_limit") >= 2, (
        "FileService.store_file / store_chunk no longer bound their reads"
    )

    s3 = (services / "s3_service.py").read_text(encoding="utf-8")
    assert 'file.read() if hasattr(file, "read")' not in s3
    assert "read_file_within_limit" in s3, (
        "S3Service.upload_file no longer bounds the handle it is given"
    )


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


# --------------------------------------------------------------------------- #
# R-A8U second review: reads that never spell `.read()`, and a one-word rename #
# --------------------------------------------------------------------------- #


R_A8U_ESCAPES = [
    pytest.param(
        "async def r(req: Request):\n    return await req.body()\n",
        id="a-request-parameter-not-called-request",
    ),
    pytest.param(
        "async def r(request: Request):\n    return await request.form()\n",
        id="request-form-parses-a-multipart-body-whole",
    ),
    pytest.param(
        "async def r(incoming: Request):\n    return await incoming.json()\n",
        id="request-json-reads-the-body-whole",
    ),
    pytest.param(
        "import shutil\n"
        "def r(file: UploadFile, dst):\n"
        "    shutil.copyfileobj(file.file, dst)\n",
        id="copyfileobj-drains-the-spooled-handle",
    ),
    pytest.param(
        "def r(file: UploadFile):\n    return file.file.readlines()\n",
        id="readlines",
    ),
    pytest.param(
        "def r(file: UploadFile):\n    return file.file.getvalue()\n",
        id="getvalue",
    ),
    pytest.param(
        'def r(file: UploadFile):\n    return b"".join(file.file)\n',
        id="join-iterates-the-handle",
    ),
    pytest.param(
        "def r(file: UploadFile):\n    return list(file.file)\n",
        id="list-iterates-the-handle",
    ),
    pytest.param(
        "def r(file: UploadFile):\n"
        "    for line in file.file:\n"
        "        pass\n",
        id="a-for-loop-over-the-handle",
    ),
    pytest.param(
        "def r(file: UploadFile):\n"
        "    handle = file.file\n"
        "    return handle.readlines()\n",
        id="a-handle-through-an-alias",
    ),
    pytest.param(
        "class S:\n"
        "    def r(self, upload_file):\n"
        "        self.handle = upload_file.file\n"
        "        return self.handle.getvalue()\n",
        id="a-handle-stashed-on-self",
    ),
]


@pytest.mark.parametrize("snippet", R_A8U_ESCAPES)
def test_each_r_a8u_escape_is_caught(snippet: str) -> None:
    """Every one of these was measured returning `[]` by the second review.

    The first is the sharpest: the whole-request channel was recognised by
    asking whether the rendered receiver contained the word "request", so
    `async def r(req: Request)` defeated it with a one-word rename. It is read
    from the annotation now.
    """
    assert uncapped_upload_reads(snippet, cap_bytes=1024 * 1024), f"not caught: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param(
            "async def r(files: List[UploadFile]):\n"
            "    for f in files:\n"
            "        await f.read(1024)\n",
            id="iterating-a-list-of-uploads-is-not-draining-a-body",
        ),
        pytest.param(
            "async def r(files: List[UploadFile]):\n"
            "    for index, f in enumerate(files):\n"
            "        await f.read(1024)\n",
            id="enumerating-a-list-of-uploads",
        ),
        pytest.param(
            "def r(rows):\n    return list(rows)\n",
            id="listing-something-that-is-not-an-upload",
        ),
        pytest.param(
            "async def r(request: Request):\n    return request.headers.get('x')\n",
            id="a-request-consulted-for-headers-only",
        ),
        pytest.param(
            "class S:\n"
            "    def r(self, upload_file):\n"
            "        self.name = upload_file.filename\n"
            "        return self.name\n",
            id="an-attribute-that-is-not-the-handle",
        ),
    ],
)
def test_the_r_a8u_rules_do_not_ban_the_normal_shapes(snippet: str) -> None:
    """The false-positive boundary, and it is load-bearing.

    The first cut of the drain rule treated any iteration of a tainted value as
    draining a body, and reported six correct bulk-upload `for f in files:`
    loops - a list of objects, not a stream. Only a *handle* drains.

    `self.name = upload_file.filename` is the other half: binding the bare
    `self` made every `self.<anything>.read()` in a module a finding.
    """
    assert uncapped_upload_reads(snippet, cap_bytes=1024 * 1024) == []


def test_a_route_declared_with_a_keyword_path_is_still_in_the_inventory() -> None:
    """`@router.post(path="/x")` is the same route written differently, and
    `if not decorator.args: continue` dropped it silently."""
    from rbac_backend.tests import upload_surface  # noqa: PLC0415

    module = ast.parse(
        "from fastapi import APIRouter, UploadFile\n"
        "router = APIRouter(prefix='/p')\n"
        "@router.post(path='/x')\n"
        "async def h(file: UploadFile):\n"
        "    return await file.read()\n"
    )
    found = [item for node in ast.walk(module) for item in upload_surface._route_decorators(node, "/p")]
    assert ("POST", "/p/x") in found, found


def test_no_module_in_the_package_root_reads_an_upload_unbounded() -> None:
    """The one directory `UPLOAD_TREES` does not scan.

    `rbac_backend/documents.py` holds two `await file.read()` calls on routes
    that look exactly like the real ones. The module is deliberately dead and
    pinned unimportable by `test_canonical_document_lookup.py`, so there is no
    live bypass - but it sits where neither the guard nor the inventory looks,
    and it is the only `APIRouter(` outside `routers/`. This states the
    boundary as a measurement: every *other* top-level module in the package
    root is scanned, and the dead one must stay dead.
    """
    dead = RBAC_BACKEND / "documents.py"
    assert dead.is_file(), "the dead module moved; re-derive this boundary"

    offenders: list = []
    for path in sorted(RBAC_BACKEND.glob("*.py")):
        if path.name == "documents.py":
            continue
        offenders.extend(
            uncapped_upload_reads(path.read_text(encoding="utf-8-sig"), path.name)
        )
    assert not offenders, (
        "a module in the package root materialises an upload body with no limit, "
        "where neither UPLOAD_TREES nor the router inventory looks:\n  "
        + "\n  ".join(offenders)
    )


def test_the_dead_root_document_module_is_still_unimportable() -> None:
    """If anyone repairs that import, both guards stay green over two unbounded
    reads. This is what makes the exclusion above safe rather than convenient."""
    import importlib  # noqa: PLC0415

    with pytest.raises(Exception):
        importlib.import_module("rbac_backend.documents")


def test_add_api_route_is_not_used_by_any_router() -> None:
    """The inventory parses decorators. `router.add_api_route("/x", handler,
    methods=["POST"])` registers the same route and would not appear in it.

    Nothing uses it today. This is the cheap way to keep that true: adding the
    first one fails here, where the message says what else has to change.
    """
    offenders = [
        str(path.relative_to(RBAC_BACKEND))
        for path in sorted(ROUTERS.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "add_api_route" in path.read_text(encoding="utf-8-sig")
    ]
    assert not offenders, (
        "these register routes without a decorator, which the upload inventory "
        f"parses and would therefore not see: {offenders}. Teach "
        "upload_surface.upload_routes about the shape in the same commit."
    )


# --------------------------------------------------------------------------- #
# R-A8U: a refusal has to say what the limit was, and cost what it says        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cap, rendered",
    [
        pytest.param(5 * 1024 * 1024, "5MB", id="megabytes"),
        pytest.param(256 * 1024, "256KB", id="kilobytes"),
        pytest.param(1024, "1KB", id="exactly-one-kilobyte"),
        pytest.param(512, "512 bytes", id="bytes"),
    ],
)
def test_the_refusal_names_a_size_the_caller_can_act_on(cap: int, rendered: str) -> None:
    """`size // (1024 * 1024)` renders **"0MB"** for every cap below a megabyte.

    R-A8U introduced the first sub-megabyte caps on this service - the webhook
    body, the public telemetry and credential bodies - so this stopped being
    cosmetic: a provider or an operator reading "0MB" cannot tell what the limit
    is, and the natural next step is to assume the service is broken.
    """
    error = UploadTooLargeError(cap)
    assert error.status_code == 413
    assert rendered in str(error.detail), error.detail
    assert "0MB" not in str(error.detail)


def test_the_webhook_cap_is_large_enough_for_a_real_provider_payload() -> None:
    """A refused webhook is a LOST BILLING EVENT, not a rejected upload.

    Razorpay `subscription.charged` and Stripe `invoice.*` payloads with many
    line items or large `metadata` can exceed 256 KB, which was the first cap
    R-A8U shipped. The provider retries, the retries are refused identically,
    and after the retry window the event is gone - and the idempotency design
    cannot recover an event that was never recorded. 1 MB is still three orders
    below the gateway's 200 MB.
    """
    assert int(settings.WEBHOOK_MAX_BODY_SIZE_KB) >= 1024, (
        "the webhook body cap is below 1 MB; a large provider payload would be "
        "refused, retried, refused again and lost"
    )


def test_a_zero_body_cap_is_refused_by_configuration() -> None:
    """`WEBHOOK_MAX_BODY_SIZE_KB=0` is the natural spelling for "no limit".

    Downstream it became `max(1, 0)` = **one byte**, which 413s every webhook
    there is. A cap that means the opposite of what it says must not load.
    """
    from pydantic import ValidationError  # noqa: PLC0415

    from rbac_backend.core.config import Settings  # noqa: PLC0415

    for field in (
        "WEBHOOK_MAX_BODY_SIZE_KB",
        "PUBLIC_TELEMETRY_MAX_BODY_SIZE_KB",
        "PUBLIC_AUTH_MAX_BODY_SIZE_KB",
    ):
        with pytest.raises(ValidationError):
            Settings.model_fields[field].annotation  # noqa: B018
            Settings(**{field: 0}, _env_file=None)


def test_the_billing_webhook_says_so_when_it_drops_an_event() -> None:
    """The generic 413 in the request log does not name the billing channel.

    Read from the source rather than from a mock: the point is that the deployed
    route logs it, and a runtime test that built its own logger would stay green
    after the line was deleted.
    """
    source = (ROUTERS / "billing_webhooks.py").read_text(encoding="utf-8")
    assert "UploadTooLargeError" in source, (
        "the billing webhook no longer distinguishes an oversize refusal"
    )
    assert "REFUSED as oversize" in source, (
        "an oversize webhook is dropped with no billing-channel signal"
    )
