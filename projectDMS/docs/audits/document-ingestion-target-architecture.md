# Document Ingestion — Recommended Target Architecture (TO-BE)

Design recommendation from the 2026-09-27 read-only audit. Nothing here is implemented. It is written against what exists today so that each step reuses a real component or names the gap it closes (issue IDs from [the register](document-ingestion-issues.md)).

## 1. Evaluation of the proposed canonical flow

| Proposed stage | Exists today? | Keep / change | Reuse |
|---|---|---|---|
| UPLOAD | yes (streaming spool + SHA-256) | keep; add presigned direct-to-S3 for bulk | `services/upload_streaming.py` |
| VALIDATE | partial (magic bytes, AV fail-open default, no encryption/corruption check, folder path bypass) | extend: encryption/corruption probe, ext↔MIME consistency, one validator for **every** entry point | `utils/file_validation.py`, `AntivirusService` |
| PERSIST IMMUTABLE ORIGINAL | yes | keep; add `If-None-Match` guard | `FileObjectService`, `StorageKeyBuilder` |
| CREATE DOCUMENT + INGESTION JOB | yes but non-atomic dedupe, racy job creation | same transaction, unique indexes (DI-H13, DI-M1) | `document_processing_jobs` |
| DETECT DOCUMENT TYPE | only in unified path | always: `SourceKindRouter` on persisted intake MIME (DI-M19) | `services/extraction/source_kind.py` |
| PAGE INVENTORY | unified/contract only | always, incl. DOCX (DI-H15) | `PageClassifier` |
| TEXT QUALITY CHECK | chars<40 only; no CID for correspondence | add CID/printable ratio, blank detection (DI-H2, DI-H22) | `services/extraction/quality/` |
| OCR ONLY WHERE REQUIRED | page-level exists but retry destroys pages; legacy is doc-level | page-level everywhere, **carry-forward** of resolved pages, new run per attempt (DI-B4, DI-H1) | `PageExtractionEngine` |
| CANONICAL TEXT | no — LLM paraphrase is authoritative | page text only, versioned + hashed (DI-H4) | `document_ocr_pages` + `publish_run` (DI-M7) |
| CANONICAL MARKDOWN | no | new: deterministic per-page Markdown with page anchors, stored in S3 + DB pointer | new; Marker optional behind flag |
| STRUCTURED METADATA EXTRACTION | text report + regex, silent empty on parse error | JSON-schema output, per-field validation, per-field provenance/confidence (DI-B3, DI-H19) | PydanticAI path as basis |
| REFERENCE RESOLUTION | letter numbers only, 30-day expiry, graph shared across tenants | typed references, reverse resolution on arrival, tenant-scoped graph (DI-M23-25, DI-H7) | `ReferenceSyncService.sync_bidirectional` (house rule: all writes go through it) |
| DOMAIN EXTRACTION | reply points as flat list; contracts regex clauses | reply-points schema (facts / issues / considerations); contract definitions/notices/time-bars as post-processing | drafting analyzer, `ClauseChunkingAgent` |
| HUMAN REVIEW WHERE REQUIRED | not exposed | review queue + per-field review state | chronology/evidence-graph review pattern |
| CHUNKING | char splitter, no page lineage | Markdown-block-aware chunks carrying page + span | `ingestion/chunker.py` |
| EMBEDDINGS | twice per doc | once, batched, model-versioned (DI-M6) | `retrieval/embeddings.py` |
| QDRANT | two payload shapes, invalid ids for clauses, swallowed failures | one payload contract, uuid5 ids, upsert-then-delete-stale, failure ⇒ retriable state (DI-B1, DI-H16, DI-H3) | `VectorClient` |
| GRAPH RELATIONSHIPS | shared Letter nodes | tenant-scoped keys, owner-filtered deletes | `FalkorGraphService` |
| SEARCH/RAG READY → COMPLETE | `completed` ignores vectors/metadata/pages | `COMPLETE` only when every required stage is green; otherwise a named degraded state | `models/processing_state.py` |

The proposed order is sound with **three changes**:
1. **Human review must not block indexing of source text**, only publication of *reviewed metadata*. Page text and chunks can be indexed as soon as canonical text exists; unreviewed metadata is searchable but flagged. Otherwise a review backlog makes letters unsearchable.
2. **Canonical Markdown should be generated deterministically from page text, before any LLM**, so the LLM reads Markdown with page anchors and every extracted fact can cite `page + span`.
3. **Contract documents branch after CANONICAL MARKDOWN**: contracts go to clause segmentation + governed projection; correspondence goes to letter metadata + references. Contract Master projection must be invalidated by any re-ingest of a governed document (DI-H17) and made `CURRENT` only after verified points exist (DI-B2).

## 2. TO-BE pipeline (single document)

