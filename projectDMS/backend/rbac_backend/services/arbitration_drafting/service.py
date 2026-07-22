from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from ...models.arbitration_drafting import (
    ArbitrationClaimHead,
    ArbitrationDraft,
    ArbitrationDraftCreate,
    ArbitrationDraftDetail,
    ArbitrationDraftStatus,
    ArbitrationDraftUpdate,
    ArbitrationDraftVersion,
    ArbitrationEvidenceSearchRequest,
    ArbitrationGenerationRun,
    ArbitrationGenerateRequest,
    ArbitrationParagraphResponse,
    ArbitrationParagraphResponseCreate,
    ArbitrationParagraphResponseUpdate,
    ArbitrationSelectedReference,
    ArbitrationSelectedReferenceCreate,
    GenerationRunStatus,
    GenerationRunType,
    ParagraphResponseType,
    PleadingImportRequest,
)
from ..audit_event_service import AuditEventService
from .context import ArbitrationContextBuilder, condense
from .exporter import ArbitrationDraftExporter
from .generator import ArbitrationDraftGenerator, PROMPT_VERSION, SECTION_KEYS_BY_DRAFT_TYPE, SOURCE_POLICY
from .llm_generator import LLMDraftGenerator
from .case_workspace import ArbitrationCaseWorkspaceService
from .repository import ArbitrationDraftingRepository, _jsonable
from .validator import ArbitrationDraftValidator
from .approval_policy import enforce_author_approver_separation, resolve_gate_role
from .workflow_governance import (
    require_approved_plan_for_generation,
    require_governed_workflow_chain,
)
from .paragraph_positions import replace_paragraph_position_projections, sync_paragraph_position_projection


def _actor_id(user: Any) -> Optional[str]:
    return getattr(user, "id", None) or getattr(user, "email", None)


_DRAFT_HASH_FIELDS = [
    "case_id",
    "organization_id",
    "project_id",
    "contract_id",
    "draft_type",
    "party_role",
    "dispute_type",
    "title",
    "case_details",
    "tribunal_details",
    "arbitration_clause",
    "governing_law",
    "relief_sought",
    "manual_facts",
    "claim_amount",
    "currency",
    "interest_rate",
]


