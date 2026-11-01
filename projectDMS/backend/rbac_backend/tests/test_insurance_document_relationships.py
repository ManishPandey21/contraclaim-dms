from __future__ import annotations

import httpx
import pytest
import hashlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Response, UploadFile
from pathlib import Path
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from rbac_backend.core.database import get_db
from rbac_backend.core.permissions import Permissions
from rbac_backend.core.security import get_current_user
from rbac_backend.models.document import Document
from rbac_backend.routers.documents import DocumentController, get_document_controller
from rbac_backend.routers.insurance import router as insurance_router
from rbac_backend.routers.document_relationships import router as relationship_router
from rbac_backend.services.insurance_service import InsuranceService
from rbac_backend.tests.selection_fixtures import pin_selection
from rbac_backend.tests.test_claim_document_relationships import (
    _Collection,
    _Database,
    _PermissionPolicy,
    _permission_user,
    _user,
    _relationship_app,
)


def _insurance_db() -> _Database:
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [
            {
                "_id": "insurance-1",
                "policy_number": "POL-17",
                "insurance_type": "Marine Cargo Insurance",
                "organization_id": "org-1",
                "project_id": "project-1",
                "date_of_issue": "2026-08-01T00:00:00Z",
                "date_of_expiry": "2027-08-01T00:00:00Z",
                "document_id": "legacy-policy-token.pdf",
                "linked_document_ids": [],
            }
        ],
    )
    return db


class _CanonicalDocumentController:
    def __init__(self, db: _Database) -> None:
        self.db = db

    async def create_document(self, **kwargs):
        document = {
            "_id": "insurance-doc-1",
            "organization_id": kwargs["organization_id"],
            "project_id": kwargs["project_id"],
            "filename": kwargs["file"].filename,
            "filetype": "application/pdf",
            "filesize": 17,
            "uploadType": kwargs["upload_type"],
            "letterNo": kwargs["letter_no"],
            "date": "2026-08-01T00:00:00Z",
            "subject": kwargs["subject"],
            "status": "draft",
            "createdBy": "user-1",
            "processing_status": "failed",
            "lifecycle_state": "active",
            "file_object_id": "insurance-file-object-1",
            "current_version_id": "insurance-version-1",
            "sha256": "abc123",
        }
        self.db.documents.documents.append(document)
        self.db.file_objects.documents.append(
            {
                "_id": "insurance-file-object-1",
                "organization_id": document["organization_id"],
                "project_id": document["project_id"],
                "sha256": "abc123",
            }
        )
        self.db.document_versions.documents.append(
            {
                "_id": "insurance-version-1",
                "document_id": document["_id"],
                "file_object_id": "insurance-file-object-1",
                "version_number": 1,
                "is_current": True,
            }
        )
        return Document(**document)

    async def compensate_failed_creation(self, document_id: str, **kwargs):
        document = next(
            (row for row in self.db.documents.documents if row.get("_id") == document_id),
            None,
        )
        if document is None:
            return
        file_object_id = document.get("file_object_id")
        self.db.documents.documents = [
            row for row in self.db.documents.documents if row.get("_id") != document_id
        ]
        self.db.document_versions.documents = [
            row
            for row in self.db.document_versions.documents
            if row.get("document_id") != document_id
        ]
        self.db.file_objects.documents = [
            row
            for row in self.db.file_objects.documents
            if row.get("_id") != file_object_id
        ]


class _DeleteTargetAfterCreateController(_CanonicalDocumentController):
    async def create_document(self, **kwargs):
        document = await super().create_document(**kwargs)
        self.db.insurance_policies.documents.clear()
        return document


class _DeleteManyCollection(_Collection):
    async def delete_many(self, query, *args, **kwargs):
        before = len(self.documents)
        if "document_id" in query:
            self.documents = [
                row for row in self.documents if row.get("document_id") != query["document_id"]
            ]
        return type("DeleteResult", (), {"deleted_count": before - len(self.documents)})()


