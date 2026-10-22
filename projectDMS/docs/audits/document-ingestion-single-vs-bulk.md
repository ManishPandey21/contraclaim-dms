# Document Ingestion — Single vs Bulk Upload, Capacity and UX

Read-only audit, 2026-09-27, working tree on `integrate/key-date-eot` @ `a179ca9` (dirty). Paths relative to `backend/rbac_backend/` unless prefixed `client/src/`.
See also: [current state](document-ingestion-current-state.md) · [issues](document-ingestion-issues.md).

## 1. What "bulk" means today

There are three different multi-file mechanisms, none of them a manifest + independent durable jobs:

| Mechanism | Where | Shape |
|---|---|---|
| Multi-select on the single tab | `client/src/pages/UploadPage.tsx:658-683` | **frontend loop of single uploads**, sequential `await` per file → `POST /documents` |
| Bulk tab (folder + CSV) | `UploadPage.tsx:1469`; `client/src/services/enhanced-api.ts:1053-1068` | **one multipart request** carrying a CSV manifest plus every file (`formData.append("files", file, relativePath)`) → `POST /documents/bulk-upload` (`routers/documents.py:3109`) → one in-process FastAPI `BackgroundTask` (`:1028`) that loops the CSV rows **sequentially** (`:1311-1345`) calling `create_document` per row |
| Contract multi-file | `client/src/pages/ContractsUploadPage.tsx:611-619`; `routers/contracts.py:299-478` | sequential client loop (small files may be batched into one multipart request); server loops files sequentially inside the request |

Not supported: ZIP bulk (ZIPs are stored intact, never unpacked, never processed), presigned direct-to-S3 upload, resumable manifest, server-side fan-out queue for bulk. `BulkUploadService.process_documents_with_metadata` (a worker-pool variant) references a missing `settings.BULK_UPLOAD_MAX_WORKERS` (`services/bulk_upload_service.py:500-512`) and has **no caller** — dead code.

## 2. Limits and controls

| Control | Value | Evidence |
|---|---|---|
| Max files per bulk | 100 | `core/config.py:150`, checked `documents.py:3138-3157` |
| Max bulk total | 500 MB | `config.py:151` — but nginx `client_max_body_size 200m` (`config/nginx-contraclaim.conf:26`) rejects anything over 200 MB first |
| Max file (documents) | 100 MB | `config.py:154`, streaming check `services/upload_streaming.py:71` |
| Max file (contracts) | 50 MB; chunks 5 MB | `config.py:423-424` |
| Upload concurrency | 3 per user, 20 per org | `services/upload_limits.py`, `config.py:194-195` |
| Request rate limiter | none on `/documents`, bulk, contracts (only folder upload) | — |
| Client-side size check | none (documents, enclosures, bulk); insurance hard-coded 20 MB | `UploadPage.tsx` fetches `max_size_mb` but never uses it |
| Byte progress | none (`fetch`, no `onUploadProgress` anywhere) | — |
| Request buffering | Starlette spools every part to disk; CSV read fully into memory (`bulk_upload_service.py:135`); each file copied to `NamedTemporaryFile` (`:244-289`) and re-spooled by `create_document` ⇒ ~3 disk copies per file | — |
| Worker concurrency (documents) | 1 job at a time per `document-worker` replica; `process_next_processing_jobs(limit=3)` per tick, 1 s / 3 s sleep | `services/background_jobs.py:448-490` |
| Worker concurrency (contracts) | `CONTRACT_QUEUE_WORKERS=1` | `config.py:437` |
| Backpressure | queue growth only; no depth limit, no per-tenant fairness (FIFO on `created_at`); **no indexes on `document_processing_jobs`** ⇒ collection scan + sort every 1–3 s | inferred from absence in `core/database.py` / migrations |
| Per-document transaction | each CSV row is its own `create_document` with compensation (`documents.py:678-685, 912-944`) | — |
| Bulk-level status | `bulk_upload_jobs` (no org/user stored), progress rewritten after every row with the full `results` array | `documents.py:1013-1022, 1325` |
| File-level status | per-row `DocumentProcessingResult`; status endpoint enriches with `processing_status`, `processing_stage`, `duplicate_status` (`documents.py:1545-1608`) | — |
| Retry | none per file; no resume | — |
| Cancel | `cancel_job` exists (`bulk_upload_service.py:667`) but **no route**; the loop never checks it; UI "Cancel" only clears local state | `UploadPage.tsx:1554-1573` |
| Duplicate prevention | `precheck_upload` per row (sha256 409 / letterNo pending) — non-atomic; duplicate filenames in one request collapse in the lookup map (last wins, `documents.py:1076-1091`) | — |
| Progress reporting | UI polls `GET /documents/bulk-upload/{job_id}/status` every 3 s, max 200 polls (~10 min) (`UploadPage.tsx:1489-1541`); endpoint does not check job owner/org (IDOR, uuid4-limited, `documents.py:1531-1532, 3169-3185`) | — |

