# Canonical document evidence

Scope: general documents (letters and other non-contract uploads) extracted by
the unified pipeline (`pipeline_version=unified_v1`). Companion to
[EXTRACTION_RUN_CONTRACT.md](EXTRACTION_RUN_CONTRACT.md).

## The rule

Chunks and embeddings are retrieval aids. They never replace the document.
Every extracted document keeps its complete canonical text, in page order,
with page provenance and review state. That evidence is persisted **before**
metadata extraction, chunking, embeddings or vector sync run. It can be read
without Qdrant, without an embedding provider and without an LLM.

```
original file (S3, documents.sha256)
  -> page evidence          document_ocr_pages      (one row per run x page)
  -> canonical manifest     document_extraction_heads (one row per document)
  -> metadata / chunks / embeddings / vectors       (derived, may fail)
```

## Storage model: derive from page rows, verify against a manifest

The page rows are the source of truth. Nothing duplicates their text.

| Representation | Where | Field |
|---|---|---|
| Original extraction (pre-repair, or withheld `(cid:N)` text) | `document_ocr_pages` | `original_text` (None when equal to the canonical page text) |
| Canonical page text | `document_ocr_pages` | `raw_text`. The name is historical: it is the *published* text |
| Applied deterministic repairs | `document_ocr_pages` | `applied_repairs` |
| Method / status / verdict / review / error / tables | `document_ocr_pages` | `source`, `status`, `page_class`, `quality_verdict`, `quality_checks`, `needs_review`, `error`, `tables` |
| Which run is canonical, and its integrity | `document_extraction_heads` | `extraction_run_id`, `canonical_sha256`, `canonical_char_count`, `page_count`, `page_numbers`, `page_map`, `review_pages`, `unresolved_pages`, `withheld_pages`, `build_status`, `canonical_revision`, `pipeline_version`, `engine_version`, `source_sha256`, `canonical_format_version`, `built_at` |

`page_map` holds, for each page: its `[start, end)` character span in the
canonical text, a per-page `content_sha256`, status, source, verdict, review
flag and repair/table counts. It holds no page text, so the head stays a few KB
for a typical letter and never approaches the 16 MB document limit.

**Assembly** (`services/extraction/canonical.py`, the only builder). Pages are
sorted by page number. A duplicate page number raises. Each page's canonical
text is used verbatim, and pages are joined by `"\n\n"`. This is byte-identical
to the `combined_text` that has always been written to `documents.ocrText`.
The engine and the processor both call this builder.

**Publish** (`DocumentPageStore.publish_canonical`). This is called from
`DocumentProcessor._apply_quality_gate` after `finalize_pages`, before any
indexing step. It reads the run's rows back, reassembles them, and refuses
(`CanonicalEvidenceInconsistentError`) if a page is missing or duplicated, or
if the rows do not hash to the text the pipeline is about to persist. Only
then does one `update_one` move the head and write its manifest. A failed
publish fails the attempt. No metadata, chunks or vectors are written from
unvouched text.

**Read** (`services/canonical_evidence_service.get_document_canonical_evidence`)
takes `(db, document_id, current_user, selection=None)`. Authorisation is the
same as `GET /documents/{id}`: `ActiveScope.require_record`, then
`PolicyService.authorize_document(dms.document.view)`. It then:

- reassembles the text from the head's run rows;
- verifies the scope and checksum against the manifest;
- returns the text, per-page evidence and manifest.

A row changed after publication, rows under another tenant, or a missing page
raises. Nothing is served. There is no HTTP endpoint, and no unscoped variant.

## Behaviour that is pinned (`tests/test_canonical_evidence.py`)

- Review page: its text stays in the canonical text, and `review_pages` names
  it. Whole-document embedding withholding is unchanged.
- Failed, deferred or withheld page: the page keeps a zero-length span with
  its status. `build_status=partial` and `unresolved_pages`/`withheld_pages`
  name it. Reliability (review) is reported apart from completeness.
- Retry of one page (same run): rows are upserted per page, the head is
  republished, and `canonical_revision` is incremented. No page appears
  twice.
- Reprocess (new job, new run): the head moves to the new run. The old run's
  rows stay as history.
- Embedding provider down, Qdrant down, or no LLM key: the canonical evidence
  is still published and readable.

## Known limitations

- **Head and rows are two writes, not a transaction.** Consistency comes from
  verify-on-publish plus verify-on-read. A crash between a row rewrite and the
  head write is detected on read as `CanonicalEvidenceInconsistentError`, and
  the next attempt republishes. It is never served as stale text.
- **Legacy pipeline (`legacy_v0`) produces no page evidence.** That is every
  production document today, since `UNIFIED_EXTRACTION_ENABLED` is off. Such a
  document reads as `status=not_built`, with its `ocrText` offered as
  `legacy_text` plus its `ocr_text_kind` label. It is never presented as
  canonical.
- **Printed page numbers are not extracted.** Only PDF page numbers are
  recorded.
- **Contracts keep their own store.** Contracts use `contract_ocr_pages` and
  the clause-first projection. Unifying them with this layer is a separate
  change.
- **Enclosures are not extracted.** Files attached through
  `POST /documents/{id}/enclosures` are stored in `documents.enclosures` but
  are never processed. An enclosure uploaded as its own document uses this
  same layer.

## Backward compatibility

There is no migration. The new fields live in the existing
`document_extraction_heads` collection, which migration `20260814_0001`
already indexes uniquely on `document_id`. Before this change, nothing ever
wrote to that collection.

For existing documents, the recommended path is an explicit, owner-approved
re-extraction through the unified pipeline. It writes a new run alongside the
old one and rewrites no existing evidence. No backfill was run.

## Acceptance criteria for later work (not implemented here)

- **Matter Context Pack.** It loads the complete text of a target letter, the
  letters it references, and relevant enclosures by `document_id`, through
  `get_document_canonical_evidence`. It never assembles top-k vector chunks.
- **Automated chronology.** An event cites `document_id`, `page_number` and a
  canonical character span (`page_text` / `page_at`), plus the
  `canonical_sha256` / `canonical_revision` it was extracted from. A later
  revision therefore invalidates the citation visibly. Paragraph or block
  offsets can be added inside a page span without changing this contract.
