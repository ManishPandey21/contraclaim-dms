"""DEF-4 (owner decisions 2026-11-19): billing and subscription authorisation, both ways.

``BILLING_EXPLICIT_PERMISSIONS`` off (the default): exactly today's behaviour, including the
legacy aliases (``organizations:read`` / ``orgs:view`` / ``reports:view`` reach billing views)
and Project Admin's role-name checkout. On: billing and subscription checks need the name
itself, granted on a role that applies in scope; financial records are separated from
operational status; checkout needs an explicit ``subscription.upgrade``; effective services
are scoped and cut to the caller's grants.

"Target grants" model a database after the approved grant step
(``services/billing_permission_plan.TARGET_ROLE_GRANTS``); nothing in the application
applies them. Real HTTP through the production ``app``; nothing overridden.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import _role, _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
PROJECT_SUB = "sub-proj-a1"
FINANCIAL_KEYS = {"billing_status", "billing_period", "payment_gateway_subscription_id"}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _headers(persona: str, *, step_up_for: Optional[tuple] = None) -> Dict[str, str]:
    import jwt  # type: ignore[import-untyped]

    from rbac_backend.core.config import settings

    now = int(time.time())
    token = jwt.encode({"sub": f"{persona}@example.com", "type": "access", "iat": now, "exp": now + 3600},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    headers = {"Authorization": f"Bearer {token}"}
    if step_up_for:
        from rbac_backend.services.step_up_service import StepUpService

        user_id, action = step_up_for
        headers["X-Step-Up-Token"] = StepUpService().create_token(user_id=user_id, action=action)
    return headers


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "BILLING_EXPLICIT_PERMISSIONS", on)


async def _seed(env: Any) -> Dict[str, str]:
    """A project subscription with financial fields, the billing administrator, a contract
    manager, a reporter, and custom roles, in organisation A."""
    await env.db.subscriptions.insert_one(
        {"_id": PROJECT_SUB, "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None,
         "plan_code": "dms_enterprise", "status": "active", "billing_status": "active",
         "billing_period": "monthly", "payment_gateway_subscription_id": "gw-secret-1",
         "current_period_end": datetime(2027, 1, 1), "updated_at": datetime.utcnow()}
    )
    await env.db.subscriptions.insert_one(
        {"_id": "sub-proj-a2", "organization_id": ORG_A, "project_id": PROJ_A2, "package_id": None,
         "plan_code": "dms_enterprise", "status": "active", "billing_status": "active", "updated_at": datetime.utcnow()}
    )
    await env.db.roles.insert_many([
        _role("def4-custom-records", ["billing.record.view"], organization_id=ORG_A),
        _role("def4-custom-orgread", ["organizations:read"], organization_id=ORG_A),
    ])
    users = {
        "billing_admin": await _user(env.db, "def4_billing_admin", ["contraclaim_billing_admin"], organization_id=ORG_A),
        "contract_manager": await _user(env.db, "def4_contract_manager", ["contractmgr_org"], organization_id=ORG_A),
        "reporter": await _user(env.db, "def4_reporter", ["reporter"], organization_id=ORG_A),
        "custom_records": await _user(env.db, "def4_custom_records", ["def4-custom-records"], organization_id=ORG_A),
        "custom_orgread": await _user(env.db, "def4_custom_orgread", ["def4-custom-orgread"], organization_id=ORG_A),
    }
    return users


async def _grant_target(env: Any) -> None:
    """Model the approved grant step on this test database only."""
    from rbac_backend.services.billing_permission_plan import TARGET_ROLE_GRANTS
    from rbac_backend.services.permission_service import complete_grant_change

    for role_id, names in TARGET_ROLE_GRANTS.items():
        await env.db.roles.update_one({"_id": role_id}, {"$addToSet": {"permissions": {"$each": sorted(names)}}})
    ids = [str(doc["_id"]) async for doc in env.db.users.find({}, {"_id": 1})]
    await complete_grant_change(ids)


async def _status(env: Any, persona: str, method: str, path: str, **kwargs: Any) -> int:
    response = await env.client.request(method, f"{BASE}{path}", headers=kwargs.pop("headers", None) or _headers(persona), **kwargs)
    env.client.cookies.clear()
    return response.status_code


READS: List[str] = [
    f"/billing/records/{ORG_A}",
    f"/billing/review-queue/{ORG_A}",
    f"/billing/summary/{ORG_A}",
    f"/billing/history/{ORG_A}",
]


# --------------------------------------------------------------------------- #
# Flag off: today's behaviour, aliases included
# --------------------------------------------------------------------------- #


def test_flag_off_keeps_todays_behaviour(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, False)
            await _seed(env)
            for persona in ("org_admin", "org_user", "def4_contract_manager", "def4_custom_orgread"):
                for path in READS:
                    assert await _status(env, persona, "GET", path) == 200, (persona, path)
            # Decision 4 (2026-11-20, both states): project-level callers never read financial
            # detail, though the alias still grants them the name.
            for persona in ("project_user", "project_admin"):
                for path in READS:
                    assert await _status(env, persona, "GET", path) == 403, (persona, path)
            # reports:view reaches subscription.usage.view (single subscription read).
            assert await _status(env, "def4_reporter", "GET", f"/subscriptions/sub-{ORG_A}") == 200
            # Decision 4 (2026-11-20, both flag states): a project-tier caller's subscription
            # listing is operational only; an organisation member keeps full rows.
            rows = (await env.client.get(f"{BASE}/subscriptions", headers=_headers("project_admin"))).json()
            assert rows and not any(FINANCIAL_KEYS & set(row) for row in rows), rows
            org_rows = (await env.client.get(f"{BASE}/subscriptions", headers=_headers("org_admin"))).json()
            assert any(FINANCIAL_KEYS & set(row) for row in org_rows)
            # Project Admin may start a checkout for their project by role name (today).
            checkout = await _status(
                env, "project_admin", "POST", "/subscriptions/checkout",
                headers=_headers("project_admin", step_up_for=("user-project_admin", "subscription.checkout")),
                json={"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_enterprise"},
            )
            assert checkout != 403, "flag off must keep the role-name checkout path"
            # Never another organisation's, in either state.
            assert await _status(env, "org_admin", "GET", f"/billing/records/{ORG_B}") == 403

    _run(scenario())


# --------------------------------------------------------------------------- #
# Flag on
# --------------------------------------------------------------------------- #


def test_flag_on_aliases_no_longer_reach_billing(monkeypatch) -> None:
    """Without the grant step, nobody but Super Admin reads billing: grant before revoke."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, True)
            await _seed(env)
            for persona in ("org_admin", "org_user", "project_user", "project_admin",
                            "def4_contract_manager", "def4_custom_orgread"):
                for path in READS:
                    assert await _status(env, persona, "GET", path) == 403, (persona, path)
            assert await _status(env, "def4_reporter", "GET", f"/subscriptions/sub-{ORG_A}") == 403
            for path in READS:
                assert await _status(env, "superadmin", "GET", path) == 200, path
            # An explicit grant on a custom organisation role counts, in its organisation only.
            assert await _status(env, "def4_custom_records", "GET", f"/billing/records/{ORG_A}") == 200
            assert await _status(env, "def4_custom_records", "GET", f"/billing/records/{ORG_B}") == 403
            assert await _status(env, "def4_custom_records", "GET", f"/billing/history/{ORG_A}") == 403

    _run(scenario())


