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
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from ...models.contract_appraisal import (
    AppraisalJob,
    AppraisalJobStatus,
    AppraisalReport,
    CompletenessStatus,
    ContractKeyDate,
    ContractObligation,
    ContractRisk,
    ReportStatus,
    ReviewComment,
)
from ..audit_event_service import AuditEventService
from .generator import AppraisalGenerator
from .prompts import APPRAISAL_PROMPT_VERSION
from .repository import AppraisalRepository

logger = logging.getLogger(__name__)

# Mandatory contract document classes. ``phrases`` match as substrings; ``abbr``
# match as whole words, so "ER" in a filename classifies as Employer's
# Requirements while "er" inside "letter"/"tender" does not. Documents are
# classified by their stored type *and* their filename, because contract uploads
# are stored with a generic ``document_type="contract"`` and the granular class
# (LoA/GCC/SCC/ER/BoQ) is only discernible from the file name.
MANDATORY_DOC_TYPES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "Letter of Acceptance": {"phrases": ("letter of acceptance",), "abbr": ("loa",)},
    "General Conditions (GCC)": {"phrases": ("general conditions",), "abbr": ("gcc",)},
    "Special/Particular Conditions (SCC/PCC)": {
        "phrases": ("special conditions", "particular conditions"),
        "abbr": ("scc", "pcc"),
    },
    "Employer's Requirements / Specifications": {
        "phrases": ("employer", "requirement", "specification", "technical spec"),
        "abbr": ("er",),
    },
    "Bill of Quantities / Price Schedule": {
        "phrases": ("bill of quantities", "price schedule", "schedule of prices"),
        "abbr": ("boq",),
    },
}


def _classify_descriptor(text: str) -> Set[str]:
    """Return the mandatory-document labels a single descriptor string matches."""
    t = str(text or "").lower()
    if not t:
        return set()
    labels: Set[str] = set()
    for label, tokens in MANDATORY_DOC_TYPES.items():
        if any(p in t for p in tokens["phrases"]) or any(
            re.search(rf"\b{re.escape(a)}\b", t) for a in tokens["abbr"]
        ):
            labels.add(label)
    return labels


def assess_completeness(present_descriptors: Set[str]) -> Tuple[CompletenessStatus, List[str]]:
    """Classify the selected documents and report which mandatory classes are absent.

    ``present_descriptors`` are the descriptor strings (document type + filename)
    of the selected documents. When *nothing* can be classified we return
    REQUIRES_REVIEW rather than asserting all classes are missing — the documents
    simply aren't tagged granularly enough to judge, and a false "everything is
    missing" banner (including the very document the user uploaded) is worse than
    an honest "needs review".
    """
    matched: Set[str] = set()
    for descriptor in present_descriptors or []:
        matched |= _classify_descriptor(descriptor)
    if not matched:
        return CompletenessStatus.REQUIRES_REVIEW, []
    missing = [label for label in MANDATORY_DOC_TYPES if label not in matched]
    if not missing:
        return CompletenessStatus.COMPLETE, []
    return CompletenessStatus.INCOMPLETE, missing


class _JobCancelled(Exception):
    """Internal signal that a job was cancelled mid-run; aborts without a report."""


# M1: hold strong references to in-flight generation tasks so the event loop's
# GC can't drop a fire-and-forget task mid-run, and bound how many generations
# run at once so a burst can't swamp the process.
_BACKGROUND_TASKS: Set["asyncio.Task[Any]"] = set()
# Semaphore is created lazily and keyed by the running loop — an asyncio.Semaphore
# binds to one loop, and a single cached instance would break under test runners
# that use a fresh loop per test.
_GENERATION_SEMAPHORES: "Dict[Any, asyncio.Semaphore]" = {}


