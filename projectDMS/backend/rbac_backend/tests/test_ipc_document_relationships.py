from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from rbac_backend.core.database import get_db
from rbac_backend.core.permissions import Permissions
from rbac_backend.core.security import get_current_user
from rbac_backend.models.ipc_bill import IPCBillCreate
from rbac_backend.routers.document_relationships import router as relationship_router
from rbac_backend.routers.document_relationships import get_document_relationship_service
from rbac_backend.routers.ipc_bills import get_policy, router as ipc_router
from rbac_backend.services.document_relationship_service import DocumentRelationshipService
from rbac_backend.services.ipc_bill_service import IPCBillService
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.tests.test_claim_document_relationships import (
    _Client,
    _Collection,
    _Database,
    _PermissionPolicy,
    _permission_user,
    _user,
)


IPC_DOCUMENT_ROLES = (
    "ipc_submission",
    "certified_ipc",
    "invoice",
    "payment_certificate",
    "supporting_document",
    "payment_correspondence",
)


class _IPCDatabase(_Database):
    def __init__(self) -> None:
        super().__init__()
        self.ipc_bills = _Collection(
            "ipc_bills",
            [
                {
                    "_id": "ipc-1",
                    "ipc_number": "IPC-001",
                    "status": "submitted",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "linked_document_ids": [],
                    "line_items": [],
                    "deductions": {},
                    "payments": [],
                    "revisions": [],
                    "current_revision": 0,
                }
            ],
        )
        self.contract_master = _Collection("contract_master")


def _app(db: _IPCDatabase, *, policy: Any = None, user: Any = None) -> FastAPI:
    app = FastAPI()
    app.include_router(relationship_router, prefix="/api")
    app.include_router(ipc_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = user or _user
    if policy is not None:
        app.dependency_overrides[get_policy] = lambda: policy
        app.dependency_overrides[get_document_relationship_service] = lambda: DocumentRelationshipService(
            db, policy=policy
        )
    else:
        app.dependency_overrides[get_policy] = lambda: PolicyService(db=db)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("relationship_role", IPC_DOCUMENT_ROLES)
async def test_ipc_native_links_accept_only_the_verified_ipc_role_vocabulary(
    relationship_role: str,
) -> None:
    db = _IPCDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": relationship_role}]},
        )
        forward = await client.get("/api/entities/ipc_bill/ipc-1/document-links")
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert linked.status_code == 201, linked.text
    assert [row["relationship_role"] for row in forward.json()["links"]] == [relationship_role]
    assert [row["target_id"] for row in reverse.json()["links"]] == ["ipc-1"]
    assert reverse.json()["links"][0]["target_route"] == "/ipc-bills?ipc_id=ipc-1"


@pytest.mark.asyncio
async def test_ipc_native_link_rejects_unknown_role_and_cross_scope_document() -> None:
    db = _IPCDatabase()
    db.documents.documents.append(
        {
            "_id": "foreign-doc",
            "organization_id": "org-2",
            "project_id": "project-2",
            "processing_status": "metadata_extracted",
            "lifecycle_state": "active",
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bad_role = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
        foreign = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={"links": [{"document_id": "foreign-doc", "relationship_role": "invoice"}]},
        )

    assert bad_role.status_code == 422, bad_role.text
    assert foreign.status_code == 403, foreign.text
    assert db.entity_document_links.documents == []


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
        ({}, True, 404),
    ],
)
async def test_ipc_document_authority_matrix(
    document_changes: dict[str, Any], missing: bool, expected_status: int
) -> None:
    db = _IPCDatabase()
    if missing:
        db.documents.documents.clear()
    else:
        db.documents.documents[0].update(document_changes)
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "invoice"}]},
        )

    assert response.status_code == expected_status, response.text
    assert len(db.entity_document_links.documents) == (1 if expected_status == 201 else 0)


@pytest.mark.asyncio
async def test_ipc_server_derives_scope_and_rejects_each_foreign_scope_axis() -> None:
    db = _IPCDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        canonical = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={
                "organization_id": "attacker-org",
                "project_id": "attacker-project",
                "links": [{"document_id": "doc-1", "relationship_role": "invoice"}],
            },
        )
        db.documents.documents[0]["_id"] = "cross-org"
        db.documents.documents[0]["organization_id"] = "org-2"
        cross_org = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={"links": [{"document_id": "cross-org", "relationship_role": "invoice"}]},
        )
        db.documents.documents[0]["_id"] = "cross-project"
        db.documents.documents[0]["organization_id"] = "org-1"
        db.documents.documents[0]["project_id"] = "project-2"
        cross_project = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={"links": [{"document_id": "cross-project", "relationship_role": "invoice"}]},
        )

    assert canonical.status_code == 201, canonical.text
    assert db.entity_document_links.documents[0]["organization_id"] == "org-1"
    assert db.entity_document_links.documents[0]["project_id"] == "project-1"
    assert cross_org.status_code == 403, cross_org.text
    assert cross_project.status_code == 403, cross_project.text


