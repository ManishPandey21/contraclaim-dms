"""Contract evidence mode: one eligible universe, three sources, real Mongo.

The invariant this pins is **parity**, and it is not the same as three passing
unit suites. Lexical, vector and graph may differ in ranking, scoring and
topology; they may not differ in which canonical documents are legally allowed
to participate. A test that only proves each source is individually contained
would still pass in a build where the orchestrator handed two of them the
eligible set and forgot the third.

So every scenario here drives the live evidence entry point end to end, with a
foreign document present *physically in all three stores at once*, and asserts
the fused output.

`A-02` is re-verified rather than re-implemented: publication authority already
runs before fusion, `total_count` and pagination, and that ordering is
deliberately untouched. The defect this cutover closes is earlier — candidate
capacity — so the assertions are about which documents reach fusion at all.

Mongo is a disposable instance addressed by `CONTRACT_EVIDENCE_MONGODB_URI`; the
suite skips cleanly when it is unset and never touches a running service.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_EVIDENCE_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_EVIDENCE_MONGODB_URI is not set; this suite needs a disposable Mongo",
        allow_module_level=True,
    )

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rbac_backend.models.contract_document import CurrentState  # noqa: E402
from rbac_backend.models.contract_models import ContractSearchRequest  # noqa: E402
from rbac_backend.models.processing_state import ProcessingState  # noqa: E402
from rbac_backend.retrieval.vector_client import VectorClient  # noqa: E402
from rbac_backend.services.contract_document_store import (  # noqa: E402
    APPLICABILITY_COLLECTION,
    APPLICABILITY_EVENTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
)
from rbac_backend.services.contract_graph_service import ContractGraphService  # noqa: E402
from rbac_backend.services.contract_scope_resolver import (  # noqa: E402
    AuthorizedContractScope,
    ContractScopeResolver,
)
from rbac_backend.services.contract_service import (  # noqa: E402
    ContractEvidenceAuthorityFailure,
    ContractService,
)

ORG = "org-evidence"
PROJECT = "project-evidence"
CONTRACT = "contract-evidence"

GOOD = "doc-good"
FOREIGN = "doc-foreign"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def _database():
    """A fresh database per test. Async fixtures are unsupported in this suite."""
    client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=4000)
    name = f"contract_evidence_{uuid.uuid4().hex[:10]}"
    try:
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()


def _clause_row(document_id: str, clause_number: str, text: str, position: int) -> Dict[str, Any]:
    return {
        "_id": f"{document_id}:{clause_number}",
        "uploadType": "contract",
        "organization_id": ORG,
        "project_id": PROJECT,
        "document_id": document_id,
        "upload_id": document_id,
        "clause_number": clause_number,
        "clause_start_position": position,
        "text": text,
        "text_enriched": text,
        "is_current": True,
        "is_authorised_for_ai": True,
    }


async def _seed(db, *, eligible_documents: List[str], foreign_lexical_rows: int = 6):
    """One applicable instrument, plus a foreign document present everywhere."""
    await db.documents.insert_many(
        [
            {
                "_id": GOOD,
                "organization_id": ORG,
                "project_id": PROJECT,
                "publication_status": "published",
                "is_active": True,
            },
            {
                "_id": FOREIGN,
                "organization_id": ORG,
                "project_id": PROJECT,
                "publication_status": "published",
                "is_active": True,
            },
        ]
    )

    for document_id in eligible_documents:
        await db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
            {
                "_id": document_id,
                "organization_id": ORG,
                "document_id": document_id,
                "contract_document_type": "contract_agreement",
                "classification_revision": 1,
                "projection_revision": 1,
                "projection_status": "CURRENT",
            }
        )
        applicability_id = f"applicability:{document_id}"
        await db[APPLICABILITY_COLLECTION].insert_one(
            {
                "_id": applicability_id,
                "organization_id": ORG,
                "project_id": PROJECT,
                "contract_id": CONTRACT,
                "contract_document_id": document_id,
            }
        )
        # Events are append-only and live in their own collection; applicability
        # is replayed from them rather than stored as a flag.
        await db[APPLICABILITY_EVENTS_COLLECTION].insert_one(
            {
                "_id": f"event:{document_id}",
                "applicability_id": applicability_id,
                "kind": "APPLIED",
                "effective_at": "2020-01-01",
            }
        )

    # Lexical: the foreign document matches the query many times over, the
    # applicable one only once. Under a shared bound the applicable clause is
    # the one that loses.
    rows = [_clause_row(GOOD, "10.1", "variation of the works", 10)]
    for index in range(foreign_lexical_rows):
        rows.append(
            _clause_row(
                FOREIGN,
                f"20.{index}",
                "variation variation variation of the variation works",
                100 + index,
            )
        )
    await db.document_vectors.insert_many(rows)


def _vector_client() -> VectorClient:
    """The repository's in-memory transport: filters, scores, then truncates."""
    client = VectorClient.__new__(VectorClient)
    client.enabled = False
    client._client = None
    client._qmodels = None
    client._memory_index = []
    client.collection_name = "document_vectors"

    def seed(document_id: str, clause_number: str, vector, position: int):
        client._memory_index.append(
            {
                "namespace": "document_vectors",
                "vector": vector,
                "payload": {
                    "chunk_id": f"{document_id}:{clause_number}",
                    "document_id": document_id,
                    "upload_id": document_id,
                    "clause_number": clause_number,
                    "clause_start_position": position,
                    "org_id": ORG,
                    "project_id": PROJECT,
                    "uploadType": "contract",
                },
            }
        )

    # The foreign points are semantically closer than the applicable one.
    for index in range(6):
        seed(FOREIGN, f"20.{index}", [1.0, 0.01 * index], 100 + index)
    seed(GOOD, "10.1", [0.6, 0.8], 10)
    return client


