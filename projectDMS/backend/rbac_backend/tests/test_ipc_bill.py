"""IPC / Contractor Bill Register roll-ups.

Function-first model: gross comes from line items (claimed/verified/approved
columns); each deduction component is likewise a list of lines carrying the
three perspective columns; payments are discrete records.
"""

from rbac_backend.models.ipc_bill import IPCDeductionLine, IPCLineItem, IPCBill
from rbac_backend.services.ipc_bill_service import (
    decorate,
    deductions_col_base,
    ipc_summary,
    line_total,
    payments_base,
)


def _li(currency, rate, claimed, verified, approved):
    return {"currency": currency, "conversion_rate": rate,
            "claimed": claimed, "verified": verified, "approved": approved}


# Deduction lines share the same column shape as line items.
_dl = _li


def _ca(currency, rate, amount):
    return {"currency": currency, "conversion_rate": rate, "amount": amount}


def test_line_total_converts_each_column_to_base():
    items = [_li("USD", 83.0, 100, 90, 90), _li("EUR", 90.0, 50, 50, 40)]
    assert line_total(items, "claimed") == 12_800.0           # 100*83 + 50*90
    assert line_total(items, "approved") == 11_070.0          # 90*83 + 40*90
    assert line_total([], "claimed") == 0.0


def test_deductions_col_base_sums_components_per_column():
    ded = {
        "recovery_of_advances": [_dl("INR", 1.0, 2200, 2100, 2000)],
        "withheld": [_dl("INR", 1.0, 600, 550, 500)],
        "penalties_ld": [_dl("INR", 1.0, 0, 0, 0)],
        "deductions": [_dl("INR", 1.0, 3300, 3100, 3000)],   # IT, Labour Cess
        "gst": [_dl("INR", 1.0, 1600, 1550, 1500)],
    }
    assert deductions_col_base(ded, "approved") == 7_000.0    # 2000+500+0+3000+1500
    assert deductions_col_base(ded, "claimed") == 7_700.0     # 2200+600+0+3300+1600
    assert deductions_col_base(None, "approved") == 0.0


def test_payments_base_sums_records():
    pays = [_ca("USD", 83.0, 1000), _ca("INR", 1.0, 60000)]
    assert payments_base(pays) == 1000 * 83.0 + 60000


def test_decorate_derives_totals_balance_and_percentages():
    ipc = {
        "original_contract_value": 1_000_000,
        "line_items": [_li("INR", 1.0, 99600, 90000, 83000)],
        "deductions": {
            "recovery_of_advances": [_dl("INR", 1.0, 6000, 5500, 5000)],
            "deductions": [_dl("INR", 1.0, 3500, 3200, 3000)],
        },
        "payments": [_ca("INR", 1.0, 60000)],
    }
    d = decorate(ipc)
    assert d["claimed_total_base"] == 99_600.0
    assert d["approved_total_base"] == 83_000.0
    assert d["total_deductions_base"] == 8_000.0     # approved col: 5000 + 3000
    assert d["net_payable_base"] == 75_000.0         # 83,000 - 8,000
    assert d["paid_base"] == 60_000.0
    assert d["balance_payable_base"] == 15_000.0     # 75,000 - 60,000
    assert d["percent_billed"] == round(99_600 / 1_000_000 * 100, 4)
    assert d["percent_approved"] == round(83_000 / 1_000_000 * 100, 4)


def test_summary_aggregates_and_counts():
    ipcs = [
        {"status": "approved", "original_contract_value": 1_000_000,
         "line_items": [_li("INR", 1.0, 100000, 95000, 90000)],
         "deductions": {"deductions": [_dl("INR", 1.0, 0, 0, 0)]},
         "payments": [_ca("INR", 1.0, 50000)]},
        {"status": "paid",
         "line_items": [_li("INR", 1.0, 40000, 40000, 40000)],
         "payments": [_ca("INR", 1.0, 40000)]},
        {"status": "submitted",
         "line_items": [_li("INR", 1.0, 10000, 0, 0)]},
    ]
    s = ipc_summary(ipcs, original_contract_value=1_000_000)
    assert s["total_ipcs"] == 3
    assert s["total_claimed_base"] == 150_000.0
    assert s["total_approved_base"] == 130_000.0
    assert s["total_paid_base"] == 90_000.0
    assert s["paid_count"] == 1
    assert s["approved_count"] == 2      # approved + paid
    assert s["pending_count"] == 1       # submitted
    assert s["percent_of_contract_billed"] == round(150_000 / 1_000_000 * 100, 4)


def test_model_validation_and_defaults():
    import pytest
    ok = IPCDeductionLine(currency="usd", conversion_rate=83.0, claimed=100, approved=90)
    assert ok.currency == "USD"
    with pytest.raises(Exception):
        IPCLineItem(currency="USD", conversion_rate=0, claimed=1)
    with pytest.raises(Exception):
        IPCDeductionLine(currency="USD", conversion_rate=0, approved=1)
    # A fresh bill has empty line items, empty deduction components and no payments.
    bill = IPCBill(project_id="p1")
    assert bill.line_items == []
    assert bill.deductions.recovery_of_advances == []
    assert bill.deductions.gst == []
    assert bill.payments == []
