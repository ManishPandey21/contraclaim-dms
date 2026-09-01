from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.errors import DuplicateKeyError

from rbac_backend.core.database import ensure_indexes, get_db
from rbac_backend.core.permissions import Permissions
from rbac_backend.core.security import get_current_user
from rbac_backend.routers.bank_guarantees import get_policy, router as bg_router
from rbac_backend.routers.document_relationships import (
    get_document_relationship_service,
    router as relationship_router,
)
from rbac_backend.services.document_relationship_service import DocumentRelationshipService
from rbac_backend.models.document_relationship import DocumentRelationshipInput
from rbac_backend.tests.test_claim_document_relationships import (
    _Collection,
    _Database,
    _IndexDatabase,
    _PermissionPolicy,
    _permission_user,
    _user,
)


class _AllowPolicy:
    async def authorize(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def authorize_document(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _BankGuaranteeDatabase(_Database):
    def __init__(self) -> None:
        super().__init__()
        self.bank_guarantees = _Collection(
            "bank_guarantees",
            [
                {
                    "_id": "bg-1",
                    "bg_type": "performance",
                    "bg_number": "BG-001",
                    "currency": "INR",
                    "bg_status": "valid",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "linked_document_ids": [],
                    "current_revision": 0,
                    "created_at": datetime(2026, 1, 1),
                }
            ],
        )
        self.bank_guarantee_events = _Collection("bank_guarantee_events")
        self.bg_extension_history = _Collection("bg_extension_history")
        self.bg_notifications = _Collection("bg_notifications")
        self.contract_master = _Collection("contract_master")


def _app(
    db: _BankGuaranteeDatabase,
    *,
    relationship_policy: Any = None,
    bg_policy: Any = None,
    user: Any = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(bg_router, prefix="/api")
    app.include_router(relationship_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = user or _user
    app.dependency_overrides[get_policy] = lambda: bg_policy or _AllowPolicy()
    app.dependency_overrides[get_document_relationship_service] = lambda: DocumentRelationshipService(
        db, policy=relationship_policy or _AllowPolicy()
    )
    return app


@pytest.mark.asyncio
async def test_release_api_persists_request_fields_as_a_stable_release_event() -> None:
    db = _BankGuaranteeDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/bank-guarantees/bg-1/release",
            json={
                "release_date": "2026-08-20T00:00:00Z",
                "release_letter_reference": "REL/2026/17",
                "remarks": "Original returned to issuer",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["bg_status"] == "released"
    assert len(db.bank_guarantee_events.documents) == 1
    event = db.bank_guarantee_events.documents[0]
    assert event["_id"]
    assert event["bank_guarantee_id"] == "bg-1"
    assert event["event_type"] == "release"
    assert event["event_date"].isoformat() == "2026-08-20T00:00:00+00:00"
    assert event["reference"] == "REL/2026/17"
    assert event["remarks"] == "Original returned to issuer"
    assert event["created_by"] == "user-1"
    assert event["organization_id"] == "org-1"
    assert event["project_id"] == "project-1"
    audit = next(
        row for row in db.audit_events.documents
        if row.get("action") == "bank_guarantee.released"
    )
    assert audit["after"]["event"]["_id"] == event["_id"]
    assert audit["after"]["event"]["event_date"] == event["event_date"]
    assert audit["after"]["event"]["reference"] == "REL/2026/17"
    assert audit["after"]["event"]["remarks"] == "Original returned to issuer"


@pytest.mark.asyncio
async def test_extension_api_creates_event_and_preserves_legacy_history() -> None:
    db = _BankGuaranteeDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/bank-guarantees/bg-1/extend",
            json={
                "revised_expiry_date": "2027-08-20T00:00:00Z",
                "revised_claim_expiry_date": "2027-09-20T00:00:00Z",
                "extension_date": "2026-08-19T00:00:00Z",
                "extension_letter_reference": "EXT/2026/02",
                "remarks": "Second extension",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["bg_status"] == "extended"
    assert len(db.bank_guarantee_events.documents) == 1
    event = db.bank_guarantee_events.documents[0]
    assert event["event_type"] == "extension"
    assert event["sequence"] == 1
    assert event["revision_number"] == 1
    assert event["expiry_after"].isoformat() == "2027-08-20T00:00:00+00:00"
    assert event["claim_expiry_after"].isoformat() == "2027-09-20T00:00:00+00:00"
    assert event["reference"] == "EXT/2026/02"
    assert len(db.bg_extension_history.documents) == 1
    assert db.bg_extension_history.documents[0]["revision_number"] == 1


@pytest.mark.asyncio
async def test_extension_identity_uses_extension_revision_not_global_event_sequence() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0].update(
        {"current_revision": 2, "event_sequence": 0}
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/bank-guarantees/bg-1/extend",
            json={"revised_expiry_date": "2029-08-20T00:00:00Z"},
        )
        event = db.bank_guarantee_events.documents[0]
        linked = await client.post(
            f"/api/entities/bank_guarantee_event/{event['_id']}/document-links:batch",
            json={
                "links": [
                    {"document_id": "doc-1", "relationship_role": "extension"}
                ]
            },
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert response.status_code == 200, response.text
    assert event["sequence"] == 1
    assert event["revision_number"] == 3
    assert linked.status_code == 201, linked.text
    assert reverse.json()["links"][0]["target_label"].endswith("Extension 3")


@pytest.mark.asyncio
async def test_create_api_creates_original_event_and_rejects_legacy_intent() -> None:
    db = _BankGuaranteeDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post(
            "/api/bank-guarantees",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "bg_number": "BG-LEGACY",
                "linked_document_ids": ["doc-1"],
            },
        )
        created = await client.post(
            "/api/bank-guarantees",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "bg_number": "BG-002",
                "linked_document_ids": [],
            },
        )

    assert rejected.status_code == 409, rejected.text
    assert created.status_code == 201, created.text
    event = next(
        item for item in db.bank_guarantee_events.documents
        if item["bank_guarantee_id"] == created.json()["_id"]
    )
    assert event["event_type"] == "original"
    assert event["sequence"] == 1
    stored = next(item for item in db.bank_guarantees.documents if item["_id"] == created.json()["_id"])
    assert "linked_document_ids" not in stored


@pytest.mark.asyncio
async def test_new_bank_guarantee_cannot_skip_explicit_lifecycle_entry() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents.clear()
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/bank-guarantees",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "bg_number": "BG-DRAFT",
            },
        )
        submitted = await client.post(
            "/api/bank-guarantees",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "bg_number": "BG-SUBMITTED-BYPASS",
                "bg_status": "submitted",
            },
        )
        valid = await client.post(
            "/api/bank-guarantees",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "bg_number": "BG-VALID-BYPASS",
                "bg_status": "valid",
            },
        )
        dated = await client.post(
            "/api/bank-guarantees",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "bg_number": "BG-DATED-BYPASS",
                "bg_status": "draft",
                "submission_date": "2026-08-22T00:00:00Z",
            },
        )

    assert created.status_code == 201, created.text
    assert created.json()["bg_status"] == "draft"
    assert submitted.status_code == 409, submitted.text
    assert valid.status_code == 409, valid.text
    assert dated.status_code == 409, dated.text
    assert len(db.bank_guarantees.documents) == 1
    assert [event["event_type"] for event in db.bank_guarantee_events.documents] == [
        "original"
    ]


