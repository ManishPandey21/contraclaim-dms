"""Phase 8 PR A: enclosure / annexure / attachment as first-class child Documents.

Domain and service layer only. Upload wiring, processing jobs, canonical
evidence, retrieval and graph participation come in later PRs.

Real Mongo with its real unique indexes, real role and permission seeds, real
``PolicyService``. Principals are resolved from the seeded user rows by
``resolve_stored_principal``, so each persona carries exactly the authority the
entitlement store gives it. Nothing in the authorization chain is overridden.
Only ``cascade_delete_cleanup`` is replaced, by a recorder: its Falkor and
Qdrant steps would need live services, and here we only care whether it ran
and for which document.

Owner decisions this suite pins:

* Identity is ``(parent_document_id, relationship_type, file sha256)``.
  - The same bytes under a different parent are a separate, valid child.
  - The same bytes under the same parent and relationship are one child: the
    retry returns it.
  - The same bytes under the same parent with a *different* relationship are a
    separate child, because the relationship is part of the identity.
* A child is never rejected because its sha256 already exists elsewhere in the
  project. The FileObject is reused, never copied.
* A child takes its organisation and project from its parent, server side.

Opt-in: set ``CHILD_DOCUMENTS_MONGODB_URI`` to a disposable replica set. The
parent-delete cascade runs inside a transaction. Every test creates and drops
its own uniquely named database.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List

import pytest
from bson import ObjectId
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URI_ENV = "CHILD_DOCUMENTS_MONGODB_URI"

pytestmark = pytest.mark.integration

ORG_A = "org-a"
ORG_B = "org-b"
PROJ_A1 = "proj-a1"
PROJ_A2 = "proj-a2"
PROJ_B1 = "proj-b1"

PERSONAS: Dict[str, Dict[str, Any]] = {
    "project_admin": {
        "roles": ["projectadmin"],
        "organization_id": ORG_A,
        "projects": [PROJ_A1],
    },
    "org_admin": {"roles": ["orgadmin"], "organization_id": ORG_A, "projects": []},
    "doc_controller": {
        "roles": ["doccontroller"],
        "organization_id": ORG_A,
        "projects": [PROJ_A1],
    },
    "other_project_admin": {
        "roles": ["projectadmin"],
        "organization_id": ORG_A,
        "projects": [PROJ_A2],
    },
    "foreign_org_admin": {
        "roles": ["orgadmin"],
        "organization_id": ORG_B,
        "projects": [],
    },
}

#: Keys a ``document.child_created`` audit event may carry. Storage paths, keys,
#: provider locations and file names are deliberately absent: the audit trail is
#: readable more widely than the files it describes.
ALLOWED_AUDIT_KEYS = {
    "parent_document_id",
    "relationship_type",
    "file_object_id",
    "enclosure_entry_id",
    "source_page_range",
}


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _letter(
    _id: ObjectId, org: str, project: str, letter_no: str, **extra: Any
) -> Dict[str, Any]:
    return {
        "_id": _id,
        "organization_id": org,
        "project_id": project,
        "project_name": f"Project {project}",
        "filename": f"{letter_no}.pdf",
        "filetype": "application/pdf",
        "filesize": 1024,
        "uploadType": "incoming",
        "letterNo": letter_no,
        "letterNoNormalized": letter_no.replace("/", ""),
        "date": datetime(2026, 9, 1),
        "subject": f"Subject of {letter_no}",
        "status": "Received",
        "tags": [],
        "subTags": [],
        "createdBy": "seed-user",
        "lifecycle_state": "active",
        "_revision": 1,
        "enclosures": [],
        "createdAt": datetime(2026, 9, 1),
        "updatedAt": datetime(2026, 9, 1),
        **extra,
    }


def _file_object(
    fo_id: str, sha: str, org: str, project: str, owner: str, **extra: Any
) -> Dict[str, Any]:
    return {
        "_id": fo_id,
        "sha256": sha,
        "size": 4096,
        "mime_type": "application/pdf",
        "original_filename": "annexure-a.pdf",
        "stored_filename": f"{fo_id}_annexure-a.pdf",
        "storage_key": f"/{org}/{project}/enclosures/2026/09/{owner}/{fo_id}_annexure-a.pdf",
        "primary_provider": "local",
        "locations": [
            {
                "provider": "local",
                "path": f"/data/{fo_id}.pdf",
                "status": "ok",
                "primary": True,
            }
        ],
        "organization_id": org,
        "project_id": project,
        "document_id": owner,
        "document_ids": [owner],
        "document_type": "enclosure",
        "immutable": True,
        "createdAt": datetime(2026, 9, 1),
        **extra,
    }


class Env:
    """Seeded tenants, two A1 parent letters, and file objects to attach."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.parent = ObjectId()  # A1
        self.second_parent = ObjectId()  # A1
        self.parent_a2 = ObjectId()
        self.parent_b = ObjectId()
        self.sha = "a" * 64
        self.fo = (
            "fo-shared-a1"  # an A1 enclosure FileObject, already on `parent`'s entry
        )
        self.fo_a2 = "fo-a2"
        self.fo_b = "fo-b"
        self.fo_solo = (
            "fo-solo-a1"  # A1, referenced by nothing but the child made from it
        )
        self.entry_id = str(ObjectId())
        self.cleaned: List[str] = []

    async def seed(self) -> None:
        from rbac_backend.services.child_document_service import ensure_child_document_indexes
        from rbac_backend.migrations.v20260820_0001_entity_document_links import (
            upgrade as link_indexes,
        )
        from rbac_backend.services.data_initialization import (
            initialize_permissions,
            initialize_roles,
        )

        db = self.db
        await link_indexes(db, dry_run=False)
        await ensure_child_document_indexes(db)
        await initialize_permissions(db)
        await initialize_roles(db)
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
                    "account_type": "client_user",
                    "organizations": [],
                    **spec,
                }
            )
        entry = {
            "id": self.entry_id,
            "filename": "Annexure A - quantities.pdf",
            "filepath_local": "/data/fo.pdf",
            "filepath_s3": None,
            "presigned_url": None,
            "filetype": "application/pdf",
            "filesize": 4096,
            "uploadedAt": datetime(2026, 9, 2),
            "uploadedBy": "user-project_admin",
            "storage_key": "/org-a/proj-a1/enclosures/k",
            "file_object_id": self.fo,
            "storage_locations": [
                {"provider": "local", "path": "/data/fo.pdf", "status": "ok"}
            ],
        }
        await db.documents.insert_many(
            [
                _letter(self.parent, ORG_A, PROJ_A1, "ENG/A1/001", enclosures=[entry]),
                _letter(self.second_parent, ORG_A, PROJ_A1, "ENG/A1/002"),
                _letter(self.parent_a2, ORG_A, PROJ_A2, "ENG/A2/001"),
                _letter(self.parent_b, ORG_B, PROJ_B1, "ENG/B1/001"),
            ]
        )
        await db.file_objects.insert_many(
            [
                _file_object(self.fo, self.sha, ORG_A, PROJ_A1, str(self.parent)),
                _file_object(self.fo_a2, "b" * 64, ORG_A, PROJ_A2, str(self.parent_a2)),
                _file_object(self.fo_b, "c" * 64, ORG_B, PROJ_B1, str(self.parent_b)),
                _file_object(
                    self.fo_solo,
                    "d" * 64,
                    ORG_A,
                    PROJ_A1,
                    "nobody",
                    document_id=None,
                    document_ids=[],
                ),
            ]
        )

    async def principal(self, persona: str) -> Any:
        from rbac_backend.core.security import resolve_stored_principal

        return await resolve_stored_principal(self.db, f"user-{persona}")

    def service(self) -> Any:
        from rbac_backend.services.child_document_service import ChildDocumentService
        from rbac_backend.services.document_service import DocumentService

        document_service = DocumentService(db=self.db)

        async def _record_cleanup(document_id: str) -> Dict[str, Any]:
            self.cleaned.append(str(document_id))
            return {"errors": []}

        document_service.cascade_delete_cleanup = _record_cleanup  # type: ignore[method-assign]
        return ChildDocumentService(db=self.db, document_service=document_service)

    async def create(self, persona: str = "project_admin", **kwargs: Any) -> Any:
        kwargs.setdefault("parent_document_id", str(self.parent))
        kwargs.setdefault("relationship_type", "enclosure")
        kwargs.setdefault("file_object_id", self.fo)
        return await self.service().create_child(
            await self.principal(persona), **kwargs
        )

    async def children(self, parent: ObjectId) -> List[Dict[str, Any]]:
        return await self.db.documents.find(
            {"parent_document_id": str(parent)}
        ).to_list(length=None)

    async def doc(self, _id: Any) -> Dict[str, Any]:
        return await self.db.documents.find_one({"_id": ObjectId(str(_id))})


