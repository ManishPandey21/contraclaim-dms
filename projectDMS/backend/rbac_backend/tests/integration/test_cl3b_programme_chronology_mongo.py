"""CL-3B: Programme Milestone and Chronology event as canonical relationship targets.

Over real HTTP, Mongo and RBAC with the CL-2/CL-3A harness discipline: a signed
JWT through the real ``get_current_user``, the real ``PolicyService`` /
``ScopeService`` chain, the real role and permission seeds, ObjectId-keyed
Documents, no dependency override, and the navbar selection sent as
``X-Proj-Id`` exactly like the browser.

Opt-in: set ``RELATIONSHIP_CL3B_MONGODB_URI`` to a disposable replica set
(relationship writes need transactions). Every test creates and drops its own
uniquely named database. CI runs it in the replica-set step and fails if it skips.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Optional

import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    DOC_A2_ID,
    DOC_B_ID,
    DOC_CONTRACT_ID,
    DOC_IN,
    DOC_IN_ID,
    DOC_OUT_ID,
    ORG_A,
    ORG_B,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    VAR_A1,
    _seed,
    _token,
)

MONGODB_URI_ENV = "RELATIONSHIP_CL3B_MONGODB_URI"

pytestmark = pytest.mark.integration

#: ``member_ab`` belongs to A1 and A2 - the principal the selection boundary exists for.
EXTRA_PERSONAS: dict[str, dict[str, Any]] = {
    "member_ab": {"roles": ["projectadmin"], "organization_id": ORG_A, "projects": [PROJ_A1, PROJ_A2]},
}

PM_A1 = "pm-a1"
PM_A1_SUPERSEDED = "pm-a1-superseded"
PM_A2 = "pm-a2"
PM_B1 = "pm-b1"

CHR_A1 = "chr-a1"
CHR_A1_ARCHIVED = "chr-a1-archived"
CHR_A2 = "chr-a2"
CHR_B1 = "chr-b1"
EV_A1 = "ev-a1"
EV_A1_UNSCOPED = "ev-a1-unscoped"
EV_A1_ARCHIVED = "ev-a1-archived"
EV_A2 = "ev-a2"
EV_B1 = "ev-b1"


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _milestone(item_id: str, organization_id: str, project_id: str, ref: str, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "_id": item_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "milestone_ref": ref,
        "title": title,
        "milestone_type": "programme_activity",
        "status": "in_progress",
        "planned_date": datetime(2026, 3, 1),
        "linked_document_ids": [],
        "metadata": {},
        "created_at": datetime(2026, 2, 1),
        **extra,
    }


def _chronology(chronology_id: str, organization_id: str, project_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "_id": chronology_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "title": f"Chronology {chronology_id}",
        "chronology_type": "eot_delay",
        "party_perspective": "claimant",
        "status": "review",
        "selected_source_ids": [],
        "selected_source_types": [],
        "settings": {},
        "summary_counts": {},
        "created_at": datetime(2026, 2, 1),
        **extra,
    }


def _event(event_id: str, chronology_id: str, title: str, *, scope: Optional[tuple[str, str]], **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "_id": event_id,
        "chronology_id": chronology_id,
        "title": title,
        "event_date": datetime(2026, 2, 10),
        "date_type": "exact",
        "verification_status": "verified",
        "related_document_ids": [],
        "related_event_ids": [],
        "source_spans": [],
        "created_at": datetime(2026, 2, 11),
        **extra,
    }
    if scope is not None:
        row["organization_id"], row["project_id"] = scope
    return row


async def _seed_cl3b(db: Any) -> None:
    await db.programme_milestones.insert_many(
        [
            # Legacy G31 supporting document: DOC_OUT.
            _milestone(PM_A1, ORG_A, PROJ_A1, "PM-110", "Pier P4 piling", linked_document_ids=[DOC_OUT_ID]),
            _milestone(PM_A1_SUPERSEDED, ORG_A, PROJ_A1, "PM-100", "Original piling sequence", status="superseded"),
            _milestone(PM_A2, ORG_A, PROJ_A2, "PM-210", "Viaduct span"),
            _milestone(PM_B1, ORG_B, PROJ_B1, "PM-910", "Quay wall"),
        ]
    )
    await db.matter_chronologies.insert_many(
        [
            _chronology(CHR_A1, ORG_A, PROJ_A1),
            _chronology(CHR_A1_ARCHIVED, ORG_A, PROJ_A1, status="archived"),
            _chronology(CHR_A2, ORG_A, PROJ_A2),
            _chronology(CHR_B1, ORG_B, PROJ_B1),
        ]
    )
    await db.matter_chronology_events.insert_many(
        [
            # Extracted from DOC_OUT (source) and related to DOC_OUT + DOC_CONTRACT,
            # exactly the shape `ChronologyService._extract_document_event` writes.
            _event(
                EV_A1, CHR_A1, "Engineer instruction on added works", scope=(ORG_A, PROJ_A1),
                source_document_id=DOC_OUT_ID, related_document_ids=[DOC_OUT_ID, DOC_CONTRACT_ID],
                letter_no="CON/VO/022",
            ),
            # Predates the parent-scope anchor: no scope of its own.
            _event(EV_A1_UNSCOPED, CHR_A1, "Site possession delayed", scope=None),
            _event(EV_A1_ARCHIVED, CHR_A1_ARCHIVED, "Archived chronology event", scope=(ORG_A, PROJ_A1)),
            _event(EV_A2, CHR_A2, "Depot handover", scope=(ORG_A, PROJ_A2)),
            _event(EV_B1, CHR_B1, "Quay wall notice", scope=(ORG_B, PROJ_B1)),
        ]
    )


def _app() -> FastAPI:
    from rbac_backend.routers.chronology import router as chronology_router
    from rbac_backend.routers.document_relationships import router as relationship_router
    from rbac_backend.routers.documents import router as documents_router
    from rbac_backend.routers.evidence_registers import router as registers_router
    from rbac_backend.routers.hindrances import router as hindrances_router
    from rbac_backend.routers.variations import router as variations_router

    app = FastAPI()
    for router in (
        relationship_router, documents_router, variations_router, hindrances_router, registers_router, chronology_router,
    ):
        app.include_router(router, prefix="/api")
    return app


class Env:
    def __init__(self, db: Any, client: httpx.AsyncClient) -> None:
        self.db = db
        self._client = client

    async def call(self, method: str, persona: str, path: str, project: Optional[str], **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {_token(persona)}"}
        if project:
            headers["X-Proj-Id"] = project
        return await self._client.request(method, path, headers=headers, **kwargs)

    async def link(self, persona: str, project: Optional[str], target_type: str, target_id: str, document_id: str,
                   role: str = "correspondence") -> httpx.Response:
        return await self.call(
            "POST", persona, f"/api/entities/{target_type}/{target_id}/document-links:batch", project,
            json={"links": [{"document_id": document_id, "relationship_role": role}]},
        )

    async def forward(self, persona: str, project: Optional[str], target_type: str, target_id: str) -> httpx.Response:
        return await self.call("GET", persona, f"/api/entities/{target_type}/{target_id}/document-links", project)

    async def reverse(self, persona: str, project: Optional[str], document_id: str = DOC_IN_ID) -> httpx.Response:
        return await self.call("GET", persona, f"/api/documents/{document_id}/entity-links", project)

    async def reverse_keys(self, persona: str, project: Optional[str], document_id: str = DOC_IN_ID) -> list[tuple[str, str, str]]:
        response = await self.reverse(persona, project, document_id)
        assert response.status_code == 200, response.text
        return sorted(
            (row["target_type"], row["target_id"], row["relationship_role"]) for row in response.json()["links"]
        )

    async def remove(self, persona: str, project: Optional[str], link_id: str, revision: int = 1) -> httpx.Response:
        return await self.call(
            "POST", persona, f"/api/document-links/{link_id}:remove", project,
            json={"reason": "CL-3B verification", "expected_revision": revision},
        )

    async def targets(self, persona: str, project: Optional[str], target_type: str, document_id: str = DOC_IN_ID,
                      q: Optional[str] = None) -> httpx.Response:
        params = {"target_type": target_type, **({"q": q} if q else {})}
        return await self.call("GET", persona, f"/api/documents/{document_id}/link-targets", project, params=params)

    async def target_types(self, persona: str, project: Optional[str], document_id: str = DOC_IN_ID) -> httpx.Response:
        return await self.call("GET", persona, f"/api/documents/{document_id}/link-target-types", project)

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

    async def active_links(self, **query: Any) -> list[dict[str, Any]]:
        return await self.db.entity_document_links.find({"removed_at": None, **query}).to_list(length=None)


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
    name = f"relationship_cl3b_{uuid.uuid4().hex[:12]}"
    db = mongo[name]
    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    try:
        await _seed(db)
        for persona, spec in EXTRA_PERSONAS.items():
            await db.users.insert_one(
                {"_id": f"user-{persona}", "email": f"{persona}@example.com", "username": persona,
                 "disabled": False, "account_type": "client_user", "organizations": [], **spec}
            )
        await _seed_cl3b(db)
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(db, client)
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _code(response: httpx.Response) -> Optional[str]:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def _forbidden(response: httpx.Response) -> bool:
    return response.status_code == 403 and _code(response) == "context_forbidden"


def _selection_required(response: httpx.Response) -> bool:
    return response.status_code == 400 and _code(response) == "selection_required"


def _links(response: httpx.Response) -> list[dict[str, Any]]:
    assert response.status_code in (200, 201), response.text
    return response.json()["links"]


# --------------------------------------------------------------------------- #
# Programme Milestone: link, forward, reverse, idempotent relink, unlink
# --------------------------------------------------------------------------- #


def test_programme_milestone_link_lifecycle() -> None:
    async def scenario() -> None:
        async with _env() as env:
            created = _links(await env.link("project_admin", PROJ_A1, "programme_milestone", PM_A1, DOC_IN_ID))
            assert len(created) == 1
            link = created[0]
            assert link["relationship_role"] == "correspondence"
            assert link["document_id"] == DOC_IN_ID  # ObjectId-keyed Document, presented as a string
            assert link["target_label"] == "PM-110 · Pier P4 piling"
            assert link["target_route"] == f"/programme-milestones/{PM_A1}"

            # Idempotent relink: same row, one audit.
            again = _links(await env.link("project_admin", PROJ_A1, "programme_milestone", PM_A1, DOC_IN_ID))
            assert again[0]["_id"] == link["_id"]
            assert len(await env.audits("document_relationship.linked", target_type="programme_milestone")) == 1

            # Forward: the canonical link plus the G31 legacy supporting document.
            forward = _links(await env.forward("project_admin", PROJ_A1, "programme_milestone", PM_A1))
            rows = {(row["document_id"], row["relationship_role"], row.get("source")) for row in forward}
            assert (DOC_IN_ID, "correspondence", "user") in rows
            assert (DOC_OUT_ID, "supporting_document", "legacy_read_through") in rows

            # Reverse: both Documents see the milestone, with its deep link.
            assert await env.reverse_keys("project_admin", PROJ_A1) == [("programme_milestone", PM_A1, "correspondence")]
            # (DOC_OUT is also the seeded source of a chronology event.)
            assert await env.reverse_keys("project_admin", PROJ_A1, DOC_OUT_ID) == [
                ("chronology_event", EV_A1, "source_document"),
                ("programme_milestone", PM_A1, "supporting_document"),
            ]

            # Role semantics: a contract Document is not correspondence; a role the
            # register has no concept of does not exist.
            assert (await env.link("project_admin", PROJ_A1, "programme_milestone", PM_A1, DOC_CONTRACT_ID)).status_code == 422
            assert (await env.link("project_admin", PROJ_A1, "programme_milestone", PM_A1, DOC_CONTRACT_ID,
                                   role="approval")).status_code == 422
            assert (await env.link("project_admin", PROJ_A1, "programme_milestone", PM_A1, DOC_CONTRACT_ID,
                                   role="progress_evidence")).status_code == 201

            # Unlink: exactly once, the Document stays.
            assert (await env.remove("project_admin", PROJ_A1, link["_id"])).status_code == 200
            assert (await env.remove("project_admin", PROJ_A1, link["_id"], revision=2)).status_code == 409
            unlinked = await env.audits("document_relationship.unlinked", target_type="programme_milestone")
            assert len(unlinked) == 1 and unlinked[0]["metadata"]["document_id"] == DOC_IN_ID
            assert await env.reverse_keys("project_admin", PROJ_A1) == []
            assert await env.db.documents.find_one({"_id": DOC_IN}) is not None

            # A superseded milestone is a status, not an archive: it still takes evidence.
            assert (await env.link("project_admin", PROJ_A1, "programme_milestone", PM_A1_SUPERSEDED, DOC_IN_ID)).status_code == 201
            # The register has no delete, so the shared delete path refuses rather than removing it.
            assert await env.db.programme_milestones.count_documents({"_id": PM_A1}) == 1

    _run(scenario())


def test_programme_register_routes_follow_the_selection() -> None:
    async def scenario() -> None:
        async with _env() as env:
            # member of A1 and A2
            assert (await env.call("GET", "member_ab", f"/api/programme-milestones/{PM_A1}", PROJ_A1)).status_code == 200
            assert _forbidden(await env.call("GET", "member_ab", f"/api/programme-milestones/{PM_A1}", PROJ_A2))
            assert _selection_required(await env.call("GET", "member_ab", f"/api/programme-milestones/{PM_A1}", None))
            assert _forbidden(await env.call(
                "PATCH", "member_ab", f"/api/programme-milestones/{PM_A1}", PROJ_A2, json={"title": "moved"}
            ))
            created = await env.call(
                "POST", "member_ab", "/api/programme-milestones", PROJ_A2,
                json={"project_id": PROJ_A1, "milestone_ref": "PM-X", "title": "wrong project",
                      "planned_date": "2026-05-01T00:00:00"},
            )
            assert _forbidden(created)
            # Lists: the selection bounds them; nothing selected stays bounded by membership.
            listed = await env.call("GET", "member_ab", "/api/programme-milestones", PROJ_A2)
            assert {row["_id"] for row in listed.json()} == {PM_A2}
            unselected = await env.call("GET", "project_admin", "/api/programme-milestones", None)
            assert unselected.status_code == 200, unselected.text
            assert {row["_id"] for row in unselected.json()} == {PM_A1, PM_A1_SUPERSEDED}
            # Compatibility: the G31-certified raw array write is unchanged (debt, not reopened).
            patched = await env.call(
                "PATCH", "project_admin", f"/api/programme-milestones/{PM_A1}", PROJ_A1,
                json={"linked_document_ids": [DOC_OUT_ID, DOC_CONTRACT_ID]},
            )
            assert patched.status_code == 200, patched.text
            assert set(patched.json()["linked_document_ids"]) == {DOC_OUT_ID, DOC_CONTRACT_ID}

    _run(scenario())


# --------------------------------------------------------------------------- #
# Chronology event: link, forward, reverse, source semantics, legacy read-through
# --------------------------------------------------------------------------- #


def test_chronology_event_link_lifecycle_and_source_semantics() -> None:
    async def scenario() -> None:
        async with _env() as env:
            created = _links(await env.link("project_admin", PROJ_A1, "chronology_event", EV_A1, DOC_IN_ID))
            link = created[0]
            assert link["target_label"] == "2026-02-10 · Engineer instruction on added works"
            assert link["target_route"] == f"/chronology/{CHR_A1}?event_id={EV_A1}"
            again = _links(await env.link("project_admin", PROJ_A1, "chronology_event", EV_A1, DOC_IN_ID))
            assert again[0]["_id"] == link["_id"]
            assert len(await env.audits("document_relationship.linked", target_type="chronology_event")) == 1

            # Forward: canonical correspondence + the source + the related contract Document,
            # the source presented once (it is also in related_document_ids).
            forward = _links(await env.forward("project_admin", PROJ_A1, "chronology_event", EV_A1))
            rows = sorted((row["document_id"], row["relationship_role"]) for row in forward)
            assert rows == sorted(
                [(DOC_IN_ID, "correspondence"), (DOC_OUT_ID, "source_document"), (DOC_CONTRACT_ID, "supporting_document")]
            )

            # The source is provenance, not a linkable role: it cannot be written...
            refused = await env.link("project_admin", PROJ_A1, "chronology_event", EV_A1, DOC_OUT_ID, role="source_document")
            assert refused.status_code == 422, refused.text
            # ...and a canonical link of the same Document does not flatten it away.
            _links(await env.link("project_admin", PROJ_A1, "chronology_event", EV_A1, DOC_OUT_ID, role="correspondence"))
            on_letter = [key for key in await env.reverse_keys("project_admin", PROJ_A1, DOC_OUT_ID)
                         if key[0] == "chronology_event"]
            assert on_letter == sorted(
                [("chronology_event", EV_A1, "correspondence"), ("chronology_event", EV_A1, "source_document")]
            )
            # A related (non-source) legacy id is superseded by a canonical link of the same Document.
            _links(await env.link("project_admin", PROJ_A1, "chronology_event", EV_A1, DOC_CONTRACT_ID,
                                  role="supporting_document"))
            contract_rows = [row for row in _links(await env.forward("project_admin", PROJ_A1, "chronology_event", EV_A1))
                             if row["document_id"] == DOC_CONTRACT_ID]
            assert [row["source"] for row in contract_rows] == ["user"]

            # An event predating the parent-scope anchor loads through its chronology.
            unscoped = _links(await env.link("project_admin", PROJ_A1, "chronology_event", EV_A1_UNSCOPED, DOC_IN_ID))
            assert unscoped[0]["project_id"] == PROJ_A1

            # Unlink one; the Document and the other event's link remain.
            assert (await env.remove("project_admin", PROJ_A1, link["_id"])).status_code == 200
            assert await env.reverse_keys("project_admin", PROJ_A1) == [("chronology_event", EV_A1_UNSCOPED, "correspondence")]
            assert len(await env.audits("document_relationship.unlinked", target_type="chronology_event")) == 1

    _run(scenario())


def test_chronology_raw_document_writes_are_closed() -> None:
    async def scenario() -> None:
        async with _env() as env:
            base = f"/api/chronologies/{CHR_A1}/events"
            # PATCH: a changed related_document_ids is a relationship write -> 409, nothing stored.
            changed = await env.call("PATCH", "project_admin", f"{base}/{EV_A1}", PROJ_A1,
                                     json={"related_document_ids": [DOC_B_ID], "title": "forged"})
            assert changed.status_code == 409, changed.text
            stored = await env.db.matter_chronology_events.find_one({"_id": EV_A1})
            assert stored["related_document_ids"] == [DOC_OUT_ID, DOC_CONTRACT_ID]
            assert stored["title"] == "Engineer instruction on added works"
            cleared = await env.call("PATCH", "project_admin", f"{base}/{EV_A1}", PROJ_A1, json={"related_document_ids": []})
            assert cleared.status_code == 409, cleared.text
            # An unchanged echo passes.
            echo = await env.call("PATCH", "project_admin", f"{base}/{EV_A1}", PROJ_A1,
                                  json={"related_document_ids": [DOC_CONTRACT_ID, DOC_OUT_ID], "manual_notes": "ok"})
            assert echo.status_code == 200, echo.text
            # /link: documents refused, events still linkable.
            assert (await env.call("POST", "project_admin", f"{base}/{EV_A1}/link", PROJ_A1,
                                   json={"related_document_ids": [DOC_IN_ID]})).status_code == 409
            assert (await env.call("POST", "project_admin", f"{base}/{EV_A1}/link", PROJ_A1,
                                   json={"related_event_ids": [EV_A1_UNSCOPED]})).status_code == 200
            # create: raw related ids refused; a foreign or other-project source refused.
            assert (await env.call("POST", "project_admin", base, PROJ_A1,
                                   json={"chronology_id": CHR_A1, "title": "x", "related_document_ids": [DOC_IN_ID]})).status_code == 409
            for foreign in (DOC_B_ID, DOC_A2_ID):
                response = await env.call("POST", "project_admin", base, PROJ_A1,
                                          json={"chronology_id": CHR_A1, "title": "x", "source_document_id": foreign})
                assert response.status_code == 403, response.text
            patched_source = await env.call("PATCH", "project_admin", f"{base}/{EV_A1_UNSCOPED}", PROJ_A1,
                                            json={"source_document_id": DOC_B_ID})
            assert patched_source.status_code == 403, patched_source.text
            own = await env.call("POST", "project_admin", base, PROJ_A1,
                                 json={"chronology_id": CHR_A1, "title": "Own source", "source_document_id": DOC_IN_ID})
            assert own.status_code == 201, own.text

    _run(scenario())


def test_chronology_register_routes_follow_the_selection() -> None:
    async def scenario() -> None:
        async with _env() as env:
            path = f"/api/chronologies/{CHR_A1}"
            assert (await env.call("GET", "member_ab", path, PROJ_A1)).status_code == 200
            assert _forbidden(await env.call("GET", "member_ab", path, PROJ_A2))
            assert _selection_required(await env.call("GET", "member_ab", path, None))
            assert _forbidden(await env.call("GET", "member_ab", f"{path}/events", PROJ_A2))
            assert _forbidden(await env.call("DELETE", "member_ab", path, PROJ_A2))
            assert _forbidden(await env.call(
                "POST", "member_ab", "/api/chronologies", PROJ_A2, json={"project_id": PROJ_A1, "title": "wrong"}
            ))
            listed = await env.call("GET", "member_ab", "/api/chronologies", PROJ_A2)
            assert {row["_id"] for row in listed.json()} == {CHR_A2}
            unselected = await env.call("GET", "project_admin", "/api/chronologies", None)
            assert unselected.status_code == 200, unselected.text
            assert {row["_id"] for row in unselected.json()} == {CHR_A1, CHR_A1_ARCHIVED}
            # Exports are plain browser downloads and stay selection-blind (membership still decides).
            assert (await env.call("GET", "project_admin", f"{path}/export/xlsx", None)).status_code == 200
            assert (await env.call("GET", "other_project_admin", f"{path}/export/xlsx", None)).status_code == 403

    _run(scenario())


# --------------------------------------------------------------------------- #
# Delete / archive
# --------------------------------------------------------------------------- #


def test_chronology_delete_retires_its_event_links_and_archive_is_read_only() -> None:
    async def scenario() -> None:
        async with _env() as env:
            _links(await env.link("org_admin", PROJ_A1, "chronology_event", EV_A1, DOC_IN_ID))
            _links(await env.link("org_admin", PROJ_A1, "chronology_event", EV_A1_UNSCOPED, DOC_OUT_ID, role="supporting_document"))
            _links(await env.link("org_admin", PROJ_A1, "programme_milestone", PM_A1, DOC_IN_ID))

            # Archived chronology: read-only both ways, never offered.
            refused = await env.link("org_admin", PROJ_A1, "chronology_event", EV_A1_ARCHIVED, DOC_IN_ID)
            assert refused.status_code == 409, refused.text
            offered = await env.targets("org_admin", PROJ_A1, "chronology_event")
            assert EV_A1_ARCHIVED not in {row["target_id"] for row in offered.json()["targets"]}

            deleted = await env.call("DELETE", "org_admin", f"/api/chronologies/{CHR_A1}", PROJ_A1)
            assert deleted.status_code == 204, deleted.text
            assert await env.active_links(target_type="chronology_event") == []
            retired = await env.audits("document_relationship.unlinked", target_type="chronology_event")
            assert len(retired) == 2 and {row["reason"] for row in retired} == {"Chronology deleted"}
            # Documents, events and the unrelated Programme link survive.
            assert await env.db.documents.count_documents({}) == 5
            assert await env.db.matter_chronology_events.count_documents({"chronology_id": CHR_A1}) == 2
            assert len(await env.active_links(target_type="programme_milestone")) == 1
            assert await env.reverse_keys("org_admin", PROJ_A1) == [("programme_milestone", PM_A1, "correspondence")]
            # The deleted chronology's events are no longer targets at all.
            assert (await env.forward("org_admin", PROJ_A1, "chronology_event", EV_A1)).status_code == 404
            assert (await env.link("org_admin", PROJ_A1, "chronology_event", EV_A1, DOC_IN_ID)).status_code == 404

    _run(scenario())


# --------------------------------------------------------------------------- #
# Many-to-many
# --------------------------------------------------------------------------- #


def test_one_correspondence_across_four_registers() -> None:
    async def scenario() -> None:
        async with _env() as env:
            hindrance = await env.create_hindrance(PROJ_A1, "Access blocked at Station S2")
            targets = [
                ("variation", VAR_A1), ("delay_event", hindrance), ("programme_milestone", PM_A1), ("chronology_event", EV_A1),
            ]
            ids: dict[str, str] = {}
            for target_type, target_id in targets:
                ids[target_type] = _links(await env.link("member_ab", PROJ_A1, target_type, target_id, DOC_IN_ID))[0]["_id"]
            assert await env.reverse_keys("member_ab", PROJ_A1) == sorted(
                (target_type, target_id, "correspondence") for target_type, target_id in targets
            )

            assert (await env.remove("member_ab", PROJ_A1, ids["programme_milestone"])).status_code == 200
            assert await env.reverse_keys("member_ab", PROJ_A1) == sorted(
                (target_type, target_id, "correspondence") for target_type, target_id in targets
                if target_type != "programme_milestone"
            )
            assert await env.db.documents.find_one({"_id": DOC_IN}) is not None
            unlinked = await env.audits("document_relationship.unlinked")
            assert [row["metadata"]["target_type"] for row in unlinked] == ["programme_milestone"]

            # Another project's selection hides all four selection-bound rows.
            assert (await env.reverse("member_ab", PROJ_A2)).json()["links"] == []

    _run(scenario())


# --------------------------------------------------------------------------- #
# Selected project on the relationship routes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("target_type", "own", "other"), [
    ("programme_milestone", PM_A1, PM_A2),
    ("chronology_event", EV_A1, EV_A2),
])
def test_relationship_routes_hold_the_selection(target_type: str, own: str, other: str) -> None:
    async def scenario() -> None:
        async with _env() as env:
            # member A+B, selected A, record A -> allowed
            link = _links(await env.link("member_ab", PROJ_A1, target_type, own, DOC_IN_ID))[0]
            assert (await env.forward("member_ab", PROJ_A1, target_type, own)).status_code == 200
            # member A+B, selected B, record A -> refused
            assert _forbidden(await env.link("member_ab", PROJ_A2, target_type, own, DOC_IN_ID))
            assert _forbidden(await env.forward("member_ab", PROJ_A2, target_type, own))
            assert _forbidden(await env.remove("member_ab", PROJ_A2, link["_id"]))
            # non-member of A -> refused, with or without claiming A
            assert (await env.forward("other_project_admin", PROJ_A2, target_type, own)).status_code == 403
            assert (await env.forward("other_project_admin", PROJ_A1, target_type, own)).status_code == 403
            # nothing selected -> record/write 400
            assert _selection_required(await env.forward("member_ab", None, target_type, own))
            assert _selection_required(await env.link("member_ab", None, target_type, own, DOC_IN_ID))
            # superadmin is held to the selection too
            assert _forbidden(await env.forward("superadmin", PROJ_A2, target_type, own))
            assert (await env.forward("superadmin", PROJ_A1, target_type, own)).status_code == 200
            # reverse: none selected -> bounded rows; mismatching -> hidden
            assert (target_type, own, "correspondence") in await env.reverse_keys("member_ab", None)
            assert (target_type, own, "correspondence") not in await env.reverse_keys("member_ab", PROJ_A2)
            # Link to Record needs the selection for these types
            assert _selection_required(await env.targets("member_ab", None, target_type))
            offered = await env.targets("member_ab", PROJ_A1, target_type)
            assert own in {row["target_id"] for row in offered.json()["targets"]}
            assert other not in {row["target_id"] for row in offered.json()["targets"]}
            assert (await env.targets("member_ab", PROJ_A2, target_type)).json()["targets"] == []
            # a Document of A2 offers A2's record under A2 only
            other_offered = await env.targets("member_ab", PROJ_A2, target_type, DOC_A2_ID)
            assert {row["target_id"] for row in other_offered.json()["targets"]} == {other}
            # unlink under the right selection works
            assert (await env.remove("member_ab", PROJ_A1, link["_id"])).status_code == 200

    _run(scenario())


# --------------------------------------------------------------------------- #
# RBAC matrix (real seeds, no new grants)
# --------------------------------------------------------------------------- #

#: persona -> (view links, link, unlink, reverse visible, offered by Link to Record).
#: Measured on the real seeds; CL-3B grants nothing new. ``None`` = reverse 403
#: (the persona cannot view the Document at all).
RBAC_EXPECTED: dict[str, dict[str, tuple[int, int, int, Optional[bool], bool]]] = {
    "programme_milestone": {
        "project_user": (403, 403, 403, False, False),
        "project_admin": (200, 201, 200, True, True),
        "org_user": (403, 403, 403, False, False),
        "org_admin": (200, 201, 200, True, True),
        "system_user": (403, 403, 403, None, False),
        "superuser": (403, 403, 403, None, False),
        "superadmin": (200, 201, 200, True, True),
    },
    "chronology_event": {
        "project_user": (403, 403, 403, False, False),
        "project_admin": (200, 201, 200, True, True),
        "org_user": (403, 403, 403, False, False),
        "org_admin": (200, 201, 200, True, True),
        "system_user": (403, 403, 403, None, False),
        "superuser": (403, 403, 403, None, False),
        "superadmin": (200, 201, 200, True, True),
    },
}


@pytest.mark.parametrize(
    ("target_type", "persona"),
    [(target_type, persona) for target_type, rows in RBAC_EXPECTED.items() for persona in sorted(rows)],
)
def test_rbac_matrix(target_type: str, persona: str) -> None:
    view, create, remove, reverse_visible, offered = RBAC_EXPECTED[target_type][persona]
    target_id = PM_A1 if target_type == "programme_milestone" else EV_A1

    async def scenario() -> None:
        async with _env() as env:
            seeded = _links(await env.link("superadmin", PROJ_A1, target_type, target_id, DOC_IN_ID))[0]
            reverse = await env.reverse(persona, PROJ_A1)
            if reverse_visible is None:
                assert reverse.status_code == 403, reverse.text
            else:
                assert reverse.status_code == 200, reverse.text
                visible = any(row["target_type"] == target_type for row in reverse.json()["links"])
                assert visible is reverse_visible
            listing = await env.targets(persona, PROJ_A1, target_type)
            if listing.status_code == 200:
                assert (target_id in {row["target_id"] for row in listing.json()["targets"]}) is offered
            else:
                assert not offered and listing.status_code == 403, listing.text
            types = await env.target_types(persona, PROJ_A1)
            if types.status_code == 200:
                assert (target_type in types.json()["target_types"]) is offered
            else:
                assert not offered and types.status_code == 403, types.text
            assert (await env.forward(persona, PROJ_A1, target_type, target_id)).status_code == view
            assert (await env.link(persona, PROJ_A1, target_type, target_id, DOC_OUT_ID, role="supporting_document")).status_code == create
            assert (await env.remove(persona, PROJ_A1, seeded["_id"])).status_code == remove

    _run(scenario())


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


def test_isolation_fails_closed_on_every_axis() -> None:
    async def scenario() -> None:
        async with _env() as env:
            for target_type, foreign_org, foreign_project in (
                ("programme_milestone", PM_B1, PM_A2), ("chronology_event", EV_B1, EV_A2),
            ):
                # foreign org / foreign project targets, under the caller's own selection
                assert (await env.forward("project_admin", PROJ_A1, target_type, foreign_org)).status_code == 403
                assert (await env.forward("project_admin", PROJ_A1, target_type, foreign_project)).status_code == 403
                assert (await env.link("project_admin", PROJ_A1, target_type, foreign_org, DOC_IN_ID)).status_code == 403
                assert (await env.link("project_admin", PROJ_A1, target_type, foreign_project, DOC_IN_ID)).status_code == 403
                # foreign Documents onto an own target
                own = PM_A1 if target_type == "programme_milestone" else EV_A1
                assert (await env.link("project_admin", PROJ_A1, target_type, own, DOC_B_ID)).status_code == 403
                assert (await env.link("project_admin", PROJ_A1, target_type, own, DOC_A2_ID)).status_code == 403
                # a foreign link id cannot be removed or read
                foreign = _links(await env.link("foreign_org_admin", PROJ_B1, target_type, foreign_org, DOC_B_ID))[0]
                assert (await env.remove("project_admin", PROJ_A1, foreign["_id"])).status_code == 403
                assert (await env.call("GET", "project_admin", f"/api/document-links/{foreign['_id']}", PROJ_A1)).status_code == 403
                other = _links(await env.link("other_project_admin", PROJ_A2, target_type, foreign_project, DOC_A2_ID))[0]
                assert (await env.remove("project_admin", PROJ_A1, other["_id"])).status_code == 403
                assert (await env.active_links(_id=other["_id"])) != []
            # reverse and Link-to-Record leakage
            assert (await env.reverse("foreign_org_admin", PROJ_B1, DOC_IN_ID)).status_code == 403
            assert (await env.reverse("project_admin", PROJ_A1, DOC_A2_ID)).status_code == 403
            for target_type in ("programme_milestone", "chronology_event"):
                assert (await env.targets("foreign_org_admin", PROJ_B1, target_type)).status_code == 403
                assert (await env.targets("other_project_admin", PROJ_A2, target_type)).status_code == 403
            assert (await env.target_types("foreign_org_admin", PROJ_B1)).status_code == 403
            # A chronology event of another tenant that names our Document is never shown on it.
            await env.db.matter_chronology_events.update_one({"_id": EV_B1}, {"$set": {"related_document_ids": [DOC_IN_ID]}})
            assert all(row[1] != EV_B1 for row in await env.reverse_keys("org_admin", PROJ_A1))

    _run(scenario())


# --------------------------------------------------------------------------- #
# Link to Record: >200 records, permission-aware types
# --------------------------------------------------------------------------- #


def test_link_to_record_finds_a_match_behind_more_than_200_records() -> None:
    async def scenario() -> None:
        async with _env() as env:
            # 250 non-matching rows sort before the one match for every type.
            await env.db.bank_guarantees.insert_many(
                [{"_id": f"bg-{index:03d}", "bg_number": f"BG-{index:03d}", "bg_type": "performance",
                  "organization_id": ORG_A, "project_id": PROJ_A1} for index in range(250)]
                + [{"_id": "bg-zzz", "bg_number": "BG-TARGET-77", "bg_type": "performance",
                    "organization_id": ORG_A, "project_id": PROJ_A1}]
            )
            await env.db.bank_guarantee_events.insert_many(
                [{"_id": f"bge-{index:03d}", "bank_guarantee_id": f"bg-{index:03d}", "event_type": "original",
                  "sequence": 1, "organization_id": ORG_A, "project_id": PROJ_A1} for index in range(250)]
                + [{"_id": "bge-zzz", "bank_guarantee_id": "bg-zzz", "event_type": "original", "sequence": 1,
                    "organization_id": ORG_A, "project_id": PROJ_A1}]
            )
            await env.db.key_date_milestones.insert_many(
                [{"_id": f"kd-{index:03d}", "milestone_ref": f"KD-{index:03d}", "title": "Section",
                  "organization_id": ORG_A, "project_id": PROJ_A1} for index in range(250)]
                + [{"_id": "kd-zzz", "milestone_ref": "KD-TARGET-77", "title": "Section 9",
                    "organization_id": ORG_A, "project_id": PROJ_A1}]
            )
            await env.db.key_date_achievements.insert_many(
                [{"_id": f"kd-{index:03d}:ach", "milestone_id": f"kd-{index:03d}", "organization_id": ORG_A,
                  "project_id": PROJ_A1} for index in range(250)]
                + [{"_id": "kd-zzz:ach", "milestone_id": "kd-zzz", "organization_id": ORG_A, "project_id": PROJ_A1}]
            )
            await env.db.programme_milestones.insert_many(
                [_milestone(f"pm-{index:03d}", ORG_A, PROJ_A1, f"PM-{index:03d}", "Activity") for index in range(250)]
                + [_milestone("pm-zzz", ORG_A, PROJ_A1, "PM-TARGET-77", "Deck pour")]
            )
            await env.db.matter_chronology_events.insert_many(
                [_event(f"ev-{index:03d}", CHR_A1, f"Routine event {index}", scope=(ORG_A, PROJ_A1)) for index in range(250)]
                + [_event("ev-zzz", CHR_A1, "Notice TARGET-77 served", scope=(ORG_A, PROJ_A1))]
            )
            for target_type, expected in (
                ("bank_guarantee_event", "bge-zzz"),
                ("key_date_achievement", "kd-zzz:ach"),
                ("programme_milestone", "pm-zzz"),
                ("chronology_event", "ev-zzz"),
            ):
                found = await env.targets("org_admin", PROJ_A1, target_type, q="target-77")
                assert found.status_code == 200, found.text
                assert [row["target_id"] for row in found.json()["targets"]] == [expected], target_type
            # Without a search the scan stays bounded (and answers).
            assert len((await env.targets("org_admin", PROJ_A1, "bank_guarantee_event")).json()["targets"]) == 25

    _run(scenario())


def test_link_target_types_follow_permissions_and_selection() -> None:
    async def scenario() -> None:
        async with _env() as env:
            admin = await env.target_types("project_admin", PROJ_A1)
            assert admin.status_code == 200, admin.text
            types = set(admin.json()["target_types"])
            assert {"programme_milestone", "chronology_event", "variation", "delay_event", "claim"} <= types
            # No selection: the selection-bound types are left out, the others stay.
            unselected = set((await env.target_types("project_admin", None)).json()["target_types"])
            assert not unselected & {"programme_milestone", "chronology_event", "variation", "delay_event"}
            assert "claim" in unselected
            # Another project's selection leaves them out too, and an unusable selection does not fail the call.
            assert not set((await env.target_types("member_ab", PROJ_A2)).json()["target_types"]) & {
                "programme_milestone", "chronology_event"}
            assert (await env.target_types("project_admin", PROJ_B1)).status_code == 200
            # A viewer who may manage nothing gets nothing - and no record was needed to say so.
            viewer = await env.target_types("project_user", PROJ_A1)
            assert viewer.status_code == 200 and viewer.json()["target_types"] == []

    _run(scenario())
