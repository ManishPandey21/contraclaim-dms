"""G29 / R12-state: physical `(:Clause)` residue must have ZERO material influence.

R12's two halves have had very different fates. The **consumer** half is closed
and pinned under G30: `retrieval/service.py::_graph_rows_to_search_results`
requires canonical Mongo text and a resolvable canonical document, and
`ContractService._assemble_results` fences unresolvable graph candidates off the
candidate set. Neither of those lets a stale clause DISCLOSE anything.

The **state** half was carried forward through G-A18, G-A19 and G-A20 as
"physical residency, state hygiene, G29 accounting", on the premise that every
production consumer is canonically fenced and therefore the residue is inert.
This file exists because that premise was never tested against the engine, and
G29's assertion is not "nothing leaks" - it is ZERO MATERIAL INFLUENCE, which
GRAPH-GATES lists as including *source suppression* and *evidence availability*,
not only disclosure.

WHAT THE ENGINE ACTUALLY DOES.
`ContractGraphService.find_related_clauses` bounds BOTH of its statements with
`LIMIT $limit`, and its two generic callers resolved authority only afterwards.
A `(:Clause)` node is never deleted, never reprojected and never deactivated by
any code path in this repository (`contract_graph_service.py` writes `is_active`
only through `COALESCE` on upsert, and nothing anywhere issues a Clause
`DELETE`). So the residue of a hard-deleted or blocked contract keeps matching
the traversal, keeps `is_active = true`, and consumes the candidate window ahead
of a clause a live contract legitimately supplies. The later fences then remove
the residue from the ANSWER - and the live clause is not in the window to take
its place.

That is exactly the capacity class G-A17 pinned for the linked-chain report and
G-A19 pinned for the contract-search candidate set, at the one seam that never
received it. The contract EVIDENCE path is immune because
`contract_graph_containment` carries a canonical eligible set INTO the Cypher,
before its `LIMIT` - which is the shape this file requires of the other two.

REAL FALKOR IS REQUIRED. The defect is engine candidate-window semantics; a fake
that returns a Python list cannot reproduce it.

Run with the authorised relay:

    G29_UMBRELLA_CERTIFICATION=1 FALKOR_TEST_HOST=127.0.0.1 FALKOR_TEST_PORT=6390 \
      pytest backend/rbac_backend/tests/integration/test_g29_clause_state_material_influence_falkor.py
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest

from rbac_backend.retrieval.models import ContractQARequest, SearchFilters, SearchResult
from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.tests.authority_band_graph import (
    disposable_graph_name,
    drop_disposable_graph,
    falkor_host,
    falkor_port,
)

pytestmark = pytest.mark.integration

ORG = "org-A"
PROJECT = "proj-A"
CLAUSE_NUMBER = "8.4"

LIVE_DOC = "doc-live-contract"
SEED_DOC = "doc-seed-contract"

#: On the stale nodes only. If it ever appears in a final object the influence
#: is disclosure; if the LIVE clause is missing the influence is suppression.
STALE_MARKER = "STALE_CLAUSE_RESIDUE_MARKER_G29"
LIVE_MARKER = "LIVE_CANONICAL_CLAUSE_TEXT_G29"

#: More than any window the callers ask for, and far fewer than a real corpus.
STALE_CLAUSE_COUNT = 12


# ---------------------------------------------------------------------------
# Canonical Mongo boundary - the queries under test are APPLIED, not stubbed.
# ---------------------------------------------------------------------------


def _same(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _matches(row: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(row, branch) for branch in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(row, branch) for branch in expected):
                return False
            continue
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                if not any(_same(actual, candidate) for candidate in expected["$in"]):
                    return False
                continue
            if "$nin" in expected:
                if any(_same(actual, candidate) for candidate in expected["$nin"]):
                    return False
                continue
            if "$exists" in expected and (key in row) is not bool(expected["$exists"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, *_a: Any, **_k: Any) -> "_Cursor":
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return deepcopy(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        async def _generate():
            for row in self.rows:
                yield deepcopy(row)

        return _generate()


class _Collection:
    def __init__(self, rows: Iterable[Dict[str, Any]] = ()) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def find(
        self,
        query: Optional[Dict[str, Any]] = None,
        _projection: Optional[Dict[str, Any]] = None,
        *_a: Any,
        **_k: Any,
    ) -> _Cursor:
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    async def count_documents(self, query: Optional[Dict[str, Any]] = None) -> int:
        return len([row for row in self.rows if _matches(row, query)])


class _Database:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {name: _Collection(rows) for name, rows in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _contract_document(document_id: str, *, blocked: bool = False) -> Dict[str, Any]:
    return {
        "_id": document_id,
        "organization_id": ORG,
        "project_id": PROJECT,
        "uploadType": "contract",
        "document_type": "contract",
        "processing_status": "human_review_required" if blocked else "metadata_extracted",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }


def _canonical_chunk(document_id: str, clause_number: str, text: str) -> Dict[str, Any]:
    return {
        "document_id": document_id,
        "clause_number": clause_number,
        "chunk_index": 0,
        "chunk_id": f"{document_id}:{clause_number}:0",
        "text": text,
        "clause_title": "Extension of time",
        "page_number": 4,
        "uploadType": "contract",
    }


# ---------------------------------------------------------------------------
# Real Falkor, run-owned namespace
# ---------------------------------------------------------------------------


def _falkor(graph_name: str):
    pytest.importorskip("redis")
    from rbac_backend.services.falkor_graph_service import (
        FalkorGraphConfig,
        FalkorGraphService,
    )

    service = FalkorGraphService(
        FalkorGraphConfig(
            host=falkor_host(),
            port=falkor_port(),
            graph_name=graph_name,
            password=None,
            enabled=True,
            cleanup=False,
        )
    )
    try:
        service._get_client().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"FalkorDB not reachable at {falkor_host()}:{falkor_port()}: {exc}")
    return service


@pytest.fixture()
def graph():
    name = disposable_graph_name("g29clause")
    service = _falkor(name)
    try:
        yield service
    finally:
        drop_disposable_graph(service._get_client(), name)


def _seed_clause(
    service: Any,
    *,
    document_id: str,
    node_id: str,
    text: str,
    clause_number: str = CLAUSE_NUMBER,
) -> None:
    service._execute(
        """
        MATCH (s:Section {section_id:'sec-1'})
        CREATE (s)-[:HAS_CLAUSE]->(:Clause {
            clause_node_id:$node_id, doc_id:$doc, clause_id:$node_id,
            clause_number:$number, title:'Extension of time',
            text_content:$text, page_number:4, is_active:true,
            organization_id:$org, project_id:$project})
        """,
        {
            "node_id": node_id,
            "doc": document_id,
            "number": clause_number,
            "text": text,
            "org": ORG,
            "project": PROJECT,
        },
    )


def _seed_residue_then_live(service: Any, *, stale_document_ids: List[str]) -> None:
    """The measured shape: residue first, one live clause behind it.

    Insertion order matters and is therefore controlled: the live clause is
    created LAST, which is the pessimistic case and the one that occurs in
    practice - residue predates the document that replaced it.
    """
    service._execute(
        "CREATE (:Section {section_id:'sec-1', doc_id:$doc, organization_id:$org, project_id:$project})",
        {"doc": SEED_DOC, "org": ORG, "project": PROJECT},
    )
    _seed_clause(service, document_id=SEED_DOC, node_id="seed-1", text="seed clause text")
    for index, document_id in enumerate(stale_document_ids):
        _seed_clause(
            service,
            document_id=document_id,
            node_id=f"stale-{index}",
            text=f"{STALE_MARKER}_{index}",
        )
    _seed_clause(service, document_id=LIVE_DOC, node_id="live-1", text="superseded generation")


def _install_graph(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    """Point BOTH unfenced consumers at the disposable graph."""
    from rbac_backend.services import contract_graph_service as graph_module
    from rbac_backend.services import contract_service as contract_module

    class _Bound(graph_module.ContractGraphService):
        def __init__(self, falkor: Any = None) -> None:
            super().__init__(falkor=service)

    monkeypatch.setattr(graph_module, "ContractGraphService", _Bound)
    monkeypatch.setattr(contract_module, "ContractGraphService", _Bound)


# ---------------------------------------------------------------------------
# Consumer 1 - contract QA. Final material object: the SearchResult list that
# becomes the prompt, the answer and the citations.
# ---------------------------------------------------------------------------


def _qa_request(limit: int = 3) -> ContractQARequest:
    return ContractQARequest(
        query=f"what does clause {CLAUSE_NUMBER} say",
        limit=limit,
        filters=SearchFilters(org_id=ORG, project_id=PROJECT),
    )


def _seed_result() -> SearchResult:
    return SearchResult(
        document_id=SEED_DOC,
        chunk_id=f"{SEED_DOC}:seed",
        score=0.9,
        snippet="seed clause text",
        payload={
            "document_id": SEED_DOC,
            "uploadType": "contract",
            "clause_number": CLAUSE_NUMBER,
            "text": "seed clause text",
        },
    )


def _augment(db: Any, limit: int = 3) -> List[SearchResult]:
    service = RetrievalService(
        db=db,
        embedding_client=SimpleNamespace(),
        vector_client=SimpleNamespace(),
        llm_generator=SimpleNamespace(),
        observability=SimpleNamespace(),
    )
    return asyncio.run(
        service._augment_with_contract_graph_results([_seed_result()], _qa_request(limit), limit)
    )


def _qa_database(*, stale_document_ids: List[str], blocked: bool = False) -> _Database:
    """Live contract exists and is consumable; the residue's documents do not.

    `blocked=True` swaps "absent from Mongo" for "present and blocked", so the
    same capacity question is asked of a contaminant that RESOLVES.
    """
    documents = [_contract_document(SEED_DOC), _contract_document(LIVE_DOC)]
    if blocked:
        documents += [_contract_document(doc_id, blocked=True) for doc_id in stale_document_ids]
    return _Database(
        documents=documents,
        document_vectors=[
            _canonical_chunk(SEED_DOC, CLAUSE_NUMBER, "seed clause text"),
            _canonical_chunk(LIVE_DOC, CLAUSE_NUMBER, LIVE_MARKER),
        ],
    )


def test_unresolvable_clause_residue_cannot_starve_the_live_clause_from_contract_qa(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suppression half of G29, at the contract-QA prompt.

    Every stale node is dropped by the existing fences, so nothing leaks. The
    question is whether the live clause ARRIVES - and before the fence moved
    into the query it did not, because twelve dead clauses filled the window.
    """
    stale = [f"doc-deleted-{index}" for index in range(STALE_CLAUSE_COUNT)]
    _seed_residue_then_live(graph, stale_document_ids=stale)
    _install_graph(monkeypatch, graph)

    results = _augment(_qa_database(stale_document_ids=stale))

    blob = repr([(item.snippet, item.payload) for item in results])
    assert STALE_MARKER not in blob, "graph residue text reached the contract-QA prompt"
    assert LIVE_DOC in {item.document_id for item in results}, (
        "the live contract's clause never reached the prompt: unresolvable "
        "graph residue consumed the candidate window before authority was "
        "resolved - suppression, which G29 counts as material influence"
    )
    assert LIVE_MARKER in blob, "the surviving row must carry CANONICAL text"