@asynccontextmanager
async def _env() -> AsyncIterator[Env]:
    from rbac_backend.core import database as database_module
    from rbac_backend.core.config import settings

    uri = _uri()
    assert not getattr(settings, "APP_REDIS_URL", None) and not getattr(
        settings, "RUNTIME_STATE_REDIS_URL", None
    ), (
        "unset APP_REDIS_URL / RUNTIME_STATE_REDIS_URL: a local Redis turns cache fail-closed into false reds"
    )
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await mongo.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        mongo.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"child_documents_{uuid.uuid4().hex[:12]}"
    db = mongo[name]
    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    try:
        env = Env(db)
        await env.seed()
        yield env
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _status(exc: BaseException) -> int:
    return int(getattr(exc, "status_code", None) or getattr(exc, "http_status", 0))


# --------------------------------------------------------------------------- #
# 1, 8: scope inheritance; the parent is never altered
# --------------------------------------------------------------------------- #


def test_child_inherits_parent_scope_and_provenance_and_parent_scope_is_unchanged() -> (
    None
):
    async def scenario() -> None:
        async with _env() as env:
            before = await env.doc(env.parent)
            result = await env.create(
                relationship_type="annexure",
                enclosure_entry_id=env.entry_id,
                source_page_range={"start": 2, "end": 5},
            )
            assert result.created is True
            child = await env.doc(result.document["_id"])
            assert child["organization_id"] == ORG_A
            assert child["project_id"] == PROJ_A1
            assert child["project_name"] == before["project_name"]
            assert child["parent_document_id"] == str(env.parent)
            assert child["relationship_type"] == "annexure"
            assert child["source_page_range"] == {"start": 2, "end": 5}
            assert child["uploadType"] == "incoming"
            assert child["sha256"] == env.sha
            assert child["file_object_id"] == env.fo
            assert child["original_filename"] == "Annexure A - quantities.pdf"
            assert child["lifecycle_state"] == "active"
            # A child is not a letter: it must not enter the letter-number and
            # duplicate-letter machinery.
            assert child.get("letterNo") is None
            assert child.get("letterNoNormalized") is None
            assert child.get("duplicate_status") is None
            provenance = child["parent_provenance"]
            assert provenance["parent_letter_no"] == "ENG/A1/001"
            assert provenance["enclosure_entry_id"] == env.entry_id
            assert provenance["attached_by"] == "user-project_admin"

            # The model still reads the row (one malformed row must not 500 a listing).
            from rbac_backend.models.document import Document

            modelled = Document(**{**child, "_id": str(child["_id"])})
            assert modelled.parent_document_id == str(env.parent)
            assert modelled.relationship_type == "annexure"

            after = await env.doc(env.parent)
            for field in (
                "organization_id",
                "project_id",
                "project_name",
                "_revision",
                "lifecycle_state",
                "letterNo",
                "uploadType",
            ):
                assert after.get(field) == before.get(field), field
            [entry] = after["enclosures"]
            assert entry["child_document_id"] == str(child["_id"])
            assert (
                entry["file_object_id"] == env.fo
            )  # the link never strips storage fields

    _run(scenario())


