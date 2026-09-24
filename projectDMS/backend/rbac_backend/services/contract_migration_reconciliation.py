"""Reconciliation store: inventory, and the separate act of materialising it.

Three operational concepts that a single "migrate" verb would have fused:

* **inventory** — read-only. It produces candidates and writes *nothing at all*,
  not even a reconciliation row. A preview that persisted would seed the
  adjudications an operator is about to make, which is worse than useless: the
  operator would be reviewing the migration's own guesses back at itself.
* **materialise inventory** — an explicitly named, separate action that turns
  those candidates into durable rows.
* everything downstream (review, claim, adjudication, promotion) belongs to
  later tickets and is deliberately absent here.

The reconciliation collection is **non-authoritative by construction**: it is not
in ``LEGAL_COLLECTIONS``, its rows carry no ``contract_document_type``, no
``classification_revision`` and no projection state, and a candidate is not an
``ApplicableInstrument`` and cannot be passed where one is required.

**Candidate identity excludes every decision.** It derives from the canonical
document alone — not the scope decision, the type decision, the review outcome,
the lease owner, the filename or the upload id. All of those change while the
same document is being adjudicated, and an identity containing any of them forks
a second candidate that disagrees with the first.

**Session evidence is captured here and never re-read.** The strongest project
signal lives on a TTL'd upload session that will be gone by promotion time
(DEBT-08), so it is copied into the candidate at inventory. Reading it again
later would be reading an absence and calling it evidence.

This borrows the accepted backfill pattern; it deliberately does **not** register
as a backfill module, because that service derives scope from a canonical target
that does not exist yet — and deriving scope is what this migration is for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from fastapi import status
from pymongo.errors import DuplicateKeyError

from ..utils.error_handler import ContractError

logger = logging.getLogger(__name__)

__all__ = [
    "CandidateNotFound",
    "ContractMigrationReconciliation",
    "MIGRATION_IDENTITY_PREFIX",
    "RECONCILIATION_COLLECTION",
    "ReconciliationCandidate",
    "ScopeClassificationState",
    "TypeClassificationState",
    "candidate_identity",
    "scoped_candidate_filter",
]

#: Deliberately outside LEGAL_COLLECTIONS. Nothing here is authoritative.
RECONCILIATION_COLLECTION = "contract_document_reconciliation"

MIGRATION_IDENTITY_PREFIX = "contract-master-migration"


class CandidateNotFound(ContractError):
    """No reconciliation candidate with this id in the authorised organisation.

    One answer, deliberately, for "does not exist" and "exists in another
    organisation": a caller authorised for X who names a candidate of Y learns
    neither that Y's candidate exists nor which organisation owns it. The message
    therefore names nothing - not the candidate's organisation, not its scope.
    """

    def __init__(self) -> None:
        super().__init__("reconciliation candidate not found", status.HTTP_404_NOT_FOUND)


def scoped_candidate_filter(candidate_id: str, organization_id: str) -> Dict[str, Any]:
    """The only filter a mutating operation may load a candidate with.

    ``organization_id`` must be the organisation the route *authorised*, never
    one read from the candidate or the request body. Candidate ids are strings
    (``contract-master-migration:<module>:<document_id>``) and so is the stored
    ``organization_id`` (``inventory`` writes ``str(...)``), so this is an exact
    match with no ObjectId variants. No organisation means no candidate, rather
    than an unscoped read.
    """
    if not organization_id:
        raise CandidateNotFound()
    return {"_id": candidate_id, "organization_id": str(organization_id)}


class ScopeClassificationState(str, Enum):
    """Scope progression. ``ORG_SCOPE_CONFIRMED`` has no automatic path.

    The legacy corpus contains no positive organisation-scope evidence: the
    upload path collapses null and empty before any durable write, so a
    surviving audit null records only "no project was in the session" — an
    absence one level removed from the question being asked.
    """

    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    PROJECT_SCOPE_CONFIRMED = "PROJECT_SCOPE_CONFIRMED"
    ORG_SCOPE_CONFIRMED = "ORG_SCOPE_CONFIRMED"
    INVALID = "INVALID"


class TypeClassificationState(str, Enum):
    """Type progression, independent of scope.

    ``TYPE_UNKNOWN`` never maps to ``other_contractual_document``: that is an
    affirmative classification an operator chooses, not a bucket for anything
    unrecognised.
    """

    TYPE_UNKNOWN = "TYPE_UNKNOWN"
    TYPE_SUGGESTED = "TYPE_SUGGESTED"
    TYPE_RESOLVED = "TYPE_RESOLVED"


@dataclass(frozen=True)
class ReconciliationCandidate:
    """One legacy document seen by inventory. Not an instrument, not evidence."""

    candidate_id: str
    canonical_document_id: str
    organization_id: str
    module: str
    scope_state: ScopeClassificationState = ScopeClassificationState.UNRESOLVED
    type_state: TypeClassificationState = TypeClassificationState.TYPE_UNKNOWN
    #: Copied from the ephemeral upload session at inventory time, because it
    #: will not exist later. Evidence, not authority.
    session_evidence: Dict[str, Any] = field(default_factory=dict)
    scope_hint: Optional[str] = None


def candidate_identity(*, module: str, canonical_document_id: str) -> str:
    """Stable identity for one legacy candidate.

    Takes the canonical document and nothing else — deliberately no decision,
    lease, filename or upload argument, so a caller cannot fold one in.
    """
    return f"{MIGRATION_IDENTITY_PREFIX}:{module}:{canonical_document_id}"


class ContractMigrationReconciliation:
    """Read-only inventory, and the separate act of materialising it."""

    def __init__(self, db: Any, *, module: str = "contracts") -> None:
        self._db = db
        self._module = module

    # -- inventory: reads only ---------------------------------------------- #

    async def inventory(
        self, *, organization_id: str, limit: Optional[int] = None
    ) -> Tuple[ReconciliationCandidate, ...]:
        """Preview the legacy candidates. Writes nothing at all.

        Not "writes no legal facts" — writes nothing. A reconciliation row
        created here would be an operator decision nobody made.
        """
        cursor = self._db["documents"].find(
            {"organization_id": organization_id, "uploadType": "contract"}
        )
        if limit is not None:
            cursor = cursor.limit(limit)
        documents = await cursor.to_list(length=limit)

        candidates: List[ReconciliationCandidate] = []
        for document in documents:
            document_id = str(document.get("_id") or "")
            if not document_id:
                continue
            session_evidence = await self._capture_session_evidence(document)
            candidates.append(
                ReconciliationCandidate(
                    candidate_id=candidate_identity(
                        module=self._module, canonical_document_id=document_id
                    ),
                    canonical_document_id=document_id,
                    organization_id=str(document.get("organization_id") or ""),
                    module=self._module,
                    session_evidence=session_evidence,
                    scope_hint=None,
                )
            )
        return tuple(candidates)

    async def _capture_session_evidence(self, document: Mapping[str, Any]) -> Dict[str, Any]:
        """Copy the TTL'd session's project signal while it still exists."""
        upload_id = document.get("upload_id")
        if not upload_id:
            return {}
        session = await self._db["contract_upload_sessions"].find_one({"upload_id": upload_id})
        if not session:
            return {}
        project_id = session.get("project_id")
        if not project_id:
            # An absent value here proves nothing in either direction: the
            # upload path collapses null and empty before writing.
            return {}
        return {"project_id": str(project_id)}

    # -- materialise: the separate, explicitly named write ------------------- #

    async def materialise_inventory(
        self, candidates: Sequence[ReconciliationCandidate]
    ) -> int:
        """Turn previewed candidates into durable reconciliation rows.

        Idempotent on candidate identity, so re-running inventory and
        materialising again converges on the same row instead of forking one.
        """
        written = 0
        for candidate in candidates:
            try:
                await self._db[RECONCILIATION_COLLECTION].insert_one(
                    {
                        "_id": candidate.candidate_id,
                        "candidate_id": candidate.candidate_id,
                        "canonical_document_id": candidate.canonical_document_id,
                        "organization_id": candidate.organization_id,
                        "module": candidate.module,
                        "scope_state": candidate.scope_state.value,
                        "type_state": candidate.type_state.value,
                        "scope_hint": candidate.scope_hint,
                        # Captured, not referenced: the session it came from will be
                        # gone by the time anyone adjudicates this row.
                        "session_evidence": dict(candidate.session_evidence),
                        "materialised_at": datetime.now(timezone.utc),
                    }
                )
            except DuplicateKeyError as exc:
                if (getattr(exc, "details", None) or {}).get("keyPattern") not in (None, {"_id": 1}):
                    # Some other unique constraint: not "already materialised".
                    raise
                # Already materialised: the identity is the candidate, so the row
                # that exists IS this candidate. Left exactly as it is - never
                # overwritten, so an adjudication or promotion on it survives.
                continue
            written += 1
        return written

    async def captured_session_evidence(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        """Read the capture from the durable row.

        Deliberately does not consult the upload session: by promotion time the
        TTL has expired, and re-reading would turn an absence into a finding.
        """
        return dict(row.get("session_evidence") or {})
