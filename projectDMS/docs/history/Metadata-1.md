# Backend Document Processing Pipeline: Upload → OCR → Metadata → MongoDB → Chunking → Vector DB

This document provides a full walkthrough of the backend document processing flow, from upload to OCR, metadata extraction, MongoDB updates, chunking, embeddings, and vector DB storage. It also details the corrections applied to fix logic and consistency issues found during review.

Contents:

- High-level sequence
- Detailed step-by-step flow with file references
- Configuration and feature flags
- Error handling and resilience
- Corrections implemented
- Flow chart (ASCII)
- File responsibility map
- Validation and testing checklist

## 1) High-Level Sequence

1. Client uploads a file to POST /documents (or via bulk upload).
2. File is validated and stored (disk).
3. A document record is created in MongoDB.
4. If OCR is enabled, a background job is queued to process the document.
5. Background processor runs:
   - OCR/Preprocessing: copy or OCR the PDF, attempt text sidecar extraction.
   - OpenAI: upload processed file, extract content.
   - Metadata extraction: PydanticAI (if enabled), otherwise regex-based parser.
   - Save results: write summary to disk, upsert document metadata in MongoDB.
   - Chunk text and create embeddings; write vector chunks to MongoDB; optionally sync to Qdrant (LangChain).
   - Update document with metadata, processing status, and references; ingest into the graph subsystem.
6. Done, awaiting API reads for the enriched data.

## 2) Step-by-Step Flow (Code and Files)

### A. Upload and Routing

- File:

  - backend/rbac_backend/routers/documents.py
    - Endpoints:
      - POST /documents → create_document
      - POST /documents/{id}/process → process_document_endpoint
      - POST /documents/bulk-upload → bulk_upload_documents

- Key class/method:

  - DocumentController.create_document(...)
    - Validates access (AuthorizationService).
    - Validates the file (utils/validation).
    - Stores content on disk via SecureFileService.store_document(...).
    - Creates a Document record via DocumentService.create_document(...).
    - If ocrEnabled flag is true, it queues background processing via DocumentService.queue_document_processing(...).

- File storage implementation:

  - backend/rbac_backend/services/file_service.py
    - class SecureFileService(FileService)
      - store_document(content, organization_id, project_id, filename, path_structure?, compression_enabled?)
      - Uses safe segments to avoid path traversal.
      - Writes to uploads/<org>/<project>/ or provided pathStructure.

- Document creation:

  - backend/rbac_backend/services/document_service.py
    - DocumentService.create_document(...)
      - Builds a Document model (backend/rbac_backend/models/document.py).
      - Determines mime from bytes.
      - Sets default status: Received (incoming) or Sent (outgoing).
      - Inserts a record in db.documents.
      - Emits optional notification for NEW_UPLOAD.

- Optional immediate processing request:
  - POST /documents/{id}/process triggers DocumentController.process_document → DocumentService.process_document_async(...).

### B. Queue and Background Processing

- Queue:

  - backend/rbac_backend/services/document_service.py
    - DocumentService.queue_document_processing(document, file_path) → submit_background_job(...) to run process_document_async

- Background processor:
  - backend/rbac_backend/services/document_service.py
    - DocumentService.process_document_async(document_id, file_path, organization_id?, project_id?, upload_type?)
      - Loads document from MongoDB.
      - Constructs path structure and delegates to DocumentProcessor.process_document(...).
      - Applies DB updates based on processing result.
      - Ingests into graph (GraphIngestionService).
      - Writes processed_path and processing metadata.
      - Sets/clears processing_error.

### C. Document Processor Orchestration

- File:

  - backend/rbac_backend/services/document_processor.py

    - class DocumentProcessor

      - process_document(pdf_path, path_structure, upload_type, document_id?)

        1. Validates file exists and reasonable size.
        2. Step 1: OCRService.process_pdf(input_path)
           - Detects if PDF has text.
           - If text exists: copies original; attempts sidecar text extraction via pdfplumber.
           - Else runs OCR via ocrmypdf with sidecar, returns processed file and raw_ocr_text.
        3. Step 2: OpenAIService.upload_file(processed_pdf_path)
        4. Step 3: OpenAIService.process_document(file_id)
           - Extracts content via OpenAI model/prompt-driven extraction.
        5. Step 4: Metadata extraction:
           - If PydanticAI enabled, attempts structured extraction; on failure/fallback: TextProcessingService.parse_extraction_report(extracted_content).
        6. Step 5: Save results via self.\_save_results(...)
           - FileService.save_summary(...) writes Markdown summaries to uploads/<org>/<project>/incoming|outgoing.md.
           - DatabaseService.save_document_data(document_id, file_path, parsed_metadata, full_text, embedding_text)
             - Upserts metadata to db.documents; creates chunk embeddings and stores vector chunks.

      - \_save_results(...)
        - Chooses text_for_db = raw_ocr_text or extracted_content.
        - embedding_text = raw_ocr_text or parsed_metadata.full_content or extracted_content.
        - Writes summary and persists data + embeddings.

