# R2R (SciPhi-AI) implementation review → What to copy into ContraClaim DMS

_Date:_ 2025-12-24  
_Target product:_ **ContraClaim DMS** (React + FastAPI + MongoDB + S3 + VectorDB (Qdrant) + FalkorDB GraphDB)

---

## 1) What R2R is doing well (patterns worth copying) https://github.com/SciPhi-AI/R2R

R2R positions itself as a **production-ready retrieval system** (not just a “framework”), centered on a **REST API** plus SDKs/CLI, and “batteries included” features: ingestion, search, RAG, auth/collections, and observability.

Key patterns visible in R2R docs and repo structure:

### A. Config-first, runtime-tunable system

R2R exposes many retrieval behaviors as **configurable knobs** and (importantly) allows **runtime overrides** (e.g., advanced RAG technique selection, search settings, agent settings).  
**What to copy:** Treat retrieval like an “engine” with a stable API surface and a configurable internal pipeline.

### B. Ingestion as an orchestrated pipeline (not a single endpoint)

R2R emphasizes ingestion orchestration and multimodal ingestion as a first-class system capability.  
**What to copy:** A job-based ingestion pipeline with clear states and logs (queued → processing → chunked → embedded → indexed → enriched → ready).

### C. Chunk contextual enrichment during ingestion

R2R adds an ingestion-time feature called **contextual enrichment** that rewrites chunks to be more self-contained using:

- _Neighborhood strategy_ (nearby chunks)
- _Semantic strategy_ (top-k similar chunks)
  …and stores both original and enriched versions for transparency.

**What to copy:** Add “enriched chunk text” as an optional parallel field that improves retrieval/answering for long PDFs and engineering/legal docs.

### D. Advanced RAG techniques are toggles

R2R supports advanced retrieval strategies like **HyDE** and **RAG-Fusion** as selectable strategies.  
**What to copy:** Make “search strategy” a request parameter and/or per-project default (vanilla vs HyDE vs fusion).

### E. Agent layer that composes tools + maintains conversation state

R2R ships an “agent” endpoint that combines:

- retrieval tool(s) (local search; optionally web search)
- conversation_id to carry state across messages
- streaming output support

**What to copy:** A “drafting agent” for contract replies that can call:

- local_search (letters, drawings, clauses)
- metadata filters (project, contract, package, dates)
- optionally web_search (off by default in enterprise/legal mode)

### F. Observability is built-in (logs + analytics)

R2R provides:

- automatic logging for search and RAG runs (query, results, latencies, responses)
- an analytics endpoint/CLI to aggregate metrics (latency stats, usage patterns, error rates)
- superuser-only access to analytics features

**What to copy:** RAG is a system you tune. You need event logs + metrics from day 1.

### G. Repo hygiene: enforced code quality gates

R2R uses **pre-commit** to enforce:

- trailing whitespace, YAML checks, large file checks
- Ruff lint + Ruff formatting
- mypy
- custom hooks to avoid `print()` statements and discourage `typing.Dict/List/Union` in favor of built-ins

**What to copy:** Add pre-commit + CI checks with fast feedback and a “no debug prints” rule.

---

## 2) What to incorporate into ContraClaim DMS (feature-by-feature)

Below is a “translation table” from R2R patterns → ContraClaim modules.

### 2.1 Retrieval engine as a stable API surface

**Add/standardize these endpoints (FastAPI):**

- `POST /v1/ingestion/jobs` (create job)
- `GET  /v1/ingestion/jobs/{id}` (status, stage, progress)
- `POST /v1/retrieval/search` (hybrid/vector/keyword + filters)
- `POST /v1/retrieval/rag` (RAG response + citations)
- `POST /v1/retrieval/agent` (multi-step tool-using agent for drafting)
- `GET  /v1/observability/logs`
- `POST /v1/observability/analytics`

**Why:** once this “engine API” is stable, you can upgrade internals (vector DB, reranker, chunking) without breaking frontend.

---

### 2.2 Ingestion orchestration (job queue + idempotency)

