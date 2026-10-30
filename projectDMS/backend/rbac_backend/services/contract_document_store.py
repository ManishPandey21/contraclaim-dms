"""Contract Master authoritative persistence: index bootstrap and the write-path
overlap guard.

Two responsibilities, both belonging to the persistence layer rather than to any
service:

**Index bootstrap.** Called from ``core.database.ensure_indexes`` so a cold
database gets the same catalogue as a warm one. Every index here protects a named
invariant; none exists merely because a field is queried.

**The overlap guard.** Two ``APPLIED`` intervals for one aggregate must not
overlap. This cannot be a unique index: overlap of half-open intervals is a
*relational* property between rows, and a unique key can only compare fields
within one row. So the guard reads the existing event stream and writes the new
event inside a single transaction — which is why the tests for it need a real
replica set and not a fake collection.

Nothing here is wired into contract search, upload or evidence. Later tickets own
activation; this module only makes the storage correct.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from ..models.contract_document import ApplicabilityLifecycleKind

logger = logging.getLogger(__name__)

CONTRACT_DOCUMENTS_COLLECTION = "contract_documents"
CLASSIFICATION_FACTS_COLLECTION = "contract_document_classification_facts"
APPLICABILITY_COLLECTION = "contract_document_applicability"
APPLICABILITY_EVENTS_COLLECTION = "contract_document_applicability_events"
LEGAL_EFFECTS_COLLECTION = "contract_document_legal_effects"

#: Collections holding legal history. None of them may ever carry a TTL index:
#: a timer that deletes legal evidence is not a retention policy, it is data loss
#: with a schedule.
LEGAL_COLLECTIONS = (
    CONTRACT_DOCUMENTS_COLLECTION,
    CLASSIFICATION_FACTS_COLLECTION,
    APPLICABILITY_COLLECTION,
    APPLICABILITY_EVENTS_COLLECTION,
    LEGAL_EFFECTS_COLLECTION,
)

#: Events that close an open interval. An interval opened by APPLIED runs until
#: one of these applies to the same aggregate.
_CLOSING_KINDS = (
    ApplicabilityLifecycleKind.WITHDRAWN.value,
    ApplicabilityLifecycleKind.SUPERSEDED.value,
)


class ApplicabilityOverlapError(Exception):
    """A second APPLIED interval would overlap one already open.

    Raised inside the transaction, so the refused event is never persisted.
    """


async def ensure_contract_document_indexes(db: Any) -> None:
    """Create the Contract Master indexes. Safe to call repeatedly.

    Each index and the invariant it protects:

    * ``contract_documents.document_id`` unique — one instrument per canonical
      document. Without it the same document could be promoted twice and produce
      two authoritative records for one legal thing.
    * ``contract_documents(organization_id, scope_level)`` — catalogue reads.
    * ``classification_facts(contract_document_id, revision)`` unique — a
      generation identifies exactly one fact, so "what was the type at revision
      N" has one answer.
    * ``applicability(organization_id, contract_document_id, project_id,
      contract_id)`` unique — the stable aggregate. Note that this permits the
      same ``contract_id`` in two different projects, because contract identity
      is the pair and ``"primary"`` is a default rather than a constraint.
    * ``applicability(organization_id, project_id, contract_id)`` — resolver
      reads for one contract.
    * ``applicability_events(applicability_id, effective_at)`` — replay by legal
      time.
    * ``applicability_events(applicability_id, recorded_at)`` — audit order,
      deliberately separate from legal order.
    * ``legal_effects(project_id, contract_id, source_contract_document_id)`` —
      precedence resolution for one contract.

    No TTL index is created on any of these.
    """
    await db[CONTRACT_DOCUMENTS_COLLECTION].create_index(
        "document_id", unique=True, background=True
    )
    await db[CONTRACT_DOCUMENTS_COLLECTION].create_index(
        [("organization_id", 1), ("scope_level", 1)], background=True
    )

    await db[CLASSIFICATION_FACTS_COLLECTION].create_index(
        [("contract_document_id", 1), ("revision", 1)], unique=True, background=True
    )

    await db[APPLICABILITY_COLLECTION].create_index(
        [
            ("organization_id", 1),
            ("contract_document_id", 1),
            ("project_id", 1),
            ("contract_id", 1),
        ],
        unique=True,
        background=True,
    )
    await db[APPLICABILITY_COLLECTION].create_index(
        [("organization_id", 1), ("project_id", 1), ("contract_id", 1)], background=True
    )

    await db[APPLICABILITY_EVENTS_COLLECTION].create_index(
        [("applicability_id", 1), ("effective_at", 1)], background=True
    )
    await db[APPLICABILITY_EVENTS_COLLECTION].create_index(
        [("applicability_id", 1), ("recorded_at", 1)], background=True
    )
    await db[APPLICABILITY_EVENTS_COLLECTION].create_index(
        "event_id", unique=True, background=True
    )

    await db[LEGAL_EFFECTS_COLLECTION].create_index(
        [("project_id", 1), ("contract_id", 1), ("source_contract_document_id", 1)],
        background=True,
    )


def _interval_is_open(events: List[Dict[str, Any]]) -> bool:
    """Is an APPLIED interval currently open for this aggregate?

    Replays by LEGAL effective time, not by insertion order, so a backdated
    withdrawal closes an interval that an earlier-written APPLIED opened. Events
    with an unknown ``effective_at`` sort first: an unknown start does not
    disqualify a currently-open interval, which is the accepted temporal rule.
    """
    ordered = sorted(
        events,
        key=lambda event: (event.get("effective_at") is not None, event.get("effective_at")),
    )
    is_open = False
    for event in ordered:
        kind = event.get("kind")
        if kind == ApplicabilityLifecycleKind.APPLIED.value:
            is_open = True
        elif kind in _CLOSING_KINDS:
            is_open = False
    return is_open


async def record_applicability_event(
    db: Any,
    *,
    organization_id: str,
    contract_document_id: str,
    project_id: str,
    contract_id: str,
    kind: ApplicabilityLifecycleKind,
    effective_at: Optional[date],
    event_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> str:
    """Append one lifecycle event, refusing an overlapping APPLIED.

    The aggregate row is created on first use; the event is appended to the
    stream. Both happen in one transaction with the overlap read, so a refusal
    leaves nothing behind and a concurrent writer cannot slip an APPLIED between
    this read and this write.

    ``effective_at=None`` is stored as ``None``. That is the UNKNOWN case, and
    substituting a timestamp would manufacture legal evidence.

    Returns the event id.
    """
    event_id = event_id or uuid.uuid4().hex
    aggregate_filter = {
        "organization_id": organization_id,
        "contract_document_id": contract_document_id,
        "project_id": project_id,
        "contract_id": contract_id,
    }

    client = getattr(db, "client", None)
    if client is None:  # pragma: no cover - defensive
        raise RuntimeError(
            "record_applicability_event requires a Motor database with a client; "
            "the overlap guard is transactional and cannot run without one"
        )

    async with await client.start_session() as session:
        async with session.start_transaction():
            aggregate = await db[APPLICABILITY_COLLECTION].find_one(
                aggregate_filter, session=session
            )
            if aggregate is None:
                applicability_id = uuid.uuid4().hex
                await db[APPLICABILITY_COLLECTION].insert_one(
                    {"_id": applicability_id, **aggregate_filter},
                    session=session,
                )
            else:
                applicability_id = str(aggregate["_id"])

            if kind is ApplicabilityLifecycleKind.APPLIED:
                cursor = db[APPLICABILITY_EVENTS_COLLECTION].find(
                    {"applicability_id": applicability_id}, session=session
                )
                existing = await cursor.to_list(length=None)
                if _interval_is_open(existing):
                    raise ApplicabilityOverlapError(
                        "an APPLIED interval is already open for "
                        f"({organization_id}, {contract_document_id}, {project_id}, "
                        f"{contract_id}); withdraw or supersede it before applying again"
                    )

            await db[APPLICABILITY_EVENTS_COLLECTION].insert_one(
                {
                    "_id": event_id,
                    "event_id": event_id,
                    "applicability_id": applicability_id,
                    "kind": kind.value,
                    "effective_at": effective_at.isoformat() if effective_at else None,
                    "actor_id": actor_id,
                    "reason": reason,
                },
                session=session,
            )

    return event_id
