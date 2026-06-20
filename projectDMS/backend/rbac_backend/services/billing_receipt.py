"""Billing receipt / tax-invoice rendering (Phase 4).

Pure builders: assemble a structured receipt from a *paid* billing record (plus
the subscription / plan / organization it relates to) and render a
print-optimized HTML invoice. There is no external PDF dependency — the browser's
"Save as PDF" produces the document, and the structured data is unit-testable.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Dict, Optional

from ..core.config import settings

SELLER_NAME = "Contraclaim DMS"


def _seller_config() -> Dict[str, Any]:
    """GST supplier details for the tax invoice (overridable via settings)."""
    gstin = getattr(settings, "BILLING_SELLER_GSTIN", "") or "29AABCU9603R1ZJ"
    return {
        "name": SELLER_NAME,
        "gstin": gstin,
        "state_code": gstin[:2],
        "address": getattr(settings, "BILLING_SELLER_ADDRESS", "")
        or "Bengaluru, Karnataka 560001, India",
        "sac": getattr(settings, "BILLING_SAC_CODE", "") or "997331",  # software licensing
        "gst_rate": float(getattr(settings, "BILLING_GST_RATE", 0) or 18.0),
    }


def _money(amount_minor: Optional[int], currency: str = "INR") -> str:
    if amount_minor is None:
        return f"{currency} 0.00"
    return f"{currency} {int(amount_minor) / 100:,.2f}"


def _fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %H:%M UTC")
    return str(value or "")


def build_receipt(
    record: Dict[str, Any],
    subscription: Optional[Dict[str, Any]] = None,
    plan: Optional[Dict[str, Any]] = None,
    organization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the structured receipt fields from a billing record + context."""
    record = record or {}
    subscription = subscription or {}
    plan = plan or {}
    organization = organization or {}

    plan_name = plan.get("name") or subscription.get("plan_code") or record.get("plan_code") or "Subscription"
    period = subscription.get("billing_period") or record.get("billing_period")
    description = f"{plan_name}" + (f" ({period})" if period else "")
    currency = record.get("currency") or "INR"
    receipt_no = str(
        record.get("gateway_payment_id") or record.get("_id") or record.get("event_id") or ""
    )

    return {
        "receipt_no": receipt_no,
        "issued_at": _fmt_date(record.get("created_at")),
        "seller_name": SELLER_NAME,
        "buyer_name": organization.get("name") or str(record.get("organization_id") or ""),
        "buyer_gstin": organization.get("gstin") or organization.get("tax_id"),
        "description": description,
        "amount_minor": record.get("amount_minor"),
        "currency": currency,
        "amount_display": _money(record.get("amount_minor"), currency),
        "status": record.get("record_status") or "",
        "payment_id": record.get("gateway_payment_id"),
        "provider": record.get("provider"),
    }


