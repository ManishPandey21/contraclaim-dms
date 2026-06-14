# Upload Documents Flow (Letters) — Frontend to Backend

This document enumerates all related code paths and describes the end‑to‑end process for uploading documents in the ContraClaim DMS app, focusing on the Letters library. It includes file inventory, request/response contracts, validations, and observed gaps.

---

## Scope and UI Overview

- The Upload page lives at route: /upload
- Tabs:
  - Upload: Single/batch file selector with metadata controls (implemented).
  - From URL: Placeholder for importing from remote URLs or cloud storage (UI only).
  - Bulk Upload: Placeholder for folder upload and CSV metadata batch (UI only).
- After a successful upload, the UI navigates to the Document Viewer for the created document.

Screenshot alignment: The provided screenshot corresponds to the Bulk Upload tab’s design; however, most bulk features are not yet wired to backend endpoints.

---

## File Inventory (Related)

Frontend:

- client/src/routes.tsx
  - Declares route path "upload" → lazy page UploadPage.
- client/src/pages/UploadPage.tsx
  - Primary UI and logic for uploading documents to /documents via FormData.
  - Handles org/project selection, Tag/Sub-Tag enforcement, OCR/compression flags, and path structure display.
- client/src/components/layout/Sidebar.tsx
  - Navigation item: { path: "/upload", label: "Upload Letters" }.
- client/src/components/layout/Navbar.tsx
  - Title mapping for path "/upload" → "Upload Documents".
- client/src/pages/DocumentsPage.tsx and client/src/pages/DocumentsSearchPage.tsx
  - Provide navigation buttons linking to /upload when no results or as quick action.
- client/src/components/document-viewer/EnclosuresPanel.tsx
  - Adds enclosures (attachments) to an existing document via backend POST /documents/{id}/enclosures.

Backend:

- backend/rbac_backend/routers/documents.py
  - Core endpoints: create/list/get/update/delete documents; upload enclosures.
  - Implements controller that delegates to services for validation, storage, and persistence.
- backend/rbac_backend/models/document.py (present)
  - Document model definitions (fields inferred downstream).
- backend/rbac_backend/services/\* (not opened here but referenced)
  - DocumentService, SecureFileService, ExportService, AuthorizationService.
- backend/rbac_backend/core/config.py
  - Includes settings (e.g., SECURE_UPLOADS_DIR, allowed MIME types).
- backend/rbac_backend/utils/\* (validation, date parsing, error handling).
- Client service helpers:
  - client/src/services/enhanced-api.ts (contains various document APIs including uploadDocument helper, not used directly by UploadPage which posts via fetch/joinApiUrl).
  - client/src/config/api (joinApiUrl used to resolve base URL).

Contracts (module):

- backend/rbac_backend/routers/contracts.py
  - Separate upload flow for “contracts” with multipart and chunked APIs (not used by Upload Letters upload page, but referenced by ContractsUploadPage).

---

## Frontend Flow (UploadPage.tsx)

1. Preconditions and selections

- User selects:
  - Organization (organizationId)
  - Project (projectId)
  - Upload type: incoming | outgoing
  - Tag (selectedTagId) and Sub‑Tag (selectedSubTagId) — both required before upload
  - Letter details: letterNo, letterDate, subject, from, to
  - Toggles: ocrEnabled (default true), compressionEnabled (default false)

2. Path structure display (UI only)

- A “shortened” slug is computed for organization and project names.
- Two strings are maintained:
  - pathStructure: orgShort/projectShort/(year)/(month)
  - pathStructure1: orgShort/projectShort/
- These are appended to FormData but are currently ignored by the backend (see Gaps).

3. File selection

- Input accepts: .pdf, .doc, .docx, .txt, .jpg, .jpeg, .png, .gif
- Selected files are added to a queue display with size and clear/remove actions.

4. Authentication headers

- Token from localStorage.accessToken → Authorization: Bearer ...
- Fallback headers (dev parity): X-User-Id, X-User-Role, X-Org-Id, X-Proj-Id.

5. Submit (handleUpload)

- Validations:
  - Files must be selected.
  - Organization and Project required.
  - Tag and Sub‑Tag required.
  - Letter date parsed; if invalid, current date is used.
- FormData assembled (one request for all selected files; UploadPage currently appends multiple files under repeated “file” keys — see Gaps).
- Request:
  - POST joinApiUrl("/documents")
  - Body: FormData (DO NOT set Content-Type explicitly).
  - On success: navigate(`/documentviewer/${data._id}`) and toast success.

Frontend payload fields (as sent today by UploadPage):

