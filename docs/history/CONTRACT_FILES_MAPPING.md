# Contract Files Mapping

This document summarizes where contract and document uploads are stored, both locally and in S3 (when enabled).

## Upload paths (local)
- The backend resolves a base uploads directory at startup (`BASE_UPLOAD_PATH` in `backend/rbac_backend/routers/contracts.py`), defaulting to `uploads/contracts`.
- `SecureFileService` (`backend/rbac_backend/services/file_service.py`) writes files under:
  - `uploads/contracts/<org_id>/<project_id>/<filename>`
  - Chunked uploads: temp parts in `uploads/contracts/__chunks/<upload_id>/`, merged into the final path above.

## Document uploads (letters/documents)
- Route: `backend/rbac_backend/routers/documents.py`
  - Single upload: `FileService.store_document(...)` writes the file locally and returns `file_path`.
  - The resulting document record stores `file_path` (local path) and may also keep `filepath_local`/`filepath_s3` fields (depending on service implementation).
  - Reprocessing uses the first available path: `document.filepath_local` or `document.filepath_s3`.

## S3 handling (when enabled)
- Not shown in the above flows by default; if S3 is enabled in the service layer, expect both:
  - A local path for immediate processing.
  - An S3 URL/key stored as `filepath_s3` (for retrieval/long-term storage).

## Key code references
- Base path resolution: `backend/rbac_backend/routers/contracts.py` (`_resolve_uploads_dir`)
- Secure file writes: `backend/rbac_backend/services/file_service.py`
- Document upload flow: `backend/rbac_backend/routers/documents.py` (`store_document` -> `create_document`, optional processing uses `filepath_local`/`filepath_s3`)