class _CompensationDocumentService:
    def __init__(self, db):
        self.db = db

    async def _get_db(self):
        return self.db


class _CompensationAudit:
    def __init__(self):
        self.events = []

    async def emit(self, **kwargs):
        self.events.append(kwargs)


def _insurance_app(db: _Database) -> FastAPI:
    app = FastAPI()
    app.include_router(relationship_router, prefix="/api")
    app.include_router(insurance_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_document_controller] = lambda: _CanonicalDocumentController(db)
    # The selection the browser sends: the policy's own project (CL-4A).
    pin_selection(app, db, "org-1", "project-1")
    return app


@pytest.mark.asyncio
async def test_insurance_policy_link_is_canonical_forward_and_reverse() -> None:
    db = _insurance_db()
    transport = httpx.ASGITransport(app=_relationship_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/entities/insurance/insurance-1/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "policy",
                        "description": "Original policy evidence",
                    }
                ]
            },
        )
        forward = await client.get(
            "/api/entities/insurance/insurance-1/document-links"
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert created.status_code == 201, created.text
    assert forward.status_code == 200, forward.text
    assert reverse.status_code == 200, reverse.text
    assert forward.json()["links"] == reverse.json()["links"]
    assert forward.json()["links"][0]["target_type"] == "insurance"
    assert forward.json()["links"][0]["target_id"] == "insurance-1"
    assert forward.json()["links"][0]["relationship_role"] == "policy"
    assert forward.json()["links"][0]["target_label"] == "POL-17"
    assert db.insurance_policies.documents[0]["document_id"] == "legacy-policy-token.pdf"


@pytest.mark.asyncio
async def test_insurance_list_projects_authorized_canonical_links_without_legacy_identity() -> None:
    db = _insurance_db()
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/insurance/insurance-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "policy"}]},
        )
        listed = await client.get("/api/insurance")

    assert linked.status_code == 201, linked.text
    assert listed.status_code == 200, listed.text
    row = listed.json()[0]
    assert row["linked_document_ids"] == ["doc-1"]
    assert row["document_id"] == "legacy-policy-token.pdf"


@pytest.mark.asyncio
async def test_insurance_update_returns_authorized_canonical_links() -> None:
    db = _insurance_db()
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/insurance/insurance-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "certificate"}]},
        )
        updated = await client.put(
            "/api/insurance/insurance-1",
            json={"remarks": "Reviewed"},
        )

    assert linked.status_code == 201, linked.text
    assert updated.status_code == 200, updated.text
    assert updated.json()["linked_document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_insurance_alerts_project_authorized_canonical_links_without_affecting_alert_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _insurance_db()
    alert_row = {
        **db.insurance_policies.documents[0],
        "status": "expiring_soon",
        "days_remaining": 14,
    }

    async def fake_alerts(self, scope_filter, now=None):
        return [alert_row]

    monkeypatch.setattr(InsuranceService, "alerts", fake_alerts)
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/insurance/insurance-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "policy"}]},
        )
        alerts = await client.get("/api/insurance/alerts")

    assert linked.status_code == 201, linked.text
    assert alerts.status_code == 200, alerts.text
    row = alerts.json()[0]
    assert row["linked_document_ids"] == ["doc-1"]
    assert row["status"] == "expiring_soon"
    assert row["days_remaining"] == 14