**Implementation approach:**

- Use **Celery/RQ/Arq** (or FastAPI background workers if small) + Redis for queue.
- Persist job state in MongoDB:
  - `queued`, `extracting`, `chunking`, `embedding`, `indexing`, `enriching`, `graph_building`, `done`, `failed`
- Idempotency keys:
  - Compute `content_hash` for each uploaded file; avoid re-embedding the same file for the same project/version.

**UI (React):**

- Ingestion queue screen with stages + per-stage timing.
- “Retry failed stage” button for admins.

---

### 2.3 Contextual enrichment for contract/engineering documents

**Add fields in chunk schema:**

- `text_original`
- `text_enriched` (optional)
- `enrichment_metadata` (strategy used, neighbors, similarity threshold, model, status)

**Two strategies (same as R2R):**

1. Neighborhood enrichment: include N previous + N next chunks (good for narrative letters).
2. Semantic enrichment: retrieve K most similar chunks (good for scattered references).

**Prompt tuned for ContraClaim:**

- rewrite in formal contract style
- resolve pronouns and vague references (“the Contractor”, “the Employer”)
- keep scope: do not introduce new facts not present in context
- preserve dates, clause numbers, letter references verbatim

**Storage:** keep both versions; let user toggle which text is used for retrieval.

---

### 2.4 Hybrid retrieval (lexical + vector + reranking)

**Baseline stack (recommended):**

- Lexical: OpenSearch/Elasticsearch **or** Postgres full-text (if you already have Postgres)
- Vector: Qdrant (fast + simple) or Weaviate
- Fusion: Reciprocal Rank Fusion (RRF) across lexical + vector
- Reranker (optional, Phase 2): cross-encoder reranker (e.g., bge-reranker / Cohere rerank / local reranker)

**Filters (must for projects):**

- org_id, project_id, package_id
- letter_no, sender/receiver, date range
- tags, doc_type, correspondence_chain_id

---

### 2.5 Advanced RAG toggles (HyDE + Fusion) as request options

**Add to `/retrieval/search` request:**

```json
{
  "query": "...",
  "strategy": "vanilla | hyde | rag_fusion",
  "limit": 10,
  "filters": {...}
}
```

**HyDE:** generate a hypothetical “ideal answer doc”, embed it, retrieve with that embedding.  
**RAG-Fusion:** generate multiple query rewrites, retrieve each, fuse results.

**UI:**

- “Search mode” dropdown (Default / HyDE / Fusion)
- show cost warning (HyDE/Fusion triggers extra LLM calls)

---

### 2.6 Agentic drafting for contractual letters (ContraClaim’s differentiator)

Build a **Drafting Agent** (LangGraph / PydanticAI / your existing agent stack) that:

1. reads the incoming letter + metadata
2. identifies missing info (asks user questions)
3. runs retrieval with filters (and optionally chain-of-correspondence traversal)
4. produces:
   - bullet-point “issues to address”
   - draft reply in your standard format
   - citations as (document_id, chunk_id, page_no)

**Conversation state:**

- `conversation_id` stored in DB
- each message stores references used + user accept/reject actions (for future fine-tuning)

---

### 2.7 Observability + analytics (make tuning systematic)

**Log schema:**

- run_id, run_type (search/rag/agent/ingestion_stage)
- timestamps + latency per stage
- query, filters, strategy
- retrieved_ids + scores
- model used + token counts (if available)
- user_id, org_id, project_id
- error traces (sanitized)

**Analytics queries:**

- top queries by project
- mean/median latency by strategy
- “no answer” rate
- most-cited docs/clauses
- drift monitoring (embedding model changes)

**Permissions:**

- analytics endpoints are **superadmin** only.

---

### 2.8 Developer hygiene (copy the repo discipline)

**Add to ContraClaim repo:**

- `.pre-commit-config.yaml` with:
  - whitespace + large files + yaml validation
  - Ruff + ruff-format
  - mypy
  - custom hook: forbid `print(` in backend code
