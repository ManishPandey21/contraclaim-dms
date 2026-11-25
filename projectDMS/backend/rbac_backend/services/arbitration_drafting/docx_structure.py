"""What a Word export says and how it is laid out, writer-independent (D-19, AQ-7).

Two writers produce arbitration Word files: the minimal writer production uses,
and python-docx (behind ``DOCX_WRITER``). Before the owner switches writer, the
two are compared by what a reader sees: the body in order, each paragraph's style
and text, each table's rows and cells. Package internals (relationships, styles
part, settings, timestamps) are not compared: they differ by design and do not
change what the pleading says.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any, Dict, List, Tuple
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

Block = Tuple[Any, ...]


def _text(element: ElementTree.Element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{W}t"))


def _style(paragraph: ElementTree.Element) -> str:
    style = paragraph.find(f"{W}pPr/{W}pStyle")
    return (style.get(f"{W}val") if style is not None else "") or ""


def structure(docx_bytes: bytes) -> List[Block]:
    """The body as ("p", style, text) and ("table", rows) blocks, in order. Raises if the
    package or its main part is not well-formed."""
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
        for name in archive.namelist():
            if name.endswith(".xml") or name.endswith(".rels"):
                ElementTree.fromstring(archive.read(name))  # every part parses
        body = ElementTree.fromstring(archive.read("word/document.xml")).find(f"{W}body")
    blocks: List[Block] = []
    for child in list(body if body is not None else []):
        if child.tag == f"{W}p":
            blocks.append(("p", _style(child), _text(child)))
        elif child.tag == f"{W}tbl":
            rows = [
                tuple(_text(cell) for cell in row.findall(f"{W}tc"))
                for row in child.findall(f"{W}tr")
            ]
            blocks.append(("table", tuple(rows)))
    return blocks


def compare(minimal: bytes, python_docx: bytes) -> Dict[str, Any]:
    """Differences a reader would notice between the two writers' files."""
    left, right = structure(minimal), structure(python_docx)
    left_text = [block[2] for block in left if block[0] == "p"]
    right_text = [block[2] for block in right if block[0] == "p"]
    left_tables = [block[1] for block in left if block[0] == "table"]
    right_tables = [block[1] for block in right if block[0] == "table"]
    differences: List[str] = []
    if left_text != right_text:
        differences.append("paragraph text or order differs")
    if left_tables != right_tables:
        differences.append("table cells differ")
    if [b[0] for b in left] != [b[0] for b in right]:
        differences.append("block sequence (paragraph/table order) differs")
    left_styles = [(b[1], b[2]) for b in left if b[0] == "p" and b[1]]
    right_styles = [(b[1], b[2]) for b in right if b[0] == "p" and b[1]]
    if left_styles != right_styles:
        differences.append("heading styles differ")
    return {
        "minimal_blocks": len(left),
        "python_docx_blocks": len(right),
        "paragraphs": len(left_text),
        "tables": len(left_tables),
        "identical_text_and_structure": not differences,
        "differences": differences,
    }


__all__ = ["compare", "structure"]
