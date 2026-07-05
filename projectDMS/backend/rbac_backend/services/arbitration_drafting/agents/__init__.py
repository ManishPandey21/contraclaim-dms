"""Arbitration agent dispatch (ARB-101).

``run_arbitration_agent`` keeps the original contract (matrix rows persisted,
result dict with created_records/warnings/errors/source_ids/prompt_version/model)
and selects the execution mode:

- ``deterministic`` (default): heuristic source-mapping agent, safe offline baseline.
- ``llm``: source-grounded LLM analysis agent; falls back to deterministic with a
  warning when no LLM client is configured.

Mode resolution order: run ``options.agent_mode`` > ``ARBITRATION_AGENT_MODE``
environment variable > ``deterministic``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .deterministic import (
    MODEL_NAME as DETERMINISTIC_MODEL_NAME,
    PROMPT_VERSION as DETERMINISTIC_PROMPT_VERSION,
    DeterministicArbitrationAgent,
)

AGENT_MODES = {"deterministic", "llm"}
DEFAULT_AGENT_MODE = "deterministic"
LLM_FALLBACK_WARNING = (
    "LLM agent mode was requested but no LLM client is configured "
    "(missing API key); the deterministic fallback agent was used."
)


def resolve_agent_mode(options: Optional[Dict[str, Any]] = None) -> str:
    raw = str(
        (options or {}).get("agent_mode")
        or os.getenv("ARBITRATION_AGENT_MODE")
        or DEFAULT_AGENT_MODE
    ).strip().lower()
    return raw if raw in AGENT_MODES else DEFAULT_AGENT_MODE


def agent_run_metadata(options: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Expected prompt_version/model for a run before it executes (for queued records)."""
    if resolve_agent_mode(options) == "llm":
        from .llm import LLM_PROMPT_VERSION, resolve_llm_model

        return {"prompt_version": LLM_PROMPT_VERSION, "model": resolve_llm_model(options)}
    return {"prompt_version": DETERMINISTIC_PROMPT_VERSION, "model": DETERMINISTIC_MODEL_NAME}


async def run_arbitration_agent(
    db: Any,
    case: Dict[str, Any],
    agent_type: str,
    *,
    payload: Any,
    current_user: Any,
) -> Dict[str, Any]:
    options = dict(getattr(payload, "options", {}) or {})
    draft_id = getattr(payload, "draft_id", None)
    mode = resolve_agent_mode(options)

    if mode == "llm":
        from .llm import LLMArbitrationAgent

        agent = LLMArbitrationAgent(
            db=db,
            case=case,
            draft_id=draft_id,
            options=options,
            current_user=current_user,
        )
        if agent.available:
            return await agent.run(agent_type)
        fallback = DeterministicArbitrationAgent(
            db=db,
            case=case,
            draft_id=draft_id,
            options=options,
            current_user=current_user,
        )
        result = await fallback.run(agent_type)
        result["warnings"] = [LLM_FALLBACK_WARNING, *(result.get("warnings") or [])]
        return result

    agent = DeterministicArbitrationAgent(
        db=db,
        case=case,
        draft_id=draft_id,
        options=options,
        current_user=current_user,
    )
    return await agent.run(agent_type)


__all__ = [
    "AGENT_MODES",
    "DEFAULT_AGENT_MODE",
    "agent_run_metadata",
    "resolve_agent_mode",
    "run_arbitration_agent",
]
