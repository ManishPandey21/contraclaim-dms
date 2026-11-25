"""4B-07 (D-06, owner amendment B.2): what a reviewer assignment confers.

``reviewer_roles_from_assignments`` is the single rule. These tests state it
case by case, so a change that lets an organisation assignment, a project
assignment or a role name reach platform authority fails here by name.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from rbac_backend.core.permissions import (
    ARBITRATION_REVIEWER_ROLE_KEYS,
    REVIEWER_ROLE_KEYS,
    SKILL_REVIEWER_ROLE_KEYS,
    reviewer_assignment_is_live,
    reviewer_roles_from_assignments,
)
from rbac_backend.models.reviewer_assignment import REVIEWER_ROLE_LABELS, ReviewerAssignmentCreate
from rbac_backend.tests.reviewer_assignment_helpers import assignment_document

NOW = datetime(2026, 11, 17, 12, 0, 0)
LEGAL = "senior_legal_approver"


def _roles(assignments, scope_type, *, user_id="u1", organization_id=None, project_id=None):
    return reviewer_roles_from_assignments(
        assignments, user_id=user_id, scope_type=scope_type, organization_id=organization_id, project_id=project_id, now=NOW
    )


def _platform(**fields):
    return assignment_document("u1", LEGAL, **fields)


def _org(org="org-a", **fields):
    return assignment_document("u1", LEGAL, scope_type="organization", organization_id=org, **fields)


def _project(org="org-a", project="p1", **fields):
    return assignment_document("u1", LEGAL, scope_type="project", organization_id=org, project_id=project, **fields)


def test_every_reviewer_role_has_a_label_and_the_two_families_do_not_overlap() -> None:
    assert set(REVIEWER_ROLE_LABELS) == set(REVIEWER_ROLE_KEYS)
    assert not (SKILL_REVIEWER_ROLE_KEYS & ARBITRATION_REVIEWER_ROLE_KEYS)


def test_a_platform_assignment_gives_platform_authority() -> None:
    assert _roles([_platform()], "platform") == {LEGAL}


@pytest.mark.parametrize("held", [_org(), _project()])
def test_an_organisation_or_project_assignment_never_gives_platform_authority(held) -> None:
    assert _roles([held], "platform") == set()


def test_a_platform_assignment_does_not_answer_for_one_tenants_case() -> None:
    assert _roles([_platform()], "organization", organization_id="org-a") == set()
    assert _roles([_platform()], "project", organization_id="org-a", project_id="p1") == set()


def test_an_organisation_assignment_answers_for_its_organisation_and_its_projects_only() -> None:
    assert _roles([_org()], "organization", organization_id="org-a") == {LEGAL}
    assert _roles([_org()], "project", organization_id="org-a", project_id="p1") == {LEGAL}
    assert _roles([_org()], "organization", organization_id="org-b") == set()
    assert _roles([_org()], "project", organization_id="org-b", project_id="p9") == set()


def test_a_project_assignment_answers_for_that_project_only() -> None:
    assert _roles([_project()], "project", organization_id="org-a", project_id="p1") == {LEGAL}
    assert _roles([_project()], "project", organization_id="org-a", project_id="p2") == set()
    assert _roles([_project()], "organization", organization_id="org-a") == set()
    # The same project id under another organisation is another resource.
    assert _roles([_project()], "project", organization_id="org-b", project_id="p1") == set()


def test_an_organisation_scope_question_with_no_organisation_gets_nothing() -> None:
    assert _roles([_org()], "organization") == set()
    assert _roles([_project()], "project", project_id="p1") == set()


def test_another_persons_assignment_confers_nothing() -> None:
    assert _roles([_platform()], "platform", user_id="u2") == set()
    assert _roles([_platform()], "platform", user_id="") == set()


def test_a_revoked_assignment_confers_nothing() -> None:
    assert _roles([_platform(revoked_at=NOW - timedelta(seconds=1))], "platform") == set()


def test_an_expired_assignment_confers_nothing_and_an_unexpired_one_does() -> None:
    assert _roles([_platform(expires_at=NOW - timedelta(seconds=1))], "platform") == set()
    assert _roles([_platform(expires_at=NOW)], "platform") == set()
    assert _roles([_platform(expires_at=NOW + timedelta(seconds=1))], "platform") == {LEGAL}


def test_an_expiry_is_compared_in_utc_whether_or_not_it_carries_a_zone() -> None:
    aware_now = NOW.replace(tzinfo=timezone.utc)
    stored = {"revoked_at": None, "expires_at": NOW + timedelta(hours=1)}
    assert reviewer_assignment_is_live(stored, aware_now)
    ahead = timezone(timedelta(hours=5, minutes=30))
    assert not reviewer_assignment_is_live({"revoked_at": None, "expires_at": NOW.replace(tzinfo=ahead)}, aware_now)


def test_an_unreadable_expiry_is_not_an_open_ended_grant() -> None:
    assert not reviewer_assignment_is_live({"revoked_at": None, "expires_at": "2099-01-01"}, NOW)


def test_a_role_key_outside_the_reviewer_roles_is_ignored_even_if_stored() -> None:
    forged = assignment_document("u1", "superadmin")
    assert _roles([forged], "platform") == set()


def test_an_unknown_scope_type_gets_nothing() -> None:
    assert _roles([_platform()], "global") == set()
    odd = assignment_document("u1", LEGAL, scope_type="global")
    assert _roles([odd], "platform") == set()


# ----------------------------------------------------------------- request model


def _create(**fields):
    base = {"user_id": "u2", "role_key": LEGAL, "scope_type": "platform", "reason": "Appointed by the board"}
    return ReviewerAssignmentCreate(**{**base, **fields})


def test_a_platform_request_names_no_organisation_or_project() -> None:
    assert _create().scope_type == "platform"
    with pytest.raises(ValidationError):
        _create(organization_id="org-a")
    with pytest.raises(ValidationError):
        _create(project_id="p1")


def test_only_skill_reviewer_roles_are_held_at_platform_scope() -> None:
    with pytest.raises(ValidationError):
        _create(role_key="quantum_reviewer")
    assert _create(role_key="quantum_reviewer", scope_type="organization", organization_id="org-a").role_key


def test_an_organisation_request_names_one_organisation_and_no_project() -> None:
    with pytest.raises(ValidationError):
        _create(scope_type="organization")
    with pytest.raises(ValidationError):
        _create(scope_type="organization", organization_id="org-a", project_id="p1")


def test_a_project_request_names_a_project() -> None:
    with pytest.raises(ValidationError):
        _create(scope_type="project", organization_id="org-a")
    assert _create(scope_type="project", project_id="p1").project_id == "p1"


@pytest.mark.parametrize("role", ["superadmin", "orgadmin", "dms.admin", "", "Senior Legal"])
def test_a_request_for_anything_but_a_reviewer_role_is_refused(role) -> None:
    with pytest.raises(ValidationError):
        _create(role_key=role)


@pytest.mark.parametrize("reason", ["", "    ", "ok", "  x  "])
def test_a_grant_needs_a_reason(reason) -> None:
    with pytest.raises(ValidationError):
        _create(reason=reason)


def test_a_request_cannot_carry_fields_the_server_owns() -> None:
    for name in ("granted_by", "granted_at", "revoked_at", "active", "_id"):
        with pytest.raises(ValidationError):
            _create(**{name: "x"})
