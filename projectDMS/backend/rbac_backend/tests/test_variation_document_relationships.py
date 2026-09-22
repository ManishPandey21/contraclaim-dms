"""CL-2: Variation on the canonical ``entity_document_links`` framework.

Always-on fake-store guards. The real-Mongo / real-RBAC proof of the same
behaviour lives in ``tests/integration/test_variation_relationships_cl2_mongo.py``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from rbac_backend.core.database import get_db
from rbac_backend.core.permissions import Permissions
from rbac_backend.core.security import get_current_user
from rbac_backend.models.document_relationship import DocumentRelationshipInput
from rbac_backend.models.variation import VariationCreate
from rbac_backend.routers.document_relationships import (
    get_document_relationship_service,
    router as relationship_router,
)
from rbac_backend.routers.variations import get_policy, router as variation_router
from rbac_backend.services.document_relationship_service import DocumentRelationshipService
from rbac_backend.services.entity_adapter_registry import (
    CORRESPONDENCE_ROLES,
    VARIATION_DOCUMENT_ROLES,
    EntityAdapterRegistry,
    VariationEntityAdapter,
)
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.variation_service import (
    AmbiguousLegacyVariationRelationshipError,
    VariationService,
)
from rbac_backend.tests.test_claim_document_relationships import (
    _Client,
    _Collection,
    _Database,
    _PermissionPolicy,
    _permission_user,
    _user,
)


def _document(_id: str, *, org: str = "org-1", project: str = "project-1", upload_type: str | None = "incoming") -> dict[str, Any]:
    row: dict[str, Any] = {
        "_id": _id,
        "filename": f"{_id}.pdf",
        "subject": f"Subject {_id}",
        "organization_id": org,
        "project_id": project,
        "processing_status": "metadata_extracted",
        "lifecycle_state": "active",
        "current_version_id": f"{_id}-v1",
    }
    if upload_type is not None:
        row["uploadType"] = upload_type
    return row


class _VariationDatabase(_Database):
    def __init__(self) -> None:
        super().__init__()
        self.documents.documents.extend(
            [
                _document("doc-out", upload_type="outgoing"),
                _document("doc-contract", upload_type="contract"),
                _document("doc-foreign-org", org="org-2", project="project-9"),
                _document("doc-foreign-project", project="project-2"),
            ]
        )
        self.variations = _Collection(
            "variations",
            [
                {
                    "_id": "var-1",
                    "variation_number": "VO-001",
                    "variation_type": "positive",
                    "status": "submitted",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "contract_id": "primary",
                    "currency_amounts": [],
                    "linked_document_ids": [],
                }
            ],
        )
        self.contract_master = _Collection("contract_master")


def _app(db: _VariationDatabase, *, policy: Any = None, user: Any = None) -> FastAPI:
    app = FastAPI()
    app.include_router(relationship_router, prefix="/api")
    app.include_router(variation_router, prefix="/api")
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


async def _client(db: _VariationDatabase, **kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(db, **kwargs)), base_url="http://test")


def _audits(db: _VariationDatabase, action: str) -> list[dict[str, Any]]:
    return [row for row in db.audit_events.documents if row.get("action") == action]


# --------------------------------------------------------------------------- #
# 1. Adapter
# --------------------------------------------------------------------------- #


def test_variation_is_a_registered_target_with_a_workflow_derived_vocabulary() -> None:
    adapter = EntityAdapterRegistry().get("variation")
    assert isinstance(adapter, VariationEntityAdapter)
    # Every role is backed by the current model: the submission (submitted
    # amount / status submitted), the approval (approved amount / approval date),
    # generic correspondence, and the generic supporting-document role.
    assert VARIATION_DOCUMENT_ROLES == frozenset(
        {"variation_submission", "variation_approval", "correspondence", "supporting_document"}
    )
    # A correspondence role is offered only alongside the generic fallback the
    # 422 message points to.
    assert VARIATION_DOCUMENT_ROLES & CORRESPONDENCE_ROLES == {"correspondence"}
    assert adapter.supports_freeze is False
    assert adapter.legacy_relationship_role == "manual_review"


def test_variation_context_carries_canonical_scope_permissions_label_and_deep_link() -> None:
    context = VariationEntityAdapter().context_from_entity(
        {
            "_id": "var-1",
            "variation_number": "VO-001",
            "organization_id": "org-1",
            "project_id": "project-1",
        }
    )
    assert context.target_type == "variation"
    assert (context.organization_id, context.project_id) == ("org-1", "project-1")
    assert context.view_permission == Permissions.VARIATION_VIEW
    assert context.manage_permission == Permissions.VARIATION_EDIT
    assert context.delete_permission == Permissions.VARIATION_DELETE
    assert context.label == "VO-001"
    assert context.route == "/variations?variation_id=var-1"
    assert context.frozen is False
    assert VariationEntityAdapter().context_from_entity({"_id": "var-x"}).label == "var-x"


@pytest.mark.asyncio
async def test_variation_link_forward_reverse_relink_unlink_round_trip() -> None:
    db = _VariationDatabase()
    async with await _client(db) as client:
        linked = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={
                "links": [
                    {"document_id": "doc-1", "relationship_role": "correspondence"},
                    {"document_id": "doc-out", "relationship_role": "correspondence"},
                ]
            },
        )
        relinked = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "correspondence"}]},
        )
        forward = await client.get("/api/entities/variation/var-1/document-links")
        reverse = await client.get("/api/documents/doc-1/entity-links")
        link = next(row for row in forward.json()["links"] if row["document_id"] == "doc-1")
        removed = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "wrong letter", "expected_revision": link["_revision"]},
        )
        after = await client.get("/api/entities/variation/var-1/document-links")
        reverse_after = await client.get("/api/documents/doc-1/entity-links")

    assert linked.status_code == 201, linked.text
    assert relinked.status_code == 201, relinked.text
    assert relinked.json()["links"][0]["_id"] == link["_id"]
    # one Variation, many letters (incoming AND outgoing)
    assert sorted(row["document_id"] for row in forward.json()["links"]) == ["doc-1", "doc-out"]
    assert reverse.json()["links"][0]["target_type"] == "variation"
    assert reverse.json()["links"][0]["target_label"] == "VO-001"
    assert reverse.json()["links"][0]["target_route"] == "/variations?variation_id=var-1"
    assert removed.status_code == 200, removed.text
    assert [row["document_id"] for row in after.json()["links"]] == ["doc-out"]
    assert reverse_after.json()["links"] == []
    # unlink removed the relationship only
    assert any(row["_id"] == "doc-1" for row in db.documents.documents)
    # exactly once each; the idempotent re-link audited nothing
    assert len(_audits(db, "document_relationship.linked")) == 2
    assert len(_audits(db, "document_relationship.unlinked")) == 1
    unlinked = _audits(db, "document_relationship.unlinked")[0]
    assert unlinked["metadata"]["target_type"] == "variation"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", sorted(VARIATION_DOCUMENT_ROLES))
async def test_every_variation_role_accepts_correspondence(role: str) -> None:
    db = _VariationDatabase()
    async with await _client(db) as client:
        response = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": role}]},
        )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_correspondence_role_refuses_contract_document_but_supporting_document_accepts_it() -> None:
    db = _VariationDatabase()
    async with await _client(db) as client:
        refused = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={"links": [{"document_id": "doc-contract", "relationship_role": "correspondence"}]},
        )
        # Not over-filtered: the generic and workflow roles make no claim about
        # the Document's type.
        supporting = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={"links": [{"document_id": "doc-contract", "relationship_role": "supporting_document"}]},
        )
        approval = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={"links": [{"document_id": "doc-contract", "relationship_role": "variation_approval"}]},
        )
        unknown = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
    assert refused.status_code == 422, refused.text
    assert supporting.status_code == 201, supporting.text
    assert approval.status_code == 201, approval.text
    assert unknown.status_code == 422, unknown.text


@pytest.mark.asyncio
async def test_canonical_link_refuses_foreign_org_and_foreign_project_documents() -> None:
    db = _VariationDatabase()
    async with await _client(db) as client:
        foreign_org = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={"links": [{"document_id": "doc-foreign-org", "relationship_role": "correspondence"}]},
        )
        foreign_project = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={"links": [{"document_id": "doc-foreign-project", "relationship_role": "correspondence"}]},
        )
    assert foreign_org.status_code == 403, foreign_org.text
    assert foreign_project.status_code == 403, foreign_project.text
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_variation_link_requires_variation_edit_and_document_view() -> None:
    for permissions in (
        (Permissions.VARIATION_EDIT,),
        (Permissions.DOCUMENT_VIEW,),
        (Permissions.VARIATION_VIEW, Permissions.DOCUMENT_VIEW),
    ):
        db = _VariationDatabase()
        user = _permission_user(*permissions)
        async with await _client(db, policy=_PermissionPolicy(), user=lambda: user) as client:
            response = await client.post(
                "/api/entities/variation/var-1/document-links:batch",
                json={"links": [{"document_id": "doc-1", "relationship_role": "correspondence"}]},
            )
        assert response.status_code == 403, response.text
        assert db.entity_document_links.documents == []

    db = _VariationDatabase()
    user = _permission_user(Permissions.VARIATION_EDIT, Permissions.DOCUMENT_VIEW)
    async with await _client(db, policy=_PermissionPolicy(), user=lambda: user) as client:
        allowed = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "correspondence"}]},
        )
    assert allowed.status_code == 201, allowed.text


@pytest.mark.asyncio
async def test_variation_view_permission_alone_reads_but_cannot_write() -> None:
    db = _VariationDatabase()
    await DocumentRelationshipService(db).link_batch(
        _user(), "variation", "var-1",
        [DocumentRelationshipInput(document_id="doc-1", relationship_role="correspondence")],
    )
    user = _permission_user(Permissions.VARIATION_VIEW, Permissions.DOCUMENT_VIEW)
    async with await _client(db, policy=_PermissionPolicy(), user=lambda: user) as client:
        forward = await client.get("/api/entities/variation/var-1/document-links")
        link_id = forward.json()["links"][0]["_id"]
        removed = await client.post(
            f"/api/document-links/{link_id}:remove",
            json={"reason": "not allowed", "expected_revision": 1},
        )
    assert forward.status_code == 200, forward.text
    assert removed.status_code == 403, removed.text
    assert db.entity_document_links.documents[0]["removed_at"] is None


# --------------------------------------------------------------------------- #
# 2. Raw linked_document_ids writes (the CL-2 scope defect)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forged",
    [["doc-foreign-org"], ["doc-foreign-project"], ["doc-1"], ["doc-1", "doc-foreign-org"]],
)
async def test_put_refuses_raw_linked_document_ids_and_persists_nothing(forged: list[str]) -> None:
    db = _VariationDatabase()
    async with await _client(db) as client:
        response = await client.put(
            "/api/variations/var-1",
            json={"description": "sneaky", "linked_document_ids": forged},
        )
    assert response.status_code == 409, response.text
    assert "document-links" in response.json()["detail"]
    row = db.variations.documents[0]
    assert row["linked_document_ids"] == []
    assert "description" not in row or row.get("description") is None
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_put_clearing_a_legacy_array_is_refused_not_silently_applied() -> None:
    db = _VariationDatabase()
    db.variations.documents[0]["linked_document_ids"] = ["doc-1"]
    async with await _client(db) as client:
        response = await client.put("/api/variations/var-1", json={"linked_document_ids": []})
    assert response.status_code == 409, response.text
    assert db.variations.documents[0]["linked_document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_put_without_the_field_or_echoing_it_unchanged_is_compatible() -> None:
    db = _VariationDatabase()
    db.variations.documents[0]["linked_document_ids"] = ["doc-1", "doc-foreign-org"]
    async with await _client(db) as client:
        plain = await client.put("/api/variations/var-1", json={"remarks": "reviewed"})
        # Only doc-1 is presented to the caller; echoing that back is a no-op.
        echoed = await client.put(
            "/api/variations/var-1",
            json={"remarks": "again", "linked_document_ids": ["doc-1"]},
        )
    assert plain.status_code == 200, plain.text
    assert echoed.status_code == 200, echoed.text
    row = db.variations.documents[0]
    assert row["remarks"] == "again"
    # The legacy array is untouched: no data loss, and no silent rewrite.
    assert row["linked_document_ids"] == ["doc-1", "doc-foreign-org"]


@pytest.mark.asyncio
async def test_create_refuses_non_empty_raw_ids_and_accepts_an_empty_array() -> None:
    db = _VariationDatabase()
    async with await _client(db) as client:
        refused = await client.post(
            "/api/variations",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "variation_number": "VO-FORGED",
                "linked_document_ids": ["doc-foreign-org"],
            },
        )
        empty = await client.post(
            "/api/variations",
            json={
                "project_id": "project-1",
                "organization_id": "org-1",
                "variation_number": "VO-EMPTY",
                "linked_document_ids": [],
            },
        )
    assert refused.status_code == 409, refused.text
    assert empty.status_code == 201, empty.text
    assert [row["variation_number"] for row in db.variations.documents] == ["VO-001", "VO-EMPTY"]
    assert "linked_document_ids" not in db.variations.documents[-1]


@pytest.mark.asyncio
async def test_service_refuses_raw_ids_even_when_called_directly() -> None:
    db = _VariationDatabase()
    service = VariationService(db)
    with pytest.raises(AmbiguousLegacyVariationRelationshipError):
        await service.create(
            VariationCreate(project_id="project-1", organization_id="org-1", linked_document_ids=["doc-1"]),
            _user(),
        )
    with pytest.raises(AmbiguousLegacyVariationRelationshipError):
        await service.update(db.variations.documents[0], {"linked_document_ids": ["doc-1"]}, _user())
    assert len(db.variations.documents) == 1
    assert db.variations.documents[0]["linked_document_ids"] == []


# --------------------------------------------------------------------------- #
# 3. Legacy rows keep reading, authority-filtered
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_legacy_ids_read_through_filtered_by_current_authority() -> None:
    db = _VariationDatabase()
    db.variations.documents[0]["linked_document_ids"] = ["doc-1", "doc-foreign-org", "missing-doc"]
    async with await _client(db) as client:
        one = await client.get("/api/variations/var-1")
        many = await client.get("/api/variations")
        forward = await client.get("/api/entities/variation/var-1/document-links")
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert one.status_code == 200, one.text
    assert one.json()["linked_document_ids"] == ["doc-1"]
    assert many.json()[0]["linked_document_ids"] == ["doc-1"]
    assert [(row["document_id"], row["source"], row["relationship_role"]) for row in forward.json()["links"]] == [
        ("doc-1", "legacy_read_through", "manual_review")
    ]
    assert reverse.json()["links"][0]["target_type"] == "variation"
    assert reverse.json()["links"][0]["source"] == "legacy_read_through"
    # reading never rewrote the stored array
    assert db.variations.documents[0]["linked_document_ids"] == ["doc-1", "doc-foreign-org", "missing-doc"]


# --------------------------------------------------------------------------- #
# 4. Delete safety
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_deleting_variation_removes_links_through_delete_target_and_keeps_documents() -> None:
    db = _VariationDatabase()
    db.client = _Client()
    async with await _client(db) as client:
        linked = await client.post(
            "/api/entities/variation/var-1/document-links:batch",
            json={
                "links": [
                    {"document_id": "doc-1", "relationship_role": "correspondence"},
                    {"document_id": "doc-out", "relationship_role": "variation_submission"},
                ]
            },
        )
        db.entity_document_links.sessions.clear()
        deleted = await client.delete("/api/variations/var-1")
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert linked.status_code == 201, linked.text
    assert deleted.status_code == 204, deleted.text
    assert db.variations.documents == []
    assert all(row["removed_at"] is not None for row in db.entity_document_links.documents)
    assert reverse.json()["links"] == []
    assert {row["_id"] for row in db.documents.documents} >= {"doc-1", "doc-out"}
    assert len(_audits(db, "document_relationship.unlinked")) == 2
    assert len(_audits(db, "variation.deleted")) == 1
    # cleanup and deletion commit together
    assert db.entity_document_links.sessions and all(db.entity_document_links.sessions)


@pytest.mark.asyncio
async def test_service_delete_delegates_to_canonical_cleanup() -> None:
    db = _VariationDatabase()
    await DocumentRelationshipService(db).link_batch(
        _user(), "variation", "var-1",
        [DocumentRelationshipInput(document_id="doc-1", relationship_role="correspondence")],
    )
    assert await VariationService(db).delete(db.variations.documents[0], _user()) is True
    assert db.variations.documents == []
    assert db.entity_document_links.documents[0]["removed_at"] is not None
    assert len(_audits(db, "variation.deleted")) == 1


@pytest.mark.asyncio
async def test_delete_requires_variation_delete_permission() -> None:
    db = _VariationDatabase()
    user = _permission_user(Permissions.VARIATION_VIEW, Permissions.VARIATION_EDIT, Permissions.DOCUMENT_VIEW)
    async with await _client(db, policy=_PermissionPolicy(), user=lambda: user) as client:
        response = await client.delete("/api/variations/var-1")
    assert response.status_code == 403, response.text
    assert len(db.variations.documents) == 1


@pytest.mark.asyncio
async def test_variation_evidence_has_no_invented_freeze_contract() -> None:
    db = _VariationDatabase()
    async with await _client(db) as client:
        response = await client.post(
            "/api/entities/variation/var-1/document-links:freeze",
            json={"reason": "no such lifecycle"},
        )
    assert response.status_code == 409, response.text
    assert "not supported" in response.text


@pytest.mark.asyncio
async def test_scope_less_legacy_variation_stays_readable_without_presenting_raw_ids() -> None:
    db = _VariationDatabase()
    db.variations.documents.append(
        {
            "_id": "var-legacy",
            "variation_number": "VO-OLD",
            "organization_id": "org-1",
            "project_id": None,
            "currency_amounts": [],
            "linked_document_ids": ["doc-1"],
        }
    )
    async with await _client(db) as client:
        response = await client.get("/api/variations/var-legacy")
    assert response.status_code == 200, response.text
    assert response.json()["linked_document_ids"] == []


# --------------------------------------------------------------------------- #
# 5. Link to Record (Document side)
# --------------------------------------------------------------------------- #


def _with_link_targets(db: _VariationDatabase) -> _VariationDatabase:
    db.variations.documents.extend(
        [
            {"_id": "var-2", "variation_number": "VO-002", "organization_id": "org-1", "project_id": "project-1"},
            {"_id": "var-foreign", "variation_number": "VO-FOREIGN", "organization_id": "org-1", "project_id": "project-2"},
            {"_id": "var-other-org", "variation_number": "VO-ORG2", "organization_id": "org-2", "project_id": "project-1"},
        ]
    )
    db.ipc_bills.documents.append(
        {"_id": "ipc-1", "ipc_number": "IPC-001", "organization_id": "org-1", "project_id": "project-1"}
    )
    return db


@pytest.mark.asyncio
async def test_link_targets_offer_only_in_scope_records_of_a_verified_type() -> None:
    db = _with_link_targets(_VariationDatabase())
    async with await _client(db) as client:
        variations = await client.get("/api/documents/doc-1/link-targets", params={"target_type": "variation"})
        filtered = await client.get("/api/documents/doc-1/link-targets", params={"target_type": "variation", "q": "002"})
        claims = await client.get("/api/documents/doc-1/link-targets", params={"target_type": "claim"})
        unsupported = [
            await client.get("/api/documents/doc-1/link-targets", params={"target_type": kind})
            for kind in ("contract_document", "bank_guarantee", "delay_event", "hindrance")
        ]
        missing = await client.get("/api/documents/nope/link-targets", params={"target_type": "variation"})

    assert variations.status_code == 200, variations.text
    offered = variations.json()["targets"]
    assert sorted(row["target_id"] for row in offered) == ["var-1", "var-2"]
    first = next(row for row in offered if row["target_id"] == "var-1")
    assert first["label"] == "VO-001"
    assert first["route"] == "/variations?variation_id=var-1"
    assert first["allowed_roles"] == sorted(VARIATION_DOCUMENT_ROLES)
    assert [row["target_id"] for row in filtered.json()["targets"]] == ["var-2"]
    assert [row["target_id"] for row in claims.json()["targets"]] == ["claim-1"]
    assert all(response.status_code == 404 for response in unsupported)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_link_targets_require_the_target_manage_permission() -> None:
    db = _with_link_targets(_VariationDatabase())
    user = _permission_user(Permissions.DOCUMENT_VIEW, Permissions.VARIATION_EDIT, Permissions.CLAIM_VIEW)
    async with await _client(db, policy=_PermissionPolicy(), user=lambda: user) as client:
        variations = await client.get("/api/documents/doc-1/link-targets", params={"target_type": "variation"})
        claims = await client.get("/api/documents/doc-1/link-targets", params={"target_type": "claim"})
    no_doc_view = _permission_user(Permissions.VARIATION_EDIT)
    async with await _client(db, policy=_PermissionPolicy(), user=lambda: no_doc_view) as client:
        refused = await client.get("/api/documents/doc-1/link-targets", params={"target_type": "variation"})

    assert len(variations.json()["targets"]) == 2
    assert claims.status_code == 200 and claims.json()["targets"] == []  # view is not manage
    assert refused.status_code == 403, refused.text


@pytest.mark.asyncio
async def test_one_letter_links_to_many_register_records_at_once() -> None:
    db = _with_link_targets(_VariationDatabase())
    async with await _client(db) as client:
        for target_type, target_id, role in (
            ("variation", "var-1", "correspondence"),
            ("variation", "var-2", "variation_submission"),
            ("claim", "claim-1", "notice"),
            ("ipc_bill", "ipc-1", "supporting_document"),
        ):
            response = await client.post(
                f"/api/entities/{target_type}/{target_id}/document-links:batch",
                json={"links": [{"document_id": "doc-1", "relationship_role": role}]},
            )
            assert response.status_code == 201, response.text
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert sorted((row["target_type"], row["target_id"]) for row in reverse.json()["links"]) == [
        ("claim", "claim-1"),
        ("ipc_bill", "ipc-1"),
        ("variation", "var-1"),
        ("variation", "var-2"),
    ]


@pytest.mark.asyncio
async def test_link_target_search_reaches_past_the_scan_limit() -> None:
    from rbac_backend.services import document_relationship_service as module

    db = _with_link_targets(_VariationDatabase())
    db.variations.documents[:0] = [
        {"_id": f"var-0{index:03d}", "variation_number": f"VO-BULK-{index}", "organization_id": "org-1", "project_id": "project-1"}
        for index in range(module.LINK_TARGET_SCAN_LIMIT + 5)
    ]
    async with await _client(db) as client:
        found = await client.get("/api/documents/doc-1/link-targets", params={"target_type": "variation", "q": "vo-002"})
    assert [row["target_id"] for row in found.json()["targets"]] == ["var-2"]


@pytest.mark.asyncio
async def test_scope_less_legacy_variation_can_still_be_deleted() -> None:
    """A row without organisation/project can hold no canonical link, so the
    canonical cleanup (which refuses scope-less targets with 409) must not make
    it undeletable."""
    db = _VariationDatabase()
    db.variations.documents.append(
        {"_id": "var-legacy", "variation_number": "VO-OLD", "organization_id": "org-1",
         "project_id": None, "currency_amounts": [], "linked_document_ids": ["doc-1"]}
    )
    async with await _client(db) as client:
        response = await client.delete("/api/variations/var-legacy")
    assert response.status_code == 204, response.text
    assert [row["_id"] for row in db.variations.documents] == ["var-1"]
    assert len(_audits(db, "variation.deleted")) == 1
    assert any(row["_id"] == "doc-1" for row in db.documents.documents)


@pytest.mark.asyncio
async def test_put_echo_is_compared_with_what_the_caller_was_shown_not_the_raw_array() -> None:
    db = _VariationDatabase()
    db.variations.documents[0]["linked_document_ids"] = ["doc-1", "doc-foreign-org"]
    async with await _client(db) as client:
        shown = (await client.get("/api/variations/var-1")).json()["linked_document_ids"]
        echoed = await client.put("/api/variations/var-1", json={"remarks": "ok", "linked_document_ids": shown})
        # Guessing the raw stored array (with an id the caller cannot see) is no
        # longer an oracle: it is simply a different value, refused.
        guessed = await client.put(
            "/api/variations/var-1", json={"linked_document_ids": ["doc-1", "doc-foreign-org"]}
        )
    assert shown == ["doc-1"]
    assert echoed.status_code == 200, echoed.text
    assert guessed.status_code == 409, guessed.text
    assert db.variations.documents[0]["linked_document_ids"] == ["doc-1", "doc-foreign-org"]


@pytest.mark.asyncio
async def test_link_targets_listing_has_its_own_scoped_rate_limit(monkeypatch) -> None:
    from rbac_backend.routers import document_relationships as module

    assert module.LINK_TARGETS_RATE_LIMITER.scope == "document_link_targets"
    monkeypatch.setattr(
        module, "LINK_TARGETS_RATE_LIMITER",
        module.RateLimiter(max_requests=2, window_seconds=60, scope="document_link_targets_test"),
    )
    db = _VariationDatabase()
    async with await _client(db) as client:
        statuses = [
            (await client.get("/api/documents/doc-1/link-targets", params={"target_type": "variation"})).status_code
            for _ in range(3)
        ]
    assert statuses == [200, 200, 429]
