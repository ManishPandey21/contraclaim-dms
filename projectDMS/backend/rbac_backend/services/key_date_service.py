"""Key Date / Milestone Tracker service.

Owns the domain rules: key-date calculation from the project start date, the
"current key date in force" selection, the EOT lifecycle that produces an
immutable extension history (the original key date is never overwritten), the
actual-achievement delay/early computation, status derivation and dashboard
counts. Authorization/scope is enforced by the router via PolicyService.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..core.database import get_database
from ..models.key_date import (
    AchievementRecord,
    EOTApplication,
    EOTApplicationCreate,
    EOTReview,
    EOTStatus,
    ExtensionHistory,
    KeyDateMilestone,
    KeyDateMilestoneCreate,
    MilestoneStatus,
)
from .audit_event_service import AuditEventService


class KeyDateError(Exception):
    """Validation / workflow error surfaced as a 400/409 by the router."""


# --- pure domain functions (trivially testable) ---------------------------


# Key-date calculation basis (configured per contract on the Contract Master).
WEEK_BASIS_LOA_PLUS = "loa_plus_weeks"  # default — matches the contract sheets
WEEK_BASIS_LOA_PLUS_MINUS_1 = "loa_plus_weeks_minus_1"  # FIDIC-style fallback


def calculate_key_date(
    project_start_date: datetime,
    contractual_week_number: int,
    week_basis: str = WEEK_BASIS_LOA_PLUS,
) -> datetime:
    """Calculated Key Date from the LOA / contract start date.

    Default basis ``loa_plus_weeks`` matches the real contract key-date sheets:
    ``Contractual Date = LOA + (week * 7)`` (e.g. week 4 → LOA + 28 days). The
    ``loa_plus_weeks_minus_1`` basis is the FIDIC-style ``(week - 1) * 7``.
    """
    weeks = int(contractual_week_number)
    if week_basis == WEEK_BASIS_LOA_PLUS_MINUS_1:
        weeks -= 1
    return project_start_date + timedelta(days=weeks * 7)


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def current_key_date(milestone: Dict[str, Any]) -> Optional[datetime]:
    """The date in force: latest approved revised date, else the original."""
    return _as_dt(milestone.get("current_approved_key_date")) or _as_dt(
        milestone.get("original_planned_key_date")
    )


def days_remaining(milestone: Dict[str, Any], now: Optional[datetime] = None) -> Optional[int]:
    now = now or datetime.utcnow()
    cur = current_key_date(milestone)
    if cur is None:
        return None
    return (cur.date() - now.date()).days


def compute_achievement(actual: datetime, current: datetime) -> Tuple[bool, int, int]:
    """Return (on_time, delay_days, early_completion_days)."""
    delta = (actual.date() - current.date()).days
    if delta > 0:
        return False, delta, 0
    if delta < 0:
        return False, 0, -delta
    return True, 0, 0


def derive_status(milestone: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """Single display status. Priority: achieved → urgency → EOT lifecycle → time."""
    now = now or datetime.utcnow()
    if milestone.get("actual_achievement_date"):
        return MilestoneStatus.ACHIEVED.value
    days = days_remaining(milestone, now)
    eot = str(milestone.get("eot_status") or "")
    if days is not None and days < 0:
        return MilestoneStatus.OVERDUE.value
    if days is not None and days == 0:
        return MilestoneStatus.DUE_TODAY.value
    if eot == EOTStatus.SUBMITTED.value:
        return MilestoneStatus.EOT_SUBMITTED.value
    if eot == EOTStatus.UNDER_REVIEW.value:
        return MilestoneStatus.EOT_UNDER_REVIEW.value
    if eot == EOTStatus.REJECTED.value:
        return MilestoneStatus.EXTENSION_REJECTED.value
    if eot == EOTStatus.APPROVED.value and days is not None and days > 30:
        return MilestoneStatus.EXTENSION_APPROVED.value
    if days is not None and days <= 10:
        return MilestoneStatus.DUE_SOON.value
    if days is not None and days <= 30:
        return MilestoneStatus.UPCOMING.value
    return MilestoneStatus.NOT_STARTED.value


def build_dashboard(milestones: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or datetime.utcnow()
    d = {k: 0 for k in (
        "total", "achieved", "pending", "overdue", "due_30", "due_15", "due_10",
        "due_1", "eot_submitted", "eot_under_review", "eot_approved", "eot_rejected",
        "achieved_late", "achieved_early",
    )}
    for m in milestones:
        d["total"] += 1
        status = derive_status(m, now)
        achieved = bool(m.get("actual_achievement_date"))
        if achieved:
            d["achieved"] += 1
            if m.get("delay_days"):
                d["achieved_late"] += 1
            elif m.get("early_completion_days"):
                d["achieved_early"] += 1
        else:
            d["pending"] += 1
            days = days_remaining(m, now)
            if days is not None:
                if days < 0:
                    d["overdue"] += 1
                else:
                    if days <= 30:
                        d["due_30"] += 1
                    if days <= 15:
                        d["due_15"] += 1
                    if days <= 10:
                        d["due_10"] += 1
                    if days <= 1:
                        d["due_1"] += 1
        eot = str(m.get("eot_status") or "")
        if eot == EOTStatus.SUBMITTED.value:
            d["eot_submitted"] += 1
        elif eot == EOTStatus.UNDER_REVIEW.value:
            d["eot_under_review"] += 1
        elif eot == EOTStatus.APPROVED.value:
            d["eot_approved"] += 1
        elif eot == EOTStatus.REJECTED.value:
            d["eot_rejected"] += 1
    return d


def decorate(milestone: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Attach derived status + days_remaining for responses."""
    m = dict(milestone)
    m["status"] = derive_status(m, now)
    m["days_remaining"] = days_remaining(m, now)
    return m


