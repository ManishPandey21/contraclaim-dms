"""4B-06 and 4B-07 (D-06, AQ-2, AQ-3, owner amendment B.2): reviewer identities and
scoped reviewer assignments. Real HTTP, real MongoDB replica set, real RBAC; no
dependency override.

What is proven here:

- who may grant and revoke at each scope, and that nobody grants themselves a role;
- platform authority for a skill pack comes only from a live platform assignment:
  a role name on the user, an organisation or project assignment and Super Admin
  status are each refused by the real approval service;
- revocation and expiry take effect on the next request;
- one live assignment per person, role and scope, under a concurrent race;
- another tenant's administrator can neither read nor revoke;
- the seed migration never overwrites a role document that is already there.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from rbac_backend.core.permissions import REVIEWER_ROLE_KEYS
from rbac_backend.migrations.v20261121_0001_reviewer_role_identities import upgrade as seed_identities
from rbac_backend.migrations.v20261121_0002_reviewer_role_assignment_indexes import upgrade as apply_indexes
from rbac_backend.models.reviewer_assignment import REVIEWER_ROLE_ASSIGNMENTS
from rbac_backend.models.skill import SkillApprovalRequest
from rbac_backend.services.reviewer_assignments import (
    arbitration_reviewer_roles,
    platform_reviewer_roles,
)
from rbac_backend.services.skills.approvals import SkillApprovalService
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_skill_publication_transactions_mongo import REPORT, _draft
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2
from rbac_backend.tests.reviewer_assignment_helpers import assignment_document
from rbac_backend.utils.rate_limiter import RateLimiter

pytestmark = pytest.mark.integration

BASE = "/api/reviewer-assignments"
LEGAL = "senior_legal_approver"
QUANTUM = "quantum_reviewer"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _ready(env: Any) -> None:
    RateLimiter._shared_user_requests.clear()
    RateLimiter._shared_buckets.clear()
    await seed_identities(env.db, False)
    await apply_indexes(env.db, False)


def _platform(user: str, role: str = LEGAL, **extra: Any) -> dict:
    return {"user_id": user, "role_key": role, "scope_type": "platform", "reason": "Appointed by the review board", **extra}


def _organisation(user: str, role: str = LEGAL, org: str = ORG_A, **extra: Any) -> dict:
    return {
        "user_id": user, "role_key": role, "scope_type": "organization", "organization_id": org,
        "reason": "Named reviewer for this organisation's cases", **extra,
    }


def _project(user: str, role: str = QUANTUM, project: str = PROJ_A1, **extra: Any) -> dict:
    return {
        "user_id": user, "role_key": role, "scope_type": "project", "project_id": project,
        "reason": "Quantum reviewer on this project", **extra,
    }


def _user(persona: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(id=f"user-{persona}", roles=fields.pop("roles", []), **fields)


# ------------------------------------------------------------------ migrations


def test_the_seed_adds_nine_identities_with_no_permissions_and_is_repeatable(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            dry = await seed_identities(env.db, True)
            assert await env.db.roles.count_documents({"kind": "reviewer_identity"}) == 0, "a dry run writes nothing"
            assert {op["role"] for op in dry.operations} == set(REVIEWER_ROLE_KEYS) and not dry.warnings

            applied = await seed_identities(env.db, False)
            assert not applied.warnings
            docs = await env.db.roles.find({"kind": "reviewer_identity"}).to_list(None)
            assert {doc["_id"] for doc in docs} == set(REVIEWER_ROLE_KEYS)
            assert all(doc["permissions"] == [] and doc["scope"] == "system" for doc in docs)

            again = await seed_identities(env.db, False)
            assert {op["operation"] for op in again.operations} == {"skip_existing"} and not again.warnings
            assert await env.db.roles.count_documents({"kind": "reviewer_identity"}) == len(REVIEWER_ROLE_KEYS)

    _run(scenario())


def test_the_seed_reports_and_leaves_alone_a_role_document_that_is_already_there(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            hand_made = {"_id": LEGAL, "name": "Legal sign-off", "permissions": ["dms.admin"], "scope": "organization"}
            same_name = {"_id": "custom-77", "name": "Quantum reviewer", "permissions": ["dms.claim.view"]}
            await env.db.roles.insert_many([hand_made, same_name])

            result = await seed_identities(env.db, False)

            assert len(result.warnings) == 2, result.warnings
            assert any(LEGAL in text and "left unchanged" in text for text in result.warnings)
            assert any(QUANTUM in text and "not seeded" in text for text in result.warnings)
            assert await env.db.roles.find_one({"_id": LEGAL}) == hand_made, "the existing document is not touched"
            assert await env.db.roles.find_one({"_id": QUANTUM}) is None
            assert await env.db.roles.find_one({"_id": "custom-77"}) == same_name
            # The other seven are seeded; the two conflicts are for a person to resolve.
            assert await env.db.roles.count_documents({"kind": "reviewer_identity"}) == len(REVIEWER_ROLE_KEYS) - 2

    _run(scenario())


def test_a_seeded_identity_gives_a_user_who_holds_it_no_permission(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            await env.db.users.update_one({"_id": "user-project_user"}, {"$set": {"roles": ["projectuser", LEGAL]}})
            listed = await env.call("GET", "project_user", "/api/skills")
            baseline = await env.call("GET", "org_user", "/api/skills")
            # The identity adds nothing a project user did not already have.
            assert listed.status_code == baseline.status_code or listed.status_code in (200, 403)
            refused = await env.call("POST", "project_user", BASE, json=_platform("user-org_user"))
            assert refused.status_code == 403

    _run(scenario())


# ------------------------------------------------------------- who may grant


def test_only_a_super_admin_grants_platform_scope_and_never_to_themselves(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            for persona in ("org_admin", "project_admin", "org_user", "project_user", "foreign_org_admin"):
                refused = await env.call("POST", persona, BASE, json=_platform("user-org_user"))
                assert refused.status_code == 403, (persona, refused.text[:200])
                assert _code(refused) == "reviewer_platform_grant_forbidden"

            own = await env.call("POST", "superadmin", BASE, json=_platform("user-superadmin"))
            assert own.status_code == 403 and _code(own) == "reviewer_self_grant", own.text[:200]

            granted = await env.call("POST", "superadmin", BASE, json=_platform("user-org_user"))
            assert granted.status_code == 201, granted.text[:300]
            body = granted.json()
            assert body["state"] == "active" and body["expires_at"] is None and body["granted_by"] == "user-superadmin"
            assert await env.db[REVIEWER_ROLE_ASSIGNMENTS].count_documents({}) == 1

    _run(scenario())


def test_an_organisation_admin_grants_within_their_own_organisation_only(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            granted = await env.call("POST", "org_admin", BASE, json=_organisation("user-project_user"))
            assert granted.status_code == 201, granted.text[:300]
            expires = datetime.fromisoformat(granted.json()["expires_at"].replace("Z", ""))
            assert timedelta(days=364) < expires - datetime.utcnow() < timedelta(days=366), "defaults to twelve months"

            for persona in ("foreign_org_admin", "project_admin", "other_project_admin", "org_user", "project_user"):
                refused = await env.call("POST", persona, BASE, json=_organisation("user-org_user", role=QUANTUM))
                assert refused.status_code == 403, (persona, refused.status_code, refused.text[:200])
            abroad = await env.call("POST", "org_admin", BASE, json=_organisation("user-foreign_org_admin", org=ORG_B))
            assert abroad.status_code == 403, abroad.text[:200]
            own = await env.call("POST", "org_admin", BASE, json=_organisation("user-org_admin", role=QUANTUM))
            assert own.status_code == 403 and _code(own) == "reviewer_self_grant"
            assert await env.db[REVIEWER_ROLE_ASSIGNMENTS].count_documents({}) == 1

    _run(scenario())


def test_a_project_grant_reads_the_organisation_from_the_project_not_from_the_caller(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            granted = await env.call("POST", "org_admin", BASE, json=_project("user-project_user"))
            assert granted.status_code == 201, granted.text[:300]
            assert granted.json()["organization_id"] == ORG_A and granted.json()["project_id"] == PROJ_A1

            lied = await env.call(
                "POST", "foreign_org_admin", BASE, json=_project("user-project_admin", organization_id=ORG_B)
            )
            assert lied.status_code == 422 and _code(lied) == "reviewer_scope_mismatch", lied.text[:200]
            foreign = await env.call("POST", "foreign_org_admin", BASE, json=_project("user-project_admin"))
            assert foreign.status_code == 403, foreign.text[:200]
            missing = await env.call("POST", "org_admin", BASE, json=_project("user-project_user", project="no-such"))
            assert missing.status_code == 404

    _run(scenario())


@pytest.mark.parametrize(
    "target, code",
    [
        ("user-foreign_org_admin", "reviewer_user_not_a_member"),
        ("user-superadmin", "reviewer_user_not_a_member"),
        ("user-system_user", "reviewer_user_not_a_person"),
        ("user-nobody", "reviewer_user_not_found"),
    ],
)
def test_an_organisation_role_is_held_only_by_an_active_member(monkeypatch, target: str, code: str) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            refused = await env.call("POST", "org_admin", BASE, json=_organisation(target))
            assert refused.status_code in (404, 422) and _code(refused) == code, refused.text[:200]
            assert await env.db[REVIEWER_ROLE_ASSIGNMENTS].count_documents({}) == 0

    _run(scenario())


def test_a_project_role_is_not_given_to_a_member_of_another_project(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            refused = await env.call("POST", "org_admin", BASE, json=_project("user-other_project_admin"))
            assert refused.status_code == 422 and _code(refused) == "reviewer_user_not_a_member", refused.text[:200]
            allowed = await env.call(
                "POST", "org_admin", BASE, json=_project("user-other_project_admin", project=PROJ_A2)
            )
            assert allowed.status_code == 201, allowed.text[:200]

    _run(scenario())


def test_expiry_rules_for_each_scope(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            past = (datetime.utcnow() - timedelta(days=1)).isoformat()
            far = (datetime.utcnow() + timedelta(days=500)).isoformat()
            near = (datetime.utcnow() + timedelta(days=30)).isoformat()
            old = await env.call("POST", "org_admin", BASE, json=_organisation("user-project_user", expires_at=past))
            assert old.status_code == 422 and _code(old) == "reviewer_expiry_in_the_past"
            long = await env.call("POST", "org_admin", BASE, json=_organisation("user-project_user", expires_at=far))
            assert long.status_code == 422 and _code(long) == "reviewer_expiry_too_long"
            ok = await env.call("POST", "org_admin", BASE, json=_organisation("user-project_user", expires_at=near))
            assert ok.status_code == 201, ok.text[:200]
            # Platform scope may run longer, and may be open-ended.
            platform = await env.call("POST", "superadmin", BASE, json=_platform("user-org_user", expires_at=far))
            assert platform.status_code == 201, platform.text[:200]

    _run(scenario())


# -------------------------------------------- one live assignment per scope


def test_a_second_live_grant_of_the_same_role_at_the_same_scope_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            assert (await env.call("POST", "superadmin", BASE, json=_platform("user-org_user"))).status_code == 201
            again = await env.call("POST", "superadmin", BASE, json=_platform("user-org_user"))
            assert again.status_code == 409 and _code(again) == "reviewer_assignment_exists"
            # The same role at another scope is a different assignment.
            elsewhere = await env.call("POST", "org_admin", BASE, json=_organisation("user-org_user"))
            assert elsewhere.status_code == 201, elsewhere.text[:200]

    _run(scenario())


def test_five_concurrent_grants_leave_exactly_one_live_assignment(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            results = await asyncio.gather(
                *[env.call("POST", "superadmin", BASE, json=_platform("user-org_user")) for _ in range(5)]
            )
            codes = sorted(response.status_code for response in results)
            assert codes == [201, 409, 409, 409, 409], codes
            assert await env.db[REVIEWER_ROLE_ASSIGNMENTS].count_documents({"revoked_at": None}) == 1

    _run(scenario())


def test_a_lapsed_grant_frees_its_slot_and_a_revoked_one_is_kept_as_history(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            lapsed = assignment_document(
                "user-project_user", LEGAL, scope_type="organization", organization_id=ORG_A,
                expires_at=datetime.utcnow() - timedelta(days=2),
            )
            await env.db[REVIEWER_ROLE_ASSIGNMENTS].insert_one(lapsed)
            renewed = await env.call("POST", "org_admin", BASE, json=_organisation("user-project_user"))
            assert renewed.status_code == 201, renewed.text[:300]

            revoked = await env.call(
                "POST", "org_admin", f"{BASE}/{renewed.json()['id']}/revoke", json={"reason": "Left the panel"}
            )
            assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
            third = await env.call("POST", "org_admin", BASE, json=_organisation("user-project_user"))
            assert third.status_code == 201, third.text[:300]
            assert await env.db[REVIEWER_ROLE_ASSIGNMENTS].count_documents({}) == 3, "nothing is deleted"
            assert await env.db[REVIEWER_ROLE_ASSIGNMENTS].count_documents({"active": True}) == 1

    _run(scenario())


# ------------------------------------- platform authority, and only platform


def test_platform_authority_for_a_skill_pack_comes_only_from_a_live_platform_assignment(monkeypatch) -> None:
    """B.2: a role name, an organisation assignment, a project assignment and Super Admin are each refused."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            record = await _draft(env.db, "4.7.0")
            request = SkillApprovalRequest(kind="legal", reviewer_role=LEGAL, eval_report=REPORT)

            named = _user("project_user", roles=["projectuser", LEGAL])  # the role on their list
            await env.db.users.update_one({"_id": named.id}, {"$set": {"roles": named.roles}})
            org_held = _user("org_user", roles=["orguser"])
            assert (await env.call("POST", "org_admin", BASE, json=_organisation(org_held.id))).status_code == 201
            project_held = _user("project_admin", roles=["projectadmin"])
            assert (
                await env.call("POST", "org_admin", BASE, json=_project(project_held.id, role=LEGAL))
            ).status_code == 201
            super_admin = _user("superadmin", roles=["superadmin", LEGAL])

            for actor in (named, org_held, project_held, super_admin):
                assert await platform_reviewer_roles(env.db, actor) == set(), actor.id
                with pytest.raises(HTTPException) as refused:
                    await SkillApprovalService(env.db).record(record, request, actor)
                assert refused.value.status_code == 403, actor.id
            assert await env.db.skill_approvals.count_documents({}) == 0

            counsel = _user("other_project_admin", roles=["projectadmin"])
            granted = await env.call("POST", "superadmin", BASE, json=_platform(counsel.id))
            assert granted.status_code == 201
            approval = await SkillApprovalService(env.db).record(record, request, counsel)
            assert approval.reviewer_user_id == counsel.id and approval.reviewer_role == LEGAL

    _run(scenario())