## 3. The required scenario answers

**If file 37 of 100 fails, what happens?** The row's exception is caught (`documents.py:1329-1345, 1463-1470`), recorded in `results`, and the loop continues. Files 1–36 are already committed (documents + durable processing jobs). The bulk job ends `completed_with_errors`.

**Can the other 99 complete?** Yes — for *intake*. Each committed row gets its own durable P1 job, processed independently by the worker. Caveat: if the API process restarts mid-batch, the BackgroundTask dies, remaining rows are never created, their temp files are gone, and `bulk_upload_jobs` stays `processing` forever (no recovery).

**Can only file 37 be retried?** **No.** There is no per-file retry endpoint and no resume; temp files are deleted in `finally` (`documents.py:1385-1391`). The user must build a new CSV/folder containing file 37; re-sending the whole batch gives 409 for the 99 already-committed files, which count as row failures. At processing level there is also no manual job-retry endpoint for a single document (only the inline `/process`).

**Can the same bulk request accidentally create duplicates?** Yes, in three ways: (1) a double-submitted request runs two BackgroundTasks concurrently that can both pass the non-atomic sha256 precheck (sha256 is `$set` after insert, failure swallowed, no unique index); (2) if the post-insert `sha256` write fails, later uploads of the same file are not detected; (3) duplicate job creation is find-then-insert without a unique index. Conversely, two different files with the same relative name silently collapse to one.

**Can bulk ingestion overwhelm OCR, embeddings, Mongo or Qdrant?** Not OCR/embeddings/Qdrant directly: intake is sequential and processing is a single-consumer queue per replica, so those are *throttled* — the failure mode is **latency** (a 100-file batch of scanned letters queues behind one serial worker; one hung legacy OCR job with no timeout blocks the whole replica indefinitely, and a poison job re-runs hourly forever because claim ignores `max_attempts`). The real pressure points are: the **API event loop** (synchronous boto3 uploads, synchronous Falkor sync), **disk** (3 copies per file, never-cleaned `tmp/document_processing` and OCR batch dirs), **Mongo** (unindexed job polling; O(n²) bytes for `results` rewrites), and the 200 MB nginx cap.

## 4. Capacity estimates (derived from code, not measured)

Assumptions: typical letter 5–15 pages, 1–3 MB; per-document processing on legacy path ≈ OCR (0–60 s, scanned only) + LLM #1 (5–30 s) + optional LLM #2 + two embedding passes + Falkor + reference sync; one `document-worker` replica.

| Batch | Upload | Intake (API) | Processing queue | Risks |
|---|---|---|---|---|
| 10 files | one request ≤200 MB OK | seconds; sequential | ~2–10 min serial | minimal |
| 100 files | one request; must be ≤200 MB total (nginx) despite 500 MB config; no byte progress; one HTTP request held for the whole transfer | minutes in one BackgroundTask; restart = lost tail | ~30–120 min serial per replica | double-submit duplicates; UI polling cap (~10 min) ends before processing does; rows in `human_review_required`/`partially_processed` show "Processing" forever |
| 1 000 files | **not possible** in one bulk (100-file cap); user must run 10+ batches or loop singles | 10 × BackgroundTasks, each non-durable | 5–20 h serial per replica; FIFO means one tenant's backlog starves others | unindexed polling cost grows with history; per-document double embedding cost; LLM spend 2× per document; disk leak of temp/OCR dirs |

Memory: whole file loaded into memory at materialize (`services/document_service.py:1701`); pdfplumber opens the whole PDF; unified path reads text+tables for every page twice. LLM input is untruncated on the default path — large documents overflow context and fall back to regex (inferred). Embedding batch sizes are library defaults. **No unbounded fan-out** exists inside the document pipeline (everything is sequential); the only unbounded memory growth is `VectorClient._memory_index` (`retrieval/vector_client.py:239-241`), appended on every upsert even when Qdrant is on.