- CI workflow to run pre-commit + unit tests on PRs.

---

## 3) Suggested implementation plan (phased, low-risk)

### Phase 0 — Foundations (1–2 weeks)

- Define API contracts for: ingestion jobs, search, rag, agent, logs.
- Add pre-commit + CI gates.
- Add logging tables/collections.

**Deliverable:** stable “retrieval engine” API skeleton + CI discipline.

### Phase 1 — Retrieval MVP (2–4 weeks)

- Vector search (Qdrant) + metadata filters
- Basic RAG with citations
- Ingestion job pipeline: extract → chunk → embed → index
- UI: upload + search + citations view

**Deliverable:** working RAG over uploaded letters.

### Phase 2 — Hybrid + Enrichment (3–5 weeks)

- Add lexical index and RRF fusion
- Add contextual enrichment during ingestion
- Add chunk toggle (original vs enriched)
- Add baseline analytics dashboards (latency, usage, errors)

**Deliverable:** improved quality on long PDFs and messy OCR.

### Phase 3 — Drafting agent + workflows (4–8 weeks)

- Drafting agent with conversation_id + tool calls
- Review/approval workflow hooks (your RBAC)
- Traceability: citations + “why this paragraph” view
- Optional: chain-of-correspondence graph traversal

**Deliverable:** contract reply drafting that is auditable and reusable.

### Phase 4 — Knowledge graph layer (optional, later)

- Triplet extraction from letters (subject–predicate–object)
- Graph DB (Neo4j/FalkorDB) for multi-hop questions:
  - “Which letters mention variation order X and refer to clause Y?”
- GraphRAG as an additional retrieval mode

**Deliverable:** multi-hop relationship queries across correspondence.

---

## 4) Concrete “how” (technical notes you can hand to an engineer)

### 4.1 MongoDB collections you’ll likely add

- `ingestion_jobs`
- `documents`
- `chunks`
- `embeddings_index_meta` (what model/version created embedding)
- `rag_runs` (observability logs)
- `agent_conversations` + `agent_messages`

### 4.2 Minimal chunk document schema (example)

```json
{
  "chunk_id": "uuid",
  "document_id": "uuid",
  "org_id": "uuid",
  "project_id": "uuid",
  "page_start": 12,
  "page_end": 12,
  "text_original": "...",
  "text_enriched": "...",
  "enrichment_metadata": {
    "status": "success",
    "strategies": ["semantic", "neighborhood"],
    "semantic_neighbors": 10,
    "similarity_threshold": 0.7
  },
  "tags": ["EOT", "Clause-8.4", "Delay"],
  "created_at": "..."
}
```

### 4.3 API response structure for citations

Return citations as structured objects:

```json
{
  "answer": "...",
  "citations": [
    {
      "document_id": "...",
      "title": "GLM-SAM-...pdf",
      "chunk_id": "...",
      "page": 12,
      "score": 0.83,
      "snippet": "..."
    }
  ]
}
```

---

## 5) Quick wins for your current ContraClaim roadmap

1. **Contextual enrichment** will likely give the biggest immediate bump for metro-project PDFs (OCR noise + long clauses).
2. **Built-in observability** will reduce debugging time when users complain “AI is wrong”.
3. **Request-selectable strategies** (vanilla/HyDE/fusion) lets you tune per project without redeploys.
4. **Conversation-aware drafting agent** aligns perfectly with your “incoming → questions → draft → approval” workflow.

---

## Appendix — Source touchpoints

- R2R overview: production-ready system with multimodal ingestion, hybrid search, knowledge graphs, agentic RAG, and access management.
- Contextual enrichment: neighborhood + semantic strategies; stores original + enriched chunk versions.
- Advanced RAG: HyDE and RAG-Fusion toggles.
- Agent cookbook: conversation_id, tool selection (local_search + optional web_search), streaming.
- Observability cookbook: logs + analytics + superuser access controls.
- Pre-commit hooks: Ruff/mypy plus custom hygiene checks.