def _generation_semaphore() -> asyncio.Semaphore:
    from ...core.config import settings

    loop = asyncio.get_running_loop()
    sem = _GENERATION_SEMAPHORES.get(loop)
    if sem is None:
        limit = max(1, int(getattr(settings, "CONTRACT_APPRAISAL_MAX_CONCURRENCY", 2)))
        sem = asyncio.Semaphore(limit)
        _GENERATION_SEMAPHORES[loop] = sem
    return sem


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

    _DESCRIPTOR_FIELDS = (
        "document_type",
        "contract_document_type",
        "doc_type",
        "original_filename",
        "file_name",
        "filename",
        "document_name",
        "title",
    )

    async def _gather_doc_descriptors(self, document_ids: List[str]) -> Set[str]:
        """Descriptor strings (type + filename) for the selected documents, used to
        classify them against the mandatory contract document set. Matches by both
        ``_id`` and a ``document_id`` field so it is robust to id typing."""
        if not document_ids:
            return set()
        db = await self._get_db()
        ids = list(document_ids)
        descriptors: Set[str] = set()
        try:
            cursor = db.documents.find({"$or": [{"_id": {"$in": ids}}, {"document_id": {"$in": ids}}]})
            async for doc in cursor:
                for field in self._DESCRIPTOR_FIELDS:
                    val = doc.get(field)
                    if val:
                        descriptors.add(str(val))
        except Exception:  # pragma: no cover - completeness is best-effort
            logger.exception("Completeness descriptor lookup failed")
        return descriptors

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
        """Fire-and-forget generation, but managed: the task is kept referenced so
        it can't be garbage-collected mid-run (M1). Concurrency and timeout are
        enforced inside run_job."""
        try:
            task = asyncio.create_task(self.run_job(job_id, current_user))
        except RuntimeError:  # pragma: no cover - no running loop (sync context)
            logger.warning("No event loop to schedule appraisal job %s", job_id)
            return
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    async def _raise_if_cancelled(self, job_id: str) -> None:
        """Abort generation if the job has been cancelled out from under us."""
        job = await self._repo().get_job(job_id)
        if job and job.get("status") == AppraisalJobStatus.CANCELLED.value:
            raise _JobCancelled()

    async def run_job(self, job_id: str, current_user: Any, retrieval_service: Any = None) -> Dict[str, Any]:
        """Managed generation (M1): bounded concurrency + a hard timeout around the
        actual work, with cancellation/timeout/failure all recorded on the job."""
        repo = self._repo()
        job = await repo.get_job(job_id)
        if not job:
            return {}
        # Cancelled before it started — don't resurrect it back to RUNNING.
        if job.get("status") == AppraisalJobStatus.CANCELLED.value:
            return {}
        from ...core.config import settings

        timeout = max(60, int(getattr(settings, "CONTRACT_APPRAISAL_TIMEOUT_SECONDS", 1800)))
        try:
            async with _generation_semaphore():
                return await asyncio.wait_for(
                    self._run_job_inner(job_id, job, current_user, retrieval_service),
                    timeout=timeout,
                )
        except _JobCancelled:
            logger.info("Appraisal job %s cancelled before completion; no report created", job_id)
            await repo.update_job(
                job_id,
                {"status": AppraisalJobStatus.CANCELLED.value, "current_step": "Cancelled", "completed_at": datetime.utcnow()},
            )
            return {}
        except asyncio.TimeoutError:
            logger.error("Appraisal job %s timed out after %ss", job_id, timeout)
            await repo.update_job(
                job_id,
                {"status": AppraisalJobStatus.FAILED.value, "error_message": f"Generation timed out after {timeout}s", "completed_at": datetime.utcnow()},
            )
            return {}
        except Exception as exc:  # pragma: no cover - failure path
            logger.exception("Appraisal job %s failed", job_id)
            await repo.update_job(job_id, {"status": AppraisalJobStatus.FAILED.value, "error_message": str(exc), "completed_at": datetime.utcnow()})
            return {}

    async def _run_job_inner(self, job_id: str, job: Dict[str, Any], current_user: Any, retrieval_service: Any = None) -> Dict[str, Any]:
        """The actual generation. Raises _JobCancelled on cancellation; all other
        outcomes (timeout, failure) are handled by the managed run_job wrapper."""
        repo = self._repo()
        await repo.update_job(
            job_id,
            {"status": AppraisalJobStatus.RUNNING.value, "started_at": datetime.utcnow(), "current_step": "Checking documents", "progress": 5},
        )
        descriptors = await self._gather_doc_descriptors(job.get("document_ids", []))
        completeness, missing = assess_completeness(descriptors)

        if retrieval_service is None:
            retrieval_service = await self._build_retrieval_service()

        await self._raise_if_cancelled(job_id)
        await repo.update_job(job_id, {"status": AppraisalJobStatus.GENERATING.value, "current_step": "Generating report", "progress": 10})

        async def _progress(pct: int, step: str) -> None:
            # Cancellation is checked between sections so a long generation
            # stops promptly instead of finishing and clobbering the status.
            await self._raise_if_cancelled(job_id)
            await repo.update_job(job_id, {"progress": max(10, min(99, pct)), "current_step": step})

        generator = AppraisalGenerator(retrieval_service)
        result = await generator.generate(
            organization_id=job.get("organization_id"),
            project_id=job.get("project_id"),
            document_ids=job.get("document_ids", []),
            current_user=current_user,
            progress_cb=_progress,
        )

        await self._raise_if_cancelled(job_id)
        version = await self._next_version(job.get("organization_id"), job.get("project_id"), job.get("document_ids", []))
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
            structured_output=result.get("structured_output", {}),
            citations=result.get("citations", []),
            confidence_score=result.get("confidence_score", 0.0),
            overall_risk_rating=result.get("overall_risk_rating"),
            created_by=getattr(current_user, "id", None),
        ).model_dump(by_alias=True)
        await repo.create_report(report)
        # Regeneration always creates a new version; supersede any prior live
        # report for the same selection so only the newest is active.
        await self._supersede_prior(report)
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

    async def reap_stuck_jobs(self, max_age_seconds: Optional[int] = None) -> int:
        """M1: fail jobs left RUNNING/GENERATING by a crashed or restarted process,
        so the UI stops polling forever. Safe to run periodically (scheduler)."""
        from ...core.config import settings

        if self.db is None:
            self.db = await self._get_db()
            self.repo = None
        timeout = max(60, int(max_age_seconds or getattr(settings, "CONTRACT_APPRAISAL_TIMEOUT_SECONDS", 1800)))
        cutoff = datetime.utcnow() - timedelta(seconds=timeout)
        return await self._repo().fail_stuck_jobs(cutoff)

    async def _next_version(self, organization_id: Optional[str], project_id: Optional[str], document_ids: List[str]) -> int:
        """M2: next version number *for this exact document selection* (per-selection,
        not per-project) so per-document and whole-project appraisals version
        independently instead of sharing one counter."""
        wanted = {str(d) for d in (document_ids or [])}
        highest = 0
        for r in await self._repo().list_reports({"organization_id": organization_id, "project_id": project_id}):
            if {str(d) for d in (r.get("document_ids") or [])} == wanted:
                highest = max(highest, int(r.get("report_version") or 0))
        return highest + 1

    async def _build_retrieval_service(self) -> Any:
        db = await self._get_db()
        from ...routers.retrieval_engine import get_observability, get_retrieval_service

        observability = await get_observability(db=db)
        return await get_retrieval_service(db=db, observability=observability)

    async def cancel_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        repo = self._repo()
        job = await repo.get_job(job_id)
        if not job:
            return None
        terminal = {
            AppraisalJobStatus.COMPLETED.value,
            AppraisalJobStatus.FAILED.value,
            AppraisalJobStatus.CANCELLED.value,
        }
        if job.get("status") in terminal:
            return job  # already finished — nothing to cancel
        return await repo.update_job(
            job_id, {"status": AppraisalJobStatus.CANCELLED.value, "current_step": "Cancelled"}
        )

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self._repo().get_job(job_id)

    # --- reports ----------------------------------------------------------

    async def list_reports(self, scope_filter: Dict[str, Any]) -> List[Dict[str, Any]]:
        return await self._repo().list_reports(scope_filter)

    async def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        return await self._repo().get_report(report_id)

    async def find_existing_report(
        self, organization_id: Optional[str], project_id: Optional[str], document_ids: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Return the live report for an (org, project, document-set) selection, if
        any. "Live" excludes superseded/rejected reports — those don't block a
        fresh generation. The document set must match exactly so a per-document
        appraisal and a whole-project appraisal are treated as distinct."""
        scope = {"organization_id": organization_id, "project_id": project_id}
        wanted = set(str(d) for d in (document_ids or []))
        terminal = {ReportStatus.SUPERSEDED.value, ReportStatus.REJECTED.value}
        for report in await self._repo().list_reports(scope):
            if report.get("status") in terminal:
                continue
            if set(str(d) for d in (report.get("document_ids") or [])) == wanted:
                return report
        return None

    async def delete_report(self, report: Dict[str, Any], current_user: Any) -> None:
        """Delete a report and its derived registers + comments so the selection
        becomes available to generate again."""
        await self._repo().delete_report_cascade(str(report["_id"]))
        await self._emit(report, "deleted", current_user)

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
        """Start a fresh job for the same scope — never overwrites the existing report.

        The prior report is left untouched until the new version is persisted, at
        which point ``run_job`` supersedes it via ``_supersede_prior`` (so a failed
        regeneration never strands the user without a live report).
        """
        return await self.create_job(
            organization_id=report.get("organization_id"),
            project_id=report.get("project_id"),
            document_ids=report.get("document_ids", []),
            current_user=current_user,
        )

    async def _supersede_prior(self, new_report: Dict[str, Any]) -> None:
        """Mark every other live report for the same (org, project, document-set)
        selection as superseded + locked, so only the newest version is active."""
        org = new_report.get("organization_id")
        proj = new_report.get("project_id")
        wanted = {str(d) for d in (new_report.get("document_ids") or [])}
        new_id = str(new_report.get("_id"))
        repo = self._repo()
        terminal = {ReportStatus.SUPERSEDED.value, ReportStatus.REJECTED.value}
        for r in await repo.list_reports({"organization_id": org, "project_id": proj}):
            if str(r.get("_id")) == new_id or r.get("status") in terminal:
                continue
            if {str(d) for d in (r.get("document_ids") or [])} == wanted:
                await repo.update_report(
                    r["_id"], {"status": ReportStatus.SUPERSEDED.value, "is_locked": True}
                )

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

    # --- registers (Phase 2) ----------------------------------------------

    @staticmethod
    def _register_rows(report: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        """Build register rows from the report's structured_output (each row keeps
        its citation provenance; no invented entries)."""
        so = report.get("structured_output") or {}
        org = report.get("organization_id")
        proj = report.get("project_id")
        rid = str(report.get("_id"))

        def _stamp(model_cls, items):
            rows = []
            for item in items or []:
                rows.append(model_cls(organization_id=org, project_id=proj, report_id=rid, **item).model_dump(by_alias=True))
            return rows

        return {
            "obligations": _stamp(ContractObligation, so.get("obligations")),
            "risks": _stamp(ContractRisk, so.get("risks")),
            "key_dates": _stamp(ContractKeyDate, so.get("key_dates")),
        }

    async def create_registers(self, report: Dict[str, Any], current_user: Any) -> Dict[str, int]:
        rows = self._register_rows(report)
        repo = self._repo()
        counts: Dict[str, int] = {}
        for register, register_rows in rows.items():
            counts[register] = await repo.replace_register(register, str(report["_id"]), register_rows)
        await self.audit.emit(
            action="contract_appraisal.register_created",
            actor_id=getattr(current_user, "id", None),
            resource_type="contract_appraisal_report",
            resource_id=str(report["_id"]),
            organization_id=report.get("organization_id"),
            project_id=report.get("project_id"),
            after=counts,
        )
        return counts

    async def list_register(self, register: str, scope_filter: Dict[str, Any], *, report_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self._repo().list_register(register, scope_filter, report_id=report_id)

    async def get_register_item(self, register: str, item_id: str) -> Optional[Dict[str, Any]]:
        return await self._repo().get_register_item(register, item_id)

    async def update_register_item(self, register: str, item_id: str, fields: Dict[str, Any], current_user: Any, *, before: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        updated = await self._repo().update_register_item(register, item_id, {k: v for k, v in fields.items() if v is not None})
        await self.audit.emit(
            action="contract_appraisal.register_updated",
            actor_id=getattr(current_user, "id", None),
            resource_type=f"contract_{register}",
            resource_id=str(item_id),
            organization_id=(updated or before or {}).get("organization_id"),
            project_id=(updated or before or {}).get("project_id"),
            after=updated,
        )
        return updated

    # --- clause library (read-only over existing chunks) ------------------

    async def list_clauses(self, scope_filter: Dict[str, Any], *, q: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        query["clause_number"] = {"$nin": [None, ""]}
        if q:
            query["$or"] = [
                {"clause_number": {"$regex": q, "$options": "i"}},
                {"clause_title": {"$regex": q, "$options": "i"}},
                {"text": {"$regex": q, "$options": "i"}},
            ]
        out: List[Dict[str, Any]] = []
        seen: set = set()
        try:
            cursor = db.document_vectors.find(query).limit(limit * 3)
            async for doc in cursor:
                clause = doc.get("clause_number")
                doc_id = doc.get("document_id")
                dedupe = (doc_id, clause)
                if clause in (None, "") or dedupe in seen:
                    continue
                seen.add(dedupe)
                out.append(
                    {
                        "document_id": doc_id,
                        "document_name": doc.get("document_name") or doc.get("file_name"),
                        "clause_number": clause,
                        "clause_title": doc.get("clause_title"),
                        "page_numbers": doc.get("page_numbers") or [],
                        "snippet": str(doc.get("text") or doc.get("text_enriched") or "")[:300],
                    }
                )
                if len(out) >= limit:
                    break
        except Exception:  # pragma: no cover - clause library is best-effort
            logger.exception("Clause library query failed")
        return out

    # --- export -----------------------------------------------------------

    @staticmethod
    def build_pdf(report: Dict[str, Any]) -> bytes:
        """Render the markdown report to a PDF byte stream (reportlab)."""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
        from xml.sax.saxutils import escape

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, title="Contract Appraisal Report")
        styles = getSampleStyleSheet()
        flow = []
        for raw in (report.get("full_report_markdown") or "").split("\n"):
            line = raw.rstrip()
            if not line or line.startswith("---"):
                flow.append(Spacer(1, 6))
            elif line.startswith("## "):
                flow.append(Paragraph(escape(line[3:]), styles["Heading2"]))
            elif line.startswith("# "):
                flow.append(Paragraph(escape(line[2:]), styles["Heading1"]))
            elif line.startswith("*") and line.endswith("*"):
                flow.append(Paragraph(f"<i>{escape(line.strip('*'))}</i>", styles["Italic"]))
            else:
                flow.append(Paragraph(escape(line), styles["BodyText"]))
        doc.build(flow)
        return buffer.getvalue()

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