class FakeFalkor:
    """Bounded evaluator of the reader's query shapes: WHERE, then LIMIT."""

    def __init__(self) -> None:
        self.enabled = True
        self.queries: List[str] = []
        self.rows = [
            {
                "doc_id": FOREIGN,
                "clause_number": "20.0",
                "section_id": "s1",
                "clause_start_position": 100 + index,
            }
            for index in range(6)
        ] + [
            {
                "doc_id": GOOD,
                "clause_number": "10.1",
                "section_id": "s1",
                "clause_start_position": 10,
            }
        ]

    def is_enabled(self) -> bool:
        return True

    def _parse_rows(self, rows):
        return list(rows)

    def _execute(self, cypher, params=None, read_only=False, **kwargs):
        self.queries.append(cypher)
        params = params or {}
        rows = [
            row
            for row in self.rows
            if row["clause_number"] in (params.get("seed_clause_numbers") or [])
            or "same_clause_number" not in cypher
        ]
        if params.get("has_eligible_document_ids"):
            allowed = set(params.get("eligible_document_ids") or [])
            rows = [row for row in rows if row["doc_id"] in allowed]
        return [
            {
                "document_id": row["doc_id"],
                "clause_number": row["clause_number"],
                "clause_start_position": row["clause_start_position"],
                "clause_node_id": f"{row['doc_id']}:{row['clause_number']}",
                "graph_relation": "same_clause_number",
            }
            for row in rows[: params.get("limit", 25)]
        ]


def _service(db, vector_client, falkor) -> ContractService:
    service = ContractService()
    service._db = db
    service._vectors = db.document_vectors
    service._documents = db.documents
    service._vector_client = vector_client
    service._graph_service = ContractGraphService(falkor=falkor)
    return service


def _scope() -> AuthorizedContractScope:
    return AuthorizedContractScope.for_tests(
        organization_id=ORG, project_id=PROJECT, contract_id=CONTRACT, actor_id="actor-1"
    )


def _request(**overrides) -> ContractSearchRequest:
    payload = {
        # 'Clause 20.0' seeds the graph leg: without a clause-like token the
        # graph source is never reached and a parity claim would cover only two
        # of the three sources.
        "query": "Clause 20.0 variation",
        "organization_id": ORG,
        "project_id": PROJECT,
        "limit": 3,
        "skip": 0,
    }
    payload.update(overrides)
    return ContractSearchRequest(**payload)


# --------------------------------------------------------------------------- #
# the parity RED
# --------------------------------------------------------------------------- #


