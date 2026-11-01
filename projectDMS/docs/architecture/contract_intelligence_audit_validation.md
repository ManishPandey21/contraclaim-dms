# Contract Intelligence Audit Validation

> Validation date: 2026-06-28
> Scope: Upload Contract, Search Clauses, Contract Q&A, Contract Appraisal, clause library extraction, hierarchical chunking, hybrid search, re-ranking, and knowledge graph integration.

## Executive Summary

The repository contains a real end-to-end contract intelligence pipeline:

- Contract upload supports multipart and chunked upload with scope checks, size limits, MIME validation, optional antivirus scan, storage-provider abstraction, queue fallback, and job status tracking.
- Contract ingestion extracts text, optionally uses Marker markdown extraction plus LLM clause spans, falls back to regex clause extraction, stores clause-level records in `document_vectors`, dual-writes to Qdrant when configured, and can upsert a contract clause graph into FalkorDB.
- Search Clauses uses `/api/contracts/search` and performs structured Mongo filtering, lexical candidate selection, optional Qdrant vector candidates, reciprocal-rank fusion, and lightweight reranking.
- Contract Q&A uses `/api/v1/retrieval/contract-qa`, expands retrieved chunks to full clauses, adds FalkorDB graph-related clauses, enforces citations, runs iterative critique/refinement, and prefers Qdrant with Mongo fallback.
- Contract Appraisal reuses the contract Q&A engine section-by-section and persists reports, citations, structured registers, review comments, approval/rejection, regeneration, and export endpoints.

The implementation is directionally sound and the focused backend validation passed. The main gaps are production observability/e2e coverage and dependence on optional services: Marker, OpenAI embeddings, Qdrant, and FalkorDB. If those are disabled or unavailable, the system degrades to regex/Mongo fallback mode, which is functional but weaker for complex clause extraction and semantic retrieval.

## Validation Commands

| Check | Result |
| --- | --- |
| `python -m pytest backend\rbac_backend\tests\test_retrieval_engine_basics.py backend\rbac_backend\tests\test_retrieval_quality.py backend\rbac_backend\tests\test_retrieval_engine_scope_isolation.py backend\rbac_backend\tests\test_contract_appraisal.py backend\rbac_backend\tests\test_contract_queue_redis_url.py backend\rbac_backend\tests\test_falkor_ro_query.py` | Passed: 53 passed, 1 skipped before local `reportlab` alignment. |
| `python -m pytest backend\rbac_backend\tests\test_contract_graph_retrieval.py backend\rbac_backend\tests\test_contract_appraisal.py backend\rbac_backend\tests\test_retrieval_quality.py backend\rbac_backend\tests\test_retrieval_engine_basics.py` | Passed: 42 passed, no skips after installing `reportlab==4.4.3` and adding graph retrieval tests. |
| `python -m compileall backend\rbac_backend\routers\contracts.py backend\rbac_backend\services\contracts_ingest.py backend\rbac_backend\retrieval\service.py backend\rbac_backend\services\contract_appraisal\service.py backend\rbac_backend\services\contract_appraisal\generator.py backend\rbac_backend\services\contract_graph_service.py` | Passed. |
| `npm test -- --run src/config/__tests__/routeInventory.test.ts src/config/__tests__/rolePermissions.sidebar.test.ts` | Passed: 8 tests. Required sandbox escalation for Node profile access. |
| `npx eslint src/pages/ContractsUploadPage.tsx src/pages/ContractsSearchPage.tsx src/pages/ContractQAPage.tsx src/pages/ContractAppraisalPage.tsx src/components/contract-appraisal/ClauseLibrary.tsx src/services/contracts-api.ts --max-warnings=0` | Passed. Required sandbox escalation for Node profile access. |

## Component Validation

### Upload Contract

Status: Validated with code review and existing tests.

Key implementation points:

- Backend routes: `backend/rbac_backend/routers/contracts.py`
  - `POST /api/contracts/upload-session`
  - `POST /api/contracts/upload-multipart`
  - `POST /api/contracts/upload-chunk`
  - `GET /api/contracts/status`
  - `GET /api/contracts/list`
  - `GET /api/contracts/{document_id}/download`
- Service: `backend/rbac_backend/services/contract_service.py`
- Queue worker: `backend/rbac_backend/services/contract_ingest_queue.py`
- Ingest job fallback: inline background task when queue is unavailable.