### D. OCR Service

- File:
  - backend/rbac_backend/services/ocr_service.py
  - class OCRService
    - is_pdf_textual(pdf_path): uses PyPDF2 and pdfplumber to detect extractable text.
    - process_pdf(input_path):
      - Creates uploads/process_file directory.
      - If OCR dependencies unavailable → copy file.
      - If text layer detected → copy and optionally create sidecar text.
      - Else → run ocrmypdf. On PriorOcrFoundError, copy; on EncryptedPdfError, fail gracefully.
      - Extract sidecar text via pdfplumber to help embeddings.
    - \_extract_sidecar_text(pdf_path, sidecar_path) returns raw text if extractable.

### E. OpenAI Service

- File:

  - backend/rbac_backend/services/openai_service.py
  - class OpenAIService
    - upload_file(file_path): creates assistants file; retries with exponential backoff.
    - process_document(file_id): chat.completions with special prompt to extract structured text/sections.
    - create_embeddings(texts): vector embeddings via OpenAI embedding model.
    - cleanup_file(file_id): best-effort cleanup; non-fatal on failure.

- Config:
  - Model and embedding model names from DocumentProcessingConfig (openai_model, openai_embedding_model; defaults: gpt-4o, text-embedding-3-small).
  - API key from DocumentProcessingConfig.openai_api_key or environment.

### F. Text Processing and Chunking

- File:
  - backend/rbac_backend/services/text_processing_service.py
    - chunk_text(text): Overlapping chunking algorithm using config.chunk_size and config.chunk_overlap.
    - parse_extraction_report(report): Regex-based extraction for date, letter_no, subject, from_company, to_company, references, summary, keywords, contractual clauses, and full_content.
    - parse_date_safe(...) used when needed (e.g., DatabaseService date normalization).
  - Usage:
    - DocumentProcessor uses parse_extraction_report(...) as fallback.
    - DatabaseService uses chunk_text(...) for payload chunking.

### G. Database Upsert and Vector Store Persistence

- File:

  - backend/rbac_backend/services/database_service.py
  - class DatabaseService
    - get_database(): lazy Motor client; uses config.mongo_uri, config.database_name; tests connection with ping.
    - save_document_data(document_id, file_path, parsed_metadata, full_text, embedding_text) → int
      - \_upsert_document_metadata:
        - Find existing document by document_id or fallback by filename.
        - Updates fields (ocrText, summary, etc.); normalizes references.
        - Date parsed via TextProcessingService.parse_date_safe where possible.
        - Consistency fix applied: uses "from" instead of "from\_".
      - \_create_and_store_embeddings:
        - Uses TextProcessingService.chunk_text to chunk embedding_text.
        - Prepares payloads with checksum_sha256, and metadata including document_id, organization_id, project_id, uploadType, letterNo, filepath_local/s3, chunk_index, source.
        - Dual write (optional): if LangChainVectorService.enabled, syncs Qdrant via replace_document(payloads) for near-real-time vector DB.
        - Backfill bookkeeping:
          - Deletes existing vectors for document_id (both vector service and db.document_vectors).
          - Indexes chunks via LlamaIndexVectorService.index_chunks(payloads).
          - Inserts vector chunk documents into db.document_vectors with embedding, dims, text, tokens, checksum, createdAt.

- Vector Services:
  - .\_get_vector_service() → LlamaIndexVectorService for embeddings/indexing (requires OpenAI API key).
  - .\_get_langchain_vector_service() → LangChainVectorService for Qdrant sync if enabled.

### H. Document Update and Graph Ingestion

- File:
  - backend/rbac_backend/services/document_service.py
  - DocumentService.process_document_async(...)
    - Builds update_fields with processing metadata:
      - ocrEnabled true, processing_metadata: processed_at, processing_time, chunks_created.
    - Updates independent metadata fields if present (summary, keywords, contractual_clauses, reference, full_text, subject, letterNo, from, to, date).
      - Consistency fix applied to ensure independence, not gated by summary presence.
    - Graph ingestion via GraphIngestionService.ingest_document(document_id, document_data, metadata, metadata_source, upload_type).
    - processed_path from result.
    - Sets processing_error to None on success or stores error with timestamp on failure.

