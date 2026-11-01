# Storage Workflow Improvement Implementation Plan

This plan translates the findings from `storage-workflow-complete.md` into actionable engineering work while respecting the current codebase (post FalkorDB integration).

---

## 1. Scope & Goals

1. Keep MongoDB as the source of truth while synchronising vectors (Qdrant) and relationships (FalkorDB).
2. Eliminate reference inconsistencies (missing backlinks, stale links).
3. Guarantee dual-write visibility & observability for embeddings.
4. Provide recovery/backfill tooling and automated health checks.

---

## 2. Current Baseline

- Mongo metadata upserts: `backend/rbac_backend/services/database_service.py::_upsert_document_metadata`.
- Vector dual write: `backend/rbac_backend/services/database_service.py::_create_and_store_embeddings` â†’ Qdrant through `LangChainVectorService`.
- Graph ingestion: `backend/rbac_backend/graph/graph_ingestion_service.py` now syncs to Falkor via `FalkorGraphService`.
- LangGraph pipeline uses Falkor threads and records agent-created references (`backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py`).

Remaining gaps (per analysis):
- No mirrored backlinks (`documents.referencedBy`) and no manual sync entrypoint.
- Vectors stored in Mongo (`document_vectors`) and Qdrant can diverge.
- No automated reconciliation log/metrics.

---

## 3. Workstreams & Tasks

### Workstream A "" Reference Synchronisation

1. [x] **Create `ReferenceSyncService`**
   - Location: `backend/rbac_backend/services/reference_sync_service.py`.
   - Responsibilities:
     - `sync_bidirectional(document_id, references)` "" ensure `referencedBy` is updated for targets found in Mongo.
     - `enqueue_missing()` "" optional background job for async handling fallback when referenced document missing.
   - Use `normalize_letter_code` & use existing `DocumentService` helpers.

2. [x] **Hook into ingestion**
   - In `DocumentService.process_document_async` after Mongo upsert (success path) invoke `ReferenceSyncService`.
   - In `DocumentService.add_reference()` update to call `ReferenceSyncService` to keep manual links in sync.

3. [x] **Manual sync endpoint**
   - Add `POST /documents/{id}/sync-references` in `backend/rbac_backend/routers/documents.py`.
   - Endpoint triggers `ReferenceSyncService.sync_bidirectional` for the stored `reference` array + Falkor update (reuse `graph_ingestion.sync_document_to_falkor`).

4. [x] **Storage schema adjustments**
   - Ensure `documents.referencedBy` is an array of `{documentId, letterNo, source}` objects (align with extracted references).
   - Update `Document` model (`backend/rbac_backend/models/document.py`) if needed to expose `referencedBy`.

### Workstream B "" Vector Consistency

1. [x] **Vector sync tracker**
   - New collection `vector_sync_status` with fields: `document_id`, `mongo_chunks`, `qdrant_chunks`, `sync_status`, `updatedAt`.
   - Update `_create_and_store_embeddings` to:
     - Upsert status before/after Qdrant call.
     - Detect mismatches and raise/log warnings.

2. [x] **Qdrant reconciliation utilities**
   - `scripts/reconcile_vectors.py` for nightly job:
     - Compare `document_vectors` count vs Qdrant response per document.
     - Optional re-push mismatched entries.

3. [x] **Config toggles**
   - Add to `Settings`: `VECTOR_DUAL_WRITE_ENABLED`, `VECTOR_VERIFY_AFTER_WRITE`.
   - Respect toggles in `_create_and_store_embeddings` to disable Qdrant writes in emergencies.

### Workstream C "" FalkorDB Enhancements

1. [x] **Edge cleanup guard**
   - Extend `FalkorGraphService.upsert_letter_with_refs` cleanup to skip manual edges unless `source="parser"`.
   - Already partially implemented; ensure configuration flag `FALKORDB_CLEANUP_REFERENCES` works end-to-end.

2. [x] **Replay job improvements**
   - Enhance `scripts/backfill_falkordb.py` to:
     - Accept `--only-missing` flag (compare existing Falkor nodes via `get_thread`).
     - Batch commits (e.g., pipeline up to 100 documents per query).

3. [x] **LangGraph agent logging**
   - Already writing CITES edges. Add optional metadata (e.g., `e.source="agent"`). Validate deduping.

### Workstream D "" Observability & Health

