"""4B-41 (D-21, D-05, B1): evaluation runs are made on the server and approvals name them.
Real HTTP, real MongoDB replica set, real RBAC. The model is replaced by a scripted
executor; everything else is the production path.

The plan's proof:

- the run record carries the session user; a request cannot set ``recorded_by``,
  the threshold or any part of the run's identity (422), and nothing updates a run;
- the threshold is server configuration: configured at 0, a run still uses 0.85;
- approvals and publication name a run (``eval_run_id``); the evaluator cannot
  approve; a failing run cannot be approved; a run of another version, or one
  made before the golden set or the model changed, is refused as stale;
- the configured model budget stops a run before any call is made.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from rbac_backend.migrations.v20261121_0001_reviewer_role_identities import upgrade as seed_identities
from rbac_backend.migrations.v20261121_0002_reviewer_role_assignment_indexes import upgrade as apply_indexes
from rbac_backend.models.skill import SkillApprovalRequest
from rbac_backend.services.skills.approvals import SkillApprovalService
from rbac_backend.services.skills.evaluation import SKILL_EVAL_RUNS, SkillEvalRunService
from rbac_backend.services.skills.executor import ExecutionResult
from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_skill_publication_transactions_mongo import _draft
from rbac_backend.tests.reviewer_assignment_helpers import hold_platform
from rbac_backend.tests.skill_eval_support import ELEMENTS, synthetic_matter, synthetic_suite, use_suite_store
from rbac_backend.utils.rate_limiter import RateLimiter

pytestmark = pytest.mark.integration

VERSION = "5.1.0"
PATH = f"/api/skills/{CLAIMS_SKILL_KEY}/versions/{VERSION}"
EVALUATOR = "superadmin"  # holds drafting.skill.publish
LEGAL = "senior_legal_approver"
CONTRACT = "chief_contract_expert"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


def _scripted(outputs: Dict[str, Dict[str, Any]], calls: List[str]):
    """The model's answers, by matter title; records each call."""

    async def execute(self: Any, version: Any, role: str, prompt_key: str, payload: Dict[str, str]) -> ExecutionResult:
        calls.append(payload["title"])
        output = outputs.get(payload["title"]) or {
            **{name: {"state": "supported", "reason": "Shown by the records.", "source_ids": []} for name in ELEMENTS},
            "classification": "excusable",
            "clause_refs": ["2.1"],
            "residual_risks": [],
            "missing_information": [],
        }
        return ExecutionResult(output=output, degraded=False, error=None, model="scripted", compiled=None,  # type: ignore[arg-type]
                               prompt_version=3)

    return execute


async def _ready(env: Any, monkeypatch: Any, outputs: Dict[str, Dict[str, Any]] | None = None) -> List[str]:
    RateLimiter._shared_user_requests.clear()
    RateLimiter._shared_buckets.clear()
    await seed_identities(env.db, False)
    await apply_indexes(env.db, False)
    calls: List[str] = []
    monkeypatch.setattr(SkillEvalRunService, "_default_execute", _scripted(outputs or {}, calls))
    use_suite_store(monkeypatch, CLAIMS_SKILL_KEY)
    await _draft(env.db, VERSION)
    return calls


def test_a_run_is_made_on_the_server_and_records_the_session_user(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings

            calls = await _ready(env, monkeypatch)
            # Nothing about the run can be supplied: not the evaluator, not the bar, not a score.
            for forged in ({"recorded_by": "someone-else"}, {"threshold": 0}, {"score": 1.0}, {"suite": "mine"}):
                refused = await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json=forged)
                assert refused.status_code == 422, (forged, refused.status_code, refused.text[:200])
            assert calls == [] and await env.db[SKILL_EVAL_RUNS].count_documents({}) == 0

            # Configured at 0, the threshold is still the floor.
            monkeypatch.setattr(settings, "SKILL_EVAL_MIN_THRESHOLD", 0.0)
            made = await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json={"note": "first run"})
            assert made.status_code == 201, made.text[:400]
            run = made.json()
            assert run["recorded_by"] == "user-superadmin"
            assert run["threshold"] == 0.85 and run["configuration"]["threshold"] == 0.85
            assert run["passed"] is True and run["case_count"] == 2 and calls == ["Late access to Site"] * 2
            assert run["prompt_version"] == 3 and len(run["suite_hash"]) == 64
            assert run["cost"]["model_calls"] == 2

            stored = await env.db[SKILL_EVAL_RUNS].find_one({"_id": run["_id"]})
            assert stored["recorded_by"] == "user-superadmin" and stored["content_hash"] == run["content_hash"]

            # Runs are kept and listed; there is no way to change one.
            listed = await env.call("GET", EVALUATOR, f"{PATH}/eval-runs")
            assert listed.status_code == 200 and [item["_id"] for item in listed.json()] == [run["_id"]]
            for method in ("PUT", "PATCH", "DELETE"):
                attempt = await env.call(method, EVALUATOR, f"{PATH}/eval-runs", json={"recorded_by": "x"})
                assert attempt.status_code == 405, (method, attempt.status_code)
            assert (await env.db[SKILL_EVAL_RUNS].find_one({"_id": run["_id"]}))["recorded_by"] == "user-superadmin"

            # Someone without the publishing permission cannot run one.
            assert (await env.call("POST", "project_user", f"{PATH}/eval-runs", json={})).status_code == 403

    _run(scenario())


