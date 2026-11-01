"""Bank Guarantee legacy evidence — RECONCILIATION / INVENTORY ONLY.

Repository-defined BG legacy evidence is parent-level (`bank_guarantees.
linked_document_ids`) and carries no exact event provenance (see
test_bank_guarantee_legacy_premise). It therefore cannot be auto-mapped to a
`bank_guarantee_event` without inventing ownership, so BG is registered
INVENTORY_ONLY: its classifier surfaces every candidate for manual review, but
`apply` refuses to write.

Charter: never manufacture contractual event provenance.

Two layers are tested here:
  * the operator control plane — inventory works, apply is refused;
  * the classifier itself — parent evidence stays ambiguous_event, and only a
    NONSTANDARD/HISTORICAL row that physically carries a document id AND a
    uniquely-matching revision resolves to an exact event (reconciliation
    capability only, never a claim that normal BG data is migratable).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from rbac_backend.core.permissions import Permissions
from rbac_backend.services.bank_guarantee_document_link_migration import (
    classify_legacy_bank_guarantee_links,
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
        Permissions.BG_VIEW,
        Permissions.BG_EDIT,
        Permissions.BG_EXTEND,
        Permissions.BG_RELEASE,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(
        id="operator-1", organization_id="org-1", permissions=granted
    )


def _seed_realistic(**document_overrides) -> _Database:
    """Realistic repository-defined legacy state: evidence on the PARENT array,
    extension history WITHOUT a document field (as the model defines it)."""
    db = _Database()
    db.bank_guarantees = _Collection(
        "bank_guarantees",
        [
            {
                "_id": "bg-1",
                "bg_number": "BG-001",
                "organization_id": "org-1",
                "project_id": "project-1",
                "bg_status": "active",
                "linked_document_ids": ["doc-1"],
            }
        ],
    )
    db.bank_guarantee_events = _Collection(
        "bank_guarantee_events",
        [
            {"_id": "E0", "bank_guarantee_id": "bg-1", "organization_id": "org-1",
             "project_id": "project-1", "event_type": "original", "sequence": 1},
            {"_id": "E2", "bank_guarantee_id": "bg-1", "organization_id": "org-1",
             "project_id": "project-1", "event_type": "extension", "sequence": 2,
             "revision_number": 1},
        ],
    )
    # Realistic history rows carry NO linked_document_ids (model-accurate).
    db.bg_extension_history = _Collection(
        "bg_extension_history",
        [
            {"_id": "hist-1", "bg_id": "bg-1", "revision_number": 1,
             "extension_date": "2026-05-01T00:00:00Z",
             "new_expiry_date": "2027-05-01T00:00:00Z"},
        ],
    )
    if document_overrides:
        db.documents.documents[0].update(document_overrides)
    return db


def _active_links(db) -> list:
    return [
        row for row in db.entity_document_links.documents if row.get("removed_at") is None
    ]


async def _inventory(db, monkeypatch, **overrides):
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)

    kwargs = {
        "request": _request(),
        "module": "bank_guarantee",
        "org_id": "org-1",
        "project_id": "project-1",
        "db": db,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(overrides)
    return await backfill_router.inventory_legacy_relationship_backfill(**kwargs)


async def _apply(db, monkeypatch, **overrides):
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)

    kwargs = {
        "request": _request(),
        "module": "bank_guarantee",
        "selections": [
            {"target_id": "E2", "document_id": "doc-1", "relationship_role": "extension"}
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


# -- operator control plane: inventory works, apply refused -----------------


@pytest.mark.asyncio
async def test_realistic_parent_evidence_is_visible_in_inventory(monkeypatch) -> None:
    """Ambiguous evidence must not disappear just because BG is not writable:
    the operator still sees it, with authority resolved, for manual review."""
    db = _seed_realistic()

    report = await _inventory(db, monkeypatch)

    parent = [
        row for row in report["candidates"]
        if row.get("source_kind") == "legacy_bg_parent_array"
    ]
    assert len(parent) == 1
    assert parent[0]["document_id"] == "doc-1"
    assert parent[0]["target_id"] is None
    assert "ambiguous_event" in parent[0]["findings"]
    assert "manual_review" in parent[0]["findings"]


@pytest.mark.parametrize(
    "fields,expected",
    [
        pytest.param({"processing_status": "human_review_required"},
                     "blocked_document", id="human_review"),
        pytest.param({"lifecycle_state": "deleted"}, "deleted_document", id="deleted"),
        pytest.param({"project_id": "project-2"}, "cross_project", id="cross_project"),
        pytest.param({"organization_id": "org-2"}, "cross_organisation", id="cross_org"),
    ],
)
@pytest.mark.asyncio
async def test_inventory_resolves_document_authority_first(
    monkeypatch, fields, expected
) -> None:
    """Authority failures outrank migration semantics even in inventory."""
    db = _seed_realistic(**fields)

    report = await _inventory(db, monkeypatch)

    parent = [
        row for row in report["candidates"]
        if row.get("source_kind") == "legacy_bg_parent_array"
    ]
    assert parent and expected in parent[0]["findings"]


@pytest.mark.asyncio
async def test_apply_is_refused_for_the_inventory_only_module(monkeypatch) -> None:
    """No operator may write through BG — the ruling requires it be impossible
    to mistake inventory for a writable backfill."""
    from fastapi import HTTPException

    db = _seed_realistic()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(
            db,
            monkeypatch,
            selections=[
                {"target_id": "bg-1", "document_id": "doc-1",
                 "relationship_role": "extension"}
            ],
        )

    assert excinfo.value.status_code == 409
    assert "inventory-only" in excinfo.value.detail
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_apply_is_refused_even_for_a_selection_that_looks_event_resolvable(
    monkeypatch,
) -> None:
    """Even a selection naming a real event id must be refused: the module has
    no write capability at all, so nothing routes to a claim or an audit."""
    from fastapi import HTTPException

    db = _seed_realistic()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(db, monkeypatch)  # selection targets event E2

    assert excinfo.value.status_code == 409
    assert _active_links(db) == []
    assert db.audit_events.documents == []


# -- classifier: no event collapse ------------------------------------------


@pytest.mark.asyncio
async def test_parent_evidence_never_resolves_to_an_event(monkeypatch) -> None:
    """The load-bearing guard: parent evidence must stay target_id=None even
    though original/submission/extension events exist to collapse onto."""
    db = _seed_realistic()

    report = await classify_legacy_bank_guarantee_links(db)

    parent = [
        row for row in report["candidates"]
        if row.get("source_kind") == "legacy_bg_parent_array"
    ]
    assert parent and parent[0]["target_id"] is None
    assert "ambiguous_event" in parent[0]["findings"]


# -- classifier: NONSTANDARD / HISTORICAL exact-revision resolution ----------
#
# These rows are NOT produced by current source (BGExtensionHistory declares no
# linked_document_ids). They model imported/hand-migrated data and exercise the
# reconciliation resolver ONLY. They are never evidence that normal BG data is
# migratable, and — because BG is inventory-only — they still cannot be written.


def _nonstandard_history_row(**over):
    row = {
        "_id": "hist-1",
        "bg_id": "bg-1",
        "revision_number": 1,
        "extension_date": "2026-05-01T00:00:00Z",
        "new_expiry_date": "2027-05-01T00:00:00Z",
        "linked_document_ids": ["doc-1"],  # NONSTANDARD HISTORICAL SHAPE
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_nonstandard_row_with_unique_revision_resolves_the_exact_event(
    monkeypatch,
) -> None:
    db = _seed_realistic()
    db.bg_extension_history.documents[0] = _nonstandard_history_row()

    report = await classify_legacy_bank_guarantee_links(db)

    ext = [
        row for row in report["candidates"]
        if row.get("source_kind") == "legacy_bg_extension_history"
    ]
    assert len(ext) == 1
    assert ext[0]["target_id"] == "E2"
    assert ext[0]["event_resolution_source"] == "extension_revision_number"


@pytest.mark.asyncio
async def test_nonstandard_row_with_ambiguous_revision_stays_unresolved(
    monkeypatch,
) -> None:
    db = _seed_realistic()
    db.bank_guarantee_events.documents.append(
        {"_id": "E2b", "bank_guarantee_id": "bg-1", "organization_id": "org-1",
         "project_id": "project-1", "event_type": "extension", "sequence": 3,
         "revision_number": 1}
    )
    db.bg_extension_history.documents[0] = _nonstandard_history_row()

    report = await classify_legacy_bank_guarantee_links(db)

    ext = [
        row for row in report["candidates"]
        if row.get("source_kind") == "legacy_bg_extension_history"
    ]
    assert ext and ext[0]["target_id"] is None
    assert "ambiguous_event" in ext[0]["findings"]


@pytest.mark.parametrize(
    "revision",
    [
        pytest.param(True, id="bool"),
        pytest.param(1.9, id="float"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param("R-1", id="non_numeric"),
        pytest.param(None, id="missing"),
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_revision_never_fabricates_ownership(
    monkeypatch, revision
) -> None:
    """HIGH-2 contract preserved: no coercion into event 1, no tenant-wide 500
    from one malformed row."""
    db = _seed_realistic()
    db.bg_extension_history.documents[0] = _nonstandard_history_row(
        revision_number=revision
    )

    report = await classify_legacy_bank_guarantee_links(db)

    ext = [
        row for row in report["candidates"]
        if row.get("source_kind") == "legacy_bg_extension_history"
    ]
    assert ext and ext[0]["target_id"] is None
    assert "ambiguous_event" in ext[0]["findings"]


@pytest.mark.asyncio
async def test_a_nonstandard_row_still_fails_authority_first(monkeypatch) -> None:
    db = _seed_realistic(processing_status="human_review_required")
    db.bg_extension_history.documents[0] = _nonstandard_history_row()

    report = await classify_legacy_bank_guarantee_links(db)

    ext = [
        row for row in report["candidates"]
        if row.get("source_kind") == "legacy_bg_extension_history"
    ]
    assert ext and "blocked_document" in ext[0]["findings"]
