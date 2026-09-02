"""Graph candidate containment for contract evidence.

The graph is **derived topology**. It is not authority for contract identity,
instrument type, applicability, legal effect, publication state, evidence
readiness or project ownership — every property on a node is a hint that may
narrow a search and may never admit anything. Authority is the positive
Mongo-resolved eligible document set, carried into Falkor as a predicate.

The predicate is evaluated **inside the query, before its `LIMIT`**. The reader's
existing document filter only constrains the *seed* clause, so related clauses
could previously come from any document in the organisation and project: four
well-connected but inapplicable clauses fill a three-row window and the
applicable clause never arrives. Filtering the returned rows afterwards protects
the results and not the candidates, which is the same capacity defect the lexical
and vector sources had.

Three outcomes stay distinct because a consumer acts differently on each:

* **valid empty** — nothing eligible; a complete answer;
* **degraded** — the graph failed or is disabled; never retried without the
  fence, and never read as "nothing applies";
* **authority failure** — eligibility was never resolved, which raises.

Graph availability is deliberately absent from that list of things it can
affect: it contributes relevance, so it must never be able to veto a legal
promotion or retract a classification.

Scope note. `ContractGraphService.find_related_clauses` keeps its previous
behaviour when no eligible set is supplied, because its existing callers are the
live evidence path and ticket 12 is the sole cutover. The fail-closed policy —
``None`` raises, empty short-circuits, caller filters intersect — lives here, at
the evidence seam, rather than in the reader, so the mechanism and the policy
cannot be confused for one another.

The GENERIC consumers (contract QA augmentation, generic contract search) get
their own before-the-`LIMIT` fence from `services/graph_expansion_scope.py`, and
deliberately NOT from here: an accepted regression pins that the retrieval stack
never reaches this module, because this module's three-outcome policy (valid
empty / degraded / authority failure) is the evidence path's contract and must
not be diluted into a relevance booster's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "GraphContainmentResult",
    "GraphEligibilityUnresolved",
    "compose_graph_document_scope",
    "contained_related_clauses",
    "stable_clause_node_id",
]

#: Fields a graph row may carry that must never reach a contract evidence
#: candidate. They are filename-derived, they are not reprojected on
#: classification correction, and a consumer that started ordering by them would
#: make the graph authoritative by accident.
NON_AUTHORITATIVE_GRAPH_FIELDS = ("section_type", "priority")


class GraphEligibilityUnresolved(Exception):
    """The canonical eligible universe was never established for this query.

    Distinct from an empty universe, which is a legitimate answer.
    """


@dataclass(frozen=True)
class GraphContainmentResult:
    """Matches plus enough state to tell three different empties apart."""

    matches: List[Dict[str, Any]] = field(default_factory=list)
    valid_empty: bool = False
    degraded: bool = False
    failure_reason: Optional[str] = None


def stable_clause_node_id(*, doc_id: str, clause_id: str) -> str:
    """Graph identity for one clause, free of any classification component.

    Deliberately takes no type argument. Classification advances by revision, so
    an identity containing it strands a node generation per correction: the
    corrected clause MERGEs as a new node beside the old one instead of updating
    it, and both then answer queries.
    """
    return f"{doc_id}:{clause_id}"


def compose_graph_document_scope(
    caller_document_ids: Optional[Sequence[str]],
    eligible_document_ids: Sequence[str],
) -> List[str]:
    """Intersect a caller's narrowing with canonical eligibility.

    A caller asking for one document it may not see gets nothing, never the full
    eligible universe. Replacing the narrowing with the eligible set widens a
    deliberately narrow request, which is the mistake that looks like a fix.
    """
    eligible = [str(item) for item in eligible_document_ids]
    if caller_document_ids is None:
        return eligible
    allowed = set(eligible)
    return [str(item) for item in caller_document_ids if str(item) in allowed]


def _strip_non_authoritative(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in row.items() if key not in NON_AUTHORITATIVE_GRAPH_FIELDS}


def contained_related_clauses(
    graph_service: Any,
    *,
    organization_id: Optional[str],
    project_id: Optional[str],
    seed_clause_numbers: Sequence[str],
    eligible_document_ids: Optional[Sequence[str]],
    caller_document_ids: Optional[Sequence[str]] = None,
    limit: int = 25,
) -> GraphContainmentResult:
    """Graph expansion for contract evidence, contained before the limit."""
    if eligible_document_ids is None:
        raise GraphEligibilityUnresolved(
            "contract evidence requires a canonical eligible document set; None "
            "means eligibility was never resolved, and is not a permissive default"
        )
    if not organization_id or not project_id:
        raise GraphEligibilityUnresolved(
            "contract graph evidence requires an organisation and a project; an "
            "unbounded traversal is not an acceptable fallback for a missing scope"
        )

    scope = compose_graph_document_scope(caller_document_ids, eligible_document_ids)
    if not scope:
        # Nothing to ask for. Issuing the query with an empty membership list
        # would depend on the store treating it as match-none, and a complete
        # answer does not need the round trip to be trustworthy.
        logger.debug("contract evidence: zero eligible graph documents, no query issued")
        return GraphContainmentResult(matches=[], valid_empty=True)

    try:
        rows = graph_service.find_related_clauses(
            organization_id=organization_id,
            project_id=project_id,
            seed_clause_numbers=list(seed_clause_numbers),
            seed_document_ids=list(caller_document_ids) if caller_document_ids else None,
            eligible_document_ids=scope,
            # The reader swallows transport errors and returns no expansion,
            # which is right for a relevance booster and wrong for evidence: it
            # makes an outage indistinguishable from "nothing applies".
            raise_on_error=True,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 - the reason is reported, not swallowed
        # No retry without the fence: turning a graph outage into a widened
        # traversal is the worst available recovery.
        logger.warning("contract evidence graph expansion failed: %s", exc)
        return GraphContainmentResult(
            matches=[],
            valid_empty=False,
            degraded=True,
            failure_reason=str(exc),
        )

    matches = [_strip_non_authoritative(dict(row)) for row in rows or []]
    return GraphContainmentResult(matches=matches, valid_empty=not matches)
