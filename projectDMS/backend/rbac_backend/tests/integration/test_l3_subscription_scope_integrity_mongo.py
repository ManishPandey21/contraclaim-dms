"""L3 (owner decision 2026-11-20): ``PUT /subscriptions/{id}`` preserves the stored scope.

Before this fix the route authorised the caller against the stored scope and then wrote
``project_id`` from the body (and silently ignored ``organization_id``), so a caller
authorised on one project could move the subscription to a sibling project, another
organisation's project or organisation-wide (``null``), and the entitlement went with it.
Now an omitted scope field is unchanged, the same value is a no-op, and any other value,
explicit ``null`` included, is refused (400) for everyone, Super Admin included. The write
is compare-and-set on the scope the caller was authorised against (409 on a concurrent
change). A transfer is a separate operation, not designed here.

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
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2, PROJ_B1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
STEP_UP = "subscription.entitlement.manage"
SUB_A1 = "l3-sub-a1"
SUB_A2 = "l3-sub-a2"
SUB_B1 = "l3-sub-b1"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


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
    for sub_id, org, project in ((SUB_A1, ORG_A, PROJ_A1), (SUB_A2, ORG_A, PROJ_A2), (SUB_B1, ORG_B, PROJ_B1)):
        await env.db.subscriptions.insert_one(
            {"_id": sub_id, "organization_id": org, "project_id": project, "package_id": None,
             "plan_code": "dms_enterprise", "status": "active", "billing_status": "active",
             "auto_renew": True, "updated_at": datetime.utcnow()}
        )


async def _put(env: Any, persona: str, user_id: Optional[str], sub_id: str, body: Dict[str, Any]) -> int:
    response = await env.client.put(f"{BASE}/subscriptions/{sub_id}", json=body, headers=_headers(persona, user_id))
    env.client.cookies.clear()
    return response.status_code


async def _scope(env: Any, sub_id: str) -> tuple:
    doc = await env.db.subscriptions.find_one({"_id": sub_id})
    return doc.get("organization_id"), doc.get("project_id"), doc.get("auto_renew")


ATTEMPTS = [
    {"project_id": PROJ_A2, "auto_renew": False},  # sibling project
    {"project_id": None, "auto_renew": False},  # organisation-wide promotion (explicit null)
    {"project_id": PROJ_B1, "auto_renew": False},  # another organisation's project
    {"project_id": "no-such-project", "auto_renew": False},
    {"organization_id": ORG_B, "auto_renew": False},  # another organisation
    {"organization_id": None, "auto_renew": False},
    {"organizationId": ORG_B, "auto_renew": False},
    {"projectId": PROJ_A2, "auto_renew": False},
    {"organization_id": ORG_B, "project_id": PROJ_B1, "auto_renew": False},
]


def test_scope_changes_are_refused_and_nothing_is_written(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            billing_admin = await _user(env.db, "l3_billing_admin", ["contraclaim_billing_admin"], organization_id=ORG_A,
                                        projects=[PROJ_A1, PROJ_A2])
            for persona, user_id in (("l3_billing_admin", billing_admin), ("superadmin", None)):
                for body in ATTEMPTS:
                    assert await _put(env, persona, user_id, SUB_A1, body) == 400, (persona, body)
                    assert await _scope(env, SUB_A1) == (ORG_A, PROJ_A1, True), (persona, body)

    _run(scenario())


def test_omitted_or_unchanged_scope_is_an_ordinary_update(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            billing_admin = await _user(env.db, "l3_ok_admin", ["contraclaim_billing_admin"], organization_id=ORG_A,
                                        projects=[PROJ_A1])
            assert await _put(env, "l3_ok_admin", billing_admin, SUB_A1, {"auto_renew": False}) == 200
            assert await _scope(env, SUB_A1) == (ORG_A, PROJ_A1, False)
            same = {"organization_id": ORG_A, "project_id": PROJ_A1, "auto_renew": True}
            assert await _put(env, "l3_ok_admin", billing_admin, SUB_A1, same) == 200
            assert await _scope(env, SUB_A1) == (ORG_A, PROJ_A1, True)
            event = await env.db.audit_events.find_one({"action": "subscription.updated", "actor_id": billing_admin})
            assert event and event["project_id"] == PROJ_A1

    _run(scenario())


def test_authority_is_judged_on_the_stored_scope_not_the_body(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            billing_admin = await _user(env.db, "l3_cross_admin", ["contraclaim_billing_admin"], organization_id=ORG_A,
                                        projects=[PROJ_A1])
            # Organisation B's subscription, with a body naming organisation A: refused by scope.
            body = {"organization_id": ORG_A, "project_id": PROJ_A1, "auto_renew": False}
            assert await _put(env, "l3_cross_admin", billing_admin, SUB_B1, body) == 403
            assert await _scope(env, SUB_B1) == (ORG_B, PROJ_B1, True)
            # A project-bound grant (inserted directly: the role fences would refuse it) reaches
            # its own project's subscription only, whatever the body names.
            await env.db.roles.insert_one(_role("l3-proj-a1", [STEP_UP], organization_id=ORG_A, project_id=PROJ_A1))
            project_user = await _user(env.db, "l3_project_user", ["l3-proj-a1"], organization_id=ORG_A, projects=[PROJ_A1])
            assert await _put(env, "l3_project_user", project_user, SUB_A2, {"project_id": PROJ_A1, "auto_renew": False}) == 403
            assert await _scope(env, SUB_A2) == (ORG_A, PROJ_A2, True)
            assert await _put(env, "l3_project_user", project_user, SUB_A1, {"auto_renew": False}) == 200
            assert await _put(env, "l3_project_user", project_user, SUB_A1, {"project_id": None}) == 400
            assert await _scope(env, SUB_A1) == (ORG_A, PROJ_A1, False)

    _run(scenario())


class _ConcurrentScopeChange:
    """A database whose subscriptions collection moves the subscription to another project
    just before the update is applied: the race the compare-and-set closes."""

    def __init__(self, db: Any, sub_id: str, to_project: str) -> None:
        self._db = db
        self._sub_id = sub_id
        self._to_project = to_project
        self.subscriptions = self

    def __getattr__(self, name: str) -> Any:
        target = self._db.subscriptions if name in {"find_one", "find"} else self._db
        return getattr(target, name)

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], **kwargs: Any) -> Any:
        await self._db.subscriptions.update_one({"_id": self._sub_id}, {"$set": {"project_id": self._to_project}})
        return await self._db.subscriptions.update_one(query, update, **kwargs)


def test_stale_reads_and_concurrent_scope_changes_are_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import SubscriptionUpdate
            from rbac_backend.services.monetization_service import (
                MonetizationService,
                SubscriptionScopeChangeRefused,
                SubscriptionScopeConflict,
            )

            await _seed(env)
            actor = type("Actor", (), {"id": "l3-actor"})()
            service = MonetizationService(env.db)
            # Authorised against A1, but the stored scope is already A2 (a stale read).
            await env.db.subscriptions.update_one({"_id": SUB_A1}, {"$set": {"project_id": PROJ_A2}})
            with pytest.raises(SubscriptionScopeConflict):
                await service.update_subscription(SUB_A1, SubscriptionUpdate(auto_renew=False), actor,
                                                  expected_scope=(ORG_A, PROJ_A1))
            assert await _scope(env, SUB_A1) == (ORG_A, PROJ_A2, True)
            await env.db.subscriptions.update_one({"_id": SUB_A1}, {"$set": {"project_id": PROJ_A1}})
            # The scope changes between the read and the write: the compare-and-set misses.
            racing = MonetizationService(_ConcurrentScopeChange(env.db, SUB_A1, PROJ_A2))
            with pytest.raises(SubscriptionScopeConflict):
                await racing.update_subscription(SUB_A1, SubscriptionUpdate(auto_renew=False), actor,
                                                 expected_scope=(ORG_A, PROJ_A1))
            assert (await env.db.subscriptions.find_one({"_id": SUB_A1}))["auto_renew"] is True
            # Internal callers (lifecycle operations) cannot change scope either.
            await env.db.subscriptions.update_one({"_id": SUB_A1}, {"$set": {"project_id": PROJ_A1}})
            with pytest.raises(SubscriptionScopeChangeRefused):
                await service.update_subscription(SUB_A1, SubscriptionUpdate(project_id=None), actor)
            assert await _scope(env, SUB_A1) == (ORG_A, PROJ_A1, True)

    _run(scenario())