1. [x] **Sync status API**
   - Add `GET /storage-sync/status` returning Mongo vs Qdrant counts, pending sync operations, Falkor edge mismatches.
   - Implement in new router module `backend/rbac_backend/routers/storage_sync.py`.

2. [x] **Background checker**
   - FastAPI background task or APScheduler job scanning:
     - `vector_sync_status` for stale entries (>X minutes).
     - `documents` where `reference` non-empty but `referencedBy` missing.
   - Log warnings / send notifications via existing notification service.

3. [x] **Logging**
   - Standardise logger namespace `storage.sync` for easier scraping.
   - Ensure every sync step logs doc ID, counts, duration.

### Workstream E "" Tests & Documentation

1. [x] **Unit tests**
   - `tests/services/test_reference_sync_service.py`
   - Mock Mongo collections; verify state transitions.
   - `tests/services/test_vector_sync_status.py` to ensure status recording.

2. [ ] **Integration tests**
   - Use dockerised Falkor + Qdrant fixtures (if available).
   - Scenario: upload doc with references â†’ verify Mongo, Qdrant, Falkor.

3. [x] **Docs**
   - Update `storage-workflow-complete.md` with implemented changes.
   - Add runbook entries: new scripts, env vars, endpoints.

---

## 4. Timeline & Dependencies

| Week | Focus | Dependencies |
|------|-------|--------------|
| 1 | Workstream A (Reference sync) | None |
| 1-2 | Workstream B (Vector status) | Qdrant connectivity |
| 2 | Workstream C (Falkor improvements) | FALKORDB_ENABLE=true |
| 2-3 | Workstream D (Observability) | A & B complete |
| 3 | Workstream E (Tests/doc) | All prior |

---

## 5. Rollout Strategy

1. **Develop on staging with real Falkor/Qdrant containers**.
2. Run `scripts/backfill_falkordb.py --limit` + vector reconciliation before deploy.
3. Enable new env toggles gradually (start with `VECTOR_VERIFY_AFTER_WRITE=false`, monitor, then enable).
4. Monitor `/storage-sync/status` + logs for a week before production rollout.

---

## 6. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Large backfill load | RedisGraph blocking | Batch writes, throttle scripts |
| Reference circular updates | Infinite loops | Deduplicate via `ReferenceSyncService` and track processed IDs |
| Vector re-sync timeouts | Lagged dual write | Run reconciliation off-hours, add retry with exponential backoff |
| Falkor outages | Draft pipeline blocked | Keep `FALKORDB_ENABLED` toggle; pipeline falls back gracefully |

---

Following this plan will close the gaps identified in `storage-workflow-complete.md` while building on the recent FalkorDB integration work.

---

## 7. Letter Workflow Enhancements (LangGraph UX)

### 7.1 Document Selection & Context Management

**Baseline**
- `LinkedDocumentSelector` (`client/src/components/letter-workflow/LinkedDocumentSelector.tsx`) already fetches curated context documents through `/letters/{letter_id}/context-documents`, surfaces suggested matches, offers search, and persists selections via `saveContextDocuments`.
- `LetterStrategicPlanPage` (`client/src/pages/LetterStrategicPlanPage.tsx`) mirrors stored selections into local state, merges them with the latest LangGraph run payload, and submits selected IDs to the AI workflow through `useLanggraphDraft`.
- The LangGraph pipeline"™s `collect_context` node (`backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py`) merges stored IDs, request-scoped `document_ids`, and fallback matches; it already enriches selections with document metadata, recent comments, and graph thread references.

**Implementation**
1. `LinkedDocumentSelector` now exposes in-place navigation and richer context:
   - Every curated, suggested, and search result row includes an **Open document** action that launches `/documentviewer/{id}` in a new tab for quick verification.
   - Conversation history sourced from the Falkor graph thread renders inline with direction badges, giving reviewers immediate visibility into related correspondence before they pin additional documents.
   - Selection order honours manual picks first (`request.document_ids`) followed by stored preferences and optional fallbacks so curated context always takes priority over auto-suggestions.
2. Context updates persist through `saveContextDocuments` as before, but the selector now surfaces live toast feedback and refresh controls so background runs can react immediately to new choices.
3. `collect_context` validates that every requested document belongs to the same organisation and project as the target letter, emits warnings for filtered identifiers, and only falls back to same-letter-number matches when no validated context remains—returning the cleaned document list, metadata, and recent comments to the UI.

