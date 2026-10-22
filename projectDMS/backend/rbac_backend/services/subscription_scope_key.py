"""Database invariant helpers for one current subscription per exact scope."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional


CURRENT_SUBSCRIPTION_STATUSES = frozenset({"trial", "pilot", "active"})


def normalize_scope_value(value: Any) -> Optional[str]:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def subscription_scope_key(
    organization_id: Any,
    project_id: Any = None,
    package_id: Any = None,
) -> str:
    """Return a collision-safe stable key for the exact subscription scope."""

    organization = normalize_scope_value(organization_id)
    if not organization:
        raise ValueError("organization_id is required for a current subscription")
    return json.dumps(
        [
            organization,
            normalize_scope_value(project_id),
            normalize_scope_value(package_id),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def current_subscription_scope_key(document: Mapping[str, Any]) -> Optional[str]:
    status = str(document.get("status") or "").strip().lower()
    if status not in CURRENT_SUBSCRIPTION_STATUSES:
        return None
    return subscription_scope_key(
        document.get("organization_id"),
        document.get("project_id"),
        document.get("package_id"),
    )
