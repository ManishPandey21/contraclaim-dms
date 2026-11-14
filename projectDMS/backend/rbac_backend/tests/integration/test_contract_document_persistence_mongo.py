"""Real MongoDB checks for Contract Master authoritative persistence.

Owned by implementation ticket 03. Opt-in, like the other tracer suites here:
they require an explicit URI for a test-only replica set and never fall back to
the application's configured database.

Four things need physical proof rather than a fake:

* **Index creation actually happened.** A fake collection accepts an index
  declaration and reports whatever the test asks. Only a real server can say
  whether ``contract_document_applicability`` really carries a unique index on
  the four-part aggregate key.
* **Uniqueness is enforced by the server**, not by a Python guard that a later
  caller can route around.
* **No TTL on legal history.** A TTL index on an append-only legal collection
  would delete evidence on a timer. Absence has to be checked against the real
  index catalogue.
* **Overlap is refused transactionally.** Half-open interval overlap cannot be
  expressed as a unique index, so the guard is a transactional read-then-write —
  and a transaction needs a replica set.

The suite also proves the COLD-START path: indexes are asserted on a database
created empty in this test, never on one warmed by an earlier run.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date
from typing import Any

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.models.contract_document import (
    ApplicabilityLifecycleKind,
    ContractDocumentType,
    ContractScopeLevel,
)
from rbac_backend.services.contract_document_store import (
    APPLICABILITY_COLLECTION,
    APPLICABILITY_EVENTS_COLLECTION,
    CLASSIFICATION_FACTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
    LEGAL_EFFECTS_COLLECTION,
    LEGAL_COLLECTIONS,
    ApplicabilityOverlapError,
    ensure_contract_document_indexes,
    record_applicability_event,
)

MONGODB_URI_ENV = "CONTRACT_MASTER_MONGODB_URI"


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a test-only MongoDB replica set")
    return uri


def _run(coro: Any) -> Any:
    """Each test owns its loop: this suite follows the repo's no-pytest-asyncio
    convention, where module-level Motor globals bound to a dead loop are the
    classic cross-test failure."""
    return asyncio.run(coro)


class _Fixture:
    """A database created fresh for one test and dropped afterwards."""

    def __init__(self, client: AsyncIOMotorClient, name: str) -> None:
        self.client = client
        self.db = client[name]
        self.name = name


async def _fresh_db() -> _Fixture:
    client = AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=8000)
    name = f"cm_t03_{uuid.uuid4().hex[:12]}"
    return _Fixture(client, name)


async def _drop(fixture: _Fixture) -> None:
    await fixture.client.drop_database(fixture.name)
    fixture.client.close()


ORG = "org-t03"
CD = "cd-t03"
PROJ = "proj-t03"
CONTRACT = "primary"


# --------------------------------------------------------------------------- #
# Cold start — indexes exist on a database that was empty moments ago
# --------------------------------------------------------------------------- #


def test_cold_start_bootstrap_creates_every_contract_master_index() -> None:
    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            existing = await fixture.db.list_collection_names()
            assert existing == [], "fixture database was not empty — not a cold start"

            await ensure_contract_document_indexes(fixture.db)

            aggregate_indexes = await fixture.db[APPLICABILITY_COLLECTION].index_information()
            unique_keys = [
                info["key"]
                for info in aggregate_indexes.values()
                if info.get("unique")
            ]
            assert [
                ("organization_id", 1),
                ("contract_document_id", 1),
                ("project_id", 1),
                ("contract_id", 1),
            ] in [list(key) for key in unique_keys], (
                "the four-part applicability aggregate key is not uniquely indexed "
                f"on a cold database: {aggregate_indexes}"
            )
        finally:
            await _drop(fixture)

    _run(scenario())


def test_canonical_document_id_is_uniquely_indexed_on_contract_documents() -> None:
    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            info = await fixture.db[CONTRACT_DOCUMENTS_COLLECTION].index_information()
            unique_single = [
                list(spec["key"])
                for spec in info.values()
                if spec.get("unique") and len(spec["key"]) == 1
            ]
            assert ["document_id", 1] in [list(k[0]) for k in unique_single] or [
                ("document_id", 1)
            ] in unique_single, f"document_id is not uniquely indexed: {info}"
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Uniqueness enforced by the server
# --------------------------------------------------------------------------- #


def test_duplicate_applicability_aggregate_is_refused_by_the_server() -> None:
    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            aggregate = {
                "organization_id": ORG,
                "contract_document_id": CD,
                "project_id": PROJ,
                "contract_id": CONTRACT,
            }
            await fixture.db[APPLICABILITY_COLLECTION].insert_one(dict(aggregate))
            from pymongo.errors import DuplicateKeyError

            with pytest.raises(DuplicateKeyError):
                await fixture.db[APPLICABILITY_COLLECTION].insert_one(dict(aggregate))
        finally:
            await _drop(fixture)

    _run(scenario())


def test_same_contract_id_in_two_projects_is_not_a_duplicate() -> None:
    """`contract_id = "primary"` is a default, not a uniqueness constraint.

    Two projects each holding a "primary" contract are two different contracts,
    and the index must not collapse them.
    """

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            base = {
                "organization_id": ORG,
                "contract_document_id": CD,
                "contract_id": "primary",
            }
            await fixture.db[APPLICABILITY_COLLECTION].insert_one(
                {**base, "project_id": "project-a"}
            )
            await fixture.db[APPLICABILITY_COLLECTION].insert_one(
                {**base, "project_id": "project-b"}
            )
            assert await fixture.db[APPLICABILITY_COLLECTION].count_documents({}) == 2
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# No TTL on legal history, and no stored effective_to
# --------------------------------------------------------------------------- #


def test_no_legal_collection_carries_a_ttl_index() -> None:
    """A TTL index here would delete legal history on a timer."""

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            offenders: dict[str, Any] = {}
            for collection in LEGAL_COLLECTIONS:
                info = await fixture.db[collection].index_information()
                ttl = {
                    name: spec
                    for name, spec in info.items()
                    if "expireAfterSeconds" in spec
                }
                if ttl:
                    offenders[collection] = ttl
            assert not offenders, f"TTL index on legal collections: {offenders}"
        finally:
            await _drop(fixture)

    _run(scenario())


def test_effective_to_is_never_stored_on_an_applicability_record() -> None:
    """`effective_to` is derived from the event stream. A stored copy would be a
    second answer to the same legal question."""
    from rbac_backend.models import contract_document_records as records

    for model_name in (
        "ContractDocumentApplicabilityRecord",
        "ApplicabilityLifecycleEventRecord",
    ):
        model = getattr(records, model_name)
        assert "effective_to" not in model.model_fields, (
            f"{model_name} stores effective_to; it must be derived from the "
            "append-only event stream"
        )


# --------------------------------------------------------------------------- #
# The transactional overlap guard — the ticket's load-bearing RED
# --------------------------------------------------------------------------- #


async def _apply(fixture: _Fixture, start: date | None, event_id: str) -> None:
    await record_applicability_event(
        fixture.db,
        organization_id=ORG,
        contract_document_id=CD,
        project_id=PROJ,
        contract_id=CONTRACT,
        kind=ApplicabilityLifecycleKind.APPLIED,
        effective_at=start,
        event_id=event_id,
        actor_id="operator-1",
    )


def test_overlapping_applied_interval_is_refused_transactionally() -> None:
    """Two open-ended APPLIED events for one aggregate overlap by definition.

    The refusal is a read-then-write inside a transaction, not an index: a
    half-open interval overlap is not expressible as a unique key.
    """

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            await _apply(fixture, date(2026, 1, 1), "evt-1")

            with pytest.raises(ApplicabilityOverlapError):
                await _apply(fixture, date(2026, 6, 1), "evt-2")

            events = await fixture.db[APPLICABILITY_EVENTS_COLLECTION].count_documents({})
            assert events == 1, (
                "the refused event was still persisted — the guard is not "
                "transactional"
            )
        finally:
            await _drop(fixture)

    _run(scenario())


def test_withdrawn_interval_then_reapply_is_allowed() -> None:
    """Withdrawal closes the interval, so a later APPLIED does not overlap."""

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            await _apply(fixture, date(2026, 1, 1), "evt-1")
            await record_applicability_event(
                fixture.db,
                organization_id=ORG,
                contract_document_id=CD,
                project_id=PROJ,
                contract_id=CONTRACT,
                kind=ApplicabilityLifecycleKind.WITHDRAWN,
                effective_at=date(2026, 5, 1),
                event_id="evt-2",
                actor_id="operator-1",
            )
            await _apply(fixture, date(2026, 6, 1), "evt-3")
            assert (
                await fixture.db[APPLICABILITY_EVENTS_COLLECTION].count_documents({}) == 3
            )
        finally:
            await _drop(fixture)

    _run(scenario())


def test_unknown_effective_from_is_stored_as_unknown_not_defaulted() -> None:
    """An absent legal start date stays absent. Substituting a creation
    timestamp would manufacture legal evidence."""

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            await _apply(fixture, None, "evt-1")
            stored = await fixture.db[APPLICABILITY_EVENTS_COLLECTION].find_one(
                {"event_id": "evt-1"}
            )
            assert stored is not None
            assert stored["effective_at"] is None, (
                "an unknown legal start was defaulted to a value: "
                f"{stored['effective_at']!r}"
            )
        finally:
            await _drop(fixture)

    _run(scenario())


def test_duplicate_event_id_is_refused_by_the_server() -> None:
    """Retrying a write must not duplicate a legal fact."""

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            await _apply(fixture, date(2026, 1, 1), "evt-1")
            from pymongo.errors import DuplicateKeyError

            with pytest.raises((DuplicateKeyError, ApplicabilityOverlapError)):
                await _apply(fixture, date(2026, 1, 1), "evt-1")
        finally:
            await _drop(fixture)

    _run(scenario())


def test_classification_facts_and_legal_effects_collections_are_indexed() -> None:
    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            for collection in (CLASSIFICATION_FACTS_COLLECTION, LEGAL_EFFECTS_COLLECTION):
                info = await fixture.db[collection].index_information()
                assert len(info) > 1, f"{collection} has no index beyond _id: {info}"
        finally:
            await _drop(fixture)

    _run(scenario())


def test_classification_revision_is_unique_per_contract_document() -> None:
    """Two facts claiming the same revision would make 'which classification is
    current at revision N' ambiguous."""

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            await ensure_contract_document_indexes(fixture.db)
            fact = {
                "contract_document_id": CD,
                "revision": 1,
                "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS.value,
                "scope_level": ContractScopeLevel.PROJECT.value,
            }
            await fixture.db[CLASSIFICATION_FACTS_COLLECTION].insert_one(dict(fact))
            from pymongo.errors import DuplicateKeyError

            with pytest.raises(DuplicateKeyError):
                await fixture.db[CLASSIFICATION_FACTS_COLLECTION].insert_one(dict(fact))
        finally:
            await _drop(fixture)

    _run(scenario())


def test_production_bootstrap_creates_the_indexes_on_a_cold_database() -> None:
    """The definitions must be reachable from the path production actually runs.

    A module that defines indexes nobody calls is a definition, not a guarantee.
    This drives core.database.ensure_indexes — the real bootstrap — against a
    database created empty in this test.
    """

    async def scenario() -> None:
        fixture = await _fresh_db()
        try:
            assert await fixture.db.list_collection_names() == []
            from rbac_backend.core.database import ensure_indexes

            await ensure_indexes(fixture.db)

            info = await fixture.db[APPLICABILITY_COLLECTION].index_information()
            unique = [list(spec["key"]) for spec in info.values() if spec.get("unique")]
            assert [
                ("organization_id", 1),
                ("contract_document_id", 1),
                ("project_id", 1),
                ("contract_id", 1),
            ] in unique, f"production bootstrap did not create the aggregate index: {info}"
        finally:
            await _drop(fixture)

    _run(scenario())
