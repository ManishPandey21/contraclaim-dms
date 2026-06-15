# Document Upload Process Flow

This document outlines the complete end-to-end flow for uploading documents in the ContractDMS application, starting from the frontend `UploadPage.tsx`, through backend API handling, OCR processing, metadata extraction (including usage of `pydantic_ai_service.py`), upsert to MongoDB, and vector storage. The flow is derived from analyzing key files: `client/src/pages/UploadPage.tsx`, `backend/rbac_backend/routers/documents.py`, `backend/rbac_backend/services/document_service.py`, `backend/rbac_backend/services/metadata_processor_service.py`, `backend/rbac_backend/services/document_processor.py`, `backend/rbac_backend/services/pydantic_ai_service.py`, and `backend/rbac_backend/services/database_service.py`.

## Overview

- **Frontend (React/TS)**: User interacts with `UploadPage.tsx` to select files and metadata, submits via `enhancedApi` to backend API.
- **Backend API (FastAPI)**: `routers/documents.py` validates, stores file locally, creates initial MongoDB record in `documents` collection.
- **Background Processing (Async)**: If OCR enabled, queues job via Celery-like `submit_background_job` to process asynchronously.
- **Processing Pipeline (`document_processor.py`)**: Orchestrates OCR (`ocr_service.py`), content extraction (`openai_service.py`), metadata parsing (AI via `pydantic_ai_service.py` or regex fallback), and storage.
- **Metadata Extraction**: Uses Pydantic-AI Agent with OpenAI for structured output; parses to `ParsedDocumentMetadata`.
- **Storage**: Upserts metadata/full text to MongoDB `documents`; chunks text, embeds (OpenAI), stores vectors in `document_vectors` with LlamaIndex.
- **Bulk Upload**: Similar per-file flow but batched via CSV + folder, with job status tracking.
- **Configs**: Controlled by `document_processing_config.py` (e.g., `use_pydantic_ai`, `vector_store_enabled`, OpenAI keys).
- **Error Handling**: Logs warnings, falls back (e.g., regex if AI fails), stores `processing_error` in doc.

Assumptions: MongoDB with Atlas Vector Search enabled; OpenAI API for extraction/embeddings; Tesseract for OCR. Paths: Files saved to `uploads/{org}/{project}/{filename}`, processed to `process_dir/{hash}.pdf/txt`.

## Detailed Flow

### 1. Frontend: File Selection and Upload (`client/src/pages/UploadPage.tsx`)

- **Initialization**: Fetches organizations (`enhancedApi.getOrganizations()`) and projects (`getProjects()`). Generates path: `${shortenName(org.name)}/${shortenName(project.name)}/` (sanitizes to lowercase, hyphens, <=10 chars).
- **UI Tabs**: Single upload (files), URL (placeholder), Bulk (folder + CSV).
- **Single Upload**:
  - Select files (multiple, PDF/DOC/TXT/images via `<input type="file" accept="...">` or drag-drop).
  - Metadata: `uploadType` ("incoming"/"outgoing"), `letterNo` (auto: `AUTO-${year}-${timestamp}`), `date` (YYYY-MM-DD, defaults today), `subject`, `from`, `to`, `tags`/`subTags` (arrays), `status="draft"`, `ocrEnabled=true`, `compressionEnabled=false`.
  - On submit (`handleUpload`): For each file, `enhancedApi.uploadDocument({file, organization_id, project_id, uploadType, letterNo, date: formatDateForApi(date), subject, from, to, tags, subTags, status, ocrEnabled, compressionEnabled})` → POST `/documents` with FormData (multipart/form-data: file + Form fields).
  - Sequential uploads (one per file). Success: Navigates to `/documentviewer/{firstDocId}`, toast success, reset form.
- **Bulk Upload**:
  - Folder select (`<input webkitdirectory>` or drag-drop with `webkitGetAsEntry`), preserves relative paths (`webkitRelativePath`), filters hidden/system files.
  - CSV metadata (download template via `enhancedApi.downloadBulkUploadTemplate()`), required cols: `filename,upload_type,letter_no,date,subject` (+ optional from/to/tags/etc.).
  - On submit: `enhancedApi.startBulkUpload({csvFile, files: bulkFiles (with paths), organization_id, project_id})` → POST `/documents/bulk-upload`.
  - Polls `/bulk-upload/{job_id}/status` every 3s for `BulkUploadStatus` (processed/total, success/failed, errors).
