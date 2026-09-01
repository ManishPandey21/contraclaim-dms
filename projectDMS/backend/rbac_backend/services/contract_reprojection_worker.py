"""Reprojection worker: claim a generation, and commit only if it is still current.

Ticket 07 made a classification correction take effect immediately — revision
N+1 commits and the previous projection stops counting as evidence. This module
closes the other half of that story: a worker that started on revision N and
finished *after* the correction must not be able to publish its generation.

The guard is a **conditional write**, not a re-read:

    update contract_documents
    where _id = <instrument> and classification_revision = N
    set   projection_revision = N, projection_status = CURRENT

If that matches zero documents, the revision moved while the worker was busy and
the completion is discarded. A read-then-write would leave a window between the
check and the update exactly as wide as the work itself — which is where the race
lives, since reprojection is the slow part.

Both writes are fenced, not only the successful one. A crashed revision-N worker
stamping ``FAILED`` over revision N+1 is the same regression wearing the other
mask.

Claim state is operational: it lives in its own collection, never beside the
legal fields, and its expiry decides who may work rather than what is true.

Scope note: this module orchestrates and fences. It does not change the embedding
input format, the vector prefilter, graph identity, or anything a consumer reads
— those are later tickets, and the fencing is correct while the legacy projection
format still exists.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ..models.contract_document import ProjectionStatus
from .contract_document_store import CONTRACT_DOCUMENTS_COLLECTION

logger = logging.getLogger(__name__)

__all__ = [
    "ContractReprojectionWorker",
    "ProjectionClaim",
    "StaleWorkerGeneration",
    "REPROJECTION_CLAIMS_COLLECTION",
]

REPROJECTION_CLAIMS_COLLECTION = "contract_reprojection_claims"

#: A crashed worker must not hold a generation forever. Ownership is a lease,
#: reclaimable once it expires — the same shape the accepted backfill claims use.
REPROJECTION_CLAIM_LEASE = timedelta(minutes=30)


class StaleWorkerGeneration(Exception):
    """This worker's generation is no longer the current one.

    Raised rather than returned as a falsy value so a caller cannot mistake a
    discarded completion for a successful one. It is also deliberately distinct
    from a transient datastore error: retrying will never help, because the
    world moved on.
    """


@dataclass(frozen=True)
class ProjectionClaim:
    """Ownership of one instrument at one classification generation.

    The revision is part of the claim, not looked up later: a claim that only
    said "this instrument has projection work" could not tell a late completion
    from a current one.
    """

    contract_document_id: str
    revision: int
    worker_id: str
    owner_token: str


class ContractReprojectionWorker:
    """Claims a generation, produces derived rows, and commits under a fence."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- identity and row shape -------------------------------------------- #

    def clause_identity(
        self,
        *,
        organization_id: str,
        document_id: str,
        clause_no: str,
        chunk_index: int,
    ) -> str:
        """Stable clause identity, deliberately free of any classification term.

        Retries converge because the identity does not move when the type does.
        Including the revision would fork the clause set on every correction, so
        a retry would build a parallel set beside the old one rather than
        replacing it.
        """
        raw = f"{organization_id}|{document_id}|{clause_no}|{chunk_index}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def build_clause_row(
        self,
        *,
        organization_id: str,
        document_id: str,
        clause_no: str,
        chunk_index: int,
        text: str,
        source_classification_revision: int,
        ingest_project_id: Optional[str] = None,
        ingest_contract_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One projected clause row.

        Two deliberate absences:

        * **no instrument-type copy.** A derived copy of the authoritative type
          is what creates the staleness problem in the first place; consumers
          join to the instrument record instead. (DEBT-01, for rows produced
          here.)
        * **document-scoped, not contract-scoped.** ``ingest_project_id`` and
          ``ingest_contract_id`` are provenance of where the text was ingested,
          never authorisation — an organisation-owned instrument has neither,
          and requiring them would make it unprojectable. (DEBT-05.)

        ``source_classification_revision`` is a staleness marker: it may exclude
        a row from evidence, never authorise one.
        """
        return {
            "_id": self.clause_identity(
                organization_id=organization_id,
                document_id=document_id,
                clause_no=clause_no,
                chunk_index=chunk_index,
            ),
            "organization_id": organization_id,
            "document_id": document_id,
            "clause_no": clause_no,
            "chunk_index": chunk_index,
            "text": text,
            "source_classification_revision": source_classification_revision,
            "ingest_project_id": ingest_project_id,
            "ingest_contract_id": ingest_contract_id,
        }

    # -- claim -------------------------------------------------------------- #

    async def claim(
        self, contract_document_id: str, *, revision: int, worker_id: str
    ) -> Optional[ProjectionClaim]:
        """Take ownership of one generation, or return ``None``.

        Returns ``None`` when the generation is already superseded, or when
        another live worker holds it. Both are ordinary outcomes, not errors:
        there is simply nothing for this worker to do.
        """
        record = await self._db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
            {"_id": contract_document_id}
        )
        if record is None:
            return None
        if int(record.get("classification_revision") or 0) != int(revision):
            logger.info(
                "reprojection claim refused for %s: revision %s is superseded by %s",
                contract_document_id,
                revision,
                record.get("classification_revision"),
            )
            return None

        owner_token = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        claim_id = f"contract-reprojection:{contract_document_id}:{revision}"
        try:
            await self._db[REPROJECTION_CLAIMS_COLLECTION].insert_one(
                {
                    "_id": claim_id,
                    "contract_document_id": contract_document_id,
                    "revision": revision,
                    "worker_id": worker_id,
                    "owner_token": owner_token,
                    "status": "processing",
                    "claimed_at": now,
                    "lease_expires_at": now + REPROJECTION_CLAIM_LEASE,
                }
            )
        except Exception:
            # A live claim already exists. Insert is the atomic primitive here:
            # a read-then-insert would let two workers both see "free".
            logger.info("reprojection generation already claimed: %s", claim_id)
            return None

        # IN_PROGRESS is operational. It deliberately does NOT set
        # projection_revision: publishing the generation before the work exists
        # would make an unfinished projection look current.
        await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": contract_document_id, "classification_revision": revision},
            {"$set": {"projection_status": ProjectionStatus.IN_PROGRESS.value}},
        )
        return ProjectionClaim(
            contract_document_id=contract_document_id,
            revision=revision,
            worker_id=worker_id,
            owner_token=owner_token,
        )

    # -- fenced terminal writes --------------------------------------------- #

    async def complete(self, claim: ProjectionClaim) -> None:
        """Publish this generation, if it is still the current one.

        Idempotent: replaying a completion that already landed matches the same
        document and sets the same values.
        """
        await self._fenced_set(
            claim,
            {
                "projection_revision": claim.revision,
                "projection_status": ProjectionStatus.CURRENT.value,
            },
            action="complete",
        )
        await self._release(claim, status="complete")

    async def fail(self, claim: ProjectionClaim, *, reason: str) -> None:
        """Record that this generation could not be built, if it is still current.

        Fenced for the same reason as ``complete``: a late failing worker must
        not stamp FAILED over a newer generation. The authoritative
        classification is never touched — a derived-store outage is not a legal
        failure, and letting it retract a confirmed classification would give a
        derived system a veto over an authoritative fact.
        """
        logger.warning(
            "reprojection failed for %s revision %s: %s",
            claim.contract_document_id,
            claim.revision,
            reason,
        )
        await self._fenced_set(
            claim,
            {"projection_status": ProjectionStatus.FAILED.value},
            action="fail",
        )
        await self._release(claim, status="failed")

    async def _fenced_set(
        self, claim: ProjectionClaim, values: Dict[str, Any], *, action: str
    ) -> None:
        result = await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {
                "_id": claim.contract_document_id,
                "classification_revision": claim.revision,
            },
            {"$set": values},
        )
        if getattr(result, "matched_count", 0) == 0:
            raise StaleWorkerGeneration(
                f"cannot {action} projection for {claim.contract_document_id} at "
                f"revision {claim.revision}: the authoritative classification has "
                "advanced. This worker's output describes a superseded generation "
                "and is discarded; retrying will not help."
            )

    async def _release(self, claim: ProjectionClaim, *, status: str) -> None:
        await self._db[REPROJECTION_CLAIMS_COLLECTION].update_one(
            {
                "_id": f"contract-reprojection:{claim.contract_document_id}:{claim.revision}",
                "owner_token": claim.owner_token,
            },
            {"$set": {"status": status, "released_at": datetime.now(timezone.utc)}},
        )
