# Contract Upload, Clause Search, Q&A, and Appraisal Audit

Date: 2026-06-30

Scope:
- Upload Contract workflow for the KNPCC-05 contract PDFs supplied by the user.
- Contract ingestion, clause extraction, `document_vectors`, hybrid search, reranking, graph retrieval, Contract Q&A, Clause Library, and Contract Appraisal.
- Validation was performed against local repo `C:\SaaS\projectDMS`, local MongoDB database `contraclaim`, frontend `http://127.0.0.1:5173`, and backend API where available.

## Executive Verdict

**Not production-ready for this workflow.**

The frontend routes and backend APIs are implemented and compile. The upload API accepted the supplied PDFs and created document/job records. However, the actual ingestion pipeline stalled before vector creation for the six uploaded files, leaving all new uploads stuck in `processing` with zero vector records. Existing completed KNPCC Vol-2 data proves that clause search and clause-library retrieval can read from `document_vectors`, but search relevance is noisy, graph retrieval is failing at runtime, and Contract Q&A returns unusable fallback output when LLM/embedding dependencies are unavailable.

Contract Appraisal depends on the same Contract Q&A engine, so it should not be treated as reliable until ingestion, retrieval, graph augmentation, and answer-generation quality are fixed.

## Files Uploaded During Validation

Organisation:
`c06c95b2-c6f3-4e79-998f-af4843524f7b`

Project:
`6d7c545c-e212-4985-90e5-5337623c60db`

Uploaded through:
`POST /api/contracts/upload-multipart`

Backend route:
[contracts.py](C:/SaaS/projectDMS/backend/rbac_backend/routers/contracts.py:285)

| File | Upload ID | Document ID | API Upload Result | Final Observed State |
|---|---:|---:|---|---|
| KNPCC-05 Vol-1 NIT ITT FOT.pdf | `6a43fa6b2da41a44a730df42` | `6a43fa6c2da41a44a730df46` | queued | stuck `processing`, stage `ocr`, 0 vectors |
| KNPCC-05 Vol-2 GCC and SCC.pdf | `6a43fa6e2da41a44a730df4b` | `6a43fa6e2da41a44a730df4e` | queued | stuck `processing`, stage `ocr`, 0 vectors |
| KNPCC-05 Vol-3 (Part-I) ER.pdf | `6a43fa712da41a44a730df53` | `6a43fa752da41a44a730df57` | queued | stuck `processing`, stage `ocr`, 0 vectors |
| KNPCC-05 Vol-4 Outline Design Specifications.pdf | `6a43fa802da41a44a730df5c` | `6a43fa842da41a44a730df60` | queued | stuck `processing`, stage `ocr`, 0 vectors |
| KNPCC-05 Vol-5 Outline Construction Specifications.pdf | `6a43fa8f2da41a44a730df65` | `6a43fa962da41a44a730df69` | queued | stuck `processing`, stage `ocr`, 0 vectors |
| UPMRC-CE-CONTRACT-KNPCC-05-2020-21-829-LOA OF GULEMARCK-SAM INDIA JOINT VENTURE.pdf | `6a43fac02da41a44a730df6e` | `6a43faf42da41a44a730df72` | queued | stuck `processing`, stage `parsing`, 0 vectors |

## Implementation Evidence

| Area | Evidence |
|---|---|
| Frontend upload route exists | [routes.tsx](C:/SaaS/projectDMS/client/src/routes.tsx:296), [ContractsUploadPage.tsx](C:/SaaS/projectDMS/client/src/pages/ContractsUploadPage.tsx:101) |
| Sidebar entries exist | [Sidebar.tsx](C:/SaaS/projectDMS/client/src/components/layout/Sidebar.tsx:196) |
| Upload API exists | [contracts.py](C:/SaaS/projectDMS/backend/rbac_backend/routers/contracts.py:285) |
| Contract search API exists | [contracts.py](C:/SaaS/projectDMS/backend/rbac_backend/routers/contracts.py:709) |
| Ingestion pipeline exists | [contracts_ingest.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contracts_ingest.py:950) |
| PDF extraction uses pdfminer | [contracts_ingest.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contracts_ingest.py:205) |
| Vector records are inserted into Mongo | [contracts_ingest.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contracts_ingest.py:869), [contracts_ingest.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contracts_ingest.py:1180) |
| Optional Qdrant indexing exists | [contracts_ingest.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contracts_ingest.py:1593) |
| Falkor graph sync exists | [contracts_ingest.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contracts_ingest.py:1161), [contract_graph_service.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contract_graph_service.py:188) |
| Contract search uses service/reranking | [contract_service.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contract_service.py:687), [contract_service.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contract_service.py:988) |
| Contract Q&A route exists | [retrieval_engine.py](C:/SaaS/projectDMS/backend/rbac_backend/routers/retrieval_engine.py:168), [service.py](C:/SaaS/projectDMS/backend/rbac_backend/retrieval/service.py:197) |
| Contract Appraisal route exists | [contract_appraisal.py](C:/SaaS/projectDMS/backend/rbac_backend/routers/contract_appraisal.py:56) |
| Clause Library route exists | [contract_appraisal.py](C:/SaaS/projectDMS/backend/rbac_backend/routers/contract_appraisal.py:403), [service.py](C:/SaaS/projectDMS/backend/rbac_backend/services/contract_appraisal/service.py:542) |
| Frontend Q&A/Appraisal integrations exist | [ContractQAPage.tsx](C:/SaaS/projectDMS/client/src/pages/ContractQAPage.tsx:255), [ContractAppraisalPage.tsx](C:/SaaS/projectDMS/client/src/pages/ContractAppraisalPage.tsx:259), [contracts-api.ts](C:/SaaS/projectDMS/client/src/services/contracts-api.ts:333) |

