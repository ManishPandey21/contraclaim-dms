"""Tenant isolation of the letter listing, ``GET /api/letters``.

The listing used to be bounded only by what the client asked for:
``AuthorizationService.build_letter_query`` copied the request's
``organization_id`` / ``project_id`` into the Mongo filter verbatim, and
``LetterService.get_letters`` then rebuilt the query from a whitelist of three
keys, discarding every other clause the builder produced. Two consequences:

* a request with no filters had no tenant predicate at all, so it listed every
  organisation's letters; and
* a request naming another organisation's id listed that organisation's letters.

The contract pinned here: the caller's organisation/project scope, as computed
by ``core.security.build_scope_query`` (the release's list-visibility rule,
``core/tenant_context.py``: "Row visibility for listings is still
``build_scope_query``"), bounds the rows. Request filters may narrow that scope
and never widen it. A request that reaches outside the scope gets an empty list,
the same answer every other ``build_scope_query`` listing gives; it is not 403.

These tests exercise the real controller, the real ``AuthorizationService`` and
the real ``LetterService`` over an in-memory collection that evaluates the Mongo
filter, so a clause the service drops is a failing test, not a passing one.
Letters are deliberately outside ``ActiveScope`` (CL-4B), so the navbar
selection headers play no part here.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

import httpx
from bson import ObjectId

from rbac_backend.core.security import CurrentUser
from rbac_backend.main import app
from rbac_backend.routers.letters import LetterController, get_letter_controller
from rbac_backend.services.authorization_service import AuthorizationService
from rbac_backend.services.letter_service import LetterService

ORG_A, ORG_B = "org-a", "org-b"
PROJ_A1, PROJ_A2, PROJ_B1 = "proj-a1", "proj-a2", "proj-b1"
DRAFTER_ID = "user-drafter"

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _letter(
    letter_id: str,
    org: str,
    project: Optional[str],
    *,
    status: str = "Draft",
    subject: str = "Notice of delay",
    assigned_to: str = "someone",
    minutes: int = 0,
) -> Dict[str, Any]:
    return {
        "_id": letter_id,
        "title": f"Letter {letter_id}",
        "recipient": "Engineer",
        "subject": subject,
        "content": "body",
        "status": status,
        "created_by": "author",
        "assigned_to": assigned_to,
        "organization_id": org,
        "project_id": project,
        "updatedAt": _BASE + timedelta(minutes=minutes),
    }


LETTERS: List[Dict[str, Any]] = [
    _letter("a1-draft", ORG_A, PROJ_A1, minutes=1, assigned_to=DRAFTER_ID),
    _letter("a1-sent", ORG_A, PROJ_A1, status="Sent", minutes=2),
    _letter("a2-draft", ORG_A, PROJ_A2, minutes=3),
    _letter("a-noproj", ORG_A, None, minutes=4),
    _letter("b1-draft", ORG_B, PROJ_B1, minutes=5, subject="Notice of delay (B)"),
    _letter("b1-sent", ORG_B, PROJ_B1, status="Sent", minutes=6, assigned_to=DRAFTER_ID),
]
ORG_A_IDS = {"a1-draft", "a1-sent", "a2-draft", "a-noproj"}
ORG_B_IDS = {"b1-draft", "b1-sent"}
ALL_IDS = ORG_A_IDS | ORG_B_IDS


# ---------------------------------------------------------------------------
# In-memory collection that evaluates the Mongo filter it is given
# ---------------------------------------------------------------------------
def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$and":
            if not all(_matches(doc, part) for part in condition):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, part) for part in condition):
                return False
            continue
        if key.startswith("$"):
            raise AssertionError(f"unsupported top-level operator {key}")
        value = doc.get(key)
        if isinstance(condition, dict) and any(k.startswith("$") for k in condition):
            for op, operand in condition.items():
                if op == "$in":
                    if value not in operand:
                        return False
                elif op == "$regex":
                    flags = re.IGNORECASE if "i" in str(condition.get("$options", "")) else 0
                    if value is None or not re.search(operand, str(value), flags):
                        return False
                elif op == "$options":
                    continue
                else:
                    raise AssertionError(f"unsupported operator {op}")
        elif value != condition:
            return False
    return True


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, spec):
        for field, direction in reversed(list(spec)):
            self._docs.sort(key=lambda d: d.get(field), reverse=direction < 0)
        return self

    def skip(self, n: int):
        self._docs = self._docs[n:]
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n] if n else self._docs
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _LettersCollection:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self.docs = [dict(d) for d in docs]
        self.filters: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        query = query or {}
        self.filters.append(query)
        return _Cursor([dict(d) for d in self.docs if _matches(d, query)])


class _Db:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self.letters = _LettersCollection(docs)


class _NoLimit:
    async def check_user_limit(self, *_args, **_kwargs) -> None:
        return None


def _controller(docs: List[Dict[str, Any]] = LETTERS) -> LetterController:
    return LetterController(
        letter_service=LetterService(_Db(docs)),
        conversation_service=None,
        auth_service=AuthorizationService(),
        rate_limiter=_NoLimit(),
    )


def _user(user_id: str, roles: List[str], org: Optional[str] = ORG_A, projects=()) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        roles=roles,
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
    )


ORG_USER = _user("u-orguser", ["orguser"])
ORG_ADMIN = _user("u-orgadmin", ["orgadmin"])
PROJECT_USER = _user("u-projuser", ["projectuser"], projects=[PROJ_A1])
PROJECT_ADMIN = _user("u-projadmin", ["projectadmin"], projects=[PROJ_A2])
DRAFTER = _user(DRAFTER_ID, ["contraclaim_expert_drafter"], projects=[PROJ_A1])
SUPERADMIN = _user("u-super", ["superadmin"], org=None)
SUPERUSER = _user("u-superuser", ["superuser"])
ORGLESS_USER = _user("u-orgless", ["orguser"], org=None)
UNASSIGNED_PROJECT_USER = _user("u-noproj", ["projectuser"], projects=[])
NON_TIER_ROLE = _user("u-reporter", ["reporter"])


async def _list(
    user: CurrentUser,
    *,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    controller: Optional[LetterController] = None,
) -> List[str]:
    controller = controller or _controller()
    filters = {
        "status": status,
        "organization_id": organization_id,
        "project_id": project_id,
        "search_query": q,
    }
    letters = await controller.get_letters(filters, {"skip": skip, "limit": limit}, user)
    return [letter.id for letter in letters if letter is not None]


# ---------------------------------------------------------------------------
# Organisation isolation
# ---------------------------------------------------------------------------
async def test_unfiltered_listing_is_bounded_to_the_callers_organisation() -> None:
    assert set(await _list(ORG_USER)) == ORG_A_IDS


async def test_org_user_never_sees_another_organisations_letters() -> None:
    for kwargs in ({}, {"status": "Sent"}, {"q": "delay"}, {"project_id": PROJ_B1}):
        assert not set(await _list(ORG_USER, **kwargs)) & ORG_B_IDS, kwargs


async def test_naming_a_foreign_organisation_returns_nothing() -> None:
    assert await _list(ORG_USER, organization_id=ORG_B) == []


async def test_naming_the_callers_own_organisation_still_works() -> None:
    assert set(await _list(ORG_USER, organization_id=ORG_A)) == ORG_A_IDS


async def test_all_sentinel_does_not_widen_scope() -> None:
    assert set(await _list(ORG_USER, organization_id="all")) == ORG_A_IDS


async def test_malformed_organisation_id_returns_nothing() -> None:
    assert await _list(ORG_USER, organization_id='{"$ne": null}') == []


# ---------------------------------------------------------------------------
# Project isolation
# ---------------------------------------------------------------------------
async def test_project_user_is_bounded_to_assigned_projects() -> None:
    assert set(await _list(PROJECT_USER)) == {"a1-draft", "a1-sent"}


async def test_unassigned_project_filter_returns_nothing() -> None:
    assert await _list(PROJECT_USER, project_id=PROJ_A2) == []
    assert await _list(PROJECT_USER, project_id=PROJ_B1) == []


async def test_assigned_project_filter_still_works() -> None:
    assert set(await _list(PROJECT_USER, project_id=PROJ_A1)) == {"a1-draft", "a1-sent"}


async def test_own_organisation_plus_foreign_project_cannot_escape() -> None:
    assert await _list(PROJECT_USER, organization_id=ORG_A, project_id=PROJ_B1) == []
    assert await _list(ORG_USER, organization_id=ORG_A, project_id=PROJ_B1) == []


async def test_org_user_project_filter_narrows_within_organisation() -> None:
    assert set(await _list(ORG_USER, project_id=PROJ_A2)) == {"a2-draft"}


# ---------------------------------------------------------------------------
# Empty scope fails closed
# ---------------------------------------------------------------------------
async def test_principal_without_an_organisation_gets_nothing() -> None:
    assert await _list(ORGLESS_USER) == []


async def test_project_tier_principal_without_projects_gets_nothing() -> None:
    assert await _list(UNASSIGNED_PROJECT_USER) == []


async def test_role_outside_the_scope_model_gets_nothing() -> None:
    """Same deny-by-default as every other ``build_scope_query`` listing."""
    assert await _list(NON_TIER_ROLE) == []


# ---------------------------------------------------------------------------
# Existing role behaviour
# ---------------------------------------------------------------------------
async def test_contract_drafter_sees_only_assigned_letters_inside_scope() -> None:
    # DRAFTER_ID is also the assignee of b1-sent in ORG_B: scope must still apply.
    assert await _list(DRAFTER) == ["a1-draft"]


async def test_org_admin_sees_the_whole_organisation_only() -> None:
    assert set(await _list(ORG_ADMIN)) == ORG_A_IDS
    assert await _list(ORG_ADMIN, organization_id=ORG_B) == []


async def test_project_admin_is_bounded_to_assigned_projects() -> None:
    assert await _list(PROJECT_ADMIN) == ["a2-draft"]
    assert await _list(PROJECT_ADMIN, project_id=PROJ_A1) == []


async def test_superadmin_lists_every_organisation_and_may_narrow() -> None:
    assert set(await _list(SUPERADMIN)) == ALL_IDS
    assert set(await _list(SUPERADMIN, organization_id=ORG_B)) == ORG_B_IDS


async def test_dormant_super_user_is_not_granted_global_reach() -> None:
    """Super User stays dormant: no Super Admin grant, bounded to its own tenants."""
    assert set(await _list(SUPERUSER)) == ORG_A_IDS
    assert await _list(SUPERUSER, organization_id=ORG_B) == []


# ---------------------------------------------------------------------------
# Status, search, ordering and pagination survive scoping
# ---------------------------------------------------------------------------
async def test_status_filter_applies_within_scope() -> None:
    assert await _list(ORG_USER, status="Sent") == ["a1-sent"]


async def test_search_applies_within_scope() -> None:
    assert set(await _list(ORG_USER, q="delay")) == ORG_A_IDS
    assert await _list(ORG_USER, q="(B)") == []


async def test_ordering_is_newest_first() -> None:
    assert await _list(ORG_USER) == ["a-noproj", "a2-draft", "a1-sent", "a1-draft"]


async def test_pagination_pages_through_scope_only() -> None:
    first = await _list(ORG_USER, skip=0, limit=2)
    second = await _list(ORG_USER, skip=2, limit=2)
    third = await _list(ORG_USER, skip=4, limit=2)
    assert first == ["a-noproj", "a2-draft"]
    assert second == ["a1-sent", "a1-draft"]
    assert third == []


# ---------------------------------------------------------------------------
# Lower layers: the service must apply the authorised query as given
# ---------------------------------------------------------------------------
async def test_service_does_not_drop_a_deny_all_scope() -> None:
    service = LetterService(_Db(LETTERS))
    letters = await service.get_letters_paginated({"_id": {"$in": []}}, {"skip": 0, "limit": 100})
    assert letters == []


async def test_service_applies_set_valued_scope_clauses() -> None:
    service = LetterService(_Db(LETTERS))
    letters = await service.get_letters_paginated(
        {"organization_id": ORG_A, "project_id": {"$in": [PROJ_A2, ObjectId()]}},
        {"skip": 0, "limit": 100},
    )
    assert [letter.id for letter in letters] == ["a2-draft"]


async def test_service_applies_assignment_narrowing() -> None:
    service = LetterService(_Db(LETTERS))
    letters = await service.get_letters_paginated(
        {"organization_id": ORG_A, "assigned_to": DRAFTER_ID}, {"skip": 0, "limit": 100}
    )
    assert [letter.id for letter in letters] == ["a1-draft"]


# ---------------------------------------------------------------------------
# Route plumbing: query parameters reach the scoped builder
# ---------------------------------------------------------------------------
@contextmanager
def _route(user: CurrentUser) -> Iterator[None]:
    from rbac_backend.routers.letters import get_current_user as letters_get_current_user

    controller = _controller()
    app.dependency_overrides[get_letter_controller] = lambda: controller
    app.dependency_overrides[letters_get_current_user] = lambda: user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_letter_controller, None)
        app.dependency_overrides.pop(letters_get_current_user, None)


async def test_http_listing_is_tenant_bound() -> None:
    with _route(ORG_USER):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unfiltered = await client.get("/api/letters")
            foreign = await client.get("/api/letters", params={"organization_id": ORG_B})
            foreign_project = await client.get(
                "/api/letters", params={"organization_id": ORG_A, "project_id": PROJ_B1}
            )
    assert unfiltered.status_code == 200
    assert {item.get("_id") or item.get("id") for item in unfiltered.json()} == ORG_A_IDS
    assert foreign.status_code == 200 and foreign.json() == []
    assert foreign_project.status_code == 200 and foreign_project.json() == []
