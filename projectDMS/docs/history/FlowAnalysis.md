# Flow Analysis: Document Upload, Metadata Extraction, and Letter Drafting

## Overview

This analysis covers the end-to-end flows in the ContraClaim DMS repository for:

- **Uploading documents**: Frontend UI to backend storage and initial processing trigger.
- **Metadata extraction**: Async OCR, AI parsing, and embedding generation.
- **Letter drafting**: AI-assisted generation with RAG on similar documents.

**Architecture Summary**:

- **Frontend**: React/TS (UploadPage.tsx for upload, ReferencePage.tsx for refs, likely DocumentsPage.tsx for drafting trigger).
- **Backend**: FastAPI (routers/documents.py, services/document_service.py, document_processor.py with sub-services).
- **AI**: OpenAI for extraction/drafting (gpt-4-turbo-preview, text-embedding-3-small).
- **DB**: MongoDB (documents, document_vectors, ai_generated_drafts).
- **Storage**: FS (uploads/process_file), S3 placeholders.
- **Triggers**: Upload queues background jobs; drafting on API request.

No broken links found (searches yielded no invalid URLs). Coding issues are mostly gaps/unimplemented features.

## Upload Flow

### Frontend (client/src/pages/UploadPage.tsx)

- **UI Components**: Tabs (Upload single/batch, From URL placeholder, Bulk placeholder).
- **Selections**:
  - Organization/project (dropdowns, filters projects by org).
  - Upload type: incoming/outgoing.
  - Metadata: letterNo, date (defaults today if invalid), subject, from, to.
  - Tags/subTags (required, loaded via API).
  - Files: Multiple select (.pdf/.docx/.txt/.images), queue display with remove.
  - Toggles: ocrEnabled (true), compressionEnabled (false).
- **Path Computation**: Shortens org/project names (lowercase, hyphens, max 10 chars) → pathStructure e.g., "acme-proj/2025/10/".
- **Validation**: Files/org/project/tags/subtags required; date parse fallback.
- **Submit (handleUpload)**:
  - FormData: Repeats 'file' for each, adds metadata + pathStructure/pathStructure1 + toggles ("1"/"0").
  - Headers: Bearer token from localStorage.
  - Loops POST /documents per file (sequential, but doc notes mismatch).
  - Success: Toast, navigate to /documentviewer/{first_id}.
  - Error: Toast for missing/HTTP errors.
- **Bulk/URL**: UI placeholders (folder drag/select + CSV template download, but inert; no backend call impl).
- **Enclosures**: From DocumentViewer (EnclosuresPanel.tsx), POST /documents/{id}/enclosures.

### Backend (backend/rbac_backend/routers/documents.py + services/)

- **Endpoint**: POST /documents (multipart/form-data).
- **Params**: file (UploadFile, single), organization_id/project_id/uploadType/letterNo/date (req), subject/from/to/tags/subTags/status (opt, default "draft"), ocrEnabled ("1"/"0").
- **Steps**:
  1. Auth: AuthorizationService.check_document_access(user, org, project, "create").
  2. Validate: Filename, MIME (ALLOWED_DOCUMENT_MIMES), content (no malware).
  3. Sanitize: Filename, parse date (parse_date_safely).
  4. Store: SecureFileService.store_document(content, org_id, project_id, filename) → uploads/org/proj/filename.
  5. Create: DocumentService.create_document → Insert to Mongo (set filepath, filetype=sniff_mime, filesize, status="Received"/"Sent" by type, tags/subTags).
  6. If ocrEnabled: submit_background_job("document-processing", process_document_async(doc.id, filepath, org, proj, type)).
  7. Return: Enriched Document (project_name, tag/subtag names resolved).
- **Other Endpoints**:
  - GET /documents/{id}: Enrich (resolve names), auth "read".
  - GET /documents: Filter by org/proj/tags/type/status, paginated, auth-aware.
  - PUT /documents/{id}: Update, auth "update".
  - DELETE /documents/{id}: Soft delete, cleanup FS, auth "delete".
  - POST /documents/{id}/enclosures: Add attachment (ALLOWED_ENCLOSURE_MIMES), update enclosures array.

### Sequence (Happy Path)

