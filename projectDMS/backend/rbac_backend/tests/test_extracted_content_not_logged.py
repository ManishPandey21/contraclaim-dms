"""Gate 5 bullet 5: sensitive extracted text is not logged.

The bullet is about the one place a document management system leaks a whole
contract at once. Extraction turns a customer's PDF into text, and every module
downstream of it holds that text in a local variable. One `logger.debug(f"...
{text}")` added while debugging a parser copies a client's confidential
correspondence into the application log, the log shipper, and every backup of
both - with none of the access control the document itself carries.

R-A8R established that *query* redaction exists (`observability/service.py`
replaces a search query with `[redacted len=N]`). It did not establish anything
about extraction, which is the larger surface. This module covers both halves
the bullet needs:

**A. A static guard.** Every logging call in the ingestion and extraction trees
is parsed and its arguments inspected. An argument that evaluates to extracted
content fails; an argument that evaluates to a length, a count, an identifier,
a status or a path does not. The validator is a pure function over source, and
it is exercised against synthetic modules that break exactly one rule, because a
guard asserted only against the real tree would pass just as happily if it
checked nothing.

**B. A runtime control.** A synthetic document carrying a distinctive marker is
pushed through the real chunking and enrichment path with logging captured at
`DEBUG` on the root logger. The marker must not appear anywhere in what was
captured - message, formatted output, structured `extra` fields, or exception
text.

Neither half weakens metadata logging. Lengths, counts, page numbers, job and
document identifiers, MIME types, filenames and durations are all still allowed,
and several tests below exist specifically to keep them allowed: a guard that
banned `len(text)` would be routed around within a week.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import re
from pathlib import Path
from typing import Iterable, List

import pytest

RBAC_BACKEND = Path(__file__).resolve().parents[1]

#: The trees that turn documents into text, and the ones that immediately
#: consume it. Every path here is checked for existence by
#: `test_the_scanned_tree_is_real`, because a guard pointed at a directory that
#: does not exist passes while checking nothing - a failure this repository has
#: already had once.
SCANNED_PATHS = (
    RBAC_BACKEND / "ingestion",
    RBAC_BACKEND / "services" / "extraction",
    RBAC_BACKEND / "services" / "contract_clause",
    RBAC_BACKEND / "retrieval",
    RBAC_BACKEND / "services" / "document_processor.py",
    RBAC_BACKEND / "services" / "contracts_ingest.py",
    RBAC_BACKEND / "services" / "metadata_processor_service.py",
    RBAC_BACKEND / "services" / "ocr_service.py",
    # Added in R-A8T. `routers/ai_assistant.py:146` rendered 50 characters of a
    # search query at INFO while `observability/service.py::_redact_query`
    # reduced the identical value to `[redacted len=N]` - so the repository
    # already held that this string is sensitive, and the module sat in neither
    # the guard's scope nor its declared hand-checked boundary. These three are
    # the query/answer surface: they consume extracted content rather than
    # producing it, which is the same reason `retrieval/` is here.
    RBAC_BACKEND / "routers" / "ai_assistant.py",
    RBAC_BACKEND / "routers" / "retrieval_engine.py",
    RBAC_BACKEND / "services" / "ai_service.py",
)

LOG_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}

#: Identifiers whose value is, or can be, extracted document content.
CONTENT_WORDS = {
    "text",
    "texts",
    "content",
    "contents",
    "markdown",
    "body",
    "chunk",
    "chunks",
    "snippet",
    "snippets",
    "excerpt",
    "excerpts",
    "paragraph",
    "paragraphs",
    "clause",
    "clauses",
    "ocr",
    "extraction",
    "extracted",
    "page_text",
    "raw",
    "payload",
    "answer",
    "summary",
    "query",
    "prompt",
    "stdout",
    "stderr",
}

#: Suffixes that describe content without carrying it. `chunk_count` is a
#: number; `content_type` is a MIME string; `text_sha256` is a digest. Banning
#: these would make the guard useless and it would be disabled rather than
#: obeyed.
METADATA_SUFFIXES = (
    "_count",
    "_counts",
    "_len",
    "_length",
    "_size",
    "_bytes",
    "_chars",
    "_id",
    "_ids",
    "_uuid",
    "_uid",
    "_uids",
    "_key",
    "_keys",
    "_type",
    "_types",
    "_kind",
    "_status",
    "_state",
    "_path",
    "_paths",
    "_name",
    "_names",
    "_file",
    "_hash",
    "_sha256",
    "_digest",
    "_version",
    "_index",
    "_number",
    "_page",
    "_pages",
    "_mode",
    "_flag",
    "_enabled",
    "_ms",
    "_seconds",
    "_score",
    "_reason",
    "_code",
    "_total",
    "_totals",
    "_source",
    "_sources",
    "_label",
)

#: Calls that reduce content to a measurement. `len(text)` is the metadata the
#: bullet explicitly permits.
SAFE_CALLS = {"len", "bool", "type", "id", "hash", "repr_length", "sorted", "int", "float"}


# --------------------------------------------------------------------------- #
# The validator - pure, so synthetic modules can break it one rule at a time
# --------------------------------------------------------------------------- #


def _final_identifier(node: ast.AST) -> str | None:
    """The name a value is reached by: `x`, `obj.x`, `d["x"]` all answer `x`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        index = node.slice
        if isinstance(index, ast.Constant) and isinstance(index.value, str):
            return index.value
        return _final_identifier(node.value)
    return None


