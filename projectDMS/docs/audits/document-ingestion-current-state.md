# Document Upload & Ingestion — Current State (AS-IS)

**Audit type:** read-only investigation. No production source modified.
**Date:** 2026-09-27
**Tree audited:** `C:\SaaS` working tree, branch `integrate/key-date-eot` @ `a179ca9` **plus uncommitted changes** (the tree is dirty; findings describe the files on disk, not a commit). Production may run a different branch — read the server before assuming any finding is live.
**Method:** six parallel code investigations (upload/security, correspondence pipeline, metadata/references, contract pipeline, Markdown/lineage/search, frontend UX) with file:line evidence, a focused Graphify AST graph (`docs/audits/document-ingestion-graphify/`), and targeted re-verification by the lead auditor of every BLOCKER and the most consequential HIGHs (see *Verification ledger* at the end).

Companion files:
[metadata matrix](document-ingestion-metadata-matrix.md) ·
[single vs bulk](document-ingestion-single-vs-bulk.md) ·
[Markdown & lineage](document-ingestion-markdown-lineage.md) ·
[issue register](document-ingestion-issues.md) ·
[target architecture](document-ingestion-target-architecture.md) ·
[graphs](document-ingestion-graphs.md)

Paths below are relative to `backend/rbac_backend/` unless prefixed `client/src/` or repo-relative.

---

## 1. Headline answer: do the paths share one pipeline?

**No.** There are at least **four** distinct ingestion mechanisms, sharing only low-level infrastructure:

| # | Pipeline | Trigger | Queue | Worker | Default in prod |
|---|---|---|---|---|---|
| P1 | Correspondence / generic document, **legacy_v0** | `POST /documents`, bulk rows, insurance attachments | Mongo `document_processing_jobs` (polled) | `document-worker` container (`python -m rbac_backend.worker`, `START_DOCUMENT_EXTRACTION_WORKERS=true`) | **Yes** — `UNIFIED_EXTRACTION_ENABLED` defaults false, `DOCUMENT_WORKER_PIPELINE_VERSIONS` defaults `legacy_v0` (`docker-compose.prod.yml:268,284`; `services/pipeline_routing.py:23-52`) |
| P2 | Correspondence, **unified_v1** (page-level engine + quality gate) | same | same collection, claim filtered by `pipeline_version` | canary worker (`docker-compose.prod.yml:326-331`) | Canary orgs only |
| P3 | Contract documents | `POST /contracts/upload-multipart`, `/upload-session` + `/upload-chunk` | **Redis** list `contract_ingest_queue` | `contract-worker` container | Yes |
| P4 | `ingestion/` IngestionPipeline | manual `POST /v1/ingestion/jobs` (`routers/retrieval_engine.py:124-135`) | **in-memory** `BackgroundJobProcessor` in API process | API process | Manual only; the upload path never feeds it |

Plus two non-pipelines: **Contract Master governance** (`routers/contract_master_api.py`, `contract_documents`) which has no upload path and no production writer that ever makes a projection `CURRENT`, and **inline reprocess** (`POST /documents/{id}/process`) which runs P1 inside the API request, bypassing the queue.

**Shared infrastructure:** `upload_streaming` spooling + SHA-256, `utils/file_validation.py` magic-byte sniffing, `AntivirusService`, `FileObjectService`/`StorageKeyBuilder` (local + S3), `PolicyService`, the `documents` collection, `PageExtractionEngine` + `OcrMyPdfRunner` (P2 and P3 only — **not** P1), `publication_policy`, Qdrant, FalkorDB.

---

## 2. Upload entry-point inventory

