from __future__ import annotations

import asyncio
from datetime import datetime

from backend.rbac_backend.services.arbitration_drafting.context import (
    ArbitrationContextBuilder,
)
from backend.rbac_backend.tests.test_arbitration_drafting import _FakeCollection, _FakeDb
from rbac_backend.tests.test_claim_document_relationships import _Collection, _user


def test_arbitration_metadata_preserves_authorized_bg_event_identity_and_role() -> None:
    db = _FakeDb()
    db.bank_guarantees.rows = [
        {
            "_id": "bg-1",
            "bg_number": "BG-001",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "contract-1",
            "bg_status": "extended",
            "linked_document_ids": ["doc-blocked"],
        }
    ]
    db.bank_guarantee_events = _FakeCollection(
        [
            {
                "_id": "event-extension-1",
                "bank_guarantee_id": "bg-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "event_type": "extension",
                "sequence": 2,
                "revision_number": 1,
                "event_date": datetime(2026, 8, 1),
                "reference": "EXT/1",
            },
            {
                "_id": "event-extension-2",
                "bank_guarantee_id": "bg-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "event_type": "extension",
                "sequence": 3,
                "revision_number": 2,
                "event_date": datetime(2026, 8, 20),
                "reference": "EXT/2",
            },
        ]
    )
    db.documents.rows.extend(
        [
            {
                "_id": "doc-e1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "processing_status": "metadata_extracted",
                "lifecycle_state": "active",
            },
            {
                "_id": "doc-e2",
                "organization_id": "org-1",
                "project_id": "project-1",
                "processing_status": "metadata_extracted",
                "lifecycle_state": "active",
            },
            {
                "_id": "doc-blocked",
                "organization_id": "org-1",
                "project_id": "project-1",
                "processing_status": "human_review_required",
                "lifecycle_state": "active",
            },
        ]
    )
    db.entity_document_links = _Collection(
        "entity_document_links",
        [
            {
                "_id": "link-e1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "target_type": "bank_guarantee_event",
                "target_id": "event-extension-1",
                "parent_type": "bank_guarantee",
                "parent_id": "bg-1",
                "document_id": "doc-e1",
                "relationship_role": "extension",
                "source": "user",
                "removed_at": None,
                "_revision": 1,
            },
            {
                "_id": "link-e2",
                "organization_id": "org-1",
                "project_id": "project-1",
                "target_type": "bank_guarantee_event",
                "target_id": "event-extension-2",
                "parent_type": "bank_guarantee",
                "parent_id": "bg-1",
                "document_id": "doc-e2",
                "relationship_role": "supporting_document",
                "source": "user",
                "removed_at": None,
                "_revision": 1,
            },
        ],
    )
    db.document_versions = _Collection("document_versions")
    draft = {
        "_id": "draft-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "contract_id": "contract-1",
        "draft_type": "statement_of_claim",
        "title": "Bank Guarantee claim",
    }

    context = asyncio.run(
        ArbitrationContextBuilder(db).build(draft, [], [], [], _user())
    )

    source = next(
        row
        for row in context["source_ledger"]
        if row.get("source_origin") == "bank_guarantee_register"
    )
    evidence = source["metadata"]["event_evidence"]
    assert [row["event_id"] for row in evidence] == [
        "event-extension-1",
        "event-extension-2",
    ]
    assert [row["revision_number"] for row in evidence] == [1, 2]
    assert [row["relationship_role"] for row in evidence] == [
        "extension",
        "supporting_document",
    ]
    assert [row["document_id"] for row in evidence] == ["doc-e1", "doc-e2"]
    assert "doc-blocked" not in str(source)
    assert "linked_document_ids" not in source["metadata"]
