"""Canonical eligibility for the GENERIC clause-expansion consumers.

Two seams expand a contract query through the graph without going anywhere near
the evidence path: `RetrievalService._augment_with_contract_graph_results`
(contract QA) and `ContractService._graph_candidates` (generic contract search).
Both bounded their traversal with the engine's `LIMIT` and resolved authority
afterwards.

WHY THAT IS A DEFECT AND NOT A STYLE POINT.
Nothing in this repository deletes, deactivates or reprojects a `(:Clause)`
node: `contract_graph_service` writes `is_active` only through `COALESCE` on
upsert, and no Clause `DELETE` exists. So the clauses of a hard-deleted or
blocked contract keep matching the traversal forever. Filtering afterwards
removes them from the ANSWER and cannot put back a legitimate clause that never
entered the window. Measured against a real FalkorDB: twelve dead clauses filled
a three-row window and the live contract's clause never arrived - nothing
disclosed, and a legitimate source suppressed. G29 counts suppression as
material influence.

WHY THIS IS NOT IN `contract_graph_containment`.
That module is the CONTRACT EVIDENCE seam, and an accepted regression pins that
the retrieval stack never imports it. Its three outcomes - valid empty,
degraded, authority failure - are the evidence path's contract: an outage there
must never read as "nothing applies", because a legal promotion may depend on
it. These two consumers are relevance boosters over an already-complete result
set, and their correct behaviour on an unresolved scope is simply to expand
nothing. Sharing one module would force one of the two policies onto the other.

FAIL CLOSED. An empty result means "expand nothing", never "expand everything".
A scope this module could not resolve is not evidence that every candidate is
admissible, so the callers skip the expansion rather than retrying it unfenced -
turning a lookup failure into a widened traversal is the worst available
recovery, on either seam.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

logger = logging.getLogger(__name__)

__all__ = ["resolve_graph_expansion_scope"]

#: What "nothing is eligible" looks like. Named so the two exits that produce it
#: - no candidates, and eligibility unresolvable - are visibly the same answer
#: rather than two incidental empty literals.
NOTHING_ELIGIBLE: List[str] = []


async def resolve_graph_expansion_scope(
    db: Any,
    graph_service: Any,
    *,
    organization_id: Optional[str],
    project_id: Optional[str],
    seed_clause_numbers: Sequence[str],
    seed_document_ids: Optional[Sequence[str]] = None,
    document_limit: int = 500,
) -> List[str]:
    """The canonical eligible documents a generic clause expansion may draw from.

    Derived from the graph's OWN candidate documents rather than from a fresh
    scope query, for three reasons: it asks about exactly the population the
    expansion would draw from, it is bounded by distinct document rather than by
    clause (a project has tens of contracts and can have thousands of clauses),
    and it adds no second copy of a scope rule that lives elsewhere. Eligibility
    itself is decided only by the certified predicates.
    """
    from .publication_policy import blocked_document_ids, resolvable_document_ids

    if not organization_id or not project_id:
        return list(NOTHING_ELIGIBLE)

    try:
        candidates = graph_service.candidate_document_ids(
            organization_id=organization_id,
            project_id=project_id,
            seed_clause_numbers=list(seed_clause_numbers),
            seed_document_ids=list(seed_document_ids) if seed_document_ids else None,
            document_limit=document_limit,
        )
    except Exception as exc:  # noqa: BLE001 - reported, and treated as nothing eligible
        logger.warning("contract graph expansion candidates unavailable: %s", exc)
        return list(NOTHING_ELIGIBLE)

    if not candidates:
        return list(NOTHING_ELIGIBLE)

    try:
        resolvable = await resolvable_document_ids(db, candidates)
        blocked = await blocked_document_ids(db, candidates)
    except Exception as exc:  # noqa: BLE001 - reported, and treated as nothing eligible
        logger.warning("contract graph expansion scope could not be resolved: %s", exc)
        return list(NOTHING_ELIGIBLE)

    return [
        document_id
        for document_id in candidates
        if str(document_id) in resolvable and str(document_id) not in blocked
    ]
