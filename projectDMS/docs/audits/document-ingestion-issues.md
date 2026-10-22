# Document Ingestion — Gap / Issue Register

Read-only audit, 2026-09-27, working tree on `integrate/key-date-eot` @ `a179ca9` (dirty; production may run another branch — check before acting). Paths relative to `backend/rbac_backend/` unless prefixed `client/src/`.

**Verdict key:** **CONFIRMED** = lead auditor re-read the code or executed it. **CODE** = read by an investigator with file:line evidence, not independently re-read. **PLAUSIBLE** = code-confirmed mechanism whose runtime effect needs a live check (never probed against production). Reproducibility: **R** = reproduced by execution, **D** = deterministic from code, **C** = conditional (needs a specific input/timing).

Complexity: S (≤1 day), M (≤1 week), L (>1 week / design).

Totals: **4 BLOCKER · 22 HIGH · 38 MEDIUM · 16 LOW**.

---

## BLOCKER

| ID | Stage | Evidence | File/module | User consequence | Data consequence | Security consequence | Repro | Remediation | Cx |
|---|---|---|---|---|---|---|---|---|---|
| DI-B1 | Search/RAG | Correspondence vectors are written by LangChain with payload nested under `metadata.*` and key `organization_id`; `RetrievalService.search` → `VectorClient.search` builds a **flat** `org_id` filter with no `metadata.` prefix (`retrieval/service.py:129-139`; `retrieval/vector_client.py:374, 455-476`). `list_chunk_ids` handles both shapes (`:319-328`), proving the dual shape is known. Mongo failsafe reads `db.chunks`, which uploads never fill. `/documents` vector search has the same mismatch (`routers/documents.py:571-592`). **PLAUSIBLE** (code-confirmed; confirm with one Qdrant scroll) | `retrieval/`, `services/langchain_vector_service.py`, `services/database_service.py` | uploaded letters invisible to RAG, agents and drafting retrieval; "no results" rather than an error | vectors exist but are unreachable | fails closed (no leak) | D | one payload contract (flat `org_id`/`project_id`/`document_id`) for all writers, or search both shapes; backfill; add a gate test that writes via the upload path and reads via `/v1/retrieval/search` | M |
| DI-B2 | Contract Master projection | Only `ContractReprojectionWorker.complete` / `PostPromotionSequencer.mark_projection_current` write `CURRENT`; neither has a non-test caller (grep). Promotion/reclassification set `PENDING` (`services/contract_promotion.py:272`, `services/contract_classification_service.py:173`); resolver requires `CURRENT` (`services/contract_scope_resolver.py:231-239`) ⇒ evidence search returns `valid_empty` (`routers/contract_master_api.py:601-621`). **CONFIRMED** | Contract Master | governed evidence search never returns anything; users told "no evidence" | promoted instruments permanently PENDING | none | D | wire the reprojection worker to a durable trigger after promotion; `complete()` must verify points exist before `CURRENT`; report `valid_empty` only when projection is CURRENT | M |
| DI-B3 | Metadata extraction | `_parse_list_block` runs `parse_legacy_reference_text` on every list field (`services/text_processing_service.py:340-368`), producing a dict inside `List[str]`; validation error is caught and **empty metadata returned, success=True** (`:315-318`). **CONFIRMED + R**: executing `parse_extraction_report` with `- letter no. XYZ/12 dated 01.08.2024` under Key Words logs `Failed to parse extraction report: 1 validation error for ParsedDocumentMetadata` and returns subject/letterNo/keywords all empty; without that line all fields parse | `services/text_processing_service.py` (default path) | letter shows no subject/number/summary; no degraded marker | document `completed` with empty metadata; references lost; wrong search | none | R | apply reference parsing only to the references field; validate per field and keep valid fields; record `partial_failures.metadata_parse` and a degraded status; replace text report with JSON schema output | S |
| DI-B4 | OCR retry / page resume | On retry, engine OCRs only `retry_pages` and rebuilds every other page from native text with status `text_layer` (`services/extraction/engine.py:100-141, 218-250`); `ReplaceOne` on `(document_id, extraction_run_id, page_number)` with run id = job id (`services/extraction_adapters/document_page_store.py:167-180`; `services/document_service.py:1746`) overwrites the earlier OCR rows; checkpoint counts them resolved ⇒ `completed` with text missing. Same engine drives `/contracts/{id}/ocr/retry`, which then rebuilds `document_vectors` from degraded text. **CONFIRMED** (by code) | `services/extraction/engine.py`, page stores | scanned pages vanish after a retry | silent text loss under `completed` | none | D (unified canary + contract OCR retry) | carry forward prior OCR results for non-retry pages (read page store), new run id per attempt, publish via heads pointer; never downgrade a page's source | M |

