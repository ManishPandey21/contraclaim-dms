"""A created user may not be granted an organisation or project the caller does not hold.

AUDIT-005 (2026-10-25): `POST /api/users` forced `organization_id` to the caller's
organisation but copied `organizations` from the body. `ScopeService` and
`AuthorizationService` treat every entry of `organizations` as membership, so an
org admin of A could create a user with `organizations: [A, B]`, sign in as it,
and pass `PolicyService.authorize` for B. `projects` was likewise written without
checking that each project belongs to the caller's organisation (AUDIT-010).

This is the create-side twin of `test_foreign_org_move_refusal.py`. Refusal, not
clamping, for the same reason: the caller asked for something it may not have.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from rbac_backend.tests.test_foreign_org_move_refusal import (
    FOREIGN,
    FOREIGN_PROJECT,
    OWN,
    OWN_PROJECT,
    _actor,
    _controller,
    _UserService,
)
from rbac_backend.utils.error_handler import UserError


def _create(**fields):
    return SimpleNamespace(
        **{
            "roles": ["orguser"],
            "organization_id": None,
            "organizations": [],
            "projects": [],
            **fields,
        }
    )


def _context(actor, user_data):
    controller = _controller(_UserService(target=None))

    async def _allow(*args, **kwargs):
        return None

    # Role assignment is covered elsewhere; this file pins tenant fields only.
    controller.auth_service.validate_role_assignment = _allow
    controller._enforce_role_assignment_scope = _allow
    asyncio.run(controller._validate_user_context(user_data, actor))
    return user_data


@pytest.mark.parametrize(
    "organizations",
    [
        pytest.param([OWN, FOREIGN], id="own-plus-foreign"),
        pytest.param([FOREIGN], id="foreign-only"),
    ],
)
@pytest.mark.parametrize("actor_role", ["orgadmin", "projectadmin"])
def test_a_tenant_admin_cannot_grant_a_foreign_organization_on_create(organizations, actor_role) -> None:
    with pytest.raises(UserError) as exc:
        _context(_actor([actor_role]), _create(organization_id=OWN, organizations=organizations))
    assert exc.value.http_status == 403


def test_a_tenant_admin_cannot_assign_a_foreign_project_on_create() -> None:
    with pytest.raises(UserError) as exc:
        _context(
            _actor(["orgadmin"]),
            _create(roles=["projectuser"], organization_id=OWN, projects=[str(FOREIGN_PROJECT)]),
        )
    assert exc.value.http_status == 403


def test_a_tenant_admin_still_creates_a_user_in_its_own_organization() -> None:
    created = _context(_actor(["orgadmin"]), _create(organization_id=OWN, organizations=[OWN]))
    assert created.organization_id == OWN
    assert created.organizations == [OWN]


def test_a_tenant_admin_still_assigns_its_own_projects() -> None:
    created = _context(
        _actor(["orgadmin"]),
        _create(roles=["projectuser"], organization_id=OWN, projects=[str(OWN_PROJECT)]),
    )
    assert created.projects == [str(OWN_PROJECT)]


def test_an_omitted_organization_list_becomes_the_callers_organization() -> None:
    created = _context(_actor(["orgadmin"]), _create())
    assert created.organization_id == OWN
    assert created.organizations == [OWN]


def test_superadmin_may_still_assign_several_organizations() -> None:
    created = _context(
        _actor(["superadmin"], org=None),
        _create(roles=["superuser"], organizations=[OWN, FOREIGN]),
    )
    assert created.organizations == [OWN, FOREIGN]
