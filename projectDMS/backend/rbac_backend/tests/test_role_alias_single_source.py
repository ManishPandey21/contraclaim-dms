"""Role-name normalization must have exactly one source of truth (M-09).

``core.security``, ``services.authorization_service`` and
``services.permission_service`` each used to define their own ``ROLE_ALIASES``.
The maps had drifted -- 27 of 40 keys were missing from at least one module --
so the same stored role string normalized differently depending on which module
ran first, and a legitimately assigned role could silently fail to resolve
(``if not role: continue``) and be denied.

These tests fail if any module reintroduces a private alias table or a private
normalization rule.
"""

from __future__ import annotations

import pytest

from rbac_backend.core import security
from rbac_backend.core.permissions import (
    CANONICAL_ROLE_ALIASES,
    normalize_role_name,
    normalize_role_names,
)
from rbac_backend.services import authorization_service, permission_service

_MODULES = {
    "core.security": security,
    "services.authorization_service": authorization_service,
    "services.permission_service": permission_service,
}


def test_every_module_shares_the_canonical_alias_map() -> None:
    for name, module in _MODULES.items():
        assert module.ROLE_ALIASES is CANONICAL_ROLE_ALIASES, (
            f"{name} defines its own ROLE_ALIASES; import the canonical map instead"
        )


def test_no_module_normalizes_roles_differently() -> None:
    """All normalizers must agree on every alias and on unknown input."""

    probes = [
        *CANONICAL_ROLE_ALIASES,
        "Organisation Admin",
        "ORG_ADMIN",
        "proj  admin",
        "totally-unknown-role",
        "",
        None,
    ]
    for probe in probes:
        expected = normalize_role_name(probe)
        assert authorization_service._normalize_role_name(probe) == expected, probe
        assert permission_service._normalize_role_name(probe) == expected, probe


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("organization-admin", "orgadmin"),
        ("organisation-admin", "orgadmin"),
        ("organization_admin", "orgadmin"),
        ("Org Admin", "orgadmin"),
        ("proj-user", "projectuser"),
        ("project administrator", "projectadmin"),
        ("super admin", "superadmin"),
    ],
)
def test_known_spellings_resolve_to_canonical_roles(raw: str, expected: str) -> None:
    assert normalize_role_name(raw) == expected


def test_unknown_roles_are_never_escalated() -> None:
    """An unrecognised role must stay unrecognised, never become privileged."""

    for probe in ["admin", "root", "owner", "superadmin-x", "orgadminx", "sa"]:
        assert normalize_role_name(probe) not in {
            "superadmin",
            "superuser",
            "orgadmin",
            "projectadmin",
        }, probe


def test_role_list_normalization_deduplicates_and_preserves_order() -> None:
    assert normalize_role_names(["Org Admin", "organization-admin", "project user"]) == [
        "orgadmin",
        "projectuser",
    ]
    assert normalize_role_names([]) == []
    assert normalize_role_names(None) == []


def test_security_role_list_normalizer_matches_the_contract() -> None:
    assert security._normalize_roles_list(["Organisation Admin", "org-admin"]) == [
        "orgadmin"
    ]