## 3) Configuration and Feature Flags

- File:
  - backend/rbac_backend/config/document_processing_config.py
  - DocumentProcessingConfig fields of interest:
    - openai_api_key, openai_model, openai_embedding_model, openai_timeout
    - ai_enabled
    - ocr_language, ocr_enabled
    - uploads_dir, process_dir, max_file_size_mb
    - chunk_size, chunk_overlap
    - mongo_uri, database_name
    - vector_store_enabled, vector_store_collection
    - use_pydantic_ai, pydantic_ai_model
    - qdrant_url, qdrant_api_key, qdrant_collection, qdrant_vector_size, qdrant_distance
    - dual_vector_write (enables LangChain+Qdrant sync)
  - Environment integration:
    - OPENAI_API_KEY, DATABASE_URL or LOCAL_MONGODB_URI
    - QDRANT_URL, QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_VECTOR_SIZE, QDRANT_DISTANCE, DUAL_VECTOR_WRITE
    - Settings override via ..core.config settings when present.

Behavioral controls:

- ai_enabled / openai_api_key: disable AI if not set.
- ocr_enabled: controls OCR pipeline preference (though OCRService auto-detects textual content).
- vector_store_enabled and dual_vector_write toggle vector indexing and Qdrant sync.
- use_pydantic_ai: attempt structured metadata via PydanticAI before fallback regex parser.

## 4) Error Handling and Resilience

- OCRService:

  - Falls back to copying original file if ocrmypdf not available or errors happen.
  - Handles prior OCR and encrypted PDF scenarios.

- OpenAIService:

  - Retries uploads, best-effort cleanup, clear exceptions on processing.

- DocumentProcessor:

  - Catches all exceptions and returns ProcessingResult(success=False, error=...), ensuring caller can update status.

- DatabaseService:

  - Ensures DB connection via ping; raises well-scoped DocumentProcessingError on failures.

- DocumentService.process_document_async:
  - Logs errors, updates processing_error; continues to set updatedAt and other safe fields.

## 5) Corrections Implemented

1. UnboundLocalError risk in process_document_async

   - Before: metadata variable used outside only-success path; could be referenced when result is None or not successful.
   - After: Initialize metadata and metadata_source before try; guards are in place.

   File: backend/rbac_backend/services/document_service.py
   Change:

   - Added at method start:
     - metadata = None
     - metadata_source: Optional[str] = None

2. Incorrect nesting causing missing updates

   - Before: keywords, contractual_clauses, references, full_text, subject, letterNo, from/to, date were nested under if metadata.summary, preventing updates when summary absent.
   - After: These fields are updated independently when present.

   File: backend/rbac_backend/services/document_service.py
   Change:

   - Rewrote the metadata update section to conditionally update each field independently of summary presence.

3. Inconsistent "from" key across services

   - Before:
     - DatabaseService._upsert_document_metadata wrote updates["from_"] = parsed_metadata.from_company.
     - DocumentService.process_document_async wrote update_fields["from"] = metadata.from_company.
     - Document model uses from\_ with alias "from"; Mongo documents conventionally store "from" for REST read consistency.
   - After:
     - Standardized to "from" everywhere for persistence.

   File: backend/rbac_backend/services/database_service.py
   Change:

   - updates["from"] = parsed_metadata.from_company

These changes align DB field names with model aliasing and ensure robust processing without hidden gating.

## 6) Flow Chart (ASCII)

Upload → Store → Create DB → Queue → Background Process → OCR → OpenAI → Metadata → Save/Chunk → Vector DB → Update Doc → Graph