def test_all_three_sources_search_one_eligible_universe():
    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[GOOD])
            vector_client = _vector_client()
            falkor = FakeFalkor()
            service = _service(db, vector_client, falkor)

            response = await service.search_contract_evidence(
                _request(),
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
            )

            documents = {row.document_id for row in response.results}
            # The foreign document exists physically in Mongo, in the vector
            # index and in the graph, and outranks the applicable one in all
            # three. None of that buys it a place in the evidence.
            assert documents == {GOOD}
            assert response.total_count == 1

    asyncio.run(scenario())


def test_no_source_can_vote_an_ineligible_document_back_in():
    """Fusion is relevance. It cannot restore what eligibility excluded."""

    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[GOOD])
            service = _service(db, _vector_client(), FakeFalkor())

            response = await service.search_contract_evidence(
                _request(limit=10),
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
            )

            assert FOREIGN not in {row.document_id for row in response.results}

    asyncio.run(scenario())


def test_candidate_capacity_is_closed_for_every_source_at_once():
    """A tiny page is where starvation used to be total."""

    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[GOOD], foreign_lexical_rows=12)
            service = _service(db, _vector_client(), FakeFalkor())

            response = await service.search_contract_evidence(
                _request(limit=1),
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
            )

            assert [row.document_id for row in response.results] == [GOOD]

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# zero eligibility
# --------------------------------------------------------------------------- #


def test_zero_eligibility_is_valid_empty_and_queries_no_derived_store():
    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[])
            vector_client = _vector_client()
            falkor = FakeFalkor()
            service = _service(db, vector_client, falkor)
            calls: List[Any] = []
            original = vector_client.search

            async def counting(*args, **kwargs):
                calls.append(kwargs)
                return await original(*args, **kwargs)

            vector_client.search = counting  # type: ignore[assignment]

            response = await service.search_contract_evidence(
                _request(),
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
            )

            assert response.results == []
            assert response.total_count == 0
            # The dangerous queries are the ones that must never be issued.
            assert calls == []
            assert falkor.queries == []

    asyncio.run(scenario())


def test_zero_eligibility_does_not_widen_to_the_project():
    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[])
            service = _service(db, _vector_client(), FakeFalkor())

            response = await service.search_contract_evidence(
                _request(limit=25),
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
            )

            assert response.results == []

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# authority failure
# --------------------------------------------------------------------------- #


class BrokenResolver:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, scope, mode):
        self.calls += 1
        raise RuntimeError("canonical eligibility could not be resolved")


def test_authority_failure_hard_fails_and_queries_nothing():
    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[GOOD])
            vector_client = _vector_client()
            falkor = FakeFalkor()
            service = _service(db, vector_client, falkor)
            calls: List[Any] = []
            original = vector_client.search

            async def counting(*args, **kwargs):
                calls.append(kwargs)
                return await original(*args, **kwargs)

            vector_client.search = counting  # type: ignore[assignment]

            with pytest.raises(ContractEvidenceAuthorityFailure):
                await service.search_contract_evidence(
                    _request(),
                    scope=_scope(),
                    mode=CurrentState(),
                    resolver=BrokenResolver(),
                )

            # Not an empty result, not a legacy search: nothing was queried.
            assert calls == []
            assert falkor.queries == []

    asyncio.run(scenario())


def test_authority_failure_is_not_reported_as_empty_evidence():
    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[GOOD])
            service = _service(db, _vector_client(), FakeFalkor())

            with pytest.raises(ContractEvidenceAuthorityFailure):
                await service.search_contract_evidence(
                    _request(),
                    scope=_scope(),
                    mode=CurrentState(),
                    resolver=BrokenResolver(),
                )

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# source degradation must not broaden
# --------------------------------------------------------------------------- #


class ExplodingVectorClient:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("qdrant unavailable")


def test_a_failing_source_never_broadens_and_never_admits_a_foreign_document():
    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[GOOD])
            vector_client = ExplodingVectorClient()
            service = _service(db, vector_client, FakeFalkor())

            response = await service.search_contract_evidence(
                _request(limit=10),
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
            )

            assert vector_client.calls == 1
            assert {row.document_id for row in response.results} == {GOOD}

    asyncio.run(scenario())


def test_source_failure_is_visible_and_distinct_from_valid_empty():
    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[GOOD])
            degraded = await _service(db, ExplodingVectorClient(), FakeFalkor()).evidence_source_report(
                _request(),
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
            )
            healthy = await _service(db, _vector_client(), FakeFalkor()).evidence_source_report(
                _request(),
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
            )

            assert degraded["vector"] == "degraded"
            assert healthy["vector"] == "success"
            assert degraded["lexical"] == "success"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# A-02 re-verification and the boundary with generic search