def test_create_child_accepts_no_caller_supplied_scope() -> None:
    from rbac_backend.services.child_document_service import ChildDocumentService

    params = set(inspect.signature(ChildDocumentService.create_child).parameters)
    assert not params & {
        "organization_id",
        "project_id",
        "project_name",
        "sha256",
        "uploadType",
        "upload_type",
    }


def test_a_child_cannot_be_the_parent_of_another_child() -> None:
    async def scenario() -> None:
        async with _env() as env:
            first = await env.create()
            with pytest.raises(Exception) as caught:
                await env.create(
                    parent_document_id=str(first.document["_id"]),
                    file_object_id=env.fo_solo,
                )
            assert _status(caught.value) == 409
            assert len(await env.children(first.document["_id"])) == 0

    _run(scenario())


def test_unknown_relationship_type_and_malformed_page_range_are_refused() -> None:
    async def scenario() -> None:
        async with _env() as env:
            with pytest.raises(Exception) as bad_type:
                await env.create(relationship_type="photo")
            assert _status(bad_type.value) == 422
            with pytest.raises(Exception) as bad_range:
                await env.create(source_page_range={"start": 5, "end": 2})
            assert _status(bad_range.value) == 422
            assert await env.children(env.parent) == []

    _run(scenario())


def test_a_deleted_parent_takes_no_new_children() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await env.db.documents.update_one(
                {"_id": env.parent}, {"$set": {"lifecycle_state": "deleted"}}
            )
            with pytest.raises(Exception) as caught:
                await env.create()
            assert _status(caught.value) == 409
            assert await env.children(env.parent) == []

    _run(scenario())


