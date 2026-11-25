"""4B-15 (D-11, C-14): the party a project acts for has one write path, an audit
trail and a lock. Real HTTP, real MongoDB replica set, real RBAC.

Before 4B-15 the position could be changed freely by two routes, with no audit
and no lock: flip a project to Engineer, author a determination, flip it back.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2

pytestmark = pytest.mark.integration

REASON = "The Employer has appointed us as its Engineer"


@pytest.fixture(autouse=True)
def _permission_service_reads_this_tests_database(monkeypatch):
    """The project routes keep module-level ``PermissionService`` objects, and each
    caches the first database it is given. Every scenario here has its own
    disposable database, so without this the second scenario in the process is
    answered from the first one's dropped database (CLAUDE.md, "module-level
    Motor globals must be cleared between tests"). Production has one database.
    """
    from rbac_backend.core import database as database_module
    from rbac_backend.services.permission_service import PermissionService

    async def current_database(self):  # noqa: ANN001
        return database_module.database

    monkeypatch.setattr(PermissionService, "_get_db", current_database)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


def _path(project: str = PROJ_A1) -> str:
    return f"/api/projects/{project}/contractual-capacity"


async def _set(env: Any, persona: str, value: str, *, project: str = PROJ_A1, reason: Any = None) -> Any:
    body = {"contractual_capacity": value}
    if reason is not None:
        body["reason"] = reason
    return await env.call("PATCH", persona, _path(project), project=project, json=body)


async def _stored(env: Any, project: str = PROJ_A1) -> Any:
    return (await env.db.projects.find_one({"_id": project})).get("contractual_capacity")


async def _audits(env: Any, action: str = "project.contractual_capacity.changed") -> list:
    return await env.db.audit_events.find({"action": action}).to_list(None)


async def _make_finding(env: Any, kind: str, project: str = PROJ_A1) -> None:
    """One finding made from the project's position, stored the way its service stores it."""
    from rbac_backend.services.claim_determinations import DETERMINATIONS
    from rbac_backend.services.claim_submission import SUBMISSIONS

    row = {"_id": f"{kind}-1", "claim_id": CLAIM, "organization_id": ORG_A, "project_id": project}
    if kind == "determination":
        await env.db[DETERMINATIONS].insert_one(row)
    elif kind == "submission":
        await env.db[SUBMISSIONS].insert_one(row)
    else:
        from rbac_backend.services.claim_privileged import PRIVILEGED  # the one module that names it

        await env.db[PRIVILEGED].insert_one({**row, "kind": "simulation"})


# ----------------------------------------------------------- one write path


