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

Before the case is prepared, the Statement of Claim's Approve and Export are refused
with their stated reasons. Then (JG-5) counsel prepares the case over HTTP, the
governed workflow runs on the default engine with the arbitration engine rollout
off, the deterministic draft is revised with every sentence cited, and each gate is
approved by the person holding its role, ending in an approved, exported Statement
of Claim. No model call is made on the pleading path.

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
from bson import ObjectId

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import MONGODB_URI_ENV, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    DOC_IN,
    DOC_IN_ID,
    DOC_OUT,
    DOC_OUT_ID,
    ORG_A,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    _document,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv(MONGODB_URI_ENV), reason=f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set"),
]

#: A site diary linked to the hindrance only (not to the claim): the record behind H1's days (JG-4).
SITE_DIARY = ObjectId()
SITE_DIARY_ID = str(SITE_DIARY)
#: The ingested conditions of contract behind each project's clause index.
GCC_DOCUMENTS = {PROJ_A1: str(ObjectId()), PROJ_A2: str(ObjectId())}
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
    # The contract's clause index (contract ingestion), for both projects, with the
    # ingested conditions of contract it was extracted from.
    for project in (CONTRACTOR, ENGINEER):
        gcc_id = GCC_DOCUMENTS[project]
        await db.documents.insert_one(_document(
            ObjectId(gcc_id), organization_id=ORG_A, project_id=project, upload_type="Contract",
            subject="General Conditions of Contract", letter_no=f"GCC-{project}", sender="Employer",
        ))
        await db.contract_clauses.insert_many([
            {"_id": f"cl-20-1-{project}", "org_id": ORG_A, "project_id": project, "is_current": True,
             "is_authorised_for_ai": True, "clause_no": "20.1", "clause_uid": f"uid-20.1-{project}",
             "clause_title": "Contractor's Claims", "text": CLAUSE_TEXT, "cleaned_text": CLAUSE_TEXT,
             "document_id": gcc_id, "document_type": "GCC"},
            {"_id": f"cl-8-4-{project}", "org_id": ORG_A, "project_id": project, "is_current": True,
             "is_authorised_for_ai": True, "clause_no": "8.4", "clause_uid": f"uid-8.4-{project}",
             "clause_title": "Extension of Time for Completion",
             "text": "The Contractor shall be entitled subject to Sub-Clause 20.1 to an extension of the Time for Completion.",
             "cleaned_text": "The Contractor shall be entitled to an extension of the Time for Completion.",
             "document_id": gcc_id, "document_type": "GCC"},
        ])
    # The two letters' stored files: upload writes each document's storage key, which the
    # filing export checks (exhibits must resolve to a file). No bytes are read by this journey.
    for letter in (DOC_IN, DOC_OUT):
        await db.documents.update_one({"_id": letter}, {"$set": {"filepath_s3": f"documents/{letter}.pdf"}})
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
# JG-5: the case prepared and the governed workflow walked to an approved pleading
# --------------------------------------------------------------------------- #

#: Case people in the Contractor project, each holding one reviewer role by assignment.
#: Two senior legal approvers, because the plan's author (whoever approved readiness,
#: which builds the plan) may not approve the plan, and the plan's approver (who
#: generates the draft) may not approve the draft.
CASE_PEOPLE = {
    "contracts_reviewer_person": ("contract_reviewer", ARBITRATION_REVIEWER),
    "delay_reviewer_person": ("delay_reviewer", ARBITRATION_REVIEWER),
    "senior_a_person": ("senior_legal_approver", ARBITRATION_REVIEWER + ["dms.arbitration.generate"]),
    "senior_b_person": ("senior_legal_approver", ARBITRATION_REVIEWER + ["dms.arbitration.generate"]),
    "export_person": ("export_authorizer", ["dms.arbitration.view", "dms.arbitration.export"]),
}

#: Matrix reviewer role (row review) -> the person who holds it.
ROW_REVIEWERS = {
    "legal": "legal_reviewer_person",
    "quantum": "quantum_reviewer_person",
    "contracts": "contracts_reviewer_person",
    "delay": "delay_reviewer_person",
    "reviewer": "legal_reviewer_person",
}

#: Who reviews a row of each matrix when the row names no roles (the service's defaults).
DEFAULT_ROW_ROLES = {
    "document-index": ["contracts"], "chronology-matrix": ["delay"], "clause-matrix": ["legal", "contracts"],
    "issue-matrix": ["legal"], "claim-matrix": ["legal", "quantum"], "quantum-annexures": ["quantum"],
    "notice-compliance": ["legal", "contracts"], "jurisdiction-matrix": ["legal"], "expert-alignment": ["reviewer"],
}