def test_hard_gates_fail_a_run_whatever_the_agreement(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            invented = {
                **{name: {"state": "supported", "reason": "Shown.", "source_ids": []} for name in ELEMENTS},
                "classification": "excusable",
                "clause_refs": ["8.4"],
                "residual_risks": ["Notice was due within 14 days; INR 4,50,000 is at risk."],
                "missing_information": [],
            }
            await _ready(env, monkeypatch, {"Late access to Site": invented})
            made = await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json={})
            assert made.status_code == 201, made.text[:300]
            run = made.json()
            assert run["passed"] is False and run["scores"]["element_agreement"] == 1.0
            failures = " | ".join(run["failures"])
            assert "figures: not in the matter: 14, 450000" in failures
            assert "clauses: cited but not in the matter: 8.4" in failures
            assert "notice: periods the contract does not state: 14" in failures
            # Full agreement does not make a failed run approvable.
            await hold_platform(env.db, SimpleNamespace(id="user-project_user", roles=[LEGAL]))
            refused = await env.call("POST", "project_user", f"{PATH}/approvals",
                                     json={"kind": "legal", "reviewer_role": LEGAL, "eval_run_id": run["_id"]})
            assert refused.status_code == 422, refused.text[:300]
            assert await env.db.skill_approvals.count_documents({}) == 0

    _run(scenario())


def test_approvals_and_publication_name_a_current_passing_run(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _ready(env, monkeypatch)
            run = (await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json={})).json()
            counsel = SimpleNamespace(id="user-project_user", roles=[LEGAL])
            expert = SimpleNamespace(id="contract-expert", roles=[CONTRACT])
            await hold_platform(env.db, counsel, expert)

            # A report cannot be typed in any more.
            typed = await env.call("POST", "project_user", f"{PATH}/approvals", json={
                "kind": "legal", "reviewer_role": LEGAL,
                "eval_report": {"suite": "s", "score": 1, "threshold": 0.85, "case_count": 1, "recorded_by": "x"},
            })
            assert typed.status_code == 422, typed.text[:200]

            approved = await env.call("POST", "project_user", f"{PATH}/approvals",
                                      json={"kind": "legal", "reviewer_role": LEGAL, "eval_run_id": run["_id"]})
            assert approved.status_code == 201, approved.text[:300]
            body = approved.json()
            assert body["eval_run_id"] == run["_id"] and body["eval_recorded_by"] == "user-superadmin"
            assert run["_id"] in body["eval_suite"]

            # The evaluator cannot also approve.
            evaluator = SimpleNamespace(id="user-superadmin", roles=[CONTRACT])
            await hold_platform(env.db, evaluator)
            record = await _version(env)
            from fastapi import HTTPException

            service = SkillEvalRunService(env.db)
            _found, report = await service.report_for(record, run["_id"])
            with pytest.raises(HTTPException) as own:
                await SkillApprovalService(env.db).record(
                    record, SkillApprovalRequest(kind="technical", reviewer_role=CONTRACT, eval_run_id=run["_id"]),
                    evaluator, report, eval_run_id=run["_id"],
                )
            assert own.value.status_code == 409

            # With both reviews on this run, the publisher publishes by naming it.
            await SkillApprovalService(env.db).record(
                record, SkillApprovalRequest(kind="technical", reviewer_role=CONTRACT, eval_run_id=run["_id"]),
                expert, report, eval_run_id=run["_id"],
            )
            # The evaluator cannot publish; a second platform administrator can.
            own_publish = await env.call("POST", EVALUATOR, f"{PATH}/publish", json={"eval_run_id": run["_id"]})
            assert own_publish.status_code == 409, own_publish.text[:300]
            admin = await env.db.users.find_one({"_id": "user-superadmin"})
            await env.db.users.insert_one(
                {**admin, "_id": "user-second_admin", "email": "second_admin@example.com", "username": "second_admin"}
            )
            published = await env.call("POST", "second_admin", f"{PATH}/publish", json={"eval_run_id": run["_id"]})
            assert published.status_code == 200, published.text[:300]
            assert published.json()["eval_run_id"] == run["_id"]

    _run(scenario())


