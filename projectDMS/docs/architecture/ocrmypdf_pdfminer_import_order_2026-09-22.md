# OCRmyPDF / pdfminer import order: a fixture defect and a production defect

Date: 2026-09-22. Base: `release/contraclaim-rc1` @ `99ab522`.
Branch: `fix/ocrmypdf-pdfminer-import-order`.

## Symptom

Three extraction tests failed only when `ocrmypdf` had been imported earlier in
the same Python process:

- `test_extraction_engine.py::test_combined_text_is_page_ordered`
- `test_extraction_ocrmypdf_runner.py::test_full_length_output_reads_absolute_pages`
- `test_extraction_ocrmypdf_runner.py::test_trimmed_output_reads_positional_pages`

Before the fix, `python -c "import ocrmypdf, pytest; pytest.main([...both files...])"`
gave 3 failed / 24 passed. Without the import, 27 passed.

## Mechanism

We read the installed OCRmyPDF 16.10.4 source (`ocrmypdf/pdfinfo/layout.py`).
Importing `ocrmypdf` runs `ocrmypdf/__init__.py`, which imports `pdfinfo` and
then `pdfinfo.layout`. That module makes two changes that stay in effect for the
rest of the process:

| Line | Mutation | Effect on text |
|---|---|---|
| 61 | `PDFSimpleFont.__init__` is replaced. After the original constructor runs, `if not self.unicode_map and 'Encoding' not in spec: self.cid2unicode = {}` | This is the whole cause. A simple font with no `/Encoding` and no `/ToUnicode` loses its Unicode map, so every glyph extracts as `(cid:N)`. |
| 66 | `PSBaseParser.BUFSIZ = 256 * 1024 * 1024` | None: the extracted text is byte-identical. The cost is performance, measured below. |

The Type3 font patches at lines 282-286 are applied through `patch.multiple`
inside a context manager, so they do not leak into the rest of the process.

We tested each mutation on its own, in a separate process each time, using
`build_mixed_pdf` page 3:

| Case | Extracted text |
|---|---|
| A, clean process | `Claim summary page 3 …` |
| B, after `import ocrmypdf` | `(cid:67)(cid:108)(cid:97)…` |
| C, only the `PDFSimpleFont` patch | `(cid:67)(cid:108)(cid:97)…` |
| D, only the `BUFSIZ` change | `Claim summary page 3 …` |

Cost of the `BUFSIZ` change for one 9-page extraction:

- On Windows (pdfminer 20250506): time rises from 263 ms to 6576 ms, and the
  `tracemalloc` peak rises from 3.5 MiB to 259 MiB.
- On Linux with the production pins (pdfminer 20260107, pdfplumber 0.11.10):
  time barely changes (574 ms, then 434 ms), but the `tracemalloc` peak is still
  258 MiB.

We measured `tracemalloc` peak only. Resident memory (RSS) was not measured.

## The fixture defect

The fixture fonts were Type1 Helvetica with no `/Encoding` and no `/ToUnicode`.

That is valid PDF: a Standard-14 font can rely on its built-in encoding.
However, PDF 32000-1 section 9.10.2 derives Unicode only from a predefined
encoding or a ToUnicode CMap. pdfminer fills the gap by guessing
StandardEncoding, and OCRmyPDF's patch disables that guess.

The fix adds `/Encoding /WinAnsiEncoding` to the fixture fonts. It is the
smallest standards-correct change, because only three encoding names are
allowed there (MacRoman, MacExpert and WinAnsi), and no fake ToUnicode CMap is
needed.

One side effect: for byte 0x27, WinAnsi gives `'` where the implicit
StandardEncoding gave `’`. No test asserts on that character.

## The production defect

The fixture was only half the problem. `PageExtractionEngine` decided whether a
page needed OCR by `len(text.strip()) < min_text_chars_per_page` alone. Each
unreadable glyph extracts as a placeholder such as `(cid:84)`, so six of them
already clear the 40-character threshold. A page of placeholders was therefore
accepted as native text, never sent to OCR, and reported `complete`.

Two real-world font shapes produce such pages:

1. A composite (Type0, Identity-H) font with no `/ToUnicode`. This yields
   `(cid:N)` in **any** interpreter, regardless of import order.
2. A simple font with no `/Encoding` and no `/ToUnicode`. This yields `(cid:N)`
   after `ocrmypdf` has been imported anywhere in the process.

