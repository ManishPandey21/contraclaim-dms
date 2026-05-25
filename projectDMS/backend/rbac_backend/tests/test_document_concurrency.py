import pytest
from bson import ObjectId

from rbac_backend.models.document import Document
from rbac_backend.services.document_service import DocumentConflictError, DocumentService


class _UpdateResult:
    def __init__(self, matched_count=1, modified_count=1):
        self.matched_count = matched_count
        self.modified_count = modified_count


class _Documents:
    def __init__(self):
        self.oid = ObjectId()
        self.revision = 2
        self.last_query = None

    async def find_one(self, query, projection=None):
        if query.get("_id") != self.oid:
            return None
        if projection:
            return {"_id": self.oid, "_revision": self.revision}
        return {
            "_id": self.oid,
            "organization_id": "org-1",
            "project_id": "proj-1",
            "filename": "doc.pdf",
            "filetype": "application/pdf",
            "filesize": 1,
            "uploadType": "incoming",
            "letterNo": "LTR-1",
            "date": "2026-05-03T00:00:00",
            "subject": "Subject",
            "status": "Received",
            "createdBy": "user-1",
            "_revision": self.revision,
        }

    async def update_one(self, query, update):
        self.last_query = query
        if query.get("_revision") not in (None, self.revision):
            return _UpdateResult(matched_count=0, modified_count=0)
        self.revision += 1
        return _UpdateResult()


class _Db:
    def __init__(self):
        self.documents = _Documents()


@pytest.mark.asyncio
async def test_update_document_uses_expected_revision_filter():
    db = _Db()
    service = DocumentService(db)
    document_id = str(db.documents.oid)
    current = await service.get_document(document_id)

    updated = await service.update_document(
        document_id,
        current.model_copy(update={"subject": "Updated"}),
        expected_revision=2,
    )

    assert updated is not None
    assert db.documents.last_query["_revision"] == 2
    assert await service.get_document_revision(document_id) == 3


@pytest.mark.asyncio
async def test_update_document_raises_conflict_for_stale_revision():
    db = _Db()
    service = DocumentService(db)
    document_id = str(db.documents.oid)
    current = await service.get_document(document_id)

    with pytest.raises(DocumentConflictError) as exc:
        await service.update_document(
            document_id,
            current.model_copy(update={"subject": "Updated"}),
            expected_revision=1,
        )

    assert exc.value.current_revision == 2