@pytest.mark.asyncio
async def test_insurance_export_receives_authorized_canonical_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _insurance_db()
    captured: dict[str, object] = {}

    async def fake_list(self, scope_filter, **kwargs):
        return [dict(db.insurance_policies.documents[0])]

    def fake_export_response(name, columns, rows, fmt):
        captured["name"] = name
        captured["rows"] = rows
        captured["format"] = fmt
        return Response(content="ok", media_type="text/plain")

    monkeypatch.setattr(InsuranceService, "list", fake_list)
    monkeypatch.setattr(
        "rbac_backend.services.contract_controls_export.export_response",
        fake_export_response,
    )
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/insurance/insurance-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "certificate"}]},
        )
        exported = await client.get("/api/insurance/export?format=csv")

    assert linked.status_code == 201, linked.text
    assert exported.status_code == 200, exported.text
    assert captured["name"] == "insurance-register"
    assert captured["format"] == "csv"
    assert captured["rows"][0]["linked_document_ids"] == ["doc-1"]
    assert captured["rows"][0]["document_id"] == "legacy-policy-token.pdf"


@pytest.mark.asyncio
async def test_new_insurance_upload_creates_canonical_document_and_relationship() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        uploaded = await client.post(
            "/api/insurance/insurance-1/documents/upload",
            data={"relationship_role": "policy"},
            files={"file": ("policy.pdf", b"%PDF-canonical", "application/pdf")},
        )
        forward = await client.get(
            "/api/entities/insurance/insurance-1/document-links"
        )
        reverse = await client.get("/api/documents/insurance-doc-1/entity-links")

    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["document"]["_id"] == "insurance-doc-1"
    assert [row["_id"] for row in db.file_objects.documents] == [
        "insurance-file-object-1"
    ]
    assert [row["_id"] for row in db.document_versions.documents if row["document_id"] == "insurance-doc-1"] == [
        "insurance-version-1"
    ]
    assert [row["document_id"] for row in forward.json()["links"]] == [
        "insurance-doc-1"
    ]
    assert forward.json()["links"] == reverse.json()["links"]
    assert db.insurance_policies.documents[0]["document_id"] == "legacy-policy-token.pdf"


@pytest.mark.asyncio
async def test_canonical_upload_fails_before_storage_when_policy_issue_date_is_missing() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    db.insurance_policies.documents[0].pop("date_of_issue")
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/insurance/insurance-1/documents/upload",
            data={"relationship_role": "policy"},
            files={"file": ("policy.pdf", b"%PDF-canonical", "application/pdf")},
        )

    assert response.status_code == 409, response.text
    assert len(db.documents.documents) == 1
    assert db.file_objects.documents == []
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_canonical_upload_rejects_invalid_role_before_storage() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    transport = httpx.ASGITransport(
        app=_insurance_app(db), raise_app_exceptions=False
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/insurance/insurance-1/documents/upload",
            data={"relationship_role": "renewal"},
            files={"file": ("policy.pdf", b"%PDF-canonical", "application/pdf")},
        )

    assert response.status_code == 422, response.text
    assert len(db.documents.documents) == 1
    assert db.file_objects.documents == []
    assert all(
        row.get("document_id") != "insurance-doc-1"
        for row in db.document_versions.documents
    )
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_canonical_upload_compensates_when_target_disappears_after_storage() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    app = _insurance_app(db)
    app.dependency_overrides[get_document_controller] = lambda: _DeleteTargetAfterCreateController(db)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/insurance/insurance-1/documents/upload",
            data={"relationship_role": "policy"},
            files={"file": ("policy.pdf", b"%PDF-canonical", "application/pdf")},
        )

    assert response.status_code == 404, response.text
    assert all(row.get("_id") != "insurance-doc-1" for row in db.documents.documents)
    assert db.file_objects.documents == []
    assert all(
        row.get("document_id") != "insurance-doc-1"
        for row in db.document_versions.documents
    )
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_production_compensation_removes_unlinked_document_identity_but_keeps_bytes() -> None:
    db = _insurance_db()
    db.documents.documents.append(
        {
            "_id": "new-document",
            "organization_id": "org-1",
            "project_id": "project-1",
            "file_object_id": "new-file-object",
            "lifecycle_state": "active",
        }
    )
    db.document_versions = _DeleteManyCollection(
        "document_versions",
        [
            *db.document_versions.documents,
            {
                "_id": "new-version",
                "document_id": "new-document",
                "file_object_id": "new-file-object",
                "version_number": 1,
                "is_current": True,
            },
        ],
    )
    db.file_objects = _Collection(
        "file_objects",
        [
            {
                "_id": "new-file-object",
                "sha256": "a" * 64,
                "organization_id": "org-1",
                "project_id": "project-1",
                "document_id": "new-document",
                "document_ids": ["new-document"],
            }
        ],
    )
    controller = DocumentController.__new__(DocumentController)
    controller.document_service = _CompensationDocumentService(db)
    controller.audit_service = _CompensationAudit()

    await controller.compensate_failed_creation(
        "new-document",
        current_user=_user(),
        reason="insurance_link_failed",
    )

    assert all(row.get("_id") != "new-document" for row in db.documents.documents)
    assert all(row.get("document_id") != "new-document" for row in db.document_versions.documents)
    assert db.file_objects.documents[0]["_id"] == "new-file-object"
    assert "new-document" not in db.file_objects.documents[0].get("document_ids", [])
    assert db.file_objects.documents[0].get("document_id") is None
    assert controller.audit_service.events[0]["event_type"] == "document.creation_compensated"


