# Deep Audit and Product Manual: ManishPandey21/contraclaim-dms

Audit date: 2026-06-15  
Repository: `ManishPandey21/contraclaim-dms`  
Default branch reviewed: `main`  
Latest reviewed commit: `34459faea300bf7c68d6dae0f433dc2ae5bb9497`

## Executive Summary

`contraclaim-dms` is a substantial contract document management and construction correspondence platform. It is not a simple upload portal. The repository contains a FastAPI backend, a React/Vite frontend, MongoDB persistence, Redis runtime/queue state, Qdrant vector search, optional FalkorDB/Graphiti graph features, OCR, OpenAI-based metadata extraction, contract clause ingestion, RAG endpoints, AI-assisted contract Q&A, and a letter drafting workflow.

The strongest areas are the backend architecture, tenant-aware document model, modern cookie/CSRF authentication, runtime configuration validation, document processing queues, contract ingestion/search, CI checks, and Docker production scaffolding. There is real product depth in files such as `backend/rbac_backend/main.py`, `backend/rbac_backend/routers/documents.py`, `backend/rbac_backend/routers/contracts.py`, `backend/rbac_backend/retrieval/service.py`, `backend/rbac_backend/services/letter_drafting/service.py`, `client/src/routes.tsx`, `client/src/pages/UploadPage.tsx`, and `client/src/pages/ContractQAPage.tsx`.

The product is not yet enterprise-production ready. Several important flows are partial or inconsistent:

- Frontend route permissions in `client/src/config/rolePermissions.ts` do not consistently match backend canonical permissions in `backend/rbac_backend/core/permissions.py`.
- `backend/rbac_backend/services/authorization_service.py` explicitly contains permissive fallback behavior, while newer routes use stricter `PolicyService`.
- `backend/rbac_backend/routers/projects.py` exposes `/projects1`, which returns projects without the same scope filtering used by `/projects`.
- The old advanced search router in `backend/rbac_backend/routers/search.py` queries fields such as `name`, `content`, `categories`, and `size`, while the active `Document` model primarily stores `filename`, `full_text`, `tags`, `subTags`, and `filesize`.
- Bulk upload has a total-size validation bug and a status shadowing bug in `backend/rbac_backend/routers/documents.py`.
- The document viewer uses an external PDF.js worker from cdnjs in `client/src/components/document-viewer/DocumentViewer.tsx`, while `config/httpd.conf` uses a production CSP that allows only self/blob workers. This can break PDF preview in production.
- Upload "From URL" and cloud storage buttons in `client/src/pages/UploadPage.tsx` are UI only.
- Docker/deployment documentation still describes optional services that are disabled or not deployed by compose.

Production readiness score: **5.8 / 10**.

This is suitable for a controlled internal pilot or staging deployment with trusted users. It needs security hardening, RBAC cleanup, workflow QA, search/RAG consolidation, deployment validation, and enterprise audit/reporting work before positioning as a production construction claims or contract DMS platform.

## Repository Overview

### Top-Level Structure