```
UPLOAD (stream, sha256)
 -> VALIDATE (magic bytes, ext/MIME, AV fail-closed, encryption/corruption probe, tenant/project scope)
 -> PERSIST ORIGINAL (unique key, If-None-Match, file_objects + document_versions)
 -> TXN{ documents insert with sha256 + unique index ; ingestion_job insert (unique active job per doc) }
 -> worker claim (attempts < max, lease token, per-job timeout)
 -> DETECT TYPE (SourceKindRouter on stored intake MIME)
 -> PAGE INVENTORY (class, native chars, CID ratio, blank)
 -> OCR candidate pages only (subprocess, timeout); carry forward resolved pages from previous run
 -> CANONICAL TEXT (per-page versions; publish_run moves heads pointer atomically)
 -> CANONICAL MARKDOWN (per page, <!-- page:N --> anchors; S3 key {org}/{proj}/derived/{doc}/{run}/document.md; sha256; DB pointer)
 -> METADATA (JSON schema; fields carry page/span/confidence/model/prompt/schema version; per-field validation; human-edited fields preserved)
 -> REFERENCES (typed; resolve within project; unresolved kept until resolved; reverse resolution on new arrivals)
 -> DOMAIN (correspondence: reply-points schema; contract: clauses, definitions, notices, time-bars)
 -> REVIEW GATE (policy per field; low-confidence -> review queue; indexing not blocked)
 -> CHUNK (Markdown blocks; page_start/end; char span; run id)
 -> EMBED once (model+version)
 -> QDRANT upsert (uuid5 ids, flat tenant payload) then delete stale points of older runs
 -> GRAPH (tenant-scoped keys, owner-filtered)
 -> COMPLETE only if: pages resolved | canonical text+md hashed | metadata valid (or explicitly degraded) | vector count == chunk count
    else one of: PARTIALLY_PROCESSED | INDEX_FAILED | METADATA_DEGRADED | HUMAN_REVIEW_REQUIRED (all retriable per stage)
```

Stage-level checkpoints: each stage writes `{stage, run_id, status, attempts, error, finished_at}` to the job so a Falkor or Qdrant failure retries **only that stage**, not OCR + LLM (DI-M4).

## 3. TO-BE bulk

```
BULK MANIFEST (POST /documents/bulk -> manifest id; CSV validated up front; duplicate names rejected)
 -> files uploaded individually (presigned PUT or per-file POST), each bound to manifest id
 -> per file: same single-document intake transaction -> independent ingestion job
 -> bounded concurrency: N worker replicas × 1 job; per-tenant fair claim (round-robin by org)
 -> per-file status in manifest (derived from job states, not rewritten arrays)
 -> PARTIAL SUCCESS: manifest status = completed | completed_with_errors | processing
 -> FAILED FILE RETRY: POST /documents/bulk/{id}/files/{file_id}/retry (re-upload or requeue)
 -> CANCEL: server-side flag checked before each claim
 -> RESUME: GET /documents/bulk?mine=1 lists manifests; UI restores on refresh
 -> BULK SUMMARY: counts by state, human-review count, link to review queue
```

Compared with today: removes the single giant multipart request (nginx 200 MB cap, no progress), the non-durable BackgroundTask, the lost tail on restart, and gives per-file retry. Intake concurrency stays bounded by `upload_limits`; processing throughput scales with worker replicas.

## 4. Data model additions (sketch)

| Collection / artifact | Key fields |
|---|---|
| `ingestion_runs` | `run_id`, `document_id`, `org`, `project`, `pipeline_version`, `engine_version`, `started_at`, `stages[]`, `status` |
| `document_pages` (versioned) | `document_id`, `run_id`, `page_no`, `source`, `text`, `text_sha256`, `quality`, `engine_version`, `created_at` |
| `document_extraction_heads` | `document_id` → `run_id` (atomic pointer; already designed, never written) |
| S3 `derived/{doc}/{run}/document.md` + `pages/{n}.md` | Markdown with page anchors; `md_sha256` on the run |
| `document_facts` | `document_id`, `field`, `value`, `page_no`, `span`, `extractor`, `model`, `prompt_version`, `schema_version`, `confidence`, `review_state`, `source` (llm/rule/human), `superseded_by` |
| `documents` | keeps a *projection* of accepted facts for listing/search; never the source of truth for text |
| `bulk_manifests` / `bulk_manifest_files` | `org`, `user`, per-file `document_id`, `job_id`, state |

## 5. Rollout order (dependency-aware)

1. **Stop silent wrongness (days):** DI-B3 parse fix; DI-H3 completion gate on vectors; DI-H8 status route + DI-H9 client states; DI-H11 claim cap + timeouts; DI-H10 `/process` enqueues; DI-H5 preserve human edits; DI-H6 no filename fallback.
2. **Make search work (1–2 weeks):** DI-B1 single payload contract + backfill + end-to-end gate test; DI-H16 uuid5 clause ids; DI-M31 orphan filtering; scheduled reconciliation over the upload-path stores.
3. **Page correctness:** DI-B4 carry-forward + per-attempt run ids + `publish_run`; DI-H2/DI-H22 quality checks; then move correspondence default from legacy_v0 to page-level (DI-H1).
4. **Canonical text + Markdown + provenance:** DI-H4, DI-H19, Markdown artifact, JSON metadata with facts table.
5. **Tenant-safe graph + references:** DI-H7, DI-M23-27.
6. **Governed contracts:** DI-B2 wire reprojection; DI-H17 invalidation on re-ingest; DI-H15 completeness; DI-H14 queue loss.
7. **Bulk manifest redesign** and review UX.

Each step needs a red→green test that exercises the *real* dependency-injected factories (house lesson: router tests override controllers and hide broken factories), and a route-inventory regeneration for any new route.