1. User → UploadPage: Select metadata/files → POST /documents (multi-file issue).
2. Backend: Auth/validate/store/create → Queue async if OCR.
3. Navigate to viewer; add enclosures if needed.

## Metadata Extraction Flow

- **Trigger**: Background job post-upload (queue_document_processing → process_document_async).
- **Entry**: DocumentService.process_document_async → DocumentProcessor.process_document(pdf_path, path=org/proj, type, doc_id).

### Steps in DocumentProcessor (services/document_processor.py)

1. **Validate**: File exists, <100MB.
2. **OCR (OCRService)**:
   - Check text layer (PyPDF2/pdfplumber, first 5 pages).
   - If scanned/no text: OCRmyPDF (eng lang, deskew/optimize/force, jobs=2) → processed PDF.
   - Always: Extract sidecar.txt (pdfplumber all pages).
   - Fallback: Copy original if OCR error/unavailable.
   - Output: processed_path (process_file/dir), raw_ocr_text.
3. **OpenAI (OpenAIService)**:
   - Upload PDF to OpenAI Files (purpose="assistants", retries=3, exp backoff).
   - Chat: model=gpt-4-turbo-preview, prompt for structured fields (1-10: Date, Letter No., From/To Company, Subject, References (bullets), Summary (3-7 bullets), Key Words (comma), Contractual Clauses (comma), Full content). Attach file, max_tokens=4000, temp=0.1.
   - Delete file post-use.
   - Output: extracted_content (report text).
4. **Parse (TextProcessingService)**:
   - parse_extraction_report: Regex/multi-pattern extract fields (e.g., r"^\s*Date\s*[:\-]\s\*(.+)$").
   - Lists: \_parse_list_block (strip bullets/commas, dedup).
   - Summary: Bullet format.
   - Filter "Not found".
   - Date: parse_date_safe (multi formats: %Y-%m-%d etc.).
   - Output: ParsedDocumentMetadata dataclass.
5. **DB (DatabaseService)**:
   - Upsert documents: Set ocrText=full*text (raw_ocr or extracted), metadata fields (subject, letterNo, from*, to, summary, reference, keywords, contractual_clauses, date parsed).
   - Chunk: text → chunks (3000 chars, 200 overlap).
   - Embeddings: OpenAI embeddings.create (batch), delete old vectors, insert document_vectors (doc_id, org, proj, chunk_index, text, embedding, checksum).
   - Output: chunks_created count.
6. **FS (FileService)**: save_summary → Append to uploads/org/proj/incoming.md or outgoing.md (Markdown source heading + extracted content + divider).
7. **Graph**: GraphIngestionService.ingest_document (payload + metadata, for refs graph; errors logged silently).

- **Error Handling**: ProcessingResult (success/error/time), set processing_error in doc if fail. Logs everywhere.
- **Config**: DocumentProcessingConfig (uploads_dir="uploads", process_dir="uploads/process_file", chunk_size=3000, etc.; from env/settings).

### Sequence (Happy Path)

1. Async trigger → Validate/OCR → OpenAI upload/extract → Parse → DB upsert/embed → FS summary → Graph.
2. Doc updated: metadata/full_text/processing_metadata; vectors ready for RAG.

## Letter Drafting Flow

- **Frontend**: Assumed from DocumentsPage/ReferencePage (search similar → trigger draft). POST /ai-assistant/generate-draft with LetterDraftRequest (subject, recipient, points (bullets), organization_id/project_id, target_letter_id optional).
- **Backend (routers/ai_assistant.py / deep_planning.py)**:
  - Endpoint: POST /ai-assistant/generate-draft → AIAssistantController.generate_letter_draft.
  - Sanitize: validate_input (escape, max_len), build LetterDraftRequest.
  - If target_id: Fetch target content for reply context.
  - **Generate (generate_draft_with_ai)**:
    - Similar letters: Vector search on embeddings (llamaindex_service? → top matches).
    - Key points: If deep-planning, extract from input_requests.
    - Prompt: templates.py LETTER_DRAFT_PROMPT_TEMPLATE (Expert Manager, 25yrs exp, protect position, mirror style from recent letters, facts only, no fabricate, quality gate).
      - Includes: Subject, recipient, points, similar refs (content), target if reply.
    - OpenAI: Chat.completions.create (gpt-4-turbo, temp=0.1) or Assistants (with file_search RAG on proj docs).
    - Fallback: If error, basic template or generate_fallback_draft (simple "Dear {recipient}, ...").
  - Store: ai_generated_drafts.insert (subject, content=plain text, embedding=new, generated_by, org/proj, generated_at).
  - Response: LetterDraftResponse (subject, body=draft, key_points, similar_letters list).