- file: repeated for each File (see gap re: backend signature).
- letterNo
- organization_id
- project_id
- uploadType
- date
- pathStructure (UI hint; currently not consumed by backend endpoint)
- pathStructure1 (UI hint; currently not consumed by backend endpoint)
- subject
- from
- to
- tags (selectedTagId)
- subTags (selectedSubTagId)
- status
- ocrEnabled ("1" or "0")
- compressionEnabled ("1" or "0")

Error handling

- Toast messages for missing inputs or HTTP errors; console logging.

---

## Backend Flow (documents.py)

Endpoint: POST /documents

- FastAPI handler parameters (Form/multipart):
  - file: UploadFile (required; single file)
  - organization_id: str (required)
  - project_id: str (required)
  - uploadType: str (required)
  - letterNo: str (required)
  - date: str (required)
  - Optional: subject, from (alias to from\_), to, tags, subTags, status ("draft" default), ocrEnabled ("0"|"1")
- Controller.create_document steps:
  1. Authorization: AuthorizationService.check_document_access(user, org, project, "create")
  2. Validate presence: file.filename and letter_no
  3. sanitize_filename(file.filename); content = await file.read()
  4. validate_file(content, safe_filename, settings.ALLOWED_DOCUMENT_MIMES); reject on invalid MIME/content
  5. Parse date via parse_date_safely(date_str)
  6. Store file securely: SecureFileService.store_document(content, organization_id, project_id, safe_filename)
  7. Persist: DocumentService.create_document(..., including file_path, filename, org, project, upload_type, letter_no, date, current_user, and any extra kwargs like subject/tags/status)
  8. Background task if ocr_enabled: document_service.process_document_async(document.id, file_path)
  9. Return Document

Other endpoints:

- GET /documents/{id}
  - Authorization “read”; returns enriched document (DocumentService.enrich_document)
- GET /documents
  - Filters query: organization_id, project_id, tags, subTags, uploadType, status; pagination: skip, limit
  - Authorization-aware query built by AuthorizationService
  - Returns DocumentListResponse { documents: [], total: n }
- PUT /documents/{id}
  - Authorization “update”; validate/perform update; returns enriched updated Document
- DELETE /documents/{id}
  - Authorization “delete”; performs soft delete & cleanup (by service)
- POST /documents/{id}/enclosures
  - Upload file as enclosure; validates via settings.ALLOWED_ENCLOSURE_MIMES; stored and referenced through DocumentService.add_enclosure; returns EnclosureResponse

Observed contracts vs. client:

- Backend expects a single file in field “file” per call (UploadFile = File(...)).
- UploadPage currently attaches multiple files in one request by repeating “file” for each selected file. If FastAPI binding is not adjusted to List[UploadFile], only the first file is likely processed. See Gaps/Recommendations.

---

## Sequence (Happy Path)

- User navigates to /upload.
- Selects organization, project, tag, sub‑tag; enters letter meta; picks one or more files.
- Clicks “Upload”:
  - UploadPage builds FormData with fields above and Authorization header.
  - POST /documents (multipart).
- Server (documents.py):
  - Authorize user for org/project.
  - Validate file and metadata; secure filename + content validation.
  - Store content to SECURE_UPLOADS_DIR hierarchy (by org/project implementation inside SecureFileService).
  - Create document record; optionally queue OCR background task.
  - Return created Document (JSON), including its id.
- Client redirects to /documentviewer/{id}.
- User can add enclosures from the Document Viewer panel via POST /documents/{id}/enclosures.

---

## API Contract Details

Base URL: joinApiUrl("/...") resolves to configured backend (via client config/env).

1. Create a document

- Method: POST /documents
- Content-Type: multipart/form-data
- Form fields:
  - file (required, single file per call as coded in backend)
  - organization_id (required)
  - project_id (required)
  - uploadType (incoming|outgoing)
  - letterNo
  - date (string; parsed via parse_date_safely)
  - subject?, from? (alias to from\_), to?
  - tags?: array or single value
  - subTags?: array or single value
  - status?: default "draft"
  - ocrEnabled?: "1"|"0" (truthy strings treated as enabled)
- Response: 200 JSON Document

Example curl:
curl -X POST "$API_BASE/documents" ^
-H "Authorization: Bearer %TOKEN%" ^
-F "file=@C:\path\to\file.pdf" ^
-F "organization_id=ORG_ID" ^
-F "project_id=PROJ_ID" ^
-F "uploadType=incoming" ^
-F "letterNo=LET-123" ^
-F "date=2025-09-15" ^
-F "subject=Subject goes here" ^
-F "from=Client A" ^
-F "to=Project Team" ^
-F "tags=TAG_ID" ^
-F "subTags=SUBTAG_ID" ^
-F "status=Received" ^
-F "ocrEnabled=1"

