"""Batched subtag lookup isolation over real HTTP, Mongo and RBAC.

``GET /api/tags/subtags/batch`` takes caller-supplied tag ids. Nothing about
those ids is trusted: they are intersected with the caller's authorized tag
universe (``AuthorizationService.build_tag_query``) inside the tag query, and
only the surviving parents reach the subtag query. A foreign, inactive or
non-existent tag therefore answers exactly alike -- absent -- so the endpoint
is not an existence oracle.

Same harness discipline as CL-2 (signed JWT through the real
``get_current_user``, real ``PolicyService``, real role/permission/subscription
seeds, no dependency override). Opt-in: set ``TAGS_SUBTAG_BATCH_MONGODB_URI`` to
a disposable replica set; every test creates and drops its own database.
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
from bson import ObjectId
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    ORG_A,
    ORG_B,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    _seed,
    _token,
)

MONGODB_URI_ENV = "TAGS_SUBTAG_BATCH_MONGODB_URI"

pytestmark = pytest.mark.integration

TAG_A = ObjectId()  # organization tag, Org A
TAG_B = ObjectId()  # organization tag, Org B (foreign to every Org A persona)
TAG_A2 = ObjectId()  # project tag, Org A / project A2
TAG_GLOBAL = "legacy-global-tag"  # legacy string id
TAG_A_INACTIVE = ObjectId()
TAG_MISSING = ObjectId()  # never stored

SELECTION = {"foreign_org_admin": PROJ_B1, "other_project_admin": PROJ_A2}


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _tag(_id: Any, name: str, visibility: str, organization_id: str | None = None,
         project_id: str | None = None, is_active: bool = True) -> dict[str, Any]:
    return {
        "_id": _id, "name": name, "visibility": visibility, "organization_id": organization_id,
        "project_id": project_id, "is_active": is_active, "created_by": "seed",
        "created_at": datetime(2026, 9, 1), "updated_at": datetime(2026, 9, 1),
    }


def _subtag(name: str, tag_id: Any, is_active: bool = True) -> dict[str, Any]:
    return {"_id": ObjectId(), "name": name, "tag_id": tag_id, "is_active": is_active,
            "created_at": datetime(2026, 9, 1), "updated_at": datetime(2026, 9, 1)}


async def _seed_tags(db: Any) -> None:
    await db.tags.insert_many(
        [
            _tag(TAG_A, "Payments", "organization", ORG_A),
            _tag(TAG_B, "Foreign secrets", "organization", ORG_B),
            _tag(TAG_A2, "A2 only", "project", ORG_A, PROJ_A2),
            _tag(TAG_GLOBAL, "Global", "global"),
            _tag(TAG_A_INACTIVE, "Retired", "organization", ORG_A, is_active=False),
        ]
    )
    await db.subtags.insert_many(
        [
            _subtag("Late payment", str(TAG_A)),
            _subtag("Retention", TAG_A),  # parent stored as ObjectId (legacy shape)
            _subtag("Withdrawn", str(TAG_A), is_active=False),
            _subtag("Org B subtag", str(TAG_B)),
            _subtag("A2 subtag", str(TAG_A2)),
            _subtag("Global subtag", TAG_GLOBAL),
            _subtag("Retired subtag", str(TAG_A_INACTIVE)),
        ]
    )


def _app() -> FastAPI:
    from rbac_backend.core.errors import register_domain_error_handler
    from rbac_backend.routers.tags import router as tags_router

    app = FastAPI()
    register_domain_error_handler(app)
    app.include_router(tags_router, prefix="/api")
    return app


class Env:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def batch(self, persona: str, *tag_ids: Any) -> httpx.Response:
        return await self._client.get(
            "/api/tags/subtags/batch",
            params=[("tag_ids", str(tag_id)) for tag_id in tag_ids],
            headers={"Authorization": f"Bearer {_token(persona)}", "X-Proj-Id": SELECTION.get(persona, PROJ_A1)},
        )


@asynccontextmanager
async def _env() -> AsyncIterator[Env]:
    from rbac_backend.core import database as database_module
    from rbac_backend.core.config import settings

    uri = _uri()
    assert not getattr(settings, "APP_REDIS_URL", None) and not getattr(settings, "RUNTIME_STATE_REDIS_URL", None), (
        "unset APP_REDIS_URL / RUNTIME_STATE_REDIS_URL: a local Redis turns cache fail-closed into false reds"
    )
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await mongo.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        mongo.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"tags_batch_{uuid.uuid4().hex[:12]}"
    db = mongo[name]
    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    try:
        await _seed(db)
        await _seed_tags(db)
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(client)
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _names(response: httpx.Response) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["name"] for item in response.json()["subtags"]}


def test_org_admin_gets_only_authorized_parents_subtags_in_one_response() -> None:
    async def scenario() -> None:
        async with _env() as env:
            response = await env.batch(
                "org_admin", TAG_A, TAG_B, TAG_A2, TAG_GLOBAL, TAG_A_INACTIVE, TAG_MISSING, TAG_A
            )
            # Own organisation (both parent-id shapes), own org's project tag,
            # global; never Org B, the inactive tag, or inactive subtags.
            assert _names(response) == {"Late payment", "Retention", "A2 subtag", "Global subtag"}
            body = response.json()
            assert body["truncated"] is False
            assert {item["tag_id"] for item in body["subtags"]} == {str(TAG_A), str(TAG_A2), TAG_GLOBAL}
            assert "Foreign secrets" not in response.text and str(TAG_B) not in response.text

    asyncio.run(scenario())


def test_a_foreign_tag_is_indistinguishable_from_a_missing_one() -> None:
    async def scenario() -> None:
        async with _env() as env:
            foreign = await env.batch("org_admin", TAG_B)
            missing = await env.batch("org_admin", TAG_MISSING)
            assert foreign.status_code == missing.status_code == 200
            assert foreign.json() == missing.json() == {"subtags": [], "truncated": False}

            # And from the other side: Org B cannot read Org A's subtags.
            reverse = await env.batch("foreign_org_admin", TAG_A, TAG_A2)
            assert reverse.status_code == 200
            assert reverse.json() == {"subtags": [], "truncated": False}
            assert _names(await env.batch("foreign_org_admin", TAG_B)) == {"Org B subtag"}

    asyncio.run(scenario())


def test_project_user_does_not_see_another_projects_tag() -> None:
    async def scenario() -> None:
        async with _env() as env:
            names = _names(await env.batch("project_user", TAG_A, TAG_A2, TAG_GLOBAL))
            assert "A2 subtag" not in names
            assert {"Late payment", "Retention", "Global subtag"} <= names

    asyncio.run(scenario())


def test_bounds_hold_on_the_real_stack() -> None:
    async def scenario() -> None:
        async with _env() as env:
            empty = await env.batch("org_admin")
            assert empty.status_code == 200 and empty.json() == {"subtags": [], "truncated": False}
            too_many = await env.batch("org_admin", *[ObjectId() for _ in range(101)])
            assert too_many.status_code == 422

    asyncio.run(scenario())


def test_a_caller_without_the_gate_permission_is_refused_403_before_validation() -> None:
    # Negative control for the real route gate: the service-account persona
    # holds no role, so PolicyService refuses it - exactly 403, and 403 rather
    # than 422 even when the ids are also malformed.
    async def scenario() -> None:
        async with _env() as env:
            refused = await env.batch("system_user", TAG_A)
            assert refused.status_code == 403, refused.text
            assert "Late payment" not in refused.text
            refused_bad_ids = await env.batch("system_user", *[ObjectId() for _ in range(101)])
            assert refused_bad_ids.status_code == 403, refused_bad_ids.text

    asyncio.run(scenario())