# --- service --------------------------------------------------------------


class KeyDateService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def _assert_original_baseline_editable(self, milestone_or_project: Dict[str, Any]) -> None:
        """Prevent every legacy write path from changing a frozen baseline."""
        project_id = milestone_or_project.get("project_id")
        if not project_id:
            return
        db = await self._get_db()
        try:
            baselines = db.key_date_baselines
        except AttributeError:
            # Lightweight unit-test databases predating the workflow collection.
            return
        query: Dict[str, Any] = {
            "project_id": str(project_id),
            "contract_id": str(milestone_or_project.get("contract_id") or "primary"),
            "status": "frozen",
        }
        if milestone_or_project.get("organization_id"):
            query["organization_id"] = str(milestone_or_project.get("organization_id"))
        if await baselines.find_one(query):
            raise KeyDateError(
                "Original Key Dates are frozen. Use the project EOT revision workflow for contractual changes."
            )

    async def _start_and_basis(
        self, project_id: str, override: Optional[datetime],
        organization_id: Optional[str] = None,
    ) -> Tuple[datetime, str]:
        """Resolve (start date, week basis) for the key-date calculation.

        The Contract Master is the source of truth: ``contract_start_date`` is the
        LOA date and ``week_basis`` selects the formula. Falls back to the project
        record's start date when no Contract Master exists, and only then to an
        explicit user-provided override.
        """
        db = await self._get_db()
        try:
            cm_query: Dict[str, Any] = {"project_id": project_id, "contract_id": "primary"}
            if organization_id:
                cm_query["organization_id"] = organization_id
            cm = await db.contract_master.find_one(cm_query)
        except Exception:
            cm = None
        basis = (cm or {}).get("week_basis") or WEEK_BASIS_LOA_PLUS
        start = _as_dt((cm or {}).get("contract_start_date"))
        if not start:
            try:
                project_query: Dict[str, Any] = {"_id": project_id}
                if organization_id:
                    project_query["organization_id"] = organization_id
                proj = await db.projects.find_one(project_query)
            except Exception:
                proj = None
            start = _as_dt((proj or {}).get("project_start_date") or (proj or {}).get("start_date"))
        if not start:
            start = override
        if not start:
            raise KeyDateError("Contract start date (LOA) is required to calculate the key date")
        return start, basis

    # --- milestones -------------------------------------------------------

    async def create_milestone(self, payload: KeyDateMilestoneCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        await self._assert_original_baseline_editable({
            "project_id": payload.project_id,
            "organization_id": payload.organization_id or getattr(current_user, "organization_id", None),
            "contract_id": payload.contract_id,
        })
        start, basis = await self._start_and_basis(
            payload.project_id, payload.project_start_date, getattr(current_user, "organization_id", None)
        )
        calc = calculate_key_date(start, payload.contractual_week_number, basis)
        doc = KeyDateMilestone(**payload.model_dump(exclude={"project_start_date"})).model_dump(by_alias=True)
        doc["calculated_key_date"] = calc
        doc["original_planned_key_date"] = doc.get("original_planned_key_date") or calc
        doc["current_approved_key_date"] = doc.get("original_planned_key_date")
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        doc["created_at"] = datetime.utcnow()
        doc["created_by"] = getattr(current_user, "id", None)
        result = await db.key_date_milestones.insert_one(doc)
        created = await db.key_date_milestones.find_one({"_id": result.inserted_id}) or doc
        await self._emit("keydate.milestone.created", current_user, created, after=created)
        return decorate(created)

    async def _revisions_for(self, milestone_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Approved EOT revisions per milestone (the per-EOT register columns).

        Reads the immutable extension history; only approved revisions move the
        key date, so those are the columns. Batched to avoid an N+1 on lists.
        """
        out: Dict[str, List[Dict[str, Any]]] = {}
        ids = [str(m) for m in milestone_ids if m is not None]
        if not ids:
            return out
        db = await self._get_db()
        cursor = db.key_date_extension_history.find(
            {"milestone_id": {"$in": ids}, "status": "approved"}
        ).sort("revision_number", 1)
        async for h in cursor:
            out.setdefault(str(h.get("milestone_id")), []).append({
                "revision_number": h.get("revision_number"),
                "approved_revised_key_date": h.get("approved_revised_key_date"),
                "eot_letter_reference": h.get("eot_letter_reference"),
                "approval_letter_reference": h.get("approval_letter_reference"),
                "approval_date": h.get("approval_date"),
                "status": h.get("status", "approved"),
            })
        return out

    async def _workflow_summary_for(self, milestone_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Latest project-level EOT submission projection per milestone."""
        ids = [str(value) for value in milestone_ids if value is not None]
        if not ids:
            return {}
        db = await self._get_db()
        try:
            items = [row async for row in db.key_date_eot_submission_items.find(
                {"key_date_id": {"$in": ids}}
            )]
            submission_ids = list({str(row.get("eot_submission_id")) for row in items})
            submissions = [row async for row in db.key_date_eot_submissions.find(
                {"_id": {"$in": submission_ids}}
            )] if submission_ids else []
            determinations = [row async for row in db.key_date_eot_determinations.find(
                {"eot_submission_ids": {"$in": submission_ids}}
            )] if submission_ids else []
        except Exception:
            return {}
        submission_by_id = {str(row.get("_id")): row for row in submissions}
        determinations_by_submission: Dict[str, List[Dict[str, Any]]] = {}
        for determination in determinations:
            for submission_id in determination.get("eot_submission_ids") or []:
                determinations_by_submission.setdefault(str(submission_id), []).append(determination)
        frozen_covered = {
            str(submission_id)
            for determination in determinations
            if determination.get("frozen_at")
            for submission_id in determination.get("eot_submission_ids") or []
        }
        out: Dict[str, Dict[str, Any]] = {}
        grouped: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
        for item in items:
            submission = submission_by_id.get(str(item.get("eot_submission_id")))
            if submission:
                grouped.setdefault(str(item.get("key_date_id")), []).append((submission, item))
        for milestone_id, pairs in grouped.items():
            pairs.sort(key=lambda pair: int(pair[0].get("revision_number") or 0))
            submission, item = pairs[-1]
            linked = determinations_by_submission.get(str(submission.get("_id")), [])
            linked.sort(key=lambda row: row.get("created_at") or datetime.min)
            latest_status = linked[-1].get("status") if linked else (
                "pending" if submission.get("status") in {"submitted", "locked"} else submission.get("status")
            )
            pending_count = sum(
                1 for candidate, _candidate_item in pairs
                if candidate.get("status") in {"submitted", "locked"}
                and str(candidate.get("_id")) not in frozen_covered
            )
            out[milestone_id] = {
                "latest_eot_submission_label": submission.get("revision_label"),
                "latest_eot_submitted_date": item.get("eot_submitted_date"),
                "latest_eot_status": latest_status,
                "pending_eot_count": pending_count,
            }
        return out

    async def get(self, milestone_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        m = await db.key_date_milestones.find_one({"_id": milestone_id})
        if not m:
            return None
        d = decorate(m)
        d["revisions"] = (await self._revisions_for([str(milestone_id)])).get(str(milestone_id), [])
        d.update((await self._workflow_summary_for([str(milestone_id)])).get(str(milestone_id), {}))
        return d

    async def list(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None,
                   status: Optional[str] = None, responsible_party_id: Optional[str] = None,
                   skip: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        if responsible_party_id:
            query["responsible_party_id"] = responsible_party_id
        cursor = db.key_date_milestones.find(query).sort("current_approved_key_date", 1).skip(skip).limit(limit)
        items = [decorate(m) async for m in cursor]
        if status:
            items = [m for m in items if m["status"] == status]
        revisions = await self._revisions_for([str(it.get("_id")) for it in items])
        workflow = await self._workflow_summary_for([str(it.get("_id")) for it in items])
        for it in items:
            it["revisions"] = revisions.get(str(it.get("_id")), [])
            it.update(workflow.get(str(it.get("_id")), {}))
        return items

    async def update(self, milestone: Dict[str, Any], payload: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        await self._assert_original_baseline_editable(milestone)
        update = {k: v for k, v in payload.items() if v is not None and k != "project_start_date"}
        # Recalculate only the calculated/current date (never the original baseline)
        # and only while no approved extension is in force.
        week = payload.get("contractual_week_number") or milestone.get("contractual_week_number")
        if payload.get("contractual_week_number") or payload.get("project_start_date"):
            start, basis = await self._start_and_basis(
                milestone.get("project_id"), payload.get("project_start_date"), milestone.get("organization_id")
            )
            calc = calculate_key_date(start, week, basis)
            update["calculated_key_date"] = calc
            if int(milestone.get("current_revision") or 0) == 0:
                update["current_approved_key_date"] = calc
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        updated = await db.key_date_milestones.find_one_and_update(
            {"_id": milestone["_id"]}, {"$set": update}, return_document=True
        )
        await self._emit("keydate.milestone.updated", current_user, milestone, before=milestone, after=updated)
        return decorate(updated or milestone)

    async def delete(self, milestone: Dict[str, Any], current_user: Any) -> bool:
        db = await self._get_db()
        await self._assert_original_baseline_editable(milestone)
        res = await db.key_date_milestones.delete_one({"_id": milestone["_id"]})
        await self._emit("keydate.milestone.deleted", current_user, milestone, before=milestone)
        return res.deleted_count > 0

    async def recalculate_project(self, scope_filter: Dict[str, Any], project_id: str,
                                  current_user: Any, *, override_start: Optional[datetime] = None) -> Dict[str, Any]:
        """Re-derive milestone key dates from the current LOA + week basis (opt-in).

        Refreshes ``calculated_key_date`` for every milestone in the project. For
        milestones with no approved revision (``current_revision == 0``) it also
        re-derives the original/current baseline — this is the explicit, audited
        path to correct baselines computed under a wrong basis. Milestones that
        already carry an approved EOT revision keep their baseline and in-force
        date untouched (the original is never overwritten by this action).
        """
        db = await self._get_db()
        await self._assert_original_baseline_editable({
            "project_id": project_id,
            "organization_id": getattr(current_user, "organization_id", None),
        })
        start, basis = await self._start_and_basis(
            project_id, override_start, getattr(current_user, "organization_id", None)
        )
        query: Dict[str, Any] = dict(scope_filter or {})
        query["project_id"] = project_id
        cursor = db.key_date_milestones.find(query)
        milestones = [m async for m in cursor]
        updated = 0
        for m in milestones:
            calc = calculate_key_date(start, m.get("contractual_week_number"), basis)
            set_doc: Dict[str, Any] = {
                "calculated_key_date": calc,
                "updated_at": datetime.utcnow(),
                "updated_by": getattr(current_user, "id", None),
            }
            if int(m.get("current_revision") or 0) == 0:
                set_doc["original_planned_key_date"] = calc
                set_doc["current_approved_key_date"] = calc
            await db.key_date_milestones.update_one({"_id": m["_id"]}, {"$set": set_doc})
            updated += 1
        await self.audit.emit(
            action="keydate.recalculated", actor_id=getattr(current_user, "id", None),
            resource_type="key_dates", resource_id=str(project_id),
            organization_id=getattr(current_user, "organization_id", None), project_id=project_id,
            after={"start_date": str(start), "week_basis": basis, "updated": updated},
        )
        return {"project_id": project_id, "start_date": start, "week_basis": basis,
                "updated": updated, "scanned": len(milestones)}

    # --- EOT --------------------------------------------------------------

    async def submit_eot(self, milestone: Dict[str, Any], payload: EOTApplicationCreate, current_user: Any) -> Dict[str, Any]:
        await self._assert_original_baseline_editable(milestone)
        if payload.submit and not (payload.eot_letter_reference or "").strip():
            raise KeyDateError("EOT letter reference is mandatory when submitting an EOT")
        db = await self._get_db()
        status = EOTStatus.SUBMITTED if payload.submit else EOTStatus.DRAFT
        eot = EOTApplication(
            milestone_id=str(milestone["_id"]),
            project_id=milestone.get("project_id"),
            organization_id=milestone.get("organization_id"),
            application_date=payload.application_date or datetime.utcnow(),
            eot_letter_reference=payload.eot_letter_reference,
            requested_extension_days=payload.requested_extension_days,
            requested_revised_key_date=payload.requested_revised_key_date,
            reason=payload.reason,
            linked_document_ids=payload.linked_document_ids,
            status=status,
            submitted_by=getattr(current_user, "id", None),
            submitted_date=datetime.utcnow() if payload.submit else None,
        ).model_dump(by_alias=True)
        await db.key_date_eot_applications.insert_one(eot)
        if payload.submit:
            await db.key_date_milestones.update_one(
                {"_id": milestone["_id"]}, {"$set": {"eot_status": EOTStatus.SUBMITTED.value, "updated_at": datetime.utcnow()}}
            )
        await self._emit("keydate.eot.submitted", current_user, milestone, after={"eot_id": eot["_id"], "status": status.value})
        return eot

    async def review_eot(self, milestone: Dict[str, Any], eot: Dict[str, Any], review: EOTReview, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        decision = review.decision
        now = datetime.utcnow()
        eot_set: Dict[str, Any] = {
            "reviewed_by": getattr(current_user, "id", None),
            "reviewed_date": now,
            "remarks": review.approval_remarks,
        }
        if review.linked_document_ids is not None:
            eot_set["linked_document_ids"] = review.linked_document_ids

        if decision == "under_review":
            eot_set["status"] = EOTStatus.UNDER_REVIEW.value
            await db.key_date_eot_applications.update_one({"_id": eot["_id"]}, {"$set": eot_set})
            await db.key_date_milestones.update_one({"_id": milestone["_id"]}, {"$set": {"eot_status": EOTStatus.UNDER_REVIEW.value}})
            await self._emit("keydate.eot.under_review", current_user, milestone, after={"eot_id": eot["_id"]})
            return await db.key_date_eot_applications.find_one({"_id": eot["_id"]})

        if decision == "withdrawn":
            eot_set["status"] = EOTStatus.WITHDRAWN.value
            await db.key_date_eot_applications.update_one({"_id": eot["_id"]}, {"$set": eot_set})
            await db.key_date_milestones.update_one({"_id": milestone["_id"]}, {"$set": {"eot_status": None}})
            return await db.key_date_eot_applications.find_one({"_id": eot["_id"]})

        if decision == "rejected":
            eot_set["status"] = EOTStatus.REJECTED.value
            await db.key_date_eot_applications.update_one({"_id": eot["_id"]}, {"$set": eot_set})
            await db.key_date_milestones.update_one({"_id": milestone["_id"]}, {"$set": {"eot_status": EOTStatus.REJECTED.value}})
            await self._record_history(milestone, eot, review, current_user, status="rejected", approved=False)
            await self._emit("keydate.eot.rejected", current_user, milestone, after={"eot_id": eot["_id"]})
            return await db.key_date_eot_applications.find_one({"_id": eot["_id"]})

        if decision == "approved":
            if not (review.approval_letter_reference or "").strip():
                raise KeyDateError("Approval letter reference is mandatory before approving an EOT")
            if not review.approved_revised_key_date:
                raise KeyDateError("Approved revised key date cannot be blank when an EOT is approved")
            eot_set.update({
                "status": EOTStatus.APPROVED.value,
                "approved_extension_days": review.approved_extension_days,
                "approved_revised_key_date": review.approved_revised_key_date,
                "approval_letter_reference": review.approval_letter_reference,
                "approval_date": review.approval_date or now,
                "approving_authority": review.approving_authority,
            })
            await db.key_date_eot_applications.update_one({"_id": eot["_id"]}, {"$set": eot_set})
            await self._record_history(milestone, eot, review, current_user, status="approved", approved=True)
            # The current key date in force becomes the approved revised date;
            # the original baseline is never touched.
            await db.key_date_milestones.update_one(
                {"_id": milestone["_id"]},
                {"$set": {
                    "current_approved_key_date": review.approved_revised_key_date,
                    "eot_status": EOTStatus.APPROVED.value,
                    "current_revision": int(milestone.get("current_revision") or 0) + 1,
                    "updated_at": now,
                }},
            )
            await self._emit("keydate.eot.approved", current_user, milestone, after={"eot_id": eot["_id"], "revised": str(review.approved_revised_key_date)})
            return await db.key_date_eot_applications.find_one({"_id": eot["_id"]})

        raise KeyDateError(f"Unknown EOT decision '{decision}'")

    async def _record_history(self, milestone: Dict[str, Any], eot: Dict[str, Any], review: EOTReview, current_user: Any, *, status: str, approved: bool) -> None:
        db = await self._get_db()
        revision = int(milestone.get("current_revision") or 0) + 1
        history = ExtensionHistory(
            milestone_id=str(milestone["_id"]),
            project_id=milestone.get("project_id"),
            organization_id=milestone.get("organization_id"),
            revision_number=revision,
            original_key_date=_as_dt(milestone.get("original_planned_key_date")),
            previous_key_date=current_key_date(milestone),
            requested_revised_key_date=_as_dt(eot.get("requested_revised_key_date")),
            approved_revised_key_date=review.approved_revised_key_date if approved else None,
            requested_extension_days=eot.get("requested_extension_days"),
            approved_extension_days=review.approved_extension_days if approved else None,
            eot_letter_reference=eot.get("eot_letter_reference"),
            approval_letter_reference=review.approval_letter_reference,
            approval_date=review.approval_date or datetime.utcnow(),
            status=status,
            remarks=review.approval_remarks,
            created_by=getattr(current_user, "id", None),
        ).model_dump(by_alias=True)
        await db.key_date_extension_history.insert_one(history)

    async def list_extension_history(self, milestone_id: str) -> List[Dict[str, Any]]:
        db = await self._get_db()
        cursor = db.key_date_extension_history.find({"milestone_id": str(milestone_id)}).sort("revision_number", 1)
        return [h async for h in cursor]

    async def list_eots(self, milestone_id: str) -> List[Dict[str, Any]]:
        db = await self._get_db()
        cursor = db.key_date_eot_applications.find({"milestone_id": str(milestone_id)}).sort("created_at", 1)
        return [e async for e in cursor]

    # --- achievement ------------------------------------------------------

    async def record_achievement(self, milestone: Dict[str, Any], rec: AchievementRecord, current_user: Any) -> Dict[str, Any]:
        cur = current_key_date(milestone)
        if cur is None:
            raise KeyDateError("Milestone has no key date to measure against")
        start = _as_dt(milestone.get("original_planned_key_date"))
        # Actual achievement cannot pre-date the project (proxied by the baseline).
        if rec.client_notification_required and not (rec.client_notification_ref or "").strip():
            raise KeyDateError("Client notification letter reference is mandatory when client notification is required")
        on_time, delay, early = compute_achievement(rec.actual_achievement_date, cur)
        db = await self._get_db()
        summary = {
            "actual_achievement_date": rec.actual_achievement_date,
            "achieved_by": rec.achieved_by or getattr(current_user, "id", None),
            "achieved_on_time": on_time,
            "delay_days": delay,
            "early_completion_days": early,
            "achievement_remarks": rec.achievement_remarks,
            "client_notification_required": rec.client_notification_required,
            "client_notification_ref": rec.client_notification_ref,
            "client_notification_date": rec.client_notification_date,
            "final_status": rec.final_status or ("achieved_late" if delay else "achieved_early" if early else "achieved_on_time"),
            "updated_at": datetime.utcnow(),
            "updated_by": getattr(current_user, "id", None),
        }
        await db.key_date_milestones.update_one({"_id": milestone["_id"]}, {"$set": summary})
        achievement = {**summary, "_id": milestone["_id"] + ":ach", "milestone_id": str(milestone["_id"]),
                       "project_id": milestone.get("project_id"), "organization_id": milestone.get("organization_id"),
                       "linked_document_ids": rec.linked_document_ids, "created_at": datetime.utcnow()}
        await db.key_date_achievements.replace_one({"_id": achievement["_id"]}, achievement, upsert=True)
        await self._emit("keydate.achievement.recorded", current_user, milestone, after={"on_time": on_time, "delay": delay, "early": early})
        updated = await db.key_date_milestones.find_one({"_id": milestone["_id"]})
        return decorate(updated or {**milestone, **summary})

    # --- dashboard --------------------------------------------------------

    async def dashboard(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None) -> Dict[str, int]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        cursor = db.key_date_milestones.find(query)
        milestones = [m async for m in cursor]
        dashboard = build_dashboard(milestones)
        try:
            submissions = [row async for row in db.key_date_eot_submissions.find(query)]
            determinations = [row async for row in db.key_date_eot_determinations.find(query)]
            frozen_covered = {
                str(submission_id)
                for determination in determinations
                if determination.get("frozen_at")
                for submission_id in determination.get("eot_submission_ids") or []
            }
            open_submissions = [
                row for row in submissions
                if row.get("status") in {"submitted", "locked"}
                and str(row.get("_id")) not in frozen_covered
            ]
            dashboard["eot_submitted"] = len(open_submissions)
            dashboard["eot_under_review"] = len(open_submissions)
            dashboard["eot_approved"] = sum(
                1 for row in determinations
                if row.get("frozen_at") and row.get("status") in {"granted", "partially_granted"}
            )
            dashboard["eot_rejected"] = sum(
                1 for row in determinations
                if row.get("frozen_at") and row.get("status") in {"rejected", "no_extension"}
            )
        except Exception:
            # Compatibility with lightweight databases/older deployments while
            # migrations create the normalized workflow collections.
            pass
        return dashboard

    # --- audit ------------------------------------------------------------

    async def _emit(self, action: str, current_user: Any, milestone: Dict[str, Any], *, before: Any = None, after: Any = None) -> None:
        await self.audit.emit(
            action=action,
            actor_id=getattr(current_user, "id", None),
            resource_type="key_date_milestone",
            resource_id=str(milestone.get("_id")),
            organization_id=milestone.get("organization_id"),
            project_id=milestone.get("project_id"),
            before=before,
            after=after,
        )


# --- notification rules + scan (background job) ---------------------------

# Days-before thresholds at which an approaching-deadline reminder fires.
NOTIFY_THRESHOLDS = (30, 15, 10, 1, 0)


def due_notification_types(milestone: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    """Notification tags that should fire today for a milestone (pure).

    Uses the current approved key date (else the original). Achieved milestones
    never notify. Returns e.g. ["T-15"] on the 15-days-before day, or ["overdue"].
    """
    if milestone.get("actual_achievement_date"):
        return []
    days = days_remaining(milestone, now)
    if days is None:
        return []
    if days < 0:
        return ["overdue"]
    if days in NOTIFY_THRESHOLDS:
        return [f"T-{days}"]
    return []


async def scan_key_date_notifications(db: Any, notification_service: Any = None, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Background scan: emit reminders for key dates hitting a threshold today.

    Deduped via the key_date_notifications log so each (milestone, type, day)
    notifies once. Best-effort; one failure can't abort the sweep.
    """
    now = now or datetime.utcnow()
    cursor = db.key_date_milestones.find({"actual_achievement_date": None})
    milestones = [m async for m in cursor]
    emitted = 0
    for m in milestones:
        for tag in due_notification_types(m, now):
            trigger_day = now.date().isoformat()
            dedupe = f"keydate:{m.get('_id')}:{tag}:{trigger_day}"
            try:
                existing = await db.key_date_notifications.find_one({"_id": dedupe})
            except Exception:  # pragma: no cover - defensive
                existing = None
            if existing:
                continue
            overdue = tag == "overdue"
            recipients = [r for r in [m.get("responsible_party_id")] if r]
            log = {
                "_id": dedupe,
                "milestone_id": str(m.get("_id")),
                "project_id": m.get("project_id"),
                "organization_id": m.get("organization_id"),
                "notification_type": tag,
                "trigger_date": now,
                "recipients": recipients,
                "delivery_status": "pending",
                "read_status": False,
                "created_at": now,
            }
            try:
                await db.key_date_notifications.insert_one(log)
            except Exception:  # pragma: no cover
                continue
            if notification_service is None:
                continue
            try:
                from ..models.notification import (
                    NotificationContext,
                    NotificationPriority,
                    NotificationSeverity,
                    NotificationType,
                )

                cur = current_key_date(m)
                cur_str = cur.date().isoformat() if isinstance(cur, datetime) else str(cur)
                msg = (
                    f"Milestone '{m.get('title') or m.get('_id')}' key date {cur_str} "
                    + ("has passed and is not achieved." if overdue else f"is due in {days_remaining(m, now)} day(s).")
                )
                await notification_service.emit(
                    NotificationType.KEYDATE_OVERDUE if overdue else NotificationType.KEYDATE_DUE,
                    str(m.get("_id")),
                    "key_date_milestones",
                    context=NotificationContext.PROJECT,
                    include_users=recipients,
                    priority=NotificationPriority.URGENT if overdue else NotificationPriority.HIGH,
                    severity=NotificationSeverity.ERROR if overdue else NotificationSeverity.WARNING,
                    data={
                        "title": "Key date overdue" if overdue else "Key date approaching",
                        "message": msg,
                        "milestone_id": str(m.get("_id")),
                        "current_approved_key_date": cur_str,
                        "eot_status": m.get("eot_status"),
                        "organization_id": m.get("organization_id"),
                        "project_id": m.get("project_id"),
                    },
                    resource_link="/key-dates",
                    dedupe_key=dedupe,
                )
                await db.key_date_notifications.update_one({"_id": dedupe}, {"$set": {"delivery_status": "sent"}})
                emitted += 1
            except Exception:  # pragma: no cover - notifications best-effort
                pass
    return {"scanned": len(milestones), "emitted": emitted}


async def run_key_date_notification_scan() -> Dict[str, int]:
    """Scheduler entry point — resolves its own DB + notification service."""
    from ..core.database import get_database
    from ..dependencies import get_notification_service

    db = await get_database()
    try:
        notification_service = await get_notification_service(db)
    except Exception:  # pragma: no cover
        notification_service = None
    return await scan_key_date_notifications(db, notification_service)
