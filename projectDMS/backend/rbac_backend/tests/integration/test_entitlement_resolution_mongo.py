"""Deterministic, status-aware entitlement resolution (owner decision 2026-11-20).

Before: ``EntitlementService._active_subscription`` took any subscription of the scope in
its period, whatever its status, preferring the most recently updated one, so an ordinary
edit of a cancelled subscription switched a scope's entitlement off. Now
(``services/subscription_resolution.py``): package, then project, then organisation; the first
tier holding a governing subscription decides; pending, cancelled, ended and expired-trial
rows never govern; past-due and paused govern (deny, no fallback); two governing rows in the
deciding tier are ambiguous (deny); the selection and its reason are recorded in the policy
audit. Real MongoDB; the ``/plan-settings`` display uses the same selection.

Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env

pytestmark = pytest.mark.integration

ORG = "org-ent"
P1 = "proj-ent-1"
P2 = "proj-ent-2"
READ = "dms.document.view"
WRITE = "dms.document.upload"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _sub(sub_id: str, *, status: str, project: Optional[str] = None, package: Optional[str] = None,
         plan: str = "dms_enterprise", **extra: Any) -> Dict[str, Any]:
    return {"_id": sub_id, "organization_id": ORG, "project_id": project, "package_id": package, "plan_code": plan,
            "status": status, "billing_status": "active" if status == "active" else "inactive",
            "updated_at": datetime.utcnow(), **extra}


async def _seed(env: Any, *subs: Dict[str, Any]) -> None:
    await env.db.organizations.update_one({"_id": ORG}, {"$set": {"name": "Entitlement org", "is_active": True}}, upsert=True)
    for project in (P1, P2):
        await env.db.projects.update_one({"_id": project}, {"$set": {"name": project, "organization_id": ORG, "is_active": True}},
                                         upsert=True)
    if subs:
        await env.db.subscriptions.insert_many([dict(sub) for sub in subs])


async def _check(env: Any, permission: str = READ, project: Optional[str] = P1, package: Optional[str] = None) -> tuple:
    from rbac_backend.services.entitlement_service import EntitlementService

    service = EntitlementService(env.db)
    result = await service.check_permission_entitlement(permission=permission, organization_id=ORG, project_id=project,
                                                        package_id=package)
    return (*result, service.last_resolution)


def test_a_cancelled_subscription_edited_recently_never_shadows_an_eligible_one(monkeypatch) -> None:
    """The review reproduction: an ordinary edit of a cancelled project subscription switched
    the project's entitlement off. Selection no longer reads updated_at."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, _sub("e-org-active", status="active", updated_at=datetime.utcnow() - timedelta(days=2)),
                        _sub("e-p1-cancelled", status="cancelled", project=P1, updated_at=datetime.utcnow() - timedelta(days=3)))
            assert (await _check(env))[:2] == (True, "dms_entitlement")
            # An ordinary metadata edit makes the cancelled row the most recently updated.
            from rbac_backend.models.rbac_monetization import SubscriptionUpdate
            from rbac_backend.services.monetization_service import MonetizationService

            await MonetizationService(env.db).update_subscription(
                "e-p1-cancelled", SubscriptionUpdate(auto_renew=False), SimpleNamespace(id="ent-actor"))
            allowed, reason, diagnostic = await _check(env)
            assert (allowed, reason) == (True, "dms_entitlement")
            assert diagnostic["subscription_id"] == "e-org-active" and diagnostic["tier"] == "organization"
            assert {"subscription_id": "e-p1-cancelled", "class": "cancelled"} in diagnostic["considered"]

    _run(scenario())


def test_precedence_package_then_project_then_organisation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.entitlement_service import EntitlementService

            await _seed(env, _sub("e-org", status="active", plan="dms_starter"),
                        _sub("e-p1", status="active", project=P1, plan="dms_professional"),
                        _sub("e-p1-pkg", status="active", project=P1, package="pkg-1", plan="dms_enterprise"))
            service = EntitlementService(env.db)
            pick = lambda **scope: service._active_subscription(organization_id=ORG, **scope)  # noqa: E731
            assert (await pick(project_id=P1, package_id="pkg-1"))["_id"] == "e-p1-pkg"
            assert (await pick(project_id=P1))["_id"] == "e-p1", "a package row never governs a project-wide request"
            assert (await pick(project_id=P1, package_id="other-pkg"))["_id"] == "e-p1"
            assert (await pick(project_id=P2))["_id"] == "e-org"
            assert (await pick())["_id"] == "e-org"

    _run(scenario())


@pytest.mark.parametrize(
    "project_status,expected",
    [
        ("cancelled", (True, "dms_entitlement", "e-org")),  # explicit rule: falls back (plan-settings "inherit")
        ("pending", (True, "dms_entitlement", "e-org")),  # never paid: falls back
        ("past_due", (False, "subscription_past_due", "e-p1")),  # blocking: no fallback
        ("paused", (False, "subscription_paused", "e-p1")),
        ("mystery", (False, "subscription_status_unknown", "e-p1")),  # unknown: fail closed
        ("trial", (True, "dms_entitlement", "e-p1")),
        ("pilot", (True, "dms_entitlement", "e-p1")),
    ],
)
def test_status_classes_and_the_fallback_rule(monkeypatch, project_status: str, expected: tuple) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, _sub("e-org", status="active"), _sub("e-p1", status=project_status, project=P1))
            allowed, reason, diagnostic = await _check(env)
            assert (allowed, reason, diagnostic["subscription_id"]) == expected, diagnostic

    _run(scenario())


