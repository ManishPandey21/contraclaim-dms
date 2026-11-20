"""Owner decision 4 (2026-11-20): minimum disclosure for Project Admin and Project User.

A project-tier caller (Project Admin / Project User without organisation-wide scope) sees,
for their own projects only, service status, enabled feature indicators and expiry. Never
negotiated prices, financial terms, raw ``entitlement_overrides`` values, payment-gateway
ids, commercial metadata, or another organisation's or project's data. This holds on every
operational route (``/plan-settings/effective-services``, ``/plan-settings``,
``/subscriptions``, ``/subscriptions/{id}``) and in both ``BILLING_EXPLICIT_PERMISSIONS``
states. The financial routes reached through the legacy aliases are DEF-4's (unchanged
until the approved removal phase).

Real HTTP through the production ``app``; nothing overridden.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any, Dict

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import _role, _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2, PROJ_B1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
SUB_A1 = "d4-sub-a1"
#: Strings that must never reach a project-tier caller.
SECRETS = ("NEGOTIATED-999", "gw-d4-secret", "terms-d4-secret", "cust-d4-secret", "d4-sub-a2", "d4-sub-b1", PROJ_A2, PROJ_B1, ORG_B)
PROJECT_STATE_KEYS = {"source", "status", "dms_enabled", "drafting_enabled", "features", "current_period_end", "trial", "trial_ends_at"}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _headers(persona: str) -> Dict[str, str]:
    import jwt  # type: ignore[import-untyped]

    from rbac_backend.core.config import settings

    now = int(time.time())
    token = jwt.encode({"sub": f"{persona}@example.com", "type": "access", "iat": now, "exp": now + 3600},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


async def _seed(env: Any) -> None:
    common = {"package_id": None, "plan_code": "dms_enterprise", "status": "active", "billing_status": "active",
              "billing_period": "annual", "updated_at": datetime.utcnow()}
    await env.db.subscriptions.insert_many([
        {"_id": SUB_A1, "organization_id": ORG_A, "project_id": PROJ_A1, **common,
         "current_period_end": datetime(2027, 3, 1),
         "payment_gateway_subscription_id": "gw-d4-secret",
         "entitlement_overrides": {"feature.drafting.enabled": True, "feature.dms.ocr": False,
                                   "negotiated_price_minor": "NEGOTIATED-999", "contract_terms": "terms-d4-secret",
                                   "limits": {"seats": 5}},
         "metadata": {"customer_ref": "cust-d4-secret"}},
        {"_id": "d4-sub-a2", "organization_id": ORG_A, "project_id": PROJ_A2, **common,
         "payment_gateway_subscription_id": "gw-d4-secret"},
        {"_id": "d4-sub-b1", "organization_id": ORG_B, "project_id": PROJ_B1, **common,
         "payment_gateway_subscription_id": "gw-d4-secret"},
    ])


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "BILLING_EXPLICIT_PERMISSIONS", on)


async def _get(env: Any, persona: str, path: str) -> Any:
    response = await env.client.get(f"{BASE}{path}", headers=_headers(persona))
    env.client.cookies.clear()
    return response


def _assert_no_secrets(body: Any, context: str) -> None:
    text = json.dumps(body, default=str)
    for secret in SECRETS:
        assert secret not in text, (context, secret)


@pytest.mark.parametrize("flag", [False, True])
def test_feature_status_never_carries_commercial_or_foreign_data(monkeypatch, flag: bool) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, flag)
            await _seed(env)
            for persona in ("project_user", "project_admin"):
                response = await _get(env, persona, "/plan-settings/effective-services")
                assert response.status_code == 200, (persona, response.text[:200])
                effective = response.json()["effective"]
                _assert_no_secrets(effective, f"{persona} effective-services flag={flag}")
                assert set(effective["projects"]) == {PROJ_A1}, (persona, effective["projects"].keys())
                state = effective["projects"][PROJ_A1]
                assert set(state) <= PROJECT_STATE_KEYS, (persona, set(state) - PROJECT_STATE_KEYS)
                # Ordinary users can still tell whether the features they need are on: the
                # override's boolean indicators are kept, its other values are dropped.
                assert state["drafting_enabled"] is True and state["dms_enabled"] is True
                assert state["features"]["feature.drafting.enabled"] is True
                assert state["features"]["feature.dms.ocr"] is False
                assert all(isinstance(value, bool) for value in state["features"].values())
                assert str(state["current_period_end"]).startswith("2027-03-01")
                for org_state in effective["organizations"].values():
                    assert set(org_state) <= PROJECT_STATE_KEYS

    _run(scenario())


@pytest.mark.parametrize("flag", [False, True])
def test_subscription_reads_are_operational_for_project_tier(monkeypatch, flag: bool) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, flag)
            await _seed(env)
            if flag:
                # The approved grant step (status view for Project Admin).
                from rbac_backend.tests.integration.test_def4_billing_explicit_permissions_mongo import _grant_target

                await _grant_target(env)
            for path in ("/subscriptions", f"/subscriptions/{SUB_A1}", "/plan-settings"):
                for persona in ("project_admin", "project_user"):
                    response = await _get(env, persona, path)
                    if response.status_code == 200:
                        _assert_no_secrets(response.json(), f"{persona} {path} flag={flag}")
                        body = response.json()
                        if path == f"/subscriptions/{SUB_A1}":
                            assert set(body) <= {"id", "organization_id", "project_id", "status", "starts_at", "ends_at",
                                                 "current_period_end", "trial", "trial_ends_at"}, body
                    else:
                        assert response.status_code in (403, 404), (persona, path, response.status_code)

    _run(scenario())


@pytest.mark.parametrize("flag", [False, True])
def test_a_project_tier_reader_of_one_subscription_gets_the_operational_row(monkeypatch, flag: bool) -> None:
    """A Project User who may read the subscription (a project role granting the usage view,
    inserted directly) still gets only the operational fields."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, flag)
            await _seed(env)
            await env.db.roles.insert_one(_role("d4-usage-a1", ["subscription.usage.view"], organization_id=ORG_A, project_id=PROJ_A1))
            await _user(env.db, "d4_reader", ["projectuser", "d4-usage-a1"], organization_id=ORG_A, projects=[PROJ_A1])
            response = await _get(env, "d4_reader", f"/subscriptions/{SUB_A1}")
            assert response.status_code == 200, response.text[:300]
            body = response.json()
            _assert_no_secrets(body, f"single read flag={flag}")
            assert set(body) <= {"id", "organization_id", "project_id", "status", "starts_at", "ends_at",
                                 "current_period_end", "trial", "trial_ends_at"}, body
            assert body["id"] == SUB_A1 and body["status"] == "active"
            # Another project's subscription stays refused.
            assert (await _get(env, "d4_reader", "/subscriptions/d4-sub-a2")).status_code == 403

    _run(scenario())


