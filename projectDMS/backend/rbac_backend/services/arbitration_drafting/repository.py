from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pymongo.errors import DuplicateKeyError


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


async def _collect(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return [dict(item) for item in await cursor.to_list(length=None)]
    return [dict(item) async for item in cursor]


class ArbitrationDraftingRepository:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def create_draft(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        payload = _jsonable(doc)
        await self.db.arbitration_drafts.insert_one(payload)
        return payload

    async def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        return await self.db.arbitration_drafts.find_one({"_id": draft_id, "deleted_at": {"$exists": False}})

    async def list_drafts(self, query: Dict[str, Any], *, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        q = dict(query or {})
        q["deleted_at"] = {"$exists": False}
        cursor = self.db.arbitration_drafts.find(q).sort("updated_at", -1).skip(skip).limit(limit)
        return await _collect(cursor)

    async def update_draft(self, draft_id: str, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await self.db.arbitration_drafts.find_one_and_update(
            {"_id": draft_id},
            {"$set": _jsonable(update)},
            return_document=True,
        )

    async def soft_delete_draft(self, draft_id: str, update: Dict[str, Any]) -> None:
        await self.db.arbitration_drafts.update_one({"_id": draft_id}, {"$set": _jsonable(update)})

    async def replace_references(self, draft_id: str, refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        await self.db.arbitration_selected_references.delete_many({"draft_id": draft_id})
        rows = [_jsonable({**row, "draft_id": draft_id}) for row in refs]
        if rows:
            await self.db.arbitration_selected_references.insert_many(rows)
        return rows

    async def add_references(self, draft_id: str, refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows = [_jsonable({**row, "draft_id": draft_id}) for row in refs]
        if rows:
            await self.db.arbitration_selected_references.insert_many(rows)
        return rows

    async def list_references(self, draft_id: str) -> List[Dict[str, Any]]:
        return await _collect(self.db.arbitration_selected_references.find({"draft_id": draft_id}))

    async def delete_reference(self, draft_id: str, reference_id: str) -> int:
        result = await self.db.arbitration_selected_references.delete_many(
            {"draft_id": draft_id, "_id": reference_id}
        )
        return int(getattr(result, "deleted_count", 0) or 0)

    async def replace_claim_heads(self, draft_id: str, heads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        await self.db.arbitration_claim_heads.delete_many({"draft_id": draft_id})
        rows = [_jsonable({**row, "draft_id": draft_id}) for row in heads]
        if rows:
            await self.db.arbitration_claim_heads.insert_many(rows)
        return rows

    async def list_claim_heads(self, draft_id: str) -> List[Dict[str, Any]]:
        return await _collect(self.db.arbitration_claim_heads.find({"draft_id": draft_id}))

    async def replace_paragraph_responses(self, draft_id: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        await self.db.arbitration_paragraph_responses.delete_many({"draft_id": draft_id})
        payload = [_jsonable({**row, "draft_id": draft_id}) for row in rows]
        if payload:
            await self.db.arbitration_paragraph_responses.insert_many(payload)
        return payload

    async def list_paragraph_responses(self, draft_id: str) -> List[Dict[str, Any]]:
        cursor = self.db.arbitration_paragraph_responses.find({"draft_id": draft_id}).sort("source_paragraph_number", 1)
        return await _collect(cursor)

    async def update_paragraph_response(self, draft_id: str, response_id: str, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await self.db.arbitration_paragraph_responses.find_one_and_update(
            {"_id": response_id, "draft_id": draft_id}, {"$set": _jsonable(update)}, return_document=True
        )

    async def create_generation_run(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        payload = _jsonable(doc)
        try:
            await self.db.arbitration_generation_runs.insert_one(payload)
            return payload
        except DuplicateKeyError:
            existing = await self.db.arbitration_generation_runs.find_one({"_id": payload.get("_id")})
            if not existing or any(
                existing.get(field) != payload.get(field)
                for field in ("draft_id", "run_type", "input_hash", "prompt_version", "model")
            ):
                raise
            return existing

    async def update_generation_run(self, run_id: str, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await self.db.arbitration_generation_runs.find_one_and_update(
            {"_id": run_id},
            {"$set": _jsonable(update)},
            return_document=True,
        )

    async def get_generation_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return await self.db.arbitration_generation_runs.find_one({"_id": run_id})

    async def next_version(self, draft_id: str) -> int:
        latest = await self.db.arbitration_draft_versions.find_one({"draft_id": draft_id}, sort=[("version", -1)])
        # $max makes this safe for pre-migration/test databases while the
        # following atomic $inc remains the sole allocator under concurrency.
        await self.db.arbitration_draft_version_counters.update_one(
            {"_id": draft_id},
            {"$max": {"value": int((latest or {}).get("version") or 0)}, "$setOnInsert": {"draft_id": draft_id}},
            upsert=True,
        )
        counter = await self.db.arbitration_draft_version_counters.find_one_and_update(
            {"_id": draft_id},
            {"$inc": {"value": 1}, "$setOnInsert": {"draft_id": draft_id}},
            upsert=True,
            return_document=True,
        )
        return int(counter.get("value") or 1)

    async def create_version(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        payload = _jsonable(doc)
        try:
            await self.db.arbitration_draft_versions.insert_one(payload)
            return payload
        except DuplicateKeyError:
            existing = await self.db.arbitration_draft_versions.find_one({"_id": payload.get("_id")})
            if not existing or any(
                existing.get(field) != payload.get(field)
                for field in ("draft_id", "version", "version_hash", "generation_run_id", "parent_version_id")
            ):
                raise
            return existing

    async def list_versions(self, draft_id: str) -> List[Dict[str, Any]]:
        return await _collect(self.db.arbitration_draft_versions.find({"draft_id": draft_id}).sort("version", -1))

    async def get_version(self, draft_id: str, version: int) -> Optional[Dict[str, Any]]:
        return await self.db.arbitration_draft_versions.find_one({"draft_id": draft_id, "version": version})

    async def latest_version(self, draft_id: str) -> Optional[Dict[str, Any]]:
        return await self.db.arbitration_draft_versions.find_one({"draft_id": draft_id}, sort=[("version", -1)])
