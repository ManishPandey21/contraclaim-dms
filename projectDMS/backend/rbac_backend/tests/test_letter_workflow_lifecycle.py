"""Regression tests for the letter lifecycle hardening (request -> approval).

Covers the audit fixes:
- change_status records the acting user_id (not a CurrentUser object) and never
  writes a non-string comment into Mongo.
- Opt-in workflow transition validation rejects invalid jumps and tolerates
  unrecognized legacy statuses.
- Separation-of-duties blocks a drafter/creator from approving their own letter.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId

from backend.rbac_backend.services.letter_service import (
    InvalidLetterTransitionError,
    LetterService,
)

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _LettersCollection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None):
        oid = query.get("_id")
        for doc in self.docs:
            if doc.get("_id") == oid:
                return dict(doc)
        return None

    async def update_one(self, query: Dict[str, Any], ops: Dict[str, Any]):
        oid = query.get("_id")

        class _Result:
            matched_count = 0

        result = _Result()
        for doc in self.docs:
            if doc.get("_id") != oid:
                continue
            result.matched_count = 1
            for key, value in (ops.get("$set") or {}).items():
                doc[key] = value
            for key, value in (ops.get("$push") or {}).items():
                doc.setdefault(key, []).append(value)
            break
        return result


class _DB:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.letters = _LettersCollection(docs)


class _Service(LetterService):
    """LetterService over a fake DB; post-update fetch/notification stubbed out."""

    async def get_letter(self, letter_id: str):  # type: ignore[override]
        return None

    async def _emit_letter_event(self, *args, **kwargs):  # type: ignore[override]
        return None


def _service_with(status: str):
    oid = ObjectId()
    docs = [{"_id": oid, "status": status, "status_history": []}]
    return _Service(_DB(docs)), str(oid), docs[0]


async def test_change_status_records_actor_id_and_clean_history():
    service, letter_id, doc = _service_with("Review")

    ok = await service.change_status(letter_id, "Approval", user_id="approver-1")

    assert ok is True
    assert doc["status"] == "Approval"
    entry = doc["status_history"][-1]
    assert entry["actor_id"] == "approver-1"
    assert "comment" not in entry  # no comment passed -> none stored


async def test_change_status_coerces_non_string_comment():
    """A CurrentUser/object passed as comment must never be written to Mongo."""
    service, letter_id, doc = _service_with("Review")
    fake_user = SimpleNamespace(id="u1", email="u1@example.com")

    ok = await service.change_status(letter_id, "Approval", comment=fake_user, user_id="approver-1")

    assert ok is True
    entry = doc["status_history"][-1]
    assert "comment" not in entry  # object coerced away, not persisted
    # No CurrentUser-like object leaked into the comments feed either.
    for comment in doc.get("comments", []):
        assert isinstance(comment, dict)


async def test_change_status_rejects_invalid_transition():
    service, letter_id, _ = _service_with("Draft")

    with pytest.raises(InvalidLetterTransitionError):
        await service.change_status(letter_id, "Completed", validate_transition=True)


async def test_change_status_allows_valid_transition():
    service, letter_id, doc = _service_with("Review")

    ok = await service.change_status(letter_id, "Approval", validate_transition=True)

    assert ok is True
    assert doc["status"] == "Approval"


async def test_change_status_tolerates_unknown_legacy_status():
    """Unrecognized stored status cannot be validated -> change is allowed."""
    service, letter_id, doc = _service_with("Under Review")

    ok = await service.change_status(letter_id, "Approval", validate_transition=True)

    assert ok is True
    assert doc["status"] == "Approval"


# --- Separation of duties -------------------------------------------------

try:
    from fastapi import HTTPException

    from rbac_backend.routers.letters import (
        _enforce_letter_separation_of_duties,
        _enforce_letter_transition,
    )

    _SOD_IMPORT_OK = True
except Exception:  # pragma: no cover - app import unavailable
    _SOD_IMPORT_OK = False

sod = pytest.mark.skipif(not _SOD_IMPORT_OK, reason="letters router unavailable")


@sod
def test_sod_blocks_creator_self_approval():
    letter = {"created_by": "user-1", "assigned_to": "drafter-9"}
    actor = SimpleNamespace(id="user-1")
    with pytest.raises(HTTPException) as exc:
        _enforce_letter_separation_of_duties(letter, actor)
    assert exc.value.status_code == 403


@sod
def test_sod_blocks_drafter_self_approval():
    letter = {"created_by": "user-1", "assigned_to": "drafter-9"}
    actor = SimpleNamespace(id="drafter-9")
    with pytest.raises(HTTPException) as exc:
        _enforce_letter_separation_of_duties(letter, actor)
    assert exc.value.status_code == 403


@sod
def test_sod_allows_independent_approver():
    letter = {"created_by": "user-1", "assigned_to": "drafter-9"}
    actor = SimpleNamespace(id="approver-42")
    _enforce_letter_separation_of_duties(letter, actor)  # must not raise


@sod
def test_transition_guard_rejects_invalid_jump_with_409():
    letter = {"status": "Draft"}
    with pytest.raises(HTTPException) as exc:
        _enforce_letter_transition(letter, "Completed")
    assert exc.value.status_code == 409


@sod
def test_transition_guard_allows_valid_jump():
    _enforce_letter_transition({"status": "Review"}, "Approval")  # must not raise


@sod
def test_transition_guard_tolerates_unknown_status():
    _enforce_letter_transition({"status": "Under Review"}, "Approval")  # must not raise
