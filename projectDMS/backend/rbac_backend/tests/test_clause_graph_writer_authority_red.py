"""G31: the clause-graph writer must recheck CURRENT authority before it writes.

`ClauseChunkingAgent.process_document` resolves canonical publication authority
ONCE, at entry (`agent.py`, `is_publication_blocked(document)`), and then runs a
long pipeline before it reaches FalkorDB: OCR page load, clause detection, table
detection, scope authorisation, Mongo clause persistence, modification
detection, and a network round trip to Qdrant for clause embeddings. Only then
does `ClauseGraphService.sync` MERGE `(:Contract)`, `(:ContractDocument)` and
`(:Clause)` nodes with their `HAS_DOCUMENT` / `HAS_CLAUSE` / `HAS_SUBCLAUSE` /
`MODIFIED_BY` edges into the graph.

The route launches that pipeline as a fire-and-forget background task
(`routers/contract_clauses._run_clause_indexing`), so the window between the
entry decision and the graph write is arbitrarily long and completely
unobserved. A reviewer who marks the contract `human_review_required` - or
duplicate detection which quarantines it, or a deletion - during that window
still gets its clause graph published, because the writer is acting on an
authority verdict that has since been withdrawn.

Its two siblings already refuse to do that, and both are pinned:

* `ContractIngestor` re-asserts `_assert_current_publication_authority` after
  embedding and immediately before each store
  (`test_contract_ingestor_authority_red.py::test_ingest_file_rechecks_authority_after_embedding_before_publication`);
* the LangGraph letter pipeline re-resolves `resolve_document_authority` per
  reference immediately before every low-level Falkor write
  (`test_letterdraft_graph_writer_authority_red.py::test_letterdraft_rechecks_canonical_authority_immediately_before_falkor_write`).

`test_clause_chunking_authority_red.py` pins the *entry* gate for this writer
against a fake graph boundary. This file pins the *currency* of that gate
against a REAL FalkorDB, because GRAPH-GATES.md field 12 for G31 names
"asserting the guard was called rather than that no physical assertion exists in
the graph" as invalid closure evidence, and a fake boundary cannot carry
retry/authority-change evidence for the gate.

Run with a local FalkorDB:
    FALKOR_TEST_HOST=localhost FALKOR_TEST_PORT=6380 \
      pytest backend/rbac_backend/tests/test_clause_graph_writer_authority_red.py
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.services.contract_clause.agent import ClauseChunkingAgent
from rbac_backend.services.contract_clause.embedding_service import ClauseEmbeddingService
from rbac_backend.services.contract_clause.graph_service import ClauseGraphService
from rbac_backend.tests.authority_band_graph import (
    disposable_graph_name,
    drop_disposable_graph,
    falkor_host,
    falkor_port,
)

DOCUMENT_ID = "clause-graph-authority-doc"

#: Two numbered clauses and a sub-clause, so the writer has a Contract, a
#: ContractDocument, several Clause nodes and real edges to publish.
SOURCE_TEXT = (
    "8.4 Instructions of the Engineer\n"
    "The Contractor shall comply with the Engineer instruction without delay.\n"
    "8.4.1 Records of Instructions\n"
    "The Contractor shall keep contemporaneous records of every instruction.\n"
    "13.3 Variation Procedure\n"
    "The Engineer may initiate a variation by instruction.\n"
)


# --------------------------------------------------------------------------
# Mongo boundary - persistent, so the assertions inspect final state
# --------------------------------------------------------------------------


class _Cursor:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self._documents = documents

    def __aiter__(self):
        self._iterator = iter(self._documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Collection:
    def __init__(self) -> None:
        self.documents: Dict[str, Dict[str, Any]] = {}
        self.inserted: List[Dict[str, Any]] = []

    async def create_index(self, *_args, **_kwargs):
        return None

    def find(self, query: Dict[str, Any]):
        matches = [
            deepcopy(document)
            for document in self.documents.values()
            if all(document.get(key) == value for key, value in query.items())
        ]
        return _Cursor(matches)

    async def find_one(self, query: Dict[str, Any]):
        if "clause_uid" in query:
            document = self.documents.get(str(query["clause_uid"]))
            return deepcopy(document) if document is not None else None
        if "_id" in query:
            wanted = query["_id"]
            for document in self.documents.values():
                if document.get("_id") == wanted or str(document.get("_id")) == str(wanted):
                    return deepcopy(document)
            return None
        for document in self.documents.values():
            return deepcopy(document)
        return None

    async def update_one(self, query, update, upsert=False):
        key = str(query.get("clause_uid") or query.get("_id"))
        existing = self.documents.get(key)
        if existing is not None:
            existing.update(deepcopy(update.get("$set", {})))
        elif upsert:
            self.documents[key] = {
                **deepcopy(update.get("$setOnInsert", {})),
                **deepcopy(update.get("$set", {})),
            }

        class _Result:
            upserted_id = None

        return _Result()

    async def insert_one(self, document):
        self.inserted.append(deepcopy(document))

        class _Result:
            inserted_id = "clause-graph-run-1"

        return _Result()


class _Database:
    def __init__(self) -> None:
        self.collections: Dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


class _AllowPolicy:
    async def authorize(self, *_args, **_kwargs):
        return None


class _EmbeddingClient:
    async def embed(self, texts: List[str]):
        return [[float(len(text)), 1.0] for text in texts]


class _Qdrant:
    async def upsert(self, vectors, chunks, namespace="default"):
        return None


class _AuthorityChangingEmbeddingService:
    """The real embedding service, plus a canonical authority change.

    The change lands where a real one would: after the clause records are
    persisted and the vectors are written, and before the graph sync. That is
    the same seam `ContractIngestor` re-checks, so the mutation is not an
    artificial hook - it is the production ordering.
    """

    def __init__(
        self,
        inner: Any,
        db: "_Database",
        mutation: Optional[Dict[str, Any]],
        *,
        remove_canonical: bool = False,
    ) -> None:
        self._inner = inner
        self._db = db
        self._mutation = mutation
        self._remove_canonical = remove_canonical

    async def index_clauses(self, records):
        result = await self._inner.index_clauses(records)
        if self._remove_canonical:
            self._db["documents"].documents.pop(DOCUMENT_ID, None)
        elif self._mutation is not None:
            self._db["documents"].documents[DOCUMENT_ID].update(deepcopy(self._mutation))
        return result


# --------------------------------------------------------------------------
# Real FalkorDB
# --------------------------------------------------------------------------


def _canonical_document(**overrides: Any) -> Dict[str, Any]:
    document = {
        "_id": DOCUMENT_ID,
        "organization_id": "org-clause-graph",
        "project_id": "project-clause-graph",
        "contract_id": "contract-clause-graph",
        "filename": "clause-graph-authority-gcc.pdf",
        "contract_categories": ["GCC"],
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }
    document.update(overrides)
    return document


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
    name = disposable_graph_name("clausegraph")
    service = _falkor(name)
    yield service
    # A failed teardown is raised, not swallowed: residue on a shared instance
    # is how `g31_letterdraft_d0fdb2ac7c` came to exist.
    drop_disposable_graph(service._get_client(), name)


def _count(graph, cypher: str) -> int:
    result = graph._execute(cypher, {})
    rows = result[1] if len(result) > 1 else []
    if not rows:
        return 0
    return int(rows[0][0][1])


def _clause_nodes(graph) -> int:
    return _count(graph, "MATCH (c:Clause) RETURN count(c)")


def _clause_edges(graph) -> int:
    return _count(graph, "MATCH ()-[e:HAS_CLAUSE]->() RETURN count(e)")


async def _index(
    graph,
    *,
    canonical: Dict[str, Any],
    mutation: Optional[Dict[str, Any]] = None,
    remove_canonical: bool = False,
    db: Optional["_Database"] = None,
) -> "_Database":
    """Run the real agent end to end against the real graph."""
    db = db if db is not None else _Database()
    db["documents"].documents[DOCUMENT_ID] = deepcopy(canonical)
    db["contract_ocr_pages"].documents["page-1"] = {
        "_id": "page-1",
        "document_id": DOCUMENT_ID,
        "page_number": 1,
        "cleaned_text": SOURCE_TEXT,
        "raw_text": SOURCE_TEXT,
        "status": "text_layer",
    }

    embedding = _AuthorityChangingEmbeddingService(
        ClauseEmbeddingService(db, _EmbeddingClient(), _Qdrant()),
        db,
        mutation,
        remove_canonical=remove_canonical,
    )
    agent = ClauseChunkingAgent(
        db=db,
        policy_service=_AllowPolicy(),
        embedding_service=embedding,
        graph_service=ClauseGraphService(falkor=graph),
    )
    await agent.process_document(current_user=None, document_id=DOCUMENT_ID)
    return db


# --- positive controls: the writer is not permanently empty --------------------


@pytest.mark.asyncio
async def test_a_clean_contract_publishes_its_clause_graph(graph) -> None:
    await _index(graph, canonical=_canonical_document())

    assert _clause_nodes(graph) > 0, "a clean contract must still reach the clause graph"
    assert _clause_edges(graph) > 0


@pytest.mark.asyncio
async def test_an_operationally_failed_contract_still_publishes(graph) -> None:
    """Model B: a worker crash mid-run is operational, not an adverse verdict."""
    await _index(
        graph,
        canonical=_canonical_document(),
        mutation={"processing_status": "failed"},
    )

    assert _clause_nodes(graph) > 0
    assert _clause_edges(graph) > 0


# --- the defect: authority withdrawn between the gate and the write ------------


@pytest.mark.asyncio
async def test_authority_withdrawn_before_the_write_publishes_no_clause_graph(
    graph,
) -> None:
    await _index(
        graph,
        canonical=_canonical_document(),
        mutation={"processing_status": "human_review_required"},
    )

    assert _clause_nodes(graph) == 0, (
        "the clause-graph writer published a contract whose canonical authority "
        "was withdrawn after the entry gate: it acted on a stale verdict"
    )
    assert _clause_edges(graph) == 0


@pytest.mark.asyncio
async def test_quarantine_before_the_write_publishes_no_clause_graph(graph) -> None:
    await _index(
        graph,
        canonical=_canonical_document(),
        mutation={"duplicate_status": "duplicate"},
    )

    assert _clause_nodes(graph) == 0
    assert _clause_edges(graph) == 0


@pytest.mark.asyncio
async def test_deletion_before_the_write_publishes_no_clause_graph(graph) -> None:
    await _index(
        graph,
        canonical=_canonical_document(),
        mutation={"lifecycle_state": "deleted"},
    )

    assert _clause_nodes(graph) == 0
    assert _clause_edges(graph) == 0


@pytest.mark.asyncio
async def test_an_unresolvable_canonical_record_publishes_no_clause_graph(graph) -> None:
    """Absence of provenance is not absence of restriction.

    A contract hard-deleted while its indexing run was in flight leaves nothing
    to resolve authority from. The writer must refuse rather than fall through
    to the verdict it started with.
    """
    await _index(graph, canonical=_canonical_document(), remove_canonical=True)

    assert _clause_nodes(graph) == 0
    assert _clause_edges(graph) == 0


@pytest.mark.asyncio
async def test_a_denied_reentry_does_not_republish_a_purged_clause_graph(graph) -> None:
    """G31's core: retraction must not be undone by the next indexing run."""
    db = await _index(graph, canonical=_canonical_document())
    assert _clause_nodes(graph) > 0

    # Containment removes the projection.
    graph._execute("MATCH (n) DETACH DELETE n", {})
    assert _clause_nodes(graph) == 0

    # The contract is now blocked, and indexing runs again.
    blocked = dict(db["documents"].documents[DOCUMENT_ID])
    blocked["processing_status"] = "human_review_required"
    await _index(graph, canonical=blocked, db=db)

    assert _clause_nodes(graph) == 0, (
        "a later indexing run re-MERGEd the blocked contract's clause graph, "
        "undoing containment"
    )
