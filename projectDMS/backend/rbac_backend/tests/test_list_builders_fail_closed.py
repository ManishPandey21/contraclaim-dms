"""Users, email groups and parties listings: an empty scope must deny, never widen.

``AuthorizationService.build_user_query``, ``build_email_group_query`` and
``build_party_query`` each guarded their tenant predicate with ``if allowed_orgs``
(and project predicates with ``if allowed_projects``), so a principal with no
organisation, or a project-tier principal with no projects, fell through with no
predicate at all:

* users - an org-less principal naming any ``organization_id`` listed that
  organisation's users (the no-filter path already refused);
* email groups - an org-less principal got no organisation predicate, with or
  without a filter; a project-tier principal with no projects saw the whole org;
* parties - an org-less principal outside the org/project tiers got no tenant
  predicate; a project-tier principal with no projects saw the whole org.

Underneath, ``PartyService.get_parties_paginated`` rebuilt its query from a
whitelist of request keys and discarded the builder's scope clauses entirely, so
the parties listing was unscoped for every caller whatever the builder returned.

Invariant pinned here: an empty authorised organisation (or, for project-tier
roles, project) scope is deny-all. An explicit organisation outside scope keeps the
builders' existing answer, ``AuthorizationError`` (403). The listing services apply
the builder's query as given; each case runs builder plus service over an
in-memory collection that evaluates the Mongo filter.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.core.security import CurrentUser
from rbac_backend.services.authorization_service import AuthorizationService
from rbac_backend.services.email_group_service import EmailGroupService
from rbac_backend.services.party_service import PartyService
from rbac_backend.services.user_service import UserService
from rbac_backend.utils.error_handler import AuthorizationError

ORG_A, ORG_B = "org-a", "org-b"
PROJ_A1, PROJ_A2, PROJ_B1 = "proj-a1", "proj-a2", "proj-b1"


# ---------------------------------------------------------------------------
# In-memory collection that evaluates the Mongo filter it is given
# ---------------------------------------------------------------------------
def _field_matches(value: Any, condition: Any) -> bool:
    values = value if isinstance(value, list) else [value]
    if isinstance(condition, dict) and any(k.startswith("$") for k in condition):
        for op, operand in condition.items():
            if op == "$in":
                if not any(v in operand for v in values):
                    return False
            elif op == "$regex":
                flags = re.IGNORECASE if "i" in str(condition.get("$options", "")) else 0
                if not any(v is not None and re.search(str(operand), str(v), flags) for v in values):
                    return False
            elif op == "$options":
                continue
            else:
                raise AssertionError(f"unsupported operator {op}")
        return True
    return condition in values or value == condition


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$and":
            if not all(_matches(doc, part) for part in condition):
                return False
        elif key == "$or":
            if not any(_matches(doc, part) for part in condition):
                return False
        elif key.startswith("$"):
            raise AssertionError(f"unsupported top-level operator {key}")
        elif not _field_matches(doc.get(key), condition):
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n] if n else self._docs
        return self

    async def to_list(self, length=None):
        return list(self._docs)

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _Collection:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def find(self, query=None, *_args, **_kwargs):
        return _Cursor([dict(d) for d in self.docs if _matches(d, query or {})])

    async def count_documents(self, query=None):
        return sum(1 for d in self.docs if _matches(d, query or {}))


def _user(uid: str, roles: List[str], org: Optional[str] = ORG_A, projects=()) -> CurrentUser:
    return CurrentUser(
        id=uid,
        username=uid,
        email=f"{uid}@example.com",
        roles=roles,
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
    )


ORG_USER = _user("u-orguser", ["orguser"])
PROJECT_USER = _user("u-proj", ["projectuser"], projects=[PROJ_A1])
PROJECT_USER_NO_PROJECTS = _user("u-proj-none", ["projectuser"], projects=[])
#: Holds a list permission through a role outside the org/project tiers, no org.
ORGLESS = _user("u-orgless", ["contraclaim_billing_admin"], org=None)
SUPERADMIN = _user("u-super", ["superadmin"], org=None)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
USERS = [
    {"_id": "ua1", "email": "ua1@example.com", "organization_id": ORG_A, "projects": [PROJ_A1], "roles": []},
    {"_id": "ua2", "email": "ua2@example.com", "organization_id": ORG_A, "projects": [PROJ_A2], "roles": []},
    {"_id": "ub1", "email": "ub1@example.com", "organization_id": ORG_B, "projects": [PROJ_B1], "roles": []},
]


async def _users(principal: CurrentUser, filters: Optional[Dict[str, Any]] = None) -> set:
    query = await AuthorizationService().build_user_query(principal, filters or {})
    db = type("Db", (), {"users": _Collection(USERS)})()
    users, total = await UserService(db).get_users_paginated(query, {"skip": 0, "limit": 100})
    assert total == len(users)
    return {u.id for u in users}


async def test_users_orgless_principal_naming_an_organisation_is_refused() -> None:
    with pytest.raises(AuthorizationError):
        await _users(ORGLESS, {"organization_id": ORG_B})


async def test_users_orgless_principal_without_filter_is_still_refused() -> None:
    with pytest.raises(AuthorizationError):
        await _users(ORGLESS)


async def test_users_org_user_is_bounded_and_foreign_org_refused() -> None:
    assert await _users(ORG_USER) == {"ua1", "ua2"}
    assert await _users(ORG_USER, {"organization_id": ORG_A}) == {"ua1", "ua2"}
    with pytest.raises(AuthorizationError):
        await _users(ORG_USER, {"organization_id": ORG_B})


async def test_users_superadmin_unchanged() -> None:
    assert await _users(SUPERADMIN) == {"ua1", "ua2", "ub1"}
    assert await _users(SUPERADMIN, {"organization_id": ORG_B}) == {"ub1"}


# ---------------------------------------------------------------------------
# Email groups
# ---------------------------------------------------------------------------
GROUPS = [
    {"_id": "ga1", "name": "A1 group", "organization_id": ORG_A, "project_id": PROJ_A1, "is_active": True},
    {"_id": "ga2", "name": "A2 group", "organization_id": ORG_A, "project_id": PROJ_A2, "is_active": True},
    {"_id": "gb1", "name": "B1 group", "organization_id": ORG_B, "project_id": PROJ_B1, "is_active": True},
]


async def _groups(principal: CurrentUser, filters: Optional[Dict[str, Any]] = None) -> set:
    query = await AuthorizationService().build_email_group_query(principal, filters or {})
    service = EmailGroupService()
    service._collection = _Collection(GROUPS)
    groups, total = await service.get_groups_paginated(query, {"skip": 0, "limit": 100})
    assert total == len(groups)
    return {g.id for g in groups}


async def test_email_groups_orgless_principal_without_filter_gets_nothing() -> None:
    assert await _groups(ORGLESS) == set()


async def test_email_groups_orgless_principal_naming_an_organisation_is_refused() -> None:
    with pytest.raises(AuthorizationError):
        await _groups(ORGLESS, {"organization_id": ORG_B})


async def test_email_groups_project_tier_without_projects_gets_nothing() -> None:
    assert await _groups(PROJECT_USER_NO_PROJECTS) == set()


async def test_email_groups_org_user_bounded_and_foreign_org_refused() -> None:
    assert await _groups(ORG_USER) == {"ga1", "ga2"}
    with pytest.raises(AuthorizationError):
        await _groups(ORG_USER, {"organization_id": ORG_B})


async def test_email_groups_project_user_bounded_to_assigned_projects() -> None:
    assert await _groups(PROJECT_USER) == {"ga1"}


async def test_email_groups_superadmin_unchanged() -> None:
    assert await _groups(SUPERADMIN) == {"ga1", "ga2", "gb1"}


# ---------------------------------------------------------------------------
# Parties
# ---------------------------------------------------------------------------
PARTIES = [
    {"_id": "pa1", "name": "Alpha", "type": "Organization", "organization_id": ORG_A, "projects": [PROJ_A1], "is_active": True},
    {"_id": "pa2", "name": "Bravo", "type": "Organization", "organization_id": ORG_A, "projects": [PROJ_A2], "is_active": True},
    {"_id": "pb1", "name": "Charlie", "type": "Individual", "organization_id": ORG_B, "projects": [PROJ_B1], "is_active": True},
]


async def _parties(principal: CurrentUser, filters: Optional[Dict[str, Any]] = None) -> set:
    query = await AuthorizationService().build_party_query(principal, filters or {})
    service = PartyService()
    service.db = type("Db", (), {"parties": _Collection(PARTIES)})()
    parties, total = await service.get_parties_paginated(query, {"skip": 0, "limit": 100})
    assert total == len(parties)
    return {p.id for p in parties}


async def test_parties_org_user_is_bounded_to_its_organisation() -> None:
    assert await _parties(ORG_USER) == {"pa1", "pa2"}


async def test_parties_project_user_is_bounded_to_assigned_projects() -> None:
    assert await _parties(PROJECT_USER) == {"pa1"}


async def test_parties_orgless_principal_gets_nothing() -> None:
    assert await _parties(ORGLESS) == set()


async def test_parties_project_tier_without_projects_gets_nothing() -> None:
    assert await _parties(PROJECT_USER_NO_PROJECTS) == set()


async def test_parties_filters_narrow_within_scope() -> None:
    assert await _parties(ORG_USER, {"type": "Organization", "search": "Bra"}) == {"pa2"}
    assert await _parties(ORG_USER, {"project_id": PROJ_B1}) == set()


async def test_parties_superadmin_unchanged() -> None:
    assert await _parties(SUPERADMIN) == {"pa1", "pa2", "pb1"}
    assert await _parties(SUPERADMIN, {"type": "Individual"}) == {"pb1"}