# --------------------------------------------------------------------------- #
# 2, 3: RBAC
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "persona", ["foreign_org_admin", "other_project_admin", "doc_controller"]
)
def test_creation_is_denied_without_edit_authority_on_the_parent(persona: str) -> None:
    async def scenario() -> None:
        async with _env() as env:
            with pytest.raises(HTTPException) as caught:
                await env.create(persona)
            assert caught.value.status_code == 403
            assert await env.children(env.parent) == []

    _run(scenario())


@pytest.mark.parametrize("persona", ["foreign_org_admin", "other_project_admin"])
def test_foreign_org_and_foreign_project_cannot_read_list_delete_restore_or_purge(
    persona: str,
) -> None:
    async def scenario() -> None:
        async with _env() as env:
            child_id = str((await env.create()).document["_id"])
            service = env.service()
            principal = await env.principal(persona)
            for call in (
                lambda: service.get_child(principal, child_id),  # a guessed id
                lambda: service.list_children(principal, str(env.parent)),
                lambda: service.soft_delete_child(principal, child_id),
                lambda: service.restore_child(principal, child_id),
                lambda: service.purge_child(principal, child_id),
            ):
                with pytest.raises(HTTPException) as caught:
                    await call()
                assert caught.value.status_code == 403
            assert (await env.doc(child_id))["lifecycle_state"] == "active"

    _run(scenario())


def test_org_admin_of_the_parent_org_may_create_and_read() -> None:
    async def scenario() -> None:
        async with _env() as env:
            result = await env.create("org_admin")
            service = env.service()
            principal = await env.principal("org_admin")
            fetched = await service.get_child(principal, str(result.document["_id"]))
            assert fetched["_id"] == result.document["_id"]
            assert [
                str(c["_id"])
                for c in await service.list_children(principal, str(env.parent))
            ] == [str(result.document["_id"])]

    _run(scenario())


def test_get_child_refuses_a_document_that_is_not_a_child() -> None:
    async def scenario() -> None:
        async with _env() as env:
            with pytest.raises(Exception) as caught:
                await env.service().get_child(
                    await env.principal("project_admin"), str(env.second_parent)
                )
            assert _status(caught.value) == 404

    _run(scenario())


# --------------------------------------------------------------------------- #
# 4-7, 14: identity, idempotency and FileObject reuse
# --------------------------------------------------------------------------- #


def test_same_sha_under_a_different_parent_is_a_separate_child_sharing_the_file_object() -> (
    None
):
    async def scenario() -> None:
        async with _env() as env:
            files_before = await env.db.file_objects.count_documents({})
            first = await env.create()
            second = await env.create(parent_document_id=str(env.second_parent))
            assert first.created and second.created
            assert first.document["_id"] != second.document["_id"]
            assert first.document["sha256"] == second.document["sha256"] == env.sha
            assert (
                await env.db.file_objects.count_documents({}) == files_before
            )  # reused, never copied
            fo = await env.db.file_objects.find_one({"_id": env.fo})
            assert {str(first.document["_id"]), str(second.document["_id"])} <= set(
                fo["document_ids"]
            )
            assert (
                str(env.parent) in fo["document_ids"]
            )  # the enclosure entry's owner is kept

    _run(scenario())


