"""Unified legacy document-relationship backfill (F1).

Every register stored evidence as a flat `linked_document_ids` array before the
canonical `entity_document_links` model existed. Reads have since moved to the
canonical model, so those legacy rows are invisible until they are backfilled.

Legacy membership is DISCOVERY INPUT, never authority: every candidate is
re-resolved against the current canonical target, the current canonical
Document, and the current authority rules before anything is written.
"""

from __future__ import annotations

from pathlib import Path
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
        Permissions.INSURANCE_EDIT,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(
        id="operator-1", organization_id="org-1", permissions=granted
    )


def _seed_insurance_legacy_array(**document_overrides) -> _Database:
    """One Insurance policy whose evidence lives only in the legacy array."""
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
                "linked_document_ids": ["doc-1"],
            }
        ],
    )
    if document_overrides:
        db.documents.documents[0].update(document_overrides)
    return db


async def _apply(db, tmp_path: Path, monkeypatch, **overrides):
    """Drive the real operator backfill route."""
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)
    monkeypatch.setattr(
        backfill_router, "_legacy_insurance_root", lambda: tmp_path
    )

    kwargs = {
        "request": _request(),
        "module": "insurance",
        "selections": [
            {
                "target_id": "insurance-1",
                "document_id": "doc-1",
                "relationship_role": "supporting_document",
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
async def test_a_clean_legacy_linked_document_id_can_become_a_canonical_relationship(
    tmp_path: Path, monkeypatch
) -> None:
    """F1: today this candidate is stranded — valid on authority, but with no
    runnable path out of the legacy array."""
    db = _seed_insurance_legacy_array()

    result = await _apply(db, tmp_path, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "backfilled"
    assert outcome["target_type"] == "insurance"
    assert outcome["target_id"] == "insurance-1"
    assert outcome["document_id"] == "doc-1"

    # Final physical state, not helper calls.
    links = [
        row for row in db.entity_document_links.documents if row.get("removed_at") is None
    ]
    assert len(links) == 1
    assert links[0]["target_type"] == "insurance"
    assert links[0]["target_id"] == "insurance-1"
    assert links[0]["document_id"] == "doc-1"
    assert links[0]["relationship_role"] == "supporting_document"
    # Provenance is recorded, but it is audit only — never authority.
    assert links[0]["source"] == "migration"

    # The legacy array is NOT deleted in this programme.
    assert db.insurance_policies.documents[0]["linked_document_ids"] == ["doc-1"]


def _active_links(db) -> list:
    return [
        row for row in db.entity_document_links.documents if row.get("removed_at") is None
    ]


# -- authority matrix ------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operationally_failed_document_still_backfills(
    tmp_path: Path, monkeypatch
) -> None:
    """Model B: a worker crash is operational, not an adverse judgement."""
    db = _seed_insurance_legacy_array(processing_status="failed")

    result = await _apply(db, tmp_path, monkeypatch)

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
    tmp_path: Path, monkeypatch, fields: dict, expected_finding: str
) -> None:
    db = _seed_insurance_legacy_array(**fields)

    result = await _apply(db, tmp_path, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert expected_finding in outcome["findings"]
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_missing_document_is_never_backfilled(
    tmp_path: Path, monkeypatch
) -> None:
    db = _seed_insurance_legacy_array()
    db.documents.documents.clear()

    result = await _apply(db, tmp_path, monkeypatch)

    assert result["results"][0]["status"] == "requires_manual_review"
    assert _active_links(db) == []


# -- semantics: never guess ------------------------------------------------


@pytest.mark.asyncio
async def test_a_candidate_without_an_operator_chosen_role_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """The system never invents a role for an undifferentiated legacy array."""
    db = _seed_insurance_legacy_array()

    result = await _apply(
        db,
        tmp_path,
        monkeypatch,
        selections=[{"target_id": "insurance-1", "document_id": "doc-1"}],
    )

    assert result["results"][0]["status"] == "role_required"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_role_outside_the_target_vocabulary_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """The entity adapter owns the vocabulary, not the backfill service — and
    the refusal stays a per-item outcome instead of aborting the batch."""
    db = _seed_insurance_legacy_array()

    result = await _apply(
        db,
        tmp_path,
        monkeypatch,
        selections=[
            {
                "target_id": "insurance-1",
                "document_id": "doc-1",
                "relationship_role": "renewal",
            }
        ],
    )

    assert result["results"][0]["status"] == "rejected"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_one_bad_selection_does_not_abort_the_rest_of_the_batch(
    tmp_path: Path, monkeypatch
) -> None:
    db = _seed_insurance_legacy_array()

    result = await _apply(
        db,
        tmp_path,
        monkeypatch,
        selections=[
            {
                "target_id": "insurance-1",
                "document_id": "doc-1",
                "relationship_role": "renewal",
            },
            {
                "target_id": "insurance-1",
                "document_id": "doc-1",
                "relationship_role": "supporting_document",
            },
        ],
    )

    statuses = [row["status"] for row in result["results"]]
    assert statuses == ["rejected", "backfilled"]
    assert len(_active_links(db)) == 1


@pytest.mark.asyncio
async def test_a_deliberately_removed_relationship_is_not_resurrected(
    tmp_path: Path, monkeypatch
) -> None:
    """Legacy membership must never overturn a canonical human decision."""
    db = _seed_insurance_legacy_array()
    await _apply(db, tmp_path, monkeypatch)
    link = _active_links(db)[0]
    link["removed_at"] = "2026-08-23T00:00:00Z"
    link["removal_reason"] = "Attached to the wrong policy"

    result = await _apply(db, tmp_path, monkeypatch)

    assert result["results"][0]["status"] == "previously_removed"
    assert result["results"][0]["removal_reason"] == "Attached to the wrong policy"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_ambiguous_event_candidate_can_never_auto_migrate(
    tmp_path: Path, monkeypatch
) -> None:
    """Guards the event-bearing registers before their adapters land: a finding
    outside the allow-list blocks the write, whatever the classification says."""
    import dataclasses

    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    db = _seed_insurance_legacy_array()
    real = backfill_router._MODULES["insurance"].classify

    async def _ambiguous(db_arg, **kwargs):
        report = await real(db_arg, **kwargs)
        for row in report["candidates"]:
            if row.get("source_kind") == "legacy_linked_document_id":
                row["findings"] = list(row["findings"]) + ["ambiguous_event"]
        return report

    monkeypatch.setitem(
        backfill_router._MODULES,
        "insurance",
        dataclasses.replace(backfill_router._MODULES["insurance"], classify=_ambiguous),
    )

    result = await _apply(db, tmp_path, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert outcome["blocking_findings"] == ["ambiguous_event"]
    assert _active_links(db) == []


# -- target authority ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_missing_canonical_target_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    db = _seed_insurance_legacy_array()
    candidate = dict(db.insurance_policies.documents[0])
    db.insurance_policies.documents.clear()
    # The legacy candidate still exists in a stale report, but the target is gone.
    import dataclasses

    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    real = backfill_router._MODULES["insurance"].classify

    async def _stale(db_arg, **kwargs):
        report = await real(db_arg, **kwargs)
        report["candidates"].append(
            {
                "target_type": "insurance",
                "target_id": candidate["_id"],
                "source_kind": "legacy_linked_document_id",
                "document_id": "doc-1",
                "classification": "manual_review",
                "findings": ["valid", "ambiguous_role", "manual_review"],
            }
        )
        return report

    monkeypatch.setitem(
        backfill_router._MODULES,
        "insurance",
        dataclasses.replace(backfill_router._MODULES["insurance"], classify=_stale),
    )

    result = await _apply(db, tmp_path, monkeypatch)

    assert result["results"][0]["status"] == "missing_target"
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_target_outside_the_requested_scope_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    db = _seed_insurance_legacy_array()
    db.insurance_policies.documents[0]["organization_id"] = "org-2"
    db.documents.documents[0]["organization_id"] = "org-2"

    result = await _apply(db, tmp_path, monkeypatch)

    assert result["results"][0]["status"] == "out_of_scope"
    assert _active_links(db) == []


# -- orchestration ---------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    db = _seed_insurance_legacy_array()

    result = await _apply(db, tmp_path, monkeypatch, dry_run=True)

    assert result["dry_run"] is True
    assert result["results"][0]["status"] == "eligible"
    assert _active_links(db) == []
    assert db.entity_document_links.documents == []
    assert db.audit_events.documents == []


@pytest.mark.asyncio
async def test_a_rerun_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    db = _seed_insurance_legacy_array()

    first = await _apply(db, tmp_path, monkeypatch)
    second = await _apply(db, tmp_path, monkeypatch)

    assert first["results"][0]["status"] == "backfilled"
    assert second["results"][0]["status"] == "already_canonical"
    assert len(_active_links(db)) == 1
    backfill_audits = [
        row
        for row in db.audit_events.documents
        if row.get("action") == "legacy_relationship.backfilled"
    ]
    assert len(backfill_audits) == 1


@pytest.mark.asyncio
async def test_the_backfill_is_audited_with_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    db = _seed_insurance_legacy_array()

    result = await _apply(db, tmp_path, monkeypatch)

    events = [
        row
        for row in db.audit_events.documents
        if row.get("action") == "legacy_relationship.backfilled"
    ]
    assert len(events) == 1
    event = events[0]
    assert event["actor_id"] == "operator-1"
    assert event["resource_type"] == "insurance"
    assert event["resource_id"] == "insurance-1"
    assert event["after"]["migration_run_id"] == result["migration_run_id"]
    assert event["after"]["relationship_role"] == "supporting_document"


@pytest.mark.asyncio
async def test_provenance_is_recorded_on_the_relationship(
    tmp_path: Path, monkeypatch
) -> None:
    db = _seed_insurance_legacy_array()

    await _apply(db, tmp_path, monkeypatch)

    link = _active_links(db)[0]
    assert link["source"] == "migration"
    metadata = link.get("metadata") or {}
    assert metadata.get("legacy_field") == "linked_document_ids"
    assert metadata.get("legacy_document_id") == "doc-1"
    assert metadata.get("operator_id") == "operator-1"


# -- operator control plane ------------------------------------------------


@pytest.mark.asyncio
async def test_an_actor_without_the_admin_permission_is_denied(
    tmp_path: Path, monkeypatch
) -> None:
    from fastapi import HTTPException

    db = _seed_insurance_legacy_array()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(
            db,
            tmp_path,
            monkeypatch,
            current_user=_operator(Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW),
        )

    assert excinfo.value.status_code == 403
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_step_up_is_required(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    db = _seed_insurance_legacy_array()
    monkeypatch.setattr(backfill_router, "_legacy_insurance_root", lambda: tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        await backfill_router.apply_legacy_relationship_backfill(
            request=_request(),
            module="insurance",
            selections=[
                {
                    "target_id": "insurance-1",
                    "document_id": "doc-1",
                    "relationship_role": "supporting_document",
                }
            ],
            org_id="org-1",
            project_id="project-1",
            dry_run=False,
            db=db,
            current_user=_operator(),
            policy=_OperatorPolicy(),
        )

    assert excinfo.value.status_code == 403
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_unscoped_run_is_refused(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException

    db = _seed_insurance_legacy_array()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(db, tmp_path, monkeypatch, org_id=None, project_id=None)

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_explicit_selection_is_required(tmp_path: Path, monkeypatch) -> None:
    """There is no migrate-everything mode."""
    from fastapi import HTTPException

    db = _seed_insurance_legacy_array()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(db, tmp_path, monkeypatch, selections=[])

    assert excinfo.value.status_code == 400
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_the_batch_is_bounded(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException

    db = _seed_insurance_legacy_array()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(
            db,
            tmp_path,
            monkeypatch,
            selections=[
                {
                    "target_id": f"insurance-{n}",
                    "document_id": "doc-1",
                    "relationship_role": "supporting_document",
                }
                for n in range(51)
            ],
        )

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_an_unknown_module_is_refused(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException

    db = _seed_insurance_legacy_array()

    with pytest.raises(HTTPException) as excinfo:
        await _apply(db, tmp_path, monkeypatch, module="contract_master")

    assert excinfo.value.status_code == 400


def _seed_key_date_legacy(db) -> None:
    """Realistic Key Date legacy state for the registry honesty gates.

    Evidence sits on the event rows themselves (achievement / EOT submission /
    EOT determination), which is precisely why Key Date is writable while the
    milestone parent array and the deprecated application shape are not.
    """
    scope = {"organization_id": "org-1", "project_id": "project-1"}
    db.key_date_milestones = _Collection(
        "key_date_milestones",
        [{"_id": "M1", "title": "Sectional completion", **scope,
          "linked_document_ids": ["doc-1"]}],
    )
    db.key_date_achievements = _Collection(
        "key_date_achievements",
        [{"_id": "A1", "milestone_id": "M1", **scope,
          "linked_document_ids": ["doc-1"]}],
    )
    db.key_date_eot_submissions = _Collection(
        "key_date_eot_submissions",
        [{"_id": "S1", "key_date_id": "M1", "contract_id": "primary", **scope,
          "revision_label": "R1", "linked_document_ids": ["doc-1"]}],
    )
    db.key_date_eot_determinations = _Collection(
        "key_date_eot_determinations",
        [{"_id": "D1", "key_date_id": "M1", "contract_id": "primary", **scope,
          "linked_document_ids": ["doc-1"]}],
    )
    db.key_date_eot_applications = _Collection(
        "key_date_eot_applications",
        [{"_id": "L1", "milestone_id": "M1", **scope,
          "linked_document_ids": ["doc-1"]}],
    )
    db.key_date_baselines = _Collection(
        "key_date_baselines",
        [{"_id": "B1", **scope, "contract_id": "primary", "status": "frozen"}],
    )
    db.key_date_eot_submission_items = _Collection("key_date_eot_submission_items", [])
    db.key_date_eot_determination_items = _Collection(
        "key_date_eot_determination_items", []
    )


@pytest.mark.asyncio
async def test_every_registered_module_can_actually_yield_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    """A module whose classifier does not tag rows with `source_kind` would
    authorise, scope and audit correctly while matching zero candidates — a
    seam that looks safe precisely because it does nothing. Registration must
    imply a classifier that this orchestrator can actually read."""
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    monkeypatch.setattr(backfill_router, "_legacy_insurance_root", lambda: tmp_path)
    db = _seed_insurance_legacy_array()
    # Seed a legacy row for every register that is registered, so the guard
    # measures the classifier rather than the fixture.
    db.claims.documents[0]["linked_document_ids"] = ["doc-1"]
    db.claims.documents[0]["linked_letter_ids"] = ["doc-1"]
    db.bank_guarantees = _Collection(
        "bank_guarantees",
        [
            {
                "_id": "bg-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "linked_document_ids": ["doc-1"],
            }
        ],
    )
    db.bank_guarantee_events = _Collection("bank_guarantee_events", [])
    db.bg_extension_history = _Collection("bg_extension_history", [])
    db.ipc_bills = _Collection(
        "ipc_bills",
        [
            {
                "_id": "ipc-1",
                "ipc_number": "IPC-07",
                "organization_id": "org-1",
                "project_id": "project-1",
                "linked_document_ids": ["doc-1"],
            }
        ],
    )
    _seed_key_date_legacy(db)

    for name, entry in backfill_router._MODULES.items():
        report = await entry.classify(db, **entry.classifier_kwargs())
        tagged = {
            str(row.get("source_kind") or "")
            for row in report.get("candidates", [])
        }
        assert entry.source_kinds & tagged, (
            f"module {name!r} is registered but its classifier emits no row "
            f"tagged with any of {sorted(entry.source_kinds)}"
        )


@pytest.mark.asyncio
async def test_the_backfilled_link_appears_in_forward_and_reverse_views(
    tmp_path: Path, monkeypatch
) -> None:
    import httpx
    from fastapi import FastAPI

    from rbac_backend.core.database import get_db
    from rbac_backend.core.security import get_current_user
    from rbac_backend.routers.document_relationships import router as relationship_router
    from rbac_backend.tests.test_claim_document_relationships import _user

    db = _seed_insurance_legacy_array()
    await _apply(db, tmp_path, monkeypatch)

    app = FastAPI()
    app.include_router(relationship_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = _user
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        forward = await client.get("/api/entities/insurance/insurance-1/document-links")
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert forward.status_code == 200, forward.text
    assert reverse.status_code == 200, reverse.text
    assert forward.json()["links"] == reverse.json()["links"]
    assert forward.json()["links"][0]["document_id"] == "doc-1"
    assert forward.json()["links"][0]["relationship_role"] == "supporting_document"


def test_the_backfill_never_deletes_legacy_fields() -> None:
    """Structural gate: this cycle backfills, it does not retire legacy arrays."""
    source = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "legacy_relationship_backfill.py"
    ).read_text(encoding="utf-8")

    for forbidden in ("$unset", "delete_many", "update_many"):
        assert forbidden not in source, f"{forbidden} must never appear here"

    # `delete_one` is permitted for exactly one purpose: releasing a migration
    # claim so a failed adjudication can be retried. It must never touch a
    # register collection or a legacy array.
    for line in source.splitlines():
        if "delete_one" in line:
            assert "_claims_collection()" in line, (
                f"delete_one outside the claims collection: {line.strip()}"
            )


# -- registry capability: WRITE_BACKFILL vs INVENTORY_ONLY -------------------


@pytest.mark.asyncio
async def test_a_write_backfill_module_yields_a_genuinely_migratable_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    """Candidate-presence is too weak. A module registered for WRITE_BACKFILL
    must prove realistic repository-defined legacy data produces at least one
    candidate that is actually migratable after authority + semantic resolution
    (findings within the backfillable set, with a resolvable target). BG's
    parent evidence is `ambiguous_event` for every realistic row, so BG cannot
    satisfy this and must be INVENTORY_ONLY."""
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router
    from rbac_backend.services.legacy_relationship_backfill import (
        BACKFILLABLE_FINDINGS,
        BackfillCapability,
    )

    monkeypatch.setattr(backfill_router, "_legacy_insurance_root", lambda: tmp_path)
    db = _seed_insurance_legacy_array()
    db.claims.documents[0]["linked_document_ids"] = ["doc-1"]
    db.claims.documents[0]["linked_letter_ids"] = ["doc-1"]
    db.bank_guarantees = _Collection(
        "bank_guarantees",
        [{"_id": "bg-1", "organization_id": "org-1", "project_id": "project-1",
          "linked_document_ids": ["doc-1"]}],
    )
    db.bank_guarantee_events = _Collection("bank_guarantee_events", [])
    db.bg_extension_history = _Collection("bg_extension_history", [])
    db.ipc_bills = _Collection(
        "ipc_bills",
        [{"_id": "ipc-1", "ipc_number": "IPC-07", "organization_id": "org-1",
          "project_id": "project-1", "linked_document_ids": ["doc-1"]}],
    )
    _seed_key_date_legacy(db)

    for name, entry in backfill_router._MODULES.items():
        if entry.capability is not BackfillCapability.WRITE_BACKFILL:
            continue
        report = await entry.classify(db, **entry.classifier_kwargs())
        migratable = [
            row
            for row in report.get("candidates", [])
            if str(row.get("source_kind") or "") in entry.source_kinds
            and "valid" in (row.get("findings") or [])
            and not (set(row.get("findings") or []) - BACKFILLABLE_FINDINGS)
        ]
        assert migratable, (
            f"WRITE_BACKFILL module {name!r} produced no realistically "
            f"migratable candidate — it must be INVENTORY_ONLY"
        )


def test_bank_guarantee_is_registered_inventory_only() -> None:
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router
    from rbac_backend.services.legacy_relationship_backfill import BackfillCapability

    assert (
        backfill_router._MODULES["bank_guarantee"].capability
        is BackfillCapability.INVENTORY_ONLY
    )
    assert (
        backfill_router._MODULES["key_date_legacy"].capability
        is BackfillCapability.INVENTORY_ONLY
    )
    for writable in ("claim", "claim_letter", "ipc", "insurance", "key_date"):
        assert (
            backfill_router._MODULES[writable].capability
            is BackfillCapability.WRITE_BACKFILL
        )


@pytest.mark.asyncio
async def test_no_writable_module_exposes_an_ambiguous_event_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    """The charter as an executable gate, applied to every register at once.

    `ambiguous_event` is the one finding no operator role may resolve: it means
    the legacy row does not say which contractual event owns the evidence. A
    WRITE_BACKFILL module must therefore never own a source that can produce it,
    or a future registration could silently make guesswork writable.
    """
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router
    from rbac_backend.services.legacy_relationship_backfill import BackfillCapability

    monkeypatch.setattr(backfill_router, "_legacy_insurance_root", lambda: tmp_path)
    db = _seed_insurance_legacy_array()
    db.claims.documents[0]["linked_document_ids"] = ["doc-1"]
    db.claims.documents[0]["linked_letter_ids"] = ["doc-1"]
    db.bank_guarantees = _Collection(
        "bank_guarantees",
        [{"_id": "bg-1", "organization_id": "org-1", "project_id": "project-1",
          "linked_document_ids": ["doc-1"]}],
    )
    db.bank_guarantee_events = _Collection("bank_guarantee_events", [])
    db.bg_extension_history = _Collection("bg_extension_history", [])
    db.ipc_bills = _Collection(
        "ipc_bills",
        [{"_id": "ipc-1", "ipc_number": "IPC-07", "organization_id": "org-1",
          "project_id": "project-1", "linked_document_ids": ["doc-1"]}],
    )
    _seed_key_date_legacy(db)

    for name, entry in backfill_router._MODULES.items():
        if entry.capability is not BackfillCapability.WRITE_BACKFILL:
            continue
        report = await entry.classify(db, **entry.classifier_kwargs())
        for row in report.get("candidates", []):
            if str(row.get("source_kind") or "") not in entry.source_kinds:
                continue
            assert "ambiguous_event" not in (row.get("findings") or []), (
                f"WRITE_BACKFILL module {name!r} owns a source that cannot "
                f"prove event ownership: {row}"
            )


# -- the whole programme, in one assertion ----------------------------------


#: The accepted capability of every registered legacy source.
#:
#: This is the programme-level contract. A register is writable only where the
#: legacy row itself proves which contractual target owns the evidence; where it
#: does not, the source stays visible for reconciliation and can never be
#: written. Changing any line here is a programme decision, not a refactor.
EXPECTED_CAPABILITY_MATRIX = {
    "claim": ("WRITE_BACKFILL", ["legacy_linked_document_id"]),
    "claim_letter": ("WRITE_BACKFILL", ["legacy_linked_letter_id"]),
    "ipc": ("WRITE_BACKFILL", ["legacy_linked_document_id"]),
    "insurance": ("WRITE_BACKFILL", ["legacy_linked_document_id"]),
    "key_date": (
        "WRITE_BACKFILL",
        [
            "legacy_eot_determination_array",
            "legacy_eot_submission_array",
            "legacy_key_date_achievement_array",
        ],
    ),
    "bank_guarantee": (
        "INVENTORY_ONLY",
        ["legacy_bg_extension_history", "legacy_bg_parent_array"],
    ),
    "key_date_legacy": (
        "INVENTORY_ONLY",
        [
            "legacy_eot_application_array",
            "legacy_key_date_parent_array",
            "legacy_key_date_unclassified_source",
        ],
    ),
}


def test_the_registered_capability_matrix_is_exactly_the_accepted_one() -> None:
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    actual = {
        name: (entry.capability.name, sorted(entry.source_kinds))
        for name, entry in backfill_router._MODULES.items()
    }
    assert actual == EXPECTED_CAPABILITY_MATRIX


def test_no_source_kind_is_claimed_by_two_modules() -> None:
    """A source owned by both a writable and an inventory-only module would be
    writable in practice, whatever the second registration said."""
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    seen: dict[str, str] = {}
    for name, entry in backfill_router._MODULES.items():
        for kind in entry.source_kinds:
            # A source kind may legitimately repeat across registers that use
            # the same legacy field name; the collision that matters is one kind
            # owned by two DIFFERENT capabilities.
            previous = seen.get(kind)
            if previous is not None:
                assert (
                    backfill_router._MODULES[previous].capability
                    is entry.capability
                ), f"{kind!r} is owned by both {previous!r} and {name!r} at "
                f"different capabilities"
            seen[kind] = name


def test_inventory_only_modules_refuse_apply_before_anything_else() -> None:
    """The refusal is a property of the registration, not of any module."""
    import ast
    import inspect
    import textwrap

    from rbac_backend.routers import legacy_relationship_backfill as backfill_router
    from rbac_backend.services.legacy_relationship_backfill import (
        BackfillCapability,
        LegacyRelationshipBackfillService,
    )

    source = textwrap.dedent(
        inspect.getsource(LegacyRelationshipBackfillService.apply)
    )
    method = ast.parse(source).body[0]
    statements = [
        node
        for node in method.body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    # The refusal must be the FIRST thing `apply` does, so an inventory-only
    # module cannot reach a selection, a durable claim, or an audit.
    first = statements[0]
    assert isinstance(first, ast.If), ast.dump(first)[:200]
    assert "BackfillCapability.WRITE_BACKFILL" in ast.unparse(first.test)

    inventory_only = [
        name
        for name, entry in backfill_router._MODULES.items()
        if entry.capability is BackfillCapability.INVENTORY_ONLY
    ]
    assert sorted(inventory_only) == ["bank_guarantee", "key_date_legacy"]
