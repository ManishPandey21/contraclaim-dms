from __future__ import annotations

from datetime import datetime

import pytest

from rbac_backend.migrations.v20260813_0001_key_date_eot_attribution import upgrade
from rbac_backend.tests.test_key_date_revision_workflow import _DB


async def _seed_legacy(db):
    """A project as it looked before P3/P4: no origin, no attribution, and a
    contractual date left behind by the pre-P1 blind write."""
    await db.key_date_milestones.insert_one({
        "_id": "m-1", "organization_id": "org-A", "project_id": "proj-A", "contract_id": "primary",
        "milestone_ref": "KD-01", "title": "KD-01",
        "original_planned_key_date": datetime(2026, 1, 1),
        # Wrong: left at EOT-1's later-frozen, less generous grant.
        "current_approved_key_date": datetime(2026, 5, 25),
    })
    await db.key_date_baselines.insert_one({
        "_id": "b-1", "organization_id": "org-A", "project_id": "proj-A", "contract_id": "primary",
        "status": "frozen", "revision_number": 0, "next_revision_number": 2,
        "items": [{"key_date_id": "m-1", "milestone_ref": "KD-01",
                   "original_contractual_date": datetime(2026, 1, 1)}],
    })
    for index, (submitted, days) in enumerate(
        [(datetime(2026, 3, 1), 60), (datetime(2026, 5, 1), 120)], start=1
    ):
        await db.key_date_eot_submissions.insert_one({
            "_id": f"s-{index}", "organization_id": "org-A", "project_id": "proj-A",
            "contract_id": "primary", "revision_number": index, "revision_label": f"EOT-{index}",
            "status": "locked", "locked_at": datetime(2026, 2, 1),
        })
        await db.key_date_eot_submission_items.insert_one({
            "_id": f"si-{index}", "eot_submission_id": f"s-{index}", "key_date_id": "m-1",
            "milestone_ref": "KD-01", "eot_submitted_date": submitted,
            "claimed_extension_days": days,
        })
    # EOT-2 determined first (01-Sep), EOT-1 determined later (25-May) — the G5 order.
    await db.key_date_eot_determinations.insert_one({
        "_id": "d-2", "organization_id": "org-A", "project_id": "proj-A", "contract_id": "primary",
        "eot_submission_ids": ["s-2"], "covered_revision_labels": ["EOT-2"],
        "determination_reference": "CLIENT/200", "determination_date": datetime(2026, 7, 1),
        "status": "partially_granted", "frozen_at": datetime(2026, 7, 2),
    })
    await db.key_date_eot_determination_items.insert_one({
        "_id": "di-2", "determination_id": "d-2", "key_date_id": "m-1", "milestone_ref": "KD-01",
        "eot_granted_date": datetime(2026, 9, 1), "granted_extension_days": 120,
        "determination_result": "partially_granted",
    })
    await db.key_date_eot_determinations.insert_one({
        "_id": "d-1", "organization_id": "org-A", "project_id": "proj-A", "contract_id": "primary",
        "eot_submission_ids": ["s-1", "s-2"], "covered_revision_labels": ["EOT-1", "EOT-2"],
        "determination_reference": "CLIENT/145", "determination_date": datetime(2026, 6, 1),
        "status": "partially_granted", "frozen_at": datetime(2026, 7, 20),
    })
    await db.key_date_eot_determination_items.insert_one({
        "_id": "di-1", "determination_id": "d-1", "key_date_id": "m-1", "milestone_ref": "KD-01",
        "eot_granted_date": datetime(2026, 5, 25), "granted_extension_days": 45,
        "determination_result": "partially_granted",
    })
    return db


@pytest.mark.asyncio
async def test_dry_run_reports_without_writing_anything():
    db = _DB()
    await _seed_legacy(db)

    result = await upgrade(db, dry_run=True)

    assert result.status == "dry_run"
    assert result.operations
    determination = await db.key_date_eot_determinations.find_one({"_id": "d-1"})
    milestone = await db.key_date_milestones.find_one({"_id": "m-1"})
    assert "origin" not in determination
    assert milestone["current_approved_key_date"] == datetime(2026, 5, 25)


@pytest.mark.asyncio
async def test_upgrade_backfills_origin_attribution_and_link_arrays():
    db = _DB()
    await _seed_legacy(db)

    await upgrade(db, dry_run=False)

    determination = await db.key_date_eot_determinations.find_one({"_id": "d-1"})
    assert determination["origin"] == "contractor_submission"
    assert determination["linked_document_ids"] == []
    assert determination["linked_letter_ids"] == []

    submission = await db.key_date_eot_submissions.find_one({"_id": "s-1"})
    assert submission["linked_document_ids"] == []
    assert submission["superseded_by_submission_id"] is None

    item = await db.key_date_eot_determination_items.find_one({"_id": "di-1"})
    # Historical meaning preserved: the pre-P4 rule attributed to the latest
    # covered submission, so the backfill must land on EOT-2, not EOT-1.
    assert item["source_submission_id"] == "s-2"
    assert [claim["revision_label"] for claim in item["covered_claims"]] == ["EOT-1", "EOT-2"]
    assert [claim["claimed_extension_days"] for claim in item["covered_claims"]] == [60, 120]


@pytest.mark.asyncio
async def test_upgrade_repairs_a_contractual_date_corrupted_by_out_of_order_freezes():
    db = _DB()
    await _seed_legacy(db)

    result = await upgrade(db, dry_run=False)

    milestone = await db.key_date_milestones.find_one({"_id": "m-1"})
    assert milestone["current_approved_key_date"] == datetime(2026, 9, 1)
    repairs = [op for op in result.operations if op["operation"] == "recompute_contractual_dates"]
    assert repairs and repairs[0]["changes"], "date repairs must be reported, never silent"
    assert repairs[0]["changes"][0]["milestone_ref"] == "KD-01"
    assert any("KD-01" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_upgrade_is_idempotent():
    db = _DB()
    await _seed_legacy(db)

    await upgrade(db, dry_run=False)
    second = await upgrade(db, dry_run=False)

    milestone = await db.key_date_milestones.find_one({"_id": "m-1"})
    assert milestone["current_approved_key_date"] == datetime(2026, 9, 1)
    repairs = [op for op in second.operations if op["operation"] == "recompute_contractual_dates"]
    assert repairs and repairs[0]["changes"] == []
    assert second.warnings == []


@pytest.mark.asyncio
async def test_upgrade_is_registered_in_the_catalog():
    from rbac_backend.migrations.catalog import MIGRATIONS

    versions = [migration.version for migration in MIGRATIONS]
    assert "20260813_0001" in versions
    assert versions == sorted(versions), "migrations must stay in ascending version order"
