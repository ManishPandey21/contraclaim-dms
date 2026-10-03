"""CL-3A: one correspondence, two scoped registers, over real HTTP, Mongo and RBAC.

Variation (CL-2) and the Hindrance & Constraint Register (PR #22) share the
canonical ``entity_document_links`` framework and, since CL-3A, the same
selected-project boundary (``core/tenant_context.py``). This suite proves them
together with the CL-2 harness discipline: a signed JWT through the real
``get_current_user``, the real ``PolicyService`` / ``ScopeService`` chain, the
real role and permission seeds, ObjectId-keyed Documents, no dependency override.

Opt-in: set ``RELATIONSHIP_CL3A_MONGODB_URI`` to a disposable replica set
(relationship writes need transactions). Every test creates and drops its own
uniquely named database. CI runs it in the replica-set step and fails if it skips.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    DOC_A2_ID,
    DOC_CONTRACT_ID,
    DOC_OUT_ID,
    DOC_IN,
    DOC_IN_ID,
    ORG_A,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    VAR_A1,
    VAR_A2,
    _seed,
    _token,
)

MONGODB_URI_ENV = "RELATIONSHIP_CL3A_MONGODB_URI"

pytestmark = pytest.mark.integration

#: Personas added to the CL-2 seeds. ``member_ab`` belongs to A1 and A2 - the
#: principal the selection boundary exists for.
EXTRA_PERSONAS: dict[str, dict[str, Any]] = {
    "member_ab": {"roles": ["projectadmin"], "organization_id": ORG_A, "projects": [PROJ_A1, PROJ_A2]},
    "evidence_manager": {"roles": ["evidence_graph_manager"], "organization_id": ORG_A, "projects": [PROJ_A1]},
}


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _app() -> FastAPI:
    from rbac_backend.routers.document_relationships import router as relationship_router
    from rbac_backend.routers.documents import router as documents_router
    from rbac_backend.routers.evidence_registers import router as registers_router
    from rbac_backend.routers.hindrances import router as hindrances_router
    from rbac_backend.routers.variations import router as variations_router

    app = FastAPI()
    for router in (relationship_router, documents_router, variations_router, hindrances_router, registers_router):
        app.include_router(router, prefix="/api")
    return app


class Env:
    def __init__(self, db: Any, client: httpx.AsyncClient) -> None:
        self.db = db
        self._client = client

    async def call(self, method: str, persona: str, path: str, project: str | None, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {_token(persona)}"}
        if project:
            headers["X-Proj-Id"] = project
        return await self._client.request(method, path, headers=headers, **kwargs)

    async def link(self, persona: str, project: str | None, target_type: str, target_id: str, document_id: str,
                   role: str = "correspondence") -> httpx.Response:
        return await self.call(
            "POST", persona, f"/api/entities/{target_type}/{target_id}/document-links:batch", project,
            json={"links": [{"document_id": document_id, "relationship_role": role}]},
        )

    async def reverse(self, persona: str, project: str | None, document_id: str = DOC_IN_ID) -> list[tuple[str, str]]:
        response = await self.call("GET", persona, f"/api/documents/{document_id}/entity-links", project)
        assert response.status_code == 200, response.text
        return sorted((row["target_type"], row["target_id"]) for row in response.json()["links"])

    async def remove(self, persona: str, project: str | None, link_id: str) -> httpx.Response:
        return await self.call(
            "POST", persona, f"/api/document-links/{link_id}:remove", project,
            json={"reason": "CL-3A verification", "expected_revision": 1},
        )

    async def create_hindrance(self, project: str, title: str) -> str:
        response = await self.call(
            "POST", "member_ab", "/api/hindrances", project,
            json={"project_id": project, "event_type": "hindrance", "title": title,
                  "start_date": "2026-02-10T00:00:00", "responsibility": "employer"},
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    async def audits(self, action: str, **metadata: Any) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"action": action}
        query.update({f"metadata.{key}": value for key, value in metadata.items()})
        return await self.db.audit_events.find(query).to_list(length=None)


@asynccontextmanager
async def _env() -> AsyncIterator[tuple[Env, str, str]]:
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
    name = f"relationship_cl3a_{uuid.uuid4().hex[:12]}"
    db = mongo[name]
    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    try:
        await _seed(db)
        # A custom role holding Evidence Graph manage/view but no dms.hindrance.*.
        await db.roles.insert_one(
            {"_id": "evidence_graph_manager", "name": "Evidence Graph Manager", "scope": "project",
             "is_system": False, "is_active": True,
             "permissions": ["dms.evidence_graph.view", "dms.evidence_graph.manage", "dms.document.view"]}
        )
        for persona, spec in EXTRA_PERSONAS.items():
            await db.users.insert_one(
                {"_id": f"user-{persona}", "email": f"{persona}@example.com", "username": persona,
                 "disabled": False, "account_type": "client_user", "organizations": [], **spec}
            )
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            env = Env(db, client)
            hin_a1 = await env.create_hindrance(PROJ_A1, "Access blocked at Station S2")
            hin_a2 = await env.create_hindrance(PROJ_A2, "Depot power outage")
            yield env, hin_a1, hin_a2
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _code(response: httpx.Response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def _forbidden(response: httpx.Response) -> bool:
    return response.status_code == 403 and _code(response) == "context_forbidden"


def _selection_required(response: httpx.Response) -> bool:
    return response.status_code == 400 and _code(response) == "selection_required"


# --------------------------------------------------------------------------- #
# 1-6. One letter, two registers, independent lifecycles
# --------------------------------------------------------------------------- #


def test_one_correspondence_links_to_a_variation_and_a_hindrance_independently() -> None:
    async def scenario() -> None:
        async with _env() as (env, hin_a1, _hin_a2):
            # 1. one incoming letter links to both registers
            variation = await env.link("member_ab", PROJ_A1, "variation", VAR_A1, DOC_IN_ID)
            hindrance = await env.link("member_ab", PROJ_A1, "delay_event", hin_a1, DOC_IN_ID)
            assert variation.status_code == 201, variation.text
            assert hindrance.status_code == 201, hindrance.text
            variation_link = variation.json()["links"][0]
            hindrance_link = hindrance.json()["links"][0]

            # 2. both appear in the Document Viewer's Linked Records, with routes
            reverse = await env.call("GET", "member_ab", f"/api/documents/{DOC_IN_ID}/entity-links", PROJ_A1)
            rows = {row["target_type"]: row for row in reverse.json()["links"]}
            assert set(rows) == {"variation", "delay_event"}
            assert rows["variation"]["target_route"] == f"/variations?variation_id={VAR_A1}"
            assert rows["delay_event"]["target_route"] == f"/hindrances/{hin_a1}"
            assert rows["delay_event"]["target_label"].startswith("HIN-")
            # ObjectId-keyed Document presented as a string on both rows
            assert rows["variation"]["document_id"] == rows["delay_event"]["document_id"] == DOC_IN_ID

            # 6a. one linked audit per target
            assert len(await env.audits("document_relationship.linked", target_type="variation")) == 1
            assert len(await env.audits("document_relationship.linked", target_type="delay_event")) == 1

            # 3. unlinking the Variation leaves the Hindrance
            assert (await env.remove("member_ab", PROJ_A1, variation_link["_id"])).status_code == 200
            assert await env.reverse("member_ab", PROJ_A1) == [("delay_event", hin_a1)]

            # 4. unlinking the Hindrance leaves nothing - and the Variation was
            # already gone, so each removal touched only its own target
            assert (await env.remove("member_ab", PROJ_A1, hindrance_link["_id"])).status_code == 200
            assert await env.reverse("member_ab", PROJ_A1) == []

            # 5. the letter remains, untouched
            letter = await env.db.documents.find_one({"_id": DOC_IN})
            assert letter is not None and letter["lifecycle_state"] == "active"

            # 6b. each unlink audited exactly once, for its own target
            unlinked_v = await env.audits("document_relationship.unlinked", target_type="variation")
            unlinked_h = await env.audits("document_relationship.unlinked", target_type="delay_event")
            assert len(unlinked_v) == len(unlinked_h) == 1
            assert unlinked_v[0]["metadata"]["target_id"] == VAR_A1
            assert unlinked_h[0]["metadata"]["target_id"] == hin_a1

            # Link to Record offers both registers for the letter under A1
            for target_type, target_id in (("variation", VAR_A1), ("delay_event", hin_a1)):
                offered = await env.call(
                    "GET", "member_ab", f"/api/documents/{DOC_IN_ID}/link-targets", PROJ_A1,
                    params={"target_type": target_type},
                )
                assert offered.status_code == 200, offered.text
                assert target_id in {row["target_id"] for row in offered.json()["targets"]}

    _run(scenario())


def test_correspondence_role_semantics_hold_on_the_hindrance_target() -> None:
    async def scenario() -> None:
        async with _env() as (env, hin_a1, _hin_a2):
            refused = await env.link("member_ab", PROJ_A1, "delay_event", hin_a1, DOC_CONTRACT_ID)
            assert refused.status_code == 422, refused.text
            accepted = await env.link("member_ab", PROJ_A1, "delay_event", hin_a1, DOC_CONTRACT_ID, role="supporting_document")
            assert accepted.status_code == 201, accepted.text

    _run(scenario())


# --------------------------------------------------------------------------- #
# 7-8. Foreign project and selected-project mismatch
# --------------------------------------------------------------------------- #


def test_foreign_project_links_are_refused() -> None:
    async def scenario() -> None:
        async with _env() as (env, hin_a1, hin_a2):
            # an A2 letter onto an A1 record, under A1: the Document is out of scope
            for target_type, target_id in (("variation", VAR_A1), ("delay_event", hin_a1)):
                response = await env.link("member_ab", PROJ_A1, target_type, target_id, DOC_A2_ID)
                # The Document is out of the target's scope - a relationship refusal,
                # not a selection one, so it carries no tenant-context code.
                assert response.status_code == 403 and _code(response) != "context_forbidden", response.text
            # an A1 letter onto an A2 record, under A1: the target is outside the selection
            for target_type, target_id in (("variation", VAR_A2), ("delay_event", hin_a2)):
                response = await env.link("member_ab", PROJ_A1, target_type, target_id, DOC_IN_ID)
                assert _forbidden(response), response.text
            assert await env.db.entity_document_links.count_documents({}) == 0

    _run(scenario())


def test_selected_project_mismatch_is_refused_on_both_registers() -> None:
    async def scenario() -> None:
        async with _env() as (env, hin_a1, _hin_a2):
            assert (await env.link("member_ab", PROJ_A1, "variation", VAR_A1, DOC_IN_ID)).status_code == 201
            assert (await env.link("member_ab", PROJ_A1, "delay_event", hin_a1, DOC_IN_ID)).status_code == 201

            # member of A1 + A2, selected A2, A1 records -> refused
            for method, path, body in (
                ("GET", f"/api/variations/{VAR_A1}", None),
                ("PUT", f"/api/variations/{VAR_A1}", {"remarks": "moved"}),
                ("DELETE", f"/api/variations/{VAR_A1}", None),
                ("GET", f"/api/hindrances/{hin_a1}", None),
                ("PATCH", f"/api/hindrances/{hin_a1}", {"title": "moved"}),
                ("GET", f"/api/delay-events/{hin_a1}", None),
                ("PATCH", f"/api/delay-events/{hin_a1}", {"title": "moved"}),
                ("GET", f"/api/entities/variation/{VAR_A1}/document-links", None),
                ("GET", f"/api/entities/delay_event/{hin_a1}/document-links", None),
            ):
                response = await env.call(method, "member_ab", path, PROJ_A2, json=body)
                assert _forbidden(response), f"{method} {path}: {response.status_code} {response.text}"
                assert "proj-a1" not in response.text

            # no project selected -> record-level 400
            for method, path in (
                ("GET", f"/api/variations/{VAR_A1}"),
                ("GET", f"/api/hindrances/{hin_a1}"),
                ("GET", f"/api/delay-events/{hin_a1}"),
            ):
                assert _selection_required(await env.call(method, "member_ab", path, None))

            # a non-member of A1 is refused whatever it selects
            declared = await env.call("GET", "other_project_admin", f"/api/hindrances/{hin_a1}", PROJ_A1)
            assert _forbidden(declared), declared.text
            own = await env.call("GET", "other_project_admin", f"/api/variations/{VAR_A1}", PROJ_A2)
            assert own.status_code == 403, own.text

            # superadmin: an explicit selection still constrains it
            for path in (f"/api/variations/{VAR_A1}", f"/api/hindrances/{hin_a1}"):
                assert _forbidden(await env.call("GET", "superadmin", path, PROJ_A2))
                assert _selection_required(await env.call("GET", "superadmin", path, None))
                assert (await env.call("GET", "superadmin", path, PROJ_A1)).status_code == 200
            broad = await env.call("GET", "superadmin", "/api/variations", None)
            assert broad.status_code == 200 and {VAR_A1, VAR_A2} <= {row["_id"] for row in broad.json()}

            # the reverse lookup never reveals A1 records under A2
            assert await env.reverse("member_ab", PROJ_A2) == []
            assert await env.reverse("member_ab", PROJ_A1) == sorted([("delay_event", hin_a1), ("variation", VAR_A1)])
            # nothing changed
            row = await env.db.variations.find_one({"_id": VAR_A1})
            assert row is not None and row.get("remarks") is None

    _run(scenario())


# --------------------------------------------------------------------------- #
# 9. Project switch moves both registers together
# --------------------------------------------------------------------------- #


def test_project_switch_changes_both_registers_consistently() -> None:
    async def scenario() -> None:
        async with _env() as (env, hin_a1, hin_a2):
            async def registers(project: str) -> tuple[set[str], set[str]]:
                variations = await env.call("GET", "member_ab", "/api/variations", project)
                hindrances = await env.call("GET", "member_ab", "/api/hindrances", project)
                assert variations.status_code == hindrances.status_code == 200
                return (
                    {row["_id"] for row in variations.json()},
                    {row["id"] for row in hindrances.json()["items"]},
                )

            assert await registers(PROJ_A1) == ({VAR_A1, "var-a1-second"}, {hin_a1})
            assert await registers(PROJ_A2) == ({VAR_A2}, {hin_a2})
            assert await registers(PROJ_A1) == ({VAR_A1, "var-a1-second"}, {hin_a1})

            # a list filter may narrow the selection, never leave it
            for path in ("/api/variations", "/api/hindrances"):
                leave = await env.call("GET", "member_ab", path, PROJ_A1, params={"project_id": PROJ_A2})
                assert _forbidden(leave), leave.text

            # no selection: bounded to membership (A1 + A2), never the foreign org
            variations = await env.call("GET", "member_ab", "/api/variations", None)
            assert {row["project_id"] for row in variations.json()} == {PROJ_A1, PROJ_A2}
            hindrances = await env.call("GET", "member_ab", "/api/hindrances", None)
            assert hindrances.status_code == 200, hindrances.text
            assert {row["project_id"] for row in hindrances.json()["items"]} == {PROJ_A1, PROJ_A2}
            assert PROJ_B1 not in variations.text

            # create follows the selection; the body's project is refused, never rewritten
            refused = await env.call(
                "POST", "member_ab", "/api/variations", PROJ_A2,
                json={"project_id": PROJ_A1, "organization_id": ORG_A, "variation_number": "VO-X"},
            )
            assert _forbidden(refused), refused.text
            assert await env.db.variations.count_documents({"variation_number": "VO-X"}) == 0

    _run(scenario())


# --------------------------------------------------------------------------- #
# 13. Compatibility delay-events: permission AND active scope
# --------------------------------------------------------------------------- #


def test_evidence_graph_manage_cannot_mutate_the_compatibility_api() -> None:
    async def scenario() -> None:
        async with _env() as (env, hin_a1, _hin_a2):
            created = await env.call(
                "POST", "evidence_manager", "/api/delay-events", PROJ_A1,
                json={"project_id": PROJ_A1, "title": "Bypass attempt", "start_date": "2026-02-10T00:00:00"},
            )
            patched = await env.call(
                "PATCH", "evidence_manager", f"/api/delay-events/{hin_a1}", PROJ_A1, json={"title": "Bypass"},
            )
            assert created.status_code == 403, created.text
            assert patched.status_code == 403, patched.text
            stored = await env.db.delay_events.find_one({"_id": hin_a1})
            assert stored["title"] == "Access blocked at Station S2"

    _run(scenario())


# --------------------------------------------------------------------------- #
# Archived entries, legacy read-through and missing Documents, on real Mongo
# --------------------------------------------------------------------------- #


def test_an_archived_hindrance_is_read_only_in_both_directions() -> None:
    async def scenario() -> None:
        async with _env() as (env, hin_a1, _hin_a2):
            first = await env.link("member_ab", PROJ_A1, "delay_event", hin_a1, DOC_IN_ID)
            assert first.status_code == 201, first.text
            link_id = first.json()["links"][0]["_id"]
            archived = await env.call(
                "POST", "member_ab", f"/api/hindrances/{hin_a1}/archive", PROJ_A1, json={"reason": "raised in error"}
            )
            assert archived.status_code == 200, archived.text

            added = await env.link("member_ab", PROJ_A1, "delay_event", hin_a1, DOC_OUT_ID)
            removed = await env.remove("member_ab", PROJ_A1, link_id)
            offered = await env.call(
                "GET", "member_ab", f"/api/documents/{DOC_IN_ID}/link-targets", PROJ_A1,
                params={"target_type": "delay_event"},
            )
            assert added.status_code == 409, added.text
            assert removed.status_code == 409, removed.text
            assert hin_a1 not in {row["target_id"] for row in offered.json()["targets"]}
            stored = await env.db.entity_document_links.find_one({"_id": link_id})
            assert stored["removed_at"] is None

            restored = await env.call(
                "POST", "member_ab", f"/api/hindrances/{hin_a1}/restore", PROJ_A1, json={"reason": "restored"}
            )
            assert restored.status_code == 200, restored.text
            assert (await env.remove("member_ab", PROJ_A1, link_id)).status_code == 200

    _run(scenario())


def test_legacy_document_ids_read_through_and_a_missing_document_can_be_unlinked() -> None:
    async def scenario() -> None:
        async with _env() as (env, hin_a1, _hin_a2):
            # A legacy array written before the canonical framework existed.
            await env.db.delay_events.update_one(
                {"_id": hin_a1}, {"$set": {"linked_document_ids": [DOC_OUT_ID, DOC_A2_ID]}}
            )
            forward = await env.call("GET", "member_ab", f"/api/entities/delay_event/{hin_a1}/document-links", PROJ_A1)
            assert forward.status_code == 200, forward.text
            rows = {(row["document_id"], row["source"], row["relationship_role"]) for row in forward.json()["links"]}
            # The foreign-project id never surfaces.
            assert rows == {(DOC_OUT_ID, "legacy_read_through", "supporting_document")}

            # A canonical link whose Document row is gone is still removable (CL-1).
            created = await env.link("member_ab", PROJ_A1, "delay_event", hin_a1, DOC_IN_ID)
            link_id = created.json()["links"][0]["_id"]
            await env.db.documents.delete_one({"_id": DOC_IN})
            removed = await env.remove("member_ab", PROJ_A1, link_id)
            assert removed.status_code == 200, removed.text
            assert (await env.db.entity_document_links.find_one({"_id": link_id}))["removed_at"] is not None

    _run(scenario())
