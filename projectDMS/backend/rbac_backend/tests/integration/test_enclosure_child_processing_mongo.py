"""Phase 8 PR B: an uploaded enclosure becomes a first-class processed child.

``POST /api/documents/{parent}/enclosures`` over real HTTP, real Mongo with its
real indexes and transactions, and the real ``get_current_user`` /
``ActiveScope`` / ``PolicyService`` chain (signed JWTs, seeded roles, no
dependency override). The upload is followed through the ordinary pipeline:

    FileObject -> child Document -> DocumentVersion -> processing job
    -> worker (DocumentService.process_document_job -> DocumentProcessor
       -> page-wise extraction -> DocumentPageStore -> canonical evidence)
    -> human review when required -> vector publication

Only the edges are replaced: OCR answers from the text drawn into each raster
(the binaries are absent in CI), OpenAI answers from the processor's own OCR
report, and the vector/graph providers are the in-process seams the extraction
review suite uses. Everything between is production code.

Owner decisions this suite pins (Phase 8 PR B brief):

* Child creation fails closed unless the live ``uq_documents_child_identity``
  (unique) and ``ix_documents_children_by_parent`` indexes hold.
* Identity is ``(parent, relationship, sha256)``; every layer of a retry is
  idempotent - FileObject, child, DocumentVersion and processing job.
* No enclosure-specific routing: the ordinary selector decides, so a canary
  organisation gets ``unified_v1`` and every other one ``legacy_v0``.
* A child's evidence is its own: its pages, head and provenance name the child,
  never the parent.
* Children stay out of ordinary listings and search until PR C.

Opt-in: set ``ENCLOSURE_CHILD_MONGODB_URI`` to a disposable replica set.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

import httpx
import pikepdf
import pytest
from bson import ObjectId
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.tests.integration.test_extraction_review_mongo import Seams
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    DOC_IN,
    DOC_IN_ID,
    DOC_OUT_ID,
    ORG_A,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    _seed,
    _token,
)

MONGODB_URI_ENV = "ENCLOSURE_CHILD_MONGODB_URI"

pytestmark = pytest.mark.integration

#: Pages read by the truth OCR runner need at least this much text to count.
MIN_TEXT_CHARS = 10

#: Keys a child audit event may carry: identifiers only, never file names,
#: storage keys, paths or content.
CHILD_AUDIT_KEYS = {
    "parent_document_id",
    "relationship_type",
    "file_object_id",
    "enclosure_entry_id",
    "source_page_range",
    "version_id",
    "version_number",
    "processing_job_id",
    "pipeline_version",
    "remaining_references",
    "file_object_unreferenced",
}

SCANNED_LINES = {
    1: ["Annexure B - Joint measurement record", "Guide wall panel GW-12 measured 14.2 m"],
    2: ["Signed for the Engineer", "Measured quantity agreed at 14.2 m"],
}
NATIVE_TEXT = "Annexure A - Programme narrative for the extended works period."


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Synthetic enclosures (customer PDFs are never committed)
# --------------------------------------------------------------------------- #


def _native_pdf(path: Path) -> Path:
    from rbac_backend.tests.fixtures.pdf_builders import build_text_pdf

    return build_text_pdf(path, pages=2, text=NATIVE_TEXT)


def _scanned_pdf(path: Path) -> Path:
    from rbac_backend.tests.fixtures.mixed_correspondence_pdfs import (
        _add_scanned_letter_page,
    )

    pdf = pikepdf.new()
    for number in sorted(SCANNED_LINES):
        _add_scanned_letter_page(pdf, SCANNED_LINES[number])
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(str(path), deterministic_id=True)
    pdf.close()
    return path


def _mixed_pdf(path: Path) -> Path:
    from rbac_backend.tests.fixtures.mixed_correspondence_pdfs import build_claim_like_pdf

    return build_claim_like_pdf(path)


def _scanned_truth() -> Dict[int, str]:
    from rbac_backend.tests.fixtures.mixed_correspondence_pdfs import page_text_map

    return page_text_map(SCANNED_LINES)


# --------------------------------------------------------------------------- #
# The worker: the real chain, with OCR and OpenAI answered in-process
# --------------------------------------------------------------------------- #


class _TruthOcr:
    """Returns the text drawn into each raster; records what it was asked."""

    def __init__(self, truth: Dict[int, str]) -> None:
        self.truth = truth
        self.requested: List[List[int]] = []

    async def run(self, source: Path, page_numbers: Sequence[int], language: str) -> Dict[int, str]:
        self.requested.append(list(page_numbers))
        return {page: self.truth[page] for page in page_numbers if page in self.truth}


class Worker:
    """Processes queued jobs exactly as ``periodic_document_processing_jobs``
    does, with a processor whose OCR and LLM edges are in-process."""

    def __init__(self, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        from rbac_backend.services import document_service as document_service_module

        self.db = db
        self.ocr = _TruthOcr({})
        self.escalate: set = set()
        monkeypatch.setattr(document_service_module, "create_document_processor", self._processor)

    def _processor(self, config: Any = None) -> Any:
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig
        from rbac_backend.services.database_service import DatabaseService
        from rbac_backend.services.document_processor import DocumentProcessor
        from rbac_backend.services.extraction.source_kind import SourceKindRouter
        from rbac_backend.services.text_processing_service import TextProcessingService
        from rbac_backend.tests.retry_harness import (
            _async_none,
            _OpenAI,
            _PagewiseOcr,
            _RecordingGate,
        )

        processor = DocumentProcessor.__new__(DocumentProcessor)
        processor_ref: Dict[str, Any] = {}
        processor.config = DocumentProcessingConfig()
        processor.config.contract_ocr_min_text_chars_per_page = MIN_TEXT_CHARS
        processor.config.contract_ocr_batch_size = 4
        processor.ocr_service = _PagewiseOcr(runner=self.ocr, min_text_chars=MIN_TEXT_CHARS, batch_size=4)
        processor.openai_service = _OpenAI(processor_ref)
        processor.text_service = TextProcessingService(processor.config)
        database_service = DatabaseService(processor.config)
        database_service._db = self.db
        processor.database_service = database_service
        processor.file_service = SimpleNamespace(save_summary=_async_none)
        processor.pydantic_ai_service = SimpleNamespace(is_enabled=False)
        processor.quality_gate = _RecordingGate([], self.escalate)
        processor.fallback_ladder = None
        processor.fallback_max_pages_per_document = 0
        processor.source_kind_router = SourceKindRouter
        processor.image_ocr_runner = object()
        processor._image_extractor = None
        processor._text_extractor = None
        processor.resolve_fallback_ladder = lambda kind, db=None: None
        processor_ref["processor"] = processor
        return processor

    async def run(self, job_id: str, *, truth: Optional[Dict[int, str]] = None) -> bool:
        from rbac_backend.services.document_service import DocumentService

        self.ocr.truth = dict(truth or {})
        return await DocumentService(self.db).process_document_job(job_id)


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #

SELECTION = {"foreign_org_admin": PROJ_B1, "other_project_admin": PROJ_A2}


def _app() -> FastAPI:
    from rbac_backend.routers.document_extraction_review import router as review_router
    from rbac_backend.routers.documents import router as documents_router
    from rbac_backend.routers.dashboard import router as dashboard_router
    from rbac_backend.routers.reports import router as reports_router
    from rbac_backend.routers.search import router as search_router

    app = FastAPI()
    app.include_router(dashboard_router, prefix="/api")
    app.include_router(reports_router, prefix="/api")
    app.include_router(review_router, prefix="/api")
    app.include_router(documents_router, prefix="/api")
    app.include_router(search_router, prefix="/api")
    return app


class Env:
    def __init__(self, db: Any, client: httpx.AsyncClient, tmp: Path, seams: Any, worker: Worker,
                 graph: "FakeGraph") -> None:
        self.db = db
        self.client = client
        self.tmp = tmp
        self.seams = seams
        self.worker = worker
        self.graph = graph

    def headers(self, persona: str, project: Optional[str] = None) -> Dict[str, str]:
        selected = project if project is not None else SELECTION.get(persona, PROJ_A1)
        return {"Authorization": f"Bearer {_token(persona)}", "X-Proj-Id": selected}

    def pdf(self, kind: str, name: Optional[str] = None) -> bytes:
        build = {"native": _native_pdf, "scanned": _scanned_pdf, "mixed": _mixed_pdf}[kind]
        return build(self.tmp / f"{name or kind}-{uuid.uuid4().hex[:6]}.pdf").read_bytes()

    async def upload(
        self,
        content: bytes,
        *,
        parent: str = DOC_IN_ID,
        persona: str = "project_admin",
        project: Optional[str] = None,
        filename: str = "annexure.pdf",
        relationship: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        form = dict(data or {})
        if relationship is not None:
            form["relationship_type"] = relationship
        return await self.client.post(
            f"/api/documents/{parent}/enclosures",
            files={"file": (filename, content, "application/pdf")},
            data=form,
            headers=self.headers(persona, project),
        )

    async def child(self, child_id: str) -> Dict[str, Any]:
        return await self.db.documents.find_one({"_id": ObjectId(child_id)})

    async def children(self, parent: str = DOC_IN_ID) -> List[Dict[str, Any]]:
        return await self.db.documents.find({"parent_document_id": parent}).to_list(length=None)

    async def count(self, collection: str, query: Dict[str, Any]) -> int:
        return await self.db[collection].count_documents(query)

    async def audits(self, resource_id: str, event_type: str) -> List[Dict[str, Any]]:
        return await self.db.document_audit_events.find(
            {"resource_id": resource_id, "event_type": event_type}
        ).to_list(length=None)


def _install_qdrant_delete_seam(monkeypatch: pytest.MonkeyPatch, seams: Any) -> None:
    """Point the delete cascade's Qdrant step at the same in-process store the
    publication seam writes, so "deleted" and "reindexed" are observable."""
    from rbac_backend.services.document_service import DocumentService

    async def delete(self: Any, raw_doc: Dict[str, Any], doc_id: str) -> bool:
        if seams.qdrant.fail:
            raise RuntimeError("qdrant unavailable (401)")
        seams.qdrant.points.pop(doc_id, None)
        return True

    monkeypatch.setattr(DocumentService, "_delete_qdrant_vectors", delete)


class FakeGraph:
    """In-process FalkorDB and Graphiti: the Letter graph as the writers see it.

    Installed for every test, so no test ever reaches a developer's local
    Falkor (``backend/.env`` enables one on :6380; CI disables it). Letter
    nodes merge on ``normCode`` alone, exactly like the real service, so a
    child that published itself as a Letter would land on - and overwrite -
    whatever real letter shares the code.
    """

    def __init__(self) -> None:
        self.letters: Dict[str, Dict[str, Any]] = {}
        self.upserts: List[Dict[str, Any]] = []
        self.deletes: List[str] = []
        self.nodes: List[Dict[str, Any]] = []

    def seed_letter(self, code: str, owner: str, **props: Any) -> None:
        from rbac_backend.services.falkor_graph_service import normalize_letter_code

        norm = normalize_letter_code(code)
        self.letters[norm] = {"code": code, "normCode": norm, "owners": {owner}, **props}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from rbac_backend.graph.graph_adapter import GraphAdapter, GraphConfig
        from rbac_backend.services.falkor_graph_service import FalkorGraphService

        graph = self

        def upsert(service: Any, letter: Dict[str, Any], references: Any, cleanup: bool = True,
                   owner_document_id: Optional[str] = None, **kwargs: Any) -> None:
            graph.upserts.append({"letter": dict(letter), "owner": owner_document_id})
            node = graph.letters.setdefault(letter["normCode"], {"owners": set()})
            node.update({k: v for k, v in letter.items()})
            node["owners"].add(owner_document_id)

        def delete(service: Any, code: str) -> bool:
            from rbac_backend.services.falkor_graph_service import normalize_letter_code

            graph.deletes.append(normalize_letter_code(code))
            graph.letters.pop(normalize_letter_code(code), None)
            return True

        def upsert_node(adapter: Any, node: Any) -> Dict[str, Any]:
            graph.nodes.append({"node_id": node.node_id, "labels": list(node.labels)})
            return {}

        monkeypatch.setattr(FalkorGraphService, "enabled", property(lambda service: True))
        monkeypatch.setattr(FalkorGraphService, "upsert_letter_with_refs", upsert)
        monkeypatch.setattr(FalkorGraphService, "delete_letter", delete)
        monkeypatch.setattr(GraphConfig, "from_settings",
                            classmethod(lambda cls: cls(base_url="http://graph.invalid", api_key=None, enabled=True)))
        monkeypatch.setattr(GraphAdapter, "upsert_node", upsert_node)
        monkeypatch.setattr(GraphAdapter, "upsert_edge", lambda adapter, edge: {})

    def owned_by(self, document_id: str) -> List[str]:
        return [u["letter"]["normCode"] for u in self.upserts if u["owner"] == document_id]

    def nodes_for(self, document_id: str) -> List[Dict[str, Any]]:
        return [n for n in self.nodes if document_id in str(n["node_id"])]


@asynccontextmanager
async def _env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, unified_orgs: str = ORG_A, real_graph: bool = False
) -> AsyncIterator[Env]:
    from rbac_backend.core import database as database_module
    from rbac_backend.core.config import settings
    from rbac_backend.services.child_document_service import ensure_child_document_indexes
    from rbac_backend.services.extraction_review_service import ensure_review_indexes

    uri = _uri()
    assert not getattr(settings, "APP_REDIS_URL", None) and not getattr(settings, "RUNTIME_STATE_REDIS_URL", None), (
        "unset APP_REDIS_URL / RUNTIME_STATE_REDIS_URL: a local Redis turns cache fail-closed into false reds"
    )
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await mongo.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        mongo.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"enclosure_child_{uuid.uuid4().hex[:12]}"
    db = mongo[name]
    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    uploads = tmp_path / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "SECURE_UPLOADS_DIR", str(uploads))
    monkeypatch.setattr(settings, "ANTIVIRUS_ENABLED", False)
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_CANARY_ORG_IDS", unified_orgs)
    from rbac_backend.graph.graph_ingestion_service import GraphIngestionService

    real_ingest = GraphIngestionService.ingest_document
    real_sync = GraphIngestionService.sync_document_to_falkor
    seams = Seams(monkeypatch)
    graph = FakeGraph()
    graph.install(monkeypatch)
    if real_graph:
        # The production Letter-graph writers, end to end, into the fake.
        monkeypatch.setattr(GraphIngestionService, "ingest_document", real_ingest)
        monkeypatch.setattr(GraphIngestionService, "sync_document_to_falkor", real_sync)
    _install_qdrant_delete_seam(monkeypatch, seams)
    worker = Worker(db, monkeypatch)
    try:
        await _seed(db)
        await ensure_child_document_indexes(db)
        await db.document_extraction_heads.create_index("document_id", unique=True)
        await ensure_review_indexes(db)
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(db, client, tmp_path, seams, worker, graph)
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _ok(response: httpx.Response) -> Dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()


async def _nothing_written(env: Env) -> None:
    assert await env.count("documents", {"parent_document_id": {"$type": "string"}}) == 0
    assert await env.count("file_objects", {"document_type": "enclosure"}) == 0
    parent = await env.db.documents.find_one({"_id": DOC_IN})
    assert not parent.get("enclosures")


# --------------------------------------------------------------------------- #
# 1-4. The mandatory live-index hard gate
# --------------------------------------------------------------------------- #


def test_01_missing_identity_unique_index_refuses_child_creation(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            await env.db.documents.drop_index("uq_documents_child_identity")
            response = await env.upload(env.pdf("native"))
            assert response.status_code == 503, response.text
            await _nothing_written(env)

    _run(scenario())


def test_02_non_unique_identity_index_refuses(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            await env.db.documents.drop_index("uq_documents_child_identity")
            await env.db.documents.create_index(
                [("parent_document_id", 1), ("relationship_type", 1), ("sha256", 1)],
                name="uq_documents_child_identity",
                partialFilterExpression={"parent_document_id": {"$type": "string"}},
            )
            response = await env.upload(env.pdf("native"))
            assert response.status_code == 503, response.text
            await _nothing_written(env)

    _run(scenario())


def test_03_missing_parent_lookup_index_refuses(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            await env.db.documents.drop_index("ix_documents_children_by_parent")
            response = await env.upload(env.pdf("native"))
            assert response.status_code == 503, response.text
            await _nothing_written(env)

    _run(scenario())


def test_04_correct_indexes_allow_and_the_live_shape_matches_the_gate(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        from rbac_backend.services.child_document_service import child_index_problems

        async with _env(monkeypatch, tmp_path) as env:
            # The gate's comparison holds against what a real server reports.
            assert child_index_problems(await env.db.documents.index_information()) == []
            body = _ok(await env.upload(env.pdf("native")))
            assert body["child_document_id"] and body["child_created"] is True

    _run(scenario())


# --------------------------------------------------------------------------- #
# 5-9. Enclosure -> child identity and idempotency
# --------------------------------------------------------------------------- #


def test_05_an_enclosure_upload_creates_one_linked_child(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            assert [str(c["_id"]) for c in await env.children()] == [child_id]
            assert body["enclosure"]["child_document_id"] == child_id
            child = await env.child(child_id)
            assert child["relationship_type"] == "enclosure"
            assert child["parent_document_id"] == DOC_IN_ID
            parent = await env.db.documents.find_one({"_id": DOC_IN})
            [entry] = parent["enclosures"]
            assert entry["child_document_id"] == child_id
            assert entry["file_object_id"] == child["file_object_id"]
            file_object = await env.db.file_objects.find_one({"_id": child["file_object_id"]})
            assert child_id in file_object["document_ids"]
            assert file_object["sha256"] == child["sha256"]

    _run(scenario())


def test_06_a_retry_returns_the_same_child_and_duplicates_no_layer(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            content = env.pdf("native")
            first = _ok(await env.upload(content))
            second = _ok(await env.upload(content))
            child_id = first["child_document_id"]
            assert second["child_document_id"] == child_id and second["child_created"] is False
            assert second["enclosure"]["id"] == first["enclosure"]["id"]
            assert second["version_id"] == first["version_id"] == child_id
            assert second["processing_job_id"] == first["processing_job_id"] == child_id
            assert await env.count("documents", {"parent_document_id": DOC_IN_ID}) == 1
            assert await env.count("file_objects", {"document_type": "enclosure"}) == 1
            assert await env.count("document_versions", {"document_id": child_id}) == 1
            assert await env.count("document_processing_jobs", {"document_id": child_id}) == 1
            parent = await env.db.documents.find_one({"_id": DOC_IN})
            assert len(parent["enclosures"]) == 1
            assert len(await env.audits(child_id, "document.child_reused")) == 1

    _run(scenario())


def test_06b_a_retry_after_the_job_has_finished_queues_nothing_new(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            content = env.pdf("native")
            first = _ok(await env.upload(content))
            assert await env.worker.run(first["processing_job_id"])
            again = _ok(await env.upload(content))
            assert again["processing_job_id"] == first["processing_job_id"]
            assert await env.count("document_processing_jobs", {"document_id": first["child_document_id"]}) == 1

    _run(scenario())


def test_07_the_same_file_under_another_parent_is_a_separate_child(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            content = env.pdf("native")
            a = _ok(await env.upload(content, parent=DOC_IN_ID))
            b = _ok(await env.upload(content, parent=DOC_OUT_ID))
            assert a["child_document_id"] != b["child_document_id"]
            child_a, child_b = await env.child(a["child_document_id"]), await env.child(b["child_document_id"])
            # One FileObject, reused, never copied; it knows both owners.
            assert child_a["file_object_id"] == child_b["file_object_id"]
            assert await env.count("file_objects", {"document_type": "enclosure"}) == 1
            file_object = await env.db.file_objects.find_one({"_id": child_a["file_object_id"]})
            assert {a["child_document_id"], b["child_document_id"]} <= set(file_object["document_ids"])

    _run(scenario())


def test_08_concurrent_identical_uploads_make_one_child_one_version_one_job(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        from rbac_backend.core.config import settings

        # The race under test is child identity, not the upload limiter: hold
        # the per-user slot count above the 4 concurrent requests explicitly.
        # The default is 3, so inheriting it - as CI does - answers the fourth
        # request 429 before it reaches child creation.
        monkeypatch.setattr(settings, "UPLOAD_MAX_CONCURRENT_PER_USER", 8)
        async with _env(monkeypatch, tmp_path) as env:
            content = env.pdf("native")
            # Warm the FileObject so the race is on the child layers.
            first = _ok(await env.upload(content))
            child_id = first["child_document_id"]
            await env.db.documents.delete_one({"_id": ObjectId(child_id)})
            await env.db.document_versions.delete_many({"document_id": child_id})
            await env.db.document_processing_jobs.delete_many({"document_id": child_id})
            await env.db.documents.update_one({"_id": DOC_IN}, {"$set": {"enclosures": []}})
            responses = await asyncio.gather(*(env.upload(content) for _ in range(4)))
            bodies = [_ok(r) for r in responses]
            assert len({b["child_document_id"] for b in bodies}) == 1
            winner = bodies[0]["child_document_id"]
            assert await env.count("documents", {"parent_document_id": DOC_IN_ID}) == 1
            assert await env.count("document_versions", {"document_id": winner}) == 1
            assert await env.count("document_versions", {"document_id": winner, "is_current": True}) == 1
            assert await env.count("document_processing_jobs", {"document_id": winner}) == 1

    _run(scenario())


def test_a_failed_job_step_is_visible_and_a_retry_repairs_it(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        from rbac_backend.services.document_service import DocumentService

        async with _env(monkeypatch, tmp_path) as env:
            real_queue = DocumentService.queue_document_processing
            calls = {"n": 0}

            async def flaky(self: Any, *args: Any, **kwargs: Any) -> str:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("queue unavailable")
                return await real_queue(self, *args, **kwargs)

            monkeypatch.setattr(DocumentService, "queue_document_processing", flaky)
            content = env.pdf("native")
            failed = await env.upload(content)
            assert failed.status_code == 500
            [child] = await env.children()
            assert child["processing_status"] == "failed"
            assert "child intake failed" in child["processing_error"]["message"]
            assert await env.count("document_processing_jobs", {"document_id": str(child["_id"])}) == 0
            repaired = _ok(await env.upload(content))
            assert repaired["child_document_id"] == str(child["_id"])
            assert repaired["processing_job_id"] == str(child["_id"])
            assert (await env.child(repaired["child_document_id"]))["processing_status"] == "queued"
            assert await env.count("document_versions", {"document_id": str(child["_id"])}) == 1

    _run(scenario())


def test_09_the_same_file_under_another_relationship_is_a_separate_child(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            content = env.pdf("native")
            enclosure = _ok(await env.upload(content, relationship="enclosure"))
            annexure = _ok(await env.upload(content, relationship="annexure"))
            assert enclosure["child_document_id"] != annexure["child_document_id"]
            assert annexure["relationship_type"] == "annexure"
            assert await env.count("documents", {"parent_document_id": DOC_IN_ID}) == 2
            refused = await env.upload(content, relationship="exhibit")
            assert refused.status_code == 422

    _run(scenario())


# --------------------------------------------------------------------------- #
# 10-12. Scope inheritance and RBAC
# --------------------------------------------------------------------------- #


def test_10_the_child_takes_its_scope_from_the_parent_not_the_request(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(
                env.pdf("native"),
                data={"organization_id": "org-b", "project_id": PROJ_B1,
                      "source_page_start": "3", "source_page_end": "5"},
            ))
            child = await env.child(body["child_document_id"])
            assert (child["organization_id"], child["project_id"]) == (ORG_A, PROJ_A1)
            assert child["source_page_range"] == {"start": 3, "end": 5}
            job = await env.db.document_processing_jobs.find_one({"_id": body["processing_job_id"]})
            assert (job["organization_id"], job["project_id"]) == (ORG_A, PROJ_A1)
            half = await env.upload(env.pdf("native"), data={"source_page_start": "3"})
            assert half.status_code == 422

    _run(scenario())


@pytest.mark.parametrize(
    "persona,project,expected",
    [
        ("project_admin", PROJ_A1, 200),  # same project
        ("foreign_org_admin", PROJ_B1, 403),  # foreign organisation
        ("other_project_admin", PROJ_A2, 403),  # foreign project
        ("project_admin", PROJ_A2, 403),  # tampered navbar selection
    ],
)
def test_11_12_enclosure_upload_rbac(monkeypatch, tmp_path, persona, project, expected) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            response = await env.upload(env.pdf("native"), persona=persona, project=project)
            assert response.status_code == expected, response.text
            if expected != 200:
                await _nothing_written(env)

    _run(scenario())


def test_child_routes_hold_scope_and_parentage(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            listed = await env.client.get(f"/api/documents/{DOC_IN_ID}/children", headers=env.headers("project_admin"))
            assert [_rid(row) for row in _ok_list(listed)] == [child_id]
            for persona, project in (("foreign_org_admin", PROJ_B1), ("other_project_admin", PROJ_A2),
                                     ("project_admin", PROJ_A2)):
                denied = await env.client.get(f"/api/documents/{DOC_IN_ID}/children", headers=env.headers(persona, project))
                assert denied.status_code == 403, (persona, denied.text)
                direct = await env.client.get(f"/api/documents/{child_id}", headers=env.headers(persona, project))
                assert direct.status_code == 403, (persona, direct.text)
            # Direct child retrieval works for an authorised actor.
            assert _rid(_ok(await env.client.get(f"/api/documents/{child_id}", headers=env.headers("project_admin")))) == child_id
            # A child named under a parent it does not belong to is not found.
            mismatched = await env.client.post(
                f"/api/documents/{DOC_OUT_ID}/children/{child_id}/restore", headers=env.headers("project_admin")
            )
            assert mismatched.status_code == 404

    _run(scenario())


def _rid(row: Dict[str, Any]) -> str:
    return str(row.get("id") or row.get("_id"))


def _ok_list(response: httpx.Response) -> List[Dict[str, Any]]:
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 13-16. Version, job and routing
# --------------------------------------------------------------------------- #


def test_13_the_child_gets_an_ordinary_document_version(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            content = env.pdf("native")
            body = _ok(await env.upload(content, filename="Annexure A.pdf",
                                        data={"source_page_start": "2", "source_page_end": "4"}))
            child = await env.child(body["child_document_id"])
            [version] = await env.db.document_versions.find({"document_id": body["child_document_id"]}).to_list(None)
            assert child["current_version_id"] == version["_id"] == body["version_id"]
            assert version["file_object_id"] == child["file_object_id"]
            assert version["version_number"] == 1 and version["is_current"] is True
            assert version["reason"] == "child_intake"
            import hashlib

            snapshot = version["metadata_snapshot"]
            # The stored name is the upload's sanitised filename, as for any upload.
            assert snapshot["original_filename"] == child["original_filename"] == "Annexure_A.pdf"
            assert snapshot["mime_type"] == "application/pdf"
            assert snapshot["size"] == len(content)
            assert snapshot["sha256"] == hashlib.sha256(content).hexdigest()
            assert snapshot["parent_document_id"] == DOC_IN_ID
            assert snapshot["relationship_type"] == "enclosure"
            assert snapshot["source_page_range"] == {"start": 2, "end": 4}
            assert snapshot["enclosure_entry_id"] == body["enclosure"]["id"]

    _run(scenario())


def test_14_one_ordinary_processing_job_is_created(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            [job] = await env.db.document_processing_jobs.find({"document_id": body["child_document_id"]}).to_list(None)
            assert job["_id"] == body["processing_job_id"]
            assert job["status"] == "queued" and job["source_mime"] == "application/pdf"
            assert job["file_path"] and Path(job["file_path"]).exists()
            child = await env.child(body["child_document_id"])
            assert child["processing_status"] == "queued" and child["processing_job_id"] == job["_id"]
            # The parent is untouched by the child's processing.
            parent = await env.db.documents.find_one({"_id": DOC_IN})
            assert parent["processing_status"] == "metadata_extracted"

    _run(scenario())


def test_15_a_unified_organisation_routes_the_child_to_unified_v1(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        from rbac_backend.services.pipeline_routing import claim_filter_for

        async with _env(monkeypatch, tmp_path, unified_orgs=ORG_A) as env:
            body = _ok(await env.upload(env.pdf("native")))
            assert body["pipeline_version"] == "unified_v1"
            job_id = body["processing_job_id"]
            # Only a unified-capable worker can claim it.
            assert await env.count("document_processing_jobs", {"_id": job_id, **claim_filter_for({"legacy_v0"})}) == 0
            assert await env.count("document_processing_jobs", {"_id": job_id, **claim_filter_for({"unified_v1"})}) == 1

    _run(scenario())


def test_16_a_legacy_organisation_routes_the_child_to_legacy_v0(monkeypatch, tmp_path) -> None:
    """Rollout limitation, recorded: a scanned enclosure in a legacy_v0 org
    may get no OCR yet. Routing is not bypassed to work around it."""

    async def scenario() -> None:
        from rbac_backend.services.pipeline_routing import claim_filter_for

        async with _env(monkeypatch, tmp_path, unified_orgs="") as env:
            body = _ok(await env.upload(env.pdf("scanned")))
            assert body["pipeline_version"] == "legacy_v0"
            job_id = body["processing_job_id"]
            assert await env.count("document_processing_jobs", {"_id": job_id, **claim_filter_for({"unified_v1"})}) == 0
            assert await env.count("document_processing_jobs", {"_id": job_id, **claim_filter_for({"legacy_v0"})}) == 1

    _run(scenario())


# --------------------------------------------------------------------------- #
# 17-18. Unified extraction and the child's own canonical evidence
# --------------------------------------------------------------------------- #


async def _process(env: Env, body: Dict[str, Any], truth: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    completed = await env.worker.run(body["processing_job_id"], truth=truth)
    # A job held for human review reports False: it did not complete.
    assert completed is (not env.worker.escalate)
    return await env.child(body["child_document_id"])


async def _evidence(env: Env, child_id: str) -> tuple:
    pages = await env.db.document_ocr_pages.find({"document_id": child_id}).sort("page_number", 1).to_list(None)
    head = await env.db.document_extraction_heads.find_one({"document_id": child_id})
    return pages, head


def _assert_child_provenance(child_id: str, pages: List[Dict[str, Any]], head: Dict[str, Any], numbers: List[int]) -> None:
    assert head is not None, "no canonical head for the child"
    assert head["canonical_revision"] >= 1 and head.get("canonical_sha256")
    assert [span["page_number"] for span in head["page_map"]] == numbers
    current = [p for p in pages if p.get("extraction_run_id") == head.get("extraction_run_id")] or pages
    for page in current:
        assert page["document_id"] == child_id
        assert page["source_pdf_page_link"] == f"document:{child_id}#page={page['page_number']}"


def test_native_unified_child_keeps_native_pages(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            await _process(env, body)
            pages, head = await _evidence(env, body["child_document_id"])
            _assert_child_provenance(body["child_document_id"], pages, head, [1, 2])
            assert env.worker.ocr.requested == []
            assert {p["source"] for p in pages} == {"text_layer"}

    _run(scenario())


def test_17_a_scanned_unified_child_gets_ocr_and_its_own_canonical_evidence(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("scanned")))
            child = await _process(env, body, truth=_scanned_truth())
            child_id = body["child_document_id"]
            pages, head = await _evidence(env, child_id)
            _assert_child_provenance(child_id, pages, head, [1, 2])
            assert sorted(n for batch in env.worker.ocr.requested for n in batch) == [1, 2]
            assert {p["source"] for p in pages} == {"ocr"}
            assert "GW-12" in " ".join(p.get("raw_text") or "" for p in pages)
            assert child["processing_status"] != "failed"
            # Nothing landed on the parent.
            assert await env.count("document_ocr_pages", {"document_id": DOC_IN_ID}) == 0
            assert await env.count("document_extraction_heads", {"document_id": DOC_IN_ID}) == 0

    _run(scenario())


def test_18_a_mixed_unified_child_produces_a_complete_page_map(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        from rbac_backend.tests.fixtures.mixed_correspondence_pdfs import (
            CLAIM_NATIVE_PAGES,
            CLAIM_OCR_TRUTH,
            CLAIM_SCANNED_PAGES,
        )

        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("mixed")))
            await _process(env, body, truth=CLAIM_OCR_TRUTH)
            child_id = body["child_document_id"]
            pages, head = await _evidence(env, child_id)
            _assert_child_provenance(child_id, pages, head, [1, 2, 3, 4])
            by_number = {p["page_number"]: p for p in pages}
            assert all(by_number[n]["source"] == "ocr" for n in CLAIM_SCANNED_PAGES)
            assert all(by_number[n]["source"] == "text_layer" for n in CLAIM_NATIVE_PAGES)
            assert sorted(n for batch in env.worker.ocr.requested for n in batch) == list(CLAIM_SCANNED_PAGES)

    _run(scenario())


# --------------------------------------------------------------------------- #
# 19-20. Human review, publication and search
# --------------------------------------------------------------------------- #


def test_19_a_review_required_child_is_withheld_and_the_parent_stays_usable(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            env.worker.escalate = {2}
            child = await _process(env, body)
            child_id = body["child_document_id"]
            assert child["processing_status"] == "human_review_required"
            assert child_id not in env.seams.qdrant.points
            assert await env.count("document_vectors", {"document_id": child_id}) == 0
            # The machine verdict is on the page; no human decision exists yet.
            pages, head = await _evidence(env, child_id)
            assert {p["page_number"]: p["quality_verdict"] for p in pages}[2] == "fail"
            assert await env.count("document_page_reviews", {"document_id": child_id}) == 0
            # The parent is untouched and still served.
            parent = _ok(await env.client.get(f"/api/documents/{DOC_IN_ID}", headers=env.headers("project_admin")))
            assert parent["processing_status"] == "metadata_extracted"
            # The child is in the review queue (internal processing), not the library.
            queue = _ok(await env.client.get("/api/document-extraction-reviews", headers=env.headers("project_admin")))
            assert child_id in {row["document_id"] for row in queue["items"]}
            listed = _ok(await env.client.get("/api/documents", headers=env.headers("project_admin")))
            assert child_id not in {_rid(row) for row in listed["documents"]}
            assert len(await env.audits(child_id, "document.child_processing_queued")) == 1
            [review] = await env.audits(child_id, "document.extraction_review_required")
            assert review["metadata"]["quality_review_pages"] == [2]
            assert not await env.audits(DOC_IN_ID, "document.extraction_review_required")

    _run(scenario())


def test_20_an_approved_child_publishes_and_indexes_normally(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            env.worker.escalate = {2}
            await _process(env, body)
            child_id = body["child_document_id"]
            head = await env.db.document_extraction_heads.find_one({"document_id": child_id})
            span = next(s for s in head["page_map"] if s["page_number"] == 2)
            approve = await env.client.post(
                f"/api/documents/{child_id}/extraction-review/pages/2/approve",
                json={"canonical_revision": head["canonical_revision"], "page_checksum": span["content_sha256"],
                      "comment": "Checked against the annexure"},
                headers=env.headers("project_admin"),
            )
            assert approve.status_code == 200, approve.text
            child = await env.child(child_id)
            assert child["processing_status"] != "human_review_required"
            assert env.seams.qdrant.points.get(child_id), "the approved child was not indexed"
            assert await env.audits(child_id, "document.extraction_review_published")
            # Human decision recorded separately; the machine verdict is unchanged.
            [decision] = await env.db.document_page_reviews.find({"document_id": child_id}).to_list(None)
            assert decision["page_number"] == 2
            page2 = await env.db.document_ocr_pages.find_one({"document_id": child_id, "page_number": 2,
                                                              "extraction_run_id": head.get("extraction_run_id")})
            assert (page2 or {}).get("quality_verdict", "fail") == "fail"

    _run(scenario())


def test_21_ordinary_listings_and_search_do_not_expose_children(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native"), filename="Engineer instruction annexure.pdf"))
            child_id = body["child_document_id"]
            await _process(env, body)
            assert env.seams.qdrant.points.get(child_id), "the child must be indexed for this to prove anything"
            headers = env.headers("org_admin")
            # A historical row that carries the field as an explicit null is
            # top-level too (DOC_IN lacks the field entirely).
            await env.db.documents.update_one({"_id": ObjectId(DOC_OUT_ID)}, {"$set": {"parent_document_id": None}})
            library = _ok(await env.client.get("/api/documents", headers=headers))
            ids = {_rid(row) for row in library["documents"]}
            assert DOC_IN_ID in ids and DOC_OUT_ID in ids and child_id not in ids
            assert library["total"] == len(ids)
            searched = _ok(await env.client.get("/api/documents", params={"search": "annexure"}, headers=headers))
            assert child_id not in {_rid(row) for row in searched["documents"]}
            picker = _ok(await env.client.get("/api/document-search", params={"q": "annexure"}, headers=headers))
            assert child_id not in {_rid(row) for row in picker["documents"]}
            # The production wildcard text index (core.database.ensure_indexes).
            await env.db.documents.create_index([("$**", "text")])
            await env.db.documents.update_one({"_id": DOC_IN}, {"$set": {"subject": "Engineer instruction annexure"}})
            advanced = _ok(await env.client.get("/api/search/documents", params={"q": "annexure"}, headers=headers))
            found = {str(r.get("_id") or r.get("id")) for r in advanced.get("documents") or advanced.get("results") or []}
            assert DOC_IN_ID in found, advanced  # positive control: the search does match
            assert child_id not in found

    _run(scenario())


def test_dashboard_vector_search_and_export_do_not_expose_children(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        from rbac_backend.routers import documents as documents_router_module
        from rbac_backend.services.document_service import DocumentService

        async with _env(monkeypatch, tmp_path) as env:
            headers = env.headers("org_admin")
            before = _ok(await env.client.get("/api/dashboard/stats", headers=headers))
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            after = _ok(await env.client.get("/api/dashboard/stats", headers=headers))
            assert after["totalDocuments"] == before["totalDocuments"]
            assert child_id not in str(after)

            # Vector search: the index returns a parent hit and a child hit.
            class _Vectors:
                enabled = True

                def __init__(self, config: Any) -> None:
                    pass

                async def similarity_search(self, **kwargs: Any) -> List[Dict[str, Any]]:
                    return [
                        {"metadata": {"document_id": DOC_IN_ID}, "text": "parent", "score": 0.9},
                        {"metadata": {"document_id": child_id}, "text": "child", "score": 0.8},
                    ]

            monkeypatch.setattr(documents_router_module, "LangChainVectorService", _Vectors)
            monkeypatch.setattr(documents_router_module, "DocumentProcessingConfig",
                                lambda: SimpleNamespace(qdrant_enabled=True))
            searched = await env.client.get("/api/documents/vector-search", params={"query": "annexure"},
                                            headers=headers)
            assert searched.status_code == 200, searched.text
            assert DOC_IN_ID in searched.text  # positive control: the parent hit is served
            assert child_id not in searched.text

            # Export goes through DocumentService.list_documents: record what it served.
            served: List[str] = []
            real_list = DocumentService.list_documents

            async def recording(self: Any, query: Dict[str, Any], pagination: Dict[str, int]) -> Any:
                items, total = await real_list(self, query, pagination)
                served.extend(str(doc.id) for doc in items)
                return items, total

            monkeypatch.setattr(DocumentService, "list_documents", recording)
            exported = await env.client.get("/api/documents/export", headers=headers)
            assert exported.status_code == 200, exported.text[:300]
            assert DOC_IN_ID in served and child_id not in served

    _run(scenario())


@pytest.mark.parametrize(
    "sort_by", [None, "date", "createdAt", "status", "tags", "letterNo", "subject"]
)
def test_library_search_sort_and_total_compose_with_child_hiding(monkeypatch, tmp_path, sort_by) -> None:
    """PR #76 x PR B: scope AND search AND parent_document_id-is-null form one
    filter; the sort orders it and the total counts it before skip/limit. Walk
    every page in both directions: the top-level matches are all there, the
    total counts only them, and no child appears on any page."""

    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            for doc in (DOC_IN, ObjectId(DOC_OUT_ID)):
                await env.db.documents.update_one({"_id": doc}, {"$set": {"subject": "Annexure covering letter"}})
            children = [
                _ok(await env.upload(env.pdf("native"), filename="Annexure A.pdf"))["child_document_id"],
                _ok(await env.upload(env.pdf("native"), parent=DOC_OUT_ID, filename="Annexure B.pdf"))[
                    "child_document_id"
                ],
            ]
            for child_id in children:  # give children every sortable value a parent has
                await env.db.documents.update_one(
                    {"_id": ObjectId(child_id)},
                    {"$set": {"subject": "Annexure covering letter", "status": "active", "tags": []}},
                )
            for order in ("asc", "desc"):
                seen: List[str] = []
                totals = set()
                skip = 0
                while True:
                    params: Dict[str, Any] = {"search": "annexure", "limit": 1, "skip": skip, "sort_order": order}
                    if sort_by:
                        params["sort_by"] = sort_by
                    page = _ok(await env.client.get("/api/documents", params=params, headers=env.headers("org_admin")))
                    totals.add(page["total"])
                    seen.extend(_rid(row) for row in page["documents"])
                    if not page["has_next"]:
                        break
                    skip += 1
                assert sorted(seen) == sorted([DOC_IN_ID, DOC_OUT_ID]), (sort_by, order, seen)
                assert totals == {2}, (sort_by, order, totals)
                assert not set(children) & set(seen)

    _run(scenario())


def _unordered(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _unordered(item) for key, item in value.items()}
    if isinstance(value, list):
        return sorted((_unordered(item) for item in value), key=repr)
    return value


@pytest.mark.parametrize(
    "report_id", ["letter-status", "letter-received", "letter-by-tags", "document-activity"]
)
def test_document_reports_do_not_count_children(monkeypatch, tmp_path, report_id) -> None:
    """A child inherits its parent's uploadType, so without the top-level clause
    every letter register would count an enclosure as a letter."""

    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            headers = env.headers("org_admin")
            request = {"reportId": report_id, "projectId": PROJ_A1}
            before = _ok(await env.client.post("/api/reports/preview", json=request, headers=headers))
            assert before["rows"], "positive control: the report lists the seeded documents"
            body = _ok(await env.upload(env.pdf("native")))
            after = _ok(await env.client.post("/api/reports/preview", json=request, headers=headers))
            assert body["child_document_id"] not in str(after)
            assert len(after["rows"]) == len(before["rows"])
            # Equal-count groups have no defined order, so compare as sets.
            assert _unordered(after["metrics"]) == _unordered(before["metrics"])

    _run(scenario())


def test_review_required_audit_is_additive_for_a_top_level_document(monkeypatch, tmp_path) -> None:
    """The new audit event rides on the ordinary pipeline; the top-level
    document's state transition is exactly what it was."""

    async def scenario() -> None:
        from rbac_backend.models.document import Document
        from rbac_backend.services.document_service import DocumentService

        async with _env(monkeypatch, tmp_path) as env:
            source = _native_pdf(tmp_path / "letter.pdf")
            await env.db.documents.update_one(
                {"_id": DOC_IN}, {"$set": {"filepath_local": str(source), "ocrEnabled": True}}
            )
            row = await env.db.documents.find_one({"_id": DOC_IN})
            job_id = await DocumentService(env.db).queue_document_processing(
                Document(**{**row, "_id": DOC_IN_ID}), str(source)
            )
            assert job_id != DOC_IN_ID  # ordinary callers still get a fresh ObjectId
            env.worker.escalate = {2}
            assert await env.worker.run(job_id) is False
            document = await env.db.documents.find_one({"_id": DOC_IN})
            assert document["processing_status"] == "human_review_required"
            assert document["processing_error"]["quality_review_pages"] == [2]
            job = await env.db.document_processing_jobs.find_one({"_id": job_id})
            assert job["status"] == "human_review_required"
            [audit] = await env.audits(DOC_IN_ID, "document.extraction_review_required")
            assert audit["metadata"] == {"job_id": job_id, "unresolved_pages": [], "quality_review_pages": [2]}

    _run(scenario())