Validated behavior:

- Uploads are permission-gated with `dms.document.upload`.
- Project-scoped users must provide a project.
- Chunked uploads validate session ownership, filename, chunk count, file size, MIME, and optional antivirus result before merge.
- Contract document is created as `documents.uploadType = "contract"`.
- Contract aggregate/version records are attached.
- Processing status moves through queued/processing/completed/failed.

Gaps:

- No browser/e2e test uploads a real PDF/DOCX and waits for completed clause indexing.
- Upload queue fallback starts an inline task, which is useful locally but needs production monitoring so failed queue infrastructure is not silent.
- PDF export test for appraisals now runs locally after installing `reportlab==4.4.3`.

### Clause-Wise Extraction And Clause Library

Status: Implemented and partially validated.

Key implementation points:

- Ingestion service: `backend/rbac_backend/services/contracts_ingest.py`
- Clause library API: `GET /api/contracts/clauses` in `backend/rbac_backend/routers/contract_appraisal.py`
- Clause library UI: `client/src/components/contract-appraisal/ClauseLibrary.tsx`
- Storage collection: `document_vectors`

Validated behavior:

- Parser extracts PDF page spans with pdfminer and DOCX text with python-docx.
- Optional Marker converts PDFs to markdown and can provide heading offsets/table of contents.
- Optional LLM clause-span extraction preserves markdown hierarchy.
- Regex fallback extracts numbered clauses/articles/sections.
- Long clauses are split on paragraph/sentence boundaries.
- Clause metadata includes clause number, title, type, level, parent clause, start/end position, clause tags, page numbers, and optional `toc_path`.
- Clause library reads unique clause entries from `document_vectors`.

Gaps:

- Regex fallback is not enough for complex contracts with non-standard numbering, tables, schedules, appendices, and nested SCC/GCC modifications.
- Marker/LLM extraction is optional and environment-dependent; production should surface whether each upload used Marker+LLM or regex fallback.
- Clause library dedupes by document and clause number only, so repeated clause numbers in separate schedules can collapse unless `clause_start_position` or `clause_id` is included in the library key.

### Hierarchical Chunking

Status: Implemented at clause-metadata level, not yet fully exploited.

Validated behavior:

- Clause chunks carry:
  - `clause_level`
  - `parent_clause_number`
  - `toc_path`
  - `section_heading`
  - `clause_start_position`
  - `clause_end_position`
  - `is_complete_clause`
- Q&A expands a hit back to the full clause by fetching all chunks for the same document, clause number, and start position.

Gaps:

- No tree API exposes the clause hierarchy directly.
- Search UI groups chunks by clause but does not expose parent/child navigation.
- Falkor graph stores Document -> Section -> Clause but does not yet model parent-child clause relationships.

### Search Clauses

Status: Implemented and validated with backend tests plus frontend lint.

Key implementation points:

- Frontend: `client/src/pages/ContractsSearchPage.tsx`
- API client: `client/src/services/contracts-api.ts`
- Backend: `ContractService.search_contracts(...)`

Validated behavior:

- Query requires text and scope.
- Search filters by organization/project/document/tags.
- Structured filters support clause number, clause title, section heading, clause tags, page range, and exact phrase.
- Lexical search over Mongo groups results by clause.
- Optional Qdrant vector search adds semantic candidates.
- Lexical and vector candidates are fused with reciprocal rank fusion.
- Final reranker boosts query term hits, exact phrase, category terms, clause number, title, heading, and page filters.
- UI groups chunks into clause cards and preserves chunk order.

Gaps:

- No dedicated frontend tests for search filters, clause grouping, pagination, or highlighting.
- The backend hybrid path depends on Qdrant and embeddings being configured; otherwise it is lexical-only.
- Reranking is heuristic, not model-based cross-encoder reranking.

### Contract Q&A

Status: Implemented and validated with backend retrieval tests.

Key implementation points:

- Frontend: `client/src/pages/ContractQAPage.tsx`
- API: `POST /api/v1/retrieval/contract-qa`
- Service: `RetrievalService.contract_iterative_qa(...)`

Validated behavior:

- Q&A can target one completed contract or all completed contracts in a project.
- Uses `rag_fusion` by default from the UI.
- Retrieves contract evidence, expands full clauses, reranks by clause hints/legal keywords, builds citation map, and enforces citations.
- Iterative critique/refinement can run up to configured iterations.
- Prompt explicitly treats retrieved context as untrusted document text.

