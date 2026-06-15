# Integration Overview

## Pydantic AI Metadata Extraction

- `backend/rbac_backend/config/document_processing_config.py:37` introduces `use_pydantic_ai` and `pydantic_ai_model`, and pulls overrides from environment via `settings` (`:59-60`). Missing OpenAI credentials disable the flag during config bootstrap (`:66`).
- `backend/rbac_backend/services/pydantic_ai_service.py:24-151` wires the Pydantic AI agent: initialization validates the toggle and API key, defers imports, and constructs an OpenAI-backed agent. `is_enabled` (`:99`) guards execution; `extract_metadata` (`:102-151`) trims text, runs the agent asynchronously, and returns a `MetadataAgentResult` with raw usage diagnostics while normalising failures into `PydanticAIMetadataError`.
- `backend/rbac_backend/services/document_processor.py:42-135` instantiates `PydanticAIService`, attempts extraction when enabled, logs soft failures, and falls back to the legacy regex parser. Successful runs tag the `ProcessingResult` with `metadata_source="pydantic_ai"` and attach agent debug payloads.
- `test_embedding_fix.py:37-104` covers the config surface area and verifies the service defaults to disabled when no configuration is supplied, ensuring deployments without the library or credentials fall back automatically.

## LlamaIndex Vector Store Pipeline

- `backend/rbac_backend/config/document_processing_config.py:35-36` defines `vector_store_enabled` and `vector_store_collection`, with environment overrides at `:57-58`.
- `backend/rbac_backend/services/database_service.py:31-299` maintains a lazy `LlamaIndexVectorService`: `_create_and_store_embeddings` (`:196-272`) honours the `vector_store_enabled` flag, chunks text via `TextProcessingService`, cleans existing vectors, calls `index_chunks`, then persists Mongo documents with embedding metadata. `_get_vector_service` (`:285-299`) injects Mongo coordinates and OpenAI credentials and raises a `DocumentProcessingError` if embeddings cannot be generated safely.
- `backend/rbac_backend/services/llamaindex_service.py:17-169` encapsulates the LlamaIndex integration. It ensures Mongo and OpenAI clients on demand, batches embeddings off the event loop, handles checksum bookkeeping, and exposes `index_chunks`, `delete_vectors`, and `close` for lifecycle management, surfacing operational failures as `DocumentProcessingError` instances.
- `backend/rbac_backend/services/contracts_ingest.py:848-890` reuses the same service for bulk ingestion, constructing a fresh `DocumentProcessingConfig` and skipping embeddings when the vector store or API prerequisites are absent.

## End-to-End Flow

- During document processing, `DocumentProcessor` orchestrates OCR, OpenAI extraction, optional Pydantic AI metadata enrichment, and database persistence. When metadata extraction succeeds, control flows to `DatabaseService.save_document_data`, which triggers the LlamaIndex path whenever vector storage is enabled.
- Both integrations rely on shared OpenAI credentials surfaced by `DocumentProcessingConfig`. Missing credentials disable Pydantic AI automatically and surface clear errors when embeddings are requested without a usable key, keeping fallbacks explicit.

## Observations

- The integration currently lacks automated tests that execute Pydantic AI or LlamaIndex code paths; coverage is limited to configuration sanity (`test_embedding_fix.py`). Stubbing the agent and vector service in unit tests would help guard regressions around prompt wiring and payload shape.
- `DocumentProcessingConfig` disables `use_pydantic_ai` when no API key is present but leaves `vector_store_enabled` true. In environments without embeddings configured, `_get_vector_service` will raise; consider mirroring the automatic disablement behaviour or documenting the requirement explicitly.

### Endpoints Involved

The `LetterWorkflowPage.tsx` file uses the `enhancedApi` service to interact with the backend. The `enhancedApi` service provides various endpoints and methods for interacting with the backend, including:

- Authentication: `/login`
- User Management: `/users`, `/users/{id}`
- Organization Management: `/organizations`, `/organizations/{id}`
- Project Management: `/projects`, `/projects/{id}`
- Document Management: `/documents`, `/documents/{id}`, `/documents/bulk-upload`, `/documents/{id}/request-draft`, `/documents/{id}/complete-draft`
- Party Management: `/parties`, `/parties/{id}`
- Representative Management: `/representatives`, `/parties/{partyId}/representatives`, `/projects/{projectId}/representatives`, `/organizations/{organizationId}/representatives`
- Tag Management: `/tags`, `/tags/{id}`
- Role Management: `/roles`, `/roles/{id}`
- Permission Management: `/permissions`, `/permissions/{id}`
- Profile Management: `/profiles/me`
- Folder Structure Management: `/folder-structure`, `/folder-structure/{id}`
- Email Sending: `/email/send`
- Task Management: `/tasks`, `/tasks/{id}`
- Letter Management: `/letters`, `/letters/{id}`, `/letters/{id}/submit`, `/letters/{id}/approve`, `/letters/{id}/complete`, `/letters/{id}/comment`, `/letters/{id}/assign/{userId}`
- Conversation Tracking: `/letters/{letterId}/conversation-tree`, `/letters/{conversationId}/conversation-summary`
- AI Assistant Methods: `/ai-assistant/search-letters`, `/ai-assistant/generate-draft`
- Deep Planning: `/deep-planning/generate-draft`