| UI screen | Frontend fn | Endpoint | Method | Single/bulk | Family | Max size | Formats | Authz | Service | Queue / worker | Collections | Initial status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `client/src/pages/UploadPage.tsx` single tab (sequential loop `:658-683`) | `enhancedApi.uploadDocument` (`client/src/services/enhanced-api.ts:1009-1052`) | `/documents` (`routers/documents.py:2536`) | POST | single (client loops) | correspondence / generic | 100 MB (`core/config.py:154`, enforced while streaming `services/upload_streaming.py:71`) | pdf, png, jpeg, txt, zip (+rar if flag) by magic bytes (`core/config.py:203-212`) | `dms.document.upload` ×3 (`documents.py:2562, 688, 790`) | `DocumentController.create_document` → `DocumentService.create_document` (`services/document_service.py:482`) | Mongo `document_processing_jobs` only if `ocr_enabled` and not archive (`documents.py:894`) → `document-worker` | `documents`, `file_objects`, `document_versions`, `document_processing_jobs` | doc `status` caller value (default `draft`), `lifecycle_state` `active`/`duplicate_review`; job `queued`, `processing_status` `queued` |
| `UploadPage.tsx` bulk tab (`:1469`) | `enhancedApi.startBulkUpload` (`enhanced-api.ts:1053-1068`) | `/documents/bulk-upload` (`documents.py:3109`) | POST | **bulk: one multipart request = CSV manifest + N files** | correspondence / generic | ≤100 files, ≤500 MB total (`config.py:150-151`); nginx body cap **200 MB** (`config/nginx-contraclaim.conf:26`) | same | `dms.document.upload` | FastAPI `BackgroundTasks` `_process_bulk_upload` (`documents.py:1279`) → `create_document` per row, **sequential** (`:1311-1345`) | in-process, non-durable; each row then P1 job | `bulk_upload_jobs` + per-row as above | bulk `processing` → `completed` / `completed_with_errors` / `failed` |
| `client/src/components/document-viewer/EnclosuresPanel.tsx:83` | authenticatedFetch loop | `/documents/{id}/enclosures` (`documents.py:2958`) | POST | single (client loop) | enclosure | 100 MB | same list | `dms.document.edit_metadata` | `controller_add_enclosure` (`:1832`) | **none** (not processed, not indexed) | `documents.enclosures[]`, `file_objects` | n/a |
| `client/src/pages/ContractsUploadPage.tsx:496` | `uploadContractsMultipart` (`client/src/services/contracts-api.ts:128`) | `/contracts/upload-multipart` (`routers/contracts.py:299`) | POST | multi-file one request, server-sequential | contract | 50 MB (`config.py:423`) | pdf, docx (+extension allow-list `services/contract_service.py:166`) | `dms.document.upload` | `create_contract_document` (`contract_service.py:272`) | Redis `contract_ingest_queue` → `contract-worker` | `documents` (`uploadType=contract`), `contracts`, `contract_versions`, `contract_upload_sessions`, `contract_ingest_jobs` | doc `queued` |
| `ContractsUploadPage` (>5 MB) | `uploadContractInChunks` (`contracts-api.ts:153`) | `/contracts/upload-session` → `/contracts/upload-chunk` (`contracts.py:275, 481`) | POST | single, chunked (5 MB) | contract | 50 MB | pdf, docx | same | same | same | same | same |
| `client/src/pages/ContractsPage.tsx` (legacy `/contracts`) | same fns | same | POST | batch ≤15 MB, chunk above | contract | — | — | — | — | — | — | **broken >15 MB** (no `uploadId`, `ContractsPage.tsx:175-186` vs `contracts-api.ts:167-169`) |
| `client/src/pages/InsuranceRegisterPage.tsx:641` | `uploadInsuranceDocument` (`client/src/services/insurance-api.ts:131`) | `/insurance/{id}/documents/upload` (`routers/insurance.py:360`) | POST | single | register attachment | client 20 MB / server 100 MB | pdf/jpg/png client-side | `INSURANCE_EDIT` then `DOCUMENT_UPLOAD` | `create_document` + link | P1 job always (`ocr_enabled=True`, `:410`) | `documents` + relationship | `draft` |
| Folder UI (inferred) | — | `/upload-file` (`routers/folder_structure.py:465`) | POST | single | folder file | 100 MB **checked after full `read()`** (`:187, 324`) | document list | `dms.document.upload` + folder access + rate limiter | `folder_service.create_file` | none | folder collection | n/a — **no AV, no dedupe** |
| BG / key-date register imports | `bank-guarantees-api.ts`, `key-dates-api.ts` | `/bank-guarantees/import*`, `/key-dates/import*`, `/key-dates/eot-*/{id}/import*` | POST | CSV rows | register data (not documents) | **unbounded `read()`** (`bank_guarantees.py:85`, `key_dates.py:88`) | `.csv` | `BG_CREATE` etc. | `import_*_csv` | none | register collections | — |
| Deep planning | — | `/deep-planning/analyze-document` (`deep_planning.py:1049`) | POST | single, not persisted | ad-hoc LLM input | **none** | none (decoded as UTF-8) | `dms.document.view` | LLM | — | — | — |
| Profile | `enhanced-api.ts:1679` | `/profiles/photo` (`profiles.py:116`) | POST | single | avatar | 5 MB after full read | client `content_type` trusted | authenticated | base64 into `users` | — | — | — |

Facts: **no presigned upload URLs exist** (presign is used only for download redirects, `documents.py:2728`, `contracts.py:978`). Claims, IPC bills, letters and key dates accept no binaries; they link existing documents. Contract Master has **no** upload endpoint; `UploadDurabilityService`/`ContractUploadScopeService` are imported in `contract_master_api.py:87-97` but never instantiated outside tests.

