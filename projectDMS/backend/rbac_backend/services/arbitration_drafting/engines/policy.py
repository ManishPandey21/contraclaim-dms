from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ....core.config import settings
from .base import ArbitrationEngineDecision


VALID_MODES = {"off", "shadow", "canary", "primary", "forced_v2"}
POLICY_VERSION = "phase6-v1"


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

    def _decision(
        self,
        engine: str,
        rollout_mode: str,
        *,
        tenant_id: str,
        project_id: str,
        request_hash: str,
        reason: str,
        shadow: bool = False,
    ) -> ArbitrationEngineDecision:
        receipt_hash = str(
            getattr(self.config, "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256", "") or ""
        ).strip().lower()
        compatibility_mode = str(
            getattr(self.config, "ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE", "active") or "active"
        ).strip().lower()
        decision_payload = {
            "policy_version": POLICY_VERSION,
            "engine": engine,
            "rollout_mode": rollout_mode,
            "reason": reason,
            "shadow": bool(shadow),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "request_hash": request_hash,
            "acceptance_receipt_sha256": receipt_hash or None,
            "v2_compatibility_mode": compatibility_mode,
        }
        decision_hash = hashlib.sha256(
            json.dumps(decision_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return ArbitrationEngineDecision(
            engine,
            rollout_mode,
            shadow=shadow,
            reason=reason,
            policy_version=POLICY_VERSION,
            decision_hash=decision_hash,
            acceptance_receipt_sha256=receipt_hash or None,
            v2_compatibility_mode=compatibility_mode,
        )

    def select(
        self,
        *,
        tenant_id: str,
        project_id: str,
        request_hash: str,
        force_v2: bool = False,
        rollout_health: dict[str, Any] | None = None,
    ) -> ArbitrationEngineDecision:
        def decision(engine: str, rollout_mode: str, reason: str, *, shadow: bool = False) -> ArbitrationEngineDecision:
            return self._decision(
                engine,
                rollout_mode,
                tenant_id=tenant_id,
                project_id=project_id,
                request_hash=request_hash,
                reason=reason,
                shadow=shadow,
            )

        compatibility_mode = str(
            getattr(self.config, "ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE", "active") or "active"
        ).lower()

        def v2_decision(rollout_mode: str, reason: str, *, shadow: bool = False) -> ArbitrationEngineDecision:
            if compatibility_mode != "active":
                return decision("unavailable", rollout_mode, "v2_new_runs_frozen")
            return decision("arbitration_v2", rollout_mode, reason, shadow=shadow)

        if force_v2:
            return v2_decision("forced_v2", "authorized_force_v2")
        mode = str(getattr(self.config, "ARBITRATION_ENGINE_ROLLOUT_MODE", "off") or "off").lower()
        default = str(getattr(self.config, "ARBITRATION_ENGINE_DEFAULT", "arbitration_v2") or "arbitration_v2").lower()
        if mode not in VALID_MODES:
            mode = "off"
        forced_tenant = tenant_id in _items(getattr(self.config, "ARBITRATION_ENGINE_FORCE_V2_TENANT_IDS", ""))
        forced_project = project_id in _items(getattr(self.config, "ARBITRATION_ENGINE_FORCE_V2_PROJECT_IDS", ""))
        if forced_tenant or forced_project:
            return v2_decision("forced_v2", "tenant_or_project_forced_v2")
        if bool(getattr(self.config, "ARBITRATION_ENGINE_ROLLOUT_PAUSED", False)):
            return v2_decision("forced_v2", "rollout_paused")
        if mode in {"off", "forced_v2"} or default != "langgraph_v1":
            return v2_decision(mode, "default_off_or_v2")
        if mode == "shadow":
            return v2_decision(mode, "shadow_preserves_v2_authority", shadow=True)
        if mode == "primary":
            def primary_fallback(reason: str) -> ArbitrationEngineDecision:
                return v2_decision("forced_v2", reason)

            if not bool(getattr(self.config, "ARBITRATION_ENGINE_PRODUCTION_ACCEPTED", False)):
                return primary_fallback("primary_not_accepted")
            receipt_id = str(
                getattr(self.config, "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID", "") or ""
            ).strip()
            receipt_hash = str(
                getattr(self.config, "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256", "") or ""
            ).strip().lower()
            if not receipt_id or not re.fullmatch(r"[0-9a-f]{64}", receipt_hash):
                return primary_fallback("primary_acceptance_receipt_missing")
            if not bool((rollout_health or {}).get("acceptance_receipt", {}).get("valid_for_scope")):
                return primary_fallback("primary_acceptance_receipt_unresolved")
            require_health = bool(
                getattr(self.config, "ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY", True)
            )
            if require_health and not bool((rollout_health or {}).get("primary_cutover", {}).get("eligible")):
                return primary_fallback("primary_health_not_ready")
            allowed = tenant_id in _items(
                getattr(self.config, "ARBITRATION_ENGINE_PRIMARY_TENANT_IDS", "")
            )
            allowed = allowed or project_id in _items(
                getattr(self.config, "ARBITRATION_ENGINE_PRIMARY_PROJECT_IDS", "")
            )
            percent = max(
                0,
                min(100, int(getattr(self.config, "ARBITRATION_ENGINE_PRIMARY_PERCENT", 0))),
            )
            bucket = int(
                hashlib.sha256(f"{tenant_id}:{project_id}:{request_hash}".encode()).hexdigest()[:8],
                16,
            ) % 100
            if allowed or bucket < percent:
                return decision("langgraph_v1", mode, "primary_scope_match")
            return v2_decision(mode, "primary_scope_miss")
        allowed = tenant_id in _items(getattr(self.config, "ARBITRATION_ENGINE_CANARY_TENANT_IDS", ""))
        allowed = allowed or project_id in _items(getattr(self.config, "ARBITRATION_ENGINE_CANARY_PROJECT_IDS", ""))
        percent = max(0, min(100, int(getattr(self.config, "ARBITRATION_ENGINE_CANARY_PERCENT", 0))))
        bucket = int(hashlib.sha256(f"{tenant_id}:{project_id}:{request_hash}".encode()).hexdigest()[:8], 16) % 100
        if allowed or bucket < percent:
            return decision("langgraph_v1", "canary", "canary_match")
        return v2_decision("canary", "canary_miss")