def test_same_sha_same_parent_same_relationship_is_idempotent() -> None:
    async def scenario() -> None:
        async with _env() as env:
            first = await env.create(enclosure_entry_id=env.entry_id)
            again = await env.create(enclosure_entry_id=env.entry_id)
            assert first.created is True and again.created is False
            assert again.document["_id"] == first.document["_id"]
            assert len(await env.children(env.parent)) == 1
            fo = await env.db.file_objects.find_one({"_id": env.fo})
            assert fo["document_ids"].count(str(first.document["_id"])) == 1

    _run(scenario())


def test_same_sha_same_parent_different_relationship_is_a_separate_child() -> None:
    async def scenario() -> None:
        async with _env() as env:
            enclosure = await env.create(relationship_type="enclosure")
            annexure = await env.create(relationship_type="annexure")
            assert enclosure.created and annexure.created
            assert enclosure.document["_id"] != annexure.document["_id"]
            kinds = sorted(
                c["relationship_type"] for c in await env.children(env.parent)
            )
            assert kinds == ["annexure", "enclosure"]

    _run(scenario())


def test_the_unique_index_holds_identity_even_if_the_service_check_is_bypassed() -> (
    None
):
    from pymongo.errors import DuplicateKeyError

    async def scenario() -> None:
        async with _env() as env:
            row = (await env.create()).document
            clone = {k: v for k, v in row.items() if k != "_id"}
            with pytest.raises(DuplicateKeyError):
                await env.db.documents.insert_one(clone)

    _run(scenario())


def test_concurrent_duplicate_retries_create_exactly_one_child() -> None:
    async def scenario() -> None:
        async with _env() as env:
            principal = await env.principal("project_admin")
            services = [env.service() for _ in range(6)]
            results = await asyncio.gather(
                *(
                    s.create_child(
                        principal,
                        parent_document_id=str(env.parent),
                        relationship_type="enclosure",
                        file_object_id=env.fo,
                    )
                    for s in services
                )
            )
            assert sum(1 for r in results if r.created) == 1
            assert len({str(r.document["_id"]) for r in results}) == 1
            assert len(await env.children(env.parent)) == 1
            events = await env.db.document_audit_events.count_documents(
                {"event_type": "document.child_created"}
            )
            assert events == 1

    _run(scenario())


@pytest.mark.parametrize("file_object", ["fo_a2", "fo_b", "missing"])
def test_a_file_object_outside_the_parent_scope_is_refused(file_object: str) -> None:
    async def scenario() -> None:
        async with _env() as env:
            fo_id = getattr(env, file_object, "fo-does-not-exist")
            with pytest.raises(Exception) as caught:
                await env.create(file_object_id=fo_id)
            # Foreign and absent file objects answer identically: no probe can
            # tell another tenant's file object from a typo.
            assert _status(caught.value) == 404
            assert await env.children(env.parent) == []

    _run(scenario())


def test_an_enclosure_entry_naming_a_different_file_object_is_refused() -> None:
    async def scenario() -> None:
        async with _env() as env:
            with pytest.raises(Exception) as caught:
                await env.create(
                    file_object_id=env.fo_solo, enclosure_entry_id=env.entry_id
                )
            assert _status(caught.value) == 422
            assert await env.children(env.parent) == []

    _run(scenario())


def test_a_deleted_child_is_not_silently_resurrected_by_a_retry() -> None:
    async def scenario() -> None:
        async with _env() as env:
            child_id = str((await env.create()).document["_id"])
            await env.service().soft_delete_child(
                await env.principal("project_admin"), child_id
            )
            with pytest.raises(Exception) as caught:
                await env.create()
            assert _status(caught.value) == 409
            assert (await env.doc(child_id))["lifecycle_state"] == "deleted"
            assert len(await env.children(env.parent)) == 1

    _run(scenario())