End-to-end measurement against the real pipeline (OCRmyPDF 16.10.4,
tesseract 5.5.0, pdfminer 20260107, the real `OcrMyPdfRunner` and
`PageExtractionEngine`):

| Input | Base `99ab522` | This branch |
|---|---|---|
| scan, composite-font page without ToUnicode, scan | `complete`, page 2 is `text_layer` with `(cid:84)(cid:104)…` | `complete`, page 2 `ocr_completed` with the real text |
| unencoded Helvetica, after `import ocrmypdf` | `complete`, `text_layer` with `(cid:67)…` | `complete`, `ocr_completed` with the real text |
| scan, CID page, scan, scan with an 8-character "RECEIVED" stamp | `partial`. The batch holding pages 3 and 4 failed with **exit 6**, so pure-scan page 3 was lost as well. | `complete`, all four pages `ocr_completed` |

## Production import order (before the fix)

On the backend, the import first happens on the first request that resolves
`get_document_controller`. The chain is:

```
routers/documents.py:2247 get_document_controller
 └ BulkUploadService.__init__ (services/bulk_upload_service.py:47)
   └ MetadataProcessorService.__init__ (metadata_processor_service.py:35)
     └ create_document_processor (document_processor.py:977)
       └ DocumentProcessor.__init__ (document_processor.py:91)
         └ OCRService.__init__ (ocr_service.py:36)
           └ _check_ocr_availability: `import ocrmypdf` (ocr_service.py:41)
```

On the worker, it happens on the first document job:
`background_jobs` calls `process_next_processing_jobs`, which calls
`process_document_async`, which reaches `create_document_processor` and then
`OCRService`.

Neither `import rbac_backend.main` nor `import rbac_backend.worker` imports
OCRmyPDF at startup. The import is lazy but then lasts for the life of the
process. In every document job, `DocumentProcessor` is constructed before
extraction runs, so even the first document a process handles is affected.

`OcrMyPdfRunner` runs OCRmyPDF in a subprocess, so the OCR run itself does not
patch the parent process. However, the parent re-reads the output PDF with
pdfplumber.

The legacy path used to import OCRmyPDF inside the process:
`OCRService._run_ocr_with_sidecar` called `ocrmypdf.ocr`, reached through
`process_pdf` from `_extract_legacy` and `MetadataProcessorService`. It no
longer does - see *Legacy path*, item 3 below - so no backend process imports
OCRmyPDF at all, and a test walks every production module's AST to keep it that
way.

## Changes

1. **Fixtures.** Every fixture font now declares `/WinAnsiEncoding`. The shared
   helpers are `_helvetica` and `_show_lines`. Two new fixtures cover the
   hazardous shapes on purpose:
   - `build_unencoded_font_pdf`, a simple font with no encoding;
   - `build_composite_font_pdf`, a composite font with or without `/ToUnicode`.
2. **Import-order guard.** `test_pdf_fixture_import_order.py` builds and
   extracts the same fixtures in two child interpreters, one clean and one that
   imports `ocrmypdf` first. Its control asserts that the patch really is
   installed in the second, and it pins the font dictionaries.
3. **Font matrix.** `test_pdf_font_encoding_matrix.py` covers:
   - A: Helvetica with an encoding;
   - B: Helvetica with no encoding;
   - C: composite font with ToUnicode, which is the same shape as OCRmyPDF's
     output font `GlyphLessFont`;
   - C-: composite font without ToUnicode.

   It also exercises the runner's full-length and trimmed page mapping inside
   the patched interpreter.
4. **Native-text quality.** `services/extraction/text_quality.py` marks a page
   as unusable if either of these holds:
   - its `(cid:N)` placeholders outnumber its readable non-whitespace
     characters;
   - it contains a run of 8 or more placeholders, with whitespace allowed
     between them.

   The engine treats an unusable page as an OCR candidate however long its text
   is. If OCR is disabled, the page gets status `ocr_disabled`, which makes the
   document `partial`, and an error message that contains only counts. OCR
   output that is itself unusable gets status `ocr_empty`. Placeholders are
   never stripped. The structural classification of the page is unchanged: the
   classifier describes the page, and the engine makes the routing decision.
5. **`--force-ocr`.** Without this flag, OCRmyPDF aborts the whole batch with
   exit 6 (`PriorOcrFoundError`) if any page in the batch has text. That is
   true of every CID page and of thin-text pages such as a scan with a stamp.
   `--skip-text` and `--redo-ocr` both leave the unusable text in place.
