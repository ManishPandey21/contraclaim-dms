# The extraction run contract

Scope: unified document extraction (`pipeline_version=unified_v1`) and the
page evidence it writes to `document_ocr_pages`.

## The unit is the run, not the attempt

A document-processing job carries one `extraction_run_id` (the job id) for its
whole life, including every retry. That id is the identity of **one cumulative
extraction run**:

* A page resolved by attempt *n* stays resolved. Attempt *n+1* may change it
  only if that exact page number is in `retry_pages`
  (`document_processing_jobs.remaining_page_numbers`).
* The `PageExtractionResult` handed downstream after a retry represents the
  whole run, not the attempt: `combined_text` is assembled in page-number
  order from the authoritative page state, and that text is what reaches
  `documents.full_text` and the embedding seam.
* One row per `(document_id, extraction_run_id, page_number)`. A retry
  replaces only the rows it re-extracted. Nothing is deleted and reinserted,
  so a crash mid-attempt leaves the run reconstructable.

### Why this is written down

Before 2026-09-22 a retry re-read every page of the PDF and upserted all of
them. A scanned page that attempt 1 had recovered by OCR was overwritten on
attempt 2 with the empty native text the retry had just re-read for it
(`status=text_layer`, `source=empty`), disappeared from `full_text` and from
the indexed text, and the job then reported `completed` - the house
silent-success pattern, with customer text as the casualty. Regression:
`backend/rbac_backend/tests/test_extraction_retry_page_preservation.py`.

## How it is enforced

| Component | Rule |
|---|---|
| `PageExtractionEngine.extract` | With `retry_pages` and a resumable store, records only the retried pages, then rebuilds the full result from the run. |
| `ResumablePageStore` (`services/extraction/page_store.py`) | Optional capability. `DocumentPageStore.load_run_pages()` implements it; `ContractPageStore`, `NullPageStore` and the image/text extractors do not and keep their previous behaviour. |
| `from_document_page_record` | Rehydrates status, source, batch, error, verdict, checks, review flag, original text, repairs and tables. An OCR page never comes back as native text. |
| `InconsistentExtractionRunError` | A page the checkpoint calls resolved whose row is missing fails the attempt visibly (page numbers only, no document text) rather than being regenerated as empty native text. |
| `ExtractedPage.carried_forward` | Attempt-scoped, never persisted. Carried pages are not reassessed by the quality gate (no duplicate fallback spend, no overwritten verdicts), are not re-written by `finalize_pages`, and are not charged a retry allowance - but their `needs_review` still counts toward the publication barrier. |

## Known adjacent gap (not addressed here)

The contract ingest path (`POST /api/contracts/{id}/ocr-retry` →
`contracts_ingest._extract_pages_with_engine`) passes `retry_ocr_pages` to the
same engine with `ContractPageStore`, which has no run-scoped read. A contract
OCR retry therefore still rebuilds the parsed document from the latest attempt
alone, so text recovered by an earlier attempt can be lost the same way.
Deliberately out of scope for this change; tracked separately.

## Graph note

`graphify-out/` in this checkout holds reports from 2026-07-14 and no
`graph.json`, so an incremental `--update` is not possible and a full rebuild
(369 files, ~1.4M words) was not run for this change. This document is the
scoped, honest substitute: the runtime contract that changed, written where the
next reader of the extraction code will find it.
