# DOCX writer comparison (D-19, AQ-7)

**Generated. Do not edit by hand.** Regenerate with `PYTHONPATH=backend python scripts/docx_writer_comparison.py > docs/DOCX_WRITER_COMPARISON.md`.

python-docx 1.2.0. Production uses the minimal writer (`DOCX_WRITER=minimal`, the default) and keeps it until the owner approves a switch after reading this. Compared: what a reader sees (paragraph text and order, heading styles, table cells); package parts are listed, not compared.

| Fixture | Paragraphs | Tables | Same text and structure | Differences |
|---|---|---|---|---|
| `citations_exhibits.md` | 11 | 0 | yes | - |
| `statement_of_claim.md` | 38 | 0 | yes | - |
| `tables.md` | 15 | 2 | no | paragraph text or order differs; block sequence (paragraph/table order) differs |
| `unicode.md` | 14 | 0 | yes | - |

## Differences in detail

### `tables.md`

Blocks: minimal 17, python-docx 15.
Empty paragraphs: minimal 8, python-docx 6. Everything else identical, in the same order: yes.

## Package parts (appearance)

- Minimal writer: [Content_Types].xml, _rels/.rels, word/document.xml.
- python-docx: [Content_Types].xml, _rels/.rels, customXml/_rels/item1.xml.rels, customXml/item1.xml, customXml/itemProps1.xml, docProps/app.xml, docProps/core.xml, docProps/thumbnail.jpeg, word/_rels/document.xml.rels, word/document.xml, word/fontTable.xml, word/numbering.xml, word/settings.xml, word/styles.xml, word/stylesWithEffects.xml, word/theme/theme1.xml, word/webSettings.xml.

The minimal package has no `word/styles.xml`, so the `Heading1`/`Heading2` styles it names are undefined: Word shows those headings as body text, and tables without the `Table Grid` borders. python-docx ships a styles part (real headings, bordered tables), default page settings and document properties. The text is the same; the appearance is not. This is the change the owner is asked to review before `DOCX_WRITER=python_docx`.

Known structural difference: the minimal writer writes an empty paragraph after every table (Word needs a paragraph between two adjacent tables, and it spaces the next heading); python-docx does not, so two tables in a row would merge visually unless a paragraph separates them in the text.

## Installing python-docx in production changes more than this writer

python-docx is pinned in `rbac_backend/requirements-docx-writer.txt`, installed by CI only. It is not in `requirements.txt` (the production image), because four other code paths pick it up when it is present, so installing it would change production behaviour beyond `DOCX_WRITER`:

- `services/letter_drafting/service.py` `_build_docx_bytes`: Letter Bench Word export switches from its fallback writer to python-docx;
- `services/contracts_ingest.py`: DOCX contracts gain a python-docx text parser at ingestion;
- `services/contract_appraisal/service.py` `build_docx` and `services/chronology.py` `_docx_bytes`: these Word exports import python-docx unconditionally, so today they fail in production and would start working.

Separately, `services/docling/ocr_service.py` (the Docling microservice, its own image) already installs `python-docx>=1.1.0` from `services/docling/requirements.txt`; it is unaffected by this setting and by the backend's requirements.

Approving `DOCX_WRITER=python_docx` therefore also needs a decision on each of these (or a guard on each) before python-docx moves into `requirements.txt`.
