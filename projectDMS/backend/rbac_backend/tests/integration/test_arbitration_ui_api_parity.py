"""4B-39 (B4): every Approve or filing Export the screen disables, the API refuses, for the
same reason. Real HTTP, MongoDB and RBAC, through the production app.

The drafting page enabled Approve and Export whenever a draft had text; the server
then refused. Now ``GET /arbitration/drafts/{id}/citations`` returns ``action_gates``
(computed by ``services/arbitration_drafting/action_gates.py``), the page disables each
button from it, and approve and filing export run the same gate first. For every code
the gate can give, a draft in that state is built, the gate is read as the screen reads
it, and the action is called: it must be refused with that code (403 for permission).

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from rbac_backend.services.arbitration_drafting.action_gates import APPROVE_CODES, EXPORT_CODES
from rbac_backend.tests.integration.test_arbitration_case_lookup_mongo import DRAFTS, _create
from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

AUTHOR = "user-author"
CALLER = "org_admin"
VIEWER = "arbitration_viewer"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _version(draft_id: str, **over: Any) -> Dict[str, Any]:
    return {
        "_id": f"{draft_id}-v1",
        "draft_id": draft_id,
        "version": 1,
        "full_markdown": "# STATEMENT OF CLAIM\\n\\nThe Respondent delayed access to the Site.",
        "validation_status": "passed",
        "missing_evidence": [],
        "structured_output": {"approval_blockers": []},
        "source_ledger": [],
        "created_by": AUTHOR,
        **over,
    }


def _draft(draft_id: str, case_id: Any, **over: Any) -> Dict[str, Any]:
    return {
        "_id": draft_id,
        "organization_id": ORG_A,
        "project_id": PROJ_A1,
        "case_id": case_id,
        "is_scratch": not case_id,
        "draft_type": "statement_of_claim",
        "party_role": "claimant",
        "title": draft_id,
        "status": "draft",
        "is_locked": False,
        "current_version": 1,
        "created_by": AUTHOR,
        "updated_by": AUTHOR,
        **over,
    }


def _approved(draft_id: str, case_id: Any, **over: Any) -> Dict[str, Any]:
    fields = {"status": "approved", "is_locked": True, "approved_version": 1, "approved_version_id": f"{draft_id}-v1"}
    return _draft(draft_id, case_id, **{**fields, **over})


async def _seed(env: Any, draft: Dict[str, Any], version: Dict[str, Any] | None) -> None:
    await env.db.arbitration_drafts.insert_one(draft)
    if version is not None:
        await env.db.arbitration_draft_versions.insert_one(version)


async def _gate(env: Any, persona: str, draft_id: str, action: str) -> Dict[str, Any]:
    response = await env.call("GET", persona, f"{DRAFTS}/{draft_id}/citations")
    assert response.status_code == 200, response.text[:300]
    return response.json()["action_gates"][action]


def _refused_codes(response: Any) -> set:
    detail = response.json().get("detail")
    assert isinstance(detail, dict) and detail.get("code") == "draft_action_refused", response.text[:300]
    return {reason["code"] for reason in detail["detail"]["reasons"]}


def test_every_disabled_approve_is_refused_by_the_api_for_the_same_reason(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _add_actor(env.db, VIEWER, VIEWER, ["dms.arbitration.view"])
            case_id = (await _create(env))["_id"]
            cases = {
                "no_approve_permission": (VIEWER, _draft("a-perm", case_id), _version("a-perm")),
                "scratch_draft": (CALLER, _draft("a-scratch", None), _version("a-scratch")),
                "no_version": (CALLER, _draft("a-none", case_id, current_version=0), None),
                "approval_blockers": (CALLER, _draft("a-block", case_id),
                                      _version("a-block", structured_output={"approval_blockers": ["Uncited figure"]})),
                "validation_not_passed": (CALLER, _draft("a-invalid", case_id), _version("a-invalid", validation_status="failed")),
                "missing_evidence": (CALLER, _draft("a-missing", case_id), _version("a-missing", missing_evidence=["Programme"])),
                "self_approval": (CALLER, _draft("a-self", case_id, updated_by="user-org_admin"), _version("a-self")),
            }
            assert set(cases) == set(APPROVE_CODES), "every code the gate can give has a scenario"
            for code, (persona, draft, version) in cases.items():
                await _seed(env, draft, version)
                gate = await _gate(env, persona, draft["_id"], "approve")
                assert gate["allowed"] is False and code in {r["code"] for r in gate["reasons"]}, (code, gate)
                response = await env.call("POST", persona, f"{DRAFTS}/{draft['_id']}/approve")
                if code == "no_approve_permission":
                    assert response.status_code == 403, (code, response.text[:300])
                else:
                    assert response.status_code == 409, (code, response.text[:300])
                    assert code in _refused_codes(response), (code, response.text[:300])
                stored = await env.db.arbitration_drafts.find_one({"_id": draft["_id"]})
                assert stored["status"] == draft["status"] and stored["is_locked"] == draft["is_locked"]

            # An enabled Approve is not refused by the gate; what remains is named as checked on action.
            await _seed(env, _draft("a-clean", case_id), _version("a-clean"))
            gate = await _gate(env, CALLER, "a-clean", "approve")
            assert gate["allowed"] is True and gate["checked_on_action"], gate
            response = await env.call("POST", CALLER, f"{DRAFTS}/a-clean/approve")
            detail = response.json().get("detail")
            assert not (isinstance(detail, dict) and detail.get("code") == "draft_action_refused"), response.text[:300]

    _run(scenario())


def test_every_disabled_filing_export_is_refused_by_the_api_for_the_same_reason(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _add_actor(env.db, VIEWER, VIEWER, ["dms.arbitration.view"])
            case_id = (await _create(env))["_id"]
            cases = {
                "no_export_permission": (VIEWER, _approved("e-perm", case_id), _version("e-perm")),
                "scratch_draft": (CALLER, _approved("e-scratch", None), _version("e-scratch")),
                "not_approved": (CALLER, _draft("e-draft", case_id), _version("e-draft")),
                "approved_version_missing": (CALLER, _approved("e-gone", case_id, approved_version_id="other"), _version("e-gone")),
                "approved_version_invalid": (CALLER, _approved("e-bad", case_id), _version("e-bad", validation_status="failed")),
                "self_approval": (CALLER, _approved("e-self", case_id, updated_by="user-org_admin"), _version("e-self")),
            }
            assert set(cases) == set(EXPORT_CODES), "every code the gate can give has a scenario"
            for code, (persona, draft, version) in cases.items():
                await _seed(env, draft, version)
                gate = await _gate(env, persona, draft["_id"], "filing_export")
                assert gate["allowed"] is False and code in {r["code"] for r in gate["reasons"]}, (code, gate)
                for fmt in ("docx", "pdf"):
                    response = await env.call("GET", persona, f"{DRAFTS}/{draft['_id']}/export/{fmt}")
                    if code == "no_export_permission":
                        assert response.status_code == 403, (code, fmt, response.text[:300])
                    else:
                        assert response.status_code == 409, (code, fmt, response.text[:300])
                        assert code in _refused_codes(response), (code, fmt, response.text[:300])
                stored = await env.db.arbitration_drafts.find_one({"_id": draft["_id"]})
                assert stored["status"] == draft["status"] and not stored.get("exported_at")

    _run(scenario())
