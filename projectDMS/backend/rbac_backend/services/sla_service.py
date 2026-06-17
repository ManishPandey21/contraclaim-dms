"""Correspondence SLA & time-bar tracker (Phase 4 / Module 2).

Derives deadline "SLA items" from claims and classifies each as ok / approaching
/ breached. Two kinds of deadline:

* ``response`` — the claim's ``response_due_date`` (an awaited reply/decision).
* ``time_bar`` — a contractual notice window: for a claim with an ``event_date``
  that has **not yet been notified** (no ``notice_date``), the notice must be
  served within a per-claim-type window (e.g. the FIDIC 28-day clause). The
  deadline is ``event_date + window``.

Computation is pure (``compute_sla_items``) so it is trivially testable; the
``SlaService`` adds tenant-scoped DB loading, and ``scan_sla_deadlines`` is the
background-job entry point that emits notifications for approaching/breached
items. Authorization/scoping is enforced by the router via ``build_scope_query``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Claim statuses that are settled — no live deadline to chase.
TERMINAL_STATUSES = {"agreed", "rejected", "closed"}

# Default contractual notice windows (days) from event_date to required notice,
# per claim type. Overridable per organization via the ``sla_rules`` collection.
DEFAULT_NOTICE_WINDOWS: Dict[str, int] = {
    "eot": 28,
    "variation": 14,
    "payment_ipc": 7,
    "loss_expense": 28,
    "acceleration": 14,
    "defect": 28,
    "other": 28,
}

DEFAULT_APPROACHING_DAYS = 14


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _classify(due: datetime, now: datetime, approaching_days: int) -> tuple[int, str]:
    days_remaining = (due.date() - now.date()).days
    if days_remaining < 0:
        state = "breached"
    elif days_remaining <= approaching_days:
        state = "approaching"
    else:
        state = "ok"
    return days_remaining, state


def _item(claim: Dict[str, Any], kind: str, due: datetime, days_remaining: int, state: str) -> Dict[str, Any]:
    return {
        "claim_id": str(claim.get("_id") or claim.get("id") or ""),
        "claim_ref": claim.get("claim_ref"),
        "title": claim.get("title"),
        "type": claim.get("type"),
        "status": claim.get("status"),
        "kind": kind,
        "due_date": due,
        "days_remaining": days_remaining,
        "state": state,
        "organization_id": claim.get("organization_id"),
        "project_id": claim.get("project_id"),
        "responsible_party_id": claim.get("responsible_party_id"),
    }


def compute_sla_items(
    claims: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    approaching_days: int = DEFAULT_APPROACHING_DAYS,
    notice_windows: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Derive SLA items from claims. Settled claims contribute nothing."""
    now = now or datetime.utcnow()
    windows = {**DEFAULT_NOTICE_WINDOWS, **(notice_windows or {})}
    items: List[Dict[str, Any]] = []

    for claim in claims:
        if str(claim.get("status") or "").lower() in TERMINAL_STATUSES:
            continue

        response_due = _as_datetime(claim.get("response_due_date"))
        if response_due is not None:
            days_remaining, state = _classify(response_due, now, approaching_days)
            items.append(_item(claim, "response", response_due, days_remaining, state))

        # Time-bar only matters while notice is still outstanding.
        event_date = _as_datetime(claim.get("event_date"))
        if event_date is not None and not claim.get("notice_date"):
            window = windows.get(str(claim.get("type") or "other"), windows["other"])
            time_bar = event_date + timedelta(days=window)
            days_remaining, state = _classify(time_bar, now, approaching_days)
            items.append(_item(claim, "time_bar", time_bar, days_remaining, state))

    items.sort(key=lambda i: i["days_remaining"])
    return items


