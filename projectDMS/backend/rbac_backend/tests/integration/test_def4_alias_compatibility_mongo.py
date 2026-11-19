"""DEF-4 compatibility evidence (2026-11-19): what the legacy view aliases reach today.

Owner decision: do not remove the aliases or change billing/subscription visibility
yet; keep them non-delegable; assess compatibility and propose a separation. These tests
pin today's behaviour so the proposal rests on observed facts, and so a later separation
shows up as a deliberate change to them (each assertion names the alias that causes it):

- every built-in tenant role carries ``organizations:read``, which by alias passes
  ``billing.plan.view``, ``billing.invoice.view``/``download`` and
  ``subscription.history.view``; ``reporter`` carries ``reports:view``, which passes
  ``subscription.usage.view``;
- through those, an ordinary member of an organisation reads its billing records,
  review queue and subscription history (PolicyService billing domain: in scope);
- the aliases do not cross organisations;
- none of the three names can be put on a role by a non-Super-Admin (DEF-4 fix).

Real HTTP through the production ``app``; nothing overridden.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import _no_role_rate_limit, _step_up
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, _token

pytestmark = pytest.mark.integration

ALIAS_REACH = {
    "organizations:read": {"billing.plan.view", "billing.invoice.view", "billing.invoice.download", "subscription.history.view"},
    "orgs:view": {"billing.plan.view", "billing.invoice.view", "billing.invoice.download", "subscription.history.view"},
    "reports:view": {"subscription.usage.view"},
}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_the_alias_reach_is_as_assessed() -> None:
    from rbac_backend.core.permissions import equivalent_permissions

    for held, billing_checks in ALIAS_REACH.items():
        reach = set(equivalent_permissions(held) or set())
        assert billing_checks <= reach, (held, sorted(reach))


def test_ordinary_members_read_their_organisations_billing_through_the_alias(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            for persona in ("org_user", "project_user"):
                headers = {"Authorization": f"Bearer {_token(persona)}"}
                for path in (
                    f"/api/rbac-monetization/billing/history/{ORG_A}",  # subscription.history.view via organizations:read
                    f"/api/rbac-monetization/billing/records/{ORG_A}",  # billing.plan.view via organizations:read
                    f"/api/rbac-monetization/billing/review-queue/{ORG_A}",  # billing.plan.view via organizations:read
                ):
                    response = await env.client.get(path, headers=headers)
                    assert response.status_code == 200, (persona, path, response.status_code, response.text[:200])
                # Never another organisation's.
                foreign = await env.client.get(f"/api/rbac-monetization/billing/records/{ORG_B}", headers=headers)
                assert foreign.status_code == 403, (persona, foreign.text[:200])

    _run(scenario())


def test_the_three_names_stay_non_delegable(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _no_role_rate_limit(monkeypatch)
            headers = _step_up("org_admin", "user-org_admin")
            for name in ALIAS_REACH:
                response = await env.client.post(
                    "/api/roles", headers=headers,
                    json={"name": f"Viewer {name}", "permissions": [name], "organization_id": ORG_A},
                )
                assert response.status_code == 403, (name, response.status_code, response.text[:200])

    _run(scenario())
