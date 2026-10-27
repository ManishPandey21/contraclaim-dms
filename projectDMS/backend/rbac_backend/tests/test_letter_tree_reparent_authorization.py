"""The letter conversation tree and reparent routes must authorise.

`GET /api/letters/{id}/tree` and `POST /api/letters/{id}/reparent` once called
`ConversationService` directly. That service loads letters by id and walks a
conversation by association alone, so any authenticated caller could read
another tenant's thread or rewrite its threading.

These tests drive the real router, `LetterController`, `AuthorizationService`,
`PolicyService`, `ScopeService` and `ConversationService` over an in-memory
Mongo boundary. Only the permission-grant and subscription-entitlement seams are
patched (as in `test_cl4a_documents_contracts_scope.py`), so scope and record
checks run for real.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI

from rbac_backend.core.database import get_database as _original_get_database
from rbac_backend.core.database import get_db
from rbac_backend.core.errors import register_domain_error_handler
from rbac_backend.core.security import get_current_user
from rbac_backend.routers.letters import LetterController, get_letter_controller
from rbac_backend.routers.letters import router as letters_router
from rbac_backend.services.authorization_service import AuthorizationService
from rbac_backend.services.conversation_service import ConversationService
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.letter_service import LetterService
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.tests.test_hindrance_register_http import _Database, _principal
from rbac_backend.utils.rate_limiter import RateLimiter

ORG_A, ORG_B = "org-A", "org-B"
A1, A2, B1 = "proj-A1", "proj-A2", "proj-B1"

ROOT_A1 = str(ObjectId())
CHILD_A1 = str(ObjectId())
GRAND_A1 = str(ObjectId())
SIBLING_A2 = str(ObjectId())  # same conversation, sibling project
FOREIGN_B1 = str(ObjectId())  # same conversation, foreign organisation
ROOT_B1 = str(ObjectId())  # an unrelated thread in organisation B
BRANCH_A1 = str(ObjectId())  # added by one test only
REPLY_A2 = str(ObjectId())  # added by one test only: a reply filed in the sibling project

MARKER = {
    BRANCH_A1: "BRANCH-A1-MARKER",
    REPLY_A2: "REPLY-A2-MARKER",
    ROOT_A1: "ROOT-A1-MARKER",
    CHILD_A1: "CHILD-A1-MARKER",
    GRAND_A1: "GRAND-A1-MARKER",
    SIBLING_A2: "SIBLING-A2-MARKER",
    FOREIGN_B1: "FOREIGN-B1-MARKER",
    ROOT_B1: "ROOT-B1-MARKER",
}

VIEW_EDIT = {"dms.document.view", "dms.document.edit_metadata"}

PROJECT_A1_EDITOR = _principal("u-editor-a1", ["projectuser"], ORG_A, [A1])
PROJECT_A1_READER = _principal("u-reader-a1", ["projectuser"], ORG_A, [A1])
ORG_B_ADMIN = _principal("u-orgadmin-b", ["orgadmin"], ORG_B)
ORG_A_ADMIN = _principal("u-orgadmin-a", ["orgadmin"], ORG_A)
DRAFTER_A1 = _principal("u-drafter-a1", ["projectuser", "contract_letter_drafter"], ORG_A, [A1])
SUPERADMIN = _principal("u-root", ["superadmin"], None)

GRANTS: dict[str, set[str]] = {
    PROJECT_A1_EDITOR.id: VIEW_EDIT,
    PROJECT_A1_READER.id: {"dms.document.view"},
    ORG_B_ADMIN.id: VIEW_EDIT,
    ORG_A_ADMIN.id: VIEW_EDIT,
    DRAFTER_A1.id: {"dms.document.view"},
}


@pytest.fixture(autouse=True)
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(self, user_id: str, permission_name: str, **_kwargs: Any) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **_kwargs: Any):
        return True, "ok"

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


def _letter(
    letter_id: str,
    org: str,
    project: str,
    *,
    previous: str | None,
    ancestors: list[str],
    conversation: str = ROOT_A1,
) -> dict[str, Any]:
    return {
        "_id": ObjectId(letter_id),
        "title": f"{MARKER[letter_id]} title",
        "recipient": "recipient",
        "subject": f"{MARKER[letter_id]} subject",
        "content": f"{MARKER[letter_id]} body",
        "status": "Draft",
        "created_by": "seed",
        "assigned_to": "seed",
        "organization_id": org,
        "project_id": project,
        "conversation_id": conversation,
        "previous_letter_id": previous,
        "ancestors": ancestors,
        "depth": len(ancestors),
        "created_at": datetime(2026, 9, 1),
        "updated_at": datetime(2026, 9, 1),
    }


def _seeded() -> _Database:
    db = _Database()
    db.projects.docs.extend(
        [
            {"_id": A1, "organization_id": ORG_A, "name": "A1", "is_active": True},
            {"_id": A2, "organization_id": ORG_A, "name": "A2", "is_active": True},
            {"_id": B1, "organization_id": ORG_B, "name": "B1", "is_active": True},
        ]
    )
    db.letters.docs.extend(
        [
            _letter(ROOT_A1, ORG_A, A1, previous=None, ancestors=[]),
            _letter(CHILD_A1, ORG_A, A1, previous=ROOT_A1, ancestors=[ROOT_A1]),
            _letter(GRAND_A1, ORG_A, A1, previous=CHILD_A1, ancestors=[ROOT_A1, CHILD_A1]),
            _letter(SIBLING_A2, ORG_A, A2, previous=ROOT_A1, ancestors=[ROOT_A1]),
            _letter(FOREIGN_B1, ORG_B, B1, previous=CHILD_A1, ancestors=[ROOT_A1, CHILD_A1]),
            _letter(ROOT_B1, ORG_B, B1, previous=None, ancestors=[], conversation=ROOT_B1),
        ]
    )
    return db


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _Database:
    database = _seeded()

    async def _get_database() -> _Database:
        return database

    for name, module in list(sys.modules.items()):
        if "rbac_backend" in name and getattr(module, "get_database", None) is _original_get_database:
            monkeypatch.setattr(module, "get_database", _get_database)
    return database


def _app(db: _Database, user: Any) -> FastAPI:
    app = FastAPI()
    register_domain_error_handler(app)
    app.include_router(letters_router, prefix="/api")

    def _controller() -> LetterController:
        letter_service = LetterService(db)
        conversation_service = ConversationService(letter_service)
        conversation_service._db = db
        return LetterController(
            letter_service,
            conversation_service,
            AuthorizationService(),
            RateLimiter(max_requests=1000, window_seconds=3600, scope="letters-test"),
        )

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[_original_get_database] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_letter_controller] = _controller
    return app


@asynccontextmanager
async def _as(db: _Database, user: Any):
    transport = httpx.ASGITransport(app=_app(db, user))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _snapshot(db: _Database) -> dict[str, tuple[Any, Any, Any]]:
    return {
        str(row["_id"]): (row.get("previous_letter_id"), list(row.get("ancestors") or []), row.get("depth"))
        for row in db.letters.docs
    }


def _tree_ids(node: dict[str, Any]) -> set[str]:
    letter = node.get("letter") or {}
    ids = {str(letter.get("_id") or letter.get("id"))}
    for child in node.get("children") or []:
        ids |= _tree_ids(child)
    return ids


def _leaks(text: str, *letter_ids: str) -> list[str]:
    return [MARKER[letter_id] for letter_id in letter_ids if MARKER[letter_id] in text]


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------


async def test_a_foreign_tenant_cannot_read_a_thread(db: _Database) -> None:
    async with _as(db, ORG_B_ADMIN) as client:
        response = await client.get(f"/api/letters/{ROOT_A1}/tree")
    assert response.status_code in (403, 404), response.text
    assert not _leaks(response.text, ROOT_A1, CHILD_A1, GRAND_A1, SIBLING_A2)


async def test_the_tree_contains_only_letters_the_caller_may_read(db: _Database) -> None:
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.get(f"/api/letters/{ROOT_A1}/tree")
    assert response.status_code == 200, response.text
    assert _tree_ids(response.json()) == {ROOT_A1, CHILD_A1, GRAND_A1}
    assert not _leaks(response.text, SIBLING_A2, FOREIGN_B1)


async def test_the_tree_from_a_mid_thread_letter_returns_the_readable_thread(db: _Database) -> None:
    async with _as(db, PROJECT_A1_READER) as client:
        response = await client.get(f"/api/letters/{GRAND_A1}/tree")
    assert response.status_code == 200, response.text
    assert _tree_ids(response.json()) == {ROOT_A1, CHILD_A1, GRAND_A1}


async def test_superadmin_sees_the_full_thread_within_the_anchor_scope(db: _Database) -> None:
    # As in `get_authorized_conversation_chain`, the anchor's organisation and
    # project narrow the view for every actor, Super Admin included.
    async with _as(db, SUPERADMIN) as client:
        response = await client.get(f"/api/letters/{ROOT_A1}/tree")
    assert response.status_code == 200, response.text
    assert _tree_ids(response.json()) == {ROOT_A1, CHILD_A1, GRAND_A1}


async def test_a_hidden_letter_is_pruned_and_its_visible_child_reattached(db: _Database) -> None:
    # Move CHILD_A1 into the sibling project: GRAND_A1 must re-attach to ROOT_A1.
    for row in db.letters.docs:
        if str(row["_id"]) == CHILD_A1:
            row["project_id"] = A2
    async with _as(db, PROJECT_A1_READER) as client:
        response = await client.get(f"/api/letters/{GRAND_A1}/tree")
    assert response.status_code == 200, response.text
    tree = response.json()
    assert str(tree["letter"]["_id"]) == ROOT_A1
    assert [str(child["letter"]["_id"]) for child in tree["children"]] == [GRAND_A1]
    assert not _leaks(response.text, CHILD_A1, SIBLING_A2, FOREIGN_B1)


# ---------------------------------------------------------------------------
# Chain: the adjacent read of the same letter family
# ---------------------------------------------------------------------------


def _chain_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("_id") or row.get("id")) for row in rows}


async def test_a_foreign_tenant_cannot_read_a_chain(db: _Database) -> None:
    async with _as(db, ORG_B_ADMIN) as client:
        response = await client.get(f"/api/letters/{ROOT_A1}/chain")
    assert response.status_code in (403, 404), response.text
    assert not _leaks(response.text, ROOT_A1, CHILD_A1, GRAND_A1, SIBLING_A2)


async def test_the_chain_contains_only_letters_the_caller_may_read(db: _Database) -> None:
    # Anchored mid-thread: the generic family loader walks `previous_letter_id`
    # upward by id, so this chain does not depend on its child lookup.
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.get(f"/api/letters/{GRAND_A1}/chain")
    assert response.status_code == 200, response.text
    assert _chain_ids(response.json()) == {ROOT_A1, CHILD_A1, GRAND_A1}
    assert not _leaks(response.text, SIBLING_A2, FOREIGN_B1)


async def test_the_chain_from_a_foreign_member_does_not_reach_back_into_the_thread(db: _Database) -> None:
    # ORG_B_ADMIN may read FOREIGN_B1, which sits under CHILD_A1 in an org-A thread.
    async with _as(db, ORG_B_ADMIN) as client:
        response = await client.get(f"/api/letters/{FOREIGN_B1}/chain")
    assert response.status_code == 200, response.text
    assert _chain_ids(response.json()) == {FOREIGN_B1}
    assert not _leaks(response.text, ROOT_A1, CHILD_A1, GRAND_A1, SIBLING_A2)


async def test_superadmin_chain_is_bounded_by_the_anchor_scope(db: _Database) -> None:
    async with _as(db, SUPERADMIN) as client:
        response = await client.get(f"/api/letters/{GRAND_A1}/chain")
    assert response.status_code == 200, response.text
    assert _chain_ids(response.json()) == {ROOT_A1, CHILD_A1, GRAND_A1}


async def test_the_tree_from_a_foreign_member_does_not_reach_back_into_the_thread(db: _Database) -> None:
    async with _as(db, ORG_B_ADMIN) as client:
        response = await client.get(f"/api/letters/{FOREIGN_B1}/tree")
    assert response.status_code == 200, response.text
    assert _tree_ids(response.json()) == {FOREIGN_B1}
    assert not _leaks(response.text, ROOT_A1, CHILD_A1, GRAND_A1, SIBLING_A2)


# ---------------------------------------------------------------------------
# Reparent: refusals leave every letter untouched
# ---------------------------------------------------------------------------


async def test_a_foreign_tenant_cannot_reparent_a_letter(db: _Database) -> None:
    before = _snapshot(db)
    async with _as(db, ORG_B_ADMIN) as client:
        response = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": ROOT_A1})
    assert response.status_code in (403, 404), response.text
    assert _snapshot(db) == before
    assert not _leaks(response.text, ROOT_A1, GRAND_A1)


async def test_a_letter_cannot_be_moved_under_an_inaccessible_parent(db: _Database) -> None:
    before = _snapshot(db)
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": FOREIGN_B1})
    assert response.status_code in (403, 404), response.text
    assert _snapshot(db) == before
    assert not _leaks(response.text, FOREIGN_B1)


async def test_a_reader_without_edit_permission_cannot_reparent(db: _Database) -> None:
    before = _snapshot(db)
    async with _as(db, PROJECT_A1_READER) as client:
        response = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": ROOT_A1})
    assert response.status_code == 403, response.text
    assert _snapshot(db) == before


async def test_a_reparent_that_would_rewrite_an_inaccessible_descendant_is_refused(db: _Database) -> None:
    # CHILD_A1's descendants include FOREIGN_B1, whose ancestors the move would rewrite.
    before = _snapshot(db)
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{CHILD_A1}/reparent", json={"new_parent_id": ROOT_A1})
    assert response.status_code == 409, response.text
    assert _snapshot(db) == before
    assert not _leaks(response.text, FOREIGN_B1)


async def test_a_cycle_is_refused_before_any_write(db: _Database) -> None:
    before = _snapshot(db)
    async with _as(db, SUPERADMIN) as client:
        response = await client.post(f"/api/letters/{ROOT_A1}/reparent", json={"new_parent_id": GRAND_A1})
    assert response.status_code == 400, response.text
    assert _snapshot(db) == before


async def test_a_letter_cannot_become_its_own_parent(db: _Database) -> None:
    before = _snapshot(db)
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": GRAND_A1})
    assert response.status_code == 400, response.text
    assert _snapshot(db) == before


async def test_an_unknown_parent_is_not_found_and_nothing_changes(db: _Database) -> None:
    before = _snapshot(db)
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": str(ObjectId())})
    assert response.status_code == 404, response.text
    assert _snapshot(db) == before


# ---------------------------------------------------------------------------
# Reparent: an authorised move still works
# ---------------------------------------------------------------------------


async def test_an_authorised_editor_can_move_a_letter_up_to_its_grandparent(db: _Database) -> None:
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": ROOT_A1})
    assert response.status_code == 200, response.text
    moved = _snapshot(db)[GRAND_A1]
    assert moved == (ROOT_A1, [ROOT_A1], 1)


async def test_an_authorised_move_rewrites_same_scope_descendants(db: _Database) -> None:
    # Detach FOREIGN_B1 so CHILD_A1's only descendant is GRAND_A1 (same project),
    # and add a second branch under the root to move CHILD_A1 onto.
    db.letters.docs[:] = [row for row in db.letters.docs if str(row["_id"]) != FOREIGN_B1]
    db.letters.docs.append(_letter(BRANCH_A1, ORG_A, A1, previous=ROOT_A1, ancestors=[ROOT_A1]))
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{CHILD_A1}/reparent", json={"new_parent_id": BRANCH_A1})
    assert response.status_code == 200, response.text
    after = _snapshot(db)
    assert after[CHILD_A1] == (BRANCH_A1, [ROOT_A1, BRANCH_A1], 2)
    assert after[GRAND_A1] == (CHILD_A1, [ROOT_A1, BRANCH_A1, CHILD_A1], 3)


# ---------------------------------------------------------------------------
# Reparent: the rewrite covers exactly the descendants that were authorised
# ---------------------------------------------------------------------------


def _without_foreign(db: _Database) -> None:
    db.letters.docs[:] = [row for row in db.letters.docs if str(row["_id"]) != FOREIGN_B1]
    db.letters.docs.append(_letter(BRANCH_A1, ORG_A, A1, previous=ROOT_A1, ancestors=[ROOT_A1]))


async def test_a_move_whose_descendant_sits_in_another_project_is_refused_before_any_write(db: _Database) -> None:
    # An org-wide editor may update every letter involved, but the move would
    # leave the A2 reply's threading half-rewritten. Refuse it whole.
    _without_foreign(db)
    db.letters.docs.append(_letter(REPLY_A2, ORG_A, A2, previous=CHILD_A1, ancestors=[ROOT_A1, CHILD_A1]))
    before = _snapshot(db)
    async with _as(db, ORG_A_ADMIN) as client:
        response = await client.post(f"/api/letters/{CHILD_A1}/reparent", json={"new_parent_id": BRANCH_A1})
    assert response.status_code == 409, response.text
    assert _snapshot(db) == before
    assert not _leaks(response.text, REPLY_A2)


async def test_descendants_whose_ancestry_is_stored_as_object_ids_are_rewritten(db: _Database) -> None:
    _without_foreign(db)
    for row in db.letters.docs:
        if str(row["_id"]) == GRAND_A1:
            row["ancestors"] = [ObjectId(ROOT_A1), ObjectId(CHILD_A1)]
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{CHILD_A1}/reparent", json={"new_parent_id": BRANCH_A1})
    assert response.status_code == 200, response.text
    assert _snapshot(db)[GRAND_A1] == (CHILD_A1, [ROOT_A1, BRANCH_A1, CHILD_A1], 3)


# ---------------------------------------------------------------------------
# Tree and chain: every member passes the same per-letter rule as the seed
# ---------------------------------------------------------------------------


def _assign(db: _Database, letter_id: str, user: Any) -> None:
    for row in db.letters.docs:
        if str(row["_id"]) == letter_id:
            row["assigned_to"] = user.id


@pytest.mark.parametrize("route", ["tree", "chain"])
async def test_a_drafter_sees_only_assigned_letters_of_the_thread(db: _Database, route: str) -> None:
    # The drafter may read GRAND_A1 (assigned to them); ROOT_A1 and CHILD_A1
    # are in the same project but assigned to someone else.
    _assign(db, GRAND_A1, DRAFTER_A1)
    async with _as(db, DRAFTER_A1) as client:
        response = await client.get(f"/api/letters/{GRAND_A1}/{route}")
    assert response.status_code == 200, response.text
    ids = _tree_ids(response.json()) if route == "tree" else _chain_ids(response.json())
    assert ids == {GRAND_A1}
    assert not _leaks(response.text, ROOT_A1, CHILD_A1, SIBLING_A2, FOREIGN_B1)


@pytest.mark.parametrize("route", ["tree", "chain"])
async def test_a_drafter_sees_every_assigned_letter_of_the_thread(db: _Database, route: str) -> None:
    for letter_id in (ROOT_A1, CHILD_A1, GRAND_A1):
        _assign(db, letter_id, DRAFTER_A1)
    async with _as(db, DRAFTER_A1) as client:
        response = await client.get(f"/api/letters/{GRAND_A1}/{route}")
    assert response.status_code == 200, response.text
    ids = _tree_ids(response.json()) if route == "tree" else _chain_ids(response.json())
    assert ids == {ROOT_A1, CHILD_A1, GRAND_A1}


# ---------------------------------------------------------------------------
# Tree and chain: no identifier of an unreturned letter appears in the response
# ---------------------------------------------------------------------------


def _letters(payload: Any, route: str) -> list[dict[str, Any]]:
    if route == "chain":
        return list(payload)
    out = [payload["letter"]]
    for child in payload.get("children") or []:
        out.extend(_letters(child, "tree"))
    return out


@pytest.mark.parametrize("route", ["tree", "chain"])
async def test_a_foreign_member_does_not_reveal_the_hidden_thread_ids(db: _Database, route: str) -> None:
    async with _as(db, ORG_B_ADMIN) as client:
        response = await client.get(f"/api/letters/{FOREIGN_B1}/{route}")
    assert response.status_code == 200, response.text
    for hidden in (ROOT_A1, CHILD_A1, GRAND_A1, SIBLING_A2):
        assert hidden not in response.text
    (letter,) = _letters(response.json(), route)
    assert letter.get("previous_letter_id") is None
    assert letter.get("ancestors") == []


async def test_a_reattached_member_reports_its_visible_parent_and_ancestry(db: _Database) -> None:
    for row in db.letters.docs:
        if str(row["_id"]) == CHILD_A1:
            row["project_id"] = A2
    async with _as(db, PROJECT_A1_READER) as client:
        response = await client.get(f"/api/letters/{GRAND_A1}/tree")
    assert response.status_code == 200, response.text
    assert CHILD_A1 not in response.text
    grand = next(item for item in _letters(response.json(), "tree") if str(item.get("_id") or item.get("id")) == GRAND_A1)
    assert grand["previous_letter_id"] == ROOT_A1
    assert grand["ancestors"] == [ROOT_A1]


async def test_visible_ancestry_is_returned_unchanged(db: _Database) -> None:
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.get(f"/api/letters/{GRAND_A1}/tree")
    grand = next(item for item in _letters(response.json(), "tree") if str(item.get("_id") or item.get("id")) == GRAND_A1)
    assert grand["previous_letter_id"] == CHILD_A1
    assert grand["ancestors"] == [ROOT_A1, CHILD_A1]
    assert grand["conversation_id"] == ROOT_A1


async def test_an_opaque_conversation_key_that_names_no_letter_is_kept(db: _Database) -> None:
    for row in db.letters.docs:
        if row.get("conversation_id") == ROOT_A1:
            row["conversation_id"] = "conversation-key-1"
    async with _as(db, ORG_B_ADMIN) as client:
        response = await client.get(f"/api/letters/{FOREIGN_B1}/chain")
    assert response.status_code == 200, response.text
    assert response.json()[0]["conversation_id"] == "conversation-key-1"


# ---------------------------------------------------------------------------
# Reparent: a denied or missing parent is indistinguishable; bad ids are not 500s
# ---------------------------------------------------------------------------


async def test_a_foreign_parent_and_a_missing_parent_get_the_same_answer(db: _Database) -> None:
    async with _as(db, PROJECT_A1_EDITOR) as client:
        foreign = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": ROOT_B1})
        missing = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": str(ObjectId())})
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json())
    assert foreign.status_code == 404


async def test_a_caller_who_cannot_edit_the_letter_learns_nothing_about_the_parent(db: _Database) -> None:
    async with _as(db, ORG_B_ADMIN) as client:
        existing = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": ROOT_A1})
        missing = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": str(ObjectId())})
    assert existing.status_code == missing.status_code
    assert existing.status_code in (403, 404)


@pytest.mark.parametrize("which", ["letter", "parent"])
async def test_a_malformed_id_is_not_a_server_error(db: _Database, which: str) -> None:
    before = _snapshot(db)
    letter_id, parent_id = (("not-an-id", ROOT_A1) if which == "letter" else (GRAND_A1, "not-an-id"))
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{letter_id}/reparent", json={"new_parent_id": parent_id})
    assert response.status_code == 404, response.text
    assert _snapshot(db) == before


# ---------------------------------------------------------------------------
# Tree and chain: an unreadable seed is indistinguishable from a missing one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["tree", "chain"])
async def test_a_foreign_seed_and_a_missing_seed_get_the_same_answer(db: _Database, route: str) -> None:
    async with _as(db, ORG_B_ADMIN) as client:
        foreign = await client.get(f"/api/letters/{ROOT_A1}/{route}")
        missing = await client.get(f"/api/letters/{ObjectId()}/{route}")
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json())
    assert foreign.status_code == 404


@pytest.mark.parametrize("route", ["tree", "chain"])
async def test_a_malformed_seed_id_is_not_a_server_error(db: _Database, route: str) -> None:
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.get(f"/api/letters/not-an-id/{route}")
    assert response.status_code == 404, response.text



# ---------------------------------------------------------------------------
# Reparent: every blocking dependent letter gets the same answer
# ---------------------------------------------------------------------------


async def test_a_hidden_and_a_visible_blocking_reply_are_indistinguishable(db: _Database) -> None:
    _without_foreign(db)
    db.letters.docs.append(_letter(REPLY_A2, ORG_A, A2, previous=CHILD_A1, ancestors=[ROOT_A1, CHILD_A1]))
    async with _as(db, ORG_A_ADMIN) as client:
        visible = await client.post(f"/api/letters/{CHILD_A1}/reparent", json={"new_parent_id": BRANCH_A1})
    async with _as(db, PROJECT_A1_EDITOR) as client:
        hidden = await client.post(f"/api/letters/{CHILD_A1}/reparent", json={"new_parent_id": BRANCH_A1})
    assert (hidden.status_code, hidden.json()) == (visible.status_code, visible.json())
    assert hidden.status_code == 409


async def test_an_unreadable_descendant_row_blocks_the_move_visibly(db: _Database) -> None:
    _without_foreign(db)
    broken = _letter(REPLY_A2, ORG_A, A1, previous=CHILD_A1, ancestors=[ROOT_A1, CHILD_A1])
    broken["created_at"] = "not-a-date"
    broken["depth"] = "not-a-number"
    db.letters.docs.append(broken)
    before = _snapshot(db)
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{CHILD_A1}/reparent", json={"new_parent_id": BRANCH_A1})
    assert response.status_code == 409, response.text
    assert _snapshot(db) == before


# ---------------------------------------------------------------------------
# The authorised chain itself applies the drafter rule (drafting, LangGraph and
# strategy consumers call it directly, not through the route)
# ---------------------------------------------------------------------------


async def test_the_authorised_chain_gives_a_drafter_only_assigned_letters(db: _Database) -> None:
    _assign(db, GRAND_A1, DRAFTER_A1)
    service = ConversationService(LetterService(db))
    service._db = db

    chain = await service.get_authorized_conversation_chain(GRAND_A1, DRAFTER_A1)

    assert [str(letter.id) for letter in chain] == [GRAND_A1]


async def test_the_authorised_chain_is_unchanged_for_other_readers(db: _Database) -> None:
    service = ConversationService(LetterService(db))
    service._db = db

    chain = await service.get_authorized_conversation_chain(GRAND_A1, PROJECT_A1_READER)

    assert {str(letter.id) for letter in chain} == {ROOT_A1, CHILD_A1, GRAND_A1}


# ---------------------------------------------------------------------------
# A stored letter row that fails validation is unreadable, not a server error,
# and its contents never reach the response or the logs
# ---------------------------------------------------------------------------


def _corrupt(db: _Database, letter_id: str) -> None:
    for row in db.letters.docs:
        if str(row["_id"]) == letter_id:
            row["created_at"] = "not-a-date"
            row["depth"] = "not-a-number"


@pytest.mark.parametrize("route", ["tree", "chain"])
async def test_a_corrupt_seed_letter_is_not_found(db: _Database, route: str, caplog: pytest.LogCaptureFixture) -> None:
    _corrupt(db, GRAND_A1)
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.get(f"/api/letters/{GRAND_A1}/{route}")
    assert response.status_code == 404, response.text
    assert MARKER[GRAND_A1] not in response.text
    assert MARKER[GRAND_A1] not in caplog.text


async def test_a_corrupt_parent_is_not_found_and_nothing_moves(db: _Database, caplog: pytest.LogCaptureFixture) -> None:
    _corrupt(db, ROOT_A1)
    before = _snapshot(db)
    async with _as(db, PROJECT_A1_EDITOR) as client:
        response = await client.post(f"/api/letters/{GRAND_A1}/reparent", json={"new_parent_id": ROOT_A1})
    assert response.status_code == 404, response.text
    assert _snapshot(db) == before
    assert MARKER[ROOT_A1] not in caplog.text
