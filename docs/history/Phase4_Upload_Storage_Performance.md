# Phase 4 Upload and Storage Performance

## Implemented

The upload path now uses bounded-memory streaming helpers:

- `services/upload_streaming.py`
  - streams `UploadFile` to a temporary spool file
  - computes SHA-256 incrementally
  - keeps only a small MIME-validation sample in memory
  - inspects existing merged chunk files without loading them fully
- `services/upload_limits.py`
  - limits concurrent uploads per user and organization
  - uses Redis when runtime Redis is configured
  - falls back to in-process counters for local development
- `FileObjectService.store_path`
  - stores immutable file objects from a file path
  - avoids forcing local/S3 writes through a full in-memory bytes object
- `SecureFileService.store_existing_file`
  - copies a spooled file into canonical local storage
- `S3Service.upload_path`
  - uploads a file path to S3 using boto3 upload semantics

## Updated Flows

### General Documents

`POST /api/documents`

Previous behavior:

- read full upload into memory
- validate full byte buffer
- write provider copies from the same byte buffer

Current behavior:

- stream upload to temp spool file
- compute SHA-256 during stream
- validate MIME from sample
- write local/S3 providers from spool path
- create `file_objects` and `document_versions`
- clean up spool file

### Enclosures

`POST /api/documents/{id}/enclosures`

Current behavior:

- stream enclosure to temp spool file
- write provider copies from spool path
- store enclosure metadata with `file_object_id`, `storage_key`, provider locations, file type, and size

### Contract Multipart Uploads

`POST /api/contracts/upload-multipart`

Current behavior:

- stream each uploaded contract to temp spool file
- validate sample and size without keeping the whole file in memory
- write file object provider copies from spool path
- enqueue contract ingestion with `file_object_id`
- avoid creating an extra local processing copy for new queue jobs

### Contract Chunked Uploads

`POST /api/contracts/upload-chunk`

Current behavior:

- each chunk is bounded by chunk-size config
- final merged file is inspected by streaming from disk
- SHA-256 and MIME sample are computed without full `read_bytes()`
- file object provider copies are written from the merged file path
- ingestion job references `file_object_id`

## New Configuration

```env
UPLOAD_STREAM_CHUNK_SIZE_MB=1
UPLOAD_VALIDATION_SAMPLE_BYTES=8192
GENERAL_UPLOAD_MAX_FILE_SIZE_MB=100
UPLOAD_MAX_CONCURRENT_PER_USER=3
UPLOAD_MAX_CONCURRENT_PER_ORG=20
```

## Remaining Work

Direct-to-S3 multipart upload is still the recommended long-term target for very large uploads.

Remaining tasks:

1. Add API endpoints to create S3 multipart sessions and presigned part URLs.
2. Let browsers upload parts directly to S3.
3. Complete multipart upload server-side.
4. Build `file_objects` from the completed S3 object metadata and checksum.
5. Add resumable upload manifests and client progress events.
6. Add queue/job metrics for upload throughput and failure rate.

## Operational Notes

- Runtime Redis should be configured in production so concurrency limits work across all API replicas.
- MIME validation still intentionally uses a small sample. Deep content inspection should run asynchronously in workers after upload.
- Existing API contracts and response formats are unchanged.