- **Deep Planning Variant**: /deep-planning/generate-draft → Extracts key_points from docs, enriches prompt.
- **Refs Integration**: ReferencePage.tsx searches docs by keyword/tag (GET /documents filter), lists with metadata (letterNo/subject/date), link to viewer. Draft uses refs via RAG.
- **Workflows**: /documents/{id}/request-draft (set status="Under Process"), /complete-draft ("Replied"); stores drafts but no finalize UI.

### Sequence (Happy Path)

1. User → API POST (subject/points) → Sanitize/fetch context/similar → Prompt build → OpenAI generate → Store → Return draft.

## Issues and Gaps

- **Functional**:
  - Upload multi-file: Frontend repeats 'file', backend single UploadFile → Only first processes; others lost (gap from Upload_Documents_Process.md).
  - Bulk/URL: Frontend UI (folder drag/CSV download), but no backend (startBulkUpload returns mock job_id; inert).
  - Compression: Toggled/sent ("1"/"0"), but ignored in backend/file_service.
  - pathStructure: Computed/sent (with year/month), but SecureFileService uses default org/proj (no subdirs).
  - Drafting: No explicit frontend integration (modal?); assumes plain text, no HTML render; no output validation (hallucination risk).
- **Coding**:
  - Unhandled: OpenAI rate limits (retries in upload, not extract); graph_ingestion fails silently.
  - Date Parse: Limited formats (add dateutil?).
  - Embeddings: Empty text → 0 chunks (ok, but warn?).
  - Errors: Async jobs don't notify (status="processing_error", but no UI poll). Frontend debug console.logs, toasts for warnings.
  - Dependencies: OCRmyPDF/pdfplumber/motor/openai may fail if missing (log warnings).
  - Performance: Large PDFs timeout risk (180s); chunking fixed but overlap=200 may miss context.
  - Security: Good (auth/MIME/sanitize), but AI prompts could be jailbroken (no filter).
- **Broken Links**: None. Searches for 'http' showed valid (OpenAI docs, pytest, CSS drafts).
- **Other**: Status defaults ("draft" → "Received"/"Sent"); no S3 impl; tests cover but no E2E.

No critical bugs/deprecations in core; mostly unimplemented (bulk) and mismatches (multi-file).

## Corrective Actions Plan

Implement via code edits/tests. Test: pytest, browser launch for UI.

### Priority 1: Core Upload/Processing

- Fix multi-file: UploadPage.tsx handleUpload → Loop individual POST /documents (enhancedApi.uploadDocument per file). Backend optional: Form(..., file: List[UploadFile]=File(...)).
- Compression: file_service.py → Add compress_pdf (PyPDF2 reduce/OCRmyPDF optimize) before store if enabled.
- pathStructure: SecureFileService.store_document → Parse year/month from date, join pathStructure + subdirs.
- Errors: DocumentsPage → Poll doc status (setInterval GET /documents/{id}), toast if processing_error.

### Priority 2: Bulk/Drafting

- Bulk Backend: routers/documents.py → POST /bulk-upload: Parse CSV (pandas), zip metadata+files, background loop (create_document + process per). Return job_id, endpoint GET /bulk/{job_id} (Celery? Simple async).
- URL Import: POST /import-url → aiohttp download to temp, then upload_as_file.
- Drafting UI: DocumentsPage → Add button/modal for generate-draft, integrate ReferencePage similar_letters.
- RAG Enhance: pydantic_ai_service → Add output validator (Pydantic for structure).

### Priority 3: Polish

- Retries: OpenAIService → @tenacity.retry on rate limits.
- Date: TextProcessingService.parse_date_safe → dateutil.parser fallback.
- Tests: Add E2E (upload → extract → draft search). Run pytest backend/rbac_backend/tests/.
- Deprecs: Update venv libs if warnings (pip install --upgrade).

**Next**: Implement P1 in code, test, create this MD. Estimated: 1-2h per priority.

Generated: 2025-10-02
