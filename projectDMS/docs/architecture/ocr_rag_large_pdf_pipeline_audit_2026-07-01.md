# OCR/RAG Large PDF Pipeline Audit

Date: 2026-07-01

Scope:
- Contract OCR/RAG pipeline for large PDFs.
- Verification against `KNPCC-05 Vol-2 GCC and SCC.pdf`.
- Code paths covering contract upload ingestion, OCR page/batch processing, text cleaning, clause chunking, vector payload generation, and API metadata exposure.

## Executive Verdict

**Pass with operational validation pending.**

The repository now implements the required page-aware OCR/RAG behavior for contract uploads:

- OCR is no longer run as one whole-document preprocessing step for contracts.
- PDFs are inspected page-wise; pages with sufficient text layer are stored as text-layer pages.
- Low-text/scanned pages are grouped into configurable batches and processed with OCRmyPDF `--pages`.
- Raw page text and cleaned page text are stored separately in MongoDB.
- Cleaned page text is used for clause extraction, contract-aware chunking, Mongo vector records, and Qdrant embeddings.
- Chunk metadata includes page and clause grounding fields required for search, Q&A, appraisal, chronology, SoC, SoD, and rejoinder workflows.

Full production validation still requires running an upload through the live worker stack with MongoDB, OCRmyPDF/Tesseract, embeddings, Qdrant, and FalkorDB enabled.

## Evidence Summary

| Requirement | Status | Evidence |
|---|---|---|
| Page-wise/batch-wise OCR for large PDFs | Pass | `CONTRACT_OCR_BATCH_SIZE` setting; `_extract_pdf_pages_with_ocr_batches`; `_run_ocr_page_batch`; OCRmyPDF command includes `--pages`. |
| Avoid whole-document contract OCR | Pass | `ContractService.process_ingest_job` sends original processing path to `ingest_contract`; no contract call to `OCRService.process_document`. |
| Raw OCR text stored separately | Pass | `contract_ocr_pages.raw_text` and `raw_text_length` are written per page. |
| Cleaned page text stored separately | Pass | `contract_ocr_pages.cleaned_text`, `cleaned_text_length`, and `cleaning` audit metadata are written per page. |
| Headers/footers/page numbers removed before chunking | Pass | `ContractTextPreprocessor.clean_document()` is called before categorization, clause extraction, payload building, and embeddings. |
| Contract-aware clause chunking | Pass | `ClauseExtractor` recognizes explicit `CLAUSE`, `SECTION`, `ARTICLE`, `GCC`, `SCC`, `PCC`, and `Sub-Clause` headings and avoids standalone page-number false positives. |
| AI-assisted clause chunking safeguard | Pass | AI spans are validated for bounds, overlap, and source coverage; deterministic fallback is used below confidence threshold. |
| Embeddings from cleaned chunks only | Pass | `_index_clause_vectors()` embeds `payloads[].text`, and payloads are built from cleaned `ParsedDocument` text. Raw OCR text is not embedded. |
| Page/clause metadata on chunks | Pass | Metadata includes `document_id`, `contract_id`, `page_start`, `page_end`, `clause_no`, `section_title`, `chunk_type`, `ai_chunked`, `ai_confidence`, and `source_pdf_page_link`. |
| Retry failed OCR pages | Pass | `POST /api/contracts/{document_id}/ocr/retry` queues selected or failed OCR pages with PolicyService authorization. |

## KNPCC Vol-2 PDF Inspection

File:
`C:\Users\santo\Downloads\Contracts\KNPCC-05\KNPCC-05 Vol-2 GCC and SCC.pdf`

Observed:

- Size: `1,211,902` bytes.
- Page count: `208`.
- OCR/runtime libraries present locally:
  - `ocrmypdf_available=True`
  - `pdfplumber_available=True`
  - `PyPDF2_available=True`
- Text-layer sampling:
  - First 10 pages had extractable text.
  - Full PDF text-layer scan found minimum page text length of `83` characters.
  - With the configured threshold of `40` characters, `low_text_count=0`.

