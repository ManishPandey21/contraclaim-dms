from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from rbac_backend.services.letter_drafting.drafting_queue import DraftingQueue
from rbac_backend.services.letter_drafting.langgraph_engine import (
    MongoDraftCheckpointStore,
    _redacted_checkpoint_values,
    build_drafting_graph,
)


def test_official_stategraph_compiles_and_checkpoints_minimal_state():
    graph = build_drafting_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-1"}}

    result = graph.invoke(
        {
            "run_id": "run-1",
            "letter_id": "letter-1",
            "state_schema_version": 1,
            "input_snapshot_id": "input-snapshot",
            "context_snapshot_id": "evidence-snapshot",
            "execution_status": "awaiting_strategy_confirmation",
            "next_action": "confirm_strategy",
        },
        config=config,
    )
    state = graph.get_state(config)

    assert result["execution_status"] == "awaiting_strategy_confirmation"
    assert result["next_action"] == "confirm_strategy"
    assert state.config["configurable"].get("checkpoint_id")


def test_evidence_node_records_only_snapshot_identifiers_in_checkpoint_state():
    graph = build_drafting_graph(checkpointer=InMemorySaver())
    result = graph.invoke(
        {
            "run_id": "run-1",
            "letter_id": "letter-1",
            "state_schema_version": 1,
            "input_snapshot_id": "input-snapshot-v1",
            "context_snapshot_id": "evidence-snapshot-v1",
        },
        {"configurable": {"thread_id": "thread-evidence"}},
    )

    assert result["input_snapshot_id"] == "input-snapshot-v1"
    assert result["context_snapshot_id"] == "evidence-snapshot-v1"
    assert result["correspondence_reviewed"] is True


def test_checkpoint_history_redacts_unapproved_content():
    redacted = _redacted_checkpoint_values(
        {
            "run_id": "run-1",
            "next_action": "poll",
            "prompt": "sensitive raw draft text",
        }
    )
    assert redacted["run_id"] == "run-1"
    assert redacted["prompt"]["redacted"] is True
    assert "sensitive" not in str(redacted)


def test_dedicated_queue_uses_a_drafting_specific_metadata_key():
    assert DraftingQueue._job_key("run-1") == "letter_drafting_job:run-1"


def test_mongodb_checkpoint_store_relies_on_saver_constructor_indexes(monkeypatch):
    """langgraph-checkpoint-mongodb 0.4 creates indexes in its constructor."""
    import rbac_backend.services.letter_drafting.langgraph_engine as runtime

    created = {}

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            created["client"] = self

        def close(self):
            created["closed"] = True

    class FakeSaver:
        def __init__(self, client, **kwargs):
            created["saver_client"] = client
            created["saver_kwargs"] = kwargs

    monkeypatch.setattr(runtime, "MongoClient", FakeClient)
    monkeypatch.setattr(runtime, "MongoDBSaver", FakeSaver)
    monkeypatch.setattr(runtime.settings, "DATABASE_URL", "mongodb://mongo1:27017/contraclaim")

    store = MongoDraftCheckpointStore()
    assert created["saver_client"] is created["client"]
    assert created["saver_kwargs"]["checkpoint_collection_name"] == "letter_draft_langgraph_checkpoints"
    store.close()
    assert created["closed"] is True


def test_question_and_strategy_gates_interrupt_before_prepare_execution():
    graph = build_drafting_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-interrupt"}}
    first = graph.invoke(
        {
            "run_id": "run-1",
            "letter_id": "letter-1",
            "state_schema_version": 1,
            "questions_required": True,
            "execution_status": "awaiting_user_direction",
            "next_action": "answer_questions",
        },
        config,
    )
    assert first["next_action"] == "answer_questions"

    second = graph.invoke(Command(resume={"run_id": "run-1"}), config)
    assert second["next_action"] == "confirm_strategy"


def test_domain_nodes_enforce_and_checkpoint_persisted_stage_markers():
    graph = build_drafting_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-domain-stages"}}
    first = graph.invoke(
        {
            "run_id": "run-1",
            "letter_id": "letter-1",
            "state_schema_version": 1,
            "questions_required": False,
            "execution_status": "awaiting_strategy_confirmation",
            "next_action": "confirm_strategy",
        },
        config,
    )
    assert first["next_action"] == "confirm_strategy"

    completed = graph.invoke(
        Command(
            resume={"run_id": "run-1"},
            update={
                "domain_run_id": "domain-run-1",
                "draft_generated": True,
                "validation_completed": True,
                "legal_risk_scanned": True,
                "approval_ready": True,
                "domain_run_status": "completed",
            },
        ),
        config,
    )
    assert completed["draft_generated"] is True
    assert completed["validation_completed"] is True
    assert completed["legal_risk_scanned"] is True
    assert completed["approval_ready"] is True
    assert completed["next_action"] == "approve"