def test_a_project_update_cannot_set_or_change_the_position(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert await _stored(env) == "contractor"
            body = {"name": "A1 renamed", "organization_id": ORG_A, "contractual_capacity": "engineer"}
            updated = await env.call("PUT", "org_admin", f"/api/projects/{PROJ_A1}", json=body)
            assert updated.status_code == 200, updated.text[:300]
            assert (await env.db.projects.find_one({"_id": PROJ_A1}))["name"] == "A1 renamed", "the rest is saved"
            assert await _stored(env) == "contractor"
            camel = {"name": "A1", "organization_id": ORG_A, "contractualCapacity": "employer"}
            assert (await env.call("PUT", "org_admin", f"/api/projects/{PROJ_A1}", json=camel)).status_code == 200
            assert await _stored(env) == "contractor"
            assert await _audits(env) == []

    _run(scenario())


def test_the_first_statement_needs_no_reason_and_is_audited(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert await _stored(env, PROJ_A2) is None
            first = await _set(env, "org_admin", "engineer", project=PROJ_A2)
            assert first.status_code == 200, first.text[:300]
            assert await _stored(env, PROJ_A2) == "engineer"
            (row,) = await _audits(env)
            assert row["actor_id"] == "user-org_admin" and row["project_id"] == PROJ_A2
            assert row["before"] == {"contractual_capacity": None, "effective": None}
            assert row["after"] == {"contractual_capacity": "engineer"}
            assert row["metadata"] == {"in_use": False}

    _run(scenario())


def test_a_change_needs_a_reason_and_records_before_after_and_why(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            for reason in (None, "", "too short"):
                refused = await _set(env, "org_admin", "engineer", reason=reason)
                assert refused.status_code == 422 and _code(refused) == "perspective_reason_required", refused.text[:200]
            assert await _stored(env) == "contractor" and await _audits(env) == []

            changed = await _set(env, "org_admin", "engineer", reason=REASON)
            assert changed.status_code == 200, changed.text[:300]
            assert changed.json()["contractual_capacity"] == "engineer"
            (row,) = await _audits(env)
            assert row["reason"] == REASON
            assert row["before"]["contractual_capacity"] == "contractor"
            assert row["after"]["contractual_capacity"] == "engineer"

            # Saying the same thing again changes nothing and is not an event.
            same = await _set(env, "org_admin", "engineer")
            assert same.status_code == 200 and len(await _audits(env)) == 1

    _run(scenario())


def test_an_unknown_position_or_an_extra_field_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert (await _set(env, "org_admin", "arbitrator", reason=REASON)).status_code == 422
            extra = await env.call(
                "PATCH", "org_admin", _path(), json={"contractual_capacity": "engineer", "reason": REASON, "in_use": False}
            )
            assert extra.status_code == 422
            assert await _stored(env) == "contractor"

    _run(scenario())


# ------------------------------------------------------------------ the lock


@pytest.mark.parametrize("kind", ["determination", "simulation", "submission"])
def test_once_a_finding_exists_only_an_organisation_admin_changes_it_with_a_reason(monkeypatch, kind: str) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _make_finding(env, kind)
            for persona in ("project_admin", "superadmin"):
                locked = await _set(env, persona, "engineer", reason=REASON)
                assert locked.status_code == 409 and _code(locked) == "perspective_locked", (persona, locked.text[:200])
            bare = await _set(env, "org_admin", "engineer")
            assert bare.status_code == 422 and _code(bare) == "perspective_reason_required"
            assert await _stored(env) == "contractor" and await _audits(env) == []

            changed = await _set(env, "org_admin", "engineer", reason=REASON)
            assert changed.status_code == 200, changed.text[:300]
            (row,) = await _audits(env)
            assert row["metadata"] == {"in_use": True} and row["reason"] == REASON

    _run(scenario())


def test_a_finding_on_another_project_does_not_lock_this_one(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _make_finding(env, "determination", project=PROJ_A2)
            changed = await _set(env, "project_admin", "engineer", reason=REASON)
            assert changed.status_code == 200, changed.text[:300]

    _run(scenario())


def test_the_flip_author_flip_back_sequence_is_stopped(monkeypatch) -> None:
    """The reproduction in C-14: Contractor project, flip to Engineer, make a finding, flip back."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert (await _set(env, "project_admin", "engineer", reason=REASON)).status_code == 200
            await _make_finding(env, "determination")
            back = await _set(env, "project_admin", "contractor", reason="Putting it back as it was")
            assert back.status_code == 409 and _code(back) == "perspective_locked"
            assert await _stored(env) == "engineer"

    _run(scenario())


# ------------------------------------------------- who is asked, who is not


def test_the_state_tells_a_screen_whether_this_person_may_set_it(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            async def state(persona: str, project: str = PROJ_A1) -> dict:
                response = await env.call("GET", persona, _path(project), project=project)
                assert response.status_code == 200, (persona, response.text[:200])
                return response.json()

            viewer = await state("project_user")
            assert viewer["value"] == "contractor" and viewer["source"] == "project"
            assert viewer["can_set"] is False and viewer["cannot_set_reason"]
            assert (await state("project_admin"))["can_set"] is True
            assert (await state("org_admin", PROJ_A2)) == {
                "value": None, "source": "none", "in_use": False, "can_set": True, "cannot_set_reason": None,
            }

            await _make_finding(env, "submission")
            locked = await state("project_admin")
            assert locked["in_use"] is True and locked["can_set"] is False
            assert "administrator of the organisation" in locked["cannot_set_reason"]
            assert (await state("org_admin"))["can_set"] is True
            assert (await state("superadmin"))["can_set"] is False

    _run(scenario())


def test_another_tenant_and_another_project_cannot_read_or_set_it(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            for persona in ("foreign_org_admin", "other_project_admin"):
                read = await env.call("GET", persona, _path())
                assert read.status_code in (403, 404), (persona, read.status_code)
                write = await _set(env, persona, "engineer", reason=REASON)
                assert write.status_code in (403, 404), (persona, write.status_code)
            viewer = await _set(env, "project_user", "engineer", reason=REASON)
            assert viewer.status_code == 403
            assert await _stored(env) == "contractor" and await _audits(env) == []
            anonymous = await env.client.patch(_path(), json={"contractual_capacity": "engineer"})
            assert anonymous.status_code == 401

    _run(scenario())


# ------------------------------------------------------ organisation default


def test_an_organisation_default_answers_for_a_project_that_states_none(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            path = f"/api/organizations/{ORG_A}/default-contractual-capacity"
            for persona in ("project_admin", "org_user", "foreign_org_admin", "superadmin"):
                refused = await env.call("PATCH", persona, path, json={"contractual_capacity": "engineer"})
                assert refused.status_code == 403, (persona, refused.status_code, refused.text[:200])
            abroad = await env.call(
                "PATCH", "org_admin", f"/api/organizations/{ORG_B}/default-contractual-capacity",
                json={"contractual_capacity": "engineer"},
            )
            assert abroad.status_code == 403

            done = await env.call("PATCH", "org_admin", path, json={"contractual_capacity": "engineer"})
            assert done.status_code == 200, done.text[:300]
            inherited = (await env.call("GET", "org_admin", _path(PROJ_A2), project=PROJ_A2)).json()
            assert inherited["value"] == "engineer" and inherited["source"] == "organisation_default"
            # A project that states its own is not changed by the default.
            own = (await env.call("GET", "org_admin", _path())).json()
            assert own["value"] == "contractor" and own["source"] == "project"
            (row,) = await _audits(env, "organization.default_contractual_capacity.changed")
            assert row["after"] == {"default_contractual_capacity": "engineer"}

            # Once a project relying on the default has a finding, the default changes only with a reason.
            await _make_finding(env, "determination", project=PROJ_A2)
            bare = await env.call("PATCH", "org_admin", path, json={"contractual_capacity": "employer"})
            assert bare.status_code == 422 and _code(bare) == "perspective_reason_required"
            reasoned = await env.call(
                "PATCH", "org_admin", path, json={"contractual_capacity": "employer", "reason": REASON}
            )
            assert reasoned.status_code == 200
            rows = await _audits(env, "organization.default_contractual_capacity.changed")
            assert rows[-1]["metadata"]["in_use"] is True and rows[-1]["reason"] == REASON

    _run(scenario())


def test_the_bench_reads_the_organisation_default_when_the_project_states_none(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$unset": {"contractual_capacity": ""}})
            opened = await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open")
            assert opened.status_code == 200 and opened.json()["perspective"] is None
            await env.call(
                "PATCH", "org_admin", f"/api/organizations/{ORG_A}/default-contractual-capacity",
                json={"contractual_capacity": "employer"},
            )
            board = await env.call("GET", "org_admin", f"/api/claims/{CLAIM}/bench")
            assert board.status_code == 200 and board.json()["perspective"] == "employer"

    _run(scenario())


def test_a_super_admin_who_is_also_listed_as_an_organisation_admin_is_still_refused(monkeypatch) -> None:
    """The platform does not decide which party a tenant's project acts for."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.users.update_one(
                {"_id": "user-superadmin"},
                {"$set": {"roles": ["superadmin", "orgadmin"], "organization_id": ORG_A, "organizations": [ORG_A]}},
            )
            await _make_finding(env, "determination")
            locked = await _set(env, "superadmin", "engineer", reason=REASON)
            assert locked.status_code == 409 and _code(locked) == "perspective_locked", locked.text[:200]
            default = await env.call(
                "PATCH", "superadmin", f"/api/organizations/{ORG_A}/default-contractual-capacity",
                json={"contractual_capacity": "engineer"},
            )
            assert default.status_code == 403, default.text[:200]

    _run(scenario())
