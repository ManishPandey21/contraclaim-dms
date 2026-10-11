"""Contract Q&A: one contract, one explicit mode, canonical eligibility.

What this replaces is a filter on *ingestion status*. That gate answered "has
this document finished processing?" and was being read as "may this document
answer a legal question?" — two questions that happen to agree most of the time,
which is the worst property a security check can have. Alongside it sat an
all-completed-files breadth control that widened the search whenever the answer
looked thin. Both are gone.

**A question needs a contract and a mode.** Not a project and a hope. A request
without a mode is refused rather than defaulted, and ``Historical`` without an
``event_date`` is refused rather than answered as of today — "what did the
contract say in March 2021" and "what does it say now" are different questions,
and silently substituting one for the other produces a confident wrong answer
with no trace.

**Precedence comes from canonical legal effect, never from retrieval rank.** A
clause that scores higher is more *relevant*; a clause that OVERRIDEs another is
more *authoritative*. Conflating them lets a search engine decide which contract
term governs.

**Unresolved precedence surfaces as unresolved.** Where two instruments contest
the same ground and no canonical effect settles it, both are returned marked
unresolved. Picking one — by score, by recency, by type — would assert a legal
position nobody established.

Zero applicable evidence is an explicit empty answer. It is never widened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional, Sequence, Tuple

from ..models.contract_document import (
    ApplicableInstrument,
    Browse,
    CurrentState,
    Historical,
    require_applicable_instruments,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ContractQARequest",
    "PrecedenceOutcome",
    "QueryModeRequired",
    "resolve_precedence",
]

#: Canonical effects that settle precedence. Nothing else does - not score, not
#: recency, not instrument type.
OVERRIDING_EFFECTS = frozenset({"OVERRIDE", "SUPERSEDE"})


class QueryModeRequired(Exception):
    """The request did not state a mode, or stated one it cannot support."""


@dataclass(frozen=True)
class ContractQARequest:
    """A question about one contract at one point in time.

    Every field is required on purpose. There is no default mode and no default
    date: a Q&A request that does not say *when* is not a narrower question, it
    is an ambiguous one.
    """

    organization_id: str
    project_id: str
    contract_id: str
    question: str
    query_mode: Any

    def __post_init__(self) -> None:
        if not self.project_id or not self.contract_id:
            raise QueryModeRequired(
                "contract Q&A requires a project and a contract; a question about "
                "'the contract' with no contract identified cannot be answered "
                "against canonical eligibility"
            )
        if self.query_mode is None:
            raise QueryModeRequired(
                "a query mode is required; there is no implicit current-state "
                "default, because a caller that forgot to say 'when' would get a "
                "confident answer to a question it did not ask"
            )
        if isinstance(self.query_mode, Browse):
            raise QueryModeRequired(
                "Browse is a catalogue mode and cannot produce evidence; its "
                "results are a list of what exists, not a statement about what "
                "applies"
            )
        if isinstance(self.query_mode, Historical):
            if not isinstance(self.query_mode.event_date, date):
                raise QueryModeRequired(
                    "Historical requires an event_date; defaulting to today would "
                    "answer today's question and label it as history"
                )
        elif not isinstance(self.query_mode, CurrentState):
            raise QueryModeRequired(
                f"unsupported query mode {type(self.query_mode).__name__}"
            )


@dataclass(frozen=True)
class PrecedenceOutcome:
    """Which instrument governs - or an honest statement that it is unsettled."""

    governing: Optional[ApplicableInstrument] = None
    superseded: Tuple[ApplicableInstrument, ...] = ()
    unresolved: Tuple[ApplicableInstrument, ...] = ()

    @property
    def is_resolved(self) -> bool:
        return self.governing is not None and not self.unresolved


def resolve_precedence(
    instruments: Sequence[ApplicableInstrument],
    *,
    legal_effects: Sequence[Dict[str, Any]],
    retrieval_ranking: Optional[Sequence[str]] = None,
) -> PrecedenceOutcome:
    """Decide precedence from canonical legal effect alone.

    ``retrieval_ranking`` is accepted and deliberately unused for the decision:
    taking it would let relevance masquerade as authority. It stays in the
    signature so a caller cannot conclude that passing it would have helped.
    """
    require_applicable_instruments(instruments)
    if not instruments:
        return PrecedenceOutcome()

    by_id = {instrument.contract_document_id: instrument for instrument in instruments}
    superseded_ids = set()
    winners = set(by_id)

    for effect in legal_effects:
        kind = str(effect.get("kind") or "").upper()
        if kind not in OVERRIDING_EFFECTS:
            continue
        source = effect.get("source_contract_document_id")
        target = effect.get("target_contract_document_id")
        if source in by_id and target in by_id:
            superseded_ids.add(target)
            winners.discard(target)

    if len(winners) == 1:
        governing_id = next(iter(winners))
        return PrecedenceOutcome(
            governing=by_id[governing_id],
            superseded=tuple(by_id[i] for i in sorted(superseded_ids)),
        )

    if len(by_id) == 1:
        return PrecedenceOutcome(governing=next(iter(by_id.values())))

    # More than one instrument survives and nothing canonical separates them.
    # Ranking would pick a winner; it would just not be the right kind of winner.
    logger.info(
        "contract precedence unresolved between %s", ", ".join(sorted(winners))
    )
    return PrecedenceOutcome(
        unresolved=tuple(by_id[i] for i in sorted(winners)),
        superseded=tuple(by_id[i] for i in sorted(superseded_ids)),
    )
