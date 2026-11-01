"""Analysis provenance and the evidence consumer boundary.

A-37 - a provenance-write failure prevents the accepted state.

The distinction this suite defends is between what *may* participate and what
*did*. Eligibility answers the first; provenance answers the second, and only
the consumer can answer it, because ranking, truncation and prompt assembly all
happen after the resolver has finished. A resolver-written record would be a
superset wearing the shape of an audit trail.

The failure test uses real Mongo because the interesting case is a write that
genuinely fails at the database, not a mock that returns an error object: the
question is whether the artefact can reach `accepted` when the record does not
exist, and only a real failed write proves the ordering.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_PROVENANCE_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_PROVENANCE_MONGODB_URI is not set; this suite needs a disposable Mongo",
        allow_module_level=True,
    )

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rbac_backend.models.contract_document import (  # noqa: E402
    ApplicableInstrument,
    ContractDocumentType,
    CurrentState,
    Historical,
)
from rbac_backend.services.contract_analysis_provenance import (  # noqa: E402
    PROVENANCE_COLLECTION,
    AcceptedStateRefused,
    AnalysisProvenanceService,
    ConsumedEvidence,
    ProvenanceIncomplete,
)

ORG = "org-provenance"
PROJECT = "project-provenance"
CONTRACT = "contract-provenance"


@asynccontextmanager
async def _database():
    client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=4000)
    name = f"contract_provenance_{uuid.uuid4().hex[:10]}"
    try:
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()


def _instrument(document_id: str = "doc-1") -> ApplicableInstrument:
    return ApplicableInstrument(
        contract_document_id=f"cd-{document_id}",
        document_id=document_id,
        document_version_id=f"{document_id}-v3",
        organization_id=ORG,
        project_id=PROJECT,
        contract_id=CONTRACT,
        contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
        classification_revision=4,
        applicability_event_id="event-1",
    )


def _consumed(**overrides) -> ConsumedEvidence:
    payload = {
        "instruments": (_instrument(),),
        "query_mode": CurrentState(),
        "retrieval_status": "complete",
        "degraded_sources": (),
        "legal_effect_references": ("effect-1",),
    }
    payload.update(overrides)
    return ConsumedEvidence(**payload)


# --------------------------------------------------------------------------- #
# A-37
# --------------------------------------------------------------------------- #


class BrokenProvenanceService(AnalysisProvenanceService):
    async def _persist(self, record):
        raise RuntimeError("provenance write failed")


def test_a_failed_provenance_write_prevents_the_accepted_state():
    async def scenario():
        async with _database() as db:
            service = BrokenProvenanceService(db)
            accepted: List[str] = []

            async def accept(run_id: str) -> None:
                accepted.append(run_id)

            with pytest.raises(AcceptedStateRefused):
                await service.record_and_accept(
                    analysis_run_id="run-1",
                    consumer_type="letter_drafting",
                    organization_id=ORG,
                    project_id=PROJECT,
                    contract_id=CONTRACT,
                    consumed=_consumed(),
                    accept=accept,
                )

            # The artefact never reached accepted, and nothing was written.
            assert accepted == []
            assert await db[PROVENANCE_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_the_failure_is_surfaced_not_swallowed():
    async def scenario():
        async with _database() as db:
            with pytest.raises(AcceptedStateRefused) as excinfo:
                await BrokenProvenanceService(db).record_and_accept(
                    analysis_run_id="run-1",
                    consumer_type="letter_drafting",
                    organization_id=ORG,
                    project_id=PROJECT,
                    contract_id=CONTRACT,
                    consumed=_consumed(),
                    accept=lambda run_id: asyncio.sleep(0),
                )
            assert "provenance write failed" in str(excinfo.value)

    asyncio.run(scenario())


def test_provenance_is_written_before_the_artefact_is_accepted():
    async def scenario():
        async with _database() as db:
            service = AnalysisProvenanceService(db)
            order: List[str] = []

            async def accept(run_id: str) -> None:
                # If provenance had not landed yet, this count would be zero.
                order.append(
                    "provenance-first"
                    if await db[PROVENANCE_COLLECTION].count_documents({}) == 1
                    else "accepted-first"
                )

            await service.record_and_accept(
                analysis_run_id="run-1",
                consumer_type="letter_drafting",
                organization_id=ORG,
                project_id=PROJECT,
                contract_id=CONTRACT,
                consumed=_consumed(),
                accept=accept,
            )

            assert order == ["provenance-first"]

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# the mandatory field set
# --------------------------------------------------------------------------- #


def test_the_record_carries_the_mandatory_field_set():
    async def scenario():
        async with _database() as db:
            await AnalysisProvenanceService(db).record_and_accept(
                analysis_run_id="run-1",
                consumer_type="letter_drafting",
                organization_id=ORG,
                project_id=PROJECT,
                contract_id=CONTRACT,
                consumed=_consumed(),
                accept=None,
            )

            record = await db[PROVENANCE_COLLECTION].find_one({})
            for field in (
                "analysis_run_id",
                "consumer_type",
                "organization_id",
                "project_id",
                "contract_id",
                "query_mode",
                "retrieval_status",
                "degraded_sources",
                "legal_effect_references",
                "retrieved_at",
                "instruments",
            ):
                assert field in record, field

            instrument = record["instruments"][0]
            assert instrument["contract_document_id"] == "cd-doc-1"
            assert instrument["document_id"] == "doc-1"
            assert instrument["document_version_id"] == "doc-1-v3"
            assert instrument["classification_revision"] == 4
            assert instrument["applicability_event_id"] == "event-1"

    asyncio.run(scenario())


def test_historical_mode_records_its_event_date():
    from datetime import date

    async def scenario():
        async with _database() as db:
            await AnalysisProvenanceService(db).record_and_accept(
                analysis_run_id="run-1",
                consumer_type="letter_drafting",
                organization_id=ORG,
                project_id=PROJECT,
                contract_id=CONTRACT,
                consumed=_consumed(query_mode=Historical(event_date=date(2021, 5, 4))),
                accept=None,
            )

            record = await db[PROVENANCE_COLLECTION].find_one({})
            assert record["query_mode"] == "historical"
            assert record["event_date"] == "2021-05-04"

    asyncio.run(scenario())


def test_no_document_bytes_are_persisted():
    async def scenario():
        async with _database() as db:
            await AnalysisProvenanceService(db).record_and_accept(
                analysis_run_id="run-1",
                consumer_type="letter_drafting",
                organization_id=ORG,
                project_id=PROJECT,
                contract_id=CONTRACT,
                consumed=_consumed(),
                accept=None,
            )

            record = await db[PROVENANCE_COLLECTION].find_one({})
            serialised = repr(record)
            for forbidden in ("text", "text_enriched", "full_clause_text", "content", "bytes"):
                assert forbidden not in serialised

    asyncio.run(scenario())


def test_an_incomplete_consumed_record_is_refused():
    """A run that cannot say what it used must not be recorded as if it could."""

    async def scenario():
        async with _database() as db:
            with pytest.raises(ProvenanceIncomplete):
                await AnalysisProvenanceService(db).record_and_accept(
                    analysis_run_id="run-1",
                    consumer_type="letter_drafting",
                    organization_id=ORG,
                    project_id=PROJECT,
                    contract_id=CONTRACT,
                    consumed=_consumed(instruments=()),
                    accept=None,
                )
            assert await db[PROVENANCE_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_only_applicable_instruments_are_accepted_as_consumed_evidence():
    with pytest.raises(TypeError):
        ConsumedEvidence(
            instruments=({"document_id": "doc-1"},),  # type: ignore[arg-type]
            query_mode=CurrentState(),
            retrieval_status="complete",
            degraded_sources=(),
            legal_effect_references=(),
        )


# --------------------------------------------------------------------------- #
# the consumer writes it, not the resolver
# --------------------------------------------------------------------------- #


def test_the_resolver_does_not_write_provenance():
    import inspect

    from rbac_backend.services import contract_scope_resolver

    source = inspect.getsource(contract_scope_resolver)
    assert PROVENANCE_COLLECTION not in source
    assert "AnalysisProvenanceService" not in source


def test_the_provenance_service_records_what_was_used_not_what_was_resolved():
    """The signature takes consumed evidence; it cannot be handed a resolver."""
    import inspect

    parameters = set(
        inspect.signature(AnalysisProvenanceService.record_and_accept).parameters
    )
    assert "consumed" in parameters
    assert "resolver" not in parameters
    assert "resolved" not in parameters


# --------------------------------------------------------------------------- #
# interactive Q&A: unrecorded, and therefore not saveable
# --------------------------------------------------------------------------- #


def test_interactive_use_may_proceed_marked_unrecorded():
    async def scenario():
        async with _database() as db:
            outcome = await AnalysisProvenanceService(db).interactive(consumed=_consumed())
            assert outcome.recorded is False
            assert outcome.provenance_state == "unrecorded"
            assert await db[PROVENANCE_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_an_unrecorded_answer_cannot_be_saved_exported_or_cited():
    async def scenario():
        async with _database() as db:
            outcome = await AnalysisProvenanceService(db).interactive(consumed=_consumed())
            for action in ("save", "export", "cite"):
                with pytest.raises(AcceptedStateRefused):
                    outcome.require_persistable(action)

    asyncio.run(scenario())


def test_a_recorded_answer_may_be_saved():
    async def scenario():
        async with _database() as db:
            service = AnalysisProvenanceService(db)
            await service.record_and_accept(
                analysis_run_id="run-1",
                consumer_type="contract_qa",
                organization_id=ORG,
                project_id=PROJECT,
                contract_id=CONTRACT,
                consumed=_consumed(),
                accept=None,
            )
            outcome = await service.interactive(consumed=_consumed(), analysis_run_id="run-1")
            assert outcome.recorded is True
            outcome.require_persistable("save")

    asyncio.run(scenario())
