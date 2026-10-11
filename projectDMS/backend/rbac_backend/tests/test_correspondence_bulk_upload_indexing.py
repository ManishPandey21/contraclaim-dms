"""The real bulk upload path, end to end, into Qdrant and back out through retrieval.

Driven, all real:

  DocumentController.bulk_upload_documents   (CSV + file, the POST /documents/bulk-upload body)
    -> the BackgroundTasks job it schedules: _process_bulk_upload
    -> _process_single_file -> DocumentController.create_document
         (spool, MIME validation, stage-1 duplicate precheck, DocumentService.create_document)
    -> DocumentService.queue_document_processing        (the durable job)
    -> DocumentService.process_next_processing_jobs     (the worker claim loop)
    -> process_document_job -> process_document_async
    -> DocumentProcessor (pipeline from the job record: legacy_v0 by default)
    -> DatabaseService -> build_correspondence_chunks -> LangChainVectorService
    -> Qdrant -> RetrievalService.search

Boundaries faked, and only these: Mongo (in-process double), the policy gate
(recorded, allows), file storage (FileObjectService writes to a temp dir), the
audit sink, OCR/LLM extraction, and the embedding model. The row sets
``ocr_enabled=true``, the template default and the indexing path. (A row
cannot currently turn it off: pandas reads ``false`` as ``False`` and
``_validate_csv_row``'s ``or 'true'`` fallback replaces it - a separate bulk
finding, not exercised here.)
"""

from __future__ import annotations

import shutil
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from bson import ObjectId
from fastapi import BackgroundTasks, UploadFile

from rbac_backend.retrieval.correspondence_payload import (
    ADVISORY_FIELDS,
    CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
    PAYLOAD_SCHEMA_VERSION_FIELD,
)
from rbac_backend.tests.correspondence_vector_harness import (
    QDRANT_BACKENDS,
    Database,
    QdrantHarness,
    build_processor,
    letter_metadata,
    run,
    scoped_search,
    user_for,
)
from rbac_backend.tests.fixtures.pdf_builders import build_text_pdf

ORG_A, PROJECT_A = str(ObjectId()), str(ObjectId())
ORG_B, PROJECT_B = str(ObjectId()), str(ObjectId())
BODY = (
    "Notice of delay to the viaduct pier foundations caused by late access to "
    "the railway corridor. The contractor requests an extension of time. "
) * 4
QUERY = "viaduct pier foundations delay railway corridor"


@pytest.fixture(params=QDRANT_BACKENDS)
def harness(request, monkeypatch):
    built = QdrantHarness(request.param, monkeypatch)
    yield built
    built.drop()


class _RecordingPolicy:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def authorize(self, user: Any, permission: Any, **kwargs: Any) -> None:
        self.calls.append({"permission": str(permission), **kwargs})


