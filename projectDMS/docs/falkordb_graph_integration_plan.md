# FalkorDB Letter Graph Integration Plan

This plan outlines how to persist ContraClaim letters and their inter-letter relationships in FalkorDB (openCypher/RedisGraph) so both ingestion workflows and LangGraph agents share the same source of truth.

---

## 1. Current State Recap

- **Document ingestion** already stores metadata in MongoDB and pushes nodes/edges through `GraphIngestionService` → `GraphAdapter`.
- `GraphAdapter` expects a Graphiti/REST deployment; it is currently effectively a no-op in most environments.
- LangGraph agents read conversation context from Mongo and have no direct graph read/write path.
- FalkorDB container is running (`contract-ai-falkordb-1`, `localhost:6380`), but the application code does not target it for document graphs yet.

---

## 2. Target Architecture

```
Document Upload ──▶ OCR/Metadata Pipeline ──▶ FalkorDB (Letter graph)
                             │                       ▲
                             ▼                       │
                        MongoDB (documents)          │
                             │                       │
                        LangGraph agents ────────────┘
```

- Store every uploaded documents as a `(:Letter)` node (props: `id`,`normCode`, `code`, `direction`, `subject`, `date`, `project`, `organisation', `from\_`, `to`, `filename`,`lastUpdated`, etc.).
- Persist references as `[:CITES {source, createdAt, updatedAt}]` (default) or `[:REPLIES_TO]`.
- Keep uniqueness/index constraints on `Letter.normCode`, `Letter.date`, `Letter.direction`.
- Offer Python helpers (redis-py) for upserts and queries shared by pipeline + agents.

---

## 3. Implementation Phases

### Phase 1 – Foundations

1. **Configuration**
   - Extend settings (`config.py` / env) with FalkorDB host/port/graph name.
   - Add a dedicated `FalkorGraphClient` (redis-py wrapper) that abstracts `GRAPH.QUERY`.
2. **Schema bootstrap**
   - Provide migration script (`scripts/falkordb_bootstrap.py`) that issues:
     ```cypher
     CREATE CONSTRAINT letter_normCode_unique IF NOT EXISTS
       ON (l:Letter) ASSERT l.normCode IS UNIQUE;
     CREATE INDEX letter_date IF NOT EXISTS FOR (l:Letter) ON (l.date);
     CREATE INDEX letter_direction IF NOT EXISTS FOR (l:Letter) ON (l.direction);
     ```
   - Run once on boot or during deployment.

### Phase 2 – Upsert Pipeline

1. **Normalization helpers**
   - Implement `normalize_letter_code(code: str) -> str` shared across backend.
   - Build payload mappers converting Mongo documents + parsed references to `{letter, refs}` expected by Cypher.
2. **Cypher upsert integration**
   - Add the provided idempotent upsert query (with optional APOC fallback) to `FalkorGraphClient.upsert_letter_with_refs(letter, refs)`.
   - Ensure reference cleanup (optional) by comparing current vs. new edges when `source != 'manual'`.
3. **Hook into ingestion**
   - In `GraphIngestionService._ingest_document_sync` (or a Falkor-specific subclass), call the new helper after `_upsert_reference_relationships`.
   - Map metadata references to `refs` with `type` = `CITES` unless `linkType` indicates reply.
   - Record `source` as `parser`, `manual`, or `agent`.

### Phase 3 – LangGraph Integration

1. **Read functions**
   - Implement wrappers for:
     - `get_thread(norm_code, depth=4)` (weakly connected component ordered by date).
     - `get_neighbors(norm_code)` for incoming/outgoing edges separately.
   - Return plain dicts for LangGraph prompts.
2. **Agent nodes**
   - Update LangGraph load node (`LoadThread`) to call the read helper and attach the chain context to agent state.
   - Update logging node (`LogUpdate`) to call `upsert_letter_with_refs` with `source="agent"`.

### Phase 4 – Backfill & Validation

1. **Backfill script**
   - Iterate existing Mongo documents + references and upsert into FalkorDB.
   - Schedule or run once after deployment.
2. **Validation checklist**
   - Verify schema constraints exist.
   - Spot-check a few letters to ensure `CITES` edges mirror current reference lists.
   - Confirm LangGraph agents resolve threads correctly (manual test case + unit test using Falkor test container).

### Phase 5 – Observability & Maintenance

1. **Logging & metrics**
   - Add structured logs around upsert/cleanup success & duration.
   - Capture failures (and optionally emit Prometheus metrics if available).
2. **Manual overrides**
   - Document how to insert manual edges (`source="manual"`) and protect them from cleanup.
3. **Operational runbook**
   - Checklist for FalkorDB container health, connection pooling, and fallback behavior if FalkorDB is offline.

---

## 4. Task Breakdown by Component

| Component              | Key Tasks                                                                                                        |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **Backend config**     | Add Falkor connection settings; instantiate client in a FastAPI dependency                                       |
| **Graph client**       | Implement `graph_query`, `upsert_letter_with_refs`, `get_thread`, `delete_stale_edges`                           |
| **Ingestion pipeline** | Call Falkor upserts with parsed references; tag `source` (`parser`, `manual`, `agent`)                           |
| **LangGraph**          | Load thread context from Falkor; log outgoing refs using Falkor                                                  |
| **Scripts**            | Schema bootstrap + backfill jobs                                                                                 |
| **Tests**              | Unit tests with dockerised Falkor or mocked redis-py responses; integration test for full upsert/read round-trip |

---

## 5. Deliverables

1. New module `backend/rbac_backend/services/falkor_graph_service.py` (client + helpers).
2. Updated `GraphIngestionService` (or new subclass) calling Falkor upsert.
3. LangGraph node updates to use Falkor read/write helpers.
4. Bootstrap/backfill scripts under `scripts/`.
5. Documentation covering schema, query patterns, and ops considerations.

---

## 6. Risks & Mitigations

- **APOC dependency**: If FalkorDB build lacks APOC, provide pure Cypher fallback.
- **Data duplication**: Ensure `normCode` normalization is consistent across ingestion/agents to avoid duplicate nodes.
- **Cleanup safety**: Tag manual edges and skip deletion unless explicitly requested.
- **Performance**: Batch backfill writes (e.g., 200 letters per transaction) to avoid blocking Redis main thread.
- **Error handling**: Implement retry/backoff around `GRAPH.QUERY`; surface failures to monitoring.

---

## 7. Timeline (Indicative)

| Week | Focus                                                     |
| ---- | --------------------------------------------------------- |
| 1    | Configuration, schema bootstrap, Falkor client            |
| 2    | Integrate upserts into ingestion, unit tests              |
| 3    | LangGraph read/write hooks, backfill script               |
| 4    | Backfill execution, end-to-end validation, docs & runbook |

---

With this plan, uploaded letters and LangGraph automations converge on FalkorDB as the live knowledge graph, enabling consistent reasoning and future analytics over document relationships.