## HIGH

| ID | Stage | Evidence | File/module | User | Data | Security | Repro | Remediation | Cx |
|---|---|---|---|---|---|---|---|---|---|
| DI-H1 | OCR decision | Legacy decides for whole doc from first 5 pages; any text ⇒ no OCR (`services/ocr_service.py:64-99, 141-151`); acknowledged at `services/document_processor.py:497-501`; legacy is prod default. CONFIRMED (default) / CODE | legacy OCR | scanned annexures unsearchable | silent partial extraction | — | D (mixed PDFs) | move correspondence to page-level engine after DI-B4 fix | M |
| DI-H2 | OCR decision | No `(cid:N)` detection in correspondence path (only `contracts_ingest.py`); ≥40 chars of CID garbage is never OCR'd and gets embedded. CODE | extraction engine | garbage text | polluted vectors | — | C | shared text-quality check (CID ratio, printable ratio) in page classifier | S |
| DI-H3 | Indexing / status | Embedding exceptions swallowed (`services/database_service.py:205-222`); Qdrant `replace_document` returns 0 on any exception after deleting (`services/langchain_vector_service.py:315-323`); completion ignores `vector_sync_status` (`services/document_service.py:1200-1225`). CONFIRMED | P1 | "Completed" but unsearchable | zero or stale vectors, no retry | — | C (Qdrant/embedding outage) | completion requires `synced`; `mismatch/error` ⇒ `index_failed` retriable state; upsert-then-delete-stale | M |
| DI-H4 | Canonical text | `documents.full_text` = LLM re-typed "Full Content"; `authoritative_text` prefers it (`services/publication_policy.py:171-185`; `services/database_service.py:276-297`); on OCR failure the PDF is sent to the LLM and its output stored. CODE | publication policy | drafting quotes an LLM paraphrase | non-verbatim "source" text | legal-evidence integrity | D | canonical text = extracted page text only; LLM content never written to text fields | M |
| DI-H5 | Human edits | Reprocess overwrites subject/letterNo/from/to/date and replaces `metadata` wholesale (`services/document_service.py:1833-1856`); `manual_summary_metadata_override` never read. CODE | document service | user corrections lost silently | regression of verified data | — | D | per-field `source=human` preserved on reprocess (pattern exists in `contract_clause/storage_service.py:336-347`) | S |
| DI-H6 | Metadata fallback | OCR-fallback report uses **filename** as letterNo and subject, first date in text as date (`services/document_processor.py:640-708`) | processor | wrong metadata looks real | false `letterNoNormalized` ⇒ false reference links | — | C (LLM failure) | fallback leaves fields empty + degraded marker | S |
| DI-H7 | Graph tenancy | `delete_letter` preserve branch `MATCH (l:Letter {normCode})-[e]->(:Letter) DELETE e` with no owner filter (`services/falkor_graph_service.py:400-412`, CONFIRMED); Letter nodes shared across tenants; `get_thread` 6 hops; `graph_codes_denied` has no org filter (`services/publication_policy.py:498`) | Falkor | another tenant's deletion removes my edges; foreign letter codes in drafting context | cross-tenant graph mutation | **cross-tenant integrity + metadata leak (codes)** | D | tenant-scoped node keys (`{org}:{normCode}`), owner filter on every delete, org filter on thread/denial queries | M |
| DI-H8 | UI status | `GET /documents/{id}/processing-status` called (`client/src/services/enhanced-api.ts:1001-1007`) but no route exists (CONFIRMED); viewer turns 404 into `not_queued`, hides banner and Retry | viewer | no processing feedback on single upload | — | — | D | add the route (service method exists `document_service.py:953`) | S |
| DI-H9 | UI status | UI does not know `human_review_required`, `partially_processed`, `stored_only`, `skipped`, `cancelled` (`client/src/pages/UploadPage.tsx:60-139`) | upload/viewer | bulk rows "Processing" forever; no review CTA | — | — | D | generate status enum for client from `models/processing_state.py`; add states + CTAs | S |
| DI-H10 | Reprocess | `POST /documents/{id}/process` runs the whole pipeline inline in the API request, no lock vs worker job, always `success=True`; on failure doc stuck `processing` (`routers/documents.py:1491-1506, 2608-2628`; `services/document_service.py:1681, 1891`) | router | blocking request; false success | concurrent double processing; stuck status | DoS vector (OCR+LLM in API) | D | enqueue a job (or force-requeue existing) and return job id | S |
| DI-H11 | Worker | Claim lacks `attempts<max_attempts` ⇒ OOM/poison job re-runs hourly forever (`services/document_service.py:1000-1015`, CONFIRMED); legacy `ocrmypdf.ocr()` has no timeout, heartbeat keeps a hung job alive, serial loop blocks the replica | worker | queue stalls | cost burn | — | C | cap in claim filter; per-job wall-clock timeout; OCR in subprocess with timeout | S |
| DI-H12 | Bulk durability | Bulk runs in FastAPI `BackgroundTasks` (`routers/documents.py:1028, 1279-1391`); restart ⇒ job stuck `processing`, temp files lost; no per-file retry; cancel unreachable | bulk | partial batches with no recovery | orphan bulk records | — | C (restart mid-batch) | persist manifest + per-file intake jobs in Mongo; worker-driven | M |
| DI-H13 | Dedup | Precheck non-atomic: `sha256` `$set` after insert, failure swallowed; no unique index (`routers/documents.py:853-870`; `core/database.py:157,176-179`) | upload | duplicates on double submit | duplicate docs/vectors/graph | — | C (concurrency) | unique partial index `(organization_id, project_id, sha256)` for active docs; write sha256 in the insert | S |
| DI-H14 | Contract queue | On failure job marked `queued` and removed from processing list before backoff sleep + re-push (`services/contract_ingest_queue.py:405-428`); kill during sleep ⇒ in neither list; later enqueue no-ops because status `queued` (`:201-203`) | contract queue | contract stuck forever, reindex can't fix | lost job | — | C (kill during backoff) | atomic `LMOVE` to delayed zset; recovery scans hash statuses | S |
| DI-H15 | Contract completeness | Engine `completeness=PARTIAL` ignored (`services/contracts_ingest.py:1390-1398`); DOCX never writes `contract_ocr_pages` and org-scoped uploads fail `validate_scope` ⇒ 0 `contract_clauses`, errors swallowed (`services/contract_service.py:489-507`), doc `completed` | contract ingest | clause-first Q&A empty for DOCX/org uploads | silent partial | — | D | propagate completeness; page records for DOCX; allow org scope or fail visibly | M |
| DI-H16 | Contract clause vectors | Qdrant point id = `clause_uid` (40-char SHA-1 hex) (`services/contract_clause/storage_service.py:185-194`; `embedding_service.py:82`; `retrieval/vector_client.py:246`); Qdrant accepts only uint/UUID; failure swallowed (`agent.py:360-362`). PLAUSIBLE | clause index | clause-first retrieval likely empty | — | — | D (if Qdrant rejects) | `uuid5(clause_uid)` as point id, keep `clause_uid` in payload; stop swallowing | S |
| DI-H17 | Governance | Reindex / OCR retry on a promoted instrument's document rewrite vectors, clauses, graph and status without touching `contract_documents` (no reference to it in the ingest path) — latent once DI-B2 is fixed | contract ingest vs Contract Master | stale "CURRENT" evidence | projection claims points that were replaced | integrity | D (latent) | ingest must invalidate projection (`PENDING`, bump revision) for governed docs, or refuse | M |
| DI-H18 | Upload security | `/upload-file` (folder): no AV, full `read()` before size check, no dedupe (`routers/folder_structure.py:161-229`) | folder upload | — | — | malware bypass; memory DoS | D | route through `upload_streaming` + AV + precheck | S |
| DI-H19 | Provenance | Correspondence metadata and chunks have no page/span, no model/prompt/schema version, no confidence (`services/database_service.py:420-473`; `services/document_service.py:1815-1824`) | metadata/chunks | cannot verify extracted facts | unverifiable lineage | — | D | fact-level provenance schema (see target architecture) | L |
| DI-H20 | Tenancy (latent) | `_find_existing_document` falls back to `find_one({"filename": filename})` across tenants (`services/database_service.py:356-359`) | database service | — | could overwrite another tenant's text/metadata/vectors | **cross-tenant write** | C (missing/invalid doc id caller) | delete the fallback; require document id + org | S |
| DI-H21 | Frontend | Legacy `ContractsPage` calls `uploadContractInChunks` without `uploadId` for >15 MB (`client/src/pages/ContractsPage.tsx:175-186` vs `client/src/services/contracts-api.ts:167-169`) | contracts UI | large contract upload fails | — | — | D | pass session id or remove legacy page | S |
| DI-H22 | OCR (unified) | Blank page ⇒ `ocr_empty` ⇒ unresolved, consumes attempts ⇒ `human_review_required` after 3 attempts (`models/processing_state.py:93-113`); classifier BLANK does not suppress OCR (`page_classifier.py:5-6` vs `engine.py:106-113`) | unified engine | every letter with a blank back page needs review | wasted OCR | — | D | treat BLANK/empty-after-OCR as resolved-empty | S |

