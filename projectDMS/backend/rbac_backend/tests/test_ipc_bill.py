"""IPC / Contractor Bill Register roll-ups (per-component multi-currency)."""

from rbac_backend.models.ipc_bill import CurrencyAmount, IPCComponents
from rbac_backend.services.ipc_bill_service import (
    component_base,
    decorate,
    ipc_summary,
    perspective_net_base,
)


def _ca(currency, rate, amount):
    return {"currency": currency, "conversion_rate": rate, "amount": amount}


def test_component_base_converts_each_currency():
    # 100 USD @83 + 50 EUR @90 = 8300 + 4500 = 12,800
    items = [_ca("USD", 83.0, 100), _ca("EUR", 90.0, 50)]
    assert component_base(items) == 12_800.0
    assert component_base([]) == 0.0


def test_net_payable_is_gross_minus_deductions_in_base():
    comp = {
        "gross": [_ca("USD", 83.0, 1000)],          # 83,000
        "deductions": [_ca("INR", 1.0, 3000)],      # 3,000
        "recovery_of_advances": [_ca("INR", 1.0, 2000)],
        "it_tax": [_ca("INR", 1.0, 1000)],
        "gst": [_ca("INR", 1.0, 1500)],
        "withheld": [_ca("INR", 1.0, 500)],
        "penalties_ld": [_ca("INR", 1.0, 0)],
    }
    # 83,000 - (3,000+2,000+1,000+1,500+500+0) = 83,000 - 8,000 = 75,000
    assert perspective_net_base(comp) == 75_000.0


def test_decorate_derives_totals_balance_and_percentages():
    ipc = {
        "original_contract_value": 1_000_000,
        "contractor_claimed": {"gross": [_ca("USD", 83.0, 1200)]},   # 99,600 claimed
        "employer_approved": {
            "gross": [_ca("USD", 83.0, 1000)],                       # 83,000 approved
            "deductions": [_ca("INR", 1.0, 8000)],                   # net 75,000
        },
        "actually_paid": {"gross": [_ca("INR", 1.0, 60000)]},        # paid 60,000
    }
    d = decorate(ipc)
    assert d["claimed_total_base"] == 99_600.0
    assert d["approved_total_base"] == 83_000.0
    assert d["net_payable_base"] == 75_000.0
    assert d["paid_base"] == 60_000.0
    assert d["balance_payable_base"] == 15_000.0   # 75,000 - 60,000
    assert d["percent_billed"] == round(99_600 / 1_000_000 * 100, 4)
    assert d["percent_approved"] == round(83_000 / 1_000_000 * 100, 4)


def test_summary_aggregates_and_counts():
    ipcs = [
        {"status": "approved", "original_contract_value": 1_000_000,
         "contractor_claimed": {"gross": [_ca("INR", 1.0, 100000)]},
         "employer_approved": {"gross": [_ca("INR", 1.0, 90000)]},
         "actually_paid": {"gross": [_ca("INR", 1.0, 50000)]}},
        {"status": "paid",
         "contractor_claimed": {"gross": [_ca("INR", 1.0, 40000)]},
         "employer_approved": {"gross": [_ca("INR", 1.0, 40000)]},
         "actually_paid": {"gross": [_ca("INR", 1.0, 40000)]}},
        {"status": "submitted",
         "contractor_claimed": {"gross": [_ca("INR", 1.0, 10000)]}},
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


def test_currency_amount_validation():
    import pytest
    ok = CurrencyAmount(currency="usd", conversion_rate=83.0, amount=100)
    assert ok.currency == "USD"
    with pytest.raises(Exception):
        CurrencyAmount(currency="USD", conversion_rate=0, amount=1)
    # IPCComponents default to empty lists.
    assert IPCComponents().gross == []
