"""One writer of a contract's source at a time, as a durable lease.

A contract ingest (upload, OCR retry, reindex) rewrites the document's accepted
page text and lexical rows; the Contract Master projection is built from that
text. ``documents.status = processing`` cannot serialise the two: two ingests of
one document can run at once (a queue job redelivered after a stalled heartbeat,
or two uploads' jobs), and the first to finish writes ``completed`` over the
other's ``processing`` while it is still writing.

This module is the serialisation, keyed by the canonical Document id:

* ``acquire`` is one conditional upsert. It takes the source when it is settled,
  failed, or its lease expired; otherwise the unique ``_id`` answers "held"
  (``ContractSourceBusy``). There is no read-then-write window.
* every acquisition bumps ``generation``. The projection builder records the
  generation it read and publishes only if it is still the settled one when the
  completion transaction commits.
* ``renew`` and ``settle`` / ``fail`` are fenced on the owner token: an owner
  whose lease expired and was reclaimed learns it (``LostSourceLease``) and must
  not write its outcome.
* a lease that expired while ``mutating`` is a crashed ingest. The source it left
  is half-written, so it is not projectable until another ingest settles it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .publication_policy import resolve_canonical_document

__all__ = [
    "CONTRACT_SOURCE_LEASES_COLLECTION",
    "SOURCE_LEASE_DURATION",
    "STOP_RELEASE_HOLD",
    "ContractSourceBusy",
    "LostSourceLease",
    "SourceLease",
    "SourceLeaseFenced",
    "SourceState",
    "SourceTainted",
    "acquire",
    "canonical_source_key",
    "fail",
    "release_untouched",
    "renew",
    "settle",
    "source_state",
    "stop_release_hold",
    "taint",
]

CONTRACT_SOURCE_LEASES_COLLECTION = "contract_source_leases"

#: Renewed by the running ingest well inside this; a crashed ingest's lease
#: becomes reclaimable once it lapses.
SOURCE_LEASE_DURATION = timedelta(minutes=10)

#: How often the running ingest renews.
SOURCE_LEASE_RENEW_EVERY = timedelta(seconds=60)

#: The ingest stops itself when it has not renewed for this long - well before
#: its lease can lapse and another ingest can take the source. Writes are not
#: fenced one by one, so this self-fence is what guarantees two writers never
#: overlap (margin covers clock skew between workers and the time to unwind).
SOURCE_LEASE_SELF_FENCE = SOURCE_LEASE_DURATION - timedelta(minutes=3)

#: How long a source released by a STOPPED ingest (self-fenced, or cancelled by
#: a shutdown) stays unacquirable. Cancelling the ingest's task does not stop a
#: driver operation already sent: Motor runs it on an executor thread, and a
#: retryable write is re-sent once after reconnecting. Releasing at once let a
#: new owner start while that write could still land in the source it was
#: rewriting - text from two writers, settled by the new owner as coherent.
#: ``None``: derived from the client's own bounds (``stop_release_hold``).
STOP_RELEASE_HOLD: Optional[timedelta] = None

#: The floor of the derived hold: the bounds cover MongoDB operations, and an
#: ingest also writes through other clients.
STOP_RELEASE_HOLD_FLOOR = timedelta(minutes=2)


def stop_release_hold() -> timedelta:
    """How long the CLIENT keeps any operation the stopped ingest already sent
    alive: two attempts (one retry) of server selection + connect + socket
    timeout, never less than ``STOP_RELEASE_HOLD_FLOOR``.

    It is a mitigation, not a proof: without CSOT (``timeoutMS``) no
    ``maxTimeMS`` reaches the server, which may finish an operation after the
    client gave up, and a pool wait has no bound. Fencing each source write on
    the lease generation is the complete fix (implementation-debt doc)."""
    if STOP_RELEASE_HOLD is not None:
        return STOP_RELEASE_HOLD
    from ..core.config import settings

    bounds = (
        int(settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS),
        int(settings.MONGODB_CONNECT_TIMEOUT_MS),
        int(settings.MONGODB_SOCKET_TIMEOUT_MS),
    )
    if min(bounds) <= 0:
        # 0 means "no timeout": the client never gives up, so nothing derives a
        # bound - hold for a whole lease rather than silently the floor.
        return max(STOP_RELEASE_HOLD_FLOOR, SOURCE_LEASE_DURATION)
    return max(STOP_RELEASE_HOLD_FLOOR, timedelta(milliseconds=2 * sum(bounds)))

MUTATING = "mutating"
SETTLED = "settled"
FAILED = "failed"


class ContractSourceBusy(Exception):
    """Another live ingest owns this document's source; nothing was written."""

    def __init__(self, message: str, *, holder: Optional[str] = None) -> None:
        super().__init__(message)
        self.holder = holder