def test_removing_one_enclosure_keeps_storage_and_child_links_on_the_others() -> None:
    """``remove_enclosure`` rewrites the array through the ``Enclosure`` model;
    an undeclared field was silently stripped from every surviving entry."""
    from rbac_backend.services.document_service import DocumentService

    async def scenario() -> None:
        async with _env() as env:
            child_id = str(
                (await env.create(enclosure_entry_id=env.entry_id)).document["_id"]
            )
            doomed = {
                "id": str(ObjectId()),
                "filename": "x.pdf",
                "filetype": "application/pdf",
                "filesize": 1,
                "uploadedAt": datetime(2026, 9, 3),
                "uploadedBy": "u",
            }
            await env.db.documents.update_one(
                {"_id": env.parent}, {"$push": {"enclosures": doomed}}
            )
            await DocumentService(db=env.db).remove_enclosure(
                str(env.parent), doomed["id"]
            )
            [entry] = (await env.doc(env.parent))["enclosures"]
            assert entry["id"] == env.entry_id
            assert entry["file_object_id"] == env.fo
            assert entry["storage_key"] == "/org-a/proj-a1/enclosures/k"
            assert entry["storage_locations"] == [
                {"provider": "local", "path": "/data/fo.pdf", "status": "ok"}
            ]
            assert entry["child_document_id"] == child_id

    _run(scenario())


def test_dedupe_hit_in_store_path_records_the_new_owner() -> None:
    from rbac_backend.services.file_object_service import FileObjectService

    async def scenario() -> None:
        async with _env() as env:
            result = await FileObjectService(env.db).store_path(
                source_path="/does/not/matter",
                size=4096,
                sha256=env.sha,
                mime_type="application/pdf",
                organization_id=ORG_A,
                project_id=PROJ_A1,
                original_filename="again.pdf",
                storage_key="/k",
                document_type="enclosure",
                document_id=str(env.second_parent),
            )
            assert result["deduped"] is True and result["file_object_id"] == env.fo
            fo = await env.db.file_objects.find_one({"_id": env.fo})
            assert set(fo["document_ids"]) == {str(env.parent), str(env.second_parent)}

    _run(scenario())


# --------------------------------------------------------------------------- #
# 9-11: lifecycle
# --------------------------------------------------------------------------- #


def test_soft_delete_child_keeps_parent_and_entry_and_runs_cleanup() -> None:
    async def scenario() -> None:
        async with _env() as env:
            child_id = str(
                (await env.create(enclosure_entry_id=env.entry_id)).document["_id"]
            )
            parent_before = await env.doc(env.parent)
            await env.service().soft_delete_child(
                await env.principal("project_admin"), child_id
            )
            child = await env.doc(child_id)
            assert child["lifecycle_state"] == "deleted" and child["deletedAt"]
            assert env.cleaned == [child_id]
            parent_after = await env.doc(env.parent)
            assert parent_after["lifecycle_state"] == "active"
            assert parent_after["_revision"] == parent_before["_revision"]
            [entry] = parent_after["enclosures"]
            assert (
                entry["file_object_id"] == env.fo
                and entry["child_document_id"] == child_id
            )
            # A deleted child is not listed.
            listed = await env.service().list_children(
                await env.principal("project_admin"), str(env.parent)
            )
            assert listed == []

    _run(scenario())


def test_restore_child_and_restore_is_refused_while_the_parent_is_deleted() -> None:
    async def scenario() -> None:
        async with _env() as env:
            service = env.service()
            principal = await env.principal("project_admin")
            child_id = str((await env.create()).document["_id"])
            with pytest.raises(Exception) as not_deleted:
                await service.restore_child(principal, child_id)
            assert _status(not_deleted.value) == 409

            await service.soft_delete_child(principal, child_id)
            restored = await service.restore_child(principal, child_id)
            assert restored["lifecycle_state"] == "active"
            row = await env.doc(child_id)
            assert row["lifecycle_state"] == "active" and row.get("deletedAt") is None

            await service.soft_delete_child(principal, child_id)
            await env.db.documents.update_one(
                {"_id": env.parent}, {"$set": {"lifecycle_state": "deleted"}}
            )
            with pytest.raises(Exception) as parent_gone:
                await service.restore_child(principal, child_id)
            assert _status(parent_gone.value) == 409
            assert (await env.doc(child_id))["lifecycle_state"] == "deleted"

    _run(scenario())


