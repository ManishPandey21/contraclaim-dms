# ContractDMS Document & Letter Flow Analysis

## System Overview

- React frontend (Documents, Upload, Letter Workflow pages) drives ingestion and drafting workflows, backed by FastAPI services under `backend/rbac_backend`.
- MongoDB stores `documents`, `letters`, and `document_vectors`; vector embeddings are managed through LlamaIndex + MongoDB Vector Search (`backend/rbac_backend/services/llamaindex_service.py`).
- Uploaded files are written to the local `uploads/` tree via `SecureFileService`, then processed asynchronously with OCR, metadata extraction, and embedding generation.
- Optional Graph ingestion publishes processed relationships through `GraphIngestionService` to external graph infrastructure.

## Document Upload Flow

### Frontend submission

- `handleUpload` sequentially posts each selected file with metadata (`client/src/pages/UploadPage.tsx:497`).
- `enhancedApi.uploadDocument` builds multipart form data for `/documents`, including org/project context, metadata, and processing toggles (`client/src/services/enhanced-api.ts:595`).

### FastAPI controller

- `DocumentController.create_document` performs RBAC checks, filename sanitisation, MIME validation, secure storage, and persistence (`backend/rbac_backend/routers/documents.py:67`).
- The request is persisted through `DocumentService.create_document`, which constructs the Pydantic `Document` model and writes it to Mongo (`backend/rbac_backend/services/document_service.py:162`).

### Background processing pipeline

- If OCR/AI processing is enabled, the controller queues a background job via `queue_document_processing` (`backend/rbac_backend/services/document_service.py:404`).
- `DocumentProcessor.process_document` orchestrates OCR (`ocr_service`), OpenAI extraction, optional DocGraph ingestion, and fallback regex parsing (`backend/rbac_backend/services/document_processor.py:45`).
- `_save_results` stores summaries, invokes database updates, and triggers embedding creation (`backend/rbac_backend/services/document_processor.py:171`).

### Persistence & indexing

- `DatabaseService.save_document_data` upserts metadata/ocr text and emits chunk payloads for embeddings (`backend/rbac_backend/services/database_service.py:64`).
- Graph enrichment is performed in `GraphIngestionService.ingest_document`, which projects projects, references, and clause relationships to the graph adapter (`backend/rbac_backend/graph/graph_ingestion_service.py:23`).

### Bulk upload path

- Folder uploads stream files with their relative paths (`client/src/services/enhanced-api.ts:643`).
- `_process_bulk_upload` maps CSV rows to uploaded files (`backend/rbac_backend/routers/documents.py:366`) and reuses `create_document` for each record (`backend/rbac_backend/routers/documents.py:436`).

## Metadata Extraction Flow

- `PydanticAIService.extract_metadata` prompts OpenAI via the Pydantic-AI agent, with configurable model/temperature and a 12k-character guard (`backend/rbac_backend/services/pydantic_ai_service.py:146`).
- Extracted models are serialised to `ParsedDocumentMetadata`; summary, keywords, clauses, and references feed into downstream upserts (`backend/rbac_backend/services/pydantic_ai_service.py:214`).
- When the agent fails or is disabled, `TextProcessingService.parse_extraction_report` provides regex-based parsing for the structured prompt output (`backend/rbac_backend/services/text_processing_service.py:36`).
- `_save_results` writes both AI and fallback results so embeddings index either OCR text or cleaned metadata.

## Letter Drafting Flow

### Entry points

- The Documents page can initiate drafting directly via `handleInitiateLetterFromDocs`, posting normalised payloads to `/letters` (`client/src/pages/DocumentsPage.tsx:951`).
- The dedicated workflow (`useLetterWorkflow`) centralises fetching users/org/projects, stateful letter drafting, and updates through the Axios client (`client/src/hooks/useLetterWorkflow.ts:352`).

### Backend services

- `LetterController.create_letter` enforces rate limits and RBAC before delegating to the service layer (`backend/rbac_backend/routers/letters.py:108`).
- `LetterService.create_letter` persists the draft with audit fields (`backend/rbac_backend/services/letter_service.py:137`), while `create_letter_with_chain` bridges legacy callers (`backend/rbac_backend/services/letter_service.py:521`).
- Conversation features depend on `ConversationService`, which currently returns empty chains and placeholder trees (`backend/rbac_backend/services/conversation_service.py:14`, `backend/rbac_backend/services/conversation_service.py:28`).

### AI assistance

- `useAIAssistant` routes search/draft/metadata calls through `/ai-assistant/*` endpoints (`client/src/hooks/useAIAssistant.ts:19`).
- `AIService.search_similar_letters` and `generate_draft` are stub implementations using simple regex queries and templated responses (`backend/rbac_backend/services/ai_service.py:33`, `backend/rbac_backend/services/ai_service.py:42`).

## Issues & Gaps

