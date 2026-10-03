"""Real MongoDB transaction checks for the Bank Guarantee event tracer.

Opt-in only: this suite requires an explicit test-only replica-set URI and
never falls back to the application's configured database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.core.database import get_db
from rbac_backend.core.permissions import Permissions
from rbac_backend.core.security import get_current_user
from rbac_backend.migrations.v20260820_0001_entity_document_links import upgrade
from rbac_backend.models.bank_guarantee import BGExtendRequest, BGReleaseRequest
from rbac_backend.models.document_relationship import DocumentRelationshipInput
from rbac_backend.services.bank_guarantee_service import (
    BankGuaranteeLifecycleError,
    BankGuaranteeService,
)
from rbac_backend.services.document_relationship_service import DocumentRelationshipService
from rbac_backend.routers.bank_guarantees import get_policy, router as bank_guarantee_router
from rbac_backend.tests.selection_fixtures import pin_selection
from rbac_backend.tests.integration.test_claim_document_relationships_mongo import (
    InjectedFailure,
    _AllowPolicy,
    _FailingCollection,
    _FaultDatabase,
)


MONGODB_URI_ENV = "BG_TRACER_MONGODB_URI"


class _TwoRequestBarrierPolicy(_AllowPolicy):
    def __init__(self, permission: str) -> None:
        self.permission = permission
        self.arrivals = 0
        self._lock = asyncio.Lock()
        self._release = asyncio.Event()

    async def authorize_document(
        self, actor: Any, permission: str, document: Any, **kwargs: Any
    ) -> None:
        if permission != self.permission:
            return
        async with self._lock:
            self.arrivals += 1
            if self.arrivals == 2:
                self._release.set()
        await asyncio.wait_for(self._release.wait(), timeout=5)


def _bg_app(database: Any, policy: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(bank_guarantee_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_current_user] = _actor
    app.dependency_overrides[get_policy] = lambda: policy
    # The selection the browser sends for the seeded BG: its own project.
    pin_selection(app, database, "org-1", "project-1")
    return app


def _actor() -> Any:
    return SimpleNamespace(
        id="bg-tracer-verifier",
        organization_id="org-1",
    )


async def _new_database() -> tuple[Any, Any]:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a test-only MongoDB replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await client.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        client.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    database = client[f"bg_tracer_verification_{uuid.uuid4().hex}"]
    await upgrade(database, dry_run=False)
    await database.bank_guarantee_events.create_index(
        [
            ("organization_id", 1),
            ("project_id", 1),
            ("bank_guarantee_id", 1),
            ("sequence", 1),
        ],
        name="uq_bank_guarantee_event_sequence",
        unique=True,
    )
    return client, database


async def _seed(database: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    parent = {
        "_id": "bg-1",
        "bg_number": "BG-REAL-001",
        "organization_id": "org-1",
        "project_id": "project-1",
        "bg_status": "valid",
        "bg_expiry_date": now,
        "current_revision": 0,
        "event_sequence": 1,
    }
    event = {
        "_id": "bg-event-original",
        "bank_guarantee_id": "bg-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "event_type": "original",
        "event_date": now,
        "sequence": 1,
        "created_at": now,
        "created_by": "seed",
    }
    await database.bank_guarantees.insert_one(parent)
    await database.bank_guarantee_events.insert_one(event)
    await database.documents.insert_one(
        {
            "_id": "doc-1",
            "filename": "original-bg.pdf",
            "organization_id": "org-1",
            "project_id": "project-1",
            "processing_status": "metadata_extracted",
            "lifecycle_state": "active",
        }
    )
    return parent, event


@pytest.mark.asyncio
async def test_real_mongo_concurrent_extension_http_has_one_winner_and_one_conflict() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        policy = _TwoRequestBarrierPolicy(Permissions.BG_EXTEND)
        transport = httpx.ASGITransport(
            app=_bg_app(database, policy), raise_app_exceptions=False
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            first, second = await asyncio.gather(
                http.post(
                    "/api/bank-guarantees/bg-1/extend",
                    json={"revised_expiry_date": "2027-08-01T00:00:00Z"},
                ),
                http.post(
                    "/api/bank-guarantees/bg-1/extend",
                    json={"revised_expiry_date": "2028-08-01T00:00:00Z"},
                ),
            )

        assert sorted([first.status_code, second.status_code]) == [200, 409]
        stored = await database.bank_guarantees.find_one({"_id": "bg-1"})
        assert stored["current_revision"] == 1
        assert stored["event_sequence"] == 2
        assert await database.bank_guarantee_events.count_documents(
            {"bank_guarantee_id": "bg-1", "event_type": "extension"}
        ) == 1
        assert await database.bg_extension_history.count_documents(
            {"bg_id": "bg-1"}
        ) == 1
        assert await database.audit_events.count_documents(
            {"action": "bank_guarantee.extended"}
        ) == 1
        assert await database.entity_document_links.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_concurrent_release_http_has_one_winner_and_one_conflict() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        policy = _TwoRequestBarrierPolicy(Permissions.BG_RELEASE)
        transport = httpx.ASGITransport(
            app=_bg_app(database, policy), raise_app_exceptions=False
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            first, second = await asyncio.gather(
                http.post(
                    "/api/bank-guarantees/bg-1/release",
                    json={"release_letter_reference": "REL/REAL/A"},
                ),
                http.post(
                    "/api/bank-guarantees/bg-1/release",
                    json={"release_letter_reference": "REL/REAL/B"},
                ),
            )

        assert sorted([first.status_code, second.status_code]) == [200, 409]
        stored = await database.bank_guarantees.find_one({"_id": "bg-1"})
        assert stored["bg_status"] == "released"
        assert stored["event_sequence"] == 2
        assert await database.bank_guarantee_events.count_documents(
            {"bank_guarantee_id": "bg-1", "event_type": "release"}
        ) == 1
        assert await database.audit_events.count_documents(
            {"action": "bank_guarantee.released"}
        ) == 1
        assert await database.entity_document_links.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_concurrent_identical_event_links_are_idempotent() -> None:
    client, database = await _new_database()
    try:
        _, event = await _seed(database)
        service = DocumentRelationshipService(database, policy=_AllowPolicy())
        request = [
            DocumentRelationshipInput(
                document_id="doc-1",
                relationship_role="original_bg",
            )
        ]

        first, second = await asyncio.gather(
            service.link_batch(
                _actor(),
                "bank_guarantee_event",
                event["_id"],
                request,
                idempotency_key="bg-real-link",
            ),
            service.link_batch(
                _actor(),
                "bank_guarantee_event",
                event["_id"],
                request,
                idempotency_key="bg-real-link",
            ),
        )

        assert first[0].id == second[0].id
        assert await database.entity_document_links.count_documents(
            {"target_type": "bank_guarantee_event", "removed_at": None}
        ) == 1
        assert await database.audit_events.count_documents(
            {"action": "document_relationship.linked"}
        ) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_extension_rolls_back_event_and_history_when_parent_update_fails() -> None:
    client, database = await _new_database()
    try:
        parent, _ = await _seed(database)
        fault_database = _FaultDatabase(
            database,
            bank_guarantees=_FailingCollection(
                database.bank_guarantees,
                fail_update=lambda update: "bg_expiry_date" in update.get("$set", {}),
            ),
        )

        with pytest.raises(InjectedFailure, match="injected update failure"):
            await BankGuaranteeService(fault_database).extend(
                parent,
                BGExtendRequest(
                    revised_expiry_date=datetime(2027, 8, 1, tzinfo=timezone.utc),
                    extension_letter_reference="EXT/REAL/1",
                ),
                _actor(),
            )

        stored = await database.bank_guarantees.find_one({"_id": "bg-1"})
        assert stored["event_sequence"] == 1
        assert stored["current_revision"] == 0
        assert await database.bank_guarantee_events.count_documents({}) == 1
        assert await database.bg_extension_history.count_documents({}) == 0
        assert await database.audit_events.count_documents(
            {"action": "bank_guarantee.extended"}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_release_rolls_back_event_and_parent_when_audit_fails() -> None:
    client, database = await _new_database()
    try:
        parent, _ = await _seed(database)
        fault_database = _FaultDatabase(
            database,
            audit_events=_FailingCollection(
                database.audit_events,
                fail_insert=lambda row: row.get("action") == "bank_guarantee.released",
            ),
        )

        with pytest.raises(InjectedFailure, match="injected insert failure"):
            await BankGuaranteeService(fault_database).release(
                parent,
                _actor(),
                BGReleaseRequest(
                    release_date=datetime(2026, 8, 21, tzinfo=timezone.utc),
                    release_letter_reference="REL/REAL/1",
                    remarks="Returned",
                ),
            )

        stored = await database.bank_guarantees.find_one({"_id": "bg-1"})
        assert stored["bg_status"] == "valid"
        assert stored["event_sequence"] == 1
        assert await database.bank_guarantee_events.count_documents({}) == 1
        assert await database.audit_events.count_documents(
            {"action": "bank_guarantee.released"}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_parent_delete_preserves_immutable_event_and_relationship() -> None:
    client, database = await _new_database()
    try:
        parent, event = await _seed(database)
        await DocumentRelationshipService(database, policy=_AllowPolicy()).link_batch(
            _actor(),
            "bank_guarantee_event",
            event["_id"],
            [
                DocumentRelationshipInput(
                    document_id="doc-1",
                    relationship_role="original_bg",
                )
            ],
        )

        with pytest.raises(BankGuaranteeLifecycleError, match="immutable event history"):
            await BankGuaranteeService(database).delete(parent, _actor())

        assert await database.bank_guarantees.count_documents({"_id": "bg-1"}) == 1
        assert await database.bank_guarantee_events.count_documents({"_id": event["_id"]}) == 1
        assert await database.entity_document_links.count_documents(
            {"target_id": event["_id"], "removed_at": None}
        ) == 1
        assert await database.documents.count_documents({"_id": "doc-1"}) == 1
    finally:
        await client.drop_database(database.name)
        client.close()
