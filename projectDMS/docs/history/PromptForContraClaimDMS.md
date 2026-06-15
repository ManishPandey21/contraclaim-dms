# Prompt for Building ContraClaim DMS SaaS App

You are an expert full-stack developer. Build a complete SaaS Contract Document Management System (DMS) called "ContractForge DMS" inspired by ContraClaim DMS. The app manages construction contract correspondence: upload letters/docs, auto-extract metadata (date, letter no., from/to companies, subject, references, summary, keywords, clauses), search/view, and AI-generate reply drafts using RAG (Retrieval-Augmented Generation) on processed docs. It's multi-tenant (orgs/projects), secure (RBAC, input sanitize), scalable (async jobs).

#### Core Features and Flows

1. **Upload Documents (Single/Multi/Bulk/URL)**:

   - UI (React/TS): UploadPage with tabs: Single (drag/multi select, metadata form: org/project/tag/subtag required, letterNo/date/subject/from/to, toggles OCR/compress default true/false). Bulk (folder drag + CSV metadata parse, template download). URL (input URL, fetch/process).
   - Fix multi-file: Frontend loops individual POST /documents per file (FormData single-file each).
   - Backend (FastAPI): POST /documents (single UploadFile, validate MIME [.pdf/.docx/.txt/.jpg/.png], auth create, sanitize filename, parse date, store SecureFileService (path=orgshort/projshort/year/month if sent, else ID-based). Default status "Received" incoming/"Sent" outgoing. Queue async process if OCR. Bulk: POST /bulk-upload (CSV + files list), parse CSV (pandas required: filename,type,letterNo,date,subject; optional tags), background loop create+process. URL: POST /import-url (fetch aiohttp to bytes, dummy UploadFile to create).
   - Enclosures: POST /documents/{id}/enclosures (separate for attachments).

2. **Metadata Extraction (Async)**:

   - Trigger: After upload if ocrEnabled (background_jobs / Celery).
   - Steps: DocumentProcessor.process_document: Validate. OCR (pdfplumber check text; if not, OCRmyPDF deskew/optimize/eng). OpenAI upload PDF, chat gpt-4o-mini prompt for structured (Date/Letter No./From/To Company/Subject/References bullets/Summary 3-7 bullets/Key Words comma/Clauses comma/Full content), temp=0.1 max=4000. Parse regex to ParsedDocumentMetadata (handle lists/bullets). DB upsert (set fields, ocrText=extracted/full, chunks 3000 chars/200 overlap, embed text-embedding-3-small OpenAI, insert document_vectors checksum). FS: Append summary txt. Error: Set doc.processing_error.
   - Retries: Tenacity 3 attempts exp wait (4-10s) on OpenAI calls (extract/embed).

3. **Letter Drafting (AI RAG)**:

   - UI: DocumentsPage button "Generate AI Draft" modal (subject input, textarea points, generate button). POST /ai-assistant/generate-draft {subject, points, recipient, org_id, proj_id, target_letter_id}.
   - Backend: POST /ai-assistant/generate-draft: Sanitize. Vector search similar (embeddings for RAG). Prompt (templates): Expert Manager 25yrs, construction/commercial law, protect position, mirror style, facts only, JSON {"body": "full draft", "key_points": ["-"], "assumptions": ["-"]}. OpenAI gpt-4o-mini chat with RAG (file_search on vectors/docs), temp=0.1 max=2000. Parse JSON Pydantic (AIDraftOutput), fallback str. Store ai_generated_drafts (body plain, embedding new). Response: LetterDraftResponse (subject, body, key_points list, assumptions list).
   - Fix: JSON output validation with Pydantic, add assumptions.

4. **Search & View**:
   - DocumentsPage: Table list (date/letterNo/dir/from/to/subject/tag/subTag/status/project, filter search/status/dir/tag, paginated, sort). Status badges (Under Process: spinner, Processing Error: alert). Poll status every 30s if any "Under Process" (fetch list). Actions: View/download, delete, request-draft (status "Under Process"), generate AI, view refs.
   - ReferencePage: Search keyword/tag, list similar (filter, paginated).
   - Viewer: /documentviewer/{id} (PDF render, metadata, enclosures, refs).
   - Workflow: /documents/{id}/request-draft (set status="Under Process"), /complete-draft ("Replied").

#### Tech Stack

- **Frontend**: React 18, TypeScript, Tailwind, shadcn/ui, Vite. State: React Query for API (enhanced-api.ts with token/refresh). Components: Table/Badge/Dialog/Input/Textarea. Auth: localStorage token, intercept 401 refresh. Routes: /upload, /documents, /reference/{id}.
- **Backend**: Python 3.11, FastAPI, MongoDB (Motor async). Pydantic models (Document, LetterDraftRequest/Response with pathStructure/compressionEnabled). Services: DocumentService (CRUD/queue), FileService (store with path/compress: gzip text, placeholder PDF PyMuPDF/OCRmyPDF optimize), OpenAIService (chat/embed retry tenacity). Background: asyncio.create_task or Celery. Config: .env (OpenAI keys, Mongo URL, uploads_dir).
- **DB**: Mongo (documents: metadata/status, document_vectors: embedding/chunk, ai_generated_drafts). Index: text on subject/letterNo, vector on embeddings.
- **Dependencies**: aiohttp (URL fetch), pandas (CSV), OCRmyPDF/pdfplumber (OCR), python-dateutil (date parse), tenacity (retries). Pip install.
- **Security**: JWT auth, RBAC (check_access create/read), sanitize inputs (filename no ../, escape), MIME validate. Tenant isolate (org/project filter).
- **Error/UI**: Toasts (success/error), loading/spinner, fallback (dateutil parse, no compress if fail). E2E test: upload → extract → draft.

#### Implementation Steps

- Project structure: backend (FastAPI routes/services), frontend (src/pages/components), shared models.
- Fix Gaps: Multi-file loop in frontend, bulk impl, compression in FileService, pathStructure custom dirs, async notify (poll status), JSON validation in drafting.
- Best Practices: Logging, exceptions (custom DocumentError), docs strings, tests (pytest unit/E2E).
- Output: Full code repo (PyCharm/VSCode ready), Dockerfile, README setup.

Generate the complete app code.
