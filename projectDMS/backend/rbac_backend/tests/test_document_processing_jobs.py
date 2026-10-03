from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from backend.rbac_backend.models.document import Document
from backend.rbac_backend.core.config import settings
from backend.rbac_backend.services.document_service import DocumentService


class FakeCollection:
    def __init__(self):
        self.docs = {}

    def _match(self, doc, query):
        for key, expected in (query or {}).items():
            if key == "$or":
                if not any(self._match(doc, branch) for branch in expected):
                    return False
                continue
            if key == "$and":
                if not all(self._match(doc, branch) for branch in expected):
                    return False
                continue
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$exists" in expected and (key in doc) != expected["$exists"]:
                    return False
                if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                    return False
                continue
            if actual != expected:
                return False
        return True

    async def insert_one(self, doc):
        stored = dict(doc)
        inserted_id = stored.get("_id") or ObjectId()
        stored["_id"] = inserted_id
        self.docs[inserted_id] = stored
        return SimpleNamespace(inserted_id=inserted_id)

    async def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self.docs.values() if self._match(doc, query)]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda item: item.get(key) or datetime.min, reverse=direction < 0)
        return dict(matches[0]) if matches else None

    async def update_one(self, query, update):
        for key, doc in self.docs.items():
            if self._match(doc, query):
                for field, value in update.get("$set", {}).items():
                    doc[field] = value
                for field, value in update.get("$inc", {}).items():
                    doc[field] = doc.get(field, 0) + value
                self.docs[key] = doc
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def find_one_and_update(self, query, update, sort=None, return_document=None):
        found = await self.find_one(query, sort=sort)
        if not found:
            return None
        await self.update_one({"_id": found["_id"]}, update)
        return await self.find_one({"_id": found["_id"]})


class FakeDB:
    def __init__(self):
        self.documents = FakeCollection()
        self.contract_documents = FakeCollection()
        self.document_processing_jobs = FakeCollection()


def make_document(document_id: ObjectId) -> Document:
    return Document(
        _id=str(document_id),
        organization_id="org-1",
        project_id="project-1",
        filename="letter.pdf",
        filepath_local=str(Path("letter.pdf")),
        filetype="application/pdf",
        filesize=10,
        uploadType="incoming",
        letterNo="LET-001",
        date=datetime.utcnow(),
        subject="Subject",
        status="Received",
        createdBy="user-1",
    )


async def insert_document(db: FakeDB, document: Document, document_id: ObjectId) -> None:
    data = document.model_dump(by_alias=True)
    data["_id"] = document_id
    await db.documents.insert_one(data)


@pytest.mark.asyncio
async def test_queue_document_processing_creates_durable_job_and_updates_document():
    db = FakeDB()
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id)
    await insert_document(db, document, document_id)

    job_id = await service.queue_document_processing(document, "letter.pdf", requested_by="user-1")

    job = await db.document_processing_jobs.find_one({"_id": job_id})
    stored = await db.documents.find_one({"_id": document_id})

    assert job["status"] == "queued"
    assert job["document_id"] == str(document_id)
    assert stored["processing_status"] == "queued"
    assert stored["processing_job_id"] == job_id


@pytest.mark.asyncio
async def test_process_document_job_marks_completed(monkeypatch):
    db = FakeDB()
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id)
    await insert_document(db, document, document_id)
    job_id = await service.queue_document_processing(document, "letter.pdf")

    async def fake_process(*args, **kwargs):
        return True

    monkeypatch.setattr(service, "process_document_async", fake_process)

    ok = await service.process_document_job(job_id)
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    stored = await db.documents.find_one({"_id": document_id})

    assert ok is True
    assert job["status"] == "completed"
    assert stored["processing_status"] == "completed"


@pytest.mark.asyncio
async def test_recover_stale_document_processing_job_marks_retrying(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_PROCESSING_STALE_AFTER_SECONDS", 300)
    db = FakeDB()
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id)
    await insert_document(db, document, document_id)
    stale_time = datetime.utcnow() - timedelta(seconds=600)
    await db.document_processing_jobs.insert_one(
        {
            "_id": "job-stale",
            "document_id": str(document_id),
            "status": "processing",
            "stage": "extracting",
            "attempts": 1,
            "max_attempts": 3,
            "updated_at": stale_time,
            "heartbeat_at": stale_time,
        }
    )

    recovered = await service.recover_stale_processing_jobs()

    job = await db.document_processing_jobs.find_one({"_id": "job-stale"})
    stored = await db.documents.find_one({"_id": document_id})
    assert recovered == 1
    assert job["status"] == "retrying"
    assert job["stage"] == "recovered_stale"
    assert job["run_after"] <= datetime.utcnow()
    assert stored["processing_status"] == "retrying"


@pytest.mark.asyncio
async def test_recover_stale_document_processing_job_keeps_fresh_heartbeat(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_PROCESSING_STALE_AFTER_SECONDS", 300)
    db = FakeDB()
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id)
    await insert_document(db, document, document_id)
    now = datetime.utcnow()
    await db.document_processing_jobs.insert_one(
        {
            "_id": "job-fresh",
            "document_id": str(document_id),
            "status": "processing",
            "stage": "extracting",
            "attempts": 1,
            "max_attempts": 3,
            "updated_at": now,
            "heartbeat_at": now,
        }
    )

    recovered = await service.recover_stale_processing_jobs()

    job = await db.document_processing_jobs.find_one({"_id": "job-fresh"})
    assert recovered == 0
    assert job["status"] == "processing"


@pytest.mark.asyncio
async def test_process_document_async_dead_letters_soft_deleted_document():
    db = FakeDB()
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id)
    data = document.model_dump(by_alias=True)
    data["_id"] = document_id
    data["lifecycle_state"] = "deleted"
    await db.documents.insert_one(data)
    await db.document_processing_jobs.insert_one(
        {
            "_id": "job-deleted",
            "document_id": str(document_id),
            "status": "processing",
            "stage": "materializing",
            "attempts": 1,
            "max_attempts": 3,
            "updated_at": datetime.utcnow(),
        }
    )

    ok = await service.process_document_async(str(document_id), "missing.pdf", job_id="job-deleted")

    job = await db.document_processing_jobs.find_one({"_id": "job-deleted"})
    stored = await db.documents.find_one({"_id": document_id})
    assert ok is False
    assert job["status"] == "dead_lettered"
    assert job["stage"] == "skipped_deleted_document"
    assert job["error"]["terminal"] is True
    assert stored["processing_status"] == "skipped"
    assert stored["processing_error"]["terminal"] is True


@pytest.mark.asyncio
async def test_create_document_honors_supplied_status(tmp_path):
    db = FakeDB()
    service = DocumentService(db)
    pdf_path = tmp_path / "letter.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    document = await service.create_document(
        filepath_local=str(pdf_path),
        filename="letter.pdf",
        organization_id="org-1",
        project_id="project-1",
        upload_type="incoming",
        letter_no="LET-001",
        date=datetime.utcnow(),
        subject="Subject",
        status="Input Required",
        current_user=SimpleNamespace(id="user-1", email="user@example.com"),
        ocr_enabled=False,
    )

    assert document.status == "Input Required"