def _is_content_identifier(name: str | None) -> bool:
    if not name:
        return False
    lowered = name.lower().lstrip("_")
    if lowered.endswith(METADATA_SUFFIXES):
        return False
    if lowered in CONTENT_WORDS:
        return True
    parts = lowered.split("_")
    return any(part in CONTENT_WORDS for part in parts)


def _content_references(node: ast.AST) -> List[str]:
    """Every content-bearing identifier this expression would render.

    Descends through f-strings, `%` formatting, `.format(...)`, concatenation
    and containers, and stops at a call that reduces its argument to a
    measurement.
    """
    found: List[str] = []

    def walk(current: ast.AST) -> None:
        if isinstance(current, ast.Call):
            callee = _final_identifier(current.func)
            if callee in SAFE_CALLS:
                return
            if callee == "format":
                for argument in list(current.args) + [kw.value for kw in current.keywords]:
                    walk(argument)
                return
            for argument in list(current.args) + [kw.value for kw in current.keywords]:
                walk(argument)
            # `x.strip()`, `x[:80]`-style access still renders `x`.
            if isinstance(current.func, ast.Attribute):
                walk(current.func.value)
            return
        if isinstance(current, (ast.Name, ast.Attribute, ast.Subscript)):
            name = _final_identifier(current)
            if _is_content_identifier(name):
                found.append(name)
            # An attribute renders the attribute, not its container:
            # `extraction.ocr_failed_pages` is a count even though `extraction`
            # holds the text. Descending into the container reported the holder
            # of every metadata field and would have made the guard unusable.
            if isinstance(current, ast.Subscript) and not isinstance(
                current.slice, ast.Constant
            ):
                walk(current.value)
            return
        for child in ast.iter_child_nodes(current):
            walk(child)

    walk(node)
    return found


