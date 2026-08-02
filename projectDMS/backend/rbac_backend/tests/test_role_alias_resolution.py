"""Role lookup must tolerate alias role ids.

``users.roles`` may hold either a canonical id (``orgadmin``) or an alias
(``organization-admin``), but the ``roles`` collection is keyed by the canonical
id only. Every lookup site queried the raw value, missed, and then hit
``if not role: continue`` -- silently dropping the role, so the user resolved to
*zero* permissions and was denied everything while looking correctly configured.

Confirmed live before the fix: three accounts carrying ``organization-admin`` or
``project-admin`` returned False for projects:read, dms.document.view and
organizations:read, while every account with a canonical id returned True.
"""

from __future__ import annotations

import inspect

import pytest

from rbac_backend.services import permission_service as ps
from rbac_backend.services.permission_service import _find_role_doc, _normalize_role_name


class _Roles:
    """Keyed by canonical id only, like production."""

    def __init__(self):
        self.docs = {
            "orgadmin": {"_id": "orgadmin", "name": "Organization-Admin", "permissions": ["projects:read"]},
            "projectadmin": {"_id": "projectadmin", "name": "Project-Admin", "permissions": ["projects:read"]},
            "superadmin": {"_id": "superadmin", "name": "Super Admin", "permissions": ["*"]},
        }
        self.queries = []

    async def find_one(self, query):
        key = str((query or {}).get("_id"))
        self.queries.append(key)
        doc = self.docs.get(key)
        return dict(doc) if doc else None


class _DB:
    def __init__(self):
        self.roles = _Roles()


# --- alias resolution ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "alias,expected",
    [
        ("organization-admin", "orgadmin"),
        ("organisation-admin", "orgadmin"),
        ("organization admin", "orgadmin"),
        ("organization_admin", "orgadmin"),
        ("project-admin", "projectadmin"),
        ("project admin", "projectadmin"),
        ("project_admin", "projectadmin"),
        ("super-admin", "superadmin"),
    ],
)
async def test_alias_role_ids_resolve_to_the_canonical_document(alias, expected):
    role = await _find_role_doc(_DB(), alias)
    assert role is not None, f"{alias} must resolve; unresolved roles grant no permissions"
    assert role["_id"] == expected


@pytest.mark.asyncio
async def test_canonical_ids_still_resolve_directly():
    db = _DB()
    role = await _find_role_doc(db, "orgadmin")
    assert role["_id"] == "orgadmin"
    # The canonical id must match on the first attempt, not via the alias pass.
    assert db.roles.queries[0] == "orgadmin"


@pytest.mark.asyncio
async def test_unknown_role_still_returns_none():
    assert await _find_role_doc(_DB(), "not-a-real-role") is None


@pytest.mark.asyncio
async def test_blank_role_returns_none():
    assert await _find_role_doc(_DB(), "") is None


@pytest.mark.asyncio
async def test_alias_lookup_does_not_shadow_a_real_raw_id():
    """A raw id that exists must win over its normalised form."""
    db = _DB()
    db.roles.docs["organization-admin"] = {
        "_id": "organization-admin",
        "name": "Legacy Org Admin",
        "permissions": ["legacy:only"],
    }
    role = await _find_role_doc(db, "organization-admin")
    assert role["_id"] == "organization-admin"
    assert role["permissions"] == ["legacy:only"]


def test_normalize_role_name_is_case_insensitive():
    assert _normalize_role_name("Organization-Admin") == "orgadmin"
    assert _normalize_role_name("  PROJECT-ADMIN  ") == "projectadmin"


# --- guard: the raw lookup must not come back -----------------------------


def test_no_site_looks_up_roles_by_raw_id():
    """Every role lookup must go through _find_role_doc.

    Reintroducing ``db.roles.find_one({"_id": role_qid})`` anywhere would
    silently strip permissions from alias-role users again, and the failure mode
    is a 403 that looks like correct authorization.
    """
    source = inspect.getsource(ps)
    assert 'db.roles.find_one({"_id": role_qid})' not in source, (
        "role lookup bypasses _find_role_doc; alias role ids will resolve to no permissions"
    )
    assert source.count("_find_role_doc(db, rid)") >= 4, (
        "expected every role lookup site to use the alias-tolerant resolver"
    )
