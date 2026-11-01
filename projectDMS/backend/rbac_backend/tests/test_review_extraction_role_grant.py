"""Release 1.1.1 owner decision: `dms.document.review_extraction` for orgadmin and
projectadmin ONLY.

Approving a page the extraction quality gate held back releases evidence for
retrieval and drafting, so the grant is deliberately narrower than the bulk
CLIENT_DMS merge (which would also hand it to contractmgr_org and to the
superadmin document). These checks pin:

* the release role definitions grant it to exactly those two roles;
* it stays a CLIENT_DMS permission (catalogued, entitlement-scoped);
* `align_role_contract` - the reviewed production cutover operation - adds it to
  those two production role documents only, touches no other role, and a second
  apply writes nothing.
"""

from __future__ import annotations

import copy

from rbac_backend.core.permissions import (
    CLIENT_DMS_PERMISSIONS,
    EXPLICIT_GRANT_ONLY_PERMISSIONS,
    PERMISSION_DOMAINS,
    Permissions,
)
from rbac_backend.initial_data.default_permissions import DEFAULT_PERMISSIONS
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from rbac_backend.services import role_contract_alignment as alignment
from rbac_backend.tests.test_role_contract_alignment import _db, _production_shaped, _row

REVIEW = Permissions.DOCUMENT_REVIEW_EXTRACTION


def test_only_orgadmin_and_projectadmin_hold_it_in_the_release_definitions() -> None:
    holders = {role["_id"] for role in DEFAULT_ROLES if REVIEW in (role.get("permissions") or [])}
    assert holders == {"orgadmin", "projectadmin"}


def test_it_is_still_a_catalogued_entitlement_scoped_client_permission() -> None:
    assert REVIEW == "dms.document.review_extraction"
    assert REVIEW in CLIENT_DMS_PERMISSIONS
    assert PERMISSION_DOMAINS[REVIEW] == "client_dms"
    assert REVIEW in EXPLICIT_GRANT_ONLY_PERMISSIONS
    assert any(row.get("_id") == REVIEW for row in DEFAULT_PERMISSIONS)


def _production_without_review():
    """Production-shaped role documents that predate Release 1.1.1."""
    return _db(
        [
            _production_shaped("orgadmin", ["dms.document.view"]),
            _production_shaped("projectadmin", ["dms.document.view"]),
            _production_shaped("contractmgr_org", ["dms.document.view"]),
            _production_shaped("orguser", ["dms.document.view", "dms.document.edit_metadata"]),
            _production_shaped("projectuser", ["dms.document.view", "dms.document.edit_metadata"]),
            _production_shaped("superadmin", ["dms.document.view"]),
        ]
    )


async def test_the_alignment_adds_it_to_the_two_approved_roles_only() -> None:
    db = _production_without_review()
    untouched = {
        role_id: copy.deepcopy(db.roles.docs[role_id])
        for role_id in ("contractmgr_org", "orguser", "projectuser", "superadmin")
    }

    plan = await alignment.align(db, dry_run=True)
    for role_id in ("orgadmin", "projectadmin"):
        assert REVIEW in _row(plan, role_id)["additions"]
    assert {row["role_id"] for row in plan.operations} == {"orgadmin", "projectadmin"}

    await alignment.align(db, dry_run=False)
    for role_id in ("orgadmin", "projectadmin"):
        assert REVIEW in db.roles.docs[role_id]["permissions"]
    for role_id, before in untouched.items():
        assert db.roles.docs[role_id] == before, f"{role_id} was modified"
        assert REVIEW not in db.roles.docs[role_id]["permissions"]


async def test_the_alignment_is_idempotent() -> None:
    db = _production_without_review()
    await alignment.align(db, dry_run=False)
    writes = len(db.roles.writes)
    snapshot = copy.deepcopy(db.roles.docs)

    again = await alignment.align(db, dry_run=False)

    assert len(db.roles.writes) == writes, "the second apply wrote"
    assert db.roles.docs == snapshot
    assert all(REVIEW not in row["additions"] for row in again.operations)


async def test_the_owner_approved_grant_operation_does_not_hand_it_out() -> None:
    db = _production_without_review()
    await alignment.grant(db, dry_run=False)
    for role_id in ("contractmgr_org", "orguser", "projectuser", "superadmin"):
        assert REVIEW not in db.roles.docs[role_id]["permissions"]
