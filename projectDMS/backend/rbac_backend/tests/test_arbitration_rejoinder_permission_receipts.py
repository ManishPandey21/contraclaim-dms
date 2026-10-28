"""FU-1: a rejoinder permission counts only when a committed receipt backs it.

A permission decision writes its grant before it commits its receipt, so a
process that dies in between leaves `permission_obtained=True` behind a
`pending` receipt. These tests drive the real `record_rejoinder_permission`
over the rejoinder fixture from `test_arbitration_matrix_approval_invalidation`
and check every reader: readiness, the drafting context and validator, the
readiness approval that drafting approval and (queued) export reuse, and the
matrix listing.

Process death is simulated by stopping the decision at a persistence step with
the in-process undo disabled, because a killed process runs no handler. The
atomic conditional writes are also exercised against real MongoDB in
`integration/test_arbitration_permission_receipts_mongo.py`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from bson import ObjectId
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
    SOURCE_UNAVAILABLE,
    STALE_PENDING_RECEIPT_AGE,
    VERIFIED,
    annotate_permission_states,
    permission_content_hash,
    reconcile_stale_permission_receipts,
    rejoinder_permission_states,
)
from backend.rbac_backend.services.arbitration_drafting.validator import ArbitrationDraftValidator
from backend.rbac_backend.tests.test_arbitration_drafting import _FakeCollection, _FakeDb
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
    database.letters = _FakeCollection()
    _rejoinder_awaiting_permission(database)
    return database


def _row(db: _FakeDb) -> dict:
    return db.arbitration_rejoinder_matrix.rows[0]


def _response(db: _FakeDb) -> dict:
    return db.arbitration_paragraph_responses.rows[0]


def _source(db: _FakeDb) -> dict:
    return next(row for row in db.documents.rows if row.get("_id") == "doc-1")


def _receipts(db: _FakeDb) -> list[dict]:
    return [r for r in db.arbitration_workflow_approvals.rows if r.get("gate") == "rejoinder_permission"]


def _state(db: _FakeDb) -> str:
    return asyncio.run(rejoinder_permission_states(db, [_row(db)]))[str(_row(db)["_id"])]


def _later() -> datetime:
    return datetime.utcnow() + STALE_PENDING_RECEIPT_AGE + timedelta(seconds=1)


def _reconcile(db: _FakeDb, now: datetime | None = None) -> dict:
    return asyncio.run(reconcile_stale_permission_receipts(db, case_id=CASE, now=now or _later()))


def _readiness_blocks_new_matter(db: _FakeDb) -> bool:
    readiness = asyncio.run(ArbitrationCaseWorkspaceService(db).readiness(CASE, draft_type="rejoinder"))
    return any(check["check_key"] == "rejoinder_new_matter" for check in readiness["blockers"])


def _validator_blocks_new_matter(db: _FakeDb) -> bool:
    builder = ArbitrationContextBuilder(db)
    ledger = asyncio.run(builder._claim_defence_matrix_sources(CASE, 0, True, []))
    context = {
        "draft": {"draft_type": "rejoinder", "case_id": CASE},
        "source_ledger": ledger,
        "matrix_context": builder._matrix_context(ledger),
        "paragraph_responses": db.arbitration_paragraph_responses.rows[:1],
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
    [listed] = asyncio.run(annotate_permission_states(db, [_row(db)]))
    assert listed["permission_effective"] is False


# A completed decision
# ---------------------------------------------------------------------------


def test_a_completed_decision_is_verified_by_every_reader(db: _FakeDb) -> None:
    asyncio.run(_grant(db))

    [receipt] = _receipts(db)
    assert receipt["receipt_status"] == "committed"
    assert receipt["receipt_version"] == permission_receipts.PERMISSION_RECEIPT_VERSION
    assert receipt["matrix_row_id"] == _row(db)["_id"]
    assert receipt["source_paragraph_response_id"] == _response(db)["_id"]
    assert receipt["matrix_row_content_hash"] == permission_content_hash(_row(db))
    assert _state(db) == VERIFIED
    assert not _readiness_blocks_new_matter(db)
    assert not _validator_blocks_new_matter(db)
    [listed] = asyncio.run(annotate_permission_states(db, [_row(db)]))
    assert listed["permission_effective"] is True and listed["permission_receipt_message"] is None


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


def test_a_decision_on_a_source_that_is_not_authoritative_is_refused(db: _FakeDb) -> None:
    _source(db)["lifecycle_state"] = "deleted"
    with pytest.raises(HTTPException) as refused:
        asyncio.run(_grant(db))
    assert refused.value.status_code == 400
    assert _receipts(db) == []


# Process death at each persistence step
# ---------------------------------------------------------------------------


def _die_at(db: _FakeDb, monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    async def _no_undo(self, *args, **kwargs):  # a dead process runs no handler
        raise _Killed

    monkeypatch.setattr(ArbitrationCaseWorkspaceService, "_withdraw_permission_grant", _no_undo)
    targets = {
        "after_receipt_insert": (db.arbitration_paragraph_responses, "update_one", None),
        "after_response_grant": (db.arbitration_rejoinder_matrix, "find_one_and_update", None),
        "after_row_write": (db.arbitration_workflow_approvals, "update_one", "committed"),
    }
    if step == "after_commit":
        monkeypatch.setattr(ArbitrationCaseWorkspaceService, "invalidate_readiness_approvals", _no_undo)
        return
    target, method, only_status = targets[step]
    original = getattr(target, method)

    async def _dies(query=None, update=None, *args, **kwargs):
        if only_status and (update or {}).get("$set", {}).get("receipt_status") != only_status:
            return await original(query, update, *args, **kwargs)
        raise _Killed

    monkeypatch.setattr(target, method, _dies)


def _killed(db: _FakeDb, monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    _die_at(db, monkeypatch, step)
    with pytest.raises(_Killed):
        asyncio.run(_grant(db))
    monkeypatch.undo()


@pytest.mark.parametrize("step", ["after_receipt_insert", "after_response_grant", "after_row_write"])
def test_a_decision_killed_before_its_commit_grants_nothing(
    db: _FakeDb, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    _killed(db, monkeypatch, step)

    assert [r["receipt_status"] for r in _receipts(db)] == ["pending"]
    assert _response(db)["permission_obtained"] is (step != "after_receipt_insert")
    assert _row(db)["permission_obtained"] is (step == "after_row_write")
    _assert_every_reader_refuses(db)


def test_a_decision_killed_after_its_commit_is_complete(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    _killed(db, monkeypatch, "after_commit")

    assert [r["receipt_status"] for r in _receipts(db)] == ["committed"]
    assert _state(db) == VERIFIED


@pytest.mark.parametrize("step", ["after_receipt_insert", "after_response_grant", "after_row_write"])
def test_the_reconciler_aborts_a_stale_receipt_and_withdraws_its_grant(
    db: _FakeDb, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    _killed(db, monkeypatch, step)

    report = _reconcile(db)

    [receipt] = _receipts(db)
    assert report["aborted"] == [receipt["_id"]]
    assert receipt["receipt_status"] == "aborted"
    assert receipt["approver_id"] == "legal-9"  # the receipt is kept as the record of the attempt
    for record in (_response(db), _row(db)):
        assert record["permission_obtained"] is False
        assert record["permission_approval_receipt_id"] is None
    _assert_every_reader_refuses(db)


def test_the_reconciler_leaves_a_fresh_pending_receipt_alone(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    _killed(db, monkeypatch, "after_row_write")

    assert _reconcile(db, now=datetime.utcnow())["aborted"] == []
    assert [r["receipt_status"] for r in _receipts(db)] == ["pending"]
    assert _row(db)["permission_obtained"] is True  # untouched, and still refused by every reader
    _assert_every_reader_refuses(db)


def test_the_reconciler_never_touches_a_committed_receipt(db: _FakeDb) -> None:
    asyncio.run(_grant(db))

    assert _reconcile(db, now=_later() + timedelta(hours=1)) == {"aborted": [], "withdrawn_from": [], "errors": []}
    assert _state(db) == VERIFIED


def test_a_dry_run_reports_and_writes_nothing(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    _killed(db, monkeypatch, "after_row_write")
    [receipt] = _receipts(db)

    report = asyncio.run(reconcile_stale_permission_receipts(db, case_id=CASE, now=_later(), dry_run=True))

    assert report["aborted"] == [receipt["_id"]]
    assert receipt["receipt_status"] == "pending"
    assert _row(db)["permission_obtained"] is True


def test_validation_is_read_only(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    # Readiness, listing and context building never clean up, however stale.
    _killed(db, monkeypatch, "after_row_write")
    for receipt in _receipts(db):
        receipt["approved_at"] = datetime.utcnow() - STALE_PENDING_RECEIPT_AGE - timedelta(hours=1)

    _assert_every_reader_refuses(db)

    assert [r["receipt_status"] for r in _receipts(db)] == ["pending"]
    assert _row(db)["permission_obtained"] is True


def test_a_failed_withdrawal_is_reported_and_retried_by_the_next_run(
    db: _FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    _killed(db, monkeypatch, "after_row_write")
    rows = db.arbitration_rejoinder_matrix
    original = rows.update_many

    async def _fails(*args, **kwargs):
        raise RuntimeError("primary stepped down")

    rows.update_many = _fails
    report = _reconcile(db)
    rows.update_many = original

    # The receipt is aborted; the row still carries the grant, refused by every reader.
    [receipt] = _receipts(db)
    assert report["aborted"] == [receipt["_id"]]
    assert report["errors"] and "primary stepped down" in report["errors"][0]
    assert receipt["receipt_status"] == "aborted" and receipt["grants_withdrawn"] is False
    assert _row(db)["permission_obtained"] is True
    _assert_every_reader_refuses(db)

    report = _reconcile(db)
    assert report == {"aborted": [], "withdrawn_from": [receipt["_id"]], "errors": []}
    assert _row(db)["permission_obtained"] is False
    assert receipt["grants_withdrawn"] is True


def test_repeated_cleanup_is_idempotent(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    _killed(db, monkeypatch, "after_row_write")
    _reconcile(db)
    snapshot = [dict(r) for r in db.arbitration_rejoinder_matrix.rows], [dict(r) for r in _receipts(db)]

    assert _reconcile(db) == {"aborted": [], "withdrawn_from": [], "errors": []}
    assert ([dict(r) for r in db.arbitration_rejoinder_matrix.rows], [dict(r) for r in _receipts(db)]) == snapshot


def test_a_reconciler_that_loses_to_the_commit_changes_nothing(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    # The reconciler reads a stale pending snapshot; the decision commits before its abort.
    _killed(db, monkeypatch, "after_row_write")
    [receipt] = _receipts(db)
    approvals = db.arbitration_workflow_approvals
    original = approvals.update_one

    async def _commit_lands_first(query=None, update=None, *args, **kwargs):
        if (update or {}).get("$set", {}).get("receipt_status") == "aborted":
            receipt["receipt_status"] = "committed"
        return await original(query, update, *args, **kwargs)

    approvals.update_one = _commit_lands_first
    report = _reconcile(db)

    assert report["aborted"] == []
    assert receipt["receipt_status"] == "committed"
    assert _state(db) == VERIFIED


def test_a_decision_whose_receipt_was_reconciled_before_commit_is_refused(db: _FakeDb) -> None:
    approvals = db.arbitration_workflow_approvals
    original = approvals.update_one

    async def _reconciled_first(query=None, update=None, *args, **kwargs):
        if (update or {}).get("$set", {}).get("receipt_status") == "committed":
            approvals.update_one = original
            await reconcile_stale_permission_receipts(db, case_id=CASE, now=_later())
        return await original(query, update, *args, **kwargs)

    approvals.update_one = _reconciled_first
    with pytest.raises(HTTPException) as refused:
        asyncio.run(_grant(db))

    assert refused.value.status_code == 409
    assert [r["receipt_status"] for r in _receipts(db)] == ["aborted"]
    assert _row(db)["permission_obtained"] is False
    assert _response(db)["permission_obtained"] is False


def test_a_fresh_grant_replacing_an_interrupted_one_survives_its_cleanup(
    db: _FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    _killed(db, monkeypatch, "after_row_write")
    [old] = _receipts(db)
    old["approved_at"] = datetime.utcnow() - STALE_PENDING_RECEIPT_AGE - timedelta(seconds=1)

    # Recording again first cleans up the interrupted decision, then grants anew.
    asyncio.run(_grant(db))

    new = next(r for r in _receipts(db) if r["_id"] != old["_id"])
    assert old["receipt_status"] == "aborted"
    assert new["receipt_status"] == "committed"
    assert _row(db)["permission_approval_receipt_id"] == new["_id"]
    _reconcile(db, now=_later() + timedelta(hours=1))
    assert _row(db)["permission_approval_receipt_id"] == new["_id"]
    assert _state(db) == VERIFIED


def test_a_fresh_grant_survives_a_late_cleanup_of_the_old_receipt(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    # The old receipt is still fresh when the new grant lands, and goes stale later.
    _killed(db, monkeypatch, "after_row_write")
    [old] = _receipts(db)
    asyncio.run(_grant(db))

    _reconcile(db)

    assert old["receipt_status"] == "aborted"
    assert _row(db)["permission_approval_receipt_id"] != old["_id"]
    assert _state(db) == VERIFIED


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
        # The response was rewritten (revision bumped, grant revoked) and the
        # process died before the projection was synced: the row looks unchanged.
        (lambda db, r: _response(db).update({"response_revision": 1, "permission_obtained": False}), RECEIPT_MISMATCH),
        # An agent revoked the response's grant without bumping its revision.
        (lambda db, r: _response(db).update({"permission_obtained": False}), RECEIPT_MISMATCH),
        (lambda db, r: db.arbitration_paragraph_responses.rows.clear(), RECEIPT_MISMATCH),
        (lambda db, r: db.documents.rows.remove(_source(db)), SOURCE_UNAVAILABLE),
        (lambda db, r: _source(db).update({"lifecycle_state": "deleted"}), SOURCE_UNAVAILABLE),
        (lambda db, r: _source(db).update({"sha256": "different-bytes"}), SOURCE_UNAVAILABLE),
        (lambda db, r: _source(db).update({"current_version_id": "version-2"}), SOURCE_UNAVAILABLE),
        (lambda db, r: r.pop("permission_source_content_hash"), SOURCE_UNAVAILABLE),
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
        "row-content-changed",
        "response-rewritten-projection-not-synced",
        "response-grant-revoked-same-revision",
        "response-deleted",
        "source-deleted",
        "source-no-longer-authoritative",
        "source-bytes-changed",
        "source-version-changed",
        "receipt-without-source-binding",
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


# Hash stability
# ---------------------------------------------------------------------------


def test_the_content_hash_ignores_id_form_and_key_order() -> None:
    oid = ObjectId()
    as_object = {"case_id": "case-1", "draft_id": oid, "nested": {"b": 1, "a": [oid]}}
    as_string = {"nested": {"a": [str(oid)], "b": 1}, "draft_id": str(oid), "case_id": "case-1"}
    assert permission_content_hash(as_object) == permission_content_hash(as_string)


@pytest.mark.parametrize(
    "field,value",
    [
        ("updated_at", datetime(2030, 1, 1)),
        ("updated_by", "someone-else"),
        ("edit_revision", 42),
        ("review_status", "approved"),
        ("approval_log", [{"action": "approve"}]),
        ("material_contributor_ids", ["x"]),
        ("status", "approved"),
        ("permission_notes", "Granted at hearing"),
    ],
)
def test_bookkeeping_changes_keep_the_permission(db: _FakeDb, field: str, value) -> None:
    asyncio.run(_grant(db))
    _row(db)[field] = value
    assert _state(db) == VERIFIED


@pytest.mark.parametrize(
    "field,value",
    [
        ("claimant_reply", "A different reply."),
        ("nature_of_defence", "A different defence."),
        ("reply_to_counterclaim", "A new counterclaim reply."),
        ("source_sod_para", "21"),
        ("supporting_source_ids", ["doc-2"]),
        ("missing_evidence", ["Something else."]),
        ("new_matter", False),
        ("source_pleading_version_hash", "another-version"),
    ],
)
def test_material_changes_drop_the_permission(db: _FakeDb, field: str, value) -> None:
    asyncio.run(_grant(db))
    _row(db)[field] = value
    assert _state(db) != VERIFIED


# Legacy grants: refused, kept, explained, re-recordable
# ---------------------------------------------------------------------------


def test_a_legacy_grant_is_kept_explained_and_can_be_recorded_again(db: _FakeDb) -> None:
    # A grant from before receipts carried a content binding: committed, unbound.
    receipt = _committed(db)
    for key in ("receipt_version", "matrix_row_content_hash", "source_response_revision", "source_paragraph_response_id"):
        receipt.pop(key, None)
    legacy = dict(receipt)

    readiness = asyncio.run(ArbitrationCaseWorkspaceService(db).readiness(CASE, draft_type="rejoinder"))
    [check] = [c for c in readiness["blockers"] if c["check_key"] == "rejoinder_new_matter"]
    assert "a qualified legal reviewer must record the permission again" in check["message"]
    [listed] = asyncio.run(annotate_permission_states(db, [_row(db)]))
    assert listed["permission_obtained"] is True  # the stored grant is kept as the record
    assert listed["permission_effective"] is False
    assert listed["permission_receipt_state"] == LEGACY_UNBOUND
    assert "record the permission again" in listed["permission_receipt_message"]
    _reconcile(db, now=_later() + timedelta(days=30))
    assert receipt == legacy  # never migrated, aborted or approved automatically

    asyncio.run(_grant(db))
    assert _state(db) == VERIFIED
    assert receipt == legacy


# The display fields are computed on read and never stored
# ---------------------------------------------------------------------------


def test_echoed_display_fields_are_dropped_and_never_stored(db: _FakeDb) -> None:
    from backend.rbac_backend.models.arbitration_drafting import ArbitrationMatrixRowCreate
    from backend.rbac_backend.tests.test_arbitration_matrix_approval_invalidation import EDITOR

    payload = ArbitrationMatrixRowCreate(
        source_sod_para="30",
        claimant_reply="A reply.",
        permission_effective=True,
        permission_receipt_state="verified",
        permission_receipt_message="forged",
    )
    created = asyncio.run(ArbitrationCaseWorkspaceService(db).create_matrix_row(CASE, "rejoinder-matrix", payload, EDITOR))

    stored = next(row for row in db.arbitration_rejoinder_matrix.rows if row["_id"] == created["_id"])
    assert not set(permission_receipts.PERMISSION_DISPLAY_FIELDS) & set(stored)


def test_display_fields_do_not_change_the_content_hash(db: _FakeDb) -> None:
    asyncio.run(_grant(db))
    [listed] = asyncio.run(annotate_permission_states(db, [_row(db)]))
    assert permission_content_hash(listed) == permission_content_hash(_row(db))


# The decision path
# ---------------------------------------------------------------------------


def test_a_source_stored_under_an_object_id_supports_a_permission(db: _FakeDb) -> None:
    from backend.rbac_backend.models.arbitration_drafting import ArbitrationRejoinderPermissionRequest
    from backend.rbac_backend.tests.test_arbitration_matrix_approval_invalidation import PERMISSION_GRANTER

    oid = ObjectId()
    _source(db)["_id"] = oid
    payload = ArbitrationRejoinderPermissionRequest(permission_source_id=str(oid))
    service = ArbitrationCaseWorkspaceService(db)
    asyncio.run(service.record_rejoinder_permission(CASE, _row(db)["_id"], payload, PERMISSION_GRANTER))

    assert _state(db) == VERIFIED


def test_a_cleanup_failure_does_not_block_a_decision(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.rbac_backend.services.arbitration_drafting import case_workspace

    async def _fails(*args, **kwargs):
        raise RuntimeError("cleanup unavailable")

    monkeypatch.setattr(case_workspace, "reconcile_stale_permission_receipts", _fails)
    asyncio.run(_grant(db))

    assert _state(db) == VERIFIED


def test_a_refused_caller_triggers_no_cleanup(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.rbac_backend.models.arbitration_drafting import ArbitrationRejoinderPermissionRequest
    from backend.rbac_backend.tests.test_arbitration_drafting import _FakeUser

    _killed(db, monkeypatch, "after_row_write")
    [stale] = _receipts(db)
    stale["approved_at"] = datetime.utcnow() - STALE_PENDING_RECEIPT_AGE - timedelta(hours=1)
    author = _FakeUser("drafter-1", roles=["legal_reviewer"])  # wrote the content; may not grant on it
    payload = ArbitrationRejoinderPermissionRequest(permission_source_id="doc-1")

    with pytest.raises(HTTPException) as refused:
        asyncio.run(ArbitrationCaseWorkspaceService(db).record_rejoinder_permission(CASE, _row(db)["_id"], payload, author))

    assert refused.value.status_code == 409  # author-approver separation
    assert stale["receipt_status"] == "pending"


def _undo_revert_fails(db: _FakeDb) -> None:
    """The row write fails, and the undo's revert of the response grant fails too."""
    rows = db.arbitration_rejoinder_matrix
    responses = db.arbitration_paragraph_responses
    original_response_write = responses.update_one
    writes = []

    async def _row_write_fails(*args, **kwargs):
        raise RuntimeError("primary stepped down")

    async def _second_response_write_fails(*args, **kwargs):
        writes.append(True)
        if len(writes) == 2:  # the first is the grant, the second the undo's revert
            raise RuntimeError("still stepping down")
        return await original_response_write(*args, **kwargs)

    rows.find_one_and_update = _row_write_fails
    responses.update_one = _second_response_write_fails
    with pytest.raises(RuntimeError):
        asyncio.run(_grant(db))
    del rows.find_one_and_update, responses.update_one  # back to the class methods


