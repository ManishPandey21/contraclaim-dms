"""H2: leader-lock dedup for scheduled jobs + scheduler gating."""

from datetime import datetime, timedelta

import pytest
from pymongo.errors import DuplicateKeyError

from rbac_backend.services import scheduler_lock
from rbac_backend.services.scheduler_lock import acquire, release, with_leader_lock


class _FakeLocks:
    """Emulates the Mongo upsert/DuplicateKeyError lock semantics."""

    def __init__(self):
        self.docs = {}

    async def find_one_and_update(self, flt, update, upsert=False):
        _id = flt["_id"]
        cutoff = flt.get("locked_until", {}).get("$lte")
        setv = update["$set"]
        existing = self.docs.get(_id)
        if existing is None:
            if upsert:
                self.docs[_id] = {"_id": _id, **setv}
            return None
        if cutoff is not None and existing["locked_until"] <= cutoff:
            existing.update(setv)
            return existing
        if upsert:
            raise DuplicateKeyError("duplicate _id")  # live lock held by someone else
        return None

    async def update_one(self, flt, update):
        d = self.docs.get(flt["_id"])
        if d and all(d.get(k) == v for k, v in flt.items() if k != "_id"):
            d.update(update["$set"])


class _FakeDB:
    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeLocks())


@pytest.mark.asyncio
async def test_lock_is_mutually_exclusive_and_releasable():
    db = _FakeDB()
    assert await acquire("job", 60, holder="A", db=db) is True
    # A second holder is blocked while the lock is live.
    assert await acquire("job", 60, holder="B", db=db) is False
    # After release the lock is free again.
    await release("job", holder="A", db=db)
    assert await acquire("job", 60, holder="B", db=db) is True


@pytest.mark.asyncio
async def test_expired_lock_can_be_reacquired():
    db = _FakeDB()
    assert await acquire("job", 60, holder="A", db=db) is True
    # Simulate a crashed holder: force the TTL into the past.
    db["scheduler_locks"].docs["job"]["locked_until"] = datetime.utcnow() - timedelta(seconds=1)
    assert await acquire("job", 60, holder="B", db=db) is True


@pytest.mark.asyncio
async def test_with_leader_lock_runs_for_holder_and_skips_when_held(monkeypatch):
    db = _FakeDB()

    async def _fake_get_database():
        return db

    monkeypatch.setattr(scheduler_lock, "get_database", _fake_get_database)

    ran = []

    @with_leader_lock("jobX", 60)
    async def job():
        ran.append(1)
        return "ran"

    # No contention → runs and returns the value.
    assert await job() == "ran"
    assert ran == [1]

    # Another instance holds the lock → the wrapped job is skipped.
    assert await acquire("jobX", 60, holder="other", db=db) is True
    assert await job() is None
    assert ran == [1]  # not run again


@pytest.mark.asyncio
async def test_start_scheduler_respects_run_scheduler_flag(monkeypatch):
    from rbac_backend.services import scheduler as sched

    monkeypatch.setattr(sched.settings, "RUN_SCHEDULER", False)
    assert await sched.start_scheduler() is None