async def _case_people(env: Any) -> None:
    for persona, (role_key, permissions) in CASE_PEOPLE.items():
        await _add_member(env.db, persona, permissions, CONTRACTOR)
        await env.db.users.update_one({"_id": f"user-{persona}"}, {"$push": {"roles": "projectuser"}})
        _ok(await env.call("POST", "org_admin", "/api/reviewer-assignments", json={
            "user_id": f"user-{persona}", "role_key": role_key, "scope_type": "project",
            "organization_id": ORG_A, "project_id": CONTRACTOR, "reason": "JG-5 journey case team",
        }), 201)


async def _approve_row(env: Any, case_id: str, slug: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Every role the row requires approves it, each by the person who holds that role."""
    row_id = row.get("_id") or row.get("id")
    roles = [str(r) for r in row.get("review_required_roles") or []] or DEFAULT_ROW_ROLES[slug]
    reviewed = row
    for role in roles:
        reviewed = _ok(await env.call("POST", ROW_REVIEWERS[role], f"{CASES}/{case_id}/{slug}/{row_id}/review",
                                      json={"action": "approve", "reviewer_role": role}))
    return reviewed


async def _add_row(env: Any, case_id: str, slug: str, body: Dict[str, Any]) -> Dict[str, Any]:
    row = _ok(await env.call("POST", "org_admin", f"{CASES}/{case_id}/{slug}", json=body), 201)
    return await _approve_row(env, case_id, slug, row)


async def _prepare_case(env: Any, case_id: str, claim_rows: List[Dict[str, Any]]) -> None:
    """What counsel does before a pleading can be planned: every readiness check met over HTTP."""
    await _case_people(env)
    # The rows escalation carried, reviewed.
    for slug in ("document-index", "quantum-annexures", "notice-compliance", "chronology-matrix"):
        for row in _ok(await env.call("GET", "org_admin", f"{CASES}/{case_id}/{slug}")):
            if str(row.get("review_status") or "") != "approved":
                await _approve_row(env, case_id, slug, row)
    # The clause the claim rests on, and the issue for the tribunal.
    await _add_row(env, case_id, "clause-matrix", {
        "clause_number": "8.4", "clause_title": "Extension of Time for Completion",
        "clause_text_excerpt": "The Contractor shall be entitled to an extension of the Time for Completion.",
        # The clause escalation carried on the heads, from the contract's clause index.
        # The clause in the contract's clause index (contract ingestion; see _preconditions).
        "clause_source_id": f"cl-8-4-{CONTRACTOR}",
    })
    await _add_row(env, case_id, "issue-matrix", {
        "issue": "Whether the Respondent's late handover of the Site entitles the Claimant to an extension of time and costs.",
    })
    # Limitation and pre-arbitration steps (the jurisdiction agent is optional; counsel can record them).
    await _add_row(env, case_id, "jurisdiction-matrix", {
        "check_type": "limitation", "limitation_status": "within_limitation",
        "summary": "The cause of action arose in 2026; the claim is within three years.",
    })
    await _add_row(env, case_id, "jurisdiction-matrix", {
        "check_type": "pre_arbitration_step", "required": True, "compliance_status": "complete",
        "summary": "Engineer's determination obtained and dispute notice served.",
    })
    # Expert alignment for each pleaded head: delay (concurrency addressed) and quantum (calculation matches).
    for row in claim_rows:
        claim_no = str(row.get("claim_no"))
        # Both heads arise from the late handover, so both rely on the delay analysis.
        await _add_row(env, case_id, "expert-alignment", {
            "expert_type": "delay", "claim_no": claim_no, "concurrency_addressed": True,
            "summary": "Delay analysis addresses concurrency.",
        })
        await _add_row(env, case_id, "expert-alignment", {
            "expert_type": "quantum", "claim_no": claim_no, "calculation_match": True,
            "summary": "Quantum expert's calculation matches the pleaded amount.",
        })


#: The governed workflow's human gates after the matrices: who approves each, in which role.
WORKFLOW_GATES = (
    ("matrix_review", "legal_reviewer_person", "legal_reviewer"),
    ("readiness", "senior_a_person", "senior_legal_approver"),
    ("plan", "senior_b_person", "senior_legal_approver"),
    ("legal_review", "legal_reviewer_person", "legal_reviewer"),
    ("draft", "senior_a_person", "senior_legal_approver"),
    ("export", "export_person", "export_authorizer"),
)

#: The workflow artifact each gate approves (the receipt binds to its hash).
GATE_ARTIFACT = {
    "matrix_review": "matrix_revision_hash", "readiness": "readiness_artifact_hash", "plan": "plan_hash",
    "legal_review": "draft_version_hash", "draft": "draft_version_hash", "export": "draft_version_hash",
}


async def _workflow(env: Any, case_id: str, run_id: str) -> Dict[str, Any]:
    return _ok(await env.call("GET", "org_admin", f"{CASES}/{case_id}/workflows/{run_id}/state"))


async def _approve_gate(env: Any, case_id: str, run_id: str, gate: str, persona: str, role: str, *, ok: bool = True) -> Any:
    """Approve the workflow's current gate as ``persona``; with ``ok=False``, return the raw response."""
    state = await _workflow(env, case_id, run_id)
    response = await env.call("POST", persona, f"{CASES}/{case_id}/workflows/{run_id}/approvals/{gate}", json={
        "state_version": state["state_version"], "artifact_hash": state[GATE_ARTIFACT[gate]],
        "reviewer_role": role, "decision": "approved", "comment": f"JG-5 journey: {gate} approved",
    })
    return _ok(response) if ok else response


def _cite(row: Dict[str, Any]) -> str:
    return f"[{row['source_key']}: {row['citation']}]"


def _statement_of_claim(ledger: List[Dict[str, Any]]) -> str:
    """Counsel's revision: every factual sentence carries a same-sentence citation from the ledger.

    The issue and jurisdiction rows are counsel's analysis, not evidence: they shape the
    pleading but are not cited as its sources.
    """
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for row in ledger:
        by_type.setdefault(str(row.get("source_type")), []).append(row)

    def line(text: str, row: Dict[str, Any]) -> str:
        return f"- {text.rstrip('.')} {_cite(row)}."

    heads = by_type.get("claim_matrix", [])
    lines = ["# STATEMENT OF CLAIM", "", "## Facts", ""]
    lines += [line(f"The record includes {row['label']} as exhibit {row['citation']}", row) for row in by_type.get("document", [])]
    lines += [line(row["label"], row) for row in by_type.get("chronology_entry", [])]
    lines += ["", "## Claims", ""]
    lines += [line(row["label"], row) for row in heads]
    lines += [line("The quantum annexure supports the amount claimed", row) for row in by_type.get("quantum_annexure", [])]
    lines += [line("Notice of the claim was given", row) for row in by_type.get("notice_compliance", [])]
    lines += ["", "## Experts", ""]
    lines += [line(row["label"], row) for row in by_type.get("expert_alignment", [])]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# The journey
# --------------------------------------------------------------------------- #
def test_the_claim_journey_from_start_to_the_pleading_gate_over_http(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            model = await _preconditions(env, monkeypatch)
            evidence: Dict[str, Any] = {}

            # JG-6: which backend build is answering; signed-in callers only.
            build = _ok(await env.call("GET", "org_admin", "/api/build"))
            assert build["service"] == "backend" and build["release_sha"]
            assert (await _anonymous(env, "GET", "/api/build")).status_code == 401
            monkeypatch.setenv("RELEASE_SHA", "379e98f")
            assert _ok(await env.call("GET", "org_admin", "/api/build"))["release_sha"] == "379e98f"

            # 1. Start. Three questions (4B-20, D-24): the claim, a draft, opens on its board with one head.
            assert _ok(await env.call("GET", "org_admin", "/api/claims/bench/availability"))["enabled"] is True
            started = _ok(await env.call("POST", "org_admin", f"{CLAIMS}/start", json={
                "project_id": CONTRACTOR, "organization_id": ORG_A,
                "what_happened": "Right of way for chainage 18+500 to 21+000 was not handed over. Piling stopped.",
                "aware_on": "2026-03-04",
            }), 201)
            assert started["claim"]["status"] == "draft" and started["head_id"] == "H1"
            first_board = _ok(await env.call("GET", "org_admin", f"{CLAIMS}/{started['claim']['_id']}/bench"))
            assert [h["head_id"] for h in first_board["bench"]["heads"]] == ["H1"]
            assert (await _anonymous(env, "POST", f"{CLAIMS}/start", json={})).status_code == 401
            # The full form ("More details") remains; the journey's claim records its notice and figures there.
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
            # The site diary behind the hindrance, linked on the register's own entry and not to the claim.
            await env.db.documents.insert_one({**_document(
                SITE_DIARY, organization_id=ORG_A, project_id=CONTRACTOR, upload_type="incoming",
                subject="Site diary, March 2026", letter_no="SD/03", sender="Site engineer"),
                "filepath_s3": f"documents/{SITE_DIARY_ID}.pdf"})
            _ok(await env.call("POST", "org_admin", f"/api/entities/delay_event/{hindrance_id}/document-links:batch", json={
                "links": [{"document_id": SITE_DIARY_ID, "relationship_role": "supporting_document"}],
            }), 201)
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
            # The author's own approval: refused with a code and a plain reason (JG-7).
            assert own.status_code == 403 and _code(own) == "determination_own_findings", own.text[:300]
            assert "made its findings" in own.json()["detail"]["user_message"]
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
            # JG-4 (fixed): the time head carries the record behind its days, from the linked hindrance.
            assert row["eot_days"] and row["evidence_ids"] == [SITE_DIARY_ID], row
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
            # (a) JG-4 (fixed): every approved head carries what a pleaded head needs, including the
            #     record behind H1's days.
            assert second["head_gaps"] == [], second["head_gaps"]
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
            evidence["pleading_before_preparation"] = {"blockers": len(second["blockers"]), "readiness_open": open_checks}

            # JG-5: counsel prepares the case and the governed workflow is walked to an approved,
            # exported Statement of Claim - over HTTP, with no model call (the draft generator is the
            # deterministic, source-grounded one) and the arbitration engine rollout off (the
            # default engine, arbitration_v2, runs the workflow).
            await _prepare_case(env, case_id, matrix)
            readiness = _ok(await env.call("GET", "org_admin", f"{CASES}/{case_id}/readiness", params={"draft_id": draft_id}))
            assert readiness["status"] == "ready" and readiness["blockers"] == [], readiness["blockers"]
            accepted = _ok(await env.call("POST", "org_admin", f"{CASES}/{case_id}/workflows", json={
                "draft_id": draft_id, "pleading_type": "statement_of_claim",
                # String ids, as a browser sends them; the documents are ObjectId-keyed.
                "selected_document_ids": [DOC_IN_ID, DOC_OUT_ID, SITE_DIARY_ID],
            }), 202)
            assert (accepted["engine"], accepted["rollout_mode"]) == ("arbitration_v2", "off"), accepted
            run_id = accepted["run_id"]
            state = await _workflow(env, case_id, run_id)
            # The case-level rows (escalated and counsel's) count for a draft-linked workflow.
            assert state["current_node"] == "matrix_review_gate" and state["blockers"] == [], state["blockers"]
            _refused(await env.call("GET", "foreign_org_admin", f"{CASES}/{case_id}/workflows/{run_id}/state", project=PROJ_B1))
            state = await _approve_gate(env, case_id, run_id, *WORKFLOW_GATES[0])
            state = await _approve_gate(env, case_id, run_id, *WORKFLOW_GATES[1])
            assert state["current_node"] == "plan_approval_gate", state
            # Whoever approved readiness built the plan, and may not approve it.
            own_plan = await _approve_gate(env, case_id, run_id, "plan", "senior_a_person", "senior_legal_approver", ok=False)
            assert own_plan.status_code == 409 and "separation" in own_plan.text, own_plan.text[:300]
            state = await _approve_gate(env, case_id, run_id, *WORKFLOW_GATES[2])
            # The generated draft is a starting point: its own validation sends it back to the drafter.
            assert (state["current_node"], state["next_action"], state["validation_status"]) == (
                "legal_review_gate", "revise_draft", "blocked"), state
            blocked_review = await _approve_gate(env, case_id, run_id, *WORKFLOW_GATES[3], ok=False)
            assert blocked_review.status_code == 409, blocked_review.text[:300]

            # Counsel's revision, every sentence cited from the draft's own source ledger, then
            # the workflow validates it as the candidate.
            probe = _ok(await env.call("POST", "org_admin", f"{DRAFTS}/{draft_id}/versions",
                                       json={"full_markdown": "# STATEMENT OF CLAIM\n"}), 201)
            revised = _ok(await env.call("POST", "org_admin", f"{DRAFTS}/{draft_id}/versions",
                                         json={"full_markdown": _statement_of_claim(probe["source_ledger"])}), 201)
            assert revised["validation_status"] == "passed", (revised["validation_status"], revised.get("warnings"))
            assert all(row["verification_status"] not in {"authority_denied"} for row in revised["source_ledger"]), \
                [(row["source_key"], row["source_type"]) for row in revised["source_ledger"] if row["verification_status"] == "authority_denied"]
            state = await _workflow(env, case_id, run_id)
            state = _ok(await env.call("POST", "org_admin", f"{CASES}/{case_id}/workflows/{run_id}/resume", json={
                "state_version": state["state_version"], "gate": "legal_review", "decision": "refresh_candidate"}))
            assert (state["next_action"], state["validation_status"], state["validation_blockers"]) == (
                "legal_review", "passed", []), state["validation_blockers"]
            state = await _approve_gate(env, case_id, run_id, *WORKFLOW_GATES[3])
            state = await _approve_gate(env, case_id, run_id, *WORKFLOW_GATES[4])
            approved_draft = _ok(await env.call("GET", "org_admin", f"{DRAFTS}/{draft_id}"))
            assert approved_draft["status"] == "approved" and approved_draft["is_locked"] is True, approved_draft["status"]
            assert approved_draft["approved_by"] == "user-senior_a_person"
            assert approved_draft["approved_version"] == revised["version"]
            state = await _approve_gate(env, case_id, run_id, *WORKFLOW_GATES[5])
            assert (state["status"], state["current_node"]) == ("completed", "complete"), state
            filed = await env.call("GET", "export_person", f"{DRAFTS}/{draft_id}/export/docx")
            assert filed.status_code == 200 and filed.content[:2] == b"PK", (filed.status_code, filed.text[:300])
            _refused(await env.call("GET", "foreign_org_admin", f"{DRAFTS}/{draft_id}/export/docx", project=PROJ_B1))
            # JG-5(f): the version records the context policy it was validated under and the rows it
            # treated as uncited context (case analysis and project registers here).
            stamp = (revised.get("structured_output") or {}).get("context_policy") or {}
            exempted = set((revised.get("structured_output") or {}).get("context_rows_exempted") or [])
            context_keys = {row["source_key"] for row in revised["source_ledger"] if row.get("ledger_context")}
            assert stamp.get("version") == 1 and len(stamp.get("hash") or "") == 64, stamp
            assert exempted and exempted == context_keys, (exempted, context_keys)
            # Every export path: the pleading as PDF, and the filing bundle's summary and source schedule.
            # File storage is the one thing not run here: the three exhibits' stored files read back
            # as fixed bytes under the keys upload wrote.
            from rbac_backend.services.s3_service import S3Service

            stored = {f"documents/{key}.pdf" for key in (DOC_IN_ID, DOC_OUT_ID, SITE_DIARY_ID)}

            async def _stored_file(self: Any, key: str, *args: Any, **kwargs: Any) -> bytes:
                assert key in stored, key
                return b"%PDF-1.4 exhibit " + key.encode()

            monkeypatch.setattr(S3Service, "download_bytes", _stored_file)
            pdf = await env.call("GET", "export_person", f"{DRAFTS}/{draft_id}/export/pdf")
            assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF", (pdf.status_code, pdf.text[:300])
            for fmt, magic in (("docx", b"PK"), ("pdf", b"%PDF")):
                bundle = await env.call("GET", "export_person", f"{CASES}/{case_id}/filing-bundle/{fmt}")
                assert bundle.status_code == 200 and bundle.content[:len(magic)] == magic, (fmt, bundle.status_code, bundle.text[:300])
            manifest = _ok(await env.call("GET", "export_person", f"{CASES}/{case_id}/filing-bundle/manifest"))
            in_bundle = [d for d in manifest.get("drafts") or [] if str(d.get("_id") or d.get("id")) == draft_id]
            assert in_bundle, "the approved pleading is in the filing bundle"
            schedule = in_bundle[0]["approved_version"]["source_ledger"]
            assert schedule and not [row["source_key"] for row in schedule if row.get("ledger_context")], schedule
            assert in_bundle[0]["approved_version"]["source_schedule"]["context_policy"]["hash"] == stamp["hash"]
            zipped = await env.call("GET", "export_person", f"{CASES}/{case_id}/filing-bundle/zip")
            assert zipped.status_code == 200 and zipped.content[:2] == b"PK", (zipped.status_code, zipped.text[:300])
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
                filed_versions = [json.loads(archive.read(name)) for name in archive.namelist()
                                  if name.endswith("/latest-version.json")]
                bundle_manifest = json.loads(archive.read("manifest.json"))
            assert filed_versions, "the bundle carries the approved pleading"
            for version in [*filed_versions, *(d["approved_version"] for d in bundle_manifest["drafts"])]:
                assert not [row["source_key"] for row in version["source_ledger"] if row.get("ledger_context")]
            evidence["pleading_filed"] = {"bundle_bytes": len(zipped.content), "schedule_rows": len(schedule),
                                          "context_rows_exempted": sorted(exempted), "policy": stamp}
            evidence["pleading_approved"] = {"engine": accepted["engine"], "rollout_mode": accepted["rollout_mode"],
                                             "approved_version": approved_draft["approved_version"],
                                             "workflow": state["status"], "export_bytes": len(filed.content)}
            print("4B-44 journey evidence:", json.dumps(evidence, sort_keys=True))

    _run(scenario())
