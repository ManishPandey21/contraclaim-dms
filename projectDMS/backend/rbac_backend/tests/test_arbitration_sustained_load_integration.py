"""Sustained real-Mongo load and cancellation-under-load acceptance."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.core.config import settings
from rbac_backend.services.arbitration_drafting.workflow_repository import (
    ArbitrationWorkflowRepository,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ARBITRATION_SUSTAINED_LOAD_INTEGRATION", "").strip().lower()
    not in {"1", "true", "yes", "on"},
    reason="set RUN_ARBITRATION_SUSTAINED_LOAD_INTEGRATION=1 to run sustained real-Mongo load",
)


def _database_name() -> str:
    return (urlparse(str(settings.DATABASE_URL)).path or "").strip("/") or "contraclaim"


@pytest.mark.asyncio
async def test_sustained_effect_load_fences_then_accepts_cancellation() -> None:
    namespace = f"acceptance:sustained-load:{uuid.uuid4().hex}"
    duration = max(
        30,
        int(os.getenv("ARBITRATION_SUSTAINED_LOAD_SECONDS", "60")),
    )
    workers = max(
        2,
        int(os.getenv("ARBITRATION_SUSTAINED_LOAD_WORKERS", "8")),
    )
    client = AsyncIOMotorClient(str(settings.DATABASE_URL), serverSelectionTimeoutMS=5000)
    database = client[_database_name()]
    repository = ArbitrationWorkflowRepository(database)
    completed = 0
    completed_lock = asyncio.Lock()
    deadline = time.monotonic() + duration

    async def worker(worker_id: int) -> None:
        nonlocal completed
        sequence = 0
        while time.monotonic() < deadline:
            sequence += 1
            run_id = f"{namespace}:run:{worker_id}:{sequence}"
            effect_key = f"{run_id}:graph-node:generate_draft:load"
            lease_token = f"{worker_id}:{sequence}:{uuid.uuid4()}"
            now = datetime.now(timezone.utc)
            await database.arbitration_workflow_runs.insert_one(
                {
                    "_id": run_id,
                    "state_version": 1,
                    "status": "running",
                    "current_node": "generate_draft",
                    "next_action": "poll",
                    "input_snapshot_hash": "a" * 64,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            effect = await repository.claim_effect(
                run_id=run_id,
                effect_key=effect_key,
                effect_type="graph_node:generate_draft",
                input_hash="b" * 64,
            )
            assert effect["_claimed_now"] is True
            assert await repository.claim_run_execution_lease(
                run_id,
                effect_key=f"{run_id}:graph-execution:{lease_token}",
                node="generate_draft",
                lease_token=lease_token,
            )
            with pytest.raises(HTTPException) as conflict:
                await repository.cancel_if_idle(
                    run_id,
                    1,
                    reason="cancellation-under-load",
                    actor_id="load-operator",
                )
            assert conflict.value.status_code == 409
            await repository.complete_effect(
                effect_key,
                {"draft_version_id": f"{run_id}:version", "draft_version_hash": "c" * 64},
                lease_token=str(effect["lease_token"]),
            )
            await repository.release_run_execution_lease(
                run_id,
                effect_key=f"{run_id}:graph-execution:{lease_token}",
                lease_token=lease_token,
            )
            cancelled = await repository.cancel_if_idle(
                run_id,
                1,
                reason="cancellation-after-node-boundary",
                actor_id="load-operator",
            )
            assert cancelled["status"] == "cancelled"
            async with completed_lock:
                completed += 1

    try:
        await asyncio.gather(*(worker(index) for index in range(workers)))
        assert completed >= workers * 10
        assert (
            await database.arbitration_workflow_runs.count_documents(
                {"_id": {"$regex": f"^{namespace}"}, "status": "cancelled"}
            )
            == completed
        )
        assert (
            await database.arbitration_workflow_effects.count_documents(
                {"run_id": {"$regex": f"^{namespace}"}, "status": "completed"}
            )
            == completed
        )
    finally:
        await database.arbitration_workflow_events.delete_many(
            {"run_id": {"$regex": f"^{namespace}"}}
        )
        await database.arbitration_workflow_effects.delete_many(
            {"run_id": {"$regex": f"^{namespace}"}}
        )
        await database.arbitration_workflow_runs.delete_many(
            {"_id": {"$regex": f"^{namespace}"}}
        )
        client.close()