6. **Availability check.** The check now uses `importlib.util.find_spec` and
   `importlib.metadata.version` instead of `import ocrmypdf`. The pagewise
   pipeline needs only the `ocrmypdf` executable, which `REQUIRED_BINARIES`
   already checks. Nothing relies on import-order avoidance to be correct.

## Legacy path (`legacy_v0`)

`legacy_v0` is the default pipeline for every tenant not on the unified canary
(`services/pipeline_routing.py`), so `OCRService.process_pdf` is a live
production path, reached from `DocumentProcessor._extract_legacy`,
`MetadataProcessorService` and `BulkUploadService`. It decided whether a PDF had
text with `text.strip()` alone, so a CID-dominated PDF was classified textual,
never OCR'd, and its placeholders were returned as the document's text.

1. **One quality policy.** `OCRService.assess_text_layer` judges the inspected
   pages with the same `assess_native_text_quality` the engine uses - no second
   detector - and returns `textual`, `ocr_required_empty` or
   `ocr_required_unusable_text`. The two OCR verdicts stay apart because an
   unusable layer has to be *replaced*: unforced, OCRmyPDF refuses a PDF that
   already has text (exit 6). `is_pdf_textual` remains as a boolean wrapper.
2. **Every copy-the-original exit is guarded.** The assessment inspects five
   pages; `_unusable_pages` reads them all, tolerating a page pdfplumber cannot
   parse so one malformed page cannot hide an unusable one after it. It runs
   before every exit that would hand the original on: OCR unavailable, the
   textual branch's sidecar, exit 6 (prior OCR), a generic OCR failure, and a
   sidecar read error. `_extract_sidecar_text` raises rather than return
   placeholder text, and never strips placeholders.
3. **OCRmyPDF runs out of process.** `_ocrmypdf` runs the CLI exactly as
   `OcrMyPdfRunner` does (`shutil.which("ocrmypdf")`, else
   `sys.executable -m ocrmypdf`), so the parent's pdfminer is never patched.
   Exit codes map to typed errors - 6 prior OCR, 8 encrypted, anything else a
   failure - mirrored from `ocrmypdf.exceptions.ExitCode` (16.10.4) and pinned
   by a test that reads the installed enum in a child interpreter. Streams are
   captured as bytes and reported as lengths plus the exit name, never decoded
   text: OCRmyPDF reads the customer's PDF, so both streams can quote it.
4. **Only the unusable pages are forced.** Forced replacement passes
   `--pages`, so a 400-page born-digital contract with one bad page does not
   have its other 399 pages rasterised.
5. **Failures are visible.** A forced replacement has no fallback: OCRmyPDF
   missing, refusing the text, any other error, or OCR output that is itself
   unusable raises `UnusableTextLayerError` (a `DocumentProcessingError`),
   which carries page numbers and glyph counts only. An encrypted PDF is now a
   refusal too; it used to be caught by the same function's `except Exception`,
   copied, and returned as a processed document with no text at all. Ordinary
   scans keep their historical copy-and-return-`None` fallback.
6. **Run-scoped outputs.** Each call writes into `process_dir/run-<uuid>/` and
   returns a path inside it. `process_dir/<filename>` was shared, so two
   documents of the same file name overwrote and deleted each other's processed
   PDF and sidecar. A failed run removes only its own directory; a run
   directory older than twice the OCR timeout is swept, because a killed worker
   never reaches that cleanup and `process_dir` is a backed-up volume.
7. **The OCR run is bounded.** The in-process call had no timeout. A flat one
   would fail long scans that used to finish, so the bound is 900s or 6s per
   page being OCR'd, whichever is larger, capped at two hours. On timeout the
   whole process group is killed, so ghostscript and tesseract do not outlive
   the run that started them.

Measured on the fixtures (the repository tracks no PDFs): a composite-font PDF
that read `textual` before now reads `ocr_required_unusable_text`; the same
unencoded-font PDF gets the same verdict before and after a legacy OCR job in
one long-lived worker, which it did not when the job imported OCRmyPDF.

## Sidecar: not adopted

The OCRmyPDF sidecar file separates pages with `\f`. However, runs of
consecutive untouched pages collapse into a single marker, for example
`[OCR skipped on page(s) 1-3]` with no form feeds between them (measured with
`--skip-text`). Splitting the sidecar on `\f` is therefore not a page mapping.
The pipeline keeps re-reading the output PDF instead. That PDF's text layer uses
a Type0 font with ToUnicode, which extracted identically in the clean and the
patched interpreter.

## Not measured

