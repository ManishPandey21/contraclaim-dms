# Correspondence source authority — later backfill plan

**Status:** plan only. Nothing here has been run. No production data or Qdrant
collection was modified by the change that introduced it
(`fix/correspondence-source-authority`).

## What the code change already fixes without a backfill

- **Reads.** `publication_policy.authoritative_text` now calls
  `services/source_text.select_body_text`. The new precedence is: `body`, then
  source `ocrText`, then `full_text` (LLM Item 25 fallback), then `summary`.
  Stored rows are classified by shape when they carry no provenance marker. So
  a stale LLM `full_text` stops outranking valid OCR/native text as soon as the
  change deploys. The extraction report stored in `ocrText` (the no-source
  case) is never served as a body.
- **Vector writers B and C.** These are the duplicate-hold release
  (`DatabaseService.create_embeddings_for_document`) and `POST /ingestion/jobs`
  (`IngestionPipeline._extract_text`). Both now choose text with the same rule,
  so any **new** embedding of an old row uses the source text.
- **Evidence graph, duplicate fingerprint, strategy context.** All three read
  through the same rule.

## What still holds LLM text until a backfill runs

| Population | Identify (read-only) | Action |
|---|---|---|
| **R1**: source `ocrText` + stale LLM `full_text` | `ocrText` non-empty and not report-shaped (`source_text.is_extraction_report_text` false) and `full_text_source` absent | `$set full_text = ocrText, full_text_source = "source_text", ocr_text_kind = "source_text", source_text_status = "unverified"`. The old completeness is unknown, so it is never `complete`. Leave `metadata.full_content` in place, or `$unset` it. Its one remaining reader is the arbitration `_first(...)` relevance-note fallback (`arbitration_drafting/agents/deterministic.py`, `agents/llm.py`), used only when subject, summary and description are all absent. |
| **R2**: no-source rows (`ocrText` = report) with Item 25 in `full_text` | `ocrText` report-shaped | `$set ocr_text_kind = "extraction_report", full_text_source = "llm_full_content", source_text_status = "absent"`. Keep `full_text`: it is the only body. Queue these rows for unified_v1 re-extraction (Option C) where OCR is now available. |
| **R3**: no-source rows with no `full_text` | report-shaped `ocrText`, empty `full_text` | Label as R2. Drafting now receives `summary` for these rows, not the report. Queue them for re-extraction. |
| **V1**: Qdrant/`document_vectors` rows embedded from LLM `full_text` (writers B and C) | Documents in R1 whose `vector_sync_status` was written by a dup-release or an `/ingestion/jobs` run. If provenance is missing, treat every R1 document as suspect. | Reindex through the Phase B canonical writer after R1 is applied. `docs/audits/correspondence-qdrant-backfill-plan.md` is the carrier. |
| **E1**: evidence-graph `ai_extractions` and events hashed and classified from Item 25 | `ai_extractions.source_document_id` ∈ R1 | Recompute on the next reprocess. The content hash changes, so this creates a new extraction row instead of mutating the old one. |
| **D1**: duplicate-detection signals computed on paraphrases | pending `duplicate_status` rows ∈ R1 | Recompute the signals. Do not re-decide already-resolved duplicates automatically. |

## Order and gates

1. Run a read-only census on a production copy: counts for R1, R2, R3 and V1,
   plus a sample diff of `full_text` against `ocrText` (length, dates, amounts,
   reference codes). This is the paired comparison the usage audit could not
   perform.
2. Run the migration as a registered, idempotent `migrations/` entry. Execute
   `migrate_database --list` / `--fail-on-warning` inside the backend
   container, as `CLAUDE.md` requires. Dry-run it on staging first.
3. Reindex V1 only after R1 is applied, and only through the Phase B writer.
4. Verify after the run:
   - `authoritative_text` equals `ocrText` for every sampled R1 row;
   - no sampled vector text matches its row's old `full_text` where that
     differs from `ocrText`.
