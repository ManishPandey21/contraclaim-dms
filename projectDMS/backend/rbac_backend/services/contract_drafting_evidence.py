"""Letter drafting and arbitration consume resolved evidence, never raw scope.

Both consumers accept ``ApplicableInstrument`` and nothing else. That is the
whole mechanism: a consumer that takes ``(project_id, contract_id)`` and goes
looking is reconstructing authority, and two reconstructions drift. Here the
authority arrives already resolved, and the type refuses anything else.

**The arbitration direct-authority path is the specific hazard.** The codebase
has two authority helpers — a batch filter used by most consumers, and a per-row
resolver arbitration calls directly. Their *rules* agree; their *failure
directions* differ, because the per-row resolver fails closed on an unresolvable
document and the batch filter deliberately does not. That divergence is safe
**only because** contract evidence is gated by the positive eligible set, which
excludes unresolvable candidates structurally rather than subtracting known-bad
ones. Safe-only-because is a conditional, and conditionals get pinned by test:
A-39 is that pin.

**Blocked-but-applicable is shown, not dropped.** An instrument that applies but
whose content is unavailable is reported with its applicability basis and no
text. Dropping it silently would tell a drafter the contract is silent on a
point when it is not; exposing the bytes would defeat the block. Neither is
acceptable, so the answer states both facts.

Every persisted run writes provenance under the accepted-state precondition, and
degraded state propagates into whatever is persisted rather than being dropped at
the boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
    "ConsumerModeRequired",
    "DraftingEvidence",
    "EvidenceItem",
    "build_drafting_evidence",
]


class ConsumerModeRequired(Exception):
    """The consumer did not state a query mode, or stated Browse."""


@dataclass(frozen=True)
class EvidenceItem:
    """One instrument as a consumer sees it."""

    instrument: ApplicableInstrument
    content_available: bool
    #: Present whether or not content is available - a blocked instrument still
    #: has an applicability basis, and hiding it hides that the point is covered.
    applicability_basis: str
    text: Optional[str] = None
    withheld_reason: Optional[str] = None


@dataclass(frozen=True)
class DraftingEvidence:
    """What a drafting or arbitration run may use, and how complete it is."""

    items: Tuple[EvidenceItem, ...]
    query_mode_name: str
    degraded_sources: Tuple[str, ...] = ()
    event_date: Optional[str] = None

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded_sources)

    @property
    def usable_items(self) -> Tuple[EvidenceItem, ...]:
        return tuple(item for item in self.items if item.content_available)

    @property
    def withheld_items(self) -> Tuple[EvidenceItem, ...]:
        return tuple(item for item in self.items if not item.content_available)


def _mode_name(query_mode: Any) -> Tuple[str, Optional[str]]:
    if query_mode is None:
        raise ConsumerModeRequired(
            "a drafting or arbitration run must state its query mode explicitly; "
            "there is no implicit today"
        )
    if isinstance(query_mode, Browse):
        raise ConsumerModeRequired(
            "Browse is a catalogue mode; a draft built from a catalogue listing "
            "would cite documents nobody established as applicable"
        )
    if isinstance(query_mode, Historical):
        return "historical", query_mode.event_date.isoformat()
    if isinstance(query_mode, CurrentState):
        return "current_state", None
    raise ConsumerModeRequired(f"unsupported query mode {type(query_mode).__name__}")


def build_drafting_evidence(
    instruments: Sequence[ApplicableInstrument],
    *,
    query_mode: Any,
    content_by_document: Dict[str, str],
    blocked_document_ids: Optional[Sequence[str]] = None,
    degraded_sources: Sequence[str] = (),
) -> DraftingEvidence:
    """Assemble evidence for a drafting or arbitration run.

    Takes instruments, not identifiers: there is no parameter here from which a
    consumer could re-derive who may participate, which is what stops the two
    authority helpers from diverging in effect.
    """
    require_applicable_instruments(instruments)
    mode_name, event_date = _mode_name(query_mode)
    blocked = {str(item) for item in (blocked_document_ids or [])}

    items = []
    for instrument in instruments:
        document_id = instrument.document_id
        if document_id in blocked:
            items.append(
                EvidenceItem(
                    instrument=instrument,
                    content_available=False,
                    applicability_basis=instrument.applicability_event_id,
                    text=None,
                    withheld_reason="content unavailable for this document",
                )
            )
            continue
        items.append(
            EvidenceItem(
                instrument=instrument,
                content_available=True,
                applicability_basis=instrument.applicability_event_id,
                text=content_by_document.get(document_id),
            )
        )

    return DraftingEvidence(
        items=tuple(items),
        query_mode_name=mode_name,
        degraded_sources=tuple(degraded_sources),
        event_date=event_date,
    )