## Runtime Validation Results

### 1. Authentication and Scope

Result: **Pass**

Authenticated successfully as `superadmin@example.com` using `/api/login`. Existing project scope was found:
- Organisation: GULERMAK-SAM INDIA KANPUR METRO JOINT VENTURE
- Project: GUL-SAM JV KNPCC05

### 2. Upload Contract

Result: **Partial pass**

The upload endpoint accepted all six PDFs and created `documents`, `contract_versions`, `contracts`, and `contract_ingest_jobs` records. Files were stored under the local KNPCC05 contract upload path.

Failure:
- The jobs did not complete.
- Five jobs remained at `processing_stage=ocr`, `progress=20`.
- One job moved to `processing_stage=parsing`, `progress=40`.
- No `document_vectors` records were created for any of the six uploaded files.
- The backend became unresponsive during processing, and `/health` and `/contracts/status` timed out before the process exited.

Likely cause:
- Ingestion is triggered inline when the queue is unavailable.
- PDF parsing is CPU-heavy and uses synchronous `pdfminer.extract_pages()` inside the async ingestion path.
- Multiple inline ingestion tasks can starve the event loop and make the API unavailable.

### 3. Vector Store

Result: **Fail for newly uploaded documents; partial pass for older completed data**

New upload validation:
- `document_vectors.count_documents({"upload_id": <new upload id>}) == 0` for all six uploaded files.

Existing KNPCC data:
- One earlier completed `KNPCC-05 Vol-2 GCC and SCC.pdf` document exists:
  - Document ID: `6a3d85a1514f2f89844a299d`
  - Upload ID: `6a3d859a514f2f89844a2999`
  - Vector count: `1303`
- This confirms the vector storage schema is used by the app, but not that the current upload pipeline is reliable.

### 4. Clause-Wise Extraction / Clause Library

Result: **Partial pass**

Clause Library returned rows from existing KNPCC Vol-2 vector records. Returned records include `document_id`, `document_name`, `clause_number`, `clause_title`, `page_numbers`, and snippet text.

Issue:
- Clause quality is noisy. A search for `extension of time` returned clause numbers such as `149`, `16.9`, `2.2`, `20`, and `21` before the expected EOT clause.
- The extractor often labels page numbers or table-of-contents/page content as clauses, for example `Clause 113`, instead of reliably extracting GCC/SCC clause hierarchy.

### 5. Search Clauses

Result: **Partial pass**

Direct `ContractService.search_contracts()` against existing completed KNPCC Vol-2 data returned results and sources for query:

`extension of time GCC 8.4`

Observed:
- `total_count=25`
- Results included page-grounded snippets and source metadata.
- Top results included related EOT content, but the first result was `Clause 113`, and `Clause 8.5` ranked above direct GCC 8.4 text.

Runtime warnings:
- Embedding call failed and fell back to deterministic embedding.
- Qdrant search failed and fell back to in-memory/local behavior.
- Falkor graph lookup failed with: `Type mismatch: expected List or Null but was String`.

Conclusion:
- Search is connected and returns data.
- Hybrid/vector/graph retrieval is not reliable enough for legal-grade search.

### 6. Contract Q&A

Result: **Fail**

Direct `RetrievalService.contract_iterative_qa()` was run against existing completed KNPCC Vol-2 data with:

`What is the notice requirement for extension of time under GCC 8.4?`

Observed answer:

`Every sentence MUST include at least one citation token like [Clause 4.3>4.3].`

This is not an answer to the legal/contract question. It appears the offline fallback generator returned prompt/guardrail text. Citations were also irrelevant: top citations were clauses `4.3`, `3.5`, `4.1`, etc., not GCC 8.4.

Runtime warnings:
- Embedding calls failed.
- LLM generation failed and used fallback.
- Falkor graph retrieval failed with parameter type mismatch.