class LostSourceLease(Exception):
    """This ingest's lease lapsed and another ingest now owns the source."""

    def __init__(self, message: str, *, holder: Optional[str] = None) -> None:
        super().__init__(message)
        self.holder = holder


class SourceLeaseFenced(Exception):
    """The ingest stopped itself: it could not renew its lease for long enough
    that another ingest might soon take the source. No other owner is known, so
    this is an ordinary failed attempt - reported and retried, never discarded.
    """


class SourceTainted(Exception):
    """The ingest finished, but a superseded writer touched the source meanwhile.

    The source was released as failed rather than settled; the ingest must be
    reported as failed (and retried), not completed.
    """


@dataclass(frozen=True)
class SourceLease:
    key: str
    owner_token: str
    generation: int
    #: What the source was before this acquisition, restored by
    #: ``release_untouched`` when the ingest stops before writing anything.
    previous_status: str = SETTLED


@dataclass(frozen=True)
class SourceState:
    """What the builder needs: may it read the source, and which generation."""

    status: str
    generation: int

    @property
    def projectable(self) -> bool:
        return self.status == SETTLED


async def canonical_source_key(db: Any, document_id: Any, *, session: Any = None) -> str:
    """The lease key: the canonical Document's ``_id``, as a string.

    Resolved, so an ingest naming a legacy string-keyed document and a build
    naming its ObjectId-keyed twin agree on one lease.
    """
    document = await resolve_canonical_document(db, document_id, session=session)
    return str(document["_id"]) if document else str(document_id)


def _leases(db: Any) -> Any:
    return db[CONTRACT_SOURCE_LEASES_COLLECTION]


