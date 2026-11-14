"""Post-promotion sequencing and derived-outage tolerance.

A promoted instrument is authoritative the moment the transaction commits, and
**evidence-capable only once its projection is current**. Those are different
questions, and collapsing them is what lets a document answer a legal query
using content nobody has re-derived yet.

The sequencing follows from that:

* promotion sets ``classification_revision = 1`` and
  ``projection_status = PENDING``, so the instrument exists in the catalogue and
  is excluded from the eligible set;
* the projection is built, and only a completion at the *matching* revision
  makes it current;
* a derived-store outage records a failure and changes nothing legal.

**An outage never rolls back a promotion.** Qdrant being down is not a statement
about whether an operator correctly identified a contract instrument, and letting
it retract one would give a derived store a veto over an authoritative legal
fact — the authority direction inverted. The instrument stays; evidence stays
off until a projection succeeds.

For the same reason an outage never touches ``contract_document_type`` or the
revision. It moves exactly one field, and only when the revision it describes is
still the live one: a worker that started on revision 1 and reports failure after
a correction landed is describing a generation nobody is waiting for.

Old vectors stop counting the instant the revision advances, not when
re-embedding finishes. That is the whole reason the fence is a revision
comparison rather than a migration flag.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models.contract_document import ProjectionStatus
from .contract_document_store import CONTRACT_DOCUMENTS_COLLECTION

logger = logging.getLogger(__name__)

__all__ = ["DerivedStoreOutage", "PostPromotionSequencer"]


class DerivedStoreOutage(Exception):
    """This outage report describes a superseded generation.

    Raised rather than applied: stamping FAILED over a newer revision is the same
    regression as a late worker publishing stale output, wearing the other mask.
    """


class PostPromotionSequencer:
    """Moves a promoted instrument between projection states, and nothing else."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def mark_projection_current(self, contract_document_id: str, *, revision: int) -> None:
        """Publish the projection for this generation, if it is still current."""
        result = await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": contract_document_id, "classification_revision": revision},
            {
                "$set": {
                    "projection_revision": revision,
                    "projection_status": ProjectionStatus.CURRENT.value,
                }
            },
        )
        if getattr(result, "matched_count", 0) == 0:
            raise DerivedStoreOutage(
                f"cannot publish projection for {contract_document_id} at revision "
                f"{revision}: the authoritative classification has advanced"
            )

    async def record_projection_outage(
        self, contract_document_id: str, *, revision: int, reason: str
    ) -> None:
        """Record that the derived stores could not be rebuilt.

        Touches ``projection_status`` and nothing else. The promotion, the
        classification, the applicability and the receipt are all untouched,
        because none of them became less true when a vector store went down.
        """
        logger.warning(
            "derived-store outage for %s revision %s: %s",
            contract_document_id,
            revision,
            reason,
        )
        result = await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": contract_document_id, "classification_revision": revision},
            {"$set": {"projection_status": ProjectionStatus.FAILED.value}},
        )
        if getattr(result, "matched_count", 0) == 0:
            raise DerivedStoreOutage(
                f"cannot record an outage for {contract_document_id} at revision "
                f"{revision}: that generation is superseded, and marking it failed "
                "would retract a newer one"
            )
