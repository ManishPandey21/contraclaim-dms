# Metadata Processing Pipeline Audit

## End-to-end Flow
- **Upload & persistence** – `DocumentController.create_document` stores the uploaded PDF with `SecureFileService.store_document`, persists a MongoDB document, and queues a processing job via `DocumentService.queue_document_processing` (`backend/rbac_backend/routers/documents.py:77`, `backend/rbac_backend/services/document_service.py:456`).
- **Background processing** – The job entrypoint `DocumentService.process_document_async` validates the file, then delegates to `DocumentProcessor.process_document` (`backend/rbac_backend/services/document_service.py:471`, `backend/rbac_backend/services/document_processor.py:45`).
- **OCR & LLM extraction** – `DocumentProcessor` conditionally OCRs the PDF (`OCRService.process_pdf`), uploads it to OpenAI, and extracts structured metadata through the Pydantic agent (falls back to regex parsing when disabled) before persisting results via `_save_results` (`backend/rbac_backend/services/document_processor.py:85`, `backend/rbac_backend/services/document_processor.py:176`).
- **Metadata storage** – `_save_results` writes the human-readable summary to disk and calls `DatabaseService.save_document_data`, which upserts Mongo document metadata in `_upsert_document_metadata` (`backend/rbac_backend/services/database_service.py:96`).
- **Vector storage** – `_create_and_store_embeddings` chunks content, indexes it through `LlamaIndexVectorService.index_chunks`, writes chunk records to MongoDB, and mirrors the payload to Qdrant through `LangChainVectorService.replace_document` when dual-write is enabled (`backend/rbac_backend/services/database_service.py:224`, `backend/rbac_backend/services/langchain_vector_service.py:104`).

## Observations
- Metadata fields (subject, letter number, parties, references, summary, clauses) are normalized before Mongo persistence ensuring consistent structure for downstream consumers (`backend/rbac_backend/services/database_service.py:146`).
- MongoDB vector entries keep bookkeeping information (organization, project, upload type, checksums) to support hybrid search and idempotent re-indexing (`backend/rbac_backend/services/database_service.py:252`).
- Qdrant sync deletes existing chunks by `document_id` prior to inserting the refreshed payloads, so dual-write stores remain consistent across retries (`backend/rbac_backend/services/langchain_vector_service.py:120`).

## Issues & Corrective Actions
1. **PydanticAI dependency gap**  
   - *Finding*: Runtime logs show `PydanticAI library not available (No module named '_griffe')`, disabling the structured extractor.  
   - *Action*: Ensure `griffe==0.48.0` (now listed in `backend/rbac_backend/requirements.txt`) is installed in every deployment image/venv; rebuild the environment or run `pip install -r backend/rbac_backend/requirements.txt`.

2. **Notification service constructor mismatch**  
   - *Finding*: The users controller previously instantiated `NotificationService()` without the required database parameter, producing a 500 during profile lookups.  
   - *Action*: Updated `get_user_controller` to pass the Motor database (`NotificationService(db)`); monitor other call sites to keep usage aligned with the constructor (`backend/rbac_backend/routers/users.py:675`).

3. **Qdrant collection bootstrap risk**  
   - *Finding*: `LangChainVectorService._ensure_collection` calls `recreate_collection` when the initial `get_collection` fails, which would drop vectors if the transient failure occurs on an existing collection.  
   - *Action*: Replace the `recreate_collection` call with a guarded `create_collection` or retry logic so transient connectivity issues do not truncate the Qdrant store (follow-up patch pending).

## Next Steps
- Verify that CI images pick up the refreshed requirements (especially `griffe`) and add a smoke test that asserts `PydanticAIService.is_enabled` remains true in staging.
- Implement the safer Qdrant collection creation guard and add telemetry around dual-write success counts for early detection of divergence.
