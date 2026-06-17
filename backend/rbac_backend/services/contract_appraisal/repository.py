"""Motor persistence for contract appraisal (v2 Phase 1).

Mirrors ``letter_drafting/repository.py``. Every document carries
``organization_id`` + ``project_id``; the approved-report immutability rule
(never overwrite, always new version) is enforced by the service via
``next_version`` + insert.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


class AppraisalRepository:
    def __init__(self, db: Any) -> None:
        if db is None:
            raise ValueError("Database connection cannot be None")
        self.db = db
        self.jobs = db.contract_appraisal_jobs
        self.reports = db.contract_appraisal_reports
        self.comments = db.contract_appraisal_review_comments

    # --- jobs -------------------------------------------------------------

    async def create_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        await self.jobs.insert_one(job)
        return job

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self.jobs.find_one({"_id": job_id})

    async def update_job(self, job_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        fields = {**fields, "updated_at": datetime.utcnow()}
        return await self.jobs.find_one_and_update(
            {"_id": job_id}, {"$set": fields}, return_document=True
        )

    # --- reports ----------------------------------------------------------

    async def create_report(self, report: Dict[str, Any]) -> Dict[str, Any]:
        await self.reports.insert_one(report)
        return report

    async def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        return await self.reports.find_one({"_id": report_id})

    async def update_report(self, report_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        fields = {**fields, "updated_at": datetime.utcnow()}
        return await self.reports.find_one_and_update(
            {"_id": report_id}, {"$set": fields}, return_document=True
        )

    async def list_reports(self, scope_filter: Dict[str, Any], *, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = self.reports.find(scope_filter).sort("created_at", -1).limit(limit)
        return [r async for r in cursor]

    async def next_version(self, organization_id: Optional[str], project_id: Optional[str], document_ids: List[str]) -> int:
        query: Dict[str, Any] = {"organization_id": organization_id, "project_id": project_id}
        latest = await self.reports.find_one(query, sort=[("report_version", -1)])
        return int((latest or {}).get("report_version", 0)) + 1

    # --- review comments --------------------------------------------------

    async def add_comment(self, comment: Dict[str, Any]) -> Dict[str, Any]:
        await self.comments.insert_one(comment)
        await self.reports.update_one(
            {"_id": comment["report_id"]}, {"$inc": {"review_comments_count": 1}}
        )
        return comment

    async def list_comments(self, report_id: str) -> List[Dict[str, Any]]:
        cursor = self.comments.find({"report_id": str(report_id)}).sort("created_at", -1)
        return [c async for c in cursor]