def receipt_to_html(data: Dict[str, Any]) -> str:
    """Render a self-contained, print-optimized HTML invoice."""
    g = lambda k: escape(str(data.get(k) or ""))  # noqa: E731 - tiny local escaper
    gstin_row = (
        f'<div class="muted">GSTIN: {g("buyer_gstin")}</div>' if data.get("buyer_gstin") else ""
    )
    payment_row = (
        f'<tr><td class="muted">Payment reference</td><td>{g("payment_id")}</td></tr>'
        if data.get("payment_id") else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Receipt {g("receipt_no")}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font-family: system-ui, Arial, sans-serif; color: #1a1a1a; margin: 0; padding: 32px; }}
  .sheet {{ max-width: 640px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .muted {{ color: #666; font-size: 13px; }}
  .row {{ display: flex; justify-content: space-between; gap: 24px; margin-top: 24px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 24px; }}
  td, th {{ text-align: left; padding: 8px 0; border-bottom: 1px solid #eee; font-size: 14px; }}
  td:last-child, th:last-child {{ text-align: right; }}
  .total td {{ font-weight: 700; font-size: 16px; border-bottom: none; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; background: #e7f6ec; color: #137a3a; font-size: 12px; }}
  .foot {{ margin-top: 32px; }}
  @media print {{ body {{ padding: 0; }} }}
</style></head>
<body><div class="sheet">
  <h1>{g("seller_name")}</h1>
  <div class="muted">Payment Receipt / Tax Invoice</div>
  <div class="row">
    <div>
      <div class="muted">Billed to</div>
      <div>{g("buyer_name")}</div>
      {gstin_row}
    </div>
    <div style="text-align:right">
      <div class="muted">Receipt no.</div>
      <div>{g("receipt_no")}</div>
      <div class="muted" style="margin-top:8px">Issued</div>
      <div>{g("issued_at")}</div>
    </div>
  </div>
  <table>
    <thead><tr><th>Description</th><th>Amount</th></tr></thead>
    <tbody>
      <tr><td>{g("description")}</td><td>{g("amount_display")}</td></tr>
      {payment_row}
      <tr class="total"><td>Total paid</td><td>{g("amount_display")}</td></tr>
    </tbody>
  </table>
  <div class="foot">
    <span class="badge">{g("status")}</span>
    <p class="muted">Amounts are inclusive of taxes as applicable. This is a
    computer-generated receipt for a payment captured via {g("provider")}.</p>
  </div>
</div></body></html>"""


# --- GST tax invoice ------------------------------------------------------


def financial_year(value: Any) -> str:
    """Indian financial year (Apr–Mar) label for a date, e.g. '2026-27'."""
    dt = value if isinstance(value, datetime) else datetime.utcnow()
    start = dt.year if dt.month >= 4 else dt.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def format_invoice_number(fy: str, seq: int) -> str:
    """Sequential, financial-year-scoped invoice number, e.g. 'INV/2026-27/00001'."""
    return f"INV/{fy}/{int(seq):05d}"


def _split_gst(total_minor: int, rate: float, intra_state: bool) -> Dict[str, int]:
    """Back-compute the GST breakdown from a tax-inclusive total (minor units)."""
    total = int(total_minor or 0)
    taxable = round(total / (1 + rate / 100.0)) if rate else total
    tax = total - taxable
    if intra_state:
        cgst = tax // 2
        return {"taxable_minor": taxable, "cgst_minor": cgst, "sgst_minor": tax - cgst,
                "igst_minor": 0, "tax_minor": tax}
    return {"taxable_minor": taxable, "cgst_minor": 0, "sgst_minor": 0,
            "igst_minor": tax, "tax_minor": tax}


def build_tax_invoice(
    record: Dict[str, Any],
    subscription: Optional[Dict[str, Any]] = None,
    plan: Optional[Dict[str, Any]] = None,
    organization: Optional[Dict[str, Any]] = None,
    invoice_number: Optional[str] = None,
    seller: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble a GST tax invoice (HSN/SAC + CGST/SGST or IGST split)."""
    data = build_receipt(record, subscription, plan, organization)
    seller = seller or _seller_config()
    organization = organization or {}

    buyer_gstin = data.get("buyer_gstin")
    buyer_state = (buyer_gstin or "")[:2] or organization.get("state_code")
    intra_state = bool(buyer_state and buyer_state == seller["state_code"])
    rate = seller["gst_rate"]
    split = _split_gst(int(record.get("amount_minor") or 0), rate, intra_state)
    currency = data["currency"]

    data.update({
        "invoice_number": invoice_number or data["receipt_no"],
        "seller_gstin": seller["gstin"],
        "seller_address": seller["address"],
        "sac_code": seller["sac"],
        "place_of_supply": buyer_state or seller["state_code"],
        "gst_rate": rate,
        "intra_state": intra_state,
        "taxable_display": _money(split["taxable_minor"], currency),
        "cgst_display": _money(split["cgst_minor"], currency),
        "sgst_display": _money(split["sgst_minor"], currency),
        "igst_display": _money(split["igst_minor"], currency),
        "tax_display": _money(split["tax_minor"], currency),
        **split,
    })
    return data


def tax_invoice_to_html(data: Dict[str, Any]) -> str:
    """Render a print-optimized GST tax invoice."""
    g = lambda k: escape(str(data.get(k) or ""))  # noqa: E731
    rate = data.get("gst_rate") or 0
    half = rate / 2
    if data.get("intra_state"):
        tax_rows = (
            f'<tr><td class="muted">CGST @ {half:g}%</td><td>{g("cgst_display")}</td></tr>'
            f'<tr><td class="muted">SGST @ {half:g}%</td><td>{g("sgst_display")}</td></tr>'
        )
    else:
        tax_rows = f'<tr><td class="muted">IGST @ {rate:g}%</td><td>{g("igst_display")}</td></tr>'
    buyer_gstin_row = (
        f'<div class="muted">GSTIN: {g("buyer_gstin")}</div>' if data.get("buyer_gstin") else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Tax Invoice {g("invoice_number")}</title>
<style>
  body {{ font-family: system-ui, Arial, sans-serif; color: #1a1a1a; margin: 0; padding: 32px; }}
  .sheet {{ max-width: 680px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin: 0; }}
  .muted {{ color: #666; font-size: 13px; }}
  .row {{ display: flex; justify-content: space-between; gap: 24px; margin-top: 20px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
  td, th {{ text-align: left; padding: 8px 0; border-bottom: 1px solid #eee; font-size: 14px; }}
  td:last-child, th:last-child {{ text-align: right; }}
  .total td {{ font-weight: 700; font-size: 16px; }}
  @media print {{ body {{ padding: 0; }} }}
</style></head>
<body><div class="sheet">
  <div class="row" style="margin-top:0">
    <div><h1>{g("seller_name")}</h1><div class="muted">Tax Invoice</div></div>
    <div style="text-align:right">
      <div class="muted">Invoice no.</div><div>{g("invoice_number")}</div>
      <div class="muted" style="margin-top:6px">Date</div><div>{g("issued_at")}</div>
    </div>
  </div>
  <div class="row">
    <div>
      <div class="muted">Supplier</div>
      <div>{g("seller_name")}</div>
      <div class="muted">{g("seller_address")}</div>
      <div class="muted">GSTIN: {g("seller_gstin")}</div>
    </div>
    <div style="text-align:right">
      <div class="muted">Billed to</div>
      <div>{g("buyer_name")}</div>
      {buyer_gstin_row}
      <div class="muted">Place of supply: {g("place_of_supply")}</div>
    </div>
  </div>
  <table>
    <thead><tr><th>Description</th><th>SAC</th><th>Taxable value</th></tr></thead>
    <tbody>
      <tr><td>{g("description")}</td><td>{g("sac_code")}</td><td>{g("taxable_display")}</td></tr>
    </tbody>
  </table>
  <table>
    <tbody>
      <tr><td class="muted">Taxable value</td><td>{g("taxable_display")}</td></tr>
      {tax_rows}
      <tr class="total"><td>Total (incl. GST)</td><td>{g("amount_display")}</td></tr>
    </tbody>
  </table>
  <p class="muted" style="margin-top:24px">Computer-generated tax invoice. Payment
  captured via {g("provider")}{(" · " + g("payment_id")) if data.get("payment_id") else ""}.</p>
</div></body></html>"""