@pytest.mark.asyncio
async def test_extension_event_owns_evidence_with_forward_reverse_hierarchy() -> None:
    db = _BankGuaranteeDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        extended = await client.post(
            "/api/bank-guarantees/bg-1/extend",
            json={
                "revised_expiry_date": "2027-08-20T00:00:00Z",
                "extension_letter_reference": "EXT/2026/03",
            },
        )
        assert extended.status_code == 200, extended.text
        event_id = db.bank_guarantee_events.documents[0]["_id"]
        linked = await client.post(
            f"/api/entities/bank_guarantee_event/{event_id}/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "extension"}]},
        )
        bad_role = await client.post(
            f"/api/entities/bank_guarantee_event/{event_id}/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "release"}]},
        )
        forward = await client.get(
            f"/api/entities/bank_guarantee_event/{event_id}/document-links"
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert linked.status_code == 201, linked.text
    assert bad_role.status_code == 422, bad_role.text
    assert len(forward.json()["links"]) == 1
    peer = forward.json()["links"][0]
    reverse_peer = reverse.json()["links"][0]
    assert peer["target_type"] == "bank_guarantee_event"
    assert peer["target_id"] == event_id
    assert peer["parent_type"] == "bank_guarantee"
    assert peer["parent_id"] == "bg-1"
    assert peer["relationship_role"] == "extension"
    assert reverse_peer["_id"] == peer["_id"]
    assert reverse_peer["target_label"] == "BG-001 · Extension 1"
    assert reverse_peer["target_route"] == f"/bank-guarantees?bg_id=bg-1&event_id={event_id}"


@pytest.mark.asyncio
async def test_release_history_preserves_prior_extension_evidence() -> None:
    db = _BankGuaranteeDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/bank-guarantees/bg-1/extend",
            json={
                "revised_expiry_date": "2027-08-20T00:00:00Z",
                "extension_letter_reference": "EXT/03",
            },
        )
        extension_id = db.bank_guarantee_events.documents[0]["_id"]
        extension_link = await client.post(
            f"/api/entities/bank_guarantee_event/{extension_id}/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "extension"}]},
        )
        released = await client.post(
            "/api/bank-guarantees/bg-1/release",
            json={
                "release_date": "2028-01-10T00:00:00Z",
                "release_letter_reference": "REL/04",
            },
        )
        release_id = db.bank_guarantee_events.documents[1]["_id"]
        release_link = await client.post(
            f"/api/entities/bank_guarantee_event/{release_id}/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "release"}]},
        )
        history = await client.get("/api/bank-guarantees/bg-1/events")
        extension_evidence = await client.get(
            f"/api/entities/bank_guarantee_event/{extension_id}/document-links"
        )
        release_evidence = await client.get(
            f"/api/entities/bank_guarantee_event/{release_id}/document-links"
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert extension_link.status_code == 201, extension_link.text
    assert released.status_code == 200, released.text
    assert release_link.status_code == 201, release_link.text
    assert history.status_code == 200, history.text
    assert [(event["event_type"], event["reference"]) for event in history.json()] == [
        ("extension", "EXT/03"),
        ("release", "REL/04"),
    ]
    assert extension_evidence.json()["links"][0]["relationship_role"] == "extension"
    assert release_evidence.json()["links"][0]["relationship_role"] == "release"
    assert {item["target_id"] for item in reverse.json()["links"]} == {
        extension_id,
        release_id,
    }


@pytest.mark.asyncio
async def test_event_link_requires_event_permission_and_document_view() -> None:
    for permissions in (
        (Permissions.BG_EXTEND,),
        (Permissions.DOCUMENT_VIEW,),
    ):
        db = _BankGuaranteeDatabase()
        db.bank_guarantee_events.documents.append(
            {
                "_id": "event-extension",
                "bank_guarantee_id": "bg-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "event_type": "extension",
                "sequence": 1,
                "created_at": datetime(2026, 8, 20),
            }
        )
        user = _permission_user(*permissions)
        transport = httpx.ASGITransport(
            app=_app(
                db,
                relationship_policy=_PermissionPolicy(),
                user=lambda: user,
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/entities/bank_guarantee_event/event-extension/document-links:batch",
                json={"links": [{"document_id": "doc-1", "relationship_role": "extension"}]},
            )
        assert response.status_code == 403, response.text
        assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_event_adapter_fails_closed_on_forged_parent_scope() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantee_events.documents.append(
        {
            "_id": "event-forged",
            "bank_guarantee_id": "bg-1",
            "organization_id": "org-foreign",
            "project_id": "project-1",
            "event_type": "extension",
            "sequence": 1,
            "created_at": datetime(2026, 8, 20),
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/bank_guarantee_event/event-forged/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "extension"}]},
        )

    assert response.status_code == 404, response.text
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_concurrent_identical_event_links_are_idempotent_and_audited_once() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantee_events.documents.append(
        {
            "_id": "event-extension",
            "bank_guarantee_id": "bg-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "event_type": "extension",
            "sequence": 1,
            "created_at": datetime(2026, 8, 20),
        }
    )
    service = DocumentRelationshipService(db, policy=_AllowPolicy())
    request = [
        DocumentRelationshipInput(
            document_id="doc-1",
            relationship_role="extension",
        )
    ]

    first, second = await asyncio.gather(
        service.link_batch(
            _user(),
            "bank_guarantee_event",
            "event-extension",
            request,
            idempotency_key="bg-event-link-1",
        ),
        service.link_batch(
            _user(),
            "bank_guarantee_event",
            "event-extension",
            request,
            idempotency_key="bg-event-link-1",
        ),
    )

    assert first[0].id == second[0].id
    assert len(db.entity_document_links.documents) == 1
    assert len(
        [
            event
            for event in db.audit_events.documents
            if event.get("action") == "document_relationship.linked"
        ]
    ) == 1


