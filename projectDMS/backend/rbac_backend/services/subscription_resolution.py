"""Deterministic, status-aware subscription selection (owner decision 2026-11-20).

Before this, entitlement resolution took any subscription of the scope in its period,
whatever its status, and preferred the most recently updated one: an ordinary edit of a
cancelled subscription (``auto_renew``, say) could switch a scope's entitlement off, and a
package row did not outrank a project row. Selection is now a pure function of the stored
subscriptions and the clock - never of ``updated_at`` - used by enforcement
(``EntitlementService``) and by the ``/plan-settings`` display alike.

**Tiers, most specific first.** Package (organisation, project, package) when a package is
named; project (organisation, project, no package) when a project is named; organisation
(organisation, no project, no package). The first tier holding a *governing* subscription
decides; later tiers are consulted only when a tier holds none.

**Status classes.**

=============  =====================================  ==============================
Class          Statuses                               Effect
=============  =====================================  ==============================
eligible       active, pilot, trial (not expired)     service per plan and features
restricted     archive / offboarding                  read-only / export-only (as before)
blocking       past_due, paused                       denies; **no fallback** to a broader tier
unknown        any other value                        denies (fail closed); no fallback
not governing  pending, cancelled, expired trial,     ignored: never shadows anything
               not started, ended
=============  =====================================  ==============================

**Fallback (explicit rule, not inferred from timestamps).** A cancelled (or never-paid
``pending``) project subscription does not govern its project, so the organisation's
subscription applies - the meaning ``/plan-settings`` "inherit" already gives a cancelled
project override. A project subscription that is past due or paused still governs: the
project is refused rather than silently moved onto the organisation's plan.

**Ambiguity.** Two or more governing subscriptions in the deciding tier cannot be ranked
safely (no contract rule says which plan wins): no subscription is selected and the reason is
``ambiguous_subscriptions`` (fail closed). P-05 counts such scopes before any release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

ELIGIBLE_STATUSES = frozenset({"active", "pilot", "trial"})
RESTRICTED_STATUSES = frozenset({"archive", "offboarding"})
BLOCKING_STATUSES = frozenset({"past_due", "paused"})
NOT_GOVERNING_STATUSES = frozenset({"pending", "cancelled"})

#: Statuses that govern their tier; the write side keeps at most one per tier (supersede).
GOVERNING_STATUSES = ELIGIBLE_STATUSES | RESTRICTED_STATUSES | BLOCKING_STATUSES

GOVERNING_CLASSES = frozenset({"eligible", "restricted", "blocking", "unknown", "invalid_period"})

_UNREADABLE = object()
_CONSIDERED_CAP = 10


@dataclass
class Resolution:
    """The outcome of selection: the governing subscription (or none) and why."""

    subscription: Optional[Dict[str, Any]]
    tier: Optional[str]
    status_class: Optional[str]
    reason: str
    considered: List[Tuple[str, str]] = field(default_factory=list)  # (subscription id, class)

    def diagnostic(self) -> Dict[str, Any]:
        # Capped (entitlement review F8): every cancelled row stays in the scope's history.
        return {
            "subscription_id": str((self.subscription or {}).get("_id") or (self.subscription or {}).get("id") or "") or None,
            "tier": self.tier,
            "status_class": self.status_class,
            "reason": self.reason,
            "considered": [{"subscription_id": sid, "class": cls} for sid, cls in self.considered[:_CONSIDERED_CAP]],
            "considered_total": len(self.considered),
        }


def _text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _instant(value: Any) -> Any:
    """A stored or normalised timestamp as naive UTC; ``None`` when absent, ``_UNREADABLE``
    when present but unreadable (entitlement review F3: an unreadable bound used to read as
    unbounded, failing open)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return _UNREADABLE
    else:
        return _UNREADABLE
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def classify(subscription: Dict[str, Any], now: datetime) -> str:
    """eligible, restricted, blocking, unknown, invalid_period, or a not-governing reason."""
    starts_at = _instant(subscription.get("starts_at"))
    ends_at = _instant(subscription.get("ends_at"))
    trial_bound = _instant(subscription.get("trial_ends_at"))
    if _UNREADABLE in (starts_at, ends_at) or (trial_bound is _UNREADABLE and str(subscription.get("status") or "").strip().lower() == "trial"):
        return "invalid_period"  # governs and denies: never read as unbounded
    if starts_at is not None and starts_at > now:
        return "not_started"
    if ends_at is not None and ends_at < now:
        return "ended"
    status = str(subscription.get("status") or "").strip().lower()
    if status in NOT_GOVERNING_STATUSES:
        return status
    if status == "trial":
        trial_ends_at = _instant(subscription.get("trial_ends_at"))
        if trial_ends_at is not None and trial_ends_at < now:
            return "trial_expired"
    if status in ELIGIBLE_STATUSES:
        return "eligible"
    if status in RESTRICTED_STATUSES:
        return "restricted"
    if status in BLOCKING_STATUSES:
        return "blocking"
    return "unknown"


def _tiers(organization_id: str, project_id: Optional[str], package_id: Optional[str]) -> List[Tuple[str, Any]]:
    tiers: List[Tuple[str, Any]] = []
    if package_id:
        tiers.append(("package", lambda s: _text(s.get("project_id")) == project_id and _text(s.get("package_id")) == package_id))
    if project_id:
        tiers.append(("project", lambda s: _text(s.get("project_id")) == project_id and _text(s.get("package_id")) is None))
    tiers.append(("organization", lambda s: _text(s.get("project_id")) is None and _text(s.get("package_id")) is None))
    return tiers


def select_subscription(
    candidates: Iterable[Dict[str, Any]],
    *,
    organization_id: Optional[str],
    project_id: Optional[str] = None,
    package_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Resolution:
    org = _text(organization_id)
    if not org:
        return Resolution(None, None, None, "no_organization")
    project, package = _text(project_id), _text(package_id)
    moment = now or datetime.utcnow()
    in_org = [s for s in candidates if _text(s.get("organization_id")) == org]
    considered: List[Tuple[str, str]] = []
    for tier, matches in _tiers(org, project, package):
        governing = []
        for subscription in (s for s in in_org if matches(s)):
            cls = classify(subscription, moment)
            considered.append((str(subscription.get("_id") or subscription.get("id") or ""), cls))
            if cls in GOVERNING_CLASSES:
                governing.append((subscription, cls))
        if not governing:
            continue
        if len(governing) > 1:
            return Resolution(None, tier, None, "ambiguous_subscriptions", considered)
        subscription, cls = governing[0]
        return Resolution(subscription, tier, cls, cls, considered)
    return Resolution(None, None, None, "no_governing_subscription", considered)


__all__ = [
    "BLOCKING_STATUSES",
    "GOVERNING_STATUSES",
    "ELIGIBLE_STATUSES",
    "NOT_GOVERNING_STATUSES",
    "RESTRICTED_STATUSES",
    "Resolution",
    "classify",
    "select_subscription",
]