Gaps:

- No frontend/e2e tests verify answer rendering, citation display, timeout/cancel behavior, or trace view.
- Citation enforcement strips uncited sentences, but it does not independently verify that an LLM sentence is actually entailed by the cited clause.
- The UI timeout is fixed at 60 seconds; large all-contract queries may need server-side job mode or streaming.

### Contract Appraisal

Status: Implemented and backend-validated.

Key implementation points:

- Router: `backend/rbac_backend/routers/contract_appraisal.py`
- Service: `backend/rbac_backend/services/contract_appraisal/service.py`
- Generator: `backend/rbac_backend/services/contract_appraisal/generator.py`
- UI: `client/src/pages/ContractAppraisalPage.tsx`

Validated behavior:

- Appraisal generation creates jobs, prevents duplicate live reports for the same selection, supports cancellation, and writes versioned reports.
- Generator asks standard appraisal questions through contract Q&A.
- Sections, citations, confidence, risk rating, and structured output are persisted.
- Registers can be created from report structured output.
- Reports support edit, approve, reject, regenerate, comments, and DOCX/PDF export endpoints.

Gaps:

- Appraisal quality is bounded by retrieval quality; incomplete clause extraction produces incomplete appraisal.
- PDF export validation is skipped locally due missing `reportlab`.
- No browser/e2e tests cover generating an appraisal from an uploaded contract through the UI.

### Hybrid Search, Re-Ranking, And Knowledge Graphs

Status: Implemented with fallback behavior; graph use is currently ingest/write-focused.

Validated behavior:

- Retrieval engine supports `vanilla`, `hyde`, and `rag_fusion`.
- Backend chooses Qdrant when healthy, otherwise Mongo.
- `/contracts/search` fuses Mongo lexical candidates and Qdrant vector candidates.
- Q&A performs full-clause expansion and heuristic reranking.
- Contract ingestion writes FalkorDB graph nodes for Document, Section, and Clause when enabled.
- Clause search and Contract Q&A now use FalkorDB graph expansion as an additional candidate source; Contract Appraisal inherits this because it calls the Q&A engine.
- Existing Falkor tests validate read-only query protection.

Gaps:

- Search/Q&A now query FalkorDB for same-section and same-clause-number expansion. Deeper precedence-chain traversal still requires richer graph relationships.
- Contract graph lacks explicit clause parent/child, modifies/supersedes, governs, cross-reference, and schedule relationships.
- Re-ranking is heuristic rather than cross-encoder/LLM reranking.
- There is no graph reconciliation report for contract clause graph drift comparable to the newer evidence graph reconciliation work.

## Overall Findings

1. The pipeline is functionally present and internally coherent.
2. Clause-level extraction and retrieval are real, not placeholder-only.
3. Fallback behavior is pragmatic, but production should not treat regex/Mongo fallback as equivalent to Marker+LLM+Qdrant.
4. Contract Q&A and Appraisal are correctly grounded on retrieved citations, but not yet independently entailment-checked.
5. Knowledge graph ingestion exists, but graph retrieval is not yet a first-class part of clause search/Q&A/appraisal.

## Recommended Next Actions

1. Add a contract intelligence e2e fixture:
   - upload a small PDF/DOCX contract
   - wait for ingestion completion
   - assert clause records in `document_vectors`
   - run clause search
   - ask Q&A
   - generate appraisal
2. Add clause extraction golden tests for:
   - GCC/SCC numbering
   - schedule clauses with repeated numbers
   - nested subclauses
   - long clauses split into multiple chunks
   - table-heavy clauses
3. Add `clause_id` and `clause_start_position` to Clause Library dedupe/display.
4. Add contract graph relationships:
   - `PARENT_OF`
   - `MODIFIES`
   - `SUPERSEDES`
   - `CROSS_REFERENCES`
   - `GOVERNS`
5. Add graph-assisted retrieval:
   - expand a retrieved clause to parent/children
   - include SCC modifiers for matching GCC clauses
   - include cross-referenced clauses
6. Add production health checks that show, per upload:
   - parser used
   - Marker status
   - clause extraction mode
   - chunks written to Mongo
   - chunks written to Qdrant
   - graph nodes written to Falkor
7. Keep `reportlab==4.4.3` installed in the runtime/test image so PDF appraisal export remains covered by tests.
