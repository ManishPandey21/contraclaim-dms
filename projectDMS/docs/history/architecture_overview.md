## Architecture Snapshot (backend + frontend)

### Backend entrypoints and core services

- **FastAPI app**: `backend/rbac_backend/main.py` registers routers for auth/users/documents/letters/ai-assistant/roles/etc plus `/v1` retrieval-engine and websocket routes.
- **Core services**:
  - Document pipeline: `DocumentService`, `DocumentProcessor`, `DatabaseService`, `GraphIngestionService`.
  - AI drafting: `AIService`, `LetterService`, `StrategyContextService`.
  - Retrieval/RAG: `IngestionService` + `IngestionPipeline`, `RetrievalService`, `DraftingAgentService`, `ObservabilityService`.
  - Storage: `StorageSettingsService`, `SecureFileService`, `S3Service`.
  - Graph: `FalkorGraphService`, `GraphAdapter` (Graphiti REST).

### Data stores and persistence

- **MongoDB (contraclaim)**: `documents`, `letters`, `document_vectors`, `chunks`, `ingestion_jobs`, `vector_sync_status`, `contract_ingest_jobs`, `rag_runs`, `agent_conversations`, `agent_messages`, `document_comments`, `ai_files`, `vector_stores`.
- **Vector stores**:
  - Qdrant via `VectorClient` (retrieval engine + contract ingestion) and `LangChainVectorService` (document processing).
  - MongoDB vector search via `LlamaIndexVectorService` (document processing + contract ingestion).
- **Graph**:
  - FalkorDB for letter threads and references (`FalkorGraphService`).
  - Graphiti REST via `GraphAdapter` (optional).
- **Files**:
  - Local uploads in `uploads/` (per org/project); Markdown summaries appended under `uploads/<org>/<project>/incoming.md|outgoing.md`.
  - Optional S3 via `StorageSettingsService`.

### Frontend entrypoints and core components

- **Routes**: `client/src/routes.tsx` defines `/letters`, `/letters/:id/strategy`, `/letters/:id/draft`, `/letters/:id/review`, `/letters/:id/approval`, `/letters/:id/completed`, `/letters/summary/:id`.
- **Hooks**:
  - `useLetterWorkflow` drives list/detail/state transitions.
  - `useLanggraphDraft`, `useLanggraphStrategyPlan`, `useLetterGraphRuns` call LangGraph endpoints.
- **Key components**:
  - `LetterWorkflowPage`, `LetterStrategicPlanPage`, `LetterDraftPage`.
  - `LinkedDocumentSelector`, `PlanViewer`, `BackgroundSummary`, `DraftSourcesPanel`.

## Key Feature Traces (code-level)

### 1) Letter drafting flow (current)

1. **List/Select**: `LetterWorkflowPage` uses `useLetterWorkflow` -> GET `/letters`.
2. **Strategy**: `LetterStrategicPlanPage` calls:
   - POST `/letters/{id}/strategy/context` (creates role-based contexts).
   - POST `/ai-assistant/langgraph/strategy-plan` (analysis_only plan).
3. **Draft**: `LetterDraftPage` calls:
   - POST `/ai-assistant/langgraph/background` (analysis_only background summary).
   - POST `/ai-assistant/langgraph/draft` (draft + plan + sources).
4. **Persist**: `LetterService.record_langgraph_result` writes plan/draft/summary/context/sources fields onto `letters`.
5. **Review**: `LetterDraftEditor` updates content -> PUT `/letters/{id}`, then POST `/letters/{id}/submit`.

### 2) LangGraph usage and nodes

The LangGraph pipeline defined in `backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py` executes:

- `load_state`: load letter, status, scope.
- `collect_context`: resolve curated context docs, auto-select via retrieval, attach comments, conversation chain, and Falkor thread.
- `retrieve_sources`: fetch contract clauses and related letters; build `sources`.
- `plan_response`: synthesize structured plan + background summary items.
- `draft_letter`: call `AIService.generate_draft` with augmented context and points.
- `review_draft`: clause consistency + placeholder checks.
- `validate_and_route`: workflow guardrails (terminal status, ready_for_review).
- Optional Falkor sync: upsert letter + references into graph.

