"""A matrix row's approval covers only the content that was reviewed.

An ordinary row update used to `$set` new content - a different exhibit source,
title, pages - onto an approved row and leave it approved, so the filing bundle
exported content no reviewer had seen. These tests drive the real
`ArbitrationCaseWorkspaceService` over the filing-bundle fixture from
`test_arbitration_drafting.py`; exhibit files are written under `tmp_path`.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.rbac_backend.models.arbitration_drafting import (
    ArbitrationMatrixReviewRequest,
    ArbitrationMatrixRowCreate,
    ArbitrationMatrixRowUpdate,
)
from backend.rbac_backend.services.arbitration_drafting import case_workspace as case_workspace_module
from backend.rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from backend.rbac_backend.services.arbitration_drafting.workflow_repository import DOWNSTREAM_APPROVAL_GATES
from backend.rbac_backend.tests.test_arbitration_drafting import _FakeDb, _FakeUser, _authorize_filing_fixture

SOURCE_A = b"EXHIBIT-SOURCE-A"
SOURCE_B = b"EXHIBIT-SOURCE-B"

EDITOR = _FakeUser("editor-1", roles=["contract_reviewer"])
REVIEWER = _FakeUser("reviewer-2", roles=["contract_reviewer"])
CASE, INDEX, ROW = "case-1", "document-index", "doc-row-1"


@pytest.fixture
def db(tmp_path: Path) -> _FakeDb:
    database = _FakeDb()
    (tmp_path / "a.txt").write_bytes(SOURCE_A)
    (tmp_path / "b.txt").write_bytes(SOURCE_B)
    source_a = next(row for row in database.documents.rows if row.get("_id") == "doc-1")
    source_a["filepath_local"] = str(tmp_path / "a.txt")
    database.documents.rows.append(
        {
            "_id": "doc-2",
            "organization_id": "org-1",
            "project_id": "project-1",
            "subject": "Revised delay notice",
            "filename": "revised-delay-notice.txt",
            "filepath_local": str(tmp_path / "b.txt"),
        }
    )
    _authorize_filing_fixture(database)
    return database


def _row(db: _FakeDb) -> dict:
    return next(row for row in db.arbitration_document_index.rows if row["_id"] == ROW)


def _export(db: _FakeDb) -> list[bytes]:
    content = asyncio.run(ArbitrationCaseWorkspaceService(db).export_filing_bundle_zip(CASE))
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return [archive.read(name) for name in archive.namelist()]


def _edit(db: _FakeDb, user=EDITOR, **fields) -> dict:
    payload = ArbitrationMatrixRowUpdate(**fields)
    return asyncio.run(ArbitrationCaseWorkspaceService(db).update_matrix_row(CASE, INDEX, ROW, payload, user))


def _approve(db: _FakeDb, user=REVIEWER) -> dict:
    payload = ArbitrationMatrixReviewRequest(action="approve", reviewer_role="contracts")
    return asyncio.run(ArbitrationCaseWorkspaceService(db).review_matrix_row(CASE, INDEX, ROW, payload, user))


def _reapprove(db: _FakeDb) -> None:
    """Re-issue the readiness and workflow gate approvals for the current revision.

    `_authorize_filing_fixture` issues them under fixed ids. The superseded
    receipts are kept, invalidated, under new ids so the history stays in the
    database. The superseded run is also renamed and detached from the draft,
    standing in for the new workflow run a real re-approval would start. The
    re-issued receipts bind the new hashes.
    """
    fixed_ids = {"readiness-receipt-1", "workflow-filing-1", *(f"workflow-{gate}-receipt" for gate in DOWNSTREAM_APPROVAL_GATES)}
    for row in [*db.arbitration_workflow_approvals.rows, *db.arbitration_workflow_runs.rows]:
        if row.get("_id") in fixed_ids:
            assert row.get("invalidated_at") or row.get("_id") == "workflow-filing-1", row
            row["_id"] = f"{row['_id']}-superseded"
            if "run_id" in row:
                row["run_id"] = f"{row['run_id']}-superseded"
            if row.get("draft_id") and row.get("current_node"):
                row["draft_id"] = f"{row['draft_id']}-superseded"
    _authorize_filing_fixture(db)


def _live_readiness_receipts(db: _FakeDb) -> list[dict]:
    return [
        receipt
        for receipt in db.arbitration_workflow_approvals.rows
        if receipt.get("gate") == "readiness" and not receipt.get("invalidated_at")
    ]


# ---------------------------------------------------------------------------
# A material edit returns the row to review and blocks export
# ---------------------------------------------------------------------------


def test_changing_an_approved_source_blocks_export_until_rereviewed_then_exports_the_new_source(db: _FakeDb) -> None:
    assert SOURCE_A in _export(db)

    edited = _edit(db, source_id="doc-2")

    assert edited["approval_status"] == "needs_review"
    assert edited["review_completed_roles"] == []
    assert edited["last_material_editor_id"] == EDITOR.id
    assert edited["approval_log"][-1]["action"] == "material_edit"
    assert edited["approval_log"][-1]["changed_fields"] == ["source_id"]
    assert _live_readiness_receipts(db) == []
    with pytest.raises(HTTPException) as refused:
        _export(db)
    assert refused.value.status_code == 409

    _approve(db)
    _reapprove(db)

    exported = _export(db)
    assert SOURCE_B in exported
    assert SOURCE_A not in exported


class _Queue:
    async def enqueue(self, *, job_id, effect_key, payload):
        return job_id


def _queue_export(db: _FakeDb) -> dict:
    return asyncio.run(ArbitrationCaseWorkspaceService(db).queue_filing_bundle_export(CASE, "zip", REVIEWER_EXPORTER))


def _execute(db: _FakeDb, export: dict) -> dict:
    return asyncio.run(ArbitrationCaseWorkspaceService(db).execute_filing_bundle_export_job(CASE, export["_id"], "zip"))


def _stored_export(db: _FakeDb, export: dict) -> dict:
    return next(row for row in db.arbitration_bundle_exports.rows if row["_id"] == export["_id"])


REVIEWER_EXPORTER = _FakeUser("exporter-3", roles=["export_authorizer"])


@pytest.fixture
def export_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_workspace_module, "get_filing_export_queue", lambda: _Queue())


def test_a_material_edit_invalidates_every_dependent_approval(db: _FakeDb) -> None:
    gates = {receipt.get("gate") for receipt in db.arbitration_workflow_approvals.rows if not receipt.get("invalidated_at")}
    assert {"readiness", "plan", "legal_review", "draft", "export"} <= gates

    _edit(db, source_id="doc-2")

    live = [
        receipt
        for receipt in db.arbitration_workflow_approvals.rows
        if receipt.get("case_id") == CASE and receipt.get("gate") in DOWNSTREAM_APPROVAL_GATES and not receipt.get("invalidated_at")
    ]
    assert live == []
    case = db.arbitration_cases.rows[0]
    assert case["readiness_approved_by"] is None
    assert case["readiness_approval_receipt_id"] is None


def test_a_queued_export_whose_source_changes_before_execution_produces_nothing(db: _FakeDb, export_queue: None) -> None:
    export = _queue_export(db)

    _edit(db, source_id="doc-2")

    with pytest.raises((HTTPException, RuntimeError)):
        _execute(db, export)
    stored = _stored_export(db, export)
    assert stored.get("status") != "completed"
    assert not stored.get("content")


def test_a_queued_export_whose_exhibit_bytes_change_before_execution_produces_nothing(
    db: _FakeDb, export_queue: None, tmp_path: Path
) -> None:
    export = _queue_export(db)

    (tmp_path / "a.txt").write_bytes(b"EXHIBIT-SOURCE-A-TAMPERED")

    with pytest.raises(RuntimeError, match="drifted"):
        _execute(db, export)
    assert not _stored_export(db, export).get("content")


def test_reapproved_export_carries_exactly_the_authorised_bytes(db: _FakeDb, export_queue: None) -> None:
    _edit(db, source_id="doc-2")
    with pytest.raises(HTTPException) as blocked:
        _queue_export(db)
    assert blocked.value.status_code == 409
    _approve(db)
    _reapprove(db)
    superseded = [row for row in db.arbitration_workflow_approvals.rows if str(row.get("_id")).endswith("-superseded")]
    assert superseded and all(row.get("invalidated_at") for row in superseded)

    export = _queue_export(db)
    _execute(db, export)

    stored = _stored_export(db, export)
    authorization = next(
        row for row in db.arbitration_export_authorizations.rows if row["_id"] == stored["export_authorization_id"]
    )
    authorised = {entry["source_id"]: entry["sha256"] for entry in authorization["immutable_manifest"]["exhibits"]}
    assert authorised["doc-2"] == hashlib.sha256(SOURCE_B).hexdigest()
    assert "doc-1" not in authorised
    with zipfile.ZipFile(io.BytesIO(bytes(stored["content"]))) as archive:
        members = [archive.read(name) for name in archive.namelist()]
    assert SOURCE_B in members
    assert SOURCE_A not in members


@pytest.mark.parametrize(
    "change",
    [
        {"title": "A different exhibit"},
        {"page_numbers": [3, 4]},
        {"exhibit_prefix": "R"},
        {"relevance_note": "Changed reasoning"},
        {"source_file_link": "/documents/doc-2"},
    ],
)
def test_every_content_change_returns_the_row_to_review(db: _FakeDb, change: dict) -> None:
    edited = _edit(db, **change)
    assert edited["approval_status"] == "needs_review"
    assert edited["verification_status"] == "needs_review"


def test_the_material_editor_cannot_approve_their_own_edit(db: _FakeDb) -> None:
    _edit(db, source_id="doc-2")
    with pytest.raises(HTTPException) as refused:
        _approve(db, user=EDITOR)
    assert refused.value.status_code in (403, 409)
    assert _row(db)["approval_status"] == "needs_review"


# ---------------------------------------------------------------------------
# No-op updates keep the approval
# ---------------------------------------------------------------------------


def test_a_no_op_update_keeps_the_approval_and_the_readiness_approval(db: _FakeDb) -> None:
    before = dict(_row(db))
    receipts = _live_readiness_receipts(db)

    returned = _edit(db, title=before["title"], source_id=before["source_id"])

    assert returned["approval_status"] == "approved"
    assert _row(db) == before
    assert _live_readiness_receipts(db) == receipts
    assert SOURCE_A in _export(db)


def test_echoed_bookkeeping_fields_are_not_a_change(db: _FakeDb) -> None:
    before = dict(_row(db))
    _edit(db, title=before["title"], created_by="someone-else", updated_by="someone-else", updated_at="2001-01-01")
    assert _row(db) == before


# ---------------------------------------------------------------------------
# Approval state cannot be written by an ordinary edit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_status", "approved"),
        ("review_status", "approved"),
        ("review_completed_roles", ["contracts"]),
        ("review_required_roles", []),
        ("review_assignments", [{"reviewer_role": "contracts", "reviewer_user_id": "editor-1", "status": "assigned"}]),
        ("last_material_editor_id", "someone-else"),
        ("edit_revision", 99),
    ],
)
def test_an_edit_cannot_write_approval_state(field: str, value) -> None:
    with pytest.raises(ValidationError):
        ArbitrationMatrixRowUpdate(**{field: value})
    with pytest.raises(ValidationError):
        ArbitrationMatrixRowCreate(**{field: value})


@pytest.mark.parametrize("value", ["approved", "ready", "verified", "supported", "selected", "valid", "user_verified", "edited_verified", " Supported "])
def test_an_edit_cannot_set_a_status_that_any_reader_treats_as_approved(value: str) -> None:
    with pytest.raises(ValidationError):
        ArbitrationMatrixRowUpdate(status=value)
    with pytest.raises(ValidationError):
        ArbitrationMatrixRowCreate(status=value)


def test_the_approval_status_set_covers_every_reader() -> None:
    from backend.rbac_backend.models.arbitration_drafting import APPROVAL_STATUS_VALUES
    from backend.rbac_backend.services.arbitration_drafting.context import VERIFIED_SOURCE_STATUSES

    assert VERIFIED_SOURCE_STATUSES <= APPROVAL_STATUS_VALUES
    assert {"approved", "ready", "verified", "supported"} <= APPROVAL_STATUS_VALUES


# ---------------------------------------------------------------------------
# An edit and a review cannot interleave
# ---------------------------------------------------------------------------


def _interleave(db: _FakeDb, concurrent) -> None:
    """Run `concurrent(row)` once, just before the next matrix-row write lands."""
    collection = db.arbitration_document_index
    original = collection.find_one_and_update
    fired = []

    async def _write(query, update, *args, **kwargs):
        if not fired:
            fired.append(True)
            concurrent(_row(db))
        return await original(query, update, *args, **kwargs)

    collection.find_one_and_update = _write


def _concurrent_source_edit(row: dict) -> None:
    row.update({"source_id": "doc-2", "edit_revision": int(row.get("edit_revision") or 0) + 1, "approval_status": "needs_review"})


def test_an_approval_decided_on_content_that_changed_meanwhile_is_refused(db: _FakeDb) -> None:
    _edit(db, title="Delay notice (revised)")
    _interleave(db, _concurrent_source_edit)

    with pytest.raises(HTTPException) as refused:
        _approve(db)

    assert refused.value.status_code == 409
    assert _row(db)["approval_status"] == "needs_review"
    assert _row(db)["source_id"] == "doc-2"


def test_an_edit_computed_from_a_stale_row_is_refused(db: _FakeDb) -> None:
    _interleave(db, _concurrent_source_edit)

    with pytest.raises(HTTPException) as refused:
        _edit(db, title="Stale edit")

    assert refused.value.status_code == 409
    assert _row(db)["title"] == "Delay notice"


# ---------------------------------------------------------------------------
# Every material contributor since the last full approval is barred from approving
# ---------------------------------------------------------------------------

TYPO_FIXER = _FakeUser("typo-fixer-3", roles=["contract_reviewer"])
LEGAL_CONTRIBUTOR = _FakeUser("legal-editor-4", roles=["legal_reviewer"])
LEGAL_REVIEWER = _FakeUser("legal-reviewer-5", roles=["legal_reviewer"])


def _approve_as(db: _FakeDb, user, role: str) -> dict:
    payload = ArbitrationMatrixReviewRequest(action="approve", reviewer_role=role)
    return asyncio.run(ArbitrationCaseWorkspaceService(db).review_matrix_row(CASE, INDEX, ROW, payload, user))


def test_an_earlier_editor_cannot_approve_after_someone_else_edits(db: _FakeDb) -> None:
    _edit(db, source_id="doc-2")
    _edit(db, user=TYPO_FIXER, title="Delay notice (typo fixed)")

    with pytest.raises(HTTPException) as refused:
        _approve(db, user=EDITOR)

    assert refused.value.status_code in (403, 409)
    assert _row(db)["approval_status"] == "needs_review"
    assert set(_row(db)["material_contributor_ids"]) == {EDITOR.id, TYPO_FIXER.id}


def test_a_partial_approval_keeps_the_contributors_and_a_full_approval_clears_them(db: _FakeDb) -> None:
    _row(db)["review_required_roles"] = ["contracts", "legal"]
    _edit(db, source_id="doc-2")
    _edit(db, user=LEGAL_CONTRIBUTOR, title="Delay notice (revised)")

    partial = _approve(db)
    assert partial["review_status"] == "partially_approved"
    assert set(partial["material_contributor_ids"]) == {EDITOR.id, LEGAL_CONTRIBUTOR.id}
    with pytest.raises(HTTPException):
        _approve_as(db, LEGAL_CONTRIBUTOR, "legal")

    approved = _approve_as(db, LEGAL_REVIEWER, "legal")
    assert approved["approval_status"] == "approved"
    assert approved["material_contributor_ids"] == []


def test_a_contributor_cannot_be_restored_or_cleared_by_an_edit() -> None:
    with pytest.raises(ValidationError):
        ArbitrationMatrixRowUpdate(material_contributor_ids=[])


# ---------------------------------------------------------------------------
# A reviewer specialty comes from a role claim or another user's assignment
# ---------------------------------------------------------------------------

UNQUALIFIED = _FakeUser("approver-6", roles=["projectuser"])
ASSIGNER = _FakeUser("lead-7", roles=["contract_reviewer"])  # holds the specialty it assigns
COLLEAGUE = _FakeUser("colleague-8", roles=["projectuser"])


def _review(db: _FakeDb, user, **fields) -> dict:
    payload = ArbitrationMatrixReviewRequest(**fields)
    return asyncio.run(ArbitrationCaseWorkspaceService(db).review_matrix_row(CASE, INDEX, ROW, payload, user))


def test_a_user_cannot_assign_themselves_a_reviewer_specialty(db: _FakeDb) -> None:
    _edit(db, source_id="doc-2")
    with pytest.raises(HTTPException) as refused:
        _review(db, UNQUALIFIED, action="assign", reviewer_role="contracts", reviewer_user_id=UNQUALIFIED.id)
    assert refused.value.status_code == 403
    assert not _row(db).get("review_assignments")


def test_a_self_made_assignment_on_record_does_not_qualify_its_holder(db: _FakeDb) -> None:
    _edit(db, source_id="doc-2")
    _row(db)["review_assignments"] = [
        {"reviewer_role": "contracts", "reviewer_user_id": UNQUALIFIED.id, "assigned_by": UNQUALIFIED.id, "status": "assigned"}
    ]
    with pytest.raises(HTTPException) as refused:
        _review(db, UNQUALIFIED, action="approve", reviewer_role="contracts")
    assert refused.value.status_code == 403
    assert _row(db)["approval_status"] == "needs_review"


def test_an_assignment_made_by_another_user_still_qualifies_the_assignee(db: _FakeDb) -> None:
    _edit(db, source_id="doc-2")
    _review(db, ASSIGNER, action="assign", reviewer_role="contracts", reviewer_user_id=UNQUALIFIED.id)
    approved = _review(db, UNQUALIFIED, action="approve", reviewer_role="contracts")
    assert approved["approval_status"] == "approved"


@pytest.mark.parametrize("field,value", [("permission_notes", "Granted at hearing"), ("permission_approval_receipt_id", "receipt-x")])
def test_an_edit_cannot_write_rejoinder_permission_records(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        ArbitrationMatrixRowUpdate(**{field: value})
    with pytest.raises(ValidationError):
        ArbitrationMatrixRowCreate(**{field: value})


# ---------------------------------------------------------------------------
# Editing a paragraph response revokes a rejoinder permission granted on it
# ---------------------------------------------------------------------------


def _rejoinder_with_permission(db: _FakeDb) -> None:
    from backend.rbac_backend.services.arbitration_drafting.paragraph_positions import sync_paragraph_position_projection

    draft = db.arbitration_drafts.rows[0]
    draft["draft_type"] = "rejoinder"
    response = {
        "_id": "para-sod-20",
        "draft_id": "draft-1",
        "source_pleading_type": "statement_of_defence",
        "source_paragraph_number": "20",
        "source_paragraph_text": "The delay was caused by the Claimant.",
        "response_type": "admit",
        "response_text": "New matter: the access log shows late handover.",
        "supporting_source_ids": [],
        "missing_evidence": [],
        "new_matter": True,
        "permission_required": True,
        "permission_obtained": True,
        "permission_source_id": "doc-1",
        "permission_approved_by": "legal-1",
        "permission_approval_receipt_id": "receipt-1",
    }
    db.arbitration_paragraph_responses.rows.append(response)
    asyncio.run(sync_paragraph_position_projection(db, draft, response, actor_id="drafter-1"))
    db.arbitration_rejoinder_matrix.rows[0].update({"approval_status": "approved", "review_status": "approved"})


def _update_response(db: _FakeDb, text: str) -> dict:
    from backend.rbac_backend.models.arbitration_drafting import ArbitrationParagraphResponseUpdate
    from backend.rbac_backend.services.arbitration_drafting.service import ArbitrationDraftingService

    payload = ArbitrationParagraphResponseUpdate(response_type="admit", response_text=text)
    return asyncio.run(ArbitrationDraftingService(db).update_paragraph_response("draft-1", "para-sod-20", payload, _FakeUser("legal-1")))


def test_rewriting_permitted_new_matter_revokes_the_permission() -> None:
    db = _FakeDb()  # an unlocked draft; the module fixture approves and locks it
    _rejoinder_with_permission(db)

    updated = _update_response(db, "New matter: an entirely different contention.")

    projection = db.arbitration_rejoinder_matrix.rows[0]
    assert updated["permission_obtained"] is False
    assert updated["permission_approval_receipt_id"] is None
    assert projection["permission_obtained"] is False
    assert projection["approval_status"] == "needs_review"


def test_saving_an_unchanged_paragraph_response_keeps_the_permission() -> None:
    db = _FakeDb()  # an unlocked draft; the module fixture approves and locks it
    _rejoinder_with_permission(db)

    updated = _update_response(db, "New matter: the access log shows late handover.")

    assert updated["permission_obtained"] is True
    assert db.arbitration_rejoinder_matrix.rows[0]["permission_obtained"] is True


def test_a_colleague_without_the_specialty_cannot_confer_it(db: _FakeDb) -> None:
    # Otherwise two unqualified users could assign each other and approve.
    _edit(db, source_id="doc-2")
    with pytest.raises(HTTPException) as refused:
        _review(db, COLLEAGUE, action="assign", reviewer_role="contracts", reviewer_user_id=UNQUALIFIED.id)
    assert refused.value.status_code == 403
    assert not _row(db).get("review_assignments")


@pytest.mark.parametrize("field,value", [("deleted_at", "2026-10-27T00:00:00"), ("deleted_by", "editor-1")])
def test_an_edit_cannot_delete_a_row(field: str, value: str) -> None:
    # Deletion is a lifecycle action, not row content.
    with pytest.raises(ValidationError):
        ArbitrationMatrixRowUpdate(**{field: value})
    with pytest.raises(ValidationError):
        ArbitrationMatrixRowCreate(**{field: value})