@pytest.mark.asyncio
async def test_ipc_roles_supporters_unlink_and_forward_reverse_parity() -> None:
    db = _IPCDatabase()
    db.documents.documents.append(
        {
            "_id": "doc-2",
            "filename": "Certificate.pdf",
            "organization_id": "org-1",
            "project_id": "project-1",
            "processing_status": "metadata_extracted",
            "lifecycle_state": "active",
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={
                "links": [
                    {"document_id": "doc-1", "relationship_role": "invoice"},
                    {"document_id": "doc-1", "relationship_role": "supporting_document"},
                    {"document_id": "doc-2", "relationship_role": "payment_certificate"},
                ]
            },
        )
        links = created.json()["links"]
        forward = await client.get("/api/entities/ipc_bill/ipc-1/document-links")
        reverse = await client.get("/api/documents/doc-1/entity-links")
        invoice = next(row for row in links if row["relationship_role"] == "invoice")
        removed = await client.post(
            f"/api/document-links/{invoice['_id']}:remove",
            json={"reason": "Invoice replaced", "expected_revision": 1},
        )
        after_unlink = await client.get("/api/entities/ipc_bill/ipc-1/document-links")
        db.documents.documents[0]["processing_status"] = "human_review_required"
        after_block = await client.get("/api/entities/ipc_bill/ipc-1/document-links")

    assert created.status_code == 201, created.text
    assert len(links) == 3
    assert {row["relationship_role"] for row in reverse.json()["links"]} == {
        "invoice", "supporting_document"
    }
    forward_by_id = {row["_id"]: row for row in forward.json()["links"]}
    for row in reverse.json()["links"]:
        peer = forward_by_id[row["_id"]]
        assert (peer["document_id"], peer["target_type"], peer["target_id"], peer["relationship_role"]) == (
            row["document_id"], row["target_type"], row["target_id"], row["relationship_role"]
        )
        assert peer["removed_at"] is None and row["removed_at"] is None
        assert peer["document_version_id"] == row["document_version_id"]
    assert removed.status_code == 200, removed.text
    assert removed.json()["link"]["_revision"] == 2
    assert removed.json()["link"]["removed_at"] is not None
    assert {row["relationship_role"] for row in after_unlink.json()["links"]} == {
        "supporting_document", "payment_certificate"
    }
    assert [row["document_id"] for row in after_block.json()["links"]] == ["doc-2"]
    unlink_audits = [
        event for event in db.audit_events.documents
        if event.get("action") == "document_relationship.unlinked"
    ]
    assert len(unlink_audits) == 1
    assert unlink_audits[0]["resource_id"] == invoice["_id"]


@pytest.mark.asyncio
async def test_ipc_link_requires_ipc_edit_and_document_view() -> None:
    for permissions in (
        (Permissions.IPC_EDIT,),
        (Permissions.DOCUMENT_VIEW,),
        (Permissions.IPC_VIEW, Permissions.DOCUMENT_VIEW),
    ):
        db = _IPCDatabase()
        user = _permission_user(*permissions)
        policy = _PermissionPolicy()
        transport = httpx.ASGITransport(app=_app(db, policy=policy, user=lambda: user))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/entities/ipc_bill/ipc-1/document-links:batch",
                json={"links": [{"document_id": "doc-1", "relationship_role": "invoice"}]},
            )
        assert response.status_code == 403, response.text
        assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_concurrent_identical_ipc_links_are_idempotent_and_audited_once() -> None:
    db = _IPCDatabase()
    service = DocumentRelationshipService(db)
    actor = _user()

    from rbac_backend.models.document_relationship import DocumentRelationshipInput

    request = [DocumentRelationshipInput(document_id="doc-1", relationship_role="invoice")]
    first, second = await asyncio.gather(
        service.link_batch(actor, "ipc_bill", "ipc-1", request, idempotency_key="ipc-link-1"),
        service.link_batch(actor, "ipc_bill", "ipc-1", request, idempotency_key="ipc-link-1"),
    )

    assert first[0].id == second[0].id
    assert len(db.entity_document_links.documents) == 1
    assert len(
        [event for event in db.audit_events.documents if event.get("action") == "document_relationship.linked"]
    ) == 1