def _is_logging_call(call: ast.Call) -> str | None:
    """The level name if this is a logging call, else None."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr.lower() not in LOG_LEVELS:
        return None
    target = _final_identifier(func.value)
    if target is None:
        return None
    if "log" not in target.lower():
        return None
    return func.attr.lower()


def violations(source: str, label: str = "<source>") -> List[str]:
    """One message per logging call that would render extracted content."""
    problems: List[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        level = _is_logging_call(node)
        if level is None:
            continue
        arguments = list(node.args)
        arguments += [kw.value for kw in node.keywords if kw.arg == "extra"]
        for argument in arguments:
            for name in _content_references(argument):
                problems.append(
                    f"{label}:{node.lineno}: logger.{level} would render {name!r}, "
                    "which can carry extracted document content"
                )
    return problems


def raise_violations(source: str, label: str = "<source>") -> List[str]:
    """One message per `raise` whose exception would carry extracted content.

    The complement of `violations`, and the gap R-A8T's review found. The
    logging validator inspects the arguments of logging calls; an exception
    reaches a log through a *different* door - `logger.exception(...)` renders a
    traceback, `logger.warning("...: %s", exc)` renders the message, and neither
    call site names anything content-shaped. So a guard that reads only logging
    arguments certifies a tree in which

        raise ValueError(f"reranker LLM returned no JSON array: {raw[:200]!r}")

    is caught seventy lines later by

        logger.warning("Reranker (%s) failed; ...: %s", self.provider, exc)

    and the passage text the model echoed goes to the log. That was live in
    `retrieval/reranker.py` - inside `SCANNED_PATHS`, invisible to the guard
    that certified Gate 5 bullet 5.

    Deliberately not restricted to exceptions we know are logged: which caller
    logs which exception is not a property this file can check, and "nothing
    logs it today" is the same kind of luck the route parser was relying on.
    """
    problems: List[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        arguments: List[ast.expr] = []
        if isinstance(node.exc, ast.Call):
            arguments = list(node.exc.args) + [kw.value for kw in node.exc.keywords]
        for argument in arguments:
            for name in _content_references(argument):
                problems.append(
                    f"{label}:{node.lineno}: raise would carry {name!r}, which can "
                    "carry extracted document content into any log that renders "
                    "the exception"
                )
    return problems


# --------------------------------------------------------------------------- #
# The validator's own breakages, before it is trusted against the real tree
# --------------------------------------------------------------------------- #


SAFE_MODULE = '''
import logging
logger = logging.getLogger(__name__)

def run(text, chunks, document_id, page_count):
    logger.info("extracted %s characters", len(text))
    logger.info("wrote %s chunks for %s", len(chunks), document_id)
    logger.debug("pages=%s content_type=%s", page_count, "application/pdf")
    logger.warning(f"job {document_id} produced {len(chunks)} chunks")
'''


def test_the_safe_module_passes() -> None:
    """Anchor. Without this the mutations below could fail for the wrong reason."""
    assert violations(SAFE_MODULE, "safe") == []


@pytest.mark.parametrize(
    "snippet, expected",
    [
        pytest.param(
            'logger.debug("extracted: %s", text)',
            "text",
            id="positional-argument",
        ),
        pytest.param(
            'logger.info(f"got {extracted_text}")',
            "extracted_text",
            id="f-string",
        ),
        pytest.param(
            'logger.warning("chunk: " + chunk)',
            "chunk",
            id="concatenation",
        ),
        pytest.param(
            'logger.error("body {}".format(page_text))',
            "page_text",
            id="str-format",
        ),
        pytest.param(
            'logger.info("first 80: %s", text[:80])',
            "text",
            id="truncated-slice-is-still-content",
        ),
        pytest.param(
            'logger.debug("clause %s", result["markdown"])',
            "markdown",
            id="dict-key",
        ),
        pytest.param(
            'logger.info("done", extra={"snippet": snippet})',
            "snippet",
            id="structured-extra-field",
        ),
        pytest.param(
            'logger.exception("failed on %s", chunk.text)',
            "text",
            id="attribute-access",
        ),
        pytest.param(
            'logger.info("normalised %s", text.strip())',
            "text",
            id="method-call-on-content",
        ),
    ],
)
def test_each_leak_shape_is_caught(snippet: str, expected: str) -> None:
    source = "import logging\nlogger = logging.getLogger(__name__)\n" + snippet + "\n"
    found = violations(source, "mutation")
    assert any(expected in message for message in found), (
        f"expected {expected!r} to be reported; got {found}"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param('logger.info("chars=%s", len(text))', id="length-of-content"),
        pytest.param('logger.info("chunks=%s", chunk_count)', id="count-suffix"),
        pytest.param('logger.info("mime=%s", content_type)', id="type-suffix"),
        pytest.param('logger.info("digest=%s", text_sha256)', id="digest-suffix"),
        pytest.param('logger.info("page=%s", page_number)', id="number-suffix"),
        pytest.param('logger.info("file=%s", input_path.name)', id="path-name"),
        pytest.param('logger.info("job %s failed", job_id)', id="identifier"),
        pytest.param('logger.info("state=%s", extraction_status)', id="status-suffix"),
    ],
)
def test_metadata_logging_is_not_banned(snippet: str) -> None:
    """The guard must leave useful logging alone, or it will be deleted."""
    source = "import logging\nlogger = logging.getLogger(__name__)\n" + snippet + "\n"
    assert violations(source, "metadata") == []


def test_a_non_logging_call_is_ignored() -> None:
    """`store.save(text)` is the job, not a leak."""
    source = (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "store.save(text)\n"
        "vectors.upsert(chunks)\n"
    )
    assert violations(source, "non-logging") == []


# --------------------------------------------------------------------------- #
# A. The static guard, against the real tree
# --------------------------------------------------------------------------- #


def _scanned_modules() -> Iterable[Path]:
    for target in SCANNED_PATHS:
        if target.is_file():
            yield target
        elif target.is_dir():
            for path in sorted(target.rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                yield path


def test_the_scanned_tree_is_real() -> None:
    """Every declared path exists, and the set is big enough to be the real one."""
    missing = [str(path) for path in SCANNED_PATHS if not path.exists()]
    assert not missing, f"the guard points at paths that do not exist: {missing}"

    modules = list(_scanned_modules())
    assert len(modules) >= 40, f"the scanned module set is implausibly small: {len(modules)}"
    names = {path.name for path in modules}
    for expected in ("pipeline.py", "contracts_ingest.py", "document_processor.py"):
        assert expected in names, f"{expected} is not in the scanned set"


#: Modules that handle document-derived text but sit OUTSIDE `SCANNED_PATHS`,
#: checked by hand in R-A8S. Each was run through `violations()` and produced no
#: real finding; `document_service.py`'s two hits are a cascade-cleanup dict of
#: counts, booleans and error strings that happens to be named `summary`.
#:
#: They are listed rather than scanned because the guard would need a per-line
#: exemption to accept that dict, and a guard carrying hand-written exemptions is
#: weaker than one with an honest, stated boundary. This constant IS the stated
#: boundary: extending the guard's reach means moving a name from here into
#: `SCANNED_PATHS`, which is a deliberate act rather than a silent one.
CHECKED_BY_HAND_OUTSIDE_THE_GUARD = (
    "services/document_service.py",
    "services/metadata.py",
    "services/data_sync.py",
    "services/duplicate_detection_service.py",
    "services/chronology.py",
    "services/evidence_graph_service.py",
    "observability/service.py",
)


def test_the_declared_scope_boundary_is_real() -> None:
    """The modules named as out-of-scope must exist and must not also be in scope.

    A boundary that names a file which does not exist, or which the guard
    already covers, records nothing. Both have happened to inventories in this
    repository before.
    """
    scanned = {path.resolve() for path in _scanned_modules()}
    for rel in CHECKED_BY_HAND_OUTSIDE_THE_GUARD:
        path = RBAC_BACKEND / rel
        assert path.is_file(), f"the declared scope boundary names a missing file: {rel}"
        assert path.resolve() not in scanned, (
            f"{rel} is listed as outside the guard and is also scanned by it; one "
            "of the two is wrong"
        )


def test_the_scanned_tree_contains_logging_calls() -> None:
    """Otherwise the guard below is vacuously green."""
    total = 0
    for path in _scanned_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_logging_call(node) is not None
        )
    assert total >= 50, f"only {total} logging calls found in the extraction trees"


def test_no_extraction_module_logs_extracted_content() -> None:
    problems: List[str] = []
    for path in _scanned_modules():
        problems.extend(
            violations(
                path.read_text(encoding="utf-8"),
                str(path.relative_to(RBAC_BACKEND.parents[1])),
            )
        )
    assert not problems, (
        "extracted document content reaches a log call:\n" + "\n".join(problems)
    )


def test_no_extraction_module_raises_with_extracted_content() -> None:
    """The class fix for F-A8T-2. Three real channels, found by running this.

    `retrieval/reranker.py` embedded the model's reply - its answer to a prompt
    carrying 600 characters of each passage - in a `ValueError` that
    `RerankerService.rerank` logs at WARNING.
    `services/extraction/image_ocr_runner.py` and `ocrmypdf_runner.py` put up to
    500 characters of an OCR tool's stderr/stdout into their exceptions, which
    is the identical channel R-A8S closed in `contracts_ingest.py` for Marker.
    All three now carry an exit code and a length.
    """
    problems: List[str] = []
    for path in _scanned_modules():
        problems.extend(
            raise_violations(
                path.read_text(encoding="utf-8"),
                str(path.relative_to(RBAC_BACKEND.parents[1])),
            )
        )
    assert not problems, (
        "an exception in the extraction trees would carry document content, and "
        "an exception message reaches the log of whichever caller renders it:\n"
        + "\n".join(problems)
    )


RAISE_LEAK_SHAPES = [
    pytest.param('raise ValueError(f"bad clause: {text}")', "text", id="f-string"),
    pytest.param('raise RuntimeError("failed: %s" % chunk)', "chunk", id="percent"),
    pytest.param("raise OcrError(stderr.decode()[:500])", "stderr", id="sliced-stderr"),
    pytest.param('raise ValueError("no array: " + raw[:200])', "raw", id="concatenation"),
    pytest.param("raise ParseError(detail=extracted_text)", "extracted_text", id="keyword"),
]


@pytest.mark.parametrize("snippet,expected", RAISE_LEAK_SHAPES)
def test_each_raise_leak_shape_is_caught(snippet: str, expected: str) -> None:
    found = raise_violations(snippet)
    assert found, f"the raise validator did not catch: {snippet}"
    assert any(expected in message for message in found), found


@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param(
            'raise ValueError(f"exit={code} stderr_bytes={len(stderr)}")', id="lengths"
        ),
        pytest.param("raise ClauseNotFoundError(clause_uid)", id="an-identifier"),
        pytest.param('raise OcrError(f"failed after {page_count} pages")', id="a-count"),
        pytest.param("raise ValueError(text_sha256)", id="a-digest"),
        pytest.param("raise", id="a-bare-reraise"),
    ],
)
def test_the_raise_validator_does_not_ban_metadata(snippet: str) -> None:
    """Same reason as the logging half: a guard that banned `len(text)` would be
    disabled rather than obeyed. `clause_uid` is an identifier, and `_uid` was
    added to the metadata suffixes for it - a validator correction, not a code
    change."""
    assert raise_violations(snippet) == []


# --------------------------------------------------------------------------- #
# B. The runtime control
# --------------------------------------------------------------------------- #


#: Distinctive enough that a substring search cannot match it by accident, and
#: shaped like the confidential text this bullet is about.
SYNTHETIC_SECRET = "ZQXJV-CONFIDENTIAL-CLAUSE-7f31a9-DO-NOT-LOG"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.seen: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        parts = [str(record.msg)]
        parts.extend(str(arg) for arg in (record.args or ()))
        try:
            parts.append(record.getMessage())
        except Exception as exc:  # a broken format string must not hide the body
            parts.append(f"<unformattable: {exc}>")
        if record.exc_info:
            parts.append(logging.Formatter().formatException(record.exc_info))
        for key, value in record.__dict__.items():
            if key in logging.LogRecord("", 0, "", 0, "", None, None).__dict__:
                continue
            parts.append(f"{key}={value}")
        self.seen.append(" ".join(parts))


def _capture_all_logging() -> _Capture:
    handler = _Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    return handler


def test_a_real_chunking_run_does_not_log_its_input() -> None:
    """Push a marked document through the real chunker and read every log line.

    This is the half the static guard cannot do: a leak through a third-party
    library's own logger, or through an exception message that quotes the input,
    has no `logger.debug(text)` for the AST to find.
    """
    from rbac_backend.ingestion.chunker import chunk_text  # noqa: PLC0415

    document = "\n\n".join(
        [
            "1. Preliminary.",
            f"2. The Contractor shall {SYNTHETIC_SECRET} within 28 days.",
            "3. Termination.",
        ]
    )

    handler = _capture_all_logging()
    try:
        chunks = list(
            chunk_text(
                "doc-gate5",
                "org-gate5",
                "proj-gate5",
                document,
                512,
                32,
            )
        )
    finally:
        logging.getLogger().removeHandler(handler)

    assert chunks, "the chunker produced nothing, so the control measured nothing"
    assert any(SYNTHETIC_SECRET in chunk.text_original for chunk in chunks), (
        "the marker did not survive chunking, so its absence from the logs proves "
        "nothing about whether extracted content is logged"
    )

    leaked = [line for line in handler.seen if SYNTHETIC_SECRET in line]
    assert not leaked, f"extracted content reached the logs: {leaked}"


def _reranker_results(text: str):
    """One candidate carrying `text` where `rerank` looks for a passage.

    `rerank` reads `payload["text_enriched"] or payload["text"] or snippet`, so
    the marker is placed in `text` - the field a real Qdrant hit carries.
    """
    from rbac_backend.retrieval.models import SearchResult  # noqa: PLC0415

    return [
        SearchResult(
            document_id="doc-1",
            chunk_id="doc-1:0",
            score=0.9,
            snippet=text,
            payload={"text": text},
        )
    ]


def test_an_extraction_failure_does_not_quote_the_document() -> None:
    """The exception channel, driven through the real code that had the defect.

    **This test used to prove nothing.** It raised
    `ValueError("clause parse failed at offset 41")` - a hand-written constant
    with no marker in it - and then asserted the marker was absent from the
    logs. The assertion was true by construction, and would have stayed true if
    every exception in the codebase quoted the document. It was the only thing
    standing between F-A8T-2 and Gate 5 bullet 5's tick.

    It now drives `RerankerService.rerank` with a backend LLM that echoes the
    passage text back instead of returning a JSON array. That is the exact
    production path: the reply reaches `LLMRerankerBackend.score`, which raises,
    and `rerank` catches and logs the exception at WARNING. The marker must
    reach the prompt (or the run proves nothing) and must not reach any log.
    """
    from rbac_backend.retrieval.reranker import (  # noqa: PLC0415
        LLMRerankerBackend,
        RerankerService,
    )

    class _EchoingLLM:
        """The failure mode: a model that repeats its prompt instead of scoring."""

        def __init__(self) -> None:
            self.saw_the_marker = False

        async def generate(self, prompt: str, **_: object) -> str:
            self.saw_the_marker = SYNTHETIC_SECRET in prompt
            # Brackets are stripped so the reply cannot accidentally parse as
            # the JSON array the backend is looking for. Echoing the prompt
            # verbatim let `[1] <passage>` be read as a valid one-element score
            # array, and no exception was raised at all - which the "was it
            # logged" assertion below caught, and a weaker test would not have.
            echoed = prompt.replace("[", "(").replace("]", ")")
            # Passages first, so a reply the code truncates - it used to embed
            # `raw[:200]` - still carries the marker. Echoing the prompt in its
            # own order put 200 characters of instructions in front of the
            # document text, and the runtime control passed against the very
            # defect it exists to catch.
            marker_first = echoed[echoed.index("Passages:") :]
            return f"{marker_first} -- I cannot score these."

    llm = _EchoingLLM()
    service = RerankerService(
        LLMRerankerBackend(llm), enabled=True, provider="llm", top_n=5
    )
    results = _reranker_results(f"Clause 14.1 {SYNTHETIC_SECRET} shall apply.")

    handler = _capture_all_logging()
    try:
        returned = asyncio.run(service.rerank("what does clause 14.1 say", results))
    finally:
        logging.getLogger().removeHandler(handler)

    assert llm.saw_the_marker, (
        "the marker never reached the reranker prompt, so its absence from the "
        "logs proves nothing - the control measured nothing"
    )
    assert handler.seen, "nothing was captured, so the control measured nothing"
    assert any("Reranker" in line for line in handler.seen), (
        "the reranker failure was not logged, so this run did not exercise the "
        f"channel: {handler.seen}"
    )
    assert returned == results, "a backend failure must return the input ordering"

    leaked = [line for line in handler.seen if SYNTHETIC_SECRET in line]
    assert not leaked, (
        f"the passage text reached a log through the exception message: {leaked}"
    )


def test_the_capture_handler_would_notice_a_leak() -> None:
    """The negative control for the control.

    Without this, a capture handler that silently dropped every record would
    make both runtime tests above pass forever.
    """
    logger = logging.getLogger("rbac_backend.tests.capture_control")
    handler = _capture_all_logging()
    try:
        logger.info("planted %s", SYNTHETIC_SECRET)
        logger.info("planted in extra", extra={"leaked": SYNTHETIC_SECRET})
        try:
            raise RuntimeError(f"boom {SYNTHETIC_SECRET}")
        except RuntimeError:
            logger.exception("planted in a traceback")
    finally:
        logging.getLogger().removeHandler(handler)

    hits = [line for line in handler.seen if SYNTHETIC_SECRET in line]
    assert len(hits) == 3, (
        "the capture handler does not see every channel a leak can use; it found "
        f"{len(hits)} of 3 (message args, structured extra, exception text)"
    )


def test_the_query_redaction_that_already_existed_still_holds() -> None:
    """R-A8R's finding, kept as a regression rather than restated as prose."""
    from rbac_backend.observability.service import ObservabilityService  # noqa: PLC0415

    redacted = ObservabilityService._redact_query(SYNTHETIC_SECRET)
    assert redacted is not None
    assert SYNTHETIC_SECRET not in redacted
    assert re.fullmatch(r"\[redacted len=\d+\]", redacted), redacted
