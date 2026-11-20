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


def test_the_p05_subscription_security_reading_counts_the_impact(monkeypatch) -> None:
    """Read-only P-05 reading of who the entitlement and gateway remediations affect."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            old, new = datetime.utcnow() - timedelta(days=5), datetime.utcnow()
            await _seed(
                env,
                _sub("r-org", status="active", updated_at=old),
                _sub("r-p1-a", status="active", project=P1, updated_at=old),
                _sub("r-p1-b", status="trial", project=P1, updated_at=old),  # ambiguous with r-p1-a
                _sub("r-p2-due", status="past_due", project=P2, updated_at=old),  # no fallback any more
                _sub("r-p2-cancel", status="cancelled", project=P2, updated_at=new),  # shadowed r-p2-due before
                _sub("r-odd", status="mystery", project="proj-ent-3"),
                # N5: ended rows never govern, so two of them are not ambiguous ...
                _sub("r-end-a", status="active", project="proj-ent-7", ends_at=old - timedelta(days=1)),
                _sub("r-end-b", status="active", project="proj-ent-7", ends_at=old - timedelta(days=1)),
                # ... and an unreadable stamp does not order a pair (still ambiguous).
                _sub("r-bad-a", status="active", project="proj-ent-8", governing_since="not a date"),
                _sub("r-bad-b", status="active", project="proj-ent-8"),
                _sub("r-gw-1", status="cancelled", project="proj-ent-4", payment_gateway_subscription_id="gw-dup",
                     payment_provider="razorpay", payment_gateway_customer_id="c1"),
                _sub("r-gw-2", status="cancelled", project="proj-ent-5", payment_gateway_subscription_id="gw-dup",
                     payment_gateway_bound_by="checkout"),
                _sub("r-str", status="cancelled", project="proj-ent-6", ends_at="2030-01-01T00:00:00", starts_at="31/12/2025"),
            )
            from pymongo import MongoClient

            from rbac_backend.tests.integration.test_claim_arbitration_readings_mongo import _reader, _uri

            reader = _reader()
            client = MongoClient(_uri(), serverSelectionTimeoutMS=5_000)
            try:
                sync_db = client[env.db.name]
                before = sync_db["subscriptions"].count_documents({})
                reading = reader._read_subscription_security_impact(reader.ReadOnlyDatabase(sync_db))
                assert sync_db["subscriptions"].count_documents({}) == before
            finally:
                client.close()
            assert reading["ambiguous_scopes_by_organization"].get(ORG) == 2, reading
            assert reading["blocking_project_scopes_by_organization"].get(ORG) == 1
            assert reading["scopes_previously_shadowed_by_a_non_governing_row"] >= 1
            assert reading["unknown_status_rows"] >= 1
            assert reading["gateway_ids_bound_more_than_once"] == 1
            assert reading["string_dated_fields"] == 2 and reading["unreadable_dated_fields"] == 1
            assert reading["gateway_bindings_without_provenance"] >= 1
            assert reading["gateway_bindings_without_provider"] >= 1
            assert "r-" not in repr({k: v for k, v in reading.items() if not k.endswith("_by_organization")})

    _run(scenario())


# --------------------------------------------------------------------------- #
# Independent review of 5740b06: F1-F6 (one governing subscription per tier on the
# write side; fail-closed periods; the same gates for features, summary and quota)
# --------------------------------------------------------------------------- #


def _governing(rows: Any) -> list:
    from rbac_backend.services.subscription_resolution import GOVERNING_STATUSES

    return [r for r in rows if str(r.get("status") or "").lower() in GOVERNING_STATUSES]


def test_f1_a_paid_upgrade_checkout_governs_without_locking_the_tier_out(monkeypatch) -> None:
    async def scenario() -> None:
        import hashlib
        import hmac
        import json as _json

        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings
            from rbac_backend.services import monetization_service as module
            from rbac_backend.services.monetization_service import MonetizationService
            from rbac_backend.services.payment_gateway import NoOpPaymentGateway

            monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "razorpay", raising=False)
            monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", "ent-secret", raising=False)
            monkeypatch.setattr(module, "get_payment_gateway", lambda *a, **k: NoOpPaymentGateway())
            await _seed(env, _sub("u-old", status="active", plan="dms_starter"))
            checkout = await MonetizationService(env.db).start_checkout(
                organization_id=ORG, project_id=None, plan_code="dms_enterprise", billing_period="monthly",
                current_user=SimpleNamespace(id="ent-actor"))
            body = _json.dumps({"event": "subscription.activated", "created_at": 1000,
                                "payload": {"subscription": {"entity": {"id": checkout["gateway_subscription_id"]}}}},
                               sort_keys=True).encode()
            signature = hmac.new(b"ent-secret", body, hashlib.sha256).hexdigest()
            response = await env.client.post("/api/billing/webhooks/razorpay", content=body,
                                             headers={"x-razorpay-signature": signature, "x-razorpay-event-id": "up-1",
                                                      "Content-Type": "application/json"})
            assert response.json()["status"] == "processed", response.text
            # The newly activated plan governs; nothing is cancelled locally (the old plan's
            # gateway billing is the owner's decision, not a side effect: re-review R2).
            old = await env.db.subscriptions.find_one({"_id": "u-old"})
            assert old["status"] == "active" and "superseded_by" not in old
            allowed, reason, diagnostic = await _check(env, project=None)
            assert (allowed, reason) == (True, "dms_entitlement") and diagnostic["subscription_id"] == checkout["subscription_id"]

    _run(scenario())


def test_f1_create_and_reactivation_govern_by_latest_activation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import SubscriptionCreate, SubscriptionUpdate
            from rbac_backend.services.monetization_service import MonetizationService

            actor = SimpleNamespace(id="ent-actor")
            service = MonetizationService(env.db)
            await _seed(env, _sub("c-active", status="active", project=P1), _sub("c-cancelled", status="cancelled", project=P1))
            created = await service.create_subscription(
                SubscriptionCreate(organization_id=ORG, project_id=P1, plan_code="dms_enterprise"), actor)
            assert (await env.db.subscriptions.find_one({"_id": "c-active"}))["status"] == "active", "nothing is cancelled"
            assert (await _check(env))[2]["subscription_id"] == created["id"]
            # Reactivating the old cancelled row makes it the most recently activated.
            await service.update_subscription("c-cancelled", SubscriptionUpdate(status="active"), actor)
            allowed, reason, diagnostic = await _check(env)
            assert (allowed, reason, diagnostic["subscription_id"]) == (True, "dms_entitlement", "c-cancelled")
            # An ordinary edit of the other rows changes nothing.
            await service.update_subscription(created["id"], SubscriptionUpdate(auto_renew=False), actor)
            assert (await _check(env))[2]["subscription_id"] == "c-cancelled"

    _run(scenario())


def test_f2_concurrent_plan_settings_writes_resolve_deterministically(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import PlanSettingsMode, PlanSettingsScopeUpdate
            from rbac_backend.services.monetization_service import MonetizationService

            await _seed(env)
            actor = SimpleNamespace(id="ent-actor")

            async def set_plan(plan: str) -> None:
                await MonetizationService(env.db).update_plan_settings_scope(
                    PlanSettingsScopeUpdate(organization_id=ORG, mode=PlanSettingsMode.PLAN, plan_code=plan), actor)

            from rbac_backend.services.subscription_operations import OperationConflict

            for _ in range(10):
                # R6-7: plan-settings changes of one scope are serialised (scope lock); a
                # concurrent one is refused, never interleaved. At least one lands each round.
                outcomes = await asyncio.gather(set_plan("dms_starter"), set_plan("dms_professional"),
                                                set_plan("dms_enterprise"), return_exceptions=True)
                assert all(o is None or isinstance(o, OperationConflict) for o in outcomes), outcomes
                assert any(o is None for o in outcomes), outcomes
                rows = await env.db.subscriptions.find({"organization_id": ORG, "project_id": None}).to_list(length=None)
                latest = max(_governing(rows), key=lambda r: (r["governing_since"], str(r["_id"])))
                allowed, reason, diagnostic = await _check(env, project=None)
                assert (allowed, reason) == (True, "dms_entitlement"), reason
                assert diagnostic["subscription_id"] == str(latest["_id"])

    _run(scenario())


def test_f3_unreadable_periods_fail_closed_and_api_dates_are_stored_as_dates(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import SubscriptionCreate
            from rbac_backend.services.monetization_service import MonetizationService

            await _seed(env, _sub("i-org", status="active"), _sub("i-p1", status="active", project=P1, ends_at="31/12/2025"),
                        _sub("i-p2", status="trial", project=P2, trial_ends_at="soon"))
            assert (await _check(env, project=P1))[:2] == (False, "subscription_period_invalid")
            assert (await _check(env, project=P2))[:2] == (False, "subscription_period_invalid")
            created = await MonetizationService(env.db).create_subscription(
                SubscriptionCreate(organization_id=ORG, project_id="proj-ent-9", plan_code="dms_enterprise",
                                   starts_at=datetime(2026, 1, 1), ends_at=datetime(2030, 1, 1)),
                SimpleNamespace(id="ent-actor"))
            stored = await env.db.subscriptions.find_one({"organization_id": ORG, "project_id": "proj-ent-9"})
            assert isinstance(stored["starts_at"], datetime) and isinstance(stored["ends_at"], datetime), created

    _run(scenario())


def test_f4_features_summary_and_quota_follow_the_same_gates(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.entitlement_service import EntitlementService

            await _seed(env, _sub("q-org", status="active"), _sub("q-p1", status="past_due", project=P1))
            service = EntitlementService(env.db)
            effective = await service.effective_features(organization_id=ORG, project_id=P1)
            assert effective["dms_enabled"] is False and effective["status_class"] == "blocking"
            summary = await service.get_subscription_summary(organization_id=ORG, project_id=P1)
            assert summary["dms_enabled"] is False
            allowed, reason = await service.assert_quota_available(quota_key="document_upload", organization_id=ORG, project_id=P1)
            assert (allowed, reason) == (False, "subscription_past_due")
            assert (await service.assert_quota_available(quota_key="document_upload", organization_id=ORG, project_id=P2))[0]

    _run(scenario())


def test_f5_f6_objectid_project_ids_and_padded_statuses_resolve_like_the_display(monkeypatch) -> None:
    async def scenario() -> None:
        from bson import ObjectId

        async with _env(monkeypatch) as env:
            oid = ObjectId()
            await _seed(env, _sub("o-org", status="active"), _sub("o-p", status="past_due", project=None),
                        _sub("o-arch", status=" Archive ", project=P2))
            await env.db.subscriptions.update_one({"_id": "o-p"}, {"$set": {"project_id": oid}})
            assert (await _check(env, project=str(oid)))[:2] == (False, "subscription_past_due")
            assert (await _check(env, READ, P2))[:2] == (True, "archive_read_only")
            assert (await _check(env, WRITE, P2))[:2] == (False, "archive_read_only")

    _run(scenario())



# --------------------------------------------------------------------------- #
# Re-review of 53c78e6: R1 (convergence), R2 (no local cancellation), R3 (a row that grants
# nothing never displaces one that does)
# --------------------------------------------------------------------------- #


def test_r1_mixed_concurrent_writers_never_lock_a_tier_out(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import PlanSettingsMode, PlanSettingsScopeUpdate, SubscriptionCreate
            from rbac_backend.services.monetization_service import MonetizationService

            await _seed(env)
            actor = SimpleNamespace(id="ent-actor")
            for round_number in range(10):
                writers = []
                for n in range(4):
                    writers.append(MonetizationService(env.db).create_subscription(
                        SubscriptionCreate(organization_id=ORG, plan_code="dms_enterprise"), actor))
                    writers.append(MonetizationService(env.db).update_plan_settings_scope(
                        PlanSettingsScopeUpdate(organization_id=ORG, mode=PlanSettingsMode.PLAN, plan_code="dms_starter"), actor))
                from rbac_backend.services.subscription_operations import OperationConflict

                outcomes = await asyncio.gather(*writers, return_exceptions=True)
                # Plan-settings writers of one scope are serialised (R6-7): the losers are refused.
                assert all(not isinstance(o, BaseException) or isinstance(o, OperationConflict) for o in outcomes), outcomes
                rows = await env.db.subscriptions.find({"organization_id": ORG, "project_id": None}).to_list(length=None)
                latest = max(_governing(rows), key=lambda r: (r["governing_since"], str(r["_id"])))
                allowed, reason, diagnostic = await _check(env, project=None)
                assert (allowed, reason) == (True, "dms_entitlement"), (round_number, reason)
                assert diagnostic["subscription_id"] == str(latest["_id"]), round_number

    _run(scenario())


def test_r1_equal_activation_times_are_broken_by_id_deterministically(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            moment = datetime.utcnow()
            await _seed(env, _sub("t-a", status="active", governing_since=moment),
                        _sub("t-b", status="active", governing_since=moment))
            for _ in range(3):
                assert (await _check(env, project=None))[2]["subscription_id"] == "t-b"

    _run(scenario())


def test_r3_trials_and_future_rows_never_displace_a_working_subscription_for_good(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import SubscriptionCreate
            from rbac_backend.services.monetization_service import MonetizationService

            now = datetime.utcnow()
            await _seed(env, _sub("paid", status="active", project=P1, governing_since=now - timedelta(days=30)))
            service = MonetizationService(env.db)
            actor = SimpleNamespace(id="ent-actor")
            # A future-dated replacement does not govern until it starts.
            await service.create_subscription(SubscriptionCreate(organization_id=ORG, project_id=P1, plan_code="dms_enterprise",
                                                                 starts_at=now + timedelta(days=10)), actor)
            assert (await _check(env))[2]["subscription_id"] == "paid"
            # An ended row created later does not govern either.
            await service.create_subscription(SubscriptionCreate(organization_id=ORG, project_id=P1, plan_code="dms_enterprise",
                                                                 ends_at=now - timedelta(days=1)), actor)
            assert (await _check(env))[2]["subscription_id"] == "paid"
            # A trial over the paid plan governs while it runs; the paid plan was never cancelled.
            trial = await service.create_subscription(SubscriptionCreate(
                organization_id=ORG, project_id=P1, plan_code="dms_enterprise", status="trial", trial=True,
                trial_ends_at=now + timedelta(days=7)), actor)
            assert (await _check(env))[2]["subscription_id"] == trial["id"]
            assert (await env.db.subscriptions.find_one({"_id": "paid"}))["status"] == "active"
            # When the trial ends, the paid plan governs again.
            await env.db.subscriptions.update_one({"organization_id": ORG, "status": "trial"},
                                                  {"$set": {"trial_ends_at": now - timedelta(hours=1)}})
            allowed, reason, diagnostic = await _check(env)
            assert (allowed, reason, diagnostic["subscription_id"]) == (True, "dms_entitlement", "paid")

    _run(scenario())


# ---------------------------------------------------------------------------------------
# Re-review of d37824e, N1/N2: plan, billing-period and add-on changes are refused on a row
# that does not govern its scope, before the gateway is touched.
# ---------------------------------------------------------------------------------------


def _record_gateway_calls(monkeypatch: Any) -> list:
    from rbac_backend.services.monetization_service import MonetizationService

    calls: list = []

    async def recorded(self: Any, sub: Dict[str, Any], **kwargs: Any) -> None:
        calls.append((str(sub.get("id") or sub.get("_id")), kwargs))

    monkeypatch.setattr(MonetizationService, "_gateway_change", recorded)
    return calls


def test_n1_lifecycle_changes_on_an_outranked_row_are_refused_before_the_gateway(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import (
                AddOnActionRequest,
                ChangeBillingPeriodRequest,
                UpgradeDowngradeRequest,
            )
            from rbac_backend.services.monetization_service import MonetizationService, SubscriptionNotGoverning

            calls = _record_gateway_calls(monkeypatch)
            now = datetime.utcnow()
            await _seed(env, _sub("shadowed", status="active", plan="dms_starter", governing_since=now - timedelta(days=9)),
                        _sub("newest", status="trial", plan="dms_starter", governing_since=now - timedelta(days=1)))
            service = MonetizationService(env.db)
            actor = SimpleNamespace(id="ent-actor")
            before = await env.db.subscriptions.find_one({"_id": "shadowed"})
            attempts = [
                service.upgrade_subscription("shadowed", UpgradeDowngradeRequest(new_plan_code="dms_enterprise",
                                                                                  effective_immediately=True), actor),
                service.downgrade_subscription("shadowed", UpgradeDowngradeRequest(new_plan_code="dms_starter"), actor),
                service.change_billing_period("shadowed", ChangeBillingPeriodRequest(new_billing_period="annual"), actor),
                service.add_addon("shadowed", AddOnActionRequest(add_on_code="anything"), actor),
                service.remove_addon("shadowed", AddOnActionRequest(add_on_code="anything"), actor),
            ]
            for attempt in attempts:
                with pytest.raises(SubscriptionNotGoverning, match="outranked by subscription newest"):
                    await attempt
            after = await env.db.subscriptions.find_one({"_id": "shadowed"})
            assert after == before, "a refused change writes nothing"
            assert calls == [], "the gateway is never asked to bill an outranked row"
            assert (await env.db.subscription_history.count_documents({"subscription_id": "shadowed"})) == 0
            # The governing row is changed normally, and keeps governing.
            await service.upgrade_subscription("newest", UpgradeDowngradeRequest(new_plan_code="dms_enterprise",
                                                                                  effective_immediately=True), actor)
            assert [sid for sid, _ in calls] == ["newest"]
            assert (await _check(env, project=None))[2]["subscription_id"] == "newest"

    _run(scenario())


def test_n2_an_upgrade_never_revives_a_cancelled_or_pending_row(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import UpgradeDowngradeRequest
            from rbac_backend.services.monetization_service import MonetizationService, SubscriptionNotGoverning

            calls = _record_gateway_calls(monkeypatch)
            now = datetime.utcnow()
            await _seed(env, _sub("current", status="active", plan="dms_starter", governing_since=now - timedelta(days=2)),
                        _sub("old", status="cancelled", plan="dms_starter"),
                        _sub("unpaid", status="pending", plan="dms_starter"))
            service = MonetizationService(env.db)
            for row in ("old", "unpaid"):
                with pytest.raises(SubscriptionNotGoverning, match="only an active, pilot or trial subscription"):
                    await service.upgrade_subscription(row, UpgradeDowngradeRequest(new_plan_code="dms_enterprise",
                                                                                     effective_immediately=True),
                                                       SimpleNamespace(id="ent-actor"))
            assert {r["_id"]: r["status"] async for r in env.db.subscriptions.find({"organization_id": ORG})} == {
                "current": "active", "old": "cancelled", "unpaid": "pending"}
            assert calls == []
            assert (await _check(env, project=None))[2]["subscription_id"] == "current"

    _run(scenario())


def test_n1_an_unorderable_tier_refuses_changes_and_a_scheduled_row_may_be_amended(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import ChangeBillingPeriodRequest
            from rbac_backend.services.monetization_service import MonetizationService, SubscriptionNotGoverning

            _record_gateway_calls(monkeypatch)
            now = datetime.utcnow()
            await _seed(env, _sub("legacy-a", status="active", project=P1), _sub("legacy-b", status="active", project=P1),
                        _sub("scheduled", status="active", project=P2, governing_since=now,
                             starts_at=now + timedelta(days=5)),
                        _sub("p2-now", status="active", project=P2, governing_since=now - timedelta(days=1)))
            service = MonetizationService(env.db)
            actor = SimpleNamespace(id="ent-actor")
            with pytest.raises(SubscriptionNotGoverning, match="cannot be ordered"):
                await service.change_billing_period("legacy-a", ChangeBillingPeriodRequest(new_billing_period="annual"), actor)
            await service.change_billing_period("scheduled", ChangeBillingPeriodRequest(new_billing_period="annual"), actor)
            assert (await env.db.subscriptions.find_one({"_id": "scheduled"}))["billing_period"] == "annual"
            # A project row is not outranked by the organisation row (another tier).
            await env.db.subscriptions.insert_one(_sub("org-row", status="active", governing_since=now))
            await service.change_billing_period("p2-now", ChangeBillingPeriodRequest(new_billing_period="annual"), actor)

    _run(scenario())


def test_n1_the_route_answers_409_and_changes_nothing(monkeypatch) -> None:
    from rbac_backend.routers import rbac_monetization
    from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import BASE, _headers

    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            calls = _record_gateway_calls(monkeypatch)
            now = datetime.utcnow()
            await _seed(env, _sub("shadowed", status="active", plan="dms_starter", governing_since=now - timedelta(days=9)),
                        _sub("newest", status="active", plan="dms_starter", governing_since=now - timedelta(days=1)))
            response = await env.client.post(f"{BASE}/subscriptions/shadowed/upgrade",
                                             json={"new_plan_code": "dms_enterprise", "effective_immediately": True},
                                             headers=_headers("superadmin", action="subscription.upgrade"))
            assert response.status_code == 409, response.text
            assert "outranked" in response.json()["detail"]
            assert (await env.db.subscriptions.find_one({"_id": "shadowed"}))["plan_code"] == "dms_starter"
            assert calls == []

    _run(scenario())


# ---------------------------------------------------------------------------------------
# Review of b9ab8b3 (R6): the guard mirrors enforcement exactly.
# ---------------------------------------------------------------------------------------


def test_r6_1_a_scheduled_row_is_judged_at_its_start(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import UpgradeDowngradeRequest
            from rbac_backend.services.monetization_service import MonetizationService, SubscriptionNotGoverning

            calls = _record_gateway_calls(monkeypatch)
            now = datetime.utcnow()
            # Scheduled row stamped before the current one: once it starts the current one still governs.
            await _seed(env, _sub("sched", status="active", plan="dms_starter", starts_at=now + timedelta(days=5),
                                  governing_since=now - timedelta(days=10)),
                        _sub("current", status="active", plan="dms_starter", governing_since=now - timedelta(days=1)))
            with pytest.raises(SubscriptionNotGoverning, match="outranked by subscription current"):
                await MonetizationService(env.db).upgrade_subscription(
                    "sched", UpgradeDowngradeRequest(new_plan_code="dms_enterprise"), SimpleNamespace(id="ent-actor"))
            assert calls == []

    _run(scenario())


def test_r6_2_an_ended_or_expired_row_is_refused_whatever_its_tier_holds(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import UpgradeDowngradeRequest
            from rbac_backend.services.monetization_service import MonetizationService, SubscriptionNotGoverning

            calls = _record_gateway_calls(monkeypatch)
            now = datetime.utcnow()
            await _seed(env, _sub("org-row", status="active", governing_since=now - timedelta(days=3)),
                        _sub("p1-ended", status="active", plan="dms_starter", project=P1, ends_at=now - timedelta(days=1)),
                        _sub("p2-trial", status="trial", plan="dms_starter", project=P2,
                             trial_ends_at=now - timedelta(days=1)))
            service = MonetizationService(env.db)
            for row, why in (("p1-ended", "outranked by subscription org-row"), ("p2-trial", "outranked by subscription org-row")):
                with pytest.raises(SubscriptionNotGoverning, match=why):
                    await service.upgrade_subscription(row, UpgradeDowngradeRequest(new_plan_code="dms_enterprise"),
                                                       SimpleNamespace(id="ent-actor"))
            await env.db.subscriptions.delete_one({"_id": "org-row"})
            with pytest.raises(SubscriptionNotGoverning, match=r"does not govern its scope \(ended\)"):
                await service.upgrade_subscription("p1-ended", UpgradeDowngradeRequest(new_plan_code="dms_enterprise"),
                                                   SimpleNamespace(id="ent-actor"))
            assert calls == []

    _run(scenario())


def test_r6_3_a_change_decided_on_a_stale_state_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import UpgradeDowngradeRequest
            from rbac_backend.services.monetization_service import MonetizationService, SubscriptionNotGoverning

            now = datetime.utcnow()
            await _seed(env, _sub("current", status="active", plan="dms_starter", governing_since=now - timedelta(days=1)))
            service = MonetizationService(env.db)

            async def cancel_in_the_window(self: Any, sub: Dict[str, Any], **kwargs: Any) -> None:
                # A concurrent cancel commits after the guard and before the write.
                await env.db.subscriptions.update_one({"_id": "current"}, {"$set": {"status": "cancelled"}})

            monkeypatch.setattr(MonetizationService, "_gateway_change", cancel_in_the_window)
            with pytest.raises(SubscriptionNotGoverning, match="changed while this change was being made"):
                await service.upgrade_subscription("current", UpgradeDowngradeRequest(new_plan_code="dms_enterprise",
                                                                                       effective_immediately=True),
                                                   SimpleNamespace(id="ent-actor"))
            row = await env.db.subscriptions.find_one({"_id": "current"})
            assert (row["status"], row["plan_code"]) == ("cancelled", "dms_starter"), "not revived, not re-stamped"
            assert row["governing_since"] < datetime.utcnow() - timedelta(hours=12)

    _run(scenario())


@pytest.mark.parametrize("status", ["past_due", "paused", "archive", "offboarding"])
def test_r6_4_an_upgrade_never_lifts_a_payment_block_or_restriction(monkeypatch, status: str) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import UpgradeDowngradeRequest
            from rbac_backend.services.monetization_service import MonetizationService, SubscriptionNotGoverning

            calls = _record_gateway_calls(monkeypatch)
            await _seed(env, _sub("held", status=status, plan="dms_starter", governing_since=datetime.utcnow()))
            with pytest.raises(SubscriptionNotGoverning, match="only an active, pilot or trial subscription"):
                await MonetizationService(env.db).upgrade_subscription(
                    "held", UpgradeDowngradeRequest(new_plan_code="dms_enterprise", effective_immediately=True),
                    SimpleNamespace(id="ent-actor"))
            assert (await env.db.subscriptions.find_one({"_id": "held"}))["status"] == status
            assert calls == []

    _run(scenario())


def test_r6_9_a_row_enforcement_cannot_see_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from bson import ObjectId

            from rbac_backend.models.rbac_monetization import ChangeBillingPeriodRequest
            from rbac_backend.services.monetization_service import MonetizationService, SubscriptionNotGoverning

            calls = _record_gateway_calls(monkeypatch)
            org_oid = ObjectId()
            await _seed(env)
            await env.db.subscriptions.insert_one({**_sub("oid-org", status="active", governing_since=datetime.utcnow()),
                                                   "organization_id": org_oid})
            with pytest.raises(SubscriptionNotGoverning, match="does not govern its scope"):
                await MonetizationService(env.db).change_billing_period(
                    "oid-org", ChangeBillingPeriodRequest(new_billing_period="annual"), SimpleNamespace(id="ent-actor"))
            assert calls == []

    _run(scenario())


def test_r6_3_the_write_itself_is_compare_and_set_on_the_decided_state(monkeypatch) -> None:
    """The concurrent cancel lands after update_subscription has read the row (its own
    pre-check passes), so only the database filter can refuse the write."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import UpgradeDowngradeRequest
            from rbac_backend.services.monetization_service import MonetizationService, SubscriptionNotGoverning

            now = datetime.utcnow()
            await _seed(env, _sub("current", status="active", plan="dms_starter", governing_since=now - timedelta(days=1)))
            armed = {"on": False}

            class RacingSubscriptions:
                def __init__(self, inner: Any) -> None:
                    self._inner = inner

                def __getattr__(self, name: str) -> Any:
                    return getattr(self._inner, name)

                async def find_one(self, *args: Any, **kwargs: Any) -> Any:
                    row = await self._inner.find_one(*args, **kwargs)
                    if armed["on"]:
                        armed["on"] = False
                        await self._inner.update_one({"_id": "current"}, {"$set": {"status": "cancelled"}})
                    return row

            class RacingDb:
                def __init__(self, inner: Any) -> None:
                    self._inner = inner
                    self.subscriptions = RacingSubscriptions(inner.subscriptions)

                def __getattr__(self, name: str) -> Any:
                    return getattr(self._inner, name)

                def __getitem__(self, name: str) -> Any:
                    return self.subscriptions if name == "subscriptions" else self._inner[name]

            async def arm(self: Any, sub: Dict[str, Any], **kwargs: Any) -> None:
                armed["on"] = True  # the next read (update_subscription's) is followed by the cancel

            monkeypatch.setattr(MonetizationService, "_gateway_change", arm)
            service = MonetizationService(RacingDb(env.db))
            with pytest.raises(SubscriptionNotGoverning, match="changed while this change was being made"):
                await service.upgrade_subscription("current", UpgradeDowngradeRequest(new_plan_code="dms_enterprise",
                                                                                       effective_immediately=True),
                                                   SimpleNamespace(id="ent-actor"))
            row = await env.db.subscriptions.find_one({"_id": "current"})
            assert (row["status"], row["plan_code"]) == ("cancelled", "dms_starter")

    _run(scenario())
