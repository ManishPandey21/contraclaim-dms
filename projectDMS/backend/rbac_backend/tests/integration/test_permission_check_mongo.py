"""POST /api/permissions/check: your own permissions freely, another user's only as a scoped administrator.

Before (pre-existing, found in the SEC review of 2026-11-19): any signed-in user could
send any ``user_id`` and learn whether that user, in any organisation, held any
permission. Now:

- a self-check is unchanged (the client's ``useHasPermission`` sends only
  ``{"permission": ...}`` and reads ``granted``);
- another user's check needs ``roles:read`` in a named organisation or project
  (Super Admin may omit it), the target must be a member of that scope and visible
  under the user-read rules, and a hidden user answers exactly like a missing one;
- every other-user check, allowed or refused, writes an audit event.

Real HTTP through the production ``app`` on a disposable replica set; nothing overridden.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import (
    MANAGE,
    ROLE_A,
    ROLE_B,
    ROLE_P,
    VIEW,
    _seed_roles,
    _user,
)
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    ORG_A,
    ORG_B,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    _token,
)

pytestmark = pytest.mark.integration

PATH = "/api/permissions/check"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _check(env: Any, persona: str, body: Dict[str, Any]) -> Any:
    return await env.client.post(PATH, headers={"Authorization": f"Bearer {_token(persona)}"}, json=body)


async def _audits(env: Any) -> List[Dict[str, Any]]:
    return await env.db.audit_events.find({"action": "permission.check.other_user"}).sort("created_at", 1).to_list(
        length=None
    )


async def _targets(env: Any) -> Dict[str, str]:
    await _seed_roles(env.db)
    return {
        "a_member": await _user(env.db, "chk_a_member", [ROLE_A], organization_id=ORG_A),
        "a1_member": await _user(env.db, "chk_a1_member", [ROLE_P], organization_id=ORG_A, projects=[PROJ_A1]),
        "a2_member": await _user(env.db, "chk_a2_member", [ROLE_A], organization_id=ORG_A, projects=[PROJ_A2]),
        "b_member": await _user(env.db, "chk_b_member", [ROLE_B], organization_id=ORG_B, projects=[PROJ_B1]),
        "dual": await _user(env.db, "chk_dual", [ROLE_A, ROLE_B], organization_id=ORG_A, organizations=[ORG_A, ORG_B]),
        "disabled": await _user(env.db, "chk_disabled", [ROLE_A], organization_id=ORG_A, disabled=True),
    }


def test_a_self_check_is_unchanged_for_every_member(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            for persona, permission, expected in [
                ("org_user", "users:read", True),
                ("org_user", "roles:create", False),
                ("project_user", "users:read", True),
                ("org_admin", "roles:read", True),
            ]:
                response = await _check(env, persona, {"permission": permission})
                assert response.status_code == 200, response.text
                body = response.json()
                assert body == {"granted": expected, "permission": permission, "user_id": f"user-{persona}"}, body
                # Naming yourself explicitly is still a self-check.
                again = await _check(env, persona, {"permission": permission, "user_id": f"user-{persona}"})
                assert again.status_code == 200 and again.json()["granted"] is expected
            assert await _audits(env) == [], "self-checks are not administrative queries"
            missing = await _check(env, "org_user", {})
            assert missing.status_code == 400

    _run(scenario())


def test_a_member_without_roles_read_cannot_check_a_colleague(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            targets = await _targets(env)
            for persona in ("org_user", "project_user"):
                response = await _check(
                    env, persona, {"permission": VIEW, "user_id": targets["a_member"], "organization_id": ORG_A}
                )
                assert response.status_code == 403, (persona, response.text)
                assert "granted" not in response.text
            events = await _audits(env)
            assert [(e["result"], e["reason"]) for e in events] == [("deny", "not_authorized")] * 2

    _run(scenario())


def test_an_administrator_must_name_a_scope(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            targets = await _targets(env)
            response = await _check(env, "org_admin", {"permission": VIEW, "user_id": targets["a_member"]})
            assert response.status_code == 422, response.text
            events = await _audits(env)
            assert len(events) == 1 and events[0]["reason"] == "scope_required"
            assert events[0]["actor_id"] == "user-org_admin" and events[0]["resource_id"] == targets["a_member"]

    _run(scenario())


def test_an_organisation_administrator_checks_members_of_their_organisation_in_scope(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            targets = await _targets(env)
            ok = await _check(env, "org_admin", {"permission": VIEW, "user_id": targets["a_member"], "organization_id": ORG_A})
            assert ok.status_code == 200, ok.text
            assert ok.json() == {
                "granted": True, "permission": VIEW, "user_id": targets["a_member"],
                "organization_id": ORG_A, "project_id": None, "account_active": True,
            }
            # The answer is the target's permission in the named scope: the dual member's B role
            # does not count in A.
            dual = await _check(env, "org_admin", {"permission": MANAGE, "user_id": targets["dual"], "organization_id": ORG_A})
            assert dual.status_code == 200 and dual.json()["granted"] is False
            # A project role counts in its project, not organisation-wide (R-3).
            org_wide = await _check(env, "org_admin", {"permission": MANAGE, "user_id": targets["a1_member"], "organization_id": ORG_A})
            in_project = await _check(env, "org_admin", {"permission": MANAGE, "user_id": targets["a1_member"], "project_id": PROJ_A1})
            assert org_wide.json()["granted"] is False and in_project.json()["granted"] is True
            assert in_project.json()["organization_id"] == ORG_A, "the project's own organisation"
            # A disabled account holds nothing.
            disabled = await _check(env, "org_admin", {"permission": VIEW, "user_id": targets["disabled"], "organization_id": ORG_A})
            assert disabled.json()["granted"] is False and disabled.json()["account_active"] is False
            events = await _audits(env)
            assert [(e["result"], e["metadata"]["granted"]) for e in events] == [
                ("allow", True), ("allow", False), ("allow", False), ("allow", True), ("allow", False),
            ]
            assert all(e["metadata"]["permission"] in {VIEW, MANAGE} for e in events)

    _run(scenario())


def test_no_cross_tenant_enumeration(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            targets = await _targets(env)
            # A's administrator naming B: refused at the scope gate.
            other_org = await _check(env, "org_admin", {"permission": MANAGE, "user_id": targets["b_member"], "organization_id": ORG_B})
            assert other_org.status_code == 403, other_org.text
            # Naming A for B's member, or a user that does not exist: the same 404, the same body.
            hidden = await _check(env, "org_admin", {"permission": MANAGE, "user_id": targets["b_member"], "organization_id": ORG_A})
            absent = await _check(env, "org_admin", {"permission": MANAGE, "user_id": "64b000000000000000000000", "organization_id": ORG_A})
            assert hidden.status_code == absent.status_code == 404
            assert hidden.json() == absent.json()
            # B's administrator naming A: refused at the scope gate.
            foreign = await _check(env, "foreign_org_admin", {"permission": VIEW, "user_id": targets["a_member"], "organization_id": ORG_A})
            assert foreign.status_code == 403
            # A project of B named with A: the request's scope does not resolve.
            mismatch = await _check(
                env, "org_admin",
                {"permission": VIEW, "user_id": targets["a_member"], "organization_id": ORG_A, "project_id": PROJ_B1},
            )
            assert mismatch.status_code == 403, "an unresolvable scope answers like a refused one"
            # A project of B on its own resolves to B, where A's administrator has no scope.
            b_project = await _check(env, "org_admin", {"permission": MANAGE, "user_id": targets["b_member"], "project_id": PROJ_B1})
            assert b_project.status_code == 403
            reasons = [e["reason"] for e in await _audits(env)]
            assert reasons == [
                "not_authorized", "target_not_visible", "target_not_visible", "not_authorized",
                "scope_unresolved", "not_authorized",
            ]

    _run(scenario())


def test_a_project_administrator_sees_only_their_project(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            targets = await _targets(env)
            own = await _check(env, "project_admin", {"permission": MANAGE, "user_id": targets["a1_member"], "project_id": PROJ_A1})
            assert own.status_code == 200 and own.json()["granted"] is True, own.text
            # Another project of the same organisation: not their scope.
            other = await _check(env, "project_admin", {"permission": VIEW, "user_id": targets["a2_member"], "project_id": PROJ_A2})
            assert other.status_code == 403, other.text
            # Naming the organisation does not widen it: the target shares no project with them.
            widened = await _check(env, "project_admin", {"permission": VIEW, "user_id": targets["a2_member"], "organization_id": ORG_A})
            assert widened.status_code == 403, widened.text
            # A member of A1 asked about in A2 is not a member of A2.
            not_member = await _check(env, "other_project_admin", {"permission": MANAGE, "user_id": targets["a1_member"], "project_id": PROJ_A2})
            assert not_member.status_code == 404, not_member.text
            # The organisation's administrator may name any of its projects, but the answer
            # is only given for a member of the project named.
            org_admin_wrong_project = await _check(
                env, "org_admin", {"permission": MANAGE, "user_id": targets["a1_member"], "project_id": PROJ_A2}
            )
            assert org_admin_wrong_project.status_code == 404, org_admin_wrong_project.text

    _run(scenario())


def test_super_admin_may_check_anyone_and_is_audited(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            targets = await _targets(env)
            unscoped = await _check(env, "superadmin", {"permission": MANAGE, "user_id": targets["b_member"]})
            assert unscoped.status_code == 200, unscoped.text
            # Unscoped, a custom role counts in the user's own organisation (R-4).
            assert unscoped.json()["granted"] is True
            scoped = await _check(env, "superadmin", {"permission": MANAGE, "user_id": targets["b_member"], "organization_id": ORG_A})
            assert scoped.status_code == 404, "not a member of the named organisation"
            # A string user id (DEF-5 data) is found as stored.
            await env.db.users.insert_one(
                {"_id": "legacy-string-id", "email": "legacy@example.com", "roles": [ROLE_A],
                 "organization_id": ORG_A, "organizations": [ORG_A], "projects": [], "disabled": False}
            )
            legacy = await _check(env, "superadmin", {"permission": VIEW, "user_id": "legacy-string-id", "organization_id": ORG_A})
            assert legacy.status_code == 200 and legacy.json()["granted"] is True, legacy.text
            events = await _audits(env)
            assert [(e["actor_id"], e["result"]) for e in events] == [
                ("user-superadmin", "allow"), ("user-superadmin", "deny"), ("user-superadmin", "allow"),
            ]

    _run(scenario())


# --------------------------------------------------------------------------- #
# Independent review of 2026-11-19 (findings 1, 3, 4, 6, 7)
# --------------------------------------------------------------------------- #


def test_no_project_existence_oracle(monkeypatch) -> None:
    """Finding 1: the scope was resolved before the caller's authority was checked, so any
    member could tell a missing project (404) from another tenant's project (403)."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            targets = await _targets(env)
            answers = set()
            for persona in ("org_user", "org_admin"):
                for body in (
                    {"project_id": "no-such-project"},
                    {"project_id": PROJ_B1},
                    {"project_id": PROJ_B1, "organization_id": ORG_A},
                    {"project_id": "no-such-project", "organization_id": ORG_A},
                ):
                    response = await _check(env, persona, {"permission": VIEW, "user_id": targets["b_member"], **body})
                    answers.add((persona, response.status_code, response.text))
            for persona in ("org_user", "org_admin"):
                mine = {(code, text) for who, code, text in answers if who == persona}
                assert len(mine) == 1, (persona, mine)
                assert next(iter(mine))[0] == 403

    _run(scenario())


