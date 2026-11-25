"""AQ-7 / 4B-30: the configurable Word writer (D-19).

- the default writer is the minimal one production uses, whatever is installed;
- python-docx is used only when chosen, and a chosen writer that is missing is an
  error, never a silent fallback; an unknown writer is refused at start-up;
- both writers produce well-formed packages with the same text, order, headings and
  table cells for the review fixtures (the differences are recorded in
  docs/DOCX_WRITER_COMPARISON.md for the owner);
- citations and exhibit references survive verbatim; every XML-forbidden
  character is removed; markup is escaped; malformed and large inputs do not break
  either writer.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from rbac_backend.services.arbitration_drafting.docx_structure import compare, structure
from rbac_backend.services.arbitration_drafting.exporter import ArbitrationDraftExporter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arbitration_export"
FIXTURE_NAMES = sorted(path.name for path in FIXTURES.glob("*.md"))
WRITERS = ("minimal", "python_docx")


def _docx(markdown: str, writer: str) -> bytes:
    if writer == "python_docx":
        pytest.importorskip("docx")
    return ArbitrationDraftExporter.build_docx({"full_markdown": markdown}, writer=writer)


def _texts(docx: bytes) -> str:
    parts = []
    for block in structure(docx):
        parts.append(block[2] if block[0] == "p" else " ".join(cell for row in block[1] for cell in row))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Choice of writer
# --------------------------------------------------------------------------- #


def test_the_default_is_the_minimal_writer_even_with_python_docx_installed(monkeypatch) -> None:
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "DOCX_WRITER", "minimal")
    markdown = (FIXTURES / "statement_of_claim.md").read_text(encoding="utf-8")
    default = ArbitrationDraftExporter.build_docx({"full_markdown": markdown})
    assert structure(default) == structure(ArbitrationDraftExporter._build_minimal_docx(markdown))


def test_python_docx_is_used_only_when_chosen(monkeypatch) -> None:
    pytest.importorskip("docx")
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "DOCX_WRITER", "python_docx")
    markdown = (FIXTURES / "tables.md").read_text(encoding="utf-8")
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(ArbitrationDraftExporter.build_docx({"full_markdown": markdown}))) as archive:
        assert "word/styles.xml" in archive.namelist()


def test_a_chosen_writer_that_is_missing_is_an_error_not_a_fallback(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "docx", None)  # import docx -> ModuleNotFoundError
    with pytest.raises(RuntimeError, match="python-docx is not installed"):
        ArbitrationDraftExporter.build_docx({"full_markdown": "# T\n"}, writer="python_docx")
    with pytest.raises(ValueError, match="Unknown DOCX_WRITER"):
        ArbitrationDraftExporter.build_docx({"full_markdown": "# T\n"}, writer="pandoc")


def test_an_unknown_writer_is_refused_at_start_up(monkeypatch) -> None:
    from rbac_backend.core.config import settings

    settings.validate_runtime_configuration()  # the configuration under test starts cleanly
    monkeypatch.setattr(settings, "DOCX_WRITER", "pandoc")
    with pytest.raises(ValueError, match="DOCX_WRITER"):
        settings.validate_runtime_configuration()  # main.py runs this at start-up


# --------------------------------------------------------------------------- #
# Regression comparison and content safety
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_both_writers_say_the_same_thing_in_the_same_order(name: str) -> None:
    markdown = (FIXTURES / name).read_text(encoding="utf-8")
    minimal, python_docx = _docx(markdown, "minimal"), _docx(markdown, "python_docx")
    result = compare(minimal, python_docx)
    without_empty = [
        [block for block in structure(docx) if block != ("p", "", "")] for docx in (minimal, python_docx)
    ]
    # The one known difference (docs/DOCX_WRITER_COMPARISON.md): the minimal writer adds an
    # empty paragraph after each table. Everything else is identical.
    assert without_empty[0] == without_empty[1], result


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize(
    "reference",
    ["[S1: C-1]", "[S8: ISS-1]", "[S9: CLM-1/H1]", "(Ex. C-3)", "[Evidence required]", "A-3. Site diary, March 2026 (C-3)"],
)
def test_citations_and_exhibit_references_survive_verbatim(writer: str, reference: str) -> None:
    markdown = (FIXTURES / "statement_of_claim.md").read_text(encoding="utf-8")
    assert reference in _texts(_docx(markdown, writer))


@pytest.mark.parametrize("writer", WRITERS)
def test_exhibit_forms_from_other_tribunals_survive(writer: str) -> None:
    text = _texts(_docx((FIXTURES / "citations_exhibits.md").read_text(encoding="utf-8"), writer))
    for reference in ("Ex. C-12", "Annexure A-7", "Exhibit R/3", "CW-1/4", "[S6: CW-1/4]", "Sub-Clause 8.4"):
        assert reference in text, reference


FORBIDDEN = [chr(code) for code in range(0x00, 0x20) if code not in (0x09, 0x0A, 0x0D)] + ["￾", "￿"]


@pytest.mark.parametrize("writer", WRITERS)
def test_every_xml_forbidden_character_is_removed(writer: str) -> None:
    markdown = "# Title" + "".join(FORBIDDEN) + "\n\n## Head\n\nBody " + "".join(FORBIDDEN) + " end [S1: C-1]\n\n| a | b" + FORBIDDEN[3] + " |\n|---|---|\n| 1 | 2 |\n"
    docx = _docx(markdown, writer)
    text = _texts(docx)  # every part parses (structure() parses them all)
    assert "Body" in text and "end [S1: C-1]" in text
    assert not any(char in text for char in FORBIDDEN)


@pytest.mark.parametrize("writer", WRITERS)
def test_markup_is_text_never_markup(writer: str) -> None:
    hostile = "<script>alert(1)</script> & <w:p/> ]]> \"quoted\" 'single'"
    text = _texts(_docx(f"# T\n\n{hostile}\n", writer))
    assert hostile in text


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize(
    "markdown",
    [
        "",
        "#",
        "##\n\n| a |\n| b | c | d |\n",  # ragged table, no separator row
        "| only | a | header |\n|---|\n",
        "**unclosed bold and _italic\n\n- - - nested\n    - deeper\n        - deepest\n",
        "x" * 20000 + "\n",
        "\n\n\n\n",
        "|||\n|---|---|---|\n|||\n",
    ],
)
def test_malformed_content_never_breaks_a_writer(writer: str, markdown: str) -> None:
    docx = _docx(markdown, writer)
    assert docx[:2] == b"PK"
    structure(docx)  # well-formed


@pytest.mark.parametrize("writer", WRITERS)
def test_a_large_pleading_is_written_in_reasonable_time(writer: str) -> None:
    paragraphs = "\n\n".join(f"{n}. The Claimant relies on the record [S{n % 50 + 1}: C-{n}]." for n in range(1, 3001))
    table = "| Item | Amount |\n|---|---|\n" + "\n".join(f"| Line {n} | INR {n * 1000:,} |" for n in range(1, 301))
    markdown = f"# Statement of Claim\n\n## Facts\n\n{paragraphs}\n\n## Quantum\n\n{table}\n"
    started = time.monotonic()
    docx = _docx(markdown, writer)
    elapsed = time.monotonic() - started
    blocks = structure(docx)
    assert elapsed < 60, elapsed
    assert sum(1 for b in blocks if b[0] == "p" and "relies on the record" in b[2]) == 3000
    assert [len(b[1]) for b in blocks if b[0] == "table"] == [301]