+------------------+ +----------------------+ +----------------------+
| Client Upload | -----> | Router: documents | -----> | SecureFileService |
| POST /documents | | DocumentController | | store_document(...) |
+------------------+ +----------------------+ +----------------------+
|
v
+-----------------------+
| DocumentService |
| create_document(...) |
+-----------------------+
|
v (if ocrEnabled)
+---------------------------+
| queue_document_processing |
+---------------------------+
|
v (background)
+---------------------------+
| process_document_async |
+---------------------------+
|
v
+---------------------------+
| DocumentProcessor |
| process_document(...) |
+---------------------------+
OCR step | OpenAI step | Metadata step
v v v
+-----------+ +-------------+ +----------------+
| OCRService| | OpenAIService| | TextProcessing |
+-----------+ +-------------+ +----------------+
\ | /
\ | /
\ v /
+-----------------------------------------+
| FileService.save_summary(...), |
| DatabaseService.save_document_data(...) |
+-----------------------------------------+
|
v
+-------------------------------+
| Chunking + Embeddings |
| LlamaIndexVectorService |
| LangChainVectorService (opt) |
+-------------------------------+
|
v
+------------------+
| MongoDB updates |
| document_vectors |
+------------------+
|
v
+-------------------------------+
| DocumentService updates doc |
| GraphIngestionService ingest |
+-------------------------------+

## 7) File Responsibility Map

- Routers

  - backend/rbac_backend/routers/documents.py
    - Endpoints for single/bulk upload, downloading, listing, processing trigger.

- Services

  - backend/rbac_backend/services/file_service.py
    - SecureFileService.store_document; FileService.save_summary
  - backend/rbac_backend/services/document_service.py
    - CRUD, background job queue, process_document_async, graph ingestion, comments, linking.
  - backend/rbac_backend/services/document_processor.py
    - Full processing orchestration; OCR → OpenAI → metadata → save + embeddings.
  - backend/rbac_backend/services/ocr_service.py
    - OCR pipeline and sidecar text extraction.
  - backend/rbac_backend/services/openai_service.py
    - Assistants file upload, extraction, embeddings, cleanup.
  - backend/rbac_backend/services/database_service.py
    - MongoDB connection, upsert document metadata, chunking, embedding/vector persistence, Qdrant sync.
  - backend/rbac_backend/services/text_processing_service.py
    - chunk_text, parse_extraction_report, parse_date_safe.

- Config

  - backend/rbac_backend/config/document_processing_config.py
    - Central config and environment variable binding.

- Models
  - backend/rbac_backend/models/document.py
    - Document schema, references, bulk upload models.

## 8) Validation and Testing Checklist

- Upload and store:

  - POST /documents with a PDF, ocrEnabled=true.
  - Verify file saved to uploads/<org>/<project>/filename.pdf.

- Document creation:

  - Confirm db.documents contains new record with filename, uploadType, status.

- Background job:

  - Should enqueue and run process_document_async; inspect logs.

- OCR and OpenAI:

  - If text layer present, file copied and sidecar attempted.
  - If scanned, OCR executed or fallback to copy if OCR not available.

- Metadata:

  - If PydanticAI enabled and configured, metadata_source "pydantic_ai"; else "legacy_regex".
  - Check fields in db.documents: summary, subject, letterNo, from, to, date, keywords, contractual_clauses, reference, full_text.

- Chunking and vectors:

  - db.document_vectors has chunks with embedding, dims, checksum_sha256, text, metadata.
  - If Qdrant enabled, verify logs indicate Qdrant chunk sync via LangChainVectorService.

- Processing status:

  - Document fields: ocrEnabled, processing_metadata, processed_path, processing_error cleared on success.

- Graph ingestion:
  - Logs show GraphIngestionService ingestion attempt; non-fatal on failure.

## 9) Notes and Practical Considerations

- Large files:

  - max_file_size_mb enforced in DocumentProcessor.

- Resilience:

  - Services avoid crashing on best-effort tasks (cleanup, save_summary, sidecar text extraction).

- Consistency:

  - Field names in Mongo now consistent for "from" across services.

- Performance:

  - Chunk size and overlap configurable; dual vector write optional.

- Security:
  - SecureFileService sanitizes path segments; routers handle authorization checks prior to create/processing.

## 10) Code Snippets of Interest (References)

- Queue processing:

  - services/document_service.py → queue_document_processing(...)

- Async processing:

  - services/document_service.py → process_document_async(...)

- Orchestration:

  - services/document_processor.py → process_document(...)

- OCR:

  - services/ocr_service.py → process_pdf, \_run_ocr_with_sidecar, \_extract_sidecar_text

- OpenAI extraction:

  - services/openai_service.py → upload_file, process_document

- Upsert metadata and embeddings:
  - services/database_service.py → save_document_data, \_upsert_document_metadata, \_create_and_store_embeddings

---

Document compiled to provide a single, end-to-end reference of the upload → OCR → AI extraction → metadata → database → chunking → vector storage pipeline and the corrections applied to ensure correctness and consistency.
