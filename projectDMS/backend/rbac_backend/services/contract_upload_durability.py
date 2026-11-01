"""Upload scope durability, retry conflict, and post-promotion immutability.

Three separate ways a scope decision could be quietly lost or changed, and the
guard for each.

**Durability.** Scope intent is copied into the durable reconciliation row at
document creation, and the TTL'd upload session is never re-read for it. A
session that has expired is not a scope of "organisation"; it is silence, and
reading silence as an answer is how the legacy path produced organisation-scoped
documents nobody asked for.

**Retry.** An idempotent retry compares the *original* ``scope_level`` and
rejects a conflict rather than taking the newer value. A retry is a claim that
this is the same upload; if the scope differs, that claim is false, and honouring
it would let a second attempt silently relocate a document.

**Immutability.** After promotion the authoritative ``scope_level`` and project
anchor are frozen. A generic metadata edit must not be able to reach them —
changing a promoted instrument's scope is not an edit, it is a different legal
statement, and it belongs to a deliberate re-adjudication rather than to whatever
field-update endpoint happens to accept a dict.

None of this makes anything evidence-capable. Uploaded, classified and
projection-pending is still not evidence.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from .contract_document_store import CONTRACT_DOCUMENTS_COLLECTION
from .contract_migration_reconciliation import RECONCILIATION_COLLECTION, candidate_identity

logger = logging.getLogger(__name__)

__all__ = [
    "IMMUTABLE_AFTER_PROMOTION",
    "ScopeConflictOnRetry",
    "ScopeIsImmutableAfterPromotion",
    "UploadDurabilityService",
]

#: Fields that stop being editable once an instrument is authoritative.
IMMUTABLE_AFTER_PROMOTION = frozenset({"scope_level", "project_id"})


class ScopeConflictOnRetry(Exception):
    """A retry named a different scope than the original upload."""


class ScopeIsImmutableAfterPromotion(Exception):
    """A generic edit tried to reach an authoritative scope field."""


class UploadDurabilityService:
    """Durable scope intent, conflict-checked retries, frozen after promotion."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def durable_scope(self, canonical_document_id: str, *, module: str = "contracts"):
        """Read scope from the durable row.

        Deliberately never consults ``contract_upload_sessions``: the session is
        the thing that expires, and that is the whole reason the value was copied.
        """
        row = await self._db[RECONCILIATION_COLLECTION].find_one(
            {"_id": candidate_identity(module=module, canonical_document_id=canonical_document_id)}
        )
        if row is None:
            return None
        return {"scope_level": row.get("scope_level"), "project_id": row.get("project_id")}

    async def check_retry(
        self,
        canonical_document_id: str,
        *,
        scope_level: str,
        project_id: Optional[str] = None,
        module: str = "contracts",
    ) -> None:
        """Refuse a retry whose scope disagrees with the original."""
        original = await self.durable_scope(canonical_document_id, module=module)
        if original is None:
            return

        if original["scope_level"] != scope_level:
            raise ScopeConflictOnRetry(
                f"upload {canonical_document_id} was created with scope "
                f"{original['scope_level']!r}; a retry claiming {scope_level!r} is "
                "rejected rather than silently relocating the document"
            )
        if original["scope_level"] == "project" and original["project_id"] != project_id:
            raise ScopeConflictOnRetry(
                f"upload {canonical_document_id} is anchored to project "
                f"{original['project_id']!r}; a retry naming {project_id!r} is a "
                "different upload, not a repeat of this one"
            )

    async def apply_metadata_edit(
        self, contract_document_id: str, changes: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Apply a generic edit, refusing any attempt on frozen scope fields."""
        record = await self._db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
            {"_id": contract_document_id}
        )
        if record is None:
            raise ScopeIsImmutableAfterPromotion(
                f"no authoritative instrument {contract_document_id}"
            )

        attempted = IMMUTABLE_AFTER_PROMOTION & set(changes)
        if attempted:
            # Rejected as a whole: applying the permitted half would half-honour
            # an edit the caller believes succeeded entirely.
            raise ScopeIsImmutableAfterPromotion(
                f"{', '.join(sorted(attempted))} cannot be changed after promotion; "
                "changing a promoted instrument's scope is a different legal "
                "statement, not a metadata edit"
            )

        await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": contract_document_id}, {"$set": dict(changes)}
        )
        return dict(changes)