---

## 3. Single correspondence upload — end-to-end trace (P1 legacy, production default)

| # | Step | File / function | Input → output | DB mutation | Status mutation | Failure | Retry | Idempotency |
|---|---|---|---|---|---|---|---|---|
| 1 | Browser | `UploadPage.tsx:658-683` | files → one `fetch` per file, **sequential**, no byte progress | — | — | loop stops at first error; earlier files stay uploaded | user re-upload → 409 duplicate | none client-side |
| 2 | API client | `enhanced-api.ts:1009-1052` | FormData(file, org_id, project_id, uploadType, letterNo…) | — | — | toast | — | — |
| 3 | Endpoint + authz | `routers/documents.py:2536` → `create_document` `:662` | `PolicyService.authorize(dms.document.upload)` on body org/project | — | — | 403 | — | — |
| 4 | Concurrency slot | `services/upload_limits.py` | 3/user, 20/org (Redis or local dict) | Redis counter | — | 429 | client | — |
| 5 | Streaming + validation | `services/upload_streaming.py:53-184` | spool to disk, SHA-256, magic-byte MIME, reject MZ/ELF/Mach-O, 100 MB cap | — | — | 400/413 | — | — |
| 6 | Letter-no rule | `documents.py:728-738` | letterNo required except archives | — | — | 400 | — | — |
| 7 | Antivirus | `services/antivirus_service.py`, called `documents.py:741` | ClamAV INSTREAM | — | — | defaults `ANTIVIRUS_ENABLED=False`, `CLAMAV_FAIL_OPEN=True` (`config.py:232,242`); prod validator forces enabled + fail-closed (`config.py:838-843`) | — | — |
| 8 | Duplicate precheck | `duplicate_detection_service.precheck_upload` (`:143-191`), `documents.py:752` | sha256 exact → 409; letterNo match → `duplicate_status=pending` hold | — | `lifecycle_state=duplicate_review` | 409 | — | **non-atomic**: `sha256` set after insert by best-effort `update_one` (`documents.py:853-870`), no unique index |
| 9 | Store original | `FileObjectService.store_path` (`services/file_object_service.py:253-310`), key `StorageKeyBuilder` (`services/storage_key_builder.py:37-89`) | `/{org}/{proj}/{incoming\|outgoing}/{YYYY}/{MM}/{uploadId}_{letterNo\|stem}{ext}` on every enabled provider | `file_objects` (dedupes on sha256+org+project+type, `:98-107`) | — | succeeds if ≥1 provider; partial provider failure recorded `status="error: …"`, not retried | — | fresh ObjectId per upload → no key collision; original **not overwritten** |
| 10 | Document record | `DocumentService.create_document` (`document_service.py:482`) | metadata from form | `documents`, `document_versions` | `status` default `draft` | compensation on error (`documents.py:912-944`) | — | — |
| 11 | Job enqueue | `queue_document_processing` (`document_service.py:860-951`) | find-then-insert | `document_processing_jobs` {`status:queued`, `max_attempts:3`, `pipeline_version`} | doc `processing_status=queued` | — | — | **race**: no unique index on `document_id` |
| 12 | Claim | `process_next_processing_jobs(limit=3)` (`services/background_jobs.py:448-474`), `find_one_and_update` (`document_service.py:998-1033`) | oldest `queued|retrying` with `run_after<=now` | job `processing`, `attempts+1` | doc `processing` | — | stale recovery after `DOCUMENT_PROCESSING_STALE_AFTER_SECONDS=3600` (`core/config.py:427`) | **claim does not check `attempts<max_attempts`**; no owner token / fencing |
| 13 | Materialize | `document_service.py:1693-1712` | S3 `download_bytes` → whole file in memory → `tmp/document_processing/<filename>` | — | stage `materializing` | — | — | temp name not unique, never cleaned |
| 14 | File-type detection | legacy: **none** (treated as PDF) | — | — | — | non-PDF paths degrade | — | — |
| 15 | Text / OCR decision | `OCRService.process_pdf` (`services/ocr_service.py:101-163`), `is_pdf_textual` (`:64-99`) | **document-level**: any text on first 5 pages ⇒ skip OCR for whole file | — | stage `extracting` | OCR exception ⇒ copy original, return `None` (`:275-286`); encrypted PDF silently no text | job retry | — |
| 16 | Metadata (LLM #1) | `DocumentProcessor._extract_and_persist` (`services/document_processor.py:783-885`), `OpenAIService.process_text` (`services/openai_service.py:136-182`) | full OCR text (untruncated) → 27-field **numbered text** report (not JSON), 60 s timeout, 4096 output tokens; no text ⇒ upload PDF to OpenAI Responses | — | — | exception ⇒ regex fallback `_build_ocr_fallback_report` (`:823-838`) using **filename as letterNo/subject** | — | — |
| 17 | Metadata (LLM #2, optional) | `PydanticAIService.extract_metadata` (`services/pydantic_ai_service.py`) | input cut to 12 000 chars | — | — | failure → regex, not recorded | — | — |
| 18 | Parse | `TextProcessingService.parse_extraction_report` (`services/text_processing_service.py:140-318`) | regex by number/label → `ParsedDocumentMetadata` | — | — | **any exception ⇒ empty metadata, silently, still success** (reproduced: DI-B3) | — | — |
| 19 | "Markdown" | `FileService.save_summary` (`services/file_service.py:17-73`) | **append** report to `uploads/{org}/{proj}/incoming.md\|outgoing.md` | local file | — | — | — | append-only, duplicates on reprocess |
| 20 | Persist metadata | `DatabaseService._upsert_document_metadata` (`services/database_service.py:161-338`) | `ocrText`, `full_text` (= LLM "Full Content"), subject, letterNo, from, to, summary, keywords, tags… | `documents` | — | fallback lookup by **filename across tenants** (`:356-359`) | — | overwrites in place, no revision |
| 21 | Chunk + embed | `database_service.py:363-616` | `RecursiveCharacterTextSplitter` 3000/200; `deterministic_chunk_id` (`ingestion/chunk_ids.py:6-18`) | `vector_sync_status` | `pending → qdrant_synced → synced\|mismatch\|mongo_only\|empty` | **exceptions swallowed** into `partial_failures.embeddings` (`:208-221`) | none | deterministic ids |
| 22 | Qdrant | `LangChainVectorService.replace_document` (`services/langchain_vector_service.py:240-323`) | delete-by-doc then `add_texts`; point id `uuid5(...)`; payload nested under `metadata.*` | Qdrant `document_vectors` | — | **any exception ⇒ return 0** (`:321-323`) | none automatic | delete-then-add not atomic |
| 23 | Mongo vector mirror | LlamaIndex `get_text_embedding_batch` (second embedding pass) | — | `document_vectors` delete + `insert_many` | — | swallowed | — | — |
| 24 | Status write | `document_service.py:1795-1856` | — | metadata fields | `metadata_extracted` or `human_review_required` | — | — | — |
| 25 | Graph + evidence | `_publish_graph_and_evidence_from_current` (`:151-198`) | `graph_ingestion.ingest_document`, `evidence_graph.ingest_document_metadata` | Falkor, `ai_extractions` | — | **swallowed at debug level** | — | MERGE |
| 26 | References | `ReferenceSyncService.sync_bidirectional` (`services/reference_sync_service.py`) | resolve refs by `letterNoNormalized` in org+project | `documents.references/referencedBy`, `reference_sync_queue` | stage `syncing_references` | raises ⇒ **whole pipeline retried** | job retry | bucket-replace |
| 27 | Falkor sync | `_sync_current_document_to_falkor(raise_on_error=True)` → **synchronous** `sync_document_to_falkor` (`graph/graph_ingestion_service.py:373`) | Letter nodes MERGEd on `normCode` only | Falkor | stage `syncing_falkor` | raises ⇒ whole pipeline retried (OCR + 2 LLM calls again) | job retry → `dead_lettered` | MERGE |
| 28 | Duplicate stage-2 | `classify_pending_document` (`duplicate_detection_service.py:197-275`) | only if `duplicate_status=pending` | `duplicate_status`, `lifecycle_state` | stage `duplicate_classification` | — | — | — |
| 29 | Complete | `process_document_job` (`document_service.py:1188-1249`) | reads job checkpoint | job `completed` | doc `processing_status=completed` | **no check of `vector_sync_status`, metadata emptiness, or page completeness** | — | — |
| 30 | Search / UI | see §8 and [single vs bulk](document-ingestion-single-vs-bulk.md) | — | — | — | viewer polls **non-existent** `GET /documents/{id}/processing-status` (`enhanced-api.ts:1001-1007`) | — | — |

Failure path: `_mark_processing_failure` (`document_service.py:1533-1566`) → job `retrying` with backoff `min(300, 30*attempts)` s, or `dead_lettered` at `attempts ≥ max_attempts`; doc `retrying` / `failed`.

## 4. Single contract-document upload — end-to-end trace (P3)

| # | Step | File / function | Notes |
|---|---|---|---|
| 1 | UI | `ContractsUploadPage.tsx:444-619` | `.pdf,.docx`; small files multipart, >5 MB chunked; sequential; per-file retry re-uploads; AbortController cancel |
| 2 | Endpoint | `routers/contracts.py:299` / `:275`+`:481` | `dms.document.upload` at session, per file, metered; scope via `ContractService._resolve_scope` (`contract_service.py:120-155`); session pins org/project, mismatch 403 |
| 3 | Validation | `contracts.py:364, 369, 599, 605` | MIME allow-list after spooling; AV if enabled; **no duplicate precheck** (sha256 stored, never compared) |
| 4 | Store original | `contracts.py:180-230` | `/{org}/{proj}/contracts/{uploadId}_{file}`; `file_objects` silent sha256 dedupe ⇒ new contract rows share old blob |
| 5 | Records | `contract_service.py:291-465` | `documents` (`uploadType=contract`, `status=queued`), `contracts`, `contract_versions`, `contract_upload_sessions` (`open→uploading→queued`), `contract_ingest_jobs` |
| 6 | Enqueue | `services/contract_ingest_queue.py:191-217` | Redis hash `contract_ingest_job:{upload_id}`; enqueue is **no-op if queued/running** (drops new payload, e.g. reindex); enqueue failure ⇒ doc `failed`, stage `queue_failed`, no auto-retry |
| 7 | Claim | `:303-386` | `BRPOPLPUSH` to processing list, `running`, attempts+1, heartbeat; visibility timeout 1800 s; 1 worker task |
| 8 | Ingest | `ContractService.process_ingest_job` → `ContractIngestor.ingest_file` (`services/contracts_ingest.py:1441-1829`) | publication-authority check before each store (`:1481, 1700, 1718, 2260`) |
| 9 | Pages / OCR | `PageExtractionEngine` (`services/extraction/engine.py`) with `ContractPageStore` → `contract_ocr_pages`, `contract_ocr_batches` | **page-level**; OCR if native text <40 chars; batches of 25; DOCX bypasses engine (`contracts_ingest.py:1250-1262`); engine `completeness=PARTIAL` **ignored** (`:1390-1398`) |
| 10 | Clean / categorize | `ContractTextPreprocessor`, `_categorize_text` | empty ⇒ `DocumentParsingError` |
| 11 | Marker | `contracts_ingest.py:438-550` | optional `.md` on local disk; **not used for chunking** (`markdown_text = text`, `:1548`); `subprocess.run` with no timeout |
| 12 | Clauses (ingest) | regex `ClauseExtractor` (`:1597`); AI spans only if `contract_ai_chunking_enabled` (default False) | payloads → Mongo `document_vectors` (delete+insert) and Qdrant default collection, `chunking_version=contract_clause_v1`, rich page/offset lineage |
| 13 | Graph | `ContractGraphService.upsert_contract_graph` (`services/contract_graph_service.py:87-216`) | synchronous MERGE; skipped without project; never deletes |
| 14 | Zero-vector guard | `contracts_ingest.py:1728-1734` | only when Qdrant enabled and payloads non-empty |
| 15 | Clause-first index | `_run_clause_indexing_safe` (`contract_service.py:489-507`) → `ClauseChunkingAgent.process_document` (`services/contract_clause/agent.py`) | reads `contract_ocr_pages`; upserts `contract_clauses` by `clause_uid` (SHA-1 hex); Qdrant namespace `contract_clauses` with **SHA-1 hex as point id** (Qdrant accepts only uint/UUID); **all failures swallowed**; DOCX and org-scoped uploads ⇒ 0 clauses |
| 16 | Complete | `contract_service.py:585-613` | doc `completed`; `contract_ingest_jobs` written by two writers; first failure shows `failed` while Redis retries |
| 17 | On-demand | `/contracts/{id}/reindex` (`contracts.py:821`), `/contracts/{id}/ocr/retry` (`:749`, not in UI), `/contracts/{id}/clauses/index` (`routers/contract_clauses.py:68`, web-tier task), `/contracts/appraisal/generate` (`routers/contract_appraisal.py:55`, in-process LLM task, 21 sections) | none of these touch `contract_documents` projection state |

**Contract Master (governed) is disconnected.** `ProjectionStatus` PENDING/IN_PROGRESS/CURRENT/FAILED (`models/contract_document.py:151-158`). Promotion and reclassification only ever set `PENDING` (`services/contract_promotion.py:272`, `services/contract_classification_service.py:173`). `ContractReprojectionWorker` and `PostPromotionSequencer` — the only writers of `CURRENT` — have **no non-test caller** (re-verified by grep). `contract_scope_resolver.py:231-239` requires `CURRENT`, so `/contract-master/evidence/search` always returns `outcome="valid_empty"`.

---

## 5. OCR decision

| Question | P1 legacy (prod default) | P2 unified (canary) | P3 contracts |
|---|---|---|---|
| Level | **document** (first 5 pages sampled, `ocr_service.py:64-99, 141-151`) | **page** (`engine.py:97-163`) | **page** |
| Criterion | any non-blank text on pages 1–5 ⇒ no OCR at all | native text <40 chars and not UNRENDERABLE | same, `CONTRACT_OCR_MIN_TEXT_CHARS_PER_PAGE=40` |
| Mixed PDF (digital cover + scanned annexures) | **scanned pages silently lost** | only thin pages OCR'd | only thin pages OCR'd |
| CID `(cid:N)` garbage | not detected | **not detected** (only in `contracts_ingest.py`) | detected |
| Blank page | n/a | `ocr_empty` ⇒ unresolved ⇒ forced human review after 3 attempts | ignored (PARTIAL ignored) |
| OCR overwrites good text | no | no within one attempt (override only for candidates with non-empty OCR, `engine.py:204-247`) | no within one attempt |
| Retry preserves resolved pages | n/a (no page records) | **NO** — retry OCRs only `retry_pages`, rebuilds all others from native text with status `text_layer`, and `ReplaceOne` on `(document_id, extraction_run_id=job_id, page_number)` overwrites earlier OCR rows (`engine.py:100-141, 226-228`; `extraction_adapters/document_page_store.py:167-180`; run id = job id, `document_service.py:1746`) — **re-verified** | **NO** — same engine; `/contracts/{id}/ocr/retry` then rebuilds `document_vectors` from degraded text |
| After OCR failure | copy original; LLM reads the PDF; LLM "Full Content" becomes embedded/authoritative text | pages `ocr_failed` ⇒ `partially_processed` ⇒ resume ⇒ `human_review_required` | pages fail but doc `completed` if any text |
| OCR process | `ocrmypdf.ocr()` in-process thread, **no timeout** (`ocr_service.py:247-259`) | CLI subprocess, 900 s timeout (`services/extraction/ocrmypdf_runner.py`) | CLI subprocess 900 s; Marker no timeout |

Verdict: **OCR DECISION = MIXED** (document-level on the production correspondence path; page-level on canary correspondence and on contracts).

---

## 6. Status vocabularies (real constants only)

- `documents.processing_status` (`models/processing_state.py:26-37` + literals): `queued`, `processing`, `retrying`, `completed`, `failed`, `metadata_extracted`, `human_review_required`, `partially_processed`, `stored_only`, `skipped`.
- `document_processing_jobs.status`: `queued`, `processing`, `retrying`, `completed`, `dead_lettered`, `human_review_required`, `stored_only` (`failed` is read at `document_service.py:1252` but never written).
- Job `stage`: `queued`, `claimed`, `started`, `recovered_stale`, `materializing`, `extracting`, `metadata_updated`, `failed`, `duplicate_classification`, `duplicate_detected`, `syncing_references`, `syncing_falkor`, `awaiting_page_resume`, `skipped_deleted_document`, `completed`, `human_review_required`, `stored_only`.
- Page `status` (`services/extraction/models.py:36-44`): `text_layer`, `ocr_completed`, `ocr_empty`, `ocr_failed`, `ocr_disabled`, `ocr_pending` (never written), `ocr_deferred`, `unrenderable`. Page `source`: `text_layer`, `ocr`, `reconstructed`, `empty`. Batch: `running`, `completed`, `failed`.
- `vector_sync_status.sync_status`: `deferred`, `disabled`, `empty`, `pending`, `qdrant_synced`, `mongo_only`, `synced`, `mismatch`, `error`.
- `duplicate_status`: `pending`, `unique`, `duplicate`, `revision`. `lifecycle_state`: `active`, `duplicate`, `duplicate_review`, `deleted`.
- `bulk_upload_jobs.status`: `processing`, `completed`, `completed_with_errors`, `failed`, `cancelled` (cancel has no route).
- Contract `documents.status`: `queued`, `processing`, `completed`, `failed`. Redis job: `queued`, `running`, `completed`, `failed`. Upload session: `open`, `uploading`, `queued`. Clause `quality_status`: `incomplete_metadata`, `needs_review`, `validated`; `embedding_status`: `pending`, `done`.
- Contract Master: `ProjectionStatus` `PENDING`, `IN_PROGRESS`, `CURRENT`, `FAILED`; `PromotionState` `RECONCILIATION`, `AUTHORITATIVE`, `QUARANTINED`; evidence outcome `complete`, `degraded`, `valid_empty`.
- `ingestion_jobs` (P4): `queued`, `extracting`, `chunking`, `embedding`, `enriching`, `indexing`, `done`, `failed`.

State diagrams: [graphs file](document-ingestion-graphs.md) §10.

---

## 7. Persistence map

| Store | Role | Authoritative? | Mutable | Rebuildable | Revisioned | Tenant scoped |
|---|---|---|---|---|---|---|
| S3 / local `uploads/` binaries | original file | **authoritative** | no (fresh key per upload) | no | `document_versions` row per attach | key prefix org/proj |
| `documents` | record + **derived** `ocrText`, `full_text`, summary, metadata | mixed | overwritten in place | text yes (reprocess) | **no** | `organization_id`, `project_id` |
| `file_objects`, `document_versions` | blob registry | authoritative | append | no | yes | yes |
| `document_processing_jobs` | operational | — | yes | n/a | no | org on job; **no indexes** |
| `document_ocr_pages` / `_batches` | page evidence (P2) | derived | ReplaceOne per run | yes | per `extraction_run_id` (= job id, so not per attempt) | yes |
| `document_extraction_heads` | published-run pointer | — | — | — | **never written** (`publish_run` no prod caller) | — |
| `document_vectors` (Mongo) | chunk mirror + embeddings | derived | delete+insert | yes | no | payload org/proj |
| `vector_sync_status` | operational | — | upsert | yes | no | **none** (keyed by `document_id`) |
| `chunks`, `ingestion_jobs` | P4 only | derived | yes | yes | no | `org_id`, `project_id` |
| `contract_ingest_jobs`, `contract_upload_sessions` | operational | — | yes | n/a | no | yes |
| `contract_ocr_pages` / `_batches` | contract page evidence | derived | upsert | yes | no | yes |
| `contract_clauses` | clause store | derived | upsert by `clause_uid`; never deletes | yes | `is_current` flag; preserves `manually_edited` | `org_id`, `project_id` |
| `contract_documents` (+ facts, applicability, legal effects) | governance | **authoritative** | revision counter | no | `classification_revision` | yes |
| `reference_sync_queue` | unresolved refs | operational | yes; expires after 30 d | yes | no | org/proj |
| `matter_chronology_events` (+ revisions), `ai_extractions`, evidence graph | derived with review states | derived | yes | partly | chronology yes | yes |
| `bulk_upload_jobs` | bulk status | operational | per-row rewrite of full `results` | no | no | **no org/user stored** |
| `uploads/{org}/{proj}/incoming.md` etc. | append-only LLM report log | derived | append | no | no | path |
| `tmp/document_processing/<filename>` | temp | — | — | — | — | **no** (filename collision across tenants) |
| Qdrant `document_vectors` | correspondence + contract chunk vectors | derived | delete+add (corr), upsert (contract) | yes | no | payload (nested `metadata.organization_id` for LangChain; flat `org_id` for VectorClient) |
| Qdrant namespace `contract_clauses` | clause vectors | derived | upsert | yes | no | payload |
| FalkorDB graph `contraclaim` | Letter/Document/Section/Clause, CITES/REPLIES_TO/HAS_* | derived | MERGE; limited deletes | partly | no | **Letter nodes shared across tenants** (MERGE on `normCode`) |
| Redis | contract queue, rate limits, upload concurrency, caches, sessions | operational | — | — | — | key-level |

---

## 8. Search / RAG after ingestion

| Consumer | Source | Can see a doc before indexing completes? | Notes |
|---|---|---|---|
| Documents list / metadata search | Mongo regex on filename/subject/letterNo/from/to (`services/authorization_service.py:596-606`); wildcard `$text` (`core/database.py:146`, `routers/search.py:82`) | yes (record exists at upload) | date filter uses `createdAt`, not letter date (`routers/search.py:98`) |
| `/v1/retrieval/search` (RAG, agents, drafting retrieval) | `RetrievalService.search` → `VectorClient.search` with **flat** `org_id`/`project_id` filter (`retrieval/service.py:129-139`; `retrieval/vector_client.py:374, 455-476`) | — | correspondence points are written by LangChain with payload nested under `metadata.*` and key `organization_id`; **filter cannot match** (DI-B1, code-confirmed; `list_chunk_ids` knows both shapes at `vector_client.py:319-328` but `search` does not). Mongo failsafe reads `db.chunks`, which uploads never fill |
| `/documents` vector search | top-level `organization_id` filter (`routers/documents.py:571-592`) | — | same shape mismatch; fails closed (empty) |
| Contract search `/contracts/search` | Qdrant + `blocked_document_ids` only (`contract_service.py:1233`) | yes | no projection gate |
| Contract Master evidence | eligible set requires `CURRENT` projection | — | always `valid_empty` (DI-B2) |
| Drafting / chronology / arbitration text | `publication_policy.authoritative_text` = first of `body`, `full_text`, `ocrText`, `summary` (`services/publication_policy.py:171-185`) | yes — `queued`, `processing`, `retrying`, `partially_processed`, `failed` and null are consumable (`:59-74, 149-153`) | `full_text` is the LLM's re-typed "Full Content" |
| Graph thread (`get_thread`) | Falkor, 6 hops on shared Letter node | yes | cross-tenant letter codes can reach drafting context (codes only; snippets withheld) |
| Reconciliation | `VectorReconciler` on `db.chunks`, manual, superadmin (`routers/retrieval_engine.py:253-264`) | — | never sees upload-path documents; no scheduled reconcile |

**Dangerous states that are reachable today** (all code-confirmed): `completed` with zero vectors; `completed` with failed/missing pages (legacy mixed PDFs; unified after resume; contracts with PARTIAL); `completed` with empty metadata (DI-B3); `human_review_required` overwritten to `completed`; contract `completed` with 0 `contract_clauses`; Contract Master `valid_empty` despite content; orphan Qdrant points served after hard delete (`publication_policy.py:396-399`).

---

## 9. Graphify evidence

Focused AST graph over 95 ingestion files: `docs/audits/document-ingestion-graphify/` (`graph.html`, `graph.json`, `GRAPH_REPORT.md`, `labels.json`). 2100 nodes, ~4.7k edges, 98 communities; 100 % EXTRACTED, 0 LLM tokens. God nodes: `DocumentService` (72 edges), `DocumentController` (50), `RetrievalService` (44), `LetterService`, `LetterController`, `FalkorGraphService`, `ContractIngestor`, `normalize_letter_code()`, `GraphIngestionService`, `Document`.

Two structural observations from directed shortest-path queries:
1. `upload_contracts_multipart() → ContractIngestor` and `ContractIngestQueue → ContractIngestor` have **no static call path**: the contract handoff is dynamic (Redis payload + callback). The same holds for `ContractIngestor → ClauseChunkingAgent`. Static tooling (including route-inventory gates) cannot see these edges; they need runtime or integration tests.
2. `.process_document_job() → DocumentProcessor` is a direct static chain (`process_document_async → create_document_processor → DocumentProcessor`), but `GraphIngestionService` and `ReferenceSyncService` are reached only via constructor injection — consistent with the house lesson that dependency-injected code escapes green unit suites.

The repo-wide graph `graphify-out/graph.json` is from 2026-08-13 and stale; it was queried but not relied on.

---

## 10. Verification ledger (lead-auditor re-checks)

| Claim | How verified | Result |
|---|---|---|
| Prod default pipeline is legacy_v0 | read `docker-compose.prod.yml:268,284` | confirmed |
| Unified resume overwrites OCR pages | read `engine.py:97-141, 218-250`, `document_page_store.py:160-180`, `document_service.py:1746` | confirmed by code |
| `completed` ignores vector state; embedding + Qdrant errors swallowed | read `document_service.py:1200-1225`, `database_service.py:205-222`, `langchain_vector_service.py:315-323` | confirmed |
| Claim has no attempts cap | read `document_service.py:1000-1015` | confirmed |
| No production writer of projection `CURRENT` | grep outside `tests/` | confirmed |
| `contract_clauses` Qdrant point id is SHA-1 hex | read `storage_service.py:185-194`, `embedding_service.py:82`, `vector_client.py:246` | confirmed by code; Qdrant rejection needs a live check |
| `GET /documents/{id}/processing-status` missing | grep routers | confirmed |
| Bulk rows processed sequentially (not a worker pool) | read `routers/documents.py:1305-1330`; `process_documents_with_metadata` has no caller | confirmed (the frontend investigator's "worker pool" claim was wrong) |
| One dated list item wipes all metadata | **executed** `parse_extraction_report` in `backend/.venv` | **reproduced**: without the item subject/letterNo/keywords parse; with `- letter no. XYZ/12 dated 01.08.2024` under Key Words, `ERROR - Failed to parse extraction report: 1 validation error for ParsedDocumentMetadata` and every field is `None`/empty |
| Retrieval search filter is flat-only | read `retrieval/service.py:125-142`, `vector_client.py:319-328, 374, 455-476` | confirmed by code; payload shape needs a live Qdrant scroll |
| Falkor `delete_letter` preserve branch deletes all outgoing edges | read `falkor_graph_service.py:400-412` | confirmed (`MATCH (l:Letter {normCode})-[e]->(:Letter) DELETE e`, no owner filter) |

Items marked *needs live check* were not probed against any running Qdrant; per project rules nothing was probed against production.