@pytest.mark.asyncio
async def test_canonical_controller_compensates_if_version_attachment_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def slot(*args, **kwargs):
        yield

    document = Document(
        _id="new-document",
        organization_id="org-1",
        project_id="project-1",
        filename="policy.pdf",
        filetype="application/pdf",
        filesize=10,
        uploadType="incoming",
        letterNo="POL-1",
        date="2026-08-01T00:00:00Z",
        subject="Insurance policy POL-1",
        status="draft",
        createdBy="user-1",
        processing_status="failed",
        lifecycle_state="active",
    )
    controller = DocumentController.__new__(DocumentController)
    controller.policy_service = SimpleNamespace(authorize=AsyncMock())
    controller.document_service = SimpleNamespace(
        duplicate_detection=SimpleNamespace(
            precheck_upload=AsyncMock(return_value={"verdict": "new_document"})
        ),
        create_document=AsyncMock(return_value=document),
    )
    controller.file_object_service = SimpleNamespace(
        attach_document_version=AsyncMock(
            side_effect=RuntimeError("injected version failure")
        )
    )
    controller.compensate_failed_creation = AsyncMock()
    controller._write_spooled_to_providers = AsyncMock(
        return_value={
            "file_object_id": "new-file-object",
            "filepath_local": str(tmp_path / "policy.pdf"),
            "filepath_s3": None,
            "storage_locations": [],
            "storage_key": "/insurance/policy.pdf",
            "sha256": "a" * 64,
        }
    )
    spooled = SimpleNamespace(
        path=tmp_path / "spooled.pdf",
        sha256="a" * 64,
        filename="policy.pdf",
        cleanup=AsyncMock(),
    )
    monkeypatch.setattr(
        "rbac_backend.routers.documents.upload_concurrency_limiter",
        SimpleNamespace(slot=lambda *args, **kwargs: slot()),
    )
    monkeypatch.setattr(
        "rbac_backend.routers.documents.spool_upload_file",
        AsyncMock(return_value=spooled),
    )
    monkeypatch.setattr(
        "rbac_backend.routers.documents.validate_spooled_upload",
        lambda *args, **kwargs: SimpleNamespace(
            is_valid=True, error=None, mime_type="application/pdf"
        ),
    )
    monkeypatch.setattr("rbac_backend.routers.documents.settings.ANTIVIRUS_ENABLED", False)

    with pytest.raises(HTTPException, match="temporarily unavailable"):
        await controller.create_document(
            background_tasks=None,
            file=UploadFile(filename="policy.pdf", file=SimpleNamespace()),
            organization_id="org-1",
            project_id="project-1",
            upload_type="incoming",
            letter_no="POL-1",
            date_str="2026-08-01",
            current_user=_user(),
            ocr_enabled=False,
        )

    controller.compensate_failed_creation.assert_awaited_once_with(
        "new-document",
        current_user=_user(),
        reason="canonical_document_creation_failed_after_insert",
    )