def test_a_project_administrator_by_membership_row_cannot_reach_the_whole_organisation(monkeypatch) -> None:
    """Finding 3: project assignment held only in project_memberships (not users.projects)."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            import time

            import jwt  # type: ignore[import-untyped]

            from rbac_backend.core.config import settings

            targets = await _targets(env)
            admin = await _user(env.db, "chk_row_admin", ["projectadmin"], organization_id=ORG_A, projects=[])
            await env.db.project_memberships.insert_one({"user_id": admin, "project_id": PROJ_A1, "status": "active"})
            now = int(time.time())
            token = jwt.encode({"sub": "chk_row_admin@example.com", "type": "access", "iat": now, "exp": now + 3600},
                               settings.SECRET_KEY, algorithm=settings.ALGORITHM)
            response = await env.client.post(
                PATH, headers={"Authorization": f"Bearer {token}"},
                json={"permission": VIEW, "user_id": targets["a2_member"], "organization_id": ORG_A},
            )
            assert response.status_code == 403, response.text
            reasons = [e["reason"] for e in await _audits(env)]
            assert reasons[-1] == "organization_wide_scope_required"

    _run(scenario())


def test_the_target_is_evaluated_as_a_request_would_see_them(monkeypatch) -> None:
    """Finding 4: a soft-deleted superadmin role must not report Super Admin authority."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _targets(env)
            # Asked by A's administrator (revoking the role below also ends the asking
            # Super Admin's own authority).
            ghost = await _user(env.db, "chk_ghost", ["superadmin"], organization_id=ORG_A)
            body = {"permission": MANAGE, "user_id": ghost, "organization_id": ORG_A}
            before = await _check(env, "org_admin", body)
            assert before.status_code == 200 and before.json()["granted"] is True, before.text
            await env.db.roles.update_one({"_id": "superadmin"}, {"$set": {"is_active": False, "deleted": True}})
            revoked = await _check(env, "org_admin", body)
            assert revoked.status_code == 200 and revoked.json()["granted"] is False, revoked.text

    _run(scenario())


