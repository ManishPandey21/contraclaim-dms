"""Regression tests for default-data seeding.

Guards the role-seeding bug where ``initialize_roles`` called ``Role(**role_data)``
with an ``_id``-keyed seed doc against a model whose field is ``id`` (no alias),
raising ``ValidationError`` so the insert never ran -> 0 of 14 roles seeded ->
non-superadmin RBAC dead on a fresh deploy.
"""

import pytest

from backend.rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from backend.rbac_backend.models.role import Role
from backend.rbac_backend.services.data_initialization import DataInitializer


class _FakeRolesCollection:
    """Minimal async stand-in for the Mongo ``roles`` collection."""

    def __init__(self):
        self.docs = []

    async def find_one(self, query):
        _id = query.get("_id")
        return next((d for d in self.docs if d.get("_id") == _id), None)

    async def insert_one(self, doc):
        self.docs.append(doc)
        return doc

    async def update_one(self, query, update):  # pragma: no cover - not hit on fresh seed
        return None


class _FakeDatabase:
    def __init__(self):
        self.roles = _FakeRolesCollection()


@pytest.mark.asyncio
async def test_initialize_roles_seeds_all_default_roles():
    db = _FakeDatabase()
    created = await DataInitializer(db).initialize_roles()

    # Every default role must be created (the bug seeded zero). Stated against
    # the catalogue rather than a literal count so adding a role -- superuser was
    # added when it turned out to have no role document at all -- does not fail a
    # test that is really about "all of them, not none".
    assert len(DEFAULT_ROLES) >= 14, "catalogue shrank unexpectedly"
    assert created == len(DEFAULT_ROLES)
    assert len(db.roles.docs) == len(DEFAULT_ROLES)

    # Each stored doc is keyed by the semantic _id and rehydrates into a Role
    # the same way role_service reads it (_id -> id).
    seeded_ids = {d["_id"] for d in db.roles.docs}
    assert seeded_ids == {r["_id"] for r in DEFAULT_ROLES}
    for doc in db.roles.docs:
        rehydrated = dict(doc)
        rehydrated["id"] = str(rehydrated.pop("_id"))
        role = Role(**rehydrated)  # must not raise
        assert role.id in seeded_ids


@pytest.mark.asyncio
async def test_initialize_roles_is_idempotent():
    db = _FakeDatabase()
    first = await DataInitializer(db).initialize_roles()
    second = await DataInitializer(db).initialize_roles()

    # Second run finds existing roles by _id and creates none.
    assert first == len(DEFAULT_ROLES)
    assert second == 0
    assert len(db.roles.docs) == len(DEFAULT_ROLES)