@pytest.mark.asyncio
async def test_legacy_token_upload_cannot_create_new_parallel_insurance_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    monkeypatch.setattr(
        "rbac_backend.routers.insurance._insurance_dir", lambda: legacy_root
    )
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/insurance/upload",
            files={"file": ("policy.pdf", b"%PDF-legacy", "application/pdf")},
        )

    assert response.status_code == 410, response.text
    assert list(legacy_root.iterdir()) == []
    assert len(db.documents.documents) == 1
    assert db.file_objects.documents == []


@pytest.mark.asyncio
async def test_legacy_file_read_requires_canonical_authority_and_hash_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _insurance_db()
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    content = b"%PDF-legacy-policy"
    (legacy_root / "legacy-policy-token.pdf").write_bytes(content)
    monkeypatch.setattr(
        "rbac_backend.routers.insurance._insurance_dir", lambda: legacy_root
    )
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unresolved = await client.get("/api/insurance/insurance-1/file")
        linked = await client.post(
            "/api/entities/insurance/insurance-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "policy"}]},
        )
        row = db.entity_document_links.documents[0]
        row["source"] = "migration"
        row["metadata"] = {
            "legacy_storage_key": "legacy-policy-token.pdf",
            "legacy_sha256": hashlib.sha256(content).hexdigest(),
        }
        authorized = await client.get("/api/insurance/insurance-1/file")
        row["metadata"]["legacy_sha256"] = "0" * 64
        changed = await client.get("/api/insurance/insurance-1/file")

    assert unresolved.status_code == 409, unresolved.text
    assert linked.status_code == 201, linked.text
    assert authorized.status_code == 200
    assert authorized.content == content
    assert changed.status_code == 409, changed.text


@pytest.mark.asyncio
async def test_create_rejects_ambiguous_legacy_linked_document_intent() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/insurance",
            json={
                "project_id": "project-1",
                "insurance_type": "Marine Cargo Insurance",
                "policy_number": "POL-18",
                "linked_document_ids": ["doc-1"],
            },
        )

    assert response.status_code == 409, response.text
    assert [row["_id"] for row in db.insurance_policies.documents] == ["insurance-1"]
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_update_rejects_ambiguous_legacy_linked_document_replacement() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/insurance/insurance-1",
            json={"linked_document_ids": ["doc-1"]},
        )

    assert response.status_code == 409, response.text
    assert db.insurance_policies.documents[0]["linked_document_ids"] == []
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_create_rejects_legacy_file_token_intent() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/insurance",
            json={
                "project_id": "project-1",
                "insurance_type": "Marine Cargo Insurance",
                "policy_number": "POL-19",
                "document_id": "new-legacy-token.pdf",
            },
        )

    assert response.status_code == 409, response.text
    assert [row["_id"] for row in db.insurance_policies.documents] == ["insurance-1"]


@pytest.mark.asyncio
async def test_update_rejects_legacy_file_token_replacement() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/insurance/insurance-1",
            json={"document_id": "replacement-token.pdf"},
        )

    assert response.status_code == 409, response.text
    assert db.insurance_policies.documents[0]["document_id"] == "legacy-policy-token.pdf"


@pytest.mark.asyncio
async def test_legacy_replace_file_route_cannot_replace_canonical_evidence() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/insurance/insurance-1/replace-file",
            json={"document_id": "replacement-token.pdf"},
        )

    assert response.status_code == 410, response.text
    assert db.insurance_policies.documents[0]["document_id"] == "legacy-policy-token.pdf"