Evidence from `README.md`, `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, and CI:

| Area | Location | Purpose |
|---|---|---|
| Backend API | `backend/rbac_backend/` | FastAPI API, auth, RBAC, documents, contracts, letters, AI/RAG, reports, admin |
| Frontend app | `client/` | React 18/Vite/TypeScript UI |
| Docker orchestration | `docker-compose.yml`, `docker-compose.prod.yml` | Local and production-style service stack |
| Reverse proxy | `config/httpd.conf` | Apache proxy and security headers |
| Deployment script | `scripts/deploy.sh` | Runs `docker compose --env-file .env pull` and `up -d --build` |
| CI | `.github/workflows/ci.yml` | Secret scan, backend lint/tests, frontend lint/tests/build, dependency audit, Docker image scan |
| Optional services | `services/graphiti`, `services/docling` references | Graphiti optional profile, Docling noted as not deployed in `.env.example` |

### Backend Overview

`backend/rbac_backend/main.py` wires the backend as `ContractDMS` and includes these routers under `/api`:

- Auth/session: `auth`
- Users/profiles: `users`, `profiles`
- Documents/DMS: `documents`, `folder_structure`, `storage_sync`, `storage_settings`
- Contracts/RAG: `contracts`, `retrieval_engine`, `search`, `ai_assistant`, `deep_planning`
- Letters/drafting: `letters`, `letter_drafting`, `input_requests`, `letter_templates`
- Admin/domain data: `organizations`, `projects`, `roles`, `permissions`, `tags`, `tasks`, `parties`, `representatives`, `concerns`
- Notifications/email: `notifications`, `email_groups`, `email_share`, legacy email
- Monetization/subscriptions: `rbac_monetization`
- Reporting/observability: `reports`, `dashboard`, `performance`, health endpoints

Startup logic in `main.py` validates runtime config, starts background services, starts contract queue workers when enabled, and schedules daily/weekly notification digest jobs.

### Frontend Overview

`client/src/routes.tsx` defines a full application shell with public `LandingPage` and `LoginPage`, then protected routes for:

- `/overview`, `/dashboard`
- `/organizations`, `/projects`, `/users`, `/permissions`, `/settings`
- `/upload`, `/documents`, `/documentsearch`, `/documentviewer/:id`
- `/contracts`, `/contracts/upload`, `/contracts/search`, `/contracts/qa`
- `/letters`, `/letter-quality`, `/letter-summary`, `/letter-templates`
- `/reports`, `/tags`, `/tasks`, `/folders`, `/share`, `/reference`
- `/notifications`, `/health`

Authentication and CSRF behavior is implemented in `client/src/services/http.ts`, `client/src/services/api.ts`, `client/src/services/auth.ts`, `client/src/services/session-api.ts`, and `client/src/hooks/use-auth.ts`.

## Architecture Review

### Backend Architecture

The backend is service-oriented inside a single FastAPI application. It separates routers, services, models, and core infrastructure:

- API composition: `backend/rbac_backend/main.py`
- Runtime config: `backend/rbac_backend/core/config.py`
- Database setup and indexes: `backend/rbac_backend/core/database.py`
- Auth and request identity: `backend/rbac_backend/core/security.py`
- CSRF: `backend/rbac_backend/core/csrf.py`
- Policy enforcement: `backend/rbac_backend/services/policy_service.py`
- Entitlements/subscriptions: `backend/rbac_backend/services/entitlement_service.py`
- Documents: `backend/rbac_backend/routers/documents.py`, `backend/rbac_backend/services/document_service.py`
- Contract ingestion/search: `backend/rbac_backend/routers/contracts.py`, `backend/rbac_backend/services/contract_service.py`, `backend/rbac_backend/services/contracts_ingest.py`
- Retrieval/RAG: `backend/rbac_backend/routers/retrieval_engine.py`, `backend/rbac_backend/retrieval/service.py`
- Letter drafting: `backend/rbac_backend/routers/letter_drafting.py`, `backend/rbac_backend/services/letter_drafting/service.py`

Positive architecture points:

- `Settings.validate_runtime_configuration()` rejects unsafe production configuration such as placeholder secrets, insecure cookies, localhost MongoDB, dev CORS origins, missing metrics token, missing Redis URLs, and insecure antivirus fail-open when antivirus is enabled.
- `core/security.py` supports HttpOnly cookie auth, bearer token fallback, JWT session invalidation via Redis, session checks, scoped query helpers, permission dependencies, and role assignment validation.
- `core/csrf.py` implements double-submit CSRF protection for unsafe methods when auth cookies are used.
- `core/database.py` creates many indexes for users, documents, file objects, document versions, contracts, letters, audit events, notifications, RBAC monetization, retrieval, conversations, and RAG runs.
- `documents.py` and `contracts.py` use storage provider services, spooled uploads, MIME sniffing, path checks, and optional antivirus scanning.
- `retrieval_engine.py` has strong scope checks through `_authorize_scope()` and `PolicyService`.

Main architecture concerns:

- There are two authorization layers: newer `PolicyService` and older `AuthorizationService`. `AuthorizationService` states that it intentionally errs on the side of allowing requests while RBAC is rebuilt. That is unsafe for production unless every sensitive route is proven to use `PolicyService` or strict checks.
- There are two search/RAG layers: old `/search/*` and newer `/v1/retrieval/*`. The newer layer is better scoped and more aligned with RAG; the older layer is partially stale.
- Some routers are visibly transitional. `backend/rbac_backend/routers/roles.py` starts with `routers/roles_simple.py`, duplicate imports, and comments saying it focuses on basic CRUD to get the UI working. `backend/rbac_backend/routers/letters.py` has many blank lines and legacy-style comments.
- Deployment docs and compose files do not fully align. `.env.example` says Docling is not deployed. README references LangGraph at `/langgraph`, but the reviewed compose stack does not show a deployed LangGraph service by default.

### Frontend Architecture

The frontend is a React/Vite SPA with:

- Routing in `client/src/routes.tsx`
- API wrappers in `client/src/services/*`
- Auth hooks in `client/src/hooks/use-auth.ts` and `client/src/hooks/useRBAC.ts`
- DMS UI in `UploadPage.tsx`, `DocumentsPage.tsx`, `EnhancedDocumentsPage.tsx`, and `DocumentViewerPage.tsx`
- Contract UI in `ContractsUploadPage.tsx`, `ContractsSearchPage.tsx`, and `ContractQAPage.tsx`
- Letter workflow UI in `LetterWorkflowPage.tsx` and `useLetterWorkflow.ts`

Positive frontend points:

- Uses HttpOnly cookie-oriented auth rather than storing bearer tokens in localStorage.
- Adds CSRF headers through fetch and Axios helpers.
- Protects routes with `ProtectedRoute` and `useRBAC`.
- Has user-facing workflows for single upload, folder/CSV bulk upload, document viewer, metadata editing, references/enclosures, contract upload, contract search, contract Q&A, reports, and letter workflow.

Frontend concerns:

- Frontend route permissions do not fully match backend canonical permissions.
- Several UI controls are not wired to backend behavior, including Upload from URL/cloud buttons and bulk upload option checkboxes.
- Some pages use browser localStorage for organization/project context (`org_id`, `proj_id`) which can create stale scope selections.
- `EnhancedDocumentsPage.tsx` calls `performSearch(1)` immediately after `setFilters(newFilters)`, so it can search with stale filters.
- `DocumentViewer.tsx` relies on a CDN PDF worker that can be blocked by the production CSP.

### Database Review

Primary database: MongoDB through Motor.

Collections evidenced in code include:

- `users`, `organizations`, `projects`, `roles`, `permissions`
- `documents`, `file_objects`, `document_versions`, `document_audit_events`
- `document_vectors`, `contract_ingest_jobs`, `contract_upload_sessions`
- `contracts`, `contract_versions`
- `letters`, `letter_draft_runs`, `letter_templates`, input requests
- `notifications`, `project_notification_subscriptions`, email logs/share tokens
- `subscriptions`, plans, plan settings, expert allocations
- `rag_runs`, conversation collections

Database positives:

- Broad index creation in `core/database.py`.
- Production MongoDB validation requires replica set unless explicitly allowed.
- Document update path supports optimistic locking through revision headers in `documents.py`.
- Contract upload sessions use TTL indexes in `ContractService._get_sessions()`.

Database concerns:

- Index creation failures are logged and retried but the app can continue in a degraded state.
- Some IDs are stored as strings and some as ObjectIds; helper code repeatedly handles both. This raises query consistency and indexing risk.
- Dashboard aggregation pulls all matching documents into memory before counting in `dashboard.py`; this will not scale for large tenants.
- Old search indexes are created inside request handling in `search.py`.

### Authentication Review

Implemented in:

- `backend/rbac_backend/routers/auth.py`
- `backend/rbac_backend/services/authentication_service.py`
- `backend/rbac_backend/core/security.py`
- `backend/rbac_backend/core/csrf.py`
- `client/src/services/http.ts`
- `client/src/services/auth.ts`
- `client/src/hooks/use-auth.ts`

Login flow:

1. `POST /api/login` accepts email/password.
2. Backend checks IP/email rate limits, authenticates user, creates a session, creates JWT, sets HttpOnly auth cookie, and sets a readable CSRF cookie.
3. Frontend uses cookies with `withCredentials` and sends `X-CSRF-Token` on unsafe requests.
4. `GET /api/me` returns the current profile.
5. `POST /api/refresh` refreshes the cookie session.
6. `POST /api/logout` invalidates the session.
7. `POST /api/step-up` issues a short-lived step-up token for sensitive actions.

Auth positives:

- HttpOnly cookie support.
- CSRF origin and token validation.
- Redis session support.
- Rate limiting by IP and email.
- Step-up tokens for destructive/admin actions.
- JWT invalidation support.

Auth concerns:

- `AuthController.login_user` uses a fixed 60-minute access token duration instead of `settings.ACCESS_TOKEN_EXPIRE_MINUTES`.
- Invalid password attempts in the primary `auth.py` login path do not appear to call `increment_failed_attempts`, so account lockout logic in `AuthenticationService` may not activate for that route. The older `users.py` auth flow does increment attempts, but the primary app includes `auth.py`.
- If Redis is unavailable, sessions/rate limits fall back to in-memory state. That is acceptable for development but not clustered production.

### RBAC and Entitlement Review

Implemented in:

- `backend/rbac_backend/core/permissions.py`
- `backend/rbac_backend/services/policy_service.py`
- `backend/rbac_backend/services/permission_service.py`
- `backend/rbac_backend/services/authorization_service.py`
- `backend/rbac_backend/services/entitlement_service.py`
- `client/src/config/rolePermissions.ts`
- `client/src/hooks/useRBAC.ts`

RBAC positives:

- Canonical permissions exist for DMS, drafting, billing/subscriptions, and system/admin functions.
- `PolicyService` checks permission, tenant scope, and subscription entitlements.
- `EntitlementService` models trial, pilot, active, grace, suspended, archived, canceled, expired, and offboarding subscription states.
- Role mutation routes use step-up in `roles.py`.
- Document delete uses step-up and policy checks.

RBAC concerns:

- Frontend permissions include names not present in backend canonical permission constants or aliases, for example `admin.user.create`, `draft.request.view`, `dms.document.share`, `dms.folder.view`, and `settings:view`.
- Backend canonical names include `drafting.request.view`, `dms.document.view`, `dms.document.upload`, `dms.report.view`, and other `drafting.*` names.
- `PermissionService.build_permission_matrix()` returns an empty placeholder, so a permissions matrix UI would be incomplete.
- `AuthorizationService` has permissive fallback behavior by design.
- `/projects1` in `projects.py` returns projects without the same scope filtering as `/projects`.

### Document Management Review

Implemented in:

- `backend/rbac_backend/models/document.py`
- `backend/rbac_backend/routers/documents.py`
- `backend/rbac_backend/services/document_service.py`
- `backend/rbac_backend/services/upload_streaming.py`
- `backend/rbac_backend/services/ocr_service.py`
- `backend/rbac_backend/services/document_processor.py`
- `client/src/pages/UploadPage.tsx`
- `client/src/pages/DocumentsPage.tsx`
- `client/src/pages/DocumentViewerPage.tsx`
- `client/src/components/document-viewer/DocumentViewer.tsx`

Supported document model:

- Organization and project
- Upload direction: `incoming`, `outgoing`, and contract documents created with `uploadType="contract"`
- Letter number and normalized letter number
- Date, subject, from/to
- Tags and subTags
- Status
- OCR/compression flags
- Full text, clauses, keywords, summary
- References and enclosures
- Storage locations, local path, S3 path, file object/version IDs
- Processing status, job ID, metadata source, error
- Audit events and lifecycle fields

Document API capabilities:

- `POST /api/documents`: single file upload
- `GET /api/documents`: list/filter documents
- `GET /api/documents/{id}`: fetch document
- `PUT /api/documents/{id}`: update metadata with revision headers
- `DELETE /api/documents/{id}`: soft delete with step-up
- `GET /api/documents/{id}/download`: local or S3 download
- `GET /api/documents/export`: XLSX export
- `GET /api/documents/vector-search`: vector search
- `POST /api/documents/{id}/process`: queue processing
- `GET /api/documents/{id}/processing-status`: processing status
- Reference/enclosure/comment endpoints
- Bulk upload endpoints and template download
- `POST /api/documents/{id}/request-draft`: create drafting request from a document

Document positives:

- Streaming spool upload and MIME validation.
- Optional antivirus integration.
- Local path allowance checks before serving files.
- Audit events for downloads and changes.
- Queue-based processing with retry/dead-letter semantics.
- Optimistic locking on metadata update.
- Bulk upload with CSV metadata and folder upload UI.

Document concerns:

- Bulk total-size check in `documents.py` uses `file.file._file.tell()` before reading, which will usually be zero and does not enforce total upload size.
- `DocumentController.get_bulk_upload_status()` shadows FastAPI's `status` module with a local variable, so a missing job can produce a 500 instead of a clean 404.
- Bulk job status lookup has a service comment saying production needs more sophisticated access control.
- Compression toggle is passed from UI but no clear backend compression implementation was found in the audited path.
- Upload from URL/cloud import UI is not wired to an API.
- Document viewer only previews PDFs; non-PDFs show a placeholder.
- Viewer production CSP likely blocks the CDN PDF worker.

### AI, OCR, Metadata Extraction, and RAG Review

Implemented in:

- `backend/rbac_backend/services/ocr_service.py`
- `backend/rbac_backend/services/document_processor.py`
- `backend/rbac_backend/services/openai_service.py`
- `backend/rbac_backend/services/pydantic_ai_service.py`
- `backend/rbac_backend/services/database_service.py`
- `backend/rbac_backend/services/langchain_vector_service.py`
- `backend/rbac_backend/routers/retrieval_engine.py`
- `backend/rbac_backend/retrieval/service.py`
- `backend/rbac_backend/services/contracts_ingest.py`

OCR and metadata flow:

1. Upload creates the document and may queue processing.
2. `DocumentService.process_document_job()` materializes the file and calls `DocumentProcessor`.
3. `OCRService` detects text PDFs, extracts sidecar text, or runs OCRmyPDF for scanned PDFs.
4. `OpenAIService` uploads the processed file and asks for structured extraction.
5. `PydanticAIService` may parse structured metadata; legacy regex fallback exists.
6. Metadata, text, summary, keywords, clauses, and references are saved.
7. Embeddings are stored in Mongo and optionally Qdrant.

RAG capabilities:

- `/api/documents/vector-search` for document vector search.
- `/api/v1/retrieval/search` for scoped retrieval.
- `/api/v1/retrieval/rag` for RAG answers with citations.
- `/api/v1/retrieval/contract-qa` for iterative contract QA.
- `/api/v1/retrieval/agent` for drafting agent requests.
- `/api/v1/observability/logs` and `/api/v1/observability/analytics` for retrieval observability.

AI/RAG positives:

- RAG includes citations, timings, observability logs, strategy support, and Qdrant/Mongo backend selection.
- Contract QA enforces completed contract status before answering against a selected contract.
- Retrieval engine requires non-empty org/project scope and re-authorizes resolved contract document scope.
- Contract ingestion preserves clause numbers, titles, page spans, and enriched text where available.

AI/RAG concerns:

- `OpenAIService.upload_file()` reads the whole file into memory before upload.
- Metadata extraction depends on OpenAI availability even when OCR is the only desired operation.
- Older `/api/search/semantic` explicitly falls back to text search, not true semantic/vector search.
- Old `/api/search/documents` uses stale/misaligned fields and catches validation HTTPExceptions into a 500.
- Marker CLI is enabled by default in processing config and `.env.example`, but README warns it must be installed. Missing Marker silently degrades contract clause extraction.
- Qdrant, FalkorDB, OpenAI, OCRmyPDF, and optional Marker are all required for best results; the manual should treat AI/RAG as environment-sensitive.

### Upload and Viewer Workflow Review

Frontend single upload:

- `client/src/pages/UploadPage.tsx`
- Uses org/project selectors, uploadType, letter number, date, subject, from/to, tags, subTags, status, OCR, compression.
- Calls `enhancedApi.uploadDocument()` in `client/src/services/enhanced-api.ts`.
- Sequentially uploads files to match the backend single-file endpoint.
- Navigates to `/documentviewer/{firstDocId}` after upload.

Frontend bulk upload:

- Folder selection uses `webkitdirectory` and drag/drop directory APIs.
- CSV template download calls `/documents/bulk-upload/template`.
- Start bulk upload calls `/documents/bulk-upload`.
- Polls `/documents/bulk-upload/{job_id}/status`.

Viewer:

- `DocumentViewerPage.tsx` fetches document metadata, processing status, tags/subtags, users, references, and available opposite-direction documents.
- `DocumentViewer.tsx` downloads `/documents/{id}/download`, creates a Blob URL, and uses `@react-pdf-viewer`.
- Metadata, enclosures, references, and details panels are separate components.

Broken or partial viewer/upload items:

- From URL/cloud import is UI only.
- Bulk options such as maintain folder structure, auto-process, metadata extraction, and notify complete are not sent to the backend.
- PDF worker CDN conflicts with production CSP.
- Non-PDF preview is not implemented.

### Search Review

Search exists in three forms:

1. DMS list filtering in `GET /api/documents`.
2. Legacy advanced search in `backend/rbac_backend/routers/search.py` and `client/src/pages/EnhancedDocumentsPage.tsx`.
3. Contract/RAG search in `contracts.py` and `retrieval_engine.py`.

Status:

- DMS list filtering is usable for metadata search.
- Contract search is comparatively strong and hybrid: lexical + vector, clause grouping, category terms, page filters, exact phrase, source list, optional summary.
- Retrieval RAG is scoped and citation-aware.
- Legacy advanced search is partial and should be refactored or removed because it uses stale fields and stubs popular/semantic behavior.

### Dashboard and Reports Review

Dashboard:

- `backend/rbac_backend/routers/dashboard.py` exposes `/api/dashboard/stats`.
- It aggregates document counts, statuses, recent documents, actionable letters, organization stats, and project stats.
- It depends on `dms.dashboard.view`.

Dashboard concerns:

- `totalLetters` is set equal to `totalDocuments`, even though letters are modeled separately elsewhere.
- It loads all scoped documents into memory and computes counts in Python.
- Search regex is not escaped.

Reports:

- `backend/rbac_backend/routers/reports.py`
- `backend/rbac_backend/services/report_service.py` was referenced by the router.
- `client/src/pages/ReportsAnalyticsPage.tsx`
- Supports catalog, preview, and CSV download with filters for dates, project, organization, tags, subtags, statuses, direction, letter number, and chain direction.

Reports status: partially working. The endpoints and UI exist, but enterprise reporting needs stronger aggregation, scheduling, exports, permissions, and auditability.

### Subscriptions and Monetization Review

Implemented in:

- `backend/rbac_backend/routers/rbac_monetization.py`
- `backend/rbac_backend/services/entitlement_service.py`
- Frontend routes `/plan-settings` and `/subscription-management`

Capabilities found:

- Plan catalog and plan CRUD
- Effective service settings
- Add-ons
- Subscription records
- Expert allocation endpoints
- Entitlement checks in `PolicyService`

Status: partial. The backend models and admin APIs exist, but I did not find evidence of a payment provider integration, invoicing, seat billing, renewal lifecycle automation, or customer-facing checkout in the inspected files.

### Deployment and Operations Review

Implemented:

- `README.md` Docker stack instructions.
- `.env.example` with production-style variables.
- `docker-compose.yml` and `docker-compose.prod.yml`.
- `scripts/deploy.sh`.
- `backend/Dockerfile` and `client/Dockerfile`.
- Health endpoints in `backend/rbac_backend/routers/health.py`.
- CI in `.github/workflows/ci.yml`.
- Apache proxy/security headers in `config/httpd.conf`.

Deployment positives:

- Production compose uses required environment substitution for secrets.
- Production compose uses Redis/FalkorDB passwords.
- Qdrant API key required in production compose.
- Health checks for backend, client, Qdrant, FalkorDB, Redis, Graphiti, ClamAV.
- Metrics endpoint requires token if configured.
- CI runs secret scanning, pre-commit, compileall, backend tests, frontend lint/tests/build, pip-audit, npm audit, Docker build, and Trivy scans.

Deployment concerns:

- `backend/.env.example` was not found, while local compose/setup references backend env behavior in places. Root `.env.example` exists and is comprehensive.
- README references services such as LangGraph at `/langgraph`, but the inspected compose stack does not deploy a LangGraph service by default.
- `.env.example` explicitly says Docling service is not registered in Docker Compose.
- Local compose exposes data services directly; production compose is more locked down.
- CSP conflicts with the frontend PDF viewer worker.

## Existing Functionality Table

| Feature | Files/APIs/Components | Intended Function | Status | Evidence and Notes |
|---|---|---|---|---|
| Public landing page | `client/src/routes.tsx`, `LandingPage` | Public marketing/entry page | Working | Public route `/` exists. |
| Login/logout/session | `auth.py`, `AuthenticationService`, `use-auth.ts`, `auth.ts` | Cookie login, refresh, logout, profile fetch | Mostly working | Auth cookie, CSRF token, `/me`, `/refresh`, `/logout` exist. Lockout issue remains. |
| CSRF protection | `core/csrf.py`, `services/http.ts` | Protect unsafe cookie-auth requests | Mostly working | Double-submit token and Origin validation implemented. |
| Step-up auth | `step_up_service.py`, `auth.py`, `roles.py`, `documents.py` | Require password re-check for sensitive actions | Working, needs broader adoption | Used for role mutations and delete document. |
| User management | `routers/users.py`, `enhanced-api.ts`, `/users` pages | Create/update/list users | Partial | Strong validation and roles, but route permission mismatch for `/register`. |
| Organization management | `routers/organizations.py`, frontend org pages | CRUD organizations | Mostly working | Superadmin create, validation, duplicate checks. |
| Project management | `routers/projects.py`, frontend projects | CRUD and notification settings | Needs improvement | Main `/projects` scoped; `/projects1` unscoped risk. |
| Role management | `routers/roles.py`, `PermissionService`, permissions pages | CRUD roles and role permissions | Partial | CRUD exists, step-up exists, but `build_permission_matrix()` empty and file is simplified. |
| RBAC route guard | `rolePermissions.ts`, `useRBAC.ts`, `ProtectedRoute.tsx` | Hide unauthorized routes | Partial | Permission naming mismatch can block valid users. |
| Subscription entitlements | `rbac_monetization.py`, `entitlement_service.py` | Plans, subscriptions, add-ons, service access | Partial | No payment provider or billing automation found. |
| Single document upload | `UploadPage.tsx`, `enhanced-api.ts`, `documents.py` | Upload one or more docs sequentially | Mostly working | Backend single-file endpoint, storage, audit, processing queue. |
| Bulk document upload | `UploadPage.tsx`, `documents.py` | Folder upload with CSV metadata and job polling | Partial | UI and API exist; total-size and missing-job bugs. |
| URL/cloud import | `UploadPage.tsx` | Import from URL/Google Drive/Dropbox/OneDrive | Missing/backend absent | UI only; no API call wired. |
| Document list | `DocumentsPage.tsx`, `GET /documents` | Filter, paginate, view/download/initiate draft | Mostly working | Metadata filters exist. |
| Enhanced document search | `EnhancedDocumentsPage.tsx`, `search.py` | Advanced search with facets/suggestions | Partial | Field mismatch and stale filter behavior. |
| Document viewer | `DocumentViewerPage.tsx`, `DocumentViewer.tsx` | PDF preview, metadata edit, enclosures, references | Partial | PDF works in dev likely; prod CSP/CDN issue. Non-PDF preview missing. |
| Metadata edit | `MetadataEditor`, `PUT /documents/{id}` | Edit direction/date/letter/subject/tags/status | Mostly working | Backend optimistic revision support; frontend does not always expose revision header behavior. |
| Enclosures | `documents.py`, `EnclosuresPanel` | Attach/list/delete enclosures | Partial | Endpoints/components exist; full behavior not runtime-verified. |
| References | `documents.py`, `ReferencesPanel` | Link related incoming/outgoing docs | Mostly working | Direct/indirect refs and sync endpoints exist. |
| Comments | `documents.py` | Add/list document comments | Partial | Endpoints exist, UI evidence limited. |
| Download all/ZIP | `documents.py`, `DocumentBulkDownloadService` | Project document archive | Partial | API exists; scale and authorization need testing. |
| OCR | `ocr_service.py` | OCR scanned PDFs, extract text PDFs | Mostly working if dependencies installed | OCRmyPDF/pdfplumber/PDF text paths exist. |
| Metadata extraction | `document_processor.py`, `openai_service.py`, `pydantic_ai_service.py` | Extract letter metadata, summary, references, clauses | Partial | OpenAI/PydanticAI plus regex fallback; external dependency heavy. |
| Document embeddings | `database_service.py`, `langchain_vector_service.py` | Store embeddings in Mongo/Qdrant | Partial | Dual write exists; requires Qdrant/OpenAI. |
| Vector document search | `GET /documents/vector-search` | Similarity search over document chunks | Partial | Endpoint exists; depends on embedding/vector writes. |
| Contract upload sessions | `contracts.py`, `ContractService` | Prepare contract upload with scope and limits | Working | Upload sessions, TTL, file type limits. |
| Contract multipart upload | `ContractsUploadPage.tsx`, `/contracts/upload-multipart` | Upload contracts in batch | Mostly working | Storage, antivirus optional, queue status. |
| Contract chunked upload | `contracts-api.ts`, `/contracts/upload-chunk` | Large contract upload in chunks | Mostly working | Chunk merge, checksums, queue scheduling. |
| Contract ingestion | `contracts_ingest.py`, `ContractService.process_ingest_job` | OCR, parse clauses, categorize, index vectors/graph | Partial | Strong code path; Marker/LLM/Qdrant/FalkorDB dependent. |
| Contract search | `ContractsSearchPage.tsx`, `/contracts/search` | Clause-level hybrid search | Mostly working | Lexical/vector fusion, structured filters, summaries. |
| Contract Q&A | `ContractQAPage.tsx`, `/v1/retrieval/contract-qa` | Iterative grounded contract answers | Partial to mostly working | Requires completed contracts and vector index. |
| General RAG | `retrieval_engine.py`, `retrieval/service.py` | Retrieval search/RAG/agent with observability | Partial | Strong backend; frontend coverage is contract-specific. |
| Letter workflow | `LetterWorkflowPage.tsx`, `useLetterWorkflow.ts`, `letters.py` | Initiate letters, assign drafter/reviewer, status workflow | Partial | UI/hook/routes exist; legacy and v2 flows coexist. |
| AI letter drafting | `letter_drafting.py`, `services/letter_drafting/service.py` | Analyze incoming, plan, draft, validate, critique, approve, issue | Partial | Rich backend models/service; needs end-to-end QA. |
| Draft quality dashboard | `/letter-drafting/metrics/dashboard` | Metrics for drafting quality/cycle/governance | Partial | Endpoint exists; page path uncertain from inspected files. |
| Notifications | `notifications` router, `enhanced-api.ts` | User/project notification preferences, test email | Partial | API methods exist; SMTP dependency. |
| Reports | `reports.py`, `ReportsAnalyticsPage.tsx` | Report catalog, preview, CSV download | Partial | Working endpoints/UI, enterprise features missing. |
| Dashboard | `dashboard.py` | Aggregate statistics | Needs improvement | Counts documents, but `totalLetters=totalDocuments`. |
| Health/metrics | `health.py` | Liveness/readiness/observability/Prometheus | Mostly working | Checks config, Mongo, Redis, storage. |
| CI/CD checks | `.github/workflows/ci.yml` | Tests, lint, scans, Docker build | Working in CI design | Latest commit says backend suite passes 183 tests; not locally rerun. |

## Intended Workflow

### Main DMS Workflow

1. Admin configures organizations, projects, roles, permissions, users, plan settings, storage, SMTP, tags, and subtags.
2. User logs in through `/login`; backend sets `cc_access_token` and `cc_csrf_token`.
3. Frontend loads `/me`, roles, and permissions.
4. User opens `/upload`.
5. User selects organization/project and uploads incoming or outgoing correspondence.
6. Backend streams and validates the file, stores it locally/S3 through storage services, creates a document, creates file/version records, emits audit events, and queues processing when enabled.
7. Processing performs OCR/text extraction, OpenAI/PydanticAI metadata extraction, database updates, vector indexing, and optional graph/reference sync.
8. User views documents in `/documents`, opens `/documentviewer/:id`, edits metadata, links references, adds enclosures/comments, downloads files, or requests drafting.
9. User searches through metadata list filters, `/documentsearch`, vector search, reports, or contract-specific search.

### Contract Workflow

1. User opens `/contracts/upload`.
2. User selects organization/project and contract PDF/DOCX files.
3. For large files, frontend creates an upload session and uploads chunks.
4. Backend stores file, creates a `Document` with `uploadType="contract"`, creates contract aggregate/version records, and queues contract ingestion.
5. Worker runs OCR, Marker/PDF parsing when available, clause extraction, categorization, vector indexing, and graph indexing.
6. Completed contracts appear in `/contracts/search` and `/contracts/qa`.
7. Contract Q&A calls `/api/v1/retrieval/contract-qa`, retrieves evidence, generates an answer, returns citations and iteration trace.

### Letter Drafting Workflow

1. User initiates a letter from `/letters` or from a document via `/documents/{id}/request-draft`.
2. Backend creates or updates a letter/drafting session.
3. Drafting v2 endpoints support:
   - start session
   - analyze incoming letter
   - confirm analysis
   - prepare plan
   - confirm plan
   - generate draft
   - revise draft
   - validate draft
   - critique draft
   - assign reviewer
   - add comments
   - return for correction
   - approve
   - export
   - issue
4. Governance data, audit, context pack, and source ledger are available through `letter_drafting.py`.

Status: intended workflow is clear and ambitious, but needs full E2E verification due legacy/v2 coexistence.

## User Manual

This manual describes functionality found in the repository only.

### Access and Login

1. Open the deployed frontend URL.
2. Use the login screen at `/login`.
3. After successful login, the app stores auth in HttpOnly cookies. Do not expect a bearer token in localStorage.
4. The app automatically refreshes sessions through `/api/refresh`.
5. Use profile/logout controls to end the session. Logout calls `/api/logout`.

Troubleshooting:

- If login succeeds but actions fail with 403, check that the user's role has backend permissions and frontend route permissions.
- If unsafe actions fail with CSRF errors, verify the browser received `cc_csrf_token`, the frontend API URL is same-site or CORS-enabled, and `CORS_ORIGINS` matches the UI origin.
- If sessions disappear between backend instances, verify Redis runtime state is configured and reachable.

### RBAC Flow

Common roles found in frontend config:

- `superadmin`
- `orgadmin`
- `orguser`
- `projectadmin`
- `projectuser`
- `doccontroller`
- `reporter`
- `settings_manager`
- `limited_user`
- `contractmgr_org`
- `contraclaim_drafting_manager`
- `contraclaim_expert_drafter`
- `contraclaim_expert_reviewer`
- `contraclaim_billing_admin`

How access is decided:

1. Backend `/me` returns roles and scope.
2. Frontend `useRBAC()` fetches roles and role permissions.
3. `ProtectedRoute` checks `client/src/config/rolePermissions.ts`.
4. Backend routers still enforce actual access through `require_permission`, `PolicyService`, and scope checks.

Important limitation: frontend permission names are not fully aligned with backend canonical permissions. If a user should see a page but is redirected, check `client/src/config/rolePermissions.ts` against backend `core/permissions.py`.

### Document Upload

Single upload:

1. Go to `/upload`.
2. Choose the `Upload` tab.
3. Select files. Supported UI extensions are PDF, DOC, DOCX, TXT, JPG, JPEG, PNG, and GIF.
4. Select file type: incoming or outgoing.
5. Select organization and project.
6. Enter optional letter number, letter date, subject, from, to, tags, subTags, and status.
7. Toggle OCR and compression.
8. Click Upload.
9. The UI uploads files sequentially through `POST /api/documents`.
10. After the first file uploads, the UI navigates to `/documentviewer/{id}`.

Notes:

- OCR defaults to enabled in the frontend.
- Compression is passed to the backend but should be treated as incomplete until implementation is verified.
- Backend validates MIME and size, stores files, creates document records, and queues processing.

Bulk upload:

1. Go to `/upload`.
2. Choose the `Bulk Upload` tab.
3. Select a full folder. Browser support is best in Chrome/Edge.
4. Select organization and project.
5. Download the CSV template from the UI.
6. Fill metadata and upload the CSV.
7. Start bulk upload.
8. The frontend polls `/api/documents/bulk-upload/{job_id}/status`.

Bulk CSV expected columns are normalized by backend code. The template uses camelCase names such as `uploadType`, `letterNo`, and `ocrEnabled`.

Known limitation: bulk upload has a backend total-size validation bug and missing job status bug.

URL/cloud import:

- The `From URL` tab and cloud buttons are visible but UI-only. No backend import API was found in the audited code.

### Document Viewer

1. Open a document from `/documents` or navigate to `/documentviewer/{document_id}`.
2. The page fetches `/api/documents/{id}`.
3. For PDFs, `DocumentViewer.tsx` downloads `/api/documents/{id}/download` and renders it with `@react-pdf-viewer`.
4. Use search and zoom controls inside the PDF viewer.
5. Use tabs for metadata, enclosures, references, and details.
6. Use retry processing if processing failed or is not queued.

Known limitations:

- Non-PDF preview is not implemented.
- Production CSP may block the CDN PDF.js worker. Bundle the worker locally or update CSP safely.

### Metadata Extraction

Processing runs through:

- `DocumentService.process_document_job()`
- `DocumentProcessor`
- `OCRService`
- `OpenAIService`
- `PydanticAIService`
- `DatabaseService`

Expected output fields include:

- `letterNo`
- `date`
- `subject`
- `from`
- `to`
- `full_text`
- `summary`
- `keywords`
- `clauses`
- `references`
- `processing_status`
- `metadata_source`

Troubleshooting:

- If OCR fails, verify OCRmyPDF and system OCR dependencies are installed in the backend image/environment.
- If metadata extraction fails, verify `OPENAI_API_KEY` and model settings.
- If vectors are missing, verify Qdrant URL/API key and embedding model configuration.
- If contract clauses are missing, verify Marker CLI if `MARKER_ENABLED=true`.

### Search and RAG Usage

Basic document filtering:

- Use `/documents` for metadata search, project/status/tag/date/direction filters, and pagination.

Advanced document search:

- Use `/documentsearch`.
- Requires organization and project selection.
- Calls `/api/search/documents`.
- Treat as partial because backend field mappings are stale.

Vector document search:

- Backend endpoint: `GET /api/documents/vector-search`.
- Requires `dms.document.view`.
- Depends on embeddings being created during processing.

Contract search:

1. Upload contracts at `/contracts/upload`.
2. Wait until status is `completed`.
3. Use `/contracts/search`.
4. Search supports query, categories, exact phrase, clause number/title, section heading, tags, and page filters.

Contract Q&A:

1. Go to `/contracts/qa`.
2. Select organization and project.
3. Select a completed contract or all completed files in the project.
4. Ask a question.
5. The UI calls `/api/v1/retrieval/contract-qa`.
6. The answer includes citations and optional trace.

### Admin Functions

Admin features found:

- Users: `/users`
- Organizations: `/organizations`
- Projects: `/projects`
- Roles/permissions: `/permissions`
- Plan settings: `/plan-settings`
- Subscription management: `/subscription-management`
- Tags/subtags: `/tags`
- SMTP/storage/settings routes
- Health page: `/health`

Sensitive actions:

- Role create/update/delete requires step-up token.
- Document delete requires step-up token.
- Production config rejects unsafe settings.

### Reports and Dashboard

Dashboard:

- Route: `/dashboard`
- Backend: `GET /api/dashboard/stats`
- Requires `dms.dashboard.view`

Reports:

- Route: `/reports`
- Backend:
  - `GET /api/reports`
  - `POST /api/reports/preview`
  - `POST /api/reports/download`
- Requires `dms.report.view`
- Supports CSV download.

Known limitation: dashboard currently equates total letters with total documents.

## Developer Manual

### Prerequisites

Expected tooling:

- Docker and Docker Compose
- Python 3.12 (project virtual environments and containers; do not replace the OS `python3` default)
- Node.js 20
- MongoDB 8.0 for local backend development
- Redis for runtime/session/queue state
- Qdrant for vector search
- FalkorDB for graph features
- OCR dependencies for OCRmyPDF
- Marker CLI for best contract ingestion, if `MARKER_ENABLED=true`

### Environment Variables

Use root `.env.example` as the main template. A separate `backend/.env.example` was not found.

Critical variables:

- `ENVIRONMENT`
- `SECRET_KEY`
- `DATABASE_URL`
- `MONGODB_URI`
- `MONGODB_DATABASE`
- `MONGODB_REPLICA_SET`
- `CORS_ORIGINS`
- `PUBLIC_BASE_URL`
- `PUBLIC_API_URL`
- `AUTH_COOKIE_SECURE`
- `AUTH_COOKIE_SAMESITE`
- `OPENAI_API_KEY`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_BUCKET_NAME`
- `AWS_REGION`
- `QDRANT_URL`
- `QDRANT_API_KEY`
- `APP_REDIS_URL`
- `RUNTIME_STATE_REDIS_URL`
- `CONTRACT_QUEUE_REDIS_URL`
- `FALKORDB_URL`
- `FALKORDB_PASSWORD`
- `SMTP_*`
- `SMTP_SETTINGS_ENCRYPTION_KEY`
- `METRICS_ENABLED`
- `METRICS_TOKEN`
- `LANGGRAPH_ENABLED`
- `LANGGRAPH_API_TOKEN`
- `ANTIVIRUS_ENABLED`
- `CLAMAV_FAIL_OPEN`
- `UPLOADS_DIR`
- `SECURE_UPLOADS_DIR`

Production guardrails:

- `ENVIRONMENT=production`
- `AUTH_COOKIE_SECURE=true`
- `ALLOW_DEV_HEADERS=false`
- `RBAC_ENTITLEMENT_FAIL_OPEN=false`
- No localhost CORS origins
- MongoDB replica set configured
- Redis URLs configured
- Metrics token configured if metrics are enabled
- Antivirus fail-open disabled if antivirus is enabled

### Docker Startup

From the repository root:

```bash
cp .env.example .env
# edit .env carefully
./scripts/deploy.sh
```

Manual equivalent:

```bash
docker compose --env-file .env pull
docker compose --env-file .env up -d --build
```

Production-style compose:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Check health:

```bash
curl http://localhost/api/health/live
curl http://localhost/api/health/ready
```

### Backend Startup

Development startup, assuming local dependencies and environment are configured:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r rbac_backend/requirements.txt
uvicorn rbac_backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend app import path:

```bash
rbac_backend.main:app
```

Health endpoints:

- `/health/live`
- `/health`
- `/health/ready`
- `/health/observability`
- `/metrics`

### Frontend Startup

```bash
cd client
npm ci
npm run dev
```

Build:

```bash
npm run build
```

Test:

```bash
npm test -- --run
```

The frontend uses API helpers in `client/src/config/api.ts`, `client/src/services/api.ts`, and `client/src/services/http.ts`. Ensure the API base URL points to the backend `/api` prefix.

### Database Setup

MongoDB:

- The backend creates indexes at startup in `core/database.py`.
- In production, use replica set and authenticated connection string.
- Avoid standalone MongoDB in production unless explicitly allowed and accepted.

Redis:

- Used for sessions, rate limits, runtime state, and contract queue.
- Do not rely on in-memory fallback in production.

Qdrant:

- Required for vector search/RAG best behavior.
- Configure `QDRANT_URL`, `QDRANT_API_KEY`, collection, vector size, and distance.

FalkorDB:

- Used for graph/reference features.
- Configure password and graph name.

### Background Workers

Contract worker command:

```bash
python -m rbac_backend.worker
```

Compose service:

- `contract-worker`

Backend settings:

- `START_BACKGROUND_SERVICES`
- `START_CONTRACT_QUEUE_WORKERS`
- `CONTRACT_QUEUE_ENABLED`
- `CONTRACT_QUEUE_REDIS_URL`

### Testing and CI

CI file: `.github/workflows/ci.yml`

CI jobs:

- Gitleaks secret scan
- Backend pre-commit
- Backend compileall
- Backend pytest against `backend/rbac_backend/tests`
- Frontend lint
- Frontend tests
- Frontend build
- pip-audit
- npm audit
- Docker build
- Trivy image scan

Latest reviewed commit message states the backend suite passed 183 tests. I could not rerun tests locally because this audit used the GitHub connector rather than a local checkout.

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Backend refuses to start in production | Runtime config validation failed | Check `SECRET_KEY`, cookies, CORS, Mongo replica set, Redis, metrics token |
| Login works but unsafe POST/PUT/DELETE fails | CSRF token/origin issue | Check `cc_csrf_token`, `X-CSRF-Token`, CORS origin, public URL |
| User can log in but page redirects to overview | Frontend permission mismatch | Align `rolePermissions.ts` with `core/permissions.py` |
| PDF viewer blank in production | CSP blocks CDN worker | Serve pdf.worker locally or update CSP carefully |
| Bulk upload missing status gives 500 | `status` variable shadowing in documents router | Rename local variable and use FastAPI status module |
| Contract upload stays queued | Worker/Redis not running | Check `contract-worker`, Redis URLs, queue settings |
| Contract search returns no results | Ingestion incomplete or vector backend unavailable | Check contract status, Qdrant, embeddings, Marker/OCR logs |
| Metadata extraction fails | OpenAI/OCR dependency missing | Check `OPENAI_API_KEY`, OCRmyPDF, file type, backend logs |
| Search suggestions empty | Search uses `name`, while documents use `filename` | Refactor old search router |
| Dashboard counts look wrong | Dashboard counts documents as letters | Refactor dashboard aggregation |

## Issue and Bug List

| Priority | Issue | Evidence | Impact | Recommended Fix |
|---|---|---|---|---|
| Critical | Unscoped project endpoint | `backend/rbac_backend/routers/projects.py`, `/projects1` returns `db.projects.find().to_list(100)` | Project data exposure across tenants for users with `projects:read` | Remove endpoint or apply `build_scope_query` and `PolicyService` |
| Critical | Permissive legacy authorization service | `backend/rbac_backend/services/authorization_service.py` states it errs on allowing requests | Routes using it can under-enforce tenant/RBAC | Migrate all sensitive routes to `PolicyService`; make legacy deny-by-default |
| Critical | Frontend/backend permission mismatch | `client/src/config/rolePermissions.ts` vs `backend/rbac_backend/core/permissions.py` | Valid users blocked from pages or inconsistent UX | Generate frontend permissions from backend constants or shared manifest |
| High | Bulk upload total-size check ineffective | `documents.py` uses `file.file._file.tell()` before reading | Oversized multi-file uploads can bypass intended total cap | Sum actual sizes during streaming or enforce per-read accumulated total |
| High | Bulk status missing-job path can 500 | Local variable `status` shadows imported FastAPI `status` | Bad job IDs return server error | Rename local variable to `job_status` |
| High | PDF viewer likely blocked by CSP | `DocumentViewer.tsx` uses cdnjs worker; `config/httpd.conf` worker-src is self/blob | Production PDF preview can fail | Bundle worker locally and set `workerUrl` to local asset |
| High | Old search queries wrong fields | `search.py` uses `name`, `content`, `categories`, `size`; document model uses `filename`, `full_text`, `tags`, `filesize` | `/documentsearch` misses results or errors | Rebuild search on actual schema or retire old router |
| High | Search validation errors converted to 500 | `search.py` catches all exceptions after raising `HTTPException(400)` | Bad date/upload type gives wrong response | Re-raise `HTTPException` before broad catch |
| High | Login lockout not incremented in primary auth route | `auth.py` uses `authenticate_user_secure`; invalid credentials do not call `increment_failed_attempts` | Account lockout protection weaker than intended | Increment failed attempts on known user in primary login flow |
| High | Dashboard loads all scoped documents | `dashboard.py` collects all docs then counts in Python | Poor scalability for large tenants | Use Mongo aggregation pipelines |
| High | Dashboard equates letters and documents | `dashboard.py` sets `totalLetters = totalDocuments` | Misleading business metrics | Count letters from letters collection and documents separately |
| High | Upload from URL/cloud is UI only | `UploadPage.tsx` From URL tab has no API integration | Misleading user workflow | Hide until implemented or add secure import service |
| Medium | Bulk option checkboxes are UI only | `UploadPage.tsx` checkboxes not sent to backend | User selections do nothing | Add payload fields and backend behavior, or remove |
| Medium | Advanced search can use stale filters | `EnhancedDocumentsPage.tsx` calls `performSearch` right after `setFilters` | Search results lag user selection | Pass explicit filters into performSearch |
| Medium | `PermissionService.build_permission_matrix()` placeholder | `permission_service.py` returns empty matrix | Permissions UI incomplete | Implement matrix from roles/permissions collections |
| Medium | Root package files look accidental | Root `package.json` only has `index` and `llama`; active frontend is `client/package.json` | Confusion and dependency risk | Remove or document root package file |
| Medium | Deployment docs mention undeployed services | README and `.env.example` reference Docling/LangGraph with caveats | Operator confusion | Align README, compose, and env templates |
| Medium | Observability raw query storage default needs review | `ObservabilityService` stores raw or redacted queries depending setting | Sensitive contract questions may be logged | Default to redaction in production and document retention |
| Medium | OpenAI file upload reads whole file | `OpenAIService.upload_file()` reads file into memory | Memory pressure on large files | Stream file object to SDK if supported |
| Medium | Mixed ObjectId/string IDs | Many helpers convert both ways | Query/index inconsistency | Standardize ID storage and migration |
| Low | Role router has duplicate imports and "simple" comments | `routers/roles.py` | Code quality | Clean comments/imports after functionality is stable |
| Low | Project router logs sample project structure | `routers/projects.py` | Possible noisy/sensitive logs | Remove sample payload logging |
| Low | Upload cancel button has no handler | `UploadPage.tsx` | UX confusion | Implement cancel/reset/upload abort |
| Low | Non-PDF viewer placeholder only | `DocumentViewer.tsx` | Poor DOCX/image/TXT UX | Add preview/rendering/download-first experience |

## Security Review

### Security Strengths

- HttpOnly auth cookie support.
- CSRF token plus Origin validation for unsafe methods.
- Runtime configuration validation for production.
- Rate limiting by IP/email/user with Redis support.
- Step-up authentication for role mutations and document deletion.
- JWT invalidation through Redis `user_jwt_min_iat`.
- Storage path allowance checks before local file serving.
- MIME sniffing and executable signature rejection in upload streaming.
- Optional ClamAV scanning.
- Audit events for document/contract/role activity.
- CI secret scanning and dependency/image scanning.
- Apache security headers: HSTS, `X-Content-Type-Options`, `X-Frame-Options`, Referrer Policy, Permissions Policy, CSP.

### Security Risks

1. `/projects1` should be removed or scoped immediately.
2. Legacy `AuthorizationService` permissive behavior must be eliminated or isolated from production routes.
3. Frontend route guard mismatch can produce inconsistent user experiences and may encourage workarounds.
4. In-memory auth/rate-limit fallbacks are unsafe in multi-instance production.
5. ClamAV is disabled by default and fail-open by default in `.env.example`.
6. Local development compose exposes databases and uses unauthenticated MongoDB.
7. CSP conflict can break viewer; broad `connect-src https:` should be reviewed.
8. Search regex in `dashboard.py` is not escaped.
9. Observability may store raw user queries if configured, which can include sensitive contract/claim content.
10. URL import UI should not be implemented without SSRF protections, URL allowlists, content validation, and malware scanning.

### Immediate Security Actions

- Delete or secure `/projects1`.
- Audit every route using `AuthorizationService`; replace with `PolicyService`.
- Align permission names and add automated tests for route access.
- Require Redis in production and fail closed if unavailable.
- Set `ANTIVIRUS_ENABLED=true` and `CLAMAV_FAIL_OPEN=false` for production file upload environments.
- Redact RAG and search queries in production observability by default.
- Add tenant-scope tests for documents, projects, contracts, reports, search, and drafting.

## Production Readiness Score

Score: **5.8 / 10**

Breakdown:

| Area | Score | Notes |
|---|---:|---|
| Product scope | 8 | Strong DMS, contract, AI, drafting vision already coded |
| Backend architecture | 7 | Good modularity and config validation; legacy permissive auth remains |
| Frontend completeness | 6 | Many screens exist; some controls are UI-only or mismatched |
| Auth/security | 6 | Good base, but `/projects1`, permissive auth, and fallback modes block production readiness |
| RBAC/entitlements | 5 | Strong concept, inconsistent permission names and partial matrix |
| Document upload/viewer | 6 | Core works; bulk/viewer bugs and non-PDF gaps |
| AI/RAG | 6 | Advanced contract RAG exists; operational dependencies and old search split |
| Database/scalability | 5 | Indexes exist; aggregation and ID consistency need work |
| Deployment/ops | 6 | Production compose and CI exist; docs/service mismatch remains |
| Testing confidence | 5 | CI is good and commit says 183 backend tests pass, but no runtime verification in this audit |

Interpretation:

- Ready for: internal demo, staging, controlled pilot with trusted users.
- Not ready for: multi-tenant enterprise production with sensitive claims/contracts.
- Blocking themes: authorization consistency, RBAC alignment, upload/search bugs, deployment validation, operational hardening.

## Improvement Roadmap

### Critical

- Remove or secure `/projects1`.
- Make all authorization deny-by-default; replace permissive `AuthorizationService` paths with `PolicyService`.
- Align frontend route permissions with backend canonical permissions.
- Add automated tenant-isolation tests for documents, projects, contracts, reports, search, and drafting.
- Fix bulk upload total-size validation and status shadowing.
- Fix production PDF viewer by bundling PDF.js worker locally and validating CSP.
- Verify primary login failed-attempt lockout and rate-limit behavior.

### High

- Consolidate search: keep `GET /documents` for metadata, migrate advanced search to actual schema, and use `/v1/retrieval` for semantic/RAG.
- Replace dashboard in-memory counting with Mongo aggregations.
- Separate document metrics from letter metrics.
- Implement or hide Upload from URL/cloud import.
- Enforce Redis availability in production for sessions/rate limits.
- Add full E2E tests for login, upload, process, viewer, search, contract upload, contract QA, and letter drafting.
- Add production smoke test script for Mongo, Redis, Qdrant, FalkorDB, OpenAI, storage, OCR, Marker, and SMTP.
- Standardize ObjectId/string handling.
- Add file processing resource limits and streaming OpenAI upload.

### Medium

- Implement permission matrix UI/backend.
- Refactor legacy `letters.py` and older letter workflow code around v2 drafting service.
- Add non-PDF preview support for DOCX, TXT, images, and downloaded-original workflow.
- Make bulk upload options functional or remove them.
- Add retry/cancel controls for uploads and processing jobs.
- Add robust document processing status UI and admin dead-letter queue.
- Add structured app logging with request/user/org/project context.
- Add audit trail UI for document, role, subscription, and drafting lifecycle.
- Add data retention and observability redaction controls.
- Update README and env templates to match deployable services.

### Low

- Clean duplicate imports and stale comments in routers.
- Remove accidental root `package.json` or document it.
- Add API docs page for non-production environments with route catalog.
- Improve empty states and error states in frontend.
- Add keyboard accessibility pass for upload/viewer controls.

## Suggested Additional Features

### Claims and Construction Contract Features

- Claim register with claim type, event date, notice date, submission date, amount, EOT days, status, responsible party, and linked correspondence.
- Delay analysis workspace with baseline/revised schedules, delay events, critical path narrative, EOT entitlement, and concurrent delay flags.
- Variation management with VO/CE/change instruction logs, valuation, approval status, and contract clause basis.
- Payment tracking for IPCs, certifications, deductions, retention, advance recovery, and payment delays.
- Correspondence tracker with required response date, SLA, pending party, overdue alerts, and reply chain.
- Notice compliance engine to detect time-bar risk.
- Claim analytics dashboard: amounts claimed/certified/rejected, delay days claimed/granted, open notices, overdue replies, high-risk issues.
- Contract obligation register extracted from clauses.
- Clause comparison between GCC/SCC and addenda.
- Dispute issue register with evidence bundle builder.

### Drafting and Legal/Contract Features

- Clause-grounded drafting templates for EOT, variation, payment, NCR, delay, records request, dispute, and general notices.
- Contract-specific legal drafting suggestions with mandatory citation requirement.
- Redline comparison between draft versions.
- Approval workflow with role-based reviewer/approver gates.
- Export to DOCX/PDF with letterhead and numbering.
- Draft quality scoring and hallucination checks.
- Matter-specific playbooks by contract type.

### DMS and Enterprise Features

- Document retention policies.
- Legal hold.
- Advanced full-text search with highlighting over OCR text.
- SharePoint/OneDrive/Google Drive connectors.
- Email ingestion from project mailboxes.
- Immutable audit trail export.
- SSO/SAML/OIDC and SCIM.
- Tenant-level encryption and key rotation.
- Enterprise reporting pack with scheduled exports.
- Data room/evidence bundle generation.

## Market Fit Analysis

### Target Users

Primary target users:

- Contractors' contract managers
- Claims consultants
- Project directors
- Planning/delay analysts
- Document controllers
- Quantity surveyors and commercial managers
- Employer/engineer representatives
- Legal/contract drafting teams

Secondary users:

- PMCs and engineering consultants
- Arbitration/dispute teams
- Internal audit/compliance teams
- Enterprise construction owners

### Main Value Proposition

The product can become a construction contract intelligence and correspondence control system:

- Centralize incoming/outgoing letters and contract documents.
- Extract metadata from correspondence.
- Link replies, enclosures, and references.
- Search across project correspondence and contracts.
- Use contract clause evidence for Q&A and drafting.
- Manage drafting workflow with source ledger and governance.
- Provide reports and dashboards for correspondence status.

### Fit Against Market Expectations

The repository fits the market direction well because construction claims work is document-heavy, deadline-heavy, and clause-heavy. The code already reflects this with contract-specific Q&A, letter drafting roles, correspondence statuses, tags/subtags, references, and reports.

However, the current product is closer to a strong technical prototype/pilot than a market-ready claims platform. The market will expect:

- Reliable claim and correspondence registers.
- Deadline and notice compliance.
- Payment/variation/delay modules.
- Approval workflow.
- Enterprise audit and reporting.
- Integrations with email and document stores.
- Strong tenant security.
- Evidence bundle generation.
- Transparent AI citations and traceability.

### Market Gaps

| Gap | Current Evidence | Required Improvement |
|---|---|---|
| Claim analytics | No dedicated claim register found | Add claim entity, status pipeline, analytics |
| Delay analysis | Contract/RAG exists, no delay module | Add delay event and EOT analysis workflows |
| Variation management | Letter category includes variation, no VO module | Add variation register and approval/valuation tracking |
| Payment tracking | Letter category includes payment, no payment module | Add IPC/payment register |
| Correspondence SLA | Statuses and reports exist | Add response deadlines, time-bar alerts, escalation |
| Approval workflow | Draft governance exists | Generalize approvals for documents, letters, claims, variations |
| Enterprise reporting | CSV reports exist | Add scheduled, scoped, dashboard-grade reports |
| Email ingestion | Email share/settings exist | Add mailbox sync and automatic correspondence filing |
| Legal drafting | AI drafting exists | Add legal review gates, clause citation enforcement, version redlines |
| Audit trail UI | Audit events exist | Build full audit trail views and exports |

### Competitive Position

Potential strengths:

- Construction-specific contract/correspondence focus.
- Clause-grounded Q&A and AI drafting.
- Integrated DMS plus drafting workflow.
- Tenant/project RBAC model.

Weaknesses to address before market:

- Product reliability and security hardening.
- Workflow completeness across claims, variations, payments, delays.
- Enterprise integration and reporting.
- Clear deployment/onboarding story.
- AI accuracy validation and citation enforcement.

## Final Recommendations

1. Treat the current repository as a serious pilot, not production.
2. First stabilize security and RBAC: remove unscoped endpoints, make auth deny-by-default, align permissions, and add scope tests.
3. Consolidate search and AI paths: retire stale `/search` behavior or rebuild it on the actual schema, then position `/v1/retrieval` as the main RAG engine.
4. Fix document workflow defects before adding new modules: bulk upload, viewer CSP, non-PDF handling, processing status, and audit visibility.
5. Build a claims-market layer on top of the DMS: claim register, correspondence tracker, delay/variation/payment modules, approval workflow, and analytics.
6. Make deployment boring: one correct `.env`, one correct compose story, clear smoke tests, and documented optional services.
7. Keep AI grounded: require citations for contract Q&A and drafting, log trace safely, show source ledger to users, and measure no-answer/hallucination rates.

## Implementation Checklist

### Security and RBAC

- [ ] Remove or secure `/api/projects1`.
- [ ] Inventory all routes using `AuthorizationService`.
- [ ] Replace permissive checks with `PolicyService`.
- [ ] Align frontend and backend permission constants.
- [ ] Add route permission tests for each protected page/API.
- [ ] Add tenant isolation tests for all list/detail/download/search endpoints.
- [ ] Require Redis in production.
- [ ] Set antivirus to fail closed in production.
- [ ] Redact observability queries in production.

### Document Workflow

- [ ] Fix bulk total-size validation.
- [ ] Fix bulk status variable shadowing.
- [ ] Add scoped job ownership checks for bulk status.
- [ ] Implement upload cancellation.
- [ ] Implement or remove compression toggle.
- [ ] Implement or remove URL/cloud import.
- [ ] Bundle PDF worker locally.
- [ ] Add DOCX/TXT/image preview or clear download-only UX.
- [ ] Add processing retry/dead-letter admin UI.

### Search, AI, and RAG

- [ ] Refactor `/api/search/documents` to actual document schema.
- [ ] Remove stub popular/semantic behavior or mark it clearly.
- [ ] Use escaped regex for search/dashboard user input.
- [ ] Make `/v1/retrieval` the canonical semantic/RAG API.
- [ ] Add RAG answer evaluation tests with known fixture contracts.
- [ ] Add citation coverage checks.
- [ ] Stream large file uploads to OpenAI or avoid full memory reads.
- [ ] Validate Marker/OCR/OpenAI/Qdrant startup in health checks.

### Product and Market Fit

- [ ] Add claim register.
- [ ] Add correspondence response deadline tracker.
- [ ] Add delay/EOT module.
- [ ] Add variation module.
- [ ] Add payment/IPC tracker.
- [ ] Add approval workflows.
- [ ] Add enterprise audit trail UI.
- [ ] Add evidence bundle export.
- [ ] Add scheduled reports.
- [ ] Add email/mailbox ingestion.

### Deployment and Operations

- [ ] Align README, `.env.example`, compose files, and actual services.
- [ ] Decide whether LangGraph is deployed, optional, or removed from docs.
- [ ] Decide whether Docling is deployed, optional, or removed from docs.
- [ ] Add production smoke test script.
- [ ] Add backup/restore runbook for Mongo, Qdrant, Redis/FalkorDB, and uploads.
- [ ] Add log retention and PII handling policy.
- [ ] Add Sentry or equivalent error monitoring.
- [ ] Add capacity tests for upload, OCR, vector indexing, and search.

### Code Quality

- [ ] Clean duplicate imports and stale comments.
- [ ] Remove root package artifacts if unused.
- [ ] Standardize ObjectId/string ID usage.
- [ ] Add typed API schemas shared between backend/frontend where feasible.
- [ ] Replace request-time index creation with migrations/startup setup.
- [ ] Add frontend E2E tests for top workflows.
- [ ] Add API contract tests for auth, CSRF, upload, search, and drafting.
