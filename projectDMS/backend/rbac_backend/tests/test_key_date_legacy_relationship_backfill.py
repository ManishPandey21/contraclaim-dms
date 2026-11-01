"""Key Date / Milestone / EOT adapter for the unified legacy backfill.

Key Date is structurally UNLIKE Bank Guarantee. BG kept evidence on the parent
with no event provenance; Key Date stores `linked_document_ids` on the EVENT
records themselves, each of which has a stable `_id` and a registered adapter:

  * `key_date_achievements`      -> key_date_achievement
  * `key_date_eot_submissions`   -> eot_submission
  * `key_date_eot_determinations`-> eot_determination

For those three, exact event ownership is mechanically known — the document is
already on the event row — so they are WRITE_BACKFILL and only the ROLE is
ambiguous (operator adjudicates, as for Insurance/IPC).

Two further sources carry no provable event ownership and are INVENTORY_ONLY:

  * `key_date_milestones.linked_document_ids` — parent-level. A parent document
    could be achievement, submission, determination or plain correspondence.
    Defaulting it to achievement because achievement is simplest would be
    fabricated provenance.
  * `key_date_eot_applications` — the deprecated milestone-EOT compatibility
    shape, retained transitionally. It must not become new authority.

Charter: never collapse contractual event evidence onto the wrong event.
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
        Permissions.KEYDATE_VIEW,
        Permissions.KEYDATE_EDIT,
        Permissions.KEYDATE_ACHIEVEMENT,
        Permissions.KEYDATE_EOT_SUBMIT,
        Permissions.KEYDATE_EOT_DETERMINE,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(
        id="operator-1", organization_id="org-1", permissions=granted
    )


def _seed(**document_overrides) -> _Database:
    """Milestone M1 with achievement A, submissions S1/S2, determinations D1/D2.

    Each event owns its OWN document, so cross-event leakage is detectable.
    """
    db = _Database()
    scope = {"organization_id": "org-1", "project_id": "project-1"}
    db.key_date_milestones = _Collection(
        "key_date_milestones",
        [{"_id": "M1", "title": "Sectional completion", **scope,
          "linked_document_ids": ["doc-parent"]}],
    )
    db.key_date_achievements = _Collection(
        "key_date_achievements",
        [{"_id": "A", "milestone_id": "M1", **scope,
          "linked_document_ids": ["doc-A"]}],
    )
    db.key_date_eot_submissions = _Collection(
        "key_date_eot_submissions",
        [
            {"_id": "S1", "key_date_id": "M1", "contract_id": "primary", **scope,
             "revision_label": "R1", "linked_document_ids": ["doc-S1"]},
            {"_id": "S2", "key_date_id": "M1", "contract_id": "primary", **scope,
             "revision_label": "R2", "linked_document_ids": ["doc-S2"]},
        ],
    )
    db.key_date_eot_determinations = _Collection(
        "key_date_eot_determinations",
        [
            {"_id": "D1", "key_date_id": "M1", "contract_id": "primary", **scope,
             "linked_document_ids": ["doc-D1"]},
            {"_id": "D2", "key_date_id": "M1", "contract_id": "primary", **scope,
             "linked_document_ids": ["doc-D2"]},
        ],
    )
    db.key_date_eot_applications = _Collection(
        "key_date_eot_applications",
        [{"_id": "L1", "milestone_id": "M1", **scope,
          "linked_document_ids": ["doc-legacy"]}],
    )
    # EOT events only resolve against a frozen baseline (accepted lifecycle).
    db.key_date_baselines = _Collection(
        "key_date_baselines",
        [{"_id": "B1", **scope, "contract_id": "primary", "status": "frozen"}],
    )
    db.key_date_eot_submission_items = _Collection("key_date_eot_submission_items", [])
    db.key_date_eot_determination_items = _Collection(
        "key_date_eot_determination_items", []
    )

    for doc_id in ("doc-parent", "doc-A", "doc-S1", "doc-S2", "doc-D1", "doc-D2",
                   "doc-legacy"):
        row = {
            "_id": doc_id, "filename": f"{doc_id}.pdf", **scope,
            "lifecycle_state": "active", "processing_status": "completed",
            "duplicate_status": "unique", "current_version_id": "version-1",
        }
        row.update(document_overrides)
        db.documents.documents.append(row)
    return db


def _active_links(db) -> list:
    return [
        row for row in db.entity_document_links.documents if row.get("removed_at") is None
    ]


async def _apply(db, monkeypatch, *, module="key_date", **overrides):
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)

    kwargs = {
        "request": _request(),
        "module": module,
        "selections": [
            {"target_id": "S1", "document_id": "doc-S1",
             "relationship_role": "eot_submission"}
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


async def _inventory(db, monkeypatch, *, module="key_date", **overrides):
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)

    kwargs = {
        "request": _request(),
        "module": module,
        "org_id": "org-1",
        "project_id": "project-1",
        "db": db,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(overrides)
    return await backfill_router.inventory_legacy_relationship_backfill(**kwargs)


@pytest.mark.asyncio
async def test_an_eot_submission_document_migrates_to_its_exact_submission(
    monkeypatch,
) -> None:
    """The document is already ON submission S1, so its event is known; only the
    role needed adjudication."""
    db = _seed()

    result = await _apply(db, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "backfilled"
    assert outcome["target_type"] == "eot_submission"
    assert outcome["target_id"] == "S1"

    links = _active_links(db)
    assert len(links) == 1
    assert links[0]["target_type"] == "eot_submission"
    assert links[0]["target_id"] == "S1"
    assert links[0]["document_id"] == "doc-S1"
    assert links[0]["relationship_role"] == "eot_submission"
    assert links[0]["source"] == "migration"

    # Legacy array preserved.
    assert db.key_date_eot_submissions.documents[0]["linked_document_ids"] == ["doc-S1"]


@pytest.mark.parametrize(
    "target_id,document_id,role,target_type",
    [
        pytest.param("A", "doc-A", "completion_certificate",
                     "key_date_achievement", id="achievement"),
        pytest.param("S1", "doc-S1", "eot_submission", "eot_submission", id="S1"),
        pytest.param("S2", "doc-S2", "eot_submission", "eot_submission", id="S2"),
        pytest.param("D1", "doc-D1", "eot_determination", "eot_determination", id="D1"),
        pytest.param("D2", "doc-D2", "eot_determination", "eot_determination", id="D2"),
    ],
)
@pytest.mark.asyncio
async def test_each_document_lands_on_exactly_its_own_event(
    monkeypatch, target_id, document_id, role, target_type
) -> None:
    db = _seed()

    result = await _apply(
        db, monkeypatch,
        selections=[{"target_id": target_id, "document_id": document_id,
                     "relationship_role": role}],
    )

    assert result["results"][0]["status"] == "backfilled"
    links = _active_links(db)
    assert len(links) == 1
    assert (links[0]["target_type"], links[0]["target_id"], links[0]["document_id"]) == (
        target_type, target_id, document_id
    )


@pytest.mark.asyncio
async def test_no_cross_event_leakage_across_the_whole_milestone(monkeypatch) -> None:
    """DS1 must never appear on S2/D1/parent; DD1 never on S1/D2/parent."""
    db = _seed()

    for target_id, document_id, role in (
        ("A", "doc-A", "completion_certificate"),
        ("S1", "doc-S1", "eot_submission"),
        ("S2", "doc-S2", "eot_submission"),
        ("D1", "doc-D1", "eot_determination"),
        ("D2", "doc-D2", "eot_determination"),
    ):
        result = await _apply(
            db, monkeypatch,
            selections=[{"target_id": target_id, "document_id": document_id,
                         "relationship_role": role}],
        )
        assert result["results"][0]["status"] == "backfilled", (target_id, result)

    owned = sorted((row["target_id"], row["document_id"]) for row in _active_links(db))
    assert owned == [
        ("A", "doc-A"), ("D1", "doc-D1"), ("D2", "doc-D2"),
        ("S1", "doc-S1"), ("S2", "doc-S2"),
    ]
    # Nothing landed on the milestone parent.
    assert not [row for row in _active_links(db) if row["target_type"] == "key_date"]


@pytest.mark.asyncio
async def test_the_milestone_parent_array_is_not_writable(monkeypatch) -> None:
    """Parent evidence could be achievement, submission, determination or plain
    correspondence — it must never be forced onto an event."""
    from fastapi import HTTPException

    db = _seed()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(
            db, monkeypatch, module="key_date_legacy",
            selections=[{"target_id": "M1", "document_id": "doc-parent",
                         "relationship_role": "supporting_document"}],
        )

    assert excinfo.value.status_code == 409
    assert "inventory-only" in excinfo.value.detail
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_the_parent_array_is_still_visible_for_reconciliation(
    monkeypatch,
) -> None:
    db = _seed()

    report = await _inventory(db, monkeypatch, module="key_date_legacy")

    parent = [
        row for row in report["candidates"]
        if row.get("source_kind") == "legacy_key_date_parent_array"
    ]
    assert len(parent) == 1
    assert parent[0]["document_id"] == "doc-parent"
    assert "ambiguous_event" in parent[0]["findings"]


@pytest.mark.asyncio
async def test_the_deprecated_eot_application_shape_is_not_writable(
    monkeypatch,
) -> None:
    """Deprecated milestone-EOT compatibility must not become new authority."""
    from fastapi import HTTPException

    db = _seed()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(
            db, monkeypatch, module="key_date_legacy",
            selections=[{"target_id": "L1", "document_id": "doc-legacy",
                         "relationship_role": "supporting_document"}],
        )

    assert excinfo.value.status_code == 409
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_the_writable_module_cannot_reach_the_ambiguous_sources(
    monkeypatch,
) -> None:
    """source_kinds scoping means a parent-array candidate is simply not a
    candidate of the writable module."""
    db = _seed()

    result = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "M1", "document_id": "doc-parent",
                     "relationship_role": "supporting_document"}],
    )

    assert result["results"][0]["status"] == "not_found"
    assert _active_links(db) == []


@pytest.mark.parametrize(
    "fields,expected",
    [
        pytest.param({"processing_status": "human_review_required"},
                     "blocked_document", id="human_review"),
        pytest.param({"duplicate_status": "duplicate"}, "blocked_document", id="duplicate"),
        pytest.param({"lifecycle_state": "deleted"}, "deleted_document", id="deleted"),
        pytest.param({"project_id": "project-2"}, "cross_project", id="cross_project"),
        pytest.param({"organization_id": "org-2"}, "cross_organisation", id="cross_org"),
    ],
)
@pytest.mark.asyncio
async def test_an_unusable_document_is_refused_on_authority_not_role(
    monkeypatch, fields, expected
) -> None:
    db = _seed(**fields)

    result = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "S1", "document_id": "doc-S1"}],
    )

    outcome = result["results"][0]
    assert outcome["status"] in {"requires_manual_review", "out_of_scope"}
    if outcome["status"] == "requires_manual_review":
        assert expected in outcome["findings"]
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_operationally_failed_document_still_migrates(monkeypatch) -> None:
    """Model B."""
    db = _seed(processing_status="failed")

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "backfilled"


@pytest.mark.asyncio
async def test_without_a_role_nothing_is_written(monkeypatch) -> None:
    db = _seed()

    result = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "S1", "document_id": "doc-S1"}],
    )

    assert result["results"][0]["status"] == "role_required"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_determination_role_is_illegal_on_a_submission(monkeypatch) -> None:
    """The submission/determination distinction is contractual and enforced by
    the role matrix on each adapter."""
    db = _seed()

    result = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "S1", "document_id": "doc-S1",
                     "relationship_role": "eot_determination"}],
    )

    assert result["results"][0]["status"] == "rejected"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_achievement_role_is_illegal_on_a_determination(monkeypatch) -> None:
    db = _seed()

    result = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "D1", "document_id": "doc-D1",
                     "relationship_role": "completion_certificate"}],
    )

    assert result["results"][0]["status"] == "rejected"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_second_role_for_the_same_candidate_conflicts(monkeypatch) -> None:
    db = _seed()

    first = await _apply(db, monkeypatch)
    second = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "S1", "document_id": "doc-S1",
                     "relationship_role": "supporting_document"}],
    )

    assert first["results"][0]["status"] == "backfilled"
    assert second["results"][0]["status"] == "role_conflict"
    assert second["results"][0]["existing_role"] == "eot_submission"
    assert len(_active_links(db)) == 1


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(monkeypatch) -> None:
    db = _seed()

    result = await _apply(db, monkeypatch, dry_run=True)

    assert result["results"][0]["status"] == "eligible"
    assert db.entity_document_links.documents == []
    assert db.audit_events.documents == []
    assert db.key_date_eot_submissions.documents[0]["linked_document_ids"] == ["doc-S1"]


@pytest.mark.asyncio
async def test_a_rerun_is_idempotent(monkeypatch) -> None:
    db = _seed()

    first = await _apply(db, monkeypatch)
    second = await _apply(db, monkeypatch)

    assert first["results"][0]["status"] == "backfilled"
    assert second["results"][0]["status"] == "already_canonical"
    assert len(_active_links(db)) == 1


@pytest.mark.asyncio
async def test_a_removed_relationship_is_not_resurrected(monkeypatch) -> None:
    db = _seed()
    await _apply(db, monkeypatch)
    link = _active_links(db)[0]
    link["removed_at"] = "2026-08-24T00:00:00Z"
    link["removal_reason"] = "Wrong submission"

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "previously_removed"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_the_audit_names_the_exact_event(monkeypatch) -> None:
    db = _seed()

    await _apply(db, monkeypatch)

    events = [row for row in db.audit_events.documents
              if row.get("action") == "legacy_relationship.backfilled"]
    assert len(events) == 1
    assert events[0]["resource_type"] == "eot_submission"
    assert events[0]["resource_id"] == "S1"


@pytest.mark.asyncio
async def test_an_ordinary_user_cannot_backfill_key_dates(monkeypatch) -> None:
    from fastapi import HTTPException

    db = _seed()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(db, monkeypatch, current_user=_operator(
            Permissions.KEYDATE_EOT_SUBMIT, Permissions.DOCUMENT_VIEW))

    assert excinfo.value.status_code == 403
    assert _active_links(db) == []


# -- one Document, several events -------------------------------------------


@pytest.mark.asyncio
async def test_one_document_cited_by_two_events_yields_two_distinct_links(
    monkeypatch,
) -> None:
    """A single letter can legitimately be evidence for a submission AND for the
    determination that answers it. Those are two relationships, never one."""
    db = _seed()
    db.key_date_eot_submissions.documents[0]["linked_document_ids"] = ["doc-shared"]
    db.key_date_eot_determinations.documents[0]["linked_document_ids"] = ["doc-shared"]
    db.documents.documents.append(
        {"_id": "doc-shared", "filename": "shared.pdf",
         "organization_id": "org-1", "project_id": "project-1",
         "lifecycle_state": "active", "processing_status": "completed",
         "duplicate_status": "unique", "current_version_id": "version-1"}
    )

    first = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "S1", "document_id": "doc-shared",
                     "relationship_role": "eot_submission"}],
    )
    second = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "D1", "document_id": "doc-shared",
                     "relationship_role": "eot_determination"}],
    )

    assert first["results"][0]["status"] == "backfilled"
    assert second["results"][0]["status"] == "backfilled"
    owned = sorted(
        (row["target_type"], row["target_id"]) for row in _active_links(db)
    )
    assert owned == [("eot_determination", "D1"), ("eot_submission", "S1")]


@pytest.mark.asyncio
async def test_migrating_one_event_does_not_migrate_the_other(monkeypatch) -> None:
    """Selection is per candidate; a shared Document must not sweep in siblings."""
    db = _seed()
    db.key_date_eot_submissions.documents[0]["linked_document_ids"] = ["doc-shared"]
    db.key_date_eot_determinations.documents[0]["linked_document_ids"] = ["doc-shared"]
    db.documents.documents.append(
        {"_id": "doc-shared", "filename": "shared.pdf",
         "organization_id": "org-1", "project_id": "project-1",
         "lifecycle_state": "active", "processing_status": "completed",
         "duplicate_status": "unique", "current_version_id": "version-1"}
    )

    await _apply(
        db, monkeypatch,
        selections=[{"target_id": "S1", "document_id": "doc-shared",
                     "relationship_role": "eot_submission"}],
    )

    links = _active_links(db)
    assert len(links) == 1
    assert links[0]["target_id"] == "S1"


# -- per-event authorization -------------------------------------------------


@pytest.mark.asyncio
async def test_submission_rights_do_not_authorize_a_determination(monkeypatch) -> None:
    """The contractual asymmetry is preserved: whoever may record a contractor
    submission may not thereby record the engineer's determination."""
    db = _seed()
    operator = _operator(
        Permissions.DMS_ADMIN,
        Permissions.KEYDATE_VIEW,
        Permissions.KEYDATE_EOT_SUBMIT,
        Permissions.DOCUMENT_VIEW,
    )

    allowed = await _apply(
        db, monkeypatch, current_user=operator,
        selections=[{"target_id": "S1", "document_id": "doc-S1",
                     "relationship_role": "eot_submission"}],
    )
    refused = await _apply(
        db, monkeypatch, current_user=operator,
        selections=[{"target_id": "D1", "document_id": "doc-D1",
                     "relationship_role": "eot_determination"}],
    )

    assert allowed["results"][0]["status"] == "backfilled"
    assert refused["results"][0]["status"] == "rejected"
    assert [row["target_id"] for row in _active_links(db)] == ["S1"]


