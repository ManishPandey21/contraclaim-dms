"""GET /api/documents/{id}/processing-status: the endpoint the viewer polls.

The client (``enhanced-api.ts::getDocumentProcessingStatus``) has always called this
path, but no backend route existed: every call answered 404, the viewer fell back to
the terminal placeholder ``not_queued``, and that placeholder overrode the
document's real ``processing_status``. So the viewer never showed
``human_review_required`` or ``partially_processed`` and never polled a job that
was still running. Found by the 2533b19 release certification.

The route only exposes what ``DocumentService.get_processing_status`` already
computes (the document's job, or the document's own status when it has none); it
does not redefine any status. Access is exactly ``GET /documents/{id}``'s: the
navbar selection holds the record, then ``dms.document.view`` on it. Runs through
the CL-4A harness: real ``resolve_active_scope`` and ``PolicyService`` over an
in-memory Mongo boundary; only permission/entitlement seams are patched.
"""

from __future__ import annotations

from datetime import datetime

from bson import ObjectId

from rbac_backend.tests.test_cl4a_documents_contracts_scope import (  # noqa: F401 - fixtures
    A1,
    A2,
    DOC_A1,
    DOC_A2,
    DOC_B1,
    MEMBER_A2,
    MEMBER_AB,
    ORG_A,
    SUPERADMIN,
    _as,
    _authorization_seams,
    _code,
    _Database,
    db,
)

#: The fields the client's ``DocumentProcessingJobStatus`` declares.
CLIENT_FIELDS = {
    "_id", "document_id", "status", "stage", "attempts", "max_attempts", "error",
    "metadata", "created_at", "queued_at", "started_at", "completed_at", "updated_at",
}


def _path(document_id: ObjectId) -> str:
    return f"/api/documents/{document_id}/processing-status"


async def _give_job(database: _Database, document_id: ObjectId, **job: object) -> str:
    job_id = str(ObjectId())
    row = {
        "_id": job_id,
        "document_id": str(document_id),
        # Internal fields that must never reach the browser.
        "file_path": "/app/uploads/org-A/proj-A1/incoming/secret-name.pdf",
        "sha256": "f" * 64,
        "requested_by": "u-member-ab",
        "organization_id": ORG_A,
        "project_id": A1,
        "pipeline_version": "unified_v1",
        "attempts": 1,
        "max_attempts": 3,
        "created_at": datetime(2026, 10, 11, 8, 0),
        "updated_at": datetime(2026, 10, 11, 8, 1),
        **job,
    }
    await database.document_processing_jobs.insert_one(row)
    await database.documents.update_one({"_id": document_id}, {"$set": {"processing_job_id": job_id}})
    return job_id


async def test_a_running_job_is_reported_so_the_viewer_keeps_polling(db: _Database) -> None:
    await db.documents.update_one({"_id": DOC_A1}, {"$set": {"processing_status": "processing"}})
    job_id = await _give_job(db, DOC_A1, status="partially_processed", stage="awaiting_page_resume")

    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.get(_path(DOC_A1))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["_id"] == job_id
    assert body["document_id"] == str(DOC_A1)
    assert body["status"] == "partially_processed"
    assert body["stage"] == "awaiting_page_resume"


async def test_human_review_carries_the_actual_review_pages(db: _Database) -> None:
    error = {
        "message": "1 page(s) need review (failed the extraction quality check: page(s) 5)",
        "pages": [5],
        "quality_review_pages": [5],
        "terminal": True,
    }
    await _give_job(db, DOC_A1, status="human_review_required", stage="completed", error=error)

    async with _as(db, MEMBER_AB, A1) as client:
        body = (await client.get(_path(DOC_A1))).json()

    assert body["status"] == "human_review_required"
    assert body["error"]["pages"] == [5]
    assert "page(s) 5" in body["error"]["message"]


async def test_the_response_carries_only_the_client_contract(db: _Database) -> None:
    await _give_job(db, DOC_A1, status="completed", stage="completed")

    async with _as(db, MEMBER_AB, A1) as client:
        body = (await client.get(_path(DOC_A1))).json()

    assert set(body) <= CLIENT_FIELDS, sorted(set(body) - CLIENT_FIELDS)
    for internal in ("file_path", "sha256", "requested_by", "organization_id", "project_id"):
        assert internal not in body
    assert "secret-name" not in str(body)


async def test_a_document_without_a_job_reports_its_own_status_not_a_placeholder(db: _Database) -> None:
    # DOC_A1 is seeded `completed` with no processing job.
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.get(_path(DOC_A1))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"


async def test_the_selection_holds_the_record_exactly_like_get_document(db: _Database) -> None:
    await _give_job(db, DOC_A1, status="completed", stage="completed")

    # Another project selected: refused before anything about the job is read.
    async with _as(db, MEMBER_AB, A2) as client:
        response = await client.get(_path(DOC_A1))
    assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
    assert A1 not in response.text

    # Nothing selected: 400 before the load, whether or not the id exists.
    async with _as(db, MEMBER_AB, None) as client:
        for document_id in (DOC_A1, ObjectId()):
            response = await client.get(_path(document_id))
            assert response.status_code == 400, response.text
            assert _code(response) == "selection_required"


async def test_a_non_member_and_a_foreign_organisation_are_refused(db: _Database) -> None:
    await _give_job(db, DOC_A1, status="completed", stage="completed")

    # Member of A2 only, selecting A1.
    async with _as(db, MEMBER_A2, A1) as client:
        response = await client.get(_path(DOC_A1))
    assert response.status_code == 403, response.text

    # The other organisation's record under this user's own selection.
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.get(_path(DOC_B1))
    assert response.status_code in (403, 404), response.text
    assert response.status_code != 200


async def test_superadmin_is_bounded_by_an_explicit_selection(db: _Database) -> None:
    async with _as(db, SUPERADMIN, A2, ORG_A) as client:
        response = await client.get(_path(DOC_A1))
    assert response.status_code == 403, response.text

    async with _as(db, SUPERADMIN, A1, ORG_A) as client:
        response = await client.get(_path(DOC_A1))
    assert response.status_code == 200, response.text


async def test_unknown_and_deleted_documents_answer_as_get_document_does(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A2) as client:
        unknown = await client.get(_path(ObjectId()))
        assert unknown.status_code == 404, unknown.text

        await _give_job(db, DOC_A2, status="completed", stage="completed")
        await db.documents.update_one({"_id": DOC_A2}, {"$set": {"lifecycle_state": "deleted"}})
        document = await client.get(f"/api/documents/{DOC_A2}")
        status = await client.get(_path(DOC_A2))

    assert document.status_code != 200, "precondition: a deleted document is not served"
    assert status.status_code == document.status_code, (status.status_code, status.text)
