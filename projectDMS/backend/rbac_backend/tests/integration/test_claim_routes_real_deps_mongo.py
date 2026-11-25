"""4B-02 / 4B-47: the claim routers over real HTTP, real MongoDB and real RBAC.

Every claim router added in Phases 1 to 4 (Claim Bench, findings, escalation)
was tested by calling route functions with stand-in policy, selection and
database objects. That pattern is how a broken factory once shipped past a
green suite (projectDMS/CLAUDE.md, "A green suite proves nothing about
dependency-injected code"). These tests instead send signed requests to the
production ``app`` itself:

- the routers as ``main.py`` mounts them, with their real ``get_*`` factories;
- the real ``get_current_user`` -> ``ActiveScope`` -> ``PolicyService`` chain,
  over roles and permissions seeded by the production initialisers;
- a disposable MongoDB replica set; nothing is overridden.

Opt-in: set ``CLAIM_BENCH_MONGODB_URI`` to a disposable replica set. CI sets it
in the real-Mongo step, where it must run, not skip.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    ORG_A,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    _seed,
    _token,
)

MONGODB_URI_ENV = "CLAIM_BENCH_MONGODB_URI"

pytestmark = pytest.mark.integration

CLAIM = "claim-bench-a1"


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _claim_doc() -> dict[str, Any]:
    return {
        "_id": CLAIM,
        "claim_ref": "CLM-001",
        "title": "Delayed handover of Site",
        "type": "eot",
        "status": "submitted",
        "description": "Right of way not handed over.",
        "event_date": datetime(2026, 3, 2),
        "notice_date": datetime(2026, 4, 11),
        "amount_claimed": 184500000.0,
        "eot_days_claimed": 146,
        "currency": "INR",
        "contract_clauses": ["8.4"],
        "linked_document_ids": [],
        "linked_letter_ids": [],
        "type_metadata": {},
        "organization_id": ORG_A,
        "project_id": PROJ_A1,
        "evidence_frozen_at": None,
        "created_at": datetime(2026, 4, 12),
        "updated_at": datetime(2026, 4, 12),
    }


class Env:
    def __init__(self, db: Any, client: httpx.AsyncClient) -> None:
        self.db = db
        self.client = client

    def headers(self, persona: str, project: str = PROJ_A1) -> dict[str, str]:
        return {"Authorization": f"Bearer {_token(persona)}", "X-Proj-Id": project}

    async def call(self, method: str, persona: str, path: str, project: str = PROJ_A1, **kwargs: Any) -> httpx.Response:
        return await self.client.request(method, path, headers=self.headers(persona, project), **kwargs)


@asynccontextmanager
async def _env(monkeypatch: pytest.MonkeyPatch, mode: str = "primary") -> AsyncIterator[Env]:
    from rbac_backend.core import database as database_module
    from rbac_backend.core.config import settings
    from rbac_backend.main import app

    uri = _uri()
    assert not getattr(settings, "APP_REDIS_URL", None) and not getattr(settings, "RUNTIME_STATE_REDIS_URL", None), (
        "unset APP_REDIS_URL / RUNTIME_STATE_REDIS_URL: a local Redis turns cache fail-closed into false reds"
    )
    # No dependency override anywhere: the point of this suite.
    assert app.dependency_overrides == {}, f"unexpected overrides: {list(app.dependency_overrides)}"
    monkeypatch.setattr(settings, "CLAIM_BENCH_ROLLOUT_MODE", mode)
    monkeypatch.setattr(settings, "CLAIM_BENCH_CANARY_TENANT_IDS", "")

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await mongo.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        mongo.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"claim_routes_{uuid.uuid4().hex[:12]}"
    db = mongo[name]
    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    # Each environment is a fresh app state: the in-process rate-limit counters (class-level,
    # shared by every RateLimiter in the process when no Redis is configured) start empty.
    # Without this, the fixed seeded user ids carry earlier modules' requests into a later
    # test's window, and that test sees 429 instead of the answer it asserts (2026-11-25:
    # test_m4_global_pricing_mongo, CI run 45020648950 attempt 1). Production limits are
    # unchanged.
    from rbac_backend.utils.rate_limiter import RateLimiter

    RateLimiter._shared_buckets.clear()
    RateLimiter._shared_user_requests.clear()
    try:
        await _seed(db)
        # The skill registry's indexes as the governed migration creates them in production
        # (T-4: skill writes verify skill_version_unique and refuse without it).
        from rbac_backend.migrations.v20261114_0001_skill_registry import INDEXES as SKILL_INDEXES
        from rbac_backend.migrations.v20261114_0001_skill_registry import apply_indexes as apply_skill_indexes

        await apply_skill_indexes(db, SKILL_INDEXES, dry_run=False)
        await db.claims.delete_many({})
        await db.claims.insert_one(_claim_doc())
        await db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "contractor"}})
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(db, client)
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _never_leaks(response: httpx.Response) -> None:
    """A refusal is a refusal: never a 200 with data, never a 500."""
    assert response.status_code in {401, 403, 404}, (response.status_code, response.text[:300])


# --------------------------------------------------------------------------- #
# Mounting and factories
# --------------------------------------------------------------------------- #


def test_the_claim_routers_are_mounted_on_the_production_app() -> None:
    from rbac_backend.main import app

    paths = set(app.openapi()["paths"])
    for path in (
        "/api/claims/bench/availability",
        "/api/claims/bench/signals",
        "/api/claims/{claim_id}/bench",
        "/api/claims/{claim_id}/bench/open",
        "/api/claims/{claim_id}/determinations",
        "/api/claims/escalation/preview",
    ):
        assert path in paths, path


# --------------------------------------------------------------------------- #
# Bench off: the server-side flag holds through the real chain
# --------------------------------------------------------------------------- #


def test_with_the_bench_off_every_bench_route_answers_404_and_availability_says_so(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, mode="off") as env:
            availability = await env.call("GET", "org_admin", "/api/claims/bench/availability")
            assert availability.status_code == 200, availability.text
            assert availability.json()["enabled"] is False
            for method, path in (
                ("GET", f"/api/claims/{CLAIM}/bench"),
                ("POST", f"/api/claims/{CLAIM}/bench/open"),
                ("GET", f"/api/claims/{CLAIM}/determinations"),
                ("GET", "/api/claims/bench/signals"),
            ):
                response = await env.call(method, "org_admin", path)
                assert response.status_code == 404, (path, response.status_code, response.text[:200])
            # Nothing was written while it was off.
            assert (await env.db.claims.find_one({"_id": CLAIM})).get("bench") is None

    _run(scenario())


# --------------------------------------------------------------------------- #
# Bench on: each router resolves its real dependencies end to end
# --------------------------------------------------------------------------- #


def test_the_bench_router_opens_and_reads_a_board_through_its_real_dependencies(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            opened = await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open")
            assert opened.status_code == 200, opened.text
            board = opened.json()
            assert board["bench"]["heads"], "the open board carries a first head"
            read = await env.call("GET", "org_admin", f"/api/claims/{CLAIM}/bench")
            assert read.status_code == 200, read.text
            assert read.json()["bench"]["board_version"] == board["bench"]["board_version"]
            stored = await env.db.claims.find_one({"_id": CLAIM})
            assert stored["bench"]["board_version"] == board["bench"]["board_version"]

    _run(scenario())


def test_the_findings_router_resolves_its_real_dependencies(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert (await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open")).status_code == 200
            response = await env.call("GET", "org_admin", f"/api/claims/{CLAIM}/determinations")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["items"] == [] and body["perspective"] == "contractor"
            # D-01 through the real chain: a contractor workspace cannot author an official determination.
            assert body["can_author"] is False and body["cannot_author_reason"]

    _run(scenario())


def test_the_escalation_router_resolves_its_real_dependencies(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert (await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open")).status_code == 200
            response = await env.call("POST", "org_admin", "/api/claims/escalation/preview", json={"claim_ids": [CLAIM]})
            # The seeded org-admin role holds claim-manage, so the real chain admits it.
            assert response.status_code == 200, response.text[:300]
            preview = response.json()
            assert [row["claim_id"] for row in preview["claims"]] == [CLAIM]
            # A preview writes nothing.
            assert (await env.db.claims.find_one({"_id": CLAIM})).get("arbitration_case_id") is None

    _run(scenario())


def test_a_letter_linked_as_the_claims_notice_becomes_each_heads_notice_record(monkeypatch) -> None:
    """4B-34 through the real relationship service: role "notice" on the claim is the notice record."""
    from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import DOC_IN_ID, DOC_OUT_ID

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert (await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open")).status_code == 200
            weak_text = "No notice letter is linked to the claim"

            async def preview_weak() -> list:
                response = await env.call("POST", "org_admin", "/api/claims/escalation/preview", json={"claim_ids": [CLAIM]})
                assert response.status_code == 200, response.text[:300]
                return response.json()["claims"][0]["weak"]

            # A letter linked as ordinary correspondence is not a notice.
            linked = await env.call(
                "POST", "org_admin", f"/api/entities/claim/{CLAIM}/document-links:batch",
                json={"links": [{"document_id": DOC_OUT_ID, "relationship_role": "correspondence"}]},
            )
            assert linked.status_code in (200, 201), linked.text[:300]
            assert any(weak_text in item for item in await preview_weak())

            linked = await env.call(
                "POST", "org_admin", f"/api/entities/claim/{CLAIM}/document-links:batch",
                json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "notice"}]},
            )
            assert linked.status_code in (200, 201), linked.text[:300]
            assert not any(weak_text in item for item in await preview_weak())

            run = await env.call(
                "POST", "org_admin", "/api/claims/escalation/run",
                json={"claim_ids": [CLAIM], "new_case": {"title": "ABC Infra v. Authority"}, "idempotency_key": "esc-route-test",
                      "acknowledged_weak": [CLAIM]},
            )
            assert run.status_code == 201, run.text[:300]
            rows = await env.db.arbitration_claim_matrix.find({"source_claim_id": CLAIM}).to_list(length=None)
            assert rows and all(row["notice_ids"] == [DOC_IN_ID] for row in rows), [r.get("notice_ids") for r in rows]

    _run(scenario())


# --------------------------------------------------------------------------- #
# Tenant isolation through the real chain
# --------------------------------------------------------------------------- #


def test_a_foreign_organisation_cannot_reach_the_claim_by_any_claim_router(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert (await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open")).status_code == 200
            for method, path, body in (
                ("GET", f"/api/claims/{CLAIM}/bench", None),
                ("POST", f"/api/claims/{CLAIM}/bench/open", None),
                ("GET", f"/api/claims/{CLAIM}/determinations", None),
                ("POST", "/api/claims/escalation/preview", {"claim_ids": [CLAIM]}),
            ):
                response = await env.call(method, "foreign_org_admin", path, project=PROJ_B1, json=body)
                _never_leaks(response)

    _run(scenario())


def test_another_projects_admin_cannot_reach_the_claim(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            for method, path in (
                ("GET", f"/api/claims/{CLAIM}/bench"),
                ("POST", f"/api/claims/{CLAIM}/bench/open"),
                ("GET", f"/api/claims/{CLAIM}/determinations"),
            ):
                _never_leaks(await env.call(method, "other_project_admin", path, project=PROJ_A2))
            assert (await env.db.claims.find_one({"_id": CLAIM})).get("bench") is None

    _run(scenario())


def test_an_unauthenticated_request_is_refused_with_401(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            response = await env.client.get(f"/api/claims/{CLAIM}/bench", headers={"X-Proj-Id": PROJ_A1})
            assert response.status_code == 401

    _run(scenario())
