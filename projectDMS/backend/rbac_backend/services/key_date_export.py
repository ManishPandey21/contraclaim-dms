"""Key Date register export (CSV / XLSX / PDF).

Pure CSV rendering (trivially testable); XLSX via openpyxl and PDF via reportlab
are lazy-imported so the module loads even where those libraries are absent.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List

from .key_date_service import DATE_DISPLAY_FORMAT

COLUMNS = [
    ("milestone_ref", "Ref"),
    ("title", "Title"),
    ("contractual_week_number", "Week"),
    ("original_planned_key_date", "Original key date"),
    ("current_approved_key_date", "Current key date"),
    ("status", "Status"),
    ("eot_status", "EOT status"),
    ("days_remaining", "Days remaining"),
    ("actual_achievement_date", "Achieved on"),
    ("delay_days", "Delay days"),
    ("early_completion_days", "Early days"),
    ("responsible_party_id", "Responsible"),
    ("current_revision", "Revisions"),
]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "date"):
        try:
            # Contractual documents read day-first; see DATE_DISPLAY_FORMAT.
            return value.strftime(DATE_DISPLAY_FORMAT)
        except Exception:
            return str(value)
    return str(value)


def milestones_to_csv(rows: List[Dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([label for _key, label in COLUMNS])
    for row in rows or []:
        writer.writerow([_cell(row.get(key)) for key, _label in COLUMNS])
    return buffer.getvalue()


def milestones_to_xlsx(rows: List[Dict[str, Any]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Key Dates"
    ws.append([label for _key, label in COLUMNS])
    for row in rows or []:
        ws.append([_cell(row.get(key)) for key, _label in COLUMNS])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def milestones_to_pdf(rows: List[Dict[str, Any]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), title="Key Date Register")
    data = [[label for _key, label in COLUMNS]]
    for row in rows or []:
        data.append([_cell(row.get(key)) for key, _label in COLUMNS])
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ])
    )
    doc.build([table])
    return buffer.getvalue()
