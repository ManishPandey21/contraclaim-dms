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

Ownership is fenced as well as currency. A claim is a lease keyed by
``(instrument, revision)``; an expired lease is taken over by one atomic
conditional upsert, and every terminal write checks the owner token, so a worker
whose lease was reclaimed can neither complete nor fail the generation.

Runtime owner: ``contract_reprojection_runtime`` in the contract-worker process
claims, builds (``contract_projection_builder``) and completes through
``complete(publish=...)``, which commits the lexical rows, the ownership check and
the CURRENT stamp in one transaction. This module still does not change the
embedding input format, the vector prefilter or graph identity.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pymongo.errors import DuplicateKeyError, OperationFailure

from ..models.contract_document import ProjectionStatus
from .contract_document_store import CONTRACT_DOCUMENTS_COLLECTION

logger = logging.getLogger(__name__)

__all__ = [
    "ContractReprojectionWorker",
    "LostProjectionClaim",
    "ProjectionClaim",
    "ProjectionPublicationUnavailable",
    "StaleWorkerGeneration",
    "REPROJECTION_CLAIMS_COLLECTION",
    "REPROJECTION_MAX_ATTEMPTS",
    "claim_identity",
    "ensure_reprojection_claim_indexes",
    "retry_backoff",
]

REPROJECTION_CLAIMS_COLLECTION = "contract_reprojection_claims"

#: A crashed worker must not hold a generation forever. Ownership is a lease,
#: reclaimable once it expires — the same shape the accepted backfill claims use.
REPROJECTION_CLAIM_LEASE = timedelta(minutes=30)

#: Lease takeovers after which a generation whose worker DIED every time (no
#: fail() ever recorded) stops being reclaimed and goes visibly FAILED. A build
#: that fails cleanly - an embedder or vector-store outage - is never exhausted:
#: it keeps retrying with capped backoff, so an outage longer than the backoff
#: series cannot strand every generation promoted during it.
REPROJECTION_MAX_ATTEMPTS = 6

#: Claim states that hold a lease, reclaimable once it expires.
_LEASED = ("processing",)

_WRITE_CONFLICT = 112
_TRANSACTION_ATTEMPTS = 3


class StaleWorkerGeneration(Exception):
    """This worker's generation is no longer the current one.

    Raised rather than returned as a falsy value so a caller cannot mistake a
    discarded completion for a successful one. It is also deliberately distinct
    from a transient datastore error: retrying will never help, because the
    world moved on.
    """


class LostProjectionClaim(StaleWorkerGeneration):
    """This worker's lease expired and another worker now owns the generation.

    A subclass of ``StaleWorkerGeneration`` because the consequence is the same:
    the output is discarded and retrying from this worker cannot help.
    """


