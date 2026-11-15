"""CL-4A: the selected navbar project bounds the core DMS modules, over real HTTP, Mongo and RBAC.

``EffectiveScope = membership/role scope ∩ explicit selected project``. CL-3A/CL-3B
bound Variation, Hindrance, Programme and Chronology; CL-4A binds Documents,
Claims, IPC, Insurance, Bank Guarantees, Key Dates / EOT and their canonical
relationships to the same ``core/tenant_context.py`` boundary. This suite proves a
real persistence path per module with the CL-2 harness discipline: a signed JWT
through the real ``get_current_user``, the real ``PolicyService`` / ``ScopeService``
chain, the real role, permission and subscription seeds, no dependency override.

Opt-in: set ``RELATIONSHIP_CL4A_MONGODB_URI`` to a disposable replica set
(relationship writes need transactions). Every test creates and drops its own
uniquely named database. CI runs it in the replica-set step and fails if it skips.
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
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    CLAIM_A1,
    DOC_A2_ID,
    DOC_IN_ID,
    IPC_A1,
    MILESTONE_A1,
    ORG_A,
    PROJ_A1,
    PROJ_A2,
    _seed,
    _token,
)

MONGODB_URI_ENV = "RELATIONSHIP_CL4A_MONGODB_URI"

pytestmark = pytest.mark.integration

#: ``member_ab`` belongs to A1 and A2 - the principal the selection boundary
#: exists for. ``other_project_admin`` (CL-2 seed) belongs to A2 only.
MEMBER_AB = {"roles": ["projectadmin"], "organization_id": ORG_A, "projects": [PROJ_A1, PROJ_A2]}

CLAIM_A2 = "claim-a2"
IPC_A2 = "ipc-a2"
INS_A1, INS_A2 = "ins-a1", "ins-a2"
BG_A1, BG_A2 = "bg-a1", "bg-a2"
MILESTONE_A2 = "kd-a2"

#: One record per module in A1 and A2: (list path, record path template, A1 id, A2 id).
MODULES: dict[str, tuple[str, str, str, str]] = {
    "documents": ("/api/documents", "/api/documents/{id}", DOC_IN_ID, DOC_A2_ID),
    "claims": ("/api/claims", "/api/claims/{id}", CLAIM_A1, CLAIM_A2),
    "ipc": ("/api/ipc-bills", "/api/ipc-bills/{id}", IPC_A1, IPC_A2),
    "insurance": ("/api/insurance", "/api/insurance/{id}", INS_A1, INS_A2),
    "bank_guarantees": ("/api/bank-guarantees", "/api/bank-guarantees/{id}", BG_A1, BG_A2),
    "key_dates": ("/api/key-dates", "/api/key-dates/{id}", MILESTONE_A1, MILESTONE_A2),
}


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _app() -> FastAPI:
    from rbac_backend.routers.bank_guarantees import router as bg_router
    from rbac_backend.routers.claims import router as claims_router
    from rbac_backend.routers.document_relationships import router as relationship_router
    from rbac_backend.routers.documents import router as documents_router
    from rbac_backend.routers.insurance import router as insurance_router
    from rbac_backend.routers.ipc_bills import router as ipc_router
    from rbac_backend.routers.key_dates import router as key_dates_router

    app = FastAPI()
    for router in (relationship_router, documents_router, claims_router, ipc_router, insurance_router,
                   bg_router, key_dates_router):
        app.include_router(router, prefix="/api")
    return app


async def _seed_core(db: Any) -> None:
    """The CL-2 seeds (A1 Claim / IPC / Key Date, A1 + A2 letters) plus an A2 twin per module."""
    await _seed(db)
    await db.users.insert_one(
        {"_id": "user-member_ab", "email": "member_ab@example.com", "username": "member_ab",
         "disabled": False, "account_type": "client_user", "organizations": [], **MEMBER_AB}
    )
    await db.claims.insert_one(
        {"_id": CLAIM_A2, "claim_ref": "CLM-A2", "title": "Claim A2", "organization_id": ORG_A,
         "project_id": PROJ_A2, "status": "draft", "evidence_frozen_at": None}
    )
    await db.ipc_bills.insert_one(
        {"_id": IPC_A2, "ipc_number": "IPC-A2", "organization_id": ORG_A, "project_id": PROJ_A2, "status": "submitted"}
    )
    await db.insurance_policies.insert_many(
        [
            {"_id": pid, "policy_number": f"POL-{pid}", "insurance_type": "CAR", "organization_id": ORG_A,
             "project_id": project, "status": "active", "date_of_issue": datetime(2026, 1, 1),
             "date_of_expiry": datetime(2027, 1, 1)}
            for pid, project in ((INS_A1, PROJ_A1), (INS_A2, PROJ_A2))
        ]
    )
    await db.bank_guarantees.insert_many(
        [
            {"_id": bid, "bg_number": f"BG-{bid}", "bg_type": "performance", "organization_id": ORG_A,
             "project_id": project, "status": "valid", "bg_amount": 1000.0,
             "issue_date": datetime(2026, 1, 1), "expiry_date": datetime(2027, 1, 1)}
            for bid, project in ((BG_A1, PROJ_A1), (BG_A2, PROJ_A2))
        ]
    )
    # The CL-2 Key Date row predates this suite and lacks a model-required field;
    # a response built from it would 500. Complete it rather than widen CL-2.
    await db.key_date_milestones.update_one({"_id": MILESTONE_A1}, {"$set": {"contractual_week_number": 40}})
    await db.key_date_milestones.insert_one(
        {"_id": MILESTONE_A2, "milestone_ref": "KD-02", "title": "Section 2", "organization_id": ORG_A,
         "project_id": PROJ_A2, "contractual_week_number": 30}
    )


class Env:
    def __init__(self, db: Any, client: httpx.AsyncClient) -> None:
        self.db = db
        self._client = client

    async def call(self, method: str, persona: str, path: str, project: str | None, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {_token(persona)}"}
        if project:
            headers["X-Proj-Id"] = project
        return await self._client.request(method, path, headers=headers, **kwargs)


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
    name = f"relationship_cl4a_{uuid.uuid4().hex[:12]}"
    db = mongo[name]
    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    try:
        await _seed_core(db)
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(db, client)
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _code(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return None
    return detail.get("code") if isinstance(detail, dict) else None


def _forbidden(response: httpx.Response) -> bool:
    return response.status_code == 403 and _code(response) == "context_forbidden"


def _selection_required(response: httpx.Response) -> bool:
    return response.status_code == 400 and _code(response) == "selection_required"


def _ids(response: httpx.Response) -> set[str]:
    assert response.status_code == 200, response.text
    body = response.json()
    rows = body.get("documents", body.get("items")) if isinstance(body, dict) else body
    return {str(row.get("id") or row.get("_id")) for row in rows}


# --------------------------------------------------------------------------- #
# Record level: the matrix every module answers the same way
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("module", sorted(MODULES))
def test_record_is_bound_by_the_selected_project(module: str) -> None:
    _list, record, a1, _a2 = MODULES[module]
    path = record.format(id=a1)

    async def scenario() -> None:
        async with _env() as env:
            allowed = await env.call("GET", "member_ab", path, PROJ_A1)
            assert allowed.status_code == 200, allowed.text
            # Member of A1 and A2, B selected: the A1 record is refused.
            assert _forbidden(await env.call("GET", "member_ab", path, PROJ_A2))
            # Nothing selected: a record-level request must name its project.
            assert _selection_required(await env.call("GET", "member_ab", path, None))
            # Not a member of A1: the selection it may use is A2, and A1 stays refused.
            assert _forbidden(await env.call("GET", "other_project_admin", path, PROJ_A2))
            # A1 is not a selection a non-member may make at all.
            assert _forbidden(await env.call("GET", "other_project_admin", path, PROJ_A1))
            # Superadmin bypasses membership, never the explicit selection.
            assert _forbidden(await env.call("GET", "superadmin", path, PROJ_A2))
            assert _selection_required(await env.call("GET", "superadmin", path, None))
            assert (await env.call("GET", "superadmin", path, PROJ_A1)).status_code == 200

    _run(scenario())


@pytest.mark.parametrize("module", sorted(MODULES))
def test_list_follows_the_selected_project(module: str) -> None:
    listing, _record, a1, a2 = MODULES[module]

    async def scenario() -> None:
        async with _env() as env:
            under_a1 = _ids(await env.call("GET", "member_ab", listing, PROJ_A1))
            under_a2 = _ids(await env.call("GET", "member_ab", listing, PROJ_A2))
            assert a1 in under_a1 and a2 not in under_a1
            assert a2 in under_a2 and a1 not in under_a2
            # Nothing selected: bounded by membership (both of the member's projects).
            unselected = _ids(await env.call("GET", "member_ab", listing, None))
            assert {a1, a2} <= unselected
            # The A2-only member never sees A1, selected or not.
            assert a1 not in _ids(await env.call("GET", "other_project_admin", listing, None))
            # A filter may narrow the selection, never leave it.
            leave = await env.call("GET", "member_ab", listing, PROJ_A2, params={"project_id": PROJ_A1})
            assert _forbidden(leave), leave.text

    _run(scenario())


def test_create_body_must_be_the_selected_project() -> None:
    async def scenario() -> None:
        async with _env() as env:
            refused = await env.call(
                "POST", "member_ab", "/api/claims", PROJ_A2, json={"title": "Wrong project", "project_id": PROJ_A1}
            )
            assert _forbidden(refused), refused.text
            assert _selection_required(
                await env.call("POST", "member_ab", "/api/claims", None, json={"title": "No selection", "project_id": PROJ_A1})
            )
            assert await env.db.claims.count_documents({"title": {"$in": ["Wrong project", "No selection"]}}) == 0
            created = await env.call(
                "POST", "member_ab", "/api/claims", PROJ_A1, json={"title": "Right project", "project_id": PROJ_A1}
            )
            assert created.status_code in (200, 201), created.text
            assert await env.db.claims.count_documents({"title": "Right project", "project_id": PROJ_A1}) == 1

    _run(scenario())


def test_eot_workflow_is_bound_by_the_selected_project() -> None:
    async def scenario() -> None:
        async with _env() as env:
            path = "/api/key-dates/workflow"
            leave = await env.call("GET", "member_ab", path, PROJ_A2, params={"project_id": PROJ_A1})
            assert _forbidden(leave), leave.text
            assert _selection_required(await env.call("GET", "member_ab", path, None, params={"project_id": PROJ_A1}))
            own = await env.call("GET", "member_ab", path, PROJ_A1, params={"project_id": PROJ_A1})
            assert own.status_code == 200, own.text

    _run(scenario())


# --------------------------------------------------------------------------- #
# Canonical relationships: forward write, reverse lookup, Link to Record
# --------------------------------------------------------------------------- #


def test_claim_relationship_reverse_lookup_and_link_to_record_follow_the_selection() -> None:
    async def scenario() -> None:
        async with _env() as env:
            links = f"/api/entities/claim/{CLAIM_A1}/document-links:batch"
            body = {"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"}]}
            assert _forbidden(await env.call("POST", "member_ab", links, PROJ_A2, json=body))
            assert _selection_required(await env.call("POST", "member_ab", links, None, json=body))
            assert await env.db.entity_document_links.count_documents({"target_id": CLAIM_A1}) == 0
            created = await env.call("POST", "member_ab", links, PROJ_A1, json=body)
            assert created.status_code == 201, created.text

            reverse = f"/api/documents/{DOC_IN_ID}/entity-links"
            under_a1 = await env.call("GET", "member_ab", reverse, PROJ_A1)
            assert [(row["target_type"], row["target_id"]) for row in under_a1.json()["links"]] == [("claim", CLAIM_A1)]
            # B selected: the A1 Claim row is never revealed.
            under_a2 = await env.call("GET", "member_ab", reverse, PROJ_A2)
            assert under_a2.status_code == 200 and under_a2.json()["links"] == []
            # Nothing selected: bounded by membership (PR #22's list rule, kept).
            unselected = await env.call("GET", "member_ab", reverse, None)
            assert [row["target_id"] for row in unselected.json()["links"]] == [CLAIM_A1]

            targets = f"/api/documents/{DOC_IN_ID}/link-targets"
            offered = await env.call("GET", "member_ab", targets, PROJ_A1, params={"target_type": "claim"})
            assert offered.status_code == 200, offered.text
            assert {row["target_id"] for row in offered.json()["targets"]} == {CLAIM_A1}
            hidden = await env.call("GET", "member_ab", targets, PROJ_A2, params={"target_type": "claim"})
            # A2 selected, Document in A1: nothing is offered (the write would be refused).
            assert hidden.status_code == 200, hidden.text
            assert hidden.json()["targets"] == []
            assert _selection_required(await env.call("GET", "member_ab", targets, None, params={"target_type": "claim"}))

            types = f"/api/documents/{DOC_IN_ID}/link-target-types"
            assert "claim" in {row["target_type"] if isinstance(row, dict) else row
                               for row in (await env.call("GET", "member_ab", types, PROJ_A1)).json()["target_types"]}
            assert "claim" not in {row["target_type"] if isinstance(row, dict) else row
                                   for row in (await env.call("GET", "member_ab", types, PROJ_A2)).json()["target_types"]}

    _run(scenario())
