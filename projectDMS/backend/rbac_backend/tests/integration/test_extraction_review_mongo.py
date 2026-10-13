"""Release 1.1.1: human review of extraction pages, over real HTTP, Mongo and RBAC.

Closes the ``human_review_required`` dead end. Each test seeds genuine page
evidence through ``DocumentPageStore.record_pages`` / ``publish_canonical`` (so
the canonical head verifies exactly as production's does), then drives the
review routes with signed JWTs through the real ``get_current_user``,
``ActiveScope`` and ``PolicyService`` chain - no dependency override.

The vector providers (OpenAI embeddings, Qdrant) are replaced with in-process
fakes at the ``DatabaseService`` seam; everything above that seam - deferred
embedding creation, the vector-sync bookkeeping, the status settlement - is the
production code. Graph publication is counted at ``GraphIngestionService`` /
``EvidenceGraphService`` so "restored exactly once" is observable.

Opt-in: set ``EXTRACTION_REVIEW_MONGODB_URI`` to a disposable replica set.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    DOC_IN_ID,
    ORG_A,
    ORG_B,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    _seed,
    _token,
)

MONGODB_URI_ENV = "EXTRACTION_REVIEW_MONGODB_URI"

pytestmark = pytest.mark.integration

#: Page 5 of the GLM 4299 acceptance letter, abbreviated: the dense side-by-side
#: rework-cost / quantity tables whose figures the quality gate could not check.
#: Synthetic stand-in text - the customer PDF is never committed.
PAGE5_TEXT = (
    "Guide Wall & D-wall Reworks Cost\nTotal 19,292,171\n"
    "Guide Wall Quantity Calculation\nConcrete Filling Works 689,012\n"
    "D-wall panel P-101 12.4 m3\nD-wall panel P-102 11.9 m3"
)
PAGE5_TABLES = [[["Item", "Amount"], ["Rework", "19,292,171"], ["Filling", "689,012"]]]

DOC_R = ObjectId()  # A1, page 5 needs review (the 4299 shape)
DOC_R2 = ObjectId()  # A1, pages 3 and 5 need review
DOC_R_A2 = ObjectId()  # A2, review required
DOC_R_B = ObjectId()  # org B, review required
DOC_R_DUP = ObjectId()  # A1, review required but quarantined duplicate
DOC_R_DEL = ObjectId()  # A1, review required but soft-deleted
DOC_R_UNRESOLVED = ObjectId()  # A1, page 2 OCR failed (nothing to approve)


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Fixtures: genuine page evidence and a review-required document
# --------------------------------------------------------------------------- #


def _page(number: int, text: str, *, review: bool = False, status: str = "ocr_completed",
          tables: Optional[list] = None) -> Any:
    from rbac_backend.services.extraction.models import (
        ExtractedPage, PageClass, PageClassification, PageSource, PageStatus,
    )

    unresolved = status in {"ocr_failed", "unrenderable"}
    return ExtractedPage(
        number=number,
        text="" if unresolved else text,
        source=PageSource.EMPTY if unresolved else PageSource.OCR,
        status=PageStatus(status),
        classification=PageClassification(
            page_class=PageClass.TABLE_HEAVY if tables else PageClass.SCANNED_IMAGE,
            char_count=len(text), image_count=1, image_coverage=0.9,
            table_count=len(tables or []), width=595.0, height=842.0, rotation=0,
        ),
        tables=list(tables or []),
        quality_verdict="fail" if review else "pass",
        quality_checks=(
            [{"name": "numeric_integrity", "verdict": "fail", "detail": "row totals not checkable",
              "candidate_value": None, "evidence_refs": []}]
            if review else [{"name": "numeric_integrity", "verdict": "pass", "detail": None,
                             "candidate_value": None, "evidence_refs": []}]
        ),
        needs_review=review,
        error="ocr engine failed" if unresolved else None,
    )


async def _publish_evidence(db: Any, doc_id: ObjectId, org: str, project: str, run_id: str,
                            pages: List[Any]) -> Dict[str, Any]:
    from rbac_backend.services.extraction.canonical import assemble_canonical_document
    from rbac_backend.services.extraction_adapters.document_page_store import DocumentPageStore

    store = DocumentPageStore(db=db, document_id=str(doc_id), organization_id=org,
                              project_id=project, extraction_run_id=run_id)
    await store.record_pages(pages)
    canonical = assemble_canonical_document(pages)
    manifest = await store.publish_canonical(
        expected=canonical, pipeline_version="unified_v1", source_sha256="a" * 64
    )
    return {"manifest": manifest, "text": canonical.text}


def _glm_pages(review_pages: tuple = (5,)) -> List[Any]:
    texts = {
        1: "GLM-SAM-KNPCC-05-UPMRC-OL-2024-4299\nSubject: Guide wall rework claim",
        2: "Reference clauses 8.4.1 and 17.1 of the Contract",
        3: "Gulermak-SAM JV reserves its right to claim",
        4: "Annexure listing",
        5: PAGE5_TEXT,
        6: "Yours faithfully",
    }
    return [
        _page(n, t, review=n in review_pages, tables=PAGE5_TABLES if n == 5 else None)
        for n, t in texts.items()
    ]


async def _seed_review_document(db: Any, doc_id: ObjectId, *, org: str, project: str,
                                review_pages: tuple = (5,), pages: Optional[List[Any]] = None,
                                **extra: Any) -> Dict[str, Any]:
    from rbac_backend.services.source_text import OCR_TEXT_KIND_SOURCE

    job_id = f"job-{doc_id}"
    pages = pages or _glm_pages(review_pages)
    evidence = await _publish_evidence(db, doc_id, org, project, job_id, pages)
    error_pages = sorted(review_pages)
    await db.documents.insert_one({
        "_id": doc_id,
        "organization_id": org,
        "project_id": project,
        "filename": "GLM-SAM-KNPCC-05-UPMRC-OL-2024-4299.pdf",
        "filetype": "application/pdf",
        "filesize": 2048,
        "date": datetime(2022, 12, 24),
        "subject": "Guide wall rework claim",
        "letterNo": f"GLM/4299/{str(doc_id)[-6:]}",
        "letterNoNormalized": f"GLM4299{str(doc_id)[-6:]}",
        "from": "Gulermak-SAM JV",
        "to": "Typsa-Italferr JV",
        "uploadType": "incoming",
        "status": "active",
        "tags": [], "subTags": [],
        "reference": [],
        "contractual_clauses": ["8.4.1", "17.1"],
        "ocrText": evidence["text"],
        "ocr_text_kind": OCR_TEXT_KIND_SOURCE,
        "source_text_status": "complete",
        "metadata_source": "openai_text_legacy_regex",
        "metadata_quality": {"status": "complete", "degraded": False, "references_authoritative": True,
                             "warnings": [], "field_failures": {}},
        "processing_status": "human_review_required",
        "processing_job_id": job_id,
        "processing_error": {
            "message": f"{len(error_pages)} page(s) need review (failed the extraction quality check)",
            "pages": error_pages, "unresolved_pages": [], "quality_review_pages": error_pages,
            "timestamp": datetime.utcnow(), "terminal": True,
        },
        "lifecycle_state": "active",
        "current_version_id": f"{doc_id}-v1",
        "uploaded_by_ref": ObjectId(),
        "created_at": datetime(2026, 10, 12), "createdAt": datetime(2026, 10, 12),
        "updatedAt": datetime(2026, 10, 12, 18, 17),
        "createdBy": "seed-user",
        **extra,
    })
    await db.document_processing_jobs.insert_one({
        "_id": job_id, "document_id": str(doc_id), "status": "human_review_required",
        "stage": "human_review_required", "attempts": 1, "max_attempts": 3,
        "review_page_numbers": error_pages, "remaining_page_numbers": [],
        "processing_state": "human_review_required", "pipeline_version": "unified_v1",
        "created_at": datetime(2026, 10, 12), "updated_at": datetime(2026, 10, 12),
    })
    await db.vector_sync_status.insert_one({
        "document_id": str(doc_id), "sync_status": "deferred", "mongo_chunks": 0,
        "deferral_reasons": ["human_review_required"],
        "details": "Embeddings withheld: page(s) 5 need human review",
    })
    return evidence["manifest"]


# --------------------------------------------------------------------------- #
# Vector / graph seams
# --------------------------------------------------------------------------- #


class _FakeVectors:
    embedding_model_name = "fake-embedding"

    def __init__(self) -> None:
        self.indexed = 0

    async def index_chunks(self, payloads: Any, persist: bool = False) -> List[Dict[str, Any]]:
        self.indexed += len(payloads)
        return [
            {"embedding_id": f"e{i}", "vector_ref": None, "metadata": dict(p.get("metadata") or {}),
             "text": str(p.get("text", "")), "embedding": [0.1, 0.2, 0.3]}
            for i, p in enumerate(payloads)
        ]

    async def delete_vectors(self, refs: Any) -> None:
        return None


class _FakeQdrant:
    enabled = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.points: Dict[str, int] = {}

    async def replace_document(self, payloads: Any) -> int:
        if self.fail:
            raise RuntimeError("qdrant unavailable (401)")
        if payloads:
            document_id = str((payloads[0].get("metadata") or {}).get("document_id") or payloads[0].get("document_id"))
            self.points[document_id] = len(payloads)
        return len(payloads)


class Seams:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, qdrant_fails: bool = False) -> None:
        from rbac_backend.graph.graph_ingestion_service import GraphIngestionService
        from rbac_backend.services.database_service import DatabaseService
        from rbac_backend.services.evidence_graph_service import EvidenceGraphService

        self.vectors = _FakeVectors()
        self.qdrant = _FakeQdrant(fail=qdrant_fails)
        self.graph_calls: List[str] = []
        self.evidence_calls: List[str] = []
        seams = self

        monkeypatch.setattr(DatabaseService, "_get_vector_service", lambda self: seams.vectors)
        monkeypatch.setattr(DatabaseService, "_get_langchain_vector_service", lambda self: seams.qdrant)

        async def ingest_document(self: Any, document_id: str, document_data: Any, metadata: Any,
                                  metadata_source: Any, upload_type: Any = None) -> None:
            seams.graph_calls.append(document_id)

        async def ingest_document_metadata(self: Any, **kwargs: Any) -> None:
            seams.evidence_calls.append(kwargs["document_id"])

        monkeypatch.setattr(GraphIngestionService, "ingest_document", ingest_document)
        monkeypatch.setattr(GraphIngestionService, "sync_document_to_falkor", lambda self, **kw: None)
        monkeypatch.setattr(EvidenceGraphService, "ingest_document_metadata", ingest_document_metadata)


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #

#: The navbar selection each persona sends.
SELECTION = {
    "foreign_org_admin": PROJ_B1,
    "other_project_admin": PROJ_A2,
}


def _app() -> FastAPI:
    from rbac_backend.routers.document_extraction_review import router as review_router
    from rbac_backend.routers.documents import router as documents_router

    app = FastAPI()
    app.include_router(review_router, prefix="/api")
    app.include_router(documents_router, prefix="/api")
    return app


class Env:
    def __init__(self, db: Any, client: httpx.AsyncClient) -> None:
        self.db = db
        self.client = client

    def headers(self, persona: str, project: Optional[str] = None) -> Dict[str, str]:
        selected = project if project is not None else SELECTION.get(persona, PROJ_A1)
        return {"Authorization": f"Bearer {_token(persona)}", "X-Proj-Id": selected}

    async def queue(self, persona: str, project: Optional[str] = None) -> httpx.Response:
        return await self.client.get("/api/document-extraction-reviews", headers=self.headers(persona, project))

    async def detail(self, persona: str, doc: Any, project: Optional[str] = None) -> httpx.Response:
        return await self.client.get(f"/api/documents/{doc}/extraction-review", headers=self.headers(persona, project))

    async def decide(self, persona: str, doc: Any, page: int, action: str = "approve", *,
                     revision: Optional[int] = None, checksum: Optional[str] = None,
                     comment: Optional[str] = "Checked against the source page",
                     expected: Optional[int] = None, project: Optional[str] = None) -> httpx.Response:
        if revision is None or checksum is None:
            head = await self.db.document_extraction_heads.find_one({"document_id": str(doc)})
            span = next(s for s in head["page_map"] if s["page_number"] == page)
            revision = head["canonical_revision"] if revision is None else revision
            checksum = span["content_sha256"] if checksum is None else checksum
        body: Dict[str, Any] = {"canonical_revision": revision, "page_checksum": checksum, "comment": comment}
        if expected is not None:
            body["expected_decision_version"] = expected
        return await self.client.post(
            f"/api/documents/{doc}/extraction-review/pages/{page}/{action}",
            json=body, headers=self.headers(persona, project),
        )

    async def document(self, doc: Any) -> Dict[str, Any]:
        return await self.db.documents.find_one({"_id": doc})

    async def decisions(self, doc: Any) -> List[Dict[str, Any]]:
        return await self.db.document_page_reviews.find({"document_id": str(doc)}).to_list(length=None)

    async def audits(self, doc: Any, event_type: str) -> List[Dict[str, Any]]:
        return await self.db.document_audit_events.find(
            {"resource_id": str(doc), "event_type": event_type}
        ).to_list(length=None)


@asynccontextmanager
async def _env(monkeypatch: pytest.MonkeyPatch, **seam_options: Any) -> AsyncIterator[tuple]:
    from rbac_backend.core import database as database_module
    from rbac_backend.core.config import settings
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
    name = f"extraction_review_{uuid.uuid4().hex[:12]}"
    db = mongo[name]
    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    seams = Seams(monkeypatch, **seam_options)
    try:
        await _seed(db)
        await db.document_extraction_heads.create_index("document_id", unique=True)
        await ensure_review_indexes(db)
        await _seed_review_document(db, DOC_R, org=ORG_A, project=PROJ_A1)
        await _seed_review_document(db, DOC_R2, org=ORG_A, project=PROJ_A1, review_pages=(3, 5))
        await _seed_review_document(db, DOC_R_A2, org=ORG_A, project=PROJ_A2)
        await _seed_review_document(db, DOC_R_B, org=ORG_B, project=PROJ_B1)
        await _seed_review_document(db, DOC_R_DUP, org=ORG_A, project=PROJ_A1, duplicate_status="duplicate")
        await _seed_review_document(db, DOC_R_DEL, org=ORG_A, project=PROJ_A1, lifecycle_state="deleted")
        unresolved_pages = _glm_pages(review_pages=())
        unresolved_pages[1] = _page(2, "", status="ocr_failed")
        await _seed_review_document(db, DOC_R_UNRESOLVED, org=ORG_A, project=PROJ_A1,
                                    review_pages=(2,), pages=unresolved_pages)
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(db, client), seams
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _ids(response: httpx.Response) -> set:
    assert response.status_code == 200, response.text
    return {row["document_id"] for row in response.json()["items"]}


def _status_code(response: httpx.Response) -> int:
    return response.status_code


# --------------------------------------------------------------------------- #
# 1. Review queue
# --------------------------------------------------------------------------- #


def test_queue_lists_review_required_only_within_the_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            ids = _ids(await env.queue("project_admin"))
            assert str(DOC_R) in ids and str(DOC_R2) in ids
            assert DOC_IN_ID not in ids  # settled document
            assert str(DOC_R_A2) not in ids  # other project
            assert str(DOC_R_B) not in ids  # foreign organisation
            assert str(DOC_R_DUP) not in ids and str(DOC_R_DEL) not in ids
            row = next(r for r in (await env.queue("project_admin")).json()["items"] if r["document_id"] == str(DOC_R))
            assert row["review_pages"] == [5] and row["first_review_page"] == 5
            assert "need review" in row["review_reason"]
            # the other project's admin sees only its own
            assert _ids(await env.queue("other_project_admin")) == {str(DOC_R_A2)}
            assert _ids(await env.queue("foreign_org_admin")) == {str(DOC_R_B)}

    _run(scenario())


# --------------------------------------------------------------------------- #
# 2. Review detail
# --------------------------------------------------------------------------- #


def test_detail_returns_the_flagged_page_and_machine_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            response = await env.detail("project_user", DOC_R)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["review_state"] == "required" and body["reviewable"] is True
            assert body["blocking_pages"] == [5] and body["can_review"] is False
            assert body["canonical_revision"] == 1 and body["extraction_run_id"] == f"job-{DOC_R}"
            (page,) = body["pages"]
            assert page["page_number"] == 5 and page["machine_quality_verdict"] == "fail"
            assert page["machine_needs_review"] is True and page["approvable"] is True
            assert page["machine_failed_checks"][0]["name"] == "numeric_integrity"
            assert "19,292,171" in page["text"] and "689,012" in page["text"]
            assert page["table_count"] == 1 and page["source"] == "ocr"
            assert page["decision"] is None
            # never the full document text by default
            assert "Yours faithfully" not in str(body)
            assert (await env.detail("project_admin", DOC_R)).json()["can_review"] is True

    _run(scenario())


# --------------------------------------------------------------------------- #
# 3-4 + RBAC matrix
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("persona", "project", "expected"),
    [
        ("project_user", None, 403),  # viewer/editor without the review permission
        ("org_user", None, 403),  # organisation-role widening
        ("other_project_admin", PROJ_A2, 403),  # foreign project (selection)
        ("other_project_admin", PROJ_A1, 403),  # project scope widening
        ("foreign_org_admin", PROJ_B1, 403),  # foreign organisation
        ("foreign_org_admin", PROJ_A1, 403),  # foreign organisation widening its selection
        ("superadmin", PROJ_A2, 403),  # super admin obeys the active selection
    ],
)
def test_unauthorised_reviewers_are_refused(monkeypatch: pytest.MonkeyPatch, persona: str,
                                             project: Optional[str], expected: int) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            response = await env.decide(persona, DOC_R, 5, project=project)
            assert response.status_code == expected, response.text
            assert await env.decisions(DOC_R) == []
            assert (await env.document(DOC_R))["processing_status"] == "human_review_required"

    _run(scenario())


@pytest.mark.parametrize("persona", ["project_admin", "org_admin", "superadmin"])
def test_authorised_reviewers_can_approve(monkeypatch: pytest.MonkeyPatch, persona: str) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            response = await env.decide(persona, DOC_R2, 3)
            assert response.status_code == 200, response.text
            assert response.json()["decision"]["action"] == "approved_as_is"
            (row,) = await env.decisions(DOC_R2)
            assert row["reviewed_by"] == f"user-{persona}"

    _run(scenario())


# --------------------------------------------------------------------------- #
# 5-6. Stale protection
# --------------------------------------------------------------------------- #


def test_stale_revision_is_a_conflict_and_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            head = await env.db.document_extraction_heads.find_one({"document_id": str(DOC_R)})
            span = next(s for s in head["page_map"] if s["page_number"] == 5)
            # Reprocessed to revision 2 after the reviewer loaded revision 1.
            await _publish_evidence(env.db, DOC_R, ORG_A, PROJ_A1, f"job-{DOC_R}", _glm_pages())
            response = await env.decide("project_admin", DOC_R, 5, revision=1, checksum=span["content_sha256"])
            assert response.status_code == 409, response.text
            assert await env.decisions(DOC_R) == []

    _run(scenario())


def test_wrong_page_checksum_is_a_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            response = await env.decide("project_admin", DOC_R, 5, revision=1, checksum="0" * 64)
            assert response.status_code == 409, response.text
            assert await env.decisions(DOC_R) == []

    _run(scenario())


# --------------------------------------------------------------------------- #
# 7-9. Partial, final, machine verdict preserved; reject
# --------------------------------------------------------------------------- #


def test_partial_then_final_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, seams):
            first = await env.decide("project_admin", DOC_R2, 3)
            assert first.status_code == 200 and first.json()["resolution"]["resolved"] is False
            assert first.json()["resolution"]["unresolved_pages"] == [5]
            assert (await env.document(DOC_R2))["processing_status"] == "human_review_required"
            assert seams.vectors.indexed == 0

            last = await env.decide("project_admin", DOC_R2, 5)
            assert last.status_code == 200, last.text
            assert last.json()["resolution"]["resolved"] is True
            document = await env.document(DOC_R2)
            assert document["processing_status"] == "completed"  # publication ran after the response
            assert document["extraction_review"]["approved_pages"] == [3, 5]

    _run(scenario())


def test_machine_verdict_is_preserved_after_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            before_row = await env.db.document_ocr_pages.find_one({"document_id": str(DOC_R), "page_number": 5})
            before_head = await env.db.document_extraction_heads.find_one({"document_id": str(DOC_R)})
            assert (await env.decide("project_admin", DOC_R, 5)).status_code == 200
            after_row = await env.db.document_ocr_pages.find_one({"document_id": str(DOC_R), "page_number": 5})
            after_head = await env.db.document_extraction_heads.find_one({"document_id": str(DOC_R)})
            assert after_row == before_row  # page evidence untouched, byte for byte
            for key in ("canonical_revision", "canonical_sha256", "review_pages", "page_map"):
                assert after_head[key] == before_head[key]
            assert after_head["review_resolution"]["approved_pages"] == [5]
            (decision,) = await env.decisions(DOC_R)
            assert decision["machine_verdict"] == "fail" and decision["machine_needs_review"] is True
            assert "text" not in decision and PAGE5_TEXT not in str(decision)
            document = await env.document(DOC_R)
            assert document["extraction_review"]["machine_verdict"]["quality_review_pages"] == [5]
            # the evidence API still reports the machine's verdict
            detail = (await env.detail("project_admin", DOC_R)).json()
            assert detail["review_state"] == "resolved"
            assert detail["pages"][0]["machine_quality_verdict"] == "fail"
            assert detail["pages"][0]["decision"]["action"] == "approved_as_is"

    _run(scenario())


def test_reject_keeps_the_document_blocked_and_needs_a_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, seams):
            assert (await env.decide("project_admin", DOC_R, 5, "reject", comment=None)).status_code == 400
            response = await env.decide("project_admin", DOC_R, 5, "reject", comment="Totals do not match source")
            assert response.status_code == 200, response.text
            assert (await env.document(DOC_R))["processing_status"] == "human_review_required"
            assert seams.vectors.indexed == 0 and seams.graph_calls == []
            (audit,) = await env.audits(DOC_R, "document.extraction_page_rejected")
            assert audit["metadata"]["comment_present"] is True
            assert "Totals" not in str(audit)  # comment text itself is not logged
            # a rejected page can later be approved, superseding the rejection
            approve = await env.decide("project_admin", DOC_R, 5, expected=1)
            assert approve.status_code == 200, approve.text
            assert (await env.document(DOC_R))["processing_status"] == "completed"

    _run(scenario())


# --------------------------------------------------------------------------- #
# 10-12. Publication after resolution
# --------------------------------------------------------------------------- #


def test_approval_creates_embeddings_vectors_and_restores_graph_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, seams):
            assert await env.db.document_vectors.count_documents({"document_id": str(DOC_R)}) == 0
            response = await env.decide("project_admin", DOC_R, 5)
            assert response.status_code == 200, response.text
            document = await env.document(DOC_R)
            assert document["processing_status"] == "completed"
            assert document["processing_error"] is None
            chunks = await env.db.document_vectors.count_documents({"document_id": str(DOC_R)})
            assert chunks >= 1
            sync = await env.db.vector_sync_status.find_one({"document_id": str(DOC_R)})
            assert sync["sync_status"] == "synced"
            assert sync["mongo_chunks"] == chunks == sync["qdrant_chunks"]
            assert "deferral_reasons" not in sync
            assert seams.qdrant.points.get(str(DOC_R)) == chunks
            assert seams.graph_calls == [str(DOC_R)] and seams.evidence_calls == [str(DOC_R)]
            job = await env.db.document_processing_jobs.find_one({"_id": f"job-{DOC_R}"})
            assert job["status"] == "completed"
            status = await env.client.get(f"/api/documents/{DOC_R}/processing-status",
                                          headers=env.headers("project_admin"))
            assert status.status_code == 200 and status.json()["status"] == "completed"
            assert str(DOC_R) not in _ids(await env.queue("project_admin"))
            for event in ("document.extraction_page_approved", "document.extraction_review_resolved",
                          "document.extraction_review_published"):
                (audit,) = await env.audits(DOC_R, event)
                assert PAGE5_TEXT not in str(audit)
            resolved = (await env.audits(DOC_R, "document.extraction_review_resolved"))[0]["metadata"]
            assert resolved["old_status"] == "human_review_required"
            assert resolved["new_status"] == "partially_processed"

    _run(scenario())


def test_vector_failure_after_approval_is_partially_processed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, qdrant_fails=True) as (env, _):
            response = await env.decide("project_admin", DOC_R, 5)
            assert response.status_code == 200, response.text
            document = await env.document(DOC_R)
            assert document["processing_status"] == "partially_processed"
            assert document["processing_error"]["stage"] == "indexing_incomplete"
            assert "vector_sync" in document["processing_error"]["stages"] or "embeddings" in document["processing_error"]["stages"]
            assert document["extraction_review"]["status"] == "resolved"  # review is not undone
            job = await env.db.document_processing_jobs.find_one({"_id": f"job-{DOC_R}"})
            assert job["status"] == "partially_processed" and job["stage"] == "indexing_incomplete"
            # not back in the review queue, and still consumable
            from rbac_backend.services.publication_policy import is_consumable

            assert is_consumable(document)
            assert str(DOC_R) not in _ids(await env.queue("project_admin"))

    _run(scenario())


def test_finalize_retries_publication_after_an_indexing_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, qdrant_fails=True) as (env, seams):
            assert (await env.decide("project_admin", DOC_R, 5)).status_code == 200
            assert (await env.document(DOC_R))["processing_status"] == "partially_processed"
            seams.qdrant.fail = False
            # viewer-only users cannot drive it
            refused = await env.client.post(f"/api/documents/{DOC_R}/extraction-review/finalize",
                                            json={"canonical_revision": 1}, headers=env.headers("project_user"))
            assert refused.status_code == 403
            retry = await env.client.post(f"/api/documents/{DOC_R}/extraction-review/finalize",
                                          json={"canonical_revision": 1}, headers=env.headers("project_admin"))
            assert retry.status_code == 200, retry.text
            assert (await env.document(DOC_R))["processing_status"] == "completed"

    _run(scenario())


# --------------------------------------------------------------------------- #
# 13-14. Idempotency and concurrency
# --------------------------------------------------------------------------- #


def test_repeat_approval_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, seams):
            assert (await env.decide("project_admin", DOC_R, 5)).status_code == 200
            chunks = await env.db.document_vectors.count_documents({"document_id": str(DOC_R)})
            again = await env.decide("project_admin", DOC_R, 5)
            assert again.status_code == 200 and again.json()["idempotent"] is True
            assert len(await env.decisions(DOC_R)) == 1
            assert await env.db.document_vectors.count_documents({"document_id": str(DOC_R)}) == chunks
            assert seams.graph_calls == [str(DOC_R)]
            assert len(await env.audits(DOC_R, "document.extraction_page_approved")) == 1
            # a reject after resolution is refused, not recorded
            late = await env.decide("project_admin", DOC_R, 5, "reject", comment="late")
            assert late.status_code == 409

    _run(scenario())


def test_concurrent_reviewers_only_one_decision_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            responses = await asyncio.gather(
                env.decide("project_admin", DOC_R2, 3, expected=0),
                env.decide("org_admin", DOC_R2, 3, "reject", comment="not legible", expected=0),
            )
            codes = sorted(r.status_code for r in responses)
            assert codes == [200, 409], [r.text for r in responses]
            assert len(await env.decisions(DOC_R2)) == 1
            # A later decision made without seeing the winner is refused too.
            stale = await env.decide("org_admin", DOC_R2, 3, "reject", comment="x", expected=0)
            assert stale.status_code == 409

    _run(scenario())


# --------------------------------------------------------------------------- #
# 15-17. Reprocess invalidation, deleted, quarantined, nothing to approve
# --------------------------------------------------------------------------- #


def test_reprocess_invalidates_an_earlier_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            assert (await env.decide("project_admin", DOC_R2, 3)).status_code == 200
            # Reprocessed: new revision, page 5 text changed and still failing.
            pages = _glm_pages(review_pages=(3, 5))
            pages[4].text = PAGE5_TEXT + "\nD-wall panel P-103 10.1 m3"
            await _publish_evidence(env.db, DOC_R2, ORG_A, PROJ_A1, f"job-{DOC_R2}", pages)
            detail = (await env.detail("project_admin", DOC_R2)).json()
            assert detail["canonical_revision"] == 2
            assert all(page["decision"] is None for page in detail["pages"])  # nothing inherited
            assert detail["unresolved_pages"] == [3, 5]
            assert (await env.decide("project_admin", DOC_R2, 5)).json()["resolution"]["resolved"] is False
            assert (await env.document(DOC_R2))["processing_status"] == "human_review_required"
            head = await env.db.document_extraction_heads.find_one({"document_id": str(DOC_R2)})
            assert "review_resolution" not in head

    _run(scenario())


def test_a_new_revision_clears_a_previous_review_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            assert (await env.decide("project_admin", DOC_R, 5)).status_code == 200
            head = await env.db.document_extraction_heads.find_one({"document_id": str(DOC_R)})
            assert head["review_resolution"]["canonical_revision"] == 1
            await _publish_evidence(env.db, DOC_R, ORG_A, PROJ_A1, f"job-{DOC_R}", _glm_pages())
            head = await env.db.document_extraction_heads.find_one({"document_id": str(DOC_R)})
            assert head["canonical_revision"] == 2 and "review_resolution" not in head

    _run(scenario())


def test_deleted_document_cannot_be_reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            assert (await env.decide("project_admin", DOC_R_DEL, 5)).status_code == 404
            assert (await env.detail("project_admin", DOC_R_DEL)).status_code == 404

    _run(scenario())


def test_quarantined_duplicate_cannot_be_released_by_review(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, seams):
            response = await env.decide("project_admin", DOC_R_DUP, 5)
            assert response.status_code == 409, response.text
            assert await env.decisions(DOC_R_DUP) == []
            assert (await env.document(DOC_R_DUP))["processing_status"] == "human_review_required"
            assert seams.vectors.indexed == 0

    _run(scenario())


def test_unresolved_page_cannot_be_approved_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            detail = (await env.detail("project_admin", DOC_R_UNRESOLVED)).json()
            assert detail["pages"][0]["approvable"] is False
            assert (await env.decide("project_admin", DOC_R_UNRESOLVED, 2)).status_code == 409
            assert await env.decisions(DOC_R_UNRESOLVED) == []
            # rejection is still recorded
            assert (await env.decide("project_admin", DOC_R_UNRESOLVED, 2, "reject",
                                     comment="page unreadable")).status_code == 200

    _run(scenario())


def test_page_not_under_review_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            assert (await env.decide("project_admin", DOC_R, 4)).status_code == 404

    _run(scenario())
