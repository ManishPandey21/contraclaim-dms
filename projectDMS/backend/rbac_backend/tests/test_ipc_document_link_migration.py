from __future__ import annotations

from copy import deepcopy

from rbac_backend.services.ipc_document_link_migration import classify_legacy_ipc_links
from rbac_backend.tests.test_ipc_document_relationships import _IPCDatabase


async def test_ipc_legacy_migration_dry_run_classifies_every_candidate_without_writes() -> None:
    db = _IPCDatabase()
    db.ipc_bills.documents[0]["linked_document_ids"] = [
        "doc-1",
        "doc-1",
        "missing-doc",
        "deleted-doc",
        "blocked-doc",
        "foreign-org-doc",
        "foreign-project-doc",
        "",
        42,
    ]
    db.documents.documents.extend(
        [
            {
                "_id": "deleted-doc",
                "organization_id": "org-1",
                "project_id": "project-1",
                "lifecycle_state": "deleted",
            },
            {
                "_id": "blocked-doc",
                "organization_id": "org-1",
                "project_id": "project-1",
                "processing_status": "human_review_required",
            },
            {
                "_id": "foreign-org-doc",
                "organization_id": "org-2",
                "project_id": "project-1",
                "processing_status": "metadata_extracted",
            },
            {
                "_id": "foreign-project-doc",
                "organization_id": "org-1",
                "project_id": "project-2",
                "processing_status": "metadata_extracted",
            },
        ]
    )
    before_ipcs = deepcopy(db.ipc_bills.documents)
    before_links = deepcopy(db.entity_document_links.documents)

    first = await classify_legacy_ipc_links(db)
    second = await classify_legacy_ipc_links(db)

    assert first == second
    assert first["dry_run"] is True
    assert first["candidate_count"] == 9
    assert first["counts"] == {
        "valid": 1,
        "missing_document": 1,
        "deleted_document": 1,
        "blocked_document": 1,
        "cross_organisation": 1,
        "cross_project": 1,
        "invalid_id": 2,
        "duplicate": 1,
        "ambiguous_role": 1,
        "manual_review": 1,
    }
    valid = next(row for row in first["candidates"] if row["document_id"] == "doc-1" and row["occurrence"] == 1)
    assert valid["classification"] == "manual_review"
    # No `ambiguous_event`: IPC's accepted model has no child events at all —
    # submission/certification/payment are embedded lifecycle data with no
    # stable entity ids — so an event-ambiguity finding would assert an
    # ambiguity that cannot exist. The role remains ambiguous and is the only
    # thing an operator adjudicates.
    assert valid["findings"] == ["valid", "ambiguous_role", "manual_review"]
    assert db.ipc_bills.documents == before_ipcs
    assert db.entity_document_links.documents == before_links


async def test_ipc_migration_never_guesses_role_or_embedded_event_identity() -> None:
    db = _IPCDatabase()
    db.ipc_bills.documents[0].update(
        {
            "linked_document_ids": ["doc-1"],
            "submission_date": "2026-01-01",
            "approval_date": "2026-01-05",
            "payments": [{"reference": "NEFT-1", "amount": 100}],
        }
    )

    report = await classify_legacy_ipc_links(db)

    candidate = report["candidates"][0]
    assert candidate["target_type"] == "ipc_bill"
    assert candidate["target_id"] == "ipc-1"
    assert candidate["relationship_role"] is None
    # The classifier still refuses to invent a child-event identity: the target
    # stays the bill, with no parent/event decomposition derived from embedded
    # submission/approval/payment data.
    assert candidate["parent_type"] is None
    assert candidate["parent_id"] is None
    assert "ambiguous_role" in candidate["findings"]
    assert "ambiguous_event" not in candidate["findings"]