Conclusion:
- Contract Q&A is wired but not functionally valid under the tested local dependency state.
- The fallback behavior is unsafe for production because it can return prompt instructions as final user-facing content.

### 7. Contract Appraisal

Result: **Blocked / not reliable**

No existing appraisal jobs or reports were present for the KNPCC-05 project. Appraisal generation is implemented and wired, but it depends on the same Contract Q&A engine that failed the direct validation above.

Because Q&A retrieval/generation returned irrelevant citations and prompt text, generating a new appraisal would not produce a reliable report. The page should be considered connected but not production-valid until the underlying Q&A and ingestion issues are fixed.

## Test and Build Validation

Commands run:

```powershell
python -m pytest backend/rbac_backend/tests/test_contract_upload_security.py backend/rbac_backend/tests/test_contract_clause_extraction.py backend/rbac_backend/tests/test_contract_graph_retrieval.py backend/rbac_backend/tests/test_contract_appraisal.py backend/rbac_backend/tests/test_search_authz.py -q
```

Result:
- `46 passed`
- Warnings include Pydantic deprecations and Qdrant insecure connection warning.

```powershell
npx eslint src/pages/ContractsUploadPage.tsx src/pages/ContractsSearchPage.tsx src/pages/ContractQAPage.tsx src/pages/ContractAppraisalPage.tsx src/components/contract-appraisal/ClauseLibrary.tsx src/components/contract-appraisal/AppraisalRegisters.tsx src/services/contracts-api.ts
```

Result:
- Passed.

```powershell
npm run build
```

Result:
- Passed.
- Existing warnings:
  - Browserslist `caniuse-lite` is 20 months old.
  - Vite mixed static/dynamic import warning for `client/src/services/auth.ts`.

## Critical Findings

### P0 - Contract ingestion can leave uploads stuck and make the backend unavailable

Fresh uploads stayed in `processing` and no vectors were produced. The backend became unresponsive while processing and later exited. This blocks upload, status polling, search, Q&A, and appraisal.

Required fix:
- Move all parsing/OCR/vector work to a real worker process.
- Never run contract ingestion inline in the API server for production-sized PDFs.
- Add job heartbeat, timeout, stale-job recovery, and failure state transitions.
- Run PDF parsing and OCR off the event loop with bounded process/thread pools.

### P0 - Contract Q&A returns unsafe fallback output

The fallback generator returned prompt/guardrail text instead of a grounded answer. This is unacceptable for legal drafting or contract advice.

Required fix:
- If LLM generation is unavailable, return a controlled error or "answer unavailable" response.
- Never expose prompts, system instructions, or guardrail text as answers.
- Add regression tests for offline/no-LLM behavior.

### P0 - New uploads do not become searchable

All six uploaded documents had zero `document_vectors` rows after validation. Contract search, Q&A, and appraisal depend on vector records.

Required fix:
- Make ingestion completion a release gate.
- Add integration tests that upload a real PDF fixture and assert document status `completed`, vector count `>0`, clause metadata populated, and status endpoint remains responsive.

### P1 - Clause extraction is too noisy for legal use

The extractor often treats pages/table text as clauses. Search for EOT surfaced `Clause 113` and other unrelated page-derived entries.

Required fix:
- Improve clause boundary detection for GCC/SCC formatted contracts.
- Prefer table of contents + section heading anchors where available.
- Add contract-specific parser tests using KNPCC-like page headers/footers.
- Strip repeated headers/footers before clause extraction.

### P1 - Falkor graph retrieval is wired but failing

Graph lookup failed with `Type mismatch: expected List or Null but was String`. This means graph augmentation is not currently dependable.

Required fix:
- Fix parameter serialization into FalkorDB so list parameters remain lists.
- Add integration tests for `find_related_clauses()` using real seed lists.
- Ensure graph retrieval failure is visible in observability, not only debug/warning logs.

### P1 - Qdrant / embedding path is not healthy

Search and Q&A fell back due embedding and Qdrant failures.

Required fix:
- Validate OpenAI/embedding provider configuration at startup or health-check time.
- Validate Qdrant collection availability and client method compatibility.
- Surface degraded mode in UI and health page.

### P2 - Appraisal quality depends on unreliable Q&A

The appraisal page and APIs exist, but generated reports would inherit the Q&A retrieval and generation failures.

Required fix:
- Block appraisal generation when selected documents are not completed or when retrieval is in degraded mode.
- Require minimum citation relevance/coverage before report creation.
- Add "requires human review" sections when confidence is low, not prompt fallback text.

## Recommended Remediation Plan

1. Stabilize ingestion:
   - Disable inline contract ingestion fallback in API process for large files.
   - Require a dedicated worker and queue for contract ingestion.
   - Add stale-job timeout and recovery.
   - Add responsive status polling under load.

