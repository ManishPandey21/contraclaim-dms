"""Lexical candidate containment for contract evidence.

The defect this closes is **candidate capacity**, not authority ordering.

Publication authority already runs before fusion, ranking, ``total_count`` and
pagination — that ordering is correct and is not touched here. The problem is
earlier: the lexical source fetches its candidates under ``candidate_limit``
*before* anything knows which instruments are applicable. Six high-scoring
inapplicable clauses fill a five-slot window and the one applicable clause never
arrives, so the authority filter downstream never gets to see the row it would
have kept.

The fix is to put the canonical eligible-document predicate into the **first**
match stage, where it is evaluated before ``$sort`` and ``$limit``. Filtering
after the aggregation returns would protect the results while reproducing the
starvation exactly.

Three properties worth stating because each has a tempting wrong version:

* **Composition is intersection, never replacement.** A caller's own document
  filter is ANDed with eligibility, so asking for a document you may not see
  yields nothing rather than yielding it.
* **An empty eligible set short-circuits.** Mongo's ``$in: []`` is correctly
  match-none, so this is a cost and parity decision rather than a safety fix —
  the vector store's filter builder has a genuine empty-list hazard, and this one
  does not. Stated explicitly so nobody imports the wrong alarm.
* **Evidence mode always carries an organisation constraint.** The generic
  search path omits it for global actors, which is fine for a global view and
  not fine for legal evidence.

Scope: this module adds capability. It does not switch contract search into
evidence mode — ticket 12 is the sole cutover, so no deployable state exists
where the three sources search different universes.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "EvidenceScopeUnbounded",
    "compose_evidence_match_stage",
    "lexical_candidates_for_evidence",
]


class EvidenceScopeUnbounded(Exception):
    """An evidence query was assembled without an organisation constraint.

    Raised rather than silently adding one: if the caller did not supply a
    tenant, the resolver that produced the eligible set was not properly scoped
    either, and inventing a value here would hide that.
    """


def compose_evidence_match_stage(
    match_stage: Dict[str, Any],
    eligible_document_ids: Sequence[str],
) -> Dict[str, Any]:
    """Return a new match stage constrained to the canonical eligible set.

    The caller's stage is copied, never mutated — a shared dict quietly gaining
    an evidence predicate would be a nasty way to leak containment into the
    generic path.

    The eligible predicate is added under ``$and`` so it intersects with any
    existing ``document_id`` or ``$or`` clause instead of overwriting it.

    Currency and AI-authorisation filters are applied here too: the contract
    retrieval path historically applied neither, while its sibling stack applied
    both, so the two disagreed about which clauses may be used.
    """
    organization_id = match_stage.get("organization_id")
    if not organization_id:
        raise EvidenceScopeUnbounded(
            "contract evidence requires an organisation constraint; the generic "
            "search path may omit it for global actors, but an unbounded legal "
            "query is not an acceptable fallback"
        )

    composed = copy.deepcopy(dict(match_stage))
    clauses: List[Dict[str, Any]] = list(composed.pop("$and", []) or [])
    clauses.append({"document_id": {"$in": list(eligible_document_ids)}})

    # Derived projection state. These may EXCLUDE a row from evidence; they can
    # never authorise one, which is why they sit beside the eligible set rather
    # than in place of it.
    clauses.append({"is_current": {"$ne": False}})
    clauses.append({"is_authorised_for_ai": {"$ne": False}})

    composed["$and"] = clauses
    return composed


async def lexical_candidates_for_evidence(
    collection: Any,
    *,
    match_stage: Dict[str, Any],
    regex: Optional[str],
    candidate_limit: int,
    eligible_document_ids: Optional[Sequence[str]],
    category_regex: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Lexical candidates for contract evidence, contained before the limit.

    ``eligible_document_ids`` is required for evidence: ``None`` means the caller
    never resolved a canonical universe, which is a programming error rather than
    a permissive default.

    An empty set returns immediately **without issuing a query** — there is
    nothing to ask for, and a valid empty answer is not a reason to widen.
    """
    if eligible_document_ids is None:
        raise EvidenceScopeUnbounded(
            "contract evidence requires a canonical eligible document set; "
            "None is not a permissive default"
        )

    eligible = list(eligible_document_ids)
    if not eligible:
        logger.debug("contract evidence: zero eligible documents, no query issued")
        return []

    composed = compose_evidence_match_stage(match_stage, eligible)

    # Stage one, before scoring and before the limit. This placement is the whole
    # point: an equivalent filter applied after `$limit` protects the results but
    # not the candidates.
    pipeline: List[Dict[str, Any]] = [{"$match": composed}]

    text_input = {"$ifNull": ["$text_enriched", {"$ifNull": ["$text", ""]}]}
    if regex:
        add_fields: Dict[str, Any] = {
            "match_score": {
                "$size": {"$regexFindAll": {"input": text_input, "regex": regex, "options": "i"}}
            }
        }
        if category_regex:
            add_fields["category_score"] = {
                "$size": {
                    "$regexFindAll": {"input": text_input, "regex": category_regex, "options": "i"}
                }
            }
        else:
            add_fields["category_score"] = 0
        pipeline.extend(
            [
                {"$addFields": add_fields},
                {"$match": {"match_score": {"$gt": 0}}},
                {
                    "$addFields": {
                        "combined_match_score": {
                            "$add": ["$match_score", {"$multiply": ["$category_score", 0.35]}]
                        }
                    }
                },
            ]
        )
    else:
        pipeline.append({"$addFields": {"match_score": 0.0, "combined_match_score": 0.0}})

    pipeline.extend(
        [
            {
                "$group": {
                    "_id": {
                        "document_id": "$document_id",
                        "clause_number": "$clause_number",
                        "clause_start": "$clause_start_position",
                    },
                    "best_score": {"$max": "$combined_match_score"},
                    "document_id": {"$first": "$document_id"},
                    "upload_id": {"$first": "$upload_id"},
                    "clause_number": {"$first": "$clause_number"},
                    "clause_start_position": {"$first": "$clause_start_position"},
                    "createdAt": {"$first": "$createdAt"},
                }
            },
            {"$sort": {"best_score": -1, "clause_start_position": 1, "createdAt": -1}},
            {"$limit": candidate_limit},
        ]
    )

    # No broad-on-failure fallback here. If the aggregation fails the caller sees
    # it: a silent empty list would be indistinguishable from "nothing applies",
    # which is a legitimate answer a consumer may act on.
    return await collection.aggregate(pipeline).to_list(length=candidate_limit)