@pytest.mark.asyncio
async def test_delete_soft_unlinks_relationship_without_deleting_document() -> None:
    db = _insurance_db()
    db.file_objects = _Collection("file_objects")
    transport = httpx.ASGITransport(app=_insurance_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/insurance/insurance-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "policy"}]},
        )
        deleted = await client.delete("/api/insurance/insurance-1")

    assert linked.status_code == 201, linked.text
    assert deleted.status_code == 204, deleted.text
    assert db.insurance_policies.documents == []
    assert db.documents.documents[0]["_id"] == "doc-1"
    assert db.documents.documents[0].get("lifecycle_state") == "active"
    assert db.entity_document_links.documents[0]["removed_at"] is not None
    assert db.entity_document_links.documents[0]["removal_reason"] == "Insurance policy deleted"
    assert any(
        event.get("action") == "document_relationship.unlinked"
        for event in db.audit_events.documents
    )
    assert any(
        event.get("action") == "insurance.deleted"
        for event in db.audit_events.documents
    )


@pytest.mark.asyncio
async def test_insurance_document_authority_matrix_is_model_b_fail_closed() -> None:
    cases = [
        ({"processing_status": "failed", "lifecycle_state": "active"}, 201),
        ({"processing_status": "human_review_required", "lifecycle_state": "active"}, 409),
        ({"duplicate_status": "duplicate", "lifecycle_state": "active"}, 409),
        ({"lifecycle_state": "duplicate"}, 409),
        ({"lifecycle_state": "deleted"}, 409),
        ({"project_id": ""}, 409),
    ]
    for overrides, expected_status in cases:
        db = _insurance_db()
        db.documents.documents[0].update(overrides)
        transport = httpx.ASGITransport(app=_relationship_app(db))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/entities/insurance/insurance-1/document-links:batch",
                json={"links": [{"document_id": "doc-1", "relationship_role": "policy"}]},
            )
        assert response.status_code == expected_status, (overrides, response.text)
        assert len(db.entity_document_links.documents) == (1 if expected_status == 201 else 0)


@pytest.mark.asyncio
async def test_insurance_link_requires_edit_and_document_view_independently() -> None:
    for permissions in (
        {Permissions.INSURANCE_EDIT},
        {Permissions.DOCUMENT_VIEW},
    ):
        db = _insurance_db()
        user = _permission_user(*permissions)
        transport = httpx.ASGITransport(
            app=_relationship_app(db, policy=_PermissionPolicy(), user=lambda: user)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/entities/insurance/insurance-1/document-links:batch",
                json={"links": [{"document_id": "doc-1", "relationship_role": "policy"}]},
            )
        assert response.status_code == 403, response.text
        assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_insurance_scope_is_derived_from_target_and_cannot_be_widened() -> None:
    for field, value in (("organization_id", "org-2"), ("project_id", "project-2")):
        db = _insurance_db()
        db.documents.documents[0][field] = value
        transport = httpx.ASGITransport(app=_relationship_app(db))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/entities/insurance/insurance-1/document-links:batch",
                json={
                    "organization_id": value,
                    "project_id": value,
                    "links": [{"document_id": "doc-1", "relationship_role": "policy"}],
                },
            )
        assert response.status_code in {403, 422}, response.text
        assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_insurance_role_matrix_excludes_nonexistent_lifecycle_events() -> None:
    db = _insurance_db()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for role in ("policy", "certificate", "correspondence", "supporting_document"):
            accepted = await client.post(
                "/api/entities/insurance/insurance-1/document-links:batch",
                json={"links": [{"document_id": "doc-1", "relationship_role": role}]},
            )
            assert accepted.status_code == 201, (role, accepted.text)
        for role in ("renewal", "extension", "endorsement"):
            rejected = await client.post(
                "/api/entities/insurance/insurance-1/document-links:batch",
                json={"links": [{"document_id": "doc-1", "relationship_role": role}]},
            )
            assert rejected.status_code == 422, (role, rejected.text)

    assert {row["relationship_role"] for row in db.entity_document_links.documents} == {
        "policy",
        "certificate",
        "correspondence",
        "supporting_document",
    }


