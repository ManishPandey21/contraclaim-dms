"""Export helpers for the Contract Control registers (Variation + Bank Guarantee).

Pure CSV rendering; XLSX (openpyxl) and PDF (reportlab) are lazy-imported so the
module loads even where those libraries are absent. ``export_response`` returns a
ready FastAPI Response in the requested format.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Tuple

from fastapi import HTTPException, Response

VARIATION_COLUMNS: List[Tuple[str, str]] = [
    ("variation_number", "Variation No"),
    ("variation_type", "Type"),
    ("description", "Description"),
    ("letter_reference", "Letter Ref"),
    ("submitted_amount", "Submitted"),
    ("approved_amount", "Approved"),
    ("difference_amount", "Difference"),
    ("status", "Status"),
    ("approval_date", "Approval Date"),
    ("remarks", "Remarks"),
]

BG_COLUMNS: List[Tuple[str, str]] = [
    ("bg_type", "BG Type"),
    ("bg_number", "BG Number"),
    ("issuing_bank", "Bank"),
    ("bg_amount", "Amount"),
    ("currency", "Currency"),
    ("contractual_required_up_to", "Required Up To"),
    ("bg_expiry_date", "Expiry Date"),
    ("claim_expiry_date", "Claim Expiry"),
    ("extension_required", "Extension Req."),
    ("bg_status", "Status"),
    ("days_to_expiry", "Days To Expiry"),
]


IPC_COLUMNS: List[Tuple[str, str]] = [
    ("ipc_number", "IPC No"),
    ("ipc_date", "IPC Date"),
    ("period_from", "Period From"),
    ("period_to", "Period To"),
    ("contractor_name", "Contractor"),
    ("approver", "Approver"),
    ("status", "Status"),
    ("payment_structure", "Payment Structure"),
    ("payment_percentage", "Payment %"),
    ("base_currency", "Base Ccy"),
    ("claimed_total_base", "Claimed (base)"),
    ("verified_total_base", "Verified (base)"),
    ("approved_total_base", "Approved (base)"),
    ("total_deductions_base", "Deductions (base)"),
    ("net_payable_base", "Net Payable (base)"),
    ("paid_base", "Paid (base)"),
    ("balance_payable_base", "Balance (base)"),
    ("percent_billed", "% Billed"),
    ("percent_approved", "% Approved"),
    ("remarks", "Remarks"),
]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except Exception:
            return str(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def rows_to_csv(columns: List[Tuple[str, str]], rows: List[Dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([label for _key, label in columns])
    for row in rows or []:
        writer.writerow([_cell(row.get(key)) for key, _label in columns])
    return buffer.getvalue()


def rows_to_xlsx(columns: List[Tuple[str, str]], rows: List[Dict[str, Any]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append([label for _key, label in columns])
    for row in rows or []:
        ws.append([_cell(row.get(key)) for key, _label in columns])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def rows_to_pdf(title: str, columns: List[Tuple[str, str]], rows: List[Dict[str, Any]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), title=title)
    data = [[label for _key, label in columns]]
    for row in rows or []:
        data.append([_cell(row.get(key)) for key, _label in columns])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
    ]))
    doc.build([table])
    return buffer.getvalue()


def export_response(name: str, columns: List[Tuple[str, str]], rows: List[Dict[str, Any]], fmt: str) -> Response:
    if fmt == "csv":
        return Response(
            content=rows_to_csv(columns, rows), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )
    if fmt == "xlsx":
        try:
            content = rows_to_xlsx(columns, rows)
        except ImportError:
            raise HTTPException(status_code=501, detail="XLSX export is not available on this server")
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )
    try:
        content = rows_to_pdf(name, columns, rows)
    except ImportError:
        raise HTTPException(status_code=501, detail="PDF export is not available on this server")
    return Response(
        content=content, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'},
    )