2. Fix vector creation:
   - Ensure uploaded contract PDFs always produce Mongo `document_vectors` rows even if external embedding is unavailable.
   - Store `embedding_dims=0` fallback records only with explicit degraded status.
   - Verify `document_id`, `upload_id`, `organization_id`, `project_id`, `uploadType`, `document_type`, clause/page metadata.

3. Fix retrieval dependencies:
   - Validate embedding provider configuration.
   - Fix Qdrant client compatibility.
   - Fix FalkorDB list parameter serialization.
   - Add graph retrieval tests using real FalkorDB or a strict fake that catches type mismatches.

4. Improve clause extraction:
   - Add PDF header/footer cleanup.
   - Add GCC/SCC-specific clause pattern tests.
   - Avoid treating page numbers as clause numbers.
   - Preserve hierarchy for `GCC 8.4`, `SCC 8.4`, subclauses, and amendments.

5. Harden Q&A:
   - Replace prompt-text fallback with safe unavailable response.
   - Require answer citations to match relevant retrieved clauses.
   - Add tests for missing LLM, missing embeddings, missing vectors, and irrelevant retrieval.

6. Harden appraisal:
   - Gate generation on completed documents and healthy retrieval.
   - Add report quality checks before saving.
   - Add audit trail and clear degraded-mode warnings.

7. Add E2E release tests:
   - Upload real small contract PDF fixture.
   - Poll status to completion.
   - Assert vector count.
   - Search for a known clause.
   - Ask Q&A for a known clause and verify relevant citation.
   - Generate appraisal and verify report sections/citations/registers.

## Final Status

| Capability | Status |
|---|---|
| Frontend routes/menu compile | Pass |
| Backend APIs exist | Pass |
| Auth/RBAC route gating | Pass based on implementation/tests |
| Upload endpoint accepts PDFs | Pass |
| Current upload ingestion completion | Fail |
| New vector creation | Fail |
| Clause library reads existing vectors | Partial pass |
| Clause extraction quality | Partial/fail |
| Hybrid search | Partial pass, degraded |
| Reranking | Present, but quality weak |
| Falkor graph retrieval | Fail at runtime |
| Contract Q&A | Fail |
| Contract appraisal | Blocked by Q&A/retrieval |

Overall readiness score for this workflow: **45 / 100**.

## Remediation Update - 2026-07-01

Implemented code remediations for the main ingestion/retrieval defects identified above:

- Contract PDF parsing now runs off the async event loop.
- Contract ingestion no longer runs the legacy whole-document OCR preprocessing step before indexing.
- PDF OCR is page/batch aware using configurable OCRmyPDF `--pages` ranges.
- OCR status is tracked in MongoDB at batch and page level through `contract_ocr_batches` and `contract_ocr_pages`.
- Raw OCR/text-layer page text is retained for audit/debugging; cleaned page text is used for clause chunking and embeddings.
- Repeated margin headers/footers, page numbers, and known boilerplate/watermark lines are removed before chunking.
- Clause extraction was tightened to avoid standalone page numbers and to recognize GCC/SCC/Sub-Clause headings.
- Optional AI-assisted clause chunking now validates span bounds, overlap, and coverage before replacing deterministic chunking.
- Contract vector metadata now includes `contract_id`, `page_start`, `page_end`, `clause_no`, `section_title`/`section_heading`, `chunk_type`, `ai_chunked`, `ai_confidence`, and `source_pdf_page_link`.
- Added `POST /api/contracts/{document_id}/ocr/retry` to retry failed or selected OCR pages under PolicyService authorization.
- FalkorDB parameter serialization now preserves list/dict parameter types for `IN` queries.
- Contract Q&A fallback now returns a controlled unavailable message instead of prompt/guardrail text.
- Regression tests added for preprocessing, clause metadata, page-number false positives, AI span fallback, Falkor list params, and LLM fallback behavior.

Validation:

```powershell
python -m pytest backend/rbac_backend/tests/test_contract_clause_extraction.py backend/rbac_backend/tests/test_falkor_ro_query.py backend/rbac_backend/tests/test_llm_generator_fallback.py backend/rbac_backend/tests/test_contract_graph_retrieval.py backend/rbac_backend/tests/test_contract_appraisal.py backend/rbac_backend/tests/test_contract_upload_security.py backend/rbac_backend/tests/test_search_authz.py -q
```

Result: `54 passed`.

Remaining operational validation:

- Re-run the KNPCC upload flow with a live worker, OCRmyPDF/Tesseract, MongoDB, embedding provider, Qdrant, and FalkorDB available.
- Confirm the six previously stuck documents can be retried/reprocessed to `completed`.
- Confirm vector counts are created from cleaned text and Contract Search/Q&A/Appraisal return relevant citations from the reprocessed documents.