# --------------------------------------------------------------------------- #
# P0: a child document is never a letter
#
# An enclosure may QUOTE a letter number ("Ref: CC/PLAN/0001" is drawn on every
# page of the native fixture). That is evidence extracted from the child, never
# the child's own correspondence identity: no letterNo, no letterNoNormalized,
# no Letter node in FalkorDB or Graphiti, and no merge into the real letter that
# owns the code - Letter nodes merge on normCode alone.
# --------------------------------------------------------------------------- #

QUOTED = "CC/PLAN/0001"
QUOTED_NORM = "cc-plan-0001"


def _assert_not_a_letter(env: Env, child: Dict[str, Any]) -> None:
    child_id = str(child["_id"])
    assert child.get("letterNo") is None, child.get("letterNo")
    assert child.get("letterNoNormalized") is None, child.get("letterNoNormalized")
    assert env.graph.owned_by(child_id) == [], env.graph.upserts
    assert env.graph.nodes_for(child_id) == [], env.graph.nodes


async def _canonical_text(env: Env, child_id: str) -> str:
    rows = await env.db.document_ocr_pages.find({"document_id": child_id}).to_list(None)
    return " ".join(str(r.get("raw_text") or "") for r in rows)


def _quote_pdf(env: Env, text: str) -> bytes:
    from rbac_backend.tests.fixtures.pdf_builders import build_text_pdf

    return build_text_pdf(env.tmp / f"quote-{uuid.uuid4().hex[:6]}.pdf", pages=2, text=text).read_bytes()


