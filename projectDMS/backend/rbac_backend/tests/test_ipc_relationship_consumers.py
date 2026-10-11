from __future__ import annotations

import asyncio

from backend.rbac_backend.services.arbitration_drafting.agents.deterministic import (
    DeterministicArbitrationAgent,
)
from backend.rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder
from backend.rbac_backend.tests.test_arbitration_drafting import _FakeCollection, _FakeDb
from rbac_backend.tests.test_claim_document_relationships import _Collection, _user
from rbac_backend.services.document_relationship_service import DocumentRelationshipService


def _ipc_db() -> _FakeDb:
    db = _FakeDb()
    db.ipc_bills.rows = [
        {
            "_id": "ipc-1",
            "ipc_number": "IPC-001",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "contract-1",
            "status": "approved",
            "approved_amount": 1000,
            "linked_document_ids": ["doc-blocked"],
        }
    ]
    db.documents.rows.extend(
        [
            {
                "_id": "doc-native",
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
                "_id": "ipc-link-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "target_type": "ipc_bill",
                "target_id": "ipc-1",
                "document_id": "doc-native",
                "relationship_role": "payment_certificate",
                "source": "user",
                "removed_at": None,
                "_revision": 1,
            }
        ]
    )
    db.document_versions = _Collection("document_versions")
    return db


def test_arbitration_register_metadata_uses_current_authorized_ipc_relationships() -> None:
    db = _ipc_db()
    resolved = asyncio.run(
        DocumentRelationshipService(db).authorized_document_ids_for_targets(
            _user(), "ipc_bill", db.ipc_bills.rows
        )
    )
    assert resolved == {"ipc-1": ["doc-native"]}
    draft = {
        "_id": "draft-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "contract_id": "contract-1",
        "draft_type": "statement_of_claim",
        "title": "Payment claim",
    }

    context = asyncio.run(ArbitrationContextBuilder(db).build(draft, [], [], [], _user()))

    ipc = next(row for row in context["source_ledger"] if row.get("source_origin") == "ipc_register")
    assert ipc["metadata"]["linked_document_ids"] == ["doc-native"]
    assert "doc-blocked" not in str(ipc)


def test_deterministic_quantum_uses_authorized_ipc_relationships_not_legacy_membership() -> None:
    db = _ipc_db()
    agent = DeterministicArbitrationAgent(
        db=db,
        case=db.arbitration_cases.rows[0],
        draft_id="draft-1",
        options={},
        current_user=_user(),
    )

    result = asyncio.run(agent.run("quantum"))

    assert result["errors"] == [], result

    row = next(
        item
        for item in db.arbitration_quantum_annexures.rows
        if (item.get("source_records") or [{}])[0].get("source_id") == "ipc-1"
    )
    assert row["evidence_ids"] == ["doc-native"]
    assert "doc-blocked" not in row["evidence_ids"]