def test_flag_on_with_target_grants_follows_the_matrix(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, True)
            await _seed(env)
            await _grant_target(env)
            for persona in ("org_admin", "def4_billing_admin"):
                for path in READS:
                    assert await _status(env, persona, "GET", path) == 200, (persona, path)
                    assert await _status(env, persona, "GET", path.replace(ORG_A, ORG_B)) == 403, (persona, path)
            for persona in ("org_user", "project_user", "project_admin", "def4_contract_manager", "def4_reporter"):
                for path in READS:
                    assert await _status(env, persona, "GET", path) == 403, (persona, path)
            # Invoices and subscription history: financial.
            for path in (f"/subscriptions/{PROJECT_SUB}/history", f"/subscriptions/{PROJECT_SUB}/invoice-preview"):
                assert await _status(env, "org_admin", "GET", path) != 403, path
                assert await _status(env, "project_admin", "GET", path) == 403, path

    _run(scenario())


def test_flag_on_project_admin_sees_status_not_finance(monkeypatch) -> None:
    """Decision 1: the project's subscription, entitlements and expiry; no financial detail."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, True)
            await _seed(env)
            await _grant_target(env)
            listing = await env.client.get(f"{BASE}/subscriptions", headers=_headers("project_admin"))
            assert listing.status_code == 200, listing.text[:300]
            rows = listing.json()
            # Only their project: not the organisation's subscription, not the sibling project's.
            assert [row["id"] for row in rows] == [PROJECT_SUB], rows
            assert not (FINANCIAL_KEYS & set(rows[0])), rows[0]
            # Decision 4: status and expiry; no plan or add-on commercial detail.
            assert rows[0]["status"] == "active" and rows[0]["current_period_end"].startswith("2027-01-01")
            assert "plan_code" not in rows[0] and "active_add_ons" not in rows[0]
            # The organisation administrator sees full rows of the organisation.
            admin_rows = (await env.client.get(f"{BASE}/subscriptions", headers=_headers("org_admin"))).json()
            assert {row["id"] for row in admin_rows} >= {PROJECT_SUB, "sub-proj-a2", f"sub-{ORG_A}"}
            assert all(row.get("billing_status") for row in admin_rows)
            assert not any(row["organization_id"] == ORG_B for row in admin_rows)
            # The billing administrator (organisation-level role, not organisation-wide) sees the
            # organisation's full rows too, consistent with reading its billing records.
            billing_rows = (await env.client.get(f"{BASE}/subscriptions", headers=_headers("def4_billing_admin"))).json()
            assert {row["id"] for row in billing_rows} >= {PROJECT_SUB, "sub-proj-a2", f"sub-{ORG_A}"}, billing_rows
            assert all(row.get("billing_status") for row in billing_rows)
            # A project user holds no subscription view: refused, not an empty financial list.
            assert await _status(env, "project_user", "GET", "/subscriptions") == 403
            # Full plan settings are financial: organisation administrator only.
            assert await _status(env, "project_admin", "GET", "/plan-settings") == 403
            assert await _status(env, "org_admin", "GET", "/plan-settings") == 200

    _run(scenario())


def test_flag_on_effective_services_are_scoped_and_minimal(monkeypatch) -> None:
    """Decision 2: available to every member for their scope; only what each may see."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, True)
            await _seed(env)
            await _grant_target(env)

            async def effective(persona: str) -> Dict[str, Any]:
                response = await env.client.get(f"{BASE}/plan-settings/effective-services", headers=_headers(persona))
                assert response.status_code == 200, (persona, response.text[:300])
                return response.json()["effective"]

            user = await effective("project_user")
            assert set(user["projects"]) == {PROJ_A1}, user
            assert set(user["projects"][PROJ_A1]) <= {"source", "status", "dms_enabled", "drafting_enabled", "features",
                                                       "current_period_end", "trial", "trial_ends_at"}
            assert "plan_code" not in user["projects"][PROJ_A1]
            admin = await effective("project_admin")
            assert set(admin["projects"]) == {PROJ_A1}
            # The organisation entry stays operational-only for a project administrator.
            assert "plan_code" not in admin["organizations"].get(ORG_A, {}), admin["organizations"]
            # Decision 4 (2026-11-20): Project Admin gets the minimum-disclosure view even with
            # subscription.status.view: status, feature indicators, expiry; no plan detail.
            assert "plan_code" not in admin["projects"][PROJ_A1]
            assert "billing_status" not in admin["projects"][PROJ_A1]
            assert str(admin["projects"][PROJ_A1].get("current_period_end", "")).startswith("2027-01-01")
            org_admin = await effective("org_admin")
            assert set(org_admin["organizations"]) == {ORG_A}
            assert {PROJ_A1, PROJ_A2} <= set(org_admin["projects"])
            assert "billing_status" in org_admin["organizations"][ORG_A], "financial holders see the full state"
            # Before, any holder of subscription.entitlement.manage saw every organisation.
            billing = await effective("def4_billing_admin")
            assert set(billing["organizations"]) == {ORG_A}, billing["organizations"].keys()
            assert "billing_status" in billing["organizations"][ORG_A]
            # No billing-finance permission is needed to know whether a feature is on.
            assert "dms_enabled" in user["projects"][PROJ_A1]

    _run(scenario())


