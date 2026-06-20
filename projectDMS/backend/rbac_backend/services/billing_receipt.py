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

SELLER_NAME = "Contraclaim DMS"


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
