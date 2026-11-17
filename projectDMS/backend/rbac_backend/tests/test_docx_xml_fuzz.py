"""4B-29 (C-16): every Word package the two hand-built writers produce is well-formed.

A title containing ``\\x0b`` (a vertical tab from a pasted spreadsheet cell)
produced a ``word/document.xml`` Word would not open, and the submission
writer's table had no ``w:tblGrid``. Each character XML 1.0 forbids is put into
a heading, a paragraph and a table cell of both writers; every part of every
package must then parse, with the content types, the relationships and a
``w:tblGrid`` that matches each row. No new test dependency: the standard
library's XML parser is the oracle.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from xml.etree import ElementTree

import pytest

from rbac_backend.models.claim_findings import ClaimSubmission, EvidenceItem, SubmissionParagraph, SubmissionSection
from rbac_backend.services.arbitration_drafting.exporter import ArbitrationDraftExporter
from rbac_backend.services.claim_submission import submission_docx
from rbac_backend.services.docx_xml import clean_text, w_table, xml_text

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

FORBIDDEN = [chr(code) for code in range(0x00, 0x20) if code not in (0x09, 0x0A, 0x0D)] + ["￾", "￿", "\ud800"]


def _parts(package: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = set(archive.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= names, names
        parsed = {}
        for name in names:
            if name.endswith(".xml") or name.endswith(".rels"):
                parsed[name] = ElementTree.fromstring(archive.read(name))  # raises on malformed XML
        return parsed


def _assert_tables_have_grids(document: ElementTree.Element, expected_tables: int) -> None:
    tables = document.iter(f"{W}tbl")
    count = 0
    for table in tables:
        count += 1
        grid = table.find(f"{W}tblGrid")
        assert grid is not None, "w:tblGrid missing"
        columns = len(grid.findall(f"{W}gridCol"))
        for row in table.findall(f"{W}tr"):
            assert len(row.findall(f"{W}tc")) == columns
    assert count == expected_tables


def _submission(text: str) -> ClaimSubmission:
    return ClaimSubmission(
        claim_id="c1",
        sections=[
            SubmissionSection(
                key="executive_summary",
                title=f"Summary {text} heading",
                paragraphs=[SubmissionParagraph(text=f"The Site was handed over late{text}, on 2 March 2026.")],
            )
        ],
        evidence=[
            EvidenceItem(number="E-1", document_id="d1", title=f"Letter{text}title", reference=f"REF{text}1", date=datetime(2026, 3, 2)),
            EvidenceItem(number="E-2", document_id="d2", title="Programme"),
        ],
    )


@pytest.mark.parametrize("char", FORBIDDEN, ids=lambda c: f"U+{ord(c):04X}")
def test_the_submission_writer_survives_every_forbidden_character(char: str) -> None:
    parts = _parts(submission_docx(_submission(char), f"Statement {char} of Claim", approved=False))
    document = parts["word/document.xml"]
    _assert_tables_have_grids(document, expected_tables=1)
    text = "".join(node.text or "" for node in document.iter(f"{W}t"))
    assert char not in text
    assert "Letter" in text and "title" in text  # the surrounding text is kept, only the character goes


@pytest.mark.parametrize("char", FORBIDDEN, ids=lambda c: f"U+{ord(c):04X}")
def test_the_arbitration_fallback_writer_survives_every_forbidden_character(char: str) -> None:
    markdown = (
        f"# STATEMENT {char} OF CLAIM\n\n## Facts{char}\n\nThe Respondent{char} delayed access.\n\n"
        f"| No. | Exhibit | Description |\n|---|---|---|\n| 1 | C-1{char} | Letter |\n| 2 | C-2 |\n"
    )
    parts = _parts(ArbitrationDraftExporter._build_minimal_docx(markdown))
    document = parts["word/document.xml"]
    _assert_tables_have_grids(document, expected_tables=1)
    assert char not in "".join(node.text or "" for node in document.iter(f"{W}t"))


def test_ragged_rows_are_padded_to_the_grid() -> None:
    xml = f'<w:document xmlns:w="{W[1:-1]}"><w:body>{w_table([["a", "b", "c"], ["d"]])}</w:body></w:document>'
    _assert_tables_have_grids(ElementTree.fromstring(xml), expected_tables=1)


def test_markup_is_escaped_and_allowed_whitespace_is_kept() -> None:
    assert xml_text('a<b>&"c"') == "a&lt;b&gt;&amp;&quot;c&quot;"
    assert clean_text("tab\tline\nreturn\r") == "tab\tline\nreturn\r"
    assert clean_text("x\x0by\x00z") == "xyz"
    assert clean_text(None) == ""


def test_markup_and_right_to_left_marks_survive_in_every_field() -> None:
    """Section 6.6: &, <, > and RTL marks are legal XML: kept, escaped, and the package parses."""
    tricky = "A&B <C> ‏RTL‮ override"
    parts = _parts(submission_docx(_submission(tricky), tricky, approved=True))
    text = "".join(node.text or "" for node in parts["word/document.xml"].iter(f"{W}t"))
    assert tricky in text
    markdown = f"# {tricky}\n\n{tricky}\n\n| a | b |\n|---|---|\n| {tricky} | x |\n"
    parts = _parts(ArbitrationDraftExporter._build_minimal_docx(markdown))
    assert tricky in "".join(node.text or "" for node in parts["word/document.xml"].iter(f"{W}t"))
