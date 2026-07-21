"""Deterministic, server-side drafting-engine selection."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from ....core.config import settings
from .base import DraftEngineDecision


VALID_ROLLOUT_MODES = {"off", "shadow", "canary", "primary", "forced_v2"}


def _normalized(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {str(key): _normalized(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple, set)):
        return [_normalized(item) for item in value]
    return value


def canonical_request_hash(*, letter_id: str, payload: Any, tenant_id: str = "", project_id: str = "") -> str:
    """Hash semantically equal create requests identically without logging them."""
    document = {
        "letter_id": str(letter_id),
        "tenant_id": str(tenant_id or ""),
        "project_id": str(project_id or ""),
        "payload": _normalized(payload),
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _tenant_allowlist(value: str) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def _percent_bucket(parts: Iterable[str]) -> int:
    digest = hashlib.sha256(":".join(str(item) for item in parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


class DraftEngineSelector:
    """A testable policy object; no client flag can bypass this selector."""

    def __init__(self, config: Any = settings) -> None:
        self.config = config

    def select(
        self,
        *,
        tenant_id: str = "",
        request_hash: str = "",
        force_v2: bool = False,
    ) -> DraftEngineDecision:
        if force_v2:
            return DraftEngineDecision("v2", "forced_v2", reason="authorized_force_v2")

        mode = str(getattr(self.config, "DRAFT_ENGINE_ROLLOUT_MODE", "off") or "off").lower()
        if mode not in VALID_ROLLOUT_MODES:
            mode = "off"
        default = str(getattr(self.config, "DRAFT_ENGINE_DEFAULT", "v2") or "v2").lower()
        if mode == "off" or default != "langgraph_v3":
            return DraftEngineDecision("v2", mode, reason="default_off_or_v2")
        if mode == "shadow":
            return DraftEngineDecision(
                "v2",
                mode,
                shadow=bool(getattr(self.config, "DRAFT_ENGINE_SHADOW_ENABLED", False)),
                reason="shadow_preserves_v2_response",
            )
        if mode == "primary":
            return DraftEngineDecision("langgraph_v3", mode, reason="primary")

        allowed = _tenant_allowlist(getattr(self.config, "DRAFT_ENGINE_CANARY_TENANT_IDS", ""))
        percentage = max(0, min(100, int(getattr(self.config, "DRAFT_ENGINE_CANARY_PERCENT", 0))))
        in_allowlist = bool(tenant_id and tenant_id in allowed)
        in_percentage = _percent_bucket((tenant_id, request_hash)) < percentage
        if in_allowlist or in_percentage:
            return DraftEngineDecision("langgraph_v3", "canary", reason="canary_match")
        return DraftEngineDecision("v2", "canary", reason="canary_miss")