- **Validation**: Toasts errors (no files/org/project, mixed selection).

### 2. Backend API: Upload Endpoint (`backend/rbac_backend/routers/documents.py`)

- **Endpoint**: `POST /documents` (single) or `/bulk-upload` (batch). Uses `BackgroundTasks`.
- **Authorization**: `get_current_user` → `CurrentUser` (JWT token), `AuthorizationService.check_document_access(user, org_id, project_id, "create")` (RBAC scopes).
- **Single Upload (`create_document`)**:
  - `UploadFile file, Form(organization_id, project_id, uploadType, letterNo, date, subject, from_, to, tags, subTags, status, ocrEnabled, compressionEnabled)`.
  - Validate: `sanitize_filename(filename)`, `validate_file(content, ALLOWED_DOCUMENT_MIMES)` (MIME/size), `parse_date_safely(date)`.
  - Store File: `SecureFileService.store_document(content, org_id, project_id, safe_filename)` → Creates dir `uploads/{org}/{project}/` (via `path_utils.py`), saves file, returns `file_path`.
  - Create Record: `DocumentService.create_document(file_path, filename, org_id, project_id, upload_type, letter_no, parsed_date, current_user, subject, from_, to, tags, sub_tags, status, ocr_enabled)` → See step 3.
  - If `ocr_enabled`: `queue_document_processing(document, file_path)` → Queues async job.
  - Enrich: `enrich_document` (resolve project_name from `projects` coll, tag/subTag names from `tags`/`subtags` cols).
  - Return: 200 OK with enriched `Document` Pydantic model.
- **Bulk Upload (`bulk_upload_documents`)**:
  - `UploadFile csv_file, List[UploadFile] files, Form(org_id, project_id)`.
  - Parse CSV: `_decode_csv_content` (multi-encoding: utf-8-sig/16/cp1252, replace NBSP), `pd.read_csv` (fallback sep=';'), normalize cols to lowercase, validate required cols.
  - Per row (`_validate_csv_row`): Parse fields (upload_type in ['incoming','outgoing'], parse_date, lists from comma-split, bools).
  - Job: UUID `job_id`, insert `BulkUploadStatus` (processing, total=len(files)), background `_process_bulk_upload(job_id, csv_data, files, org_id, project_id, user)` → Per CSV row: Find file by name, call `create_document` (same as single), track success/fail, update progress via `BulkUploadService.update_progress`, complete job (completed/completed_with_errors/failed).
  - Return: `BulkUploadResponse` (job_id, status="processing").
- **Status Endpoint**: `GET /bulk-upload/{job_id}/status` → `get_bulk_upload_status` → `BulkUploadService.get_job_status` (from DB), authorize loosely.

### 3. Initial Document Creation (`backend/rbac_backend/services/document_service.py`)

- `create_document(..., file_path, filename, ...)`: Builds `Document` Pydantic model (filepath_local, filetype=`sniff_mime_from_bytes`, filesize=stat, status="Received"/"Sent"/"draft" based on uploadType, createdBy=user.id/email).
- Insert: `db.documents.insert_one(model_dump(by_alias=True, exclude_unset=True))` → MongoDB `documents` coll, auto `_id=ObjectId`.
- `queue_document_processing(document, file_path)`: `submit_background_job("document-processing", process_document_async, doc.id, file_path, org_id, project_id, uploadType)`.
- `process_document_async`: Loads doc by `_id`, if found: `create_document_processor(config).process_document(pdf_path, f"{org_id}/{project_id}", uploadType, doc_id)` (see step 4).
- Updates doc: If success, `$set` {ocrEnabled=True, processing_metadata={processed_at, time, chunks_created}, subject, summary, keywords, contractual_clauses, reference, full_text, processed_path}; if error, `processing_error={message, timestamp}`. Always `updatedAt=now`.

### 4. Background Processing Pipeline (`backend/rbac_backend/services/document_processor.py` + `metadata_processor_service.py`)

