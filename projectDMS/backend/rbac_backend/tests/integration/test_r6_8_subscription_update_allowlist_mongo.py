"""R6-8 (owner decision 2026-11-21): ``PUT /subscriptions/{id}`` is not a commercial back door.

Before: an entitlement manager (a tenant billing administrator included) could change a
subscription's plan, billing period, add-ons, lifecycle status, billing status, period dates,
trial and renewal flags through the ordinary update, with no gateway call, no payment and no
lifecycle check - a free upgrade, a revived subscription or a "paid" status without payment.

Now the update accepts only a documented allowlist of non-commercial fields
(``admin_note``, ``cancellation_reason``; ``entitlement_overrides`` for Super Admin only).
Every commercial, lifecycle, payment-identity, scope or activation-precedence field - under any
spelling - is refused with 400 naming the dedicated workflow, for every caller, before
anything is written. Step-up, scope authorisation, compare-and-set and audit are unchanged.

Real HTTP through the production ``app``. Opt-in: ``CLAIM_BENCH_MONGODB_URI``; CI real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_l3_subscription_scope_integrity_mongo import _headers
from rbac_backend.tests.integration.test_security_remediation_mongo import _role, _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
SUB = "r68-sub-a1"
SUB_B = "r68-sub-b1"
CUSTOM_ROLE = "r68-custom-entitlement"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_monetization_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from rbac_backend.routers import rbac_monetization

    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)


STORED = {
    "plan_code": "dms_starter", "billing_period": "monthly", "status": "cancelled", "billing_status": "inactive",
    "active_add_ons": [], "auto_renew": False, "trial": False, "pilot": False, "admin_note": "kept",
}


async def _seed(env: Any) -> None:
    for sub_id, org, project in ((SUB, ORG_A, PROJ_A1), (SUB_B, ORG_B, PROJ_B1)):
        await env.db.subscriptions.insert_one({"_id": sub_id, "organization_id": org, "project_id": project,
                                               "package_id": None, **STORED, "updated_at": datetime.utcnow()})
    await env.db.roles.insert_one(_role(CUSTOM_ROLE, ["subscription.entitlement.manage"], organization_id=ORG_A))


async def _personas(env: Any) -> Dict[str, Optional[str]]:
    return {
        "superadmin": None,
        "r68_billing_admin": await _user(env.db, "r68_billing_admin", ["contraclaim_billing_admin"],
                                         organization_id=ORG_A, projects=[PROJ_A1]),
        "r68_custom": await _user(env.db, "r68_custom", [CUSTOM_ROLE], organization_id=ORG_A, projects=[PROJ_A1]),
        "r68_ordinary": await _user(env.db, "r68_ordinary", ["contraclaim_user"], organization_id=ORG_A,
                                    projects=[PROJ_A1]),
    }


async def _put(env: Any, persona: str, user_id: Optional[str], sub_id: str, body: Dict[str, Any]) -> Any:
    response = await env.client.put(f"{BASE}/subscriptions/{sub_id}", json=body, headers=_headers(persona, user_id))
    env.client.cookies.clear()
    return response


async def _stored(env: Any, sub_id: str = SUB) -> Dict[str, Any]:
    doc = await env.db.subscriptions.find_one({"_id": sub_id})
    return {key: doc.get(key) for key in STORED}


COMMERCIAL = [
    {"plan_code": "dms_enterprise"},  # free upgrade
    {"billing_period": "annual"},
    {"active_add_ons": ["ai_drafting_pack"]},
    {"status": "active"},  # revive without payment
    {"billing_status": "active"},  # "paid" without payment
    {"starts_at": "2026-01-01T00:00:00"},
    {"ends_at": "2099-01-01T00:00:00"},  # free extension
    {"current_period_end": "2099-01-01T00:00:00"},
    {"trial": True},
    {"trial_ends_at": "2099-01-01T00:00:00"},
    {"pilot": True},
    {"auto_renew": True},  # renewal is a gateway matter (cancel / reactivate)
    {"cancelled_at": None},
    # Other spellings and mass assignment: refused, never dropped.
    {"planCode": "dms_enterprise"},
    {"Plan-Code": "dms_enterprise"},
    {"plan": "dms_enterprise"},
    {"billingPeriod": "annual"},
    {"addOns": ["x"]},
    {"Status": "active"},
    {"subscription_status": "active"},
    {"billingStatus": "active"},
    {"governing_since": "2099-01-01T00:00:00"},  # activation precedence
    {"governingSince": "2099-01-01T00:00:00"},
    {"autoRenew": True},
    {"admin_note": "x", "plan_code": "dms_enterprise"},  # an allowed field does not carry a refused one
]


def test_commercial_fields_are_refused_for_every_caller_and_nothing_is_written(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            personas = await _personas(env)
            for persona in ("superadmin", "r68_billing_admin", "r68_custom"):
                for body in COMMERCIAL:
                    response = await _put(env, persona, personas[persona], SUB, body)
                    assert response.status_code == 400, (persona, body, response.status_code, response.text[:200])
                    assert await _stored(env) == STORED, (persona, body)
            for body in COMMERCIAL[:5]:
                response = await _put(env, "r68_ordinary", personas["r68_ordinary"], SUB, body)
                assert response.status_code == 403, (body, response.status_code)
            # Another organisation's subscription: refused before the body is considered.
            for body in COMMERCIAL[:5]:
                response = await _put(env, "r68_billing_admin", personas["r68_billing_admin"], SUB_B, body)
                assert response.status_code == 403, (body, response.status_code)
            assert await _stored(env) == STORED and await _stored(env, SUB_B) == STORED
            assert await env.db.subscription_history.count_documents({"subscription_id": {"$in": [SUB, SUB_B]}}) == 0

    _run(scenario())


def test_the_refusal_names_the_dedicated_workflow(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            expectations = {
                "plan_code": ("upgrade", "dms_enterprise"), "billing_period": ("change-period", "annual"),
                "active_add_ons": ("add-ons", ["x"]), "status": ("reactivate", "active"),
                "billing_status": ("webhook", "active"), "governing_since": ("activation", "2099-01-01T00:00:00"),
            }
            for field, (workflow, value) in expectations.items():
                response = await _put(env, "superadmin", None, SUB, {field: value})
                assert response.status_code == 400 and workflow in response.json()["detail"], (field, response.text)
            for alias, workflow in (("planCode", "upgrade"), ("Billing-Status", "webhook"), ("activatedAt", "activation")):
                response = await _put(env, "superadmin", None, SUB, {alias: "active"})
                assert response.status_code == 400 and workflow in response.json()["detail"], (alias, response.text)

    _run(scenario())


def test_the_allowlist_still_works_with_audit_and_compare_and_set(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            personas = await _personas(env)
            response = await _put(env, "r68_billing_admin", personas["r68_billing_admin"], SUB,
                                  {"admin_note": "renewal discussed with customer", "cancellation_reason": "budget"})
            assert response.status_code == 200, response.text[:200]
            doc = await env.db.subscriptions.find_one({"_id": SUB})
            assert (doc["admin_note"], doc["cancellation_reason"]) == ("renewal discussed with customer", "budget")
            assert {key: doc.get(key) for key in STORED if key != "admin_note"} == {
                key: value for key, value in STORED.items() if key != "admin_note"}
            audit = await env.db.audit_events.find_one({"action": "subscription.updated", "resource_id": SUB})
            assert audit is not None
            # Unchanged scope is still a no-op; an unknown key is still refused.
            assert (await _put(env, "superadmin", None, SUB, {"organization_id": ORG_A, "admin_note": "y"})).status_code == 200
            assert (await _put(env, "superadmin", None, SUB, {"surprise": 1})).status_code == 400

    _run(scenario())


def test_entitlement_overrides_are_super_admin_only(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            personas = await _personas(env)
            body = {"entitlement_overrides": {"ai_drafting": True, "max_users": 10_000}}
            for persona in ("r68_billing_admin", "r68_custom"):
                response = await _put(env, persona, personas[persona], SUB, body)
                assert response.status_code == 400, (persona, response.status_code, response.text[:200])
            assert not (await env.db.subscriptions.find_one({"_id": SUB})).get("entitlement_overrides")
            response = await _put(env, "superadmin", None, SUB, body)
            assert response.status_code == 200, response.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": SUB}))["entitlement_overrides"] == body["entitlement_overrides"]

    _run(scenario())


def test_concurrent_allowed_updates_and_commercial_attempts_leave_commerce_untouched(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            personas = await _personas(env)
            bodies = [{"admin_note": f"note-{n}"} if n % 2 else {"plan_code": "dms_enterprise", "status": "active"}
                      for n in range(12)]
            responses = await asyncio.gather(*[
                env.client.put(f"{BASE}/subscriptions/{SUB}", json=body,
                               headers=_headers("r68_billing_admin", personas["r68_billing_admin"]))
                for body in bodies
            ])
            codes = sorted(r.status_code for r in responses)
            assert codes.count(400) == 6 and set(codes) <= {200, 400, 409}, codes
            stored = await _stored(env)
            assert {k: v for k, v in stored.items() if k != "admin_note"} == {
                k: v for k, v in STORED.items() if k != "admin_note"}

    _run(scenario())