## 5. What the user sees

| Capability | Exposed? | Where | Endpoint |
|---|---|---|---|
| Single/multi upload | yes, sequential loop, stops at first error without listing what succeeded | `UploadPage.tsx:658-683` | `POST /documents` |
| Upload byte progress | **no** | — | — |
| Single-doc processing status | **broken**: viewer polls `GET /documents/{id}/processing-status`, which **no router registers** (re-verified); 404 → `not_queued` → banner hidden, polling stops, Retry button hidden | `client/src/pages/DocumentViewerPage.tsx:201-290, 803-888` | none |
| Bulk status | yes, 3 s polling, stage labels | `UploadPage.tsx:60-81, 1489-1541` | `GET /documents/bulk-upload/{id}/status` |
| States the UI understands | stages `queued, started, materializing, extracting, metadata_updated, syncing_references, duplicate_classification, duplicate_detected, syncing_falkor, completed, failed`; statuses `queued, processing, metadata_extracted, retrying, completed, failed` | `UploadPage.tsx:60-81` | — |
| States the backend emits that the UI does not know | `human_review_required`, `partially_processed`, `stored_only`, `skipped`, `cancelled`; stages `claimed`, `recovered_stale`, `awaiting_page_resume`, `skipped_deleted_document` | `models/processing_state.py:26-48` | — |
| Page failures / partial success / degraded markers / attempts | **no** | — | — |
| Retry processing | hidden (bug above); backend runs whole pipeline **inline in the API request**, no job id, always `success=True` | `DocumentViewerPage.tsx:877-888`; `routers/documents.py:1491-1506, 2608-2628` | `POST /documents/{id}/process` |
| Reindex | contracts only | `client/src/pages/ContractViewerPage.tsx:290` | `POST /contracts/{id}/reindex` |
| Contract OCR page retry | **no UI** | — | `POST /contracts/{id}/ocr/retry` |
| Cancel | bulk: client-only; single: dead button; contracts: client abort | `UploadPage.tsx:944, 1554` | none |
| Per-file retry in bulk | **no** (contracts: re-upload) | — | — |
| Bulk resume after refresh | **no** (job id only in React state; no job-list endpoint) | `UploadPage.tsx:209` | — |
| Extracted metadata preview | yes, read-only cards | `client/src/pages/LetterSummaryPage.tsx:537-845` | `GET /documents/{id}` |
| Edit metadata | core fields (PUT) and 6 extracted fields + keywords/tags (PATCH); **summary, key reply points, clauses not editable** | `components/document-viewer/MetadataEditor.tsx`; `LetterSummaryPage.tsx:471-516` | `PUT /documents/{id}`, `PATCH /documents/{id}/summary-metadata` |
| Markdown / OCR text preview | **no** (no backend text endpoint) | — | — |
| Original file | PDF only; images/text show "not a PDF"; dev fallback `/letter1.pdf` | `components/document-viewer/DocumentViewer.tsx:67-251` | `GET /documents/{id}/download` (307 → S3 presigned) |
| References | yes (link/unlink/sync) | `ReferencesPanel.tsx`, `ReferencePage.tsx:587-660` | `/documents/{id}/references`, `/linked`, `/sync-references` |
| Keywords / tags | yes (editable) | LSP | PATCH |
| Points to address while replying | view only | LSP:813-844 | — |
| Processing history | **synthetic** (built client-side from createdAt/updatedAt); real endpoint unused | `DocumentViewerPage.tsx:716-751` | `GET /documents/{id}/audit-events` (unused) |
| Human review / confidence | **no** | — | — |

Other UX defects: bulk "Batch Processing Options" checkboxes are never sent; one `letterNo` is applied to every file in a multi-file single upload (`UploadPage.tsx:661-662`); single tab says "Drag files here" but has no drop handler; header renders undefined pages/size; a mock `relatedDocuments` array is shown; the legacy `ContractsPage` chunked path is broken above 15 MB.

**Verdicts:** BULK PARTIAL SUCCESS = **YES** (intake continues past a failed row). FAILED-FILE RETRY = **NO**.
