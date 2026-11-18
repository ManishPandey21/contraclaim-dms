"""4B-46 (D-27, D-25): the privilege wall and provenance hold at the API, whatever the UI hides.
Real HTTP, real MongoDB replica set, real RBAC. Every call is made directly with a token
whose UI would not offer the action.

The plan's proof:

- the privilege wall: the simulations and the strategy note are refused to everyone
  without ``dms.claim.strategy.read`` held by name - Super Admin, organisation and
  project administrators included - for reading and for writing; and no other claim
  route, export or store carries their content, even for a holder of the grant (the
  database is searched, collection by collection, after every route has been called);
- provenance (D-25): for a caller who cannot read some of the claim's evidence, no claim
  route names a restricted item (title, reference, date, direction, file type or id),
  and every route that would carry evidence out of the claim - the submission export,
  the evidence bundle and escalation - is refused with ``evidence_restricted``, audited,
  and writes nothing.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_submission_evidence_restricted_mongo import IDENTIFYING
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import DOC_IN_ID, DOC_OUT_ID, PROJ_A1

pytestmark = pytest.mark.integration

BASE = f"/api/claims/{CLAIM}"
MARK = "PRIVILEGED-4B46-settle-at-forty-percent"
STRATEGIST = ["dms.claim.view", "dms.claim.edit", "dms.claim.strategy.read", "dms.document.view"]
# Can manage, export and escalate the claim, and read its audit trail; cannot read its documents.
NARROW = ["dms.claim.view", "dms.claim.edit", "dms.claim.manage", "dms.audit.view", "dms.arbitration.create"]
WITHOUT_GRANT = ("org_admin", "project_admin", "superadmin", "project_user", "narrow")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return ""
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _ok(response: Any) -> Any:
    assert response.status_code in (200, 201), (response.status_code, response.text[:400])
    return response.json()


async def _version(env: Any) -> int:
    return int((await env.db.claims.find_one({"_id": CLAIM}))["bench"]["board_version"])


async def _prepared(env: Any) -> None:
    await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "contractor"}})
    await _add_actor(env.db, "strategist", "role-strategist", STRATEGIST)
    await _add_actor(env.db, "narrow", "role-narrow", NARROW)
    await _ok(await env.call("POST", "org_admin", f"{BASE}/bench/open"))
    await _ok(await env.call(
        "POST", "org_admin", f"/api/entities/claim/{CLAIM}/document-links:batch",
        json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"},
                        {"document_id": DOC_OUT_ID, "relationship_role": "supporting_document"}]},
    ))
    head_id = (await env.db.claims.find_one({"_id": CLAIM}))["bench"]["heads"][0]["head_id"]
    # A quantum line citing the evidence: the quantum view is a route that could name it.
    await _ok(await env.call(
        "POST", "org_admin", f"{BASE}/bench/heads/{head_id}/quantum/lines",
        json={"board_version": await _version(env), "category": "direct", "description": "Idle plant, March",
              "amount": 250000, "records": [{"document_id": DOC_IN_ID, "role": "support"}]},
    ))
    await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"bench.stage": "locked"}})
    await _ok(await env.call("POST", "org_admin", f"{BASE}/submission/compose", json={"sections": []}))

    # Privileged material, made through its own routes by the one person who holds the grant.
    note = await _ok(await env.call("GET", "strategist", f"{BASE}/strategy-note"))
    await _ok(await env.call(
        "PUT", "strategist", f"{BASE}/strategy-note",
        json={"version": note.get("version", 0) if isinstance(note, dict) else 0,
              "strengths": ["Handover records are complete."], "weaknesses": [MARK],
              "residual_risks": [], "notes": f"Fallback position: {MARK}"},
    ))
    await _ok(await env.call("POST", "strategist", f"{BASE}/simulations", json={"simulated_party": "engineer"}))


def _reads(head_id: str) -> List[str]:
    """Every claim route that returns claim, determination, submission or escalation content."""
    return [
        "/api/claims",
        BASE,
        f"{BASE}/assessments",
        f"{BASE}/approval",
        f"{BASE}/bench",
        f"{BASE}/bench/heads/{head_id}/delay",
        f"{BASE}/bench/delay/candidates",
        f"{BASE}/bench/heads/{head_id}/quantum",
        "/api/claims/bench/signals",
        f"/api/claims/notice/candidates?claim_id={CLAIM}",
        f"{BASE}/determinations",
        f"{BASE}/submission",
        f"{BASE}/evidence-contract",
        f"{BASE}/submission/export?format=docx",
        f"{BASE}/evidence-bundle",
    ]


async def _search_database(env: Any, needle: str, skip: set) -> List[str]:
    """Collections, other than ``skip``, holding ``needle`` anywhere in any document."""
    found: List[str] = []
    for name in await env.db.list_collection_names():
        if name in skip:
            continue
        async for doc in env.db[name].find({}):
            if needle in json.dumps(doc, default=str):
                found.append(name)
                break
    return found


def test_the_privilege_wall_holds_on_every_route_and_in_every_store(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            steps_before = await env.db.claim_skill_steps.count_documents({}) if "claim_skill_steps" in await env.db.list_collection_names() else 0
            await _prepared(env)
            head_id = (await env.db.claims.find_one({"_id": CLAIM}))["bench"]["heads"][0]["head_id"]

            # The holder reads it (the control): the material exists and is reachable.
            assert MARK in (await env.call("GET", "strategist", f"{BASE}/strategy-note")).text

            # Without the grant, by name, nobody reads or writes it - administrators and Super Admin included.
            for persona in WITHOUT_GRANT:
                for method, path, body in (
                    ("GET", f"{BASE}/simulations", None),
                    ("POST", f"{BASE}/simulations", {"simulated_party": "engineer"}),
                    ("GET", f"{BASE}/strategy-note", None),
                    ("PUT", f"{BASE}/strategy-note", {"version": 1, "notes": "overwritten"}),
                    ("POST", f"{BASE}/strategy-note/generate", {}),
                ):
                    response = await env.call(method, persona, path, json=body)
                    assert response.status_code in (403, 404), (persona, method, path, response.status_code)
                    assert MARK not in response.text, (persona, path)

            # No other route carries it, for anyone - the holder of the grant included.
            for persona in ("strategist", *WITHOUT_GRANT):
                for path in _reads(head_id):
                    response = await env.call("GET", persona, path)
                    assert response.status_code < 500, (persona, path, response.status_code, response.text[:200])
                    assert MARK.encode() not in response.content, (persona, path)
            preview = await env.call("POST", "org_admin", "/api/claims/escalation/preview", json={"claim_ids": [CLAIM]})
            assert preview.status_code == 200 and MARK not in preview.text, preview.text[:300]
            escalated = await env.call("POST", "org_admin", "/api/claims/escalation/run", json={
                "claim_ids": [CLAIM], "new_case": {"title": "Wall check"}, "idempotency_key": "wall-check-0001",
                "acknowledged_weak": [CLAIM],
            })
            assert escalated.status_code == 201, escalated.text[:300]

            # Nor any store: only the privileged collection holds it. No skill step was written
            # for the simulation (it is never in a step store).
            assert await _search_database(env, MARK, {"claim_privileged"}) == []
            steps_after = await env.db.claim_skill_steps.count_documents({}) if "claim_skill_steps" in await env.db.list_collection_names() else 0
            assert steps_after == steps_before
            assert await env.db.claim_privileged.count_documents({"claim_id": CLAIM}) >= 2

    _run(scenario())


def test_provenance_no_route_names_restricted_evidence_and_nothing_carries_it_out(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            head_id = (await env.db.claims.find_one({"_id": CLAIM}))["bench"]["heads"][0]["head_id"]

            # Nothing identifying a restricted item, on any route the narrow caller can reach.
            leaks: Dict[str, List[str]] = {}
            for path in _reads(head_id):
                response = await env.call("GET", "narrow", path)
                assert response.status_code < 500, (path, response.status_code, response.text[:200])
                found = [value for value in IDENTIFYING if value.encode() in response.content]
                if found:
                    leaks[path] = found
            assert leaks == {}, leaks

            # Each route that carries evidence out of the claim is refused, audited, and writes nothing.
            cases_before = await env.db.arbitration_case_workspaces.count_documents({})
            export = await env.call("GET", "narrow", f"{BASE}/submission/export?format=docx")
            bundle = await env.call("GET", "narrow", f"{BASE}/evidence-bundle")
            run = await env.call("POST", "narrow", "/api/claims/escalation/run", json={
                "claim_ids": [CLAIM], "new_case": {"title": "Should not exist"}, "idempotency_key": "narrow-run-0001",
                "acknowledged_weak": [CLAIM],
            })
            for response in (export, bundle, run):
                assert response.status_code == 403 and _code(response) == "evidence_restricted", response.text[:300]
                assert response.json()["detail"]["restricted_items"] == 2
            assert await env.db.arbitration_case_workspaces.count_documents({}) == cases_before
            assert (await env.db.claims.find_one({"_id": CLAIM})).get("arbitration_case_id") is None
            for action in ("claim.submission_export_refused", "claim.evidence_export_refused", "claim.escalation_refused"):
                event = await env.db.audit_events.find_one({"action": action, "actor_id": "user-narrow"})
                assert event and event["after"]["restricted_items"] == 2, action

            preview = await _ok(await env.call(
                "POST", "narrow", "/api/claims/escalation/preview", json={"claim_ids": [CLAIM]}))
            assert preview["can_escalate"] is False
            assert any("2 items of its evidence are restricted for you" in item for item in preview["blockers"])

            # With access to all of it, the same calls go through.
            assert (await env.call("GET", "org_admin", f"{BASE}/submission/export?format=docx")).status_code == 200
            assert (await env.call("GET", "org_admin", f"{BASE}/evidence-bundle")).status_code == 200

    _run(scenario())