def stable_generation_input_hash(
    context: Dict[str, Any],
    *,
    section_key: Optional[str] = None,
    additional_instruction: Optional[str] = None,
    include_unverified_graph_links: bool = False,
    run_type: GenerationRunType | str = GenerationRunType.FULL_DRAFT,
    draft_mode: Optional[str] = None,
) -> str:
    draft = context.get("draft") or {}
    payload = {
        "draft": {key: draft.get(key) for key in _DRAFT_HASH_FIELDS},
        "source_hashes": [row.get("source_hash") for row in context.get("source_ledger") or []],
        "matrix_context": context.get("matrix_context") or {},
        "claim_heads": context.get("claim_heads") or [],
        "paragraph_responses": context.get("paragraph_responses") or [],
        "missing_evidence": context.get("missing_evidence") or [],
        "pleading_plan_hash": (context.get("pleading_plan") or {}).get("plan_hash"),
        "section_key": section_key,
        "additional_instruction": additional_instruction,
        "include_unverified_graph_links": include_unverified_graph_links,
        "run_type": getattr(run_type, "value", run_type),
        "draft_mode": str(draft_mode or "deterministic").lower(),
        "prompt_version": PROMPT_VERSION,
        "source_policy": SOURCE_POLICY,
    }
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def immutable_version_hash(version: Dict[str, Any]) -> str:
    payload = {
        "draft_id": version.get("draft_id"),
        "version": version.get("version"),
        "parent_version_id": version.get("parent_version_id"),
        "full_markdown": version.get("full_markdown") or "",
        "sections": version.get("sections") or [],
        "source_ledger": version.get("source_ledger") or [],
        "validation_status": version.get("validation_status"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ArbitrationDraftingService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.repo = ArbitrationDraftingRepository(db)
        self.audit = AuditEventService(db)
        self.context_builder = ArbitrationContextBuilder(db)
        self.case_workspace = ArbitrationCaseWorkspaceService(db)
        self.generator = ArbitrationDraftGenerator()
        self.validator = ArbitrationDraftValidator()
        self.exporter = ArbitrationDraftExporter()

    async def create_draft(self, payload: ArbitrationDraftCreate, current_user: Any) -> Dict[str, Any]:
        draft_payload = payload.model_dump(exclude={"selected_references", "claim_heads"})
        draft_payload["organization_id"] = payload.organization_id or getattr(current_user, "organization_id", None)
        draft = ArbitrationDraft(
            **draft_payload,
            created_by=_actor_id(current_user),
            updated_at=datetime.utcnow(),
        ).model_dump(by_alias=True)
        trusted_refs = await self.context_builder.rehydrate_selected_references(
            draft,
            [ref.model_dump() for ref in payload.selected_references],
        )
        await self.repo.create_draft(draft)
        refs = [
            ArbitrationSelectedReference(**ref, draft_id=draft["_id"], selected_by=_actor_id(current_user)).model_dump(by_alias=True)
            for ref in trusted_refs
        ]
        heads = [ArbitrationClaimHead(**head.model_dump(), draft_id=draft["_id"]).model_dump(by_alias=True) for head in payload.claim_heads]
        await self.repo.replace_references(draft["_id"], refs)
        await self.repo.replace_claim_heads(draft["_id"], heads)
        await self._emit("created", draft, current_user, after=draft)
        return await self.detail(draft["_id"])

    async def detail(self, draft_id: str) -> Dict[str, Any]:
        draft = await self.repo.get_draft(draft_id)
        if not draft:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration draft not found")
        refs = await self.repo.list_references(draft_id)
        heads = await self.repo.list_claim_heads(draft_id)
        paragraphs = await self.repo.list_paragraph_responses(draft_id)
        latest = await self.repo.latest_version(draft_id)
        return {
            **draft,
            "selected_references": refs,
            "claim_heads": heads,
            "paragraph_responses": paragraphs,
            "latest_version": latest,
        }

    async def list_drafts(self, scope: Dict[str, Any], filters: Dict[str, Any], *, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        query = dict(scope or {})
        for key in ["project_id", "contract_id", "draft_type", "party_role", "dispute_type", "status"]:
            if filters.get(key):
                query[key] = filters[key]
        if filters.get("q"):
            query["title"] = {"$regex": str(filters["q"]), "$options": "i"}
        return await self.repo.list_drafts(query, skip=skip, limit=limit)

    async def update_draft(self, draft_id: str, payload: ArbitrationDraftUpdate, current_user: Any) -> Dict[str, Any]:
        draft = await self._load_unlocked(draft_id)
        update = payload.model_dump(exclude_unset=True)
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = _actor_id(current_user)
        updated = await self.repo.update_draft(draft_id, update)
        await self._emit("updated", draft, current_user, before=draft, after=updated)
        return await self.detail(draft_id)

    async def delete_draft(self, draft_id: str, current_user: Any) -> None:
        draft = await self._load_unlocked(draft_id)
        await self.repo.soft_delete_draft(
            draft_id,
            {"deleted_at": datetime.utcnow(), "deleted_by": _actor_id(current_user), "updated_at": datetime.utcnow()},
        )
        await self._emit("deleted", draft, current_user)

    async def evidence_search(
        self,
        draft_id: str,
        payload: ArbitrationEvidenceSearchRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        draft = await self._load(draft_id)
        query_text = payload.query or draft.get("title") or ""
        results: List[ArbitrationSelectedReferenceCreate] = []
        results.extend(await self._search_documents(draft, query_text, payload.limit))
        results.extend(await self._search_clauses(draft, query_text, payload.limit))
        return {"results": [item.model_dump() for item in results[: payload.limit]]}

    async def refresh_source_ledger(
        self,
        draft_id: str,
        current_user: Any,
        *,
        include_unverified_graph_links: bool = False,
    ) -> Dict[str, Any]:
        context = await self._context(draft_id, current_user, include_unverified_graph_links=include_unverified_graph_links)
        return {
            "draft_id": draft_id,
            "source_count": len(context["source_ledger"]),
            "sources": context["source_ledger"],
            "missing_evidence": context["missing_evidence"],
        }

    async def add_references(
        self,
        draft_id: str,
        payloads: List[ArbitrationSelectedReferenceCreate],
        current_user: Any,
    ) -> Dict[str, Any]:
        draft = await self._load_unlocked(draft_id)
        trusted_refs = await self.context_builder.rehydrate_selected_references(
            draft,
            [payload.model_dump() for payload in payloads],
        )
        rows = []
        for payload in trusted_refs:
            trusted = dict(payload)
            trusted.pop("draft_id", None)
            trusted.pop("_id", None)
            rows.append(
                ArbitrationSelectedReference(
                    **trusted,
                    draft_id=draft_id,
                    selected_by=_actor_id(current_user),
                ).model_dump(by_alias=True)
            )
        await self.repo.add_references(draft_id, rows)
        if draft.get("case_id"):
            await self.case_workspace.invalidate_readiness_approvals(
                str(draft.get("case_id")), "selected_evidence_changed", current_user
            )
        await self._emit(
            "references_added",
            draft,
            current_user,
            after={"count": len(rows), "source_ids": [row.get("source_id") for row in rows]},
        )
        return await self.detail(draft_id)

    async def remove_reference(self, draft_id: str, reference_id: str, current_user: Any) -> Dict[str, Any]:
        draft = await self._load_unlocked(draft_id)
        deleted = await self.repo.delete_reference(draft_id, reference_id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected reference not found")
        if draft.get("case_id"):
            await self.case_workspace.invalidate_readiness_approvals(
                str(draft.get("case_id")), "selected_evidence_changed", current_user
            )
        await self._emit("reference_removed", draft, current_user, after={"reference_id": reference_id})
        return await self.detail(draft_id)

    async def import_pleading_paragraphs(
        self,
        draft_id: str,
        payload: PleadingImportRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        draft = await self._load_unlocked(draft_id)
        source_version_id = payload.source_pleading_version_id
        source_version_hash = None
        if payload.source_pleading_document_id:
            source = await self.db.documents.find_one(
                {
                    "_id": payload.source_pleading_document_id,
                    "organization_id": draft.get("organization_id"),
                    "project_id": draft.get("project_id"),
                }
            )
            if source:
                source_version_id = source_version_id or source.get("current_version_id")
                source_version_hash = source.get("sha256") or hashlib.sha256(
                    json.dumps({key: source.get(key) for key in ("_id", "current_version_id", "updated_at")}, sort_keys=True, default=str).encode()
                ).hexdigest()
            else:
                opponent = await self.db.arbitration_drafts.find_one(
                    {"_id": payload.source_pleading_document_id, "case_id": draft.get("case_id"), "deleted_at": {"$exists": False}}
                )
                version = await self.db.arbitration_draft_versions.find_one(
                    {"draft_id": payload.source_pleading_document_id, "_id": source_version_id}
                ) if source_version_id else None
                if not opponent or not version:
                    raise HTTPException(status_code=400, detail="Opponent pleading source/version is unavailable or outside case scope")
                source_version_hash = version.get("version_hash") or immutable_version_hash(version)
        rows = [
            ArbitrationParagraphResponse(
                draft_id=draft_id,
                source_pleading_document_id=payload.source_pleading_document_id,
                source_pleading_version_id=source_version_id,
                source_pleading_version_hash=source_version_hash,
                source_pleading_type=payload.source_pleading_type,
                source_paragraph_number=number,
                source_paragraph_text=text,
                response_type=ParagraphResponseType.REQUIRE_PROOF,
                response_text="[Evidence required]",
                missing_evidence=["Supporting response evidence not selected."],
            ).model_dump(by_alias=True)
            for number, text in self._split_paragraphs(payload.text)
        ]
        saved = await self.repo.replace_paragraph_responses(draft_id, rows)
        await replace_paragraph_position_projections(
            self.db,
            draft,
            saved,
            actor_id=_actor_id(current_user),
        )
        draft = await self.repo.get_draft(draft_id)
        if draft:
            await self._emit("paragraphs_imported", draft, current_user, after={"count": len(saved), "source_type": payload.source_pleading_type})
        return {"draft_id": draft_id, "count": len(saved), "paragraph_responses": saved}

    async def update_paragraph_response(
        self, draft_id: str, response_id: str, payload: ArbitrationParagraphResponseUpdate, current_user: Any
    ) -> Dict[str, Any]:
        draft = await self._load_unlocked(draft_id)
        supporting_ids = sorted(set(payload.supporting_source_ids))
        if supporting_ids:
            selected = await self.repo.list_references(draft_id)
            ledger = await self.context_builder.rehydrate_selected_references(
                draft,
                [row for row in selected if str(row.get("source_id")) in supporting_ids],
            )
            if {str(row.get("source_id")) for row in ledger} != set(supporting_ids):
                raise HTTPException(status_code=400, detail="One or more paragraph response sources are not authoritative and scoped")
        missing = list(payload.missing_evidence)
        if payload.response_type == ParagraphResponseType.DENY and not (payload.response_reason or "").strip():
            raise HTTPException(status_code=422, detail="A denial requires a reason")
        if not supporting_ids and payload.response_type in {ParagraphResponseType.DENY, ParagraphResponseType.REQUIRE_PROOF}:
            missing = missing or ["Supporting response evidence not selected."]
        updated = await self.repo.update_paragraph_response(
            draft_id,
            response_id,
            {
                **payload.model_dump(mode="json"),
                "supporting_source_ids": supporting_ids,
                "missing_evidence": missing,
                "last_material_editor_id": _actor_id(current_user),
                "updated_by": _actor_id(current_user),
                "updated_at": datetime.utcnow(),
            },
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Paragraph response not found")
        await sync_paragraph_position_projection(
            self.db,
            draft,
            updated,
            actor_id=_actor_id(current_user),
        )
        if draft.get("case_id"):
            await self.case_workspace.invalidate_readiness_approvals(str(draft["case_id"]), "paragraph_response_changed", current_user)
        await self._emit("paragraph_response_updated", draft, current_user, after={"response_id": response_id})
        return updated

    async def generate(
        self,
        draft_id: str,
        payload: ArbitrationGenerateRequest,
        current_user: Any,
        *,
        run_type: GenerationRunType = GenerationRunType.FULL_DRAFT,
        pleading_plan: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        draft = await self._load_unlocked(draft_id)
        await self.case_workspace.assert_case_ready_for_draft(draft, allow_standalone_working_draft=True)
        if draft.get("case_id") and run_type == GenerationRunType.FULL_DRAFT:
            if not pleading_plan:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Case-linked full drafting must be started from an approved pleading-plan workflow gate",
                )
            await require_approved_plan_for_generation(self.db, pleading_plan)
        self._validate_section_key(draft, payload.section_key)
        context = await self._context(
            draft_id,
            current_user,
            include_unverified_graph_links=payload.include_unverified_graph_links,
        )
        if pleading_plan:
            context["pleading_plan"] = dict(pleading_plan)
        latest = await self.repo.latest_version(draft_id)
        if run_type == GenerationRunType.SECTION_REGENERATION:
            if not payload.section_key:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="section_key is required for section regeneration")
            if not latest:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A complete parent version is required before section regeneration",
                )
            context["source_ledger"] = self._merge_source_ledgers(
                latest.get("source_ledger") or [], context.get("source_ledger") or []
            )
        input_hash = stable_generation_input_hash(
            context,
            section_key=payload.section_key,
            additional_instruction=payload.additional_instruction,
            include_unverified_graph_links=payload.include_unverified_graph_links,
            run_type=run_type,
            draft_mode=payload.draft_mode,
        )
        latest_structured = (latest or {}).get("structured_output") or {}
        if latest and latest_structured.get("input_hash") == input_hash and latest_structured.get("prompt_version") == PROMPT_VERSION:
            await self._emit("generation_reused", draft, current_user, after={"version": latest.get("version"), "input_hash": input_hash})
            return await self.detail(draft_id)
        retrieval_queries = self._retrieval_queries(context)
        generator, run_model, run_prompt_version = self._resolve_generator(payload.draft_mode, context)
        run = ArbitrationGenerationRun(
            draft_id=draft_id,
            run_type=run_type,
            section_key=payload.section_key,
            status=GenerationRunStatus.RUNNING,
            input_hash=input_hash,
            retrieval_queries=retrieval_queries,
            prompt_version=run_prompt_version,
            model=run_model,
            source_ids=[row.get("source_id") for row in context["source_ledger"]],
            created_by=_actor_id(current_user),
            started_at=datetime.utcnow(),
        ).model_dump(by_alias=True)
        await self.repo.create_generation_run(run)
        try:
            generated = generator.generate(
                context,
                section_key=payload.section_key,
                additional_instruction=payload.additional_instruction,
            )
            if inspect.isawaitable(generated):
                generated = await generated
            parent_version_id = None
            parent_version = None
            if run_type == GenerationRunType.SECTION_REGENERATION:
                generated = self._merge_regenerated_section(
                    draft,
                    context,
                    latest or {},
                    generated,
                    str(payload.section_key),
                    payload.additional_instruction,
                )
                parent_version_id = str((latest or {}).get("_id") or "") or None
                parent_version = (latest or {}).get("version")
            safety_report = self.validator.validation_report(context, generated["full_markdown"])
            warnings = self._merge_warnings(context.get("context_warnings") or [], safety_report["warnings"])
            structured_output = {
                **generated["structured_output"],
                "input_hash": input_hash,
                "retrieval_queries": retrieval_queries,
                "validation_warnings": warnings,
                "approval_blockers": safety_report["approval_blockers"],
                "legal_review_required": bool(warnings or generated["missing_evidence"]),
                "context_warnings": context.get("context_warnings") or [],
            }
            version_no = await self.repo.next_version(draft_id)
            version = ArbitrationDraftVersion(
                draft_id=draft_id,
                version=version_no,
                status=ArbitrationDraftStatus.DRAFT,
                sections=generated["sections"],
                full_markdown=generated["full_markdown"],
                structured_output=structured_output,
                source_ledger=context["source_ledger"],
                missing_evidence=generated["missing_evidence"],
                paragraph_responses=context["paragraph_responses"],
                claim_heads=context["claim_heads"],
                annexures=generated["annexures"],
                warnings=warnings,
                validation_status="blocked" if safety_report["approval_blockers"] else ("needs_review" if warnings else "passed"),
                ai_prompt_version=generated["ai_prompt_version"],
                model=generated["model"],
                generation_run_id=run["_id"],
                parent_version_id=parent_version_id,
                parent_version=parent_version,
                created_by=_actor_id(current_user),
            ).model_dump(by_alias=True)
            version["version_hash"] = immutable_version_hash(version)
            await self.repo.create_version(version)
            await self.repo.update_generation_run(
                run["_id"],
                {
                    "status": GenerationRunStatus.COMPLETED.value,
                    "completed_at": datetime.utcnow(),
                    "parsed_output": structured_output,
                    "raw_output": generated["full_markdown"],
                    "warnings": warnings,
                },
            )
            await self.repo.update_draft(
                draft_id,
                {
                    "status": ArbitrationDraftStatus.DRAFT.value,
                    "current_version": version_no,
                    "latest_generation_run_id": run["_id"],
                    "updated_at": datetime.utcnow(),
                    "updated_by": _actor_id(current_user),
                },
            )
            await self._emit("generated", draft, current_user, after={"version": version_no, "warnings": warnings})
            return await self.detail(draft_id)
        except Exception as exc:
            await self.repo.update_generation_run(
                run["_id"],
                {"status": GenerationRunStatus.FAILED.value, "completed_at": datetime.utcnow(), "error_message": str(exc)},
            )
            await self.repo.update_draft(draft_id, {"status": ArbitrationDraftStatus.FAILED.value, "updated_at": datetime.utcnow()})
            raise

    async def create_manual_version(self, draft_id: str, full_markdown: str, current_user: Any) -> Dict[str, Any]:
        draft = await self._load_unlocked(draft_id)
        context = await self._context(draft_id, current_user)
        safety_report = self.validator.validation_report(context, full_markdown)
        warnings = self._merge_warnings(context.get("context_warnings") or [], safety_report["warnings"])
        version_no = await self.repo.next_version(draft_id)
        input_hash = stable_generation_input_hash(context, run_type="manual_version")
        version = ArbitrationDraftVersion(
            draft_id=draft_id,
            version=version_no,
            status=ArbitrationDraftStatus.DRAFT,
            full_markdown=full_markdown,
            structured_output={
                "draft_type": draft.get("draft_type"),
                "prompt_version": None,
                "source_policy": SOURCE_POLICY,
                "input_hash": input_hash,
                "validation_warnings": warnings,
                "approval_blockers": safety_report["approval_blockers"],
                "legal_review_required": bool(warnings or context["missing_evidence"]),
                "manual_version": True,
            },
            source_ledger=context["source_ledger"],
            missing_evidence=context["missing_evidence"],
            paragraph_responses=context["paragraph_responses"],
            claim_heads=context["claim_heads"],
            warnings=warnings,
            validation_status="blocked" if safety_report["approval_blockers"] else ("needs_review" if warnings else "passed"),
            created_by=_actor_id(current_user),
        ).model_dump(by_alias=True)
        version["version_hash"] = immutable_version_hash(version)
        await self.repo.create_version(version)
        await self.repo.update_draft(draft_id, {"current_version": version_no, "updated_at": datetime.utcnow(), "updated_by": _actor_id(current_user)})
        await self._emit("version_saved", draft, current_user, after={"version": version_no})
        return version

    async def list_versions(self, draft_id: str) -> List[Dict[str, Any]]:
        await self._load(draft_id)
        return await self.repo.list_versions(draft_id)

    async def get_version(self, draft_id: str, version: int) -> Dict[str, Any]:
        await self._load(draft_id)
        row = await self.repo.get_version(draft_id, version)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft version not found")
        return row

    async def approve(
        self,
        draft_id: str,
        current_user: Any,
        *,
        workflow_run: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        draft = await self._load(draft_id)
        resolve_gate_role("draft", current_user)
        await self.case_workspace.assert_case_ready_for_draft(draft)
        if draft.get("current_version", 0) < 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Generate or save a version before approval")
        latest = await self.repo.latest_version(draft_id)
        enforce_author_approver_separation(
            current_user,
            [draft.get("created_by"), draft.get("updated_by"), (latest or {}).get("created_by")],
            gate="final draft approval",
        )
        blockers = ((latest or {}).get("structured_output") or {}).get("approval_blockers") or []
        if blockers:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Legal drafting safety blockers must be resolved before approval", "blockers": blockers},
            )
        if (latest or {}).get("validation_status") != "passed" or (latest or {}).get("missing_evidence"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Only a fully validated immutable version can be approved",
                    "validation_status": (latest or {}).get("validation_status"),
                    "missing_evidence": (latest or {}).get("missing_evidence") or [],
                },
            )
        version_hash = (latest or {}).get("version_hash") or immutable_version_hash(latest or {})
        governed_run = await require_governed_workflow_chain(
            self.db,
            draft_id=draft_id,
            draft_version_hash=version_hash,
            required_gates=("readiness", "plan", "legal_review"),
            run=workflow_run,
            allowed_nodes={"draft_approval_gate"},
            allowed_statuses={"awaiting_draft_approval"},
        )
        updated = await self.repo.update_draft(
            draft_id,
            {
                "status": ArbitrationDraftStatus.APPROVED.value,
                "is_locked": True,
                "approved_by": _actor_id(current_user),
                "approved_at": datetime.utcnow(),
                "approved_version_id": (latest or {}).get("_id"),
                "approved_version": (latest or {}).get("version"),
                "approved_version_hash": version_hash,
                "workflow_run_id": governed_run.get("_id"),
                "workflow_plan_approval_receipt_id": governed_run["_resolved_approval_receipts"]["plan"],
                "workflow_legal_review_receipt_id": governed_run["_resolved_approval_receipts"]["legal_review"],
                "readiness_approval_receipt_id": (await self.case_workspace.require_readiness_approval(
                    await self.case_workspace.get_case(str(draft.get("case_id"))), draft
                )).get("_id"),
                "updated_at": datetime.utcnow(),
            },
        )
        await self._emit("approved", draft, current_user, after=updated)
        return await self.detail(draft_id)

    async def return_for_revision(self, draft_id: str, reason: str, current_user: Any) -> Dict[str, Any]:
        draft = await self._load(draft_id)
        updated = await self.repo.update_draft(
            draft_id,
            {
                "status": ArbitrationDraftStatus.UNDER_REVIEW.value,
                "is_locked": False,
                "approved_by": None,
                "approved_at": None,
                "approved_version_id": None,
                "approved_version": None,
                "approved_version_hash": None,
                "updated_at": datetime.utcnow(),
                "updated_by": _actor_id(current_user),
                "return_reason": reason,
            },
        )
        await self._emit("returned_for_revision", draft, current_user, after={"reason": reason})
        return await self.detail(draft_id)

    async def export(self, draft_id: str, fmt: str, current_user: Any) -> bytes:
        draft = await self._load(draft_id)
        resolve_gate_role("export", current_user)
        if not draft.get("case_id"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Standalone drafts can only be downloaded through the watermarked preview export",
            )
        if str(draft.get("status") or "") not in {
            ArbitrationDraftStatus.APPROVED.value,
            ArbitrationDraftStatus.EXPORTED.value,
        } or not draft.get("is_locked"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Draft must be approved and locked before filing export")
        approved_version_no = draft.get("approved_version")
        approved_version = await self.repo.get_version(draft_id, int(approved_version_no or 0)) if approved_version_no else None
        if not approved_version or str(approved_version.get("_id")) != str(draft.get("approved_version_id")):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approved immutable draft version is unavailable")
        version_hash = approved_version.get("version_hash") or immutable_version_hash(approved_version)
        if version_hash != draft.get("approved_version_hash"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approved draft version hash does not match")
        if approved_version.get("validation_status") != "passed" or approved_version.get("missing_evidence"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approved draft version has unresolved validation issues")
        governed_run = await require_governed_workflow_chain(
            self.db,
            draft_id=draft_id,
            draft_version_hash=version_hash,
            required_gates=("readiness", "plan", "legal_review", "draft", "export"),
            allowed_nodes={"complete"},
            allowed_statuses={"completed"},
        )
        case = await self.case_workspace.get_case(str(draft.get("case_id")))
        enforce_author_approver_separation(
            current_user,
            [draft.get("created_by"), draft.get("updated_by"), approved_version.get("created_by")],
            gate="filing export authorization",
        )
        receipt = await self.case_workspace.require_readiness_approval(case, draft)
        audit = await self.case_workspace.citation_audit(str(draft.get("case_id")), draft_ids={draft_id})
        if not audit.get("ok"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Citation or exhibit audit blocks filing export", "issues": audit.get("issues") or []},
            )
        authorization = {
            "_id": str(uuid.uuid4()),
            "gate": "filing_export",
            "case_id": str(draft.get("case_id")),
            "draft_id": draft_id,
            "draft_version_id": approved_version.get("_id"),
            "draft_version_hash": version_hash,
            "readiness_approval_receipt_id": receipt.get("_id"),
            "citation_audit_hash": hashlib.sha256(
                json.dumps(audit, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            "authorized_by": _actor_id(current_user),
            "authorized_at": datetime.utcnow(),
            "format": fmt,
            "workflow_run_id": governed_run.get("_id"),
            "workflow_approval_receipt_ids": governed_run.get("_resolved_approval_receipts") or {},
        }
        existing_authorization = await self.db.arbitration_export_authorizations.find_one(
            {"draft_id": draft_id, "draft_version_hash": version_hash, "format": fmt}
        )
        if existing_authorization:
            authorization = existing_authorization
        else:
            await self.db.arbitration_export_authorizations.insert_one(_jsonable(authorization))
        content = self.exporter.build_docx(approved_version) if fmt == "docx" else self.exporter.build_pdf(approved_version)
        await self.repo.update_draft(
            draft_id,
            {
                "status": ArbitrationDraftStatus.EXPORTED.value,
                "exported_by": _actor_id(current_user),
                "exported_at": datetime.utcnow(),
                "export_authorization_id": authorization["_id"],
                "updated_at": datetime.utcnow(),
            },
        )
        await self._emit("exported", draft, current_user, after={"format": fmt})
        return content

    async def preview_export(self, draft_id: str, fmt: str, current_user: Any) -> bytes:
        await self._load(draft_id)
        latest = await self.repo.latest_version(draft_id)
        if not latest:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No draft version available to preview")
        preview = {
            **latest,
            "full_markdown": "# DRAFT — NOT APPROVED FOR FILING\n\n" + str(latest.get("full_markdown") or ""),
        }
        await self._emit("preview_exported", await self._load(draft_id), current_user, after={"format": fmt})
        return self.exporter.build_docx(preview) if fmt == "docx" else self.exporter.build_pdf(preview)

    async def get_run(self, draft_id: str, run_id: str) -> Dict[str, Any]:
        await self._load(draft_id)
        run = await self.repo.get_generation_run(run_id)
        if not run or run.get("draft_id") != draft_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation run not found")
        return run

    async def _context(self, draft_id: str, current_user: Any, *, include_unverified_graph_links: bool = False) -> Dict[str, Any]:
        draft = await self._load(draft_id)
        return await self.context_builder.build(
            draft,
            await self.repo.list_references(draft_id),
            await self.repo.list_claim_heads(draft_id),
            await self.repo.list_paragraph_responses(draft_id),
            current_user,
            include_unverified_graph_links=include_unverified_graph_links,
        )

    async def _load(self, draft_id: str) -> Dict[str, Any]:
        draft = await self.repo.get_draft(draft_id)
        if not draft:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration draft not found")
        return draft

    async def _load_unlocked(self, draft_id: str) -> Dict[str, Any]:
        draft = await self._load(draft_id)
        if draft.get("is_locked"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approved arbitration draft is locked")
        return draft

    async def _search_documents(self, draft: Dict[str, Any], query_text: str, limit: int) -> List[ArbitrationSelectedReferenceCreate]:
        if not query_text:
            return []
        query = {
            "organization_id": draft.get("organization_id"),
            "project_id": draft.get("project_id"),
            "$or": [
                {"subject": {"$regex": query_text, "$options": "i"}},
                {"letterNo": {"$regex": query_text, "$options": "i"}},
                {"filename": {"$regex": query_text, "$options": "i"}},
            ],
        }
        out: List[ArbitrationSelectedReferenceCreate] = []
        try:
            cursor = self.db.documents.find(query).limit(limit)
            async for doc in cursor:
                out.append(
                    ArbitrationSelectedReferenceCreate(
                        source_type="document",
                        source_id=str(doc.get("_id")),
                        label=doc.get("subject") or doc.get("filename") or "Document",
                        citation=doc.get("letterNo") or doc.get("filename"),
                        snippet=condense(doc.get("summary") or doc.get("ocrText") or doc.get("subject"), 500),
                        letter_no=doc.get("letterNo"),
                    )
                )
        except Exception:
            return []
        return out

    async def _search_clauses(self, draft: Dict[str, Any], query_text: str, limit: int) -> List[ArbitrationSelectedReferenceCreate]:
        if not query_text:
            return []
        query = {
            "organization_id": draft.get("organization_id"),
            "project_id": draft.get("project_id"),
            "$or": [
                {"clause_number": {"$regex": query_text, "$options": "i"}},
                {"clause_title": {"$regex": query_text, "$options": "i"}},
                {"text": {"$regex": query_text, "$options": "i"}},
            ],
        }
        out: List[ArbitrationSelectedReferenceCreate] = []
        try:
            cursor = self.db.document_vectors.find(query).limit(limit)
            async for doc in cursor:
                out.append(
                    ArbitrationSelectedReferenceCreate(
                        source_type="clause",
                        source_id=str(doc.get("_id") or doc.get("document_id")),
                        label=f"{doc.get('clause_number') or 'Clause'} {doc.get('clause_title') or ''}".strip(),
                        citation=doc.get("clause_number"),
                        snippet=condense(doc.get("text") or doc.get("text_enriched"), 500),
                        page_numbers=doc.get("page_numbers") or [],
                        clause_number=doc.get("clause_number"),
                        allowed_use="clause",
                    )
                )
        except Exception:
            return []
        return out

    @staticmethod
    def _merge_source_ledgers(
        parent_ledger: List[Dict[str, Any]],
        current_ledger: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged = [dict(row) for row in parent_ledger or []]
        seen = {
            (str(row.get("source_type") or ""), str(row.get("source_id") or ""), str(row.get("source_hash") or ""))
            for row in merged
        }
        next_number = max(
            [int(str(row.get("source_key") or "S0")[1:]) for row in merged if str(row.get("source_key") or "").startswith("S") and str(row.get("source_key"))[1:].isdigit()]
            or [0]
        ) + 1
        for row in current_ledger or []:
            key = (str(row.get("source_type") or ""), str(row.get("source_id") or ""), str(row.get("source_hash") or ""))
            if key in seen:
                continue
            appended = dict(row)
            appended["source_key"] = f"S{next_number}"
            next_number += 1
            merged.append(appended)
            seen.add(key)
        return merged

    def _merge_regenerated_section(
        self,
        draft: Dict[str, Any],
        context: Dict[str, Any],
        parent: Dict[str, Any],
        generated: Dict[str, Any],
        section_key: str,
        additional_instruction: Optional[str],
    ) -> Dict[str, Any]:
        parent_sections = [dict(section) for section in parent.get("sections") or []]
        replacement = next(
            (dict(section) for section in generated.get("sections") or [] if section.get("key") == section_key),
            None,
        )
        if not parent_sections or replacement is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Section regeneration requires a complete parent section set and a generated replacement",
            )
        replaced = False
        complete_sections: List[Dict[str, Any]] = []
        for section in parent_sections:
            if section.get("key") == section_key:
                complete_sections.append(replacement)
                replaced = True
            else:
                complete_sections.append(section)
        if not replaced:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Section '{section_key}' does not exist in the immutable parent version",
            )
        parent_keys = [str(section.get("key")) for section in parent_sections]
        complete_keys = [str(section.get("key")) for section in complete_sections]
        if complete_keys != parent_keys:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Section regeneration changed parent section ordering")
        annexures: List[Dict[str, Any]] = []
        annexure_seen = set()
        for annexure in [*(parent.get("annexures") or []), *(generated.get("annexures") or [])]:
            key = (str(annexure.get("source_id") or ""), str(annexure.get("citation") or ""))
            if key in annexure_seen:
                continue
            annexure_seen.add(key)
            annexures.append(dict(annexure))
        structured = {
            **((parent.get("structured_output") or {})),
            **(generated.get("structured_output") or {}),
            "section_keys": complete_keys,
            "section_key": section_key,
            "section_regeneration": True,
            "parent_version_id": parent.get("_id"),
            "parent_version": parent.get("version"),
        }
        return {
            **generated,
            "sections": complete_sections,
            "full_markdown": self.generator._markdown(
                draft,
                complete_sections,
                context,
                additional_instruction=additional_instruction,
            ),
            "structured_output": structured,
            "annexures": annexures,
        }

    def _validate_section_key(self, draft: Dict[str, Any], section_key: Optional[str]) -> None:
        if not section_key:
            return
        draft_type = str(draft.get("draft_type") or "statement_of_claim")
        allowed = SECTION_KEYS_BY_DRAFT_TYPE.get(draft_type, set())
        if section_key not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Unsupported arbitration drafting section key",
                    "section_key": section_key,
                    "allowed_section_keys": sorted(allowed),
                },
            )

    def _resolve_generator(self, draft_mode: Optional[str], context: Dict[str, Any]):
        """Pick the draft generator by mode (audit item 6).

        LLM mode requires a configured model client; otherwise it falls back to
        the deterministic generator with a visible context warning so the caller
        never silently gets a different engine than requested.
        """
        mode = str(draft_mode or os.getenv("ARBITRATION_DRAFT_MODE") or "deterministic").strip().lower()
        if mode == "llm":
            llm = LLMDraftGenerator()
            if llm.available:
                return llm, llm.model_name, "arbitration_pleadings_llm.v1"
            context.setdefault("context_warnings", []).append(
                "LLM draft mode was requested but no model client is configured; "
                "the deterministic source-grounded draft was used instead."
            )
        return self.generator, "deterministic-source-grounded", PROMPT_VERSION

    def _retrieval_queries(self, context: Dict[str, Any]) -> List[str]:
        draft = context.get("draft") or {}
        queries = [
            str(draft.get("title") or "").strip(),
            str(draft.get("manual_facts") or "").strip(),
            str(draft.get("relief_sought") or "").strip(),
            str(draft.get("arbitration_clause") or "").strip(),
        ]
        for head in context.get("claim_heads") or []:
            queries.append(str(head.get("description") or "").strip())
            queries.append(str(head.get("calculation_basis") or "").strip())
        return [query[:800] for query in queries if query]

    def _merge_warnings(self, *groups: List[str]) -> List[str]:
        merged: List[str] = []
        seen = set()
        for group in groups:
            for item in group or []:
                if item in seen:
                    continue
                seen.add(item)
                merged.append(item)
        return merged

    def _split_paragraphs(self, text: str) -> List[tuple[str, str]]:
        paragraphs: List[tuple[str, str]] = []
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text or "") if chunk.strip()]
        for idx, chunk in enumerate(chunks, start=1):
            match = re.match(r"^(\d+(?:\.\d+)*)[\).\s-]+(.+)$", chunk, flags=re.S)
            if match:
                paragraphs.append((match.group(1), condense(match.group(2), 2000)))
            else:
                paragraphs.append((str(idx), condense(chunk, 2000)))
        return paragraphs

    async def _emit(
        self,
        action: str,
        draft: Dict[str, Any],
        current_user: Any,
        *,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self.audit.emit(
            action=f"arbitration_drafting.{action}",
            actor_id=_actor_id(current_user),
            resource_type="arbitration_draft",
            resource_id=str(draft.get("_id")),
            organization_id=draft.get("organization_id"),
            project_id=draft.get("project_id"),
            before=before,
            after=after,
        )
        # Best-effort: keep the assignment board in step with the pleading.
        try:
            from ..task_sync_service import TaskSyncService

            sync = TaskSyncService(self.db)
            if action == "created":
                await sync.on_arbitration_draft_created(draft, _actor_id(current_user))
            elif action == "returned_for_revision":
                await sync.on_arbitration_status_changed(draft, "under_review", _actor_id(current_user))
            elif action == "approved":
                await sync.on_arbitration_status_changed(draft, "approved", _actor_id(current_user))
        except Exception:
            import logging

            logging.getLogger(__name__).debug("Arbitration task sync skipped", exc_info=True)
