"""Production-like Mongo checkpoint TTL and post-TTL resume acceptance.

The test is opt-in because it waits for MongoDB's TTL monitor. It creates only
namespaced checkpoint records and removes the active thread in teardown.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from langgraph.types import Command

from rbac_backend.core.config import settings
from rbac_backend.services.arbitration_drafting.langgraph_engine import (
    MongoArbitrationCheckpointStore,
    build_arbitration_graph,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ARBITRATION_TTL_INTEGRATION", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set RUN_ARBITRATION_TTL_INTEGRATION=1 to run the real Mongo TTL drill",
)


def test_expired_terminal_checkpoint_is_deleted_while_active_thread_resumes() -> None:
    namespace = f"acceptance:ttl:{uuid.uuid4().hex}"
    active_thread = f"{namespace}:active"
    expired_thread = f"{namespace}:expired"
    store = MongoArbitrationCheckpointStore()
    database = store.client.get_database()
    checkpoints = database.arbitration_langgraph_checkpoints
    writes = database.arbitration_langgraph_checkpoint_writes
    expected_ttl = int(settings.ARBITRATION_ENGINE_CHECKPOINT_RETENTION_DAYS) * 86400

    try:
        for collection in (checkpoints, writes):
            ttl_index = next((row for row in collection.list_indexes() if row.get("name") == "created_at_1"), None)
            assert ttl_index is not None
            assert int(ttl_index.get("expireAfterSeconds")) == expected_ttl

        graph = build_arbitration_graph(checkpointer=store.saver)
        config = {"configurable": {"thread_id": active_thread}}
        graph.invoke(
            {
                "run_id": f"{namespace}:run",
                "thread_id": active_thread,
                "case_id": f"{namespace}:case",
                "draft_id": f"{namespace}:draft",
                "pleading_type": "statement_of_claim",
                "graph_version": "phase4-v1",
                "state_schema_version": 2,
                "state_version": 1,
                "input_snapshot_id": f"{namespace}:snapshot",
                "input_snapshot_hash": "a" * 64,
                "documents_selected": False,
                "user_direction_complete": False,
            },
            config,
        )
        paused = graph.get_state(config)
        assert paused.next == ("document_selection_gate",)

        marker_id = f"{namespace}:terminal-marker"
        checkpoints.insert_one(
            {
                "_id": marker_id,
                "thread_id": expired_thread,
                "checkpoint_ns": "",
                "checkpoint_id": marker_id,
                "created_at": datetime.now(timezone.utc) - timedelta(seconds=expected_ttl + 300),
                "acceptance_marker": True,
            }
        )
        deadline = time.monotonic() + 90
        while checkpoints.find_one({"_id": marker_id}) is not None and time.monotonic() < deadline:
            time.sleep(2)
        assert checkpoints.find_one({"_id": marker_id}) is None, "Mongo TTL monitor did not delete expired checkpoint"

        # The active checkpoint remains within retention and resumes only after
        # the TTL monitor has demonstrably removed the expired terminal marker.
        graph.invoke(
            Command(
                resume={"run_id": f"{namespace}:run"},
                update={"documents_selected": True, "state_version": 2},
            ),
            config,
        )
        resumed = graph.get_state(config)
        assert resumed.next == ("material_question_gate",)
        assert resumed.values.get("documents_selected") is True
    finally:
        checkpoints.delete_many({"thread_id": {"$regex": f"^{namespace}"}})
        writes.delete_many({"thread_id": {"$regex": f"^{namespace}"}})
        store.close()
