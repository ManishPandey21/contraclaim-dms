from __future__ import annotations

import hashlib
import json
from typing import Any

from ....core.config import settings
from .base import ArbitrationEngineDecision


VALID_MODES = {"off", "shadow", "canary", "primary", "forced_v2"}


def canonical_workflow_request_hash(*, case_id: str, payload: Any, tenant_id: str, project_id: str) -> str:
    value = payload.model_dump(mode="json", exclude_none=True) if hasattr(payload, "model_dump") else payload
    raw = json.dumps(
        {"case_id": case_id, "tenant_id": tenant_id, "project_id": project_id, "payload": value},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _items(value: Any) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


class ArbitrationEngineSelector:
    def __init__(self, config: Any = settings) -> None:
        self.config = config

    def select(self, *, tenant_id: str, project_id: str, request_hash: str, force_v2: bool = False) -> ArbitrationEngineDecision:
        if force_v2:
            return ArbitrationEngineDecision("arbitration_v2", "forced_v2", reason="authorized_force_v2")
        mode = str(getattr(self.config, "ARBITRATION_ENGINE_ROLLOUT_MODE", "off") or "off").lower()
        default = str(getattr(self.config, "ARBITRATION_ENGINE_DEFAULT", "arbitration_v2") or "arbitration_v2").lower()
        if mode not in VALID_MODES:
            mode = "off"
        forced_tenant = tenant_id in _items(getattr(self.config, "ARBITRATION_ENGINE_FORCE_V2_TENANT_IDS", ""))
        forced_project = project_id in _items(getattr(self.config, "ARBITRATION_ENGINE_FORCE_V2_PROJECT_IDS", ""))
        if forced_tenant or forced_project:
            return ArbitrationEngineDecision("arbitration_v2", "forced_v2", reason="tenant_or_project_forced_v2")
        if bool(getattr(self.config, "ARBITRATION_ENGINE_ROLLOUT_PAUSED", False)):
            return ArbitrationEngineDecision("arbitration_v2", "forced_v2", reason="rollout_paused")
        if mode in {"off", "forced_v2"} or default != "langgraph_v1":
            return ArbitrationEngineDecision("arbitration_v2", mode, reason="default_off_or_v2")
        if mode == "shadow":
            return ArbitrationEngineDecision("arbitration_v2", mode, shadow=True, reason="shadow_preserves_v2_authority")
        if mode == "primary":
            if not bool(getattr(self.config, "ARBITRATION_ENGINE_PRODUCTION_ACCEPTED", False)):
                return ArbitrationEngineDecision("arbitration_v2", "forced_v2", reason="primary_not_accepted")
            return ArbitrationEngineDecision("langgraph_v1", mode, reason="primary")
        allowed = tenant_id in _items(getattr(self.config, "ARBITRATION_ENGINE_CANARY_TENANT_IDS", ""))
        allowed = allowed or project_id in _items(getattr(self.config, "ARBITRATION_ENGINE_CANARY_PROJECT_IDS", ""))
        percent = max(0, min(100, int(getattr(self.config, "ARBITRATION_ENGINE_CANARY_PERCENT", 0))))
        bucket = int(hashlib.sha256(f"{tenant_id}:{project_id}:{request_hash}".encode()).hexdigest()[:8], 16) % 100
        return ArbitrationEngineDecision(
            "langgraph_v1" if allowed or bucket < percent else "arbitration_v2",
            "canary",
            reason="canary_match" if allowed or bucket < percent else "canary_miss",
        )
