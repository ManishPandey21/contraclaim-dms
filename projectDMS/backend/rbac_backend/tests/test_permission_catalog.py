"""Guard: the register/feature permissions the routers enforce must be in the
seeded DEFAULT_PERMISSIONS catalog, so they appear in (and are assignable from)
the frontend Permissions page. Prevents the gap where a new feature adds a
permission the UI never surfaces.
"""

from rbac_backend.core.permissions import CANONICAL_PERMISSIONS, CLIENT_DMS_PERMISSIONS
from rbac_backend.initial_data.default_permissions import DEFAULT_PERMISSIONS as INITIAL_DATA_PERMISSIONS
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from rbac_backend.models.permission import DEFAULT_PERMISSIONS, PermissionCreate


# The contract-controls + register + claims permissions enforced by the routers.
REQUIRED_REGISTER_PERMISSIONS = {
    "dms.contract.master.view", "dms.contract.master.manage",
    "dms.bankguarantee.view", "dms.bankguarantee.create", "dms.bankguarantee.edit",
    "dms.bankguarantee.delete", "dms.bankguarantee.export", "dms.bankguarantee.extend",
    "dms.bankguarantee.release",
    "dms.ipc.view", "dms.ipc.create", "dms.ipc.edit", "dms.ipc.delete",
    "dms.ipc.approve", "dms.ipc.export",
    "dms.variation.view", "dms.variation.create", "dms.variation.edit",
    "dms.variation.delete", "dms.variation.approve", "dms.variation.export",
    "dms.keydate.view", "dms.keydate.create", "dms.keydate.edit", "dms.keydate.delete",
    "dms.keydate.manage", "dms.keydate.export",
    "dms.claim.view", "dms.claim.create", "dms.claim.edit", "dms.claim.delete",
    "dms.claim.manage",
    "dms.chronology.view", "dms.chronology.create", "dms.chronology.edit",
    "dms.chronology.verify", "dms.chronology.export", "dms.chronology.admin",
}


def test_register_permissions_are_in_the_seeded_catalog():
    seeded = {p["name"] for p in DEFAULT_PERMISSIONS}
    missing = sorted(REQUIRED_REGISTER_PERMISSIONS - seeded)
    assert not missing, f"register permissions missing from DEFAULT_PERMISSIONS (won't show in the Permissions page): {missing}"


def test_seeded_permissions_match_router_enforced_strings():
    from rbac_backend.core.permissions import Permissions as P

    seeded = {p["name"] for p in DEFAULT_PERMISSIONS}
    for perm in (
        P.CONTRACT_MASTER_VIEW, P.CONTRACT_MASTER_MANAGE,
        P.BG_VIEW, P.BG_CREATE, P.BG_EXPORT,
        P.IPC_VIEW, P.IPC_CREATE, P.IPC_APPROVE, P.IPC_EXPORT,
        P.VARIATION_VIEW, P.VARIATION_CREATE, P.VARIATION_APPROVE,
    ):
        assert perm in seeded, f"router enforces {perm} but it is not seeded/assignable"


def test_all_default_permissions_validate():
    # Every catalog entry must satisfy PermissionCreate (category/action enums,
    # name format) — a malformed entry would break seeding.
    for entry in DEFAULT_PERMISSIONS:
        PermissionCreate(**entry)


def test_model_permission_catalog_contains_every_canonical_permission():
    seeded = {p["name"] for p in DEFAULT_PERMISSIONS}
    missing = sorted(set(CANONICAL_PERMISSIONS) - seeded)
    assert not missing, f"canonical permissions missing from model DEFAULT_PERMISSIONS: {missing}"


def test_initial_data_permission_catalog_contains_every_canonical_permission():
    seeded = {p["_id"] for p in INITIAL_DATA_PERMISSIONS}
    missing = sorted(set(CANONICAL_PERMISSIONS) - seeded)
    assert not missing, f"canonical permissions missing from initial_data DEFAULT_PERMISSIONS: {missing}"


def test_organization_admin_seed_has_explicit_client_dms_permissions():
    roles = {role["_id"]: set(role.get("permissions") or []) for role in DEFAULT_ROLES}
    missing = sorted(set(CLIENT_DMS_PERMISSIONS) - roles["orgadmin"])
    assert not missing, f"orgadmin seed missing explicit Client DMS permissions: {missing}"
