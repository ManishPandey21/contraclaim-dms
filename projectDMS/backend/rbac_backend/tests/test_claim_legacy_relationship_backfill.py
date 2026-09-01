"""Claim adapter for the unified legacy relationship backfill.

Claim already migrates `linked_document_ids` when a user edits the claim
(`replace_legacy_document_ids`), and surfaces un-migrated ids through the
`legacy_read_through` merge. What is missing is a path for claims nobody has
edited since the tracer landed: this plugs Claim into the common orchestrator.

Claim's legacy role is NOT a guess. Three independent, already-accepted sources
agree it is `supporting_document`:
  * ClaimEntityAdapter.legacy_relationship_role
  * replace_legacy_document_ids, which writes exactly that role
  * the read-through view users already see today
Backfilling with any other role would change shipped behaviour.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from rbac_backend.core.permissions import Permissions
from rbac_backend.tests.test_claim_document_relationships import (
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
        Permissions.CLAIM_VIEW,
        Permissions.CLAIM_EDIT,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(
        id="operator-1", organization_id="org-1", permissions=granted
    )


def _seed_claim_legacy_array(**document_overrides) -> _Database:
    db = _Database()
    db.claims.documents[0]["linked_document_ids"] = ["doc-1"]
    if document_overrides:
        db.documents.documents[0].update(document_overrides)
    return db


def _active_links(db) -> list:
    return [
        row for row in db.entity_document_links.documents if row.get("removed_at") is None
    ]


async def _apply(db, monkeypatch, **overrides):
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)

    kwargs = {
        "request": _request(),
        "module": "claim",
        "selections": [{"target_id": "claim-1", "document_id": "doc-1"}],
        "org_id": "org-1",
        "project_id": "project-1",
        "dry_run": False,
        "db": db,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(overrides)
    return await backfill_router.apply_legacy_relationship_backfill(**kwargs)


@pytest.mark.asyncio
async def test_a_claim_legacy_document_id_backfills_with_the_accepted_role(
    monkeypatch,
) -> None:
    """The operator need not name the role: Claim's accepted semantics resolve
    it deterministically to `supporting_document`."""
    db = _seed_claim_legacy_array()

    result = await _apply(db, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "backfilled"
    assert outcome["target_type"] == "claim"
    assert outcome["relationship_role"] == "supporting_document"

    links = _active_links(db)
    assert len(links) == 1
    assert links[0]["target_type"] == "claim"
    assert links[0]["target_id"] == "claim-1"
    assert links[0]["document_id"] == "doc-1"
    assert links[0]["relationship_role"] == "supporting_document"
    assert links[0]["source"] == "migration"

    # Legacy array is preserved: this programme backfills, it does not retire.
    assert db.claims.documents[0]["linked_document_ids"] == ["doc-1"]


# -- Document authority matrix --------------------------------------------


@pytest.mark.asyncio
async def test_an_operationally_failed_document_still_backfills(monkeypatch) -> None:
    """Model B: a worker crash is operational, not an adverse judgement, and
    must not be over-blocked."""
    db = _seed_claim_legacy_array(processing_status="failed")

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "backfilled"
    assert len(_active_links(db)) == 1


@pytest.mark.parametrize(
    "fields,expected_finding",
    [
        pytest.param(
            {"processing_status": "human_review_required"},
            "blocked_document",
            id="human_review",
        ),
        pytest.param({"duplicate_status": "duplicate"}, "blocked_document", id="duplicate"),
        pytest.param({"lifecycle_state": "deleted"}, "deleted_document", id="deleted"),
        pytest.param({"lifecycle_state": "duplicate"}, "blocked_document", id="quarantined"),
        pytest.param({"organization_id": "org-2"}, "cross_organisation", id="cross_org"),
        pytest.param({"project_id": "project-2"}, "cross_project", id="cross_project"),
    ],
)
@pytest.mark.asyncio
async def test_an_unusable_document_is_never_backfilled(
    monkeypatch, fields: dict, expected_finding: str
) -> None:
    db = _seed_claim_legacy_array(**fields)

    result = await _apply(db, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert expected_finding in outcome["findings"]
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_missing_document_is_never_backfilled(monkeypatch) -> None:
    db = _seed_claim_legacy_array()
    db.documents.documents.clear()

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "requires_manual_review"
    assert _active_links(db) == []


# -- Claim target authority -------------------------------------------------


@pytest.mark.asyncio
async def test_a_frozen_claim_is_never_mutated(monkeypatch) -> None:
    """Claim's accepted model refuses relationship writes on a frozen claim.
    Legacy membership earns no retrospective exception."""
    db = _seed_claim_legacy_array()
    db.claims.documents[0]["evidence_frozen_at"] = "2026-08-01T00:00:00Z"

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "frozen_target"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_project_null_claim_is_refused(monkeypatch) -> None:
    db = _seed_claim_legacy_array()
    db.claims.documents[0]["project_id"] = ""

    result = await _apply(db, monkeypatch)

    # Scope is derived from the canonical target, so a project-null claim never
    # matches a project-scoped run.
    assert result["results"][0]["status"] == "out_of_scope"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_project_null_claim_is_refused_on_an_org_only_run(monkeypatch) -> None:
    """With no project filter the target is in scope, so the classifier's own
    fail-closed project_null branch is what refuses it."""
    db = _seed_claim_legacy_array()
    db.claims.documents[0]["project_id"] = ""

    result = await _apply(db, monkeypatch, project_id=None)

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert "project_null" in outcome["findings"]
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_document_listed_twice_is_still_backfillable_once(monkeypatch) -> None:
    """The classifier marks the second occurrence `duplicate`; that must not
    shadow the usable first occurrence and strand the link."""
    db = _seed_claim_legacy_array()
    db.claims.documents[0]["linked_document_ids"] = ["doc-1", "doc-1"]

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "backfilled"
    assert len(_active_links(db)) == 1


@pytest.mark.asyncio
async def test_a_backfilled_link_survives_a_legacy_array_edit(monkeypatch) -> None:
    """Pins accepted behaviour: `replace_legacy_document_ids` only retires links
    it created (`source="legacy_compatibility"`). A canonical relationship an
    operator backfilled is authoritative and must NOT be removed by editing the
    legacy array — that would let the legacy array overturn canonical state."""
    from rbac_backend.services.document_relationship_service import (
        DocumentRelationshipService,
    )

    db = _seed_claim_legacy_array()
    await _apply(db, monkeypatch)

    await DocumentRelationshipService(db, policy=_OperatorPolicy()).replace_legacy_document_ids(
        _operator(), "claim", "claim-1", []
    )

    links = _active_links(db)
    assert len(links) == 1
    assert links[0]["source"] == "migration"
    assert links[0]["relationship_role"] == "supporting_document"


@pytest.mark.asyncio
async def test_a_claim_outside_the_requested_scope_is_refused(monkeypatch) -> None:
    db = _seed_claim_legacy_array()
    db.claims.documents[0]["organization_id"] = "org-2"
    db.documents.documents[0]["organization_id"] = "org-2"

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "out_of_scope"
    assert _active_links(db) == []


# -- role -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operator_may_override_the_resolved_role(monkeypatch) -> None:
    db = _seed_claim_legacy_array()

    result = await _apply(
        db,
        monkeypatch,
        selections=[
            {
                "target_id": "claim-1",
                "document_id": "doc-1",
                "relationship_role": "notice",
            }
        ],
    )

    assert result["results"][0]["status"] == "backfilled"
    assert _active_links(db)[0]["relationship_role"] == "notice"


@pytest.mark.asyncio
async def test_a_role_outside_the_claim_vocabulary_is_refused(monkeypatch) -> None:
    db = _seed_claim_legacy_array()

    result = await _apply(
        db,
        monkeypatch,
        selections=[
            {
                "target_id": "claim-1",
                "document_id": "doc-1",
                "relationship_role": "policy",
            }
        ],
    )

    assert result["results"][0]["status"] == "rejected"
    assert _active_links(db) == []


# -- orchestration ----------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(monkeypatch) -> None:
    db = _seed_claim_legacy_array()

    result = await _apply(db, monkeypatch, dry_run=True)

    assert result["results"][0]["status"] == "eligible"
    assert result["results"][0]["relationship_role"] == "supporting_document"
    assert db.entity_document_links.documents == []
    assert db.audit_events.documents == []
    assert db.claims.documents[0]["linked_document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_a_rerun_is_idempotent(monkeypatch) -> None:
    db = _seed_claim_legacy_array()

    first = await _apply(db, monkeypatch)
    second = await _apply(db, monkeypatch)

    assert first["results"][0]["status"] == "backfilled"
    assert second["results"][0]["status"] == "already_canonical"
    assert len(_active_links(db)) == 1
    assert (
        len(
            [
                row
                for row in db.audit_events.documents
                if row.get("action") == "legacy_relationship.backfilled"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_a_deliberately_removed_relationship_is_not_resurrected(
    monkeypatch,
) -> None:
    db = _seed_claim_legacy_array()
    await _apply(db, monkeypatch)
    link = _active_links(db)[0]
    link["removed_at"] = "2026-08-23T00:00:00Z"
    link["removal_reason"] = "Wrong claim"

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "previously_removed"
    assert result["results"][0]["removal_reason"] == "Wrong claim"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_provenance_and_audit_are_recorded(monkeypatch) -> None:
    db = _seed_claim_legacy_array()

    result = await _apply(db, monkeypatch)

    link = _active_links(db)[0]
    metadata = link.get("metadata") or {}
    assert link["source"] == "migration"
    assert metadata.get("legacy_field") == "linked_document_ids"
    assert metadata.get("legacy_target_id") == "claim-1"
    assert metadata.get("operator_id") == "operator-1"

    events = [
        row
        for row in db.audit_events.documents
        if row.get("action") == "legacy_relationship.backfilled"
    ]
    assert len(events) == 1
    assert events[0]["resource_type"] == "claim"
    assert events[0]["resource_id"] == "claim-1"
    assert events[0]["after"]["migration_run_id"] == result["migration_run_id"]


@pytest.mark.asyncio
async def test_an_ordinary_user_cannot_backfill_claims(monkeypatch) -> None:
    from fastapi import HTTPException

    db = _seed_claim_legacy_array()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(
            db,
            monkeypatch,
            current_user=_operator(Permissions.CLAIM_EDIT, Permissions.DOCUMENT_VIEW),
        )

    assert excinfo.value.status_code == 403
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_forward_and_reverse_views_agree_after_backfill(monkeypatch) -> None:
    import httpx
    from fastapi import FastAPI

    from rbac_backend.core.database import get_db
    from rbac_backend.core.security import get_current_user
    from rbac_backend.routers.document_relationships import router as relationship_router
    from rbac_backend.tests.test_claim_document_relationships import _user

    db = _seed_claim_legacy_array()
    await _apply(db, monkeypatch)

    app = FastAPI()
    app.include_router(relationship_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = _user
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        forward = await client.get("/api/entities/claim/claim-1/document-links")
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert forward.status_code == 200, forward.text
    assert reverse.status_code == 200, reverse.text
    forward_links = forward.json()["links"]
    reverse_links = reverse.json()["links"]
    # One logical evidence item: the legacy read-through must not double-count a
    # relationship that is now canonical.
    assert len(forward_links) == 1
    assert forward_links == reverse_links
    assert forward_links[0]["relationship_role"] == "supporting_document"
    assert forward_links[0]["source"] == "migration"
