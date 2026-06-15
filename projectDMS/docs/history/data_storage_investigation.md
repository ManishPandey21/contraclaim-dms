# Data Storage and Usage Analysis

This note captures how the Contraclaim stack persists and consumes data across MongoDB, Qdrant, and FalkorDB, and how that content surfaces in the web application. File paths below are relative to the repository root.

## 1. MongoDB

### 1.1 Primary collections

- **`documents`** – canonical metadata for uploaded letters/documents. Created in `DocumentService.create_document` (`backend/rbac_backend/services/document_service.py`) and updated after background processing. Key fields include filenames, organization/project identifiers, OCR flags, summaries, references, and dates.
- **`document_vectors`** – embedding chunks tied to documents. Written by `DatabaseService._create_and_store_embeddings` (`backend/rbac_backend/services/database_service.py`) via `LlamaIndexVectorService`. Each record stores the chunk text, SHA-256 checksum, embedding vector, model metadata, and linkage fields (`document_id`, `organization_id`, `project_id`, `chunk_index`, etc.).
- **`letters`** – conversational letters drafted within the system. Queried by `AIService.search_similar_letters` to drive the assistant UI (`backend/rbac_backend/services/ai_service.py`). Letters also receive LangGraph run snapshots for iterative drafting.
- Auxiliary collections such as `ai_vector_stores`, `ai_files`, and RBAC entities (`users`, `organizations`, `projects`, etc.) are initialized in `backend/rbac_backend/core/database.py` and power authorization and UI drop-down data.

### 1.2 Write pipeline

1. **Upload** – `DocumentController.create_document` (`backend/rbac_backend/routers/documents.py`) validates the file and calls `DocumentService.create_document`, which inserts a base record into `documents` with metadata, size, MIME type, and status.
2. **Background processing** – `DocumentService` queues `DocumentProcessor.process_document` (`backend/rbac_backend/services/document_processor.py`) to run OCR, OpenAI extraction, and metadata parsing.
3. **Persistence** – `_save_results` in `DocumentProcessor` calls `DatabaseService.save_document_data`, which:
   - Upserts OCR text, parsed metadata, and normalized references into `documents`.
   - Generates text chunks and embeddings, persisting them to `document_vectors` through `LlamaIndexVectorService.index_chunks`. Existing vector refs for the document are removed prior to insert.
   - Optionally dual-writes to Qdrant (see Section 2).
4. **Graph ingestion** – When metadata is available, `DocumentService` invokes `GraphIngestionService.ingest_document` (`backend/rbac_backend/graph/graph_ingestion_service.py`) to mirror the document into FalkorDB (Section 3).

### 1.3 Read patterns

- **Document listing** – The REST controllers return paginated `documents` results, feeding the Documents table UI (`client/src/pages/DocumentsPage.tsx`).
- **Vector retrieval** – RAG features (e.g., `/deep-planning/generate-draft`) scan `document_vectors` for matching chunks scoped to the user’s organization/project (`backend/rbac_backend/routers/deep_planning.py`). Results are formatted into prompt context.
- **AI assistant** – `AIService.search_similar_letters` uses full-text style regex queries against `letters` to power “similar letter” suggestions in the letter drafting UI.

### 1.4 Configuration

`DocumentProcessingConfig` (`backend/rbac_backend/config/document_processing_config.py`) resolves Mongo connection details from environment variables, `.env`, or `core.config.settings`. It also controls vector-store toggles (`VECTOR_STORE_ENABLED`) and dual-write behaviour (`DUAL_VECTOR_WRITE`).

## 2. Qdrant Vector Store

### 2.1 Ingestion

`LangChainVectorService` (`backend/rbac_backend/services/langchain_vector_service.py`) manages Qdrant writes when dual-write is enabled. During `DatabaseService._create_and_store_embeddings`:

- Payloads are built per text chunk, carrying the same metadata used for Mongo persistence.
- `replace_document` deletes previous vectors for the `document_id`, then upserts new chunks via LangChain’s `QdrantVectorStore`. Vector IDs are deterministic (`{document_id}:{chunk_index}`) to simplify replacements.

### 2.2 Configuration

Connection parameters (`QDRANT_URL`, `QDRANT_API_KEY`, collection name, vector size, metric) are pulled from `DocumentProcessingConfig`. The service ensures the collection exists with the correct schema before writing.

### 2.3 Consumers

