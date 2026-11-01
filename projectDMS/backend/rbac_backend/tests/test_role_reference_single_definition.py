"""One role-reference resolver: authorization and the audit cannot diverge again.

The R-A9B production-copy drill found three interpretations of one stored string
`organization-admin`: the principal's normaliser (org-admin tier), the permission
resolver's exact `_id` lookup (no document), and the audit (a WARN claiming it resolved).
These guards keep a single definition, `core/role_reference.py`:

* the audit's embedded copy is byte-identical to the module;
* the principal and every permission resolver reach role documents only through it;
* its two fetchers (one query per principal, one lookup chain per reference) hand it
  enough documents to decide exactly as it decides over the whole role collection;
* role aliases never touch the permission-alias graph, and the reverse.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.core import role_reference, security
from rbac_backend.services import permission_service

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
MODULE = BACKEND_ROOT / "core" / "role_reference.py"
AUDIT = REPO_ROOT / "scripts" / "system_role_audit.py"
START = "# >>> role_reference: verbatim copy of backend/rbac_backend/core/role_reference.py - never edit here\n"
END = "# <<< role_reference\n"


# --------------------------------------------------------------------------- #
# The audit carries the module, byte for byte
# --------------------------------------------------------------------------- #


def test_the_audit_embeds_the_module_verbatim() -> None:
    script = AUDIT.read_text(encoding="utf-8")
    assert script.count(START) == 1 and script.count(END) == 1, "the role_reference markers are missing or repeated"
    embedded = script.split(START, 1)[1].split(END, 1)[0]
    assert embedded == MODULE.read_text(encoding="utf-8"), (
        "scripts/system_role_audit.py no longer carries core/role_reference.py verbatim; "
        "replace everything between its role_reference markers with the module's source"
    )


def test_the_module_can_be_embedded() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "__future__", "a __future__ import cannot sit in the middle of the audit script"
            pytest.fail(f"role_reference must import only whole stdlib modules, found `from {node.module}`")
        if isinstance(node, ast.Import):
            assert all(alias.name in {"re"} for alias in node.names), "role_reference must stay standard-library only"


def test_the_audit_defines_no_resolution_rules_of_its_own() -> None:
    script = AUDIT.read_text(encoding="utf-8")
    outside = script.split(START, 1)[0] + script.split(END, 1)[1]
    for forbidden in ("ROLE_ALIASES", "by_name", "_normalize_roles_list", "def _scope", "def normalize"):
        assert forbidden not in outside, f"the audit re-implements resolution outside the embedded copy: {forbidden}"


# --------------------------------------------------------------------------- #
# Runtime authorization reaches role documents only through the resolver
# --------------------------------------------------------------------------- #


def _function(path: Path, name: str) -> ast.AST:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path.name} no longer defines {name}")


def _calls(node: ast.AST) -> set:
    names = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def test_the_principal_judges_references_with_the_resolver() -> None:
    calls = _calls(_function(BACKEND_ROOT / "core" / "security.py", "_without_revoked_roles"))
    assert {"fetch_role_documents", "resolve_role_reference"} <= calls


def test_every_permission_resolver_loads_roles_through_the_resolver() -> None:
    path = BACKEND_ROOT / "services" / "permission_service.py"
    assert "load_role_resolution" in _calls(_function(path, "_load_role"))
    for name in ("get_user_permissions", "get_effective_permission_names", "user_has_permission", "check_resource_access"):
        calls = _calls(_function(path, name))
        assert "_load_role" in calls, f"{name} no longer resolves role references through _load_role"


def test_role_documents_are_read_by_reference_only_inside_the_fetchers() -> None:
    """A `db.roles` read added to the authority path is a second interpretation."""
    allowed = {
        ("core/security.py", None),  # none: security reads roles only through fetch_role_documents
        ("services/permission_service.py", "load_role_resolution"),
        ("services/permission_service.py", "fetch_role_documents"),
        ("services/permission_service.py", "get_permissions_for_role"),  # a role id from the catalogue API, not a user reference
    }
    offenders = []
    for relative in ("core/security.py", "services/permission_service.py"):
        tree = ast.parse((BACKEND_ROOT / relative).read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in {"find_one", "find", "aggregate"}
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "roles"
                    and (relative, function.name) not in allowed
                ):
                    offenders.append(f"{relative}::{function.name}:{node.lineno}")
    # A nested function is walked twice (itself and its parent); report each site once.
    assert sorted(set(offenders)) == [], offenders


def test_there_is_one_principal_alias_table() -> None:
    assert security.ROLE_ALIASES is role_reference.ROLE_ALIASES
    tree = ast.parse((BACKEND_ROOT / "core" / "security.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ROLE_ALIASES" for t in node.targets):
            assert not isinstance(node.value, ast.Dict), "core/security.py grew its own role alias table again"
    assert security._normalize_roles_list(["organization-admin", "Project Admin", "super-admin"]) == [
        role_reference.normalize_role_key("organization-admin"),
        role_reference.normalize_role_key("Project Admin"),
        role_reference.normalize_role_key("super-admin"),
    ]


def test_role_aliases_and_permission_aliases_are_separate_graphs() -> None:
    identifiers = {
        getattr(node, "id", None) or getattr(node, "attr", None)
        for node in ast.walk(ast.parse(MODULE.read_text(encoding="utf-8")))
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    crossing = identifiers & {"equivalent_permissions", "LEGACY_PERMISSION_ALIASES", "LEGACY_LABEL_ALIASES", "permissions"}
    assert crossing == set(), f"the role resolver reads the permission-alias graph: {crossing}"
    permissions_source = (BACKEND_ROOT / "core" / "permissions.py").read_text(encoding="utf-8")
    assert "role_reference" not in permissions_source, "permission resolution now depends on role aliases"


# --------------------------------------------------------------------------- #
# Both fetchers decide exactly as the resolver does over the whole collection
# --------------------------------------------------------------------------- #


def _values(value: Any) -> List[Any]:
    return value if isinstance(value, list) else [value]


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, condition in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, branch) for branch in condition):
                return False
            continue
        value = doc.get(key)
        if isinstance(condition, dict):
            if "$in" in condition and not any(item == value for item in condition["$in"]):
                return False
            if "$ne" in condition and value == condition["$ne"]:
                return False
        elif value != condition:
            return False
    return True


class _Roles:
    """Type-exact matching, like MongoDB: a str never matches an ObjectId `_id`."""

    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, query, *_args, **_kwargs):
        return next((dict(doc) for doc in self.docs if _matches(doc, query)), None)

    def find(self, query=None, *_args, **_kwargs):
        docs = [dict(doc) for doc in self.docs if _matches(doc, query or {})]

        class _Cursor:
            async def to_list(self, length=None):
                return docs

        return _Cursor()


CUSTOM_ID = ObjectId()
RETIRED_ID = ObjectId()
COLLECTION = [
    {"_id": "superadmin", "name": "Super Admin"},
    {"_id": "orgadmin", "name": "Organization-Admin"},
    {"_id": "projectadmin", "name": "Project Admin", "is_active": False},
    {"_id": "orguser", "name": "Organization - User", "is_active": True},
    {"_id": CUSTOM_ID, "name": "Site Reviewer", "is_active": True},
    {"_id": RETIRED_ID, "name": "Retired", "is_active": False},
    {"_id": ObjectId(), "name": "organization user", "is_active": True},  # lookalike of an alias spelling
    {"_id": "reporter", "name": "Reporter", "is_active": True},
]
REFERENCES = [
    "superadmin", "super-admin", "Super Admin", "orgadmin", "organization-admin", "Organization Admin",
    "project-admin", "projectadmin", "organization user", "organization-user", "orguser",
    CUSTOM_ID, str(CUSTOM_ID), str(RETIRED_ID), "Site Reviewer", "xyzadmin", "proj-user", "", "  orgadmin  ",
    "Reporter", "REPORTER", "OrgAdmin", "SuperAdmin", "organisation-admin",
]


@pytest.mark.parametrize(
    "reference,status",
    [
        ("Reporter", role_reference.LEGACY_ALIAS),  # the principal's key; the target's own name is no lookalike
        ("REPORTER", role_reference.LEGACY_ALIAS),
        ("SuperAdmin", role_reference.SYSTEM_ALIAS),
        ("organisation-admin", role_reference.UNRESOLVED),  # only permission_service's display-name table knows it
        ("OrgAdmin", role_reference.LEGACY_ALIAS),
        ("reporter", role_reference.CANONICAL),
    ],
)
def test_a_reference_resolves_exactly_through_the_principals_key(reference, status) -> None:
    resolution = role_reference.resolve_role_reference(reference, COLLECTION)
    assert resolution.status == status, resolution
    if status == role_reference.UNRESOLVED and resolution.is_alias:
        assert not resolution.keeps_role_key, "an unresolved spelling that differs from its key must carry no tier"
    if reference == "organisation-admin":
        assert resolution.key == "organisation-admin", "a bare unknown spelling may only carry itself, never a tier key"


@pytest.mark.parametrize("reference", REFERENCES, ids=[repr(r) for r in REFERENCES])
def test_both_fetchers_resolve_like_the_whole_collection(reference) -> None:
    expected = role_reference.resolve_role_reference(reference, COLLECTION)
    db = type("DB", (), {"roles": _Roles(COLLECTION)})()

    chained = asyncio.run(permission_service.load_role_resolution(db, reference))
    batched = role_reference.resolve_role_reference(
        reference, asyncio.run(permission_service.fetch_role_documents(db, REFERENCES))
    )
    single = role_reference.resolve_role_reference(
        reference, asyncio.run(permission_service.fetch_role_documents(db, [reference]))
    )
    assert chained == expected, f"per-reference lookup: {chained} != {expected}"
    assert batched == expected, f"principal batch query: {batched} != {expected}"
    assert single == expected, f"principal single query: {single} != {expected}"


def test_the_parity_matrix_covers_every_outcome() -> None:
    outcomes = {role_reference.resolve_role_reference(ref, COLLECTION).status for ref in REFERENCES}
    assert outcomes == {
        role_reference.CANONICAL,
        role_reference.LEGACY_ALIAS,
        role_reference.SYSTEM_ALIAS,
        role_reference.INACTIVE,
        role_reference.AMBIGUOUS,
        role_reference.UNRESOLVED,
    }, outcomes


def test_a_store_error_while_resolving_is_raised_not_read_as_no_role() -> None:
    class _Broken:
        async def find_one(self, *_args, **_kwargs):
            raise RuntimeError("roles unreachable")

    db = type("DB", (), {"roles": _Broken()})()
    with pytest.raises(RuntimeError):
        asyncio.run(permission_service.load_role_resolution(db, "organization-admin"))