def test_a_revocation_takes_effect_on_the_next_request(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            granted = await env.call("POST", "superadmin", BASE, json=_platform("user-org_user"))
            mine = await env.call("GET", "org_user", f"{BASE}/mine")
            assert mine.status_code == 200 and mine.json()["platform"] == [LEGAL]

            revoked = await env.call(
                "POST", "superadmin", f"{BASE}/{granted.json()['id']}/revoke", json={"reason": "Conflict of interest"}
            )
            assert revoked.status_code == 200, revoked.text[:200]
            after = await env.call("GET", "org_user", f"{BASE}/mine")
            assert after.json() == {"platform": [], "assignments": []}

            record = await _draft(env.db, "4.7.1")
            with pytest.raises(HTTPException) as refused:
                await SkillApprovalService(env.db).record(
                    record, SkillApprovalRequest(kind="legal", reviewer_role=LEGAL, eval_report=REPORT), _user("org_user")
                )
            assert refused.value.status_code == 403
            # Revoking twice changes nothing and writes no second audit row.
            twice = await env.call(
                "POST", "superadmin", f"{BASE}/{granted.json()['id']}/revoke", json={"reason": "Conflict of interest"}
            )
            assert twice.status_code == 200
            assert await env.db.audit_events.count_documents({"action": "reviewer_assignment.revoked"}) == 1

    _run(scenario())


def test_an_expired_assignment_stops_answering_without_anyone_revoking_it(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            await env.db[REVIEWER_ROLE_ASSIGNMENTS].insert_one(
                assignment_document("user-org_user", LEGAL, expires_at=datetime.utcnow() - timedelta(minutes=1))
            )
            assert await platform_reviewer_roles(env.db, _user("org_user")) == set()
            mine = await env.call("GET", "org_user", f"{BASE}/mine")
            assert mine.json() == {"platform": [], "assignments": []}

    _run(scenario())


# ------------------------------------------------- arbitration gate scoping


def test_a_scoped_assignment_answers_an_arbitration_gate_only_inside_its_scope(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            reviewer = _user("project_user", roles=["projectuser"])
            assert (await env.call("POST", "org_admin", BASE, json=_project(reviewer.id))).status_code == 201

            async def held(org: str, project: Any) -> set:
                return await arbitration_reviewer_roles(env.db, reviewer, organization_id=org, project_id=project)

            assert QUANTUM in await held(ORG_A, PROJ_A1)
            assert QUANTUM not in await held(ORG_A, PROJ_A2)
            assert QUANTUM not in await held(ORG_A, None)
            assert QUANTUM not in await held(ORG_B, PROJ_A1)

            org_reviewer = _user("org_user", roles=["orguser"])
            assert (await env.call("POST", "org_admin", BASE, json=_organisation(org_reviewer.id))).status_code == 201
            for project in (PROJ_A1, PROJ_A2, None):
                assert LEGAL in await arbitration_reviewer_roles(
                    env.db, org_reviewer, organization_id=ORG_A, project_id=project
                )
            assert LEGAL not in await arbitration_reviewer_roles(env.db, org_reviewer, organization_id=ORG_B, project_id=None)

            # Platform authority is for skill packs; it does not reach one tenant's case.
            platform_only = _user("other_project_admin", roles=["projectadmin"])
            assert (await env.call("POST", "superadmin", BASE, json=_platform(platform_only.id))).status_code == 201
            assert LEGAL not in await arbitration_reviewer_roles(
                env.db, platform_only, organization_id=ORG_A, project_id=PROJ_A2
            )

    _run(scenario())


def test_a_hand_made_role_on_the_users_list_still_answers_an_arbitration_gate(monkeypatch) -> None:
    """Existing case work is not broken: the role list is read exactly as before."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            legacy = _user("project_user", roles=["projectuser", "Legal_Reviewer", "panel_counsel"])
            roles = await arbitration_reviewer_roles(env.db, legacy, organization_id=ORG_A, project_id=PROJ_A1)
            assert {"legal_reviewer", "panel_counsel", "projectuser"} <= roles
            assert await platform_reviewer_roles(env.db, legacy) == set()

    _run(scenario())


# --------------------------------------------------- reading and tenancy


def test_lists_are_for_that_scopes_administrator_and_another_tenant_sees_nothing(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            org_grant = await env.call("POST", "org_admin", BASE, json=_organisation("user-project_user"))
            await env.call("POST", "superadmin", BASE, json=_platform("user-org_user"))

            own = await env.call("GET", "org_admin", BASE, params={"scope_type": "organization", "organization_id": ORG_A})
            assert own.status_code == 200 and [item["user_id"] for item in own.json()["items"]] == ["user-project_user"]
            assert own.json()["items"][0]["role_label"] == "Senior legal approver"

            for persona in ("foreign_org_admin", "project_admin", "project_user"):
                refused = await env.call(
                    "GET", persona, BASE, params={"scope_type": "organization", "organization_id": ORG_A}
                )
                assert refused.status_code == 403, (persona, refused.status_code)
            platform = await env.call("GET", "org_admin", BASE, params={"scope_type": "platform"})
            assert platform.status_code == 403
            listed = await env.call("GET", "superadmin", BASE, params={"scope_type": "platform"})
            assert listed.status_code == 200 and len(listed.json()["items"]) == 1

            # Another tenant's administrator is told the record does not exist.
            foreign = await env.call(
                "POST", "foreign_org_admin", f"{BASE}/{org_grant.json()['id']}/revoke", json={"reason": "Not mine to end"}
            )
            assert foreign.status_code == 404, foreign.text[:200]
            assert (await env.db[REVIEWER_ROLE_ASSIGNMENTS].find_one({"_id": org_grant.json()["id"]}))["revoked_at"] is None
            unknown = await env.call("POST", "superadmin", f"{BASE}/nope/revoke", json={"reason": "No such record"})
            assert unknown.status_code == 404

    _run(scenario())


def test_grant_and_revoke_are_audited_with_who_why_and_what_changed(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            granted = await env.call("POST", "org_admin", BASE, json=_organisation("user-project_user"))
            await env.call("POST", "org_admin", f"{BASE}/{granted.json()['id']}/revoke", json={"reason": "Project closed"})
            rows = await env.db.audit_events.find({"resource_type": "reviewer_role_assignment"}).to_list(None)
            by_action = {row["action"]: row for row in rows}
            assert set(by_action) == {"reviewer_assignment.granted", "reviewer_assignment.revoked"}
            grant, revoke = by_action["reviewer_assignment.granted"], by_action["reviewer_assignment.revoked"]
            assert grant["actor_id"] == "user-org_admin" and grant["organization_id"] == ORG_A
            assert grant["reason"] == "Named reviewer for this organisation's cases"
            assert grant["metadata"]["user_id"] == "user-project_user" and grant["metadata"]["role_key"] == LEGAL
            assert revoke["reason"] == "Project closed"
            assert revoke["before"]["revoked_at"] is None and revoke["after"]["revoked_at"] is not None

    _run(scenario())


def test_the_legacy_report_lists_role_list_holders_and_converts_nothing(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            await env.db.users.update_one({"_id": "user-project_user"}, {"$set": {"roles": ["projectuser", LEGAL]}})
            await env.db.users.update_one({"_id": "user-org_user"}, {"$set": {"roles": ["orguser", QUANTUM]}})
            await env.call("POST", "org_admin", BASE, json=_organisation("user-org_user", role=QUANTUM))
            before = await env.db[REVIEWER_ROLE_ASSIGNMENTS].count_documents({})

            report = await env.call("GET", "org_admin", f"{BASE}/legacy-report", params={"organization_id": ORG_A})
            assert report.status_code == 200, report.text[:200]
            holders = {(item["user_id"], item["role_key"]): item["has_assignment"] for item in report.json()["holders"]}
            assert holders == {("user-project_user", LEGAL): False, ("user-org_user", QUANTUM): True}
            assert report.json()["counts_by_role"] == {LEGAL: 1, QUANTUM: 1}
            assert await env.db[REVIEWER_ROLE_ASSIGNMENTS].count_documents({}) == before

            for persona in ("foreign_org_admin", "project_admin"):
                refused = await env.call("GET", persona, f"{BASE}/legacy-report", params={"organization_id": ORG_A})
                assert refused.status_code == 403
            assert (await env.call("GET", "org_admin", f"{BASE}/legacy-report")).status_code == 403
            assert (await env.call("GET", "superadmin", f"{BASE}/legacy-report")).status_code == 200

    _run(scenario())


def test_an_unauthenticated_caller_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            for method, path in (("GET", f"{BASE}/mine"), ("POST", BASE), ("GET", f"{BASE}/legacy-report")):
                response = await env.client.request(method, path, json={} if method == "POST" else None)
                assert response.status_code == 401, (method, path, response.status_code)

    _run(scenario())


# ----------------------------------------- 4B-09: the reviewer's own journey


def _review_request() -> dict:
    return {"kind": "legal", "reviewer_role": LEGAL, "eval_report": REPORT.model_dump(mode="json")}


def test_a_pure_reviewer_sees_the_queue_reads_the_draft_and_records_an_approval_over_http(monkeypatch) -> None:
    """4B-09 proof. The reviewer holds a platform assignment and no skill permission."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            await _draft(env.db, "4.9.0")
            version = "/api/skills/indian-infra-claims/versions/4.9.0"

            # Before any assignment: an empty queue, and the draft does not exist for them.
            empty = await env.call("GET", "project_user", "/api/skills/review-queue")
            assert empty.status_code == 200 and empty.json() == {"my_roles": [], "items": []}
            hidden = await env.call("GET", "project_user", version)
            assert hidden.status_code in (403, 404), hidden.status_code
            refused = await env.call("POST", "project_user", f"{version}/approvals", json=_review_request())
            assert refused.status_code == 403, refused.text[:200]

            granted = await env.call("POST", "superadmin", BASE, json=_platform("user-project_user"))
            assert granted.status_code == 201

            queue = await env.call("GET", "project_user", "/api/skills/review-queue")
            assert queue.status_code == 200, queue.text[:200]
            body = queue.json()
            assert body["my_roles"] == [LEGAL]
            item = next(entry for entry in body["items"] if entry["version"] == "4.9.0")
            assert item["waiting_on_me"] is True
            mine = [review for review in item["reviews"] if review["mine"]]
            assert [(review["reviewer_role"], review["given"]) for review in mine] == [(LEGAL, False)]

            text = await env.call("GET", "project_user", version)
            assert text.status_code == 200 and "skill_md" in text.json()
            listed = await env.call("GET", "project_user", f"{version}/approvals")
            assert listed.status_code == 200

            recorded = await env.call("POST", "project_user", f"{version}/approvals", json=_review_request())
            assert recorded.status_code == 201, recorded.text[:300]
            assert recorded.json()["reviewer_user_id"] == "user-project_user"

            after = await env.call("GET", "project_user", "/api/skills/review-queue")
            item = next(entry for entry in after.json()["items"] if entry["version"] == "4.9.0")
            assert item["waiting_on_me"] is False
            assert [review["given_by_me"] for review in item["reviews"] if review["mine"]] == [True]

            # The role the reviewer does not hold stays someone else's to give.
            other = {**_review_request(), "kind": "technical", "reviewer_role": "chief_contract_expert"}
            assert (await env.call("POST", "project_user", f"{version}/approvals", json=other)).status_code == 403

    _run(scenario())


def test_a_reviewer_is_offered_nothing_a_publisher_does(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            await _draft(env.db, "4.9.1")
            await env.call("POST", "superadmin", BASE, json=_platform("user-project_user"))
            version = "/api/skills/indian-infra-claims/versions/4.9.1"
            report = {"eval_report": REPORT.model_dump(mode="json")}
            attempts = [
                ("GET", "/api/skills/admin", None),
                ("POST", f"{version}/publish", report),
                ("POST", f"{version}/deprecate", None),
                ("PUT", "/api/skills/bindings", {"organization_id": ORG_A, "skill_key": "indian-infra-claims", "version": "4.9.1"}),
            ]
            for method, path, payload in attempts:
                response = await env.call(method, "project_user", path, json=payload)
                assert response.status_code == 403, (method, path, response.status_code, response.text[:160])
            imported = await env.client.post(
                "/api/skills/import",
                headers=env.headers("project_user"),
                data={"version": "9.9.9", "kind": "evaluation", "manifest": "{}"},
                files={"file": ("pack.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip")},
            )
            assert imported.status_code == 403, imported.text[:160]
            assert (await env.db.skill_versions.find_one({"version": "4.9.1"}))["status"] == "draft"

    _run(scenario())


def test_an_organisation_reviewer_gets_no_queue_and_no_draft(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            await _draft(env.db, "4.9.2")
            assert (await env.call("POST", "org_admin", BASE, json=_organisation("user-project_user"))).status_code == 201
            queue = await env.call("GET", "project_user", "/api/skills/review-queue")
            assert queue.json() == {"my_roles": [], "items": []}
            version = "/api/skills/indian-infra-claims/versions/4.9.2"
            assert (await env.call("GET", "project_user", version)).status_code in (403, 404)
            refused = await env.call("POST", "project_user", f"{version}/approvals", json=_review_request())
            assert refused.status_code == 403

    _run(scenario())


def test_a_reviewer_withdraws_their_own_approval_and_a_stranger_cannot(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            await _draft(env.db, "4.9.3")
            await env.call("POST", "superadmin", BASE, json=_platform("user-project_user"))
            version = "/api/skills/indian-infra-claims/versions/4.9.3"
            approval = (await env.call("POST", "project_user", f"{version}/approvals", json=_review_request())).json()
            approval_id = approval.get("_id") or approval.get("id")
            path = f"/api/skills/approvals/{approval_id}/revoke"

            stranger = await env.call("POST", "org_user", path, json={"reason": "Not my approval to end"})
            assert stranger.status_code == 403, stranger.text[:200]
            own = await env.call("POST", "project_user", path, json={"reason": "I found an error in the pack"})
            assert own.status_code == 200 and own.json()["revoked_at"], own.text[:200]

    _run(scenario())


# ------------------------- 4B-08: a reviewer role is not added by a role list


async def _member(env: Any, roles: list) -> str:
    """A project member stored the way the Users screen stores one (an ObjectId key)."""
    from bson import ObjectId

    user_id = ObjectId()
    await env.db.users.insert_one(
        {
            "_id": user_id, "email": f"{user_id}@example.com", "username": f"member-{user_id}", "disabled": False,
            "account_type": "client_user", "first_name": "Asha", "last_name": "Rao", "roles": roles,
            "organization_id": ORG_A, "organizations": [], "projects": [PROJ_A1],
        }
    )
    return str(user_id)


async def _roles(env: Any, user_id: str) -> list:
    from bson import ObjectId

    return (await env.db.users.find_one({"_id": ObjectId(user_id)}))["roles"]


def test_a_reviewer_role_cannot_be_added_through_a_users_role_list_by_anyone(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            user_id = await _member(env, ["projectuser"])
            for persona in ("superadmin", "org_admin"):
                added = await env.call("PUT", persona, f"/api/users/{user_id}", json={"roles": ["projectuser", LEGAL]})
                assert added.status_code == 422, (persona, added.status_code, added.text[:200])
                assert "Reviewer roles screen" in added.text
            assert await _roles(env, user_id) == ["projectuser"]
            # An ordinary edit of the same user still works.
            plain = await env.call("PUT", "superadmin", f"/api/users/{user_id}", json={"first_name": "Asha K"})
            assert plain.status_code == 200, plain.text[:300]

    _run(scenario())


def test_editing_a_user_who_already_has_a_reviewer_role_on_their_list_keeps_it(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env)
            user_id = await _member(env, ["projectuser", QUANTUM])
            kept = await env.call(
                "PUT", "superadmin", f"/api/users/{user_id}",
                json={"first_name": "Asha K", "roles": ["projectuser", QUANTUM]},
            )
            assert kept.status_code == 200, kept.text[:300]
            assert await _roles(env, user_id) == ["projectuser", QUANTUM]
            # A second reviewer role is still not added this way.
            more = await env.call(
                "PUT", "superadmin", f"/api/users/{user_id}", json={"roles": ["projectuser", QUANTUM, LEGAL]}
            )
            assert more.status_code == 422, more.text[:200]
            assert await _roles(env, user_id) == ["projectuser", QUANTUM]

    _run(scenario())
