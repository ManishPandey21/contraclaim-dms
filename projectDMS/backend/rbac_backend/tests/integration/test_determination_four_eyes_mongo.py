"""4B-26 (D-13, C-09): determination approval is four-eyes in substance.
Real HTTP, real MongoDB replica set, real RBAC.

The plan's proof, through the production app:

- a person who is not the named reviewer cannot approve or send back (403), even
  holding determination authority;
- a reviewer who does not hold the authority by name, or is not a member of the
  project, is refused at submission (422);
- a revision clears every result, so it cannot be submitted undecided (409);
- if the party the project acts for changes, approval is refused (409);
- only the latest revision can be revised (409).

Before 4B-26: A authors v1, B approves; B opens v2 unchanged and submits it; A
approves v2, which is A's own findings re-issued.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_B, PROJ_A1

pytestmark = pytest.mark.integration

BASE = f"/api/claims/{CLAIM}/determinations"
AUTHORITY = ["dms.claim.view", "dms.claim.edit", "dms.claim.determine"]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _ok(response: Any) -> Dict[str, Any]:
    assert response.status_code == 200, (response.status_code, response.text[:400])
    return response.json()


async def _prepared(env: Any) -> None:
    await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "engineer"}})
    for persona in ("author", "reviewer", "colleague"):
        await _add_actor(env.db, persona, f"role-{persona}", AUTHORITY)
    # Holds the authority, but in another organisation: not a member of this project.
    await env.db.roles.insert_one(
        {"_id": "role-outsider", "name": "role-outsider", "permissions": AUTHORITY, "scope": "organization",
         "organization_id": ORG_B, "is_active": True}
    )
    await env.db.users.insert_one(
        {"_id": "user-outsider", "email": "outsider@example.com", "username": "outsider", "disabled": False,
         "account_type": "client_user", "organizations": [], "roles": ["role-outsider"],
         "organization_id": ORG_B, "projects": []}
    )
    await _ok(await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open"))


async def _decided_draft(env: Any) -> Dict[str, Any]:
    view = await _ok(await env.call("POST", "author", BASE, json={"origin": "authored"}))
    det = view["determination"]
    for finding in det["heads"]:
        view = await _ok(await env.call(
            "PUT", "author", f"{BASE}/{det['_id']}/heads/{finding['head_id']}",
            json={"version": view["determination"]["version"], "result": "partial", "days": 10,
                  "findings": "Access was given ten days late; the rest is not shown."},
        ))
    return view["determination"]


async def _submit(env: Any, det: Dict[str, Any], reviewer: str, persona: str = "author") -> Any:
    return await env.call("POST", persona, f"{BASE}/{det['_id']}/submit",
                          json={"version": det["version"], "reviewer_id": reviewer})


def test_only_the_named_reviewer_decides_and_the_reviewer_must_hold_authority_here(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            det = await _decided_draft(env)

            # A person without determination authority, or outside the project, is not an approver.
            for candidate in ("user-org_admin", "user-outsider", "user-nobody"):
                refused = await _submit(env, det, candidate)
                assert refused.status_code == 422, (candidate, refused.text[:300])
                assert _code(refused) == "determination_reviewer_not_authorised"
            stored = await env.db.claim_determinations.find_one({"_id": det["_id"]})
            assert stored["status"] == "draft" and stored.get("reviewer_id") is None

            det = (await _ok(await _submit(env, det, "user-reviewer")))["determination"]
            assert det["status"] == "in_review" and det["reviewer_id"] == "user-reviewer"

            # Someone else holding the same authority is not the reviewer.
            for action, body in (("approve", {}), ("return", {"comment": "Days not explained."})):
                refused = await env.call("POST", "colleague", f"{BASE}/{det['_id']}/{action}",
                                         json={"version": det["version"], **body})
                assert refused.status_code == 403 and _code(refused) == "determination_not_the_reviewer", refused.text[:300]

            # The view tells each person whether they may decide.
            for persona, expected in (("reviewer", True), ("colleague", False), ("author", False)):
                listed = await _ok(await env.call("GET", persona, BASE))
                assert listed["items"][0]["may_decide"] is expected, persona
                assert listed["project_id"] == PROJ_A1

            approved = await _ok(await env.call("POST", "reviewer", f"{BASE}/{det['_id']}/approve",
                                                json={"version": det["version"]}))
            assert approved["determination"]["status"] == "approved"
            assert approved["determination"]["decided_by"] == "user-reviewer"

    _run(scenario())


def test_a_revision_is_decided_again_and_only_the_latest_can_be_revised(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            det = await _decided_draft(env)
            det = (await _ok(await _submit(env, det, "user-reviewer")))["determination"]
            await _ok(await env.call("POST", "reviewer", f"{BASE}/{det['_id']}/approve", json={"version": det["version"]}))

            # The reviewer opens v2 and, unchanged, tries to send it to the author: refused.
            v2 = (await _ok(await env.call("POST", "reviewer", f"{BASE}/{det['_id']}/revise", json={})))["determination"]
            assert all(finding["result"] is None for finding in v2["heads"])
            assert v2["heads"][0]["findings"]  # the reasons are kept as a starting point
            undecided = await _submit(env, v2, "user-author", persona="reviewer")
            assert undecided.status_code == 409 and "No finding yet" in undecided.text, undecided.text[:300]

            # v1 has been revised: revising it again is refused.
            again = await env.call("POST", "author", f"{BASE}/{det['_id']}/revise", json={})
            assert again.status_code == 409 and _code(again) == "determination_superseded", again.text[:300]

    _run(scenario())


def test_a_change_of_party_stops_approval(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            det = await _decided_draft(env)
            det = (await _ok(await _submit(env, det, "user-reviewer")))["determination"]
            # The project now acts for the Employer (4B-15's own route is exercised in its suite).
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "employer"}})
            refused = await env.call("POST", "reviewer", f"{BASE}/{det['_id']}/approve", json={"version": det["version"]})
            assert refused.status_code == 409 and _code(refused) == "determination_capacity_changed", refused.text[:300]
            stored = await env.db.claim_determinations.find_one({"_id": det["_id"]})
            assert stored["status"] == "in_review" and stored.get("decided_by") is None

    _run(scenario())


def test_every_write_re_reads_the_party_the_project_acts_for(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            det = await _decided_draft(env)
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "employer"}})
            finding = det["heads"][0]
            edit = await env.call(
                "PUT", "author", f"{BASE}/{det['_id']}/heads/{finding['head_id']}",
                json={"version": det["version"], "result": "full", "days": 12, "findings": "Changed after the flip."},
            )
            assert edit.status_code == 409 and _code(edit) == "determination_capacity_changed", edit.text[:300]
            submitted = await _submit(env, det, "user-reviewer")
            assert submitted.status_code == 409 and _code(submitted) == "determination_capacity_changed"
            # Back to the Engineer: the same draft can go on, and goes to its reviewer.
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "engineer"}})
            det = (await _ok(await _submit(env, det, "user-reviewer")))["determination"]
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "employer"}})
            back = await env.call("POST", "reviewer", f"{BASE}/{det['_id']}/return",
                                  json={"version": det["version"], "comment": "Days not explained."})
            assert back.status_code == 409 and _code(back) == "determination_capacity_changed", back.text[:300]

    _run(scenario())
