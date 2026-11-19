"""DEF-4: the target billing and subscription grants, defined once (owner decisions 2026-11-19).

Used by the real-MongoDB tests (to model a database after the grant step), by the P-05
read-only reading (to count who would gain or lose access) and by the migration plan in
``docs/DEF4_BILLING_PERMISSION_SEPARATION.md``. **Nothing in application code applies these
grants**: production roles are changed only by an approved migration after P-05, the
authorised security review, staging evidence and a recorded release decision.

Principles (Decisions 1 to 3):

- financial views (records, invoices, history, usage) and operational status are separate
  names, and a view never implies a mutation;
- Organisation Admin and the billing administrator are granted the views explicitly before
  any alias is removed; the billing administrator alone also holds the subscription
  mutations, and only Super Admin manages plans and entitlements platform-wide as today;
- Project Admin sees its project's operational subscription status (subscription,
  entitlements, features, expiry) and nothing financial; Project User only what
  ``/plan-settings/effective-services`` returns to everyone (service availability);
- every other built-in role gets nothing billing-related.
"""

from __future__ import annotations

from typing import Dict, FrozenSet

#: Financial reads: billing records, review queue, summary, invoices, history, usage.
FINANCIAL_VIEW_PERMISSIONS: FrozenSet[str] = frozenset(
    {
        "billing.record.view",
        "billing.plan.view",
        "billing.invoice.view",
        "billing.invoice.download",
        "subscription.history.view",
        "subscription.usage.view",
    }
)

#: Operational status only: the subscription, entitlements, features and expiry.
STATUS_VIEW_PERMISSIONS: FrozenSet[str] = frozenset({"subscription.status.view"})

#: Commercially binding changes to a subscription.
SUBSCRIPTION_MUTATION_PERMISSIONS: FrozenSet[str] = frozenset(
    {
        "subscription.upgrade",
        "subscription.downgrade",
        "subscription.cancel",
        "subscription.addon.manage",
        "subscription.trial.manage",
    }
)

#: The grant step, per built-in role key: names to add (never to remove).
TARGET_ROLE_GRANTS: Dict[str, FrozenSet[str]] = {
    "orgadmin": FINANCIAL_VIEW_PERMISSIONS | STATUS_VIEW_PERMISSIONS,
    "contraclaim_billing_admin": FINANCIAL_VIEW_PERMISSIONS | STATUS_VIEW_PERMISSIONS | SUBSCRIPTION_MUTATION_PERMISSIONS,
    "projectadmin": STATUS_VIEW_PERMISSIONS,
}

__all__ = [
    "FINANCIAL_VIEW_PERMISSIONS",
    "STATUS_VIEW_PERMISSIONS",
    "SUBSCRIPTION_MUTATION_PERMISSIONS",
    "TARGET_ROLE_GRANTS",
]
