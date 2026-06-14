# Bulk Upload Failure Analysis

## Current flows
- Single upload (`/api/documents`): `DocumentController.create_document` validates the upload, stores the bytes via `SecureFileService.store_document`, writes metadata with `DocumentService.create_document`, then optionally queues OCR/AI processing.
- Bulk upload (`/api/documents/bulk-upload`): `DocumentController.bulk_upload_documents` (backend/rbac_backend/routers/documents.py:297) parses the CSV via `BulkUploadService.read_csv_from_upload_file` (backend/rbac_backend/services/bulk_upload_service.py:121), checks required columns, creates a `BulkUploadStatus` job, then schedules `_process_bulk_upload` (documents.py:559) to iterate CSV rows and call `_process_single_file` (documents.py:628) which reuses `create_document` for each file.

## Findings
1. CSV header normalization dropped
   - The current CSV reader leaves headers untouched and does not strip BOM/NBSP characters or normalize case. The required-column check in `bulk_upload_documents` expects exact lower-case headers (`filename`, `upload_type`, `letter_no`, `date`, `subject`), so Excel-saved CSVs with a BOM or different casing fail early with "Missing required columns".
   - Earlier `_parse_and_validate_csv` logic (still present in documents1.py) normalized headers and supported semicolon fallbacks; the active route no longer uses it.
2. Background job reuses request-scoped UploadFile handles
   - `_process_bulk_upload` receives the original `UploadFile` objects from the request and reads them after the response is sent. These temporary files can be closed or cleaned up once the request finishes, so reads inside `create_document` can return empty bytes or raise I/O errors, resulting in per-row failures even though single uploads work.
   - The older implementation persisted uploads to temp files (`_persist_uploaded_files` in documents1.py) and reopened them for background processing; that safety net is gone in the active code.
3. `csv_encoding` parameter is ignored
   - The endpoint exposes `csv_encoding` (documents.py:1758), but `bulk_upload_documents` calls `read_csv_from_upload_file` without passing it, so callers cannot override detection when chardet guesses the wrong encoding.
4. Additional robustness gaps
   - No delimiter fallback now; semicolon-delimited CSVs are parsed as single-column frames.
   - Row-level validation errors are logged but not surfaced in job status, and progress metrics (processing_rate/estimated_completion) stay `None` when job lookups fail.

## Corrective actions (no code applied yet)
- backend/rbac_backend/routers/documents.py:
  - Normalize CSV headers (strip BOM/NBSP, lower-case, trim) and add delimiter fallback before the required-column check; consider reusing `_parse_and_validate_csv` logic.
  - Thread the `csv_encoding` form value through to `read_csv_from_upload_file` and improve error messages when encoding detection fails.
  - Persist uploaded files (copy to temp paths or buffer bytes) before scheduling `_process_bulk_upload`, reopen them inside the background task, and clean up afterward to avoid closed/empty file reads; reset file pointers before reads.
  - Surface validation failures in the job status response so users can see why rows failed.
- backend/rbac_backend/services/bulk_upload_service.py:
  - Add a header-normalization helper (lower-case, strip BOM/NBSP) and optional semicolon detection that the router can call before validation.
  - Optionally provide a helper to persist `UploadFile` streams for background reuse to keep file handling in one place.
- Tests/ops:
  - Add integration tests for BOM-prefixed headers, semicolon CSVs, cp1252/latin1 encoded files, and closed/empty UploadFile handles to prevent regressions.