# --------------------------------------------------------------------------- #


def test_publication_authority_still_precedes_fusion_count_and_pagination():
    """A-02. Blocked clauses must not inflate total_count or take a slot."""

    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[GOOD, FOREIGN])
            # HUMAN_REVIEW_REQUIRED is the only adverse processing state;
            # "failed" is operational and deliberately does NOT block.
            await db.documents.update_one(
                {"_id": FOREIGN},
                {"$set": {"processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value}},
            )
            service = _service(db, _vector_client(), FakeFalkor())

            response = await service.search_contract_evidence(
                _request(limit=3),
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
            )

            assert {row.document_id for row in response.results} == {GOOD}
            # The blocked document is gone before the count is taken.
            assert response.total_count == 1
            assert response.has_more is False

    asyncio.run(scenario())


def test_generic_contract_search_is_not_constrained_by_evidence_mode():
    """The activation boundary is explicit; generic search keeps its semantics."""
    import inspect

    from rbac_backend.services import contract_service

    generic = inspect.getsource(contract_service.ContractService.search_contracts)
    assert "eligible_document_ids" not in generic
    assert "search_contract_evidence" not in generic


def test_browse_mode_cannot_produce_evidence():
    async def scenario():
        async with _database() as db:
            from rbac_backend.models.contract_document import Browse

            await _seed(db, eligible_documents=[GOOD])
            service = _service(db, _vector_client(), FakeFalkor())

            with pytest.raises(ContractEvidenceAuthorityFailure):
                await service.search_contract_evidence(
                    _request(),
                    scope=_scope(),
                    mode=Browse(),
                    resolver=ContractScopeResolver(db),
                )

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# no broad fallback anywhere on the cutover path
# --------------------------------------------------------------------------- #


def test_the_evidence_path_has_no_legacy_fallback_branch():
    import inspect

    from rbac_backend.services import contract_service

    source = inspect.getsource(contract_service.ContractService.search_contract_evidence)
    source += inspect.getsource(contract_service.ContractService._evidence_candidates)
    assert "search_contracts(" not in source
    # Every source call must carry the fence; none may be re-issued without it.
    assert source.count("eligible_document_ids") >= 3


def test_capability_modules_were_not_modified_by_the_cutover():
    import inspect

    from rbac_backend.services import (
        contract_graph_containment,
        contract_lexical_containment,
        contract_vector_containment,
    )

    assert sorted(contract_lexical_containment.__all__) == [
        "EvidenceScopeUnbounded",
        "compose_evidence_match_stage",
        "lexical_candidates_for_evidence",
    ]
    assert sorted(contract_vector_containment.__all__) == [
        "EligibilityUnresolved",
        "VectorContainmentResult",
        "compose_vector_filters",
        "vector_candidates_for_evidence",
    ]
    assert sorted(contract_graph_containment.__all__) == [
        "GraphContainmentResult",
        "GraphEligibilityUnresolved",
        "compose_graph_document_scope",
        "contained_related_clauses",
        "stable_clause_node_id",
    ]
    assert inspect.getsource(contract_lexical_containment).count("def ") >= 2


def test_the_graph_leg_actually_participates_in_this_suite():
    """Guards the parity claim itself.

    A scenario whose query yields no clause seed never reaches the graph source,
    so a "three sources agree" assertion would silently be about two. This test
    fails if that ever becomes true again.
    """

    async def scenario():
        async with _database() as db:
            await _seed(db, eligible_documents=[GOOD])
            falkor = FakeFalkor()
            service = _service(db, _vector_client(), falkor)
            request = _request()

            assert service._extract_clause_numbers_for_graph(request)

            _, _, graph_rows, _ = await service._evidence_candidates(
                request,
                scope=_scope(),
                mode=CurrentState(),
                resolver=ContractScopeResolver(db),
                candidate_limit=15,
            )
            assert falkor.queries, "graph source was never queried"
            assert graph_rows, "graph source contributed no candidates"
            assert {row["document_id"] for row in graph_rows} == {GOOD}

    asyncio.run(scenario())