def test_p0_a_quoted_ref_never_becomes_the_childs_letter_identity(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path, real_graph=True) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child = await _process(env, body)
            assert child["processing_status"] == "completed"
            _assert_not_a_letter(env, child)
            # The quoted number survives as evidence extracted FROM the child.
            assert QUOTED in await _canonical_text(env, body["child_document_id"])
            # And the child's evidence still publishes: vectors and evidence graph.
            assert env.seams.qdrant.points.get(body["child_document_id"])
            assert body["child_document_id"] in env.seams.evidence_calls
            assert QUOTED_NORM not in env.graph.letters
            # Nothing can find the child by letter number.
            assert await env.count("documents", {"letterNoNormalized": QUOTED_NORM}) == 0

    _run(scenario())


def test_p0_b_a_top_level_letter_with_the_same_text_keeps_its_identity(monkeypatch, tmp_path) -> None:
    """The contrast: the guard is child-specific, ordinary letters are unchanged."""

    async def scenario() -> None:
        from rbac_backend.models.document import Document
        from rbac_backend.services.document_service import DocumentService
        from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import _document

        async with _env(monkeypatch, tmp_path, real_graph=True) as env:
            source = _native_pdf(tmp_path / "letter.pdf")
            letter_id = ObjectId()
            row = _document(letter_id, organization_id=ORG_A, project_id=PROJ_A1, upload_type="incoming",
                            subject="Planning letter", letter_no="", sender="Engineer")
            row.update({"letterNo": None, "filepath_local": str(source), "ocrEnabled": True})
            await env.db.documents.insert_one(row)
            job_id = await DocumentService(env.db).queue_document_processing(
                Document(**{**row, "_id": str(letter_id)}), str(source)
            )
            assert await env.worker.run(job_id)
            letter = await env.db.documents.find_one({"_id": letter_id})
            assert letter["letterNo"] == QUOTED and letter["letterNoNormalized"] == QUOTED_NORM
            assert set(env.graph.owned_by(str(letter_id))) == {QUOTED_NORM}
            assert str(letter_id) in env.graph.letters[QUOTED_NORM]["owners"]

    _run(scenario())


