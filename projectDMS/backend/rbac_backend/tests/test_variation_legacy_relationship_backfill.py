"""CL-2: legacy Variation links through the unified backfill operator.

Two legacy sources exist on a Variation row: the role-less
``linked_document_ids`` array and the free-text ``letter_reference``. Both are
discovery input only. The classifier re-resolves every candidate against the
current canonical Document and the Variation's own scope; the operator scopes,
dry-runs, selects and applies. Nothing in either legacy field is rewritten.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from rbac_backend.core.permissions import Permissions
from rbac_backend.scripts.variation_legacy_link_census import ReadOnlyDatabase
from rbac_backend.services.variation_document_link_migration import (
    classify_legacy_variation_links,
    variation_legacy_census,
)
from rbac_backend.tests.test_claim_document_relationships import (
    _Collection,
    _Database,
    _PermissionPolicy,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/legacy-relationship-backfill/apply",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


class _OperatorPolicy(_PermissionPolicy):
    async def authorize(self, actor, permission: str, **kwargs) -> None:
        if permission not in getattr(actor, "permissions", set()):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Denied")


def _operator(*permissions: str) -> SimpleNamespace:
    granted = set(permissions) or {
        Permissions.DMS_ADMIN,
        Permissions.VARIATION_VIEW,
        Permissions.VARIATION_EDIT,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(id="operator-1", organization_id="org-1", permissions=granted)


def _doc(_id: str, *, org: str = "org-1", project: str = "project-1", upload_type: Any = "incoming", **extra: Any) -> dict[str, Any]:
    row = {
        "_id": _id,
        "organization_id": org,
        "project_id": project,
        "processing_status": "metadata_extracted",
        "lifecycle_state": "active",
        "subject": f"Confidential subject {_id}",
        **extra,
    }
    if upload_type is not None:
        row["uploadType"] = upload_type
    return row


def _seed() -> _Database:
    db = _Database()
    db.documents.documents.extend(
        [
            _doc("doc-out", upload_type="outgoing", letterNo="ABC/OUT/9", letterNoNormalized="abc-out-9"),
            _doc("doc-contract", upload_type="contract"),
            _doc("doc-untyped", upload_type=None),
            _doc("doc-foreign-org", org="org-2", project="project-9"),
            _doc("doc-foreign-project", project="project-2"),
            _doc("doc-deleted", lifecycle_state="deleted"),
            _doc("doc-letter", letterNo="VO/LTR/7", letterNoNormalized="vo-ltr-7"),
            _doc("doc-twin-a", letterNo="DUP/1", letterNoNormalized="dup-1"),
            _doc("doc-twin-b", letterNo="DUP/1", letterNoNormalized="dup-1"),
            # Same letter number in another tenant: must never be searched.
            _doc("doc-letter-elsewhere", org="org-2", project="project-9", letterNo="VO/LTR/7", letterNoNormalized="vo-ltr-7"),
        ]
    )
    db.variations = _Collection(
        "variations",
        [
            {
                "_id": "var-1",
                "variation_number": "VO-001",
                "organization_id": "org-1",
                "project_id": "project-1",
                "letter_reference": "VO/LTR/7",
                "linked_document_ids": [
                    "doc-1",  # incoming correspondence
                    "doc-contract",  # contract -> supporting document
                    "doc-foreign-org",
                    "doc-foreign-project",
                    "missing-doc",
                    "doc-deleted",
                    "doc-1",  # duplicate
                    "",  # invalid
                ],
            },
            {
                "_id": "var-2",
                "variation_number": "VO-002",
                "organization_id": "org-1",
                "project_id": "project-1",
                "letter_reference": "DUP/1",
                "linked_document_ids": [],
            },
            {
                "_id": "var-3",
                "variation_number": "VO-003",
                "organization_id": "org-1",
                "project_id": "project-1",
                "letter_reference": "abc out 9",  # normalizes onto doc-out
            },
            {
                "_id": "var-4",
                "variation_number": "VO-004",
                "organization_id": "org-1",
                "project_id": "project-1",
                "letter_reference": "NOPE/404",
                "linked_document_ids": ["doc-untyped"],
            },
        ],
    )
    return db


def _active_links(db) -> list:
    return [row for row in db.entity_document_links.documents if row.get("removed_at") is None]


async def _call(db, monkeypatch, endpoint: str, **overrides):
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)
    kwargs = {
        "request": _request(),
        "module": "variation",
        "org_id": "org-1",
        "project_id": "project-1",
        "db": db,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    if endpoint == "apply":
        kwargs.update({"selections": [], "dry_run": False})
    kwargs.update(overrides)
    handler = (
        backfill_router.apply_legacy_relationship_backfill
        if endpoint == "apply"
        else backfill_router.inventory_legacy_relationship_backfill
    )
    return await handler(**kwargs)


def _by(report: dict, variation_id: str, field: str) -> list[tuple[str, str]]:
    return [
        (row["document_id"], row["classification"])
        for row in report["candidates"]
        if row["target_id"] == variation_id and row["legacy_field"] == field
    ]


@pytest.mark.asyncio
async def test_classifier_covers_every_legacy_shape_without_writing() -> None:
    db = _seed()
    before = [dict(row) for row in db.variations.documents]

    report = await classify_legacy_variation_links(db)

    assert _by(report, "var-1", "linked_document_ids") == [
        ("doc-1", "valid_correspondence"),
        ("doc-contract", "valid_supporting_document"),
        ("doc-foreign-org", "cross_organisation"),
        ("doc-foreign-project", "cross_project"),
        ("missing-doc", "missing_document"),
        ("doc-deleted", "deleted_document"),
        ("doc-1", "duplicate"),
        ("", "invalid_id"),
    ]
    assert _by(report, "var-1", "letter_reference") == [("doc-letter", "valid_correspondence")]
    assert _by(report, "var-2", "letter_reference") == [("", "ambiguous")]
    assert _by(report, "var-3", "letter_reference") == [("doc-out", "valid_correspondence")]
    assert _by(report, "var-4", "letter_reference") == [("", "missing_document")]
    # A Document with no uploadType is not correspondence: generic role only.
    assert _by(report, "var-4", "linked_document_ids") == [("doc-untyped", "valid_supporting_document")]

    roles = {
        (row["target_id"], row["legacy_field"], row["document_id"]): row["relationship_role"]
        for row in report["candidates"]
    }
    assert roles[("var-1", "linked_document_ids", "doc-1")] is None  # operator adjudicates
    assert roles[("var-1", "linked_document_ids", "doc-contract")] == "supporting_document"
    assert roles[("var-1", "letter_reference", "doc-letter")] == "correspondence"
    ambiguous = next(row for row in report["candidates"] if row["classification"] == "ambiguous")
    assert ambiguous["matched_document_ids"] == ["doc-twin-a", "doc-twin-b"]
    # never searched outside the Variation's own scope
    assert all("doc-letter-elsewhere" not in row.get("matched_document_ids", []) for row in report["candidates"])

    assert db.variations.documents == before
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_inventory_dry_run_apply_and_reapply_are_idempotent(monkeypatch) -> None:
    db = _seed()
    inventory = await _call(db, monkeypatch, "inventory")
    assert inventory["module"] == "variation"
    assert inventory["counts"]["valid_correspondence"] == 3

    selections = [
        {"target_id": "var-1", "document_id": "doc-1", "relationship_role": "correspondence"},
        {"target_id": "var-1", "document_id": "doc-contract"},  # classifier role
        {"target_id": "var-1", "document_id": "doc-letter"},  # classifier role
        {"target_id": "var-1", "document_id": "doc-foreign-org", "relationship_role": "supporting_document"},
        {"target_id": "var-1", "document_id": "missing-doc", "relationship_role": "supporting_document"},
    ]
    dry = await _call(db, monkeypatch, "apply", selections=selections, dry_run=True)
    assert [row["status"] for row in dry["results"]] == [
        "eligible", "eligible", "eligible", "requires_manual_review", "requires_manual_review",
    ]
    assert db.entity_document_links.documents == []

    applied = await _call(db, monkeypatch, "apply", selections=selections)
    assert [row["status"] for row in applied["results"]] == [
        "backfilled", "backfilled", "backfilled", "requires_manual_review", "requires_manual_review",
    ]
    links = _active_links(db)
    assert sorted((row["document_id"], row["relationship_role"]) for row in links) == [
        ("doc-1", "correspondence"),
        ("doc-contract", "supporting_document"),
        ("doc-letter", "correspondence"),
    ]
    by_doc = {row["document_id"]: row["metadata"] for row in links}
    assert by_doc["doc-1"]["role_source"] == "operator"
    assert by_doc["doc-contract"]["role_source"] == "classifier"
    assert by_doc["doc-letter"]["legacy_field"] == "letter_reference"

    again = await _call(db, monkeypatch, "apply", selections=selections)
    assert [row["status"] for row in again["results"]][:3] == ["already_canonical"] * 3
    assert len(_active_links(db)) == 3
    linked_audits = [row for row in db.audit_events.documents if row.get("action") == "document_relationship.linked"]
    assert len(linked_audits) == 3

    # No data loss: both legacy fields are exactly as they were.
    row = db.variations.documents[0]
    assert row["letter_reference"] == "VO/LTR/7"
    assert len(row["linked_document_ids"]) == 8


@pytest.mark.asyncio
async def test_correspondence_role_on_a_contract_document_is_refused_by_the_service(monkeypatch) -> None:
    db = _seed()
    result = await _call(
        db,
        monkeypatch,
        "apply",
        selections=[{"target_id": "var-1", "document_id": "doc-contract", "relationship_role": "correspondence"}],
    )
    assert result["results"][0]["status"] == "rejected"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_ambiguous_correspondence_candidate_requires_an_operator_role(monkeypatch) -> None:
    db = _seed()
    result = await _call(
        db, monkeypatch, "apply", selections=[{"target_id": "var-1", "document_id": "doc-1"}]
    )
    assert result["results"][0]["status"] == "role_required"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_ordinary_user_cannot_backfill_variation(monkeypatch) -> None:
    from fastapi import HTTPException

    db = _seed()
    with pytest.raises(HTTPException) as refused:
        await _call(
            db,
            monkeypatch,
            "apply",
            current_user=_operator(Permissions.VARIATION_EDIT, Permissions.DOCUMENT_VIEW),
            selections=[{"target_id": "var-1", "document_id": "doc-1", "relationship_role": "correspondence"}],
        )
    assert refused.value.status_code == 403
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_census_is_read_only_counts_only_and_carries_no_identifiers() -> None:
    db = _seed()
    census = await variation_legacy_census(ReadOnlyDatabase(db))

    assert census["read_only"] is True
    assert census["variation_rows"] == 4
    assert census["rows_with_linked_document_ids"] == 2
    assert census["total_linked_ids"] == 9
    assert census["linked_ids"] == {
        "valid_correspondence": 1,
        "valid_supporting_document": 2,
        "missing_documents": 1,
        "deleted_documents": 1,
        "blocked_documents": 0,
        "foreign_org_ids": 1,
        "foreign_project_ids": 1,
        "duplicate_ids": 1,
        "invalid_ids": 1,
        "project_null_rows": 0,
    }
    assert census["linked_document_upload_types"] == {
        "incoming": 4, "outgoing": 0, "contract": 1, "other": 0, "missing": 1, "unresolved": 1,
    }
    assert census["rows_with_letter_reference"] == 4
    assert census["letter_references"]["resolved_correspondence"] == 2
    assert census["letter_references"]["ambiguous"] == 1
    assert census["letter_references"]["unresolved"] == 1

    rendered = repr(census)
    for secret in ("var-1", "VO-001", "doc-1", "VO/LTR/7", "Confidential", "org-1", "project-1"):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_census_respects_scope() -> None:
    db = _seed()
    census = await variation_legacy_census(db, organization_id="org-2")
    assert census["variation_rows"] == 0
    assert census["total_linked_ids"] == 0
    assert census["scope"] == {"organization": True, "project": False}


@pytest.mark.asyncio
async def test_census_handle_refuses_writes_before_the_driver() -> None:
    db = _seed()
    handle = ReadOnlyDatabase(db)
    for method in ("insert_one", "update_one", "delete_many", "aggregate", "create_index"):
        with pytest.raises(PermissionError):
            getattr(handle.variations, method)
    with pytest.raises(PermissionError):
        handle.command
    assert len(db.variations.documents) == 4
