"""Real MongoDB proof of reprojection worker fencing.

Owned by implementation ticket 08. Opt-in, like the other tracer suites here.

Ticket 07 established that a classification correction commits immediately and
makes the previous projection non-current. This suite establishes the other half:
**a worker that finishes late cannot make its generation current again.**

Why real Mongo rather than a fake: the fence is a *conditional write*. A fake
collection performs the read and the write with nothing able to interleave, so
the race the guard exists to close cannot even be expressed — the test would pass
whether or not the guard were there. Only a real server can show a completion
matching zero documents because the revision moved underneath it.

The scenario that matters:

1. revision 1 exists, worker W1 claims it;
2. an operator confirms revision 2 while W1 is still working;
3. W1 finishes and attempts completion;
4. W1's completion must match nothing and change nothing.

Every assertion reads the stored document afterwards. Asserting that
``update_one`` was *called* with a revision would pass while the filter did
nothing.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, Optional

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.models.contract_document import (
    ContractDocumentType,
    ContractScopeLevel,
    ProjectionStatus,
)
from rbac_backend.services.contract_classification_service import (
    ContractClassificationService,
)
from rbac_backend.services.contract_document_store import (
    CONTRACT_DOCUMENTS_COLLECTION,
    ensure_contract_document_indexes,
)
from rbac_backend.services.contract_reprojection_worker import (
    ContractReprojectionWorker,
    ProjectionClaim,
    StaleWorkerGeneration,
)

MONGODB_URI_ENV = "CONTRACT_MASTER_MONGODB_URI"

ORG = "org-t08"
CD = "cd-t08"
DOC = "doc-t08"


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a test-only MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _Fixture:
    def __init__(self, client: AsyncIOMotorClient, name: str) -> None:
        self.client = client
        self.db = client[name]
        self.name = name


async def _fresh() -> _Fixture:
    client = AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=8000)
    fixture = _Fixture(client, f"cm_t08_{uuid.uuid4().hex[:12]}")
    await ensure_contract_document_indexes(fixture.db)
    await fixture.db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
        {
            "_id": CD,
            "organization_id": ORG,
            "document_id": DOC,
            "scope_level": ContractScopeLevel.PROJECT.value,
            "scope_project_id": "project-1",
            "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS.value,
            "classification_revision": 1,
            "projection_revision": None,
            "projection_status": ProjectionStatus.PENDING.value,
        }
    )
    return fixture


async def _drop(fixture: _Fixture) -> None:
    await fixture.client.drop_database(fixture.name)
    fixture.client.close()


async def _record(fixture: _Fixture) -> Dict[str, Any]:
    row = await fixture.db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": CD})
    assert row is not None
    return row


async def _advance_to(fixture: _Fixture, *, expected: int, to_type: ContractDocumentType) -> int:
    return await ContractClassificationService(fixture.db).confirm(
        CD,
        contract_document_type=to_type,
        expected_revision=expected,
        actor_id="operator-1",
    )


# --------------------------------------------------------------------------- #
# C08-03 — the late worker race, physically
# --------------------------------------------------------------------------- #


def test_c08_03_late_revision_one_worker_cannot_overwrite_revision_two() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            worker = ContractReprojectionWorker(fixture.db)

            claim = await worker.claim(CD, revision=1, worker_id="W1")
            assert claim is not None

            # The operator corrects the classification mid-flight.
            new_revision = await _advance_to(
                fixture, expected=1, to_type=ContractDocumentType.PARTICULAR_CONDITIONS
            )
            assert new_revision == 2

            # W1 finishes late and tries to publish generation 1.
            with pytest.raises(StaleWorkerGeneration):
                await worker.complete(claim)

            record = await _record(fixture)
            assert record["classification_revision"] == 2
            assert record["projection_revision"] != 1, (
                "a late revision-1 worker published its generation over revision 2"
            )
            assert record["projection_status"] != ProjectionStatus.CURRENT.value
        finally:
            await _drop(fixture)

    _run(scenario())


def test_the_current_revision_worker_completes_normally() -> None:
    """The positive case, so the fence is not simply refusing everything."""

    async def scenario() -> None:
        fixture = await _fresh()
        try:
            worker = ContractReprojectionWorker(fixture.db)
            claim = await worker.claim(CD, revision=1, worker_id="W1")
            assert claim is not None

            await worker.complete(claim)

            record = await _record(fixture)
            assert record["projection_revision"] == 1
            assert record["projection_status"] == ProjectionStatus.CURRENT.value
            assert record["classification_revision"] == 1
        finally:
            await _drop(fixture)

    _run(scenario())


def test_claiming_marks_in_progress_but_not_current() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await ContractReprojectionWorker(fixture.db).claim(CD, revision=1, worker_id="W1")
            record = await _record(fixture)
            assert record["projection_status"] == ProjectionStatus.IN_PROGRESS.value
            assert record["projection_revision"] != 1, (
                "claiming published the generation before any work was done"
            )
        finally:
            await _drop(fixture)

    _run(scenario())


def test_a_worker_cannot_claim_a_revision_that_is_no_longer_current() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _advance_to(fixture, expected=1, to_type=ContractDocumentType.AMENDMENT)
            claim = await ContractReprojectionWorker(fixture.db).claim(
                CD, revision=1, worker_id="W-late"
            )
            assert claim is None, "a worker claimed a superseded generation"
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Failure writes are fenced too, not only success
# --------------------------------------------------------------------------- #


def test_stale_worker_failure_handler_cannot_overwrite_a_newer_generation() -> None:
    """A failing late worker must not stamp FAILED over revision 2.

    Fencing only the success path would let a crashed revision-1 worker mark the
    live generation failed — the same regression wearing the other mask.
    """

    async def scenario() -> None:
        fixture = await _fresh()
        try:
            worker = ContractReprojectionWorker(fixture.db)
            claim = await worker.claim(CD, revision=1, worker_id="W1")
            assert claim is not None

            await _advance_to(fixture, expected=1, to_type=ContractDocumentType.ADDENDUM)
            before = await _record(fixture)

            with pytest.raises(StaleWorkerGeneration):
                await worker.fail(claim, reason="embedder unavailable")

            after = await _record(fixture)
            assert after["classification_revision"] == 2
            assert after["projection_status"] == before["projection_status"]
        finally:
            await _drop(fixture)

    _run(scenario())


def test_current_revision_worker_failure_is_recorded() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            worker = ContractReprojectionWorker(fixture.db)
            claim = await worker.claim(CD, revision=1, worker_id="W1")
            assert claim is not None

            await worker.fail(claim, reason="chunking crashed")

            record = await _record(fixture)
            assert record["projection_status"] == ProjectionStatus.FAILED.value
            assert record["classification_revision"] == 1
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# C08-04 — derived failure never touches the legal fact
# --------------------------------------------------------------------------- #


def test_c08_04_reprojection_failure_leaves_classification_untouched() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _advance_to(fixture, expected=1, to_type=ContractDocumentType.AMENDMENT)
            worker = ContractReprojectionWorker(fixture.db)
            claim = await worker.claim(CD, revision=2, worker_id="W2")
            assert claim is not None

            await worker.fail(claim, reason="qdrant unavailable")

            record = await _record(fixture)
            assert record["classification_revision"] == 2
            assert record["contract_document_type"] == ContractDocumentType.AMENDMENT.value
            assert record["projection_status"] == ProjectionStatus.FAILED.value
        finally:
            await _drop(fixture)

    _run(scenario())


def test_worker_success_does_not_alter_classification_authority() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            worker = ContractReprojectionWorker(fixture.db)
            claim = await worker.claim(CD, revision=1, worker_id="W1")
            await worker.complete(claim)

            record = await _record(fixture)
            assert record["classification_revision"] == 1
            assert record["contract_document_type"] == ContractDocumentType.GENERAL_CONDITIONS.value
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Duplicates, retries and idempotency
# --------------------------------------------------------------------------- #


def test_two_workers_cannot_both_claim_the_same_generation() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            worker = ContractReprojectionWorker(fixture.db)
            first = await worker.claim(CD, revision=1, worker_id="W1")
            second = await worker.claim(CD, revision=1, worker_id="W2")
            assert first is not None
            assert second is None, "two workers claimed the same generation"
        finally:
            await _drop(fixture)

    _run(scenario())


def test_a_second_completion_does_not_regress_projection_state() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            worker = ContractReprojectionWorker(fixture.db)
            claim = await worker.claim(CD, revision=1, worker_id="W1")
            await worker.complete(claim)
            first = await _record(fixture)

            await worker.complete(claim)  # idempotent replay
            second = await _record(fixture)

            assert second["projection_revision"] == first["projection_revision"]
            assert second["projection_status"] == first["projection_status"]
        finally:
            await _drop(fixture)

    _run(scenario())


def test_retry_for_a_superseded_revision_does_not_revive_it() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            worker = ContractReprojectionWorker(fixture.db)
            claim = await worker.claim(CD, revision=1, worker_id="W1")
            await worker.fail(claim, reason="transient")

            await _advance_to(fixture, expected=1, to_type=ContractDocumentType.AMENDMENT)

            retried = await worker.claim(CD, revision=1, worker_id="W1-retry")
            assert retried is None, "a retry revived a superseded generation"

            record = await _record(fixture)
            assert record["classification_revision"] == 2
            assert record["projection_revision"] != 1
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# C08-12 / DEBT-01 / DEBT-05 — the shape of what the worker produces
# --------------------------------------------------------------------------- #


def test_c08_12_clause_identity_contains_no_classification_component() -> None:
    """Retries must converge on the same rows.

    If identity carried the classification, every correction would fork the
    clause set and a retry would create a parallel one instead of replacing it.
    """
    worker = ContractReprojectionWorker(None)
    first = worker.clause_identity(
        organization_id=ORG, document_id=DOC, clause_no="8.4", chunk_index=0
    )
    second = worker.clause_identity(
        organization_id=ORG, document_id=DOC, clause_no="8.4", chunk_index=0
    )
    assert first == second

    # The load-bearing half: identity CANNOT vary with classification, because
    # the signature has no way to accept it. A substring check against a hash
    # would prove nothing — a digest contains arbitrary characters.
    import inspect

    parameters = set(inspect.signature(worker.clause_identity).parameters)
    forbidden = {
        "revision",
        "classification_revision",
        "contract_document_type",
        "document_type",
        "source_classification_revision",
    }
    assert not (parameters & forbidden), (
        f"clause identity accepts a classification component: {parameters & forbidden}. "
        "Every correction would fork the clause set and retries would stop converging."
    )


def test_projected_clause_rows_carry_no_instrument_type_copy() -> None:
    """DEBT-01, for rows this worker produces.

    A derived copy of the authoritative type is what creates the staleness
    problem; consumers join to the instrument record instead.
    """
    worker = ContractReprojectionWorker(None)
    row = worker.build_clause_row(
        organization_id=ORG,
        document_id=DOC,
        clause_no="8.4",
        chunk_index=0,
        text="Extension of Time",
        source_classification_revision=1,
        ingest_project_id="project-1",
        ingest_contract_id="primary",
    )
    assert "document_type" not in row
    assert "contract_document_type" not in row
    assert row["source_classification_revision"] == 1


def test_projected_clause_rows_are_document_scoped_with_provenance_only() -> None:
    """DEBT-05. An organisation-owned instrument has no project or contract, so
    scope cannot be a requirement of the clause row."""
    worker = ContractReprojectionWorker(None)
    row = worker.build_clause_row(
        organization_id=ORG,
        document_id=DOC,
        clause_no="8.4",
        chunk_index=0,
        text="Extension of Time",
        source_classification_revision=1,
        ingest_project_id=None,
        ingest_contract_id=None,
    )
    assert row["document_id"] == DOC
    assert row["organization_id"] == ORG
    assert row["ingest_project_id"] is None
    assert row["ingest_contract_id"] is None


def test_worker_writes_no_evidence_readiness_flag() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            worker = ContractReprojectionWorker(fixture.db)
            claim = await worker.claim(CD, revision=1, worker_id="W1")
            await worker.complete(claim)

            record = await _record(fixture)
            banned = {
                "evidence_ready",
                "evidence_capable",
                "ready_for_ai",
                "searchable",
                "is_current_for_evidence",
            }
            assert not (set(record) & banned)
        finally:
            await _drop(fixture)

    _run(scenario())


def test_claim_state_is_not_stored_on_the_authoritative_record() -> None:
    """Worker ownership is operational. It must not sit beside legal fields."""

    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await ContractReprojectionWorker(fixture.db).claim(CD, revision=1, worker_id="W1")
            record = await _record(fixture)
            for operational in ("owner_token", "worker_id", "lease_expires_at", "claimed_at"):
                assert operational not in record
        finally:
            await _drop(fixture)

    _run(scenario())
