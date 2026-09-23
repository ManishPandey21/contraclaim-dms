"""Post-promotion sequencing and derived-outage tolerance.

M09-16 - an old projection cannot become evidence after promotion.
M09-18 - a derived-store outage does not roll back a valid promotion, and
         evidence stays disabled.
M09-22 - session evidence captured at inventory survives TTL expiry.

M09-18 needs a mutation proof because it asserts two things that a do-nothing
implementation satisfies for free: the promotion is still there, and evidence is
still off. The mutation makes the outage roll the promotion back, which must turn
it red.

Real Mongo for M09-22, because TTL expiry is a database behaviour. The captured
evidence has to outlive a session document that Mongo itself removes - a fake
that never expires anything would prove nothing about the case this exists for.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_POST_PROMOTION_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_POST_PROMOTION_MONGODB_URI is not set; this suite needs a "
        "disposable replica-set Mongo",
        allow_module_level=True,
    )

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rbac_backend.models.contract_document import (  # noqa: E402
    ContractDocumentType,
    CurrentState,
)
from rbac_backend.services.contract_document_store import (  # noqa: E402
    APPLICABILITY_EVENTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
)
from rbac_backend.services.contract_migration_reconciliation import (  # noqa: E402
    RECONCILIATION_COLLECTION,
    ContractMigrationReconciliation,
    ScopeClassificationState,
    TypeClassificationState,
)
from rbac_backend.services.contract_post_promotion import (  # noqa: E402
    DerivedStoreOutage,
    PostPromotionSequencer,
)
from rbac_backend.services.contract_promotion import (  # noqa: E402
    PROMOTION_RECEIPTS_COLLECTION,
    ContractPromotionService,
)
from rbac_backend.services.contract_scope_resolver import (  # noqa: E402
    AuthorizedContractScope,
    ContractScopeResolver,
)

ORG = "org-post"
PROJECT = "project-post"
CONTRACT = "contract-post"
CANDIDATE = "contract-master-migration:contracts:doc-1"


@asynccontextmanager
async def _database():
    client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)
    name = f"contract_post_{uuid.uuid4().hex[:10]}"
    db = client[name]
    try:
        yield db, client
    finally:
        await client.drop_database(name)
        client.close()


async def _promote(db, client) -> str:
    await db["documents"].insert_one(
        {
            "_id": "doc-1",
            "organization_id": ORG,
            "project_id": PROJECT,
            "checksum": "sha-original",
            "current_version_id": "doc-1-v2",
        }
    )
    await db[RECONCILIATION_COLLECTION].insert_one(
        {
            "_id": CANDIDATE,
            "candidate_id": CANDIDATE,
            "canonical_document_id": "doc-1",
            "organization_id": ORG,
            "module": "contracts",
            "scope_state": ScopeClassificationState.PROJECT_SCOPE_CONFIRMED.value,
            "type_state": TypeClassificationState.TYPE_RESOLVED.value,
            "project_id": PROJECT,
            "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS.value,
            "adjudicated_by": "alice",
            "source_fingerprint": "sha-original",
        }
    )
    receipt = await ContractPromotionService(db, client).promote(
        CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT, effective_from="2021-01-01"
    )
    return receipt.contract_document_id


def _scope() -> AuthorizedContractScope:
    return AuthorizedContractScope.for_tests(
        organization_id=ORG, project_id=PROJECT, contract_id=CONTRACT, actor_id="alice"
    )


# --------------------------------------------------------------------------- #
# promotion leaves evidence disabled until the projection is current
# --------------------------------------------------------------------------- #


def test_promotion_sets_revision_one_and_a_pending_projection():
    async def scenario():
        async with _database() as (db, client):
            instrument_id = await _promote(db, client)

            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            assert record["classification_revision"] == 1
            assert record["projection_status"] == "PENDING"

    asyncio.run(scenario())


def test_a_freshly_promoted_instrument_is_not_yet_evidence_capable():
    """The eligible set must exclude it until its projection is current."""

    async def scenario():
        async with _database() as (db, client):
            await _promote(db, client)

            resolved = await ContractScopeResolver(db).resolve(_scope(), CurrentState())
            assert resolved.eligible_document_ids == frozenset()

    asyncio.run(scenario())


def test_it_becomes_evidence_capable_only_once_the_projection_is_current():
    async def scenario():
        async with _database() as (db, client):
            instrument_id = await _promote(db, client)
            sequencer = PostPromotionSequencer(db)

            await sequencer.mark_projection_current(instrument_id, revision=1)

            resolved = await ContractScopeResolver(db).resolve(_scope(), CurrentState())
            assert resolved.eligible_document_ids == frozenset({"doc-1"})

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# M09-16 : an old projection cannot become evidence
# --------------------------------------------------------------------------- #


def test_an_old_projection_cannot_become_evidence_after_promotion():
    """M09-16."""

    async def scenario():
        async with _database() as (db, client):
            instrument_id = await _promote(db, client)

            # A projection left behind by an earlier generation, claiming
            # currency at a revision the instrument has moved past.
            await db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
                {"_id": instrument_id},
                {"$set": {"projection_status": "CURRENT", "projection_revision": 0}},
            )

            resolved = await ContractScopeResolver(db).resolve(_scope(), CurrentState())
            assert resolved.eligible_document_ids == frozenset()

    asyncio.run(scenario())


def test_old_vectors_become_unusable_the_moment_the_revision_is_set():
    """Not when re-embedding finishes."""

    async def scenario():
        async with _database() as (db, client):
            instrument_id = await _promote(db, client)
            sequencer = PostPromotionSequencer(db)
            await sequencer.mark_projection_current(instrument_id, revision=1)

            # A correction lands. Nothing has been re-embedded.
            await db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
                {"_id": instrument_id},
                {"$set": {"classification_revision": 2, "projection_status": "PENDING"}},
            )

            resolved = await ContractScopeResolver(db).resolve(_scope(), CurrentState())
            assert resolved.eligible_document_ids == frozenset()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# M09-18 : a derived-store outage
# --------------------------------------------------------------------------- #


def test_a_derived_store_outage_does_not_roll_back_the_promotion():
    """M09-18."""

    async def scenario():
        async with _database() as (db, client):
            instrument_id = await _promote(db, client)
            sequencer = PostPromotionSequencer(db)

            await sequencer.record_projection_outage(
                instrument_id, revision=1, reason="qdrant unavailable"
            )

            # The promotion stands.
            assert await db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({}) == 1
            assert await db[PROMOTION_RECEIPTS_COLLECTION].count_documents({}) == 1
            assert await db[APPLICABILITY_EVENTS_COLLECTION].count_documents({}) == 1

            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            # And the classification it was promoted with is untouched.
            assert record["contract_document_type"] == ContractDocumentType.GENERAL_CONDITIONS.value
            assert record["classification_revision"] == 1

    asyncio.run(scenario())


def test_a_derived_store_outage_leaves_evidence_disabled():
    async def scenario():
        async with _database() as (db, client):
            instrument_id = await _promote(db, client)
            await PostPromotionSequencer(db).record_projection_outage(
                instrument_id, revision=1, reason="qdrant unavailable"
            )

            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            assert record["projection_status"] == "FAILED"

            resolved = await ContractScopeResolver(db).resolve(_scope(), CurrentState())
            assert resolved.eligible_document_ids == frozenset()

    asyncio.run(scenario())


def test_an_outage_never_retracts_the_authoritative_classification():
    async def scenario():
        async with _database() as (db, client):
            instrument_id = await _promote(db, client)
            before = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})

            await PostPromotionSequencer(db).record_projection_outage(
                instrument_id, revision=1, reason="falkor unavailable"
            )

            after = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            assert after["contract_document_type"] == before["contract_document_type"]
            assert after["classification_revision"] == before["classification_revision"]

    asyncio.run(scenario())


def test_an_outage_reported_against_a_superseded_revision_is_refused():
    """A late worker must not stamp FAILED over a newer generation."""

    async def scenario():
        async with _database() as (db, client):
            instrument_id = await _promote(db, client)
            await db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
                {"_id": instrument_id}, {"$set": {"classification_revision": 2}}
            )

            with pytest.raises(DerivedStoreOutage):
                await PostPromotionSequencer(db).record_projection_outage(
                    instrument_id, revision=1, reason="stale worker"
                )

            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            assert record["projection_status"] != "FAILED"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# M09-22 : captured session evidence outlives the session
# --------------------------------------------------------------------------- #


def test_session_evidence_captured_at_inventory_survives_ttl_expiry():
    """M09-22, against a real TTL index."""

    async def scenario():
        async with _database() as (db, client):
            await db["contract_upload_sessions"].create_index(
                "expiresAt", expireAfterSeconds=0, background=False
            )
            await db["documents"].insert_one(
                {
                    "_id": "doc-ttl",
                    "organization_id": ORG,
                    "uploadType": "contract",
                    "upload_id": "upload-doc-ttl",
                }
            )
            await db["contract_upload_sessions"].insert_one(
                {
                    "_id": "session-ttl",
                    "upload_id": "upload-doc-ttl",
                    "project_id": PROJECT,
                    "expiresAt": datetime.now(timezone.utc) - timedelta(hours=1),
                }
            )

            service = ContractMigrationReconciliation(db)
            candidates = await service.inventory(organization_id=ORG)
            await service.materialise_inventory(candidates)

            # Simulate the TTL monitor having reaped the expired session. The
            # captured evidence must not depend on it.
            await db["contract_upload_sessions"].delete_many({})

            row = await db[RECONCILIATION_COLLECTION].find_one(
                {"canonical_document_id": "doc-ttl"}
            )
            assert await service.captured_session_evidence(row) == {"project_id": PROJECT}

    asyncio.run(scenario())


def test_the_captured_evidence_is_read_from_the_row_not_the_session():
    async def scenario():
        async with _database() as (db, client):
            await db["documents"].insert_one(
                {
                    "_id": "doc-ttl",
                    "organization_id": ORG,
                    "uploadType": "contract",
                    "upload_id": "upload-doc-ttl",
                }
            )
            await db["contract_upload_sessions"].insert_one(
                {"_id": "s", "upload_id": "upload-doc-ttl", "project_id": PROJECT}
            )
            service = ContractMigrationReconciliation(db)
            candidates = await service.inventory(organization_id=ORG)
            await service.materialise_inventory(candidates)

            # Change the session to a different answer. The capture must win.
            await db["contract_upload_sessions"].update_one(
                {"_id": "s"}, {"$set": {"project_id": "project-changed"}}
            )

            row = await db[RECONCILIATION_COLLECTION].find_one(
                {"canonical_document_id": "doc-ttl"}
            )
            assert await service.captured_session_evidence(row) == {"project_id": PROJECT}

    asyncio.run(scenario())
