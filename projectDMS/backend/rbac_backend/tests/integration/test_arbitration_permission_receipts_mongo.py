"""FU-1 on real MongoDB: the receipt commit and the stale-receipt cleanup are atomic.

The unit suite (`test_arbitration_rejoinder_permission_receipts.py`) drives the
decision path over an in-memory fake that runs every write in sequence. What
only a real server can show is that the two conditional writes racing for one
`pending` receipt - the decision's commit and the reconciler's abort - let
exactly one win, and that the cleanup withdraws only grants that still cite the
aborted receipt.

What this pins, against a disposable replica set:

* a stale `pending` receipt is aborted and its grants withdrawn; a fresh one and
  a committed one are left alone; `approved_at` compares as a BSON date;
* commit and abort raced on the same receipt: exactly one wins, every time, and
  the readers agree with the winner; both orders are also forced deterministically
  (abort before commit; cleanup reads `pending`, the commit lands, then its abort);
* a newer grant under another receipt is never withdrawn;
* a withdrawal that failed after the abort is finished by the next run;
* ids stored as ObjectId and as strings hash and verify the same.

Opt-in: set ``ARBITRATION_PERMISSION_RECEIPTS_MONGODB_URI`` to a disposable replica set.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.services.arbitration_drafting.permission_receipts import (
    PERMISSION_RECEIPT_GATE,
    RECEIPT_NOT_COMMITTED,
    STALE_PENDING_RECEIPT_AGE,
    VERIFIED,
    commit_permission_receipt,
    permission_receipt_binding,
    reconcile_stale_permission_receipts,
    rejoinder_permission_states,
)

MONGODB_URI_ENV = "ARBITRATION_PERMISSION_RECEIPTS_MONGODB_URI"

pytestmark = pytest.mark.integration

CASE = "case-fu1"
ORG, PROJECT = "org-fu1", "project-fu1"


@pytest.fixture(autouse=True)
def _uri() -> None:
    if not os.getenv(MONGODB_URI_ENV):
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")


def _with_db(body: Callable[[Any], Awaitable[None]]) -> None:
    async def _run() -> None:
        client = AsyncIOMotorClient(os.environ[MONGODB_URI_ENV], serverSelectionTimeoutMS=5_000)
        name = f"fu1_receipts_{uuid.uuid4().hex[:12]}"
        try:
            await body(client[name])
        finally:
            await client.drop_database(name)
            client.close()

    asyncio.run(_run())


async def _seed(db: Any, *, receipt_status: str, age: timedelta, row_id: Any = "row-1", source_id: Any = "doc-1") -> dict:
    """One rejoinder row and its paragraph response granted under one receipt."""
    source = {"_id": source_id, "organization_id": ORG, "project_id": PROJECT, "sha256": "abc", "current_version_id": "v1"}
    await db.documents.insert_one(source)
    response = {"_id": "resp-1", "draft_id": "draft-1", "response_revision": 0}
    row = {
        "_id": row_id,
        "case_id": CASE,
        "draft_id": "draft-1",
        "organization_id": ORG,
        "project_id": PROJECT,
        "source_paragraph_response_id": "resp-1",
        "source_response_revision": 0,
        "source_sod_para": "20",
        "claimant_reply": "New matter: the access log shows late handover.",
        "new_matter": True,
        "permission_required": True,
        "edit_revision": 1,
    }
    receipt = {
        "_id": str(uuid.uuid4()),
        "gate": PERMISSION_RECEIPT_GATE,
        "decision": "approved",
        "receipt_status": receipt_status,
        "organization_id": ORG,
        "project_id": PROJECT,
        "permission_source_id": str(source_id),
        "approver_id": "legal-9",
        "approved_at": datetime.utcnow() - age,
        **permission_receipt_binding(row, source),
    }
    grant = {"permission_obtained": True, "permission_source_id": str(source_id), "permission_approval_receipt_id": receipt["_id"]}
    await db.arbitration_workflow_approvals.insert_one(receipt)
    await db.arbitration_paragraph_responses.insert_one({**response, **grant})
    await db.arbitration_rejoinder_matrix.insert_one({**row, **grant})
    return receipt


STALE = STALE_PENDING_RECEIPT_AGE + timedelta(minutes=1)


async def _row(db: Any, row_id: Any = "row-1") -> dict:
    return await db.arbitration_rejoinder_matrix.find_one({"_id": row_id})


async def _state(db: Any, row_id: Any = "row-1") -> str:
    row = await _row(db, row_id)
    return (await rejoinder_permission_states(db, [row]))[str(row["_id"])]


def test_a_stale_pending_receipt_is_aborted_and_its_grants_withdrawn() -> None:
    async def body(db: Any) -> None:
        receipt = await _seed(db, receipt_status="pending", age=STALE)
        assert await _state(db) == RECEIPT_NOT_COMMITTED

        report = await reconcile_stale_permission_receipts(db, case_id=CASE)

        assert report["aborted"] == [receipt["_id"]]
        stored = await db.arbitration_workflow_approvals.find_one({"_id": receipt["_id"]})
        assert stored["receipt_status"] == "aborted" and stored["approver_id"] == "legal-9"
        for collection in (db.arbitration_rejoinder_matrix, db.arbitration_paragraph_responses):
            record = await collection.find_one({"permission_approval_receipt_id": receipt["_id"]})
            assert record is None
        assert (await _row(db))["permission_obtained"] is False

    _with_db(body)


@pytest.mark.parametrize("status,age", [("pending", timedelta(minutes=1)), ("committed", STALE)])
def test_fresh_and_committed_receipts_are_left_alone(status: str, age: timedelta) -> None:
    async def body(db: Any) -> None:
        receipt = await _seed(db, receipt_status=status, age=age)

        report = await reconcile_stale_permission_receipts(db, case_id=CASE)

        assert report == {"aborted": [], "withdrawn_from": [], "errors": []}
        stored = await db.arbitration_workflow_approvals.find_one({"_id": receipt["_id"]})
        assert stored["receipt_status"] == status
        assert (await _row(db))["permission_obtained"] is True

    _with_db(body)


def test_commit_and_abort_raced_on_one_receipt_have_exactly_one_winner() -> None:
    async def body(db: Any) -> None:
        for attempt in range(25):
            await db.arbitration_workflow_approvals.delete_many({})
            await db.arbitration_rejoinder_matrix.delete_many({})
            await db.arbitration_paragraph_responses.delete_many({})
            await db.documents.delete_many({})
            receipt = await _seed(db, receipt_status="pending", age=STALE)

            committed, report = await asyncio.gather(
                commit_permission_receipt(db, receipt["_id"]),
                reconcile_stale_permission_receipts(db, case_id=CASE),
            )

            aborted = receipt["_id"] in report["aborted"]
            assert committed != aborted, f"attempt {attempt}: committed={committed} aborted={aborted}"
            stored = await db.arbitration_workflow_approvals.find_one({"_id": receipt["_id"]})
            assert stored["receipt_status"] == ("committed" if committed else "aborted")
            if committed:
                assert await _state(db) == VERIFIED
            else:
                # The losing decision withdraws its own grant; the next run sweeps anything left.
                await reconcile_stale_permission_receipts(db, case_id=CASE)
                assert (await _row(db))["permission_obtained"] is False

    _with_db(body)


def test_a_newer_grant_under_another_receipt_is_never_withdrawn() -> None:
    async def body(db: Any) -> None:
        old = await _seed(db, receipt_status="pending", age=STALE)
        new = {**old, "_id": str(uuid.uuid4()), "receipt_status": "committed", "approved_at": datetime.utcnow()}
        await db.arbitration_workflow_approvals.insert_one(new)
        for collection in (db.arbitration_rejoinder_matrix, db.arbitration_paragraph_responses):
            await collection.update_many({}, {"$set": {"permission_approval_receipt_id": new["_id"]}})

        report = await reconcile_stale_permission_receipts(db, case_id=CASE)

        assert report["aborted"] == [old["_id"]] and report["withdrawn_from"] == []
        assert (await _row(db))["permission_approval_receipt_id"] == new["_id"]
        assert await _state(db) == VERIFIED

    _with_db(body)


def test_a_withdrawal_that_failed_after_the_abort_is_finished_by_the_next_run() -> None:
    async def body(db: Any) -> None:
        receipt = await _seed(db, receipt_status="pending", age=STALE)
        # The abort landed; the withdrawal never ran.
        await db.arbitration_workflow_approvals.update_one(
            {"_id": receipt["_id"]}, {"$set": {"receipt_status": "aborted", "grants_withdrawn": False}}
        )
        assert (await _row(db))["permission_obtained"] is True

        report = await reconcile_stale_permission_receipts(db, case_id=CASE)

        assert report["withdrawn_from"] == [receipt["_id"]]
        assert (await _row(db))["permission_obtained"] is False

    _with_db(body)


def test_object_id_and_string_ids_verify_the_same() -> None:
    async def body(db: Any) -> None:
        source_id = ObjectId()
        receipt = await _seed(db, receipt_status="committed", age=timedelta(0), source_id=source_id)
        assert receipt["permission_source_id"] == str(source_id)

        assert await _state(db) == VERIFIED

    _with_db(body)


def test_a_commit_after_the_abort_is_refused() -> None:
    async def body(db: Any) -> None:
        receipt = await _seed(db, receipt_status="pending", age=STALE)

        report = await reconcile_stale_permission_receipts(db, case_id=CASE)
        committed = await commit_permission_receipt(db, receipt["_id"])

        assert report["aborted"] == [receipt["_id"]]
        assert committed is False
        stored = await db.arbitration_workflow_approvals.find_one({"_id": receipt["_id"]})
        assert stored["receipt_status"] == "aborted"
        assert (await _row(db))["permission_obtained"] is False

    _with_db(body)


class _CommitAfterCleanupReads:
    """The cleanup reads its `pending` candidates; the decision commits before
    the cleanup's conditional abort reaches the server."""

    def __init__(self, db: Any, receipt_id: str) -> None:
        self._db, self._receipt_id, self.committed = db, receipt_id, None

    def __getattr__(self, name: str) -> Any:
        if name != "arbitration_workflow_approvals":
            return getattr(self._db, name)
        outer = self
        approvals = self._db.arbitration_workflow_approvals

        class _Approvals:
            def __getattr__(self, attr: str) -> Any:
                return getattr(approvals, attr)

            def find(self, *args: Any, **kwargs: Any) -> Any:
                cursor = approvals.find(*args, **kwargs)

                class _Cursor:
                    async def to_list(self, length: Any = None) -> Any:
                        found = await cursor.to_list(length=length)
                        if outer.committed is None and found:
                            outer.committed = await commit_permission_receipt(outer._db, outer._receipt_id)
                        return found

                return _Cursor()

        return _Approvals()


def test_a_cleanup_that_read_pending_loses_to_a_commit_that_lands_first() -> None:
    async def body(db: Any) -> None:
        receipt = await _seed(db, receipt_status="pending", age=STALE)
        racing = _CommitAfterCleanupReads(db, receipt["_id"])

        report = await reconcile_stale_permission_receipts(racing, case_id=CASE)

        assert racing.committed is True
        assert report["aborted"] == [] and report["withdrawn_from"] == []
        stored = await db.arbitration_workflow_approvals.find_one({"_id": receipt["_id"]})
        assert stored["receipt_status"] == "committed"
        assert await _state(db) == VERIFIED

    _with_db(body)