@pytest.mark.asyncio
async def test_legacy_parent_evidence_is_read_through_only_after_current_authority() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0]["linked_document_ids"] = ["doc-1"]
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        visible = await client.get("/api/entities/bank_guarantee/bg-1/document-links")
        native_parent_write = await client.post(
            "/api/entities/bank_guarantee/bg-1/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "supporting_document",
                    }
                ]
            },
        )
        db.documents.documents[0]["lifecycle_state"] = "deleted"
        blocked = await client.get("/api/entities/bank_guarantee/bg-1/document-links")

    assert visible.status_code == 200, visible.text
    assert visible.json()["links"][0]["source"] == "legacy_read_through"
    assert visible.json()["links"][0]["relationship_role"] == "manual_review"
    assert native_parent_write.status_code == 422, native_parent_write.text
    assert blocked.json()["links"] == []
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_legacy_update_and_extension_writes_are_manual_review_without_mutation() -> None:
    db = _BankGuaranteeDatabase()
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        updated = await client.put(
            "/api/bank-guarantees/bg-1",
            json={"linked_document_ids": ["doc-1"]},
        )
        extended = await client.post(
            "/api/bank-guarantees/bg-1/extend",
            json={
                "revised_expiry_date": "2027-08-20T00:00:00Z",
                "linked_document_ids": ["doc-1"],
            },
        )

    assert updated.status_code == 409, updated.text
    assert extended.status_code == 409, extended.text
    assert db.bank_guarantees.documents[0]["linked_document_ids"] == []
    assert db.bank_guarantee_events.documents == []
    assert db.bg_extension_history.documents == []
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_migration_dry_run_is_deterministic_complete_and_read_only() -> None:
    from rbac_backend.services.bank_guarantee_document_link_migration import (
        classify_legacy_bank_guarantee_links,
    )

    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0].update(
        {
            "bg_status": "released",
            "linked_document_ids": ["doc-1", "doc-1", "missing-doc"],
        }
    )
    db.bg_extension_history.documents.extend(
        [
            {
                "_id": "history-1",
                "bg_id": "bg-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "revision_number": 1,
                "extension_date": datetime(2026, 7, 1),
                "new_expiry_date": datetime(2027, 7, 1),
            },
            {
                "_id": "history-orphan",
                "bg_id": "missing-bg",
                "organization_id": "org-1",
                "project_id": "project-1",
                "revision_number": 2,
            },
        ]
    )
    db.bank_guarantee_events.documents.append(
        {
            "_id": "forged-release",
            "bank_guarantee_id": "bg-1",
            "organization_id": "foreign-org",
            "project_id": "foreign-project",
            "event_type": "release",
            "sequence": 99,
        }
    )
    before = deepcopy(
        {
            "bgs": db.bank_guarantees.documents,
            "history": db.bg_extension_history.documents,
            "events": db.bank_guarantee_events.documents,
            "links": db.entity_document_links.documents,
        }
    )

    first = await classify_legacy_bank_guarantee_links(db)
    second = await classify_legacy_bank_guarantee_links(db)

    assert first == second
    assert first["dry_run"] is True
    assert set(first["counts"]) >= {
        "valid", "missing_document", "deleted_document", "blocked_document",
        "cross_organisation", "cross_project", "invalid_id", "duplicate",
        "ambiguous_role", "ambiguous_event", "missing_event_date",
        "missing_parent", "manual_review", "resolvable_event",
        "ambiguous_extension_ownership", "unresolved_evidence",
        "release_history_inconsistency",
    }
    assert first["counts"]["duplicate"] == 1
    assert first["counts"]["missing_document"] == 1
    assert first["counts"]["resolvable_event"] == 1
    assert first["counts"]["missing_parent"] == 1
    assert first["counts"]["release_history_inconsistency"] == 2
    assert {row["kind"] for row in first["release_history_inconsistencies"]} == {
        "missing_release_event",
        "scope_mismatch",
    }
    assert before == {
        "bgs": db.bank_guarantees.documents,
        "history": db.bg_extension_history.documents,
        "events": db.bank_guarantee_events.documents,
        "links": db.entity_document_links.documents,
    }


