from __future__ import annotations

import gzip
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import Binary, ObjectId

from ...models.letter_drafting import (
    DraftContextPack,
    DraftReviewAssignment,
    DraftReviewComment,
    DraftLifecycleEvent,
    DraftLifecycleEventType,
    DraftMode,
    DraftRun,
)


class DraftRunRepository:
    """Persistence adapter for v2 drafting runs."""

    COLLECTION = "letter_draft_runs"

    def __init__(self, db: Any):
        if db is None:
            raise ValueError("Database connection cannot be None")
        self.db = db
        self.collection = db[self.COLLECTION]
        self.events = db["letter_draft_events"]
        self.context_packs = db["draft_context_packs"]
        self.assignments = db["letter_draft_assignments"]
        self.comments = db["letter_draft_comments"]

    async def create(self, run: DraftRun) -> DraftRun:
        payload = run.model_dump(by_alias=True, exclude_none=True)
        payload.pop("_id", None)
        await self.collection.insert_one(payload)
        stored = await self.collection.find_one({"run_id": run.run_id})
        return DraftRun(**stored) if stored else run

    async def get(self, letter_id: str, run_id: str) -> Optional[DraftRun]:
        doc = await self.collection.find_one(
            {"letter_id": str(letter_id), "run_id": str(run_id)}
        )
        return DraftRun(**doc) if doc else None

    async def latest(self, letter_id: str, mode: Optional[DraftMode] = None) -> Optional[DraftRun]:
        query: Dict[str, Any] = {"letter_id": str(letter_id)}
        if mode:
            query["mode"] = mode
        doc = await self.collection.find_one(query, sort=[("started_at", -1)])
        return DraftRun(**doc) if doc else None

    async def update_fields(
        self,
        letter_id: str,
        run_id: str,
        fields: Dict[str, Any],
    ) -> Optional[DraftRun]:
        payload: Dict[str, Any] = {}
        for key, value in fields.items():
            if hasattr(value, "model_dump"):
                payload[key] = value.model_dump(exclude_none=True)
            elif isinstance(value, list):
                payload[key] = [
                    item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
                    for item in value
                ]
            else:
                payload[key] = value
        payload["completed_at"] = datetime.now(timezone.utc)
        await self.collection.update_one(
            {"letter_id": str(letter_id), "run_id": str(run_id)},
            {"$set": payload},
        )
        return await self.get(letter_id, run_id)

    async def append_event(
        self,
        letter_id: str,
        run_id: str,
        event_type: DraftLifecycleEventType,
        *,
        actor_user_id: Optional[str] = None,
        status: Optional[str] = None,
        detail: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self.events.insert_one(
            {
                "event_id": str(uuid.uuid4()),
                "letter_id": str(letter_id),
                "run_id": str(run_id),
                "event_type": event_type,
                "actor_user_id": actor_user_id,
                "status": status,
                "detail": detail,
                "payload": payload or {},
                "created_at": datetime.now(timezone.utc),
            }
        )

    async def list_events(self, letter_id: str, run_id: str) -> List[DraftLifecycleEvent]:
        cursor = self.events.find(
            {"letter_id": str(letter_id), "run_id": str(run_id)}
        ).sort("created_at", 1)
        docs = await cursor.to_list(length=200)
        return [DraftLifecycleEvent(**doc) for doc in docs]

    async def create_context_pack(self, pack: DraftContextPack) -> DraftContextPack:
        payload = pack.model_dump(by_alias=True, exclude_none=True)
        payload.pop("_id", None)
        
        # Compress data fields to save disk space
        meta_keys = {"context_pack_id", "letter_id", "run_id", "created_at"}
        data_to_compress = {k: v for k, v in payload.items() if k not in meta_keys}
        
        for k in list(payload.keys()):
            if k not in meta_keys:
                payload.pop(k)
                
        json_bytes = json.dumps(data_to_compress, default=str).encode("utf-8")
        compressed = gzip.compress(json_bytes)
        payload["compressed_data"] = Binary(compressed)
        
        await self.context_packs.insert_one(payload)
        return pack

    async def get_context_pack(self, letter_id: str, run_id: str) -> Optional[DraftContextPack]:
        doc = await self.context_packs.find_one(
            {"letter_id": str(letter_id), "run_id": str(run_id)},
            sort=[("created_at", -1)],
        )
        if not doc:
            return None
            
        if "compressed_data" in doc:
            compressed_bytes = doc.pop("compressed_data")
            decompressed_bytes = gzip.decompress(bytes(compressed_bytes))
            data = json.loads(decompressed_bytes.decode("utf-8"))
            doc.update(data)
            
        return DraftContextPack(**doc)

    async def upsert_assignment(self, assignment: DraftReviewAssignment) -> DraftReviewAssignment:
        payload = assignment.model_dump(by_alias=True, exclude_none=True)
        payload.pop("_id", None)
        await self.assignments.update_one(
            {
                "letter_id": assignment.letter_id,
                "run_id": assignment.run_id,
                "reviewer_user_id": assignment.reviewer_user_id,
                "status": "assigned",
            },
            {"$set": payload},
            upsert=True,
        )
        stored = await self.assignments.find_one(
            {
                "letter_id": assignment.letter_id,
                "run_id": assignment.run_id,
                "reviewer_user_id": assignment.reviewer_user_id,
                "status": "assigned",
            }
        )
        return DraftReviewAssignment(**stored) if stored else assignment

    async def list_assignments(self, letter_id: str, run_id: str) -> List[DraftReviewAssignment]:
        cursor = self.assignments.find(
            {"letter_id": str(letter_id), "run_id": str(run_id)}
        ).sort("created_at", 1)
        docs = await cursor.to_list(length=100)
        return [DraftReviewAssignment(**doc) for doc in docs]

    async def add_comment(self, comment: DraftReviewComment) -> DraftReviewComment:
        payload = comment.model_dump(by_alias=True, exclude_none=True)
        payload.pop("_id", None)
        await self.comments.insert_one(payload)
        stored = await self.comments.find_one({"comment_id": comment.comment_id})
        return DraftReviewComment(**stored) if stored else comment

    async def list_comments(self, letter_id: str, run_id: str) -> List[DraftReviewComment]:
        cursor = self.comments.find(
            {"letter_id": str(letter_id), "run_id": str(run_id)}
        ).sort("created_at", 1)
        docs = await cursor.to_list(length=200)
        return [DraftReviewComment(**doc) for doc in docs]

    async def save_strategy_plan(
        self,
        letter_id: str,
        run: DraftRun,
        saved_by: Optional[str],
        *,
        status: str = "generated",
    ) -> int:
        existing = await self.db.letters.find_one(
            {"_id": ObjectId(letter_id)},
            {"strategy_versions": 1},
        )
        last_version = 0
        for version in (existing or {}).get("strategy_versions", []) or []:
            try:
                last_version = max(last_version, int(version.get("version", 0)))
            except Exception:
                continue
        next_version = last_version + 1
        now = datetime.now(timezone.utc)
        version_entry = {
            "version": next_version,
            "status": status,
            "plan": run.plan or "",
            "planning_sheet": run.planning_sheet.model_dump() if run.planning_sheet else None,
            "reply_matrix": [row.model_dump() for row in run.reply_matrix],
            "incoming_analysis": (
                run.incoming_analysis.model_dump() if run.incoming_analysis else None
            ),
            "source_ids": [source.source_id for source in run.sources],
            "run_id": run.run_id,
            "created_at": now,
            "created_by": saved_by,
        }
        await self.db.letters.update_one(
            {"_id": ObjectId(letter_id)},
            {
                "$set": {
                    "strategy_plan": run.plan or "",
                    "draft_plan": run.plan or "",
                    "strategy_run_id": run.run_id,
                    "strategy_graph_status": run.status,
                    "current_strategy_version": next_version,
                    "updated_at": now,
                },
                "$push": {"strategy_versions": version_entry},
            },
        )
        return next_version

    async def mark_accepted_plan(self, letter_id: str, run: DraftRun, accepted_by: Optional[str]) -> int:
        version = await self.save_strategy_plan(
            letter_id,
            run,
            accepted_by,
            status="accepted",
        )
        update = {
            "strategy_plan": run.plan or "",
            "draft_plan": run.plan or "",
            "strategy_run_id": run.run_id,
            "strategy_graph_status": run.status,
            "strategy_plan_approved_by": accepted_by,
            "strategy_plan_approved_at": datetime.now(timezone.utc),
            "accepted_strategy_version": version,
            "updated_at": datetime.now(timezone.utc),
        }
        await self.db.letters.update_one({"_id": ObjectId(letter_id)}, {"$set": update})
        return version

    async def accept_draft(self, letter_id: str, run: DraftRun, accepted_by: Optional[str]) -> int:
        existing = await self.db.letters.find_one(
            {"_id": ObjectId(letter_id)},
            {"draft_versions": 1},
        )
        last_version = 0
        for version in (existing or {}).get("draft_versions", []) or []:
            try:
                last_version = max(last_version, int(version.get("version", 0)))
            except Exception:
                continue
        next_version = last_version + 1
        artifact = run.draft_artifact
        body = artifact.draft_letter if artifact else ""
        version_entry = {
            "version": next_version,
            "status": run.status,
            "body": body,
            "plan": run.plan,
            "sources": [source.model_dump() for source in run.sources],
            "reviewer_findings": [
                finding.model_dump() for finding in run.validation_report.findings
            ],
            "run_id": run.run_id,
            "locked": False,
            "created_at": datetime.now(timezone.utc),
            "created_by": accepted_by,
        }
        await self.db.letters.update_one(
            {"_id": ObjectId(letter_id)},
            {
                "$set": {
                    "draft_output": body,
                    "draft_plan": run.plan,
                    "graph_run_id": run.run_id,
                    "graph_status": run.status,
                    "reviewer_blocking": run.validation_report.blocking,
                    "reviewer_findings": [
                        finding.model_dump() for finding in run.validation_report.findings
                    ],
                    "draft_sources": [source.model_dump() for source in run.sources],
                    "current_draft_version": next_version,
                    "updated_at": datetime.now(timezone.utc),
                },
                "$push": {"draft_versions": version_entry},
            },
        )
        return next_version

    async def lock_approved_draft_version(
        self,
        letter_id: str,
        run_id: str,
        approved_by: Optional[str],
    ) -> Optional[int]:
        existing = await self.db.letters.find_one(
            {"_id": ObjectId(letter_id)},
            {"draft_versions": 1, "current_draft_version": 1},
        )
        if not existing:
            return None
        versions = list(existing.get("draft_versions", []) or [])
        approved_version: Optional[int] = None
        now = datetime.now(timezone.utc)
        for version in versions:
            if str(version.get("run_id") or "") != str(run_id):
                continue
            version["locked"] = True
            version["approved_by"] = approved_by
            version["approved_at"] = now
            version["status"] = "approved"
            try:
                approved_version = int(version.get("version", 0))
            except Exception:
                approved_version = existing.get("current_draft_version")
            break
        if approved_version is None:
            return None
        await self.db.letters.update_one(
            {"_id": ObjectId(letter_id)},
            {
                "$set": {
                    "draft_versions": versions,
                    "approved_draft_version": approved_version,
                    "approved_run_id": run_id,
                    "approved_by": approved_by,
                    "approved_at": now,
                    "approved_version_locked": True,
                    "updated_at": now,
                }
            },
        )
        return approved_version
