#!/usr/bin/env python3
"""Compare the two arbitration Word writers for owner review (D-19, AQ-7).

Renders every fixture in backend/rbac_backend/tests/fixtures/arbitration_export with
the minimal writer (production today) and the python-docx writer, and reports what a
reader would notice: text and order, headings, tables, and the package parts that
decide appearance. Read-only; writes the report to stdout.

    PYTHONPATH=backend python scripts/docx_writer_comparison.py > docs/DOCX_WRITER_COMPARISON.md
"""

from __future__ import annotations

import io
import zipfile
from importlib import metadata
from pathlib import Path

from rbac_backend.services.arbitration_drafting.docx_structure import compare, structure
from rbac_backend.services.arbitration_drafting.exporter import ArbitrationDraftExporter

FIXTURES = Path(__file__).resolve().parents[1] / "backend" / "rbac_backend" / "tests" / "fixtures" / "arbitration_export"


def _parts(docx: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        return sorted(archive.namelist())


def main() -> None:
    lines = [
        "# DOCX writer comparison (D-19, AQ-7)",
        "",
        "**Generated. Do not edit by hand.** Regenerate with "
        "`PYTHONPATH=backend python scripts/docx_writer_comparison.py > docs/DOCX_WRITER_COMPARISON.md`.",
        "",
        f"python-docx {metadata.version('python-docx')}. Production uses the minimal writer (`DOCX_WRITER=minimal`, "
        "the default) and keeps it until the owner approves a switch after reading this. Compared: what a reader sees "
        "(paragraph text and order, heading styles, table cells); package parts are listed, not compared.",
        "",
        "| Fixture | Paragraphs | Tables | Same text and structure | Differences |",
        "|---|---|---|---|---|",
    ]
    details = []
    for fixture in sorted(FIXTURES.glob("*.md")):
        markdown = fixture.read_text(encoding="utf-8")
        minimal = ArbitrationDraftExporter.build_docx({"full_markdown": markdown}, writer="minimal")
        python_docx = ArbitrationDraftExporter.build_docx({"full_markdown": markdown}, writer="python_docx")
        result = compare(minimal, python_docx)
        lines.append(
            f"| `{fixture.name}` | {result['paragraphs']} | {result['tables']} | "
            f"{'yes' if result['identical_text_and_structure'] else 'no'} | {'; '.join(result['differences']) or '-'} |"
        )
        if result["differences"]:
            left, right = structure(minimal), structure(python_docx)
            details.append(f"### `{fixture.name}`")
            details.append("")
            details.append(f"Blocks: minimal {len(left)}, python-docx {len(right)}.")
            def empties(blocks: list) -> int:
                return sum(1 for b in blocks if b == ("p", "", ""))

            same_non_empty = [b for b in left if b != ("p", "", "")] == [b for b in right if b != ("p", "", "")]
            details.append(
                f"Empty paragraphs: minimal {empties(left)}, python-docx {empties(right)}. "
                f"Everything else identical, in the same order: {'yes' if same_non_empty else 'no'}."
            )
            details.append("")
    sample = (FIXTURES / "statement_of_claim.md").read_text(encoding="utf-8")
    minimal_parts = _parts(ArbitrationDraftExporter.build_docx({"full_markdown": sample}, writer="minimal"))
    python_parts = _parts(ArbitrationDraftExporter.build_docx({"full_markdown": sample}, writer="python_docx"))
    lines += [
        "",
        "## Differences in detail",
        "",
        *(details or ["None."]),
        "## Package parts (appearance)",
        "",
        f"- Minimal writer: {', '.join(minimal_parts)}.",
        f"- python-docx: {', '.join(python_parts)}.",
        "",
        "The minimal package has no `word/styles.xml`, so the `Heading1`/`Heading2` styles it names are undefined: "
        "Word shows those headings as body text, and tables without the `Table Grid` borders. python-docx ships a "
        "styles part (real headings, bordered tables), default page settings and document properties. The text is "
        "the same; the appearance is not. This is the change the owner is asked to review before `DOCX_WRITER=python_docx`.",
        "",
        "Known structural difference: the minimal writer writes an empty paragraph after every table (Word needs a "
        "paragraph between two adjacent tables, and it spaces the next heading); python-docx does not, so two tables in "
        "a row would merge visually unless a paragraph separates them in the text.",
        "",
        "## Installing python-docx in production changes more than this writer",
        "",
        "python-docx is pinned in `rbac_backend/requirements-docx-writer.txt`, installed by CI only. It is not in "
        "`requirements.txt` (the production image), because four other code paths pick it up when it is present, so "
        "installing it would change production behaviour beyond `DOCX_WRITER`:",
        "",
        "- `services/letter_drafting/service.py` `_build_docx_bytes`: Letter Bench Word export switches from its fallback writer to python-docx;",
        "- `services/contracts_ingest.py`: DOCX contracts gain a python-docx text parser at ingestion;",
        "- `services/contract_appraisal/service.py` `build_docx` and `services/chronology.py` `_docx_bytes`: these Word exports "
        "import python-docx unconditionally, so today they fail in production and would start working.",
        "",
        "Separately, `services/docling/ocr_service.py` (the Docling microservice, its own image) already installs "
        "`python-docx>=1.1.0` from `services/docling/requirements.txt`; it is unaffected by this setting and by the "
        "backend's requirements.",
        "",
        "Approving `DOCX_WRITER=python_docx` therefore also needs a decision on each of these (or a guard on each) before "
        "python-docx moves into `requirements.txt`.",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