@pytest.mark.asyncio
async def test_determination_rights_do_not_authorize_an_achievement(
    monkeypatch,
) -> None:
    db = _seed()
    operator = _operator(
        Permissions.DMS_ADMIN,
        Permissions.KEYDATE_VIEW,
        Permissions.KEYDATE_EOT_DETERMINE,
        Permissions.DOCUMENT_VIEW,
    )

    result = await _apply(
        db, monkeypatch, current_user=operator,
        selections=[{"target_id": "A", "document_id": "doc-A",
                     "relationship_role": "completion_certificate"}],
    )

    assert result["results"][0]["status"] == "rejected"
    assert _active_links(db) == []


# -- freeze / version pinning ------------------------------------------------


@pytest.mark.asyncio
async def test_a_locked_submission_is_not_backfilled(monkeypatch) -> None:
    """A locked submission is pinned evidence; backfill must not mutate it."""
    db = _seed()
    db.key_date_eot_submissions.documents[0]["locked_at"] = "2026-08-01T00:00:00Z"

    result = await _apply(db, monkeypatch)

    assert result["results"][0]["status"] == "frozen_target"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_frozen_determination_is_not_backfilled(monkeypatch) -> None:
    db = _seed()
    db.key_date_eot_determinations.documents[0]["frozen_at"] = "2026-08-01T00:00:00Z"

    result = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "D1", "document_id": "doc-D1",
                     "relationship_role": "eot_determination"}],
    )

    assert result["results"][0]["status"] == "frozen_target"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_freezing_one_event_leaves_its_siblings_migratable(monkeypatch) -> None:
    """Freeze is per event, not per milestone."""
    db = _seed()
    db.key_date_eot_submissions.documents[0]["locked_at"] = "2026-08-01T00:00:00Z"

    frozen = await _apply(db, monkeypatch)
    sibling = await _apply(
        db, monkeypatch,
        selections=[{"target_id": "S2", "document_id": "doc-S2",
                     "relationship_role": "eot_submission"}],
    )

    assert frozen["results"][0]["status"] == "frozen_target"
    assert sibling["results"][0]["status"] == "backfilled"
    assert [row["target_id"] for row in _active_links(db)] == ["S2"]


