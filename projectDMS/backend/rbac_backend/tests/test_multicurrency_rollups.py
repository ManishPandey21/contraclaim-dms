"""Multi-currency roll-ups for Bank Guarantees and Variations (MC-3).

BG amounts and variation amounts may be denominated in several contract
currencies; registers and summaries must add up in the contract base currency,
using the award-fixed rates carried on each record.
"""

from rbac_backend.services.bank_guarantee_service import bg_summary, decorate as bg_decorate
from rbac_backend.services.variation_service import (
    decorate as var_decorate,
    variation_base_amounts,
    variation_summary,
)


# --- Bank Guarantees ------------------------------------------------------


def test_bg_summary_sums_in_base_currency():
    bgs = [
        {"bg_amount": 1000.0, "currency": "INR", "bg_status": "valid"},  # rate absent => 1.0
        {"bg_amount": 100.0, "currency": "USD", "conversion_rate": 83.0, "bg_status": "valid"},
    ]
    out = bg_summary(bgs)
    # 1000 + 100*83 = 9300
    assert out["total_bg_amount"] == 9300.0


def test_bg_decorate_adds_base_amount():
    b = bg_decorate({"bg_amount": 100.0, "currency": "USD", "conversion_rate": 83.0})
    assert b["bg_amount_base"] == 8300.0
    # No rate => base equals the raw amount.
    assert bg_decorate({"bg_amount": 500.0, "currency": "INR"})["bg_amount_base"] == 500.0


# --- Variations -----------------------------------------------------------


def test_variation_base_amounts_split_across_currencies():
    v = {
        "currency_amounts": [
            {"currency": "USD", "conversion_rate": 83.0, "submitted_amount": 100, "approved_amount": 90},
            {"currency": "EUR", "conversion_rate": 90.0, "submitted_amount": 50, "approved_amount": 50},
        ]
    }
    sub, app = variation_base_amounts(v)
    assert sub == round(100 * 83 + 50 * 90, 2)  # 12,800
    assert app == round(90 * 83 + 50 * 90, 2)   # 11,970


def test_variation_summary_uses_base_amounts():
    variations = [
        {
            "variation_type": "positive",
            "status": "approved",
            "currency_amounts": [
                {"currency": "USD", "conversion_rate": 83.0, "submitted_amount": 100, "approved_amount": 90},
                {"currency": "EUR", "conversion_rate": 90.0, "submitted_amount": 50, "approved_amount": 50},
            ],
        }
    ]
    s = variation_summary(variations, original_contract_value=1_000_000)
    assert s["total_submitted_amount"] == 12_800.0
    assert s["total_approved_amount"] == 11_970.0
    assert s["cumulative_approved_variation"] == 11_970.0
    assert s["revised_contract_value"] == 1_011_970.0


def test_variation_single_currency_unchanged():
    # Flat amounts (no split) are treated as base currency, behaviour preserved.
    v = var_decorate({"submitted_amount": 200.0, "approved_amount": 150.0})
    assert v["submitted_amount_base"] == 200.0
    assert v["approved_amount_base"] == 150.0
    assert v["difference_amount"] == 50.0