## MEDIUM

| ID | Stage | Evidence | Consequence | Remediation | Cx |
|---|---|---|---|---|---|
| DI-M1 | Worker | duplicate job race: find-then-insert, no unique index (`services/document_service.py:873-933`) | two workers process one doc | unique index on active job per document | S |
| DI-M2 | Worker | no indexes on `document_processing_jobs` | collection scan every 1–3 s | index `(status, run_after, created_at)` | S |
| DI-M3 | Worker | no owner token/fencing on heartbeat/complete writes (`document_service.py:1035-1120`) | stale + live worker both write | lease token in every write filter | S |
| DI-M4 | Retry cost | Falkor/reference-sync failure retries whole pipeline incl. OCR + 2 LLM calls | cost, latency, dead-letter of good extraction | stage-level checkpoints; retry only failed stage | M |
| DI-M5 | Event loop | synchronous `sync_document_to_falkor` and boto3 uploads inside async (`graph/graph_ingestion_service.py:373`; `services/s3_service.py:74,95`) | API/worker stalls under load | `to_thread` or async clients | S |
| DI-M6 | Cost | embeddings computed twice (LangChain + LlamaIndex) (`services/database_service.py:363-616`) | 2× embedding spend | embed once, write both stores | S |
| DI-M7 | Page store | `publish_run` has no prod caller; `document_extraction_heads` never written; `document_ocr_pages` never read | page evidence unusable | wire publish + readers | M |
| DI-M8 | Quality gate | review pages not "remaining" ⇒ `retry_pages=None` ⇒ whole doc re-OCR'd + re-LLM'd up to 3× (`document_service.py:1399-1421, 1733-1747`) | cost; confusing "0 page(s)" review message | route QG failures straight to review | S |
| DI-M9 | Ops | no manual job retry/requeue endpoint for documents | operators cannot recover dead-lettered docs | admin requeue route | S |
| DI-M10 | OCR failure | LLM "Full Content" from PDF embedded when OCR fails (`services/database_service.py:765`) | hallucinated text indexed | never embed LLM output as source | S |
| DI-M11 | LLM | invalid/empty LLM output still `completed` (regex parse, no validation) | empty metadata looks final | metadata completeness check ⇒ degraded state | S |
| DI-M12 | Contracts dedup | no duplicate precheck; `file_objects` silently reuses blob (`services/file_object_service.py:226-235`) | duplicate contracts/vectors | call `precheck_upload` (house rule) | S |
| DI-M13 | Contract chunks | chunk read before size check; concurrent final chunks can both merge (`routers/contracts.py:517-578`) | memory; duplicate documents | size-limited read; merge lock | S |
| DI-M14 | Contract multipart | exception on file k aborts with error after files 1..k-1 committed; ids lost (`contracts.py:324-478`) | orphaned uploads user can't see | per-file results | S |
| DI-M15 | Bulk | status endpoint IDOR: no owner/org check; `bulk_upload_jobs` lacks org/user (`routers/documents.py:1531, 1013-1022, 3169-3185`) | cross-tenant status read (uuid4-limited) | store and check org/user | S |
| DI-M16 | Bulk | duplicate relative filenames collapse (last wins) (`documents.py:1076-1091`) | file silently dropped/substituted | reject duplicates in manifest | S |
| DI-M17 | Insurance | `letter_no` = policy number ⇒ second doc held `duplicate_review` (`routers/insurance.py:404`) | attachments hidden | don't set letterNo for register attachments | S |
| DI-M18 | PDF | encrypted PDF ⇒ silent no text (`services/ocr_service.py:271-286`) | unsearchable doc marked complete | intake detection ⇒ explicit `encrypted` failure | S |
| DI-M19 | Routing | `source_mime` re-sniffed without filename; S3-only ⇒ `open()` fails ⇒ octet-stream (`services/document_service.py:529-532, 907`) | wrong unified dispatch | persist intake MIME | S |
| DI-M20 | Upload | deep-planning upload unbounded, straight to LLM (`routers/deep_planning.py:1069-1074`) | memory/cost DoS | size cap | S |
| DI-M21 | Upload | CSV imports unbounded `read()` before authorize (`routers/bank_guarantees.py:208`, `routers/key_dates.py:88`) | memory DoS | cap + authorize first | S |
| DI-M22 | AV | `ANTIVIRUS_ENABLED=False`, `CLAMAV_FAIL_OPEN=True` defaults; only prod validator protects (`core/config.py:232,242,838-843`) | unscanned uploads outside prod / if validator bypassed | fail-closed default | S |
| DI-M23 | References | no project ⇒ org-wide resolution; ignores date, lifecycle, duplicate/deleted (`services/reference_sync_service.py:420-430, 547-561`) | false links to revisions/deleted/other projects | require project; filter lifecycle; date tiebreak | S |
| DI-M24 | References | unresolved refs expire after 30 days; no reverse lookup on target arrival (`reference_sync_service.py:326-336`) | late targets never linked | resolve on target ingest | M |
| DI-M25 | References | regex misses `Ref: X dtd`, `vide letter X dt`, `[1]` lists, drawings/BOQ/VO/IPC/EOT (`services/reference_parser.py:11-18`) | unresolved references | typed reference extractor | M |
| DI-M26 | Graph | `previous_letter_id` (ObjectId) pushed as letter code for REPLIES_TO (`graph/graph_ingestion_service.py:550-558`) | junk Letter nodes | separate id namespace | S |
| DI-M27 | Graph | adapter node ids `clause:{slug}`, `party:{slug}`, `letter-ref:{slug}` unscoped (`graph_ingestion_service.py:323-330`) | cross-tenant node sharing | tenant prefix | S |
| DI-M28 | Extraction | PydanticAI input silently cut to 12 000 chars; failure not recorded | incomplete metadata for long letters | chunked extraction + record | S |
| DI-M29 | Search | date filter uses `createdAt` not letter date (`routers/search.py:98`) | wrong search results | filter on `date` | S |
| DI-M30 | Markdown | Marker output unused (`markdown_text = text`, `services/contracts_ingest.py:1548`); Marker `subprocess.run` no timeout (`:515-521`) | wasted work; hung jobs | use or remove; timeout | S |
| DI-M31 | Search | orphan Qdrant points served after hard delete (`services/publication_policy.py:396-399`) | deleted docs in results | treat missing doc as blocked | S |
| DI-M32 | Disk/tenancy | `tmp/document_processing/<filename>` never cleaned, collides across tenants; OCR batch dirs never removed (`services/document_service.py:1702-1707`) | disk leak; wrong file processed on collision | unique temp dir per job + cleanup | S |
| DI-M33 | Markdown | `.md` summary append-only, duplicates on reprocess, not removed on delete (`services/file_service.py:17-73`) | stale/duplicated content on disk | replace with per-doc canonical artifact | S |
| DI-M34 | Stores | `vector_sync_status` has no tenant fields | ops queries unscoped | add org/project | S |
| DI-M35 | Contract queue | enqueue dedupe drops new payload (reindex/retry pages) while queued/running; router still reports `reindex_queued` (`services/contract_ingest_queue.py:201-203`; `routers/contracts.py:886`) | user action silently ignored | coalesce payloads or 409 | S |
| DI-M36 | Contract queue | requeue race between `BRPOPLPUSH` and `running` (`:273-286`); first failure shows `failed` while Redis retries (`services/contract_service.py:600-613`) | double processing; misleading status | set running atomically; `retrying` state | S |
| DI-M37 | Contract Master | expired reprojection claims never reclaimed (`services/contract_reprojection_worker.py:55-57, 183-201`); evidence route uses `AuthorizedContractScope.for_tests(...)` in production (`routers/contract_master_api.py:577-582`) | stuck revision; weaker contract-scope authz | lease expiry check; real `authorize_contract_scope` | S |
| DI-M38 | Contract vectors | non-atomic Mongo vector swap; stale Qdrant points and clauses never deleted; only `validated` clauses embedded (`services/contracts_ingest.py:1154-1177`; `contract_clause/storage_service.py:206-215`) | zero/stale vectors; most clauses not embedded | versioned swap; delete stale; embed with quality flag | M |