def test_a_failed_undo_records_an_unfinished_withdrawal_that_cleanup_finishes(db: _FakeDb) -> None:
    _undo_revert_fails(db)

    [receipt] = _receipts(db)
    assert receipt["receipt_status"] == "aborted"
    assert receipt["grants_withdrawn"] is False  # the revert did not happen
    assert _response(db)["permission_obtained"] is True  # left behind, refused by every reader
    _assert_every_reader_refuses(db)

    # The receipt was aborted by the decision itself, not by cleanup, and is not stale.
    report = _reconcile(db, now=datetime.utcnow())
    assert report == {"aborted": [], "withdrawn_from": [receipt["_id"]], "errors": []}
    assert _response(db)["permission_obtained"] is False
    assert receipt["grants_withdrawn"] is True


def test_a_successful_undo_records_its_withdrawal(db: _FakeDb) -> None:
    rows = db.arbitration_rejoinder_matrix

    async def _row_write_fails(*args, **kwargs):
        raise RuntimeError("primary stepped down")

    rows.find_one_and_update = _row_write_fails
    with pytest.raises(RuntimeError):
        asyncio.run(_grant(db))
    del rows.find_one_and_update

    [receipt] = _receipts(db)
    assert receipt["receipt_status"] == "aborted" and receipt["grants_withdrawn"] is True
    assert _reconcile(db) == {"aborted": [], "withdrawn_from": [], "errors": []}


