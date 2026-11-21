"""P-05 ``payment_integrity_impact`` (R6 closure, owner decision 2026-11-21): a read-only count,
before release, of legacy gateway bindings by provenance, duplicate payments, overlapping
governing subscriptions, cancellations the gateway may never have heard of, governing rows
without an activation date, and unfinished payment work. Counts and organisation ids only.

Opt-in: ``CLAIM_BENCH_MONGODB_URI``; CI real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env

pytestmark = pytest.mark.integration

ORG = "org-p05pi"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _sub(sub_id: str, project: str, *, status: str = "active", gid: str = "", **extra: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    row = {"_id": sub_id, "organization_id": ORG, "project_id": project, "package_id": None, "plan_code": "dms_starter",
           "status": status, "billing_status": "active", "governing_since": now - timedelta(days=2), **extra}
    if gid:
        row["payment_gateway_subscription_id"] = gid
        row.setdefault("payment_provider", "razorpay")
    return row


def _read(database: str) -> Dict[str, Any]:
    from pymongo import MongoClient

    from rbac_backend.tests.integration.test_claim_arbitration_readings_mongo import _reader, _uri

    reader = _reader()
    client = MongoClient(_uri(), serverSelectionTimeoutMS=5_000)
    try:
        sync_db = client[database]
        counts = {name: sync_db[name].count_documents({}) for name in ("subscriptions", "billing_records", "audit_events")}
        reading = reader._read_payment_integrity_impact(reader.ReadOnlyDatabase(sync_db))
        assert {name: sync_db[name].count_documents({}) for name in counts} == counts, "read-only"
        return reading
    finally:
        client.close()


def test_the_payment_integrity_reading_classifies_and_counts_without_identifiers(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            db = env.db
            baseline = _read(db.name)  # the environment seeds other organisations' subscriptions
            await db.subscriptions.insert_many([
                _sub("pi-verified", "pp-1", gid="gw_secret_1", payment_gateway_bound_by="checkout"),
                _sub("pi-dup-a", "pp-2", status="cancelled", gid="gw_secret_dup", payment_gateway_bound_by="checkout"),
                _sub("pi-dup-b", "pp-3", status="cancelled", gid="gw_secret_dup"),
                _sub("pi-put", "pp-4", gid="gw_secret_put"),
                _sub("pi-recon", "pp-5", gid="gw_secret_recon", payment_gateway_reconciled_at=datetime.utcnow()),
                _sub("pi-unverified", "pp-6", status="cancelled", gid="gw_secret_un"),  # no history: no gateway evidence
                _sub("pi-cancel-ok", "pp-7", status="cancelled", gid="gw_secret_ok", payment_gateway_bound_by="checkout"),
                # Two gateway-bound subscriptions governing one scope: possible double billing.
                _sub("pi-over-a", "pp-8", gid="gw_secret_o1", payment_gateway_bound_by="checkout"),
                _sub("pi-over-b", "pp-8", gid="gw_secret_o2", payment_gateway_bound_by="checkout",
                     governing_since=datetime.utcnow() - timedelta(hours=1)),
                # Governing without an activation date: alone, and with another.
                _sub("pi-old-alone", "pp-9", governing_since=None),
                _sub("pi-old-x", "pp-10", governing_since=None),
                _sub("pi-old-y", "pp-10", governing_since=None),
                _sub("pi-trial", "pp-11", status="trial", conversion={"state": "pending_payment"}),
                _sub("pi-free", "pp-12", billing_status="no_charge_override"),
                # Marked as checkout-bound, but no checkout ever recorded it: unverified.
                _sub("pi-claims-checkout", "pp-13", gid="gw_secret_claim", payment_gateway_bound_by="checkout"),
                # An ended subscription beside a live one is not an overlap.
                _sub("pi-ended", "pp-14", gid="gw_secret_end", payment_gateway_bound_by="checkout",
                     ends_at=datetime.utcnow() - timedelta(days=3)),
                _sub("pi-live", "pp-14", gid="gw_secret_live", payment_gateway_bound_by="checkout"),
            ])
            await db.audit_events.insert_many([
                {"action": "subscription.checkout_started", "resource_id": rid}
                for rid in ("pi-verified", "pi-dup-a", "pi-cancel-ok", "pi-over-a", "pi-over-b", "pi-ended", "pi-live")
            ] + [{"action": "subscription.updated", "resource_id": "pi-put",
                  "after": {"payment_gateway_subscription_id": "gw_secret_put"}}])
            await db.subscription_history.insert_one({"subscription_id": "pi-cancel-ok", "to_status": "cancelled",
                                                      "change_type": "cancellation"})
            await db.billing_records.insert_many([
                {"organization_id": ORG, "provider": "razorpay", "gateway_payment_id": "pay_secret_1", "event_type": "payment.succeeded"},
                {"organization_id": ORG, "provider": "razorpay", "gateway_payment_id": "pay_secret_1", "event_type": "payment.succeeded"},
                {"organization_id": ORG, "provider": "razorpay", "gateway_payment_id": "pay_secret_2", "event_type": "payment.succeeded",
                 "requires_review": True},
            ])
            await db.subscription_operations.insert_one({"_id": "op_pi", "organization_id": ORG, "status": "needs_review"})

            reading = _read(db.name)
            delta = {key: reading[key] - baseline[key] for key in reading if isinstance(reading[key], int)}
            classes = {name: reading["gateway_bindings_by_class"][name] - baseline["gateway_bindings_by_class"][name]
                       for name in reading["gateway_bindings_by_class"]}
            assert classes == {"verified_checkout": 6, "verified_provider_reconciliation": 1, "conflicting": 3,
                               "unverified": 2}, reading
            assert reading["gateway_bindings_by_class_and_organization"]["conflicting"] == {ORG: 3}
            assert delta["duplicate_payment_groups"] == 1 and delta["payments_refused_for_review"] == 1
            assert delta["overlapping_gateway_bound_scopes"] == 1
            assert delta["overlapping_governing_scopes"] == 2  # pp-8 and pp-10
            assert delta["cancelled_bound_without_gateway_evidence"] == 3  # both duplicates and the unverified one
            assert delta["governing_without_activation_date_alone"] == 1
            assert delta["governing_without_activation_date_with_others"] == 2
            assert delta["operations_needing_review"] == 1
            assert delta["trial_conversions_awaiting_payment"] == 1 and delta["no_charge_conversions"] == 1
            assert "secret" not in repr(reading) and "pi-" not in repr(reading), "no gateway, payment or subscription ids"

    _run(scenario())


def _legacy(database: str) -> Dict[str, Any]:
    from pymongo import MongoClient

    from rbac_backend.tests.integration.test_claim_arbitration_readings_mongo import _reader, _uri

    reader = _reader()
    client = MongoClient(_uri(), serverSelectionTimeoutMS=5_000)
    try:
        sync_db = client[database]
        names = ("subscriptions", "billing_records", "audit_events", "subscription_operations")
        counts = {name: sync_db[name].count_documents({}) for name in names}
        reading = reader._read_legacy_binding_and_cancellation_reconciliation(reader.ReadOnlyDatabase(sync_db))
        assert {name: sync_db[name].count_documents({}) for name in names} == counts, "read-only"
        return reading
    finally:
        client.close()


def test_workstream_3_classifies_legacy_bindings_and_old_cancellations_by_organisation(monkeypatch) -> None:
    """Workstream 3: bindings as verified / conflicting / unverified / requires_reconciliation from
    checkout provenance, gateway records, audit events and payment history; cancelled bound
    subscriptions by what the database shows about the provider still charging."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            db = env.db
            now = datetime.utcnow()
            baseline = _legacy(db.name)  # the environment seeds other organisations' subscriptions
            await db.subscriptions.insert_many([
                _sub("lb-verified", "p1", gid="gw-lb-1", payment_gateway_bound_by="checkout"),
                _sub("lb-reconciled", "p2", gid="gw-lb-2", payment_gateway_reconciled_at=now),
                _sub("lb-unverified", "p3", gid="gw-lb-3"),
                _sub("lb-admin-bound", "p4", gid="gw-lb-4", payment_gateway_bound_by="admin_update"),
                _sub("lb-paid-elsewhere", "p5", gid="gw-lb-5", payment_gateway_bound_by="checkout"),
                _sub("lb-mismatch", "p6", gid="gw-lb-6", payment_gateway_bound_by="checkout"),
                _sub("lb-open-op", "p7", gid="gw-lb-7", payment_gateway_bound_by="checkout"),
                # Old cancellations.
                _sub("lb-c-charged", "c1", gid="gw-lb-c1", status="cancelled", cancelled_at=now - timedelta(days=40),
                     cancellation_reason="replaced_by_plan_settings"),
                _sub("lb-c-legacy-ps", "c2", gid="gw-lb-c2", status="cancelled", cancelled_at=now - timedelta(days=40),
                     cancellation_reason="replaced_by_plan_settings"),
                _sub("lb-c-confirmed", "c3", gid="gw-lb-c3", status="cancelled", cancelled_at=now - timedelta(days=5),
                     gateway_cancellation={"mode": "cycle_end", "request_confirmed": True, "effective_at": now}),
                _sub("lb-c-other", "c4", gid="gw-lb-c4", status="cancelled", cancelled_at=now - timedelta(days=5),
                     cancellation_reason="customer_request"),
            ])
            await db.audit_events.insert_many([
                {"action": "subscription.checkout_started", "resource_id": rid}
                for rid in ("lb-verified", "lb-paid-elsewhere", "lb-mismatch", "lb-open-op")])
            await db.billing_records.insert_many([
                {"subscription_id": "lb-verified", "gateway_subscription_id": "gw-lb-1", "record_status": "paid",
                 "created_at": now - timedelta(days=3), "gateway_payment_id": "pay-lb-1", "event_type": "payment.succeeded"},
                {"subscription_id": "someone-else", "gateway_subscription_id": "gw-lb-5", "record_status": "paid",
                 "created_at": now - timedelta(days=3), "gateway_payment_id": "pay-lb-5", "event_type": "payment.succeeded"},
                {"subscription_id": "lb-mismatch", "gateway_subscription_id": "gw-lb-6", "record_status": "amount_mismatch",
                 "created_at": now - timedelta(days=3), "gateway_payment_id": "pay-lb-6", "event_type": "payment.succeeded"},
                {"subscription_id": "lb-c-charged", "gateway_subscription_id": "gw-lb-c1", "record_status": "paid",
                 "created_at": now - timedelta(days=10), "gateway_payment_id": "pay-lb-c1", "event_type": "payment.succeeded"},
            ])
            await db.subscription_operations.insert_one({
                "_id": "op-lb-7", "kind": "cancellation", "status": "needs_review", "organization_id": ORG,
                "items": [{"subscription_id": "lb-open-op", "gateway": "outcome_unknown"}]})

            reading = _legacy(db.name)
            by_org = reading["bindings_by_category_and_organization"]
            assert by_org["verified"][ORG] == 2, by_org             # checkout-audited; provider reconciliation recorded
            assert by_org["unverified"][ORG] == 5, by_org           # lb-unverified and the four cancelled rows
            assert by_org["conflicting"][ORG] == 2                  # admin-bound, payment recorded elsewhere
            assert by_org["requires_reconciliation"][ORG] == 2      # amount mismatch, open operation
            cancelled = reading["cancelled_bound_by_class_and_organization"]
            assert cancelled["charged_after_cancellation"][ORG] == 1
            assert cancelled["historical_plan_settings_without_gateway_evidence"][ORG] == 1
            assert cancelled["confirmed_at_gateway"][ORG] == 1
            assert cancelled["other_without_gateway_evidence"][ORG] == 1
            assert reading["cancellations_needing_a_provider_check"] - baseline["cancellations_needing_a_provider_check"] == 3
            assert reading["verified_bindings_with_payment_history"] >= 1
            text = repr(reading)
            for secret in ("gw-lb", "pay-lb", "lb-verified", "lb-c-charged", "op-lb"):
                assert secret not in text, secret

    _run(scenario())