**Production impact cannot be fully measured from available non-production
data.** The repository tracks no PDFs (`*.pdf` is gitignored), and the only
local PDFs are customer uploads, which were not read.

## Owner decisions, 2026-09-22 (second pass on this branch)

The owner approved two behaviours as intended:

1. **OCR routing.** A page whose native text is unusable, including a
   CID-dominated page, is an OCR candidate even though the PDF has a text
   layer.
2. **Metering.** OCR metering counts only the pages actually submitted to OCR
   (`attempted`).
   - A CID page that is detected while OCR is disabled is not metered.
   - A page deferred past `max_ocr_pages_per_attempt` is not metered.
   - A retry meters only the retried pages, with `retry=True`.

   Pinned by tests in `test_extraction_engine_cid_text.py`.

They also required three fixes.

3. **`ocr_disabled` lifecycle.** First, the retry path as traced in the code:
   - Every claim increments `document_processing_jobs.attempts`
     (`_claim_next_processing_job`, `process_document_job`).
   - `attempts_exhausted` is `attempts >= max_attempts` (3).
   - `PARTIALLY_PROCESSED` is requeued by `_schedule_page_resume` with a 5 s
     back-off.

   So the old behaviour was bounded, not infinite. But a permanent capability
   condition still ran extraction **three times** (measured, red) before
   reaching `human_review_required`.

   `derive_processing_state` now treats any `ocr_disabled` page like an
   unrenderable one: the document goes straight to `human_review_required`.
   That state is terminal, not a success, and the page is kept in
   `remaining_page_numbers` for the reviewer. After a configuration fix, the
   document is reprocessed by queueing a new job.

   `test_ocr_disabled_is_not_reclaimed_by_repeated_worker_passes` drives the
   real claim loop ten times and sees exactly one extraction. The contract
   path has no automatic page resume, so it cannot loop.

4. **Indexing.** One rule, `text_quality.withhold_unusable`, is applied once
   by the producer (`PageExtractionEngine._merge`). For a page with unusable
   text:
   - `page.text` becomes `""`;
   - the unusable text is kept whole as evidence in `page.raw_text`, which
     `DocumentPageStore` persists as `original_text`;
   - tables read from that same text layer are dropped;
   - the status stays unresolved (`ocr_disabled` / `ocr_failed` /
     `ocr_empty`).

   Every downstream consumer therefore sees only published text:
   `combined_text`, `DocumentProcessor` (OpenAI text extraction, `full_text`,
   embeddings), the document page records, the contract page records (whose
   `raw_text` is the published text, read by the contract clause agent) and
   contract chunking.

   The placeholders are not regex-stripped: an unusable page publishes nothing
   at all. `test_cid_text_indexing_seam.py` runs `DocumentProcessor` on the
   unified path with the real `OCRService`, engine and quality gate. It
   asserts that no `(cid:` reaches `save_document_data` or the OpenAI text
   call, in three cases:
   - OCR unavailable;
   - OCR failed;
   - OCR output itself unusable.

   These tests fail against the previous engine.
5. **Legacy path.** Implemented in `OCRService`. See
   `tests/test_legacy_ocr_cid_guard.py`, which uses the same
   `assess_native_text_quality` policy.

## Residual risks

- **`BUFSIZ`.** The 256 MiB `BUFSIZ` still applies in any process that imports
  OCRmyPDF. No backend process does any more, so this now bites only a tool
  that imports it deliberately.
- **Deterministic legacy failures still retry.** `document_service` decides
  terminal status from the attempt count alone, so a document that fails for a
  reason no retry can change - OCR unavailable, a refusal, unusable OCR output
  - still uses all three attempts before it is dead-lettered. Retries are
  bounded, unusable text cannot publish, and run-scoped outputs mean no attempt
  can damage another document's artefacts, so this is accepted debt rather than
  a blocker. Giving the job contract a non-retryable classification is a
  separate change across `ProcessingResult`, `documents.processing_error` and
  `_mark_processing_failure`.
- **A large scan whose OCR fails still completes with no text.** In the
  `ocr_required_empty` branch, an OCR failure on a PDF with nothing unusable in
  it copies the original and returns `None`, unmarked. That predates this work.
- **Force-OCR is still document-level in one case.** Only the unusable pages
  are forced, but a document with many such pages is largely rasterised, and
  the legacy path has no per-page `withheld_pages` equivalent.
- **Retries replace page records.** A retry attempt replaces the page records
  of pages outside its retry set. This behaviour predates this branch, was
  observed during this work and was not investigated.
