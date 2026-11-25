"""4B-20 (D-24, JG-1): a claim starts from three questions.
Real HTTP, real MongoDB replica set, real RBAC.

What happened, when you became aware, and which contract. ``POST /api/claims/start``
creates the claim (a draft) and its board with the first head; everything else is
captured on the board, or on the full form ("More details", ``POST /api/claims``).

- the claim is a draft with a working title, the event in the claimant's words, the
  awareness date and the contract; its board is open with one head;
- with several contracts one must be chosen, and nothing is created until it is;
- creating a claim and opening its board are both required (claim-create and
  claim-edit); without either nothing is created;
- where the Claim Bench is off nothing is created;
- status, origin and every other field are refused: the start takes three answers;
- a date in the future and an empty account are refused;
- if the board cannot be opened, the claim is taken back out;
- another organisation and anonymous callers are refused.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_contract_identity_mongo import DEPOT, MAIN, _contracts
from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

START = "/api/claims/start"
HAPPENED = (
    "Access to the depot site was handed over on 20 March, eighteen days after the programme date. "
    "Piling could not start until then."
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


def _answers(**fields: Any) -> dict:
    return {"project_id": PROJ_A1, "organization_id": ORG_A, "what_happened": HAPPENED, "aware_on": "2026-03-20", **fields}


async def _claims(env: Any) -> int:
    return await env.db.claims.count_documents({"description": HAPPENED})


def test_three_answers_create_a_draft_claim_and_its_board_with_the_first_head(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, MAIN)
            started = await env.call("POST", "org_admin", START, json=_answers())
            assert started.status_code == 201, started.text[:400]
            body = started.json()
            claim = body["claim"]
            assert claim["status"] == "draft"
            assert claim["title"] == "Access to the depot site was handed over on 20 March, eighteen days after the programme date"
            assert claim["description"] == HAPPENED
            assert claim["awareness_date"].startswith("2026-03-20")
            assert claim["contract_id"] == "primary", "the only contract is used without asking"
            assert body["head_id"] == "H1"

            stored = await env.db.claims.find_one({"_id": claim["_id"]})
            assert stored["created_by"] == "user-org_admin" and stored.get("origin") is None
            board = stored["bench"]
            assert board["board_version"] == body["board_version"] and [h["head_id"] for h in board["heads"]] == ["H1"]
            read = await env.call("GET", "org_admin", f"/api/claims/{claim['_id']}/bench")
            assert read.status_code == 200 and read.json()["bench"]["heads"][0]["head_id"] == "H1"

    _run(scenario())


def test_with_several_contracts_one_must_be_chosen_and_nothing_is_created_until_it_is(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, MAIN, DEPOT)
            unchosen = await env.call("POST", "org_admin", START, json=_answers())
            assert unchosen.status_code == 422 and _code(unchosen) == "claim_contract_required", unchosen.text[:300]
            assert await _claims(env) == 0
            chosen = await env.call("POST", "org_admin", START, json=_answers(contract_id="pkg-depot"))
            assert chosen.status_code == 201, chosen.text[:300]
            assert chosen.json()["claim"]["contract_id"] == "pkg-depot"

    _run(scenario())


def test_both_creating_and_opening_the_board_are_required(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _add_actor(env.db, "creator_only", "role-creator-only", ["dms.claim.view", "dms.claim.create"])
            await _add_actor(env.db, "editor_only", "role-editor-only", ["dms.claim.view", "dms.claim.edit"])
            for persona in ("creator_only", "editor_only"):
                refused = await env.call("POST", persona, START, json=_answers())
                assert refused.status_code == 403, (persona, refused.status_code, refused.text[:300])
            assert await _claims(env) == 0

    _run(scenario())


def test_nothing_is_created_where_the_claim_bench_is_off(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings

            monkeypatch.setattr(settings, "CLAIM_BENCH_ROLLOUT_MODE", "off")
            refused = await env.call("POST", "org_admin", START, json=_answers())
            assert refused.status_code == 404, refused.text[:300]
            assert await _claims(env) == 0

    _run(scenario())


@pytest.mark.parametrize(
    "change, code",
    [
        ({"status": "submitted"}, None),
        ({"origin": {"type": "hindrance", "id": "h-1"}}, None),
        ({"amount_claimed": 100}, None),
        ({"what_happened": "Late."}, None),
        ({"aware_on": (date.today() + timedelta(days=2)).isoformat()}, "claim_start_awareness_future"),
    ],
)
def test_the_start_takes_three_answers_and_refuses_anything_else(monkeypatch, change: dict, code: str) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            refused = await env.call("POST", "org_admin", START, json=_answers(**change))
            assert refused.status_code == 422, (change, refused.status_code, refused.text[:300])
            if code:
                assert _code(refused) == code
            assert await env.db.claims.count_documents({"description": {"$regex": "^Access to the depot"}}) == 0

    _run(scenario())


def test_a_claim_whose_board_cannot_be_opened_is_taken_back_out(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.claim_bench import ClaimBenchService

            async def broken(self: Any, claim: Any, user_id: Any) -> Any:
                raise RuntimeError("the board could not be written")

            monkeypatch.setattr(ClaimBenchService, "open", broken)
            failed = await env.call("POST", "org_admin", START, json=_answers())
            assert failed.status_code >= 500, failed.text[:300]
            assert await _claims(env) == 0

    _run(scenario())


def test_another_organisation_and_anonymous_callers_are_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            foreign = await env.call("POST", "foreign_org_admin", START, json=_answers(), project=PROJ_B1)
            assert foreign.status_code in (403, 404), foreign.text[:300]
            anonymous = await env.client.post(START, json=_answers())
            assert anonymous.status_code == 401
            assert await _claims(env) == 0

    _run(scenario())