def test_an_id_stored_in_both_forms_is_never_answered(monkeypatch) -> None:
    """Finding 6: a 24-hex string id and an ObjectId user with the same hex are two users."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from bson import ObjectId

            hex_id = "64b0000000000000000000bb"
            for key, persona in ((hex_id, "chk_twin_str"), (ObjectId(hex_id), "chk_twin_oid")):
                await env.db.users.insert_one({"_id": key, "email": f"{persona}@example.com", "roles": [ROLE_A],
                                               "organization_id": ORG_A, "organizations": [ORG_A], "projects": [], "disabled": False})
            await _seed_roles(env.db)
            response = await _check(env, "superadmin", {"permission": VIEW, "user_id": hex_id, "organization_id": ORG_A})
            assert response.status_code == 404, response.text

    _run(scenario())


def test_rate_limited_checks_are_audited(monkeypatch) -> None:
    """Finding 7: a refusal by the limiter is an administrative query too."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.routers import permissions as permissions_router

            targets = await _targets(env)
            from rbac_backend.utils.rate_limiter import RateLimiter

            monkeypatch.setattr(
                permissions_router, "permission_check_limiter",
                RateLimiter(max_requests=1, window_seconds=60, scope="permission_check_other_user_test"),
            )
            body = {"permission": VIEW, "user_id": targets["a_member"], "organization_id": ORG_A}
            first = await _check(env, "org_admin", body)
            second = await _check(env, "org_admin", body)
            assert first.status_code == 200 and second.status_code == 429, (first.text, second.text)
            assert [e["reason"] for e in await _audits(env)] == ["checked", "rate_limited"]

    _run(scenario())


