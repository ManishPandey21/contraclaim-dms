"""`get_link_history` was the ungated sibling of `list_links`.

`GET /api/evidence-graph/event-links/{link_group_id}/history` reads `event_links`
directly and returned raw `evidence_text`/`source_spans`. Its siblings on the
same collection (`list_links`, `downstream_links`) route through
`safe_event_records`; this history route was missed, so a link whose document
target is blocked kept serving the document's span text.

Final-certification HIGH. Fixed with the same projection.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.evidence_graph_service import EvidenceGraphService

MARKER = "BLOCKED-LINK-EVIDENCE-TEXT"


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    def limit(self, value: int) -> "_Cursor":
        self.rows = self.rows[:value]
        return self

    def __aiter__(self):
        async def _gen():
            for row in self.rows:
                yield row

        return _gen()

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows)


class _Links:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Optional[Dict[str, Any]] = None) -> _Cursor:
        return _Cursor(self.rows)


class _Documents:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        wanted = (query.get("_id", {}) or {})
        ids = wanted["$in"] if isinstance(wanted, dict) and "$in" in wanted else [wanted]
        for row in self.rows:
            if any(str(row.get("_id")) == str(i) for i in ids):
                return row
        return None


class _DB:
    def __init__(self, links: List[Dict[str, Any]], documents: List[Dict[str, Any]]) -> None:
        self.event_links = _Links(links)
        self.documents = _Documents(documents)


BLOCKED = {"_id": "doc-1", "processing_status": "human_review_required"}
CLEAN = {"_id": "doc-1", "processing_status": "metadata_extracted"}
FAILED = {"_id": "doc-1", "processing_status": "failed"}


def _link(**extra: Any) -> Dict[str, Any]:
    row = {
        "_id": "el-1",
        "link_group_id": "lg-1",
        "revision": 1,
        "target_type": "document",
        "target_id": "doc-1",
        "evidence_text": f"{MARKER}: quoted from the document.",
        "source_spans": [{"page": 3, "text": f"{MARKER}: span"}],
    }
    row.update(extra)
    return row


def _history(documents: List[Dict[str, Any]], link: Optional[Dict[str, Any]] = None) -> str:
    svc = EvidenceGraphService(_DB([link or _link()], documents))
    rows = asyncio.run(svc.get_link_history({"organization_id": "org-1"}, "lg-1"))
    return " ".join(str(r.get("evidence_text") or "") + " " + str(r.get("source_spans") or "") for r in rows)


def test_a_clean_link_keeps_its_evidence_in_history() -> None:
    assert MARKER in _history([CLEAN])


def test_a_blocked_link_evidence_is_withheld_from_history() -> None:
    assert MARKER not in _history([BLOCKED]), (
        "get_link_history served a blocked document's evidence_text/source_spans"
    )


def test_an_operational_failure_keeps_history_evidence() -> None:
    assert MARKER in _history([FAILED])


def test_a_non_document_link_history_is_untouched() -> None:
    link = _link(target_type="claim", target_id="c-1", evidence_text="claim-authored text",
                 source_spans=[])
    assert "claim-authored text" in _history([BLOCKED], link)
