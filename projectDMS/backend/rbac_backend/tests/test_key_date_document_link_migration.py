from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime

import pytest

from rbac_backend.services.key_date_document_link_migration import (
    classify_legacy_key_date_links,
)
from rbac_backend.tests.test_key_date_document_relationships import _KeyDateDatabase


@pytest.mark.asyncio
async def test_key_date_migration_classifier_is_deterministic_read_only_and_event_precise() -> None:
    db = _KeyDateDatabase()
    db.key_date_milestones.documents[0]["client_notification_ref"] = "CON/KD-01/META"
    db.key_date_achievements.documents.extend(
        [
            {
                "_id": "kd-1:ach",
                "milestone_id": "kd-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "client_notification_ref": "CON/KD-01/ACH",
                "linked_document_ids": ["doc-1", "doc-1", "missing-doc"],
            },
            {
                "_id": "orphan:ach",
                "milestone_id": "missing-key-date",
                "organization_id": "org-1",
                "project_id": "project-1",
                "linked_document_ids": ["doc-1"],
            },
        ]
    )
    db.key_date_eot_submissions.documents.append(
        {
            "_id": "submission-frozen",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "revision_label": "EOT-1",
            "status": "superseded",
            "locked_at": datetime(2026, 8, 10),
            "linked_document_ids": ["doc-1", "CON/LEGACY/EOT-1"],
        }
    )
    db.key_date_milestones.documents.append(
        {
            "_id": "kd-2",
            "milestone_ref": "KD-02",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "actual_achievement_date": datetime(2026, 8, 12),
        }
    )
    db.key_date_eot_determinations.documents.append(
        {
            "_id": "determination-orphan",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "eot_submission_ids": ["submission-missing"],
            "status": "rejected",
            "linked_document_ids": ["doc-1"],
        }
    )
    db.key_date_eot_applications.documents.append(
        {
            "_id": "legacy-eot-1",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "eot_letter_reference": "CON/LEGACY/EOT-1",
            "linked_document_ids": ["doc-1"],
        }
    )
    before = deepcopy(
        {
            "achievements": db.key_date_achievements.documents,
            "submissions": db.key_date_eot_submissions.documents,
            "legacy": db.key_date_eot_applications.documents,
            "links": db.entity_document_links.documents,
        }
    )

    first = await classify_legacy_key_date_links(db)
    second = await classify_legacy_key_date_links(db)

    assert json.dumps(first, default=str, sort_keys=True) == json.dumps(
        second, default=str, sort_keys=True
    )
    assert first["dry_run"] is True
    assert first["counts"]["duplicate"] == 1
    assert first["counts"]["missing_document"] == 2
    assert first["counts"]["orphan_event"] >= 1
    assert first["counts"]["missing_event"] >= 2
    assert first["counts"]["legacy_eot"] >= 1
    assert first["counts"]["superseded_submission"] >= 1
    assert first["counts"]["frozen_target"] >= 1
    achievement = next(
        row
        for row in first["candidates"]
        if row["target_id"] == "kd-1:ach" and row["document_id"] == "doc-1"
    )
    assert achievement["target_type"] == "key_date_achievement"
    assert "ambiguous_role" in achievement["findings"]
    legacy = next(
        row for row in first["candidates"] if row["target_id"] == "legacy-eot-1"
    )
    assert legacy["target_type"] == "legacy_eot"
    assert legacy["classification"] == "manual_review"
    assert any(
        row["kind"] == "notification_metadata_without_document"
        for row in first["metadata_references"]
    )
    assert any(
        row["kind"] == "textual_reference_mistaken_for_document"
        and row["reference"] == "CON/LEGACY/EOT-1"
        for row in first["metadata_references"]
    )
    assert any(
        row["target_type"] == "key_date_achievement"
        and row["target_id"] == "kd-2:ach"
        and row["classification"] == "missing_event"
        for row in first["event_findings"]
    )
    assert any(
        row["target_type"] == "eot_determination"
        and row["target_id"] == "determination-orphan"
        and row["classification"] == "orphan_event"
        for row in first["event_findings"]
    )
    assert before == {
        "achievements": db.key_date_achievements.documents,
        "submissions": db.key_date_eot_submissions.documents,
        "legacy": db.key_date_eot_applications.documents,
        "links": db.entity_document_links.documents,
    }


@pytest.mark.asyncio
async def test_key_date_migration_inventory_finds_invalid_child_event_ownership() -> None:
    db = _KeyDateDatabase()
    db.key_date_milestones.documents.append(
        {
            "_id": "kd-foreign",
            "milestone_ref": "KD-X",
            "organization_id": "org-2",
            "project_id": "project-2",
            "contract_id": "primary",
        }
    )
    db.key_date_achievements.documents.extend(
        [
            {
                "_id": "kd-1:ach",
                "milestone_id": "kd-1",
                "organization_id": "org-1",
                "project_id": "project-1",
            },
            {
                "_id": "duplicate-achievement",
                "milestone_id": "kd-1",
                "organization_id": "org-1",
                "project_id": "project-1",
            },
        ]
    )
    db.key_date_eot_submissions.documents.append(
        {
            "_id": "submission-owned",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
        }
    )
    db.key_date_eot_submission_items.documents.append(
        {
            "_id": "submission-item-orphan",
            "eot_submission_id": "submission-owned",
            "key_date_id": "missing-key-date",
        }
    )
    db.key_date_eot_determinations.documents.append(
        {
            "_id": "determination-owned",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
        }
    )
    db.key_date_eot_determination_items.documents.append(
        {
            "_id": "determination-item-foreign",
            "determination_id": "determination-owned",
            "key_date_id": "kd-foreign",
        }
    )

    result = await classify_legacy_key_date_links(db)

    orphan_details = {
        (row["target_type"], row["target_id"], row["detail"])
        for row in result["event_findings"]
        if row["classification"] == "orphan_event"
    }
    assert any(
        target_type == "key_date_achievement"
        and target_id == "duplicate-achievement"
        and "canonical" in detail
        for target_type, target_id, detail in orphan_details
    )
    assert any(
        target_type == "eot_submission"
        and target_id == "submission-owned"
        and "missing or foreign-scope Key Date" in detail
        for target_type, target_id, detail in orphan_details
    )
    assert any(
        target_type == "eot_determination"
        and target_id == "determination-owned"
        and "missing or foreign-scope Key Date" in detail
        for target_type, target_id, detail in orphan_details
    )


@pytest.mark.asyncio
async def test_achievement_inherits_non_primary_contract_from_canonical_milestone() -> None:
    db = _KeyDateDatabase()
    db.key_date_milestones.documents[0]["contract_id"] = "contract-2"
    db.key_date_achievements.documents.append(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
        }
    )

    result = await classify_legacy_key_date_links(db)

    assert not any(
        row["target_type"] == "key_date_achievement"
        and row["target_id"] == "kd-1:ach"
        and row["classification"] == "orphan_event"
        for row in result["event_findings"]
    )

    db.key_date_achievements.documents[0]["contract_id"] = "foreign-contract"
    conflicting = await classify_legacy_key_date_links(db)
    assert any(
        row["target_type"] == "key_date_achievement"
        and row["target_id"] == "kd-1:ach"
        and row["classification"] == "orphan_event"
        for row in conflicting["event_findings"]
    )
