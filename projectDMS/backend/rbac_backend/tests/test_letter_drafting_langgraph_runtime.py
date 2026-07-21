from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from rbac_backend.services.letter_drafting.drafting_queue import DraftingQueue
from rbac_backend.services.letter_drafting.langgraph_engine import (
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
