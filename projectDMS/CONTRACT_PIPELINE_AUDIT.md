# Contract Upload Processing Search QA Pipeline Audit

## Executive Summary

Original audit score: **6.3/10**

First implementation reassessment score: **7.8/10**

Latest reassessment score after remaining blocker implementation: **8.6/10**

The latest score improves because the remaining blocker class items have now been implemented: upload status exposes stage-level ingestion progress, chunk upload tracks received/missing chunks and merges only after all chunks are present, `/contracts/search` accepts structured legal filters, contract search uses category terms as ranking context instead of mutating the user query, iterative Q&A critique/refinement now requests and parses JSON, and contract search applies a clause-aware post-fusion reranker. Remaining gaps are now mostly production-hardening items: persisted queue recovery/replay, extraction confidence review, benchmarked retrieval evals, citation repair instead of sentence dropping, and a neural/cross-encoder reranker if latency and deployment budget allow.

The pipeline now has a stronger contract-specific retrieval foundation: scoped upload sessions, multipart and chunked upload paths, stage-aware ingestion, clause-aware chunking, rich clause metadata, Qdrant dual-write, lexical plus vector search, clause expansion for Q&A, structured contract search, and iterative Q&A. The remaining weak points are mostly operational and quality-measurement gaps rather than core flow gaps.

End-to-end flow found:

Upload page creates a session, uploads small files via multipart and large files via chunks, then polls status. Backend stores the file, creates a contract document, enqueues ingestion, OCRs/materializes the file, extracts clauses, writes `document_vectors`, dual-writes Qdrant, then search and Q&A consume those vectors.

Key files reviewed:

- `client/src/pages/ContractsUploadPage.tsx`
- `client/src/pages/ContractsSearchPage.tsx`
- `client/src/pages/ContractQAPage.tsx`
- `client/src/services/contracts-api.ts`
- `backend/rbac_backend/routers/contracts.py`
- `backend/rbac_backend/routers/retrieval_engine.py`
- `backend/rbac_backend/services/contract_service.py`
- `backend/rbac_backend/services/contracts_ingest.py`
- `backend/rbac_backend/services/contract_ingest_queue.py`
- `backend/rbac_backend/retrieval/service.py`
- `backend/rbac_backend/retrieval/vector_client.py`
- `backend/rbac_backend/retrieval/embeddings.py`
- `backend/rbac_backend/retrieval/generator.py`
- `backend/rbac_backend/models/contract_models.py`

## Strengths

- Upload is scope-aware and session-bound. Filename, org/project scope, max size, chunk count, and expiry are validated server-side.
- Large file support exists, with chunk validation, merge, storage, cleanup, and queued ingestion.
- Contract ingestion is clause-oriented, not arbitrary fixed chunking. It extracts complete clauses, splits long clauses, and stores clause number/title/type/hierarchy/page/tags.
- `/contracts/search` groups results back to clause-level hits and fetches all chunks for matched clauses, which is good for legal review.
- Search UX displays clause metadata, source files, pages, tags, and grouped clause content.
- Q&A has useful guardrails: only retrieved context, SCC over GCC, citation requirement, prompt-injection warning, and iterative critique/refinement.

## Original Critical Issues & Bottlenecks

The following findings are retained from the original audit for traceability. Several high and medium items are now resolved by the implementation record at the end of this file.

1. **Q&A can retrieve non-contract content in all-file mode.**
   `ContractQAPage` sends org/project/document filters but no `uploadType=document_type=contract` metadata filter. The generic retrieval service forwards only org/project/document/tags/metadata to Qdrant. Since the vector collection is shared (`document_vectors`), all-project Q&A can mix letters/documents with contracts.

2. **Q&A Mongo fallback likely misses contract ingestion records.**
   Generic retrieval falls back to `db.chunks`. Contract ingestion stores records in `document_vectors`. If Qdrant is unhealthy, contract Q&A can silently degrade to irrelevant or empty results.

3. **Q&A context is too shallow.**
   `_build_snippet` truncates evidence to 400 chars. Iterative Q&A prompts only those snippets. This loses most long clauses and weakens legal answers.

4. **`use_enriched_text` is effectively inert for contract clauses.**
   Contract Qdrant payloads set `text_enriched` to `None`. The Q&A page always sends `use_enriched_text: true`, but retrieval falls back to raw text.

5. **Ingestion is not idempotent in Mongo.**
   `insert_document_vectors` always uses `insert_many`; it does not delete/upsert by `document_id`, `chunk_id`, or checksum. Retries can create duplicate Mongo vector records and duplicate search hits.

6. **Queue failure can break upload after storage.**
   Upload endpoints call `queue.enqueue(...)` directly. If Redis/queue is disabled or unavailable, enqueue raises; there is no synchronous fallback or durable DB-backed pending state, despite startup tolerating queue failure.

7. **Indexes are incomplete and possibly never created.**
   The code defines only org/project/type/created and text indexes. Search later filters by `document_id`, `clause_number`, and `clause_start_position`. I did not find `ensure_indexes()` being called.

8. **Frontend processing timeout is too blunt.**
   Upload polling marks processing failed after 10 minutes client-side. Large OCR/Marker/LLM extraction jobs may legitimately exceed that, creating false failure UX.

9. **Q&A citation enforcement can delete useful output.**
   `_enforce_citations` drops every sentence without a citation token when required. If the model gives a good answer but citation formatting is imperfect, the user may get an empty or partial answer.

10. **Critique refinement parser is noisy.**
    `_critique_and_refine` treats any short bullet, including issue bullets, as a refined query. This can add irrelevant second-pass searches.

## Improvement Recommendations

### Upload & Ingestion

