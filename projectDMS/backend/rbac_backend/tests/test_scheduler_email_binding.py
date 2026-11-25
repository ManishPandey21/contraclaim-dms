"""T-3: scheduled digest jobs resolve the email service when they run.

Before, ``start_scheduler`` captured ``email_service.send_daily_digests`` (and weekly) at
start-up, so after a database reconnect the digest jobs kept writing through the first,
closed client, beyond the reach of the DEF-6 rebinding. Now each run asks
``dependencies.get_email_service`` for the service bound to the current handle.
Unchanged: job ids, triggers, ``max_instances=1``, ``coalesce=True`` and the leader lock
around every job.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from rbac_backend import dependencies
from rbac_backend.services import scheduler as scheduler_module
from rbac_backend.services import scheduler_lock


class _Db:
    """A database handle; only its identity matters."""


class _Trigger:
    def __init__(self, **fields: Any) -> None:
        self.fields = fields


class _Scheduler:
    instances: List["_Scheduler"] = []

    def __init__(self) -> None:
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.running = False
        _Scheduler.instances.append(self)

    def add_job(self, func: Any, trigger: Any, **options: Any) -> None:
        self.jobs[options["id"]] = {"func": func, "trigger": trigger, **options}

    def start(self) -> None:
        self.running = True


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    state: Dict[str, Any] = {"db": _Db(), "lock": True, "sent": [], "locks": []}

    async def current_database() -> Any:
        return state["db"]

    async def acquire(job_id: str, ttl_seconds: int, holder: str = "", db: Any = None) -> bool:
        state["locks"].append(job_id)
        return state["lock"]

    async def release(job_id: str, holder: str = "", db: Any = None) -> None:
        return None

    async def daily(self: Any) -> None:
        state["sent"].append(("daily", self.db))

    async def weekly(self: Any) -> None:
        state["sent"].append(("weekly", self.db))

    monkeypatch.setattr(scheduler_module.settings, "RUN_SCHEDULER", True)
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _Scheduler)
    monkeypatch.setattr(scheduler_module, "CronTrigger", _Trigger)
    monkeypatch.setattr(dependencies, "get_database", current_database)
    monkeypatch.setattr(dependencies, "_notification_service", None)
    monkeypatch.setattr(dependencies, "_email_service", None)
    monkeypatch.setattr(scheduler_lock, "acquire", acquire)
    monkeypatch.setattr(scheduler_lock, "release", release)
    monkeypatch.setattr(dependencies.EmailService, "send_daily_digests", daily)
    monkeypatch.setattr(dependencies.EmailService, "send_weekly_digests", weekly)
    _Scheduler.instances.clear()
    return state


def _start() -> _Scheduler:
    asyncio.run(scheduler_module.start_scheduler())
    return _Scheduler.instances[-1]


def test_start_up_captures_no_email_service(harness: Dict[str, Any]) -> None:
    _start()
    assert dependencies._email_service is None, "the scheduler resolved the service at start-up"


def test_each_run_uses_the_service_bound_to_the_current_handle(harness: Dict[str, Any]) -> None:
    scheduler = _start()
    first = harness["db"]
    asyncio.run(scheduler.jobs["daily_digests"]["func"]())
    harness["db"] = _Db()  # disconnect/connect: a new handle
    asyncio.run(scheduler.jobs["daily_digests"]["func"]())
    asyncio.run(scheduler.jobs["weekly_digests"]["func"]())
    assert harness["sent"] == [("daily", first), ("daily", harness["db"]), ("weekly", harness["db"])]


def test_an_unchanged_handle_keeps_one_service(harness: Dict[str, Any]) -> None:
    scheduler = _start()
    asyncio.run(scheduler.jobs["daily_digests"]["func"]())
    service = dependencies._email_service
    asyncio.run(scheduler.jobs["weekly_digests"]["func"]())
    assert dependencies._email_service is service


def test_the_leader_lock_still_gates_every_job(harness: Dict[str, Any]) -> None:
    scheduler = _start()
    harness["lock"] = False  # another replica holds the lock
    asyncio.run(scheduler.jobs["daily_digests"]["func"]())
    asyncio.run(scheduler.jobs["weekly_digests"]["func"]())
    assert harness["sent"] == []
    assert harness["locks"] == ["daily_digests", "weekly_digests"]


def test_schedule_and_options_are_unchanged(harness: Dict[str, Any]) -> None:
    scheduler = _start()
    assert scheduler.running
    assert set(scheduler.jobs) == {
        "daily_digests", "weekly_digests", "sla_deadline_scan", "key_date_notification_scan",
        "bg_expiry_scan", "insurance_expiry_scan", "legal_word_daily_publish",
        "reference_sync_reaper", "appraisal_stuck_job_reaper",
    }
    assert scheduler.jobs["daily_digests"]["trigger"].fields == {"hour": 9, "minute": 0}
    assert scheduler.jobs["weekly_digests"]["trigger"].fields == {"day_of_week": "sun", "hour": 9, "minute": 0}
    for job in scheduler.jobs.values():
        assert job["max_instances"] == 1 and job["coalesce"] is True
