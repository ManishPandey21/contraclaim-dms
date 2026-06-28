from __future__ import annotations

import hashlib
import re
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
from .generator import ArbitrationDraftGenerator, PROMPT_VERSION
from .repository import ArbitrationDraftingRepository
from .validator import ArbitrationDraftValidator


def _actor_id(user: Any) -> Optional[str]:
    return getattr(user, "id", None) or getattr(user, "email", None)


class ArbitrationDraftingService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.repo = ArbitrationDraftingRepository(db)
        self.audit = AuditEventService(db)
        self.context_builder = ArbitrationContextBuilder(db)
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
        await self.repo.create_draft(draft)
        refs = [
            ArbitrationSelectedReference(**ref.model_dump(), draft_id=draft["_id"], selected_by=_actor_id(current_user)).model_dump(by_alias=True)
            for ref in payload.selected_references
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

    async def import_pleading_paragraphs(
        self,
        draft_id: str,
        payload: PleadingImportRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        await self._load_unlocked(draft_id)
        rows = [
            ArbitrationParagraphResponse(
                draft_id=draft_id,
                source_pleading_document_id=payload.source_pleading_document_id,
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
        draft = await self.repo.get_draft(draft_id)
        if draft:
            await self._emit("paragraphs_imported", draft, current_user, after={"count": len(saved), "source_type": payload.source_pleading_type})
        return {"draft_id": draft_id, "count": len(saved), "paragraph_responses": saved}

    async def generate(
        self,
        draft_id: str,
        payload: ArbitrationGenerateRequest,
        current_user: Any,
        *,
        run_type: GenerationRunType = GenerationRunType.FULL_DRAFT,
    ) -> Dict[str, Any]:
        draft = await self._load_unlocked(draft_id)
        context = await self._context(
            draft_id,
            current_user,
            include_unverified_graph_links=payload.include_unverified_graph_links,
        )
        input_hash = hashlib.sha256(str(context).encode("utf-8")).hexdigest()
        run = ArbitrationGenerationRun(
            draft_id=draft_id,
            run_type=run_type,
            section_key=payload.section_key,
            status=GenerationRunStatus.RUNNING,
            input_hash=input_hash,
            prompt_version=PROMPT_VERSION,
            model="deterministic-source-grounded",
            source_ids=[row.get("source_id") for row in context["source_ledger"]],
            created_by=_actor_id(current_user),
            started_at=datetime.utcnow(),
        ).model_dump(by_alias=True)
        await self.repo.create_generation_run(run)
        try:
            generated = self.generator.generate(
                context,
                section_key=payload.section_key,
                additional_instruction=payload.additional_instruction,
            )
            warnings = self.validator.validate_generated(context, generated)
            version_no = await self.repo.next_version(draft_id)
            version = ArbitrationDraftVersion(
                draft_id=draft_id,
                version=version_no,
                status=ArbitrationDraftStatus.DRAFT,
                sections=generated["sections"],
                full_markdown=generated["full_markdown"],
                structured_output=generated["structured_output"],
                source_ledger=context["source_ledger"],
                missing_evidence=generated["missing_evidence"],
                paragraph_responses=context["paragraph_responses"],
                claim_heads=context["claim_heads"],
                annexures=generated["annexures"],
                ai_prompt_version=generated["ai_prompt_version"],
                model=generated["model"],
                generation_run_id=run["_id"],
                created_by=_actor_id(current_user),
            ).model_dump(by_alias=True)
            await self.repo.create_version(version)
            await self.repo.update_generation_run(
                run["_id"],
                {
                    "status": GenerationRunStatus.COMPLETED.value,
                    "completed_at": datetime.utcnow(),
                    "parsed_output": generated["structured_output"],
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
        version_no = await self.repo.next_version(draft_id)
        version = ArbitrationDraftVersion(
            draft_id=draft_id,
            version=version_no,
            status=ArbitrationDraftStatus.DRAFT,
            full_markdown=full_markdown,
            source_ledger=context["source_ledger"],
            missing_evidence=context["missing_evidence"],
            paragraph_responses=context["paragraph_responses"],
            claim_heads=context["claim_heads"],
            created_by=_actor_id(current_user),
        ).model_dump(by_alias=True)
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

    async def approve(self, draft_id: str, current_user: Any) -> Dict[str, Any]:
        draft = await self._load(draft_id)
        if draft.get("current_version", 0) < 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Generate or save a version before approval")
        updated = await self.repo.update_draft(
            draft_id,
            {
                "status": ArbitrationDraftStatus.APPROVED.value,
                "is_locked": True,
                "approved_by": _actor_id(current_user),
                "approved_at": datetime.utcnow(),
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
                "updated_at": datetime.utcnow(),
                "updated_by": _actor_id(current_user),
                "return_reason": reason,
            },
        )
        await self._emit("returned_for_revision", draft, current_user, after={"reason": reason})
        return await self.detail(draft_id)

    async def export(self, draft_id: str, fmt: str, current_user: Any) -> bytes:
        draft = await self._load(draft_id)
        latest = await self.repo.latest_version(draft_id)
        if not latest:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No draft version available to export")
        content = self.exporter.build_docx(latest) if fmt == "docx" else self.exporter.build_pdf(latest)
        await self.repo.update_draft(
            draft_id,
            {
                "status": ArbitrationDraftStatus.EXPORTED.value,
                "exported_by": _actor_id(current_user),
                "exported_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            },
        )
        await self._emit("exported", draft, current_user, after={"format": fmt})
        return content

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
