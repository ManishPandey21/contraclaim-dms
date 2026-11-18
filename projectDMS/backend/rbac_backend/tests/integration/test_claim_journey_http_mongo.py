"""4B-44: the claim journey, start to pleading, over HTTP only. Real routes, real
RBAC and PolicyService, a real MongoDB replica set.

The same calls ``client/e2e/staging/claim-journey-api.spec.ts`` makes against a
deployed stack, here against the production ``app`` in process (the
``test_claim_routes_real_deps_mongo._env`` harness: no dependency override). The
language model is the only thing scripted: the Claims skill's head test answers
from a fixed reply, and the Letter Bench's first analysis is skipped, as
``test_determination_letters_mongo.py`` does. Everything else is a request.

Two projects of one organisation:

- A1 acts for the Contractor: start, heads, notice, delay, quantum, submission,
  escalation into an arbitration case and the pleading path;
- A2 acts for the Engineer: a determination authored, submitted and approved by a
  different person, then the Letter Bench opened pre-filled from it.

What is seeded directly, because no HTTP route in this repository creates it (each
is listed in the 4B-44 report): organisations, projects, users and their roles; the
subscriptions; the clause index of the contract (built by contract ingestion, which
needs the document pipeline); two letters (built by upload and extraction); and a
published copy of the bundled Claims skill (publication is the 4B-05 review
workflow, outside the claim journey).

Where the HTTP path toward an approved Statement of Claim stops, the test asserts
the refusal and its stated reason, so a change to that boundary is a red test,
not a silent green one.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set; the harness also
reads ``RELATIONSHIP_CL2_MONGODB_URI``'s seed). Run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

import httpx
import pytest

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import MONGODB_URI_ENV, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    DOC_IN_ID,
    DOC_OUT_ID,
    ORG_A,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv(MONGODB_URI_ENV), reason=f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set"),
]

CLAIMS = "/api/claims"
NOTICE = "/api/claims/notice/requirements"
CASES = "/api/arbitration/cases"
DRAFTS = "/api/arbitration/drafts"

#: Contractor project (A1): org_admin works the claim; project_admin is the second person.
CONTRACTOR = PROJ_A1
#: Engineer project (A2).
ENGINEER = PROJ_A2

AUTHORITY = ["dms.claim.view", "dms.claim.edit", "dms.claim.determine"]
DRAFTER = ["dms.claim.view", "drafting.request.create", "drafting.request.view"]
ARBITRATION_REVIEWER = ["dms.arbitration.view", "dms.arbitration.approve"]

CLAUSE_TEXT = (
    "If the Contractor considers himself to be entitled to any extension of the Time for Completion, "
    "the Contractor's notice shall be given to the Engineer, describing the event or circumstance giving "
    "rise to the claim. The notice shall be given as soon as practicable, and not later than 28 days "
    "after the Contractor became aware, or should have become aware, of the event or circumstance."
)
QUOTE = "The notice shall be given as soon as practicable, and not later than 28 days after the Contractor became aware"

#: The scripted model's answer to the head test: every element supported.
HEAD_TEST_REPLY = {
    **{
        key: {"state": "supported", "reason": "The record establishes this.", "source_ids": []}
        for key in ("event", "notice", "contractual_basis", "causation", "quantum")
    },
    "classification": "compensable",
    "clause_refs": ["8.4", "20.1"],
    "residual_risks": ["Programme logic not checked."],
    "missing_information": [],
}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _ok(response: httpx.Response, status: int = 200) -> Any:
    assert response.status_code == status, (response.request.method, str(response.request.url), response.status_code,
                                            response.text[:600])
    return response.json() if response.content else None


def _code(response: httpx.Response) -> str:
    detail = response.json().get("detail")
    return str(detail.get("code", "")) if isinstance(detail, dict) else ""


def _refused(response: httpx.Response) -> None:
    """A refusal across a tenant or project boundary: never data, never a 500."""
    assert response.status_code in {403, 404}, (str(response.request.url), response.status_code, response.text[:300])


async def _anonymous(env: Any, method: str, path: str, project: str = CONTRACTOR, **kwargs: Any) -> httpx.Response:
    return await env.client.request(method, path, headers={"X-Proj-Id": project}, **kwargs)


async def _add_member(db: Any, persona: str, permissions: List[str], project: str) -> None:
    """``_add_actor``, for a member of one named project."""
    await _add_actor(db, persona, f"role-{persona}", permissions)
    await db.users.update_one({"_id": f"user-{persona}"}, {"$set": {"projects": [project]}})


async def _preconditions(env: Any, monkeypatch: pytest.MonkeyPatch) -> Dict[str, List[str]]:
    """What no HTTP route creates; see the module docstring. Returns the model's call log."""
    from rbac_backend.core.config import settings
    from rbac_backend.services.letter_drafting.bench import LetterBenchService
    from rbac_backend.services.skills import executor as executor_module
    from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY, system_skill_version

    calls: Dict[str, List[str]] = {"answered": [], "declined": []}

    async def scripted_model(prompt: str, max_tokens: int) -> str:
        # The head test is the one prompt that asks for a classification of a head.
        if '"classification"' in prompt or "classification:" in prompt or "- classification" in prompt:
            calls["answered"].append("head_test")
            return json.dumps(HEAD_TEST_REPLY)
        calls["declined"].append(prompt[:80])
        raise RuntimeError("scripted model: no reply for this prompt")

    monkeypatch.setattr(executor_module, "_default_llm", scripted_model)

    async def _no_analysis(self: Any, letter_id: str, current_user: Any) -> None:
        return None  # the Letter Bench's first analysis is a model call

    monkeypatch.setattr(LetterBenchService, "run_dissect", _no_analysis)
    monkeypatch.setattr(settings, "LETTER_BENCH_ROLLOUT_MODE", "primary")

    db = env.db
    # A plan that carries claims, arbitration and drafting.
    await db.subscriptions.update_many({"organization_id": ORG_A}, {"$set": {"plan_code": "claims_commercial_desk"}})
    # The contract's clause index (contract ingestion), for both projects.
    for project in (CONTRACTOR, ENGINEER):
        await db.contract_clauses.insert_many([
            {"_id": f"cl-20-1-{project}", "org_id": ORG_A, "project_id": project, "is_current": True,
             "is_authorised_for_ai": True, "clause_no": "20.1", "clause_uid": f"uid-20.1-{project}",
             "clause_title": "Contractor's Claims", "text": CLAUSE_TEXT, "cleaned_text": CLAUSE_TEXT,
             "document_id": "doc-gcc", "document_type": "GCC"},
            {"_id": f"cl-8-4-{project}", "org_id": ORG_A, "project_id": project, "is_current": True,
             "is_authorised_for_ai": True, "clause_no": "8.4", "clause_uid": f"uid-8.4-{project}",
             "clause_title": "Extension of Time for Completion",
             "text": "The Contractor shall be entitled subject to Sub-Clause 20.1 to an extension of the Time for Completion.",
             "cleaned_text": "The Contractor shall be entitled to an extension of the Time for Completion.",
             "document_id": "doc-gcc", "document_type": "GCC"},
        ])
    # The Claims skill, published (the 4B-05 review workflow is not part of this journey).
    draft = system_skill_version(CLAIMS_SKILL_KEY)
    await db.skill_versions.insert_one({**draft.model_dump(by_alias=True, exclude={"id"}), "status": "published"})
    # People.
    for persona in ("eng_author", "eng_reviewer"):
        await _add_member(db, persona, AUTHORITY, ENGINEER)
    await _add_member(db, "eng_drafter", DRAFTER, ENGINEER)
    for persona in ("legal_reviewer_person", "quantum_reviewer_person"):
        await _add_member(db, persona, ARBITRATION_REVIEWER, CONTRACTOR)
        # A built-in project role key: build_scope_query reads row access from it.
        await db.users.update_one({"_id": f"user-{persona}"}, {"$push": {"roles": "projectuser"}})
    return calls


