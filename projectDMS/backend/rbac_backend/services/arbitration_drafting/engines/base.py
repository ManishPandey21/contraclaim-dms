from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ArbitrationEngineDecision:
    engine: str
    rollout_mode: str
    shadow: bool = False
    reason: str = "policy"
    policy_version: str = "phase6-v1"
    decision_hash: str = ""
    acceptance_receipt_sha256: str | None = None
    v2_compatibility_mode: str = "active"


class ArbitrationWorkflowEngine(Protocol):
    name: str
    version: str

    async def create_workflow(self, case_id: str, payload: Any, current_user: Any, **kwargs: Any) -> Any: ...
    async def get_state(self, run_id: str) -> Any: ...
    async def resume_workflow(self, run_id: str, payload: Any, current_user: Any) -> Any: ...
    async def cancel_workflow(self, run_id: str, payload: Any, current_user: Any) -> Any: ...
    async def fallback_to_v2(self, run_id: str, payload: Any, current_user: Any) -> Any: ...