@pytest.mark.asyncio
async def test_bank_guarantee_event_indexes_cover_sequence_scope_and_event_type() -> None:
    db = _IndexDatabase()
    await ensure_indexes(db)
    calls = [call for call in db.calls if call[0] == "bank_guarantee_events"]

    assert any(
        keys
        == [
            ("organization_id", 1),
            ("project_id", 1),
            ("bank_guarantee_id", 1),
            ("sequence", 1),
        ]
        and kwargs.get("unique") is True
        for _, keys, kwargs in calls
    )
    assert any(
        keys == [("bank_guarantee_id", 1), ("event_type", 1), ("event_date", 1)]
        for _, keys, _ in calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document_changes", "missing", "expected_status"),
    [
        ({}, False, 201),
        ({"processing_status": "failed"}, False, 201),
        ({"processing_status": "human_review_required"}, False, 409),
        ({"duplicate_status": "duplicate"}, False, 409),
        ({"lifecycle_state": "duplicate"}, False, 409),
        ({"lifecycle_state": "deleted"}, False, 409),
        ({"project_id": None}, False, 409),
        ({"organization_id": "foreign-org"}, False, 403),
        ({"project_id": "foreign-project"}, False, 403),
        ({}, True, 404),
    ],
)
async def test_bank_guarantee_event_document_authority_matrix(
    document_changes: dict[str, Any], missing: bool, expected_status: int
) -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantee_events.documents.append(
        {
            "_id": "event-extension",
            "bank_guarantee_id": "bg-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "event_type": "extension",
            "sequence": 1,
            "created_at": datetime(2026, 8, 20),
        }
    )
    if missing:
        db.documents.documents.clear()
    else:
        db.documents.documents[0].update(document_changes)
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/bank_guarantee_event/event-extension/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "extension"}]},
        )

    assert response.status_code == expected_status, response.text
    assert len(db.entity_document_links.documents) == (1 if expected_status == 201 else 0)


