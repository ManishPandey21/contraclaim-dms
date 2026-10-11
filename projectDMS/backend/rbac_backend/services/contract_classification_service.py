"""Classification lifecycle and the projection currency fence.

Two halves of one change, deliberately not separable:

* **Authoritative classification** advances through append-only facts. A
  correction appends a new fact and increments
  ``ContractDocument.classification_revision``.
* **The derived projection is invalidated in the same transition**, so no state
  exists where revision N+1 is confirmed while a projection built for N still
  reads as evidence-current.

Shipping the correction path before the invalidation would open exactly that
window — which is why this is one module and one ticket.

Three things this deliberately does *not* do:

* it does not run the reprojection worker (ticket 08 owns that, including the
  conditional commit that stops a late worker overwriting a newer generation);
* it does not touch applicability or legal effect — classification names the
  instrument *kind*, precedence is a separate canonical fact;
* it does not write any evidence-readiness flag. Currency is derived at read
  time by the resolver, from ``projection_status`` and the revision comparison.

The fence itself lives in :mod:`rbac_backend.services.contract_scope_resolver`,
where it is the fourth conjunct of the eligible set. This module's job is to
make sure the values that fence reads are correct the moment a correction lands.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..models.contract_document import ContractDocumentType, ProjectionStatus
from .contract_document_store import (
    CLASSIFICATION_FACTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ClassificationRevisionConflict",
    "ContractClassificationService",
    "SuggestionIsNotAuthority",
    "UnknownContractDocument",
]

#: Bases that may establish an AUTHORITATIVE classification. A filename match, a
#: model guess or an appraisal inference is a suggestion — it may inform a human,
#: it may never stand in for one.
AUTHORITATIVE_BASES = frozenset({"operator_confirmed", "migration_adjudicated"})


class UnknownContractDocument(Exception):
    """No such instrument."""


class ClassificationRevisionConflict(Exception):
    """The caller's view of the current revision is stale.

    Two operators who both believe the current revision is N must not both
    create N+1: one would silently overwrite the other's reading of history.
    """


class SuggestionIsNotAuthority(Exception):
    """A suggestion was offered where a confirmation is required.

    Raised rather than quietly downgrading, so a caller cannot half-succeed and
    believe it resolved a type when it only recorded a guess.
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContractClassificationService:
    """Confirms, corrects and records instrument classification."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def suggest(
        self,
        contract_document_id: str,
        *,
        contract_document_type: ContractDocumentType,
        basis: str,
        actor_id: Optional[str] = None,
    ) -> None:
        """Record a non-authoritative suggestion.

        Advances nothing: not the revision, not the stored type, not projection
        state. A suggestion that moved the revision would make a filename an
        authority by the back door.
        """
        record = await self._load(contract_document_id)
        logger.info(
            "classification suggestion recorded for %s: %s (basis=%s, revision unchanged at %s)",
            contract_document_id,
            contract_document_type.value,
            basis,
            record.get("classification_revision"),
        )

    async def confirm(
        self,
        contract_document_id: str,
        *,
        contract_document_type: ContractDocumentType,
        expected_revision: int,
        actor_id: Optional[str],
        basis: str = "operator_confirmed",
    ) -> int:
        """Append an authoritative classification fact and advance the revision.

        Returns the new revision.

        The projection is invalidated in the same call, before this returns: the
        instant the revision advances, a projection built for the previous one
        must stop counting as evidence.
        """
        if basis not in AUTHORITATIVE_BASES:
            raise SuggestionIsNotAuthority(
                f"basis {basis!r} cannot establish an authoritative classification; "
                f"expected one of {sorted(AUTHORITATIVE_BASES)}. A filename or model "
                "suggestion informs a human decision, it does not replace one."
            )

        record = await self._load(contract_document_id)
        current_revision = int(record.get("classification_revision") or 0)
        if current_revision != int(expected_revision):
            raise ClassificationRevisionConflict(
                f"expected revision {expected_revision}, found {current_revision} "
                f"for {contract_document_id}; reload before correcting so the "
                "existing classification history is not overwritten"
            )

        next_revision = current_revision + 1

        # Append first: the fact is the history, and it must exist before the
        # pointer that names it moves.
        await self._db[CLASSIFICATION_FACTS_COLLECTION].insert_one(
            {
                "_id": uuid.uuid4().hex,
                "contract_document_id": contract_document_id,
                "contract_document_type": contract_document_type.value,
                "revision": next_revision,
                "basis": basis,
                "actor_id": actor_id,
                "asserted_at": _now(),
            }
        )

        # Advance the authoritative pointer AND invalidate the derived
        # projection in one write. Splitting these would leave a window where
        # revision N+1 is live and projection N still reads CURRENT.
        #
        # `projection_revision` is deliberately left pointing at the old
        # generation rather than cleared: the resolver's fence is an equality
        # comparison, so a stale value is already excluded, and keeping it lets
        # an operator see which generation the derived data belongs to.
        await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": contract_document_id},
            {
                "$set": {
                    "contract_document_type": contract_document_type.value,
                    "classification_revision": next_revision,
                    "projection_status": ProjectionStatus.PENDING.value,
                }
            },
        )
        return next_revision

    async def mark_projection_failed(
        self,
        contract_document_id: str,
        *,
        revision: int,
        reason: str,
    ) -> None:
        """Record that the derived projection could not be built.

        The authoritative classification is untouched. A derived-store failure
        is not a legal failure: if an embedder outage could retract a confirmed
        classification, a derived system would hold a veto over an authoritative
        legal fact, which inverts the authority direction entirely.

        The instrument simply has no current projection, so it is not
        evidence-capable until one succeeds.
        """
        record = await self._load(contract_document_id)
        logger.warning(
            "projection failed for %s at revision %s: %s",
            contract_document_id,
            revision,
            reason,
        )
        # Fenced on the revision the failure describes: a late report about
        # revision N must not stamp FAILED over revision N+1.
        await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": contract_document_id, "classification_revision": int(revision)},
            {"$set": {"projection_status": ProjectionStatus.FAILED.value}},
        )

    async def _load(self, contract_document_id: str) -> Any:
        record = await self._db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
            {"_id": contract_document_id}
        )
        if record is None:
            raise UnknownContractDocument(contract_document_id)
        return record
