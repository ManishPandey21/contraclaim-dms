"""F3 (owner decision 2026-11-20): ``package_id`` is a protected subscription-scope field.

``EntitlementService._active_subscription`` matches a subscription on organisation, project
**and package**, so changing a stored ``package_id`` moves the entitlement as surely as
changing the project. ``PUT /subscriptions/{id}`` let an entitlement manager do it (an
organisation-wide subscription made package-only; a package subscription promoted with
``null``). Now, as for organisation and project (L3): omitted is unchanged, the same value is
a no-op, any other value (explicit null, another or an invalid package, other spellings) is
refused with 400 for everyone, and the write is compare-and-set on all three fields.

Audit of writers (2026-11-20): on an existing subscription only
``MonetizationService.update_subscription`` can set ``package_id`` (the route and the
lifecycle operations, which never pass it); the lifecycle service, the payment webhook and
``/plan-settings/scope`` never write it; it is set only at creation (create, checkout,
plan-settings insert) and by the development seed. No transfer operation exists.

Real HTTP through the production ``app``; nothing overridden.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import _role, _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
STEP_UP = "subscription.entitlement.manage"
SUB_ORG = "f3-org"  # organisation-wide, no package
SUB_PKG = "f3-pkg"  # project A1, package pkg-a
SUB_PROJ = "f3-proj"  # project A1, no package
SUB_B = "f3-b"  # organisation B, package pkg-b


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_monetization_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests make more subscription updates a minute than the per-user limit (30)
    allows; the limiter is not under test here (same pattern as _no_role_rate_limit)."""
    from rbac_backend.routers import rbac_monetization

    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)


