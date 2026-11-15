"""Contract Master reprojection has exactly one runtime owner, and it is wired.

The staging failure was not a bug inside the reprojection worker - it was that
nothing ever ran it. These guards pin the wiring itself, so the gap cannot
quietly reopen:

* the contract-worker process starts the reprojection loop when its flag is set,
  and only then;
* the web process never starts it;
* exactly one production service sets the flag, and it is ``contract-worker``;
* nothing in production code marks a projection current by hand - the one step
  every earlier suite performed and production never did;
* the embedding client refuses its deterministic fallback for strict callers.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parents[1]


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# the owner
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("enabled", [True, False])
def test_the_worker_process_starts_reprojection_only_when_flagged(monkeypatch, enabled):
    from rbac_backend import worker

    calls = []

    async def noop(*args, **kwargs):
        return None

    async def fake_start(db, *, interval_seconds):
        calls.append(("start", interval_seconds))

    async def fake_stop():
        calls.append(("stop",))

    class _SetEvent(asyncio.Event):
        def __init__(self) -> None:
            super().__init__()
            self.set()

    for name in (
        "connect_database",
        "disconnect_database",
        "start_background_services",
        "stop_background_services",
        "start_document_extraction_workers",
        "stop_document_extraction_workers",
        "start_contract_ingest_queue",
        "stop_contract_ingest_queue",
        "start_drafting_queue",
        "stop_drafting_queue",
        "start_filing_export_queue",
        "stop_filing_export_queue",
        "start_scheduler",
        "stop_scheduler",
    ):
        monkeypatch.setattr(worker, name, noop)
    monkeypatch.setattr(worker, "start_contract_reprojection_runtime", fake_start)
    monkeypatch.setattr(worker, "stop_contract_reprojection_runtime", fake_stop)
    monkeypatch.setattr(worker.asyncio, "Event", _SetEvent)
    monkeypatch.setattr(
        worker,
        "settings",
        SimpleNamespace(
            validate_runtime_configuration=lambda: None,
            START_BACKGROUND_SERVICES=False,
            START_DOCUMENT_EXTRACTION_WORKERS=False,
            START_CONTRACT_QUEUE_WORKERS=False,
            START_DRAFTING_QUEUE_WORKERS=False,
            START_FILING_EXPORT_QUEUE_WORKERS=False,
            START_CONTRACT_REPROJECTION_WORKERS=enabled,
            CONTRACT_REPROJECTION_POLL_SECONDS=15.0,
        ),
    )

    async def fake_database():
        return object()

    import rbac_backend.core.database as database_module

    monkeypatch.setattr(database_module, "get_database", fake_database)

    class _State:
        async def close(self):
            return None

    monkeypatch.setattr(worker, "get_runtime_state", lambda: _State())

    _run(worker._run())

    if enabled:
        assert calls[0][0] == "start" and calls[-1] == ("stop",)
    else:
        assert calls == []


def test_only_the_worker_entrypoint_starts_the_reprojection_loop():
    """The web process must never own derived-store work."""
    starters = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "start_contract_reprojection_runtime(" in text:
            starters.append(path.relative_to(BACKEND).as_posix())
    assert sorted(starters) == [
        "services/contract_reprojection_runtime.py",
        "worker.py",
    ], starters
    main = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "reprojection" not in main


def test_exactly_one_production_service_runs_reprojection():
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    owners = [
        name
        for name, service in (compose.get("services") or {}).items()
        if str((service.get("environment") or {}).get("START_CONTRACT_REPROJECTION_WORKERS", "")).lower()
        == "true"
    ]
    assert owners == ["contract-worker"], owners
    worker_command = compose["services"]["contract-worker"]["command"]
    assert worker_command == ["python", "-m", "rbac_backend.worker"]


def test_the_web_tier_default_does_not_start_reprojection():
    from rbac_backend.core.config import Settings

    assert Settings.model_fields["START_CONTRACT_REPROJECTION_WORKERS"].default is False


# --------------------------------------------------------------------------- #
# no hand-marked CURRENT in production code
# --------------------------------------------------------------------------- #


def test_no_production_code_marks_a_projection_current_by_hand():
    """Only the fenced worker completion may publish a generation.

    ``PostPromotionSequencer.mark_projection_current`` asserts derived work exists
    without doing any; a production caller of it would re-create the exact lie
    this runtime replaces.
    """
    callers = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "mark_projection_current":
                callers.append(path.relative_to(BACKEND).as_posix())
    assert callers == [], callers


def test_projection_current_is_written_only_by_the_worker_fence():
    """A literal CURRENT stamp outside the worker (and the test-only sequencer)
    is a second, unfenced way to publish a generation."""
    allowed = {
        "services/contract_reprojection_worker.py",
        "services/contract_post_promotion.py",
    }
    writers = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "ProjectionStatus.CURRENT.value" in text and '"$set"' in text:
            rel = path.relative_to(BACKEND).as_posix()
            if rel not in allowed and "projection_status" in text:
                for line in text.splitlines():
                    if (
                        "ProjectionStatus.CURRENT.value" in line
                        and ":" in line
                        and "projection_status" in line
                        # A filter excluding CURRENT is not a write of it.
                        and "$ne" not in line
                    ):
                        writers.append(rel)
    assert writers == [], writers


# --------------------------------------------------------------------------- #
# strict embeddings
# --------------------------------------------------------------------------- #


def _client(api_key=None):
    from rbac_backend.config.document_processing_config import DocumentProcessingConfig
    from rbac_backend.retrieval.embeddings import EmbeddingClient

    config = DocumentProcessingConfig()
    config.openai_api_key = api_key
    return EmbeddingClient(config)


def test_a_strict_caller_never_receives_the_deterministic_fallback():
    from rbac_backend.retrieval.embeddings import EmbeddingUnavailable

    client = _client(api_key=None)
    with pytest.raises(EmbeddingUnavailable):
        _run(client.embed(["clause text"], strict=True))
    # The non-strict contract is unchanged: offline development still works.
    assert _run(client.embed(["clause text"]))[0], "offline fallback stopped working"


def test_a_provider_error_is_raised_to_a_strict_caller():
    from rbac_backend.retrieval.embeddings import EmbeddingUnavailable

    client = _client(api_key=None)

    class _Failing:
        class embeddings:  # noqa: N801 - mirrors the SDK shape
            @staticmethod
            async def create(**kwargs):
                raise ConnectionError("provider down")

    client._client = _Failing()
    with pytest.raises(EmbeddingUnavailable):
        _run(client.embed(["clause text"], strict=True))
    fallback = _run(client.embed(["clause text"]))
    assert fallback[0], "the non-strict fallback changed"


def test_a_short_provider_answer_is_not_accepted_by_a_strict_caller():
    from rbac_backend.retrieval.embeddings import EmbeddingUnavailable

    client = _client(api_key=None)

    class _Item:
        embedding = [0.1, 0.2]

    class _Short:
        class embeddings:  # noqa: N801
            @staticmethod
            async def create(**kwargs):
                class _Response:
                    data = [_Item()]

                return _Response()

    client._client = _Short()
    with pytest.raises(EmbeddingUnavailable):
        _run(client.embed(["a", "b"], strict=True))


def test_a_live_vector_store_keeps_no_in_memory_copy() -> None:
    """Review: the stand-in index grew for the life of the contract-worker."""
    import asyncio
    from types import SimpleNamespace

    from rbac_backend.config.document_processing_config import DocumentProcessingConfig
    from rbac_backend.retrieval.vector_client import VectorClient

    client = VectorClient(DocumentProcessingConfig())
    upserts = []
    client.enabled = True
    client._client = SimpleNamespace(upsert=lambda **kwargs: upserts.append(kwargs))
    client._qmodels = SimpleNamespace(PointStruct=lambda **kwargs: kwargs)
    client._ensure_collection = lambda namespace=None: "document_vectors"  # type: ignore[method-assign]

    written = asyncio.run(
        client.upsert([[1.0, 0.0]], [{"chunk_id": "c1", "org_id": "o", "document_id": "d"}])
    )
    assert written == 1 and len(upserts) == 1
    assert client._memory_index == []


def test_no_production_code_completes_a_generation_without_publishing_it():
    """``complete(claim)`` with no ``publish`` stamps CURRENT over no rows (tests only)."""
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "complete"
                and "worker" in ast.unparse(node.func.value).lower()
                and not any(keyword.arg == "publish" for keyword in node.keywords)
            ):
                offenders.append(f"{path.relative_to(BACKEND).as_posix()}:{node.lineno}")
    assert offenders == [], offenders


def test_general_reprocess_answers_409_for_a_governed_contract() -> None:
    """Fail visible: the route refuses instead of queueing a job the service drops."""
    import asyncio
    from types import SimpleNamespace

    from rbac_backend.routers.documents import DocumentController

    queued: list = []

    class _Service:
        async def get_document(self, _document_id):
            return SimpleNamespace(processing_status="completed", processing_error=None)

        async def is_governed_contract(self, _document_id):
            return True

        async def process_document_async(self, *args, **kwargs):  # pragma: no cover
            queued.append(args)
            return True

    controller = DocumentController.__new__(DocumentController)
    controller.document_service = _Service()
    try:
        asyncio.run(controller.process_document("doc-1", None, skip_authorization=True))
    except Exception as exc:  # DocumentError or its HTTP rendering
        status = getattr(exc, "http_status", getattr(exc, "status_code", None))
        detail = str(getattr(exc, "detail", "") or getattr(exc, "message", "") or exc)
    else:  # pragma: no cover - the refusal is the point
        raise AssertionError("a governed contract was accepted for general reprocessing")
    assert status == 409, (status, detail)
    assert "Contract Master" in detail
    assert queued == []