def test_periods_and_trial_expiry(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            now = datetime.utcnow()
            await _seed(
                env,
                _sub("e-org", status="active"),
                _sub("e-p1-ended", status="active", project=P1, ends_at=now - timedelta(days=1)),
                _sub("e-p1-future", status="active", project=P1, starts_at=now + timedelta(days=1)),
                _sub("e-p2-expired-trial", status="trial", project=P2, trial_ends_at=now - timedelta(hours=1)),
            )
            assert (await _check(env, project=P1))[2]["subscription_id"] == "e-org", "ended and not-yet-started rows do not govern"
            allowed, reason, diagnostic = await _check(env, project=P2)
            assert diagnostic["subscription_id"] == "e-org" and allowed
            assert {"subscription_id": "e-p2-expired-trial", "class": "trial_expired"} in diagnostic["considered"]

    _run(scenario())


def test_archive_and_offboarding_keep_their_semantics(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, _sub("e-org", status="active"), _sub("e-p1", status="archive", project=P1),
                        _sub("e-p2", status="offboarding", project=P2))
            assert (await _check(env, READ, P1))[:2] == (True, "archive_read_only")
            assert (await _check(env, WRITE, P1))[:2] == (False, "archive_read_only")
            assert (await _check(env, "dms.document.download", P2))[:2] == (True, "offboarding_export")
            assert (await _check(env, READ, P2))[:2] == (False, "offboarding_export_only")

    _run(scenario())


def test_ambiguous_governing_subscriptions_fail_closed_and_are_shown_as_ambiguous(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.monetization_service import MonetizationService

            await _seed(env, _sub("e-org", status="active"), _sub("e-p1-a", status="active", project=P1, plan="dms_starter"),
                        _sub("e-p1-b", status="trial", project=P1, plan="dms_enterprise"))
            allowed, reason, diagnostic = await _check(env)
            assert (allowed, reason) == (False, "ambiguous_subscriptions")
            assert diagnostic["subscription_id"] is None and diagnostic["tier"] == "project"
            settings_payload = await MonetizationService(env.db).get_plan_settings(organization_ids=[ORG], project_ids=None)
            state = settings_payload["effective"]["projects"][P1]
            assert state["status"] == "ambiguous" and state["dms_enabled"] is False
            # The organisation scope itself is unambiguous.
            assert (await _check(env, project=None))[:2] == (True, "dms_entitlement")

    _run(scenario())


def test_the_display_matches_enforcement_for_blocking_and_fallback(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.monetization_service import MonetizationService

            await _seed(env, _sub("e-org", status="active"), _sub("e-p1", status="past_due", project=P1),
                        _sub("e-p2", status="cancelled", project=P2))
            payload = await MonetizationService(env.db).get_plan_settings(organization_ids=[ORG], project_ids=None)
            p1, p2 = payload["effective"]["projects"][P1], payload["effective"]["projects"][P2]
            assert p1["subscription_id"] == "e-p1" and p1["dms_enabled"] is False and p1["source"] == "project"
            assert p2["source"] == "inherited" and p2["subscription_id"] == "e-org" and p2["dms_enabled"] is True

    _run(scenario())


def test_concurrent_metadata_edits_never_change_the_outcome(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, _sub("e-org", status="active"), _sub("e-p1-cancelled", status="cancelled", project=P1))

            async def edit(n: int) -> None:
                await env.db.subscriptions.update_one({"_id": "e-p1-cancelled"},
                                                      {"$set": {"updated_at": datetime.utcnow() + timedelta(seconds=n),
                                                                "auto_renew": bool(n % 2)}})

            outcomes = await asyncio.gather(*[edit(n) for n in range(10)], *[_check(env) for _ in range(10)])
            checks = [o for o in outcomes if o is not None]
            assert {(allowed, reason, d["subscription_id"]) for allowed, reason, d in checks} == {(True, "dms_entitlement", "e-org")}

    _run(scenario())


def test_the_policy_audit_records_the_selected_subscription_and_reason(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from fastapi import HTTPException

            from rbac_backend.services.policy_service import PolicyService

            await _seed(env, _sub("e-org", status="active"), _sub("e-p1", status="past_due", project=P1))
            await env.db.users.insert_one({"_id": "user-ent-admin", "email": "ent_admin@example.com", "username": "ent_admin",
                                           "roles": ["orgadmin"], "organization_id": ORG, "organizations": [ORG],
                                           "projects": [P1], "disabled": False})
            principal = SimpleNamespace(id="user-ent-admin", roles=["orgadmin"], organization_id=ORG, organizations=[ORG],
                                        projects=[P1], account_type="client_user")
            with pytest.raises(HTTPException):
                await PolicyService().authorize(principal, READ, organization_id=ORG, project_id=P1, resource_type="probe")
            event = await env.db.audit_events.find_one({"action": "policy.authorize", "actor_id": "user-ent-admin",
                                                       "result": "deny"}, sort=[("created_at", -1)])
            assert event and event["reason"] == "subscription_past_due", event
            entitlement = event["metadata"]["entitlement"]
            assert entitlement["subscription_id"] == "e-p1" and entitlement["tier"] == "project"
            assert entitlement["status_class"] == "blocking"

    _run(scenario())
