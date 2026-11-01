"""Vector candidate containment for contract evidence.

The eligible document set reaches the store *before* the candidate limit is
consumed. Filtering the returned top-K afterwards protects the results and not
the candidates: four semantically closer but inapplicable points fill a
three-slot window and the applicable point is already gone by the time anything
downstream could keep it. That is the same capacity defect the lexical source
had, wearing a different transport.

Two things make this stricter than its lexical sibling rather than a copy of it.

**An empty eligible set is a safety rule here, not a cost decision.** The filter
builder drops an empty list condition instead of emitting a match-none, so a
zero-eligibility query loses its document fence entirely and searches whatever
the surviving filters allow — organisation-wide for a scoped caller, wider still
where the tenant filter is also absent. So the query is not issued at all. An
empty *intersection* is the same shape and is treated the same way.

**The payload carries no authority.** ``contract_id``, ``project_id``,
``document_type``, ``is_current`` and ``source_classification_revision`` may
narrow a search or hint that a point is stale; none of them may admit a point.
Authority is the positive Mongo-resolved eligible set and nothing else, which is
why a physically surviving old vector is harmless: it is unreachable, not
deleted.

Three outcomes stay distinct, because a consumer will act differently on each:
a **valid empty** answer (nothing applies), a **store failure** (degraded, and
never retried without the fence), and an **authority failure** (the eligible set
could not be resolved at all, which raises).

Scope: this module adds capability. It does not switch contract search into
evidence mode — ticket 12 is the sole cutover, so no deployable state exists
where the three sources search different universes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "EligibilityUnresolved",
    "VectorContainmentResult",
    "compose_vector_filters",
    "vector_candidates_for_evidence",
]


class EligibilityUnresolved(Exception):
    """The canonical eligible universe was never established for this query.

    Distinct from an empty universe, which is a legitimate answer. ``None``
    eligibility or a missing organisation constraint means the caller skipped
    the resolver, and an unrestricted vector search is not an acceptable
    interpretation of that.
    """


@dataclass(frozen=True)
class VectorContainmentResult:
    """Matches plus enough state to tell three different empties apart."""

    matches: List[Dict[str, Any]] = field(default_factory=list)
    #: nothing applies - a complete, trustworthy answer
    valid_empty: bool = False
    #: the store failed; this result is not evidence of absence
    degraded: bool = False
    failure_reason: Optional[str] = None


def _as_id_list(value: Any) -> Optional[List[str]]:
    """The caller's document filter, normalised. ``None`` means 'not supplied'."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def compose_vector_filters(
    filters: Dict[str, Any],
    eligible_document_ids: Sequence[str],
) -> Dict[str, Any]:
    """Return new filters whose ``document_id`` is the *intersection*.

    A caller asking for one document it may not see gets an empty intersection,
    never the full eligible universe. Replacing the caller's narrowing with the
    eligible set would widen a deliberately narrow request, which is the one
    mistake that looks like a correct fix.

    The caller's dict is copied: a shared mapping quietly gaining an evidence
    fence would leak containment into the generic search path.
    """
    composed = dict(filters)
    eligible = [str(item) for item in eligible_document_ids]
    requested = _as_id_list(composed.get("document_id"))

    if requested is None:
        composed["document_id"] = eligible
    else:
        allowed = set(eligible)
        composed["document_id"] = [item for item in requested if item in allowed]
    return composed


async def vector_candidates_for_evidence(
    vector_client: Any,
    *,
    query_vector: Sequence[float],
    filters: Dict[str, Any],
    candidate_limit: int,
    eligible_document_ids: Optional[Sequence[str]],
    namespace: Optional[str] = None,
) -> VectorContainmentResult:
    """Vector candidates for contract evidence, contained before the limit.

    The eligible ids become the store-side ``document_id`` filter, so the limit
    is spent inside the canonical universe rather than on whatever ranked
    highest across the tenant.
    """
    if eligible_document_ids is None:
        raise EligibilityUnresolved(
            "contract evidence requires a canonical eligible document set; None "
            "means eligibility was never resolved, and is not a permissive default"
        )
    if not filters.get("org_id") and not filters.get("organization_id"):
        raise EligibilityUnresolved(
            "contract evidence requires an organisation constraint; the generic "
            "search path may omit it for global actors, but an unbounded legal "
            "query is not an acceptable fallback"
        )

    composed = compose_vector_filters(filters, eligible_document_ids)

    # Both zero cases are the same hazard: the builder would drop an empty list
    # and search on whatever filters survive. Nothing to ask for is a complete
    # answer, so no query is issued.
    if not composed["document_id"]:
        logger.debug("contract evidence: zero eligible vector documents, no query issued")
        return VectorContainmentResult(matches=[], valid_empty=True)

    try:
        matches = await vector_client.search(
            list(query_vector),
            filters=composed,
            limit=candidate_limit,
            namespace=namespace,
            # The organisation constraint is mandatory above, so a global escape
            # hatch has nothing left to unlock here.
            allow_global=False,
        )
    except Exception as exc:  # noqa: BLE001 - the reason is reported, not swallowed
        # Deliberately no retry: a second attempt without the fence would turn a
        # store outage into a widened search, which is the worst possible way to
        # recover. The caller is told the answer is degraded so it cannot be read
        # as "nothing applies".
        logger.warning("contract evidence vector search failed: %s", exc)
        return VectorContainmentResult(
            matches=[],
            valid_empty=False,
            degraded=True,
            failure_reason=str(exc),
        )

    results = list(matches or [])
    return VectorContainmentResult(matches=results, valid_empty=not results)