async def _bench(env: Any, claim_id: str, project: str = CONTRACTOR, persona: str = "org_admin") -> Dict[str, Any]:
    return _ok(await env.call("GET", persona, f"{CLAIMS}/{claim_id}/bench", project=project))


async def _version(env: Any, claim_id: str, project: str = CONTRACTOR) -> int:
    return int((await _bench(env, claim_id, project))["bench"]["board_version"])


async def _decide_heads(env: Any, claim_id: str, project: str = CONTRACTOR) -> Dict[str, Any]:
    """A person's call on every element and the classification of every head: what the lock gate asks."""
    board = await _bench(env, claim_id, project)
    for head in board["bench"]["heads"]:
        for key in ("event", "notice", "contractual_basis", "causation", "quantum"):
            proposed = (head["elements"].get(key) or {}).get("proposed_state") or "weak"
            board = _ok(await env.call(
                "PUT", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads/{head['head_id']}/elements/{key}", project=project,
                json={"board_version": board["bench"]["board_version"], "state": proposed,
                      "note": "Handover of the Site was late, so the works started late on the critical path."
                      if key == "causation" else None},
            ))
        board = _ok(await env.call(
            "PATCH", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads/{head['head_id']}", project=project,
            json={"board_version": board["bench"]["board_version"],
                  "classification": head.get("proposed_classification") or "compensable"},
        ))
    return board