def test_the_existing_user_read_rule_still_applies(monkeypatch) -> None:
    """A member of A whose own organisation is B is not readable by A's administrator under
    the user-read rule (GET /api/users/{id}); the check answers the same way."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_roles(env.db)
            guest = await _user(env.db, "chk_guest", [ROLE_A], organization_id=ORG_B, organizations=[ORG_B, ORG_A])
            check = await _check(env, "org_admin", {"permission": VIEW, "user_id": guest, "organization_id": ORG_A})
            read = await env.client.get(f"/api/users/{guest}", headers={"Authorization": f"Bearer {_token('org_admin')}"})
            assert check.status_code == 404, check.text
            assert read.status_code in {403, 404}, read.text

    _run(scenario())


def test_a_target_without_a_valid_email_is_answered(monkeypatch) -> None:
    """Re-review: a stored email the user model rejects (none, a local domain) gave 500."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_roles(env.db)
            from bson import ObjectId

            for email in (None, "admin@localhost"):
                oid = ObjectId()
                doc = {"_id": oid, "roles": [ROLE_A], "organization_id": ORG_A, "organizations": [ORG_A],
                       "projects": [], "disabled": False}
                if email:
                    doc["email"] = email
                await env.db.users.insert_one(doc)
                response = await _check(env, "org_admin", {"permission": VIEW, "user_id": str(oid), "organization_id": ORG_A})
                assert response.status_code == 200 and response.json()["granted"] is True, (email, response.text)

    _run(scenario())
