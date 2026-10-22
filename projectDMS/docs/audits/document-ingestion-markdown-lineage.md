# Document Ingestion — Markdown Pathway and Data Lineage

Read-only audit, 2026-09-27, working tree on `integrate/key-date-eot` @ `a179ca9` (dirty). Paths relative to `backend/rbac_backend/`.
See also: [current state](document-ingestion-current-state.md) · [issues](document-ingestion-issues.md).

## 1. Is there a canonical Markdown representation? — No

| Required property | Correspondence (P1/P2) | Contracts (P3) |
|---|---|---|
| Markdown produced | only an **append-only LLM-report log**: `FileService.save_summary` appends `## Source: {filename}` + fields 1–27 to `uploads/{org}/{proj}/incoming.md \| outgoing.md \| projectid.md` (`services/file_service.py:17-73`; called from `services/document_processor.py:745-781`) | optional Marker CLI output `uploads/marker/{upload_id}/*.md` on local disk (`services/contracts_ingest.py:438-550`) |
| One file per document | no — one file per org/project/direction, shared | yes (per upload), if Marker binary present |
| DB reference to source | none | path + TOC + headings stored on the job-status `extra` only (`:1561-1576`) |
| Content hash / version | none | none |
| Regenerated on reprocess | appended again (duplicates) | overwritten |
| Removed on delete | no | no (inferred) |
| Stored durably (S3) | no — local disk of whichever container ran it | no — local disk |
| Manually editable | no | no |
| Indexed / used for RAG | no | **no** — `markdown_text = text` (`contracts_ingest.py:1548`) is never reassigned from `marker_result.markdown_text`, so chunking runs on cleaned OCR text |
| Preserves headings/tables/lists/clauses/page boundaries/footnotes/signatures/page provenance | no (LLM report fields only) | headings/TOC only |
| Docling | **dead**: no `docling`/`to_markdown` reference in the backend; `.env.example:108-112` states it is not deployed; CI only builds the image | — |

Verdicts: **CANONICAL MARKDOWN CREATED = NO** (a report log and an unused Marker side-file exist; neither is canonical). **MARKDOWN DURABLY STORED = NO** (local container disk, no DB pointer, no hash, not in S3).

## 2. What *is* the canonical text today

- Consumers call `publication_policy.authoritative_text`, which returns the first non-empty of `body`, `full_text`, `ocrText`, `summary` (`services/publication_policy.py:103, 171-185`).
- `documents.full_text` = `parsed_metadata.full_content` = the **LLM's re-typed "Full Content"** from the extraction report (`services/database_service.py:276-297`; `services/document_service.py:1840-1841`). When OCR fails on the legacy path, the PDF itself is sent to the LLM and its "Full Content" is what gets stored.
- Embeddings use `raw_ocr_text` first (`document_processor.py:764-765`), but deferred embeddings read `full_text` first (`database_service.py:256`).
- Result: **drafting/chronology/arbitration may quote an LLM paraphrase while retrieval cites OCR text**; the two can differ, and neither is revisioned or hashed. Reprocess overwrites in place.
- Page text exists durably only on the canary unified path (`document_ocr_pages.raw_text`, `tables`) and for contract PDFs (`contract_ocr_pages.raw_text`, `cleaned_text`). `document_ocr_pages` is write-only in production: `DocumentPageStore.publish_run` has no production caller, so `document_extraction_heads` is never written and nothing reads the pages back.

## 3. Original immutability

- Originals are written under a fresh `upload_id` key per upload (`services/storage_key_builder.py:37-89`) to every enabled provider (`services/file_object_service.py:253-310`), with a `document_versions` row per attach.
- OCR writes to `config.process_dir/<name>` (`services/ocr_service.py:123-157`), not to the stored key; no code path found that `$set`s `filepath_local`, `filepath_s3` or `storage_key` after OCR.
- Caveats: S3 `put_object` has no `If-None-Match` guard (`services/s3_service.py:74,95`) — safe only because document keys are unique; folder-upload keys are deterministic and check-then-write (LOW). `file_objects` silently dedupes on sha256+org+project+type, so two contract records can share one blob (by design, but undeclared). Partial provider failure is recorded, not retried.
- **ORIGINAL PRESERVED = YES** for the binary. The *derived text* however is not separated from the record: `ocrText`, `full_text`, summary and metadata live on the same `documents` document and are overwritten in place, and the LLM paraphrase takes precedence as authoritative text. That violates the spirit of "derived artifacts must not replace the original" at the text layer.