# -- inventory shape ---------------------------------------------------------


@pytest.mark.asyncio
async def test_inventory_reports_every_event_source_kind(monkeypatch) -> None:
    db = _seed()

    report = await _inventory(db, monkeypatch)

    kinds = {row["source_kind"] for row in report["candidates"]}
    assert kinds == {
        "legacy_key_date_achievement_array",
        "legacy_eot_submission_array",
        "legacy_eot_determination_array",
    }
    assert all(row["legacy_field"] == "linked_document_ids"
               for row in report["candidates"])


@pytest.mark.asyncio
async def test_an_unmapped_source_stays_visible_and_stays_unwritable(
    monkeypatch,
) -> None:
    """A future target type that nobody classified must not vanish.

    `source_kind=None` would belong to no registered `source_kinds`, so the row
    would be invisible to the writable module AND to the reconciliation module
    — evidence disappearing rather than being refused, which is the exact
    silent-success shape this programme exists to prevent.
    """
    from rbac_backend.services import key_date_document_link_migration as migration

    db = _seed()
    monkeypatch.setitem(
        migration.SOURCE_KIND_BY_TARGET, "eot_submission", "eot_submission"
    )
    monkeypatch.delitem(migration.SOURCE_KIND_BY_TARGET, "eot_submission")

    report = await _inventory(db, monkeypatch, module="key_date_legacy")

    unclassified = [
        row for row in report["candidates"]
        if row["source_kind"] == migration.UNCLASSIFIED_SOURCE_KIND
    ]
    assert {row["target_id"] for row in unclassified} == {"S1", "S2"}
    assert migration.UNCLASSIFIED_SOURCE_KIND in migration.INVENTORY_ONLY_SOURCE_KINDS
    assert (
        migration.UNCLASSIFIED_SOURCE_KIND not in migration.EVENT_PROVEN_SOURCE_KINDS
    )


@pytest.mark.asyncio
async def test_inventory_never_marks_an_event_candidate_ambiguous(monkeypatch) -> None:
    """ambiguous_event is the one finding an operator role can never resolve, so
    it must not appear on sources whose event identity is physically proven."""
    db = _seed()

    report = await _inventory(db, monkeypatch)

    assert report["candidates"]
    for row in report["candidates"]:
        assert "ambiguous_event" not in row["findings"], row
        assert row["target_id"]
