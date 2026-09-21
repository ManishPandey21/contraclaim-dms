"""CL-1 regression guards for canonical Document relationships (always-on).

The real-Mongo / real-RBAC proof lives in
``tests/integration/test_document_relationships_cl1_mongo.py`` and is opt-in.
This module pins the same defects at unit level so CI - which runs without that
replica set - still fails if any of them comes back:

* an ObjectId-keyed Document (the production shape) made link, forward list,
  reverse lookup and unlink answer 500 *after* the write had committed;
* a link whose Document row was hard-deleted could never be removed;
* the Contract Document target raised NotImplementedError on every write;
* ``correspondence`` accepted any Document, including a contract;
* ``/history`` claimed a history that is not stored.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import httpx
import pytest
from bson import Binary, ObjectId
from bson.decimal128 import Decimal128
from bson.timestamp import Timestamp
from fastapi import HTTPException

from rbac_backend.core.permissions import Permissions
from rbac_backend.models.document_relationship import DocumentRelationshipView
from rbac_backend.routers.documents import _resolve_upload_type_filter
from rbac_backend.services.entity_adapter_registry import (
    CORRESPONDENCE_ROLES,
    EntityAdapter,
    EntityAdapterRegistry,
    is_correspondence_document,
)
from rbac_backend.tests.test_claim_document_relationships import (
    _Database,
    _relationship_app,
)
from rbac_backend.utils.bson_presentation import present_bson

DOC_OID = ObjectId("65f000000000000000000001")
DOC_OID_ID = str(DOC_OID)


def _objectid_database() -> _Database:
    """The fake store with the Document keyed the way production keys it."""
    db = _Database()
    document = db.documents.documents[0]
    document["_id"] = DOC_OID
    document["uploaded_by_ref"] = ObjectId()
    document["current_version_id"] = "version-1"
    db.document_versions.documents[0]["document_id"] = DOC_OID_ID
    return db


def _client(db: _Database, **kwargs: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(
        app=_relationship_app(db, **kwargs), raise_app_exceptions=False
    )
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _link(client: httpx.AsyncClient, document_id: str, role: str = "correspondence"):
    return await client.post(
        "/api/entities/claim/claim-1/document-links:batch",
        json={"links": [{"document_id": document_id, "relationship_role": role}]},
    )


def _audits(db: _Database, action: str) -> list[dict[str, Any]]:
    return [row for row in db.audit_events.documents if row.get("action") == action]


# --------------------------------------------------------------------------- #
# The presentation boundary
# --------------------------------------------------------------------------- #


class _Colour(Enum):
    RED = "red"


def test_present_bson_is_total_and_json_safe() -> None:
    oid = ObjectId()
    raw = {
        "_id": oid,
        "nested": {"ref": ObjectId(), "list": [ObjectId(), {"deep": ObjectId()}]},
        "money": Decimal128("12.50"),
        "decimal": Decimal("1.10"),
        "blob": Binary(b"\x00\xff"),
        "bytes": b"\x00\xff",
        "uuid": uuid.UUID(int=1),
        "when": datetime(2026, 9, 21, 10, 0),
        "day": date(2026, 9, 21),
        "tags": {"a"},
        "colour": _Colour.RED,
        "ts": Timestamp(1, 1),
        7: "non-string key",
    }
    presented = present_bson(raw)

    assert presented["_id"] == str(oid)
    assert presented["nested"]["list"][1]["deep"] == str(raw["nested"]["list"][1]["deep"])
    assert presented["money"] == "12.50" and presented["decimal"] == "1.10"
    assert presented["blob"] is None and presented["bytes"] is None
    assert presented["when"] == raw["when"] and presented["day"] == raw["day"]
    assert presented["colour"] == "red" and presented["7"] == "non-string key"
    json.dumps(presented, default=str)  # nothing left that json cannot walk
    # The canonical row is not mutated for presentation.
    assert raw["_id"] is oid and isinstance(raw["nested"]["ref"], ObjectId)


def test_relationship_view_serializes_objectid_keyed_documents() -> None:
    oid = ObjectId()
    document = {"_id": oid, "owner": ObjectId(), "created_at": datetime(2026, 1, 1)}
    view = DocumentRelationshipView(
        _id="link-1",
        organization_id="org-1",
        project_id="project-1",
        target_type="claim",
        target_id="claim-1",
        document_id=str(oid),
        relationship_role="correspondence",
        document=document,
    )
    payload = view.model_dump(mode="json", by_alias=True)
    assert payload["document"]["_id"] == str(oid)
    assert isinstance(payload["document"]["owner"], str)
    json.dumps(payload)
    assert document["_id"] is oid


# --------------------------------------------------------------------------- #
# P0-1 / P0-2 - ObjectId-keyed Documents over HTTP; write succeeds -> response succeeds
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_objectid_document_round_trips_without_a_single_500() -> None:
    db = _objectid_database()
    async with _client(db) as client:
        created = await _link(client, DOC_OID_ID)
        assert created.status_code == 201, created.text
        link = created.json()["links"][0]
        assert link["document"]["_id"] == DOC_OID_ID
        assert len(db.entity_document_links.documents) == 1
        assert len(_audits(db, "document_relationship.linked")) == 1

        forward = await client.get("/api/entities/claim/claim-1/document-links")
        reverse = await client.get(f"/api/documents/{DOC_OID_ID}/entity-links")
        current = await client.get(f"/api/document-links/{link['_id']}")
        history = await client.get(f"/api/document-links/{link['_id']}/history")
        assert forward.status_code == 200, forward.text
        assert reverse.status_code == 200, reverse.text
        assert current.status_code == 200, current.text
        assert history.status_code == 200, history.text
        assert forward.json()["links"][0]["document"]["_id"] == DOC_OID_ID
        assert reverse.json()["links"][0]["target_id"] == "claim-1"

        removed = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "wrong claim", "expected_revision": 1},
        )
        assert removed.status_code == 200, removed.text
        assert len(_audits(db, "document_relationship.unlinked")) == 1
        assert db.documents.documents[0]["_id"] == DOC_OID  # Document survives

        relinked = await _link(client, DOC_OID_ID)
        assert relinked.status_code == 201, relinked.text
    active = [row for row in db.entity_document_links.documents if row.get("removed_at") is None]
    assert len(active) == 1


@pytest.mark.asyncio
async def test_idempotent_link_is_one_row_and_one_audit_event() -> None:
    db = _objectid_database()
    async with _client(db) as client:
        first = await _link(client, DOC_OID_ID)
        second = await _link(client, DOC_OID_ID)
    assert first.status_code == second.status_code == 201
    assert first.json()["links"][0]["_id"] == second.json()["links"][0]["_id"]
    assert len(db.entity_document_links.documents) == 1
    assert len(_audits(db, "document_relationship.linked")) == 1


@pytest.mark.asyncio
async def test_relationship_audit_events_name_what_changed() -> None:
    db = _objectid_database()
    async with _client(db) as client:
        link = (await _link(client, DOC_OID_ID)).json()["links"][0]
        await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "wrong claim", "expected_revision": 1},
        )
    expected = {
        "target_type": "claim",
        "target_id": "claim-1",
        "document_id": DOC_OID_ID,
        "relationship_role": "correspondence",
    }
    (linked,) = _audits(db, "document_relationship.linked")
    (unlinked,) = _audits(db, "document_relationship.unlinked")
    for event in (linked, unlinked):
        assert event["metadata"] == expected
        assert event["actor_id"] == "user-1"
        assert (event["organization_id"], event["project_id"]) == ("org-1", "project-1")
        assert event["resource_id"] == link["_id"]
        assert isinstance(event["created_at"], datetime)
    assert linked["after"]["document_id"] == DOC_OID_ID
    assert unlinked["before"]["removed_at"] is None and unlinked["after"]["removed_at"]


# --------------------------------------------------------------------------- #
# P0-3 - missing Document on unlink
# --------------------------------------------------------------------------- #


class _RecordingPolicy:
    def __init__(self, *, deny: set[str] = frozenset()) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.deny = set(deny)

    async def authorize_document(self, actor: Any, permission: str, document: Any, **kwargs: Any) -> None:
        self.calls.append((permission, dict(document)))
        if permission in self.deny:
            raise HTTPException(status_code=403, detail="Denied")


async def _link_then_delete_document(db: _Database) -> dict[str, Any]:
    async with _client(db) as client:
        link = (await _link(client, DOC_OID_ID)).json()["links"][0]
    db.documents.documents.clear()
    return link


@pytest.mark.asyncio
async def test_missing_document_link_is_removable_and_audited() -> None:
    db = _objectid_database()
    link = await _link_then_delete_document(db)
    policy = _RecordingPolicy()
    async with _client(db, policy=policy) as client:
        removed = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "Document was purged", "expected_revision": 1},
        )
    assert removed.status_code == 200, removed.text
    assert removed.json()["link"]["document"] is None
    assert db.documents.documents == []  # never resurrected
    assert len(_audits(db, "document_relationship.unlinked")) == 1
    # Not weaker: DOCUMENT_VIEW is still decided, on the link's own stored scope.
    document_checks = [doc for permission, doc in policy.calls if permission == Permissions.DOCUMENT_VIEW]
    assert document_checks == [
        {"_id": DOC_OID_ID, "organization_id": "org-1", "project_id": "project-1"}
    ]
    assert policy.calls[0][0] == Permissions.CLAIM_EDIT  # target manage decided first


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [Permissions.CLAIM_EDIT, Permissions.DOCUMENT_VIEW])
async def test_missing_document_unlink_still_requires_both_decisions(denied: str) -> None:
    db = _objectid_database()
    link = await _link_then_delete_document(db)
    async with _client(db, policy=_RecordingPolicy(deny={denied})) as client:
        refused = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "x", "expected_revision": 1},
        )
    assert refused.status_code == 403, refused.text
    assert db.entity_document_links.documents[0]["removed_at"] is None
    assert _audits(db, "document_relationship.unlinked") == []


@pytest.mark.asyncio
async def test_link_row_whose_scope_disagrees_with_its_target_is_not_trusted() -> None:
    db = _objectid_database()
    link = await _link_then_delete_document(db)
    db.entity_document_links.documents[0]["project_id"] = "project-elsewhere"
    async with _client(db, policy=_RecordingPolicy()) as client:
        refused = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "x", "expected_revision": 1},
        )
    assert refused.status_code == 409, refused.text
    assert db.entity_document_links.documents[0]["removed_at"] is None


# --------------------------------------------------------------------------- #
# P1 - correspondence semantics are enforced by the server
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"uploadType": "incoming"}, True),
        ({"uploadType": "Outgoing"}, True),
        ({"upload_type": "incoming"}, True),
        ({"uploadType": "contract"}, False),
        ({"uploadType": "drawing"}, False),
        ({"uploadType": "contract", "upload_type": "incoming"}, False),
        ({}, False),
        (None, False),
    ],
)
def test_correspondence_is_the_incoming_outgoing_upload_types(document: Any, expected: bool) -> None:
    assert is_correspondence_document(document) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("upload_type", ["contract", "drawing", None])
async def test_correspondence_role_refuses_non_correspondence_documents(upload_type: Any) -> None:
    db = _objectid_database()
    if upload_type is None:
        db.documents.documents[0].pop("uploadType", None)
    else:
        db.documents.documents[0]["uploadType"] = upload_type
    async with _client(db) as client:
        refused = await _link(client, DOC_OID_ID, role="correspondence")
        supporting = await _link(client, DOC_OID_ID, role="supporting_document")
    assert refused.status_code == 422, refused.text
    assert supporting.status_code == 201, supporting.text
    assert {row["relationship_role"] for row in db.entity_document_links.documents} == {
        "supporting_document"
    }


def test_every_correspondence_role_target_also_offers_supporting_document() -> None:
    """The 422 tells the user to use supporting_document; it must exist there."""
    from rbac_backend.services import entity_adapter_registry as registry_module

    role_sets = [
        registry_module.CLAIM_DOCUMENT_ROLES,
        registry_module.IPC_DOCUMENT_ROLES,
        registry_module.KEY_DATE_ACHIEVEMENT_ROLES,
        registry_module.EOT_SUBMISSION_ROLES,
        registry_module.EOT_DETERMINATION_ROLES,
        registry_module.INSURANCE_DOCUMENT_ROLES,
        *registry_module.BANK_GUARANTEE_EVENT_ROLES.values(),
    ]
    for roles in role_sets:
        if roles & CORRESPONDENCE_ROLES:
            assert "supporting_document" in roles, roles


def test_link_selector_correspondence_filter_maps_to_the_taxonomy() -> None:
    assert _resolve_upload_type_filter("correspondence") == {
        "$regex": "^(incoming|outgoing)$",
        "$options": "i",
    }
    assert _resolve_upload_type_filter(" Correspondence ") == _resolve_upload_type_filter("correspondence")
    assert _resolve_upload_type_filter("incoming") == "incoming"
    assert _resolve_upload_type_filter(None) is None


# --------------------------------------------------------------------------- #
# P0-4 - no registered target may answer a write with NotImplementedError
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("adapter", EntityAdapterRegistry().adapters(), ids=lambda a: a.target_type)
def test_every_registered_adapter_implements_its_write_seams(adapter: EntityAdapter) -> None:
    """The class of defect, not the instance: a registered target that inherits
    the abstract write guard turns every link into an unhandled 500."""
    for seam in ("load", "context_from_entity", "guard_relationship_write", "delete", "freeze"):
        assert getattr(type(adapter), seam) is not getattr(EntityAdapter, seam), (
            f"{adapter.target_type} does not implement {seam}"
        )


def _contract_database(scope_level: str = "project") -> _Database:
    db = _Database()
    db["contract_documents"].documents.append(
        {
            "_id": "cd-1",
            "organization_id": "org-1",
            "document_id": "doc-contract",
            "scope_level": scope_level,
            "scope_project_id": "project-1" if scope_level == "project" else None,
            "contract_document_type": "particular_conditions",
            "classification_revision": 1,
        }
    )
    return db


@pytest.mark.asyncio
async def test_contract_document_target_links_and_unlinks() -> None:
    db = _contract_database()
    async with _client(db) as client:
        created = await client.post(
            "/api/entities/contract_document/cd-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "supporting_document"}]},
        )
        assert created.status_code == 201, created.text
        link = created.json()["links"][0]
        assert link["target_route"] == "/contracts/viewer/doc-contract"
        removed = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "x", "expected_revision": 1},
        )
        assert removed.status_code == 200, removed.text
    record = db["contract_documents"].documents[0]
    assert record["document_id"] == "doc-contract"  # identity untouched
    assert record["document_relationship_revision"] == 1


@pytest.mark.asyncio
async def test_contract_document_rescoped_between_load_and_commit_refuses_the_write() -> None:
    db = _contract_database()
    registry = EntityAdapterRegistry()
    adapter = registry.get("contract_document")
    context = await adapter.load(db, "cd-1")
    db["contract_documents"].documents[0]["scope_project_id"] = "project-2"
    assert await adapter.guard_relationship_write(db, context) is False
    assert await adapter.delete(db, context) is False


# --------------------------------------------------------------------------- #
# History endpoint is truthful
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_history_route_is_marked_as_current_state_only() -> None:
    db = _objectid_database()
    async with _client(db) as client:
        link = (await _link(client, DOC_OID_ID)).json()["links"][0]
        history = await client.get(f"/api/document-links/{link['_id']}/history")
        current = await client.get(f"/api/document-links/{link['_id']}")
    assert history.headers["Deprecation"] == "@1789948800"
    assert f"/api/document-links/{link['_id']}" in history.headers["Link"]
    assert history.json()["links"] == [current.json()["link"]]


def test_openapi_marks_history_deprecated() -> None:
    app = _relationship_app(_Database())
    operation = app.openapi()["paths"]["/api/document-links/{link_id}/history"]["get"]
    assert operation.get("deprecated") is True


# --------------------------------------------------------------------------- #
# Review follow-ups
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("upload_type", ["contract", None])
async def test_letter_backfill_dry_run_agrees_with_apply_on_non_correspondence(
    upload_type: Any, monkeypatch: Any
) -> None:
    """The dry run must not report as valid a candidate the apply would refuse."""
    from rbac_backend.services.claim_document_link_migration import (
        classify_legacy_claim_letter_links,
    )
    from rbac_backend.tests.test_claim_legacy_letter_backfill import _apply, _seed

    db = _seed(["doc-1"])
    if upload_type is None:
        db.documents.documents[0].pop("uploadType", None)
    else:
        db.documents.documents[0]["uploadType"] = upload_type

    inventory = await classify_legacy_claim_letter_links(db)
    (candidate,) = inventory["candidates"]
    assert candidate["classification"] == "non_correspondence"
    assert candidate["relationship_role"] is None
    assert inventory["counts"]["non_correspondence"] == 1

    result = await _apply(db, monkeypatch)
    assert result["results"][0]["status"] != "backfilled"
    assert [row for row in db.entity_document_links.documents if row.get("removed_at") is None] == []


@pytest.mark.asyncio
async def test_linked_contract_document_row_still_validates_as_its_record_model() -> None:
    """The write fence adds a field to an `extra="forbid"` record; it must be declared."""
    from rbac_backend.models.contract_document_records import ContractDocumentRecord

    db = _contract_database()
    async with _client(db) as client:
        created = await client.post(
            "/api/entities/contract_document/cd-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "supporting_document"}]},
        )
    assert created.status_code == 201, created.text
    record = ContractDocumentRecord.model_validate(db["contract_documents"].documents[0])
    assert record.document_relationship_revision == 1


@pytest.mark.asyncio
async def test_target_gate_is_decided_on_the_targets_canonical_project() -> None:
    """A Contract Document row keeps its project in scope_project_id. The target
    gate must still hand PolicyService that project, or a project-tier user of a
    different project is judged at organisation level and passes."""
    db = _contract_database()
    policy = _RecordingPolicy()
    async with _client(db, policy=policy) as client:
        await client.get("/api/entities/contract_document/cd-1/document-links")
    permission, subject = policy.calls[0]
    assert permission == Permissions.CONTRACT_MASTER_VIEW
    assert (subject["organization_id"], subject["project_id"]) == ("org-1", "project-1")
