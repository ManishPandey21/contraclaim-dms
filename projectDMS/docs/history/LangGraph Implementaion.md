# LangGraph Integration Plan for Letter Workflow Management

## Objectives

- Replace the current ad-hoc AI draft flow with a reproducible LangGraph pipeline that orchestrates context collection, LLM reasoning, validation, and persistence.
- Keep parity with existing document + letter services while enabling observability and retry controls.
- Expose minimal backend surface area to the web app for running drafts, inspecting intermediate steps, and handling remediation paths (e.g., missing data or review loops).

## Backend Plan

### 1. Graph Design

- **Graph package**: create `backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py`.

  - Define typed state object (letter id, metadata (Subject, summary, keywords, contractual clauses, references), extracted context (full_text, ocrText), draft content, validation results).
  - Nodes:
    1. `load_state` — pull letter + document context via `LetterService` and `DocumentService`; compute workflow guardrails (`WorkflowEngine`).
    2. `collect_context` — parallel branches to:
       - vectors (`LlamaIndexVectorService` / `LangChainVectorService`) for semantic snippets;
       - conversation tree via `ConversationService`;
       - metadata snapshots from `DatabaseService` + parsed OCR summaries.
    3. `plan_response` — call OpenAI (or configured model) with `LETTER_DRAFT_PROMPT_TEMPLATE`; returns a structured plan (sections, gaps, required confirmations).
    4. `draft_letter` — generate final text body + subject; include trace metadata for tooltips.
    5. `validate_and_route` — reuse `WorkflowEngine` + field validators to either approve status updates or branch to `needs_input`.
    6. `persist_results` — update letter record (`LetterService.change_status` or `update_letter`), attach generated draft, and emit notifications (`NotificationService`).
    7. `summarise` — return payload for API (draft, plan, trace, warnings).
  - Include error handler node for retries, capturing context in `processing_metadata`.

- **Reusable tools**: add `backend/rbac_backend/ai_workflows/tools.py` for shared helper functions (vector search, clause matching, reference formatting).

### 2. Service Layer Changes

- Extend `AIService` with LangGraph executor:
  - `generate_draft_with_langgraph(letter_id, request_payload, current_user)` orchestrates the graph and persists results.
  - Maintain legacy `generate_draft` for compatibility (gated by feature flag).
- Update `routers/ai_assistant.py` to expose:
  - `POST /ai-assistant/langgraph/draft` — starts the graph, returns streamed or final result.
  - `GET /ai-assistant/langgraph/runs/{letter_id}` — retrieve latest run summary (for UI replay).
- Add configuration keys in `backend/rbac_backend/config/settings.py`:
  - `LANGGRAPH_ENABLED`, `LANGGRAPH_MODEL`, `LANGGRAPH_TIMEOUT`, `LANGGRAPH_TRACE_STORE`.

### 3. Persistence & Observability

- Extend `DocumentService` and `LetterService` data models with:
  - `draft_plan` (LLM plan text),
  - `draft_trace` (list of node outputs, trimmed),
  - `graph_status` (success, needs_input, failed),
  - `graph_started_at` / `graph_completed_at`.
- Add `backend/rbac_backend/models/langgraph_run.py` for run history if detailed auditing is required.
- Emit structured logs per node (via `configure_pipeline_logger`) and optional trace storage (filesystem or Mongo).
- Tests: augment `backend/rbac_backend/tests/test_letters_critical.py` with graph happy-path and failure cases; add unit tests for tool helpers.

### 4. Feature Flagging & Migration

- Default the new flow off; expose admin toggle via `/config` endpoints.
- Provide migration script to backfill existing letters with `graph_status="legacy"` to differentiate analytics.

## Web App Plan

### 1. New Draft Experience

- Introduce `client/src/pages/LetterDraftPage.tsx`:
  - Fetch `langgraph` run data.
  - Display context summary (documents, conversation chain).
  - Show LLM plan, final draft, validation warnings.
  - Actions: `Run LangGraph`, `Request Inputs`, `Accept Draft`.
- Create hooks:
  - `client/src/hooks/useLanggraphDraft.ts` — wraps `/ai-assistant/langgraph` endpoints.
  - `client/src/hooks/useLetterGraphRuns.ts` — polling for run status.

### 2. Reference & Thread View Enhancements

- Update `client/src/pages/ReferencePage.tsx`:
  - Add LangGraph status badge next to letter metadata.
  - Provide quick link to the draft page.
  - Surface latest plan summary in a collapsible section.

### 3. Shared UI Components

- Add `client/src/components/langgraph/GraphStatusBadge.tsx`.
- Add `client/src/components/langgraph/PlanViewer.tsx` to render step outputs with collapsible details.

### 4. Routing & Navigation

- Update `client/src/AppRoutes.tsx` to include `/letters/:id/draft`.
- Extend sidebar/menu to link to the new drafting workspace.

### 5. Feature Gate

- Add environment toggle (e.g., `VITE_LANGGRAPH_ENABLED`) to show/hide new UI elements.
- Gracefully fall back to legacy draft modal when disabled.

## Delivery Roadmap

1. **Sprint 1** — backend scaffolding (graph engine, executor integration, feature flag).
2. **Sprint 2** — persistence upgrades, API endpoints, initial tests.
3. **Sprint 3** — frontend pages, hooks, visual components.
4. **Sprint 4** — QA hardening, observability dashboards, documentation updates.

All code changes are additive, preserving existing behavior until the flag is flipped.