async def _version(env: Any):
    from rbac_backend.services.skills.registry import SkillRegistry

    return await SkillRegistry(env.db).get_version(CLAIMS_SKILL_KEY, VERSION)


def test_a_stale_failing_or_foreign_run_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            failing = {
                **{name: {"state": "missing", "reason": "Not shown.", "source_ids": []} for name in ELEMENTS},
                "classification": "undetermined", "clause_refs": [], "residual_risks": [], "missing_information": [],
            }
            await _ready(env, monkeypatch, {"Late access to Site": failing})
            counsel = SimpleNamespace(id="user-project_user", roles=[LEGAL])
            await hold_platform(env.db, counsel)
            failed = (await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json={})).json()
            assert failed["passed"] is False
            refused = await env.call("POST", "project_user", f"{PATH}/approvals",
                                     json={"kind": "legal", "reviewer_role": LEGAL, "eval_run_id": failed["_id"]})
            assert refused.status_code == 422, refused.text[:300]

            # A run of another version does not count for this one.
            await _draft(env.db, "5.1.1")
            other = f"/api/skills/{CLAIMS_SKILL_KEY}/versions/5.1.1/approvals"
            foreign = await env.call("POST", "project_user", other,
                                     json={"kind": "legal", "reviewer_role": LEGAL, "eval_run_id": failed["_id"]})
            assert foreign.status_code == 404 and _code(foreign) == "eval_run_not_found", foreign.text[:300]

            # The golden set changes: every earlier run is stale.
            monkeypatch.setattr(SkillEvalRunService, "_default_execute", _scripted({}, []))
            passing = (await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json={})).json()
            assert passing["passed"] is True
            use_suite_store(monkeypatch, CLAIMS_SKILL_KEY, synthetic_suite(
                CLAIMS_SKILL_KEY, [synthetic_matter("S01"), synthetic_matter("S02"), synthetic_matter("S03")]))
            stale = await env.call("POST", "project_user", f"{PATH}/approvals",
                                   json={"kind": "legal", "reviewer_role": LEGAL, "eval_run_id": passing["_id"]})
            assert stale.status_code == 409 and _code(stale) == "eval_run_stale", stale.text[:300]
            assert "the golden set" in json.dumps(stale.json())

            # So does a change to the pack's text or its policy (a re-import over the draft).
            fresh = (await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json={})).json()
            for change, words in (
                ({"content_hash": "f" * 64}, "the pack's text"),
                ({"manifest.use_when": "Changed after the run."}, "its policy"),
            ):
                original = await env.db.skill_versions.find_one({"skill_key": CLAIMS_SKILL_KEY, "version": VERSION})
                await env.db.skill_versions.update_one({"_id": original["_id"]}, {"$set": change})
                changed = await env.call("POST", "project_user", f"{PATH}/approvals",
                                         json={"kind": "legal", "reviewer_role": LEGAL, "eval_run_id": fresh["_id"]})
                assert changed.status_code == 409 and _code(changed) == "eval_run_stale", changed.text[:300]
                assert words in json.dumps(changed.json())
                await env.db.skill_versions.replace_one({"_id": original["_id"]}, original)

            # So does a change of model.
            from rbac_backend.core.config import settings

            again = (await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json={})).json()
            monkeypatch.setattr(settings, "SKILL_MODEL", "another-model")
            moved = await env.call("POST", "project_user", f"{PATH}/approvals",
                                   json={"kind": "legal", "reviewer_role": LEGAL, "eval_run_id": again["_id"]})
            assert moved.status_code == 409 and "the model" in json.dumps(moved.json()), moved.text[:300]
            assert await env.db.skill_approvals.count_documents({}) == 0

    _run(scenario())


def test_the_model_budget_stops_a_run_before_any_call(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings

            calls = await _ready(env, monkeypatch)
            monkeypatch.setattr(settings, "SKILL_EVAL_MAX_MODEL_CALLS", 1)
            refused = await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json={})
            assert refused.status_code == 409 and _code(refused) == "eval_budget_exceeded", refused.text[:300]
            assert calls == [] and await env.db[SKILL_EVAL_RUNS].count_documents({}) == 0
            monkeypatch.setattr(settings, "SKILL_EVAL_SUITE_DIR", "")
            unset = await env.call("POST", EVALUATOR, f"{PATH}/eval-runs", json={})
            assert unset.status_code == 409 and _code(unset) == "eval_suite_store_not_configured"

    _run(scenario())
