"""Append-only provenance for every LLM intervention.

This is the audit answer to "why does this page read differently from the
PDF?" - a question a system whose output feeds arbitration must be able to
answer. Records are only ever inserted: a later tier's attempt does not
overwrite an earlier one, so the whole escalation history survives.

Scoped to the document, so it inherits document RBAC rather than defining its
own.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .models import Intervention

logger = logging.getLogger(__name__)


class InterventionLedger:
    COLLECTION = "page_extraction_interventions"

    def __init__(self, *, db: Any) -> None:
        self.db = db

    async def record(self, intervention: Intervention) -> None:
        record = intervention.to_record()
        record["recorded_at"] = datetime.now(timezone.utc)
        try:
            await self.db[self.COLLECTION].insert_one(record)
        except Exception:
            # A failure to write provenance must not silently lose the
            # intervention itself, but it also must not fail the extraction -
            # the page is already reconstructed either way.
            logger.exception(
                "Unable to record extraction intervention for %s page %s",
                intervention.document_id,
                intervention.page_number,
            )