def test_parent_delete_cascades_to_children_with_a_restorable_marker() -> None:
    async def scenario() -> None:
        async with _env() as env:
            enclosure = str((await env.create()).document["_id"])
            annexure = str(
                (await env.create(relationship_type="annexure")).document["_id"]
            )
            other = str(
                (await env.create(parent_document_id=str(env.second_parent))).document[
                    "_id"
                ]
            )
            service = env.service()
            deleted = await service.document_service.delete_document(str(env.parent))
            assert deleted
            for child_id in (enclosure, annexure):
                row = await env.doc(child_id)
                assert row["lifecycle_state"] == "deleted"
                assert row["lifecycle_cascade_parent_id"] == str(env.parent)
            assert (await env.doc(other))["lifecycle_state"] == "active"
            assert set(env.cleaned) == {str(env.parent), enclosure, annexure}
            assert set(deleted.cascade["children_deleted"]) == {enclosure, annexure}

    _run(scenario())


def test_a_child_with_active_entity_links_blocks_parent_delete_atomically() -> None:
    from rbac_backend.services.document_service import DocumentDependencyError

    async def scenario() -> None:
        async with _env() as env:
            child_id = str((await env.create()).document["_id"])
            await env.db.entity_document_links.insert_one(
                {
                    "_id": "link-1",
                    "organization_id": ORG_A,
                    "project_id": PROJ_A1,
                    "target_type": "claim",
                    "target_id": "claim-1",
                    "document_id": child_id,
                    "relationship_role": "supporting",
                    "removed_at": None,
                    "created_at": datetime.utcnow(),
                }
            )
            with pytest.raises(DocumentDependencyError):
                await env.service().document_service.delete_document(str(env.parent))
            assert (await env.doc(env.parent))["lifecycle_state"] == "active"
            assert (await env.doc(child_id))["lifecycle_state"] == "active"
            assert env.cleaned == []

    _run(scenario())


# --------------------------------------------------------------------------- #
# 12, 13: hard purge is reference-aware and never deletes bytes
# --------------------------------------------------------------------------- #


def test_purge_requires_soft_delete_first() -> None:
    async def scenario() -> None:
        async with _env() as env:
            child_id = str((await env.create()).document["_id"])
            with pytest.raises(Exception) as caught:
                await env.service().purge_child(
                    await env.principal("project_admin"), child_id
                )
            assert _status(caught.value) == 409
            assert await env.doc(child_id)

    _run(scenario())


def test_purge_refuses_a_child_with_active_entity_links() -> None:
    async def scenario() -> None:
        async with _env() as env:
            service = env.service()
            principal = await env.principal("project_admin")
            child_id = str((await env.create()).document["_id"])
            await service.soft_delete_child(principal, child_id)
            await env.db.entity_document_links.insert_one(
                {
                    "_id": "link-2",
                    "organization_id": ORG_A,
                    "project_id": PROJ_A1,
                    "target_type": "claim",
                    "target_id": "claim-1",
                    "document_id": child_id,
                    "relationship_role": "supporting",
                    "removed_at": None,
                    "created_at": datetime.utcnow(),
                }
            )
            with pytest.raises(Exception) as caught:
                await service.purge_child(principal, child_id)
            assert _status(caught.value) == 409
            assert await env.doc(child_id)

    _run(scenario())


def test_purging_one_child_keeps_a_shared_file_object_referenced() -> None:
    async def scenario() -> None:
        async with _env() as env:
            service = env.service()
            principal = await env.principal("project_admin")
            first = str(
                (await env.create(enclosure_entry_id=env.entry_id)).document["_id"]
            )
            second = str(
                (await env.create(parent_document_id=str(env.second_parent))).document[
                    "_id"
                ]
            )
            await service.soft_delete_child(principal, first)
            result = await service.purge_child(principal, first)

            assert result.purged is True
            assert await env.doc(first) is None
            fo = await env.db.file_objects.find_one({"_id": env.fo})
            assert fo is not None and fo["locations"]  # never physically removed
            assert first not in fo["document_ids"]
            assert second in fo["document_ids"]
            assert result.remaining_references > 0
            assert result.file_object_unreferenced is False
            assert "unreferenced_at" not in fo
            [entry] = (await env.doc(env.parent))["enclosures"]
            assert entry["file_object_id"] == env.fo
            assert (
                "child_document_id" not in entry
            )  # the link to a purged row is cleared

    _run(scenario())