class _LocalStorage:
    """FileObjectService at the storage boundary: a temp directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def resolve_storage_context(self, organization_id: str, project_id: Any) -> Dict[str, Any]:
        return {"organization": organization_id, "project": project_id, "providers": []}

    async def store_path(self, *, source_path: Path, sha256: str, original_filename: str, storage_key: str, **_: Any):
        target = self.root / storage_key.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        return {
            "file_object_id": str(ObjectId()),
            "sha256": sha256,
            "storage_key": storage_key,
            "filepath_local": str(target),
            "filepath_s3": None,
            "storage_locations": [{"provider": "local", "path": str(target), "status": "ok"}],
            "deduped": False,
        }

    async def attach_document_version(self, **_: Any) -> str:
        return str(ObjectId())


class _Audit:
    async def emit(self, **_: Any) -> None:
        return None


def _controller(db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from rbac_backend.routers import documents
    from rbac_backend.services.bulk_upload_service import BulkUploadService
    from rbac_backend.services.document_service import DocumentService

    async def _get_database():
        return db

    monkeypatch.setattr(documents, "get_database", _get_database)
    bulk = BulkUploadService()
    bulk.db = db
    controller = documents.DocumentController(
        document_service=DocumentService(db),
        file_service=SimpleNamespace(),
        export_service=SimpleNamespace(),
        auth_service=SimpleNamespace(),
        bulk_upload_service=bulk,
    )
    policy = _RecordingPolicy()
    controller.policy_service = policy  # type: ignore[assignment]
    controller.file_object_service = _LocalStorage(tmp_path / "storage")  # type: ignore[assignment]
    controller.audit_service = _Audit()  # type: ignore[assignment]

    async def _no_notification(**_: Any) -> None:
        return None

    monkeypatch.setattr(controller, "_emit_bulk_upload_notification", _no_notification)
    return controller, policy


def _bulk_upload(controller, tmp_path: Path, *, org: str, project: str, letter_no: str, user) -> str:
    pdf = build_text_pdf(tmp_path / f"{letter_no.replace('/', '-')}.pdf", text=BODY)
    csv = (
        "filename,upload_type,letter_no,date,ocr_enabled,subject\n"
        f"{pdf.name},incoming,{letter_no},2026-08-01,true,Delay to viaduct pier foundations\n"
    ).encode()
    background = BackgroundTasks()
    response = run(
        controller.bulk_upload_documents(
            background,
            UploadFile(filename="rows.csv", file=BytesIO(csv)),
            [UploadFile(filename=pdf.name, file=BytesIO(pdf.read_bytes()))],
            org,
            project,
            user,
        )
    )
    assert response.total_files == 1
    run(background())  # the scheduled _process_bulk_upload
    return response.job_id


def _run_worker(db: Database, harness: QdrantHarness, monkeypatch, *, letter_no: str) -> int:
    from rbac_backend.services.document_service import DocumentService

    async def _none(*_a: Any, **_k: Any) -> None:
        return None

    processor = build_processor(
        db,
        harness,
        body=BODY,
        metadata=letter_metadata(
            letter_no=letter_no, subject="Delay to viaduct pier foundations", body=BODY
        ),
        pipeline="legacy_v0",
    )
    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor", lambda: processor
    )
    worker = DocumentService(db)
    monkeypatch.setattr(worker.graph_ingestion, "ingest_document", _none)
    monkeypatch.setattr(worker.graph_ingestion, "sync_document_to_falkor", lambda **_k: None)
    return run(worker.process_next_processing_jobs(limit=5))


def test_bulk_uploaded_letter_is_indexed_canonically_and_found_by_scoped_search(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = Database()
    controller, policy = _controller(db, tmp_path, monkeypatch)
    user_a = user_for(ORG_A, PROJECT_A)
    job_id = _bulk_upload(controller, tmp_path, org=ORG_A, project=PROJECT_A, letter_no="BULK/001", user=user_a)

    # The bulk job reports the row created, and it was the real create path.
    bulk_job = run(db.bulk_upload_jobs.find_one({"job_id": job_id})) or run(
        db.bulk_upload_jobs.find_one({"_id": job_id})
    )
    assert bulk_job is not None and bulk_job.get("successful_uploads") == 1, bulk_job
    [row] = [doc for doc in db.documents._docs.values()]
    assert row["organization_id"] == ORG_A and row["project_id"] == PROJECT_A
    assert row["letterNo"] == "BULK/001"
    [job] = [j for j in db.document_processing_jobs._docs.values()]
    assert job["status"] == "queued" and job["document_id"] == str(row["_id"])
    assert job["pipeline_version"] == "legacy_v0"
    assert {c["organization_id"] for c in policy.calls} == {ORG_A}

    # Nothing is indexed until the worker runs the queued job.
    assert harness.scroll() == []
    assert _run_worker(db, harness, monkeypatch, letter_no="BULK/001") == 1

    document_id = str(row["_id"])
    points = [p for p in harness.scroll() if p.payload.get("document_id") == document_id]
    assert points, "the bulk-created letter was not indexed"
    for point in points:
        payload = point.payload
        assert payload[PAYLOAD_SCHEMA_VERSION_FIELD] == CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
        assert payload["org_id"] == payload["organization_id"] == ORG_A
        assert payload["project_id"] == PROJECT_A
        assert payload["doc_type"] == "letter" and payload["letterNo"] == "BULK/001"
        assert not ADVISORY_FIELDS.intersection(payload)
    status = run(db.vector_sync_status.find_one({"document_id": document_id}))
    assert status["sync_status"] == "synced", status

    found = run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A))
    assert document_id in [r.document_id for r in found.results]
    assert "viaduct pier foundations" in found.results[0].snippet
    # Another tenant's scoped search does not see it.
    foreign = run(scoped_search(db, harness, QUERY, org_id=ORG_B, project_id=PROJECT_B))
    assert foreign.results == []