## LOW

| ID | Evidence | Remediation |
|---|---|---|
| DI-L1 | no extension/MIME consistency check (`services/upload_streaming.py:145-184`); `x.html` passes as text/plain | reject mismatches |
| DI-L2 | raw `file.filename` stored (`routers/documents.py:827`) | store sanitized name |
| DI-L3 | job `sha256=None` (stale object, `services/document_service.py:900`) | pass computed hash |
| DI-L4 | profile photo trusts client `content_type` (`routers/profiles.py:123`) | sniff |
| DI-L5 | nginx 200 MB < configured 500 MB bulk (`config/nginx-contraclaim.conf:26`) | align |
| DI-L6 | folder upload deterministic keys, check-then-write overwrite race (`routers/folder_structure.py:202-216`) | unique keys |
| DI-L7 | prompt numbering skips #10; #18/#21 both "Key Words" (`services/openai_service.py:275-286`) | JSON schema output |
| DI-L8 | PATCH summary-metadata bypasses tag allow-list (`routers/documents.py:174-191, 224-230`) | validate |
| DI-L9 | edit audit records field names only, not before/after | record diffs |
| DI-L10 | P4 chunk `content_hash` overwritten with doc hash ⇒ always re-embed; dedup ignores model version (`ingestion/pipeline.py:137, 314, 382-393`) | fix hash; include model |
| DI-L11 | `VectorClient._memory_index` grows on every upsert (`retrieval/vector_client.py:239-241`) | only when Qdrant disabled |
| DI-L12 | no Qdrant payload indexes created | create for org/project/document |
| DI-L13 | P4 in-memory `BackgroundJobProcessor` retry never fires (`ingestion/pipeline.py:264-277`) | durable or remove |
| DI-L14 | UI dead controls: single Cancel, "Add Field", bulk option checkboxes; undefined pages/size; mock related docs; `/letter1.pdf` fallback | clean up |
| DI-L15 | chronology `_event_date` falls back to `created_at` labelled EXACT (`services/chronology.py:668-672`) | label ESTIMATED |
| DI-L16 | `mark_projection_failed` not revision-fenced (`services/contract_classification_service.py:203-206`) | fence |

