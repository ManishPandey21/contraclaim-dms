from __future__ import annotations

import io
from typing import Any, Dict


class ArbitrationDraftExporter:
    @staticmethod
    def build_pdf(version: Dict[str, Any]) -> bytes:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
        from xml.sax.saxutils import escape

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, title="Arbitration Pleading")
        styles = getSampleStyleSheet()
        flow = []
        for raw in (version.get("full_markdown") or "").splitlines():
            line = raw.rstrip()
            if not line:
                flow.append(Spacer(1, 6))
            elif line.startswith("## "):
                flow.append(Paragraph(escape(line[3:]), styles["Heading2"]))
            elif line.startswith("# "):
                flow.append(Paragraph(escape(line[2:]), styles["Heading1"]))
            else:
                flow.append(Paragraph(escape(line), styles["BodyText"]))
        doc.build(flow)
        return buffer.getvalue()

    @staticmethod
    def build_docx(version: Dict[str, Any]) -> bytes:
        import docx

        document = docx.Document()
        for raw in (version.get("full_markdown") or "").splitlines():
            line = raw.rstrip()
            if not line:
                document.add_paragraph("")
            elif line.startswith("## "):
                document.add_heading(line[3:], level=2)
            elif line.startswith("# "):
                document.add_heading(line[2:], level=1)
            else:
                document.add_paragraph(line)
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

