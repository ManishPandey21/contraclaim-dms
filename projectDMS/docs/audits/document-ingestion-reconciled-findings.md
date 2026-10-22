# Document Ingestion — Lineage-Reconciled Findings

Read-only reconciliation, 2026-09-27. Input: the 80 findings in
[document-ingestion-issues.md](document-ingestion-issues.md) (4 BLOCKER · 22 HIGH · 38 MEDIUM · 16 LOW).
Output: the status of each finding on the authoritative release lineage and on the two pending feature lines.
No source file was modified. Companion documents:
[current-release gap register](document-ingestion-current-release-gap-register.md) ·
[implementation roadmap](document-ingestion-implementation-roadmap.md).

Paths are relative to `projectDMS/backend/rbac_backend/` unless prefixed `client/` or `projectDMS/`.

---

## 1. Lineages compared (verified after `git fetch --all`, 2026-09-27)

| Label | Ref | SHA | Relationship |
|---|---|---|---|
| **A** (audited) | `integrate/key-date-eot` | `a179ca9` **+ dirty working tree** | 292 modified/untracked paths, including an *untracked* copy of the Contract Master services. The audit's file:line anchors refer to this dirty tree, not to `a179ca9`. |
| **REL** | `origin/release/contraclaim-rc1` | `5a8cca2` | Authoritative release. Contains PR #25 (OCRmyPDF/pdfminer) and PR #28 (extraction retry / resolved-page preservation). `a179ca9` lacks 179 of its commits. |
| **RECON** | `reconcile/cl4a-onto-release-rc1` | `4fcd012` | REL + 57 commits (CL-1 … CL-4A, PR #31 Contract Master v1 policy/scope). **Not merged into REL.** |
| **CM** | `fix/contract-master-reprojection-runtime` (PR #34) | `3ca0149` | RECON + 1 commit. **Not integrated.** CI is blocked by billing. |

Traps found while mapping the branches:

- **Stale local branch.** The local `release/contraclaim-rc1` ref, and the worktree `C:/SaaS-release-rc1`, sit at `99ab522`. That is behind `origin` (`5a8cca2`) and lacks PR #25 and PR #28. Any comparison against the local ref would wrongly classify DI-B4, DI-H2 and DI-M18 as unfixed. This reconciliation used a clean detached checkout of `5a8cca2`.
- **Audited tree older than REL.** For extraction code the audited tree predates REL. For example, A's `services/extraction/engine.py` is 302 lines and REL's is 636. So the audit is stale for every finding touched by PR #25 or PR #28.
- **Production not re-read.** Production has historically run behind the newest release candidate. This document classifies code lineages only; production state was not read.

## 2. Method and verification rungs reached

- Every finding was checked by reading the cited anchor at each of A, REL, RECON and CM, or by proving that the files are identical, using `git diff` / `git hash-object`. No finding was classified from a commit message.
- Seven read-only reviewers each covered one group: Contract Master, extraction/retry, Qdrant, contracts queue, correspondence metadata/worker, router/UI, and graph/references. The lead re-checked the load-bearing claims: DI-B2 caller absence and the flag wiring; the DI-B3 reference wipe; the DI-B1 proof output; and the DI-B4 contract page-store resume.
- **Executed evidence:**
  - **DI-B3.** Reproduced with the real parser on all four checkouts (§3.3).
  - **DI-B1 and DI-H16.** Demonstrated with the real writer and reader code against `qdrant-client` local mode (`:memory:`), on all four checkouts (§3.1).
  - **DI-M25.** The reference parser was executed at REL.
  - **CM test files.** Run at `3ca0149`: **123 passed, 27 skipped, 0 failed**. All five runtime integration suites were among the skipped; they need a replica-set Mongo.
  - **REL extraction tests (13 files).** **194 passed, 1 failed.** The failure is `test_legacy_ocr_cid_guard.py::test_the_legacy_assessment_holds_in_both_import_orders[ocrmypdf-first]`. It passed on rerun in isolation (2/2), so it is order- or load-dependent. It is reported here, not waived.
- **Not reached:**
  - No live Qdrant server was used. A local one answered on :6333 but requires an API key, and it was not used.
  - No production payload scroll.
  - No replica-set Mongo run of the Contract Master runtime.
  - No production read of any kind.

Classification vocabulary:

| Label | Meaning |
|---|---|
| CURRENT | Present on REL, RECON and CM. |
| FIXED_IN_RELEASE | Fixed at REL `5a8cca2`, and still fixed downstream. |
| FIXED_NOT_INTEGRATED | Present on REL; fixed only on RECON and/or CM. |
| PARTIAL | Part of the defect fixed; the remainder is stated. |
| STALE | The claim is wrong on A, or absent from every lineage. |
| UNVERIFIED | Needs runtime evidence that was not produced. |

---

## 3. Special findings

### 3.1 DI-B1 — Qdrant payload / search mismatch → **CONFIRMED (mechanically), CURRENT on REL, RECON and CM**

This finding was PLAUSIBLE in the original audit. It is now demonstrated, with the limits stated below.

**Writer × reader matrix (REL; identical at RECON and CM).**

- **Correspondence writer.** Both legacy_v0 and unified_v1 end in `DatabaseService._create_and_store_embeddings` → `LangChainVectorService.replace_document` → `langchain_qdrant.QdrantVectorStore.add_texts`.
  - This stores `{page_content, metadata:{organization_id, project_id, document_id, chunk_id, …}}` in the collection `document_vectors`.
  - It writes no `org_id` key anywhere.
- **Tenant-scoped readers.** `VectorClient.search` / `_build_filter`, `RetrievalService.search` (AUTO) and `list_chunk_ids` all filter on `org_id` / `project_id`: flat, or `metadata.org_id` for `list_chunk_ids`.
  - `/documents` vector search passes a plain `dict` as the Qdrant filter. That raises `'dict' object has no attribute 'must'`, and the error is swallowed.
  - Even a proper `models.Filter` on the router's flat `organization_id` key returns 0.
- **Who can be found.** Only contract token chunks, which `contracts_ingest` writes flat, are reachable by scoped readers.

**Proof.** The run used the real classes with these substitutes: a deterministic 64-dimension fake embedding, a fake Motor database, a stubbed LlamaIndex embedder (it writes Mongo only), and Qdrant local mode. Results were identical at REL, RECON, CM and A.

| Probe | Hits |
|---|---|
| Points written by the real correspondence writer | 1 (`vector_sync_status` = synced 1/1) |
| `VectorClient.search`, flat `org_id` + `project_id` filter | **0** |
| `VectorClient.search`, no filter (`allow_global`) | 1 — the vector exists and is searchable |
| Filter on `metadata.organization_id` (the writer's real key) | 1 |
| Filter on `metadata.org_id` (what `list_chunk_ids` uses) | 0 |
| `list_chunk_ids` | **0** |
| `RetrievalService.search` (AUTO) | `backend_used=qdrant`, **0** results; no exception, so the Mongo failsafe never engages |
| `/documents` router vector search | 0 |
| **Positive control:** a flat point written through the real `VectorClient.upsert`, same org and project | found by `RetrievalService.search` (1) |

**Correction to the original audit.** The audit said `list_chunk_ids` "handles both shapes". That is wrong. It adds the `metadata.` prefix but keeps the key `org_id`, which the LangChain writer never writes. So reconcile, storage-sync and manual-pipeline sync status all compare correspondence documents against 0 points (new finding DI-N3).

**Remaining uncertainty.** A production scroll of `document_vectors` would confirm the payload shape on real data. The classification rests on mechanical execution of the real code paths, not on reading alone.

**Pipelines.**

| legacy_v0 | unified_v1 | contracts | manual |
|---|---|---|---|
| AFFECTED | AFFECTED | NOT_APPLICABLE (flat token chunks) | NOT_APPLICABLE for the shape. The manual pipeline cannot write at all; see DI-N1. |

### 3.2 DI-B2 — Contract Master projection never CURRENT → **FIXED ON PR #34 (CM `3ca0149`), NOT INTEGRATED**

| Question | A / REL / RECON | CM `3ca0149` |
|---|---|---|
| Runtime owner of CURRENT | Only `ContractReprojectionWorker.complete` (`services/contract_reprojection_worker.py:219`) and `PostPromotionSequencer.mark_projection_current` (`services/contract_post_promotion.py:60`) write CURRENT. Neither has a non-test caller in the backend, `worker.py` or any compose file (lead re-ran the `git grep` at all three SHAs: empty). | `worker.py:70-76` starts `contract_reprojection_runtime` when `START_CONTRACT_REPROJECTION_WORKERS` is set. The config default is false (`core/config.py:474`). `docker-compose.prod.yml:249` sets it to `"true"` on `contract-worker` only. The loop reaches `complete()` at `contract_reprojection_runtime.py:173`. |
| Durable PENDING signal | Promotion and reclassification write `contract_documents.projection_status=PENDING` (`contract_promotion.py:272`, `contract_classification_service.py:173`). Nothing consumes it. | The instrument row is the queue. Each pass reads `due_projection_filter` from Mongo (`contract_reprojection_runtime.py:67-81`). No in-process event and no Redis. |
| Lease / claim | `claim()` is `insert_one` (`:183-201`). An existing claim blocks forever and `lease_expires_at` is never read (DI-M37). `complete`/`fail` are fenced on revision only (DI-L16). | One conditional upsert takes over an expired lease (fewer than 6 attempts) or a failed claim whose backoff has passed (`:243-283`). Crash-looping claims are exhausted to FAILED (`:380-458`). Renew, complete and fail are fenced by owner token plus `classification_revision`. |
| CURRENT publication | Written unconditionally. | One Mongo transaction covers the ownership flip, the revision fence, the lexical row replacement and the source-generation check (`contract_reprojection_worker.py:462-542`, `contract_projection_builder.py:289-341`). Upsert count must equal payload count. **No read-back count of Qdrant points before CURRENT.** |
| Qdrant generation | None. | `source_classification_revision` is in each payload. Ids are stable, so a rebuild overwrites. Stale ids are deleted by diff, with an ownership re-check. **Search does not filter points by revision** (`is_stale_generation` has test callers only); currency is enforced per instrument by the resolver. |
| Evidence authority | The resolver silently skips non-CURRENT instruments, so the route answers `valid_empty` (REL `routers/contract_master_api.py:619`, RECON `:714`). REL still builds scope with `AuthorizedContractScope.for_tests(...)` (`:579`); RECON and CM use `authorize_contract_scope` (PR #31). | The resolver reports non-current instruments (`contract_scope_resolver.py:329-338`). `contract_service.py:1286-1308` raises `projection_not_current`, and the route returns **409**. |
| Governed generic-ingestion protection (DI-H17) | None: no reference to `contract_documents` in any ingest path. | Contract ingest invalidates the projection before, after and on failure (`contract_service.py:890, 972, 730/757/1007`), via `request_retry`, which sets PENDING and revokes the running claim in one transaction. Generic pipelines refuse governed documents through `governed_by_contract_master` (fails closed): `ingestion/pipeline.py:345`, `document_service.py:1158, 1719`, `routers/documents.py:1562`, `routers/retrieval_engine.py:182`. |
| Concurrent ingest protection | None. | A durable per-document source lease with a generation number (`contract_source_lease.py`), taken at `contract_service.py:585`. The builder refuses a source that is being written, and publication rechecks the generation inside the transaction. |

Residual gaps on CM, which are not blockers for integration but must be tracked:

- (a) No Qdrant read-back count before CURRENT.
- (b) No point-level revision filter at query time.
- (c) `PostPromotionSequencer.mark_projection_current` / `mark_projection_failed` remain public with no callers. The first writes CURRENT with no owner or vector check, and only an AST test stops it being called.
- (d) Publication requires Mongo transactions. On a standalone Mongo every generation goes to FAILED, visibly.
- (e) Every pass scans the full due set.
- (f) Ingest writes are protected by a time-based self-fence (7 minutes inside a 10-minute lease), not a per-write check.

**Runtime proof missing.** The five runtime integration suites were skipped because no replica-set Mongo was available. The classification is proven from code and the unit/AST tests. The runtime guarantee stays **PARTIAL** until those suites run against a disposable replica set, or until a staging promotion reaches CURRENT.

### 3.3 DI-B3 — metadata parser wipe → **CURRENT, reproduced on A, REL, RECON and CM**

`services/text_processing_service.py` is whitespace-identical across all four lineages. Script: the real `TextProcessingService.parse_extraction_report`, imported from each checkout in turn.

**Input.** A 27-field-style numbered report:

- `2) Letter No: ABC/PMC/2024/101`
- `6) References:` followed by `- letter no. XYZ/12 dated 01.08.2024`
- `8) Key Words:` followed by the case-specific lines below

**Output (every lineage, byte-identical):**

| Field | Control (`- EOT`, `- hindrance`) | Trigger (`- EOT`, `- letter no. XYZ/12 dated 01.08.2024`) |
|---|---|---|
| date | `05-08-2024` | `null` |
| subject | `Extension of time for Package 3` | `null` |
| letter_no | `ABC/PMC/2024/101` | `null` |
| from_company / to_company | `Alpha Constructions Ltd` / `Beta Infra Authority` | `null` / `null` |
| references | `[{letterNo: XYZ/12, date: 01-08-2024, …}]` | `[]` |
| summary | `- Contractor requests EOT of 45 days.` | `null` |
| keywords | `["EOT","hindrance"]` | `[]` |
| contractual_clauses / key_reply_points / full_content | populated | `[]` / `[]` / `null` |

Logged only as `ERROR Failed to parse extraction report: 1 validation error for ParsedDocumentMetadata keywords.1 Input should be a valid string … input_type=dict`.

**Mechanism.** `_parse_list_block` runs `parse_legacy_reference_text` on every list item. A reference-shaped keyword line becomes a `dict` inside `keywords: List[str]`. Pydantic rejects it, the broad `except` returns `ParsedDocumentMetadata()`, and every field is lost, not just the offending one.

**Trigger fields** (executed at REL and CM): `keywords`, `additional_keywords`, `contractual_clauses`, `key_reply_points`, `tags` and `sub_tags`, in both bullet and comma-joined form. `references` cannot trigger it, because it is typed `List[Union[str, Dict]]`. Prompt item #23 explicitly asks for "prior records relied upon", so reference-shaped list items are *expected* LLM output.

**Caller behaviour at REL** (shared by legacy_v0 and unified_v1 through `document_processor.py:1038`):

- **Parser path.** The regex parser is the production default: `use_pydantic_ai=False` (`config/document_processing_config.py:150`). The real production env value was not read.
- **Result reported.** The caller gets `success=True`, `metadata_source="openai_text_legacy_regex"`. No `partial_failures` entry and no degraded marker are written.
- **Status.** The document goes `metadata_extracted` → `completed`.
- **References wiped.** `update_fields["reference"] = []` (`document_service.py:1891-1897`), then `sync_bidirectional(references=[], clear_existing=True)` (`:2050-2056`). This **deletes existing parser reference links**. Lead verified this at REL.
- **Metadata snapshot.** `documents.metadata` is replaced by the empty snapshot. Scalar top-level fields are written only when truthy, so a first upload ends `completed` with every field empty, while a reprocess keeps stale scalars but loses references, links and the snapshot.

Incidental observation: *any* exception inside `parse_extraction_report` takes the same path. During the reproduction, a missing-config `Settings` validation error raised inside the parser produced the same silent all-empty result.

### 3.4 DI-B4 — resolved pages destroyed on retry → **PARTIAL; classified per pipeline**

| Pipeline | Status | Evidence |
|---|---|---|
| **legacy_v0** (production default) | **AFFECTED, by a different mechanism** | Legacy has no page rows and no page resume. A retry reruns the whole pipeline and overwrites `ocrText` and `full_text` without comparing them to what was stored (`database_service.py:276-277`, `document_service.py:1899-1900`). If the retry's OCR fails transiently, `ocr_service.py:629-648` returns None and the LLM's whole-file output replaces earlier OCR text (`document_processor.py:885`). This needs a later-stage failure plus a transient OCR failure. Same at all lineages. Severity HIGH, conditional. |
| **unified_v1** | **FIXED_IN_RELEASE** (PR #28) | The run is cumulative. The engine writes only pages it may change (`engine.py:272-312`), reads the run back via `load_run_pages` (`extraction_adapters/document_page_store.py:284-323`), and carries resolved pages forward (`engine.py:353-421`). A missing or malformed row raises `InconsistentExtractionRunError`, which sends the document to human review (`document_processor.py:708-726`). A partial result with nothing to retry goes to review (`document_service.py:1195-1205`). Pinned by `test_extraction_retry_page_preservation.py` (19 tests), `test_retry_cid_cross_regression.py`, `test_retry_withheld_pages_cross_regression.py` and `test_document_page_store.py`. The run id is still the job id: stable across attempts of one job, new per job. |
| **contracts** | **AFFECTED at REL; FIXED_NOT_INTEGRATED (RECON/CM)** | At REL the contract page store has no `load_run_pages` (lead verified: only `document_page_store` and the base `page_store` define it). An OCR retry takes the non-cumulative branch (`engine.py:188-189`), rebuilds other pages from native text as `TEXT_LAYER` (empty for scans), overwrites `contract_ocr_pages` on `(document_id, page_number)` (`contracts_ingest.py:1042-1061`), completes, and rebuilds `document_vectors` from the degraded text (`:1163-1183`). RECON adds `ResumableContractPageStore` (`extraction_adapters/contract_page_store.py:181-192`) and a hold-for-review (tests: `test_contract_ingest_withheld_pages.py`). **Residual on RECON/CM** (new finding DI-N8): rows already degraded by a REL-era retry are read back as resolved and cannot be re-OCR'd by an explicit retry; only `/contracts/{id}/reindex` repairs them. |
| **manual** `/v1/ingestion/jobs` | **NOT_APPLICABLE** | No extraction at all. It reads stored `full_text` / `ocrText` (`ingestion/pipeline.py:337-350`). |

Severity now: HIGH. The BLOCKER mechanism is gone on the page engine. What remains is the conditional legacy overwrite plus the unintegrated contract fix.

---

## 4. Pipeline taxonomy (verified at REL and RECON)

The four-pipeline split holds, with three refinements:

1. **legacy_v0 and unified_v1 are one pipeline with a single fork at the text extractor** (`document_processor.py:609`). They share one job queue (`document_processing_jobs`), one processor, the LLM metadata step, the parser, the Mongo writes, the Qdrant writer, reference sync and Falkor. Every finding downstream of text extraction therefore hits both.
2. **legacy_v0 is the production default.**
   - `UNIFIED_EXTRACTION_ENABLED=False` and the canary org list is empty (`core/config.py:202-206`).
   - `DOCUMENT_WORKER_PIPELINE_VERSIONS` defaults to `legacy_v0` (`docker-compose.prod.yml:275, 291`).
   - `document-worker-canary` defaults to **0 replicas** (`:320-339`). So a unified_v1 job created while the canary is scaled to 0 is never claimed (new finding DI-N5).
3. **The contracts pipeline always uses the unified `PageExtractionEngine` for PDFs** (`contracts_ingest.py:1364-1390`), independent of the correspondence flag. DOCX bypasses the engine. CM adds a fifth long-running process, the reprojection runtime, in `contract-worker`. It is not an ingestion entry point.

The manual pipeline runs as an in-memory background job inside the API container (`routers/retrieval_engine.py:143`, `services/background_jobs.py:38`). No upload path calls it.

Other entry points (REL):

| Entry point | What it feeds |
|---|---|
| Bulk upload; canonical insurance upload (`insurance.py:360`) | P1 |
| Inline `/documents/{id}/process` | P1, run inside the API request |
| Storage-sync resync | Re-embeds only |
| Enclosures | Nothing: stored, never extracted |
| Folder `/upload-file` | Nothing |
| Bank-guarantee and key-date CSV imports | Nothing |
| Deep-planning analyze | Nothing: ephemeral |
| `/insurance/upload` | Retired: returns 410 |

---

## 5. Full reconciliation table (80 findings)

Pipeline columns:

| Code | Meaning |
|---|---|
| A | AFFECTED |
| F | FIXED |
| – | NOT_APPLICABLE |
| ? | UNKNOWN |
| A→F@X | AFFECTED on REL, FIXED on lineage X |

**Now** = recommended severity of what remains on REL.

| ID | Orig | Title (short) | Classification | Now | v0 | v1 | contracts | manual | Deciding evidence (REL unless stated) |
|---|---|---|---|---|---|---|---|---|---|
| DI-B1 | BLK | Qdrant payload/search mismatch | CURRENT (CONFIRMED mech.) | BLOCKER | A | A | – | – | §3.1; 0 hits vs positive control 1 |
| DI-B2 | BLK | CM projection never CURRENT | FIXED_NOT_INTEGRATED (PR #34) | BLOCKER on REL | – | – | A→F@CM | – | §3.2 |
| DI-B3 | BLK | parser wipe → empty metadata, success | CURRENT (reproduced) | BLOCKER | A | A | – | – | §3.3 |
| DI-B4 | BLK | retry destroys resolved pages | PARTIAL | HIGH | A (whole-doc overwrite) | F | A→F@RECON | – | §3.4 |
| DI-H1 | HIGH | legacy OCR decision from first 5 pages | CURRENT | HIGH | A | F | – | – | `ocr_service.py:166-198, 282-305`; legacy is default |
| DI-H2 | HIGH | no (cid:N) detection | FIXED_IN_RELEASE | — | F | F | F | – | `text_quality.py`; legacy `ocr_service.py:189-224, 650-730`; unified `engine.py:136, 183-200, 513-575` |
| DI-H3 | HIGH | embedding errors swallowed; completion ignores sync | CURRENT | HIGH | A | A | ? | – | `database_service.py:206-221`; `document_service.py:1215-1240` |
| DI-H4 | HIGH | LLM "Full Content" is authoritative text | CURRENT | HIGH | A | A | – | – | `publication_policy.py:102-103, 171-185`; `database_service.py:296` |
| DI-H5 | HIGH | reprocess overwrites human edits | CURRENT | HIGH | A | A | – | – | `document_service.py:1893-1911`; override flag written `:2842`, never read |
| DI-H6 | HIGH | OCR fallback uses filename as letterNo/subject | CURRENT | HIGH | A | A | – | – | `document_processor.py:784-785` |
| DI-H7 | HIGH | Falkor graph cross-tenant delete/read | CURRENT | HIGH | A | A | – | – | `falkor_graph_service.py:407-411`; `publication_policy.py:498`; `letter_drafting/context.py:586` |
| DI-H8 | HIGH | processing-status route missing | CURRENT | HIGH | A | A | – | – | no router path contains `processing-status`; `get_processing_status` at `document_service.py:953` unrouted |
| DI-H9 | HIGH | UI unaware of review/partial states | CURRENT | HIGH | A | A | – | – | `client/src/pages/UploadPage.tsx:136-138` |
| DI-H10 | HIGH | inline `/process`, false success | PARTIAL | HIGH | A | A | A→F@RECON/CM | – | controller `documents.py:1476-1506`; contracts only guarded on RECON/CM (409) |
| DI-H11 | HIGH | no attempts cap; OCR no timeout | PARTIAL | HIGH | A | A | – | – | OCR subprocess + 900 s timeout at REL (`ocr_service.py:123, 533-566`); claim `document_service.py:1006` still uncapped |
| DI-H12 | HIGH | bulk in BackgroundTasks | CURRENT | HIGH | A | A | – | – | `documents.py:1028, 1283`; no cancel/retry route |
| DI-H13 | HIGH | dedupe non-atomic, no unique index | CURRENT | HIGH | A | A | ? | – | `core/database.py:211, 230-233` non-unique; `$set` after insert swallowed |
| DI-H14 | HIGH | contract job lost during backoff | FIXED_NOT_INTEGRATED (CM) | HIGH on REL | – | – | A→F@CM | – | queue identical A/REL/RECON; CM `waiting` state + recovery |
| DI-H15 | HIGH | contract PARTIAL ignored; DOCX/org → 0 clauses | PARTIAL | HIGH | – | ? | A | – | PARTIAL hold only RECON/CM (`contracts_ingest.py:1660`); DOCX no pages (`:1250/1324`), org scope fails, error swallowed — all lineages |
| DI-H16 | HIGH | clause point id not UUID | CURRENT (CONFIRMED mech.) | HIGH | – | – | A | – | real `VectorClient.upsert` raised "not a valid UUID"; `contract_clause/agent.py:357-362` logs only |
| DI-H17 | HIGH | ingest mutates governed CM docs | FIXED_NOT_INTEGRATED (CM) | HIGH on REL (latent) | A→F@CM | ? | A→F@CM | A→F@CM | §3.2 row 7 |
| DI-H18 | HIGH | folder upload no AV / size / dedupe | PARTIAL | HIGH | – | – | – | – | size cap fixed (`folder_structure.py:186-192`); no AV, no content dedupe |
| DI-H19 | HIGH | no fact-level provenance | CURRENT | HIGH | A | A | ? | ? | `database_service.py:420-470`; §6 |
| DI-H20 | HIGH | cross-tenant filename fallback | CURRENT (latent) | HIGH | A | A | – | – | `database_service.py:357-359` |
| DI-H21 | HIGH | legacy ContractsPage large upload | CURRENT | MEDIUM | – | – | A | – | `/contracts/upload` page works; legacy page is also a TS error |
| DI-H22 | HIGH | blank page burns OCR attempts | CURRENT | MEDIUM | – | A | F@RECON/CM | – | engine still OCRs BLANK; only RECON contract path settles blanks |
| DI-M1 | MED | duplicate job race | CURRENT | MEDIUM | A | A | – | – | find-then-insert `document_service.py:872-933` |
| DI-M2 | MED | no job indexes | CURRENT | MEDIUM | A | A | – | – | zero index definitions |
| DI-M3 | MED | no lease token on job writes | CURRENT | MEDIUM | A | A | – | – | `:1118`, `:1215` |
| DI-M4 | MED | stage failure retries whole pipeline | CURRENT | MEDIUM | A | A | – | – | `:2057-2082` |
| DI-M5 | MED | sync I/O in async | CURRENT | MEDIUM | A | A | A | – | `s3_service.py:75, 96`; `document_service.py:224` |
| DI-M6 | MED | double embedding | CURRENT | MEDIUM | A | A | – | – | `database_service.py:499, 532` |
| DI-M7 | MED | page store publish/heads unused | PARTIAL | LOW | – | A | – | – | pages now read for resume; `publish_run` still has no caller |
| DI-M8 | MED | quality-gate re-OCR loop | PARTIAL | LOW | – | A | – | – | QG → review at REL (`:1195-1205`); message still "0 page(s)" |
| DI-M9 | MED | no document requeue endpoint | CURRENT | MEDIUM | A | A | F | ? | only `/contracts/{id}/ocr/retry` exists |
| DI-M10 | MED | LLM text embedded on OCR failure | CURRENT (anchor file wrong) | MEDIUM | A | A | – | – | real site `document_processor.py:886` |
| DI-M11 | MED | invalid LLM output still `completed` | CURRENT | **HIGH** | A | A | – | – | same root as DI-B3; `document_processor.py:1036-1076` |
| DI-M12 | MED | contracts no duplicate precheck | CURRENT | MEDIUM | – | – | A | ? | no `precheck_upload` on any contract path |
| DI-M13 | MED | contract chunk size/merge race | PARTIAL | LOW | – | – | A | – | size cap fixed at REL; no merge lock, index non-unique |
| DI-M14 | MED | multipart partial commit | CURRENT | MEDIUM | – | – | A | – | RECON/CM add a per-file hold that can fail file k |
| DI-M15 | MED | bulk status IDOR | CURRENT | MEDIUM | A | A | – | – | no org/user on job; controller "basic security check" placeholder |
| DI-M16 | MED | duplicate bulk filenames collapse | CURRENT | MEDIUM | A | A | – | – | `_build_bulk_file_lookup` `:1076-1091` |
| DI-M17 | MED | insurance letterNo = policy no. | CURRENT | MEDIUM | A | A | – | – | `insurance.py:404` (RECON `:422`) |
| DI-M18 | MED | encrypted PDF silent | FIXED_IN_RELEASE | — | F | F | F | – | `ocr_service.py:615-627` fails visibly; residual: retried as transient |
| DI-M19 | MED | MIME re-sniffed without filename | UNVERIFIED | LOW | – | A | – | – | code holds (`document_service.py:532`); impact depends on the production storage mode, not read |
| DI-M20 | MED | deep-planning upload unbounded | PARTIAL | LOW | – | – | – | – | 100 MB read cap at REL; no prompt-size cap |
| DI-M21 | MED | CSV import read before authorize | PARTIAL | LOW | – | – | – | – | read capped at REL; RECON adds `require_project`; authorize still after read |
| DI-M22 | MED | AV fail-open defaults | CURRENT | LOW | A | A | A | – | defaults unchanged; prod compose + validator enforce AV |
| DI-M23 | MED | reference resolution org-wide / no lifecycle | CURRENT | MEDIUM | A | A | – | – | `reference_sync_service.py:420-430, 507-582` |
| DI-M24 | MED | unresolved refs expire, no reverse lookup | CURRENT | MEDIUM | A | A | – | – | a daily reaper exists (`scheduler.py:64`, 200/run) — audit understated retry |
| DI-M25 | MED | reference regex gaps | CURRENT (executed) | MEDIUM | A | A | – | – | `Ref: X dtd`, `vide letter X dt`, `[1] X` polluted; `VO-12` read as a letter |
| DI-M26 | MED | ObjectId as Letter code | CURRENT | MEDIUM | A | A | – | – | cited line latent; live via `graph_ingestion_service.py:458-464, 522-535` |
| DI-M27 | MED | unscoped Graphiti node ids | CURRENT (dormant) | LOW | ? | ? | – | – | Graphiti off in prod (`docker-compose.prod.yml:54-55`) |
| DI-M28 | MED | PydanticAI 12 000-char truncation | CURRENT | LOW | A* | A* | – | – | *only when PydanticAI is enabled (off by default) |
| DI-M29 | MED | search date filter on createdAt | CURRENT | LOW | A | A | – | – | `routers/search.py:99` |
| DI-M30 | MED | Marker unused, no timeout | CURRENT | MEDIUM | – | – | A | – | `contracts_ingest.py:516, 1557` |
| DI-M31 | MED | orphan Qdrant points served | CURRENT | MEDIUM | A | A | A | A | `publication_policy.py:396-415` |
| DI-M32 | MED | temp dirs never cleaned | PARTIAL | MEDIUM | A | A | – | – | legacy OCR run dirs fixed (PR #25); `document_processing/<filename>` and `page_batches/<doc>` remain |
| DI-M33 | MED | append-only `.md` summary | CURRENT | MEDIUM | A | A | – | – | `file_service.py:27-75` |
| DI-M34 | MED | vector_sync_status untenanted | CURRENT | LOW | A | A | A | A | no writer sets org/project |
| DI-M35 | MED | contract enqueue drops payload | CURRENT | MEDIUM | – | – | A | – | router still writes `reindex_queued` |
| DI-M36 | MED | contract claim race / status | PARTIAL | MEDIUM | – | – | A (mitigated @CM) | – | CM per-source lease stops double processing; no `retrying` state |
| DI-M37 | MED | expired reprojection claims; `for_tests` scope | FIXED_NOT_INTEGRATED | MEDIUM on REL | – | – | A→F@CM | – | part 2 fixed RECON (PR #31); part 1 fixed CM (PR #34) |
| DI-M38 | MED | non-atomic contract vector swap | CURRENT | MEDIUM | – | – | A | – | `delete_many`+`insert_many`; CM `reconcile.py` skips contracts |
| DI-L1 | LOW | ext/MIME mismatch | CURRENT | LOW | A | A | A (partial) | – | |
| DI-L2 | LOW | raw filename stored | CURRENT | LOW | A | A | ? | – | `documents.py:827` |
| DI-L3 | LOW | job sha256=None | CURRENT | LOW | A | A | – | – | |
| DI-L4 | LOW | profile photo trusts content_type | FIXED_IN_RELEASE | — | – | – | – | – | `profiles.py:152-161` sniffs bytes |
| DI-L5 | LOW | nginx 200 MB < 500 MB config | CURRENT | LOW | A | A | – | – | lower the app setting; nginx 200m is the deliberate outer bound |
| DI-L6 | LOW | folder key overwrite race | CURRENT | LOW | – | – | – | – | |
| DI-L7 | LOW | prompt numbering gaps | CURRENT | LOW | A | A | – | – | `openai_service.py:275-286` |
| DI-L8 | LOW | PATCH summary-metadata tags unvalidated | CURRENT | LOW | A | A | ? | – | no allow-list exists at all |
| DI-L9 | LOW | edit audit keys only | CURRENT | LOW | – | – | – | – | |
| DI-L10 | LOW | P4 chunk hash overwritten | CURRENT | LOW | – | – | – | A | `ingestion/pipeline.py:364` |
| DI-L11 | LOW | `_memory_index` grows | FIXED_NOT_INTEGRATED (CM) | LOW | – | – | A→F@CM | A→F@CM | proof: `_memory_index` 2 on REL/RECON, 0 on CM |
| DI-L12 | LOW | no Qdrant payload indexes | CURRENT | LOW | A | A | A | A | |
| DI-L13 | LOW | P4 retry never fires | CURRENT | LOW | – | – | – | A | `pipeline.py:309-322` swallows |
| DI-L14 | LOW | dead UI controls | CURRENT | LOW | A | A | – | – | |
| DI-L15 | LOW | chronology fallback labelled EXACT | CURRENT | LOW | A | A | – | – | `chronology.py:667-678` |
| DI-L16 | LOW | `mark_projection_failed` unfenced | FIXED_NOT_INTEGRATED (CM) | LOW | – | – | A→F@CM | – | method has no caller on any lineage |

**Totals:**

| Classification | Count | IDs |
|---|---|---|
| CURRENT | 58 | |
| PARTIAL | 12 | B4, H10, H11, H15, H18, M7, M8, M13, M20, M21, M32, M36 |
| FIXED_IN_RELEASE | 3 | H2, M18, L4 |
| FIXED_NOT_INTEGRATED | 6 | B2, H14, H17, M37, L11, L16 |
| UNVERIFIED | 1 | M19 |
| STALE | **0** | |

No finding was wholly wrong. Anchor and claim corrections are listed in §7.

---

## 6. Data model: source facts vs analysis vs reply recommendations

Measured at REL; identical at RECON and CM (`models/document_metadata.py`, `openai_service.py`, `pydantic_ai_service.py`, `database_service.py`, `evidence_graph_service.py` and `letter_drafting/*` are unchanged).

**The current model does not separate the three layers at all.**

- **One extraction call.** A single LLM prompt (`services/openai_service.py:254-292`, 27 numbered items, #10 missing, "Key Words" at both #18 and #21) produces source facts, analysis and reply advice together.
- **One flat destination.** They land as sibling top-level fields on `documents` (`models/document.py:75-108`) and again inside the `documents.metadata` snapshot of the whole `ParsedDocumentMetadata`.
- **No field-level provenance.** Only a document-level `metadata_source` label exists.

**`key_reply_points` ("points to be addressed while responding") is treated as a source fact:**

- **Qdrant.** It is copied into every Qdrant chunk payload next to `summary`, `issue_nature`, `claim_category` and `alleged_responsibility` (`database_service.py:440-462`), though it is not in the embedded text.
- **UI.** It is shown in its own tab with no AI-generated label (`client/src/pages/LetterSummaryPage.tsx:813-842`).
- **Drafting.**
  - Each point becomes a `ReplyMatrixRow.incoming_point` (`letter_drafting/planning.py:99-134`). LLM advice is thus presented as *what the incoming letter says*, and each row is stamped with the same unrelated fact `source_ids` (`:120`).
  - It derives `issue_type` (`incoming_analyzer.py:112-114`).
  - It feeds `clause_checker.py:87` and `user_direction.py:45-102`.
- **Reference sync** does not use it.

This is recorded as new finding **DI-N6 (HIGH)**.

**`summary` is served as the document body** when no body text exists (`publication_policy._TEXT_FIELDS` includes `"summary"`, `:102`), and drafting uses it as `main_request`. This is recorded as new finding **DI-N7**.

**`ai_extractions` provenance is nominal:**

- `prompt_version` is the hard-coded label `"existing_document_metadata.v2"`.
- `model` is the `metadata_source` label; the real model name is never stored.
- `confidence` is a constant `0.75`.
- `parsed_output` omits `key_reply_points`.

| Target block | Status at REL | Notes |
|---|---|---|
| `source_facts.letter_number/date/from/to/subject` | PRESENT-MIXED | Flat fields plus a metadata copy. No span or confidence. OCR fallback uses the filename (DI-H6). One parser failure empties all of them (DI-B3). |
| `source_facts.references` | PRESENT-MIXED | Untyped, so VO/IPC/drawings are read as letters (DI-M25). |
| `source_facts.clauses` | PRESENT-MIXED | Mixes cited clauses with LLM-inferred "relied upon" items. |
| `source_facts.amounts`, `deadlines`, `attachments` | MISSING | Amounts and deadlines are re-derived by regex at each drafting run and never stored. The letter's "Encl:" list is never extracted. |
| `summaries.short_summary` | PRESENT-MIXED | Contains analysis ("responsibility alleged… required action") and is served as body text. |
| `summaries.detailed_summary` | MISSING | `full_text` is an LLM re-typing, not a summary (DI-H4). |
| `issues.*` | MOSTLY MISSING | Only `issue_nature` and `alleged_responsibility` exist, as single enums stored beside facts. Requests are folded into `key_reply_points`. Decisions, commitments and unresolved matters are absent. |
| `reply_analysis.points_requiring_response` / `suggested_response_considerations` | PRESENT-MIXED | Both are `key_reply_points`: stored, indexed and displayed as fact (DI-N6). |
| `reply_analysis.evidence_needed`, `contractual_points_to_verify`, `deadlines_to_protect` | MISSING on the document | Computed per draft run only. |
| `classification.document_type` | PRESENT-MIXED | `uploadType` plus a first-keyword-wins heuristic in `ai_extractions`. |
| `classification.keywords` | PRESENT-MIXED | Two prompt items share the label. It is one of the DI-B3 trigger fields. |
| `classification.tags` / `subtags` | **PRESENT, correctly separated** | Controlled vocabulary in `extracted_tags` / `extracted_subTags`, kept apart from user tags. |
| `provenance.*` | MISSING per field | `document_id` exists at record level. Page exists only in the unified page store and is not linked to fields. The span, model, prompt version, confidence and review status that exist are fake or constant. |

---

## 7. Corrections to the original audit

| Finding | Correction |
|---|---|
| All | The audited tree was **older than REL** for the extraction, OCR and upload-cap code. The audit's findings on those areas therefore describe code REL has already replaced. It was also dirty, so its anchors do not resolve against any commit. |
| DI-B1 | `list_chunk_ids` does **not** handle both shapes (§3.1). Otherwise confirmed. |
| DI-B4 | The cited unified_v1 mechanism is fixed at REL. The contract path remains on REL, and the legacy path has a different overwrite mechanism. |
| DI-H10 | "Doc stuck `processing` on failure" holds only for a non-exception failure. An exception sets `failed` (`document_service.py:2084-2118`). |
| DI-H16 | PLAUSIBLE → confirmed by executing the real `VectorClient.upsert`. |
| DI-H18, DI-M13, DI-M20, DI-M21 | The size caps (`read_upload_within_limit`) are already in REL. |
| DI-M10 | Anchor names the wrong file. The code is in `document_processor.py` (`:765` on A, `:886` on REL). |
| DI-M24 | A daily reaper exists (`scheduler.py:64`). The defect is the 30-day expiry plus the missing reverse lookup. |
| DI-M26 | The cited `previous_letter_id` line is latent. Two sibling paths that key Letter nodes on ObjectIds are live. |
| DI-L4 | Already fixed at REL. |

## 8. New findings discovered during reconciliation

These are not in the original 80 and are carried into the gap register as `DI-N*`.

| ID | Sev | Pipelines | Finding | Evidence |
|---|---|---|---|---|
| DI-N1 | HIGH | manual; reconcile/storage-sync repair | Chunk ids from `deterministic_chunk_id` (`"<docid>-<24hex>"`, `ingestion/chunk_ids.py:18-20`) are used as Qdrant point ids (`retrieval/vector_client.py:276`) and rejected as "not a valid UUID". The job goes FAILED, so manual-pipeline and repair vectors never land. | Executed at REL and CM (local mode). |
| DI-N2 | MEDIUM | v0, v1 | `/documents` vector search passes a plain `dict` filter. `similarity_search` swallows the exception and returns `[]`. | B1 proof, `router_dict_filter=0`. |
| DI-N3 | MEDIUM | v0, v1, manual | `list_chunk_ids` filters on `metadata.org_id`, which no writer produces. Reconcile, storage-sync and sync status see 0 points for correspondence. | B1 proof, `list_chunk_ids=0`. |
| DI-N4 | MEDIUM | contracts / CM | `ContractClassificationService.confirm` checks `expected_revision` on read (`:134-141`) but updates on `_id` only (`:167-176`). Two concurrent corrections can both write revision N+1. | All lineages including CM (lead verified at CM). |
| DI-N5 | MEDIUM | v1 | A unified_v1 job created while `document-worker-canary` has 0 replicas (the default) is never claimed, because the main worker claims only `legacy_v0`. | `docker-compose.prod.yml:320-339`; conditional on enabling a canary org without scaling the worker. |
| DI-N6 | HIGH | v0, v1 → drafting | LLM reply advice (`key_reply_points`) is presented as incoming-letter content in the drafting reply matrix, with unrelated `source_ids`; it is also indexed in Qdrant payloads and shown unlabelled. | §6. |
| DI-N7 | MEDIUM | v0, v1 | `authoritative_text` serves the LLM `summary` as document body when no text exists. | `publication_policy.py:102, 171-185`. |
| DI-N8 | MEDIUM | contracts (RECON/CM) | Contract pages degraded by a REL-era OCR retry are read back as resolved. An explicit OCR retry cannot repair them; only reindex can. | `engine.py:301-305`; router refusal of pages with nothing unresolved. |

Smaller siblings are recorded under their parent finding rather than as new findings:

| Parent | Sibling |
|---|---|
| DI-H13 | The swallowed `$set` also carries `duplicate_status=pending`. If it fails, a possible duplicate is never held back. |
| DI-H10 | The viewer's Retry expects a `job_id` that `DocumentProcessingResult` lacks. |
| DI-M35 | OCR retry, reindex and upload share `upload_id` as the job id, so they collide. |
| DI-H14 | A graceful shutdown during the backoff sleep loses the job, just as a kill does. |