1. **Sender metadata dropped on upload**

   - Frontend posts the sender under `"from"` (`client/src/services/enhanced-api.ts:609`) but the FastAPI endpoint only binds `from_` (`backend/rbac_backend/routers/documents.py:1029`). As a result, sender information arrives as `None`, so documents lose provenance in Mongo and downstream embeddings/graph ingestion.
   - _Fix_: align the client to send `from_`; migrate existing records if required.

2. **Structured references silently degraded**

   - Pydantic-AI now serialises references as dictionaries (`backend/rbac_backend/services/pydantic_ai_service.py:217`ï¿½`backend/rbac_backend/services/pydantic_ai_service.py:227`), but the `Document` model still declares `reference: Optional[List[str]]` (`backend/rbac_backend/models/document.py:57`). Pydantic coerces the dicts to their string repr, losing letter number/date structure and undermining graph relationships.
   - _Fix_: promote `reference` (or introduce `referenceDetails`) to store typed dicts, adjust enrichment/graph ingestion, and backfill existing records with structured data.

3. **Bulk upload file matching is path-sensitive**

   - The client submits folder uploads with `relativePath` as the filename (`client/src/services/enhanced-api.ts:643`), while the server expects CSV `filename` columns to match `file.filename` exactly (`backend/rbac_backend/routers/documents.py:366`, `backend/rbac_backend/routers/documents.py:446`). When the CSV lists only basenames (as in the provided template), the lookup fails and rows are marked missing.
   - _Fix_: normalise to `Path(file.filename).name` on the server, update validation messaging, and clarify the template expectations.

4. **Letter conversation & AI support are stubs**
   - Conversation APIs return empty chains/placeholder trees (`backend/rbac_backend/services/conversation_service.py:14`, `backend/rbac_backend/services/conversation_service.py:28`), so UI timelines and linkage features cannot surface real conversations.
   - AI drafting relies on trivial templates (`backend/rbac_backend/services/ai_service.py:42`), providing little value beyond manual drafting.
   - _Fix_: implement actual conversation traversal over `letters` and upgrade AI flows (see LangGraph plan).

## Corrective Action Plan

- Align upload field names and verify with integration tests covering single + bulk ingestion.
- Refactor reference storage (schema migration, reindex graph/vector metadata, update serializers).
- Harden bulk upload matching and extend automated tests with relative-path fixtures.
- Deliver backed conversation queries and richer AI drafting, then update frontend components to consume the enhanced data.
- After code fixes, add regression tests in `test_services.py` / `test_api_endpoints.py` to lock behaviour, and document the workflow in Ops runbooks.

## Docling Integration Proposal

1. **Evaluation** ï¿½ Benchmark Docling parsing on representative PDFs (scanned + digital). Capture accuracy vs current OCR+LLM output and assess latency/cost.
2. **Adapter Layer** ï¿½ Wrap Docling in a service (`docling_service.py`) that exposes: text extraction, structural elements (tables, sections), and metadata hints.
3. **Pipeline Hook** ï¿½ Introduce a configurable branch inside `DocumentProcessor.process_document` to call Docling before OpenAI. Use Docling output for: (a) direct metadata heuristics, (b) richer context passed to Pydantic-AI, (c) chunk generation.
4. **Fallback Strategy** ï¿½ Fall back to existing OCR/OpenAI when Docling confidence is low or encounters unsupported formats. Persist Docling diagnostics for observability.
5. **Rollout** ï¿½ Behind a feature flag in `DocumentProcessingConfig` (`use_docling`), enable in staging, validate vector/graph ingestion, and gradually roll into production.

## LangGraph Integration Proposal

1. **Model the workflow** ï¿½ Identify nodes for retrieval (document context, similar letters), reasoning (metadata summarisation), drafting, and review.
2. **Graph Construction** ï¿½ Build a LangGraph app that orchestrates these nodes with explicit state transitions (e.g., context -> outline -> draft -> QA).
3. **Service Integration** ï¿½ Expose the LangGraph pipeline through a dedicated service (e.g., `langgraph_letter_service`) invoked from `AIService.generate_draft`. Provide hooks for deterministic prompts and auditing.
4. **Human-in-the-loop** ï¿½ Capture reviewer feedback in the graph state so updated drafts can re-enter the pipeline. Surface intermediate artefacts (outline, clause mapping) back to the UI.
5. **Monitoring & Cost Control** ï¿½ Instrument node runtimes and token usage; add guardrails (timeout, budget caps) before enabling for high-volume drafting.

## Additional Recommendations

- Persist background job IDs on documents to expose processing status in the UI and simplify troubleshooting.
- Add coverage for end-to-end ingestion (single + bulk) and regression tests around reference serialisation.
- Expand operational dashboards to track queue depth, metadata failure rates, and vector ingestion lag.
- Update documentation (Upload & Letter workflow guides) once Docling/LangGraph enhancements land.

