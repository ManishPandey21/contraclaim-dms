"""Real MongoDB proof of the Contract Master permission transition.

Owned by implementation ticket 04, which is deliberately atomic. Two invariants
have to hold *together*, and either one alone is a defect:

``P-01``
    ``CONTRACT_APPLICABILITY_MANAGE`` and ``CONTRACT_CATALOGUE_BROWSE`` are
    canonical CLIENT_DMS permissions — seeded into both permission catalogues and
    classified as entitlement-scoped like every other DMS permission.

``P-02``
    Project Admin does not receive them through the bulk CLIENT_DMS merge.

Why both: ``CLIENT_DMS_PERMISSIONS`` does three unrelated jobs at once. It feeds
the canonical catalogue, it *defines* the entitlement-scoped set, and it is
merged wholesale into four default roles. Dropping the two permissions from that
list — the obvious way to keep Project Admin out — would leave them unseeded and
unassignable, and would make the entitlement service classify them as
"not entitlement scoped", which returns *True* and bypasses the subscription
gate. So the exclusion has to live in the role merge, not in the list.

Why real Mongo: the thing that matters is the **persisted role document**, not
the Python constant. Re-seeding uses ``$addToSet``, which only ever adds — so a
constant-only assertion would say nothing about what an existing deployment's
``projectadmin`` role actually ends up holding.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.core.permissions import (
    CANONICAL_PERMISSIONS,
    CLIENT_DMS_PERMISSIONS,
    ORG_TIER_ONLY_PERMISSIONS,
    Permissions,
)
from rbac_backend.services.entitlement_service import (
    DMS_FEATURE_PERMISSIONS,
    required_feature_keys,
)

MONGODB_URI_ENV = "CONTRACT_MASTER_MONGODB_URI"

APPLICABILITY = "dms.contract.applicability.manage"
CATALOGUE = "dms.contract.catalogue.browse"
NEW_PERMISSIONS = (APPLICABILITY, CATALOGUE)


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a test-only MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _Fixture:
    def __init__(self, client: AsyncIOMotorClient, name: str) -> None:
        self.client = client
        self.db = client[name]
        self.name = name


async def _fresh_db() -> _Fixture:
    client = AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=8000)
    return _Fixture(client, f"cm_t04_{uuid.uuid4().hex[:12]}")


async def _drop(fixture: _Fixture) -> None:
    await fixture.client.drop_database(fixture.name)
    fixture.client.close()


async def _seed_roles(db: Any) -> None:
    from rbac_backend.services.data_initialization import DataInitializer

    await DataInitializer(db).initialize_roles()


async def _role_permissions(db: Any, role_id: str) -> set[str]:
    role = await db.roles.find_one({"_id": role_id})
    assert role is not None, f"role {role_id} was not seeded"
    return {str(permission) for permission in (role.get("permissions") or [])}


# --------------------------------------------------------------------------- #
# P-01 — canonical membership, catalogue seeding, entitlement scoping
# --------------------------------------------------------------------------- #


def test_p01_both_permissions_are_canonical_client_dms_permissions() -> None:
    for permission in NEW_PERMISSIONS:
        assert permission in CLIENT_DMS_PERMISSIONS, (
            f"{permission} is not in CLIENT_DMS_PERMISSIONS. Removing it to keep "
            "Project Admin out is the superseded approach: it also unseeds the "
            "permission and bypasses the entitlement gate."
        )
        assert permission in CANONICAL_PERMISSIONS


def test_p01_constants_are_exposed_on_the_permissions_class() -> None:
    assert Permissions.CONTRACT_APPLICABILITY_MANAGE == APPLICABILITY
    assert Permissions.CONTRACT_CATALOGUE_BROWSE == CATALOGUE


def test_p01_both_permissions_are_entitlement_scoped() -> None:
    """The CM-14 failure mode, asserted directly.

    A permission outside ``DMS_FEATURE_PERMISSIONS`` is classified
    "not entitlement scoped" and passes the subscription gate unconditionally.
    """
    for permission in NEW_PERMISSIONS:
        assert permission in DMS_FEATURE_PERMISSIONS, (
            f"{permission} is outside the entitlement-scoped set and would "
            "bypass the subscription gate"
        )
        assert required_feature_keys(permission)[0] == "feature.dms.enabled"


def test_p01_both_permissions_are_in_both_seed_catalogues() -> None:
    from rbac_backend.initial_data.default_permissions import (
        DEFAULT_PERMISSIONS as INITIAL_DATA_PERMISSIONS,
    )
    from rbac_backend.models.permission import DEFAULT_PERMISSIONS as MODEL_PERMISSIONS

    model_names = {entry["name"] for entry in MODEL_PERMISSIONS}
    seed_ids = {entry["_id"] for entry in INITIAL_DATA_PERMISSIONS}
    for permission in NEW_PERMISSIONS:
        assert permission in model_names, f"{permission} missing from model catalogue"
        assert permission in seed_ids, f"{permission} missing from initial_data catalogue"


# --------------------------------------------------------------------------- #
# P-02 — Project Admin does not inherit, proven on persisted state
# --------------------------------------------------------------------------- #


def test_p02_org_tier_only_set_names_exactly_the_two_permissions() -> None:
    assert set(ORG_TIER_ONLY_PERMISSIONS) == set(NEW_PERMISSIONS)


def test_p02_project_admin_persisted_role_excludes_both_on_cold_start() -> None:
    """The load-bearing assertion: the role document as it lands in Mongo."""

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            assert await fixture.db.roles.count_documents({}) == 0, "not a cold start"
            await _seed_roles(fixture.db)

            granted = await _role_permissions(fixture.db, "projectadmin")
            leaked = granted & set(NEW_PERMISSIONS)
            assert not leaked, (
                f"projectadmin inherited organisation-tier permissions: {leaked}"
            )
            # and it still got the ordinary DMS permissions
            assert "dms.document.view" in granted
        finally:
            await _drop(fixture)

    _run(scenario())


def test_p02_org_tier_roles_retain_the_full_list_on_cold_start() -> None:
    """Guard against over-correcting: the exclusion applies to projectadmin only."""

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await _seed_roles(fixture.db)
            for role_id in ("superadmin", "orgadmin", "contractmgr_org"):
                granted = await _role_permissions(fixture.db, role_id)
                missing = set(NEW_PERMISSIONS) - granted
                assert not missing, f"{role_id} lost organisation-tier permissions: {missing}"
        finally:
            await _drop(fixture)

    _run(scenario())


def test_p02_project_user_does_not_receive_either_permission() -> None:
    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await _seed_roles(fixture.db)
            role = await fixture.db.roles.find_one({"_id": "projectuser"})
            if role is None:
                pytest.skip("no projectuser role in the default set")
            granted = {str(p) for p in (role.get("permissions") or [])}
            assert not (granted & set(NEW_PERMISSIONS))
        finally:
            await _drop(fixture)

    _run(scenario())


def test_p02_reseeding_cannot_reintroduce_the_overgrant() -> None:
    """Re-seeding uses ``$addToSet``, which only ever adds.

    So the exclusion has to hold on every run, not just the first: if the merged
    default ever contained them, no later seed would take them away.
    """

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await _seed_roles(fixture.db)
            first = await _role_permissions(fixture.db, "projectadmin")
            await _seed_roles(fixture.db)
            await _seed_roles(fixture.db)
            third = await _role_permissions(fixture.db, "projectadmin")

            assert not (third & set(NEW_PERMISSIONS)), (
                "re-seeding reintroduced organisation-tier permissions to projectadmin"
            )
            assert first == third, "re-seeding changed projectadmin's permission set"

            stored = await fixture.db.roles.find_one({"_id": "projectadmin"})
            assert stored is not None
            permissions = [str(p) for p in (stored.get("permissions") or [])]
            assert len(permissions) == len(set(permissions)), (
                f"re-seeding duplicated permission entries: {permissions}"
            )
        finally:
            await _drop(fixture)

    _run(scenario())


def test_p02_exclusion_does_not_shrink_the_canonical_catalogue() -> None:
    """Both invariants at once, in one assertion.

    P-02 must be achieved by narrowing the *merge*, never by shrinking the list —
    so the permission is absent from projectadmin while still present in the
    canonical set.
    """

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await _seed_roles(fixture.db)
            granted = await _role_permissions(fixture.db, "projectadmin")
            for permission in NEW_PERMISSIONS:
                assert permission not in granted  # P-02
                assert permission in CLIENT_DMS_PERMISSIONS  # P-01
                assert permission in DMS_FEATURE_PERMISSIONS  # P-01
        finally:
            await _drop(fixture)

    _run(scenario())
