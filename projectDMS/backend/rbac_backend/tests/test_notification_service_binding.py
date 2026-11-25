"""DEF-6: the notification service follows the database handle it is asked for.

Before, ``dependencies.get_notification_service`` kept the first handle for the life of
the process: after ``disconnect``/``connect`` (or a test harness pointing the app at
another database) every ``/api/notifications`` call ran on a closed client and failed
with 500. Production behaviour is otherwise unchanged: with one handle there is one
service, and every service shares the one WebSocket connection manager.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend import dependencies


class _Db:
    """A database handle; only its identity matters here."""

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dependencies, "_notification_service", None)
    monkeypatch.setattr(dependencies, "_email_service", None)


def test_one_handle_one_service() -> None:
    db = _Db()

    async def scenario() -> None:
        first = await dependencies.get_notification_service(db)
        second = await dependencies.get_notification_service(db)
        assert first is second
        email = await dependencies.get_email_service(db)
        assert email is first.email_service and email.notification_service is first

    asyncio.run(scenario())


def test_a_new_handle_gets_a_service_bound_to_it() -> None:
    old, new = _Db(), _Db()

    async def scenario() -> None:
        before = await dependencies.get_notification_service(old)
        after = await dependencies.get_notification_service(new)
        assert after is not before
        assert after.db is new and after.email_service.db is new
        assert after.email_service.notification_service is after
        # One connection manager for every socket in the process.
        assert after.manager is before.manager is dependencies._connection_manager

    asyncio.run(scenario())


def test_the_email_service_follows_the_handle_too() -> None:
    old, new = _Db(), _Db()

    async def scenario() -> None:
        await dependencies.get_notification_service(old)
        email = await dependencies.get_email_service(new)
        assert email.db is new
        assert email.notification_service is None, "never linked to a service on another handle"
        service = await dependencies.get_notification_service(new)
        assert service.email_service is email and email.notification_service is service

    asyncio.run(scenario())