def _headers(persona: str, user_id: Optional[str] = None) -> Dict[str, str]:
    import jwt  # type: ignore[import-untyped]

    from rbac_backend.core.config import settings
    from rbac_backend.services.step_up_service import StepUpService

    now = int(time.time())
    token = jwt.encode({"sub": f"{persona}@example.com", "type": "access", "iat": now, "exp": now + 3600},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return {"Authorization": f"Bearer {token}",
            "X-Step-Up-Token": StepUpService().create_token(user_id=user_id or f"user-{persona}", action=STEP_UP)}


async def _seed(env: Any) -> None:
    base = {"plan_code": "dms_enterprise", "status": "active", "billing_status": "active", "auto_renew": True,
            "updated_at": datetime.utcnow()}
    await env.db.subscriptions.insert_many([
        {"_id": SUB_ORG, "organization_id": ORG_A, "project_id": None, "package_id": None, **base},
        {"_id": SUB_PKG, "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": "pkg-a", **base},
        {"_id": SUB_PROJ, "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None, **base},
        {"_id": SUB_B, "organization_id": ORG_B, "project_id": PROJ_B1, "package_id": "pkg-b", **base},
    ])


async def _put(env: Any, persona: str, user_id: Optional[str], sub_id: str, body: Dict[str, Any]) -> int:
    response = await env.client.put(f"{BASE}/subscriptions/{sub_id}", json=body, headers=_headers(persona, user_id))
    env.client.cookies.clear()
    return response.status_code


async def _state(env: Any, sub_id: str) -> tuple:
    doc = await env.db.subscriptions.find_one({"_id": sub_id})
    return doc.get("organization_id"), doc.get("project_id"), doc.get("package_id"), doc.get("auto_renew")


ATTEMPTS = {
    SUB_ORG: [
        {"package_id": "pkg-zz", "auto_renew": False},  # organisation-wide made package-only
        {"package_id": "pkg-b", "auto_renew": False},  # another organisation's package
        {"package_id": "no-such-package", "auto_renew": False},  # invalid reference
        {"packageId": "pkg-zz", "auto_renew": False},
        {"package": "pkg-zz", "auto_renew": False},
    ],
    SUB_PKG: [
        {"package_id": None, "auto_renew": False},  # promotion by explicit null
        {"package_id": "", "auto_renew": False},  # promotion by empty string
        {"package_id": "pkg-zz", "auto_renew": False},
        {"packageId": None, "auto_renew": False},
        {"package_ID": None, "auto_renew": False},  # F3 review 1: case and separator variants
        {"PackageId": None, "auto_renew": False},
        {"Package-Id": "zz", "auto_renew": False},
        {"ProjectID": None, "auto_renew": False},
        {"Organization_Id": ORG_B, "auto_renew": False},
        {"package_id": "pkg-a", "project_id": None, "auto_renew": False},  # same package, project promoted
    ],
}


def test_package_changes_are_refused_and_nothing_is_written(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            billing = await _user(env.db, "f3_billing", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            before = {sub: await _state(env, sub) for sub in ATTEMPTS}
            audits_before = await env.db.audit_events.count_documents({"action": "subscription.updated"})
            history_before = await env.db.subscription_history.count_documents({})
            for persona, user_id in (("f3_billing", billing), ("superadmin", None)):
                for sub_id, bodies in ATTEMPTS.items():
                    for body in bodies:
                        assert await _put(env, persona, user_id, sub_id, body) == 400, (persona, sub_id, body)
            for sub_id, state in before.items():
                assert await _state(env, sub_id) == state, sub_id
            # A refused attempt leaves no trace as a change.
            assert await env.db.audit_events.count_documents({"action": "subscription.updated"}) == audits_before
            assert await env.db.subscription_history.count_documents({}) == history_before

    _run(scenario())


def test_omitted_or_unchanged_package_is_an_ordinary_update(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            billing = await _user(env.db, "f3_ok", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            assert await _put(env, "f3_ok", billing, SUB_PKG, {"auto_renew": False}) == 200
            assert await _state(env, SUB_PKG) == (ORG_A, PROJ_A1, "pkg-a", False)
            assert await _put(env, "f3_ok", billing, SUB_PKG, {"package_id": "pkg-a", "auto_renew": True}) == 200
            assert await _put(env, "f3_ok", billing, SUB_ORG, {"package_id": None, "packageId": "", "auto_renew": False}) == 200
            assert await _state(env, SUB_ORG) == (ORG_A, None, None, False)
            event = await env.db.audit_events.find_one({"action": "subscription.updated", "resource_id": SUB_PKG})
            assert event and event["after"]["package_id"] == "pkg-a"

    _run(scenario())


def test_authority_follows_the_stored_scope_for_mixed_and_custom_roles(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            await env.db.roles.insert_many([
                _role("f3-a-entitlement", [STEP_UP], organization_id=ORG_A),
                _role("f3-a1-entitlement", [STEP_UP], organization_id=ORG_A, project_id=PROJ_A1),
            ])
            custom = await _user(env.db, "f3_custom_org", ["f3-a-entitlement"], organization_id=ORG_A, projects=[PROJ_A1])
            mixed = await _user(env.db, "f3_mixed", ["projectuser", "f3-a1-entitlement"], organization_id=ORG_A, projects=[PROJ_A1])
            for persona, user_id in (("f3_custom_org", custom), ("f3_mixed", mixed)):
                # Another organisation's subscription: refused by scope, whatever the body names.
                assert await _put(env, persona, user_id, SUB_B, {"package_id": "pkg-b", "auto_renew": False}) == 403, persona
                # Its own project's package subscription: an ordinary update passes, a package change does not.
                assert await _put(env, persona, user_id, SUB_PKG, {"auto_renew": False}) == 200, persona
                assert await _put(env, persona, user_id, SUB_PKG, {"package_id": None}) == 400, persona
            assert await _state(env, SUB_B) == (ORG_B, PROJ_B1, "pkg-b", True)
            assert (await _state(env, SUB_PKG))[2] == "pkg-a"

    _run(scenario())


def test_entitlement_resolution_is_unchanged_by_refused_attempts(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.entitlement_service import EntitlementService

            await _seed(env)
            service = EntitlementService(env.db)
            probes = [(ORG_A, PROJ_A1, None), (ORG_A, PROJ_A1, "pkg-a"), (ORG_A, PROJ_A2_OR_NONE, None)]

            async def resolved() -> list:
                out = []
                for org, project, package in probes:
                    sub = await service._active_subscription(organization_id=org, project_id=project, package_id=package)
                    out.append((sub or {}).get("_id"))
                return out

            baseline = await resolved()
            for sub_id, bodies in ATTEMPTS.items():
                for body in bodies:
                    assert await _put(env, "superadmin", None, sub_id, body) == 400
            assert await resolved() == baseline
            # Why it matters: the same change written directly moves the entitlement.
            await env.db.subscriptions.update_one({"_id": SUB_PKG}, {"$set": {"package_id": None}})
            assert await resolved() != baseline

    _run(scenario())


PROJ_A2_OR_NONE = None  # an organisation-level probe (no project)


class _ConcurrentPackageChange:
    """Moves the subscription to another package just before the update is applied."""

    def __init__(self, db: Any, sub_id: str, package: Optional[str]) -> None:
        self._db = db
        self._sub_id = sub_id
        self._package = package
        self.subscriptions = self

    def __getattr__(self, name: str) -> Any:
        target = self._db.subscriptions if name in {"find_one", "find"} else self._db
        return getattr(target, name)

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], **kwargs: Any) -> Any:
        await self._db.subscriptions.update_one({"_id": self._sub_id}, {"$set": {"package_id": self._package}})
        return await self._db.subscriptions.update_one(query, update, **kwargs)


def test_stale_reads_and_concurrent_package_changes_are_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import SubscriptionUpdate
            from rbac_backend.services.monetization_service import (
                MonetizationService,
                SubscriptionScopeChangeRefused,
                SubscriptionScopeConflict,
            )

            await _seed(env)
            actor = type("Actor", (), {"id": "f3-actor"})()
            service = MonetizationService(env.db)
            # Authorised against pkg-a, but the stored package is already pkg-zz.
            await env.db.subscriptions.update_one({"_id": SUB_PKG}, {"$set": {"package_id": "pkg-zz"}})
            with pytest.raises(SubscriptionScopeConflict):
                await service.update_subscription(SUB_PKG, SubscriptionUpdate(auto_renew=False), actor,
                                                  expected_scope=(ORG_A, PROJ_A1, "pkg-a"))
            await env.db.subscriptions.update_one({"_id": SUB_PKG}, {"$set": {"package_id": "pkg-a"}})
            # The package changes between the read and the write: the compare-and-set misses.
            racing = MonetizationService(_ConcurrentPackageChange(env.db, SUB_PKG, None))
            with pytest.raises(SubscriptionScopeConflict):
                await racing.update_subscription(SUB_PKG, SubscriptionUpdate(auto_renew=False), actor,
                                                 expected_scope=(ORG_A, PROJ_A1, "pkg-a"))
            assert (await env.db.subscriptions.find_one({"_id": SUB_PKG}))["auto_renew"] is True
            # Internal callers (lifecycle operations) cannot change the package either.
            await env.db.subscriptions.update_one({"_id": SUB_PKG}, {"$set": {"package_id": "pkg-a"}})
            with pytest.raises(SubscriptionScopeChangeRefused):
                await service.update_subscription(SUB_PKG, SubscriptionUpdate(package_id=None), actor)
            with pytest.raises(TypeError):
                await service.update_subscription(SUB_PKG, SubscriptionUpdate(auto_renew=False), actor,
                                                  expected_scope=(ORG_A, PROJ_A1))  # type: ignore[arg-type]
            assert await _state(env, SUB_PKG) == (ORG_A, PROJ_A1, "pkg-a", True)

    _run(scenario())


def test_stored_package_shapes_missing_empty_and_objectid(monkeypatch) -> None:
    """F3 review 2: the compare-and-set filter uses the raw stored value, so a field that is
    absent (checkout never writes it), an empty string, or an ObjectId must each accept an
    ordinary update and the matching no-op, and refuse a real change."""

    async def scenario() -> None:
        from bson import ObjectId

        async with _env(monkeypatch) as env:
            oid = ObjectId()
            base = {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_enterprise", "status": "active",
                    "billing_status": "active", "auto_renew": True, "updated_at": datetime.utcnow()}
            await env.db.subscriptions.insert_many([
                {"_id": "f3-missing", **base},
                {"_id": "f3-empty", **base, "package_id": ""},
                {"_id": "f3-oid", **base, "package_id": oid},
            ])
            cases = {
                "f3-missing": ([{"auto_renew": False}, {"package_id": None}, {"package_id": ""}], [{"package_id": "x"}]),
                "f3-empty": ([{"auto_renew": False}, {"package_id": None}, {"package_id": ""}], [{"package_id": "x"}]),
                "f3-oid": ([{"auto_renew": False}, {"package_id": str(oid)}], [{"package_id": None}, {"package_id": "x"}]),
            }
            for sub_id, (allowed, refused) in cases.items():
                for body in allowed:
                    assert await _put(env, "superadmin", None, sub_id, body) == 200, (sub_id, body)
                for body in refused:
                    assert await _put(env, "superadmin", None, sub_id, body) == 400, (sub_id, body)
            assert "package_id" not in await env.db.subscriptions.find_one({"_id": "f3-missing"})
            assert (await env.db.subscriptions.find_one({"_id": "f3-empty"}))["package_id"] == ""
            assert (await env.db.subscriptions.find_one({"_id": "f3-oid"}))["package_id"] == oid

    _run(scenario())
