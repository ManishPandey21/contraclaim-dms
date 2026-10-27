"""FU-1: a rejoinder permission counts only when a committed receipt backs it.

A permission decision writes its grant before it commits its receipt, so a
process that dies in between leaves `permission_obtained=True` behind a
`pending` receipt. These tests drive the real `record_rejoinder_permission`
over the rejoinder fixture from `test_arbitration_matrix_approval_invalidation`
and check every reader: readiness, the drafting context and validator, and the
readiness approval that drafting approval and export reuse.

Process death is simulated by stopping the decision at a write step with the
in-process undo disabled, because a killed process runs no handler.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.rbac_backend.services.arbitration_drafting import permission_receipts
from backend.rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from backend.rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder
from backend.rbac_backend.services.arbitration_drafting.paragraph_positions import sync_paragraph_position_projection
from backend.rbac_backend.services.arbitration_drafting.permission_receipts import (
    LEGACY_UNBOUND,
    NOT_GRANTED,
    RECEIPT_MISMATCH,
    RECEIPT_MISSING,
    RECEIPT_NOT_COMMITTED,
    STALE_PENDING_RECEIPT_AGE,
    VERIFIED,
    reconcile_stale_permission_receipts,
    rejoinder_permission_states,
)
from backend.rbac_backend.services.arbitration_drafting.validator import ArbitrationDraftValidator
from backend.rbac_backend.tests.test_arbitration_drafting import _FakeDb
from backend.rbac_backend.tests.test_arbitration_matrix_approval_invalidation import (
    CASE,
    _grant,
    _rejoinder_awaiting_permission,
)


class _Killed(BaseException):
    """The process died here: nothing after this point runs."""


@pytest.fixture
def db() -> _FakeDb:
    database = _FakeDb()
    _rejoinder_awaiting_permission(database)
    return database


def _row(db: _FakeDb) -> dict:
    return db.arbitration_rejoinder_matrix.rows[0]


def _response(db: _FakeDb) -> dict:
    return db.arbitration_paragraph_responses.rows[0]


def _receipts(db: _FakeDb) -> list[dict]:
    return [r for r in db.arbitration_workflow_approvals.rows if r.get("gate") == "rejoinder_permission"]


def _state(db: _FakeDb) -> str:
    return asyncio.run(rejoinder_permission_states(db, [_row(db)]))[str(_row(db)["_id"])]


def _readiness_blocks_new_matter(db: _FakeDb) -> bool:
    readiness = asyncio.run(
        ArbitrationCaseWorkspaceService(db).readiness(CASE, draft_type="rejoinder", persist=False)
    )
    return any(check["check_key"] == "rejoinder_new_matter" for check in readiness["blockers"])


def _validator_blocks_new_matter(db: _FakeDb) -> bool:
    builder = ArbitrationContextBuilder(db)
    ledger = asyncio.run(builder._claim_defence_matrix_sources(CASE, 0, True, []))
    context = {
        "draft": {"draft_type": "rejoinder", "case_id": CASE},
        "source_ledger": ledger,
        "matrix_context": builder._matrix_context(ledger),
        "paragraph_responses": [_response(db)],
    }
    report = ArbitrationDraftValidator().validation_report(context, "Reply. [S1]")
    return any("introduces new matter" in blocker for blocker in report["approval_blockers"])


def _readiness_approval_refuses_unverified_permission(db: _FakeDb) -> bool:
    service = ArbitrationCaseWorkspaceService(db)
    case = asyncio.run(service.get_case(CASE))
    with pytest.raises(HTTPException) as refused:
        asyncio.run(service.require_readiness_approval(case, {"_id": "draft-1", "draft_type": "rejoinder"}))
    detail = refused.value.detail
    return refused.value.status_code == 409 and isinstance(detail, dict) and "permission_state" in detail


def _assert_every_reader_refuses(db: _FakeDb) -> None:
    assert _readiness_blocks_new_matter(db)
    assert _validator_blocks_new_matter(db)
    assert _readiness_approval_refuses_unverified_permission(db)


# A completed decision
# ---------------------------------------------------------------------------


def test_a_completed_decision_is_verified_by_every_reader(db: _FakeDb) -> None:
    asyncio.run(_grant(db))

    [receipt] = _receipts(db)
    assert receipt["receipt_status"] == "committed"
    assert receipt["receipt_version"] == permission_receipts.PERMISSION_RECEIPT_VERSION
    assert receipt["matrix_row_id"] == _row(db)["_id"]
    assert receipt["source_paragraph_response_id"] == _response(db)["_id"]
    assert receipt["matrix_row_content_hash"] == permission_receipts.permission_content_hash(_row(db))
    assert _state(db) == VERIFIED
    assert not _readiness_blocks_new_matter(db)
    assert not _validator_blocks_new_matter(db)


def test_a_later_review_or_unchanged_projection_sync_keeps_the_permission(db: _FakeDb) -> None:
    asyncio.run(_grant(db))
    # A review decision advances the revision and review state, not the content.
    _row(db).update(
        {
            "edit_revision": int(_row(db).get("edit_revision") or 0) + 1,
            "review_status": "approved",
            "approval_status": "approved",
            "status": "approved",
            "approved_by": "reviewer-7",
            "updated_at": datetime.utcnow(),
        }
    )
    asyncio.run(sync_paragraph_position_projection(db, db.arbitration_drafts.rows[0], _response(db), actor_id="drafter-1"))

    assert _state(db) == VERIFIED


# Process death between the grant writes and the receipt commit
# ---------------------------------------------------------------------------


def _die_at(db: _FakeDb, monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    async def _no_undo(self, *args, **kwargs):  # a dead process runs no handler
        raise _Killed

    monkeypatch.setattr(ArbitrationCaseWorkspaceService, "_withdraw_permission_grant", _no_undo)
    if step == "after_response_grant":
        target, method = db.arbitration_rejoinder_matrix, "find_one_and_update"
    else:  # after the row write, before the commit
        target, method = db.arbitration_workflow_approvals, "update_one"
    original = getattr(target, method)

    async def _dies(query=None, update=None, *args, **kwargs):
        if method == "update_one" and (update or {}).get("$set", {}).get("receipt_status") != "committed":
            return await original(query, update, *args, **kwargs)
        raise _Killed

    setattr(target, method, _dies)


@pytest.mark.parametrize("step", ["after_response_grant", "after_row_write"])
def test_a_decision_killed_before_its_commit_grants_nothing(
    db: _FakeDb, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    _die_at(db, monkeypatch, step)
    with pytest.raises(_Killed):
        asyncio.run(_grant(db))
    monkeypatch.undo()

    # What the dead process left behind: a grant and a pending receipt.
    assert _response(db)["permission_obtained"] is True
    if step == "after_row_write":
        assert _row(db)["permission_obtained"] is True
    assert [r["receipt_status"] for r in _receipts(db)] == ["pending"]

    _assert_every_reader_refuses(db)


def test_the_reconciler_aborts_a_stale_receipt_and_withdraws_its_grant(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    _die_at(db, monkeypatch, "after_row_write")
    with pytest.raises(_Killed):
        asyncio.run(_grant(db))
    monkeypatch.undo()

    later = datetime.utcnow() + STALE_PENDING_RECEIPT_AGE + timedelta(seconds=1)
    aborted = asyncio.run(reconcile_stale_permission_receipts(db, case_id=CASE, now=later))

    [receipt] = _receipts(db)
    assert aborted == [receipt["_id"]]
    assert receipt["receipt_status"] == "aborted"
    assert _response(db)["permission_obtained"] is False
    assert _row(db)["permission_obtained"] is False
    assert _response(db)["permission_approval_receipt_id"] is None
    assert _row(db)["permission_approval_receipt_id"] is None
    _assert_every_reader_refuses(db)


def test_the_reconciler_leaves_a_fresh_pending_receipt_alone(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    _die_at(db, monkeypatch, "after_row_write")
    with pytest.raises(_Killed):
        asyncio.run(_grant(db))
    monkeypatch.undo()

    assert asyncio.run(reconcile_stale_permission_receipts(db, case_id=CASE)) == []
    assert [r["receipt_status"] for r in _receipts(db)] == ["pending"]
    assert _row(db)["permission_obtained"] is True  # untouched, and still refused by every reader
    _assert_every_reader_refuses(db)


def test_the_reconciler_never_touches_a_committed_receipt(db: _FakeDb) -> None:
    asyncio.run(_grant(db))
    later = datetime.utcnow() + STALE_PENDING_RECEIPT_AGE + timedelta(hours=1)

    assert asyncio.run(reconcile_stale_permission_receipts(db, case_id=CASE, now=later)) == []
    assert _state(db) == VERIFIED


def test_a_decision_whose_receipt_was_reconciled_before_commit_is_refused(db: _FakeDb) -> None:
    # The reconciler judges the receipt stale between the row write and the commit.
    approvals = db.arbitration_workflow_approvals
    original = approvals.update_one

    async def _reconciled_first(query=None, update=None, *args, **kwargs):
        if (update or {}).get("$set", {}).get("receipt_status") == "committed":
            later = datetime.utcnow() + STALE_PENDING_RECEIPT_AGE + timedelta(seconds=1)
            approvals.update_one = original
            await reconcile_stale_permission_receipts(db, case_id=CASE, now=later)
        return await original(query, update, *args, **kwargs)

    approvals.update_one = _reconciled_first
    with pytest.raises(HTTPException) as refused:
        asyncio.run(_grant(db))

    assert refused.value.status_code == 409
    assert [r["receipt_status"] for r in _receipts(db)] == ["aborted"]
    assert _row(db)["permission_obtained"] is False
    assert _response(db)["permission_obtained"] is False


def test_readiness_reconciles_stale_receipts_when_it_persists(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    _die_at(db, monkeypatch, "after_row_write")
    with pytest.raises(_Killed):
        asyncio.run(_grant(db))
    monkeypatch.undo()
    for receipt in _receipts(db):
        receipt["approved_at"] = datetime.utcnow() - STALE_PENDING_RECEIPT_AGE - timedelta(seconds=1)

    asyncio.run(ArbitrationCaseWorkspaceService(db).readiness(CASE, draft_type="rejoinder"))

    assert [r["receipt_status"] for r in _receipts(db)] == ["aborted"]
    assert _row(db)["permission_obtained"] is False


# Every way a receipt can fail to back a permission
# ---------------------------------------------------------------------------


def _committed(db: _FakeDb) -> dict:
    asyncio.run(_grant(db))
    [receipt] = _receipts(db)
    return receipt


@pytest.mark.parametrize(
    "corrupt,expected",
    [
        (lambda db, r: r.update({"receipt_status": "pending"}), RECEIPT_NOT_COMMITTED),
        (lambda db, r: r.update({"receipt_status": "aborted"}), RECEIPT_NOT_COMMITTED),
        (lambda db, r: r.update({"invalidated_at": datetime.utcnow()}), RECEIPT_NOT_COMMITTED),
        (lambda db, r: db.arbitration_workflow_approvals.rows.remove(r), RECEIPT_MISSING),
        (lambda db, r: _row(db).update({"permission_approval_receipt_id": "receipt-of-something-else"}), RECEIPT_MISSING),
        (lambda db, r: r.update({"case_id": "case-other"}), RECEIPT_MISMATCH),
        (lambda db, r: r.update({"matrix_row_id": "row-other"}), RECEIPT_MISMATCH),
        (lambda db, r: r.update({"draft_id": "draft-other"}), RECEIPT_MISMATCH),
        (lambda db, r: r.update({"decision": "rejected"}), RECEIPT_MISMATCH),
        (lambda db, r: r.update({"permission_source_id": "doc-other"}), RECEIPT_MISMATCH),
        (lambda db, r: r.update({"source_response_revision": 99}), RECEIPT_MISMATCH),
        (lambda db, r: _row(db).update({"claimant_reply": "Content no one decided on."}), RECEIPT_MISMATCH),
        (lambda db, r: r.pop("receipt_version"), LEGACY_UNBOUND),
        (lambda db, r: _row(db).update({"permission_approval_receipt_id": None}), LEGACY_UNBOUND),
    ],
    ids=[
        "pending",
        "aborted",
        "invalidated",
        "receipt-deleted",
        "row-cites-another-receipt",
        "other-case",
        "other-row",
        "other-draft",
        "not-an-approval",
        "other-permission-source",
        "earlier-response-revision",
        "content-changed",
        "receipt-without-binding",
        "grant-without-receipt",
    ],
)
def test_a_permission_without_a_valid_committed_receipt_is_refused(db: _FakeDb, corrupt, expected: str) -> None:
    corrupt(db, _committed(db))

    assert _state(db) == expected
    _assert_every_reader_refuses(db)


def test_a_row_without_a_grant_is_not_granted(db: _FakeDb) -> None:
    assert _state(db) == NOT_GRANTED
    _assert_every_reader_refuses(db)


# Legacy grants: refused, surfaced, and re-recordable
# ---------------------------------------------------------------------------


def test_a_legacy_grant_is_named_in_readiness_and_can_be_recorded_again(db: _FakeDb) -> None:
    # A grant from before receipts carried a content binding: committed, unbound.
    receipt = _committed(db)
    for key in ("receipt_version", "matrix_row_content_hash", "source_response_revision", "source_paragraph_response_id"):
        receipt.pop(key, None)

    readiness = asyncio.run(ArbitrationCaseWorkspaceService(db).readiness(CASE, draft_type="rejoinder", persist=False))
    [check] = [c for c in readiness["blockers"] if c["check_key"] == "rejoinder_new_matter"]
    assert "record the permission again" in check["message"]

    asyncio.run(_grant(db))
    assert _state(db) == VERIFIED
