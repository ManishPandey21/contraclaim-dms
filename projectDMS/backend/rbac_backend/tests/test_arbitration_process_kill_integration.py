"""Real-Mongo process-kill acceptance for arbitration graph boundaries.

The opt-in test uses only namespaced rows. It kills a child process after a
durable idempotent side effect at every authoritative command node, expires
the dead lease as an operator/visibility monitor would, and proves recovery
does not duplicate the side effect. It also kills and reopens the process at
every human interrupt gate using the official MongoDB checkpointer.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from rbac_backend.core.config import settings
from rbac_backend.services.arbitration_drafting.graph_commands import (
    ArbitrationGraphCommandExecutor,
    VALIDATION_NODE_BRANCH,
)
from rbac_backend.services.arbitration_drafting.langgraph_engine import (
    MongoArbitrationCheckpointStore,
    build_arbitration_graph,
)
from rbac_backend.services.arbitration_drafting.workflow_domain import (
    ANALYSIS_NODE_BRANCHES,
)
from rbac_backend.services.arbitration_drafting.workflow_repository import (
    ArbitrationWorkflowRepository,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ARBITRATION_PROCESS_KILL_INTEGRATION", "").strip().lower()
    not in {"1", "true", "yes", "on"},
    reason="set RUN_ARBITRATION_PROCESS_KILL_INTEGRATION=1 to run real process-kill recovery",
)

COMMAND_NODES = (
    "validate_intake",
    "capture_input_snapshot",
    *ANALYSIS_NODE_BRANCHES,
    "merge_evidence_and_matrices",
    "build_pleading_plan",
    "generate_draft",
    *VALIDATION_NODE_BRANCH,
    "merge_validation_artifacts",
    "remediate_draft",
    "commit_draft_approval",
    "create_filing_export",
)

GATE_FLAGS = (
    ("document_selection_gate", "documents_selected"),
    ("material_question_gate", "user_direction_complete"),
    ("matrix_review_gate", "matrices_approved"),
    ("readiness_approval_gate", "readiness_approved"),
    ("plan_approval_gate", "plan_approved"),
    ("legal_review_gate", "legal_review_approved"),
    ("draft_approval_gate", "draft_approved"),
    ("export_authorization_gate", "export_authorized"),
)


def _database_name() -> str:
    return (urlparse(str(settings.DATABASE_URL)).path or "").strip("/") or "contraclaim"


async def _child_effect(namespace: str, node: str, mode: str) -> None:
    client = AsyncIOMotorClient(str(settings.DATABASE_URL), serverSelectionTimeoutMS=5000)
    database = client[_database_name()]
    run_id = f"{namespace}:run:{node}"
    executor = ArbitrationGraphCommandExecutor(database)

    async def operation(_run):
        side_effect_id = f"{namespace}:side-effect:{node}"
        await database.arbitration_process_kill_side_effects.update_one(
            {"_id": side_effect_id},
            {
                "$setOnInsert": {
                    "_id": side_effect_id,
                    "run_id": run_id,
                    "node": node,
                    "delivery_count": 1,
                    "created_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        print("SIDE_EFFECT_COMMITTED", flush=True)
        if mode == "kill":
            await asyncio.sleep(300)
        return {
            "current_node": node,
            "execution_status": "running",
            "next_action": "poll",
        }

    try:
        await executor._run_effect(node, {"run_id": run_id}, operation)
        await executor.release_execution_lease(run_id)
    finally:
        client.close()


def _gate_state(namespace: str, target: str) -> dict:
    state = {
        "run_id": f"{namespace}:run:{target}",
        "thread_id": f"{namespace}:thread:{target}",
        "case_id": f"{namespace}:case",
        "draft_id": f"{namespace}:draft",
        "pleading_type": "statement_of_claim",
        "graph_version": str(settings.ARBITRATION_ENGINE_GRAPH_VERSION),
        "state_schema_version": int(settings.ARBITRATION_ENGINE_STATE_SCHEMA_VERSION),
        "state_version": 1,
        "input_snapshot_id": f"{namespace}:input",
        "input_snapshot_hash": "a" * 64,
        "plan_id": f"{namespace}:plan",
        "plan_hash": "b" * 64,
        "draft_version_id": f"{namespace}:version",
        "draft_version_hash": "c" * 64,
        "validation_artifact_set_id": f"{namespace}:validation-set",
        "validation_artifact_set_hash": "d" * 64,
        "validation_report_id": f"{namespace}:validation-report",
        "validation_report_hash": "e" * 64,
        "validation_route": "review",
    }
    target_index = next(index for index, (gate, _flag) in enumerate(GATE_FLAGS) if gate == target)
    for index, (_gate, flag) in enumerate(GATE_FLAGS):
        state[flag] = index < target_index
    return state


def _child_gate(namespace: str, target: str, mode: str) -> None:
    store = MongoArbitrationCheckpointStore()
    try:
        config = {"configurable": {"thread_id": f"{namespace}:thread:{target}"}}
        graph = build_arbitration_graph(checkpointer=store.saver)
        if mode == "kill":
            graph.invoke(_gate_state(namespace, target), config)
        checkpoint = graph.get_state(config)
        assert checkpoint.next == (target,)
        print("GATE_CHECKPOINT_COMMITTED", flush=True)
        if mode == "kill":
            time.sleep(300)
    finally:
        store.close()


def _start_child(*args: str) -> subprocess.Popen:
    child_env = os.environ.copy()
    backend_root = str(Path(__file__).resolve().parents[2])
    child_env["PYTHONPATH"] = os.pathsep.join(
        value for value in (backend_root, child_env.get("PYTHONPATH", "")) if value
    )
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )


def _child_environment() -> dict[str, str]:
    child_env = os.environ.copy()
    backend_root = str(Path(__file__).resolve().parents[2])
    child_env["PYTHONPATH"] = os.pathsep.join(
        value for value in (backend_root, child_env.get("PYTHONPATH", "")) if value
    )
    return child_env


def _wait_for_marker(process: subprocess.Popen, expected: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        line = process.stdout.readline().strip()
        if line == expected:
            return
        if process.poll() is not None:
            stderr = process.stderr.read()
            raise AssertionError(f"child exited before {expected}: {stderr}")
    process.kill()
    raise AssertionError(f"child did not reach {expected}")


async def _assert_cancel_fenced(run_id: str) -> None:
    client = AsyncIOMotorClient(str(settings.DATABASE_URL), serverSelectionTimeoutMS=5000)
    try:
        repository = ArbitrationWorkflowRepository(client[_database_name()])
        with pytest.raises(HTTPException) as conflict:
            await repository.cancel_if_idle(
                run_id,
                1,
                reason="process-kill load drill",
                actor_id="acceptance-operator",
            )
        assert conflict.value.status_code == 409
        assert "authoritative graph node is active" in str(conflict.value.detail)
    finally:
        client.close()


def test_every_command_node_and_human_gate_recovers_after_process_kill() -> None:
    namespace = f"acceptance:process-kill:{uuid.uuid4().hex}"
    sync_client = MongoClient(str(settings.DATABASE_URL), serverSelectionTimeoutMS=5000)
    database = sync_client[_database_name()]
    now = datetime.now(timezone.utc)

    try:
        for node in COMMAND_NODES:
            run_id = f"{namespace}:run:{node}"
            database.arbitration_workflow_runs.insert_one(
                {
                    "_id": run_id,
                    "state_version": 1,
                    "status": "running",
                    "current_node": node,
                    "next_action": "poll",
                    "input_snapshot_hash": "a" * 64,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            killed = _start_child("--child-effect", namespace, node, "kill")
            _wait_for_marker(killed, "SIDE_EFFECT_COMMITTED")
            asyncio.run(_assert_cancel_fenced(run_id))
            killed.kill()
            killed.wait(timeout=10)

            expired = datetime.now(timezone.utc) - timedelta(seconds=1)
            database.arbitration_workflow_effects.update_many(
                {"run_id": run_id, "status": "claimed"},
                {"$set": {"lease_expires_at": expired}},
            )
            database.arbitration_workflow_runs.update_one(
                {"_id": run_id},
                {"$set": {"active_effect_lease_expires_at": expired}},
            )
            recovered = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--child-effect",
                    namespace,
                    node,
                    "complete",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=_child_environment(),
            )
            assert recovered.returncode == 0, recovered.stderr
            assert (
                database.arbitration_process_kill_side_effects.count_documents(
                    {"_id": f"{namespace}:side-effect:{node}"}
                )
                == 1
            )
            effect = database.arbitration_workflow_effects.find_one(
                {"run_id": run_id, "effect_type": f"graph_node:{node}"}
            )
            assert effect["status"] == "completed"
            assert effect["attempt_count"] == 2

        for gate, _flag in GATE_FLAGS:
            killed = _start_child("--child-gate", namespace, gate, "kill")
            _wait_for_marker(killed, "GATE_CHECKPOINT_COMMITTED")
            killed.kill()
            killed.wait(timeout=10)
            recovered = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--child-gate",
                    namespace,
                    gate,
                    "recover",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=_child_environment(),
            )
            assert recovered.returncode == 0, recovered.stderr
            assert "GATE_CHECKPOINT_COMMITTED" in recovered.stdout
    finally:
        database.arbitration_workflow_runs.delete_many({"_id": {"$regex": f"^{namespace}"}})
        database.arbitration_workflow_effects.delete_many({"run_id": {"$regex": f"^{namespace}"}})
        database.arbitration_process_kill_side_effects.delete_many({"run_id": {"$regex": f"^{namespace}"}})
        database.arbitration_langgraph_checkpoints.delete_many({"thread_id": {"$regex": f"^{namespace}"}})
        database.arbitration_langgraph_checkpoint_writes.delete_many({"thread_id": {"$regex": f"^{namespace}"}})
        sync_client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child-effect", nargs=3, metavar=("NAMESPACE", "NODE", "MODE"))
    parser.add_argument("--child-gate", nargs=3, metavar=("NAMESPACE", "GATE", "MODE"))
    arguments = parser.parse_args()
    if arguments.child_effect:
        asyncio.run(_child_effect(*arguments.child_effect))
    elif arguments.child_gate:
        _child_gate(*arguments.child_gate)