def test_blocked_clause_residue_cannot_starve_the_live_clause_from_contract_qa(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same question for a contaminant that resolves but may not be published."""
    stale = [f"doc-blocked-{index}" for index in range(STALE_CLAUSE_COUNT)]
    _seed_residue_then_live(graph, stale_document_ids=stale)
    _install_graph(monkeypatch, graph)

    results = _augment(_qa_database(stale_document_ids=stale, blocked=True))

    assert LIVE_DOC in {item.document_id for item in results}, (
        "a blocked contract's clause residue evicted a publishable clause from "
        "the contract-QA window"
    )


def test_a_clean_clause_neighbourhood_still_expands_the_contract_qa_answer(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control. A system that returns nothing is not a secure system."""
    _seed_residue_then_live(graph, stale_document_ids=[])
    _install_graph(monkeypatch, graph)

    results = _augment(_qa_database(stale_document_ids=[]))

    assert LIVE_DOC in {item.document_id for item in results}
    assert LIVE_MARKER in repr([item.payload for item in results])


# ---------------------------------------------------------------------------
# Consumer 2 - generic contract search. Final material object: the candidate
# set that becomes rows, RRF ordering, `total_count` and `has_more`.
# ---------------------------------------------------------------------------


def _graph_candidate_ids(db: Any, *, candidate_limit: int) -> List[str]:
    from rbac_backend.models.contract_models import ContractSearchRequest
    from rbac_backend.services.contract_service import ContractService

    service = ContractService()
    service._db = db
    request = ContractSearchRequest(query=f"clause {CLAUSE_NUMBER} extension of time")
    rows = asyncio.run(service._graph_candidates(request, ORG, PROJECT, candidate_limit))
    return [str(row.get("document_id")) for row in rows]


def test_unresolvable_clause_residue_cannot_starve_the_live_clause_from_contract_search(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_assemble_results` fences an unresolvable candidate OUT of the answer.

    It cannot put back a live candidate that never entered the window, and a
    graph leg reduced to residue contributes nothing to RRF while a legitimate
    clause is missing from the page.
    """
    stale = [f"doc-deleted-{index}" for index in range(STALE_CLAUSE_COUNT)]
    _seed_residue_then_live(graph, stale_document_ids=stale)
    _install_graph(monkeypatch, graph)

    db = _Database(documents=[_contract_document(SEED_DOC), _contract_document(LIVE_DOC)])
    ids = _graph_candidate_ids(db, candidate_limit=3)

    assert LIVE_DOC in ids, (
        "the live contract's clause never became a graph candidate: dead "
        "(:Clause) residue consumed the candidate window"
    )


def test_a_clean_clause_neighbourhood_still_produces_graph_candidates(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control for the search leg."""
    _seed_residue_then_live(graph, stale_document_ids=[])
    _install_graph(monkeypatch, graph)

    db = _Database(documents=[_contract_document(SEED_DOC), _contract_document(LIVE_DOC)])

    assert LIVE_DOC in _graph_candidate_ids(db, candidate_limit=3)


# ---------------------------------------------------------------------------
# The already-fenced consumer, asserted rather than assumed.
# ---------------------------------------------------------------------------


def test_the_contract_evidence_seam_is_fenced_before_the_engine_limit(graph: Any) -> None:
    """T12's containment, re-proven here because G29 may not take it on trust.

    This is the shape the two consumers above must share: the canonical
    eligible set is a PREDICATE INSIDE the query, so residue never occupies the
    window in the first place.
    """
    from rbac_backend.services.contract_graph_containment import contained_related_clauses
    from rbac_backend.services.contract_graph_service import ContractGraphService

    stale = [f"doc-deleted-{index}" for index in range(STALE_CLAUSE_COUNT)]
    _seed_residue_then_live(graph, stale_document_ids=stale)

    result = contained_related_clauses(
        ContractGraphService(falkor=graph),
        organization_id=ORG,
        project_id=PROJECT,
        seed_clause_numbers=[CLAUSE_NUMBER],
        eligible_document_ids=[SEED_DOC, LIVE_DOC],
        limit=3,
    )

    admitted = [row.get("document_id") for row in result.matches]
    assert LIVE_DOC in admitted, "the eligible live clause was starved even WITH the fence"
    assert not any(str(item).startswith("doc-deleted-") for item in admitted)
    assert result.degraded is False


# ---------------------------------------------------------------------------
# The state fact itself, asserted against the engine.
# ---------------------------------------------------------------------------


def test_nothing_in_this_repository_retracts_a_clause_node(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12-state, stated as a fact about the engine rather than a docstring.

    The residue is still physically there after every consumer above has run.
    G29 permits that - presence is not the violation - but it must be RECORDED,
    because it is the reason the fences above are load-bearing rather than
    belt-and-braces.
    """
    stale = [f"doc-deleted-{index}" for index in range(3)]
    _seed_residue_then_live(graph, stale_document_ids=stale)
    _install_graph(monkeypatch, graph)

    _augment(_qa_database(stale_document_ids=stale))

    result = graph._execute(
        "MATCH (c:Clause) WHERE c.doc_id STARTS WITH 'doc-deleted-' RETURN count(c)"
    )
    rows = result[1] if len(result) > 1 else []
    assert int(rows[0][0][1]) == 3, (
        "the residue is expected to SURVIVE: G29 is satisfied by inertness, not "
        "by deletion, and no Clause retraction path exists"
    )
