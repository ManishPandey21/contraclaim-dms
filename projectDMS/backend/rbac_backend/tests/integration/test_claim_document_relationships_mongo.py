"""Real MongoDB transaction checks for the Foundation + Claim tracer.

These tests are deliberately opt-in: they require an explicit URI for a
test-only replica set and never fall back to the application's configured
database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.core.permissions import Permissions
from rbac_backend.core.security import get_current_user
from rbac_backend.migrations.v20260820_0001_entity_document_links import upgrade
from rbac_backend.models.document_relationship import DocumentRelationshipInput
from rbac_backend.tests.selection_fixtures import pin_selection
from rbac_backend.routers.document_relationships import (
    get_document_relationship_service,
    router as document_relationship_router,
)
from rbac_backend.services.document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)
from rbac_backend.services.document_service import (
    DocumentDependencyError,
    DocumentService,
)


MONGODB_URI_ENV = "CLAIM_TRACER_MONGODB_URI"


class InjectedFailure(RuntimeError):
    """Deterministic failure injected at a transactional persistence boundary."""


class _AllowPolicy:
    async def authorize_document(
        self, actor: Any, permission: str, document: Any, **kwargs: Any
    ) -> None:
        return None


class _BlockingDocumentPolicy(_AllowPolicy):
    def __init__(self, document_id: str) -> None:
        self.document_id = document_id
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def authorize_document(
        self, actor: Any, permission: str, document: Any, **kwargs: Any
    ) -> None:
        if (
            permission == Permissions.DOCUMENT_VIEW
            and str(document.get("_id") or "") == self.document_id
        ):
            self.reached.set()
            await self.release.wait()


class _BlockingPermissionPolicy(_AllowPolicy):
    def __init__(self, permission: str) -> None:
        self.permission = permission
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def authorize_document(
        self, actor: Any, permission: str, document: Any, **kwargs: Any
    ) -> None:
        if permission == self.permission:
            self.reached.set()
            await self.release.wait()


class _FailingCollection:
    def __init__(
        self,
        collection: Any,
        *,
        fail_insert: Callable[[dict[str, Any]], bool] | None = None,
        fail_update: Callable[[dict[str, Any]], bool] | None = None,
        fail_delete: bool = False,
    ) -> None:
        self._collection = collection
        self._fail_insert = fail_insert
        self._fail_update = fail_update
        self._fail_delete = fail_delete

    def __getattr__(self, name: str) -> Any:
        return getattr(self._collection, name)

    async def insert_one(self, document: dict[str, Any], *args: Any, **kwargs: Any):
        if self._fail_insert and self._fail_insert(document):
            raise InjectedFailure("injected insert failure")
        return await self._collection.insert_one(document, *args, **kwargs)

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ):
        if self._fail_update and self._fail_update(update):
            raise InjectedFailure("injected update failure")
        return await self._collection.update_one(query, update, *args, **kwargs)

    async def delete_one(self, query: dict[str, Any], *args: Any, **kwargs: Any):
        if self._fail_delete:
            raise InjectedFailure("injected delete failure")
        return await self._collection.delete_one(query, *args, **kwargs)


class _BlockingLinkCollection:
    def __init__(self, collection: Any) -> None:
        self._collection = collection
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self._blocked = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._collection, name)

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ):
        if "$setOnInsert" in update and not self._blocked:
            self._blocked = True
            self.reached.set()
            await self.release.wait()
        return await self._collection.update_one(query, update, *args, **kwargs)


class _FaultDatabase:
    def __init__(self, database: Any, **collections: Any) -> None:
        self._database = database
        self.client = database.client
        self._collections = collections

    def __getattr__(self, name: str) -> Any:
        if name in self._collections:
            return self._collections[name]
        return getattr(self._database, name)

    def __getitem__(self, name: str) -> Any:
        if name in self._collections:
            return self._collections[name]
        return self._database[name]


def _actor() -> Any:
    return SimpleNamespace(id="claim-tracer-verifier")


async def _new_database() -> tuple[Any, Any]:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a test-only MongoDB replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await client.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        client.close()
        pytest.fail("CLAIM_TRACER_MONGODB_URI is not a writable replica set")
    database = client[f"claim_tracer_verification_{uuid.uuid4().hex}"]
    await upgrade(database, dry_run=False)
    return client, database


async def _seed_claim(
    database: Any,
    *,
    document_ids: tuple[str, ...] = ("doc-1",),
) -> None:
    await database.claims.insert_one(
        {
            "_id": "claim-1",
            "claim_ref": "CLM-REAL-001",
            "title": "Real Mongo transaction verification",
            "organization_id": "org-1",
            "project_id": "project-1",
            "evidence_frozen_at": None,
        }
    )
    for index, document_id in enumerate(document_ids, start=1):
        version_id = f"version-{index}"
        await database.documents.insert_one(
            {
                "_id": document_id,
                "filename": f"{document_id}.pdf",
                "organization_id": "org-1",
                "project_id": "project-1",
                "processing_status": "metadata_extracted",
                "lifecycle_state": "active",
                "current_version_id": version_id,
            }
        )
        await database.document_versions.insert_one(
            {
                "_id": version_id,
                "document_id": document_id,
                "version_number": index,
                "is_current": True,
                "file_object_id": f"file-{index}",
            }
        )


def _service(database: Any) -> DocumentRelationshipService:
    return DocumentRelationshipService(database, policy=_AllowPolicy())


def _link(document_id: str, role: str = "notice") -> DocumentRelationshipInput:
    return DocumentRelationshipInput(
        document_id=document_id,
        relationship_role=role,
    )


@pytest.mark.asyncio
async def test_real_mongo_concurrent_identical_links_are_idempotent_and_audited_once() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database)
        service = _service(database)
        app = FastAPI()
        app.include_router(document_relationship_router, prefix="/api")
        app.dependency_overrides[get_current_user] = _actor
        app.dependency_overrides[get_document_relationship_service] = lambda: service
        # The selection the browser sends: the claim's own project (CL-4A).
        pin_selection(app, database, "org-1", "project-1")
        payload = {
            "links": [{"document_id": "doc-1", "relationship_role": "notice"}],
            "idempotency_key": "same-request",
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://claim-tracer.test",
        ) as request_client:
            first, second = await asyncio.gather(
                request_client.post(
                    "/api/entities/claim/claim-1/document-links:batch",
                    json=payload,
                ),
                request_client.post(
                    "/api/entities/claim/claim-1/document-links:batch",
                    json=payload,
                ),
            )

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["links"][0]["_id"] == second.json()["links"][0]["_id"]
        assert await database.entity_document_links.count_documents({"removed_at": None}) == 1
        assert await database.audit_events.count_documents(
            {"action": "document_relationship.linked"}
        ) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_link_cannot_commit_after_claim_freeze() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database, document_ids=("doc-1", "doc-2"))
        await _service(database).link_batch(
            _actor(),
            "claim",
            "claim-1",
            [_link("doc-2", "supporting_document")],
        )
        barrier = _BlockingDocumentPolicy("doc-1")
        pending_link = asyncio.create_task(
            DocumentRelationshipService(database, policy=barrier).link_batch(
                _actor(), "claim", "claim-1", [_link("doc-1")]
            )
        )
        await asyncio.wait_for(barrier.reached.wait(), timeout=5)
        await _service(database).freeze(
            _actor(), "claim", "claim-1", reason="race verification"
        )
        barrier.release.set()

        with pytest.raises(DocumentRelationshipError, match="frozen|changed"):
            await pending_link
        active = await database.entity_document_links.find(
            {"target_type": "claim", "target_id": "claim-1", "removed_at": None}
        ).to_list(length=None)
        assert len(active) == 1
        assert active[0]["document_id"] == "doc-2"
        assert active[0]["document_version_id"] == "version-2"
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_link_cannot_commit_after_claim_deletion() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database)
        barrier = _BlockingDocumentPolicy("doc-1")
        pending_link = asyncio.create_task(
            DocumentRelationshipService(database, policy=barrier).link_batch(
                _actor(), "claim", "claim-1", [_link("doc-1")]
            )
        )
        await asyncio.wait_for(barrier.reached.wait(), timeout=5)
        await _service(database).delete_target(
            _actor(), "claim", "claim-1", reason="race verification"
        )
        barrier.release.set()

        with pytest.raises(DocumentRelationshipError, match="changed|not found"):
            await pending_link
        assert await database.claims.count_documents({"_id": "claim-1"}) == 0
        assert await database.entity_document_links.count_documents(
            {"target_type": "claim", "target_id": "claim-1", "removed_at": None}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_link_cannot_commit_after_document_deletion() -> None:
    client, database = await _new_database()
    try:
        document_oid = ObjectId()
        document_id = str(document_oid)
        await _seed_claim(database, document_ids=(document_oid,))
        barrier = _BlockingDocumentPolicy(document_id)
        pending_link = asyncio.create_task(
            DocumentRelationshipService(database, policy=barrier).link_batch(
                _actor(), "claim", "claim-1", [_link(document_id)]
            )
        )
        await asyncio.wait_for(barrier.reached.wait(), timeout=5)

        document_service = DocumentService(db=database)

        async def no_cleanup(_: str) -> dict[str, Any]:
            return {}

        document_service.cascade_delete_cleanup = no_cleanup
        deleted = await document_service.delete_document(document_id)
        assert deleted
        barrier.release.set()

        with pytest.raises(DocumentRelationshipError, match="Document|changed|consumable"):
            await pending_link
        assert await database.entity_document_links.count_documents(
            {"document_id": document_id, "removed_at": None}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_document_deletion_rolls_back_when_link_commits_during_overlap() -> None:
    client, database = await _new_database()
    try:
        document_oid = ObjectId()
        document_id = str(document_oid)
        await _seed_claim(database, document_ids=(document_oid,))
        blocking_links = _BlockingLinkCollection(database.entity_document_links)
        pending_link = asyncio.create_task(
            _service(
                _FaultDatabase(database, entity_document_links=blocking_links)
            ).link_batch(_actor(), "claim", "claim-1", [_link(document_id)])
        )
        await asyncio.wait_for(blocking_links.reached.wait(), timeout=5)

        document_service = DocumentService(db=database)

        async def no_cleanup(_: str) -> dict[str, Any]:
            return {}

        document_service.cascade_delete_cleanup = no_cleanup
        pending_delete = asyncio.create_task(
            document_service.delete_document(document_id)
        )
        await asyncio.sleep(0.05)
        blocking_links.release.set()

        linked = await pending_link
        with pytest.raises(DocumentDependencyError):
            await pending_delete
        document = await database.documents.find_one({"_id": document_oid})
        assert document["lifecycle_state"] == "active"
        assert await database.entity_document_links.count_documents(
            {"_id": linked[0].id, "removed_at": None}
        ) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_claim_deletion_cannot_commit_after_freeze() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database)
        barrier = _BlockingPermissionPolicy(Permissions.CLAIM_DELETE)
        pending_delete = asyncio.create_task(
            DocumentRelationshipService(database, policy=barrier).delete_target(
                _actor(), "claim", "claim-1", reason="race verification"
            )
        )
        await asyncio.wait_for(barrier.reached.wait(), timeout=5)
        await _service(database).freeze(
            _actor(), "claim", "claim-1", reason="race verification"
        )
        barrier.release.set()

        with pytest.raises(DocumentRelationshipError, match="changed|frozen"):
            await pending_delete
        claim = await database.claims.find_one({"_id": "claim-1"})
        assert claim is not None
        assert claim["evidence_frozen_at"] is not None
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_overlapping_link_and_freeze_serialize_without_partial_evidence() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database)
        blocking_links = _BlockingLinkCollection(database.entity_document_links)
        pending_link = asyncio.create_task(
            _service(
                _FaultDatabase(database, entity_document_links=blocking_links)
            ).link_batch(_actor(), "claim", "claim-1", [_link("doc-1")])
        )
        await asyncio.wait_for(blocking_links.reached.wait(), timeout=5)
        pending_freeze = asyncio.create_task(
            _service(database).freeze(
                _actor(), "claim", "claim-1", reason="overlap verification"
            )
        )
        await asyncio.sleep(0.05)
        blocking_links.release.set()

        linked, frozen = await asyncio.gather(pending_link, pending_freeze)
        assert linked[0].id == frozen[0].id
        stored = await database.entity_document_links.find_one(
            {"_id": linked[0].id}
        )
        assert stored["document_version_id"] == "version-1"
        assert stored["frozen_at"] is not None
        claim = await database.claims.find_one({"_id": "claim-1"})
        assert claim["evidence_frozen_at"] is not None
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_overlapping_link_and_deletion_leave_no_orphan_relationship() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database)
        blocking_links = _BlockingLinkCollection(database.entity_document_links)
        pending_link = asyncio.create_task(
            _service(
                _FaultDatabase(database, entity_document_links=blocking_links)
            ).link_batch(_actor(), "claim", "claim-1", [_link("doc-1")])
        )
        await asyncio.wait_for(blocking_links.reached.wait(), timeout=5)
        pending_delete = asyncio.create_task(
            _service(database).delete_target(
                _actor(), "claim", "claim-1", reason="overlap verification"
            )
        )
        await asyncio.sleep(0.05)
        blocking_links.release.set()

        linked, _ = await asyncio.gather(pending_link, pending_delete)
        assert await database.claims.count_documents({"_id": "claim-1"}) == 0
        assert await database.entity_document_links.count_documents(
            {"target_type": "claim", "target_id": "claim-1", "removed_at": None}
        ) == 0
        stored = await database.entity_document_links.find_one(
            {"_id": linked[0].id}
        )
        assert stored["removed_at"] is not None
        assert await database.audit_events.count_documents(
            {"action": "document_relationship.unlinked"}
        ) == 1
        assert await database.audit_events.count_documents(
            {"action": "claim.deleted"}
        ) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_link_and_audit_failures_roll_back_together() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database)
        failing_audit = _FailingCollection(
            database.audit_events,
            fail_insert=lambda event: event.get("action") == "document_relationship.linked",
        )
        with pytest.raises(InjectedFailure, match="injected insert failure"):
            await _service(_FaultDatabase(database, audit_events=failing_audit)).link_batch(
                _actor(), "claim", "claim-1", [_link("doc-1")]
            )
        assert await database.entity_document_links.count_documents({}) == 0
        assert await database.audit_events.count_documents({}) == 0

        failing_links = _FailingCollection(
            database.entity_document_links,
            fail_update=lambda update: True,
        )
        with pytest.raises(InjectedFailure, match="injected update failure"):
            await _service(
                _FaultDatabase(database, entity_document_links=failing_links)
            ).link_batch(_actor(), "claim", "claim-1", [_link("doc-1")])
        assert await database.entity_document_links.count_documents({}) == 0
        assert await database.audit_events.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_unlink_failure_does_not_partially_commit() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database)
        created = await _service(database).link_batch(
            _actor(), "claim", "claim-1", [_link("doc-1")]
        )
        failing_audit = _FailingCollection(
            database.audit_events,
            fail_insert=lambda event: event.get("action") == "document_relationship.unlinked",
        )
        with pytest.raises(InjectedFailure, match="injected insert failure"):
            await _service(_FaultDatabase(database, audit_events=failing_audit)).remove(
                _actor(), created[0].id, reason="rollback verification", expected_revision=1
            )

        stored = await database.entity_document_links.find_one({"_id": created[0].id})
        assert stored["removed_at"] is None
        assert stored["_revision"] == 1
        assert await database.audit_events.count_documents(
            {"action": "document_relationship.unlinked"}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_claim_deletion_failure_rolls_back_cleanup_and_audits() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database)
        created = await _service(database).link_batch(
            _actor(), "claim", "claim-1", [_link("doc-1")]
        )
        failing_links = _FailingCollection(
            database.entity_document_links,
            fail_update=lambda update: "$set" in update
            and "removed_at" in update["$set"],
        )
        with pytest.raises(InjectedFailure, match="injected update failure"):
            await _service(
                _FaultDatabase(database, entity_document_links=failing_links)
            ).delete_target(_actor(), "claim", "claim-1", reason="rollback verification")

        assert await database.claims.count_documents({"_id": "claim-1"}) == 1
        stored = await database.entity_document_links.find_one({"_id": created[0].id})
        assert stored["removed_at"] is None
        assert stored["_revision"] == 1
        assert await database.audit_events.count_documents(
            {"action": {"$in": ["document_relationship.unlinked", "claim.deleted"]}}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_freeze_pins_all_supporters_or_none() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database, document_ids=("doc-1", "doc-2"))
        service = _service(database)
        await service.link_batch(
            _actor(),
            "claim",
            "claim-1",
            [_link("doc-1", "notice"), _link("doc-2", "supporting_document")],
        )

        frozen = await service.freeze(
            _actor(), "claim", "claim-1", reason="complete freeze verification"
        )
        assert {link.document_version_id for link in frozen} == {"version-1", "version-2"}
        assert await database.entity_document_links.count_documents(
            {"frozen_at": {"$ne": None}, "document_version_id": {"$ne": None}}
        ) == 2
        assert await database.claims.count_documents(
            {"_id": "claim-1", "evidence_frozen_at": {"$ne": None}}
        ) == 1

        await client.drop_database(database.name)
        database = client[f"claim_tracer_verification_{uuid.uuid4().hex}"]
        await upgrade(database, dry_run=False)
        await _seed_claim(database, document_ids=("doc-1", "doc-2"))
        service = _service(database)
        await service.link_batch(
            _actor(),
            "claim",
            "claim-1",
            [_link("doc-1", "notice"), _link("doc-2", "supporting_document")],
        )
        await database.documents.update_one(
            {"_id": "doc-2"}, {"$set": {"current_version_id": "missing-version"}}
        )

        with pytest.raises(
            DocumentRelationshipError,
            match="Current Document version is missing",
        ):
            await service.freeze(
                _actor(), "claim", "claim-1", reason="failed version resolution"
            )
        assert await database.entity_document_links.count_documents(
            {"document_version_id": {"$ne": None}}
        ) == 0
        assert await database.claims.count_documents(
            {"_id": "claim-1", "evidence_frozen_at": {"$ne": None}}
        ) == 0

        await database.documents.update_one(
            {"_id": "doc-2"}, {"$set": {"current_version_id": "version-2"}}
        )
        failing_claims = _FailingCollection(
            database.claims,
            fail_update=lambda update: "evidence_frozen_at" in update.get("$set", {}),
        )
        with pytest.raises(DocumentRelationshipError, match="injected update failure"):
            await _service(_FaultDatabase(database, claims=failing_claims)).freeze(
                _actor(), "claim", "claim-1", reason="transaction rollback verification"
            )
        assert await database.entity_document_links.count_documents(
            {"document_version_id": {"$ne": None}}
        ) == 0
        assert await database.claims.count_documents(
            {"_id": "claim-1", "evidence_frozen_at": {"$ne": None}}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_freeze_rejects_version_without_immutable_file_artifact() -> None:
    client, database = await _new_database()
    try:
        await _seed_claim(database)
        created = await _service(database).link_batch(
            _actor(), "claim", "claim-1", [_link("doc-1")]
        )
        await database.document_versions.update_one(
            {"_id": "version-1"},
            {"$unset": {"file_object_id": ""}},
        )

        with pytest.raises(DocumentRelationshipError, match="version|artifact"):
            await _service(database).freeze(
                _actor(), "claim", "claim-1", reason="artifact verification"
            )

        claim = await database.claims.find_one({"_id": "claim-1"})
        assert claim["evidence_frozen_at"] is None
        stored = await database.entity_document_links.find_one({"_id": created[0].id})
        assert stored["document_version_id"] is None
        assert stored["frozen_at"] is None
    finally:
        await client.drop_database(database.name)
        client.close()
