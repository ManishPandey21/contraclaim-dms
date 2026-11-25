"""Whether an account may authenticate at all (SEC-4).

One definition, read by every door a person or service comes through: password
login, the access-token check behind every authenticated request and the
notifications WebSocket, token refresh, development headers, and the
re-resolution of a stored actor on a background worker. Before this, only login,
refresh and the worker path looked at ``disabled``; an access token minted before
an account was disabled kept working until its session was cleared in Redis, and
not at all on a deployment without a runtime Redis.

An account is inactive when ``disabled`` is set, or when ``is_active`` is
explicitly ``False`` (``UserUpdate`` accepts both fields; the UI reports
``is_active = not disabled``, so either written alone must count). A missing
field means active, as it always has. Service accounts (``account_type``
``system_service``) follow the same rule: a disabled service account stops.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def account_is_active(user: Optional[Mapping[str, Any]]) -> bool:
    if not user:
        return False
    if bool(user.get("disabled")):
        return False
    if user.get("is_active") is False:
        return False
    return True


def object_account_is_active(user: Any) -> bool:
    """The same rule for the light user objects the auth controllers carry."""
    if user is None:
        return False
    if bool(getattr(user, "disabled", False)):
        return False
    if getattr(user, "is_active", True) is False:
        return False
    return True


__all__ = ["account_is_active", "object_account_is_active"]
