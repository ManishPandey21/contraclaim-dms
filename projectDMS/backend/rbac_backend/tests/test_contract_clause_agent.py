"""Phase 2 tests for the Contract Clause Chunking Agent orchestrator:
long-clause splitting into records, section chunks, table detection + linking,
audit-run summary, idempotent processing and scope/permission enforcement.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from rbac_backend.core.permissions import Permissions
from rbac_backend.services.contract_clause import (
    ClauseChunkingAgent,
    ClauseScopeError,
    DetectedClause,
    DetectedTable,
    DocumentScope,
)


# --------------------------------------------------------------------------- #
# In-memory async Mongo doubles
# --------------------------------------------------------------------------- #
class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict = {}
        self.inserted: list = []

    async def create_index(self, keys, **kwargs):
        return None

    async def find_one(self, filt):
        uid = filt.get("clause_uid")
        doc = self.docs.get(uid)
        return dict(doc) if doc is not None else None

    async def update_one(self, filt, update, upsert=False):
        uid = filt["clause_uid"]
        set_fields = update.get("$set", {})
        set_on_insert = update.get("$setOnInsert", {})
        if uid in self.docs:
            self.docs[uid].update(set_fields)
        elif upsert:
            doc = {"clause_uid": uid}
            doc.update(set_on_insert)
            doc.update(set_fields)
            self.docs[uid] = doc

        class _R:
            upserted_id = None

        return _R()

    async def insert_one(self, doc):
        self.inserted.append(doc)

        class _R:
            inserted_id = "run-1"

        return _R()


class FakeDB:
    def __init__(self) -> None:
        self.collections: dict = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class AllowPolicy:
    def __init__(self) -> None:
        self.calls: list = []

    async def authorize(self, current_user, permission, **kwargs):
        self.calls.append({"permission": permission, **kwargs})


class DenyPolicy:
    async def authorize(self, current_user, permission, **kwargs):
        raise HTTPException(status_code=403, detail="denied")


SCOPE = DocumentScope(
    org_id="org-A", project_id="proj-A", contract_id="ct-1", document_id="doc-1",
    document_title="Vol-2 GCC", document_type="GCC", volume="Vol-2", revision="R0",
)


def _agent(db=None, policy=None, max_chars=4000):
    return ClauseChunkingAgent(db or FakeDB(), policy_service=policy, max_clause_chars=max_chars)


# --------------------------------------------------------------------------- #
# Long-clause splitting into records (req 14)
# --------------------------------------------------------------------------- #
def test_long_clause_splits_into_parts_with_shared_clause_no():
    agent = _agent(max_chars=50)
    long_text = "\n\n".join(f"Paragraph {i} " + "x" * 40 for i in range(4))
    clauses = [DetectedClause(clause_no="8.4", clause_title="EOT", text=long_text)]
    records, summary = agent.build_records(SCOPE, clauses)

    assert summary.clauses_detected == 1
    assert len(records) > 1  # split into multiple parts
    assert {r.clause_no for r in records} == {"8.4"}
    assert all(r.chunk_type == "clause_part" for r in records)
    assert {r.chunk_part for r in records} == set(range(1, len(records) + 1))
    assert all(r.chunk_total == len(records) for r in records)
    assert len({r.clause_uid for r in records}) == len(records)  # distinct ids


def test_short_clause_is_single_record():
    agent = _agent()
    records, summary = agent.build_records(
        SCOPE, [DetectedClause(clause_no="8.4", clause_title="EOT", text="short")]
    )
    assert len(records) == 1
    assert records[0].chunk_type == "clause"
    assert records[0].chunk_total == 1


def test_section_without_clause_number_is_section_chunk():
    agent = _agent()
    records, summary = agent.build_records(
        SCOPE, [DetectedClause(clause_no=None, clause_title="Preamble", text="intro text")]
    )
    assert summary.section_chunks == 1
    assert summary.clauses_detected == 0
    assert records[0].chunk_type == "section_chunk"


def test_duplicate_clause_numbers_counted():
    agent = _agent()
    _, summary = agent.build_records(
        SCOPE,
        [
            DetectedClause(clause_no="8.4", text="a"),
            DetectedClause(clause_no="8.4", text="b"),
            DetectedClause(clause_no="9.1", text="c"),
        ],
    )
    assert summary.duplicates_detected == 1


# --------------------------------------------------------------------------- #
# Table detection + linking (req 15)
# --------------------------------------------------------------------------- #
def test_detect_tables_from_captions():
    pages = [
        {"page_no": 45, "cleaned_text": "Some text\nTable 3: Key Dates\ncol a col b\n1 2"},
        {"page_no": 46, "cleaned_text": "No table here"},
    ]
    tables = ClauseChunkingAgent.detect_tables(pages)
    assert len(tables) == 1
    assert tables[0].page_no == 45
    assert "Key Dates" in (tables[0].table_title or "")


def test_table_links_to_nearest_preceding_clause():
    clauses = [
        DetectedClause(clause_no="8.4", page_start=44),
        DetectedClause(clause_no="9.1", page_start=48),
    ]
    table = DetectedTable(page_no=45, table_title="Table 3: Key Dates")
    assert ClauseChunkingAgent.link_table_to_clause(table, clauses) == "8.4"


def test_table_record_is_linked_and_typed():
    agent = _agent()
    clauses = [DetectedClause(clause_no="8.4", clause_title="EOT", text="x", page_start=44)]
    tables = [DetectedTable(page_no=45, table_title="Table 3: Key Dates", text="a b\n1 2")]
    records, summary = agent.build_records(SCOPE, clauses, tables)

    table_records = [r for r in records if r.chunk_type == "table"]
    assert summary.tables_detected == 1
    assert len(table_records) == 1
    assert table_records[0].linked_clause_no == "8.4"
    assert table_records[0].page_start == 45


# --------------------------------------------------------------------------- #
# Orchestration: authorize, save, audit (req 21, 23)
# --------------------------------------------------------------------------- #
class _User:
    id = "user-1"


@pytest.mark.asyncio
async def test_process_saves_records_and_writes_audit_run():
    db = FakeDB()
    agent = _agent(db, policy=AllowPolicy())
    clauses = [
        DetectedClause(clause_no="8.4", clause_title="EOT", text="clause body"),
        DetectedClause(clause_no=None, clause_title="Preamble", text="intro"),
    ]
    tables = [DetectedTable(page_no=10, table_title="Table 1")]

    summary = await agent.process(_User(), SCOPE, clauses, tables, total_pages=12)

    assert summary.clauses_detected == 1
    assert summary.section_chunks == 1
    assert summary.tables_detected == 1
    assert summary.records_written["inserted"] == 3
    # Audit run persisted with the counts.
    runs = db[ClauseChunkingAgent.RUN_COLLECTION].inserted
    assert len(runs) == 1
    assert runs[0]["document_id"] == "doc-1"
    assert runs[0]["clauses_detected"] == 1
    assert runs[0]["tables_detected"] == 1
    assert runs[0]["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_reprocessing_is_idempotent():
    db = FakeDB()
    agent = _agent(db, policy=AllowPolicy())
    clauses = [DetectedClause(clause_no="8.4", clause_title="EOT", text="clause body")]

    first = await agent.process(_User(), SCOPE, clauses, [], total_pages=1)
    second = await agent.process(_User(), SCOPE, clauses, [], total_pages=1)

    assert first.records_written["inserted"] == 1
    assert second.records_written.get("inserted", 0) == 0
    assert second.records_written["unchanged"] == 1
    # One clause record total (no duplicate), two audit runs.
    assert len(db[agent.storage.COLLECTION].docs) == 1
    assert len(db[ClauseChunkingAgent.RUN_COLLECTION].inserted) == 2


@pytest.mark.asyncio
async def test_process_denied_without_permission():
    agent = _agent(FakeDB(), policy=DenyPolicy())
    with pytest.raises(HTTPException):
        await agent.process(_User(), SCOPE, [DetectedClause(clause_no="1", text="x")], [])


@pytest.mark.asyncio
async def test_process_blocks_missing_scope():
    agent = _agent(FakeDB(), policy=AllowPolicy())
    bad_scope = DocumentScope(org_id="org-A", project_id="proj-A", contract_id="", document_id="doc-1")
    with pytest.raises(ClauseScopeError):
        await agent.process(_User(), bad_scope, [DetectedClause(clause_no="1", text="x")], [])


# --------------------------------------------------------------------------- #
# Phase 3 wiring: modification detection + embedding + graph
# --------------------------------------------------------------------------- #
class FakeEmbeddingService:
    def __init__(self):
        self.records = None

    async def index_clauses(self, records):
        self.records = records
        return {"embedded": len(records), "skipped": 0}


class FakeGraphService:
    def __init__(self):
        self.calls = []

    async def sync(self, records, modification_links=None):
        self.calls.append((records, modification_links or []))
        return len(records)


SCC_SCOPE = DocumentScope(
    org_id="org-A", project_id="proj-A", contract_id="ct-1", document_id="doc-scc",
    document_type="SCC",
)


@pytest.mark.asyncio
async def test_process_wires_embedding_and_graph():
    db = FakeDB()
    embed, graph = FakeEmbeddingService(), FakeGraphService()
    agent = ClauseChunkingAgent(db, policy_service=AllowPolicy(), embedding_service=embed, graph_service=graph)
    clauses = [DetectedClause(clause_no="8.4", clause_title="EOT", text="clause body")]

    await agent.process(_User(), SCOPE, clauses, [])

    assert embed.records is not None and len(embed.records) == 1  # embedding invoked
    assert len(graph.calls) == 1                                   # graph invoked
    assert graph.calls[0][0] == embed.records                      # same records


@pytest.mark.asyncio
async def test_process_detects_scc_modification():
    db = FakeDB()
    graph = FakeGraphService()
    agent = ClauseChunkingAgent(db, policy_service=AllowPolicy(), graph_service=graph)
    clauses = [
        DetectedClause(
            clause_no="8.4", clause_title="EOT",
            text="Sub-Clause 8.4 is deleted and replaced by the following.",
        )
    ]
    summary = await agent.process(_User(), SCC_SCOPE, clauses, [])

    assert summary.modifications_detected == 1
    assert summary.human_review_required is True
    # The modification link is passed through to the graph sync.
    _, links = graph.calls[0]
    assert links and links[0]["base_clause_no"] == "8.4"
    assert links[0]["modification_type"] == "replace"


@pytest.mark.asyncio
async def test_gcc_clause_not_treated_as_modifier():
    agent = ClauseChunkingAgent(FakeDB(), policy_service=AllowPolicy())
    clauses = [DetectedClause(clause_no="8.4", text="Clause 8.4 is amended by agreement.")]
    # document_type GCC -> its own clauses are the base, not modifiers.
    summary = await agent.process(_User(), SCOPE, clauses, [])  # SCOPE is GCC
    assert summary.modifications_detected == 0


@pytest.mark.asyncio
async def test_embedding_failure_is_non_fatal():
    class Boom:
        async def index_clauses(self, records):
            raise RuntimeError("qdrant down")

    db = FakeDB()
    agent = ClauseChunkingAgent(db, policy_service=AllowPolicy(), embedding_service=Boom())
    summary = await agent.process(_User(), SCOPE, [DetectedClause(clause_no="8.4", text="x")], [])

    # Clauses still saved and audit written despite the embedding failure.
    assert summary.records_written["inserted"] == 1
    assert any("embedding_failed" in e for e in summary.errors)
    assert len(db[ClauseChunkingAgent.RUN_COLLECTION].inserted) == 1