2. Get a document

- GET /documents/{id}
- Response: 200 JSON Document (enriched)

3. List documents

- GET /documents?organization_id=&amp;project_id=&amp;tags=&amp;subTags=&amp;uploadType=&amp;status=&amp;skip=&amp;limit=
- Response: 200 { documents: [...], total: n }

4. Update a document

- PUT /documents/{id}
- Body: JSON DocumentUpdate
- Response: 200 JSON Document (enriched)

5. Delete a document

- DELETE /documents/{id}
- Response: 204 No Content

6. Add enclosure to a document

- POST /documents/{id}/enclosures
- Content-Type: multipart/form-data
- Fields: file
- Response: 200 EnclosureResponse

---

## Validations and Security

- AuthorizationService enforces access at create/read/update/delete.
- Filename sanitization and MIME validation prior to storage (prevents unsafe content).
- SecureFileService handles filesystem operations under settings.SECURE_UPLOADS_DIR.
- Background OCR task is optionally queued when ocrEnabled is true-like.

Client-side checks:

- Organization, Project, Tag, Sub‑Tag are required in UploadPage.
- Date is parsed; invalid input falls back to current date.
- Token presence guarded; attaches Authorization and dev fallback headers.

---

## Gaps, Mismatches, and Recommendations

1. Multiple file submission from UploadPage vs backend single-file signature

- UploadPage appends multiple file fields in one request.
- Backend endpoint accepts a single UploadFile (file: UploadFile = File(...)).
- Recommendation:
  - Either change backend create_document to accept List[UploadFile] and iterate to create N documents; OR
  - On the client, loop files and POST each file individually (preferable for simpler error handling and progress UX).

2. pathStructure/pathStructure1 sent by client but unused on backend

- Backend persists using SecureFileService.store_document(organization_id, project_id, safe_filename) and does not read pathStructure.
- Recommendation: Either remove these fields from client or explicitly add server support (e.g., override storage path template safely on backend).

3. Bulk Upload (folder + CSV) is UI-only

- No wired endpoints for:
  - Directory selection (webkitdirectory) and recursive upload
  - CSV metadata ingestion/template download
  - “Start Bulk Upload” button is inert
- Recommendation: Implement backend batch endpoints (CSV ingestion and folder manifests) and update UploadPage to post a batch artifact; or integrate with existing contracts upload chunk/multipart APIs if applicable.

4. Compression toggle is not used

- Client sends compressionEnabled; backend does not consume it.
- Recommendation: Implement optional compression in file service or remove from UI until supported.

5. Typing/Param naming consistency

- Client sends from while backend binds to from\_ via alias, which is already handled correctly; ensure all fields are documented in API docs and accepted both in UI and server.

6. Error surfacing and UX

- For multi-file scenario (if kept), backend should return per-file results; otherwise client loops per file and shows per-file toasts/progress.

---

## Testing Checklist

Manual test (single file, current behavior):

- Login to obtain accessToken.
- Go to /upload.
- Select Organization and Project, Tag and Sub‑Tag.
- Select 1 file.
- Click Upload.
- Expect navigation to /documentviewer/{id} and toast “Files uploaded successfully.”

API (curl):

- Use the example curl in the API section with your org/project IDs and a PDF file.
- Expect 200 and a JSON document containing an id field.

Enclosure upload:

- From the Document Viewer, add an enclosure; or curl:
  curl -X POST "$API_BASE/documents/{id}/enclosures" ^
  -H "Authorization: Bearer %TOKEN%" ^
  -F "file=@C:\path\to\attachment.pdf"

---

## Related “Contracts” Upload (separate)

- Contracts use distinct endpoints in backend/rbac_backend/routers/contracts.py:
  - POST /contracts/upload-multipart
  - POST /contracts/upload-chunk
  - GET /contracts/status
  - GET /contracts/download
- These are used by ContractsUploadPage.tsx and services in client/src/services/contracts-api.ts and are not part of the Letters upload described above.

---

## Summary

- UploadPage.tsx implements a complete single document upload flow using POST /documents and redirects to the viewer on success.
- Backend documents.py provides secure, authorized handling: validation, storage, persistence, and optional OCR.
- Bulk upload and certain UI toggles are currently placeholders or unused by the backend.
- The main immediate fix is to align the multi-file submission pattern with backend expectations (single file per request or adjust backend to accept List[UploadFile]).
