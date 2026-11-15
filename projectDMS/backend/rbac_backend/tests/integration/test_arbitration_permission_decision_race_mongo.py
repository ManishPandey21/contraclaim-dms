"""FU-1 on real MongoDB: two rejoinder permission decisions on one row.

Each decision writes its grant to the paragraph response, then to the matrix
row (compare-and-set on `edit_revision`), then commits its receipt. A grant does
not advance `response_revision`, so the response write is also conditional on
the grant the decision read with the row (or on no grant, when cleanup withdrew
that one meanwhile), and a failed decision's undo puts back the response grant
it overwrote instead of blanking it.

These tests force, step by step, the interleavings in which the two decisions'
writes land in different orders, run the real `record_rejoinder_permission`
against a disposable replica set, and check the receipts, both grants, and what
readiness and the generate/approve/export gate conclude. Before that fix, the
interleaved, stale and in-flight cases left a committed permission reading
`receipt_mismatch`: the losing decision's undo revoked the winner's response grant.

Opt-in: set ``ARBITRATION_PERMISSION_RECEIPTS_MONGODB_URI`` to a disposable replica set.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import os
import uuid
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from backend.rbac_backend.models.arbitration_drafting import ArbitrationRejoinderPermissionRequest
from backend.rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from backend.rbac_backend.services.arbitration_drafting.permission_receipts import (
    VERIFIED,
    rejoinder_permission_states,
)
from backend.rbac_backend.tests.test_arbitration_drafting import _FakeCollection, _FakeDb, _FakeUser
from backend.rbac_backend.tests.test_arbitration_matrix_approval_invalidation import (
    CASE,
    _rejoinder_awaiting_permission,
)

MONGODB_URI_ENV = "ARBITRATION_PERMISSION_RECEIPTS_MONGODB_URI"

pytestmark = pytest.mark.integration

FIRST = _FakeUser("legal-9", roles=["legal_reviewer"])
SECOND = _FakeUser("legal-8", roles=["legal_reviewer"])

_ACTOR: contextvars.ContextVar[str] = contextvars.ContextVar("actor", default="")


@pytest.fixture(autouse=True)
def _uri() -> None:
    if not os.getenv(MONGODB_URI_ENV):
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")


def _fixture() -> _FakeDb:
    """The shared rejoinder fixture: one row awaiting permission. Built outside any event loop."""
    fake = _FakeDb()
    fake.letters = _FakeCollection()
    _rejoinder_awaiting_permission(fake)
    return fake


async def _seed(db: Any, fake: _FakeDb) -> str:
    """Copy the fixture's collections to MongoDB."""
    for name, collection in vars(fake).items():
        if isinstance(collection, _FakeCollection) and collection.rows:
            await db[name].insert_many(copy.deepcopy(collection.rows))
    return fake.arbitration_rejoinder_matrix.rows[0]["_id"]


class _Steps:
    """Named points two decisions wait on, so their writes land in a fixed order."""

    def __init__(self) -> None:
        self._events: Dict[str, asyncio.Event] = {}

    def event(self, name: str) -> asyncio.Event:
        return self._events.setdefault(name, asyncio.Event())

    async def wait(self, name: str) -> None:
        await asyncio.wait_for(self.event(name).wait(), timeout=10)

    def done(self, name: str) -> None:
        self.event(name).set()


class _Gated:
    """A collection proxy that runs hooks around one method for one actor."""

    def __init__(self, inner: Any, hooks: Dict[tuple, Dict[str, Any]]) -> None:
        self._inner = inner
        self._hooks = hooks

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if name not in {"update_one", "find_one_and_update", "find_one"}:
            return target

        async def call(*args: Any, **kwargs: Any) -> Any:
            hook = self._hooks.get((name, _ACTOR.get()))
            granting = bool(hook) and (
                hook.get("any") or (name in {"update_one", "find_one_and_update"} and _is_grant(args, kwargs))
            )
            if granting and hook.get("before"):
                await hook["before"]()
            try:
                return await target(*args, **kwargs)
            finally:
                if granting and hook.get("after"):
                    hook["after"]()

        return call


def _is_grant(args: tuple, kwargs: dict) -> bool:
    update = args[1] if len(args) > 1 else kwargs.get("update") or {}
    return bool((update.get("$set") or {}).get("permission_obtained") is True)


class _GatedDb:
    def __init__(self, inner: Any, gated: Dict[str, Dict[tuple, Dict[str, Any]]]) -> None:
        self._inner = inner
        self._gated = gated

    def __getattr__(self, name: str) -> Any:
        collection = getattr(self._inner, name)
        return _Gated(collection, self._gated[name]) if name in self._gated else collection

    def __getitem__(self, name: str) -> Any:
        return self.__getattr__(name)