def test_p0_c_parent_and_child_with_the_same_number_stay_distinct(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path, real_graph=True) as env:
            await env.db.documents.update_one(
                {"_id": DOC_IN}, {"$set": {"letterNo": QUOTED, "letterNoNormalized": QUOTED_NORM}}
            )
            env.graph.seed_letter(QUOTED, owner=DOC_IN_ID, subject="Parent letter")
            before = {k: v for k, v in env.graph.letters[QUOTED_NORM].items() if k != "owners"}
            body = _ok(await env.upload(env.pdf("native")))
            child = await _process(env, body)
            _assert_not_a_letter(env, child)
            node = env.graph.letters[QUOTED_NORM]
            assert node["owners"] == {DOC_IN_ID}
            assert {k: v for k, v in node.items() if k != "owners"} == before
            [owner] = await env.db.documents.find({"letterNoNormalized": QUOTED_NORM}).to_list(None)
            assert owner["_id"] == DOC_IN and owner.get("parent_document_id") is None

    _run(scenario())


def test_p0_d_a_child_quoting_another_real_letter_does_not_become_it(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path, real_graph=True) as env:
            await env.db.documents.update_one(
                {"_id": ObjectId(DOC_OUT_ID)}, {"$set": {"letterNo": "ABC/123", "letterNoNormalized": "abc-123"}}
            )
            env.graph.seed_letter("ABC/123", owner=DOC_OUT_ID, subject="Letter A")
            letter_a = dict(env.graph.letters["abc-123"])
            body = _ok(await env.upload(_quote_pdf(env, "Reference is made to ABC/123")))
            child = await _process(env, body)
            _assert_not_a_letter(env, child)
            assert env.graph.letters["abc-123"] == letter_a  # not overwritten, no new owner
            assert "abc-123" not in env.graph.deletes
            assert "ABC/123" in await _canonical_text(env, body["child_document_id"])
            [owner] = await env.db.documents.find({"letterNoNormalized": "abc-123"}).to_list(None)
            assert str(owner["_id"]) == DOC_OUT_ID

    _run(scenario())