## 4. Lineage — what exists per derived value

| Derived value | document_id | page_no | span/offset | extractor/model version | timestamp | confidence | human-correction status |
|---|---|---|---|---|---|---|---|
| Original file | yes | — | — | — | yes | — | — |
| Page text (P2 `document_ocr_pages`) | yes | yes | geometry | run id + batch id; **no `engine_version`** although `PageExtractionResult.engine_version` exists (`services/extraction/engine.py:163`) | **no** | quality verdict/checks | `needs_review` flag |
| Page text (P1 legacy) | — | **none** | — | — | — | — | — |
| Page text (contracts) | yes | yes | — | no | no | no | no |
| `ocrText` / `full_text` | yes | no | no | `metadata_source` label only | `processed_at` | no | no |
| Metadata fields | yes | **no** | **no** | label only; `ai_extractions` has `schema_version`, `prompt_version`, hard-coded 0.75 | `processed_at` | **no** | `manual_summary_metadata_override` written, never read |
| Correspondence chunk (Qdrant + `document_vectors`) | yes | **no** | **no** | `embedding_model/provider/version`, `chunking_version`, `checksum_sha256` | `createdAt` (Mongo) | no | no |
| Contract clause payload | yes | `page_numbers`, `page_start/end` | `clause_start/end_position` (offsets into cleaned text) | `chunking_version=contract_clause_v1`, `ai_chunked`, `ai_confidence` | no | `ai_confidence` | no |
| `contract_clauses` record | yes | yes | — | — | `created_at` | `confidence` | `manually_edited` preserved on reprocess |
| P4 `chunks` | yes | `page_start/end` always `None` (`ingestion/chunker.py:32-33`) | no | model; content_hash overwritten with whole-doc hash (`ingestion/pipeline.py:314`) | yes | no | no |
| Falkor nodes/edges | owner_document_id on edges | Clause `page_number` (contracts) | — | no | no | no | reference `source` parser/manual/system |
| Chronology events | yes | `source_page` (lookup reads fields documents don't set) | `source_spans` | `ai_extractions` schema/prompt | yes | placeholder 0.72/0.45 | verify/reject + revisions |
| Evidence ledger | yes | `page_numbers` | — | — | `created_at` | scores | — |
| Search log `rag_runs` | chunk ids | — | — | — | yes | — | — |

**SOURCE-PAGE PROVENANCE = PARTIAL** — present for contract clauses and unified page records; absent for correspondence metadata and correspondence chunks on the production path, so a correspondence citation cannot point to a page.

## 5. AS-IS lineage chain (correspondence, production default)

```
Original file (S3/local, immutable key)
  -> [no page inventory]
  -> pdfplumber sidecar text  OR  ocrmypdf whole-document OCR  OR  (OCR failed) PDF sent to LLM
  -> raw_ocr_text ──────────────┐
  -> LLM numbered report ──> regex parse ──> ParsedDocumentMetadata ──> documents.{subject,letterNo,...}
                     └─> "Full Content" ──> documents.full_text  (== authoritative_text)
                     └─> appended to uploads/{org}/{proj}/incoming.md
  -> chunk(raw_ocr_text, 3000/200) ──> embed (LangChain) ──> Qdrant (payload nested under metadata.*)
                                   └─> embed again (LlamaIndex) ──> Mongo document_vectors
  -> resolved references ──> documents.references/referencedBy ──> Falkor (:Letter {normCode}) CITES
  -> UI: LetterSummaryPage cards (no source highlighting)
```

Missing links in the chain: page → text (no page records), text → metadata (no spans), metadata → confidence/version, chunk → page, Markdown → anything.

## 6. Target lineage (see [target architecture](document-ingestion-target-architecture.md))

```
original(sha256, immutable) -> page_inventory(page_no, class, native_chars, cid_ratio)
  -> page_text(version, source=text_layer|ocr, engine_version, quality)   [never overwritten; new version per run]
  -> canonical_text(run_id, hash) -> canonical_markdown(run_id, hash, S3 key, page anchors)
  -> metadata_fact(field, value, page_no, span, extractor, prompt_version, schema_version, confidence, review_state)
  -> chunk(run_id, page_start/end, char span, markdown block ids) -> embedding(model, version) -> qdrant point(uuid5)
  -> graph edge(owner_document_id, tenant, source fact id)
  -> UI/search result (citation = document_id + page + span)
```
