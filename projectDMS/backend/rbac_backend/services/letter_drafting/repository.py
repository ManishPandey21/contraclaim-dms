from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import Binary, ObjectId
from pymongo.errors import DuplicateKeyError

from ...models.letter_drafting import (
    DraftEvidenceSnapshot,
    DraftExecutionEffect,
    DraftInputSnapshot,
    DraftOutboxEvent,
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
        self.input_snapshots = db["letter_draft_input_snapshots"]
        self.evidence_snapshots = db["letter_draft_evidence_snapshots"]
        self.effects = db["letter_draft_effects"]
        self.outbox = db["letter_draft_outbox"]
        self.shadow_comparisons = db["letter_draft_shadow_comparisons"]

    async def create(self, run: DraftRun) -> DraftRun:
        payload = run.model_dump(by_alias=True, exclude_none=True)
        payload.pop("_id", None)
        try:
            await self.collection.insert_one(payload)
        except DuplicateKeyError:
            # A duplicate (letter_id, idempotency_key) is an expected race
            # between two equivalent client retries.  The caller receives the
            # original immutable run instead of launching duplicate work.
            if run.idempotency_key:
                existing = await self.collection.find_one(
                    {"letter_id": run.letter_id, "idempotency_key": run.idempotency_key}
                )
                if existing:
                    return DraftRun(**existing)
            raise
        stored = await self.collection.find_one({"run_id": run.run_id})
        return DraftRun(**stored) if stored else run

    async def get_by_idempotency_key(
        self, letter_id: str, idempotency_key: str
    ) -> Optional[DraftRun]:
        doc = await self.collection.find_one(
            {"letter_id": str(letter_id), "idempotency_key": str(idempotency_key)}
        )
        return DraftRun(**doc) if doc else None

    async def get(self, letter_id: str, run_id: str) -> Optional[DraftRun]:
        doc = await self.collection.find_one(
            {"letter_id": str(letter_id), "run_id": str(run_id)}
        )
        return DraftRun(**doc) if doc else None

    async def get_by_run_id(self, run_id: str) -> Optional[DraftRun]:
        doc = await self.collection.find_one({"run_id": str(run_id)})
        return DraftRun(**doc) if doc else None

    async def compare_and_set_execution(
        self,
        letter_id: str,
        run_id: str,
        expected_state_version: int,
        fields: Dict[str, Any],
    ) -> Optional[DraftRun]:
        """Atomically apply a user/worker transition and advance its ETag."""
        payload: Dict[str, Any] = {}
        for key, value in fields.items():
            payload[key] = value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else value
        payload["updated_at"] = datetime.now(timezone.utc)
        result = await self.collection.update_one(
            {
                "letter_id": str(letter_id),
                "run_id": str(run_id),
                "state_version": int(expected_state_version),
            },
            {"$set": payload, "$inc": {"state_version": 1}},
        )
        if not getattr(result, "modified_count", 0):
            return None
        return await self.get(letter_id, run_id)

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
        now = datetime.now(timezone.utc)
        payload["updated_at"] = now
        terminal_statuses = {"completed", "blocked", "needs_attention", "failed", "approved", "exported", "issued", "cancelled"}
        if (
            "status" in payload
            and payload["status"] in terminal_statuses
            and "completed_at" not in payload
        ):
            payload["completed_at"] = now
        await self.collection.update_one(
            {"letter_id": str(letter_id), "run_id": str(run_id)},
            {"$set": payload},
        )
        return await self.get(letter_id, run_id)

    @staticmethod
    def _payload_hash(payload: Dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    async def create_immutable_snapshots(self, run: DraftRun) -> Dict[str, str]:
        """Persist immutable inputs and a text-free evidence ledger once per run."""
        input_payload = dict(run.inputs or {})
        input_snapshot = DraftInputSnapshot(
            snapshot_id=str(uuid.uuid4()),
            letter_id=run.letter_id,
            run_id=run.run_id,
            payload=input_payload,
            payload_hash=self._payload_hash(input_payload),
        )
        evidence_rows = [
            {
                "source_id": source.source_id,
                "source_type": source.source_type,
                "allowed_use": source.allowed_use,
                "organization_id": source.organization_id,
                "project_id": source.project_id,
                "document_id": source.document_id,
                "letter_id": source.letter_id,
                "clause_number": source.clause_number,
                "page_numbers": list(source.page_numbers or []),
                "source_hash": source.source_hash,
                "metadata": source.metadata,
            }
            for source in run.sources
        ]
        context_hash = self._payload_hash(
            {"context": run.context_bundle.model_dump(mode="json"), "sources": evidence_rows}
        )
        evidence_snapshot = DraftEvidenceSnapshot(
            snapshot_id=str(uuid.uuid4()),
            letter_id=run.letter_id,
            run_id=run.run_id,
            sources=evidence_rows,
            context_hash=context_hash,
        )
        try:
            await self.input_snapshots.insert_one(input_snapshot.model_dump(mode="python"))
            await self.evidence_snapshots.insert_one(evidence_snapshot.model_dump(mode="python"))
        except DuplicateKeyError:
            # Reconciliation may be safely retried after an interrupted write.
            existing_input = await self.input_snapshots.find_one({"run_id": run.run_id})
            existing_evidence = await self.evidence_snapshots.find_one({"run_id": run.run_id})
            if not existing_input or not existing_evidence:
                raise
            input_snapshot = DraftInputSnapshot(**existing_input)
            evidence_snapshot = DraftEvidenceSnapshot(**existing_evidence)
        return {
            "input_snapshot_id": input_snapshot.snapshot_id,
            "context_snapshot_id": evidence_snapshot.snapshot_id,
            "input_snapshot_hash": input_snapshot.payload_hash,
            "context_snapshot_hash": evidence_snapshot.context_hash,
        }

    async def record_effect(self, effect: DraftExecutionEffect) -> DraftExecutionEffect:
        payload = effect.model_dump(mode="python")
        try:
            await self.effects.insert_one(payload)
            return effect
        except DuplicateKeyError:
            existing = await self.effects.find_one({"effect_key": effect.effect_key})
            return DraftExecutionEffect(**existing) if existing else effect

    async def get_effect(self, effect_key: str) -> Optional[DraftExecutionEffect]:
        existing = await self.effects.find_one({"effect_key": str(effect_key)})
        return DraftExecutionEffect(**existing) if existing else None

    async def complete_effect(
        self,
        effect_key: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        fields: Dict[str, Any] = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc),
        }
        if metadata is not None:
            fields["metadata"] = metadata
        await self.effects.update_one(
            {"effect_key": str(effect_key)},
            {"$set": fields},
        )

    async def enqueue_outbox(self, event: DraftOutboxEvent) -> DraftOutboxEvent:
        payload = event.model_dump(mode="python")
        try:
            await self.outbox.insert_one(payload)
            return event
        except DuplicateKeyError:
            existing = await self.outbox.find_one({"event_id": event.event_id})
            return DraftOutboxEvent(**existing) if existing else event

    async def reconciliation_candidates(self, *, limit: int = 100) -> List[DraftRun]:
        """Find graph runs whose snapshots/checkpoints need safe operator repair."""
        cursor = self.collection.find(
            {
                "engine": "langgraph_v3",
                "execution_status": {"$in": ["queued", "running", "awaiting_user_direction", "awaiting_strategy_confirmation"]},
            }
        ).sort("updated_at", 1).limit(max(1, min(int(limit), 500)))
        docs = await cursor.to_list(length=max(1, min(int(limit), 500)))
        return [DraftRun(**doc) for doc in docs]

    async def has_completed_effects(self, run_id: str) -> bool:
        return bool(await self.effects.find_one({"run_id": str(run_id), "status": "completed"}))

    async def record_shadow_baseline(self, run: DraftRun) -> None:
        """Capture comparable, hashed v2 output without invoking external effects."""
        artifacts = {
            "source_ledger_hash": self._payload_hash({"sources": [source.model_dump(mode="json") for source in run.sources]}),
            "draft_hash": self._payload_hash({"draft": run.draft_artifact.draft_letter if run.draft_artifact else ""}),
            "validation_codes": [finding.code for finding in run.validation_report.findings],
            "legal_risk_count": len(run.legal_risk_report.flags) if run.legal_risk_report else 0,
        }
        await self.shadow_comparisons.update_one(
            {"run_id": run.run_id},
            {"$set": {"run_id": run.run_id, "engine": run.engine, "artifacts": artifacts, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

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

    async def _accept_draft_document(
        self,
        letter_id: str,
        run: DraftRun,
        accepted_by: Optional[str],
        *,
        lock: bool = False,
        session: Any = None,
    ) -> int:
        find_kwargs = {"session": session} if session is not None else {}
        existing = await self.db.letters.find_one(
            {"_id": ObjectId(letter_id)},
            {"draft_versions": 1},
            **find_kwargs,
        )
        if not existing:
            raise RuntimeError("Letter disappeared while accepting draft")
        original_versions = list(existing.get("draft_versions", []) or [])
        versions = [dict(version) for version in original_versions]
        last_version = 0
        matched = None
        for version in versions:
            try:
                last_version = max(last_version, int(version.get("version", 0)))
            except Exception:
                continue
            if str(version.get("run_id") or "") == str(run.run_id):
                matched = version
        artifact = run.draft_artifact
        body = artifact.draft_letter if artifact else ""
        now = datetime.now(timezone.utc)
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if matched is None:
            matched = {
                "version": last_version + 1,
                "status": "approved" if lock else run.status,
                "body": body,
                "body_hash": body_hash,
                "plan": run.plan,
                "sources": [source.model_dump() for source in run.sources],
                "reviewer_findings": [
                    finding.model_dump() for finding in run.validation_report.findings
                ],
                "run_id": run.run_id,
                "locked": lock,
                "created_at": now,
                "created_by": accepted_by,
            }
            versions.append(matched)
        elif hashlib.sha256(str(matched.get("body") or "").encode("utf-8")).hexdigest() != body_hash:
            raise RuntimeError("Accepted draft run is already bound to different immutable content")
        if lock:
            matched.update(
                {
                    "locked": True,
                    "approved_by": accepted_by,
                    "approved_at": now,
                    "status": "approved",
                }
            )
        version_number = int(matched.get("version", last_version + 1))
        update = {
            "draft_versions": versions,
            "draft_output": body,
            "draft_plan": run.plan,
            "graph_run_id": run.run_id,
            "governed_draft_hash": body_hash,
            "graph_status": "approved" if lock else run.status,
            "reviewer_blocking": run.validation_report.blocking,
            "reviewer_findings": [
                finding.model_dump() for finding in run.validation_report.findings
            ],
            "draft_sources": [source.model_dump() for source in run.sources],
            "current_draft_version": version_number,
            "updated_at": now,
        }
        if lock:
            update.update(
                {
                    "approved_draft_version": version_number,
                    "approved_run_id": run.run_id,
                    "approved_by": accepted_by,
                    "approved_at": now,
                    "approved_version_locked": True,
                }
            )
        update_kwargs = {"session": session} if session is not None else {}
        await self.db.letters.update_one(
            {"_id": ObjectId(letter_id)},
            {"$set": update},
            **update_kwargs,
        )
        return version_number

    async def accept_draft(
        self,
        letter_id: str,
        run: DraftRun,
        accepted_by: Optional[str],
        *,
        lock: bool = False,
    ) -> int:
        """Idempotently bind one run to one letter version in a single write."""
        return await self._accept_draft_document(
            letter_id,
            run,
            accepted_by,
            lock=lock,
        )

    async def finalize_draft_approval(
        self,
        letter_id: str,
        run: DraftRun,
        approvals: List[Any],
        approved_by: Optional[str],
        approved_at: datetime,
    ) -> tuple[DraftRun, int]:
        """Atomically lock the letter artifact and approve its governed run."""

        fields = {
            "approvals": [
                item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
                for item in approvals
            ],
            "approval_status": "approved",
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": approved_at,
            "updated_at": approved_at,
            "completed_at": approved_at,
        }
        client = getattr(self.db, "client", None)
        if client is None or not hasattr(client, "start_session"):
            version = await self._accept_draft_document(
                letter_id, run, approved_by, lock=True
            )
            await self.collection.update_one(
                {"letter_id": str(letter_id), "run_id": str(run.run_id)},
                {"$set": fields},
            )
        else:
            async with await client.start_session() as session:
                async with session.start_transaction():
                    version = await self._accept_draft_document(
                        letter_id,
                        run,
                        approved_by,
                        lock=True,
                        session=session,
                    )
                    result = await self.collection.update_one(
                        {"letter_id": str(letter_id), "run_id": str(run.run_id)},
                        {"$set": fields},
                        session=session,
                    )
                    if not getattr(result, "matched_count", 0):
                        raise RuntimeError("Draft run disappeared during final approval")
        updated = await self.get(letter_id, run.run_id)
        if not updated:
            raise RuntimeError("Approved draft run could not be reloaded")
        return updated, version

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
