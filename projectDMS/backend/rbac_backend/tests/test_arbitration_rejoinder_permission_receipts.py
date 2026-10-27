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


def test_a_failed_withdrawal_is_retried_by_the_next_run(db: _FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    _killed(db, monkeypatch, "after_row_write")
    rows = db.arbitration_rejoinder_matrix
    original = rows.update_many

    async def _fails(*args, **kwargs):
        raise RuntimeError("primary stepped down")

    rows.update_many = _fails
    with pytest.raises(RuntimeError):
        _reconcile(db)
    rows.update_many = original

    # The receipt is aborted; the row still carries the grant, refused by every reader.
    assert [r["receipt_status"] for r in _receipts(db)] == ["aborted"]
    assert _row(db)["permission_obtained"] is True
    _assert_every_reader_refuses(db)

    report = _reconcile(db)
    assert report["withdrawn_from"] == [_receipts(db)[0]["_id"]]
    assert _row(db)["permission_obtained"] is False


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


def test_receipts_aborted_by_the_decision_itself_are_not_rescanned(db: _FakeDb) -> None:
    # The decision's own undo already withdrew its grants; only the cleanup's
    # own unfinished aborts are revisited, so the sweep stays bounded.
    rows = db.arbitration_rejoinder_matrix
    original = rows.find_one_and_update

    async def _row_write_fails(*args, **kwargs):
        raise RuntimeError("primary stepped down")

    rows.find_one_and_update = _row_write_fails
    with pytest.raises(RuntimeError):
        asyncio.run(_grant(db))
    rows.find_one_and_update = original
    [receipt] = _receipts(db)
    assert receipt["receipt_status"] == "aborted" and "grants_withdrawn" not in receipt

    calls = []
    original_many = rows.update_many

    async def _counted(*args, **kwargs):
        calls.append(args)
        return await original_many(*args, **kwargs)

    rows.update_many = _counted
    _reconcile(db)
    assert calls == []
