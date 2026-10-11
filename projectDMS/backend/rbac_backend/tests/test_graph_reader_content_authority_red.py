"""G30: a graph node is relevance and topology. It is never content, and its
mere presence is never a slot in the answer.

Two reader defects, both reproduced against unmodified production before the
fixes that close them, and both terminating at a FINAL material object rather
than at a helper (GRAPH-GATES.md, G30 field 12).

1. RAW ``Clause.text_content`` AS PROMPT CONTENT.
   ``RetrievalService._graph_rows_to_search_results`` hydrates each
   graph-expanded clause from ``document_vectors`` and then falls back::

       "text": text or row.get("text") or ""

   ``row["text"]`` is ``related.text_content`` read straight off the FalkorDB
   ``(:Clause)`` node by ``ContractGraphService.find_related_clauses``. So
   whenever the canonical chunk store has no row for that clause number - and
   nothing anywhere deletes or reprojects a ``(:Clause)`` node, so a superseded
   generation survives every correction (T11; GRAPH-GATES U7 records the state
   half as CONFIRMED OPEN) - the graph's own copy of the clause became the
   ``SearchResult.snippet`` and the ``full_clause_text`` in the contract-QA
   prompt.

   The provenance check above it is not the same assertion. It answers "does a
   canonical document exist?", which is why an unresolvable id is already
   dropped (``test_contract_graph_retrieval.py::
   test_graph_expansion_denies_clause_whose_canonical_document_is_absent``). It
   does not answer "is this TEXT the canonical text?", and a document can be
   perfectly resolvable and consumable while the words on the node are a
   revision nobody approved.

   The same seam carries ``section_type`` into ``section_heading``.
   ``contract_graph_containment.NON_AUTHORITATIVE_GRAPH_FIELDS`` exists
   precisely to strip ``section_type`` and ``priority`` before a consumer can
   order by them - but this reader calls ``find_related_clauses`` DIRECTLY and
   never passes through that seam, so the strip does not happen. GRAPH-GATES U8
   records "no live read path was found ... the evidence seam strips them".
   This is the live read path.

2. CAPACITY, AT THE OTHER CONTRACT CONSUMER.
   ``ContractService._assemble_results`` fuses lexical, vector and graph
   candidates, and ``blocked_document_ids`` removes the positively-blocked ones
   before fusion. It deliberately does NOT remove ids absent from Mongo - the
   right contract for a lexical or vector hit, which can only exist because a
   store row exists. A GRAPH candidate is different: the node outlives its
   document. An unresolvable one therefore survives into ``key_meta``, joins the
   RRF ranking, is counted in ``total_count``, sets ``has_more``, and consumes a
   page slot - after which hydration finds nothing and the row simply is not
   there. Influence without disclosure, which is the class G-A17 pinned for the
   linked-chain report and this consumer never had.

Both are asserted on the final object - ``SearchResult`` and the assembled
contract rows - rather than on the reader row.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.services.contract_service import ContractService

#: Written onto the graph node only. Its appearance anywhere downstream means a
#: node value became content.
GRAPH_TEXT_MARKER = "RAW_GRAPH_CLAUSE_TEXT_G_A19_31337"
GRAPH_SECTION_MARKER = "RAW_GRAPH_SECTION_TYPE_G_A19_31337"
CANONICAL_TEXT_MARKER = "CANONICAL_MONGO_CLAUSE_TEXT_G_A19"


# ---------------------------------------------------------------------------
# Mongo boundary. Queries are APPLIED, not ignored: a collection that answered
# everything would make every assertion below vacuous.
# ---------------------------------------------------------------------------


def _matches(row: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$or":
            if not any(_matches(row, branch) for branch in expected):
                return False
            continue
        if isinstance(expected, dict) and "$in" in expected:
            if not any(str(row.get(key)) == str(item) for item in expected["$in"]):
                return False
            continue
        if isinstance(expected, dict):
            continue
        if str(row.get(key)) != str(expected):
            return False
    return True


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [dict(row) for row in rows]

    def sort(self, *_a: Any, **_k: Any) -> "_Cursor":
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return [dict(row) for row in (self.rows if length is None else self.rows[:length])]

    def __aiter__(self):
        async def _generate():
            for row in self.rows:
                yield dict(row)

        return _generate()


class _Collection:
    def __init__(self, rows: Iterable[Dict[str, Any]] = ()) -> None:
        self.rows = [dict(row) for row in rows]

    def find(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any) -> _Cursor:
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any):
        for row in self.rows:
            if _matches(row, query):
                return dict(row)
        return None

    async def count_documents(self, query: Optional[Dict[str, Any]] = None) -> int:
        return len([row for row in self.rows if _matches(row, query)])

    def aggregate(self, _pipeline: Any) -> _Cursor:
        return _Cursor([])


class _DB:
    def __init__(self, **collections: Iterable[Dict[str, Any]]) -> None:
        self._collections = {name: _Collection(rows) for name, rows in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _document(doc_id: str, *, blocked: bool = False) -> Dict[str, Any]:
    return {
        "_id": doc_id,
        "organization_id": "org-A",
        "project_id": "proj-A",
        "uploadType": "contract",
        "processing_status": "human_review_required" if blocked else "metadata_extracted",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }


def _graph_row(
    doc_id: str,
    clause_number: str,
    *,
    text: str = GRAPH_TEXT_MARKER,
    relation: str = "same_clause_number",
) -> Dict[str, Any]:
    """Exactly the shape ``ContractGraphService.find_related_clauses`` returns."""
    return {
        "document_id": doc_id,
        "clause_id": f"{doc_id}-{clause_number}",
        "clause_number": clause_number,
        "clause_title": "Extension of Time",
        "text": text,
        "page_number": 4,
        "section_type": GRAPH_SECTION_MARKER,
        "priority": 9,
        "clause_node_id": f"{doc_id}:{clause_number}",
        "graph_relation": relation,
    }


def _retrieval(db: Any) -> RetrievalService:
    return RetrievalService(
        db=db,
        embedding_client=SimpleNamespace(),
        vector_client=SimpleNamespace(),
        llm_generator=SimpleNamespace(),
        observability=SimpleNamespace(),
    )


def _rows_to_results(db: Any, rows: List[Dict[str, Any]]):
    service = _retrieval(db)
    return asyncio.run(service._graph_rows_to_search_results(rows, base_score=0.62))


def _blob(results) -> str:
    return repr([(result.snippet, result.payload) for result in results])


# ===========================================================================
# 1 - raw graph clause content may never become material text
# ===========================================================================


def test_raw_graph_clause_text_never_becomes_a_search_result_snippet() -> None:
    """Resolvable, consumable document; NO canonical chunk for that clause.

    The provenance gate passes - there IS a canonical document - and the
    authority gate passes - it is consumable. Neither says the words on the
    node are the words in the contract. Nothing deletes or reprojects a
    ``(:Clause)`` node, so those words may be any superseded generation.
    """
    db = _DB(documents=[_document("doc-1")], document_vectors=[])

    results = _rows_to_results(db, [_graph_row("doc-1", "8.4")])

    assert GRAPH_TEXT_MARKER not in _blob(results), (
        "the graph node's own `text_content` reached a final SearchResult - a "
        "node value became the contract-QA prompt's clause text"
    )


def test_a_graph_expanded_clause_with_no_canonical_text_is_not_served() -> None:
    """Fail closed, not fail quiet.

    Emitting the row with an empty body would be worse than dropping it: an
    empty ``full_clause_text`` under a real clause number and title reads to a
    consumer as "this clause says nothing".
    """
    db = _DB(documents=[_document("doc-1")], document_vectors=[])

    results = _rows_to_results(db, [_graph_row("doc-1", "8.4")])

    assert results == [], (
        "a graph-expanded clause the canonical store cannot substantiate was "
        "served anyway"
    )


def test_raw_graph_section_type_never_becomes_a_section_heading() -> None:
    """``NON_AUTHORITATIVE_GRAPH_FIELDS`` is stripped at the evidence seam, and
    this reader does not go through it.

    ``section_type`` is filename-derived and is not reprojected when a
    document's classification is corrected, so a consumer that displays or
    orders by it has made the graph authoritative by accident.
    """
    db = _DB(
        documents=[_document("doc-1")],
        document_vectors=[
            {
                "document_id": "doc-1",
                "clause_number": "8.4",
                "chunk_index": 0,
                "chunk_id": "chunk-1",
                "uploadType": "contract",
                "text": CANONICAL_TEXT_MARKER,
            }
        ],
    )

    results = _rows_to_results(db, [_graph_row("doc-1", "8.4")])

    assert results, "the positive path must still serve, or this proves nothing"
    assert GRAPH_SECTION_MARKER not in _blob(results), (
        "a filename-derived, never-reprojected graph field reached a final "
        "SearchResult as `section_heading`"
    )


def test_a_canonically_substantiated_graph_clause_is_still_served() -> None:
    """Positive control. Containment must not collapse into serving nothing."""
    db = _DB(
        documents=[_document("doc-1")],
        document_vectors=[
            {
                "document_id": "doc-1",
                "clause_number": "8.4",
                "chunk_index": 0,
                "chunk_id": "chunk-1",
                "uploadType": "contract",
                "text": CANONICAL_TEXT_MARKER,
            }
        ],
    )

    results = _rows_to_results(db, [_graph_row("doc-1", "8.4")])

    assert len(results) == 1
    assert CANONICAL_TEXT_MARKER in _blob(results)
    assert results[0].payload.get("graph_expanded") is True


def test_a_blocked_graph_clause_is_still_denied_even_with_canonical_text() -> None:
    """The existing authority gate is not weakened by the content gate."""
    db = _DB(
        documents=[_document("doc-blocked", blocked=True)],
        document_vectors=[
            {
                "document_id": "doc-blocked",
                "clause_number": "8.4",
                "chunk_index": 0,
                "chunk_id": "chunk-b",
                "uploadType": "contract",
                "text": CANONICAL_TEXT_MARKER,
            }
        ],
    )

    assert _rows_to_results(db, [_graph_row("doc-blocked", "8.4")]) == []


# ===========================================================================
# 2 - an unresolvable graph candidate may not consume a contract-search slot
# ===========================================================================


def _clause_chunk(doc_id: str, clause_number: str, *, start: int) -> Dict[str, Any]:
    return {
        "document_id": doc_id,
        "upload_id": doc_id,
        "clause_number": clause_number,
        "clause_start_position": start,
        "chunk_index": 0,
        "chunk_id": f"{doc_id}-{clause_number}",
        "uploadType": "contract",
        "organization_id": "org-A",
        "project_id": "proj-A",
        "text": CANONICAL_TEXT_MARKER,
    }


def _candidate(doc_id: str, clause_number: str, *, start: int, score: float) -> Dict[str, Any]:
    return {
        "document_id": doc_id,
        "clause_number": clause_number,
        "clause_start_position": start,
        "score": score,
        "graph_relation": "same_clause_number",
    }


def _assemble(db: Any, graph_candidates: List[Dict[str, Any]], *, page_size: int):
    """Drive the real fusion and return ``(rows, total_count, has_more)``."""
    service = ContractService()
    # `_assemble_results` resolves its authority database from
    # `collection.database` and falls back to `self._db`. The fake collection
    # exposes neither, so the fallback is pinned explicitly - leaving it None
    # would make `blocked_document_ids` return an empty set and every assertion
    # below vacuous.
    service._db = db
    return asyncio.run(
        service._assemble_results(
            db.document_vectors,
            [],
            [],
            graph_candidates,
            page_size,
            0,
        )
    )


def test_an_unresolvable_graph_candidate_does_not_consume_a_contract_search_slot() -> None:
    """One slot, one orphan ahead of one legitimate clause.

    The orphan resolves to no Mongo document at all - pure graph residue of the
    kind nothing ever deletes. It discloses nothing, because hydration finds no
    chunk; what it must also not do is spend the page.
    """
    db = _DB(
        documents=[_document("doc-real")],
        document_vectors=[_clause_chunk("doc-real", "8.4", start=10)],
    )

    rows, total_count, _has_more = _assemble(
        db,
        [
            _candidate("doc-ghost", "8.4", start=1, score=0.75),
            _candidate("doc-real", "8.4", start=10, score=0.45),
        ],
        page_size=1,
    )

    assert [row.get("document_id") for row in rows] == ["doc-real"], (
        "an unresolvable graph candidate consumed the page window before "
        "provenance was resolved, so a legitimate clause vanished from the "
        "contract search answer"
    )
    assert total_count == 1, (
        f"total_count counted raw graph candidates rather than substantiated "
        f"ones (got {total_count})"
    )


def test_unresolvable_graph_candidates_do_not_inflate_total_count() -> None:
    """``total_count`` and ``has_more`` drive the client's pagination.

    Ten orphans and one real clause reported as eleven results is a wrong
    answer even when every page renders correctly.
    """
    db = _DB(
        documents=[_document("doc-real")],
        document_vectors=[_clause_chunk("doc-real", "8.4", start=10)],
    )
    ghosts = [
        _candidate(f"doc-ghost-{index}", "8.4", start=index, score=0.75)
        for index in range(10)
    ]

    rows, total_count, has_more = _assemble(
        db, ghosts + [_candidate("doc-real", "8.4", start=10, score=0.45)], page_size=5
    )

    assert total_count == 1
    assert has_more is False
    assert [row.get("document_id") for row in rows] == ["doc-real"]


def test_a_resolvable_graph_candidate_still_reaches_the_contract_search_answer() -> None:
    """Positive control for the capacity fence."""
    db = _DB(
        documents=[_document("doc-real")],
        document_vectors=[_clause_chunk("doc-real", "8.4", start=10)],
    )

    rows, total_count, _has_more = _assemble(
        db, [_candidate("doc-real", "8.4", start=10, score=0.45)], page_size=5
    )

    assert total_count == 1
    assert [row.get("document_id") for row in rows] == ["doc-real"]


def test_a_blocked_graph_candidate_is_still_removed_before_fusion() -> None:
    """The pre-existing pre-fusion block is not disturbed by the new fence."""
    db = _DB(
        documents=[_document("doc-blocked", blocked=True), _document("doc-real")],
        document_vectors=[
            _clause_chunk("doc-blocked", "8.4", start=1),
            _clause_chunk("doc-real", "8.4", start=10),
        ],
    )

    rows, total_count, _has_more = _assemble(
        db,
        [
            _candidate("doc-blocked", "8.4", start=1, score=0.75),
            _candidate("doc-real", "8.4", start=10, score=0.45),
        ],
        page_size=1,
    )

    assert [row.get("document_id") for row in rows] == ["doc-real"]
    assert total_count == 1


def test_the_contract_graph_candidate_projection_carries_no_node_content() -> None:
    """The seam upstream of the fusion, pinned so it cannot regain content.

    `_graph_candidates` projects the reader's rows down to identity, position
    and relevance - `document_id`, `clause_number`, `clause_start_position`,
    `score`, `graph_relation` - and the clause body is then hydrated from Mongo
    by `_assemble_results`. That is the correct shape and it is currently
    correct by omission: `find_related_clauses` returns `text`, `clause_title`,
    `section_type` and `priority`, and this projection simply does not copy
    them. Omission is a decision nobody wrote down, and the sibling reader in
    `RetrievalService` is where the same omission was not made (above). Written
    down here.
    """
    service = ContractService()
    # The candidate set is now fenced by canonical eligibility BEFORE the
    # engine's clause window is chosen (G29: dead `(:Clause)` residue was
    # spending that window and starving a live clause). So this leg needs a
    # canonical document to be eligible at all - completing the fixture, not
    # weakening the assertion: every projection assertion below is unchanged.
    service._db = _DB(documents=[_document("doc-1")])

    class _PoisonedGraph:
        def candidate_document_ids(self, **_kwargs: Any) -> List[str]:
            return ["doc-1"]

        def find_related_clauses(self, **_kwargs: Any) -> List[Dict[str, Any]]:
            return [_graph_row("doc-1", "8.4")]

    import rbac_backend.services.contract_service as contract_service_module

    original = contract_service_module.ContractGraphService
    contract_service_module.ContractGraphService = lambda *_a, **_k: _PoisonedGraph()
    try:
        candidates = asyncio.run(
            service._graph_candidates(
                SimpleNamespace(clause_number="8.4", query="clause 8.4", document_id=None),
                "org-A",
                "proj-A",
                10,
            )
        )
    finally:
        contract_service_module.ContractGraphService = original

    assert candidates, "the graph leg must genuinely run, or this proves nothing"
    blob = repr(candidates)
    assert GRAPH_TEXT_MARKER not in blob
    assert GRAPH_SECTION_MARKER not in blob
    assert set(candidates[0]) == {
        "document_id",
        "clause_number",
        "clause_start_position",
        "score",
        "graph_relation",
    }, (
        "the contract graph candidate projection grew a field beyond identity, "
        "position and relevance"
    )


# ===========================================================================
# 3 - a graph node property may not become tenant attribution
# ===========================================================================
#
# `routers/storage_sync._fetch_falkor_stats` grouped `(:Letter)` nodes by
# `n.organization_id` / `n.project_id`, resolved those ids to organisation and
# project NAMES out of Mongo, and rendered a per-tenant table on the health
# page. Both properties were removed from the shared node by G32, so a current
# node reads NULL and a legacy node carries whichever tenant wrote it last.
#
# The static walk in `test_graph_letter_reader_properties.py` did not catch it
# for a reason worth pinning separately: its variable allowlist is
# `node`/`l`/`src`/`dst`/`root`/`letter`, and this query binds `n`. That hole is
# closed there; this asserts the resulting PAYLOAD, because a guard on the
# source and a guard on the answer fail in different ways.


def test_falkor_health_stats_report_no_invented_tenant_attribution() -> None:
    """The final object: what the health endpoint actually returns."""
    from rbac_backend.routers import storage_sync

    class _Svc:
        enabled = True

        def _execute(self, cypher: str, *_a: Any, **_k: Any) -> Any:
            # A contaminated LEGACY graph: every node still carries the values
            # an older writer left. If anything reads them, org-B's marker
            # reaches a payload that org-B has nothing to do with.
            assert "organization_id" not in cypher and "project_id" not in cypher, (
                f"health stats read a removed Letter property: {cypher!r}"
            )
            return [[], [{"nodes": 215, "edges": 310}], []]

        def _parse_rows(self, result: Any) -> List[Dict[str, Any]]:
            return list(result[1]) if result and len(result) > 1 else []

    original = storage_sync.FalkorGraphService
    storage_sync.FalkorGraphService = lambda *_a, **_k: _Svc()
    try:
        stats = asyncio.run(storage_sync._fetch_falkor_stats(db=None))
    finally:
        storage_sync.FalkorGraphService = original

    assert stats["available"] is True
    assert stats["nodes"] == 215, "the honest half must still be reported"
    assert stats["per_org"] == [], (
        "a per-tenant breakdown was built from properties the shared Letter "
        "node does not carry"
    )
    assert stats["per_org_available"] is False, (
        "an unavailable breakdown must say so - an empty list alone reads as "
        "'no graph', which is a different and equally wrong answer"
    )
    assert "G32" in stats["per_org_reason"]