def test_purging_the_last_reference_marks_the_file_object_and_keeps_its_bytes() -> None:
    async def scenario() -> None:
        async with _env() as env:
            service = env.service()
            principal = await env.principal("project_admin")
            child_id = str(
                (await env.create(file_object_id=env.fo_solo)).document["_id"]
            )
            await service.soft_delete_child(principal, child_id)
            result = await service.purge_child(principal, child_id)
            assert result.purged is True
            assert result.remaining_references == 0
            assert result.file_object_unreferenced is True
            fo = await env.db.file_objects.find_one({"_id": env.fo_solo})
            assert fo is not None and fo["locations"] and fo["unreferenced_at"]
            assert fo["document_ids"] == []

    _run(scenario())


def test_a_soft_deleted_document_still_counts_as_a_reference() -> None:
    async def scenario() -> None:
        async with _env() as env:
            service = env.service()
            principal = await env.principal("project_admin")
            child_id = str((await env.create()).document["_id"])
            await env.db.documents.update_one(
                {"_id": env.parent}, {"$set": {"lifecycle_state": "deleted"}}
            )
            await env.db.documents.update_one(
                {"_id": ObjectId(child_id)}, {"$set": {"lifecycle_state": "deleted"}}
            )
            result = await service.purge_child(principal, child_id)
            # The deleted parent's enclosure entry still names the file object.
            assert result.file_object_unreferenced is False
            assert "unreferenced_at" not in await env.db.file_objects.find_one(
                {"_id": env.fo}
            )

    _run(scenario())


# --------------------------------------------------------------------------- #
# 15: audit
# --------------------------------------------------------------------------- #


def test_audit_records_parent_child_creation_without_storage_details() -> None:
    async def scenario() -> None:
        async with _env() as env:
            child_id = str(
                (
                    await env.create(
                        relationship_type="annexure", enclosure_entry_id=env.entry_id
                    )
                ).document["_id"]
            )
            await env.create(
                relationship_type="annexure", enclosure_entry_id=env.entry_id
            )  # replay
            events = await env.db.document_audit_events.find(
                {"event_type": "document.child_created"}
            ).to_list(None)
            assert len(events) == 1
            [event] = events
            assert (
                event["resource_type"] == "document"
                and event["resource_id"] == child_id
            )
            assert event["organization_id"] == ORG_A and event["project_id"] == PROJ_A1
            assert event["actor_id"] == "user-project_admin"
            meta = event["metadata"]
            assert set(meta) <= ALLOWED_AUDIT_KEYS
            assert meta["parent_document_id"] == str(env.parent)
            assert meta["relationship_type"] == "annexure"
            blob = repr(meta)
            for leak in (
                "/data/",
                "storage_key",
                "enclosures/",
                "annexure-a.pdf",
                "Annexure A",
                "locations",
            ):
                assert leak not in blob, leak

    _run(scenario())


def test_lifecycle_transitions_are_audited() -> None:
    async def scenario() -> None:
        async with _env() as env:
            service = env.service()
            principal = await env.principal("project_admin")
            child_id = str((await env.create()).document["_id"])
            await service.soft_delete_child(principal, child_id)
            await service.restore_child(principal, child_id)
            await service.soft_delete_child(principal, child_id)
            await service.purge_child(principal, child_id)
            kinds = [
                e["event_type"]
                for e in await env.db.document_audit_events.find(
                    {"resource_id": child_id}
                )
                .sort("$natural", 1)
                .to_list(None)
            ]
            assert kinds == [
                "document.child_created",
                "document.child_deleted",
                "document.child_restored",
                "document.child_deleted",
                "document.child_purged",
            ]

    _run(scenario())