- The production code primarily treats Qdrant as a secondary store. Reads continue to flow from Mongo’s `document_vectors`. A sync utility (`backend/rbac_backend/services/data_sync.py`) can hydrate Qdrant directly from Mongo (via `MongoDBReader`) and mirror data into FalkorDB.
- Additional tooling (e.g., services under `services/langgraph`) reference Qdrant, but the main FastAPI application does not yet issue search queries to Qdrant.

## 3. FalkorDB (Graph)

### 3.1 Ingestion pipeline

`GraphIngestionService` transforms processed document metadata into graph nodes and edges (labels like `Letter`, `Project`, `Clause`, `Party`). It delegates storage to `GraphAdapter` (`backend/rbac_backend/graph/graph_adapter.py`), which talks to the Graphiti service hosted in `services/graphiti/app.py`. Graphiti uses RedisGraph/FalkorDB under the hood.

### 3.2 Data captured

- **Nodes**: documents (with metadata and status), projects, parties, clause references, etc.
- **Relationships**: `BELONGS_TO_PROJECT`, `REFERENCES`, `MENTIONS_CLAUSE`, `REPLIES_TO`, `VERSION_OF`, `APPROVED_BY` and others derived from document metadata.
- **Configuration**: `GraphConfig.from_settings` checks `GRAPHITI_BASE_URL` and `GRAPHITI_API_KEY`. If unset, graph ingestion becomes a no-op, allowing environments without FalkorDB to operate.

### 3.3 Additional entry points

`services/falkordb_vector_service.py` and `services/data_sync.py` provide a lightweight direct Redis client for storing precomputed vectors or text, but the primary graph ingestion path runs through the Graphiti REST bridge to ensure schema management and retries.

## 4. Frontend Consumption

### 4.1 API plumbing

- `client/src/config/api.ts` resolves the REST base URL (Vite env, runtime override, or sensible default) and exposes `joinApiUrl` for consistent endpoint construction.
- `client/src/services/api.ts` wraps Axios, handling JWT refresh and injecting development headers (`X-User-Id`, `X-Org-Id`, etc.) used by backend security stubs.
- `client/src/services/enhanced-api.ts` provides a high-level typed client for documents, letters, AI endpoints, and bulk upload flows.

### 4.2 UI entry points

- **Documents page** (`client/src/pages/DocumentsPage.tsx`): fetches `/documents`, applies client-side filters, and renders metadata pulled from Mongo (letter numbers, dates, tags, references). It also orchestrates organization/project lookups for the letter workflow modal.
- **Letter workflow** (`client/src/hooks/useLetterWorkflow.ts`, `client/src/components/letter-workflow/LetterInitiationForm.tsx`): drives CRUD operations on `/letters`, `/input-requests`, and related endpoints. Selected documents are persisted in `localStorage` (`LETTER_INITIATION_PREFILL_KEY`) to pre-fill new letter drafts with metadata coming from the documents collection.
- **AI features**: components call `enhancedApi` methods such as `searchSimilarLetters`, `generateLetterDraft`, and `deepPlanning`. These backend handlers ultimately pull from Mongo collections (`letters`, `documents`, `document_vectors`) and, when enabled, combine them with OpenAI outputs.
- **LangGraph dashboards** (`client/src/pages/LetterDraftPage.tsx`, `client/src/hooks/useLetterGraphRuns.ts`): display LangGraph run results stored alongside letters. These views consume Mongo-backed snapshots created by `LetterService.record_langgraph_result`.

### 4.3 Client-side state

The webapp keeps lightweight identifiers (selected organization, project, access token) in `localStorage`, aligning requests with backend org/project scoping enforced inside Mongo queries and vector selection.

## 5. Supporting Utilities

- **`backend/rbac_backend/services/data_sync.py`**: batch job that reads Mongo documents, formats payloads, pushes them into Qdrant (`LangChainVectorService`) and FalkorDB (`FalkorDBVectorService`). Useful for initial migrations or backfills.
- **`services/graphiti/app.py`**: standalone FastAPI service deployed alongside FalkorDB to accept higher-level graph commands from the backend. Handles node/edge upserts, temporal queries, and optional OpenAI summarisation for graph episodes.
- **`backend/rbac_backend/core/database.py`**: centralizes Mongo client creation and index bootstrapping to support the query patterns used throughout the application.

---

This document should provide enough context to trace how an uploaded PDF flows from ingestion, through MongoDB persistence, into optional vector stores (Mongo + Qdrant) and graph storage (FalkorDB), and how the frontend surfaces that information for end users.
