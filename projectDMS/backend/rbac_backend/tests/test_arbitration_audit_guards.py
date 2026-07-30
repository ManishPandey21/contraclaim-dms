"""Static guards for defect classes found in the 2026-07-30 arbitration audit.

Each test pins a *contract* that was previously only held by convention, so the
defect class cannot silently reappear:

1. Observability — the root logger must have a handler and honour ``LOG_LEVEL``.
   Without it every ``rbac_backend.*`` logger falls through to Python's
   lastResort handler, which drops everything below WARNING and hides all
   workflow/queue/lease telemetry in production.
2. Privileged matrix fields — the model-side rejection list and the
   service-side stripping list must stay identical, or a field is validated in
   one layer and silently accepted in the other.
3. Arbitration checkpoint state — the persisted-key allowlist must exactly
   match the typed graph state, or a new field is either rejected at runtime or
   written to the checkpoint unvalidated.
"""

from __future__ import annotations

import copy
import logging
import logging.config

import pytest


# --- 1. observability ------------------------------------------------------


def test_logging_config_configures_a_root_logger_with_a_handler():
    from rbac_backend.core.config import Settings

    root = Settings.LOGGING_CONFIG.get("root")
    assert root, "LOGGING_CONFIG must define a root logger"
    assert root.get("handlers"), "root logger must have at least one handler"


def test_logging_config_does_not_disable_existing_loggers():
    from rbac_backend.core.config import Settings

    assert Settings.LOGGING_CONFIG.get("disable_existing_loggers") is False, (
        "dictConfig defaults this to True, which disables every logger created "
        "before configure_logging() runs"
    )


@pytest.mark.parametrize("level", ["INFO", "WARNING"])
def test_root_logger_emits_at_configured_level(level, monkeypatch, capsys):
    """An rbac_backend.* logger must actually reach a stream at LOG_LEVEL."""
    from rbac_backend.core.config import Settings, configure_logging

    original = copy.deepcopy(Settings.LOGGING_CONFIG)
    monkeypatch.setenv("LOG_LEVEL", level)
    try:
        configure_logging()
        assert logging.getLevelName(logging.getLogger().level) == level
        logging.getLogger("rbac_backend.worker").warning("guard-probe")
        assert "guard-probe" in capsys.readouterr().err
    finally:
        logging.config.dictConfig(original)


# --- 2. privileged matrix field parity -------------------------------------


def test_privileged_matrix_field_lists_match():
    from rbac_backend.models.arbitration_drafting import PRIVILEGED_MATRIX_FIELDS
    from rbac_backend.services.arbitration_drafting.case_workspace import (
        SERVER_CONTROLLED_MATRIX_FIELDS,
    )

    assert PRIVILEGED_MATRIX_FIELDS == SERVER_CONTROLLED_MATRIX_FIELDS, (
        "model rejection list and service stripping list have drifted: "
        f"model-only={sorted(PRIVILEGED_MATRIX_FIELDS - SERVER_CONTROLLED_MATRIX_FIELDS)} "
        f"service-only={sorted(SERVER_CONTROLLED_MATRIX_FIELDS - PRIVILEGED_MATRIX_FIELDS)}"
    )


# --- 3. checkpoint state allowlist parity ----------------------------------


def test_checkpoint_allowlist_matches_typed_graph_state():
    from rbac_backend.services.arbitration_drafting.langgraph_engine import (
        ALLOWED_CHECKPOINT_KEYS,
        ArbitrationGraphState,
    )

    state_keys = set(ArbitrationGraphState.__annotations__)
    assert state_keys == ALLOWED_CHECKPOINT_KEYS, (
        f"state-only={sorted(state_keys - ALLOWED_CHECKPOINT_KEYS)} "
        f"allowlist-only={sorted(ALLOWED_CHECKPOINT_KEYS - state_keys)}"
    )


def test_typed_checkpoint_keys_are_declared_in_graph_state():
    from rbac_backend.services.arbitration_drafting.langgraph_engine import (
        BOOLEAN_CHECKPOINT_KEYS,
        INTEGER_CHECKPOINT_KEYS,
        ArbitrationGraphState,
    )

    state_keys = set(ArbitrationGraphState.__annotations__)
    assert not BOOLEAN_CHECKPOINT_KEYS - state_keys
    assert not INTEGER_CHECKPOINT_KEYS - state_keys


def test_checkpoint_validation_rejects_raw_content():
    from rbac_backend.services.arbitration_drafting.langgraph_engine import (
        validate_checkpoint_state,
    )

    with pytest.raises(ValueError):
        validate_checkpoint_state({"run_id": "r1", "draft_markdown": "raw pleading text"})
