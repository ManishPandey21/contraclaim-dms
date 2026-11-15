"""Signals that a claim may exist before anyone has opened one.

A signal is computed at read time from records other modules own. Nothing is
written to those modules, and no claim is created until a person accepts the
signal. What a signal does carry is its notice clock: the period is already
running from the day of the event, whether or not a claim has been raised.

Two sources:

- a hindrance marked as affecting the critical path whose responsibility is
  the Employer's or concurrent;
- a Letter Bench whose analysis routed the incoming letter to the claims or
  EOT skill.

A source that already has a claim raised from it is not a signal any more.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from ..models.claim import ClaimCreate, ClaimOrigin, ClaimStatus, ClaimType, NoticeAssessment
from .claim_notice_requirements import NoticeRequirementService
from .claim_service import ClaimService
from .claims_notice import assess_notice
from .skills.system_skills import CLAIMS_SKILL_KEY, EOT_SKILL_KEY
from .sla_service import SlaService

HINDRANCES = "delay_events"
BOARDS = "letter_position_boards"
SIGNAL_RESPONSIBILITIES = ("employer", "concurrent")
MAX_SIGNALS = 100

SignalSource = Literal["hindrance", "letter_bench"]


class ClaimSignal(BaseModel):
    source_type: SignalSource
    source_id: str
    label: str
    title: str
    reason: str
    claim_type: ClaimType
    event_date: Optional[datetime] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    # The clock a claim raised from this signal would start with.
    notice_assessment: Optional[NoticeAssessment] = None


class PotentialClaimRequest(BaseModel):
    source_type: SignalSource
    source_id: str = Field(..., min_length=1, max_length=120)


def _hindrance_signal(row: Dict[str, Any]) -> ClaimSignal:
    reference = row.get("hindrance_ref") or row.get("delay_ref") or str(row.get("_id"))
    responsibility = str(getattr(row.get("responsibility"), "value", row.get("responsibility")) or "")
    return ClaimSignal(
        source_type="hindrance",
        source_id=str(row.get("_id")),
        label=f"Hindrance {reference}",
        title=str(row.get("title") or f"Hindrance {reference}")[:300],
        reason=(
            "Recorded as affecting the critical path, with responsibility "
            + ("shared (concurrent)." if responsibility == "concurrent" else "on the Employer.")
        ),
        claim_type=ClaimType.EOT,
        event_date=row.get("start_date"),
        organization_id=row.get("organization_id"),
        project_id=row.get("project_id"),
    )


def _board_signal(board: Dict[str, Any]) -> Optional[ClaimSignal]:
    routed = {
        str(link.get("skill_key"))
        for link in board.get("chain") or []
        if str(link.get("skill_key")) in {CLAIMS_SKILL_KEY, EOT_SKILL_KEY}
    }
    if not routed:
        return None
    meta = board.get("incoming_meta") or {}
    reference = meta.get("letter_no") or (board.get("trigger") or {}).get("label") or "an incoming letter"
    is_delay = EOT_SKILL_KEY in routed
    event_date = None
    raw_date = meta.get("letter_date")
    if isinstance(raw_date, datetime):
        event_date = raw_date
    elif isinstance(raw_date, str) and raw_date:
        try:
            event_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except ValueError:
            event_date = None
    return ClaimSignal(
        source_type="letter_bench",
        source_id=str(board.get("letter_id")),
        label=f"Letter {reference}",
        title=str(meta.get("subject") or f"Matter raised in {reference}")[:300],
        reason=(
            "The incoming letter was read as raising "
            + ("a delay or extension of time." if is_delay else "a claim.")
        ),
        claim_type=ClaimType.EOT if is_delay else ClaimType.OTHER,
        event_date=event_date,
        organization_id=board.get("organization_id"),
        project_id=board.get("project_id"),
    )


class ClaimSignalService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def _raised(self, organization_id: str, source_type: str, source_ids: List[str]) -> set:
        if not source_ids:
            return set()
        docs = await self.db.claims.find(
            {"organization_id": organization_id, "origin.type": source_type, "origin.id": {"$in": source_ids}}
        ).to_list(length=len(source_ids) + 10)
        return {str((doc.get("origin") or {}).get("id")) for doc in docs}

    async def _with_clock(self, signals: List[ClaimSignal], now: Optional[datetime]) -> List[ClaimSignal]:
        requirements: Dict[tuple, Dict[str, Any]] = {}
        windows: Dict[str, Dict[str, int]] = {}
        sla = SlaService(self.db)
        notice = NoticeRequirementService(self.db)
        for signal in signals:
            if signal.event_date is None:
                continue
            org = str(signal.organization_id or "")
            scope = (org, str(signal.project_id or ""))
            if scope not in requirements:
                requirements[scope] = await notice.current_for_project(scope[0] or None, scope[1] or None)
            if org not in windows:
                windows[org] = await sla.notice_window_overrides_for(org or None)
            signal.notice_assessment = assess_notice(
                {"type": signal.claim_type.value, "status": "potential", "event_date": signal.event_date},
                now=now,
                organisation_windows=windows[org],
                requirement=requirements[scope].get(signal.claim_type.value),
            )
        order = {"overdue": 0, "due_soon": 1, "open": 2}
        signals.sort(
            key=lambda item: (
                order.get(item.notice_assessment.state, 3) if item.notice_assessment else 3,
                item.notice_assessment.days_remaining
                if item.notice_assessment and item.notice_assessment.days_remaining is not None
                else 10**6,
            )
        )
        return signals

    async def list(
        self,
        *,
        organization_id: str,
        hindrance_query: Optional[Dict[str, Any]],
        board_query: Optional[Dict[str, Any]],
        now: Optional[datetime] = None,
    ) -> List[ClaimSignal]:
        """Signals in scope. A ``None`` query means the caller may not read that source."""
        signals: List[ClaimSignal] = []
        if hindrance_query is not None:
            rows = await self.db[HINDRANCES].find(
                {
                    **hindrance_query,
                    "critical_path_impact": True,
                    "responsibility": {"$in": list(SIGNAL_RESPONSIBILITIES)},
                    "archived_at": None,
                }
            ).to_list(length=MAX_SIGNALS)
            raised = await self._raised(organization_id, "hindrance", [str(row.get("_id")) for row in rows])
            signals.extend(_hindrance_signal(row) for row in rows if str(row.get("_id")) not in raised)
        if board_query is not None:
            boards = await self.db[BOARDS].find(
                {**board_query, "chain.skill_key": {"$in": [CLAIMS_SKILL_KEY, EOT_SKILL_KEY]}}
            ).to_list(length=MAX_SIGNALS)
            raised = await self._raised(organization_id, "letter_bench", [str(b.get("letter_id")) for b in boards])
            for board in boards:
                signal = _board_signal(board)
                if signal is not None and signal.source_id not in raised:
                    signals.append(signal)
        return await self._with_clock(signals, now)

    async def load_source(self, source_type: str, source_id: str) -> Optional[Dict[str, Any]]:
        """The record behind a signal, for the router to authorise before a claim is raised."""
        if source_type == "hindrance":
            from bson import ObjectId

            candidates: List[Any] = [source_id]
            if ObjectId.is_valid(source_id):
                candidates.append(ObjectId(source_id))
            for candidate in candidates:
                row = await self.db[HINDRANCES].find_one({"_id": candidate})
                if row:
                    return row
            return None
        return await self.db[BOARDS].find_one({"letter_id": source_id})

    async def raise_potential(
        self, source_type: str, source: Dict[str, Any], current_user: Any
    ) -> tuple[Dict[str, Any], bool]:
        """Create the potential claim for a signal, or return the one already raised from it."""
        signal = _hindrance_signal(source) if source_type == "hindrance" else _board_signal(source)
        if signal is None:
            raise HTTPException(status_code=422, detail="This record does not indicate a claim")
        organization_id = str(signal.organization_id or "")
        existing = await self.db.claims.find_one(
            {"organization_id": organization_id, "origin.type": source_type, "origin.id": signal.source_id}
        )
        if existing:
            return existing, False
        payload = ClaimCreate(
            title=signal.title,
            type=signal.claim_type,
            status=ClaimStatus.POTENTIAL,
            description=f"Raised from {signal.label}. {signal.reason}",
            event_date=signal.event_date,
            organization_id=organization_id or None,
            project_id=signal.project_id,
            origin=ClaimOrigin(type=source_type, id=signal.source_id, label=signal.label),  # type: ignore[arg-type]
        )
        try:
            return await ClaimService(self.db).create(payload, current_user), True
        except DuplicateKeyError:
            # Two people accepted the same signal; the unique index let one win.
            winner = await self.db.claims.find_one(
                {"organization_id": organization_id, "origin.type": source_type, "origin.id": signal.source_id}
            )
            if not winner:
                raise
            return winner, False


__all__ = ["ClaimSignal", "ClaimSignalService", "PotentialClaimRequest"]
