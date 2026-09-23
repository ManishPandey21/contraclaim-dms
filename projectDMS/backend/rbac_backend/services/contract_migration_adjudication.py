"""Candidate claim, lease and operator adjudication.

Two operators cannot adjudicate the same candidate at once, and an abandoned
claim expires without changing anything legal.

**The claim is a unique insert, not a read-then-write.** A read-then-insert lets
two operators both observe "free" and both proceed; the unique ``_id`` is what
actually decides, and it decides in the database rather than in the process that
happened to check first.

**Lease state is operational and never legal.** It lives in its own collection,
disjoint from every authoritative one, and expiry has *exactly one* effect:
another operator may claim. It does not withdraw a decision, does not touch
applicability, does not touch a lifecycle event and does not touch a
classification. ``adjudicate`` reads the claim only to verify ownership — the
lease is never an input to the decision itself.

**Conflicts fail closed, and the loser is told what happened.** A second operator
disagreeing with a recorded adjudication does not overwrite it and does not
silently succeed: they are told who decided, and what they decided, so the
disagreement becomes a conversation rather than a race.

Adjudication is a decision *about a candidate*. It writes nothing authoritative —
promotion is a separate transaction owned by a later ticket.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..models.contract_document import ContractDocumentType
from .contract_migration_reconciliation import (
    RECONCILIATION_COLLECTION,
    CandidateNotFound,
    ScopeClassificationState,
    TypeClassificationState,
    scoped_candidate_filter,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ADJUDICATION_CLAIMS_COLLECTION",
    "ADJUDICATION_CLAIM_LEASE",
    "CandidateClaim",
    "CandidateNotFound",
    "ClaimUnavailable",
    "ConflictingAdjudication",
    "ContractMigrationAdjudication",
]

#: Operational only. Deliberately not in LEGAL_COLLECTIONS.
ADJUDICATION_CLAIMS_COLLECTION = "contract_migration_adjudication_claims"

#: The same lease shape the accepted backfill primitive uses: an abandoned claim
#: must not lock a candidate out forever.
ADJUDICATION_CLAIM_LEASE = timedelta(minutes=30)


class ClaimUnavailable(Exception):
    """Somebody else holds this candidate, or this claim is no longer live."""


class ConflictingAdjudication(Exception):
    """A different decision is already recorded for this candidate.

    Raised rather than overwritten. Two operators reaching different conclusions
    is a disagreement to be resolved by people; last-write-wins would hide it and
    make the surviving decision look unanimous.
    """


@dataclass(frozen=True)
class CandidateClaim:
    candidate_id: str
    operator_id: str
    owner_token: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContractMigrationAdjudication:
    """Claims a candidate, records a decision, releases."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def ensure_indexes(self) -> None:
        await self._db[ADJUDICATION_CLAIMS_COLLECTION].create_index(
            "candidate_id", unique=True, background=True
        )

    # -- claim --------------------------------------------------------------- #

    async def claim(
        self, candidate_id: str, *, organization_id: str, operator_id: str
    ) -> CandidateClaim:
        """Take the candidate, or raise.

        The insert is the primitive. Whoever's insert lands owns the candidate;
        everybody else is told immediately rather than proceeding on a stale read.

        The candidate must exist inside ``organization_id`` - the organisation
        the caller was authorised for - *before* the claim row is written, so a
        candidate of another organisation, or none at all, never acquires one.
        This read is not the race the unique insert decides: a candidate's
        organisation is written once at materialisation and never updated.
        """
        candidate = await self._db[RECONCILIATION_COLLECTION].find_one(
            scoped_candidate_filter(candidate_id, organization_id), {"_id": 1}
        )
        if candidate is None:
            raise CandidateNotFound()

        owner_token = uuid.uuid4().hex
        now = _now()
        try:
            await self._db[ADJUDICATION_CLAIMS_COLLECTION].insert_one(
                {
                    "_id": f"contract-migration-claim:{candidate_id}",
                    "candidate_id": candidate_id,
                    "operator_id": operator_id,
                    "owner_token": owner_token,
                    "claimed_at": now,
                    "lease_expires_at": now + ADJUDICATION_CLAIM_LEASE,
                }
            )
        except Exception as exc:
            raise ClaimUnavailable(
                f"candidate {candidate_id} is already claimed by another operator"
            ) from exc
        return CandidateClaim(
            candidate_id=candidate_id, operator_id=operator_id, owner_token=owner_token
        )

    async def release(self, claim: CandidateClaim) -> None:
        await self._db[ADJUDICATION_CLAIMS_COLLECTION].delete_one(
            {"candidate_id": claim.candidate_id, "owner_token": claim.owner_token}
        )

    async def expire_stale_claims(self) -> int:
        """Remove expired claims. This is the whole effect of expiry.

        Nothing legal is touched here, and nothing legal may ever be added: a
        lease that could withdraw a decision would make an operator's coffee
        break a legal event.
        """
        result = await self._db[ADJUDICATION_CLAIMS_COLLECTION].delete_many(
            {"lease_expires_at": {"$lte": _now()}}
        )
        return int(getattr(result, "deleted_count", 0) or 0)

    # -- adjudication -------------------------------------------------------- #

    async def adjudicate(
        self,
        claim: CandidateClaim,
        *,
        organization_id: str,
        scope_state: ScopeClassificationState,
        contract_document_type: Optional[ContractDocumentType],
        reason: str,
    ) -> None:
        """Record one operator's decision about one candidate.

        Two independent proofs, and the order is deliberate. The candidate must
        exist inside the authorised organisation first: checking the lease first
        would answer a foreign candidate with "no longer live", which tells the
        caller it exists. Only then must the claim be live and owned. A valid
        owner token proves the lease and nothing about tenancy.
        """
        candidate_filter = scoped_candidate_filter(claim.candidate_id, organization_id)
        row = await self._db[RECONCILIATION_COLLECTION].find_one(candidate_filter)
        if row is None:
            raise CandidateNotFound()

        live = await self._db[ADJUDICATION_CLAIMS_COLLECTION].find_one(
            {"candidate_id": claim.candidate_id, "owner_token": claim.owner_token}
        )
        if live is None:
            raise ClaimUnavailable(
                f"claim on {claim.candidate_id} is no longer live; re-claim before "
                "adjudicating so two operators cannot both believe they hold it"
            )

        if row.get("scope_state") == ScopeClassificationState.INVALID.value:
            # INVALID is terminal. Downgrading it to AMBIGUOUS would put a
            # tenancy violation back into the adjudication queue as an open
            # question.
            raise ConflictingAdjudication(
                f"candidate {claim.candidate_id} is INVALID, which is terminal and "
                "is never downgraded to an open state"
            )

        recorded_type = row.get("contract_document_type")
        proposed_type = (
            contract_document_type.value if contract_document_type is not None else None
        )
        if (
            row.get("type_state") == TypeClassificationState.TYPE_RESOLVED.value
            and recorded_type != proposed_type
        ):
            raise ConflictingAdjudication(
                f"candidate {claim.candidate_id} was already adjudicated as "
                f"{recorded_type} by {row.get('adjudicated_by')}; recording "
                f"{proposed_type} instead would overwrite that decision rather "
                "than resolve the disagreement"
            )

        update = {
            "scope_state": scope_state.value,
            "adjudicated_by": claim.operator_id,
            "adjudication_reason": reason,
            "adjudicated_at": _now(),
        }
        if contract_document_type is not None:
            update["type_state"] = TypeClassificationState.TYPE_RESOLVED.value
            update["contract_document_type"] = contract_document_type.value

        await self._db[RECONCILIATION_COLLECTION].update_one(
            candidate_filter, {"$set": update}
        )
