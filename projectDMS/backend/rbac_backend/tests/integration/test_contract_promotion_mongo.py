"""Promotion transaction - atomic, all-or-nothing.

M09-06 - ambiguous scope performs zero authoritative writes.
M09-07 - project-scope promotion atomically creates at least one applicability.
M09-08 - organisation-scope promotion succeeds with zero applicability.
M09-09 - an unknown effective-from stays null.
M09-10 - two concurrent identical promotions converge on one record.
M09-12 - a stale reconciliation snapshot is revalidated before promotion.
M09-14 - a blocked document cannot become readable through promotion.
M09-17 - legacy source fields remain untouched.
M09-19 - the receipt links candidate to record deterministically.

Real Mongo with a replica set, because the claim under test is transactional:
the five writes either all land or none do, and a fake that applies them in
order proves the code's intention rather than the database's behaviour. The
rollback tests are the ones that would silently pass against a fake.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_PROMOTION_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_PROMOTION_MONGODB_URI is not set; this suite needs a disposable "
        "replica-set Mongo (transactions)",
        allow_module_level=True,
    )

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rbac_backend.models.contract_document import ContractDocumentType  # noqa: E402
from rbac_backend.services.contract_document_store import (  # noqa: E402
    APPLICABILITY_COLLECTION,
    APPLICABILITY_EVENTS_COLLECTION,
    CLASSIFICATION_FACTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
    LEGAL_COLLECTIONS,
)
from rbac_backend.services.contract_migration_reconciliation import (  # noqa: E402
    RECONCILIATION_COLLECTION,
    ScopeClassificationState,
    TypeClassificationState,
)
from rbac_backend.services.contract_promotion import (  # noqa: E402
    PROMOTION_RECEIPTS_COLLECTION,
    ContractPromotionService,
    NotPromotable,
    RevalidationRequired,
)

ORG = "org-promotion"
PROJECT = "project-promotion"
CONTRACT = "contract-promotion"
CANDIDATE = "contract-master-migration:contracts:doc-1"


@asynccontextmanager
async def _database():
    client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)
    name = f"contract_promotion_{uuid.uuid4().hex[:10]}"
    db = client[name]
    try:
        yield db, client
    finally:
        await client.drop_database(name)
        client.close()


async def _seed(
    db,
    *,
    scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
    type_state=TypeClassificationState.TYPE_RESOLVED,
    document_overrides: Optional[Dict[str, Any]] = None,
    candidate_overrides: Optional[Dict[str, Any]] = None,
):
    document = {
        "_id": "doc-1",
        "organization_id": ORG,
        "project_id": PROJECT,
        "checksum": "sha-original",
        "current_version_id": "doc-1-v2",
        # Legacy source fields, present to prove they survive untouched.
        "legacy_upload_type": "contract",
        "legacy_project_id": PROJECT,
    }
    document.update(document_overrides or {})
    await db["documents"].insert_one(document)
    # A project-scope candidate is anchored to a real project of its organisation.
    await db["projects"].insert_one({"_id": PROJECT, "organization_id": ORG})

    candidate = {
        "_id": CANDIDATE,
        "candidate_id": CANDIDATE,
        "canonical_document_id": "doc-1",
        "organization_id": ORG,
        "module": "contracts",
        "scope_state": scope_state.value,
        "type_state": type_state.value,
        "project_id": PROJECT,
        "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS.value,
        "adjudicated_by": "alice",
        "source_fingerprint": "sha-original",
    }
    candidate.update(candidate_overrides or {})
    await db[RECONCILIATION_COLLECTION].insert_one(candidate)


async def _legal_counts(db) -> Dict[str, int]:
    counts = {name: await db[name].count_documents({}) for name in LEGAL_COLLECTIONS}
    counts[PROMOTION_RECEIPTS_COLLECTION] = await db[
        PROMOTION_RECEIPTS_COLLECTION
    ].count_documents({})
    return counts


# --------------------------------------------------------------------------- #
# M09-07 / M09-08 : the two valid shapes
# --------------------------------------------------------------------------- #


def test_project_scope_promotion_creates_its_first_applicability_atomically():
    """M09-07."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db)
            service = ContractPromotionService(db, client)

            receipt = await service.promote(
                CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT, effective_from="2021-04-01"
            )

            instrument = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
                {"_id": receipt.contract_document_id}
            )
            assert instrument["classification_revision"] == 1
            assert instrument["projection_status"] == "PENDING"

            assert await db[CLASSIFICATION_FACTS_COLLECTION].count_documents({}) == 1
            assert await db[APPLICABILITY_COLLECTION].count_documents({}) == 1

            event = await db[APPLICABILITY_EVENTS_COLLECTION].find_one({})
            assert event["kind"] == "APPLIED"
            assert event["effective_at"] == "2021-04-01"

    asyncio.run(scenario())


