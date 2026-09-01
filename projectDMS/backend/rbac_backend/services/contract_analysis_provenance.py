"""Analysis provenance: what a run actually used, written by the run itself.

Eligibility and provenance answer different questions, and conflating them is
the failure this module exists to prevent. Eligibility says **what may
participate**. Provenance says **what did** — after ranking, truncation and
prompt assembly, all of which happen downstream of the resolver. So the resolver
cannot write this record: it only knows the superset it handed over, and a
superset recorded as an audit trail is worse than no record, because it looks
authoritative while being wrong.

**Provenance is a precondition of the accepted state.** The record is written
first; only then is the artefact accepted. If the write fails the artefact stays
non-accepted and the failure surfaces — an artefact whose evidence cannot be
reconstructed is not a record, and silently accepting one would leave a
persisted legal document with no way to say where it came from.

Interactive use is the deliberate exception: an on-screen answer is a
conversation, not a record, so it may proceed marked ``unrecorded``. What it may
not do is become a record later by the back door — saving, exporting and citing
all require a provenance write that actually happened.

No document bytes are stored. Provenance identifies; it does not duplicate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, Tuple

from ..models.contract_document import (
    ApplicableInstrument,
    CurrentState,
    Historical,
    require_applicable_instruments,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AcceptedStateRefused",
    "AnalysisProvenanceService",
    "ConsumedEvidence",
    "InteractiveOutcome",
    "PROVENANCE_COLLECTION",
    "ProvenanceIncomplete",
]

PROVENANCE_COLLECTION = "contract_analysis_provenance"


class ProvenanceIncomplete(Exception):
    """The run cannot say what it used.

    Raised rather than writing a partial record: a provenance row missing the
    instruments it is supposed to identify would satisfy a existence check while
    proving nothing.
    """


class AcceptedStateRefused(Exception):
    """The artefact may not reach the accepted state.

    Either the provenance write failed, or a caller tried to persist, export or
    cite an answer whose provenance was never recorded.
    """


@dataclass(frozen=True)
class ConsumedEvidence:
    """What one run actually used.

    ``instruments`` is validated on construction: a dict that merely looks like
    an instrument would let a consumer record something the resolver never
    authorised.
    """

    instruments: Tuple[ApplicableInstrument, ...]
    query_mode: Any
    retrieval_status: str
    degraded_sources: Tuple[str, ...] = ()
    legal_effect_references: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_applicable_instruments(self.instruments)


@dataclass(frozen=True)
class InteractiveOutcome:
    """An on-screen answer, and whether it may ever become a record."""

    recorded: bool
    provenance_state: str
    analysis_run_id: Optional[str] = None

    def require_persistable(self, action: str) -> None:
        """Gate save / export / cite on provenance having been written."""
        if not self.recorded:
            raise AcceptedStateRefused(
                f"cannot {action} an answer whose provenance was never recorded; "
                "an interactive answer is a conversation, and it does not become "
                "a record by being saved"
            )


def _serialise_mode(mode: Any) -> Tuple[str, Optional[str]]:
    if isinstance(mode, Historical):
        event_date = mode.event_date
        return "historical", (
            event_date.isoformat() if isinstance(event_date, date) else str(event_date)
        )
    if isinstance(mode, CurrentState):
        return "current_state", None
    raise ProvenanceIncomplete(
        f"unsupported query mode for provenance: {type(mode).__name__}; a record "
        "that cannot state its temporal basis is not reproducible"
    )


class AnalysisProvenanceService:
    """Writes provenance, and gates the accepted state on it."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def record_and_accept(
        self,
        *,
        analysis_run_id: str,
        consumer_type: str,
        organization_id: str,
        project_id: str,
        contract_id: str,
        consumed: ConsumedEvidence,
        accept: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Record what was used, then accept the artefact.

        The ordering is the contract, not an implementation detail: accepting
        first and recording afterwards would leave a window in which a persisted
        artefact exists with no provenance, which is exactly the state this
        forbids.
        """
        if not consumed.instruments:
            raise ProvenanceIncomplete(
                f"analysis run {analysis_run_id} consumed no identified instruments; "
                "an artefact whose evidence cannot be reconstructed is not a record"
            )

        mode_name, event_date = _serialise_mode(consumed.query_mode)
        record: Dict[str, Any] = {
            "_id": f"{consumer_type}:{analysis_run_id}",
            "analysis_run_id": analysis_run_id,
            "consumer_type": consumer_type,
            "organization_id": organization_id,
            "project_id": project_id,
            "contract_id": contract_id,
            "query_mode": mode_name,
            "retrieval_status": consumed.retrieval_status,
            "degraded_sources": list(consumed.degraded_sources),
            "legal_effect_references": list(consumed.legal_effect_references),
            "retrieved_at": datetime.now(timezone.utc),
            # Identifiers only. No clause text, no document bytes.
            "instruments": [
                {
                    "contract_document_id": instrument.contract_document_id,
                    "document_id": instrument.document_id,
                    "document_version_id": instrument.document_version_id,
                    "classification_revision": instrument.classification_revision,
                    "applicability_event_id": instrument.applicability_event_id,
                }
                for instrument in consumed.instruments
            ],
        }
        if event_date is not None:
            record["event_date"] = event_date

        try:
            await self._persist(record)
        except Exception as exc:
            logger.error(
                "provenance write failed for %s run %s: %s", consumer_type, analysis_run_id, exc
            )
            raise AcceptedStateRefused(
                f"provenance write failed for analysis run {analysis_run_id}; the "
                f"artefact is not accepted: {exc}"
            ) from exc

        if accept is not None:
            await accept(analysis_run_id)
        return record

    async def _persist(self, record: Dict[str, Any]) -> None:
        """The single write. Separated so a test can make it genuinely fail."""
        await self._db[PROVENANCE_COLLECTION].insert_one(record)

    async def interactive(
        self,
        *,
        consumed: ConsumedEvidence,
        analysis_run_id: Optional[str] = None,
    ) -> InteractiveOutcome:
        """An on-screen answer, marked by whether provenance exists for it."""
        if analysis_run_id is None:
            return InteractiveOutcome(recorded=False, provenance_state="unrecorded")

        existing = await self._db[PROVENANCE_COLLECTION].find_one(
            {"analysis_run_id": analysis_run_id}
        )
        if existing is None:
            return InteractiveOutcome(
                recorded=False, provenance_state="unrecorded", analysis_run_id=analysis_run_id
            )
        return InteractiveOutcome(
            recorded=True, provenance_state="recorded", analysis_run_id=analysis_run_id
        )
