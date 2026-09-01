"""IPC adapter for the unified legacy relationship backfill.

IPC's accepted posture differs from Claim's. Ordinary non-empty legacy writes
FAIL CLOSED (`reject_ambiguous_legacy_write` on create, and
`AmbiguousLegacyIPCRelationshipError` in the service), because the flat array
carries no role information and IPC's vocabulary is six genuinely different
roles. Nothing in the accepted code assigns a deterministic legacy role, so this
adapter does not invent one either.

That makes IPC the Insurance shape, not the Claim shape: the classifier reports
`ambiguous_role`, and only a privileged operator may adjudicate the role. The
provenance records that the role was operator-supplied, never that the
classifier derived it.

Two accepted IPC decisions are preserved verbatim:
  * target_type is `ipc_bill` and target_id is the bill — submission,
    certification and payment are embedded lifecycle data with no stable entity
    ids, so no child-event model is invented here;
  * IPC does not freeze evidence (`frozen=False` on the adapter).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from rbac_backend.core.permissions import Permissions
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
        Permissions.IPC_VIEW,
        Permissions.IPC_EDIT,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(
        id="operator-1", organization_id="org-1", permissions=granted
    )


def _seed(**document_overrides) -> _Database:
    db = _Database()
    db.ipc_bills = _Collection(
        "ipc_bills",
        [
            {
                "_id": "ipc-1",
                "ipc_number": "IPC-07",
                "organization_id": "org-1",
                "project_id": "project-1",
                "status": "submitted",
                "linked_document_ids": ["doc-1"],
            }
        ],
    )
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
        "module": "ipc",
        "selections": [
            {
                "target_id": "ipc-1",
                "document_id": "doc-1",
                "relationship_role": "invoice",
            }
        ],
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
async def test_an_operator_adjudicated_role_migrates_the_candidate(monkeypatch) -> None:
    db = _seed()

    result = await _apply(db, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "backfilled"
    assert outcome["target_type"] == "ipc_bill"
    assert outcome["relationship_role"] == "invoice"

    links = _active_links(db)
    assert len(links) == 1
    assert links[0]["target_type"] == "ipc_bill"
    assert links[0]["target_id"] == "ipc-1"
    assert links[0]["document_id"] == "doc-1"
    assert links[0]["relationship_role"] == "invoice"
    assert links[0]["source"] == "migration"

    metadata = links[0].get("metadata") or {}
    assert metadata.get("legacy_field") == "linked_document_ids"
    # The classifier did NOT derive this role; provenance must say so.
    assert metadata.get("role_source") == "operator"

    # Legacy array is preserved.
    assert db.ipc_bills.documents[0]["linked_document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_without_an_adjudicated_role_nothing_is_written(monkeypatch) -> None:
    """`ambiguous_role` is never an implicit fallback."""
    db = _seed()

    result = await _apply(
        db,
        monkeypatch,
        selections=[{"target_id": "ipc-1", "document_id": "doc-1"}],
    )

    assert result["results"][0]["status"] == "role_required"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_role_outside_the_ipc_vocabulary_is_rejected(monkeypatch) -> None:
    db = _seed()

    result = await _apply(
        db,
        monkeypatch,
        selections=[
            {
                "target_id": "ipc-1",
                "document_id": "doc-1",
                "relationship_role": "policy",
            }
        ],
    )

    assert result["results"][0]["status"] == "rejected"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_different_controlled_role_is_equally_valid(monkeypatch) -> None:
    """The operator adjudicates; the system has no preferred answer."""
    db = _seed()

    result = await _apply(
        db,
        monkeypatch,
        selections=[
            {
                "target_id": "ipc-1",
                "document_id": "doc-1",
                "relationship_role": "supporting_document",
            }
        ],
    )

    assert result["results"][0]["status"] == "backfilled"
    assert _active_links(db)[0]["relationship_role"] == "supporting_document"


# -- authority before role --------------------------------------------------


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
async def test_an_unusable_document_is_refused_on_authority_not_role(
    monkeypatch, fields: dict, expected_finding: str
) -> None:
    """A blocked Document must not be reported as `role_required` — the
    authority verdict must not be masked by role ambiguity."""
    db = _seed(**fields)

    result = await _apply(
        db,
        monkeypatch,
        selections=[{"target_id": "ipc-1", "document_id": "doc-1"}],
    )

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert expected_finding in outcome["findings"]
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_operationally_failed_document_is_still_migratable(monkeypatch) -> None:
    """Model B: a worker crash is operational, not adverse."""
    db = _seed(processing_status="failed")

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "backfilled"
    assert len(_active_links(db)) == 1


@pytest.mark.asyncio
async def test_a_missing_document_is_refused(monkeypatch) -> None:
    db = _seed()
    db.documents.documents.clear()

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "requires_manual_review"
    assert _active_links(db) == []


# -- target authority -------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ipc_outside_the_requested_scope_is_refused(monkeypatch) -> None:
    db = _seed()
    db.ipc_bills.documents[0]["organization_id"] = "org-2"
    db.documents.documents[0]["organization_id"] = "org-2"

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "out_of_scope"
    assert _active_links(db) == []


# -- orchestration ----------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(monkeypatch) -> None:
    db = _seed()

    result = await _apply(db, monkeypatch, dry_run=True)

    assert result["results"][0]["status"] == "eligible"
    assert result["results"][0]["relationship_role"] == "invoice"
    assert db.entity_document_links.documents == []
    assert db.audit_events.documents == []
    assert db.ipc_bills.documents[0]["linked_document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_a_rerun_is_idempotent(monkeypatch) -> None:
    db = _seed()

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
    db = _seed()
    await _apply(db, monkeypatch)
    link = _active_links(db)[0]
    link["removed_at"] = "2026-08-23T00:00:00Z"
    link["removal_reason"] = "Wrong bill"

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "previously_removed"
    assert result["results"][0]["removal_reason"] == "Wrong bill"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_flat_legacy_id_never_creates_two_roles_by_itself(
    monkeypatch,
) -> None:
    """IPC permits the same Document under different roles, but a single flat
    legacy id must only ever produce the ONE role the operator adjudicated.
    Re-adjudicating the same legacy id under a second role must not mint a
    second relationship out of the same flat membership."""
    db = _seed()

    first = await _apply(db, monkeypatch)
    second = await _apply(
        db,
        monkeypatch,
        selections=[
            {
                "target_id": "ipc-1",
                "document_id": "doc-1",
                "relationship_role": "certified_ipc",
            }
        ],
    )

    assert first["results"][0]["status"] == "backfilled"
    # A second, different adjudication of the SAME legacy candidate is a
    # conflict to report — not a second migration-derived relationship.
    assert second["results"][0]["status"] == "role_conflict"
    assert second["results"][0]["existing_role"] == "invoice"
    links = _active_links(db)
    assert len(links) == 1
    assert [row["relationship_role"] for row in links] == ["invoice"]


@pytest.mark.asyncio
async def test_an_ordinary_user_cannot_backfill_ipc(monkeypatch) -> None:
    from fastapi import HTTPException

    db = _seed()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(
            db,
            monkeypatch,
            current_user=_operator(Permissions.IPC_EDIT, Permissions.DOCUMENT_VIEW),
        )

    assert excinfo.value.status_code == 403
    assert _active_links(db) == []


# -- candidate lease: deterministic, not raced ------------------------------
#
# The non-terminal `in_progress` answer used to be proven only by a real-Mongo
# race, so it was proven only when the machine happened to be slow enough.
# These pin the same property from the claim state itself, so the guarantee
# holds regardless of interleaving.


def _seed_with_claim(*, status: str, lease_minutes: int) -> _Database:
    from datetime import datetime, timedelta, timezone

    from rbac_backend.routers import legacy_relationship_backfill as backfill_router
    from rbac_backend.services.legacy_relationship_backfill import (
        BACKFILL_CLAIMS_COLLECTION,
        LegacyRelationshipBackfillService,
    )

    db = _seed()
    # Built by the production identity function, so the test can never drift
    # from the real claim key.
    claim_id = LegacyRelationshipBackfillService._candidate_claim_id(
        backfill_router._MODULES["ipc"],
        {"legacy_field": "linked_document_ids"},
        "ipc_bill",
        "ipc-1",
        "doc-1",
    )
    now = datetime.now(timezone.utc)
    setattr(
        db,
        BACKFILL_CLAIMS_COLLECTION,
        _Collection(
            BACKFILL_CLAIMS_COLLECTION,
            [
                {
                    "_id": claim_id,
                    "status": status,
                    "owner_token": "another-run",
                    "operator_id": "other-operator",
                    "relationship_role": "invoice",
                    "target_type": "ipc_bill",
                    "target_id": "ipc-1",
                    "document_id": "doc-1",
                    "lease_expires_at": now + timedelta(minutes=lease_minutes),
                }
            ],
        ),
    )
    return db


@pytest.mark.asyncio
async def test_a_live_lease_held_by_another_run_is_reported_non_terminally(
    monkeypatch,
) -> None:
    """While another run holds the candidate the outcome is genuinely unknown.

    A terminal status here would be a success-shaped answer for a relationship
    that may never exist.
    """
    db = _seed_with_claim(status="processing", lease_minutes=10)

    result = await _apply(db, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "in_progress"
    assert _active_links(db) == []
    assert db.audit_events.documents == []


@pytest.mark.asyncio
async def test_a_completed_claim_for_another_role_conflicts_terminally(
    monkeypatch,
) -> None:
    """Once the winner has confirmed, the answer is known: refuse it terminally
    rather than leaving the operator polling forever."""
    db = _seed_with_claim(status="complete", lease_minutes=10)

    result = await _apply(
        db,
        monkeypatch,
        selections=[
            {
                "target_id": "ipc-1",
                "document_id": "doc-1",
                "relationship_role": "certified_ipc",
            }
        ],
    )

    outcome = result["results"][0]
    assert outcome["status"] == "role_conflict"
    assert outcome["existing_role"] == "invoice"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_expired_lease_is_reclaimed_rather_than_blocking_forever(
    monkeypatch,
) -> None:
    """A run that died mid-adjudication must not lock the candidate out."""
    db = _seed_with_claim(status="processing", lease_minutes=-30)

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "backfilled"
    assert len(_active_links(db)) == 1
