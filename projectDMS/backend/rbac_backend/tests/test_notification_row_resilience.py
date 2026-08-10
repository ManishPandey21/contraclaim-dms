"""One unreadable row must cost that row, not the whole feed.

``list_notifications`` mapped stored documents in a bare list comprehension, so a
single row the model could no longer parse raised inside the request handler and
returned 500 to *every* recipient in *every* tenant -- an availability failure
with a blast radius of the entire platform, triggered by one bad document.

The fix drops the row instead. That is only acceptable because the drop is loud:
each skipped row is logged with its id and the validation failure, and an
aggregate line records how many were lost. A silent short list would trade an
obvious outage for a quiet wrong answer, which is worse.

Single-document reads keep the old behaviour on purpose -- asking for one
specific unreadable notification should fail rather than pretend it is missing.
"""

from __future__ import annotations

import datetime
import logging

import pytest

from rbac_backend.utils import notification_service as ns
from rbac_backend.utils.notification_service import (
    _notification_from_doc,
    _notifications_from_docs,
)


def _good(doc_id: str = "ok-1") -> dict:
    return {
        "_id": doc_id,
        "type": "new_upload",
        "category": "uploads",
        "resource_id": "res-1",
        "resource_type": "document",
        "recipients": ["user-1"],
        "title": "A readable notification",
        "message": "body",
        "created_at": datetime.datetime.utcnow(),
    }


def _malformed(doc_id: str = "bad-1") -> dict:
    """Shaped like a legacy row: an id, and a type the enum no longer accepts."""
    return {"_id": doc_id, "type": "NOT_A_REAL_TYPE"}


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture(fn):
    """Run ``fn`` with a handler bound to the module logger.

    Not ``caplog``: this suite runs under a custom async bridge, and a capture
    that quietly collects nothing would let these assertions pass while the
    warning had been removed.
    """
    handler = _Collector()
    logger = ns.logger
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        result = fn()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    return result, [r.getMessage() for r in handler.records]


def test_one_malformed_row_does_not_take_down_the_feed():
    docs = [_good("ok-1"), _malformed("bad-1"), _good("ok-2")]

    items, _ = _capture(lambda: _notifications_from_docs(docs, where="test"))

    assert len(items) == 2, "the readable rows must still be returned"
    assert {str(i.id) for i in items} == {"ok-1", "ok-2"}


def test_every_readable_row_survives_when_nothing_is_malformed():
    """Guard against the drop path quietly eating good rows."""
    docs = [_good("ok-1"), _good("ok-2"), _good("ok-3")]

    items, messages = _capture(lambda: _notifications_from_docs(docs, where="test"))

    assert len(items) == 3
    assert not messages, "nothing was skipped, so nothing should be logged"


def test_the_skipped_row_is_identified_in_the_logs():
    docs = [_good("ok-1"), _malformed("bad-row-42")]

    _, messages = _capture(lambda: _notifications_from_docs(docs, where="list_test"))

    joined = "\n".join(messages)
    assert "bad-row-42" in joined, "a dropped row must be findable from the logs"
    assert "list_test" in joined, "the call site must be identified"


def test_the_loss_is_summarised_so_a_short_list_is_never_silent():
    docs = [_malformed("bad-1"), _malformed("bad-2"), _good("ok-1")]

    items, messages = _capture(lambda: _notifications_from_docs(docs, where="test"))

    assert len(items) == 1
    assert any("2 notification row(s) skipped" in m for m in messages), messages


def test_an_empty_page_is_not_an_error():
    items, messages = _capture(lambda: _notifications_from_docs([], where="test"))
    assert items == []
    assert not messages


def test_single_document_read_still_raises():
    """Asking for one unreadable notification must fail, not return nothing."""
    with pytest.raises(Exception):
        _notification_from_doc(_malformed())


def test_no_unguarded_row_mapping_remains():
    """The comprehension is the defect; keep it from coming back."""
    import pathlib

    src = pathlib.Path(ns.__file__).read_text(encoding="utf-8")
    assert "[_notification_from_doc(item) for item in" not in src, (
        "a bare comprehension over _notification_from_doc lets one malformed row "
        "abort the whole response; use _notifications_from_docs"
    )