@pytest.mark.asyncio
async def test_e0_e1_e2_e3_evidence_ownership_and_same_document_multiple_events() -> None:
    db = _BankGuaranteeDatabase()
    db.documents.documents.extend(
        [
            {
                "_id": f"doc-{number}",
                "filename": f"Evidence-{number}.pdf",
                "organization_id": "org-1",
                "project_id": "project-1",
                "processing_status": "metadata_extracted",
                "lifecycle_state": "active",
            }
            for number in range(2, 5)
        ]
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/bank-guarantees",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "bg_number": "BG-EVENTS",
            },
        )
        bg_id = created.json()["_id"]
        await client.post(
            f"/api/bank-guarantees/{bg_id}/extend",
            json={"revised_expiry_date": "2027-01-01T00:00:00Z"},
        )
        await client.post(
            f"/api/bank-guarantees/{bg_id}/extend",
            json={"revised_expiry_date": "2028-01-01T00:00:00Z"},
        )
        await client.post(
            f"/api/bank-guarantees/{bg_id}/release",
            json={"release_date": "2028-02-01T00:00:00Z"},
        )
        events = [
            event for event in db.bank_guarantee_events.documents
            if event["bank_guarantee_id"] == bg_id
        ]
        roles = ("original_bg", "extension", "extension", "release")
        for event, document_id, role in zip(events, ("doc-1", "doc-2", "doc-3", "doc-4"), roles):
            linked = await client.post(
                f"/api/entities/bank_guarantee_event/{event['_id']}/document-links:batch",
                json={"links": [{"document_id": document_id, "relationship_role": role}]},
            )
            assert linked.status_code == 201, linked.text
        for event in events[1:3]:
            same_document = await client.post(
                f"/api/entities/bank_guarantee_event/{event['_id']}/document-links:batch",
                json={
                    "links": [
                        {
                            "document_id": "doc-1",
                            "relationship_role": "supporting_document",
                        }
                    ]
                },
            )
            assert same_document.status_code == 201, same_document.text

    assert [event["event_type"] for event in events] == [
        "original", "extension", "extension", "release"
    ]
    links_by_event = {
        event["_id"]: [
            link for link in db.entity_document_links.documents
            if link["target_id"] == event["_id"]
        ]
        for event in events
    }
    assert {link["document_id"] for link in links_by_event[events[0]["_id"]]} == {"doc-1"}
    assert {link["document_id"] for link in links_by_event[events[1]["_id"]]} == {"doc-1", "doc-2"}
    assert {link["document_id"] for link in links_by_event[events[2]["_id"]]} == {"doc-1", "doc-3"}
    assert {link["document_id"] for link in links_by_event[events[3]["_id"]]} == {"doc-4"}


