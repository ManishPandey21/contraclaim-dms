"""Vector containment: the eligible set reaches the store before the limit does.

C08-05 - stale vectors cannot gain authority.

Two facts about the current store make this ticket load-bearing rather than
cosmetic, and both are asserted here from live code rather than assumed:

* the filter builder **drops an empty list condition** instead of emitting a
  match-none, so a zero-eligibility query loses its document fence and searches
  whatever the surviving filters allow;
* the same drop exists in the in-memory transport, which is what makes that
  transport an honest stand-in for the physical proof below.

The in-memory index is a real supported transport in this repository, not a
mock: it applies filters, scores, and only then truncates to the limit. That is
precisely the ordering the capacity proof needs, so the starvation scenario runs
through ``VectorClient.search`` itself.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.retrieval.vector_client import VectorClient
from rbac_backend.services.contract_vector_containment import (
    EligibilityUnresolved,
    VectorContainmentResult,
    compose_vector_filters,
    vector_candidates_for_evidence,
)


# --------------------------------------------------------------------------- #
# transports
# --------------------------------------------------------------------------- #


class RecordingVectorClient:
    """Counts calls. The security invariant is that some queries never happen."""

    def __init__(self, results: Optional[List[Dict[str, Any]]] = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._results = results or []

    async def search(self, query_vector, filters, limit=5, namespace=None, allow_global=False):
        self.calls.append(
            {
                "filters": filters,
                "limit": limit,
                "namespace": namespace,
                "allow_global": allow_global,
            }
        )
        return list(self._results)


class ExplodingVectorClient:
    """Stands in for an unavailable store."""

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("qdrant unavailable")


def _memory_client() -> VectorClient:
    client = VectorClient.__new__(VectorClient)
    client.enabled = False
    client._client = None
    client._qmodels = None
    client._memory_index = []
    client.collection_name = "contract_clauses"
    return client


def _seed(client: VectorClient, chunk_id: str, document_id: str, vector: List[float]) -> None:
    client._memory_index.append(
        {
            "namespace": "contract_clauses",
            "vector": vector,
            "payload": {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "org_id": "org-1",
                "uploadType": "contract",
            },
        }
    )


# --------------------------------------------------------------------------- #
# the hazard, proven from current source rather than from the write-up
# --------------------------------------------------------------------------- #


def test_qdrant_filter_builder_still_drops_an_empty_list_condition():
    """If this ever fails, the hazard was fixed elsewhere and T10 must be re-scoped."""
    qdrant_models = pytest.importorskip("qdrant_client.http.models")

    client = VectorClient.__new__(VectorClient)
    client._qmodels = qdrant_models

    built = client._build_filter({"org_id": "org-1", "document_id": []})

    keys = [condition.key for condition in built.must]
    assert "org_id" in keys
    # The document fence has vanished. What survives is an organisation-wide
    # query - broader than intended, not necessarily globally unbounded.
    assert "document_id" not in keys


def test_in_memory_transport_shares_the_same_empty_list_drop():
    payload = {"document_id": "doc-foreign", "org_id": "org-1"}
    assert VectorClient._matches_filters(payload, {"document_id": [], "org_id": "org-1"}) is True
    assert VectorClient._matches_filters(payload, {"document_id": ["doc-a"]}) is False


# --------------------------------------------------------------------------- #
# empty eligible set: valid empty, and no query issued
# --------------------------------------------------------------------------- #


def test_empty_eligible_set_issues_no_query_and_returns_valid_empty():
    client = RecordingVectorClient(results=[{"score": 1.0, "payload": {"document_id": "doc-x"}}])

    result = asyncio.run(
        vector_candidates_for_evidence(
            client,
            query_vector=[1.0, 0.0],
            filters={"org_id": "org-1", "uploadType": "contract"},
            candidate_limit=5,
            eligible_document_ids=[],
        )
    )

    assert client.calls == []
    assert result.matches == []
    assert result.valid_empty is True
    assert result.degraded is False


def test_empty_eligible_set_is_not_reported_as_failure():
    client = RecordingVectorClient()
    result = asyncio.run(
        vector_candidates_for_evidence(
            client,
            query_vector=[1.0, 0.0],
            filters={"org_id": "org-1"},
            candidate_limit=5,
            eligible_document_ids=[],
        )
    )
    assert isinstance(result, VectorContainmentResult)
    assert result.valid_empty is True
    assert result.failure_reason is None


# --------------------------------------------------------------------------- #
# None is not empty
# --------------------------------------------------------------------------- #


def test_none_eligibility_raises_rather_than_searching_everything():
    client = RecordingVectorClient()

    with pytest.raises(EligibilityUnresolved):
        asyncio.run(
            vector_candidates_for_evidence(
                client,
                query_vector=[1.0, 0.0],
                filters={"org_id": "org-1"},
                candidate_limit=5,
                eligible_document_ids=None,
            )
        )

    assert client.calls == []


def test_missing_organisation_scope_raises_rather_than_searching_globally():
    client = RecordingVectorClient()

    with pytest.raises(EligibilityUnresolved):
        asyncio.run(
            vector_candidates_for_evidence(
                client,
                query_vector=[1.0, 0.0],
                filters={"uploadType": "contract"},
                candidate_limit=5,
                eligible_document_ids=["doc-a"],
            )
        )

    assert client.calls == []


# --------------------------------------------------------------------------- #
# composition is intersection
# --------------------------------------------------------------------------- #


def test_caller_document_narrowing_is_intersected_not_replaced():
    composed = compose_vector_filters(
        {"org_id": "org-1", "document_id": ["D1"]},
        ["D1", "D2"],
    )
    assert composed["document_id"] == ["D1"]


def test_caller_asking_for_an_ineligible_document_gets_nothing():
    composed = compose_vector_filters(
        {"org_id": "org-1", "document_id": ["D9"]},
        ["D1", "D2"],
    )
    assert composed["document_id"] == []


def test_caller_asking_for_an_ineligible_document_issues_no_query():
    client = RecordingVectorClient(results=[{"score": 1.0, "payload": {"document_id": "D9"}}])

    result = asyncio.run(
        vector_candidates_for_evidence(
            client,
            query_vector=[1.0, 0.0],
            filters={"org_id": "org-1", "document_id": ["D9"]},
            candidate_limit=5,
            eligible_document_ids=["D1", "D2"],
        )
    )

    # An empty intersection is the same dangerous shape as an empty eligible
    # set: it must never reach a builder that would drop the condition.
    assert client.calls == []
    assert result.matches == []
    assert result.valid_empty is True


def test_scalar_caller_document_filter_is_also_intersected():
    composed = compose_vector_filters({"org_id": "org-1", "document_id": "D2"}, ["D1", "D2"])
    assert composed["document_id"] == ["D2"]


def test_caller_filters_are_not_mutated():
    caller = {"org_id": "org-1", "document_id": ["D1"]}
    compose_vector_filters(caller, ["D1", "D2"])
    assert caller["document_id"] == ["D1"]


def test_eligible_set_is_applied_when_caller_supplies_no_document_filter():
    composed = compose_vector_filters({"org_id": "org-1"}, ["D1", "D2"])
    assert sorted(composed["document_id"]) == ["D1", "D2"]


# --------------------------------------------------------------------------- #
# physical capacity proof, through the real transport
# --------------------------------------------------------------------------- #


def test_foreign_points_cannot_consume_candidate_capacity():
    """Four closer but ineligible points, one lower-ranked applicable point, K=3."""
    client = _memory_client()
    query = [1.0, 0.0]

    for index in range(4):
        _seed(client, f"chunk-foreign-{index}", f"doc-foreign-{index}", [1.0, 0.02 * index])
    _seed(client, "chunk-applicable", "doc-applicable", [0.6, 0.8])

    # Baseline: without containment the applicable point never survives top-K.
    uncontained = asyncio.run(
        client.search(query, filters={"org_id": "org-1"}, limit=3, namespace="contract_clauses")
    )
    returned = {match["payload"]["document_id"] for match in uncontained}
    assert len(uncontained) == 3
    assert "doc-applicable" not in returned

    # Contained: the same limit, the same ranking, the applicable point present.
    result = asyncio.run(
        vector_candidates_for_evidence(
            client,
            query_vector=query,
            filters={"org_id": "org-1"},
            candidate_limit=3,
            eligible_document_ids=["doc-applicable"],
            namespace="contract_clauses",
        )
    )
    contained = {match["payload"]["document_id"] for match in result.matches}
    assert contained == {"doc-applicable"}


def test_stale_generation_points_cannot_be_returned_once_ineligible():
    """C08-05. The eligible set is the whole authority; the payload is not."""
    client = _memory_client()
    query = [1.0, 0.0]

    # A stale point that still *claims* to be current and authorised.
    client._memory_index.append(
        {
            "namespace": "contract_clauses",
            "vector": [1.0, 0.0],
            "payload": {
                "chunk_id": "chunk-stale",
                "document_id": "doc-stale",
                "org_id": "org-1",
                "is_current": True,
                "is_authorised_for_ai": True,
                "contract_id": "contract-1",
                "source_classification_revision": 7,
            },
        }
    )
    _seed(client, "chunk-live", "doc-live", [0.5, 0.86])

    result = asyncio.run(
        vector_candidates_for_evidence(
            client,
            query_vector=query,
            filters={"org_id": "org-1"},
            candidate_limit=5,
            eligible_document_ids=["doc-live"],
            namespace="contract_clauses",
        )
    )

    returned = {match["payload"]["document_id"] for match in result.matches}
    assert returned == {"doc-live"}


def test_payload_contract_id_cannot_admit_a_document_the_eligible_set_excludes():
    client = _memory_client()
    client._memory_index.append(
        {
            "namespace": "contract_clauses",
            "vector": [1.0, 0.0],
            "payload": {
                "chunk_id": "chunk-liar",
                "document_id": "doc-not-eligible",
                "org_id": "org-1",
                # A row asserting membership of exactly the contract under
                # examination. It is still not evidence.
                "contract_id": "contract-1",
                "project_id": "project-1",
                "document_type": "CONTRACT_AGREEMENT",
            },
        }
    )

    result = asyncio.run(
        vector_candidates_for_evidence(
            client,
            query_vector=[1.0, 0.0],
            filters={"org_id": "org-1", "contract_id": "contract-1"},
            candidate_limit=5,
            eligible_document_ids=["doc-eligible"],
            namespace="contract_clauses",
        )
    )

    assert result.matches == []


# --------------------------------------------------------------------------- #
# failure semantics
# --------------------------------------------------------------------------- #


def test_store_failure_does_not_retry_without_the_fence():
    client = ExplodingVectorClient()

    result = asyncio.run(
        vector_candidates_for_evidence(
            client,
            query_vector=[1.0, 0.0],
            filters={"org_id": "org-1"},
            candidate_limit=5,
            eligible_document_ids=["doc-a"],
        )
    )

    assert client.calls == 1
    assert result.matches == []
    assert result.degraded is True
    assert result.valid_empty is False
    assert result.failure_reason is not None


def test_store_failure_is_distinguishable_from_valid_empty():
    failed = asyncio.run(
        vector_candidates_for_evidence(
            ExplodingVectorClient(),
            query_vector=[1.0, 0.0],
            filters={"org_id": "org-1"},
            candidate_limit=5,
            eligible_document_ids=["doc-a"],
        )
    )
    empty = asyncio.run(
        vector_candidates_for_evidence(
            RecordingVectorClient(),
            query_vector=[1.0, 0.0],
            filters={"org_id": "org-1"},
            candidate_limit=5,
            eligible_document_ids=[],
        )
    )

    assert (failed.degraded, failed.valid_empty) == (True, False)
    assert (empty.degraded, empty.valid_empty) == (False, True)


# --------------------------------------------------------------------------- #
# no cutover, no cross-ticket bleed
# --------------------------------------------------------------------------- #


def test_the_vector_capability_is_reachable_only_from_evidence_mode():
    """Ticket 12 wired this capability in; generic search must not have moved.

    This replaced an absence assertion ("nothing imports it yet") once the
    cutover landed. The surviving invariant is the one that still matters: the
    fence belongs to evidence mode, and the generic search path keeps its own
    unbounded semantics.
    """
    import inspect

    from rbac_backend.services import contract_service

    generic = inspect.getsource(contract_service.ContractService.search_contracts)
    assert "vector_candidates_for_evidence" not in generic
    assert "eligible_document_ids" not in generic

    evidence = inspect.getsource(contract_service.ContractService._evidence_candidates)
    assert "vector_candidates_for_evidence" in evidence
    assert "eligible_document_ids=eligible" in evidence


def test_lexical_containment_does_not_import_the_vector_capability():
    """The three sources stay independently testable."""
    import inspect

    from rbac_backend.services import contract_lexical_containment

    source = inspect.getsource(contract_lexical_containment)
    assert "contract_vector_containment" not in source
    assert sorted(contract_lexical_containment.__all__) == [
        "EvidenceScopeUnbounded",
        "compose_evidence_match_stage",
        "lexical_candidates_for_evidence",
    ]
