# Bulk Upload Investigation and Corrective Action Plan

## Overview

This document summarizes the investigation of bulk upload failure issues based on analysis of controller, service, and core document service code in the project. It identifies potential failure areas and proposes corrective actions to improve robustness and reliability.

---

## Components Involved

- **routers/documents.py**:
  - `DocumentController.bulk_upload_documents` handles CSV validation, job creation, and background task scheduling.
  - Parses CSV, normalizes filenames, validates rows.
  - Background task `_process_bulk_upload` processes files asynchronously.
- **services/bulk_upload_service.py**:
  - `BulkUploadService` manages job tracking in DB and memory cache.
  - Handles reading CSV encoding, parallel document processing.
  - Creates documents via `DocumentService.create_document`.
  - Updates job progress, marks job complete/fail.
- **services/document_service.py**:
  - `DocumentService.create_document` inserts document metadata and file info into MongoDB.
  - Handles document creation errors, notifications.
  - Supports metadata extraction and updates.

---

## Observed Potential Failure Points

### 1. CSV Encoding and Parsing Issues

- CSVs with unusual or inconsistent encodings may fail parsing or produce invalid rows.
- Controller and service both attempt encoding detection and fallback, which may conflict or be redundant.
- Malformed CSV or missing required columns cause bulk upload to fail early.

### 2. File Mapping Mismatches

- Filename normalization and lookup between CSV metadata and uploaded files are crucial.
- Potential mismatches due to case sensitivity, path elements, or filename encodings may cause "file not found" errors per row.
- Missing files lead to failed uploads per individual document, accumulating errors.

### 3. Asynchronous Background Processing and Concurrency

- Background task processes each CSV row asynchronously.
- Concurrent DB updates of job progress can race or fail silently, leading to stale or incorrect job statuses.
- Potential unhandled exceptions inside per-file processing cause partial failure or silent skips.

### 4. Document Creation Failures

- Failures during insertion into DB, file IO errors, or validation exceptions in `create_document` cause individual document processing failures.
- These failures may be logged but do not halt entire bulk upload, potentially confusing users.

### 5. Metadata Extraction Issues

- Metadata extraction is attempted post document creation in background.
- Failures in metadata processing do not fail upload but may cause incomplete document info.
- These errors should be tracked and surfaced appropriately.

### 6. Job State and Progress Tracking

- Job progress is maintained both in DB collection and an in-memory cache.
- Synchronization issues could lead to stale or missing job status updates.
- Cancelled or failed jobs need cleanup; potential for orphaned states.

---

## Corrective Action Plan

### CSV Handling

- Standardize CSV encoding handling to a single source (preferably in service).
- Add pre-upload CSV validation utility to catch format and encoding issues before job creation.
- Provide clearer user feedback on CSV errors including line numbers and error details.

### Filename Matching Improvements

- Enhance filename normalization to cover common user errors:
  - Trim paths, normalize case, remove special characters or whitespace glitches.
- Add detailed logging on filename mismatches to diagnose errors.
- Optionally provide pre-upload file sanity check to detect missing files.

### Background Job Robustness

- Ensure all per-file processing exceptions are caught and logged.
- Use task queue or job management system with retries for transient failures.
- Synchronize DB and in-memory job state updates optimally to prevent race conditions.

### Document Creation Resilience

- Augment document creation with retries on transient DB errors.
- Validate critical fields strictly and return detailed error messages.
- Surface document creation failures to job status and user interface.

### Metadata Processing Improvements

- Log metadata extraction issues separately from upload status.
- Offer retries or manual processing triggers for metadata failures.

### Job and Cache Synchronization

- Regularly reconcile in-memory and DB job states to detect inconsistencies.
- Implement cleanup tasks for orphaned or stuck jobs.
- Provide API to cancel and restart failed bulk upload jobs.

---

## Next Steps

- Review detailed logs of actual bulk upload failures from production environment for specific patterns.
- Create test cases covering diverse CSV encodings, filename edge cases, and large file sets to reproduce issues.
- Implement corrective actions incrementally with monitoring on each improvement.
- Enhance user-facing messages during bulk upload to improve transparency.

---

This document serves as a comprehensive investigation and roadmap for solving the bulk upload issues. Actual code changes should adhere to this plan.