def test_p0_e_review_and_approval_never_promote_a_quoted_number(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path, real_graph=True) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            env.worker.escalate = {2}
            child = await _process(env, body)
            assert child["processing_status"] == "human_review_required"
            _assert_not_a_letter(env, child)
            head = await env.db.document_extraction_heads.find_one({"document_id": child_id})
            span = next(s for s in head["page_map"] if s["page_number"] == 2)
            _ok(await env.client.post(
                f"/api/documents/{child_id}/extraction-review/pages/2/approve",
                json={"canonical_revision": head["canonical_revision"], "page_checksum": span["content_sha256"],
                      "comment": "ok"}, headers=env.headers("project_admin")))
            child = await env.child(child_id)
            assert env.seams.qdrant.points.get(child_id), "the approved child must still publish"
            _assert_not_a_letter(env, child)

    _run(scenario())


def test_p0_f_reprocess_clears_a_stale_accidental_identity(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        from rbac_backend.models.document import Document
        from rbac_backend.services.document_service import DocumentService

        async with _env(monkeypatch, tmp_path, real_graph=True) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            await _process(env, body)
            # A row from before this fix carries an accidental letter identity.
            await env.db.documents.update_one(
                {"_id": ObjectId(child_id)}, {"$set": {"letterNo": QUOTED, "letterNoNormalized": QUOTED_NORM}}
            )
            row = await env.child(child_id)
            job_id = await DocumentService(env.db).queue_document_processing(
                Document(**{**row, "_id": child_id}), row["filepath_local"]
            )
            assert job_id != body["processing_job_id"]
            assert await env.worker.run(job_id)
            _assert_not_a_letter(env, await env.child(child_id))
            head = await env.db.document_extraction_heads.find_one({"document_id": child_id})
            assert head["canonical_revision"] >= 2  # a new canonical revision, still no identity

    _run(scenario())


def test_p0_g_restore_and_reprocess_never_give_a_child_an_identity(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path, real_graph=True) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            await _process(env, body)
            service = await _service(env)
            await service.soft_delete_child(await _principal(env), child_id)
            _ok(await env.client.post(f"/api/documents/{DOC_IN_ID}/children/{child_id}/restore",
                                      headers=env.headers("project_admin")))
            child = await env.child(child_id)
            assert await env.worker.run(child["processing_job_id"])
            _assert_not_a_letter(env, await env.child(child_id))

    _run(scenario())


def test_p0_h_a_metadata_edit_cannot_give_a_child_a_letter_number(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            refused = await env.client.put(f"/api/documents/{child_id}", json={"letterNo": QUOTED},
                                           headers=env.headers("project_admin"))
            assert refused.status_code == 422, refused.text
            assert (await env.child(child_id)).get("letterNo") is None
            # Other metadata edits on a child are unaffected.
            edited = await env.client.put(f"/api/documents/{child_id}", json={"subject": "Annexure A"},
                                          headers=env.headers("project_admin"))
            assert edited.status_code == 200, edited.text

    _run(scenario())


def test_p0_i_deleting_a_child_never_deletes_a_letter_node(monkeypatch, tmp_path) -> None:
    """Even a stale child identity must not let the delete cascade DETACH DELETE
    the Letter node of the real letter that owns the code."""

    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path, real_graph=True) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            env.graph.seed_letter(QUOTED, owner="letter-in-another-tenant")
            await env.db.documents.update_one(
                {"_id": ObjectId(child_id)}, {"$set": {"letterNo": QUOTED, "letterNoNormalized": QUOTED_NORM}}
            )
            service = await _service(env)
            principal = await _principal(env)
            await service.soft_delete_child(principal, child_id)
            await service.purge_child(principal, child_id)
            assert QUOTED_NORM not in env.graph.deletes
            assert QUOTED_NORM in env.graph.letters

    _run(scenario())


# --------------------------------------------------------------------------- #
# Purge and restore
# --------------------------------------------------------------------------- #


async def _service(env: Env) -> Any:
    from rbac_backend.services.child_document_service import ChildDocumentService
    from rbac_backend.services.document_service import DocumentService

    cleanups: List[str] = []
    document_service = DocumentService(env.db)
    real_cleanup = document_service.cascade_delete_cleanup

    async def recorded(document_id: str) -> Dict[str, Any]:
        cleanups.append(document_id)
        return await real_cleanup(document_id)

    document_service.cascade_delete_cleanup = recorded  # type: ignore[method-assign]
    service = ChildDocumentService(db=env.db, document_service=document_service)
    service.cleanups = cleanups  # type: ignore[attr-defined]
    return service


async def _principal(env: Env, persona: str = "project_admin") -> Any:
    from rbac_backend.core.security import resolve_stored_principal

    return await resolve_stored_principal(env.db, f"user-{persona}")


def test_purge_removes_every_child_owned_artifact_and_keeps_shared_bytes(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            content = env.pdf("native")
            body = _ok(await env.upload(content))
            sibling = _ok(await env.upload(content, parent=DOC_OUT_ID))
            child_id = body["child_document_id"]
            env.worker.escalate = {2}
            await _process(env, body)
            head = await env.db.document_extraction_heads.find_one({"document_id": child_id})
            span = next(s for s in head["page_map"] if s["page_number"] == 2)
            _ok(await env.client.post(
                f"/api/documents/{child_id}/extraction-review/pages/2/approve",
                json={"canonical_revision": head["canonical_revision"], "page_checksum": span["content_sha256"],
                      "comment": "ok"}, headers=env.headers("project_admin")))
            for collection in ("document_ocr_pages", "document_extraction_heads", "document_page_reviews",
                               "document_vectors", "vector_sync_status", "document_processing_jobs",
                               "document_versions"):
                assert await env.count(collection, {"document_id": child_id}), collection

            service = await _service(env)
            principal = await _principal(env)
            await service.soft_delete_child(principal, child_id)
            result = await service.purge_child(principal, child_id)

            assert result.purged and result.remaining_references > 0 and not result.file_object_unreferenced
            assert await env.db.documents.find_one({"_id": ObjectId(child_id)}) is None
            from rbac_backend.services.child_document_service import CHILD_OWNED_COLLECTIONS

            for collection in CHILD_OWNED_COLLECTIONS:
                assert await env.count(collection, {"document_id": child_id}) == 0, collection
            assert child_id in service.cleanups  # Qdrant/Falkor cascade ran at purge
            assert child_id not in env.seams.qdrant.points
            parent = await env.db.documents.find_one({"_id": DOC_IN})
            assert all(e.get("child_document_id") != child_id for e in parent["enclosures"])
            file_object = await env.db.file_objects.find_one({"_id": (await env.child(sibling["child_document_id"]))["file_object_id"]})
            assert child_id not in file_object["document_ids"]
            assert sibling["child_document_id"] in file_object["document_ids"]
            assert "unreferenced_at" not in file_object
            stored = [loc["path"] for loc in file_object["locations"] if loc.get("provider") == "local"]
            assert stored and all(Path(p).exists() for p in stored), "shared bytes were deleted"
            # The audit trail outlives the purge.
            assert await env.audits(child_id, "document.child_purged")

    _run(scenario())


def test_a_failed_vector_cleanup_keeps_the_child_for_a_retry(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        from rbac_backend.services.child_document_service import ChildDocumentCleanupError

        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            service = await _service(env)
            principal = await _principal(env)
            await service.soft_delete_child(principal, child_id)

            env.seams.qdrant.fail = True  # the real cascade, failing at Qdrant
            with pytest.raises(ChildDocumentCleanupError):
                await service.purge_child(principal, child_id)
            assert await env.child(child_id) is not None
            assert await env.count("document_versions", {"document_id": child_id}) == 1

    _run(scenario())


def test_purge_refuses_while_the_child_is_being_processed(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        from rbac_backend.services.child_document_service import ChildDocumentConflictError

        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            service = await _service(env)
            principal = await _principal(env)
            await service.soft_delete_child(principal, child_id)
            await env.db.document_processing_jobs.update_one({"_id": body["processing_job_id"]},
                                                             {"$set": {"status": "processing"}})
            with pytest.raises(ChildDocumentConflictError):
                await service.purge_child(principal, child_id)
            assert await env.child(child_id) is not None

    _run(scenario())


def test_restore_schedules_reprocessing_and_reindexes_the_child(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            await _process(env, body)
            assert env.seams.qdrant.points.get(child_id)
            service = await _service(env)
            await service.soft_delete_child(await _principal(env), child_id)
            assert child_id not in env.seams.qdrant.points  # the soft delete's cascade
            assert await env.count("document_vectors", {"document_id": child_id}) == 0

            restored = await env.client.post(
                f"/api/documents/{DOC_IN_ID}/children/{child_id}/restore", headers=env.headers("project_admin")
            )
            assert restored.status_code == 200, restored.text
            child = await env.child(child_id)
            assert child["lifecycle_state"] == "active"
            assert child["processing_status"] == "queued"
            job_id = child["processing_job_id"]
            assert job_id != body["processing_job_id"]
            job = await env.db.document_processing_jobs.find_one({"_id": job_id})
            assert job["status"] == "queued" and job["pipeline_version"] == "unified_v1"
            assert await env.audits(child_id, "document.child_restore_reprocess_queued")
            again = await env.client.post(
                f"/api/documents/{DOC_IN_ID}/children/{child_id}/restore", headers=env.headers("project_admin")
            )
            assert again.status_code == 409, again.text  # a live child cannot be restored twice
            assert await env.count("document_processing_jobs", {"document_id": child_id}) == 2
            assert await env.count("document_versions", {"document_id": child_id}) == 1
            assert await env.worker.run(job_id)
            assert env.seams.qdrant.points.get(child_id), "the restored child was not reindexed"

    _run(scenario())


def test_purge_over_http_requires_step_up(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            service = await _service(env)
            await service.soft_delete_child(await _principal(env), child_id)
            refused = await env.client.post(
                f"/api/documents/{DOC_IN_ID}/children/{child_id}/purge", headers=env.headers("project_admin")
            )
            assert refused.status_code in (401, 403), refused.text
            assert await env.child(child_id) is not None

    _run(scenario())


def test_restore_rbac(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native")))
            child_id = body["child_document_id"]
            service = await _service(env)
            await service.soft_delete_child(await _principal(env), child_id)
            for persona, project in (("foreign_org_admin", PROJ_B1), ("other_project_admin", PROJ_A2),
                                     ("project_admin", PROJ_A2)):
                denied = await env.client.post(f"/api/documents/{DOC_IN_ID}/children/{child_id}/restore",
                                               headers=env.headers(persona, project))
                assert denied.status_code == 403, (persona, denied.text)
            assert (await env.child(child_id))["lifecycle_state"] == "deleted"

    _run(scenario())


# --------------------------------------------------------------------------- #
# Audit and performance
# --------------------------------------------------------------------------- #


def test_the_intake_is_audited_with_identifiers_only(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, tmp_path) as env:
            body = _ok(await env.upload(env.pdf("native"), filename="Confidential settlement.pdf"))
            child_id = body["child_document_id"]
            for event in ("document.child_created", "document.child_version_created",
                          "document.child_processing_queued"):
                [row] = await env.audits(child_id, event)
                assert set(row.get("metadata") or {}) <= CHILD_AUDIT_KEYS, (event, row.get("metadata"))
                assert "Confidential" not in str(row)
            assert await env.audits(DOC_IN_ID, "document.enclosure_added")
            # A retry is audited as a reuse, not a second creation.
            assert len(await env.audits(child_id, "document.child_created")) == 1

    _run(scenario())


def test_intake_latency_is_measured(monkeypatch, tmp_path, capsys) -> None:
    """Measured, not asserted against a wall clock: printed for the PR record."""

    async def scenario() -> Dict[str, float]:
        from rbac_backend.services.child_document_service import require_child_document_indexes

        async with _env(monkeypatch, tmp_path) as env:
            gate: List[float] = []
            for _ in range(50):
                start = time.perf_counter()
                await require_child_document_indexes(env.db)
                gate.append((time.perf_counter() - start) * 1000)
            first: List[float] = []
            retry: List[float] = []
            for _ in range(5):
                content = env.pdf("native")
                start = time.perf_counter()
                _ok(await env.upload(content))
                first.append((time.perf_counter() - start) * 1000)
                start = time.perf_counter()
                _ok(await env.upload(content))
                retry.append((time.perf_counter() - start) * 1000)
            # The intake layers alone, on an existing FileObject.
            body = _ok(await env.upload(env.pdf("native")))
            file_object_id = (await env.child(body["child_document_id"]))["file_object_id"]
            service = await _service(env)
            principal = await _principal(env)
            layers: Dict[str, List[float]] = {"child": [], "version": [], "job": []}
            for parent in (DOC_OUT_ID, DOC_IN_ID):
                for relationship in ("annexure", "attachment"):
                    t0 = time.perf_counter()
                    creation = await service.create_child(
                        principal, parent_document_id=parent, relationship_type=relationship,
                        file_object_id=file_object_id,
                    )
                    t1 = time.perf_counter()
                    await service._ensure_intake_version(principal, creation.document)
                    t2 = time.perf_counter()
                    await service._ensure_intake_job(principal, creation.document)
                    t3 = time.perf_counter()
                    layers["child"].append((t1 - t0) * 1000)
                    layers["version"].append((t2 - t1) * 1000)
                    layers["job"].append((t3 - t2) * 1000)
            return {
                "child_create_ms_median": statistics.median(layers["child"]),
                "version_create_ms_median": statistics.median(layers["version"]),
                "job_create_ms_median": statistics.median(layers["job"]),
                "gate_ms_median": statistics.median(gate),
                "upload_create_ms_median": statistics.median(first),
                "upload_retry_ms_median": statistics.median(retry),
            }

    measured = _run(scenario())
    with capsys.disabled():
        print(f"\n[PR B intake latency] {measured}")
    assert measured["gate_ms_median"] < measured["upload_create_ms_median"]