### 7.2 Background Generation Control

**Baseline**
- `LetterStrategicPlanPage` exposes a dedicated "oeGenerate Background" button that calls `useLanggraphDraft` with `analysisOnly: true`, hitting `POST /ai-assistant/langgraph/background` (`backend/rbac_backend/routers/ai_assistant.py`).
- `LetterDraftGraph.plan_response` computes summary points, document/comment highlights, and exits early when `analysis_only` is set; `LetterService.record_langgraph_result` persists the background payload without overwriting the drafted body.

**Implementation**
1. The dedicated **Generate Background** control still funnels requests through `analysisOnly` runs, but the page now caches the returned summary (`backgroundItems`), stamps a `lastGeneratedLabel`, and highlights stale results whenever the active context IDs drift from the stored LangGraph run.
2. Background analyses hydrate both the strategic plan and draft workspaces so writers can review the latest context before drafting; cached payloads remain visible until a new run succeeds, enabling side-by-side comparison without losing prior insights.
3. The LangGraph pipeline continues to short-circuit the drafting node for analysis-only requests, now returning the validated document IDs, any scope-related warnings, and the refreshed Falkor thread so the UI can annotate context and conversation panels without re-fetching data.

### 7.3 Enhanced Summary Presentation

**Baseline**
- `PlanViewer` (`client/src/components/langgraph/PlanViewer.tsx`) renders plan text, summary points, and trace metadata but provides no citations or annotation support.
- `BackgroundSummary` (`client/src/components/letter-workflow/BackgroundSummary.tsx`) already groups background entries, supports collapsing, and displays document badges, yet it lacks inline annotations and clickable citations.

**Implementation**
1. `PlanViewer` has been rebuilt as an accordion-based summary: each major paragraph becomes a collapsible section with a global toggle that lets reviewers hide or reveal the full plan without leaving the page.
2. Sections automatically surface citations by matching plan text against the merged context documents, rendering actionable links to `/documentviewer/{id}` and displaying matching summary points as inline annotation callouts.
3. The execution timeline remains available beneath the plan, providing node-level trace data while the condensed layout keeps the strategic overview readable during drafting.

### 7.4 Validation & Release Checklist

- Manual QA: select documents, run background analysis, and verify `letters` documents show updated `context_document_ids`, `background_summary`, warnings for filtered documents, and `graph_status="analysis_only"`.
- Automated tests (follow-up): add LangGraph pipeline coverage for the scope validation/fallback branch and front-end tests capturing PlanViewer section parsing plus citation rendering.
- Rollout: document the refreshed workflow in `storage-workflow-complete.md`, enable feature flags in staging, and monitor `/ai-assistant/langgraph/runs/{letter_id}` responses to ensure background-only runs report without drafts.

---

## Letter Workflow End-to-End Reference

### 1. High-level flow

1. **Listing & initiation**
   - `client/src/pages/LetterWorkflowPage.tsx` renders the kanban-like overview.
   - Data comes from `client/src/hooks/useLetterWorkflow.ts`, which issues `GET /api/letters` (router: `backend/rbac_backend/routers/letters.py@get_letters`).
   - Creating a letter invokes `handleLetterInitiation` → `POST /api/letters` → `LetterService.create_letter`.

2. **Input stage**
   - `client/src/pages/LetterInputPage.tsx` displays core metadata entry and uses `handleLetterUpdate` (`PUT /api/letters/{id}`).
   - Backend validation and persistence handled by `LetterService.update_letter`.

3. **Strategic plan & context**
   - `client/src/pages/LetterStrategicPlanPage.tsx` combines:
     - `useLetterWorkflow` for letter metadata.
     - `useLetterGraphRuns` → `GET /api/ai-assistant/langgraph/runs/{letter_id}` (`AIAssistantController.get_langgraph_run`, `AIService.get_latest_langgraph_run`).
     - `useLanggraphDraft` → `POST /api/ai-assistant/langgraph/background` or `/draft`.
   - Document selection lives in `client/src/components/letter-workflow/LinkedDocumentSelector.tsx`, calling:
     - `GET /api/letters/{id}/context-documents`
     - `PUT /api/letters/{id}/context-documents`
     (both wired through `LetterService.get_context_documents` / `save_context_documents`).
   - Background presentation via `client/src/components/letter-workflow/BackgroundSummary.tsx` and plan display via `client/src/components/langgraph/PlanViewer.tsx`.