class ProjectionPublicationUnavailable(Exception):
    """The projection cannot be published atomically in this environment."""


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
        self,
        contract_document_id: str,
        *,
        revision: int,
        worker_id: str,
        lease: timedelta = REPROJECTION_CLAIM_LEASE,
    ) -> Optional[ProjectionClaim]:
        """Take ownership of one generation, or return ``None``.

        Returns ``None`` when the generation is superseded, when another live
        worker holds it, when it already completed, or when a failed attempt is
        still inside its retry backoff. Those are ordinary outcomes: there is
        simply nothing for this worker to do.

        A datastore failure is **not** one of them and propagates. Reporting an
        outage as "already claimed" would make every worker quietly skip the
        generation for as long as the database is unwell, and PENDING would look
        like a queue that is merely busy.

        The lease primitive is one conditional upsert. The filter matches only a
        claim that may be taken over - an expired lease, or a failure whose
        backoff has elapsed; when nothing matches, the upsert tries to insert
        the deterministic ``_id`` and the unique ``_id`` index answers "held"
        with ``DuplicateKeyError``. There is no read-then-write window in which
        two workers could both see the generation as free.
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
        claim_id = claim_identity(contract_document_id, revision)
        takeovers: List[Dict[str, Any]] = [
            {
                "status": {"$in": list(_LEASED)},
                "lease_expires_at": {"$lte": now},
                # A build that kills its process never reaches fail();
                # without this bound it would be reclaimed forever.
                "attempts": {"$lt": REPROJECTION_MAX_ATTEMPTS},
            },
            {"status": "failed", "next_attempt_at": {"$lte": now}},
        ]
        if not _projection_current_at(record, revision):
            # The instrument row is the queue. A generation it still reports as
            # due is never "held" by a claim that says it completed: whatever
            # left the two disagreeing, the row wins and the generation is
            # rebuilt, rather than skipped forever as contention.
            takeovers.append({"status": "complete"})
        try:
            await self._db[REPROJECTION_CLAIMS_COLLECTION].update_one(
                {"_id": claim_id, "$or": takeovers},
                {
                    "$set": {
                        "worker_id": worker_id,
                        "owner_token": owner_token,
                        "status": "processing",
                        "claimed_at": now,
                        "lease_expires_at": now + lease,
                    },
                    "$inc": {"attempts": 1},
                    "$setOnInsert": {
                        "contract_document_id": contract_document_id,
                        "revision": int(revision),
                        "created_at": now,
                    },
                },
                upsert=True,
            )
        except DuplicateKeyError:
            # Contention, and only contention: a live lease, a completed
            # generation, or a failure still inside its backoff.
            logger.debug("reprojection generation not claimable now: %s", claim_id)
            return None

        claim = ProjectionClaim(
            contract_document_id=contract_document_id,
            revision=revision,
            worker_id=worker_id,
            owner_token=owner_token,
        )
        try:
            # The due check above was read before the upsert. A completion that
            # committed in between leaves the generation CURRENT while this
            # worker has just taken over its ``complete`` claim; building it
            # again would rewrite derived stores under a published projection.
            # CURRENT at this revision implies the claim had completed, so it is
            # handed straight back as ``complete``.
            latest = await self._db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
                {"_id": contract_document_id}
            )
            if latest is not None and _projection_current_at(latest, revision):
                await self._db[REPROJECTION_CLAIMS_COLLECTION].update_one(
                    {**self._owned(claim), "status": "processing"},
                    {
                        "$set": {"status": "complete", "released_at": now},
                        "$inc": {"attempts": -1},
                    },
                )
                return None
            # IN_PROGRESS is operational. It deliberately does NOT set
            # projection_revision: publishing the generation before the work
            # exists would make an unfinished projection look current.
            # Never over CURRENT: a claimant that stalled past its lease must not
            # demote a generation another worker has already published.
            await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
                {
                    "_id": contract_document_id,
                    "classification_revision": revision,
                    "projection_status": {"$ne": ProjectionStatus.CURRENT.value},
                },
                {"$set": {"projection_status": ProjectionStatus.IN_PROGRESS.value}},
            )
        except BaseException:
            # Cancelled (a redeploy) or failed after the claim landed but before
            # the caller could hold it: give it back now rather than strand the
            # generation for a whole lease and spend a crash-budget attempt.
            await self._give_back(claim)
            raise
        return claim

    async def _give_back(self, claim: ProjectionClaim) -> None:
        try:
            now = datetime.now(timezone.utc)
            await self._db[REPROJECTION_CLAIMS_COLLECTION].update_one(
                {**self._owned(claim), "status": "processing"},
                {
                    "$set": {
                        "status": "failed",
                        "released_at": now,
                        "next_attempt_at": now,
                        "last_error": "released before the build started",
                    },
                    "$inc": {"attempts": -1},
                },
            )
        except Exception:
            logger.exception(
                "could not give back reprojection claim %s; its lease will expire",
                claim_identity(claim.contract_document_id, claim.revision),
            )

    async def renew(
        self, claim: ProjectionClaim, *, lease: timedelta = REPROJECTION_CLAIM_LEASE
    ) -> None:
        """Extend this worker's lease, or learn that it was lost."""
        now = datetime.now(timezone.utc)
        result = await self._db[REPROJECTION_CLAIMS_COLLECTION].update_one(
            {**self._owned(claim), "status": "processing"},
            {"$set": {"lease_expires_at": now + lease}},
        )
        if getattr(result, "matched_count", 0) == 0:
            raise LostProjectionClaim(self._lost_message(claim, "renew"))

    async def verify(self, claim: ProjectionClaim) -> None:
        """Still the owner AND still the live revision.

        Checked before each derived write that cannot join the completion
        transaction (vector store, graph). Extends the lease as a side effect.
        """
        await self.renew(claim)
        record = await self._db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
            {"_id": claim.contract_document_id}, {"classification_revision": 1}
        )
        if record is None or int(record.get("classification_revision") or 0) != claim.revision:
            raise StaleWorkerGeneration(
                f"revision {claim.revision} of {claim.contract_document_id} is no longer "
                "current; derived writes for it stop here"
            )

    async def exhaust_crashed_claims(self) -> int:
        """Turn crash-looping generations into a visible FAILED.

        A claim whose lease expired after ``REPROJECTION_MAX_ATTEMPTS`` attempts
        (each one died without reaching ``fail()``) is no longer reclaimable. It
        is marked failed/exhausted here, and its instrument - if still at that
        revision - stamped FAILED instead of sitting IN_PROGRESS forever.
        """
        now = datetime.now(timezone.utc)
        exhausted = 0
        claims = self._db[REPROJECTION_CLAIMS_COLLECTION]
        stale = await claims.find(
            {
                "status": {"$in": list(_LEASED)},
                "lease_expires_at": {"$lte": now},
                "attempts": {"$gte": REPROJECTION_MAX_ATTEMPTS},
            }
        ).to_list(length=None)
        for row in stale:
            if await self._exhaust_one(row, now):
                exhausted += 1
                logger.error(
                    "reprojection of %s revision %s exhausted by repeated worker death",
                    row.get("contract_document_id"),
                    row.get("revision"),
                )
        return exhausted

    async def _exhaust_one(self, row: Dict[str, Any], now: datetime) -> bool:
        """The claim and its instrument move together, or neither does.

        Two separate writes let a failure between them exhaust the claim while
        the instrument stayed IN_PROGRESS: due forever, and never claimable.
        """
        claims = self._db[REPROJECTION_CLAIMS_COLLECTION]

        async def exhaust(session: Any) -> bool:
            result = await claims.update_one(
                {
                    "_id": row["_id"],
                    "owner_token": row.get("owner_token"),
                    "status": {"$in": list(_LEASED)},
                    # Re-checked in the write: an owner that renewed between the
                    # find and this update is alive and must not be exhausted.
                    "lease_expires_at": {"$lte": now},
                    "attempts": {"$gte": REPROJECTION_MAX_ATTEMPTS},
                },
                {
                    "$set": {
                        "status": "failed",
                        "released_at": now,
                        "exhausted": True,
                        "next_attempt_at": None,
                        "last_error": (
                            "worker died during every attempt: the lease expired "
                            f"{row.get('attempts')} times with no recorded outcome"
                        ),
                    }
                },
                session=session,
            )
            if getattr(result, "matched_count", 0) == 0:
                return False
            await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
                {
                    "_id": row.get("contract_document_id"),
                    "classification_revision": row.get("revision"),
                    "projection_status": {"$ne": ProjectionStatus.CURRENT.value},
                },
                {"$set": {"projection_status": ProjectionStatus.FAILED.value}},
                session=session,
            )
            return True

        client = getattr(self._db, "client", None)
        if client is not None and hasattr(client, "start_session"):
            async with await client.start_session() as session:
                return bool(await session.with_transaction(exhaust))
        return await exhaust(None)

    # -- fenced terminal writes --------------------------------------------- #

    async def complete(
        self,
        claim: ProjectionClaim,
        *,
        publish: Optional[Callable[[Any], Awaitable[None]]] = None,
    ) -> None:
        """Publish this generation, if this worker still owns it and it is current.

        With ``publish``, the derived rows and the CURRENT stamp commit in ONE
        transaction together with the ownership check, so none of them can land
        without the others:

        * ownership: the claim moves ``processing -> complete`` only under this
          worker's owner token, so a worker whose lease was reclaimed cannot
          complete (``LostProjectionClaim``);
        * currency: the instrument is updated only where
          ``classification_revision == claim.revision``. A correction committed
          first makes that match nothing and the whole transaction - rows
          included - is discarded (``StaleWorkerGeneration``); a correction
          racing the commit is a write conflict, retried against the new state
          and then discarded the same way.

        Idempotent for the owner: replaying a completion that already landed
        under the same owner token changes nothing and raises nothing.
        """
        if publish is None:
            await self._complete_steps(claim, session=None)
            return

        client = getattr(self._db, "client", None)
        if client is None or not hasattr(client, "start_session"):
            raise ProjectionPublicationUnavailable(
                "publishing a projection requires a transactional session; without "
                "one the rows could land without the CURRENT stamp or the reverse"
            )
        for attempt in range(1, _TRANSACTION_ATTEMPTS + 1):
            try:
                async with await client.start_session() as session:
                    async with session.start_transaction():
                        if await self._complete_steps(claim, session=session):
                            await publish(session)
                return
            except OperationFailure as exc:
                transient = exc.has_error_label("TransientTransactionError") or (
                    getattr(exc, "code", None) == _WRITE_CONFLICT
                )
                if not transient or attempt == _TRANSACTION_ATTEMPTS:
                    raise
                logger.info(
                    "reprojection publication for %s revision %s conflicted; retrying",
                    claim.contract_document_id,
                    claim.revision,
                )

    async def _complete_steps(self, claim: ProjectionClaim, *, session: Any) -> bool:
        """Ownership, then the fenced CURRENT stamp. False = already completed."""
        now = datetime.now(timezone.utc)
        owned = await self._db[REPROJECTION_CLAIMS_COLLECTION].update_one(
            {**self._owned(claim), "status": {"$in": list(_LEASED)}},
            {"$set": {"status": "complete", "released_at": now}},
            session=session,
        )
        if getattr(owned, "matched_count", 0) == 0:
            existing = await self._db[REPROJECTION_CLAIMS_COLLECTION].find_one(
                self._owned(claim), session=session
            )
            if existing is not None and existing.get("status") == "complete":
                # This owner's completion already landed; replaying it is a no-op.
                return False
            raise LostProjectionClaim(self._lost_message(claim, "complete"))

        await self._fenced_set(
            claim,
            {
                "projection_revision": claim.revision,
                "projection_status": ProjectionStatus.CURRENT.value,
            },
            action="complete",
            session=session,
        )
        return True

    async def fail(self, claim: ProjectionClaim, *, reason: str) -> None:
        """Record that this generation could not be built, if it is still current.

        Fenced twice, like ``complete``: a worker whose lease was reclaimed does
        not get to report on work it no longer owns, and a late failing worker
        must not stamp FAILED over a newer generation. The authoritative
        classification is never touched - a derived-store outage is not a legal
        failure, and letting it retract a confirmed classification would give a
        derived system a veto over an authoritative fact.

        The attempt is always scheduled for retry, with exponential backoff
        capped at an hour: a derived-store outage heals by itself once the store
        is back, whatever its length. The instrument stays FAILED - evidence
        withheld, visibly - until a retry succeeds.
        """
        logger.warning(
            "reprojection failed for %s revision %s: %s",
            claim.contract_document_id,
            claim.revision,
            reason,
        )
        now = datetime.now(timezone.utc)
        existing = await self._db[REPROJECTION_CLAIMS_COLLECTION].find_one(self._owned(claim))
        failures = int((existing or {}).get("failures") or 0) + 1
        next_attempt_at = now + retry_backoff(failures)
        owned = await self._db[REPROJECTION_CLAIMS_COLLECTION].update_one(
            {**self._owned(claim), "status": "processing"},
            {
                "$set": {
                    "status": "failed",
                    "released_at": now,
                    "last_error": str(reason)[:2000],
                    "next_attempt_at": next_attempt_at,
                    "exhausted": False,
                    # Clean failures do not spend the crash budget.
                    "attempts": 0,
                    "failures": failures,
                }
            },
        )
        if getattr(owned, "matched_count", 0) == 0:
            raise LostProjectionClaim(self._lost_message(claim, "fail"))
        await self._fenced_set(
            claim,
            {"projection_status": ProjectionStatus.FAILED.value},
            action="fail",
            session=None,
            unless_current=True,
        )

    async def release_on_shutdown(self, claim: ProjectionClaim) -> None:
        """Give the generation back at once when the worker is being stopped.

        A redeploy must not strand the build in progress for a whole lease, nor
        spend one of the crash-budget attempts. Best effort by nature: if this
        write does not land, the lease expiry recovers the generation anyway.
        """
        now = datetime.now(timezone.utc)
        released = await self._db[REPROJECTION_CLAIMS_COLLECTION].update_one(
            {**self._owned(claim), "status": "processing"},
            {
                "$set": {
                    "status": "failed",
                    "released_at": now,
                    "next_attempt_at": now,
                    "last_error": "released: worker stopped mid-build",
                },
                "$inc": {"attempts": -1},
            },
        )
        if getattr(released, "matched_count", 0):
            await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
                {
                    "_id": claim.contract_document_id,
                    "classification_revision": claim.revision,
                    "projection_status": ProjectionStatus.IN_PROGRESS.value,
                },
                {"$set": {"projection_status": ProjectionStatus.PENDING.value}},
            )

    async def request_retry(
        self,
        contract_document_id: str,
        *,
        reason: str = "operator retry",
        only_if_failed: bool = False,
        only_if_current_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Rebuild the live revision's projection on the next pass.

        Used by an operator (a failed generation) and by ingestion (the source
        text changed under a CURRENT generation - an OCR retry or a reindex -
        so the published projection no longer describes the document).

        A completed generation is re-opened and the instrument is moved back to
        PENDING (fenced on the revision, derived field only), so evidence stops
        answering from the superseded text at once. Never touches a legal field.
        A generation being built right now is revoked: its owner token is rotated
        and its lease expired, so the running build - which read the old source -
        can no longer complete (``LostProjectionClaim``), and the next pass
        rebuilds from the new source.

        The live revision is read inside the same transaction as the writes: read
        before it, a correction committing in between made the retry re-open the
        superseded generation and leave the live one untouched.

        ``only_if_failed`` (storage repair): re-open a FAILED generation and
        nothing else, decided inside the transaction, so a generation that
        became CURRENT meanwhile is never withdrawn by a repair.
        """
        outcome: Dict[str, Any] = {}

        async def reopen(session: Any) -> bool:
            record = await self._db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
                {"_id": contract_document_id}, session=session
            )
            if record is None:
                raise LookupError(f"no instrument {contract_document_id}")
            revision = int(record.get("classification_revision") or 0)
            claim_id = claim_identity(contract_document_id, revision)
            outcome.update(revision=revision, claim_id=claim_id)
            if only_if_failed and record.get("projection_status") != ProjectionStatus.FAILED.value:
                return False
            if only_if_current_at is not None and not (
                revision == int(only_if_current_at) and _projection_current_at(record, revision)
            ):
                return False
            now = datetime.now(timezone.utc)
            # ONE write whatever state the claim is in. A completed or failed
            # generation is re-opened; a running build is revoked - its owner
            # token rotates, so it can neither complete nor fail - and the
            # generation is claimable at once.
            reopened = await self._db[REPROJECTION_CLAIMS_COLLECTION].update_one(
                {"_id": claim_id},
                {
                    "$set": {
                        "status": "failed",
                        "owner_token": uuid.uuid4().hex,
                        "next_attempt_at": now,
                        "lease_expires_at": now,
                        "released_at": now,
                        "exhausted": False,
                        "attempts": 0,
                        "retry_requested_at": now,
                        "retry_reason": reason,
                    }
                },
                session=session,
            )
            await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
                {
                    "_id": contract_document_id,
                    "classification_revision": revision,
                    "projection_status": ProjectionStatus.FAILED.value
                    if only_if_failed
                    else {"$ne": ProjectionStatus.PENDING.value},
                },
                {"$set": {"projection_status": ProjectionStatus.PENDING.value}},
                session=session,
            )
            return bool(getattr(reopened, "matched_count", 0))

        # The claim and the instrument move together. Done as separate writes, a
        # completion transaction committing between them left the instrument
        # PENDING beside a ``complete`` claim - due, and never claimable again.
        # Inside a transaction the two serialise against ``complete``: whichever
        # commits second sees the other's result (a write conflict is retried).
        client = getattr(self._db, "client", None)
        if client is not None and hasattr(client, "start_session"):
            async with await client.start_session() as session:
                scheduled = await session.with_transaction(reopen)
        else:
            scheduled = await reopen(None)
        return {
            "contract_document_id": contract_document_id,
            "revision": outcome["revision"],
            "claim_id": outcome["claim_id"],
            "retry_scheduled": bool(scheduled),
        }

    async def claim_state(self, contract_document_id: str, revision: int) -> Optional[Dict[str, Any]]:
        """Operational diagnostics for one generation (never authority)."""
        row = await self._db[REPROJECTION_CLAIMS_COLLECTION].find_one(
            {"_id": claim_identity(contract_document_id, revision)}
        )
        if row is None:
            return None
        state = {
            key: row.get(key)
            for key in (
                "status",
                "attempts",
                "failures",
                "exhausted",
                "last_error",
                "next_attempt_at",
                "lease_expires_at",
            )
        }
        state["last_error"] = _operator_safe(state.get("last_error"))
        return state

    async def _fenced_set(
        self,
        claim: ProjectionClaim,
        values: Dict[str, Any],
        *,
        action: str,
        session: Any,
        unless_current: bool = False,
    ) -> None:
        fence: Dict[str, Any] = {
            "_id": claim.contract_document_id,
            "classification_revision": claim.revision,
        }
        if unless_current:
            # A failure report must never withdraw a generation that another
            # worker has already published at this revision.
            fence["projection_status"] = {"$ne": ProjectionStatus.CURRENT.value}
        result = await self._db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            fence,
            {"$set": values},
            session=session,
        )
        if getattr(result, "matched_count", 0) == 0:
            raise StaleWorkerGeneration(
                f"cannot {action} projection for {claim.contract_document_id} at "
                f"revision {claim.revision}: the authoritative classification has "
                "advanced or the generation is already published. This worker's "
                "output is discarded; retrying will not help."
            )

    @staticmethod
    def _owned(claim: ProjectionClaim) -> Dict[str, Any]:
        return {
            "_id": claim_identity(claim.contract_document_id, claim.revision),
            "owner_token": claim.owner_token,
        }

    @staticmethod
    def _lost_message(claim: ProjectionClaim, action: str) -> str:
        return (
            f"cannot {action} projection for {claim.contract_document_id} at revision "
            f"{claim.revision}: worker {claim.worker_id} no longer owns the claim (its "
            "lease expired and another worker took it over). Its output is "
            "discarded; the new owner publishes the generation."
        )


def _projection_current_at(record: Dict[str, Any], revision: int) -> bool:
    return record.get("projection_status") == ProjectionStatus.CURRENT.value and int(
        record.get("projection_revision") or 0
    ) == int(revision)


_URL = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


#: ``host:port`` without a scheme (driver topology text, httpx connect errors).
_HOST_PORT = re.compile(r"\b[\w.-]+:\d{2,5}\b")
#: Absolute filesystem paths (POSIX or Windows), e.g. a stored-file location.
_PATH = re.compile(r"(?:\b[A-Za-z]:)?[\\/](?:[\w.~-]+[\\/])+[\w.~-]*")


def _operator_safe(text: Any) -> Any:
    """Failure text for the projection route: no endpoints or paths, bounded length."""
    if not isinstance(text, str):
        return text
    text = _URL.sub("<endpoint>", text)
    text = _HOST_PORT.sub("<endpoint>", text)
    text = _PATH.sub("<path>", text)
    return text[:500]


def claim_identity(contract_document_id: str, revision: int) -> str:
    """Projection work identity: one instrument at one classification revision.

    Never the document id alone, never "latest": a retry of revision N converges
    on this key, and revision N+1 is a different key that N cannot block.
    """
    return f"contract-reprojection:{contract_document_id}:{int(revision)}"


def retry_backoff(attempts: int) -> timedelta:
    """1, 2, 4, 8 ... minutes, capped at an hour."""
    return timedelta(minutes=min(60, 2 ** max(0, attempts - 1)))


async def ensure_reprojection_claim_indexes(db: Any) -> None:
    """Claim lookups by instrument, and the crash-exhaustion sweep."""
    await db[REPROJECTION_CLAIMS_COLLECTION].create_index(
        [("contract_document_id", 1), ("revision", 1)],
        name="reprojection_claim_instrument_revision",
    )
    await db[REPROJECTION_CLAIMS_COLLECTION].create_index(
        [("status", 1), ("lease_expires_at", 1)],
        name="reprojection_claim_status_lease",
    )