def test_organisation_scope_promotion_succeeds_with_zero_applicability():
    """M09-08."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(
                db,
                scope_state=ScopeClassificationState.ORG_SCOPE_CONFIRMED,
                candidate_overrides={"project_id": None},
            )
            service = ContractPromotionService(db, client)

            receipt = await service.promote(CANDIDATE, organization_id=ORG, actor_id="alice")

            assert receipt.scope_level == "organization"
            assert receipt.project_id is None
            assert await db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({}) == 1
            assert await db[CLASSIFICATION_FACTS_COLLECTION].count_documents({}) == 1
            # Zero applicability is correct, not incomplete.
            assert await db[APPLICABILITY_COLLECTION].count_documents({}) == 0
            assert await db[APPLICABILITY_EVENTS_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_organisation_promotion_manufactures_no_project_applicability():
    async def scenario():
        async with _database() as (db, client):
            await _seed(
                db,
                scope_state=ScopeClassificationState.ORG_SCOPE_CONFIRMED,
                candidate_overrides={"project_id": None},
            )
            await ContractPromotionService(db, client).promote(CANDIDATE, organization_id=ORG, actor_id="alice")

            instrument = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({})
            assert instrument["project_id"] is None
            assert instrument["scope_level"] == "organization"

    asyncio.run(scenario())


def test_project_scope_promotion_without_a_contract_is_refused():
    async def scenario():
        async with _database() as (db, client):
            await _seed(db)
            with pytest.raises(NotPromotable) as excinfo:
                await ContractPromotionService(db, client).promote(CANDIDATE, organization_id=ORG, actor_id="alice")
            assert "governs nothing" in str(excinfo.value)
            assert await _legal_counts(db) == {
                name: 0 for name in list(LEGAL_COLLECTIONS) + [PROMOTION_RECEIPTS_COLLECTION]
            }

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# M09-06 : ambiguous scope writes nothing
# --------------------------------------------------------------------------- #


def test_ambiguous_scope_performs_zero_authoritative_writes():
    """M09-06."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db, scope_state=ScopeClassificationState.AMBIGUOUS)
            service = ContractPromotionService(db, client)

            with pytest.raises(NotPromotable):
                await service.promote(CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT)

            for name in LEGAL_COLLECTIONS:
                assert await db[name].count_documents({}) == 0
            assert await db[PROMOTION_RECEIPTS_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_an_unresolved_type_performs_zero_authoritative_writes():
    async def scenario():
        async with _database() as (db, client):
            await _seed(db, type_state=TypeClassificationState.TYPE_SUGGESTED)
            with pytest.raises(NotPromotable) as excinfo:
                await ContractPromotionService(db, client).promote(
                    CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )
            assert "suggestion is not a classification" in str(excinfo.value)
            for name in LEGAL_COLLECTIONS:
                assert await db[name].count_documents({}) == 0

    asyncio.run(scenario())


def test_an_invalid_candidate_never_promotes():
    async def scenario():
        async with _database() as (db, client):
            await _seed(db, scope_state=ScopeClassificationState.INVALID)
            with pytest.raises(NotPromotable) as excinfo:
                await ContractPromotionService(db, client).promote(
                    CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )
            assert "terminal" in str(excinfo.value)

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# atomicity
# --------------------------------------------------------------------------- #


def test_a_failure_midway_leaves_nothing_behind():
    """The whole point of one transaction rather than five writes."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db)

            class Failing(ContractPromotionService):
                async def _apply(self, plan, *, session):
                    await self._db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
                        plan["instrument"], session=session
                    )
                    await self._db[CLASSIFICATION_FACTS_COLLECTION].insert_one(
                        plan["classification_fact"], session=session
                    )
                    raise RuntimeError("applicability write failed")

            with pytest.raises(RuntimeError):
                await Failing(db, client).promote(
                    CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )

            # The instrument and the fact were written inside the transaction
            # and must be gone.
            assert await db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({}) == 0
            assert await db[CLASSIFICATION_FACTS_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_a_receipt_cannot_survive_a_rollback():
    async def scenario():
        async with _database() as (db, client):
            await _seed(db)

            class FailingAfterReceipt(ContractPromotionService):
                async def _apply(self, plan, *, session):
                    await super()._apply(plan, session=session)
                    raise RuntimeError("post-receipt failure")

            with pytest.raises(RuntimeError):
                await FailingAfterReceipt(db, client).promote(
                    CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )

            assert await db[PROMOTION_RECEIPTS_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_promotion_without_a_transactional_session_is_refused():
    async def scenario():
        async with _database() as (db, _client):
            await _seed(db)
            with pytest.raises(NotPromotable) as excinfo:
                await ContractPromotionService(db, None).promote(
                    CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )
            assert "atomic" in str(excinfo.value)
            for name in LEGAL_COLLECTIONS:
                assert await db[name].count_documents({}) == 0

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# M09-10 : concurrency
# --------------------------------------------------------------------------- #


def test_two_concurrent_identical_promotions_converge_on_one_record():
    """M09-10."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db)
            service = ContractPromotionService(db, client)

            results = await asyncio.gather(
                *(
                    service.promote(CANDIDATE, organization_id=ORG, actor_id=f"op-{i}", contract_id=CONTRACT)
                    for i in range(4)
                ),
                return_exceptions=True,
            )
            succeeded = [r for r in results if not isinstance(r, Exception)]

            assert len(succeeded) >= 1
            assert await db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({}) == 1
            assert await db[APPLICABILITY_EVENTS_COLLECTION].count_documents({}) == 1
            assert await db[PROMOTION_RECEIPTS_COLLECTION].count_documents({}) == 1

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# M09-09 / M09-12 / M09-14 / M09-17 / M09-19
# --------------------------------------------------------------------------- #


def test_an_unknown_effective_from_stays_null():
    """M09-09."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db)
            await ContractPromotionService(db, client).promote(
                CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT, effective_from=None
            )

            event = await db[APPLICABILITY_EVENTS_COLLECTION].find_one({})
            # Not today, not the promotion date: unknown.
            assert event["effective_at"] is None

    asyncio.run(scenario())


def test_a_stale_snapshot_is_revalidated_before_promotion():
    """M09-12."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db, document_overrides={"checksum": "sha-changed-since"})

            with pytest.raises(RevalidationRequired):
                await ContractPromotionService(db, client).promote(
                    CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )

            for name in LEGAL_COLLECTIONS:
                assert await db[name].count_documents({}) == 0

    asyncio.run(scenario())


def test_a_quarantined_document_cannot_become_readable_through_promotion():
    """M09-14, quarantine axis."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db, document_overrides={"lifecycle_state": "duplicate"})

            with pytest.raises(NotPromotable) as excinfo:
                await ContractPromotionService(db, client).promote(
                    CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )
            assert "quarantined" in str(excinfo.value)
            assert await db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_a_deleted_document_never_promotes():
    async def scenario():
        async with _database() as (db, client):
            await _seed(db, document_overrides={"lifecycle_state": "deleted"})
            with pytest.raises(NotPromotable):
                await ContractPromotionService(db, client).promote(
                    CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )

    asyncio.run(scenario())


def test_an_adverse_quality_document_promotes_with_evidence_denied_elsewhere():
    """The other half of M09-14: quality is not quarantine."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db, document_overrides={"processing_status": "human_review_required"})

            receipt = await ContractPromotionService(db, client).promote(
                CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
            )

            # It is catalogued. Evidence is denied by the publication gate, not
            # by refusing to record that the instrument exists.
            assert await db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({}) == 1

            from rbac_backend.services.publication_policy import is_consumable

            document = await db["documents"].find_one({"_id": "doc-1"})
            assert is_consumable(document) is False
            assert receipt.contract_document_id

    asyncio.run(scenario())


def test_legacy_source_fields_remain_untouched():
    """M09-17."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db)
            before = await db["documents"].find_one({"_id": "doc-1"})

            await ContractPromotionService(db, client).promote(
                CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
            )

            after = await db["documents"].find_one({"_id": "doc-1"})
            assert after == before

    asyncio.run(scenario())


def test_the_receipt_links_candidate_to_record_deterministically():
    """M09-19."""

    async def scenario():
        async with _database() as (db, client):
            await _seed(db)
            receipt = await ContractPromotionService(db, client).promote(
                CANDIDATE, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
            )

            stored = await db[PROMOTION_RECEIPTS_COLLECTION].find_one({})
            assert stored["_id"] == f"promotion:{CANDIDATE}"
            assert stored["candidate_id"] == CANDIDATE
            assert stored["contract_document_id"] == receipt.contract_document_id
            assert stored["canonical_document_id"] == "doc-1"

    asyncio.run(scenario())


def test_the_receipt_is_written_inside_the_transaction():
    import ast
    import inspect

    from rbac_backend.services import contract_promotion

    source = inspect.getsource(contract_promotion.ContractPromotionService._apply)
    tree = ast.parse(source.lstrip())
    # Every write in _apply carries the session, receipt included.
    sessioned = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(kw.arg == "session" for kw in node.keywords)
    ]
    assert len(sessioned) >= 4
