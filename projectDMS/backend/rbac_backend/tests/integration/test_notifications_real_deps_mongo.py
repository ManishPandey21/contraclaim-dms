"""DEF-6: /api/notifications keeps working when the process changes database handle.

Reproduced on 2026-11-19: the second real-route test of a run got 500 ("Event loop is
closed") from every /api/notifications call, because the notification service kept the
first test's database handle. The same handle swap happens in production on
``disconnect``/``connect``. Two harness databases one after another, each in its own
event loop as two tests of one run are; real HTTP, no override.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env

pytestmark = pytest.mark.integration


def test_notifications_answer_from_each_database_in_turn(monkeypatch) -> None:
    async def one_database(marker: str) -> None:
        async with _env(monkeypatch) as env:
            await env.db.notifications.insert_one(
                {"user_id": "user-org_user", "type": "system", "title": marker, "message": marker,
                 "read": False, "created_at": datetime.utcnow(), "metadata": {}}
            )
            listing = await env.call("GET", "org_user", "/api/notifications")
            assert listing.status_code == 200, (marker, listing.text[:300])
            count = await env.call("GET", "org_user", "/api/notifications/unread-count")
            assert count.status_code == 200, (marker, count.text[:300])

    asyncio.run(one_database("first"))
    asyncio.run(one_database("second"))  # was 500: the service still held the first handle
