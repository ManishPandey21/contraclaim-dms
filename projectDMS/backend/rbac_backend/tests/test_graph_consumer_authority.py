"""G30: graph-derived drafting evidence must resolve current source authority.

`LetterDraftContextBuilder._graph_sources` took whatever `get_thread` returned
and put `node["subject"]` into a `SourceEvidence` label/snippet, with
`metadata=dict(node)` carrying the whole raw node - with no authority check and
no provenance resolution. A blocked document's letter therefore reached the
drafting context (and the prompt built from it) purely by being in the graph.

Two things fix it, and both are asserted here on the FINAL evidence objects:

* the shared Letter node is identity-only after G32, so a stale legacy
  `subject` left on it by an older writer must never become drafting content;
* each graph code is resolved back to its canonical Mongo letter/document and
  gated by the certified authority predicate before it becomes evidence.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

BLOCKED_MARKER = "BLOCKED_GRAPH_MARKER_987"
CLEAN_MARKER = "CLEAN_GRAPH_MARKER_123"
LEGACY_MARKER = "STALE_LEGACY_NODE_SUBJECT_555"


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    def limit(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    def __aiter__(self):
        async def _gen():
            for row in self.rows:
                yield row
        return _gen()

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows)


class _Coll:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Optional[Dict[str, Any]] = None) -> _Cursor:
        return _Cursor(self.rows)

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        wanted = (query or {}).get("_id")
        ids = wanted["$in"] if isinstance(wanted, dict) and "$in" in wanted else [wanted]
        for row in self.rows:
            if any(str(row.get("_id")) == str(i) for i in ids):
                return row
            for key in ("letterNoNormalized", "normCode", "letterNo"):
                if key in (query or {}) and str(row.get(key)) == str(query.get(key)):
                    return row
        return None


class _DB:
    def __init__(self, documents: List[Dict[str, Any]], letters: List[Dict[str, Any]]) -> None:
        self._c = {"documents": _Coll(documents), "letters": _Coll(letters)}

    def __getitem__(self, name: str) -> _Coll:
        return self._c.setdefault(name, _Coll([]))

    def __getattr__(self, name: str) -> _Coll:
        return self[name]


class _Graph:
    """Stands in for FalkorDB, returning nodes for a thread query."""

    enabled = True

    def __init__(self, nodes: List[Dict[str, Any]]) -> None:
        self.nodes = nodes

    def get_thread(self, code: str, depth: int = 6):
        return self.nodes


def _evidence(nodes, documents, letters):
    from rbac_backend.services.letter_drafting.context import LetterDraftContextBuilder
    from rbac_backend.services.publication_policy import graph_codes_denied

    builder = LetterDraftContextBuilder(db=_DB(documents, letters), graph_service=_Graph(nodes))
    codes = [n.get("normCode") for n in nodes]
    denied = asyncio.run(graph_codes_denied(_DB(documents, letters), codes))
    return denied


def test_a_blocked_letter_code_is_denied_for_graph_use() -> None:
    """Provenance: normCode -> canonical letter/document -> current authority."""
    from rbac_backend.services.publication_policy import graph_codes_denied

    db = _DB(
        documents=[{"_id": "doc-b", "letterNoNormalized": "ltr-b",
                    "subject": BLOCKED_MARKER, "processing_status": "human_review_required"}],
        letters=[],
    )
    denied = asyncio.run(graph_codes_denied(db, ["ltr-b"]))

    assert "ltr-b" in denied


def test_a_clean_letter_code_is_not_denied() -> None:
    from rbac_backend.services.publication_policy import graph_codes_denied

    db = _DB(
        documents=[{"_id": "doc-c", "letterNoNormalized": "ltr-c",
                    "subject": CLEAN_MARKER, "processing_status": "metadata_extracted"}],
        letters=[],
    )
    denied = asyncio.run(graph_codes_denied(db, ["ltr-c"]))

    assert denied == set()


def test_an_operationally_failed_letter_code_is_not_denied() -> None:
    """Model B: last-known-good graph support stays usable."""
    from rbac_backend.services.publication_policy import graph_codes_denied

    db = _DB(
        documents=[{"_id": "doc-f", "letterNoNormalized": "ltr-f",
                    "processing_status": "failed"}],
        letters=[],
    )
    assert asyncio.run(graph_codes_denied(db, ["ltr-f"])) == set()


def test_a_quarantined_letter_code_is_denied() -> None:
    from rbac_backend.services.publication_policy import graph_codes_denied

    db = _DB(
        documents=[{"_id": "doc-q", "letterNoNormalized": "ltr-q",
                    "processing_status": "metadata_extracted", "duplicate_status": "duplicate"}],
        letters=[],
    )
    assert "ltr-q" in asyncio.run(graph_codes_denied(db, ["ltr-q"]))


def test_a_code_with_no_resolvable_provenance_fails_closed() -> None:
    """Serving uncertainty must fail closed, per the graph safety principle."""
    from rbac_backend.services.publication_policy import graph_codes_denied

    db = _DB(documents=[], letters=[])
    assert "ltr-unknown" in asyncio.run(graph_codes_denied(db, ["ltr-unknown"]))


# --- Phase 13: the FINAL drafting evidence, not the graph query ----------------


def _graph_evidence(nodes, documents, include_codes=None):
    """Drive the real consumer and return the SourceEvidence it produces."""
    from rbac_backend.models.letter import Letter
    from rbac_backend.services.letter_drafting.context import DraftContextBuilder

    builder = DraftContextBuilder(
        document_service=None,
        conversation_service=None,
        contract_service=object(),
        graph_service=_Graph(nodes),
        db=_DB(documents, []),
    )

    class _Req:
        include_letter_codes = list(include_codes or [])
        exclude_letter_codes: list = []

    class _Ltr:
        letter_no = "LTR-TARGET"

    codes, sources = asyncio.run(
        builder._graph_sources(_Ltr(), _Req(), "org-A", "proj-A", [])
    )
    return codes, sources


def _blob(sources) -> str:
    return " ".join(
        f"{s.label or ''} {s.snippet or ''} {s.metadata or {}}" for s in sources
    )


def test_a_blocked_graph_node_never_becomes_drafting_evidence() -> None:
    """The G30 marker test: absent from label, snippet AND raw metadata."""
    nodes = [{"normCode": "ltr-b", "code": "LTR-B", "subject": BLOCKED_MARKER}]
    documents = [{"_id": "doc-b", "letterNoNormalized": "ltr-b",
                  "processing_status": "human_review_required"}]

    codes, sources = _graph_evidence(nodes, documents)

    assert "ltr-b" not in codes
    assert BLOCKED_MARKER not in _blob(sources)


def test_a_clean_graph_node_still_becomes_drafting_evidence() -> None:
    nodes = [{"normCode": "ltr-c", "code": "LTR-C", "subject": CLEAN_MARKER}]
    documents = [{"_id": "doc-c", "letterNoNormalized": "ltr-c",
                  "processing_status": "metadata_extracted"}]

    codes, sources = _graph_evidence(nodes, documents)

    assert "ltr-c" in codes


def test_a_stale_legacy_node_subject_cannot_influence_drafting() -> None:
    """Phase 6: legacy contaminated properties on the shared node are inert.

    An older writer left `subject` on the globally-shared Letter node. Its
    supporting document is blocked, so the code is denied and the stale value
    cannot reach drafting - the node is never the authority.
    """
    nodes = [{"normCode": "ltr-legacy", "code": "LTR-LEGACY", "subject": LEGACY_MARKER}]
    documents = [{"_id": "doc-l", "letterNoNormalized": "ltr-legacy",
                  "processing_status": "human_review_required"}]

    _, sources = _graph_evidence(nodes, documents)

    assert LEGACY_MARKER not in _blob(sources)


def test_a_manually_included_blocked_code_is_still_denied() -> None:
    """The include-list must not be an authority bypass."""
    documents = [{"_id": "doc-b", "letterNoNormalized": "ltr-b",
                  "processing_status": "human_review_required"}]

    codes, sources = _graph_evidence([], documents, include_codes=["ltr-b"])

    assert "ltr-b" not in codes


def test_an_unresolvable_code_fails_closed_in_drafting() -> None:
    nodes = [{"normCode": "ltr-ghost", "code": "LTR-GHOST", "subject": BLOCKED_MARKER}]

    codes, sources = _graph_evidence(nodes, documents=[])

    assert "ltr-ghost" not in codes
    assert BLOCKED_MARKER not in _blob(sources)