- Add a resilient enqueue path: if Redis is unavailable, persist a DB job as `queued_pending_worker` and expose a repair/requeue action.
- Add backend progress stages: `uploaded`, `stored`, `ocr`, `parsing`, `clause_extraction`, `embedding`, `vector_upsert`, `completed`. The response model already has `progress`.
- Make chunk upload resumable. Store received chunk indexes and checksums; do not merge solely because `chunkIndex == totalChunks - 1`.
- Use server-reported long-running states instead of client-side failure after 10 minutes.

### Clause Processing & Storage

- Replace `insert_many` with idempotent bulk upserts keyed by deterministic `chunk_id`, or delete existing `document_id` vectors before reinserting.
- Actually call `ensure_indexes()` during ingestor startup and add indexes for:
  `uploadType + organization_id + project_id + document_id + clause_number + clause_start_position + chunk_index`.
- Generate `text_enriched` for contract chunks:
  `Clause {number}: {title}\nSection path: ...\nTags: ...\nPage: ...\nText: ...`
- Improve regex clause extraction for legal numbering variants, schedules, appendices, tables, subclauses like `(a)`, `(i)`, and `Part A/B`.
- Store clause extraction confidence/source and allow fallback review when Marker/LLM extraction is skipped because of size.

### Search Experience

- Keep `/contracts/search` as the contract-specific retrieval API, but add real semantic controls: exact phrase, clause number, title, tag, page range, GCC/SCC filter, schedule/section type.
- Do not append category keywords into the visible query string. Send categories as structured filters/boosts; currently quoted phrases cause lexical search to ignore appended category keywords.
- Replace raw RRF score display with percentage/relevance label or hide it. Current values are not user-meaningful.
- Add a reranker after Qdrant/lexical retrieval, ideally clause-aware cross-encoder or LLM lightweight rerank for top 30 clauses.
- Return `matched_terms`, `why_matched`, and highlighted spans from backend rather than frontend regex fallback.

### Q&A / LLM Interaction

- Make contract Q&A use the same clause assembly strategy as `/contracts/search`: retrieve top chunks, expand to complete clauses from `document_vectors`, then build the prompt.
- Add mandatory contract filters for Q&A: `uploadType=contract` or `document_type=contract`, especially for all-project mode.
- Increase or dynamically allocate context per clause. Use full clause text for top 3-5 clauses and concise summaries for secondary clauses instead of 400-char snippets.
- Expose clause metadata in citations: clause number, title, page numbers, file name, section path.
- Change citation enforcement from "drop sentence" to "repair citations or return validation warning." Dropping sentences hides model failures.
- Parse critique output structurally as JSON, not free-text bullets.

### UX & Question Framing

- Add guided question templates: obligations, approvals, notices, timelines, payment, variation, EOT, LD/penalty, termination, precedence, dispute resolution.
- Let users ask against "selected clauses from search" directly. Search should have "Ask about these clauses."
- Probing questions should be based on retrieved clause metadata and answer gaps, not only regex over the answer.
- Add "scope clarity" in Q&A: selected file count, contract names, date/version, and whether SCC/GCC sections were retrieved.
- Show "not enough evidence" separately from "information not found," with suggestions to broaden scope or search by clause number.

## Original Prioritized Action Plan

This action plan reflects the original audit state. Implemented items are summarized in the latest implementation record below.

### High

1. Add `uploadType/document_type=contract` filter to `ContractQAPage` payload and backend retrieval filters.
2. Change contract Q&A to expand retrieved chunks into complete clauses from `document_vectors`.
3. Fix Mongo fallback for contract Q&A to search `document_vectors`, not only `db.chunks`.
4. Make vector ingestion idempotent with deterministic `chunk_id` upserts/deletes.
5. Add the missing compound indexes and ensure they run at startup.

### Medium

1. Implement `text_enriched` for contract clauses and embed enriched text.
2. Replace free-text critique parsing with JSON refinements.
3. Add stage-level progress and avoid false client timeout failures.
4. Add structured search filters for clause number/title/tag/page/GCC/SCC.
5. Improve chunk resumability and queue failure recovery.

### Low

1. Improve relevance score display.
2. Add guided legal question templates and "Ask about selected clauses."
3. Add extraction confidence display for admins.
4. Add saved Q&A sessions with source snapshots for auditability.

## Notes

The original audit was audit-only. Subsequent implementation passes modified the contract upload, ingestion, search, and Q&A code paths described above.

## Latest Implementation Record

Implemented after the 7.8/10 reassessment:

- Stage-level ingestion progress is now persisted through job status updates (`materializing`, `ocr`, `ingestion`, `parsing`, `categorization`, `clause_extraction`, `embedding`, `vector_storage`, `completed`, `failed`) and surfaced in the upload page.
- Chunk upload is resumable at the API contract level: upload sessions track `received_chunks`, `missing_chunks`, `chunk_checksums`, and `upload_complete`; merge is triggered only when no chunks are missing.
- `/contracts/search` now accepts structured legal filters for exact phrase, clause number, clause title, section heading, clause tag, category terms, and page range.
- Contract search applies structured filters server-side, post-filters vector hits so they cannot bypass those filters, uses category terms for lexical/vector ranking context, and applies a clause-aware reranker after RRF assembly.
- Iterative contract Q&A critique/refinement now requests JSON and parses `issues` plus `refinements` before falling back to legacy free-text parsing.
- Search UI now exposes legal filters, preserves filters in saved searches/history, and no longer appends category keywords to the visible query.

Validation:

- `python -m py_compile backend/rbac_backend/models/contract_models.py backend/rbac_backend/routers/contracts.py backend/rbac_backend/services/contract_service.py backend/rbac_backend/services/contracts_ingest.py backend/rbac_backend/retrieval/service.py backend/rbac_backend/retrieval/vector_client.py`
- `npm run build` in `client`
