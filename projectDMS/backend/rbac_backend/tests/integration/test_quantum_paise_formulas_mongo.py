"""4B-24 (D-14, C-06): quantum in paise, formulas computed, locks re-checked.
Real HTTP, real MongoDB replica set, real RBAC.

- A Hudson line's amount is computed on the server from its inputs; a typed
  amount on a formula line is refused, and so are missing inputs.
- Amounts are stored in integer paise and the claim's total is summed in paise.
- Unlinking a document a line relies on locks the line, naming the record, for
  every viewer: the lock is judged against the claim's whole document set.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import DOC_IN_ID, DOC_OUT_ID

pytestmark = pytest.mark.integration

BENCH = f"/api/claims/{CLAIM}/bench"
QUANTUM = f"{BENCH}/heads/H1/quantum"
LINES = f"{QUANTUM}/lines"
DOC_LINKS = f"/api/entities/claim/{CLAIM}/document-links:batch"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _version(env: Any) -> int:
    return int((await env.db.claims.find_one({"_id": CLAIM}))["bench"]["board_version"])


async def _prepared(env: Any) -> dict:
    assert (await env.call("POST", "org_admin", f"{BENCH}/open")).status_code == 200
    linked = await env.call(
        "POST", "org_admin", DOC_LINKS,
        json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"},
                        {"document_id": DOC_OUT_ID, "relationship_role": "supporting_document"}]},
    )
    assert linked.status_code in (200, 201), linked.text[:300]
    return {row["document_id"]: row["_id"] for row in await env.db.entity_document_links.find({"target_id": CLAIM}).to_list(None)}


def _hudson(**over: Any) -> dict:
    return {
        "category": "overhead_formula", "description": "Head-office overhead", "formula": "hudson",
        "period_start": "2026-03-01T00:00:00", "period_end": "2026-03-30T00:00:00",
        "formula_inputs": {
            "tender_percentage": {"value": "5", "document_id": DOC_IN_ID},
            "contract_sum": {"value": "1,00,00,000", "document_id": DOC_IN_ID},
            "contract_period_days": {"value": "365", "document_id": DOC_IN_ID},
        },
        "records": [{"document_id": DOC_OUT_ID, "role": "actual_loss"}],
        **over,
    }


def test_a_formula_line_is_computed_on_the_server_and_never_typed(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            typed = await env.call("POST", "org_admin", LINES, json={"board_version": await _version(env), **_hudson(amount=99999)})
            assert typed.status_code == 422 and _code(typed) == "quantum_formula_amount_computed", typed.text[:300]

            short = _hudson()
            del short["formula_inputs"]["contract_sum"]
            missing = await env.call("POST", "org_admin", LINES, json={"board_version": await _version(env), **short})
            assert missing.status_code == 422 and _code(missing) == "quantum_formula_inputs_missing", missing.text[:300]
            assert "Contract sum" in missing.json()["detail"]["user_message"]

            added = await env.call("POST", "org_admin", LINES, json={"board_version": await _version(env), **_hudson()})
            assert added.status_code == 200, added.text[:300]
            line = added.json()["lines"][0]["line"]
            # 5% x Rs 1,00,00,000 / 365 x 30 days = Rs 41,095.89 (worked in tests/test_quantum_formulas.py).
            assert line["amount_paise"] == 4_109_589 and line["amount"] == 41095.89
            stored = (await env.db.claims.find_one({"_id": CLAIM}))
            stored_line = stored["bench"]["heads"][0]["quantum_lines"][0]
            assert stored_line["amount_paise"] == 4_109_589
            assert stored_line["formula_inputs"]["contract_sum"]["value"] == "10000000"
            assert stored["amount_claimed_paise"] == 4_109_589 and stored["amount_claimed"] == 41095.89

    _run(scenario())


def test_amounts_are_held_in_paise_and_the_claim_total_is_exact(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            for amount in ("33333333333.33", "33333333333.33", "33333333333.34"):
                added = await env.call(
                    "POST", "org_admin", LINES,
                    json={"board_version": await _version(env), "category": "direct", "description": "Works",
                          "amount": amount, "records": [{"document_id": DOC_IN_ID, "role": "support"}]},
                )
                assert added.status_code == 200, added.text[:300]
            view = (await env.call("GET", "org_admin", QUANTUM)).json()
            assert view["claimed_total_paise"] == 10_000_000_000_000  # Rs 10,000 crore, to the paisa
            claim = await env.db.claims.find_one({"_id": CLAIM})
            assert claim["amount_claimed_paise"] == 10_000_000_000_000

    _run(scenario())


def test_unlinking_a_record_locks_the_line_for_every_viewer(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            links = await _prepared(env)
            added = await env.call(
                "POST", "org_admin", LINES,
                json={"board_version": await _version(env), "category": "direct", "description": "Idle plant",
                      "amount": "125000.50", "records": [{"document_id": DOC_IN_ID, "role": "support"}]},
            )
            assert added.status_code == 200, added.text[:300]
            assert added.json()["lines"][0]["locked"] is False

            removed = await env.call(
                "POST", "org_admin", f"/api/document-links/{links[DOC_IN_ID]}:remove",
                json={"reason": "Wrong invoice attached to this claim", "expected_revision": 1},
            )
            assert removed.status_code == 200, removed.text[:300]
            for persona in ("org_admin", "project_admin"):
                view = (await env.call("GET", persona, QUANTUM)).json()
                line = view["lines"][0]
                assert line["locked"] is True, (persona, line)
                assert any("Record 1 on this line" in reason and "no longer linked" in reason for reason in line["lock_reasons"])
                assert view["supported_total_paise"] == 0 and view["locked_total_paise"] == 12_500_050

    _run(scenario())


def test_the_claim_total_keeps_paise_beyond_what_a_float_holds(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            for _ in range(3):
                added = await env.call(
                    "POST", "org_admin", LINES,
                    json={"board_version": await _version(env), "category": "direct", "description": "Works",
                          "amount": "900000000000000.01", "records": [{"document_id": DOC_IN_ID, "role": "support"}]},
                )
                assert added.status_code == 200, added.text[:300]
            claim = await env.db.claims.find_one({"_id": CLAIM})
            assert claim["bench"]["heads"][0]["amount_claimed_paise"] == 270_000_000_000_000_003
            assert claim["amount_claimed_paise"] == 270_000_000_000_000_003

    _run(scenario())


def test_eichleay_figures_that_cannot_be_true_together_are_refused_and_a_calculation_is_not_a_finding(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)

            def eichleay(contract_billings: str) -> dict:
                return {
                    "category": "overhead_formula", "description": "Unabsorbed overhead", "formula": "eichleay",
                    "period_start": "2026-03-01T00:00:00", "period_end": "2026-04-14T00:00:00",  # 45 days
                    "formula_inputs": {
                        "contract_billings": {"value": contract_billings, "document_id": DOC_IN_ID},
                        "total_billings": {"value": "2000000000", "document_id": DOC_IN_ID},
                        "total_overhead": {"value": "160000000", "document_id": DOC_IN_ID},
                        "performance_days": {"value": "500", "document_id": DOC_IN_ID},
                    },
                    "records": [{"document_id": DOC_OUT_ID, "role": "actual_loss"}],
                }

            refused = await env.call("POST", "org_admin", LINES, json={"board_version": await _version(env), **eichleay("2000000001")})
            assert refused.status_code == 422 and _code(refused) == "quantum_formula_inputs_inconsistent", refused.text[:300]
            assert "cannot exceed the company's total billings" in refused.json()["detail"]["user_message"]

            added = await env.call("POST", "org_admin", LINES, json={"board_version": await _version(env), **eichleay("300000000")})
            assert added.status_code == 200, added.text[:300]
            view = added.json()
            line = view["lines"][0]
            assert line["line"]["amount_paise"] == 216_000_000  # Rs 21,60,000.00, worked in tests/test_quantum_formulas.py
            assert line["calculated"] is True and "not a finding that this amount is recoverable" in line["calculation_note"]
            assert view["calculated_total_paise"] == 216_000_000

    _run(scenario())