- `MetadataProcessorService.process_document(pdf_path, path_structure, upload_type, doc_id, enable_ocr=True, enable_embeddings=True)`: Wrapper, creates `DocumentProcessor`, calls `process_document`.
- `DocumentProcessor.process_document` (orchestrator):
  1. **Validate**: Exists (`Path(pdf_path).exists()`), PDF suffix, size < `config.max_file_size_mb * 1MB`.
  2. **OCR (`ocr_service.process_pdf`)**: `is_pdf_textual(pdf)` (check text layer via PyMuPDF?). If no: Tesseract OCR (lang=`config.ocr_language`), save processed PDF to `process_dir/{md5(pdf_path)}.pdf`, return `processed_path`, `raw_ocr_text=full extracted text`.
  3. **OpenAI Content Extraction (`openai_service`)**: `upload_file(str(processed_path))` → OpenAI Files API ID. `process_document(file_id)` → Likely GPT-4-Vision/Assistant API call to extract structured text/content (e.g., "Extract all text and metadata"), returns `extracted_content` (JSON/str with sections).
  4. **Metadata Parsing**:
     - Text: `raw_ocr_text or extracted_content`.
     - Primary: If `config.use_pydantic_ai=true`, `pydantic_ai_service.extract_metadata(text, context={filename: basename(pdf_path), upload_type})` (see step 5).
     - Fallback: `text_service.parse_extraction_report(extracted_content)` → Regex/date parsing for subject/date/letterNo/from/to/references/summary/keywords/clauses/full_content → `ParsedDocumentMetadata`.
     - Source: "pydantic_ai" or "legacy_regex".
  5. **Save (`_save_results`)**: `file_service.save_summary(extracted_content, pdf_path, path_structure, upload_type)` → Save full text to `process_dir/{org}/{project}/{filename}.txt`. Then `database_service.save_document_data(doc_id, pdf_path, parsed_metadata, full_text=raw_ocr_text or extracted_content, embedding_text=raw_ocr_text or parsed.full_content or extracted_content)` (see step 6).
- Returns `ProcessingResult(success=True/False, processed_path, metadata, chunks_created, time, source, debug)`.
- Bulk: `process_batch_documents(list[doc_info: {pdf_path, path_structure, ...}], max_concurrent=3)` → Semaphore-limited `asyncio.gather`.

### 5. PydanticAI Metadata Parsing (`backend/rbac_backend/services/pydantic_ai_service.py`)

- **Initialization**: In `DocumentProcessor.__init__`, if `config.use_pydantic_ai` and `OPENAI_API_KEY`: Import Pydantic-AI, create `DocumentMetadataModel` (Pydantic BaseModel: date/str?, subject/str?, letterNo=letter_no/str?, fromCompany=from_company/str?, toCompany=to_company/str?, references=[LetterRef(letter_no/date)], summary_points=[str], summary=summary_text/str?, keywords=[str], clauses=contractual_clauses=[str], full_content/str?; `populate_by_name=True, extra="ignore"`).
- Agent: `pydantic_ai.Agent(OpenAIModel(model=config.pydantic_ai_model or openai_model, api_key), result_type=DocumentMetadataModel, system_prompt="Expert contract analyst... return null if absent", model_settings={temperature=0, max_tokens<=2048, timeout})`.
- **Call (`extract_metadata(document_text, context)`)**:
  - If enabled and text: Trim to <=12k chars.
  - Prompt: "Extract: date/subject/letter number/sender/recipient/references/summary bullet points/keywords/clauses/cleaned full content. Return null if absent." + Context (filename/upload_type) + "Document text:\n{text}".
  - `agent.run(prompt)` → Async OpenAI chat, parses response to `DocumentMetadataModel` (structured JSON via Pydantic-AI).
  - Convert: `ParsedDocumentMetadata(summary="\n".join(f"- {p}" for p in summary_points or [summary_text]) or None, references=map(LetterRef to dict/list), etc.; full_content=text if none)`.
  - Debug: Usage tokens (requests/request/response/total), messages JSON.
  - Returns `MetadataAgentResult(metadata, raw_result=model_dump(by_alias), debug)`.
- Errors: `PydanticAIMetadataError` (AgentRunError/UnexpectedModelBehavior/UserError) → Fallback.
- Storage: `parsed_metadata` fields directly upserted to doc (step 6).

### 6. MongoDB Upsert and Vector Storage (`backend/rbac_backend/services/database_service.py`)

