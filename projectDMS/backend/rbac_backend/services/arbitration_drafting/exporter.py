"""Arbitration pleading exporters (DOCX / PDF).

Audit item 13: markdown tables are rendered as real tables and a Contents list
of the top-level (``##``) section headings is inserted after the title, instead
of dumping raw markdown pipes and headings only.
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Tuple


def _is_separator_row(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(set(cell) <= set("-: ") and "-" in cell for cell in cells)


def _split_table_row(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_blocks(markdown: str) -> List[Tuple[str, Any]]:
    """Parse markdown into ('h1'|'h2'|'para'|'blank'|'table', payload) blocks."""
    lines = (markdown or "").splitlines()
    blocks: List[Tuple[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        # A table: a pipe row immediately followed by a separator row.
        if "|" in line and i + 1 < len(lines) and _is_separator_row(lines[i + 1]):
            header = _split_table_row(line)
            rows = [header]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.append(("table", rows))
            continue
        if not line:
            blocks.append(("blank", ""))
        elif line.startswith("## "):
            blocks.append(("h2", line[3:]))
        elif line.startswith("# "):
            blocks.append(("h1", line[2:]))
        else:
            blocks.append(("para", line))
        i += 1
    return blocks


def _contents(blocks: List[Tuple[str, Any]]) -> List[str]:
    return [payload for kind, payload in blocks if kind == "h2"]


class ArbitrationDraftExporter:
    @staticmethod
    def build_pdf(version: Dict[str, Any]) -> bytes:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from xml.sax.saxutils import escape

        blocks = _parse_blocks(version.get("full_markdown") or "")
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, title="Arbitration Pleading")
        styles = getSampleStyleSheet()
        flow: List[Any] = []

        contents = _contents(blocks)
        if contents:
            flow.append(Paragraph("Contents", styles["Heading2"]))
            for idx, heading in enumerate(contents, start=1):
                flow.append(Paragraph(f"{idx}. {escape(heading)}", styles["BodyText"]))
            flow.append(Spacer(1, 12))

        for kind, payload in blocks:
            if kind == "blank":
                flow.append(Spacer(1, 6))
            elif kind == "h1":
                flow.append(Paragraph(escape(payload), styles["Heading1"]))
            elif kind == "h2":
                flow.append(Paragraph(escape(payload), styles["Heading2"]))
            elif kind == "table":
                data = [[Paragraph(escape(cell), styles["BodyText"]) for cell in row] for row in payload]
                table = Table(data, hAlign="LEFT")
                table.setStyle(
                    TableStyle(
                        [
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ]
                    )
                )
                flow.append(table)
                flow.append(Spacer(1, 6))
            else:
                flow.append(Paragraph(escape(payload), styles["BodyText"]))
        doc.build(flow)
        return buffer.getvalue()

    @staticmethod
    def build_docx(version: Dict[str, Any]) -> bytes:
        try:
            import docx
        except ModuleNotFoundError:
            return ArbitrationDraftExporter._build_minimal_docx(version.get("full_markdown") or "")

        blocks = _parse_blocks(version.get("full_markdown") or "")
        document = docx.Document()

        contents = _contents(blocks)
        if contents:
            document.add_heading("Contents", level=2)
            for idx, heading in enumerate(contents, start=1):
                document.add_paragraph(f"{idx}. {heading}")
            document.add_paragraph("")

        for kind, payload in blocks:
            if kind == "blank":
                document.add_paragraph("")
            elif kind == "h1":
                document.add_heading(payload, level=1)
            elif kind == "h2":
                document.add_heading(payload, level=2)
            elif kind == "table":
                rows = payload
                cols = max((len(r) for r in rows), default=1)
                table = document.add_table(rows=0, cols=cols)
                try:
                    table.style = "Table Grid"
                except KeyError:  # pragma: no cover - style set unavailable
                    pass
                for r_idx, row in enumerate(rows):
                    cells = table.add_row().cells
                    for c_idx in range(cols):
                        cells[c_idx].text = row[c_idx] if c_idx < len(row) else ""
                        if r_idx == 0:
                            for paragraph in cells[c_idx].paragraphs:
                                for r in paragraph.runs:
                                    r.bold = True
            else:
                document.add_paragraph(payload)
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    @staticmethod
    def _build_minimal_docx(markdown: str) -> bytes:
        import zipfile
        from xml.sax.saxutils import escape

        def paragraph_xml(text: str, style: Optional[str] = None) -> str:
            body = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
            return f'<w:p>{body}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'

        blocks = _parse_blocks(markdown)
        parts: List[str] = []
        contents = _contents(blocks)
        if contents:
            parts.append(paragraph_xml("Contents", "Heading2"))
            for idx, heading in enumerate(contents, start=1):
                parts.append(paragraph_xml(f"{idx}. {heading}"))
            parts.append(paragraph_xml(""))
        for kind, payload in blocks:
            if kind == "h1":
                parts.append(paragraph_xml(payload, "Heading1"))
            elif kind == "h2":
                parts.append(paragraph_xml(payload, "Heading2"))
            elif kind == "table":
                # Degrade tables to tab-joined rows (no table styling without python-docx).
                for row in payload:
                    parts.append(paragraph_xml(" \t ".join(row)))
            elif kind == "blank":
                parts.append(paragraph_xml(""))
            else:
                parts.append(paragraph_xml(payload))

        body = "".join(parts)
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr></w:body>"
            "</w:document>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "[Content_Types].xml",
                (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                    "</Types>"
                ),
            )
            archive.writestr(
                "_rels/.rels",
                (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                    "</Relationships>"
                ),
            )
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()
