"""CL-1: canonical Document relationships over real HTTP, real Mongo, real RBAC.

Every earlier relationship suite either seeds string-keyed Documents or stands a
recording policy in for ``PolicyService``. Production Documents are ObjectId-keyed
(``document_service`` pops any supplied ``_id`` before ``insert_one``), and the
authorization decision is the thing under test - so neither shortcut is taken here:

* the principal comes from the real ``get_current_user`` reading a signed JWT;
* every decision runs through the real ``PolicyService`` -> ``PermissionService``
  -> ``EntitlementService`` -> ``ScopeService`` chain;
* roles and permissions are the real seeds (``initialize_permissions`` /
  ``initialize_roles``), never a hand-written permission map;
* no dependency override is installed - the app gets its database the way
  production does, through the module globals in ``core.database``.

Opt-in: set ``RELATIONSHIP_CL1_MONGODB_URI`` to a disposable replica set. The
suite never falls back to the application's configured database, and every test
creates and drops its own uniquely named database.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

import httpx
import jwt
import pytest
from bson import ObjectId
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URI_ENV = "RELATIONSHIP_CL1_MONGODB_URI"

pytestmark = pytest.mark.integration

ORG_A = "org-a"
ORG_B = "org-b"
PROJ_A1 = "proj-a1"
PROJ_A2 = "proj-a2"
PROJ_B1 = "proj-b1"

#: ObjectId-keyed, like every Document the upload path creates.
DOC_OID = ObjectId()
DOC_OID_ID = str(DOC_OID)
DOC_STR_ID = "doc-string-outgoing"
DOC_CONTRACT_OID = ObjectId()
DOC_CONTRACT_ID = str(DOC_CONTRACT_OID)
DOC_UNTYPED_ID = "doc-untyped"
DOC_A2_OID = ObjectId()
DOC_A2_ID = str(DOC_A2_OID)
DOC_B_OID = ObjectId()
DOC_B_ID = str(DOC_B_OID)
DOC_DOOMED_OID = ObjectId()
DOC_DOOMED_ID = str(DOC_DOOMED_OID)

CLAIM_A1 = "claim-a1"
CLAIM_A2 = "claim-a2"
CLAIM_B1 = "claim-b1"
CLAIM_FROZEN = "claim-a1-frozen"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _document(
    _id: Any,
    *,
    organization_id: str,
    project_id: str,
    upload_type: Any,
    subject: str,
) -> dict[str, Any]:
    document_id = str(_id)
    row: dict[str, Any] = {
        "_id": _id,
        "organization_id": organization_id,
        "project_id": project_id,
        "filename": f"{subject}.pdf",
        "filetype": "application/pdf",
        "filesize": 1024,
        "date": datetime(2026, 9, 1),
        "subject": subject,
        "letterNo": f"LTR/{document_id[-6:]}",
        "status": "active",
        "tags": [],
        "subTags": [],
        "processing_status": "metadata_extracted",
        "lifecycle_state": "active",
        "current_version_id": f"{document_id}-v1",
        # Nested BSON the presenter must also make JSON-safe.
        "uploaded_by_ref": ObjectId(),
        "created_at": datetime(2026, 9, 1, 12, 0),
        # Required by the Document model: a row without it is silently dropped
        # from listings while still counted in `total`.
        "createdBy": "seed-user",
    }
    if upload_type is not None:
        row["uploadType"] = upload_type
    return row


def _claim(claim_id: str, organization_id: str, project_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "_id": claim_id,
        "claim_ref": claim_id.upper(),
        "title": f"Claim {claim_id}",
        "organization_id": organization_id,
        "project_id": project_id,
        "status": "draft",
        "evidence_frozen_at": None,
        **extra,
    }


#: persona -> users row. Role references are the seeded role ``_id`` values,
#: exactly as user administration stores them.
PERSONAS: dict[str, dict[str, Any]] = {
    "project_user": {"roles": ["projectuser"], "organization_id": ORG_A, "projects": [PROJ_A1]},
    "project_admin": {"roles": ["projectadmin"], "organization_id": ORG_A, "projects": [PROJ_A1]},
    "org_user": {"roles": ["orguser"], "organization_id": ORG_A, "projects": []},
    "org_admin": {"roles": ["orgadmin"], "organization_id": ORG_A, "projects": []},
    # No "System User" role exists in the catalogue. The nearest real principal is
    # a system-service account holding no client role.
    "system_user": {
        "roles": [],
        "organization_id": None,
        "projects": [],
        "account_type": "system_service",
    },
    # Super User is dormant (CONTEXT.md): no role document exists and none may be
    # seeded. A bare reference is what a stray assignment would look like.
    "superuser": {"roles": ["superuser"], "organization_id": ORG_A, "organizations": [ORG_A], "projects": []},
    "superadmin": {"roles": ["superadmin"], "organization_id": None, "projects": []},
    # Isolation personas.
    "foreign_org_admin": {"roles": ["orgadmin"], "organization_id": ORG_B, "projects": []},
    "other_project_admin": {"roles": ["projectadmin"], "organization_id": ORG_A, "projects": [PROJ_A2]},
}


async def _seed(db: Any) -> None:
    from rbac_backend.migrations.v20260820_0001_entity_document_links import upgrade
    from rbac_backend.services.data_initialization import (
        initialize_permissions,
        initialize_roles,
    )

    await upgrade(db, dry_run=False)
    await initialize_permissions(db)
    await initialize_roles(db)
    assert await db.roles.find_one({"_id": "superuser"}) is None, (
        "Super User is dormant; the seed must not create a superuser role document"
    )

    await db.organizations.insert_many(
        [{"_id": ORG_A, "name": "Org A"}, {"_id": ORG_B, "name": "Org B"}]
    )
    await db.projects.insert_many(
        [
            {"_id": PROJ_A1, "name": "A1", "organization_id": ORG_A},
            {"_id": PROJ_A2, "name": "A2", "organization_id": ORG_A},
            {"_id": PROJ_B1, "name": "B1", "organization_id": ORG_B},
        ]
    )
    now = datetime.utcnow()
    await db.subscriptions.insert_many(
        [
            {
                "_id": f"sub-{org}",
                "organization_id": org,
                "project_id": None,
                "package_id": None,
                "status": "active",
                "billing_status": "active",
                "plan_code": "dms_enterprise",
                "updated_at": now,
            }
            for org in (ORG_A, ORG_B)
        ]
    )
    for persona, spec in PERSONAS.items():
        await db.users.insert_one(
            {
                "_id": f"user-{persona}",
                "email": f"{persona}@example.com",
                "username": persona,
                "disabled": False,
                "account_type": spec.get("account_type", "client_user"),
                "organizations": spec.get("organizations", []),
                **{k: v for k, v in spec.items() if k not in {"account_type", "organizations"}},
            }
        )

    documents = [
        _document(DOC_OID, organization_id=ORG_A, project_id=PROJ_A1, upload_type="incoming", subject="Incoming notice"),
        _document(DOC_STR_ID, organization_id=ORG_A, project_id=PROJ_A1, upload_type="Outgoing", subject="Outgoing reply"),
        _document(DOC_CONTRACT_OID, organization_id=ORG_A, project_id=PROJ_A1, upload_type="contract", subject="Particular conditions"),
        # A value the model would refuse today but legacy rows can still carry.
        _document(DOC_UNTYPED_ID, organization_id=ORG_A, project_id=PROJ_A1, upload_type="drawing", subject="GA drawing"),
        _document(DOC_A2_OID, organization_id=ORG_A, project_id=PROJ_A2, upload_type="incoming", subject="Other project letter"),
        _document(DOC_B_OID, organization_id=ORG_B, project_id=PROJ_B1, upload_type="incoming", subject="Foreign letter"),
        _document(DOC_DOOMED_OID, organization_id=ORG_A, project_id=PROJ_A1, upload_type="incoming", subject="Doomed letter"),
    ]
    await db.documents.insert_many(documents)
    await db.document_versions.insert_many(
        [
            {
                "_id": f"{row['_id']}-v1",
                "document_id": str(row["_id"]),
                "version_number": 1,
                "is_current": True,
                "file_object_id": f"file-{row['_id']}",
            }
            for row in documents
        ]
    )
    await db.claims.insert_many(
        [
            _claim(CLAIM_A1, ORG_A, PROJ_A1),
            _claim(CLAIM_A2, ORG_A, PROJ_A2),
            _claim(CLAIM_B1, ORG_B, PROJ_B1),
            _claim(CLAIM_FROZEN, ORG_A, PROJ_A1, evidence_frozen_at=datetime(2026, 9, 2)),
        ]
    )


def _app() -> FastAPI:
    from rbac_backend.routers.document_relationships import router as relationship_router
    from rbac_backend.routers.documents import router as documents_router

    app = FastAPI()
    app.include_router(relationship_router, prefix="/api")
    app.include_router(documents_router, prefix="/api")
    return app


def _token(persona: str) -> str:
    from rbac_backend.core.config import settings

    now = int(time.time())
    return jwt.encode(
        {"sub": f"{persona}@example.com", "type": "access", "iat": now, "exp": now + 3600},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


class Env:
    def __init__(self, db: Any, client: httpx.AsyncClient) -> None:
        self.db = db
        self._client = client

    def _headers(self, persona: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {_token(persona)}"}

    async def link(self, persona: str, target_id: str, document_id: str, role: str = "correspondence", target_type: str = "claim"):
        return await self._client.post(
            f"/api/entities/{target_type}/{target_id}/document-links:batch",
            json={"links": [{"document_id": document_id, "relationship_role": role}]},
            headers=self._headers(persona),
        )

    async def forward(self, persona: str, target_id: str, target_type: str = "claim"):
        return await self._client.get(
            f"/api/entities/{target_type}/{target_id}/document-links",
            headers=self._headers(persona),
        )

    async def reverse(self, persona: str, document_id: str):
        return await self._client.get(
            f"/api/documents/{document_id}/entity-links",
            headers=self._headers(persona),
        )

    async def remove(self, persona: str, link_id: str, expected_revision: int = 1):
        return await self._client.post(
            f"/api/document-links/{link_id}:remove",
            json={"reason": "CL-1 verification", "expected_revision": expected_revision},
            headers=self._headers(persona),
        )

    async def get(self, persona: str, path: str, **params: Any):
        return await self._client.get(path, params=params or None, headers=self._headers(persona))

    async def links(self, **query: Any) -> list[dict[str, Any]]:
        return await self.db.entity_document_links.find(query).to_list(length=None)

    async def audits(self, action: str) -> list[dict[str, Any]]:
        return await self.db.audit_events.find({"action": action}).to_list(length=None)


@asynccontextmanager
async def _env() -> AsyncIterator[Env]:
    from rbac_backend.core import database as database_module
    from rbac_backend.core.config import settings

    uri = _uri()
    assert not getattr(settings, "APP_REDIS_URL", None) and not getattr(
        settings, "RUNTIME_STATE_REDIS_URL", None
    ), "unset APP_REDIS_URL / RUNTIME_STATE_REDIS_URL: a local Redis turns cache fail-closed into false reds"

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await mongo.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        mongo.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"relationship_cl1_{uuid.uuid4().hex[:12]}"
    db = mongo[name]

    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    try:
        await _seed(db)
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(db, client)
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _only(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert len(body["links"]) == 1, body
    return body["links"][0]


# --------------------------------------------------------------------------- #
# P0-1 / P0-2 - ObjectId-keyed Documents round-trip over HTTP
# --------------------------------------------------------------------------- #


def test_objectid_document_link_list_reverse_unlink_relink_and_history() -> None:
    async def scenario() -> None:
        async with _env() as env:
            created = await env.link("project_admin", CLAIM_A1, DOC_OID_ID)
            assert created.status_code == 201, created.text
            link = _only(created)
            assert link["document_id"] == DOC_OID_ID
            assert link["document"]["_id"] == DOC_OID_ID
            assert isinstance(link["document"]["uploaded_by_ref"], str)

            # write succeeded -> response succeeded; persisted exactly once
            stored = await env.links(target_id=CLAIM_A1, document_id=DOC_OID_ID)
            assert len(stored) == 1 and stored[0]["removed_at"] is None
            assert len(await env.audits("document_relationship.linked")) == 1

            forward = await env.forward("project_admin", CLAIM_A1)
            assert forward.status_code == 200, forward.text
            assert _only(forward)["document"]["_id"] == DOC_OID_ID

            reverse = await env.reverse("project_admin", DOC_OID_ID)
            assert reverse.status_code == 200, reverse.text
            assert _only(reverse)["target_id"] == CLAIM_A1

            history = await env.get("project_admin", f"/api/document-links/{link['_id']}/history")
            assert history.status_code == 200, history.text

            removed = await env.remove("project_admin", link["_id"])
            assert removed.status_code == 200, removed.text
            assert removed.json()["link"]["removed_at"] is not None
            assert len(await env.audits("document_relationship.unlinked")) == 1
            # unlink removes the relationship only; the Document survives untouched
            source = await env.db.documents.find_one({"_id": DOC_OID})
            assert source is not None and source["lifecycle_state"] == "active"
            assert (await env.forward("project_admin", CLAIM_A1)).json()["links"] == []

            relinked = await env.link("project_admin", CLAIM_A1, DOC_OID_ID)
            assert relinked.status_code == 201, relinked.text
            assert _only(relinked)["_id"] != link["_id"]
            active = await env.links(target_id=CLAIM_A1, document_id=DOC_OID_ID, removed_at=None)
            assert len(active) == 1
            assert len(await env.audits("document_relationship.linked")) == 2

    _run(scenario())


def test_string_id_document_round_trip_still_works() -> None:
    async def scenario() -> None:
        async with _env() as env:
            created = await env.link("project_admin", CLAIM_A1, DOC_STR_ID)
            assert created.status_code == 201, created.text
            link = _only(created)
            assert link["document"]["_id"] == DOC_STR_ID
            assert (await env.forward("project_admin", CLAIM_A1)).status_code == 200
            assert (await env.reverse("project_admin", DOC_STR_ID)).status_code == 200
            assert (await env.remove("project_admin", link["_id"])).status_code == 200

    _run(scenario())


def test_idempotent_link_persists_and_audits_exactly_once() -> None:
    async def scenario() -> None:
        async with _env() as env:
            first = await env.link("project_admin", CLAIM_A1, DOC_OID_ID)
            second = await env.link("project_admin", CLAIM_A1, DOC_OID_ID)
            assert first.status_code == 201, first.text
            assert second.status_code == 201, second.text
            assert _only(first)["_id"] == _only(second)["_id"]
            assert len(await env.links(target_id=CLAIM_A1, document_id=DOC_OID_ID)) == 1
            assert len(await env.audits("document_relationship.linked")) == 1

    _run(scenario())


def test_stale_revision_and_frozen_target_are_refused_cleanly() -> None:
    async def scenario() -> None:
        async with _env() as env:
            link = _only(await env.link("project_admin", CLAIM_A1, DOC_OID_ID))
            stale = await env.remove("project_admin", link["_id"], expected_revision=7)
            assert stale.status_code == 409, stale.text
            assert len(await env.links(target_id=CLAIM_A1, removed_at=None)) == 1

            frozen = await env.link("project_admin", CLAIM_FROZEN, DOC_OID_ID)
            assert frozen.status_code == 409, frozen.text
            assert await env.links(target_id=CLAIM_FROZEN) == []

    _run(scenario())


def test_audit_events_carry_the_full_relationship_identity() -> None:
    async def scenario() -> None:
        async with _env() as env:
            link = _only(await env.link("project_admin", CLAIM_A1, DOC_OID_ID))
            await env.remove("project_admin", link["_id"])
            (linked,) = await env.audits("document_relationship.linked")
            (unlinked,) = await env.audits("document_relationship.unlinked")
            for event in (linked, unlinked):
                assert event["actor_id"] == "user-project_admin"
                assert event["organization_id"] == ORG_A
                assert event["project_id"] == PROJ_A1
                assert event["resource_id"] == link["_id"]
                assert isinstance(event["created_at"], datetime)
                assert event["metadata"] == {
                    "target_type": "claim",
                    "target_id": CLAIM_A1,
                    "document_id": DOC_OID_ID,
                    "relationship_role": "correspondence",
                }
            assert linked["before"] is None and linked["after"]["document_id"] == DOC_OID_ID
            assert unlinked["before"]["removed_at"] is None
            assert unlinked["after"]["removed_at"] is not None

    _run(scenario())


# --------------------------------------------------------------------------- #
# P0-3 - a link whose Document row is gone can still be removed
# --------------------------------------------------------------------------- #


def test_missing_document_link_is_removable_with_audit_and_no_resurrection() -> None:
    async def scenario() -> None:
        async with _env() as env:
            link = _only(await env.link("project_admin", CLAIM_A1, DOC_DOOMED_ID))
            await env.db.documents.delete_one({"_id": DOC_DOOMED_OID})

            removed = await env.remove("project_admin", link["_id"])
            assert removed.status_code == 200, removed.text
            body = removed.json()["link"]
            assert body["removed_at"] is not None
            assert body["document"] is None
            assert await env.db.documents.find_one({"_id": DOC_DOOMED_OID}) is None
            (event,) = await env.audits("document_relationship.unlinked")
            assert event["resource_id"] == link["_id"]
            assert event["metadata"]["document_id"] == DOC_DOOMED_ID

    _run(scenario())


def test_missing_document_unlink_is_not_weaker_for_foreign_or_view_only_actors() -> None:
    async def scenario() -> None:
        async with _env() as env:
            link = _only(await env.link("project_admin", CLAIM_A1, DOC_DOOMED_ID))
            await env.db.documents.delete_one({"_id": DOC_DOOMED_OID})
            for persona in ("foreign_org_admin", "other_project_admin", "project_user"):
                refused = await env.remove(persona, link["_id"])
                assert refused.status_code == 403, (persona, refused.text)
            active = await env.links(_id=link["_id"], removed_at=None)
            assert len(active) == 1
            assert await env.audits("document_relationship.unlinked") == []

    _run(scenario())


def test_missing_document_unlink_refuses_a_link_whose_stored_scope_disagrees() -> None:
    """A link row that claims a different project than its target is not trusted."""

    async def scenario() -> None:
        async with _env() as env:
            link = _only(await env.link("project_admin", CLAIM_A1, DOC_DOOMED_ID))
            await env.db.documents.delete_one({"_id": DOC_DOOMED_OID})
            await env.db.entity_document_links.update_one(
                {"_id": link["_id"]}, {"$set": {"project_id": PROJ_A2}}
            )
            refused = await env.remove("org_admin", link["_id"])
            assert refused.status_code == 409, refused.text
            assert len(await env.links(_id=link["_id"], removed_at=None)) == 1

    _run(scenario())


# --------------------------------------------------------------------------- #
# P1 - correspondence semantics
# --------------------------------------------------------------------------- #


def test_correspondence_role_accepts_incoming_and_outgoing_only() -> None:
    async def scenario() -> None:
        async with _env() as env:
            assert (await env.link("project_admin", CLAIM_A1, DOC_OID_ID)).status_code == 201
            assert (await env.link("project_admin", CLAIM_A1, DOC_STR_ID)).status_code == 201
            for document_id in (DOC_CONTRACT_ID, DOC_UNTYPED_ID):
                refused = await env.link("project_admin", CLAIM_A1, document_id)
                assert refused.status_code == 422, refused.text
                assert await env.links(document_id=document_id) == []

    _run(scenario())


def test_supporting_document_role_is_not_restricted_to_correspondence() -> None:
    async def scenario() -> None:
        async with _env() as env:
            created = await env.link("project_admin", CLAIM_A1, DOC_CONTRACT_ID, role="supporting_document")
            assert created.status_code == 201, created.text

    _run(scenario())


# --------------------------------------------------------------------------- #
# RBAC matrix - real seeds, real policy
# --------------------------------------------------------------------------- #

#: persona -> observed outcome on a Claim target in PROJ_A1, measured against the
#: real seeds. ``reverse`` is (status, links visible): reverse lookup authorizes
#: the Document, then filters each target by the target's own view permission.
#:
#: Project User and Organisation User hold ``dms.document.view`` but no
#: ``dms.claim.*`` permission in DEFAULT_ROLES, so they cannot open a Claim's
#: links and the Claim is filtered out of their reverse lookup.
EXPECTED_MATRIX: dict[str, dict[str, Any]] = {
    "project_user": {"view": 403, "reverse": (200, 0), "create": 403, "remove": 403},
    "project_admin": {"view": 200, "reverse": (200, 1), "create": 201, "remove": 200},
    "org_user": {"view": 403, "reverse": (200, 0), "create": 403, "remove": 403},
    "org_admin": {"view": 200, "reverse": (200, 1), "create": 201, "remove": 200},
    "system_user": {"view": 403, "reverse": (403, None), "create": 403, "remove": 403},
    "superuser": {"view": 403, "reverse": (403, None), "create": 403, "remove": 403},
    "superadmin": {"view": 200, "reverse": (200, 1), "create": 201, "remove": 200},
}


@pytest.mark.parametrize("persona", sorted(EXPECTED_MATRIX))
def test_rbac_matrix(persona: str) -> None:
    async def scenario() -> dict[str, Any]:
        async with _env() as env:
            seeded = _only(await env.link("superadmin", CLAIM_A1, DOC_OID_ID))
            reverse = await env.reverse(persona, DOC_OID_ID)
            observed = {
                "view": (await env.forward(persona, CLAIM_A1)).status_code,
                "reverse": (
                    reverse.status_code,
                    len(reverse.json()["links"]) if reverse.status_code == 200 else None,
                ),
                "create": (await env.link(persona, CLAIM_A1, DOC_STR_ID)).status_code,
                "remove": (await env.remove(persona, seeded["_id"])).status_code,
            }
            # A refused write leaves nothing behind.
            if observed["create"] != 201:
                assert await env.links(document_id=DOC_STR_ID) == []
            if observed["remove"] != 200:
                assert len(await env.links(_id=seeded["_id"], removed_at=None)) == 1
            return observed

    observed = _run(scenario())
    assert observed == EXPECTED_MATRIX[persona], (persona, observed)


# --------------------------------------------------------------------------- #
# Tenant / project isolation
# --------------------------------------------------------------------------- #


def test_foreign_organization_and_project_documents_cannot_be_linked() -> None:
    async def scenario() -> None:
        async with _env() as env:
            for document_id in (DOC_B_ID, DOC_A2_ID):
                refused = await env.link("org_admin", CLAIM_A1, document_id)
                assert refused.status_code == 403, refused.text
            assert await env.links() == []

    _run(scenario())


def test_foreign_target_records_are_refused() -> None:
    async def scenario() -> None:
        async with _env() as env:
            foreign_target = await env.link("project_admin", CLAIM_B1, DOC_OID_ID)
            other_project = await env.link("project_admin", CLAIM_A2, DOC_OID_ID)
            assert foreign_target.status_code == 403, foreign_target.text
            assert other_project.status_code == 403, other_project.text
            assert (await env.forward("project_admin", CLAIM_B1)).status_code == 403
            assert (await env.forward("project_admin", CLAIM_A2)).status_code == 403
            assert await env.links() == []

    _run(scenario())


def test_foreign_link_id_cross_project_unlink_and_reverse_lookup_are_refused() -> None:
    async def scenario() -> None:
        async with _env() as env:
            link = _only(await env.link("project_admin", CLAIM_A1, DOC_OID_ID))
            for persona in ("foreign_org_admin", "other_project_admin"):
                assert (await env.remove(persona, link["_id"])).status_code == 403
                assert (await env.get(persona, f"/api/document-links/{link['_id']}/history")).status_code == 403
                reverse = await env.reverse(persona, DOC_OID_ID)
                assert reverse.status_code == 403, (persona, reverse.text)
                assert CLAIM_A1 not in reverse.text
            unknown = await env.remove("project_admin", "no-such-link")
            assert unknown.status_code == 404
            assert len(await env.links(removed_at=None)) == 1

    _run(scenario())


def test_link_selector_search_never_returns_foreign_documents() -> None:
    async def scenario() -> None:
        async with _env() as env:
            own = await env.get("project_admin", "/api/document-search", q="letter")
            assert own.status_code == 200, own.text
            ids = {row["_id"] for row in own.json()["documents"]}
            assert DOC_DOOMED_ID in ids
            assert DOC_A2_ID not in ids and DOC_B_ID not in ids

            foreign_project = await env.get("project_admin", "/api/document-search", project_id=PROJ_A2)
            assert foreign_project.status_code == 403, foreign_project.text
            assert DOC_A2_ID not in foreign_project.text

            foreign_org = await env.get("project_admin", "/api/document-search", organization_id=ORG_B)
            assert foreign_org.status_code == 403, foreign_org.text

            org_wide = await env.get("org_admin", "/api/document-search", q="letter")
            org_ids = {row["_id"] for row in org_wide.json()["documents"]}
            assert DOC_B_ID not in org_ids

    _run(scenario())


def test_link_selector_supports_correspondence_filtering() -> None:
    async def scenario() -> None:
        async with _env() as env:
            found = await env.get(
                "project_admin", "/api/document-search", uploadType="correspondence", limit=100
            )
            assert found.status_code == 200, found.text
            ids = {row["_id"] for row in found.json()["documents"]}
            assert {DOC_OID_ID, DOC_STR_ID} <= ids
            assert DOC_CONTRACT_ID not in ids and DOC_UNTYPED_ID not in ids

    _run(scenario())


# --------------------------------------------------------------------------- #
# P0-4 - Contract Document target
# --------------------------------------------------------------------------- #


async def _seed_contract_document(db: Any, *, scope_level: str = "project") -> str:
    await db["contract_documents"].insert_one(
        {
            "_id": "cd-a1",
            "organization_id": ORG_A,
            "document_id": DOC_CONTRACT_ID,
            "scope_level": scope_level,
            "scope_project_id": PROJ_A1 if scope_level == "project" else None,
            "contract_document_type": "particular_conditions",
            "classification_revision": 1,
        }
    )
    return "cd-a1"


def test_contract_document_target_links_and_unlinks_without_500() -> None:
    async def scenario() -> None:
        async with _env() as env:
            target = await _seed_contract_document(env.db)
            created = await env.link(
                "org_admin", target, DOC_STR_ID, role="supporting_document", target_type="contract_document"
            )
            assert created.status_code == 201, created.text
            link = _only(created)
            assert link["target_route"] == f"/contracts/viewer/{DOC_CONTRACT_ID}"
            listed = await env.forward("org_admin", target, target_type="contract_document")
            assert listed.status_code == 200, listed.text
            assert (await env.remove("org_admin", link["_id"])).status_code == 200
            record = await env.db["contract_documents"].find_one({"_id": target})
            assert record["document_id"] == DOC_CONTRACT_ID

    _run(scenario())


def test_organisation_scoped_contract_document_is_refused_cleanly() -> None:
    async def scenario() -> None:
        async with _env() as env:
            target = await _seed_contract_document(env.db, scope_level="organization")
            refused = await env.link(
                "org_admin", target, DOC_STR_ID, role="supporting_document", target_type="contract_document"
            )
            assert refused.status_code == 409, refused.text

    _run(scenario())


#: Contract Document target, measured against the same seeds. View is
#: dms.contract.master.view, which the permission alias contract makes equivalent
#: to the legacy ``projects:read`` held by Project/Organisation User; create needs
#: .manage. ``other_project_admin`` is refused: before CL-1 the target gate saw no
#: project (the row's anchor is ``scope_project_id``) and let it through.
CONTRACT_DOCUMENT_MATRIX: dict[str, dict[str, int]] = {
    "project_user": {"view": 200, "create": 403, "remove": 403},
    "project_admin": {"view": 200, "create": 201, "remove": 200},
    "org_user": {"view": 200, "create": 403, "remove": 403},
    "org_admin": {"view": 200, "create": 201, "remove": 200},
    "system_user": {"view": 403, "create": 403, "remove": 403},
    "superuser": {"view": 403, "create": 403, "remove": 403},
    "superadmin": {"view": 200, "create": 201, "remove": 200},
    "foreign_org_admin": {"view": 403, "create": 403, "remove": 403},
    "other_project_admin": {"view": 403, "create": 403, "remove": 403},
}


@pytest.mark.parametrize("persona", sorted(CONTRACT_DOCUMENT_MATRIX))
def test_contract_document_rbac_matrix(persona: str) -> None:
    async def scenario() -> dict[str, int]:
        async with _env() as env:
            target = await _seed_contract_document(env.db)
            seeded = _only(
                await env.link(
                    "superadmin", target, DOC_OID_ID, role="supporting_document", target_type="contract_document"
                )
            )
            observed = {
                "view": (await env.forward(persona, target, target_type="contract_document")).status_code,
                "create": (
                    await env.link(
                        persona, target, DOC_STR_ID, role="supporting_document", target_type="contract_document"
                    )
                ).status_code,
            }
            observed["remove"] = (await env.remove(persona, seeded["_id"])).status_code
            if observed["create"] != 201:
                assert await env.links(target_type="contract_document", document_id=DOC_STR_ID) == []
            if observed["remove"] != 200:
                assert len(await env.links(_id=seeded["_id"], removed_at=None)) == 1
            return observed

    observed = _run(scenario())
    assert observed == CONTRACT_DOCUMENT_MATRIX[persona], (persona, observed)
