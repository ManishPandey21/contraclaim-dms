from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from ..core.database import get_database
from ..models.input_request import (
    InputRequest,
    InputRequestCreate,
    InputRequestUpdate,
    InputRequestResponse,
    InputRequestStatus,
    SuggestedKeyPoints,
)
from ..utils.error_handler import InputRequestError


class InputRequestService:
    """Service for managing input requests."""

    def __init__(self) -> None:
        self._collection = None

    async def _collection_handle(self):
        if self._collection is None:
            db = await get_database()
            self._collection = db.input_requests
        return self._collection

    async def get_requests_for_letter_paginated(
        self, letter_id: str, pagination: Dict[str, int]
    ) -> Tuple[List[InputRequest], int]:
        col = await self._collection_handle()
        criteria = {"letter_id": letter_id}
        skip = max(pagination.get("skip", 0), 0)
        limit = max(pagination.get("limit", 20), 1)
        total = await col.count_documents(criteria)
        docs = await col.find(criteria).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
        return [self._to_model(doc) for doc in docs], total

    async def create_request(
        self, letter_id: str, data: InputRequestCreate, current_user: Any
    ) -> InputRequest:
        col = await self._collection_handle()
        payload = data.model_dump(exclude_none=True)
        payload["letter_id"] = letter_id
        payload["requested_by"] = getattr(current_user, "id", None)
        payload["status"] = InputRequestStatus.OPEN.value
        payload["responses"] = []
        payload["created_at"] = datetime.utcnow()
        payload["updated_at"] = datetime.utcnow()
        result = await col.insert_one(payload)
        payload["_id"] = str(result.inserted_id)
        return InputRequest(**payload)

    async def get_request_by_id(self, request_id: str) -> Optional[InputRequest]:
        col = await self._collection_handle()
        query_id = self._to_query_id(request_id)
        doc = await col.find_one({"_id": query_id})
        return self._to_model(doc) if doc else None

    async def add_response(
        self, request_id: str, response: InputRequestResponse, current_user: Any
    ) -> InputRequest:
        col = await self._collection_handle()
        entry = {
            "message": response.message,
            "responded_by": getattr(current_user, "id", None),
            "responded_at": datetime.utcnow(),
        }
        query_id = self._to_query_id(request_id)
        result = await col.update_one(
            {"_id": query_id},
            {
                "$push": {"responses": entry},
                "$set": {
                    "status": InputRequestStatus.IN_PROGRESS.value,
                    "updated_at": datetime.utcnow(),
                },
            },
        )
        if result.matched_count == 0:
            raise InputRequestError("Input request not found", 404)
        updated = await col.find_one({"_id": query_id})
        return self._to_model(updated)

    async def close_request(self, request_id: str, current_user: Any) -> InputRequest:
        col = await self._collection_handle()
        query_id = self._to_query_id(request_id)
        result = await col.update_one(
            {"_id": query_id},
            {
                "$set": {
                    "status": InputRequestStatus.CLOSED.value,
                    "closed_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        if result.matched_count == 0:
            raise InputRequestError("Input request not found", 404)
        updated = await col.find_one({"_id": query_id})
        return self._to_model(updated)

    async def generate_key_points(self, letter_id: str) -> SuggestedKeyPoints:
        # Basic placeholder implementation until AI integration is wired in.
        base_points = [
            "Summarize the current status of the request",
            "Highlight any blockers or pending dependencies",
            "Provide next steps or required actions",
        ]
        return SuggestedKeyPoints(letter_id=letter_id, key_points=base_points)

    def _to_query_id(self, value: str):
        try:
            return ObjectId(value)
        except Exception:
            return value

    def _to_model(self, doc: Optional[Dict[str, Any]]) -> InputRequest:
        if not doc:
            raise InputRequestError("Input request payload missing", 500)
        payload = dict(doc)
        payload["_id"] = str(payload.get("_id"))
        return InputRequest(**payload)
