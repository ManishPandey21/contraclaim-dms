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

The legacy path still imports OCRmyPDF inside the process, and does so
legitimately: `OCRService._run_ocr_with_sidecar` calls `ocrmypdf.ocr`, and is
reached through `process_pdf` from `_extract_legacy` and
`MetadataProcessorService`.

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

## Residual risks (not addressed on this branch)

- **Legacy path unguarded.** `is_pdf_textual` and `_extract_sidecar_text`, used
  by the legacy `legacy_v0` rollback path and by
  `MetadataProcessorService.extract_metadata`, still accept `(cid:N)` text.
- **Metering.** On the contract path, CID pages now count as metered OCR pages.
  A tenant close to quota can hit `QuotaExceededError` on documents that used
  to pass.
- **Retry loop.** `ocr_disabled` does not use up a retry attempt. Documents
  with CID pages on a deployment where OCR is off will therefore resume as
  `partially_processed` indefinitely. The same loop already existed for thin
  pages.
- **Placeholder indexing.** The `(cid:N)` text of unresolved pages still goes
  into `combined_text`.
- **`BUFSIZ`.** The 256 MiB `BUFSIZ` still applies in any process that imports
  OCRmyPDF. Reversing another library's global patch was out of scope.
