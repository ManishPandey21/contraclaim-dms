"""4B-49 (AQ-12): the seed catalogue's inheritance matrix for dms.document.review_extraction.

The matrix is produced by code: every role in ``initial_data/default_roles.py`` is
classified by the same function reading P-15 uses
(``review_extraction_grant_paths`` in ``scripts/claim_arbitration_readonly_readings.py``).
That function is pinned against the real PolicyService on a real MongoDB in
``tests/integration/test_review_extraction_inheritance_mongo.py``.

The expected table below is the CURRENT behaviour, pinned on purpose. Closing the
inheritance (explicit-grant-only) is a behaviour change that needs the owner's approval
of a migration path (docs/RBAC_REVIEW_EXTRACTION_REMEDIATION.md); when it lands, this
table changes with it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Dict, FrozenSet

from bson import ObjectId

from rbac_backend.core.permissions import EXPLICIT_GRANT_ONLY_PERMISSIONS, SUPERADMIN_EXCLUDED_PERMISSIONS
from rbac_backend.core.role_reference import resolve_role_reference
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES

READER = Path(__file__).resolve().parents[3] / "scripts" / "claim_arbitration_readonly_readings.py"
PERMISSION = "dms.document.review_extraction"


def _reader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("claim_arbitration_readonly_readings_matrix", READER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Seed role -> the paths by which a user holding only that role passes the check today.
CURRENT_SEED_MATRIX: Dict[str, FrozenSet[str]] = {
    "superadmin": frozenset({"superadmin", "dms_admin"}),
    "orgadmin": frozenset({"named", "dms_admin"}),
    "contractmgr_org": frozenset({"dms_admin"}),
    "projectadmin": frozenset({"named", "dms_admin"}),
    "orguser": frozenset(),
    "projectuser": frozenset(),
    "doccontroller": frozenset(),
    "reporter": frozenset(),
    "settings_manager": frozenset(),
    "limited_user": frozenset(),
    "contraclaim_drafting_manager": frozenset(),
    "contraclaim_expert_drafter": frozenset(),
    "contraclaim_expert_reviewer": frozenset(),
    "contraclaim_billing_admin": frozenset(),
}


def seed_matrix() -> Dict[str, FrozenSet[str]]:
    reader = _reader()
    documents = [dict(role) for role in DEFAULT_ROLES]
    return {
        str(role["_id"]): reader.review_extraction_grant_paths(
            [role["_id"]], documents, set(), resolve_role_reference
        )
        for role in DEFAULT_ROLES
    }


def test_the_seed_inheritance_matrix_is_pinned_to_the_current_behaviour() -> None:
    assert seed_matrix() == CURRENT_SEED_MATRIX


def test_the_permission_is_explicit_grant_only_at_seeding_but_not_at_the_check() -> None:
    # The intent (release 1.1.1): granted by name to two roles only.
    assert PERMISSION in EXPLICIT_GRANT_ONLY_PERMISSIONS
    named = {role for role, paths in CURRENT_SEED_MATRIX.items() if "named" in paths}
    assert named == {"orgadmin", "projectadmin"}
    # The gap AQ-12 records: the check does not enforce it.
    assert PERMISSION not in SUPERADMIN_EXCLUDED_PERMISSIONS
    inherited_only = {role for role, paths in CURRENT_SEED_MATRIX.items() if paths and "named" not in paths}
    assert inherited_only == {"superadmin", "contractmgr_org"}


def test_a_role_alias_resolves_to_the_same_paths_as_its_canonical_role() -> None:
    reader = _reader()
    documents = [dict(role) for role in DEFAULT_ROLES]
    for alias, canonical in (("organization-admin", "orgadmin"), ("Project Admin", "projectadmin")):
        assert reader.review_extraction_grant_paths([alias], documents, set(), resolve_role_reference) == (
            CURRENT_SEED_MATRIX[canonical]
        )
    # A system alias keeps the Super Admin key but never reaches the system document.
    assert reader.review_extraction_grant_paths(["super-admin"], documents, set(), resolve_role_reference) == (
        frozenset({"superadmin"})
    )
    # Super User has no seed document, so no path.
    assert reader.review_extraction_grant_paths(["superuser"], documents, set(), resolve_role_reference) == frozenset()


def test_a_permission_document_can_spell_the_grant_another_way() -> None:
    reader = _reader()
    seeded = {"_id": PERMISSION, "name": "Review DMS Document Extraction"}
    by_object_id = {"_id": ObjectId("65f0000000000000000000aa"), "name": PERMISSION}
    unrelated = {"_id": "dms.document.view", "name": "View DMS Documents"}
    refs = reader.permission_document_references([seeded, by_object_id, unrelated], PERMISSION)
    assert refs == {"Review DMS Document Extraction", "65f0000000000000000000aa"}
    role = {"_id": "custom", "name": "custom", "permissions": ["Review DMS Document Extraction"]}
    assert reader.review_extraction_grant_paths(["custom"], [role], refs, resolve_role_reference) == (
        frozenset({"named_by_permission_document"})
    )


def test_a_deactivated_role_confers_no_path_not_even_the_super_admin_key() -> None:
    # A deactivated role is dropped from the principal (core.security), so a soft-deleted
    # Super Admin role no longer carries the shortcut, and its document grants nothing.
    reader = _reader()
    documents = [dict(role, is_active=False) if role["_id"] == "superadmin" else dict(role) for role in DEFAULT_ROLES]
    assert reader.review_extraction_grant_paths(["superadmin"], documents, set(), resolve_role_reference) == frozenset()
    named = [{"_id": "rx", "name": "rx", "permissions": [PERMISSION], "is_active": False}]
    assert reader.review_extraction_grant_paths(["rx"], named, set(), resolve_role_reference) == frozenset()
