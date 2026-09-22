"""CL-2: Variation relationships over real HTTP, real Mongo, real RBAC.

Same harness discipline as the CL-1 suite
(``test_document_relationships_cl1_mongo.py``): a signed JWT through the real
``get_current_user``, the real ``PolicyService`` chain, the real role and
permission seeds, ObjectId-keyed Documents, and no dependency override.

Opt-in: set ``RELATIONSHIP_CL2_MONGODB_URI`` to a disposable replica set
(relationship writes need transactions). Every test creates and drops its own
uniquely named database.
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

MONGODB_URI_ENV = "RELATIONSHIP_CL2_MONGODB_URI"

pytestmark = pytest.mark.integration

ORG_A = "org-a"
ORG_B = "org-b"
PROJ_A1 = "proj-a1"
PROJ_A2 = "proj-a2"
PROJ_B1 = "proj-b1"

DOC_IN = ObjectId()
DOC_IN_ID = str(DOC_IN)
DOC_OUT = ObjectId()
DOC_OUT_ID = str(DOC_OUT)
DOC_CONTRACT = ObjectId()
DOC_CONTRACT_ID = str(DOC_CONTRACT)
DOC_A2 = ObjectId()
DOC_A2_ID = str(DOC_A2)
DOC_B = ObjectId()
DOC_B_ID = str(DOC_B)

VAR_A1 = "var-a1"
VAR_A1_SECOND = "var-a1-second"
VAR_A2 = "var-a2"
VAR_B1 = "var-b1"
CLAIM_A1 = "claim-a1"
IPC_A1 = "ipc-a1"
MILESTONE_A1 = "kd-a1"
ACHIEVEMENT_A1 = f"{MILESTONE_A1}:ach"

PERSONAS: dict[str, dict[str, Any]] = {
    "project_user": {"roles": ["projectuser"], "organization_id": ORG_A, "projects": [PROJ_A1]},
    "project_admin": {"roles": ["projectadmin"], "organization_id": ORG_A, "projects": [PROJ_A1]},
    "org_user": {"roles": ["orguser"], "organization_id": ORG_A, "projects": []},
    "org_admin": {"roles": ["orgadmin"], "organization_id": ORG_A, "projects": []},
    "system_user": {"roles": [], "organization_id": None, "projects": [], "account_type": "system_service"},
    # Dormant: no role document exists and none may be seeded.
    "superuser": {"roles": ["superuser"], "organization_id": ORG_A, "organizations": [ORG_A], "projects": []},
    "superadmin": {"roles": ["superadmin"], "organization_id": None, "projects": []},
    "foreign_org_admin": {"roles": ["orgadmin"], "organization_id": ORG_B, "projects": []},
    "other_project_admin": {"roles": ["projectadmin"], "organization_id": ORG_A, "projects": [PROJ_A2]},
}


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _document(_id: Any, *, organization_id: str, project_id: str, upload_type: str, subject: str, letter_no: str, sender: str) -> dict[str, Any]:
    return {
        "_id": _id,
        "organization_id": organization_id,
        "project_id": project_id,
        "filename": f"{subject}.pdf",
        "filetype": "application/pdf",
        "filesize": 1024,
        "date": datetime(2026, 9, 1),
        "subject": subject,
        "letterNo": letter_no,
        "from": sender,
        "to": "Employer",
        "uploadType": upload_type,
        "status": "active",
        "tags": [],
        "subTags": [],
        "processing_status": "metadata_extracted",
        "lifecycle_state": "active",
        "current_version_id": f"{_id}-v1",
        "uploaded_by_ref": ObjectId(),
        "created_at": datetime(2026, 9, 1, 12, 0),
        "createdBy": "seed-user",
    }


def _variation(variation_id: str, organization_id: str, project_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "_id": variation_id,
        "variation_number": variation_id.upper(),
        "variation_type": "positive",
        "status": "submitted",
        "organization_id": organization_id,
        "project_id": project_id,
        "contract_id": "primary",
        "currency_amounts": [],
        "created_at": datetime(2026, 9, 2),
        **extra,
    }


async def _seed(db: Any) -> None:
    from rbac_backend.migrations.v20260820_0001_entity_document_links import upgrade
    from rbac_backend.services.data_initialization import initialize_permissions, initialize_roles

    await upgrade(db, dry_run=False)
    await initialize_permissions(db)
    await initialize_roles(db)
    assert await db.roles.find_one({"_id": "superuser"}) is None

    await db.organizations.insert_many([{"_id": ORG_A, "name": "Org A"}, {"_id": ORG_B, "name": "Org B"}])
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
                "_id": f"sub-{org}", "organization_id": org, "project_id": None, "package_id": None,
                "status": "active", "billing_status": "active", "plan_code": "dms_enterprise", "updated_at": now,
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
        _document(DOC_IN, organization_id=ORG_A, project_id=PROJ_A1, upload_type="incoming",
                  subject="Engineer instruction on added works", letter_no="ENG/VO/014", sender="Engineer"),
        _document(DOC_OUT, organization_id=ORG_A, project_id=PROJ_A1, upload_type="Outgoing",
                  subject="Contractor variation quotation", letter_no="CON/VO/022", sender="Contractor"),
        _document(DOC_CONTRACT, organization_id=ORG_A, project_id=PROJ_A1, upload_type="contract",
                  subject="Particular conditions", letter_no="PC-1", sender="Employer"),
        _document(DOC_A2, organization_id=ORG_A, project_id=PROJ_A2, upload_type="incoming",
                  subject="Other project letter", letter_no="ENG/VO/014-A2", sender="Engineer"),
        _document(DOC_B, organization_id=ORG_B, project_id=PROJ_B1, upload_type="incoming",
                  subject="Foreign letter", letter_no="ENG/VO/014-B", sender="Engineer"),
    ]
    await db.documents.insert_many(documents)
    await db.document_versions.insert_many(
        [
            {"_id": f"{row['_id']}-v1", "document_id": str(row["_id"]), "version_number": 1,
             "is_current": True, "file_object_id": f"file-{row['_id']}"}
            for row in documents
        ]
    )
    await db.variations.insert_many(
        [
            _variation(VAR_A1, ORG_A, PROJ_A1),
            _variation(VAR_A1_SECOND, ORG_A, PROJ_A1),
            _variation(VAR_A2, ORG_A, PROJ_A2),
            _variation(VAR_B1, ORG_B, PROJ_B1),
        ]
    )
    await db.claims.insert_one(
        {"_id": CLAIM_A1, "claim_ref": "CLM-A1", "title": "Claim A1", "organization_id": ORG_A,
         "project_id": PROJ_A1, "status": "draft", "evidence_frozen_at": None}
    )
    await db.ipc_bills.insert_one(
        {"_id": IPC_A1, "ipc_number": "IPC-A1", "organization_id": ORG_A, "project_id": PROJ_A1, "status": "submitted"}
    )
    await db.key_date_milestones.insert_one(
        {"_id": MILESTONE_A1, "milestone_ref": "KD-01", "title": "Section 1", "organization_id": ORG_A, "project_id": PROJ_A1}
    )
    await db.key_date_achievements.insert_one(
        {"_id": ACHIEVEMENT_A1, "milestone_id": MILESTONE_A1, "organization_id": ORG_A, "project_id": PROJ_A1}
    )


def _app() -> FastAPI:
    from rbac_backend.routers.document_relationships import router as relationship_router
    from rbac_backend.routers.documents import router as documents_router
    from rbac_backend.routers.variations import router as variations_router

    app = FastAPI()
    app.include_router(relationship_router, prefix="/api")
    app.include_router(documents_router, prefix="/api")
    app.include_router(variations_router, prefix="/api")
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

    def headers(self, persona: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {_token(persona)}"}

    async def link(self, persona: str, target_id: str, document_id: str, role: str = "correspondence", target_type: str = "variation"):
        return await self._client.post(
            f"/api/entities/{target_type}/{target_id}/document-links:batch",
            json={"links": [{"document_id": document_id, "relationship_role": role}]},
            headers=self.headers(persona),
        )

    async def forward(self, persona: str, target_id: str, target_type: str = "variation"):
        return await self._client.get(f"/api/entities/{target_type}/{target_id}/document-links", headers=self.headers(persona))

    async def reverse(self, persona: str, document_id: str):
        return await self._client.get(f"/api/documents/{document_id}/entity-links", headers=self.headers(persona))

    async def remove(self, persona: str, link_id: str, expected_revision: int = 1):
        return await self._client.post(
            f"/api/document-links/{link_id}:remove",
            json={"reason": "CL-2 verification", "expected_revision": expected_revision},
            headers=self.headers(persona),
        )

    async def request(self, method: str, persona: str, path: str, **kwargs: Any):
        return await self._client.request(method, path, headers=self.headers(persona), **kwargs)

    async def links(self, **query: Any) -> list[dict[str, Any]]:
        return await self.db.entity_document_links.find(query).to_list(length=None)

    async def audits(self, action: str, **query: Any) -> list[dict[str, Any]]:
        return await self.db.audit_events.find({"action": action, **query}).to_list(length=None)


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
    name = f"relationship_cl2_{uuid.uuid4().hex[:12]}"
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


def _links(response: httpx.Response) -> list[dict[str, Any]]:
    assert response.status_code in (200, 201), response.text
    return response.json()["links"]


# --------------------------------------------------------------------------- #
# 17. Full workflow, incoming and outgoing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("document_id", [DOC_IN_ID, DOC_OUT_ID], ids=["incoming", "outgoing"])
def test_full_variation_correspondence_workflow(document_id: str) -> None:
    async def scenario() -> None:
        async with _env() as env:
            # 1-2. the letter and the Variation exist
            assert (await env.db.documents.find_one({"_id": ObjectId(document_id)})) is not None
            opened = await env.request("GET", "project_admin", f"/api/variations/{VAR_A1}")
            assert opened.status_code == 200, opened.text

            # 3. link
            created = _links(await env.link("project_admin", VAR_A1, document_id))
            assert len(created) == 1 and created[0]["document"]["_id"] == document_id
            link = created[0]

            # 4. forward
            forward = _links(await env.forward("project_admin", VAR_A1))
            assert [row["_id"] for row in forward] == [link["_id"]]

            # 5. reverse
            reverse = _links(await env.reverse("project_admin", document_id))
            assert [(row["target_type"], row["target_id"], row["target_route"]) for row in reverse] == [
                ("variation", VAR_A1, f"/variations?variation_id={VAR_A1}")
            ]

            # presented on the Variation record itself
            opened = await env.request("GET", "project_admin", f"/api/variations/{VAR_A1}")
            assert opened.json()["linked_document_ids"] == [document_id]

            # 6. idempotent re-link: same row, no second audit
            again = _links(await env.link("project_admin", VAR_A1, document_id))
            assert again[0]["_id"] == link["_id"]
            assert len(await env.links(target_id=VAR_A1, document_id=document_id)) == 1
            linked = await env.audits("document_relationship.linked")
            assert len(linked) == 1 and linked[0]["metadata"]["target_type"] == "variation"

            # 7. unlink
            removed = await env.remove("project_admin", link["_id"])
            assert removed.status_code == 200, removed.text
            unlinked = await env.audits("document_relationship.unlinked")
            assert len(unlinked) == 1 and unlinked[0]["metadata"]["document_id"] == document_id

            # 8-9. forward and reverse absent
            assert _links(await env.forward("project_admin", VAR_A1)) == []
            assert _links(await env.reverse("project_admin", document_id)) == []

            # 10. the letter still exists, untouched
            source = await env.db.documents.find_one({"_id": ObjectId(document_id)})
            assert source is not None and source["lifecycle_state"] == "active"

    _run(scenario())


def test_contract_document_under_correspondence_role_is_422_but_supporting_document_is_accepted() -> None:
    async def scenario() -> None:
        async with _env() as env:
            refused = await env.link("project_admin", VAR_A1, DOC_CONTRACT_ID, role="correspondence")
            assert refused.status_code == 422, refused.text
            assert await env.links(target_id=VAR_A1) == []
            accepted = await env.link("project_admin", VAR_A1, DOC_CONTRACT_ID, role="supporting_document")
            assert accepted.status_code == 201, accepted.text

    _run(scenario())


# --------------------------------------------------------------------------- #
# 2. Raw linked_document_ids writes over real HTTP
# --------------------------------------------------------------------------- #


def test_raw_linked_document_ids_are_refused_and_never_persisted() -> None:
    async def scenario() -> None:
        async with _env() as env:
            for forged in ([DOC_B_ID], [DOC_A2_ID], [DOC_IN_ID]):
                response = await env.request(
                    "PUT", "project_admin", f"/api/variations/{VAR_A1}",
                    json={"remarks": "forged", "linked_document_ids": forged},
                )
                assert response.status_code == 409, response.text
            row = await env.db.variations.find_one({"_id": VAR_A1})
            assert "linked_document_ids" not in row and row.get("remarks") is None

            created = await env.request(
                "POST", "project_admin", "/api/variations",
                json={"project_id": PROJ_A1, "organization_id": ORG_A, "variation_number": "VO-X",
                      "linked_document_ids": [DOC_B_ID]},
            )
            assert created.status_code == 409, created.text
            assert await env.db.variations.count_documents({"variation_number": "VO-X"}) == 0
            assert await env.links() == []

    _run(scenario())


def test_legacy_rows_still_read_but_foreign_ids_never_surface() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await env.db.variations.update_one(
                {"_id": VAR_A1}, {"$set": {"linked_document_ids": [DOC_IN_ID, DOC_A2_ID, DOC_B_ID]}}
            )
            opened = await env.request("GET", "project_admin", f"/api/variations/{VAR_A1}")
            assert opened.json()["linked_document_ids"] == [DOC_IN_ID]
            forward = _links(await env.forward("project_admin", VAR_A1))
            assert [(row["document_id"], row["source"]) for row in forward] == [(DOC_IN_ID, "legacy_read_through")]
            listed = await env.request("GET", "project_admin", "/api/variations", params={"project_id": PROJ_A1})
            by_id = {row["_id"]: row for row in listed.json()}
            assert by_id[VAR_A1]["linked_document_ids"] == [DOC_IN_ID]
            # echoing back what the caller was shown stays compatible ...
            echoed = await env.request(
                "PUT", "project_admin", f"/api/variations/{VAR_A1}",
                json={"remarks": "ok", "linked_document_ids": [DOC_IN_ID]},
            )
            assert echoed.status_code == 200, echoed.text
            # ... but the raw stored array (with ids never shown) is refused
            guessed = await env.request(
                "PUT", "project_admin", f"/api/variations/{VAR_A1}",
                json={"linked_document_ids": [DOC_B_ID, DOC_IN_ID, DOC_A2_ID]},
            )
            assert guessed.status_code == 409, guessed.text
            row = await env.db.variations.find_one({"_id": VAR_A1})
            assert row["linked_document_ids"] == [DOC_IN_ID, DOC_A2_ID, DOC_B_ID]

    _run(scenario())


# --------------------------------------------------------------------------- #
# 11. Many-to-many
# --------------------------------------------------------------------------- #


def test_one_letter_many_records_and_one_variation_many_letters() -> None:
    async def scenario() -> None:
        async with _env() as env:
            for target_type, target_id, role in (
                ("variation", VAR_A1, "correspondence"),
                ("variation", VAR_A1_SECOND, "variation_submission"),
                ("claim", CLAIM_A1, "correspondence"),
                ("ipc_bill", IPC_A1, "supporting_document"),
                ("key_date_achievement", ACHIEVEMENT_A1, "correspondence"),
            ):
                response = await env.link("project_admin", target_id, DOC_IN_ID, role=role, target_type=target_type)
                assert response.status_code == 201, (target_type, response.text)
            reverse = _links(await env.reverse("project_admin", DOC_IN_ID))
            assert sorted((row["target_type"], row["target_id"]) for row in reverse) == sorted(
                [
                    ("variation", VAR_A1), ("variation", VAR_A1_SECOND), ("claim", CLAIM_A1),
                    ("ipc_bill", IPC_A1), ("key_date_achievement", ACHIEVEMENT_A1),
                ]
            )
            routes = {row["target_type"]: row["target_route"] for row in reverse}
            assert routes["key_date_achievement"] == f"/key-dates/{MILESTONE_A1}?achievement_id={ACHIEVEMENT_A1}"

            assert (await env.link("project_admin", VAR_A1, DOC_OUT_ID, role="variation_approval")).status_code == 201
            forward = _links(await env.forward("project_admin", VAR_A1))
            assert sorted(row["document_id"] for row in forward) == sorted([DOC_IN_ID, DOC_OUT_ID])

            # Link to Record lists the verified targets, in scope only
            targets = await env.request(
                "GET", "project_admin", f"/api/documents/{DOC_IN_ID}/link-targets", params={"target_type": "variation"}
            )
            assert targets.status_code == 200, targets.text
            assert sorted(row["target_id"] for row in targets.json()["targets"]) == [VAR_A1, VAR_A1_SECOND]

    _run(scenario())


# --------------------------------------------------------------------------- #
# 12. Delete safety
# --------------------------------------------------------------------------- #


def test_deleting_a_variation_removes_its_links_atomically_and_keeps_letters() -> None:
    async def scenario() -> None:
        async with _env() as env:
            assert (await env.link("project_admin", VAR_A1, DOC_IN_ID)).status_code == 201
            assert (await env.link("project_admin", VAR_A1, DOC_OUT_ID)).status_code == 201
            assert (await env.link("project_admin", CLAIM_A1, DOC_IN_ID, target_type="claim")).status_code == 201
            deleted = await env.request("DELETE", "project_admin", f"/api/variations/{VAR_A1}")
            assert deleted.status_code == 204, deleted.text
            assert await env.db.variations.find_one({"_id": VAR_A1}) is None
            assert await env.links(target_type="variation", target_id=VAR_A1, removed_at=None) == []
            assert len(await env.audits("document_relationship.unlinked")) == 2
            assert len(await env.audits("variation.deleted")) == 1
            reverse = _links(await env.reverse("project_admin", DOC_IN_ID))
            assert [(row["target_type"], row["target_id"]) for row in reverse] == [("claim", CLAIM_A1)]
            assert await env.db.documents.count_documents({"_id": {"$in": [DOC_IN, DOC_OUT]}}) == 2

            # the project user cannot delete (no dms.variation.delete)
            refused = await env.request("DELETE", "project_user", f"/api/variations/{VAR_A1_SECOND}")
            assert refused.status_code == 403, refused.text

    _run(scenario())


# --------------------------------------------------------------------------- #
# 13. RBAC matrix
# --------------------------------------------------------------------------- #

#: persona -> (view links, create link, remove link, reverse count). Measured
#: against the real seeds; Project/Organisation User hold dms.document.view but no
#: dms.variation.* permission, exactly like Claim in CL-1.
RBAC_EXPECTED = {
    "project_user": (403, 403, 403, 0),
    "project_admin": (200, 201, 200, 1),
    "org_user": (403, 403, 403, 0),
    "org_admin": (200, 201, 200, 1),
    "system_user": (403, 403, 403, None),
    "superuser": (403, 403, 403, None),
    "superadmin": (200, 201, 200, 1),
}


@pytest.mark.parametrize("persona", sorted(RBAC_EXPECTED))
def test_variation_relationship_rbac_matrix(persona: str) -> None:
    view, create, remove, reverse_count = RBAC_EXPECTED[persona]

    async def scenario() -> None:
        async with _env() as env:
            seeded = _links(await env.link("superadmin", VAR_A1, DOC_IN_ID))[0]
            assert (await env.forward(persona, VAR_A1)).status_code == view
            assert (await env.link(persona, VAR_A1, DOC_OUT_ID)).status_code == create
            assert (await env.remove(persona, seeded["_id"])).status_code == remove
            reverse = await env.reverse(persona, DOC_IN_ID)
            if reverse_count is None:
                assert reverse.status_code == 403, reverse.text
            else:
                assert reverse.status_code == 200, reverse.text
                visible = [row for row in reverse.json()["links"] if row["target_type"] == "variation"]
                assert len(visible) == (0 if remove == 200 else reverse_count)

    _run(scenario())


# --------------------------------------------------------------------------- #
# 14. Tenant isolation
# --------------------------------------------------------------------------- #


def test_isolation_fails_closed_on_every_axis() -> None:
    async def scenario() -> None:
        async with _env() as env:
            # foreign-org and foreign-project Variations
            assert (await env.forward("project_admin", VAR_B1)).status_code == 403
            assert (await env.forward("project_admin", VAR_A2)).status_code == 403
            assert (await env.link("project_admin", VAR_B1, DOC_IN_ID)).status_code == 403
            assert (await env.link("project_admin", VAR_A2, DOC_IN_ID)).status_code == 403
            # foreign correspondence onto an own Variation
            assert (await env.link("project_admin", VAR_A1, DOC_B_ID)).status_code == 403
            assert (await env.link("project_admin", VAR_A1, DOC_A2_ID)).status_code == 403
            assert await env.links() == []

            # a link in A2, seen from A1's admin
            foreign = _links(await env.link("superadmin", VAR_A2, DOC_A2_ID))[0]
            assert (await env.remove("project_admin", foreign["_id"])).status_code == 403
            assert (await env.request("GET", "project_admin", f"/api/document-links/{foreign['_id']}")).status_code == 403
            assert (await env.remove("foreign_org_admin", foreign["_id"])).status_code == 403
            assert (await env.links(_id=foreign["_id"]))[0]["removed_at"] is None

            # reverse lookup leakage
            assert (await env.reverse("project_admin", DOC_A2_ID)).status_code == 403
            assert (await env.reverse("foreign_org_admin", DOC_A2_ID)).status_code == 403
            own = _links(await env.reverse("other_project_admin", DOC_A2_ID))
            assert [row["target_id"] for row in own] == [VAR_A2]

            # Link-to-Record leakage
            targets = await env.request(
                "GET", "other_project_admin", f"/api/documents/{DOC_IN_ID}/link-targets", params={"target_type": "variation"}
            )
            assert targets.status_code == 403, targets.text
            foreign_targets = await env.request(
                "GET", "project_admin", f"/api/documents/{DOC_IN_ID}/link-targets", params={"target_type": "variation"}
            )
            assert VAR_A2 not in {row["target_id"] for row in foreign_targets.json()["targets"]}

            # selector leakage: correspondence filter never crosses scope
            for persona, forbidden in (("project_admin", {DOC_A2_ID, DOC_B_ID}), ("org_admin", {DOC_B_ID})):
                found = await env.request(
                    "GET", persona, "/api/document-search",
                    params={"q": "letter", "uploadType": "correspondence", "limit": 50},
                )
                assert found.status_code == 200, found.text
                assert not ({row["_id"] for row in found.json()["documents"]} & forbidden)
            foreign_filter = await env.request(
                "GET", "project_admin", "/api/document-search",
                params={"organization_id": ORG_B, "uploadType": "correspondence"},
            )
            assert foreign_filter.status_code == 403, foreign_filter.text

    _run(scenario())


def test_selector_correspondence_filter_returns_incoming_and_outgoing_only() -> None:
    async def scenario() -> None:
        async with _env() as env:
            correspondence = await env.request(
                "GET", "project_admin", "/api/document-search",
                params={"project_id": PROJ_A1, "uploadType": "correspondence", "limit": 50},
            )
            assert correspondence.status_code == 200, correspondence.text
            assert {row["_id"] for row in correspondence.json()["documents"]} == {DOC_IN_ID, DOC_OUT_ID}
            everything = await env.request(
                "GET", "project_admin", "/api/document-search", params={"project_id": PROJ_A1, "limit": 50}
            )
            assert DOC_CONTRACT_ID in {row["_id"] for row in everything.json()["documents"]}
            by_letter = await env.request(
                "GET", "project_admin", "/api/document-search",
                params={"project_id": PROJ_A1, "uploadType": "correspondence", "letterNo": "ENG/VO/014"},
            )
            assert [row["_id"] for row in by_letter.json()["documents"]] == [DOC_IN_ID]
            by_date = await env.request(
                "GET", "project_admin", "/api/document-search",
                params={"project_id": PROJ_A1, "uploadType": "correspondence", "date_from": "2026-09-02"},
            )
            assert by_date.json()["documents"] == []

    _run(scenario())