4. **LangGraph background + plan generation**
   - `useLanggraphDraft` builds the request (`LangGraphDraftRequest`) and posts to AI routes.
   - `backend/rbac_backend/routers/ai_assistant.py` delegates to `AIService.generate_draft_with_langgraph`.
   - Core orchestration in `backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py`:
     - `collect_context` merges curated IDs, validates scope through `DocumentService.get_documents_by_ids`, hydrates comments via `DocumentService.get_comments`, and pulls related conversation nodes through `ConversationService`.
     - `plan_response` constructs strategic plan text, summary points, and background items.
     - Optional `draft_letter` node uses injected `LetterService.generate_draft` (via workflow engine) when `analysis_only` is false.
   - Results persisted with `LetterService.record_langgraph_result`, updating the letter document with plan, draft, `background_summary`, and trace metadata.

5. **Drafting workspace**
   - `client/src/pages/LetterDraftPage.tsx` mirrors strategic plan data and allows editing through `LetterDraftEditor`.
   - Regeneration still uses `useLanggraphDraft`; context remains in sync with `LinkedDocumentSelector`.
   - Submitting for review triggers `submitForReview` → `POST /api/letters/{id}/submit` → `LetterService.submit` (transitions validated by `workflow_engine`).

6. **Review → Approval → Completion**
   - Pages: `LetterReviewPage.tsx`, `LetterApprovalPage.tsx`, `LetterCompletedPage.tsx`.
   - Actions exposed by `useLetterWorkflow`: `POST /api/letters/{id}/approve`, `/complete`, etc., which flow through `LetterService` and emit notifications.

7. **Conversation & history**
   - `ConversationService` powers `GET /api/letters/{id}/chain` and `/tree`, used by various components (e.g., timeline sidebar).
   - Falkor graph threads accessed in the LangGraph pipeline via `FalkorGraphService.get_thread`, and rendered on the frontend via `LinkedDocumentSelector` (conversation pane) and plan viewer citations.

### 2. Frontend files involved

- Pages:
  - `client/src/pages/LetterWorkflowPage.tsx`
  - `client/src/pages/LetterInputPage.tsx`
  - `client/src/pages/LetterStrategicPlanPage.tsx`
  - `client/src/pages/LetterDraftPage.tsx`
  - `client/src/pages/LetterReviewPage.tsx`
  - `client/src/pages/LetterApprovalPage.tsx`
  - `client/src/pages/LetterCompletedPage.tsx`
- Hooks / services:
  - `client/src/hooks/useLetterWorkflow.ts`
  - `client/src/hooks/useLetterGraphRuns.ts`
  - `client/src/hooks/useLanggraphDraft.ts`
  - `client/src/services/letter-workflow-api.ts`
  - `client/src/services/documents-api.ts`
- Components:
  - `client/src/components/letter-workflow/LinkedDocumentSelector.tsx`
  - `client/src/components/letter-workflow/BackgroundSummary.tsx`
  - `client/src/components/langgraph/PlanViewer.tsx`
  - `client/src/components/langgraph/GraphStatusBadge.tsx`
  - `client/src/components/letter-workflow/LetterDraftEditor.tsx`
- Types & utilities:
  - `client/src/types/langgraph.ts`
  - `client/src/utils/letterWorkflowMapping.ts`
  - `client/src/utils/dateFormat.ts`

### 3. Backend files involved

- Routers / controllers:
  - `backend/rbac_backend/routers/letters.py`
  - `backend/rbac_backend/routers/ai_assistant.py`
- Services & core logic:
  - `backend/rbac_backend/services/letter_service.py`
  - `backend/rbac_backend/services/ai_service.py`
  - `backend/rbac_backend/services/document_service.py` (context hydration)
  - `backend/rbac_backend/services/conversation_service.py`
  - `backend/rbac_backend/services/workflow.py` (status transitions)
  - `backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py`
  - `backend/rbac_backend/services/falkor_graph_service.py`
- Models & schemas:
  - `backend/rbac_backend/models/letter.py`
  - `backend/rbac_backend/models/ai_models.py`
  - `backend/rbac_backend/models/document.py`