def test_a_grant_written_after_cleanup_ran_is_withdrawn_by_the_next_run(
    db: _FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The decision stalls past the cutoff after its response grant. Cleanup aborts
    # its receipt (and withdraws the response grant); the stalled decision then
    # writes the row grant and dies before its commit.
    _killed(db, monkeypatch, "after_response_grant")
    [receipt] = _receipts(db)
    _reconcile(db)
    assert receipt["receipt_status"] == "aborted" and receipt["grants_withdrawn"] is True
    _row(db).update({"permission_obtained": True, "permission_approval_receipt_id": receipt["_id"]})
    _assert_every_reader_refuses(db)

    report = _reconcile(db)

    assert report["withdrawn_from"] == [receipt["_id"]]
    assert _row(db)["permission_obtained"] is False


def test_a_decision_that_continues_after_the_cutoff_is_refused_and_leaves_nothing(db: _FakeDb) -> None:
    # Cleanup aborts the receipt while the decision is between its row write and commit.
    approvals = db.arbitration_workflow_approvals
    original = approvals.update_one

    async def _stalls_past_the_cutoff(query=None, update=None, *args, **kwargs):
        if (update or {}).get("$set", {}).get("receipt_status") == "committed":
            approvals.update_one = original
            await reconcile_stale_permission_receipts(db, case_id=CASE, now=_later())
        return await original(query, update, *args, **kwargs)

    approvals.update_one = _stalls_past_the_cutoff
    with pytest.raises(HTTPException) as refused:
        asyncio.run(_grant(db))

    assert refused.value.status_code == 409
    [receipt] = _receipts(db)
    assert receipt["receipt_status"] == "aborted"
    assert _row(db)["permission_obtained"] is False and _response(db)["permission_obtained"] is False
    assert _reconcile(db, now=_later()) == {"aborted": [], "withdrawn_from": [], "errors": []}


def test_a_replacement_grant_survives_cleanup_of_a_failed_undo(db: _FakeDb) -> None:
    _undo_revert_fails(db)
    [old] = _receipts(db)

    asyncio.run(_grant(db))  # a legal reviewer records the permission again
    new = next(r for r in _receipts(db) if r["_id"] != old["_id"])
    _reconcile(db, now=_later() + timedelta(hours=1))

    assert _row(db)["permission_approval_receipt_id"] == new["_id"]
    assert _response(db)["permission_approval_receipt_id"] == new["_id"]
    assert _state(db) == VERIFIED


def test_a_response_resave_never_creates_a_grant_without_its_receipt(db: _FakeDb) -> None:
    # A response grant left behind by a failed undo, then the response is resaved
    # unchanged, which re-syncs its projection row.
    _undo_revert_fails(db)
    [receipt] = _receipts(db)
    asyncio.run(
        sync_paragraph_position_projection(db, db.arbitration_drafts.rows[0], _response(db), actor_id="drafter-1")
    )

    # The row now carries the grant together with the receipt behind it, so it
    # reads as an incomplete decision, not as a legacy grant with no receipt.
    assert _row(db)["permission_approval_receipt_id"] == receipt["_id"]
    assert _state(db) == RECEIPT_NOT_COMMITTED
    _reconcile(db, now=datetime.utcnow())
    assert _row(db)["permission_obtained"] is False and _response(db)["permission_obtained"] is False


def test_a_dry_run_lists_unfinished_withdrawals(db: _FakeDb) -> None:
    _undo_revert_fails(db)
    [receipt] = _receipts(db)

    report = asyncio.run(reconcile_stale_permission_receipts(db, case_id=CASE, dry_run=True))

    assert report == {"aborted": [], "withdrawn_from": [receipt["_id"]], "errors": []}
    assert _response(db)["permission_obtained"] is True  # nothing written


def test_the_script_prints_its_report_and_fails_on_errors(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from backend.rbac_backend.core import database
    from backend.rbac_backend.scripts import reconcile_rejoinder_permission_receipts as script

    async def _noop() -> None:
        return None

    async def _database():
        return object()

    async def _partial(db, **kwargs):
        return {"aborted": ["r-1"], "withdrawn_from": [], "errors": ["r-2: withdrawing: lost"]}

    monkeypatch.setattr(database, "connect", _noop)
    monkeypatch.setattr(database, "disconnect", _noop)
    monkeypatch.setattr(database, "get_database", _database)
    monkeypatch.setattr(permission_receipts, "reconcile_stale_permission_receipts", _partial)

    assert asyncio.run(script._main(None, apply=True)) == 1
    out, err = capsys.readouterr()
    assert '"aborted": ["r-1"]' in out
    assert "r-2: withdrawing: lost" in err


# The content hash: versioned, and every written field classified
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(permission_receipts.HISTORICAL_REVIEW_FIELDS))
def test_historical_review_labels_keep_the_permission(db: _FakeDb, field: str) -> None:
    asyncio.run(_grant(db))
    _row(db)[field] = "labelled"
    assert _state(db) == VERIFIED


_CONTENT_FIELDS_WITHOUT_A_SEPARATE_CHECK = sorted(
    permission_receipts.REJOINDER_CONTENT_FIELDS - {"case_id", "draft_id", "source_paragraph_response_id", "source_response_revision"}
)


@pytest.mark.parametrize("field", _CONTENT_FIELDS_WITHOUT_A_SEPARATE_CHECK)
def test_each_material_field_change_drops_the_permission(db: _FakeDb, field: str) -> None:
    asyncio.run(_grant(db))
    _row(db)[field] = f"changed {field}"
    assert _state(db) == RECEIPT_MISMATCH


def test_an_unclassified_field_counts_as_content(db: _FakeDb) -> None:
    # A field no one has classified yet is hashed, so it fails closed.
    asyncio.run(_grant(db))
    _row(db)["a_future_field"] = "anything"
    assert _state(db) == RECEIPT_MISMATCH


def test_a_receipt_written_before_hash_versioning_is_checked_with_version_1(db: _FakeDb) -> None:
    receipt = _committed(db)
    receipt.pop("matrix_row_content_hash_version")
    receipt["matrix_row_content_hash"] = permission_content_hash(_row(db), 1)
    assert _state(db) == VERIFIED
    # Version 1 counted the historical labels as content; it stays that strict.
    _row(db)["historical_review_status"] = "needs_legal_review"
    assert _state(db) == RECEIPT_MISMATCH


@pytest.mark.parametrize("version", [3, "2", None, True])
def test_an_unknown_hash_version_fails_closed(db: _FakeDb, version) -> None:
    receipt = _committed(db)
    receipt["matrix_row_content_hash_version"] = version
    assert _state(db) == RECEIPT_MISMATCH


def _client_rejoinder_columns() -> set[str]:
    import re
    from pathlib import Path as _Path

    page = _Path(__file__).resolve().parents[3] / "client" / "src" / "pages" / "ArbitrationCaseWorkspacePage.tsx"
    block = re.search(r'"rejoinder-matrix": \[(.*?)\]', page.read_text(encoding="utf-8"), re.S)
    assert block, "the client's rejoinder-matrix column list moved"
    return set(re.findall(r'key: "([a-z_]+)"', block.group(1)))


def _written_rejoinder_fields() -> set[str]:
    """Every field a known writer puts on a rejoinder row."""
    import re
    from pathlib import Path as _Path

    from backend.rbac_backend.services.arbitration_drafting import case_workspace, paragraph_positions
    from backend.rbac_backend.services.arbitration_drafting.approval_policy import REVOKED_REJOINDER_PERMISSION

    service_dir = _Path(case_workspace.__file__).parent
    historical = set(re.findall(r'"(historical_[a-z_]+)"', (service_dir / "historical_paragraph_review.py").read_text(encoding="utf-8")))
    projection = set(paragraph_positions._projection_content("rejoinder-matrix", {}))
    sync = {"projection_hash", "source_response_revision", "updated_by", "updated_at", "edit_revision",
            "last_material_editor_id", "material_contributor_ids", "_id", "case_id", "draft_id",
            "organization_id", "project_id", "contract_id", "created_by", "created_at"}
    reviews = set(paragraph_positions._review_defaults()) | set(case_workspace._matrix_review_defaults("rejoinder-matrix"))
    decision = {*REVOKED_REJOINDER_PERMISSION, "permission_source_id", "permission_notes"}
    return (
        historical | projection | sync | reviews | decision | _client_rejoinder_columns()
        | set(case_workspace.SERVER_CONTROLLED_MATRIX_FIELDS) | set(case_workspace.SERVER_BOOKKEEPING_MATRIX_FIELDS)
        | set(case_workspace.MATRIX_ROW_OWNERSHIP_FIELDS) | {"tribunal_permission_required"}
        | _legacy_and_migration_fields()
    )


def _legacy_and_migration_fields() -> set[str]:
    """Fields historical rows and the phase-0 migration carry."""
    import re
    from pathlib import Path as _Path

    from backend.rbac_backend.services.arbitration_drafting import case_workspace

    service_dir = _Path(case_workspace.__file__).parent
    migration = service_dir.parents[1] / "migrations" / "v20260721_0001_arbitration_phase0_containment.py"
    # The legacy paragraph identifiers historical_paragraph_review reads from rejoinder rows.
    legacy = {"paragraph_response_id", "source_paragraph_number", "paragraph_number"}
    historical = (service_dir / "historical_paragraph_review.py").read_text(encoding="utf-8")
    assert all(f'"{key}"' in historical for key in legacy), "historical review no longer reads these fields"
    return set(re.findall(r'"(migration_[a-z_]+)":', migration.read_text(encoding="utf-8"))) | legacy


def test_every_written_rejoinder_field_is_classified() -> None:
    """A new field must be declared content or non-content, never left to chance.

    Unclassified fields are hashed (fail closed), so this guard is what stops a
    new bookkeeping field from silently revoking every permission.
    """
    content, non_content = permission_receipts.REJOINDER_CONTENT_FIELDS, permission_receipts.NON_CONTENT_FIELDS
    assert not content & non_content, f"classified both ways: {sorted(content & non_content)}"
    unclassified = sorted(_written_rejoinder_fields() - content - non_content)
    assert unclassified == [], (
        f"rejoinder row fields with no classification: {unclassified}. Add each to "
        "REJOINDER_CONTENT_FIELDS (a change needs a new permission) or NON_CONTENT_FIELDS."
    )


def test_withdrawing_an_old_receipt_never_touches_a_newer_grant(db: _FakeDb) -> None:
    _undo_revert_fails(db)
    [old] = _receipts(db)
    asyncio.run(_grant(db))

    # Called directly, as cleanup would for any receipt it finds aborted.
    withdrawn = asyncio.run(permission_receipts.withdraw_grants_citing(db, old["_id"]))

    assert withdrawn is False
    assert _row(db)["permission_obtained"] is True and _response(db)["permission_obtained"] is True
    assert _state(db) == VERIFIED


def test_a_response_grant_the_grant_scan_cannot_reach_is_still_withdrawn(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    # The decision died after its response grant; the response's draft is gone,
    # so the grant scan cannot find it. The aborted receipt records the
    # withdrawal as unfinished, and cleanup withdraws it by receipt id.
    _killed(db, monkeypatch, "after_response_grant")
    db.arbitration_drafts.rows.clear()
    [receipt] = _receipts(db)

    report = _reconcile(db)

    assert report["aborted"] == [receipt["_id"]]
    assert report["withdrawn_from"] == [receipt["_id"]]
    assert _response(db)["permission_obtained"] is False
    assert receipt["grants_withdrawn"] is True