async def _latest_version(env: Any, draft_id: str) -> Dict[str, Any]:
    versions = _ok(await env.call("GET", "org_admin", f"{DRAFTS}/{draft_id}/versions"))
    latest = max(versions, key=lambda item: item["version"])
    structured = latest.get("structured_output") or {}
    return {"version": latest["version"], "validation_status": latest.get("validation_status"),
            "blockers": [str(item) for item in structured.get("approval_blockers") or []],
            "head_gaps": list(structured.get("pleaded_head_gaps") or [])}


async def _lock(env: Any, claim_id: str, project: str = CONTRACTOR) -> Dict[str, Any]:
    board = _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/lock", project=project,
                               json={"board_version": await _version(env, claim_id, project)}))
    assert board["bench"]["stage"] == "locked", board["gate"]
    return board


# --------------------------------------------------------------------------- #
# The journey
# --------------------------------------------------------------------------- #
def test_the_claim_journey_from_start_to_the_pleading_gate_over_http(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            model = await _preconditions(env, monkeypatch)
            evidence: Dict[str, Any] = {}

            # 1. Start. There is no /claims/start: 4B-20's three-question start is not built.
            assert _ok(await env.call("GET", "org_admin", "/api/claims/bench/availability"))["enabled"] is True
            from rbac_backend.main import app

            assert "/api/claims/start" not in app.openapi()["paths"]
            created = _ok(await env.call("POST", "org_admin", CLAIMS, json={
                "title": "Delayed handover of Site", "type": "eot", "status": "notified",
                "description": "Right of way for chainage 12+000 to 18+500 not handed over.",
                "event_date": "2026-03-02T00:00:00", "awareness_date": "2026-03-02T00:00:00",
                "notice_date": "2026-03-20T00:00:00", "amount_claimed": 1250000.5, "eot_days_claimed": 60,
                "contract_clauses": ["Sub-Clause 8.4", "20.1"], "project_id": CONTRACTOR, "organization_id": ORG_A,
            }), 201)
            claim_id = str(created.get("id") or created.get("_id"))
            stored = await env.db.claims.find_one({"_id": claim_id})
            assert stored["project_id"] == CONTRACTOR and stored["organization_id"] == ORG_A and stored["status"] == "notified"
            # Tenant and identity boundaries at the start.
            _refused(await env.call("GET", "foreign_org_admin", f"{CLAIMS}/{claim_id}", project=PROJ_B1))
            _refused(await env.call("GET", "other_project_admin", f"{CLAIMS}/{claim_id}", project=PROJ_A2))
            assert (await _anonymous(env, "GET", f"{CLAIMS}/{claim_id}")).status_code == 401
            assert (await _anonymous(env, "POST", CLAIMS, json={"title": "x", "project_id": CONTRACTOR})).status_code == 401

            # 2. Heads: open the bench, add a head, test it, lock.
            board = _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/open"))
            assert [h["head_id"] for h in board["bench"]["heads"]] == ["H1"]
            assert board["bench"]["heads"][0]["clause_refs"] == ["8.4", "20.1"]
            board = _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads", json={
                "board_version": board["bench"]["board_version"], "title": "Idling of plant during the hold",
                "kind": "cost", "event_summary": "Plant stood idle while access was withheld.",
                "clause_refs": ["8.4"], "amount_claimed": 500000,
            }))
            assert [h["head_id"] for h in board["bench"]["heads"]] == ["H1", "H2"]
            _refused(await env.call("POST", "foreign_org_admin", f"{CLAIMS}/{claim_id}/bench/heads", project=PROJ_B1,
                                    json={"board_version": board["bench"]["board_version"], "title": "x"}))
            assert (await _anonymous(env, "GET", f"{CLAIMS}/{claim_id}/bench")).status_code == 401

            # The head test reads the project's position: A1 acts for the Contractor (set over HTTP below for A2).
            for head_id in ("H1", "H2"):
                board = _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads/{head_id}/test",
                                           json={"board_version": board["bench"]["board_version"]}))
            heads = {h["head_id"]: h for h in board["bench"]["heads"]}
            assert model["answered"] == ["head_test", "head_test"], model
            for head in heads.values():
                assert head["step_id"] and head["test_stale"] is False
                assert {e["proposed_by"] for e in head["elements"].values()} == {"skill"}, head["elements"]
                # Proposes; decides nothing (held to the records by apply_fact_guards).
                assert all(e["state"] is None for e in head["elements"].values())
            step = _ok(await env.call("GET", "org_admin", f"{CLAIMS}/{claim_id}/bench/steps/{heads['H1']['step_id']}"))
            assert step["degraded"] is False and step["role"] == "contractor" and step["skill_version"], step
            # Nothing decided yet: the lock is refused with the plain reason.
            refused = await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/lock",
                                     json={"board_version": board["bench"]["board_version"]})
            assert refused.status_code == 409 and "still need your call" in refused.text, refused.text[:300]
            board = await _decide_heads(env, claim_id)
            assert board["gate"]["can_lock"] is True, board["gate"]
            board = await _lock(env, claim_id)
            evidence["locked_at_version"] = board["bench"]["board_version"]

            # 3. Notice: recorded by one person, verified by another.
            requirement = _ok(await env.call("POST", "org_admin", NOTICE, json={
                "project_id": CONTRACTOR, "claim_type": "eot", "period_days": 28, "trigger": "awareness",
                "clause_no": "20.1", "quote": QUOTE,
            }), 201)
            requirement_id = str(requirement.get("id") or requirement.get("_id"))
            assert requirement["origin"] == "clause_index" and requirement["verification"] == "extracted_unverified"
            own = await env.call("POST", "org_admin", f"{NOTICE}/{requirement_id}/verify")
            assert own.status_code == 403 and _code(own) == "notice_self_verification", own.text[:300]
            _refused(await env.call("POST", "foreign_org_admin", f"{NOTICE}/{requirement_id}/verify", project=PROJ_B1))
            assert (await _anonymous(env, "POST", f"{NOTICE}/{requirement_id}/verify")).status_code == 401
            verified = _ok(await env.call("POST", "project_admin", f"{NOTICE}/{requirement_id}/verify"))
            assert verified["verification"] == "verified"
            assert verified["verified_by"] == "user-project_admin" and verified["created_by"] == "user-org_admin"
            current = _ok(await env.call("GET", "org_admin", NOTICE, params={"claim_type": "eot"}))
            assert current["current"]["verification"] == "verified"

            # 4. Delay: a hindrance on the register, linked to H1; the delay view and its net EOT.
            hindrance = _ok(await env.call("POST", "org_admin", "/api/hindrances", json={
                "project_id": CONTRACTOR, "organization_id": ORG_A, "title": "Right of way not handed over",
                "start_date": "2026-03-02T00:00:00", "end_date": "2026-04-30T00:00:00",
                "responsibility": "employer", "critical_path_impact": True,
            }), 201)
            hindrance_id = str(hindrance.get("id") or hindrance.get("_id"))
            candidates = _ok(await env.call("GET", "org_admin", f"{CLAIMS}/{claim_id}/bench/delay/candidates"))
            assert any(c["target_id"] == hindrance_id and c["target_type"] == "hindrance" for c in candidates)
            link = {"target_type": "hindrance", "target_id": hindrance_id}
            # D-22 (JG-3, fixed): the board is locked, so the link is refused until someone who
            # may manage the claim reopens it with a reason, which is audited.
            refused = await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads/H1/delay/links", json={
                "board_version": await _version(env, claim_id), **link,
            })
            assert refused.status_code == 409 and _code(refused) == "claim_board_locked", refused.text[:300]
            assert (await _bench(env, claim_id))["bench"]["stage"] == "locked"
            _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/unlock", json={
                "board_version": await _version(env, claim_id), "reason": "The hindrance register now records the access delay",
            }))
            assert await env.db.audit_events.count_documents({"action": "claim.bench.unlocked", "resource_id": claim_id}) == 1
            delay = _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads/H1/delay/links", json={
                "board_version": await _version(env, claim_id), **link,
            }))
            (event,) = delay["events"]
            assert (await _bench(env, claim_id))["bench"]["stage"] == "heads"
            assert "H1: choose the delay analysis method" in delay["blockers"]
            delay = _ok(await env.call("PUT", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads/H1/delay/settings", json={
                "board_version": await _version(env, claim_id), "method": "as_planned_vs_as_built",
            }))
            delay = _ok(await env.call(
                "PUT", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads/H1/delay/events/{event['event_id']}",
                json={"board_version": await _version(env, claim_id), "risk_owner": "employer", "criticality": "critical"},
            ))
            read = _ok(await env.call("GET", "project_admin", f"{CLAIMS}/{claim_id}/bench/heads/H1/delay"))
            assert read["blockers"] == [], read["blockers"]
            net = read["net"]
            assert net["settled"] is True and net["days"] == read["employer_critical_days"] > 0, read
            assert sum(row["days"] for row in read["net_table"]) == net["days"], read["net_table"]
            assert read["compensable_periods"], "an Employer critical delay gives a compensable period"
            evidence["net_eot_days"] = net["days"]
            _refused(await env.call("GET", "foreign_org_admin", f"{CLAIMS}/{claim_id}/bench/heads/H1/delay", project=PROJ_B1))

            # 5. Quantum: one line with its record, and the total in paise.
            linked = _ok(await env.call("POST", "org_admin", f"/api/entities/claim/{claim_id}/document-links:batch", json={
                "links": [{"document_id": DOC_OUT_ID, "relationship_role": "correspondence"},
                          {"document_id": DOC_IN_ID, "relationship_role": "notice"}],
            }), 201)
            assert linked is not None
            quantum = _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads/H2/quantum/lines", json={
                "board_version": await _version(env, claim_id), "category": "direct",
                "description": "Hire of idle excavator, 12 to 30 March", "amount": "500000.25",
                "records": [{"document_id": DOC_OUT_ID, "role": "support"}],
            }))
            assert quantum["claimed_total_paise"] == 50000025 and quantum["supported_total_paise"] == 50000025, quantum
            (line,) = quantum["lines"]
            assert line["locked"] is False and line["lock_reasons"] == []
            total = _ok(await env.call("GET", "project_admin", f"{CLAIMS}/{claim_id}/bench/heads/H2/quantum"))
            assert total["claimed_total_paise"] == 50000025 and total["currency"] == "INR"
            evidence["quantum_total_paise"] = total["claimed_total_paise"]
            assert (await _anonymous(env, "GET", f"{CLAIMS}/{claim_id}/bench/heads/H2/quantum")).status_code == 401

            # Re-decide what the edits made stale, and lock again for the submission.
            board = await _bench(env, claim_id)
            if not board["gate"]["can_lock"]:
                stale = [h["head_id"] for h in board["bench"]["heads"] if h["test_stale"]]
                for head_id in stale:
                    _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads/{head_id}/test",
                                       json={"board_version": await _version(env, claim_id)}))
                board = await _bench(env, claim_id)
                assert board["gate"]["can_lock"] is True, board["gate"]
            await _lock(env, claim_id)

            # 6. Determination, on the Engineer's project (A2).
            _ok(await env.call("PATCH", "org_admin", f"/api/projects/{ENGINEER}/contractual-capacity",
                               project=ENGINEER, json={"contractual_capacity": "engineer"}))
            received = _ok(await env.call("POST", "org_admin", CLAIMS, project=ENGINEER, json={
                "title": "Contractor's claim: delayed handover of Site", "type": "eot", "status": "notified",
                "event_date": "2026-03-02T00:00:00", "notice_date": "2026-03-20T00:00:00",
                "amount_claimed": 1250000.5, "eot_days_claimed": 60, "contract_clauses": ["8.4"],
                "project_id": ENGINEER, "organization_id": ORG_A,
            }), 201)
            engineer_claim = str(received.get("id") or received.get("_id"))
            _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{engineer_claim}/bench/open", project=ENGINEER))
            base = f"{CLAIMS}/{engineer_claim}/determinations"
            listed = _ok(await env.call("GET", "eng_author", base, project=ENGINEER))
            assert listed["perspective"] == "engineer" and listed["can_author"] is True, listed
            # Without the authority by name, no determination (D-12), even as an organisation administrator.
            assert (await env.call("POST", "org_admin", base, project=ENGINEER, json={"origin": "authored"})).status_code == 403
            view = _ok(await env.call("POST", "eng_author", base, project=ENGINEER, json={"origin": "authored"}))
            det = view["determination"]
            view = _ok(await env.call("POST", "eng_author", f"{base}/{det['_id']}/propose", project=ENGINEER,
                                      json={"version": det["version"]}))
            det = view["determination"]
            for finding in det["heads"]:
                view = _ok(await env.call(
                    "PUT", "eng_author", f"{base}/{det['_id']}/heads/{finding['head_id']}", project=ENGINEER,
                    json={"version": view["determination"]["version"], "result": "partial", "days": 10,
                          "amount": 1250000.5, "findings": "Access was given ten days late; the rest is not shown."},
                ))
            det = view["determination"]
            submitted = _ok(await env.call("POST", "eng_author", f"{base}/{det['_id']}/submit", project=ENGINEER,
                                           json={"version": det["version"], "reviewer_id": "user-eng_reviewer"}))
            det = submitted["determination"]
            assert det["status"] == "in_review" and det["reviewer_id"] == "user-eng_reviewer"
            own = await env.call("POST", "eng_author", f"{base}/{det['_id']}/approve", project=ENGINEER,
                                 json={"version": det["version"]})
            # The author's own approval: refused with a plain reason (a bare string, no code).
            assert own.status_code == 403 and "made its findings" in own.text, own.text[:300]
            _refused(await env.call("POST", "foreign_org_admin", f"{base}/{det['_id']}/approve", project=PROJ_B1,
                                    json={"version": det["version"]}))
            approved = _ok(await env.call("POST", "eng_reviewer", f"{base}/{det['_id']}/approve", project=ENGINEER,
                                          json={"version": det["version"]}))
            assert approved["determination"]["status"] == "approved"
            assert approved["determination"]["decided_by"] == "user-eng_reviewer"
            # The Letter Bench, pre-filled from the approved determination.
            opened = _ok(await env.call("POST", "eng_drafter", "/api/letters/bench/open", project=ENGINEER,
                                        json={"trigger_type": "determination", "trigger_id": det["_id"]}))
            assert opened["created"] is True
            letter_path = f"/api/letters/{opened['letter_id']}/bench"
            # JG-2 (fixed): the client drafter who opened the letter reads its board, read-only.
            letter_bench = _ok(await env.call("GET", "eng_drafter", letter_path, project=ENGINEER))
            assert letter_bench["read_only"] is True
            board_text = json.dumps(letter_bench)
            assert "10 days extension of time" in board_text and "INR 12,50,000.50" in board_text, board_text[:600]
            _refused(await env.call("GET", "foreign_org_admin", letter_path, project=PROJ_B1))
            assert (await _anonymous(env, "POST", "/api/letters/bench/open", project=ENGINEER,
                                     json={"trigger_type": "determination", "trigger_id": det["_id"]})).status_code == 401
            _refused(await env.call("POST", "foreign_org_admin", "/api/letters/bench/open", project=PROJ_B1,
                                    json={"trigger_type": "determination", "trigger_id": det["_id"]}))

            # 7. Submission (Contractor project): compose from the locked board; every paragraph says where it is from.
            submission = _ok(await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/submission/compose", json={}))
            assert submission["exists"] is True, submission
            paragraphs = [p for view in submission["sections"] for p in view["section"]["paragraphs"]]
            assert paragraphs, submission
            unsourced = [p["text"][:60] for p in paragraphs if not p["sources"]]
            assert unsourced == [], unsourced
            kinds = {s["kind"] for p in paragraphs for s in p["sources"]}
            assert {"head", "quantum", "delay"} <= kinds, kinds
            read_back = _ok(await env.call("GET", "project_admin", f"{CLAIMS}/{claim_id}/submission"))
            assert read_back["exists"] is True and read_back["stale_sections"] == 0
            evidence["submission_export"] = {"can_export": read_back["can_export"],
                                             "approved_for_issue": read_back["approved_for_issue"],
                                             "cannot_export_reason": read_back["cannot_export_reason"]}
            _refused(await env.call("GET", "foreign_org_admin", f"{CLAIMS}/{claim_id}/submission", project=PROJ_B1))
            evidence["submission_source_kinds"] = sorted(kinds)

            # 8. Escalation: preview, then run into a new case.
            preview = _ok(await env.call("POST", "org_admin", "/api/claims/escalation/preview", json={"claim_ids": [claim_id]}))
            (carry,) = preview["claims"]
            assert preview["can_escalate"] is True, preview["blockers"]
            assert not any("No notice letter" in item for item in carry["weak"]), carry["weak"]
            assert await env.db.arbitration_cases.count_documents({}) == 0, "a preview writes nothing"
            _refused(await env.call("POST", "foreign_org_admin", "/api/claims/escalation/preview", project=PROJ_B1,
                                    json={"claim_ids": [claim_id]}))
            assert (await _anonymous(env, "POST", "/api/claims/escalation/preview",
                                     json={"claim_ids": [claim_id]})).status_code == 401
            run = _ok(await env.call("POST", "org_admin", "/api/claims/escalation/run", json={
                "claim_ids": [claim_id], "new_case": {"title": "ABC Infra v. Authority"},
                "idempotency_key": f"journey-{claim_id[:8]}",
            }), 201)
            case_id = run["case_id"]
            assert run["created_case"] is True
            # The claim is frozen: the board no longer changes.
            frozen = await env.call("POST", "org_admin", f"{CLAIMS}/{claim_id}/bench/heads", json={
                "board_version": await _version(env, claim_id), "title": "Late head"})
            assert frozen.status_code == 409 and "frozen" in frozen.text, frozen.text[:300]

            # The escalated heads appear in the case's claim matrix, awaiting review.
            matrix = _ok(await env.call("GET", "org_admin", f"{CASES}/{case_id}/claim-matrix"))
            assert len(matrix) == 2, matrix
            row = next(r for r in matrix if r.get("claim_head", "").startswith("Delayed handover"))
            assert row["notice_ids"] == [DOC_IN_ID] and row["clause_ids"] and row["facts"], row
            _refused(await env.call("GET", "foreign_org_admin", f"{CASES}/{case_id}/claim-matrix", project=PROJ_B1))

            # The pleading path: a Statement of Claim in the case, refused approval and export while gates are open.
            draft = _ok(await env.call("POST", "org_admin", DRAFTS, json={
                "case_id": case_id, "organization_id": ORG_A, "project_id": CONTRACTOR,
                "draft_type": "statement_of_claim", "party_role": "claimant", "title": "Statement of Claim",
            }), 201)
            draft_id = str(draft.get("id") or draft.get("_id"))
            _ok(await env.call("POST", "org_admin", f"{DRAFTS}/{draft_id}/versions", json={
                "full_markdown": "# STATEMENT OF CLAIM\n\n1. The Respondent delayed access to the Site.\n"}), 201)
            gates = _ok(await env.call("GET", "project_admin", f"{DRAFTS}/{draft_id}/citations"))["action_gates"]
            approve_gate = gates["approve"]
            assert approve_gate["allowed"] is False, approve_gate
            approve = await env.call("POST", "project_admin", f"{DRAFTS}/{draft_id}/approve")
            assert approve.status_code == 409, approve.text[:400]
            detail = approve.json()["detail"]
            assert detail["code"] == "draft_action_refused"
            assert {r["code"] for r in detail["detail"]["reasons"]} == {r["code"] for r in approve_gate["reasons"]}
            assert all(r["message"] for r in detail["detail"]["reasons"]), "each refusal says why in words"
            export = await env.call("GET", "project_admin", f"{DRAFTS}/{draft_id}/export/docx")
            assert export.status_code == 409 and "not_approved" in {r["code"] for r in export.json()["detail"]["detail"]["reasons"]}
            evidence["approve_refused_with"] = sorted(r["code"] for r in approve_gate["reasons"])
            first = await _latest_version(env, draft_id)
            assert any("still awaiting approval" in b for b in first["blockers"]), first["blockers"]

            # The escalated heads are approved through the case's own review path, by a legal reviewer and
            # a different quantum reviewer, each granted the reviewer role over HTTP by the organisation admin.
            for persona, role in (("legal_reviewer_person", "legal_reviewer"), ("quantum_reviewer_person", "quantum_reviewer")):
                _ok(await env.call("POST", "org_admin", "/api/reviewer-assignments", json={
                    "user_id": f"user-{persona}", "role_key": role, "scope_type": "project",
                    "organization_id": ORG_A, "project_id": CONTRACTOR, "reason": "4B-44 journey reviewer",
                }), 201)
            for matrix_row in matrix:
                row_path = f"{CASES}/{case_id}/claim-matrix/{matrix_row.get('_id') or matrix_row.get('id')}/review"
                for persona, role in (("legal_reviewer_person", "legal"), ("quantum_reviewer_person", "quantum")):
                    reviewed = _ok(await env.call("POST", persona, row_path, json={"action": "approve", "reviewer_role": role}))
                assert reviewed["approval_status"] == "approved", reviewed
            _ok(await env.call("POST", "org_admin", f"{DRAFTS}/{draft_id}/versions", json={
                "full_markdown": "# STATEMENT OF CLAIM\n\n1. The Respondent delayed access to the Site.\n"}), 201)
            second = await _latest_version(env, draft_id)
            assert second["version"] == 2
            assert not any("awaiting approval" in b or "none is approved" in b for b in second["blockers"]), second["blockers"]
            # Where the HTTP path stops (each reported in 4B-44):
            # (a) GAP: the time head H1 escalated with no record behind its 60 days. Its days rest on a
            #     linked hindrance and a delay assessment, neither of which escalation carries as evidence.
            assert second["head_gaps"] == [
                {"claim": f"{claim_id}/H1", "missing": ["records supporting the amount or days pleaded"]}
            ], second["head_gaps"]
            # (b) pleading text written by hand has no ledger citations; a cited, validated version is
            #     what generation produces, and generation is a model call.
            assert any("without a same-sentence source citation" in b for b in second["blockers"]), second["blockers"]
            assert second["validation_status"] != "passed"
            gates = _ok(await env.call("GET", "project_admin", f"{DRAFTS}/{draft_id}/citations"))["action_gates"]
            assert gates["approve"]["allowed"] is False
            # (c) after the gate: case readiness approval, and a governed workflow's plan and legal-review
            #     approvals bound to the version, are checked only on action.
            assert gates["approve"]["checked_on_action"] == ["Case readiness approval", "Plan approval", "Legal review approval"]
            refused = await env.call("POST", "project_admin", f"{DRAFTS}/{draft_id}/approve")
            assert refused.status_code == 409 and _code(refused) == "draft_action_refused", refused.text[:300]
            readiness = _ok(await env.call("GET", "org_admin", f"{CASES}/{case_id}/readiness", params={"draft_id": draft_id}))
            open_checks = sorted(b["check_key"] for b in readiness["blockers"])
            assert {"document_index_verified", "issue_framing", "limitation_analysis",
                    "pre_arbitration_compliance"} <= set(open_checks), open_checks
            evidence["pleading_stops_at"] = {"blockers": len(second["blockers"]), "readiness_open": open_checks}
            print("4B-44 journey evidence:", json.dumps(evidence, sort_keys=True))

    _run(scenario())
