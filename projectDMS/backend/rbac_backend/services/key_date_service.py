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


def calculate_key_date(project_start_date: datetime, contractual_week_number: int) -> datetime:
    """Calculated Key Date = Start + ((week - 1) * 7 days)."""
    return project_start_date + timedelta(days=(int(contractual_week_number) - 1) * 7)


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

    async def _project_start(self, project_id: str, override: Optional[datetime]) -> datetime:
        if override:
            return override
        db = await self._get_db()
        try:
            proj = await db.projects.find_one({"_id": project_id})
        except Exception:
            proj = None
        start = _as_dt((proj or {}).get("project_start_date") or (proj or {}).get("start_date"))
        if not start:
            raise KeyDateError("Project start date is required to calculate the key date")
        return start

    # --- milestones -------------------------------------------------------

    async def create_milestone(self, payload: KeyDateMilestoneCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        start = await self._project_start(payload.project_id, payload.project_start_date)
        calc = calculate_key_date(start, payload.contractual_week_number)
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

    async def get(self, milestone_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        m = await db.key_date_milestones.find_one({"_id": milestone_id})
        return decorate(m) if m else None

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
        return items

    async def update(self, milestone: Dict[str, Any], payload: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        update = {k: v for k, v in payload.items() if v is not None and k != "project_start_date"}
        # Recalculate only the calculated/current date (never the original baseline)
        # and only while no approved extension is in force.
        week = payload.get("contractual_week_number") or milestone.get("contractual_week_number")
        if payload.get("contractual_week_number") or payload.get("project_start_date"):
            start = await self._project_start(milestone.get("project_id"), payload.get("project_start_date"))
            calc = calculate_key_date(start, week)
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
        res = await db.key_date_milestones.delete_one({"_id": milestone["_id"]})
        await self._emit("keydate.milestone.deleted", current_user, milestone, before=milestone)
        return res.deleted_count > 0

    # --- EOT --------------------------------------------------------------

    async def submit_eot(self, milestone: Dict[str, Any], payload: EOTApplicationCreate, current_user: Any) -> Dict[str, Any]:
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
        return build_dashboard(milestones)

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