Conclusion for this specific PDF:

- The contract ingestion pipeline should not invoke OCRmyPDF for this file because every page already has enough text-layer content.
- The pipeline should still store page text in `contract_ocr_pages.raw_text`, clean it into `cleaned_text`, and build vectors only from cleaned clause chunks.

## Code Evidence

Key files:

- `backend/rbac_backend/core/config.py`
  - `CONTRACT_OCR_BATCH_SIZE`
  - `CONTRACT_OCR_MIN_TEXT_CHARS_PER_PAGE`
  - `CONTRACT_TEXT_CLEANING_ENABLED`
  - `CONTRACT_AI_CHUNKING_ENABLED`
  - `CONTRACT_AI_CHUNKING_MIN_CONFIDENCE`

- `backend/rbac_backend/services/contract_service.py`
  - Contract ingestion now starts page/batch OCR and contract ingestion directly.
  - The previous whole-document `OCRService.process_document()` contract preprocessing call is removed.

- `backend/rbac_backend/services/contracts_ingest.py`
  - `DocumentParser._extract_pdf_text()` runs sync pdfminer extraction in an executor.
  - `ContractTextPreprocessor` removes repeated margin text and page numbers.
  - `DatabaseService._create_ocr_status_indexes()` creates `contract_ocr_batches` and `contract_ocr_pages` indexes.
  - `_extract_pdf_pages_with_ocr_batches()` decides which pages need OCR.
  - `_run_ocr_page_batch()` invokes OCRmyPDF with `--pages`.
  - `_build_clause_payloads()` builds cleaned contract chunks and metadata.
  - `_index_clause_vectors()` embeds only chunk text from cleaned payloads.

- `backend/rbac_backend/models/contract_models.py`
  - Status response exposes OCR page/batch counters.
  - Contract clause search response exposes page/clause/chunk metadata.

- `backend/rbac_backend/routers/contracts.py`
  - `POST /api/contracts/{document_id}/ocr/retry` supports retrying failed/selected OCR pages.

## Validation Run

Command:

```powershell
python -m pytest backend/rbac_backend/tests/test_contract_clause_extraction.py backend/rbac_backend/tests/test_falkor_ro_query.py backend/rbac_backend/tests/test_llm_generator_fallback.py -q
```

Result:

```text
11 passed
```

Test coverage includes:

- Repeated header/footer removal while preserving clause headings.
- Avoiding page-number false positives as clauses.
- New vector metadata fields such as `source_pdf_page_link`.
- AI chunk span fallback when confidence/coverage is too low.
- Falkor list parameter serialization.
- Safe LLM fallback behavior.

## Observations

1. The contract pipeline meets the requested architecture at code level.
2. The attached KNPCC Vol-2 file is not a scanned/low-text PDF, so it is a good test of page-wise text-layer storage and cleaning, not OCR batch execution.
3. `backend/rbac_backend/services/ocr_service.py` still contains whole-document OCR for non-contract document-processing paths. This is not used by the contract ingestion path audited here.
4. Full end-to-end verification still requires a live ingestion worker and database-backed upload run. This audit verified code paths, tests, local OCR dependencies, and the attached PDF text-layer characteristics.

## Recommended Follow-Up

1. Run a live upload of KNPCC Vol-2 and verify:
   - `contract_ocr_pages.count_documents({"document_id": ...}) == 208`
   - `raw_text` and `cleaned_text` are both populated.
   - `document_vectors` rows contain `text_source="cleaned_contract_text"`.
   - No vector row contains raw page OCR boilerplate.

2. Run one scanned/low-text PDF fixture to verify actual OCRmyPDF `--pages` execution and failed-page retry behavior.

3. Add an integration test with a synthetic mixed PDF:
   - Some text-layer pages.
   - Some image-only pages.
   - Repeated headers/footers.
   - Known clause headings.

4. Add production observability counters for:
   - OCR pages skipped due text layer.
   - OCR pages processed.
   - OCR pages failed.
   - cleaned/raw text length ratio.
   - vector chunks created per document.
