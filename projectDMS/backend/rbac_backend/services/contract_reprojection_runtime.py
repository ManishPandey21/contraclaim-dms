"""The runtime owner of Contract Master reprojection: the contract-worker.

Promotion and classification correction commit ``projection_status = PENDING``
in the same write that makes revision N authoritative. That write **is** the
durable job: the instrument row itself says "revision N has no current
projection". This runtime finds such rows, claims ``(instrument, N)`` under a
lease, builds the projection, and publishes it under the worker's fences.

Why the instrument row is the queue, and not the Redis contract-ingest queue:

* **No dual write.** A Redis enqueue after the promotion transaction can fail
  after the commit (or be lost to a Redis flush), leaving PENDING forever -
  exactly the staging failure, by a different route. The PENDING write cannot be
  lost without losing the promotion itself.
* **No upload identity to fake.** The ingest queue is keyed by ``upload_id`` and
  its job hash is the upload-facing status record; a projection job would have to
  impersonate an upload to use it.
* **Identity is the fence.** Work is keyed by ``(contract_document_id,
  classification_revision)`` in ``contract_reprojection_claims``: a retry of N
  converges on the same key, N+1 is a different key N cannot block, and a stale
  N completion is discarded by the instrument's revision fence.

A derived-store outage never reaches the legal fields: the build fails, the
worker records FAILED (fenced) with a retry backoff, and the promotion, the
classification and the applicability are exactly as they were.

Only ``rbac_backend.worker`` starts the loop, and only when
``START_CONTRACT_REPROJECTION_WORKERS`` is set (the contract-worker service).
The web process never runs it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..models.contract_document import ProjectionStatus
from .contract_document_store import CONTRACT_DOCUMENTS_COLLECTION
from .contract_projection_builder import ContractProjectionBuilder
from .contract_reprojection_worker import (
    ContractReprojectionWorker,
    StaleWorkerGeneration,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RUNTIME_HEARTBEATS_COLLECTION",
    "ContractReprojectionRuntime",
    "ReprojectionPassReport",
    "due_projection_filter",
]

#: One row per running reprojection loop, rewritten after every pass. Operational
#: only: ``post_deploy_verify.sh`` reads it to prove the owner is the
#: contract-worker, is alive, and runs the deployed release.
RUNTIME_HEARTBEATS_COLLECTION = "contract_reprojection_runtime_heartbeats"


def due_projection_filter() -> Dict[str, Any]:
    """Instruments whose projection is not current for their live revision.

    The same comparison the scope resolver uses, negated: anything that would
    NOT be evidence-eligible on projection grounds is due. IN_PROGRESS rows are
    included on purpose - a worker that died mid-build leaves one, and the claim
    lease (not this filter) decides when it may be taken over.
    """
    return {
        "classification_revision": {"$gte": 1},
        "$or": [
            {"projection_status": {"$ne": ProjectionStatus.CURRENT.value}},
            {"$expr": {"$ne": ["$projection_revision", "$classification_revision"]}},
        ],
    }


@dataclass
class ReprojectionPassReport:
    completed: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    discarded: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


class ContractReprojectionRuntime:
    """Finds due generations, claims, builds, and publishes under the fences."""

    def __init__(
        self,
        db: Any,
        *,
        builder: Optional[ContractProjectionBuilder] = None,
        worker_id: Optional[str] = None,
        batch_size: int = 10,
    ) -> None:
        self._db = db
        self._builder = builder or ContractProjectionBuilder(db)
        self._worker = ContractReprojectionWorker(db)
        self._worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        self._batch_size = max(1, int(batch_size))
        #: Called while a pass runs (per instrument, and from build heartbeats),
        #: so liveness does not wait for a long pass to end.
        self.alive: Optional[Callable[[], Awaitable[None]]] = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def _alive(self) -> None:
        if self.alive is not None:
            await self.alive()

    async def run_once(self) -> ReprojectionPassReport:
        """One pass over the due instruments. Datastore errors propagate."""
        report = ReprojectionPassReport()
        await self._alive()
        await self._worker.exhaust_crashed_claims()
        # A snapshot of ids, not a cursor held open across slow builds. Every due
        # row is visited, but at most ``batch_size`` are built per pass; a row that
        # cannot be claimed (live lease, backoff, exhausted) is skipped cheaply,
        # so it can never starve the rows behind it.
        due_ids = [
            row["_id"]
            for row in await self._db[CONTRACT_DOCUMENTS_COLLECTION]
            .find(due_projection_filter(), {"_id": 1})
            .sort([("_id", 1)])
            .to_list(length=None)
        ]
        for instrument_id in due_ids:
            if len(report.completed) + len(report.failed) + len(report.discarded) >= self._batch_size:
                break
            instrument = await self._db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            if instrument is None:
                continue
            await self._project(instrument, report)
            await self._alive()
        return report

    async def _project(self, instrument: Dict[str, Any], report: ReprojectionPassReport) -> None:
        instrument_id = str(instrument["_id"])
        revision = int(instrument.get("classification_revision") or 0)
        claim = await self._worker.claim(instrument_id, revision=revision, worker_id=self._worker_id)
        if claim is None:
            report.skipped.append(instrument_id)
            return

        try:
            async def heartbeat() -> None:
                await self._worker.verify(claim)
                await self._alive()

            built = await self._builder.build(instrument, revision=revision, heartbeat=heartbeat)
        except asyncio.CancelledError:
            await self._release_on_shutdown(claim)
            raise
        except StaleWorkerGeneration as exc:
            # The lease was lost while building; the new owner reports.
            logger.info("reprojection of %s r%s abandoned: %s", instrument_id, revision, exc)
            report.discarded.append(instrument_id)
            return
        except Exception as exc:
            await self._record_failure(claim, instrument_id, exc, report)
            return

        try:
            await self._worker.complete(
                claim, publish=lambda session: self._builder.publish_rows(built, session)
            )
        except asyncio.CancelledError:
            await self._release_on_shutdown(claim)
            raise
        except StaleWorkerGeneration as exc:
            logger.info("reprojection of %s r%s discarded: %s", instrument_id, revision, exc)
            report.discarded.append(instrument_id)
            return
        except Exception as exc:
            await self._record_failure(claim, instrument_id, exc, report)
            return

        logger.info(
            "reprojection of %s r%s is CURRENT: rows=%s vectors=%s graph=%s source=%s",
            instrument_id,
            revision,
            len(built.rows),
            built.vector_points,
            built.graph_status,
            built.clause_source,
        )
        report.completed.append(instrument_id)

    async def _release_on_shutdown(self, claim) -> None:
        try:
            await self._worker.release_on_shutdown(claim)
        except Exception:
            logger.exception("could not release reprojection claim on shutdown; the lease will")

    async def _record_failure(self, claim, instrument_id, exc, report) -> None:
        reason = f"{type(exc).__name__}: {exc}"
        try:
            await self._worker.fail(claim, reason=reason)
        except StaleWorkerGeneration as stale:
            logger.info("reprojection failure of %s not recorded: %s", instrument_id, stale)
            report.discarded.append(instrument_id)
            return
        report.failed.append(instrument_id)


# --------------------------------------------------------------------------- #
# process lifecycle (contract-worker only)
# --------------------------------------------------------------------------- #

_task: Optional[asyncio.Task] = None


async def run_forever(db: Any, *, interval_seconds: float, runtime=None) -> None:
    runtime = runtime or ContractReprojectionRuntime(db)
    beat = _Heartbeat(db, runtime, interval_seconds)
    runtime.alive = beat.alive
    try:
        await _run_passes(db, runtime, beat, interval_seconds)
    finally:
        # A stopped owner is not a live one: its row must not read as a second
        # owner (or as this one) to a verification run right after a redeploy.
        await beat.remove()


async def _run_passes(db: Any, runtime: Any, beat: "_Heartbeat", interval_seconds: float) -> None:
    from .contract_reprojection_worker import ensure_reprojection_claim_indexes

    indexes_ready = False
    while True:
        try:
            if not indexes_ready:
                # Inside the loop, not at process start: an index problem must not
                # take the contract-worker's other owners (ingest, scheduler) down.
                await ensure_reprojection_claim_indexes(db)
                indexes_ready = True
                try:
                    # Operational only: a conflicting heartbeat index must never
                    # stop reprojection itself.
                    await ensure_runtime_heartbeat_indexes(db)
                except Exception:
                    logger.warning("could not ensure the reprojection heartbeat index", exc_info=True)
            # Liveness for the whole pass, including build steps that call no
            # heartbeat (clause extraction, graph sync, the completion commit).
            # The pass start is recorded too, so a pass that never ends reads
            # as stuck - liveness alone would keep a hung owner looking healthy.
            await beat.pass_started()
            ticker = asyncio.create_task(beat.tick_forever())
            try:
                report = await runtime.run_once()
            finally:
                ticker.cancel()
                await asyncio.gather(ticker, return_exceptions=True)
            await beat.pass_completed(report)
            if report.completed or report.failed or report.discarded:
                logger.info(
                    "reprojection pass: completed=%s failed=%s discarded=%s",
                    report.completed,
                    report.failed,
                    report.discarded,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Loud, and the loop keeps its owner alive: PENDING rows stay due and
            # are retried on the next pass rather than abandoned.
            logger.exception("contract reprojection pass failed")
        await asyncio.sleep(interval_seconds)


#: How often liveness is written while a pass is running.
_ALIVE_EVERY_SECONDS = 30.0

#: Rows of processes that died without removing theirs expire on their own.
_HEARTBEAT_TTL_SECONDS = 7 * 24 * 3600


class _Heartbeat:
    """The runtime's own liveness row. Best effort: never stops the owner."""

    def __init__(self, db: Any, runtime: Any, interval_seconds: float) -> None:
        self._db = db
        self._id = getattr(runtime, "worker_id", None) or socket.gethostname()
        self._interval = float(interval_seconds)
        self._started_at = datetime.now(timezone.utc)
        self._last_written = 0.0

    async def _write(self, extra: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        try:
            await self._db[RUNTIME_HEARTBEATS_COLLECTION].update_one(
                {"_id": self._id},
                {
                    "$set": {
                        "host": socket.gethostname(),
                        "pid": os.getpid(),
                        "release": os.environ.get("RELEASE_SHA") or "unknown",
                        "interval_seconds": self._interval,
                        "started_at": self._started_at,
                        "last_alive_at": now,
                        **extra,
                    }
                },
                upsert=True,
            )
            self._last_written = time.monotonic()
        except Exception:
            logger.warning("could not record the reprojection runtime heartbeat", exc_info=True)

    async def alive(self) -> None:
        if time.monotonic() - self._last_written >= _ALIVE_EVERY_SECONDS:
            await self._write({})

    async def tick_forever(self) -> None:
        while True:
            await self.alive()
            await asyncio.sleep(max(1.0, _ALIVE_EVERY_SECONDS))

    async def pass_started(self) -> None:
        await self._write({"pass_started_at": datetime.now(timezone.utc)})

    async def pass_completed(self, report: "ReprojectionPassReport") -> None:
        await self._write(
            {
                "last_pass_at": datetime.now(timezone.utc),
                "last_pass": {
                    "completed": len(report.completed),
                    "failed": len(report.failed),
                    "discarded": len(report.discarded),
                    "skipped": len(report.skipped),
                },
            }
        )

    async def remove(self) -> None:
        try:
            await self._db[RUNTIME_HEARTBEATS_COLLECTION].delete_one({"_id": self._id})
        except Exception:
            logger.warning("could not remove the reprojection runtime heartbeat", exc_info=True)


async def ensure_runtime_heartbeat_indexes(db: Any) -> None:
    await db[RUNTIME_HEARTBEATS_COLLECTION].create_index(
        [("last_alive_at", 1)],
        name="reprojection_heartbeat_ttl",
        expireAfterSeconds=_HEARTBEAT_TTL_SECONDS,
    )


async def start_contract_reprojection_runtime(db: Any, *, interval_seconds: float) -> None:
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(run_forever(db, interval_seconds=interval_seconds))
    logger.info("Contract reprojection runtime started interval=%ss", interval_seconds)


async def stop_contract_reprojection_runtime() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