### 3) Clause/document ingestion and vectorization

- **General document upload**:
  - Upload via `/documents` or bulk upload -> file stored local/S3.
  - `DocumentService.queue_document_processing` enqueues OCR + metadata.
  - `DocumentProcessor` does OCR, OpenAI extraction, metadata parsing, then `DatabaseService.save_document_data`.
  - `DatabaseService` writes OCR/metadata to `documents`, chunks to `document_vectors`, and optionally Qdrant (LangChain).
- **Contract ingestion**:
  - `ContractService` delegates to `contracts_ingest.py`.
  - Marker/regex/LLM clause extraction produces clause chunks with metadata (clause_number, clause_title, toc_path).
  - Marker extraction requires the external Marker CLI on PATH; configure via `MARKER_ENABLED`, `MARKER_CMD`, and `MARKER_OUTPUT_DIR`.
  - Vectorization uses LlamaIndex (Mongo) + Qdrant upsert via `VectorClient`.
- **Retrieval ingestion** (retrieval engine):
  - POST `/v1/ingestion/jobs` -> `IngestionPipeline` chunks text -> `chunks` collection.
  - Embeddings stored in Qdrant; sync tracked in `vector_sync_status`.

### 4) RAG setup (current)

- `/v1/retrieval/search` and `/v1/retrieval/rag` use `RetrievalService` + Qdrant (`VectorClient`) and `LLMGenerator`.
- `/v1/retrieval/agent` uses `DraftingAgentService` to run search + draft reply.
- Observability logs to `rag_runs` via `ObservabilityService`.
- `routers/deep_planning.py` and `routers/rag_utils.py` show OpenAI vector store utilities but are not wired in `main.py`.

### 5) Context passing backend -> frontend

- **Stored on letter**: `draft_plan`, `draft_output`, `summary_points`, `context_document_ids`, `context_documents`, `background_summary`, `graph_thread`, `draft_sources`, `reviewer_findings`, `strategy_plan`/`strategic_outline`.
- **Fetch on UI**:
  - GET `/ai-assistant/langgraph/runs/{id}` for latest run.
  - GET/PUT `/letters/{id}/context-documents` for curated context docs.
  - POST `/letters/{id}/strategy/context` and POST `/ai-assistant/langgraph/strategy-plan` for strategy stage.
  - GET `/documents` (search) for context selection.

## Areas for Improvement (observed in code)

- **LangGraph import ambiguity**: `backend/rbac_backend/ai_workflows/langgraph.py` and `backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py` both define `LetterDraftGraph`; `AIService` imports `..ai_workflows.langgraph` which can resolve to either. Consolidate to one implementation to avoid mismatched return types.
- **Multiple vector pipelines**: Document processing writes `document_vectors` (LlamaIndex) and Qdrant (LangChain), while retrieval ingestion writes `chunks` + Qdrant, and contracts ingestion writes both. Standardize schemas and metadata (org_id vs organization_id, project_id vs project_id) to reduce drift.
- **Legacy/unused endpoints**: `routers/deep_planning.py` and `routers/Old/*` contain separate vector-store logic but are not registered in `main.py`. Either remove or formally integrate.
- **Draft generation still template-based**: `AIService.generate_draft` does not use RAG results directly; any retrieval is only used to select context docs. Consider swapping in `RetrievalService` or contract clause sources for grounded drafting.
- **Encoding artifacts**: `AIService._build_snippet` and some UI strings contain garbled placeholders. Normalize to simple ASCII ellipses and ensure consistent encoding across files.
- **Config duplication**: `document_processing_config.py` and `document_processing_config1.py` overlap; choose one and deprecate the other to avoid confusion.