class SlaService:
    def __init__(self, db: Any = None) -> None:
        self.db = db

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        from ..core.database import get_database

        return await get_database()

    async def notice_windows_for(self, organization_id: Optional[str]) -> Dict[str, int]:
        """Default windows overlaid with any per-org override in ``sla_rules``."""
        if not organization_id:
            return dict(DEFAULT_NOTICE_WINDOWS)
        db = await self._get_db()
        try:
            rule = await db.sla_rules.find_one({"organization_id": organization_id})
        except Exception:  # pragma: no cover - defensive (collection optional)
            rule = None
        override = (rule or {}).get("notice_windows") or {}
        return {**DEFAULT_NOTICE_WINDOWS, **override}

    async def list_sla(
        self,
        scope_filter: Dict[str, Any],
        *,
        days: int = DEFAULT_APPROACHING_DAYS,
        only_breached: bool = False,
        organization_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Tenant-scoped SLA items. ``scope_filter`` comes from build_scope_query."""
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        query["status"] = {"$nin": list(TERMINAL_STATUSES)}
        cursor = db.claims.find(query)
        claims = [c async for c in cursor]
        windows = await self.notice_windows_for(organization_id)
        items = compute_sla_items(claims, now=now, approaching_days=days, notice_windows=windows)
        if only_breached:
            return [i for i in items if i["state"] == "breached"]
        return [i for i in items if i["state"] in ("approaching", "breached")]


async def scan_sla_deadlines(
    db: Any,
    notification_service: Any = None,
    *,
    approaching_days: int = DEFAULT_APPROACHING_DAYS,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Background scan: emit a reminder per approaching/breached claim deadline.

    Runs across all tenants (system context). Notification delivery is
    best-effort and per-item guarded so one failure can't abort the sweep.
    Returns counts for observability/testing.
    """
    now = now or datetime.utcnow()
    cursor = db.claims.find({"status": {"$nin": list(TERMINAL_STATUSES)}})
    claims = [c async for c in cursor]
    items = compute_sla_items(claims, now=now, approaching_days=approaching_days)

    emitted = 0
    for item in items:
        if item["state"] not in ("approaching", "breached"):
            continue
        if notification_service is None:
            continue
        breached = item["state"] == "breached"
        try:
            from ..models.notification import (
                NotificationContext,
                NotificationPriority,
                NotificationSeverity,
                NotificationType,
            )

            due = item["due_date"]
            due_str = due.date().isoformat() if isinstance(due, datetime) else str(due)
            label = "Time-bar" if item["kind"] == "time_bar" else "Response"
            verb = "has passed" if breached else f"is due in {item['days_remaining']} day(s)"
            recipients = [r for r in [item.get("responsible_party_id")] if r]
            await notification_service.emit(
                NotificationType.CLAIM_DEADLINE_BREACHED if breached else NotificationType.CLAIM_DEADLINE_APPROACHING,
                item["claim_id"],
                "claims",
                context=NotificationContext.ORGANIZATION,
                include_users=recipients,
                priority=NotificationPriority.URGENT if breached else NotificationPriority.HIGH,
                severity=NotificationSeverity.ERROR if breached else NotificationSeverity.WARNING,
                data={
                    "title": f"{label} deadline {'breached' if breached else 'approaching'}",
                    "message": f"{label} deadline for '{item.get('title') or item['claim_id']}' {verb} ({due_str}).",
                    "claim_id": item["claim_id"],
                    "kind": item["kind"],
                    "due_date": due_str,
                    "organization_id": item.get("organization_id"),
                    "project_id": item.get("project_id"),
                },
                resource_link="/sla",
                dedupe_key=f"sla:{item['claim_id']}:{item['kind']}:{item['state']}:{due_str}",
            )
            emitted += 1
        except Exception:  # pragma: no cover - notification path is best-effort
            logger.exception("SLA notification emit failed for claim %s", item.get("claim_id"))

    breached_count = sum(1 for i in items if i["state"] == "breached")
    approaching_count = sum(1 for i in items if i["state"] == "approaching")
    logger.info(
        "SLA scan: %s approaching, %s breached, %s notifications emitted",
        approaching_count,
        breached_count,
        emitted,
    )
    return {"approaching": approaching_count, "breached": breached_count, "emitted": emitted}


async def run_sla_scan() -> Dict[str, int]:
    """Scheduler entry point — resolves its own DB + notification service."""
    from ..core.database import get_database
    from ..dependencies import get_notification_service

    db = await get_database()
    try:
        notification_service = await get_notification_service(db)
    except Exception:  # pragma: no cover - notifications optional
        notification_service = None
    return await scan_sla_deadlines(db, notification_service)
