"""H1 regression: startup must build indexes, and ensure_indexes must define the
critical unique / TTL constraints (webhook idempotency, share-token hash,
expiring cleanups). Guards against the bug where connect()/ensure_indexes was
only ever called by the seed script and never at app startup.
"""

import inspect

import pytest

from rbac_backend.core.database import ensure_indexes


class _RecCollection:
    def __init__(self, name, sink):
        self.name = name
        self.sink = sink

    async def create_index(self, keys, **kwargs):
        self.sink.append((self.name, keys, kwargs))
        return "idx"


class _RecDB:
    """Records every collection.create_index(...) call ensure_indexes makes.

    ensure_indexes reaches collections both as attributes (db.users) and by
    subscript (db[name]), so support both.
    """

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return _RecCollection(name, self.__dict__["calls"])

    def __getitem__(self, name):
        return _RecCollection(name, self.calls)


def test_startup_wires_database_connect():
    # The web process must call connect() at startup (and disconnect() on
    # shutdown) so indexes/constraints exist in a fresh deployment.
    from rbac_backend import main

    assert "connect_database" in inspect.getsource(main.startup_event)
    assert "disconnect_database" in inspect.getsource(main.shutdown_event)


@pytest.mark.asyncio
async def test_ensure_indexes_defines_critical_constraints():
    db = _RecDB()
    await ensure_indexes(db)
    calls = db.calls
    assert calls, "ensure_indexes created no indexes"

    def has(coll, key, **want):
        return any(
            c == coll and k == key and all(kw.get(wk) == wv for wk, wv in want.items())
            for c, k, kw in calls
        )

    # Webhook idempotency + share-token hash uniqueness.
    assert has("billing_webhook_events", "event_id", unique=True)
    assert has("document_share_tokens", "token_hash", unique=True)
    # At least one TTL cleanup (upload sessions / share tokens).
    assert any("expireAfterSeconds" in kw for _, _, kw in calls)
