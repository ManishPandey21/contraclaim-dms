"""A0b over real Mongo: undated timeline events sort and filter honestly.

The fakes elsewhere order ``None`` however Python happens to; this pins what
the server actually does with ``event_date: null`` and a missing field:

* sort - dated events newest first, then the undated group, each tie broken by
  ``created_at`` then ``_id`` so paging is deterministic;
* filter - a date range matches stored event dates only, never an undated
  event's ``created_at``.

Opt-in through ``RELATIONSHIP_CL3B_MONGODB_URI`` (a disposable replica set);
CI runs it in the replica-set step and fails if it skips.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, List

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.models.evidence_graph import ProjectEvent, ProjectEventCreate
from rbac_backend.services.evidence_graph_service import EvidenceGraphService

pytestmark = pytest.mark.integration

MONGODB_URI_ENV = "RELATIONSHIP_CL3B_MONGODB_URI"
SCOPE = {"organization_id": "org-A", "project_id": "proj-A"}
#: Recorded "today"-ish; an undated event must never sort or filter as if it
#: happened then.
RECORDED = datetime(2026, 10, 1, 9, 0)


class _Audit:
    async def emit(self, **_kwargs: Any) -> None:
        return None


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


async def _seeded(run: Any) -> Any:
    mongo = AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=5_000)
    name = f"a0b_dates_{uuid.uuid4().hex[:10]}"
    db = mongo[name]
    try:
        svc = EvidenceGraphService(db)
        svc.audit = _Audit()
        user = SimpleNamespace(id="user-1", organization_id="org-A")
        rows = [
            ("dated-mar", datetime(2026, 3, 10)),
            ("dated-jan", datetime(2026, 1, 5)),
            ("undated-b", None),
            ("undated-a", None),
        ]
        for title, event_date in rows:
            await svc.create_project_event(ProjectEventCreate(**SCOPE, title=title, event_date=event_date), user)
        # Fix recording times so the tie-break is asserted, not incidental:
        # undated-a was recorded after undated-b.
        await db.project_events.update_one({"title": "undated-b"}, {"$set": {"created_at": datetime(2026, 9, 1)}})
        await db.project_events.update_one({"title": "undated-a"}, {"$set": {"created_at": RECORDED}})
        # A legacy row may lack the field entirely rather than hold null.
        legacy = ProjectEvent(**SCOPE, title="legacy-missing").model_dump(by_alias=True)
        legacy.pop("event_date")
        legacy["created_at"] = datetime(2026, 8, 1)
        await db.project_events.insert_one(legacy)
        return await run(svc)
    finally:
        await mongo.drop_database(name)
        mongo.close()


def _titles(rows: List[dict]) -> List[str]:
    return [row["title"] for row in rows]


def test_dated_events_sort_first_then_undated_group_deterministically() -> None:
    async def run(svc: EvidenceGraphService) -> None:
        first = _titles(await svc.list_project_events(SCOPE))
        again = _titles(await svc.list_project_events(SCOPE))
        assert first == ["dated-mar", "dated-jan", "undated-a", "undated-b", "legacy-missing"]
        assert again == first
        page_one = _titles(await svc.list_project_events(SCOPE, skip=0, limit=3))
        page_two = _titles(await svc.list_project_events(SCOPE, skip=3, limit=3))
        assert page_one + page_two == first

    asyncio.run(_seeded(run))


def test_date_range_matches_event_dates_only_not_recording_time() -> None:
    async def run(svc: EvidenceGraphService) -> None:
        # The window covers when the undated rows were recorded, not any event.
        recorded_window = _titles(
            await svc.list_project_events(SCOPE, date_from=datetime(2026, 8, 1), date_to=datetime(2026, 10, 31))
        )
        assert recorded_window == []
        march = _titles(await svc.list_project_events(SCOPE, date_from=datetime(2026, 3, 1), date_to=datetime(2026, 3, 31)))
        assert march == ["dated-mar"]
        open_ended = _titles(await svc.list_project_events(SCOPE, date_to=datetime(2026, 12, 31)))
        assert open_ended == ["dated-mar", "dated-jan"]

    asyncio.run(_seeded(run))


def test_undated_rows_serialise_through_the_response_model() -> None:
    async def run(svc: EvidenceGraphService) -> None:
        rows = await svc.list_project_events(SCOPE)
        models = [ProjectEvent(**row) for row in rows]
        assert [m.event_date for m in models[2:]] == [None, None, None]

    asyncio.run(_seeded(run))