def test_flag_on_financial_mutations_need_explicit_scoped_authority(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, True)
            users = await _seed(env)
            await _grant_target(env)
            checkout_body = {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_enterprise"}
            # Project Admin can no longer purchase by role name; nor can the organisation admin,
            # who holds the views but no mutation.
            for persona, user_id in (("project_admin", "user-project_admin"), ("org_admin", "user-org_admin")):
                refused = await _status(
                    env, persona, "POST", "/subscriptions/checkout",
                    headers=_headers(persona, step_up_for=(user_id, "subscription.checkout")), json=checkout_body,
                )
                assert refused == 403, persona
                for action, path, body in (
                    ("subscription.upgrade", f"/subscriptions/{PROJECT_SUB}/upgrade", {"new_plan_code": "dms_enterprise"}),
                    ("subscription.cancel", f"/subscriptions/{PROJECT_SUB}/cancel", {}),
                ):
                    code = await _status(env, persona, "POST", path,
                                         headers=_headers(persona, step_up_for=(user_id, action)), json=body)
                    assert code in {403, 422}, (persona, path, code)
                    if code == 422:
                        pytest.fail(f"{persona} {path}: body rejected before authorisation; fix the body")
            # The billing administrator holds the mutations explicitly: an organisation-level
            # checkout is authorised (the no-op gateway answers 200 here)...
            billing_headers = _headers("def4_billing_admin", step_up_for=(users["billing_admin"], "subscription.checkout"))
            allowed = await _status(env, "def4_billing_admin", "POST", "/subscriptions/checkout",
                                    headers=billing_headers, json={"organization_id": ORG_A, "plan_code": "dms_enterprise"})
            assert allowed == 200
            # ...but not for a project outside their scope (no organisation-wide scope, not on A1).
            project_level = await _status(env, "def4_billing_admin", "POST", "/subscriptions/checkout",
                                          headers=billing_headers, json=checkout_body)
            assert project_level == 403
            # ...but not in another organisation.
            foreign = await _status(
                env, "def4_billing_admin", "POST", "/subscriptions/checkout",
                headers=_headers("def4_billing_admin", step_up_for=(users["billing_admin"], "subscription.checkout")),
                json={"organization_id": ORG_B, "plan_code": "dms_enterprise"},
            )
            assert foreign == 403

    _run(scenario())


def test_flag_on_advertised_list_drops_alias_billing_names(monkeypatch) -> None:
    """The client must not offer what the flag-on policy refuses."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.permission_service import PermissionService

            await _seed(env)
            _flag(monkeypatch, False)
            before = set(await PermissionService().get_effective_permission_names("user-org_user"))
            assert "billing.plan.view" in before, "today the alias advertises it"
            _flag(monkeypatch, True)
            after = set(await PermissionService().get_effective_permission_names("user-org_user"))
            assert not any(name.startswith(("billing.", "subscription.")) for name in after), after
            await _grant_target(env)
            admin = set(await PermissionService().get_effective_permission_names("user-org_admin"))
            assert {"billing.record.view", "subscription.history.view", "subscription.status.view"} <= admin
            assert not ({"subscription.upgrade", "subscription.cancel"} & admin)

    _run(scenario())


def test_the_p05_billing_reading_counts_who_keeps_and_loses_access(monkeypatch) -> None:
    """The read-only P-05 extension, on the same seeded database: counts, no names."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            from pymongo import MongoClient

            from rbac_backend.tests.integration.test_claim_arbitration_readings_mongo import _reader, _uri

            reader = _reader()
            client = MongoClient(_uri(), serverSelectionTimeoutMS=5_000)
            try:
                sync_db = client[env.db.name]
                before = {name: sync_db[name].count_documents({}) for name in ("users", "roles", "subscriptions")}
                reading = reader._read_billing_permission_impact(reader.ReadOnlyDatabase(sync_db))
                after = {name: sync_db[name].count_documents({}) for name in ("users", "roles", "subscriptions")}
            finally:
                client.close()
            assert after == before
            # orguser, projectuser, projectadmin, contract manager, reporter and the custom
            # organizations:read holder reach billing only through the aliases; the
            # organisation administrators keep it through the grant step.
            assert reading["users_relying_on_legacy_aliases_for_billing"] >= 6
            assert reading["of_whom_keep_billing_after_the_grant_step"] >= 2  # org_admin, foreign_org_admin
            losing = reading["losing_billing_by_role_type"]
            for role_type in ("orguser", "projectuser", "projectadmin", "contractmgr_org", "reporter", "custom"):
                assert losing.get(role_type, 0) >= 1, (role_type, losing)
            assert reading["keeping_billing_by_role_type"].get("orgadmin", 0) >= 1
            assert ORG_A in reading["losing_billing_by_organization"]
            assert reading["project_admins_with_role_name_checkout_today"] >= 2
            assert reading["users_holding_subscription_entitlement_manage"] >= 1  # the billing administrator
            assert reading["custom_roles_holding_legacy_view_names"] == 1
            assert reading["custom_roles_holding_explicit_billing_names"] == 1
            missing = reading["grant_step_names_missing_per_built_in_role"]
            assert "billing.record.view" in missing["orgadmin"] and "subscription.status.view" in missing["projectadmin"]
            text = repr(reading)
            for private in ("example.com", "def4_", "user-"):
                assert private not in text, private

    _run(scenario())


def test_flag_on_a_project_role_grant_never_reaches_organisation_level(monkeypatch) -> None:
    """Review M2: if Project Admin's built-in role is ever given a financial view or a mutation,
    it must count for their project, never for the organisation or its other projects."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, True)
            await _seed(env)
            await _grant_target(env)
            from rbac_backend.services.permission_service import complete_grant_change

            await env.db.roles.update_one(
                {"_id": "projectadmin"},
                {"$addToSet": {"permissions": {"$each": ["billing.record.view", "subscription.history.view", "subscription.upgrade"]}}},
            )
            await complete_grant_change(["user-project_admin"])
            for path in (f"/billing/records/{ORG_A}", f"/billing/history/{ORG_A}", f"/subscriptions/sub-{ORG_A}/history"):
                assert await _status(env, "project_admin", "GET", path) == 403, path
            org_checkout = await _status(
                env, "project_admin", "POST", "/subscriptions/checkout",
                headers=_headers("project_admin", step_up_for=("user-project_admin", "subscription.checkout")),
                json={"organization_id": ORG_A, "plan_code": "dms_enterprise"},
            )
            assert org_checkout == 403, "a project role's grant reached organisation level"
            # Their own project: explicit scoped authority now exists, so it is allowed.
            project_checkout = await _status(
                env, "project_admin", "POST", "/subscriptions/checkout",
                headers=_headers("project_admin", step_up_for=("user-project_admin", "subscription.checkout")),
                json={"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_enterprise"},
            )
            assert project_checkout == 200
            # The project's own history, yes; a sibling project's, no.
            assert await _status(env, "project_admin", "GET", f"/subscriptions/{PROJECT_SUB}/history") == 200
            assert await _status(env, "project_admin", "GET", "/subscriptions/sub-proj-a2/history") == 403

    _run(scenario())


def test_flag_on_scope_evaluation_reads_grants_once_per_permission(monkeypatch) -> None:
    """Review M3: effective services and the subscription list evaluate every scope from one
    grants load per permission, not one per organisation and project."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, True)
            await _seed(env)
            await _grant_target(env)
            await env.db.projects.insert_many(
                [{"_id": f"proj-bulk-{n}", "name": f"Bulk {n}", "organization_id": ORG_A} for n in range(40)]
            )
            from rbac_backend.services.permission_service import PermissionService

            calls = {"n": 0}
            original = PermissionService.scoped_grants

            async def counting(self: Any, db: Any, user_doc: Any) -> Any:
                calls["n"] += 1
                return await original(self, db, user_doc)

            monkeypatch.setattr(PermissionService, "scoped_grants", counting)
            response = await env.client.get(f"{BASE}/plan-settings/effective-services", headers=_headers("org_admin"))
            assert response.status_code == 200
            assert len(response.json()["effective"]["projects"]) >= 42
            assert calls["n"] <= 2, calls  # one per permission evaluated (history, status)
            calls["n"] = 0
            listing = await env.client.get(f"{BASE}/subscriptions", headers=_headers("org_admin"))
            assert listing.status_code == 200 and calls["n"] <= 2, calls

    _run(scenario())