@pytest.mark.asyncio
async def test_insurance_multi_supporter_filters_blocked_document_independently() -> None:
    db = _insurance_db()
    db.documents.documents.append(
        {
            **db.documents.documents[0],
            "_id": "doc-2",
            "filename": "Certificate.pdf",
            "current_version_id": "version-2",
        }
    )
    db.document_versions.documents.append(
        {
            "_id": "version-2",
            "document_id": "doc-2",
            "version_number": 1,
            "is_current": True,
            "file_object_id": "file-v2",
        }
    )
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/insurance/insurance-1/document-links:batch",
            json={
                "links": [
                    {"document_id": "doc-1", "relationship_role": "supporting_document"},
                    {"document_id": "doc-2", "relationship_role": "supporting_document"},
                ]
            },
        )
        db.documents.documents[0]["lifecycle_state"] = "deleted"
        visible = await client.get(
            "/api/entities/insurance/insurance-1/document-links"
        )

    assert linked.status_code == 201, linked.text
    assert [row["document_id"] for row in visible.json()["links"]] == ["doc-2"]
    assert len(db.entity_document_links.documents) == 2


@pytest.mark.asyncio
async def test_concurrent_identical_insurance_links_are_idempotent() -> None:
    db = _insurance_db()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async def create_link():
            return await client.post(
                "/api/entities/insurance/insurance-1/document-links:batch",
                json={
                    "idempotency_key": "insurance-concurrent-1",
                    "links": [{"document_id": "doc-1", "relationship_role": "policy"}],
                },
            )

        first, second = await asyncio.gather(create_link(), create_link())

    assert first.status_code == second.status_code == 201
    assert first.json()["links"][0]["_id"] == second.json()["links"][0]["_id"]
    assert len(db.entity_document_links.documents) == 1


@pytest.mark.asyncio
async def test_legacy_read_through_rechecks_document_authority_and_stays_manual_review() -> None:
    db = _insurance_db()
    db.insurance_policies.documents[0]["linked_document_ids"] = ["doc-1"]
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        visible = await client.get(
            "/api/entities/insurance/insurance-1/document-links"
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")
        db.documents.documents[0]["lifecycle_state"] = "deleted"
        blocked = await client.get(
            "/api/entities/insurance/insurance-1/document-links"
        )

    assert visible.status_code == reverse.status_code == blocked.status_code == 200
    forward_link = visible.json()["links"][0]
    reverse_link = reverse.json()["links"][0]
    identity_fields = (
        "_id",
        "target_type",
        "target_id",
        "document_id",
        "relationship_role",
        "source",
    )
    assert {field: forward_link[field] for field in identity_fields} == {
        field: reverse_link[field] for field in identity_fields
    }
    assert forward_link["source"] == "legacy_read_through"
    assert forward_link["relationship_role"] == "manual_review"
    assert forward_link["document_id"] == "doc-1"
    assert blocked.json()["links"] == []
    assert db.insurance_policies.documents[0]["document_id"] == "legacy-policy-token.pdf"


@pytest.mark.asyncio
async def test_relationship_changes_do_not_change_policy_expiry_facts() -> None:
    db = _insurance_db()
    policy = db.insurance_policies.documents[0]
    policy.update(
        {
            "date_of_issue": "2026-08-01T00:00:00Z",
            "date_of_expiry": "2027-08-01T00:00:00Z",
            "status": "active",
            "days_remaining": 344,
        }
    )
    expected = {
        key: policy[key]
        for key in ("date_of_issue", "date_of_expiry", "status", "days_remaining")
    }
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/insurance/insurance-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "policy"}]},
        )
        link = linked.json()["links"][0]
        removed = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "Evidence superseded", "expected_revision": 1},
        )

    assert linked.status_code == 201, linked.text
    assert removed.status_code == 200, removed.text
    stored = db.insurance_policies.documents[0]
    assert {key: stored[key] for key in expected} == expected
