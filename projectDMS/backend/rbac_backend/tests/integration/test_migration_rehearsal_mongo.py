"""T31 - migration rehearsal and operator flow verification.

The full operator path, end to end, against a realistic corpus copy:

    inventory -> materialise -> review -> claim -> adjudicate -> revalidate -> promote

This is a rehearsal, not a demonstration. Every step runs the production service
that will run in the real migration, against a real replica-set Mongo, and the
assertions are about what the authoritative store holds afterwards.

Two things it is emphatically not:

* **It is not a production migration.** The corpus is synthetic, the database is
  disposable, and nothing here reaches a real deployment.
* **It does not reinterpret legacy data.** A blank `project_id` does not become
  organisation scope, a filename saying GCC does not become a classification,
  and a legacy `contract_id` does not become applicability. Every one of those is
  exercised below as a candidate that *stays* ambiguous until a human decides.

Throughput and review-queue volume are measured and reported, because the queue
size is the number that decides whether the migration is operationally feasible
and nobody has one yet.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_REHEARSAL_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_REHEARSAL_MONGODB_URI is not set; this rehearsal needs a "
        "disposable replica-set Mongo",
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
from rbac_backend.services.contract_migration_adjudication import (  # noqa: E402
    ADJUDICATION_CLAIMS_COLLECTION,
    ClaimUnavailable,
    ContractMigrationAdjudication,
)
from rbac_backend.services.contract_migration_classifiers import (  # noqa: E402
    ScopeSignal,
    TypeSuggestion,
    classify_scope,
    classify_type,
)
from rbac_backend.services.contract_migration_reconciliation import (  # noqa: E402
    RECONCILIATION_COLLECTION,
    ContractMigrationReconciliation,
    ScopeClassificationState,
    TypeClassificationState,
)
from rbac_backend.services.contract_post_promotion import PostPromotionSequencer  # noqa: E402
from rbac_backend.services.contract_promotion import (  # noqa: E402
    PROMOTION_RECEIPTS_COLLECTION,
    ContractPromotionService,
    NotPromotable,
    RevalidationRequired,
)

ORG = "org-rehearsal"
PROJECT = "project-rehearsal"
CONTRACT = "contract-rehearsal"


@asynccontextmanager
async def _corpus():
    """A synthetic but realistically messy corpus copy."""
    client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
    name = f"contract_rehearsal_{uuid.uuid4().hex[:10]}"
    db = client[name]
    try:
        await db["projects"].insert_one({"_id": PROJECT, "organization_id": ORG})
        await db["documents"].insert_many(_legacy_documents())
        await db["contract_upload_sessions"].insert_one(
            {
                "_id": "session-clean",
                "upload_id": "upload-clean",
                "project_id": PROJECT,
                "expiresAt": datetime.now(timezone.utc) - timedelta(hours=2),
            }
        )
        yield db, client
    finally:
        await client.drop_database(name)
        client.close()


def _legacy_documents() -> List[Dict[str, Any]]:
    """One document per rehearsal case, in the shapes the legacy corpus has."""
    base = {
        "organization_id": ORG,
        "uploadType": "contract",
        "checksum": "sha-original",
        "current_version_id": "v1",
    }
    return [
        # A clean project candidate: a real project, a live session.
        {**base, "_id": "doc-clean", "project_id": PROJECT, "upload_id": "upload-clean",
         "filename": "SCC Particular Conditions.pdf"},
        # Scope ambiguity: blank project, audit row present and null.
        {**base, "_id": "doc-blank-scope", "project_id": "", "upload_id": "upload-blank",
         "filename": "GCC Volume 2.pdf"},
        # Scope ambiguity with no audit row at all.
        {**base, "_id": "doc-no-audit", "project_id": None, "upload_id": "upload-no-audit",
         "filename": "Conditions of Contract.pdf"},
        # Type ambiguity: a filename that looks decisive and is not.
        {**base, "_id": "doc-type-guess", "project_id": PROJECT, "upload_id": "upload-guess",
         "filename": "GCC-and-SCC-combined.pdf"},
        # A confirmed duplicate: quarantined, must never promote.
        {**base, "_id": "doc-duplicate", "project_id": PROJECT, "upload_id": "upload-dup",
         "filename": "GCC Volume 2 (copy).pdf", "duplicate_status": "duplicate"},
        # A deleted document: quarantined for a different reason.
        {**base, "_id": "doc-deleted", "project_id": PROJECT, "upload_id": "upload-del",
         "filename": "Superseded GCC.pdf", "lifecycle_state": "deleted"},
        # Adverse quality: promotes, evidence denied downstream.
        {**base, "_id": "doc-adverse", "project_id": PROJECT, "upload_id": "upload-adv",
         "filename": "Scanned BOQ.pdf", "processing_status": "human_review_required"},
        # Fingerprint drift since inventory.
        {**base, "_id": "doc-drifted", "project_id": PROJECT, "upload_id": "upload-drift",
         "filename": "Amendment 3.pdf", "checksum": "sha-changed"},
    ]


async def _legal_counts(db) -> Dict[str, int]:
    return {name: await db[name].count_documents({}) for name in LEGAL_COLLECTIONS}


async def _adjudicate(db, candidate_id: str, *, scope_state, document_type, operator="alice"):
    service = ContractMigrationAdjudication(db)
    await service.ensure_indexes()
    claim = await service.claim(candidate_id, organization_id=ORG, operator_id=operator)
    await service.adjudicate(
        claim,
        organization_id=ORG,
        scope_state=scope_state,
        contract_document_type=document_type,
        reason="rehearsal adjudication",
    )
    await service.release(claim)
    return claim


# --------------------------------------------------------------------------- #
# the full operator path
# --------------------------------------------------------------------------- #


def test_the_full_operator_path_runs_end_to_end():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)

            # 1. inventory - read only
            before = await _legal_counts(db)
            started = time.perf_counter()
            candidates = await reconciliation.inventory(organization_id=ORG)
            inventory_seconds = time.perf_counter() - started

            assert len(candidates) == 8
            assert await _legal_counts(db) == before
            assert await db[RECONCILIATION_COLLECTION].count_documents({}) == 0

            # 2. materialise - the separate, named write
            written = await reconciliation.materialise_inventory(candidates)
            assert written == 8
            assert await _legal_counts(db) == before

            # 3. review - every candidate starts unresolved on both axes
            rows = await db[RECONCILIATION_COLLECTION].find({}).to_list(length=None)
            assert {row["scope_state"] for row in rows} == {
                ScopeClassificationState.UNRESOLVED.value
            }
            assert {row["type_state"] for row in rows} == {
                TypeClassificationState.TYPE_UNKNOWN.value
            }

            # 4. adjudicate one clean candidate, then promote it
            clean = next(c for c in candidates if c.canonical_document_id == "doc-clean")
            await _adjudicate(
                db,
                clean.candidate_id,
                scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
            )
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": clean.candidate_id}, {"$set": {"project_id": PROJECT}}
            )

            promotion_started = time.perf_counter()
            receipt = await ContractPromotionService(db, client).promote(
                clean.candidate_id,
                organization_id=ORG,
                actor_id="alice",
                contract_id=CONTRACT,
                effective_from="2021-04-01",
            )
            promotion_seconds = time.perf_counter() - promotion_started

            # 5. the receipt links candidate to instrument
            stored = await db[PROMOTION_RECEIPTS_COLLECTION].find_one({})
            assert stored["candidate_id"] == clean.candidate_id
            assert stored["contract_document_id"] == receipt.contract_document_id
            assert await db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({}) == 1
            assert await db[APPLICABILITY_EVENTS_COLLECTION].count_documents({}) == 1

            # 6. and it is not yet evidence
            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({})
            assert record["classification_revision"] == 1
            assert record["projection_status"] == "PENDING"

            print(
                f"\nREHEARSAL THROUGHPUT: inventory of 8 candidates in "
                f"{inventory_seconds:.3f}s; one promotion in {promotion_seconds:.3f}s"
            )

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# legacy data is never reinterpreted
# --------------------------------------------------------------------------- #


def test_a_blank_project_stays_ambiguous():
    outcome = classify_scope(
        _candidate("doc-blank-scope"),
        signal=ScopeSignal(legacy_project_id="", audit_row_present=True, audit_project_id=None),
    )
    assert outcome.state is ScopeClassificationState.AMBIGUOUS
    assert outcome.state is not ScopeClassificationState.ORG_SCOPE_CONFIRMED


def test_an_absent_audit_row_stays_ambiguous_with_no_hint():
    outcome = classify_scope(
        _candidate("doc-no-audit"),
        signal=ScopeSignal(legacy_project_id=None, audit_row_present=False, audit_project_id=None),
    )
    assert outcome.state is ScopeClassificationState.AMBIGUOUS
    assert outcome.hint is None


def test_a_decisive_looking_filename_is_still_only_a_suggestion():
    outcome = classify_type(
        _candidate("doc-type-guess"),
        suggestions=(
            TypeSuggestion(
                contract_document_type=ContractDocumentType.GENERAL_CONDITIONS, basis="filename"
            ),
        ),
    )
    assert outcome.state is TypeClassificationState.TYPE_SUGGESTED
    assert outcome.contract_document_type is None


def _candidate(document_id: str):
    from rbac_backend.services.contract_migration_reconciliation import (
        ReconciliationCandidate,
        candidate_identity,
    )

    return ReconciliationCandidate(
        candidate_id=candidate_identity(module="contracts", canonical_document_id=document_id),
        canonical_document_id=document_id,
        organization_id=ORG,
        module="contracts",
    )


# --------------------------------------------------------------------------- #
# the refusal cases
# --------------------------------------------------------------------------- #


def test_an_ambiguous_candidate_cannot_be_promoted():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            ambiguous = next(c for c in candidates if c.canonical_document_id == "doc-blank-scope")

            with pytest.raises(NotPromotable):
                await ContractPromotionService(db, client).promote(
                    ambiguous.candidate_id, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )

            for name in LEGAL_COLLECTIONS:
                assert await db[name].count_documents({}) == 0

    asyncio.run(scenario())


def test_a_quarantined_duplicate_never_promotes():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            duplicate = next(c for c in candidates if c.canonical_document_id == "doc-duplicate")

            await _adjudicate(
                db,
                duplicate.candidate_id,
                scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                document_type=ContractDocumentType.GENERAL_CONDITIONS,
            )
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": duplicate.candidate_id}, {"$set": {"project_id": PROJECT}}
            )

            with pytest.raises(NotPromotable) as excinfo:
                await ContractPromotionService(db, client).promote(
                    duplicate.candidate_id, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )
            assert "quarantined" in str(excinfo.value)

    asyncio.run(scenario())


def test_a_deleted_document_never_promotes():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            deleted = next(c for c in candidates if c.canonical_document_id == "doc-deleted")
            await _adjudicate(
                db,
                deleted.candidate_id,
                scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                document_type=ContractDocumentType.GENERAL_CONDITIONS,
            )
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": deleted.candidate_id}, {"$set": {"project_id": PROJECT}}
            )

            with pytest.raises(NotPromotable):
                await ContractPromotionService(db, client).promote(
                    deleted.candidate_id, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )

    asyncio.run(scenario())


def test_adverse_quality_promotes_but_evidence_is_denied():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            adverse = next(c for c in candidates if c.canonical_document_id == "doc-adverse")
            await _adjudicate(
                db,
                adverse.candidate_id,
                scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                document_type=ContractDocumentType.BOQ,
            )
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": adverse.candidate_id}, {"$set": {"project_id": PROJECT}}
            )

            await ContractPromotionService(db, client).promote(
                adverse.candidate_id, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
            )
            # Catalogued, because quality is not quarantine.
            assert await db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({}) == 1

            from rbac_backend.services.publication_policy import is_consumable

            document = await db["documents"].find_one({"_id": "doc-adverse"})
            assert is_consumable(document) is False

    asyncio.run(scenario())


def test_a_drifted_fingerprint_requires_revalidation():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            drifted = next(c for c in candidates if c.canonical_document_id == "doc-drifted")
            await _adjudicate(
                db,
                drifted.candidate_id,
                scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                document_type=ContractDocumentType.AMENDMENT,
            )
            # The snapshot recorded what the operator reviewed.
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": drifted.candidate_id},
                {"$set": {"project_id": PROJECT, "source_fingerprint": "sha-original"}},
            )

            with pytest.raises(RevalidationRequired):
                await ContractPromotionService(db, client).promote(
                    drifted.candidate_id, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )
            for name in LEGAL_COLLECTIONS:
                assert await db[name].count_documents({}) == 0

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# claim, lease, rollback, outage, idempotency
# --------------------------------------------------------------------------- #


def test_two_operators_cannot_adjudicate_the_same_candidate():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            target = candidates[0].candidate_id

            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()
            await service.claim(target, organization_id=ORG, operator_id="alice")
            with pytest.raises(ClaimUnavailable):
                await service.claim(target, organization_id=ORG, operator_id="bob")

    asyncio.run(scenario())


def test_an_expired_lease_is_reclaimable_and_changes_nothing_legal():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            target = candidates[0].candidate_id

            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()
            await service.claim(target, organization_id=ORG, operator_id="alice")
            before = await _legal_counts(db)

            await db[ADJUDICATION_CLAIMS_COLLECTION].update_one(
                {"candidate_id": target},
                {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(hours=1)}},
            )
            await service.expire_stale_claims()

            reclaimed = await service.claim(target, organization_id=ORG, operator_id="bob")
            assert reclaimed.operator_id == "bob"
            assert await _legal_counts(db) == before

    asyncio.run(scenario())


def test_a_rolled_back_promotion_leaves_nothing_behind():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            clean = next(c for c in candidates if c.canonical_document_id == "doc-clean")
            await _adjudicate(
                db,
                clean.candidate_id,
                scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
            )
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": clean.candidate_id}, {"$set": {"project_id": PROJECT}}
            )

            class Failing(ContractPromotionService):
                async def _apply(self, plan, *, session):
                    await self._db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
                        plan["instrument"], session=session
                    )
                    raise RuntimeError("rehearsal-induced failure")

            with pytest.raises(RuntimeError):
                await Failing(db, client).promote(
                    clean.candidate_id, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
                )

            for name in LEGAL_COLLECTIONS:
                assert await db[name].count_documents({}) == 0
            assert await db[PROMOTION_RECEIPTS_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_a_derived_outage_after_promotion_does_not_roll_it_back():
    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            clean = next(c for c in candidates if c.canonical_document_id == "doc-clean")
            await _adjudicate(
                db,
                clean.candidate_id,
                scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
            )
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": clean.candidate_id}, {"$set": {"project_id": PROJECT}}
            )
            receipt = await ContractPromotionService(db, client).promote(
                clean.candidate_id, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
            )

            await PostPromotionSequencer(db).record_projection_outage(
                receipt.contract_document_id, revision=1, reason="rehearsal outage"
            )

            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({})
            assert record is not None
            assert record["projection_status"] == "FAILED"
            assert record["contract_document_type"] == "particular_conditions"
            assert await db[PROMOTION_RECEIPTS_COLLECTION].count_documents({}) == 1

    asyncio.run(scenario())


def test_rerunning_inventory_and_materialise_converges():
    """Idempotency: a second pass adds no second candidate."""

    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            first = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(first)
            count_after_first = await db[RECONCILIATION_COLLECTION].count_documents({})

            second = await reconciliation.inventory(organization_id=ORG)
            assert [c.candidate_id for c in second] == [c.candidate_id for c in first]

            # A duplicate insert on the same identity is refused, not forked.
            for candidate in second:
                try:
                    await reconciliation.materialise_inventory([candidate])
                except Exception:
                    pass

            assert await db[RECONCILIATION_COLLECTION].count_documents({}) == count_after_first

    asyncio.run(scenario())


def test_legacy_source_fields_are_untouched_by_the_whole_rehearsal():
    async def scenario():
        async with _corpus() as (db, client):
            before = {
                doc["_id"]: doc for doc in await db["documents"].find({}).to_list(length=None)
            }
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)
            clean = next(c for c in candidates if c.canonical_document_id == "doc-clean")
            await _adjudicate(
                db,
                clean.candidate_id,
                scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
            )
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": clean.candidate_id}, {"$set": {"project_id": PROJECT}}
            )
            await ContractPromotionService(db, client).promote(
                clean.candidate_id, organization_id=ORG, actor_id="alice", contract_id=CONTRACT
            )

            after = {
                doc["_id"]: doc for doc in await db["documents"].find({}).to_list(length=None)
            }
            assert after == before

    asyncio.run(scenario())


def test_the_review_queue_volume_is_measured():
    """The number that decides whether the migration is operationally feasible."""

    async def scenario():
        async with _corpus() as (db, client):
            reconciliation = ContractMigrationReconciliation(db)
            candidates = await reconciliation.inventory(organization_id=ORG)
            await reconciliation.materialise_inventory(candidates)

            rows = await db[RECONCILIATION_COLLECTION].find({}).to_list(length=None)
            needing_review = [
                row
                for row in rows
                if row["scope_state"]
                not in (
                    ScopeClassificationState.ORG_SCOPE_CONFIRMED.value,
                    ScopeClassificationState.PROJECT_SCOPE_CONFIRMED.value,
                )
                or row["type_state"] != TypeClassificationState.TYPE_RESOLVED.value
            ]

            # Every candidate needs a human on at least one axis: the corpus
            # contains no positive type evidence and no organisation-scope
            # evidence at all.
            assert len(needing_review) == len(rows) == 8
            print(
                f"\nREHEARSAL REVIEW QUEUE: {len(needing_review)} of {len(rows)} candidates "
                "require operator adjudication on at least one axis"
            )

    asyncio.run(scenario())