@pytest.mark.asyncio
async def test_ipc_legacy_writes_are_rejected_for_manual_review_without_mutation() -> None:
    db = _IPCDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/ipc-bills",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "ipc_number": "IPC-LEGACY",
                "linked_document_ids": ["doc-1"],
            },
        )
        updated = await client.put(
            "/api/ipc-bills/ipc-1",
            json={"linked_document_ids": ["doc-1"]},
        )

    assert created.status_code == 409, created.text
    assert updated.status_code == 409, updated.text
    assert len(db.ipc_bills.documents) == 1
    assert db.ipc_bills.documents[0]["linked_document_ids"] == []
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_empty_legacy_array_on_ipc_create_is_a_compatible_noop() -> None:
    db = _IPCDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/ipc-bills",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "ipc_number": "IPC-EMPTY-LEGACY",
                "linked_document_ids": [],
            },
        )

    assert created.status_code == 201, created.text
    assert len(db.ipc_bills.documents) == 2
    assert "linked_document_ids" not in db.ipc_bills.documents[-1]
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_ipc_service_rejects_ambiguous_legacy_intent_instead_of_dropping_it() -> None:
    db = _IPCDatabase()
    service = IPCBillService(db)
    actor = _user()

    with pytest.raises(ValueError, match="manual review"):
        await service.create(
            IPCBillCreate(
                project_id="project-1",
                organization_id="org-1",
                ipc_number="IPC-DIRECT-LEGACY",
                linked_document_ids=["doc-1"],
            ),
            actor,
        )
    with pytest.raises(ValueError, match="manual review"):
        await service.update(
            db.ipc_bills.documents[0],
            {"linked_document_ids": []},
            actor,
        )

    assert len(db.ipc_bills.documents) == 1
    assert db.ipc_bills.documents[0]["linked_document_ids"] == []


@pytest.mark.asyncio
async def test_ipc_service_delete_delegates_to_relationship_cleanup() -> None:
    db = _IPCDatabase()
    db.client = _Client()
    actor = _user()
    from rbac_backend.models.document_relationship import DocumentRelationshipInput

    await DocumentRelationshipService(db).link_batch(
        actor,
        "ipc_bill",
        "ipc-1",
        [DocumentRelationshipInput(document_id="doc-1", relationship_role="invoice")],
    )
    deleted = await IPCBillService(db).delete(db.ipc_bills.documents[0], actor)

    assert deleted is True
    assert db.ipc_bills.documents == []
    assert db.entity_document_links.documents[0]["removed_at"] is not None
    actions = [row.get("action") for row in db.audit_events.documents]
    assert actions.count("document_relationship.unlinked") == 1
    assert actions.count("ipc_bill.deleted") == 1


@pytest.mark.asyncio
async def test_ipc_legacy_read_through_rechecks_current_document_authority() -> None:
    db = _IPCDatabase()
    db.ipc_bills.documents[0]["linked_document_ids"] = ["doc-1"]
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        visible = await client.get("/api/ipc-bills/ipc-1")
        visible_list = await client.get("/api/ipc-bills")
        visible_forward = await client.get("/api/entities/ipc_bill/ipc-1/document-links")
        visible_reverse = await client.get("/api/documents/doc-1/entity-links")
        db.documents.documents[0]["lifecycle_state"] = "deleted"
        blocked = await client.get("/api/ipc-bills/ipc-1")
        blocked_reverse = await client.get("/api/documents/doc-1/entity-links")

    assert visible.status_code == 200, visible.text
    assert visible.json()["linked_document_ids"] == ["doc-1"]
    assert visible_list.json()[0]["linked_document_ids"] == ["doc-1"]
    assert visible_forward.json()["links"][0]["source"] == "legacy_read_through"
    assert visible_forward.json()["links"][0]["relationship_role"] == "manual_review"
    assert visible_reverse.json()["links"][0]["relationship_role"] == "manual_review"
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["linked_document_ids"] == []
    assert blocked_reverse.status_code == 200, blocked_reverse.text
    assert blocked_reverse.json()["links"] == []


@pytest.mark.asyncio
async def test_ipc_has_no_invented_freeze_contract() -> None:
    db = _IPCDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "invoice"}]},
        )
        db.document_versions.documents.clear()
        response = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:freeze",
            json={"reason": "not a supported IPC lifecycle operation"},
        )

    assert linked.status_code == 201, linked.text
    assert response.status_code == 409, response.text
    assert "not supported" in response.text
    assert db.ipc_bills.documents[0].get("evidence_frozen_at") is None
    assert db.entity_document_links.documents[0].get("frozen_at") is None


@pytest.mark.asyncio
async def test_deleting_ipc_cleans_relationships_and_audits_in_one_transaction() -> None:
    db = _IPCDatabase()
    db.client = _Client()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/ipc_bill/ipc-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "invoice"}]},
        )
        db.entity_document_links.sessions.clear()
        db.audit_events.sessions.clear()
        deleted = await client.delete("/api/ipc-bills/ipc-1")

    assert linked.status_code == 201, linked.text
    assert deleted.status_code == 204, deleted.text
    assert db.ipc_bills.documents == []
    assert db.entity_document_links.documents[0]["removed_at"] is not None
    assert db.entity_document_links.sessions == [db.client.session]
    actions = [row.get("action") for row in db.audit_events.documents]
    assert "document_relationship.unlinked" in actions
    assert "ipc_bill.deleted" in actions