- **Connection**: `AsyncIOMotorClient(config.mongo_uri)[database_name]`, `await db.command("ping")`.
- **save_document_data**: `_upsert_document_metadata` + `_create_and_store_embeddings`.
- **Upsert Metadata (`_upsert_document_metadata`)**:
  - Find: By `_id=ObjectId(doc_id)` or `{"filename": basename(file_path)}` in `documents`.
  - Updates: `{"$set": {ocrText=full_text, updatedAt=now, subject=parsed.subject, letterNo=parsed.letter_no, from_=parsed.from_company, to=parsed.to_company, summary=parsed.summary, reference=parsed.references (list), keywords=parsed.keywords, contractual_clauses=parsed.clauses}}`. Date: `text_service.parse_date_safe(parsed.date)` if present.
  - If exists: `update_one({"_id": oid}, updates)`.
  - If new: `insert_one({filename, filepath_local, createdAt/updatedAt, ...updates})`.
- **Vectors (`_create_and_store_embeddings`)**:
  - If `config.vector_store_enabled`: Chunk `embedding_text` via `text_service.chunk_text` (size/overlap from config).
  - Per chunk: metadata={doc_id=str(oid), org_id, project_id, uploadType, letterNo, filepath_local/s3, chunk_index, source="document_processing"}, checksum=sha256(chunk).
  - Cleanup: `db.document_vectors.delete_many({"document_id": doc_id})`, `vector_service.delete_vectors(existing refs)` (from LlamaIndex).
  - Embed: `LlamaIndexVectorService.index_chunks([{"text":chunk, "metadata":..., "checksum":...}])` → OpenAI embeddings (`config.openai_embedding_model`), stores nodes in MongoDB coll `document_vectors` (Atlas Vector Search index via llamaindex_mongodb).
  - Bookkeeping: `insert_many` to `db.document_vectors`: {\*\*metadata, vector_ref=node.id, embedding_id, embedding_model, embedding_dims=len(vector), embedding=vector (list[float]), text=chunk, num_tokens=len(split), checksum_sha256, createdAt=now}.
  - Returns len(chunks).
- Cleanup: `close_connection` closes client/vector_service.

## Diagrams

### High-Level Flow

```
User (UploadPage.tsx) --> POST /documents --> DocumentController.create_document
  ├── File Store (uploads/{org}/{project}/file.pdf)
  ├── DocumentService.create_document --> INSERT documents {_id, filepath_local, basic metadata}
  └── If ocrEnabled: queue_document_processing --> process_document_async
       └── DocumentProcessor.process_document:
           ├── OCR (ocr_service) --> processed.pdf + raw_ocr_text
           ├── OpenAI (openai_service) --> file_id + extracted_content
           ├── Metadata: pydantic_ai_service.extract_metadata(text) or fallback regex --> parsed_metadata
           └── _save_results:
               ├── file_service.save_summary --> process_dir/.../file.txt (full text)
               └── database_service.save_document_data:
                   ├── _upsert_document_metadata --> UPDATE documents {subject, letterNo, from_, to, summary, reference, keywords, contractual_clauses, ocrText=full_text}
                   └── _create_and_store_embeddings:
                       ├── Chunk text
                       ├── Embed (OpenAI + LlamaIndex) --> vectors in document_vectors coll
                       └── INSERT document_vectors {doc_id, chunk_index, embedding, text, metadata...}
```

### PydanticAI Call Detail

```
extract_metadata(text, context):
├── Trim text <=12k chars
├── Build prompt: System (analyst) + Fields list + Context + "Document text:\n{text}"
├── agent.run(prompt) --> OpenAI chat --> Parse to DocumentMetadataModel (structured)
├── Convert: ParsedDocumentMetadata (join summaries, map refs)
└── Return: metadata + raw (dump) + debug (tokens, messages)
→ Upsert fields to MongoDB documents
```

## Notes & Edge Cases

- **Fallbacks**: OCR if no text; PydanticAI fail → regex; No vectors if disabled.
- **Bulk**: Validates CSV rows, matches filename to files, processes sequentially, tracks in BulkUploadStatus (DB coll?).
- **Security**: Auth/RBAC per org/project; Secure paths/filenames.
- **Performance**: Async background; Chunking for large docs; Token limits (12k text, 2048 output).
- **Errors**: Toast in FE; Log in BE; Continue on partial fail (e.g., no metadata → empty fields).
- **Testing**: Use postman_collection_tags.json for API; test_openai_fix.py for AI issues.

This flow ensures robust document ingestion with AI-enhanced metadata and searchable vectors.