- Supporting utilities:
  - `backend/rbac_backend/utils/validation.py`, `error_handler.py`
  - `backend/rbac_backend/core/database.py`, `core/security.py`

### 4. Step-by-step request lifecycle (example: strategic plan regeneration)

1. User clicks **Generate Strategy Plan** (`LetterStrategicPlanPage.tsx`).
2. `useLanggraphDraft.runDraft` posts to `/api/ai-assistant/langgraph/draft` with selected `document_ids`, letter subject, recipient, and optional context/points.
3. `AIAssistantController.generate_langgraph_draft` rate-limits, sanitizes request, and forwards to `AIService.generate_draft_with_langgraph`.
4. `AIService` instantiates `LetterDraftGraph` and calls `.run`.
5. Pipeline nodes:
   - `load_state` fetches letter via `LetterService`.
   - `collect_context` merges curated IDs (`LetterService` + `DocumentService`), validates scope, enriches comments, conversation, and Falkor thread.
   - `plan_response` builds plan text, summary points, and background items.
   - `draft_letter` optionally composes full draft (skipped for analysis-only runs).
   - `validate_and_route` checks workflow status via `workflow_engine`.
6. Result stored with `LetterService.record_langgraph_result`; response returned to client.
7. Frontend updates plan text, summary arrays, and background cards; `useLetterGraphRuns.fetchRun` refreshes stored snapshot for consistency.

This reference should help new contributors trace the letter workflow from React components through FastAPI controllers down to the LangGraph-inspired pipeline and supporting services.


## 8. Operational Follow-ups

1. **Expose Qdrant retrieval to the app**
   - New endpoint `GET /documents/vector-search` (`backend/rbac_backend/routers/documents.py`) queries Qdrant via `LangChainVectorService.similarity_search`.
   - Optional filters (`organization_id`, `project_id`, `uploadType`) are converted to Qdrant filters; responses include chunk metadata and hydrated document summaries for the UI.
   - Frontend consumers should call this route to surface semantically matched documents before invoking LangGraph runs.

2. **FalkorDB deployment defaults**
   - Set `FALKORDB_ENABLED=true` in deployment values so `FalkorGraphService.ensure_schema` runs as soon as ingestion begins; with the toggle off, schema creation remains dormant.
   - Keep `FALKORDB_CLEANUP_REFERENCES` aligned with environment expectations—production should leave cleanup enabled unless manual graph edges are being bulk loaded.

3. **Production monitoring**
   - Add `/storage-sync/status` (`backend/rbac_backend/routers/storage_sync.py`) to dashboards/alerting.
   - Track `vector_store.qdrant_chunk_count`, mismatch counts, and stale sync entries; alert if Qdrant totals diverge from Mongo or the route becomes unavailable so vector drift is caught early.

## 9. Staging Validation Playbook

To exercise the full document processing pipeline with Qdrant and Falkor enabled before promoting changes:

1. Ensure the staging deployment exports the following (preferably via helm values or environment manager):
   - `VECTOR_DUAL_WRITE_ENABLED=true`
   - `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `QDRANT_VECTOR_SIZE`
   - `FALKORDB_ENABLED=true`, `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_PASSWORD`
2. SSH or `kubectl exec` into the backend pod/container once a sample document is uploaded. The document must have a reachable `filepath_local`.
3. Run the validation helper:

   ```bash
   python backend/scripts/staging_vector_falkor_check.py \
     --document-id <mongo_document_id> \
     --reprocess \
     --timeout 300
   ```

4. The script replays `process_document_async`, waits for `vector_sync_status` to become `synced`, confirms Qdrant chunk counts, and queries Falkor via `FalkorGraphService.get_letter` to assert the node exists. On success it prints the synced status, Qdrant chunk tally, and the Falkor `normCode`.
5. Investigate failures using:
   - `db.vector_sync_status.find({"document_id": "<id>"})` in MongoDB.
   - `qdrant_client count --collection <name> --filter '{"must":[{"key":"document_id","match":{"value":"<id>"}}]}'`.
   - `redis-cli -h $FALKORDB_HOST -p $FALKORDB_PORT -a $FALKORDB_PASSWORD GRAPH.QUERY contraclaim "MATCH (l:Letter {normCode:'<normCode>'}) RETURN l"`.

This playbook provides a repeatable staging check that Qdrant and Falkor stay in lockstep after metadata extraction.
