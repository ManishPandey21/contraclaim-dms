"""Candidate claim, lease and operator adjudication.

M09-11 - conflicting operator classifications fail closed and the loser is told.
M09-13 - lease expiry changes no applicability, lifecycle event or classification.

M09-13 needs a mutation proof because it asserts an absence, and a lease that
does nothing at all satisfies it for free. The mutation gives expiry a legal
side effect, which must turn it red.

Real Mongo, because claim atomicity is a database primitive: a read-then-insert
lets two operators both observe "free", and only the unique index actually
decides. A fake that serialises calls would prove the code's intention rather
than its behaviour.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_MIGRATION_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_MIGRATION_MONGODB_URI is not set; this suite needs a disposable Mongo",
        allow_module_level=True,
    )

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rbac_backend.models.contract_document import ContractDocumentType  # noqa: E402
from rbac_backend.services.contract_document_store import LEGAL_COLLECTIONS  # noqa: E402
from rbac_backend.services.contract_migration_adjudication import (  # noqa: E402
    ADJUDICATION_CLAIMS_COLLECTION,
    CandidateClaim,
    ClaimUnavailable,
    ContractMigrationAdjudication,
    ConflictingAdjudication,
)
from rbac_backend.services.contract_migration_reconciliation import (  # noqa: E402
    RECONCILIATION_COLLECTION,
    ScopeClassificationState,
    TypeClassificationState,
)

CANDIDATE = "contract-master-migration:contracts:doc-1"


@asynccontextmanager
async def _database():
    client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=4000)
    name = f"contract_migration_{uuid.uuid4().hex[:10]}"
    db = client[name]
    try:
        await db[RECONCILIATION_COLLECTION].insert_one(
            {
                "_id": CANDIDATE,
                "candidate_id": CANDIDATE,
                "canonical_document_id": "doc-1",
                "organization_id": "org-1",
                "module": "contracts",
                "scope_state": ScopeClassificationState.AMBIGUOUS.value,
                "type_state": TypeClassificationState.TYPE_UNKNOWN.value,
                "session_evidence": {},
            }
        )
        yield db
    finally:
        await client.drop_database(name)
        client.close()


async def _legal_snapshot(db) -> Dict[str, int]:
    return {name: await db[name].count_documents({}) for name in LEGAL_COLLECTIONS}


# --------------------------------------------------------------------------- #
# claim atomicity
# --------------------------------------------------------------------------- #


def test_two_operators_cannot_hold_the_same_candidate():
    async def scenario():
        async with _database() as db:
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()

            first = await service.claim(CANDIDATE, operator_id="alice")
            assert isinstance(first, CandidateClaim)

            with pytest.raises(ClaimUnavailable):
                await service.claim(CANDIDATE, operator_id="bob")

    asyncio.run(scenario())


def test_concurrent_claims_produce_exactly_one_winner():
    async def scenario():
        async with _database() as db:
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()

            results = await asyncio.gather(
                *(service.claim(CANDIDATE, operator_id=f"operator-{i}") for i in range(8)),
                return_exceptions=True,
            )
            winners = [r for r in results if isinstance(r, CandidateClaim)]
            losers = [r for r in results if isinstance(r, ClaimUnavailable)]

            assert len(winners) == 1
            assert len(losers) == 7

    asyncio.run(scenario())


def test_the_claim_collection_is_disjoint_from_every_authoritative_one():
    assert ADJUDICATION_CLAIMS_COLLECTION not in LEGAL_COLLECTIONS
    assert ADJUDICATION_CLAIMS_COLLECTION != RECONCILIATION_COLLECTION


# --------------------------------------------------------------------------- #
# M09-13: lease expiry has exactly one effect
# --------------------------------------------------------------------------- #


def test_lease_expiry_changes_no_legal_state_at_all():
    """M09-13."""

    async def scenario():
        async with _database() as db:
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()
            await service.claim(CANDIDATE, operator_id="alice")

            before_legal = await _legal_snapshot(db)
            before_row = await db[RECONCILIATION_COLLECTION].find_one({"_id": CANDIDATE})

            # Age the lease past its expiry.
            await db[ADJUDICATION_CLAIMS_COLLECTION].update_one(
                {"candidate_id": CANDIDATE},
                {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(hours=1)}},
            )
            await service.expire_stale_claims()

            assert await _legal_snapshot(db) == before_legal
            after_row = await db[RECONCILIATION_COLLECTION].find_one({"_id": CANDIDATE})
            assert after_row["scope_state"] == before_row["scope_state"]
            assert after_row["type_state"] == before_row["type_state"]
            assert "contract_document_type" not in after_row

    asyncio.run(scenario())


def test_lease_expiry_has_exactly_one_effect_another_operator_may_claim():
    async def scenario():
        async with _database() as db:
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()
            await service.claim(CANDIDATE, operator_id="alice")

            with pytest.raises(ClaimUnavailable):
                await service.claim(CANDIDATE, operator_id="bob")

            await db[ADJUDICATION_CLAIMS_COLLECTION].update_one(
                {"candidate_id": CANDIDATE},
                {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(hours=1)}},
            )
            await service.expire_stale_claims()

            reclaimed = await service.claim(CANDIDATE, operator_id="bob")
            assert reclaimed.operator_id == "bob"

    asyncio.run(scenario())


def test_lease_state_is_not_an_input_to_any_legal_decision():
    import ast
    import inspect

    from rbac_backend.services import contract_migration_adjudication

    source = inspect.getsource(contract_migration_adjudication.ContractMigrationAdjudication.adjudicate)
    tree = ast.parse(source.lstrip())
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    # The adjudication reads the claim only to verify ownership, never to decide.
    assert "lease_expires_at" not in names | attrs


# --------------------------------------------------------------------------- #
# M09-11: conflicting adjudications fail closed
# --------------------------------------------------------------------------- #


def test_conflicting_operator_classifications_fail_closed():
    """M09-11."""

    async def scenario():
        async with _database() as db:
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()

            alice = await service.claim(CANDIDATE, operator_id="alice")
            await service.adjudicate(
                alice,
                scope_state=ScopeClassificationState.ORG_SCOPE_CONFIRMED,
                contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
                reason="volume 1 is the GCC",
            )
            await service.release(alice)

            bob = await service.claim(CANDIDATE, operator_id="bob")
            with pytest.raises(ConflictingAdjudication) as excinfo:
                await service.adjudicate(
                    bob,
                    scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                    contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
                    reason="I think it is the PCC",
                )

            # The loser is told, specifically.
            message = str(excinfo.value)
            assert "alice" in message
            assert "general_conditions" in message

            row = await db[RECONCILIATION_COLLECTION].find_one({"_id": CANDIDATE})
            assert row["type_state"] == TypeClassificationState.TYPE_RESOLVED.value
            assert row["contract_document_type"] == ContractDocumentType.GENERAL_CONDITIONS.value

    asyncio.run(scenario())


def test_an_identical_re_adjudication_is_not_a_conflict():
    async def scenario():
        async with _database() as db:
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()

            alice = await service.claim(CANDIDATE, operator_id="alice")
            for _ in range(2):
                await service.adjudicate(
                    alice,
                    scope_state=ScopeClassificationState.ORG_SCOPE_CONFIRMED,
                    contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
                    reason="volume 1 is the GCC",
                )

    asyncio.run(scenario())


def test_adjudication_records_actor_and_reason():
    async def scenario():
        async with _database() as db:
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()

            alice = await service.claim(CANDIDATE, operator_id="alice")
            await service.adjudicate(
                alice,
                scope_state=ScopeClassificationState.ORG_SCOPE_CONFIRMED,
                contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
                reason="volume 1 is the GCC",
            )

            row = await db[RECONCILIATION_COLLECTION].find_one({"_id": CANDIDATE})
            assert row["adjudicated_by"] == "alice"
            assert row["adjudication_reason"] == "volume 1 is the GCC"
            assert row["adjudicated_at"] is not None

    asyncio.run(scenario())


def test_adjudication_without_a_live_claim_is_refused():
    async def scenario():
        async with _database() as db:
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()

            alice = await service.claim(CANDIDATE, operator_id="alice")
            await service.release(alice)

            with pytest.raises(ClaimUnavailable):
                await service.adjudicate(
                    alice,
                    scope_state=ScopeClassificationState.ORG_SCOPE_CONFIRMED,
                    contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
                    reason="stale claim",
                )

    asyncio.run(scenario())


def test_adjudication_writes_no_authoritative_collection():
    async def scenario():
        async with _database() as db:
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()
            before = await _legal_snapshot(db)

            alice = await service.claim(CANDIDATE, operator_id="alice")
            await service.adjudicate(
                alice,
                scope_state=ScopeClassificationState.ORG_SCOPE_CONFIRMED,
                contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
                reason="volume 1 is the GCC",
            )

            # Adjudication is a decision about a candidate, not a promotion.
            assert await _legal_snapshot(db) == before

    asyncio.run(scenario())


def test_an_invalid_candidate_is_terminal_and_never_downgraded():
    async def scenario():
        async with _database() as db:
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": CANDIDATE},
                {"$set": {"scope_state": ScopeClassificationState.INVALID.value}},
            )
            service = ContractMigrationAdjudication(db)
            await service.ensure_indexes()

            alice = await service.claim(CANDIDATE, operator_id="alice")
            with pytest.raises(ConflictingAdjudication):
                await service.adjudicate(
                    alice,
                    scope_state=ScopeClassificationState.AMBIGUOUS,
                    contract_document_type=None,
                    reason="let us reconsider",
                )

    asyncio.run(scenario())
