# Retrieval Engine (v1)

Versioned retrieval endpoints live under `/v1` and are additive to the existing `/api` surface. All routes are RBAC-scoped: `org_id` and `project_id` must match the caller unless the user is a `superadmin`.

## Endpoints

- `POST /v1/retrieval/search`
  - Body: `{"query": "...", "strategy": "vanilla|hyde|rag_fusion", "limit": 8, "use_enriched_text": false, "filters": {"org_id": "...", "project_id": "...", "tags": [], "letter_no": "...", "chain_id": "..."} }`
  - Returns scored chunks with payload metadata and timings.
  - Strategies:
    - `vanilla`: direct vector search.
    - `hyde`: hypothetical doc generated then embedded.
    - `rag_fusion`: 3–4 query rewrites, reciprocal-rank fused.

- `POST /v1/retrieval/rag`
  - Body: same as search + `{"answer_style": "...", "max_tokens": 512}`
  - Returns `{answer, citations[], strategy_used, timings}` with structured citations (`document_id`, `chunk_id`, `page`, `snippet`).

- `POST /v1/retrieval/agent`
  - Body: `{"conversation_id"?, "org_id", "project_id", "incoming_letter_id"?, "incoming_text"?, "user_goal"?, "strategy", "filters"?, "streaming": false}`
  - Drafts a reply, returns issues/questions, draft text, citations, tool traces, and `conversation_id` for stateful threads.
  - Web search is disabled; only local retrieval is used.

- `GET /v1/observability/logs?org_id&project_id&run_type&from&to`
  - Returns the latest RAG/search/agent run logs (superadmins can request cross-scope).

- `POST /v1/observability/analytics`
  - Body: `{"org_id"?, "project_id"?, "window": 7, "group_by": ["strategy","run_type"]}`
  - Returns latency percentiles, no-answer rate, top queries, and error counts. Non-superadmins must scope to an org/project.

## Data contracts

Pydantic models live in `rbac_backend/retrieval/models.py`, `ingestion/models.py`, `agents/models.py`, and `observability/models.py`. Every response includes `timings` for traceability. Citations always include `document_id`, `chunk_id`, `page`, and a grounded snippet.

## Strategy toggles

- **HyDE**: generates a short hypothetical answer, embeds it, and searches with that embedding.
- **RAG-Fusion**: creates multiple query rewrites and applies simple RRF fusion to blend results.
- **use_enriched_text**: when `true`, uses ingestion-time enriched chunks for snippets/context; otherwise uses original text.

## Filters and RBAC

`org_id` and `project_id` are mandatory and enforced in the router. Optional filters include `tags`, `letter_no`, `chain_id`, `doc_type`, and `date_range`. Future lexical/hybrid layers can plug into the same request model without breaking the API.

