"""Appraisal service (Contract Appraisal Report — v2 Phase 1).

Owns the job lifecycle, persistence, versioning, review/approval and DOCX export.
Generation is delegated to ``AppraisalGenerator`` (which orchestrates the existing
contract-QA engine). Authorization is the router's job via ``PolicyService``; this
layer enforces the *workflow* rules: approved reports are locked and regeneration
always creates a new version — it never overwrites an approved report.
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from ...models.contract_appraisal import (
    AppraisalJob,
    AppraisalJobStatus,
    AppraisalReport,
    CompletenessStatus,
    ReportStatus,
    ReviewComment,
)
from ..audit_event_service import AuditEventService
from .generator import AppraisalGenerator
from .prompts import APPRAISAL_PROMPT_VERSION
from .repository import AppraisalRepository

logger = logging.getLogger(__name__)

# Mandatory contract document types for a complete appraisal (normalised tokens).
MANDATORY_DOC_TYPES: Dict[str, Tuple[str, ...]] = {
    "Letter of Acceptance": ("letter of acceptance", "loa"),
    "General Conditions (GCC)": ("gcc", "general conditions"),
    "Special/Particular Conditions (SCC/PCC)": ("scc", "pcc", "special conditions", "particular conditions"),
    "Employer's Requirements / Specifications": ("employer", "requirement", "specification", "technical spec"),
    "Bill of Quantities / Price Schedule": ("boq", "bill of quantities", "price schedule"),
}


def assess_completeness(present_types: Set[str]) -> Tuple[CompletenessStatus, List[str]]:
    """Pure completeness check: which mandatory document classes are absent."""
    normalised = {str(t).lower() for t in present_types if t}
    if not normalised:
        return CompletenessStatus.REQUIRES_REVIEW, []
    missing: List[str] = []
    for label, tokens in MANDATORY_DOC_TYPES.items():
        if not any(any(tok in nt for tok in tokens) for nt in normalised):
            missing.append(label)
    if not missing:
        return CompletenessStatus.COMPLETE, []
    return CompletenessStatus.INCOMPLETE, missing


class AppraisalService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.repo = AppraisalRepository(db) if db is not None else None
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        from ...core.database import get_database

        return await get_database()

    def _repo(self) -> AppraisalRepository:
        if self.repo is None:
            self.repo = AppraisalRepository(self.db)
        return self.repo

    # --- completeness -----------------------------------------------------

    async def _gather_doc_types(self, document_ids: List[str]) -> Set[str]:
        if not document_ids:
            return set()
        db = await self._get_db()
        types: Set[str] = set()
        try:
            cursor = db.documents.find({"_id": {"$in": list(document_ids)}})
            async for doc in cursor:
                t = doc.get("document_type") or doc.get("contract_document_type") or doc.get("doc_type")
                if t:
                    types.add(str(t))
        except Exception:  # pragma: no cover - completeness is best-effort
            logger.exception("Completeness doc-type lookup failed")
        return types

    # --- job lifecycle ----------------------------------------------------

    async def create_job(self, *, organization_id: Optional[str], project_id: str, document_ids: List[str], current_user: Any) -> Dict[str, Any]:
        job = AppraisalJob(
            organization_id=organization_id,
            project_id=project_id,
            document_ids=list(document_ids or []),
            requested_by=getattr(current_user, "id", None),
            status=AppraisalJobStatus.QUEUED,
            ai_prompt_version=APPRAISAL_PROMPT_VERSION,
        ).model_dump(by_alias=True)
        await self._repo().create_job(job)
        await self.audit.emit(
            action="contract_appraisal.job_created",
            actor_id=getattr(current_user, "id", None),
            resource_type="contract_appraisal_job",
            resource_id=job["_id"],
            organization_id=organization_id,
            project_id=project_id,
        )
        return job

    def schedule(self, job_id: str, current_user: Any) -> None:
        """Fire-and-forget generation — the repo's Redis-disabled inline fallback."""
        try:
            asyncio.create_task(self.run_job(job_id, current_user))
        except RuntimeError:  # pragma: no cover - no running loop (sync context)
            logger.warning("No event loop to schedule appraisal job %s", job_id)

    async def run_job(self, job_id: str, current_user: Any, retrieval_service: Any = None) -> Dict[str, Any]:
        repo = self._repo()
        job = await repo.get_job(job_id)
        if not job:
            return {}
        try:
            await repo.update_job(
                job_id,
                {"status": AppraisalJobStatus.RUNNING.value, "started_at": datetime.utcnow(), "current_step": "Checking documents", "progress": 5},
            )
            doc_types = await self._gather_doc_types(job.get("document_ids", []))
            completeness, missing = assess_completeness(doc_types)

            if retrieval_service is None:
                retrieval_service = await self._build_retrieval_service()

            await repo.update_job(job_id, {"status": AppraisalJobStatus.GENERATING.value, "current_step": "Generating report", "progress": 10})

            async def _progress(pct: int, step: str) -> None:
                await repo.update_job(job_id, {"progress": max(10, min(99, pct)), "current_step": step})

            generator = AppraisalGenerator(retrieval_service)
            result = await generator.generate(
                organization_id=job.get("organization_id"),
                project_id=job.get("project_id"),
                document_ids=job.get("document_ids", []),
                current_user=current_user,
                progress_cb=_progress,
            )

            version = await repo.next_version(job.get("organization_id"), job.get("project_id"), job.get("document_ids", []))
            report = AppraisalReport(
                organization_id=job.get("organization_id"),
                project_id=job.get("project_id"),
                job_id=job_id,
                document_ids=job.get("document_ids", []),
                report_version=version,
                status=ReportStatus.DRAFT,
                ai_prompt_version=result.get("ai_prompt_version"),
                document_completeness_status=completeness,
                missing_documents=missing,
                executive_summary=result.get("executive_summary", ""),
                full_report_markdown=result.get("full_report_markdown", ""),
                sections=result.get("sections", []),
                citations=result.get("citations", []),
                confidence_score=result.get("confidence_score", 0.0),
                overall_risk_rating=result.get("overall_risk_rating"),
                created_by=getattr(current_user, "id", None),
            ).model_dump(by_alias=True)
            await repo.create_report(report)
            await repo.update_job(
                job_id,
                {"status": AppraisalJobStatus.COMPLETED.value, "report_id": report["_id"], "progress": 100, "current_step": "Completed", "completed_at": datetime.utcnow()},
            )
            await self.audit.emit(
                action="contract_appraisal.generated",
                actor_id=getattr(current_user, "id", None),
                resource_type="contract_appraisal_report",
                resource_id=report["_id"],
                organization_id=job.get("organization_id"),
                project_id=job.get("project_id"),
                after={"version": version, "completeness": completeness.value, "confidence": report["confidence_score"]},
            )
            return report
        except Exception as exc:  # pragma: no cover - failure path
            logger.exception("Appraisal job %s failed", job_id)
            await repo.update_job(job_id, {"status": AppraisalJobStatus.FAILED.value, "error_message": str(exc), "completed_at": datetime.utcnow()})
            return {}

    async def _build_retrieval_service(self) -> Any:
        db = await self._get_db()
        from ...routers.retrieval_engine import get_observability, get_retrieval_service

        observability = await get_observability(db=db)
        return await get_retrieval_service(db=db, observability=observability)

    async def cancel_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self._repo().update_job(job_id, {"status": AppraisalJobStatus.CANCELLED.value})

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self._repo().get_job(job_id)

    # --- reports ----------------------------------------------------------

    async def list_reports(self, scope_filter: Dict[str, Any]) -> List[Dict[str, Any]]:
        return await self._repo().list_reports(scope_filter)

    async def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        return await self._repo().get_report(report_id)

    async def edit_report(self, report: Dict[str, Any], fields: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        updated = await self._repo().update_report(report["_id"], {k: v for k, v in fields.items() if v is not None})
        await self._emit(report, "edited", current_user)
        return updated

    async def approve(self, report: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        updated = await self._repo().update_report(
            report["_id"],
            {"status": ReportStatus.APPROVED.value, "is_locked": True, "approved_by": getattr(current_user, "id", None), "approved_at": datetime.utcnow()},
        )
        await self._emit(report, "approved", current_user)
        return updated

    async def reject(self, report: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        updated = await self._repo().update_report(
            report["_id"], {"status": ReportStatus.REJECTED.value, "reviewed_by": getattr(current_user, "id", None)}
        )
        await self._emit(report, "rejected", current_user)
        return updated

    async def regenerate(self, report: Dict[str, Any], current_user: Any) -> Dict[str, Any]:
        """Start a fresh job for the same scope — never overwrites the existing report."""
        job = await self.create_job(
            organization_id=report.get("organization_id"),
            project_id=report.get("project_id"),
            document_ids=report.get("document_ids", []),
            current_user=current_user,
        )
        # Mark the prior approved report as superseded once a new one exists is
        # handled at approval time; here we only kick off the new version.
        return job

    async def add_comment(self, report: Dict[str, Any], text: str, current_user: Any, *, section_reference: Optional[str] = None) -> Dict[str, Any]:
        comment = ReviewComment(
            organization_id=report.get("organization_id"),
            project_id=report.get("project_id"),
            report_id=report["_id"],
            commented_by=getattr(current_user, "id", None),
            comment_text=text,
            section_reference=section_reference,
        ).model_dump(by_alias=True)
        await self._repo().add_comment(comment)
        if report.get("status") == ReportStatus.DRAFT.value:
            await self._repo().update_report(report["_id"], {"status": ReportStatus.UNDER_REVIEW.value})
        return comment

    async def list_comments(self, report_id: str) -> List[Dict[str, Any]]:
        return await self._repo().list_comments(report_id)

    async def _emit(self, report: Dict[str, Any], action: str, current_user: Any) -> None:
        await self.audit.emit(
            action=f"contract_appraisal.{action}",
            actor_id=getattr(current_user, "id", None),
            resource_type="contract_appraisal_report",
            resource_id=str(report.get("_id")),
            organization_id=report.get("organization_id"),
            project_id=report.get("project_id"),
        )

    # --- export -----------------------------------------------------------

    @staticmethod
    def build_docx(report: Dict[str, Any]) -> bytes:
        """Render the markdown report to a DOCX byte stream (python-docx)."""
        import docx

        document = docx.Document()
        markdown = report.get("full_report_markdown") or ""
        for raw in markdown.split("\n"):
            line = raw.rstrip()
            if not line:
                document.add_paragraph("")
            elif line.startswith("## "):
                document.add_heading(line[3:], level=2)
            elif line.startswith("# "):
                document.add_heading(line[2:], level=1)
            elif line.startswith("---"):
                document.add_paragraph("")
            elif line.startswith("*") and line.endswith("*"):
                p = document.add_paragraph()
                run = p.add_run(line.strip("*"))
                run.italic = True
            else:
                document.add_paragraph(line)
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()