async def acquire(
    db: Any, key: str, *, owner: str, lease: timedelta = SOURCE_LEASE_DURATION
) -> SourceLease:
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    try:
        row = await _leases(db).find_one_and_update(
            {
                "_id": key,
                "$or": [
                    # Released - and, after a stopped ingest, past the hold.
                    {"status": {"$in": [SETTLED, FAILED]}, "reacquirable_at": {"$not": {"$gt": now}}},
                    {"lease_expires_at": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "status": MUTATING,
                    "owner_token": token,
                    "owner": owner,
                    "acquired_at": now,
                    "lease_expires_at": now + lease,
                },
                "$inc": {"generation": 1},
                # A new writer rewrites the whole source: an earlier taint is
                # about text this ingest replaces.
                "$unset": {"tainted_at": "", "taint_reason": "", "reacquirable_at": ""},
            },
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
    except DuplicateKeyError as exc:
        row = await _leases(db).find_one({"_id": key}, {"owner": 1, "status": 1, "reacquirable_at": 1})
        if row and row.get("status") != MUTATING:
            # Held after a stopped ingest: nobody owns it, so no holder - the
            # waiting job runs again rather than taking itself for a live copy.
            raise ContractSourceBusy(
                f"the source of {key} is held until {row.get('reacquirable_at')} after a stopped "
                "ingest, so a write it had already sent cannot land under a new owner",
                holder=None,
            ) from exc
        raise ContractSourceBusy(
            f"another ingest is writing the source of {key}; this one did not start",
            holder=(row or {}).get("owner"),
        ) from exc
    before = row or {}
    previous = before.get("status")
    if previous not in (SETTLED, FAILED) or before.get("tainted_at"):
        # Never leased: settled. A lapsed ``mutating`` (a crashed ingest) or a
        # tainted source: half-written, so failed - whatever the status field
        # said when this acquisition cleared the taint.
        previous = SETTLED if not before else FAILED
    return SourceLease(
        key=key,
        owner_token=token,
        generation=int(before.get("generation") or 0) + 1,
        previous_status=previous,
    )


async def renew(db: Any, lease: SourceLease, *, duration: timedelta = SOURCE_LEASE_DURATION) -> None:
    now = datetime.now(timezone.utc)
    result = await _leases(db).update_one(
        {"_id": lease.key, "owner_token": lease.owner_token, "status": MUTATING},
        {"$set": {"lease_expires_at": now + duration}},
    )
    if getattr(result, "matched_count", 0) == 0:
        raise LostSourceLease(
            f"the source lease of {lease.key} lapsed and was taken over; this ingest's "
            "outcome is discarded",
            holder=await _holder(db, lease.key),
        )


async def _release(
    db: Any, lease: SourceLease, status: str, error: Optional[str], *, hold: bool = False
) -> str:
    now = datetime.now(timezone.utc)
    owned = {"_id": lease.key, "owner_token": lease.owner_token, "status": MUTATING}
    values = {
        "released_at": now,
        "lease_expires_at": None,
        "last_error": error,
        "reacquirable_at": now + stop_release_hold() if hold else None,
    }
    if status == SETTLED:
        # Settles only while untainted. A taint recorded while this owner was
        # writing (a superseded ingest kept writing) leaves text interleaved
        # from two writers: the release below turns it into failed instead.
        result = await _leases(db).update_one(
            {**owned, "tainted_at": {"$exists": False}},
            {"$set": {**values, "status": SETTLED}},
        )
        if getattr(result, "matched_count", 0):
            return SETTLED
        status = FAILED
    result = await _leases(db).update_one(owned, {"$set": {**values, "status": status}})
    if getattr(result, "matched_count", 0) == 0:
        raise LostSourceLease(
            f"the source lease of {lease.key} was taken over before this ingest finished",
            holder=await _holder(db, lease.key),
        )
    return status


#: The holder could not be read. Treated like a live owner: guessing "nobody"
#: would write a failed status over an ingest that may still be running.
UNKNOWN_HOLDER = "unknown"


async def _holder(db: Any, key: str) -> Optional[str]:
    try:
        row = await _leases(db).find_one({"_id": key}, {"owner": 1, "status": 1})
    except Exception:
        return UNKNOWN_HOLDER
    if not row or row.get("status") != MUTATING:
        return None
    return row.get("owner")


async def settle(db: Any, lease: SourceLease) -> None:
    """The ingest finished: this generation of the source may be projected.

    Raises ``SourceTainted`` when a superseded writer touched the source while
    this one owned it: the release then recorded failed, and the ingest must say
    so rather than report a completed contract nothing can project.
    """
    # Decided by the write itself, not by reading the row back: once released,
    # a waiting ingest may already own the row.
    if await _release(db, lease, SETTLED, None) != SETTLED:
        raise SourceTainted(
            f"the source of {lease.key} was written by a superseded ingest while this "
            "one owned it; it is not projectable until a clean ingest"
        )


async def fail(db: Any, lease: SourceLease, *, error: str, stopped: bool = False) -> None:
    """The ingest failed part-way: the source is not projectable until re-ingested.

    ``stopped``: the ingest was cancelled (self-fence, shutdown) rather than
    finishing its own awaits, so a write it sent may still be in flight; the
    source stays unacquirable for ``stop_release_hold()``.
    """
    await _release(db, lease, FAILED, str(error)[:500], hold=stopped)


async def taint(db: Any, key: str, *, reason: str) -> None:
    """A superseded ingest may have written after it lost the lease.

    Its page writes are not fenced one by one, so the source any owner leaves
    behind is no longer known to be coherent. The taint is recorded first, so an
    owner still writing releases as failed; then a source already settled is
    failed. Either order of the two writers ends failed. Only a fresh ingest
    (``acquire`` clears the taint) makes the source projectable again.
    """
    now = datetime.now(timezone.utc)
    # One write: a settled source becomes failed in the same update that records
    # the taint, so no acquisition can clear the taint between the two.
    await _leases(db).update_one(
        {"_id": key},
        [
            {
                "$set": {
                    "tainted_at": now,
                    "taint_reason": str(reason)[:500],
                    "status": {"$cond": [{"$eq": ["$status", SETTLED]}, FAILED, "$status"]},
                }
            }
        ],
    )


async def release_untouched(db: Any, lease: SourceLease) -> None:
    """The ingest stopped before writing the source: put back what was there."""
    await _release(db, lease, lease.previous_status, None)


async def source_state(db: Any, key: str, *, session: Any = None) -> SourceState:
    """Settled/generation as recorded; a source never leased is settled at 0.

    A ``mutating`` row is reported as such whether or not its lease lapsed: a
    lapsed one is a crashed ingest, and its source is half-written.
    """
    kwargs = {"session": session} if session is not None else {}
    row = await _leases(db).find_one({"_id": key}, **kwargs)
    if row is None:
        return SourceState(status=SETTLED, generation=0)
    return SourceState(status=str(row.get("status") or MUTATING), generation=int(row.get("generation") or 0))
