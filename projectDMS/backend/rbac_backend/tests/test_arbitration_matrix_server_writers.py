"""Server-side writers that rewrite a reviewed matrix row follow the same rules as a user edit.

A changed row goes back to review, and every write that changes reviewed
content - or decides on it - moves `edit_revision`, so a review or permission
decision computed from the earlier content is refused. Covers the claim-summary
rollup, the LLM document-index refresh, paragraph-response projections, the
rejoinder permission decision and exhibit numbering. In-memory Mongo; no
external service.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import HTTPException

from rbac_backend.models.arbitration_drafting import ArbitrationMatrixRowUpdate, ArbitrationRejoinderPermissionRequest
from rbac_backend.services.arbitration_drafting.agents.deterministic import DeterministicArbitrationAgent
from rbac_backend.services.arbitration_drafting.agents.llm import LLMArbitrationAgent
from rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from rbac_backend.services.arbitration_drafting.paragraph_positions import sync_paragraph_position_projection
from rbac_backend.tests.test_arbitration_matrix_approval_http import _WorkspaceDatabase

CASE = {"_id": "case-1", "organization_id": "org-1", "project_id": "project-1", "contract_id": "contract-1", "title": "EOT"}
AGENT_USER = {"id": "agent-runner"}
APPROVED = {
    "approval_status": "approved",
    "human_approval_status": "approved",
    "verification_status": "verified",
    "readiness_status": "ready",
    "review_status": "approved",
    "review_completed_roles": ["quantum"],
    "approved_by": "reviewer-1",
}


class _LegalReviewer:
    id = "legal-1"
    email = "legal-1@example.test"
    roles = ["legal_reviewer"]


@pytest.fixture
def db() -> _WorkspaceDatabase:
    database = _WorkspaceDatabase()
    database.arbitration_cases.docs.append(dict(CASE))
    return database


def _row(db: _WorkspaceDatabase, collection: str, row_id: str) -> dict[str, Any]:
    return next(row for row in getattr(db, collection).docs if row["_id"] == row_id)


# ---------------------------------------------------------------------------
# Claim-summary rollup
# ---------------------------------------------------------------------------


def _annexure(row_id: str, amount: float, **fields: Any) -> dict[str, Any]:
    return {"_id": row_id, "case_id": CASE["_id"], "calculation_id": row_id, "cost_head": row_id, "amount": amount, "currency": "INR", **fields}


def _rollup(db: _WorkspaceDatabase) -> DeterministicArbitrationAgent:
    agent = DeterministicArbitrationAgent(db=db, case=CASE, draft_id=None, options={}, current_user=AGENT_USER)
    asyncio.run(agent._claim_summary_rollup("quantum-calculation"))
    return agent


def _summary(db: _WorkspaceDatabase) -> dict[str, Any]:
    return next(row for row in db.arbitration_quantum_annexures.docs if row.get("calculation_id") == "Q-CLAIM-SUMMARY")


def _approve_summary(db: _WorkspaceDatabase) -> None:
    _summary(db).update({**APPROVED, "edit_revision": 1})


def test_a_recomputed_claim_summary_returns_to_review(db: _WorkspaceDatabase) -> None:
    db.arbitration_quantum_annexures.docs.append(_annexure("P-1", 100.0, **APPROVED))
    _rollup(db)
    _approve_summary(db)

    # An unapproved annexure changes the rollup's totals.
    db.arbitration_quantum_annexures.docs.append(_annexure("P-2", 10_000_000.0))
    agent = _rollup(db)

    summary = _summary(db)
    assert summary["principal_total"] == 10_000_100.0
    assert summary["approval_status"] == "needs_review"
    assert summary["review_completed_roles"] == []
    assert summary["approved_by"] is None
    assert summary["last_material_editor_id"] == AGENT_USER["id"]
    assert summary["edit_revision"] == 2
    assert summary["approval_log"][-1]["action"] == "material_edit"
    # Recorded as a matrix change, which is what makes the run invalidate readiness.
    assert [record["action"] for record in agent.created_records] == ["updated"]


def test_an_unchanged_claim_summary_keeps_its_approval(db: _WorkspaceDatabase) -> None:
    db.arbitration_quantum_annexures.docs.append(_annexure("P-1", 100.0, **APPROVED))
    _rollup(db)
    _approve_summary(db)
    before = dict(_summary(db))

    agent = _rollup(db)

    assert _summary(db) == before
    assert agent.created_records == []


# ---------------------------------------------------------------------------
# LLM document-index refresh
# ---------------------------------------------------------------------------


class _Generator:
    available = True

    async def generate(self, prompt: str, *args: Any, **kwargs: Any) -> str:
        return json.dumps([{"source_id": "doc-1", "document_type": "notice", "relevance_note": "LLM analysis"}])


def _index_row(**fields: Any) -> dict[str, Any]:
    return {
        "_id": "index-1",
        "case_id": CASE["_id"],
        "source_id": "doc-1",
        "title": "Delay notice",
        "relevance_note": "Reviewed note",
        "approval_status": "needs_review",
        "edit_revision": 3,
        **fields,
    }


def _refresh(db: _WorkspaceDatabase) -> LLMArbitrationAgent:
    db.documents.docs.append(
        {"_id": "doc-1", "organization_id": "org-1", "project_id": "project-1", "subject": "Delay notice", "full_content": "Late access", "filename": "n.pdf"}
    )
    agent = LLMArbitrationAgent(db=db, case=CASE, draft_id=None, options={}, current_user=AGENT_USER, generator=_Generator())
    asyncio.run(agent._document_understanding("document-understanding"))
    return agent


def test_an_llm_refresh_moves_the_revision_so_an_earlier_review_is_refused(db: _WorkspaceDatabase) -> None:
    db.arbitration_document_index.docs.append(_index_row())

    _refresh(db)

    row = _row(db, "arbitration_document_index", "index-1")
    assert row["relevance_note"] == "LLM analysis"
    assert row["edit_revision"] == 4
    assert row["last_material_editor_id"] == AGENT_USER["id"]


def test_an_llm_refresh_never_overwrites_a_review_that_landed_meanwhile(db: _WorkspaceDatabase) -> None:
    db.arbitration_document_index.docs.append(_index_row())
    collection = db.arbitration_document_index
    original = collection.update_one

    async def _approved_first(query, update, *args, **kwargs):
        _row(db, "arbitration_document_index", "index-1").update({**APPROVED, "edit_revision": 4})
        return await original(query, update, *args, **kwargs)

    collection.update_one = _approved_first
    agent = _refresh(db)

    row = _row(db, "arbitration_document_index", "index-1")
    assert row["approval_status"] == "approved"
    assert row["relevance_note"] == "Reviewed note"
    assert any("changed during analysis" in warning for warning in agent.warnings)


# ---------------------------------------------------------------------------
# Paragraph-response projections
# ---------------------------------------------------------------------------

DRAFT = {"_id": "draft-1", "case_id": CASE["_id"], "draft_type": "statement_of_defence", "organization_id": "org-1", "project_id": "project-1"}


def _response(text: str) -> dict[str, Any]:
    return {"_id": "response-1", "draft_id": "draft-1", "source_paragraph_number": "1", "response_type": "deny", "response_text": text}


def test_a_changed_projection_returns_to_review_and_moves_the_revision(db: _WorkspaceDatabase) -> None:
    asyncio.run(sync_paragraph_position_projection(db, DRAFT, _response("Denied."), actor_id="drafter-1"))
    projection = db.arbitration_defence_matrix.docs[0]
    projection.update({**APPROVED, "edit_revision": 2})

    asyncio.run(sync_paragraph_position_projection(db, DRAFT, _response("Denied; see the access log."), actor_id="drafter-1"))

    assert projection["approval_status"] == "needs_review"
    assert projection["edit_revision"] == 3


def test_an_unchanged_projection_keeps_its_approval_and_revision(db: _WorkspaceDatabase) -> None:
    asyncio.run(sync_paragraph_position_projection(db, DRAFT, _response("Denied."), actor_id="drafter-1"))
    projection = db.arbitration_defence_matrix.docs[0]
    projection.update({**APPROVED, "edit_revision": 2})

    asyncio.run(sync_paragraph_position_projection(db, DRAFT, _response("Denied."), actor_id="drafter-1"))

    assert projection["approval_status"] == "approved"
    assert projection["edit_revision"] == 2


# ---------------------------------------------------------------------------
# Rejoinder permission decision
# ---------------------------------------------------------------------------


def _rejoinder_row(db: _WorkspaceDatabase) -> None:
    db.documents.docs.append({"_id": "order-1", "organization_id": "org-1", "project_id": "project-1", "filename": "order.pdf"})
    db.arbitration_rejoinder_matrix.docs.append(
        {
            "_id": "rejoinder-1",
            "case_id": CASE["_id"],
            "claimant_reply": "New matter: the access log.",
            "new_matter": True,
            "permission_required": True,
            "created_by": "drafter-1",
            "edit_revision": 1,
        }
    )


def test_a_permission_decided_on_content_that_changed_meanwhile_is_refused(db: _WorkspaceDatabase) -> None:
    _rejoinder_row(db)
    collection = db.arbitration_rejoinder_matrix
    original = collection.find_one_and_update

    async def _edited_first(query, update, *args, **kwargs):
        _row(db, "arbitration_rejoinder_matrix", "rejoinder-1").update({"claimant_reply": "Different new matter.", "edit_revision": 2})
        return await original(query, update, *args, **kwargs)

    collection.find_one_and_update = _edited_first
    service = ArbitrationCaseWorkspaceService(db)

    with pytest.raises(HTTPException) as refused:
        asyncio.run(
            service.record_rejoinder_permission(
                CASE["_id"], "rejoinder-1", ArbitrationRejoinderPermissionRequest(permission_source_id="order-1"), _LegalReviewer()
            )
        )

    assert refused.value.status_code == 409
    assert not _row(db, "arbitration_rejoinder_matrix", "rejoinder-1").get("permission_obtained")
    receipts = [receipt for receipt in db.arbitration_workflow_approvals.docs if receipt.get("gate") == "rejoinder_permission"]
    # The refused decision is closed out, not left pending.
    assert [receipt["receipt_status"] for receipt in receipts] == ["aborted"]


def test_editing_the_new_matter_revokes_a_granted_permission(db: _WorkspaceDatabase) -> None:
    _rejoinder_row(db)
    _row(db, "arbitration_rejoinder_matrix", "rejoinder-1").update(
        {**APPROVED, "permission_obtained": True, "permission_approved_by": "legal-1", "permission_approval_receipt_id": "r-1"}
    )
    service = ArbitrationCaseWorkspaceService(db)

    asyncio.run(
        service.update_matrix_row(
            CASE["_id"], "rejoinder-matrix", "rejoinder-1", ArbitrationMatrixRowUpdate(claimant_reply="Wider new matter."), _LegalReviewer()
        )
    )

    row = _row(db, "arbitration_rejoinder_matrix", "rejoinder-1")
    assert row["permission_obtained"] is False
    assert row["permission_approval_receipt_id"] is None
    assert row["approval_status"] == "needs_review"


# ---------------------------------------------------------------------------
# Exhibit numbering
# ---------------------------------------------------------------------------


def test_exhibit_numbering_is_part_of_the_edit_write(db: _WorkspaceDatabase) -> None:
    db.arbitration_document_index.docs.append(
        {"_id": "index-1", "case_id": CASE["_id"], "source_id": "doc-1", "title": "Notice", "exhibit_prefix": "C", "exhibit_number": 1, "exhibit_id": "C-1", **APPROVED, "edit_revision": 5}
    )
    collection = db.arbitration_document_index
    original = collection.find_one_and_update
    writes: list[dict[str, Any]] = []

    async def _recording(query, update, *args, **kwargs):
        writes.append({"query": query, "update": update})
        return await original(query, update, *args, **kwargs)

    collection.find_one_and_update = _recording
    service = ArbitrationCaseWorkspaceService(db)

    asyncio.run(
        service.update_matrix_row(
            CASE["_id"], "document-index", "index-1", ArbitrationMatrixRowUpdate(exhibit_id="", exhibit_number=None), _LegalReviewer()
        )
    )

    # One compare-and-set write carries both the reset and the new exhibit number.
    assert len(writes) == 1
    assert writes[0]["query"]["edit_revision"] == 5
    assert writes[0]["update"]["$set"]["exhibit_id"] == "C-1"
    row = _row(db, "arbitration_document_index", "index-1")
    assert row["exhibit_id"] == "C-1"
    assert row["edit_revision"] == 6
    assert row["approval_status"] == "needs_review"


# ---------------------------------------------------------------------------
# Server writers add their actor to the material contributors
# ---------------------------------------------------------------------------


def test_a_recomputed_claim_summary_records_its_actor_as_a_contributor(db: _WorkspaceDatabase) -> None:
    db.arbitration_quantum_annexures.docs.append(_annexure("P-1", 100.0, **APPROVED))
    _rollup(db)
    _summary(db).update({**APPROVED, "edit_revision": 1, "material_contributor_ids": ["drafter-1"]})
    db.arbitration_quantum_annexures.docs.append(_annexure("P-2", 5.0))

    _rollup(db)

    assert set(_summary(db)["material_contributor_ids"]) == {"drafter-1", AGENT_USER["id"]}


def test_an_llm_refresh_records_its_actor_as_a_contributor(db: _WorkspaceDatabase) -> None:
    db.arbitration_document_index.docs.append(_index_row(material_contributor_ids=["editor-1"]))
    _refresh(db)
    assert set(_row(db, "arbitration_document_index", "index-1")["material_contributor_ids"]) == {"editor-1", AGENT_USER["id"]}


def test_a_changed_projection_records_its_actor_and_a_no_op_sync_preserves_contributors(db: _WorkspaceDatabase) -> None:
    asyncio.run(sync_paragraph_position_projection(db, DRAFT, _response("Denied."), actor_id="drafter-1"))
    projection = db.arbitration_defence_matrix.docs[0]
    assert projection["material_contributor_ids"] == ["drafter-1"]

    asyncio.run(sync_paragraph_position_projection(db, DRAFT, _response("Denied."), actor_id="someone-else"))
    assert projection["material_contributor_ids"] == ["drafter-1"]
    assert projection["last_material_editor_id"] == "drafter-1"

    asyncio.run(sync_paragraph_position_projection(db, DRAFT, _response("Denied; see the log."), actor_id="drafter-2"))
    assert set(projection["material_contributor_ids"]) == {"drafter-1", "drafter-2"}


def test_a_rejoinder_contributor_cannot_grant_the_permission(db: _WorkspaceDatabase) -> None:
    _rejoinder_row(db)
    _row(db, "arbitration_rejoinder_matrix", "rejoinder-1")["material_contributor_ids"] = ["drafter-1", _LegalReviewer.id]
    service = ArbitrationCaseWorkspaceService(db)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(
            service.record_rejoinder_permission(
                CASE["_id"], "rejoinder-1", ArbitrationRejoinderPermissionRequest(permission_source_id="order-1"), _LegalReviewer()
            )
        )
    assert refused.value.status_code == 409
    assert not _row(db, "arbitration_rejoinder_matrix", "rejoinder-1").get("permission_obtained")


def test_a_rollup_never_overwrites_an_edit_that_landed_between_its_read_and_write(db: _WorkspaceDatabase) -> None:
    db.arbitration_quantum_annexures.docs.append(_annexure("P-1", 100.0, **APPROVED))
    _rollup(db)
    _summary(db).update({**APPROVED, "edit_revision": 1, "material_contributor_ids": [], "approval_log": []})
    db.arbitration_quantum_annexures.docs.append(_annexure("P-2", 5.0))
    collection = db.arbitration_quantum_annexures
    original = collection.update_one
    fired = []

    async def _edit_lands_first(query, update, *args, **kwargs):
        if not fired:
            fired.append(True)
            # A user edit commits under the compare-and-set rules in the window.
            _summary(db).update(
                {
                    "notes": "user note",
                    "edit_revision": 2,
                    "material_contributor_ids": ["editor-7"],
                    "approval_log": [{"_id": "edit-1", "action": "material_edit", "actor_id": "editor-7"}],
                }
            )
        return await original(query, update, *args, **kwargs)

    collection.update_one = _edit_lands_first
    _rollup(db)

    summary = _summary(db)
    assert summary["notes"] == "user note"
    assert set(summary["material_contributor_ids"]) == {"editor-7", AGENT_USER["id"]}
    assert [entry.get("actor_id") for entry in summary["approval_log"]] == ["editor-7", AGENT_USER["id"]]
    assert summary["edit_revision"] == 3
    assert summary["principal_total"] == 105.0


def test_a_rollup_does_not_write_a_summary_deleted_between_its_read_and_write(db: _WorkspaceDatabase) -> None:
    db.arbitration_quantum_annexures.docs.append(_annexure("P-1", 100.0, **APPROVED))
    _rollup(db)
    _summary(db).update({**APPROVED, "edit_revision": 1})
    db.arbitration_quantum_annexures.docs.append(_annexure("P-2", 5.0))
    collection = db.arbitration_quantum_annexures
    original = collection.update_one
    fired = []

    async def _deleted_first(query, update, *args, **kwargs):
        if not fired:
            fired.append(True)
            _summary(db)["deleted_at"] = "2026-10-27T00:00:00"
        return await original(query, update, *args, **kwargs)

    collection.update_one = _deleted_first
    _rollup(db)

    assert _summary(db)["principal_total"] == 100.0