@pytest.mark.asyncio
async def test_event_multi_supporter_block_and_unlink_are_independent() -> None:
    db = _BankGuaranteeDatabase()
    db.documents.documents.append(
        {
            "_id": "doc-2",
            "filename": "Second.pdf",
            "organization_id": "org-1",
            "project_id": "project-1",
            "processing_status": "metadata_extracted",
            "lifecycle_state": "active",
        }
    )
    db.bank_guarantee_events.documents.append(
        {
            "_id": "event-extension",
            "bank_guarantee_id": "bg-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "event_type": "extension",
            "sequence": 1,
            "created_at": datetime(2026, 8, 20),
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/bank_guarantee_event/event-extension/document-links:batch",
            json={
                "links": [
                    {"document_id": "doc-1", "relationship_role": "supporting_document"},
                    {"document_id": "doc-2", "relationship_role": "supporting_document"},
                ]
            },
        )
        first_link = next(item for item in linked.json()["links"] if item["document_id"] == "doc-1")
        db.documents.documents[0]["lifecycle_state"] = "deleted"
        after_block = await client.get(
            "/api/entities/bank_guarantee_event/event-extension/document-links"
        )
        db.documents.documents[0]["lifecycle_state"] = "active"
        removed = await client.post(
            f"/api/document-links/{first_link['_id']}:remove",
            json={"reason": "Superseded supporter", "expected_revision": 1},
        )
        after_remove = await client.get(
            "/api/entities/bank_guarantee_event/event-extension/document-links"
        )

    assert linked.status_code == 201, linked.text
    assert removed.status_code == 200, removed.text
    assert [item["document_id"] for item in after_remove.json()["links"]] == ["doc-2"]
    assert [item["document_id"] for item in after_block.json()["links"]] == ["doc-2"]
    link_audit = next(
        event for event in db.audit_events.documents
        if event.get("action") == "document_relationship.linked"
    )
    assert link_audit["after"]["target_id"] == "event-extension"
    assert link_audit["after"]["parent_id"] == "bg-1"
    assert link_audit["after"]["document_id"] in {"doc-1", "doc-2"}
    assert link_audit["after"]["relationship_role"] == "supporting_document"
    assert link_audit["organization_id"] == "org-1"
    assert link_audit["project_id"] == "project-1"
    unlink_audit = next(
        event for event in db.audit_events.documents
        if event.get("action") == "document_relationship.unlinked"
    )
    assert unlink_audit["reason"] == "Superseded supporter"


@pytest.mark.asyncio
async def test_parent_delete_is_blocked_once_immutable_event_history_exists() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantee_events.documents.append(
        {
            "_id": "event-extension",
            "bank_guarantee_id": "bg-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "event_type": "extension",
            "sequence": 1,
            "created_at": datetime(2026, 8, 20),
        }
    )
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/bank-guarantees/bg-1")

    assert response.status_code == 409, response.text
    assert len(db.bank_guarantees.documents) == 1
    assert len(db.bank_guarantee_events.documents) == 1
    assert len(db.documents.documents) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_source", ["parent_array", "extension_history"])
async def test_parent_delete_is_blocked_by_unmigrated_legacy_evidence_or_history(
    legacy_source: str,
) -> None:
    db = _BankGuaranteeDatabase()
    if legacy_source == "parent_array":
        db.bank_guarantees.documents[0]["linked_document_ids"] = ["doc-1"]
    else:
        db.bg_extension_history.documents.append(
            {
                "_id": "history-1",
                "bg_id": "bg-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "revision_number": 1,
                "new_expiry_date": datetime(2027, 1, 1),
            }
        )
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/bank-guarantees/bg-1")

    assert response.status_code == 409, response.text
    assert len(db.bank_guarantees.documents) == 1
    assert len(db.documents.documents) == 1


@pytest.mark.asyncio
async def test_bg_api_merges_only_currently_authorized_event_and_legacy_documents() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0]["linked_document_ids"] = ["doc-1"]
    db.documents.documents.append(
        {
            "_id": "doc-2",
            "filename": "Event.pdf",
            "organization_id": "org-1",
            "project_id": "project-1",
            "processing_status": "metadata_extracted",
            "lifecycle_state": "active",
        }
    )
    db.bank_guarantee_events.documents.append(
        {
            "_id": "event-extension",
            "bank_guarantee_id": "bg-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "event_type": "extension",
            "sequence": 1,
            "created_at": datetime(2026, 8, 20),
        }
    )
    db.entity_document_links.documents.append(
        {
            "_id": "link-event",
            "organization_id": "org-1",
            "project_id": "project-1",
            "target_type": "bank_guarantee_event",
            "target_id": "event-extension",
            "parent_type": "bank_guarantee",
            "parent_id": "bg-1",
            "document_id": "doc-2",
            "relationship_role": "extension",
            "source": "user",
            "created_at": datetime(2026, 8, 20),
            "removed_at": None,
            "_revision": 1,
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        visible = await client.get("/api/bank-guarantees/bg-1")
        db.documents.documents[0]["lifecycle_state"] = "deleted"
        legacy_blocked = await client.get("/api/bank-guarantees/bg-1")
        db.documents.documents[1]["processing_status"] = "human_review_required"
        all_blocked = await client.get("/api/bank-guarantees/bg-1")

    assert visible.json()["linked_document_ids"] == ["doc-2", "doc-1"]
    assert legacy_blocked.json()["linked_document_ids"] == ["doc-2"]
    assert all_blocked.json()["linked_document_ids"] == []


@pytest.mark.asyncio
async def test_generic_update_cannot_bypass_extension_or_release_event_workflows() -> None:
    db = _BankGuaranteeDatabase()
    before = dict(db.bank_guarantees.documents[0])
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/bank-guarantees/bg-1",
            json={
                "bg_status": "released",
                "bg_expiry_date": "2030-01-01T00:00:00Z",
                "claim_expiry_date": "2030-02-01T00:00:00Z",
                "bg_amount": 1,
            },
        )

    assert response.status_code == 409, response.text
    assert db.bank_guarantees.documents[0] == before
    assert db.bank_guarantee_events.documents == []


@pytest.mark.asyncio
async def test_generic_update_cannot_overwrite_contract_master_required_date_projection() -> None:
    db = _BankGuaranteeDatabase()
    before = dict(db.bank_guarantees.documents[0])
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/bank-guarantees/bg-1",
            json={"contractual_required_up_to": "2030-01-01T00:00:00Z"},
        )

    assert response.status_code == 409, response.text
    assert db.bank_guarantees.documents[0] == before
    assert db.bank_guarantee_events.documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_status", ["extended", "released", "encashed"])
async def test_create_rejects_status_that_requires_missing_lifecycle_history(
    initial_status: str,
) -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents.clear()
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/bank-guarantees",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "bg_number": "BG-BYPASS",
                "bg_status": initial_status,
            },
        )

    assert response.status_code == 409, response.text
    assert db.bank_guarantees.documents == []
    assert db.bank_guarantee_events.documents == []


