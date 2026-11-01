"""The durable document-job claim must be a single atomic operation.

Phase 2 of the extraction plan introduces a second process (document-worker)
claiming from `document_processing_jobs`. A find-then-update claim, or a filter
that does not exclude already-claimed jobs, would hand the same document to two
workers - producing duplicate OCR spend and racing writes to the same record.

Verified 2026-08-14: `_claim_next_processing_job` is already a single
find_one_and_update. These tests keep it that way.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Dict, List, Optional

from rbac_backend.services.document_service import DocumentService


def _claim_source() -> str:
    return inspect.getsource(DocumentService._claim_next_processing_job)


def test_claim_uses_a_single_find_one_and_update() -> None:
    source = _claim_source()

    assert "find_one_and_update" in source, (
        "The job claim must be a single atomic find_one_and_update. A find_one "
        "followed by update_one lets two workers claim the same job."
    )
    assert "update_one" not in source
    assert "find_one(" not in source


def test_claim_filters_on_unclaimed_statuses_only() -> None:
    source = _claim_source()

    assert '"status": {"$in": ["queued", "retrying"]}' in source, (
        "The claim filter must exclude jobs already in 'processing', otherwise "
        "a second worker re-claims work in flight."
    )


def test_claim_transitions_the_job_out_of_the_claimable_set() -> None:
    source = _claim_source()

    assert '"status": "processing"' in source
    assert "$inc" in source and "attempts" in source


class _AtomicJobCollection:
    """Fake collection with Mongo's find_one_and_update matching semantics.

    Only the behaviour the claim depends on is modelled: filter by status,
    apply $set/$inc to the first match, and return it. Because the update
    changes `status`, a correctly-filtered claim cannot return the same
    document twice.
    """

    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self.documents = documents
        self.calls = 0

    def _matches(self, document: Dict[str, Any], query: Dict[str, Any]) -> bool:
        status_clause = query.get("status") or {}
        allowed = status_clause.get("$in", [])
        return document.get("status") in allowed

    async def find_one_and_update(
        self,
        query: Dict[str, Any],
        update: Dict[str, Any],
        *,
        sort: Any = None,
        return_document: Any = None,
    ) -> Optional[Dict[str, Any]]:
        self.calls += 1
        for document in self.documents:
            if not self._matches(document, query):
                continue
            document.update(update.get("$set", {}))
            for field, delta in (update.get("$inc") or {}).items():
                document[field] = document.get(field, 0) + delta
            return dict(document)
        return None


async def test_two_claimers_never_receive_the_same_job() -> None:
    collection = _AtomicJobCollection(
        [
            {"_id": "job-1", "status": "queued", "created_at": datetime(2026, 8, 1)},
            {"_id": "job-2", "status": "queued", "created_at": datetime(2026, 8, 2)},
        ]
    )
    claim_filter = {"status": {"$in": ["queued", "retrying"]}}
    claim_update = {"$set": {"status": "processing"}, "$inc": {"attempts": 1}}

    first = await collection.find_one_and_update(claim_filter, claim_update)
    second = await collection.find_one_and_update(claim_filter, claim_update)

    assert first is not None and second is not None
    assert first["_id"] != second["_id"]


async def test_a_third_claimer_gets_nothing_once_the_queue_is_drained() -> None:
    collection = _AtomicJobCollection(
        [{"_id": "job-1", "status": "queued", "created_at": datetime(2026, 8, 1)}]
    )
    claim_filter = {"status": {"$in": ["queued", "retrying"]}}
    claim_update = {"$set": {"status": "processing"}, "$inc": {"attempts": 1}}

    assert await collection.find_one_and_update(claim_filter, claim_update) is not None
    assert await collection.find_one_and_update(claim_filter, claim_update) is None


async def test_a_claimed_job_records_an_attempt() -> None:
    collection = _AtomicJobCollection(
        [{"_id": "job-1", "status": "queued", "created_at": datetime(2026, 8, 1)}]
    )

    claimed = await collection.find_one_and_update(
        {"status": {"$in": ["queued", "retrying"]}},
        {"$set": {"status": "processing"}, "$inc": {"attempts": 1}},
    )

    assert claimed is not None
    assert claimed["attempts"] == 1
    assert claimed["status"] == "processing"


def test_stale_recovery_transitions_the_job_atomically() -> None:
    """Recovery must claim the job in one operation, like the claim itself.

    It also writes the recovered status onto `db.documents`, which is a
    different collection and a different concern - that write is allowed to
    fail without un-recovering the job, and does. Only the write to
    document_processing_jobs has to be atomic.
    """
    source = inspect.getsource(DocumentService.recover_stale_processing_jobs)

    assert "document_processing_jobs.find_one_and_update" in source
    assert "document_processing_jobs.update_one" not in source
