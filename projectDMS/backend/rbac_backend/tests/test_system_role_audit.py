"""The pre-deploy system-role audit fails on exactly what ADR 0001 says the control is.

Owner decisions (2026-09-15): Super User is dormant (Q1), Super Admin is name-based
(Q2/ADR 0001), 2 holders is the approved production count and any other count is a
stop (Q14), and the audit is a hard gate in `pre_deploy_readiness.sh` (Q15). Owner
contract (D2): a reference is classified by the resolver authorization uses
(`core/role_reference.py`). Only a legacy spelling that really resolves one hop to an
active, non-system canonical document may WARN; an unresolved, ambiguous, deactivated
or system-alias reference FAILS, and so does any disagreement with the running image.

The baseline below is the production shape measured read-only that day: a
`superadmin` document with no lifecycle/scope metadata, 9 users, and three legacy
alias references (`organization-admin` x2, `project-admin` x1). Each failure test
starts from that passing baseline, so it proves one rule rather than a broken fixture.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.core import role_reference

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "system_role_audit.py"
READINESS = REPO_ROOT / "scripts" / "pre_deploy_readiness.sh"

_spec = importlib.util.spec_from_file_location("system_role_audit", SCRIPT)
audit = importlib.util.module_from_spec(_spec)
# `dataclasses` resolves annotations through sys.modules, so the module must be registered first.
sys.modules.setdefault("system_role_audit", audit)
_spec.loader.exec_module(audit)

REVIEWER_ID = ObjectId()


def _roles() -> List[Dict[str, Any]]:
    return [
        {"_id": "superadmin", "name": "Super Admin"},
        {"_id": "orgadmin", "name": "Organization Admin", "scope": "organization", "is_system": True, "is_active": True},
        {"_id": "orguser", "name": "Organization - User", "scope": "organization", "is_system": True, "is_active": True},
        {"_id": "projectadmin", "name": "Project Admin", "scope": "project", "is_system": True, "is_active": True},
        {"_id": "projectuser", "name": "Project - User", "scope": "project", "is_system": True, "is_active": True},
        {"_id": REVIEWER_ID, "name": "Site Reviewer", "scope": "organization", "organization_id": "org1",
         "is_system": False, "is_active": True},
    ]


def _users() -> List[tuple]:
    return [
        (["superadmin"], False),
        (["superadmin"], False),
        (["orgadmin"], False),
        (["projectadmin"], False),
        (["projectuser"], False),
        (["projectuser"], False),
        (["organization-admin"], False),
        (["organization-admin"], False),
        (["project-admin"], False),
    ]


def _evaluate(roles=None, users=None, expected=2, runtime_resolve=None):
    return audit.evaluate(
        _roles() if roles is None else roles,
        _users() if users is None else users,
        expected,
        runtime_resolve,
    )


def _failed(report, fragment: str) -> bool:
    return any(fragment in line for line in report.failures)


# --------------------------------------------------------------------------- #
# Baseline: production as measured passes, with the alias spellings as warnings
# --------------------------------------------------------------------------- #


def test_the_measured_production_shape_passes_with_alias_warnings() -> None:
    report = _evaluate()

    assert report.failures == [], report.lines()
    assert report.exit_code == audit.EXIT_OK
    assert any("Super Admin holders = 2 (2 enabled, 0 disabled)" in line for line in report.passes)
    warning = " ".join(report.warnings)
    assert "authorization resolves to their active canonical role (tier and permissions)" in warning
    assert "organization-admin -> orgadmin x2" in warning
    assert "project-admin -> projectadmin x1" in warning


# --------------------------------------------------------------------------- #
# Each stop condition, from the passing baseline
# --------------------------------------------------------------------------- #


def test_a_super_admin_holder_count_other_than_the_approved_one_fails() -> None:
    assert _failed(_evaluate(users=_users() + [(["superadmin"], False)]), "Super Admin holders = 3")
    assert _failed(_evaluate(expected=1), "approved = 1")


def test_a_disabled_super_admin_still_counts_as_a_holder() -> None:
    users = _users() + [(["superadmin"], True)]
    report = _evaluate(users=users)
    assert _failed(report, "Super Admin holders = 3 (2 enabled, 1 disabled)")


def test_a_missing_superadmin_document_fails() -> None:
    roles = [doc for doc in _roles() if doc["_id"] != "superadmin"]
    report = _evaluate(roles=roles, users=[u for u in _users() if u[0] != ["superadmin"]], expected=0)
    assert _failed(report, "no 'superadmin' role document exists")


def test_a_deactivated_superadmin_document_fails() -> None:
    roles = _roles()
    roles[0]["is_active"] = False
    assert _failed(_evaluate(roles=roles), "'superadmin' role document is deactivated")


@pytest.mark.parametrize("spelling", ["superuser", "Super User", "super-user"])
def test_any_super_user_holder_fails(spelling: str) -> None:
    assert _failed(_evaluate(users=_users() + [([spelling], False)]), "Super User role reference")


def test_an_alias_of_super_admin_fails_even_though_it_resolves() -> None:
    report = _evaluate(users=_users() + [(["super-admin"], False)], expected=3)
    assert _failed(report, "super-admin -> superadmin (alias of a system role)")


def test_an_unresolved_reference_fails() -> None:
    assert _failed(_evaluate(users=_users() + [(["xyzadmin"], False)]), "no role document: xyzadmin x1")


def test_a_legacy_spelling_whose_canonical_document_is_gone_fails() -> None:
    roles = [doc for doc in _roles() if doc["_id"] != "orgadmin"]
    report = _evaluate(roles=roles, users=[u for u in _users() if u[0] != ["orgadmin"]])
    assert _failed(report, "no role document: organization-admin x2")
    assert not any("organization-admin" in line for line in report.warnings)


def test_a_reference_matching_two_documents_fails() -> None:
    roles = _roles() + [{"_id": ObjectId(), "name": "organization-admin", "scope": "organization", "is_active": True}]
    report = _evaluate(roles=roles)
    assert _failed(report, "matching more than one role document: organization-admin -> orgadmin")
    assert not any("organization-admin" in line for line in report.warnings)


def test_a_reference_stored_by_display_name_resolves_to_nothing() -> None:
    """Authorization never looks a stored reference up by name, so neither does the audit."""
    assert _failed(_evaluate(users=_users() + [(["Site Reviewer"], False)]), "no role document: Site Reviewer x1")


def test_a_reference_to_a_deactivated_role_fails() -> None:
    roles = _roles()
    retired = ObjectId()
    roles.append({"_id": retired, "name": "Retired", "scope": "organization", "is_active": False})
    report = _evaluate(roles=roles, users=_users() + [([str(retired)], False)])
    assert _failed(report, f"deactivated roles (they grant nothing): {retired} -> {retired} x1")


def test_a_legacy_spelling_of_a_deactivated_role_fails_instead_of_warning() -> None:
    roles = _roles()
    roles[1]["is_active"] = False
    report = _evaluate(roles=roles)
    assert _failed(report, "organization-admin -> orgadmin x2")
    assert not any("organization-admin" in line for line in report.warnings)


def test_canonical_ids_and_custom_role_ids_pass_without_warnings() -> None:
    users = [(["superadmin"], False), (["superadmin"], False), (["orguser", str(REVIEWER_ID)], False)]
    report = _evaluate(users=users)
    assert report.failures == [] and report.warnings == [], report.lines()


# --------------------------------------------------------------------------- #
# The audit and the running image cannot disagree
# --------------------------------------------------------------------------- #


def test_without_the_module_in_the_image_the_audit_says_whose_resolver_it_used() -> None:
    report = _evaluate()
    assert any("no core.role_reference and may not resolve legacy spellings" in line for line in report.notes)
    assert report.lines()[0].startswith("NOTE:")
    warning = " ".join(report.warnings)
    assert "that the candidate's authorization resolves" in warning, (
        "inside an image that predates the resolver the audit must not claim the running authorization resolves them"
    )


def test_the_running_image_resolving_identically_passes() -> None:
    report = _evaluate(runtime_resolve=role_reference.resolve_role_reference)
    assert report.failures == [], report.lines()
    assert any("resolves every reference exactly as this audit does" in line for line in report.passes)


def test_a_running_image_that_resolves_a_reference_differently_fails() -> None:
    def _exact_id_only(reference, documents):
        """The pre-fix runtime: a legacy spelling loads nothing."""
        resolution = role_reference.resolve_role_reference(reference, documents)
        if resolution.status == role_reference.LEGACY_ALIAS:
            return role_reference.RoleResolution(resolution.reference, resolution.key, role_reference.UNRESOLVED)
        return resolution

    report = _evaluate(runtime_resolve=_exact_id_only)
    assert _failed(report, "resolve references differently: organization-admin: audit legacy_alias, runtime unresolved x2")


def test_main_resolves_with_the_image_module_when_it_exists(monkeypatch, capsys) -> None:
    async def _collected():
        return _roles(), _users()

    monkeypatch.setattr(audit, "_collect_from_application", _collected)
    assert audit.main(["--expected-superadmin-holders", "2"]) == audit.EXIT_OK
    output = capsys.readouterr().out
    assert "PASS: the running image resolves every reference exactly as this audit does" in output


# --------------------------------------------------------------------------- #
# What it reads and prints
# --------------------------------------------------------------------------- #


class _Cursor:
    def __init__(self, docs):
        self._docs = iter(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._docs)
        except StopIteration:
            raise StopAsyncIteration


class _Collection:
    def __init__(self, docs):
        self.docs = docs
        self.projections: List[Dict[str, Any]] = []

    def find(self, query, projection):
        self.projections.append(projection)
        keep = {key for key, on in projection.items() if on}
        return _Cursor([{k: v for k, v in doc.items() if k in keep or (k == "_id" and projection.get("_id", 1))}
                        for doc in self.docs])


def test_the_output_never_carries_user_identity() -> None:
    planted_email = "planted.holder@example.com"
    planted_id = ObjectId()
    users = [
        {"_id": planted_id, "email": planted_email, "username": "planted", "roles": ["superadmin"]},
        {"_id": ObjectId(), "email": "second@example.com", "roles": ["superadmin"], "disabled": False},
    ]
    db = type("DB", (), {"roles": _Collection(_roles()), "users": _Collection(users)})()
    assert planted_email in str(db.users.docs), "positive control: the identity is present in the store"

    roles, collected = asyncio.run(audit.collect(db))
    output = "\n".join(audit.evaluate(roles, collected, 2).lines())

    assert "Super Admin holders = 2" in output, "positive control: the planted holders were counted"
    assert planted_email not in output and str(planted_id) not in output and "planted" not in output
    assert db.users.projections == [{"roles": 1, "disabled": 1, "_id": 0}]


def test_the_audit_reads_every_field_the_resolver_decides_on() -> None:
    db = type("DB", (), {"roles": _Collection(_roles()), "users": _Collection([])})()
    asyncio.run(audit.collect(db))
    projection = db.roles.projections[0]
    for decided_on in ("name", "is_active", "scope"):
        assert projection.get(decided_on) == 1, f"the resolver reads `{decided_on}` but the audit does not collect it"


def test_an_unreadable_role_store_is_a_failure_that_does_not_leak_the_error(monkeypatch, capsys) -> None:
    from rbac_backend.core import database

    async def _broken():
        raise RuntimeError("mongodb://audit:hunter2@mongo1:27017/contraclaim unreachable")

    monkeypatch.setattr(database, "get_database", _broken)

    assert audit.main(["--expected-superadmin-holders", "2"]) == audit.EXIT_UNEVALUATED
    output = capsys.readouterr().out
    assert "FAIL: the system-role audit could not read the role store (RuntimeError)" in output
    assert "hunter2" not in output and "mongodb://" not in output


def test_the_approved_count_is_required() -> None:
    with pytest.raises(SystemExit):
        audit.main([])


# --------------------------------------------------------------------------- #
# The deploy gate runs it, inside the backend container, and fails closed
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def gate_block() -> str:
    script = READINESS.read_text(encoding="utf-8")
    match = re.search(r"expected_superadmin_holders=.*?\nfi\n", script, re.S)
    assert match, "pre_deploy_readiness.sh no longer runs the system-role audit"
    return match.group(0)


def test_the_readiness_gate_runs_the_audit_in_the_backend_container_on_stdin(gate_block: str) -> None:
    assert re.search(r"exec -T backend\s*\\?\s*python - --expected-superadmin-holders", gate_block)
    assert '<"$ROOT_DIR/scripts/system_role_audit.py"' in gate_block


def test_the_readiness_gate_fails_without_an_approved_count_and_on_any_audit_failure(gate_block: str) -> None:
    lines = [line.strip() for line in gate_block.splitlines()]
    assert any(line.startswith('fail "EXPECTED_SUPERADMIN_HOLDERS') for line in lines), (
        "a missing approved count must fail the gate"
    )
    assert lines[-3].startswith("else") and lines[-2].startswith('fail "System-role audit'), (
        "a non-zero audit exit (1 = failure, 2 = could not read) must fail the gate"
    )
    assert "warn " not in gate_block, "the gate must not downgrade an audit failure to a warning"