---

## Checklist of requested defect classes

| Class | Found? | IDs |
|---|---|---|
| N+1 API behaviour | yes — single tab loops one request per file; enclosures loop; bulk rewrites full `results` per row | DI-H12, single-vs-bulk §2 |
| duplicate jobs | yes | DI-M1, DI-M36 |
| unbounded bulk fan-out | no (sequential everywhere); unbounded *latency* instead | single-vs-bulk §3 |
| blocking OCR in API process | yes (inline `/process`) | DI-H10 |
| missing backpressure | yes (queue growth only, no fairness) | single-vs-bulk §2 |
| missing deduplication | yes (contracts, enclosures, folder; non-atomic for documents) | DI-H13, DI-M12, DI-H18 |
| silent partial extraction | yes | DI-H1, DI-H15, DI-B4 |
| wrong COMPLETE status | yes | DI-H3, DI-B3, DI-B4, DI-H15 |
| lost original | no | — |
| Markdown not persisted | yes | DI-M33, markdown-lineage §1 |
| derived text replacing source | yes (text layer: LLM `full_text` is authoritative) | DI-H4, DI-M10 |
| metadata without provenance | yes | DI-H19 |
| LLM hallucinated metadata | yes (filename fallback; LLM "Full Content") | DI-H6, DI-H4 |
| cross-project reference links | yes when source has no project; graph cross-tenant | DI-M23, DI-H7 |
| retry destroying resolved pages | yes | DI-B4 |
| generic ingestion mutating governed Contract Master docs | yes (latent) | DI-H17 |
| invalid search authority states | yes | DI-B2, DI-M31, consumable-while-processing |
| no human review | yes | metadata-matrix §5 |
| no confidence score | yes | DI-H19 |
| missing schema versioning | yes on `documents`; partial on `ai_extractions` | DI-H19 |