@pytest.mark.asyncio
async def test_terminal_statuses_reject_extension_and_release_without_new_history() -> None:
    db = _BankGuaranteeDatabase()
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        db.bank_guarantees.documents[0]["bg_status"] = "released"
        extension = await client.post(
            "/api/bank-guarantees/bg-1/extend",
            json={"revised_expiry_date": "2030-01-01T00:00:00Z"},
        )
        db.bank_guarantees.documents[0]["bg_status"] = "encashed"
        release = await client.post(
            "/api/bank-guarantees/bg-1/release",
            json={"release_date": "2026-08-21T00:00:00Z"},
        )

    assert extension.status_code == 409, extension.text
    assert release.status_code == 409, release.text
    assert db.bank_guarantee_events.documents == []
    assert db.bg_extension_history.documents == []


@pytest.mark.asyncio
async def test_event_and_extension_history_reads_recheck_parent_scope() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantee_events.documents.extend(
        [
            {
                "_id": "event-local",
                "bank_guarantee_id": "bg-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "event_type": "extension",
                "sequence": 1,
                "created_at": datetime(2026, 8, 20),
            },
            {
                "_id": "event-forged",
                "bank_guarantee_id": "bg-1",
                "organization_id": "foreign-org",
                "project_id": "foreign-project",
                "event_type": "release",
                "sequence": 2,
                "created_at": datetime(2026, 8, 21),
            },
        ]
    )
    db.bg_extension_history.documents.extend(
        [
            {
                "_id": "history-local",
                "bg_id": "bg-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "revision_number": 1,
                "new_expiry_date": datetime(2027, 1, 1),
            },
            {
                "_id": "history-forged",
                "bg_id": "bg-1",
                "organization_id": "foreign-org",
                "project_id": "foreign-project",
                "revision_number": 2,
                "new_expiry_date": datetime(2028, 1, 1),
            },
        ]
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        events = await client.get("/api/bank-guarantees/bg-1/events")
        history = await client.get("/api/bank-guarantees/bg-1/history")

    assert events.status_code == 200, events.text
    assert [row["_id"] for row in events.json()] == ["event-local"]
    assert history.status_code == 200, history.text
    assert [row["_id"] for row in history.json()] == ["history-local"]


@pytest.mark.asyncio
async def test_legacy_extension_history_without_scope_inherits_authorized_parent_scope() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0]["current_revision"] = 2
    db.bg_extension_history.documents.extend(
        [
            {
                "_id": "history-scoped",
                "bg_id": "bg-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "revision_number": 1,
            },
            {
                "_id": "history-legacy-unscoped",
                "bg_id": "bg-1",
                "revision_number": 2,
            },
            {
                "_id": "history-forged",
                "bg_id": "bg-1",
                "organization_id": "foreign-org",
                "project_id": "foreign-project",
                "revision_number": 3,
            },
        ]
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/bank-guarantees/bg-1/history")

    assert response.status_code == 200, response.text
    assert [row["_id"] for row in response.json()] == [
        "history-scoped",
        "history-legacy-unscoped",
    ]


@pytest.mark.asyncio
async def test_ambiguous_or_malformed_unscoped_extension_history_fails_closed() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0]["current_revision"] = 3
    db.bg_extension_history.documents.extend(
        [
            {
                "_id": "history-ambiguous-a",
                "bg_id": "bg-1",
                "revision_number": 2,
            },
            {
                "_id": "history-ambiguous-b",
                "bg_id": "bg-1",
                "revision_number": 2,
            },
            {
                "_id": "history-malformed",
                "bg_id": "bg-1",
            },
        ]
    )
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/bank-guarantees/bg-1/history")

    assert response.status_code == 200, response.text
    assert response.json() == []