async def _decide(db: Any, row_id: str, user: Any, actor: str) -> Optional[int]:
    """Run one decision as `actor`; return its HTTP error status, or None on success."""
    _ACTOR.set(actor)
    payload = ArbitrationRejoinderPermissionRequest(permission_source_id="doc-1")
    try:
        await ArbitrationCaseWorkspaceService(db).record_rejoinder_permission(CASE, row_id, payload, user)
    except HTTPException as error:
        return error.status_code
    return None


async def _outcome(db: Any, row_id: str) -> Dict[str, Any]:
    row = await db.arbitration_rejoinder_matrix.find_one({"_id": row_id})
    response = await db.arbitration_paragraph_responses.find_one({"_id": row["source_paragraph_response_id"]})
    receipts = await db.arbitration_workflow_approvals.find({"gate": "rejoinder_permission"}).to_list(None)
    by_approver = {receipt["approver_id"]: receipt for receipt in receipts}
    state = (await rejoinder_permission_states(db, [row]))[str(row["_id"])]
    service = ArbitrationCaseWorkspaceService(db)
    readiness = await service.readiness(CASE, draft_type="rejoinder", persist=False)
    readiness_blocks = any(check["check_key"] == "rejoinder_new_matter" for check in readiness["blockers"])
    # The gate generate/approve/export reuse. The fixture has no readiness approval,
    # so it always refuses; what matters is whether it refuses for the permission.
    try:
        await service.require_readiness_approval(await service.get_case(CASE), {"_id": "draft-1", "draft_type": "rejoinder"})
        export_gate_permission_state = None
    except HTTPException as refused:
        detail = refused.detail
        export_gate_permission_state = detail.get("permission_state") if isinstance(detail, dict) else None
    return {
        "readiness_blocks_new_matter": readiness_blocks,
        "export_gate_permission_state": export_gate_permission_state,
        "receipts": {approver: receipt["receipt_status"] for approver, receipt in by_approver.items()},
        "row_cites": _who(by_approver, row.get("permission_approval_receipt_id")),
        "response_cites": _who(by_approver, response.get("permission_approval_receipt_id")),
        "row_permission_obtained": row.get("permission_obtained"),
        "response_permission_obtained": response.get("permission_obtained"),
        "state": state,
    }


def _who(by_approver: Dict[str, dict], receipt_id: Any) -> Optional[str]:
    if receipt_id is None:
        return None
    for approver, receipt in by_approver.items():
        if receipt["_id"] == receipt_id:
            return approver
    return f"unknown:{receipt_id}"


def _run(script: Any) -> Dict[str, Any]:
    fake = _fixture()

    async def _main() -> Dict[str, Any]:
        client = AsyncIOMotorClient(os.environ[MONGODB_URI_ENV], serverSelectionTimeoutMS=5_000)
        name = f"fu1_race_{uuid.uuid4().hex[:12]}"
        try:
            raw = client[name]
            row_id = await _seed(raw, fake)
            result = await script(raw, row_id)
            result["outcome"] = await _outcome(raw, row_id)
            return result
        finally:
            await client.drop_database(name)
            client.close()

    return asyncio.run(_main())


def _assert_first_decision_stands(result: Dict[str, Any]) -> None:
    """The first decision is committed and verified; the second was refused and left no grant."""
    outcome = result["outcome"]
    assert result["first_error"] is None
    assert result["second_error"] == 409
    assert outcome["receipts"] == {"legal-9": "committed", "legal-8": "aborted"}
    assert outcome["row_cites"] == outcome["response_cites"] == "legal-9"
    assert outcome["row_permission_obtained"] is outcome["response_permission_obtained"] is True
    assert outcome["state"] == VERIFIED
    assert outcome["readiness_blocks_new_matter"] is False
    assert outcome["export_gate_permission_state"] is None


def test_interleaved_decisions_the_second_response_write_loses_the_row_write() -> None:
    """A writes the response, B overwrites it, A wins the row, B loses and undoes."""

    async def script(raw: Any, row_id: str) -> Dict[str, Any]:
        steps = _Steps()
        db = _GatedDb(
            raw,
            {
                "arbitration_paragraph_responses": {
                    ("update_one", "B"): {"before": lambda: steps.wait("a_response"), "after": lambda: steps.done("b_response")},
                    ("update_one", "A"): {"after": lambda: steps.done("a_response")},
                },
                "arbitration_rejoinder_matrix": {
                    ("find_one_and_update", "A"): {"before": lambda: steps.wait("b_response"), "after": lambda: steps.done("a_row")},
                    ("find_one_and_update", "B"): {"before": lambda: steps.wait("a_row")},
                },
            },
        )
        first, second = await asyncio.gather(_decide(db, row_id, FIRST, "A"), _decide(db, row_id, SECOND, "B"))
        return {"first_error": first, "second_error": second}

    # B is refused at the response write: the response no longer carries the
    # grant B read with the row.
    _assert_first_decision_stands(_run(script))


