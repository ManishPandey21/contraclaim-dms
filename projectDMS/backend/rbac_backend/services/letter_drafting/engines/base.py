from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DraftEngineDecision:
    engine: str
    rollout_mode: str
    shadow: bool = False
    reason: str = "policy"


class DraftingEngine(Protocol):
    """Small adapter contract shared by the legacy and graph engines."""

    name: str
    version: str

    async def create(self, letter_id: str, payload: Any, current_user: Any) -> Any:
        ...