@pytest.mark.asyncio
async def test_orphan_legacy_extension_history_cannot_resolve_without_parent() -> None:
    db = _BankGuaranteeDatabase()
    db.bg_extension_history.documents.append(
        {
            "_id": "history-orphan",
            "bg_id": "missing-bg",
            "revision_number": 1,
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/bank-guarantees/missing-bg/history")

    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_unscoped_legacy_history_cannot_self_authorize() -> None:
    class _DenyParentPolicy:
        async def authorize_document(self, *_args: Any, **_kwargs: Any) -> None:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Parent scope denied")

    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0]["current_revision"] = 1
    db.bg_extension_history.documents.append(
        {
            "_id": "history-unscoped",
            "bg_id": "bg-1",
            "revision_number": 1,
        }
    )
    transport = httpx.ASGITransport(app=_app(db, bg_policy=_DenyParentPolicy()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/bank-guarantees/bg-1/history")

    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_explicit_status_transition_creates_submission_event_then_activates() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0].update(
        {"bg_status": "draft", "event_sequence": 1}
    )
    db.bank_guarantee_events.documents.append(
        {
            "_id": "event-original",
            "bank_guarantee_id": "bg-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "event_type": "original",
            "sequence": 1,
            "created_at": datetime(2026, 8, 20),
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        submitted = await client.post(
            "/api/bank-guarantees/bg-1/status",
            json={
                "target_status": "submitted",
                "submission_date": "2026-08-21T00:00:00Z",
                "reference": "SUB/2026/01",
                "remarks": "Submitted to the employer",
            },
        )
        activated = await client.post(
            "/api/bank-guarantees/bg-1/status",
            json={"target_status": "valid"},
        )

    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["bg_status"] == "submitted"
    assert activated.status_code == 200, activated.text
    assert activated.json()["bg_status"] == "valid"
    assert [row["event_type"] for row in db.bank_guarantee_events.documents] == [
        "original",
        "submission",
    ]
    submission = db.bank_guarantee_events.documents[1]
    assert submission["sequence"] == 2
    assert submission["event_date"].isoformat() == "2026-08-21T00:00:00+00:00"
    assert submission["reference"] == "SUB/2026/01"
    assert submission["remarks"] == "Submitted to the employer"
    assert {
        row["action"] for row in db.audit_events.documents
    } >= {"bank_guarantee.submitted", "bank_guarantee.activated"}
    lifecycle_audits = [
        row for row in db.audit_events.documents
        if row["action"] in {"bank_guarantee.submitted", "bank_guarantee.activated"}
    ]
    assert {row["actor_id"] for row in lifecycle_audits} == {"user-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        ("draft", "valid"),
        ("valid", "submitted"),
        ("valid", "draft"),
        ("released", "valid"),
    ],
)
async def test_explicit_status_transition_rejects_unsupported_lifecycle_edges(
    current_status: str, target_status: str
) -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0]["bg_status"] = current_status
    before = deepcopy(db.bank_guarantees.documents[0])
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/bank-guarantees/bg-1/status",
            json={"target_status": target_status},
        )

    assert response.status_code == 409, response.text
    assert db.bank_guarantees.documents[0] == before
    assert db.bank_guarantee_events.documents == []
    assert db.audit_events.documents == []


@pytest.mark.asyncio
async def test_status_transition_requires_parent_edit_authority() -> None:
    class _DenyEditPolicy:
        async def authorize_document(self, *_args: Any, **_kwargs: Any) -> None:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="BG edit denied")

    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0]["bg_status"] = "draft"
    before = deepcopy(db.bank_guarantees.documents[0])
    transport = httpx.ASGITransport(app=_app(db, bg_policy=_DenyEditPolicy()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/bank-guarantees/bg-1/status",
            json={"target_status": "submitted"},
        )

    assert response.status_code == 403, response.text
    assert db.bank_guarantees.documents[0] == before
    assert db.bank_guarantee_events.documents == []
    assert db.audit_events.documents == []


@pytest.mark.asyncio
async def test_concurrent_status_transition_loser_has_no_partial_side_effects() -> None:
    db = _BankGuaranteeDatabase()
    db.bank_guarantees.documents[0]["bg_status"] = "draft"

    async def lose_update(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(matched_count=0, modified_count=0)

    db.bank_guarantees.update_one = lose_update  # type: ignore[method-assign]
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/bank-guarantees/bg-1/status",
            json={"target_status": "submitted"},
        )

    assert response.status_code == 409, response.text
    assert db.bank_guarantee_events.documents == []
    assert db.audit_events.documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["extend", "release"])
async def test_concurrent_lifecycle_loser_returns_controlled_conflict(operation: str) -> None:
    db = _BankGuaranteeDatabase()

    async def lose_update(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(matched_count=0, modified_count=0)

    db.bank_guarantees.update_one = lose_update  # type: ignore[method-assign]
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        if operation == "extend":
            response = await client.post(
                "/api/bank-guarantees/bg-1/extend",
                json={"revised_expiry_date": "2030-01-01T00:00:00Z"},
            )
        else:
            response = await client.post(
                "/api/bank-guarantees/bg-1/release",
                json={"release_date": "2026-08-21T00:00:00Z"},
            )

    assert response.status_code == 409, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("details", "expected_status"),
    [
        (
            {
                "index": "uq_bank_guarantee_event_sequence",
                "keyPattern": {
                    "organization_id": 1,
                    "project_id": 1,
                    "bank_guarantee_id": 1,
                    "sequence": 1,
                },
            },
            409,
        ),
        ({"index": "_id_", "keyPattern": {"_id": 1}}, 500),
    ],
)
async def test_lifecycle_duplicate_translation_only_handles_event_sequence_races(
    details: dict[str, Any], expected_status: int
) -> None:
    db = _BankGuaranteeDatabase()

    async def duplicate_event(*_args: Any, **_kwargs: Any) -> Any:
        raise DuplicateKeyError("duplicate key", 11000, details)

    db.bank_guarantee_events.insert_one = duplicate_event  # type: ignore[method-assign]
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/bank-guarantees/bg-1/extend",
            json={"revised_expiry_date": "2030-01-01T00:00:00Z"},
        )

    assert response.status_code == expected_status, response.text