def test_a_stale_decision_after_a_newer_one_committed_cannot_undo_it() -> None:
    """B reads the row, A then completes and commits, then B writes and loses the row."""

    async def script(raw: Any, row_id: str) -> Dict[str, Any]:
        steps = _Steps()
        db = _GatedDb(
            raw,
            {
                "arbitration_paragraph_responses": {
                    # A writes nothing until B's decision has read the row.
                    ("update_one", "A"): {"before": lambda: steps.wait("b_read")},
                    # B writes nothing until A has committed.
                    ("update_one", "B"): {"before": lambda: steps.wait("a_done")},
                },
                "arbitration_rejoinder_matrix": {
                    ("find_one", "B"): {"any": True, "after": lambda: steps.done("b_read")},
                },
            },
        )

        async def _decide_a() -> Optional[int]:
            try:
                return await _decide(db, row_id, FIRST, "A")
            finally:
                steps.done("a_done")

        async def _decide_b() -> Optional[int]:
            return await _decide(db, row_id, SECOND, "B")

        first, second = await asyncio.gather(_decide_a(), _decide_b())
        return {"first_error": first, "second_error": second}

    # B read the row before A's grant existed, so its response write is refused.
    _assert_first_decision_stands(_run(script))


def test_decisions_whose_writes_land_in_the_same_order_leave_the_winner_verified() -> None:
    """Control: B writes the response second and also wins the row; A loses cleanly."""

    async def script(raw: Any, row_id: str) -> Dict[str, Any]:
        steps = _Steps()
        db = _GatedDb(
            raw,
            {
                "arbitration_paragraph_responses": {
                    ("update_one", "A"): {"after": lambda: steps.done("a_response")},
                    ("update_one", "B"): {"before": lambda: steps.wait("a_response"), "after": lambda: steps.done("b_response")},
                },
                "arbitration_rejoinder_matrix": {
                    ("find_one_and_update", "A"): {"before": lambda: steps.wait("b_row")},
                },
            },
        )

        async def _decide_b() -> Optional[int]:
            try:
                return await _decide(db, row_id, SECOND, "B")
            finally:
                # B has written its row, or been refused before reaching it.
                steps.done("b_row")

        first, second = await asyncio.gather(_decide(db, row_id, FIRST, "A"), _decide_b())
        return {"first_error": first, "second_error": second}

    result = _run(script)
    outcome = result["outcome"]
    # Whichever decision ends committed, the row and response agree and it is verified.
    committed = [who for who, status in outcome["receipts"].items() if status == "committed"]
    assert len(committed) == 1
    assert outcome["row_cites"] == outcome["response_cites"] == committed[0]
    assert outcome["state"] == VERIFIED


def test_a_decision_that_starts_while_another_is_in_flight_cannot_undo_it() -> None:
    """B reads the row and response after A wrote the response, before A wrote the row."""

    async def script(raw: Any, row_id: str) -> Dict[str, Any]:
        steps = _Steps()
        db = _GatedDb(
            raw,
            {
                "arbitration_paragraph_responses": {
                    ("update_one", "A"): {"after": lambda: steps.done("a_response")},
                    ("update_one", "B"): {"after": lambda: steps.done("b_response")},
                },
                "arbitration_rejoinder_matrix": {
                    # A's row write waits until B has written (or been refused at) the response.
                    ("find_one_and_update", "A"): {"before": lambda: steps.wait("b_response"), "after": lambda: steps.done("a_row")},
                    ("find_one_and_update", "B"): {"before": lambda: steps.wait("a_row")},
                },
            },
        )

        async def _decide_b() -> Optional[int]:
            await steps.wait("a_response")
            try:
                return await _decide(db, row_id, SECOND, "B")
            finally:
                steps.done("b_response")

        first, second = await asyncio.gather(_decide(db, row_id, FIRST, "A"), _decide_b())
        return {"first_error": first, "second_error": second}

    # B read A's in-flight grant, overwrote it and lost the row write; its undo
    # puts A's grant back instead of blanking the response.
    _assert_first_decision_stands(_run(script))
