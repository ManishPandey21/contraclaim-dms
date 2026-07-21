from __future__ import annotations

from typing import Any


class V2DraftingEngine:
    """Adapter for the mature synchronous drafting service."""

    name = "v2"
    version = "v2"

    def __init__(self, service: Any) -> None:
        self.service = service

    async def create(self, letter_id: str, payload: Any, current_user: Any) -> Any:
        return await self.service.create_run(letter_id, payload, current_user)
