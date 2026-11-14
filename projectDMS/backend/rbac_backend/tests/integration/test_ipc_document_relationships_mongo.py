"""Real MongoDB transaction checks for the IPC relationship tracer.

Opt-in only: this suite requires an explicit test-only replica-set URI.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.migrations.v20260820_0001_entity_document_links import upgrade
from rbac_backend.models.document_relationship import DocumentRelationshipInput
from rbac_backend.services.document_relationship_service import DocumentRelationshipService
from rbac_backend.services.document_service import DocumentDependencyError, DocumentService
from rbac_backend.tests.integration.test_claim_document_relationships_mongo import (
    InjectedFailure,
    _AllowPolicy,
    _FailingCollection,
    _FaultDatabase,
)


MONGODB_URI_ENV = "IPC_TRACER_MONGODB_URI"


async def _new_database() -> tuple[Any, Any]:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a test-only MongoDB replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await client.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        client.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    database = client[f"ipc_tracer_verification_{uuid.uuid4().hex}"]
    await upgrade(database, dry_run=False)
    return client, database


async def _seed(database: Any) -> None:
    await database.ipc_bills.insert_one(
        {
            "_id": "ipc-1",
            "ipc_number": "IPC-REAL-001",
            "organization_id": "org-1",
            "project_id": "project-1",
            "status": "submitted",
        }
    )
    await database.documents.insert_one(
        {
            "_id": "doc-1",
            "filename": "invoice.pdf",
            "organization_id": "org-1",
            "project_id": "project-1",
            "processing_status": "metadata_extracted",
            "lifecycle_state": "active",
        }
    )


def _actor() -> Any:
    return SimpleNamespace(id="ipc-tracer-verifier")


@pytest.mark.asyncio
async def test_real_mongo_concurrent_identical_ipc_links_are_idempotent() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        service = DocumentRelationshipService(database, policy=_AllowPolicy())
        request = [DocumentRelationshipInput(document_id="doc-1", relationship_role="invoice")]

        first, second = await asyncio.gather(
            service.link_batch(_actor(), "ipc_bill", "ipc-1", request, idempotency_key="ipc-real-1"),
            service.link_batch(_actor(), "ipc_bill", "ipc-1", request, idempotency_key="ipc-real-1"),
        )

        assert first[0].id == second[0].id
        assert await database.entity_document_links.count_documents({"removed_at": None}) == 1
        assert await database.audit_events.count_documents(
            {"action": "document_relationship.linked"}
        ) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_ipc_delete_rolls_back_cleanup_when_audit_fails() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        service = DocumentRelationshipService(database, policy=_AllowPolicy())
        await service.link_batch(
            _actor(),
            "ipc_bill",
            "ipc-1",
            [DocumentRelationshipInput(document_id="doc-1", relationship_role="invoice")],
        )
        fault_db = _FaultDatabase(
            database,
            audit_events=_FailingCollection(
                database.audit_events,
                fail_insert=lambda row: row.get("action") == "ipc_bill.deleted",
            ),
        )

        with pytest.raises(InjectedFailure):
            await DocumentRelationshipService(fault_db, policy=_AllowPolicy()).delete_target(
                _actor(), "ipc_bill", "ipc-1", reason="test rollback"
            )

        assert await database.ipc_bills.count_documents({"_id": "ipc-1"}) == 1
        assert await database.entity_document_links.count_documents(
            {"target_type": "ipc_bill", "target_id": "ipc-1", "removed_at": None}
        ) == 1
        assert await database.audit_events.count_documents(
            {"action": "document_relationship.unlinked"}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_document_delete_is_blocked_by_legacy_ipc_membership() -> None:
    client, database = await _new_database()
    try:
        document_id = ObjectId()
        await database.documents.insert_one(
            {
                "_id": document_id,
                "organization_id": "org-1",
                "project_id": "project-1",
                "processing_status": "metadata_extracted",
                "lifecycle_state": "active",
                "_revision": 1,
            }
        )
        await database.ipc_bills.insert_one(
            {
                "_id": "ipc-legacy",
                "organization_id": "org-1",
                "project_id": "project-1",
                "linked_document_ids": [str(document_id)],
            }
        )

        with pytest.raises(DocumentDependencyError):
            await DocumentService(db=database).delete_document(
                str(document_id), expected_revision=1
            )

        stored = await database.documents.find_one({"_id": document_id})
        assert stored["lifecycle_state"] == "active"
    finally:
        await client.drop_database(database.name)
        client.close()