def test_organisation_tier_and_dual_role_users_keep_their_view(monkeypatch) -> None:
    """The cut is for project-tier callers only; an organisation administrator, and a
    Project Admin who is also the organisation's administrator, see what they saw before."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, False)
            await _seed(env)
            await _user(env.db, "d4_dual", ["projectadmin", "orgadmin"], organization_id=ORG_A, projects=[PROJ_A1])
            for persona in ("org_admin", "d4_dual"):
                rows = (await _get(env, persona, "/subscriptions")).json()
                row = next(row for row in rows if row["id"] == SUB_A1)
                assert row.get("payment_gateway_subscription_id") == "gw-d4-secret", persona
                state = (await _get(env, persona, "/plan-settings/effective-services")).json()["effective"]["projects"][PROJ_A1]
                assert "billing_status" in state, persona
                assert not any(sub.endswith("b1") for sub in (r["id"] for r in rows)), persona

    _run(scenario())


def test_feature_indicators_drop_every_non_boolean_value() -> None:
    from rbac_backend.routers.rbac_monetization import feature_indicators

    assert feature_indicators({"a": True, "b": False, "c": 1, "d": "yes", "e": {"x": True}, "f": None, "g": 0.0}) == {
        "a": True, "b": False,
    }
    assert feature_indicators(None) == {} and feature_indicators(["a"]) == {}
