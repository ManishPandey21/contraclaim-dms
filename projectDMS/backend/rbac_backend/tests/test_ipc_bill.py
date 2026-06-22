"""IPC / Contractor Bill Register roll-ups.

New function-first model: gross comes from line items (claimed/verified/approved
columns), deductions are captured per perspective, payments are discrete records.
"""

from rbac_backend.models.ipc_bill import CurrencyAmount, IPCLineItem, IPCBill
from rbac_backend.services.ipc_bill_service import (
    component_base,
    decorate,
    ipc_summary,
    line_total,
    payments_base,
    perspective_deductions_base,
)


def _li(currency, rate, claimed, verified, approved):
    return {"currency": currency, "conversion_rate": rate,
            "claimed": claimed, "verified": verified, "approved": approved}


def _ca(currency, rate, amount):
    return {"currency": currency, "conversion_rate": rate, "amount": amount}


def test_line_total_converts_each_column_to_base():
    items = [_li("USD", 83.0, 100, 90, 90), _li("EUR", 90.0, 50, 50, 40)]
    # claimed: 100*83 + 50*90 = 8300 + 4500 = 12,800
    assert line_total(items, "claimed") == 12_800.0
    # approved: 90*83 + 40*90 = 7470 + 3600 = 11,070
    assert line_total(items, "approved") == 11_070.0
    assert line_total([], "claimed") == 0.0


def test_perspective_deductions_sums_all_components():
    persp = {
        "recovery_of_advances": [_ca("INR", 1.0, 2000)],
        "deductions": [_ca("INR", 1.0, 3000)],
        "it_tax": [_ca("INR", 1.0, 1000)],
        "gst": [_ca("INR", 1.0, 1500)],
        "withheld": [_ca("INR", 1.0, 500)],
        "penalties_ld": [_ca("INR", 1.0, 0)],
    }
    assert perspective_deductions_base(persp) == 8_000.0
    assert perspective_deductions_base(None) == 0.0


def test_payments_base_sums_records():
    pays = [_ca("USD", 83.0, 1000), _ca("INR", 1.0, 60000)]
    assert payments_base(pays) == 1000 * 83.0 + 60000


def test_decorate_derives_totals_balance_and_percentages():
    ipc = {
        "original_contract_value": 1_000_000,
        "line_items": [_li("INR", 1.0, 99600, 90000, 83000)],
        "deductions": {
            "employer_approved": {
                "recovery_of_advances": [_ca("INR", 1.0, 5000)],
                "deductions": [_ca("INR", 1.0, 3000)],
            }
        },
        "payments": [_ca("INR", 1.0, 60000)],
    }
    d = decorate(ipc)
    assert d["claimed_total_base"] == 99_600.0
    assert d["approved_total_base"] == 83_000.0
    assert d["total_deductions_base"] == 8_000.0
    assert d["net_payable_base"] == 75_000.0     # 83,000 - 8,000
    assert d["paid_base"] == 60_000.0
    assert d["balance_payable_base"] == 15_000.0  # 75,000 - 60,000
    assert d["percent_billed"] == round(99_600 / 1_000_000 * 100, 4)
    assert d["percent_approved"] == round(83_000 / 1_000_000 * 100, 4)


def test_summary_aggregates_and_counts():
    ipcs = [
        {"status": "approved", "original_contract_value": 1_000_000,
         "line_items": [_li("INR", 1.0, 100000, 95000, 90000)],
         "deductions": {"employer_approved": {"deductions": [_ca("INR", 1.0, 0)]}},
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
    ok = CurrencyAmount(currency="usd", conversion_rate=83.0, amount=100)
    assert ok.currency == "USD"
    with pytest.raises(Exception):
        IPCLineItem(currency="USD", conversion_rate=0, claimed=1)
    # A fresh bill has empty line items and a default deductions structure.
    bill = IPCBill(project_id="p1")
    assert bill.line_items == []
    assert bill.deductions.employer_approved.deductions == []
    assert bill.payments == []
