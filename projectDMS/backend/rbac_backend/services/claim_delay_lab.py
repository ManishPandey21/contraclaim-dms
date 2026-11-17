"""Delay Lab: the delay assessment of one head of claim.

A head links to records other registers own (hindrances, key-date EOT
submissions and determinations). Those registers stay authoritative. A link is
pinned to the record as it was when linked, so the assessment can be told when
the record has since changed; nothing re-points on its own.

From the linked hindrances the engine computes facts. People decide, for each
event, who carries its risk and whether it was critical, and, for each overlap
between a critical Employer event and a critical Contractor event, how it is
treated and on what contractual basis. Until every overlap is decided the
extension is shown as a range, and the board cannot be locked.

Days already granted are read from linked determinations. They are never typed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from . import claim_contract_scope as contract_scope
from .claim_contract_scope import ensure_same_contract
from ..models.claim import ClaimBench, ClaimHead
from ..models.claim_delay import (
    UNAVAILABLE_METHODS,
    ClaimHeadLink,
    ConcurrencyFinding,
    CostBasis,
    EventDecision,
    HeadDelay,
)
from ..utils.datetime import now_utc
from .claim_delay_engine import (
    EngineEvent,
    Treatment,
    COMPENSABLE_TREATMENTS,
    EMPLOYER_CONTRACTOR,
    NEUTRAL_CONTRACTOR,
    DelayFacts,
    compensable_days,
    compute_facts,
    net_eot,
    treatment_days,
)
from .key_date_determination_rule import FINAL_DETERMINATION_STATUSES, governing_determination_id, superseded_by
from .key_date_revision_service import KeyDateRevisionService
from .skills.executor import canonical_hash
from .sla_service import _as_datetime

LINKS = "claim_head_links"
HINDRANCES = "delay_events"
SUBMISSIONS = "key_date_eot_submissions"
DETERMINATIONS = "key_date_eot_determinations"
DETERMINATION_ITEMS = "key_date_eot_determination_items"
GRANTING_RESULTS = {"granted", "partially_granted"}
MAX_LINKS_PER_HEAD = 200

_REGISTER_OWNER = {"employer": "employer", "contractor": "contractor", "neutral": "neutral"}
_VALID_OWNERS = {"employer", "contractor", "neutral"}
_VALID_CRITICALITY = {"critical", "not_critical", "unproven"}
_VALID_METHODS = {"as_planned_vs_as_built", "windows"}
_VALID_TREATMENTS = {
    "eot_granted_no_cost", "eot_granted_with_cost", "apportioned", "no_eot", "not_true_concurrency"
}


# --------------------------------------------------------------------------- #
# Requests and views
# --------------------------------------------------------------------------- #
class LinkCreateRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    target_type: str
    target_id: str = Field(..., min_length=1, max_length=200)
    # For a determination that covers several milestones: which one this head concerns.
    milestone_ref: Optional[str] = Field(default=None, max_length=100)


class LinkRemoveRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    # True replaces the link with one pinned to the record as it is now.
    refresh: bool = False


class DelaySettingsRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    method: Optional[str] = None
    as_of_date: Optional[datetime] = None
    clear_as_of_date: bool = False
    include_neutral: Optional[bool] = None
    neutral_basis: Optional[str] = Field(default=None, max_length=2000)


class EventDecisionRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    risk_owner: Optional[str] = None
    criticality: Optional[str] = None
    clear: bool = False
    note: Optional[str] = Field(default=None, max_length=2000)


class OverlapDecisionRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    treatment: Optional[str] = None
    days_to_eot: Optional[int] = Field(default=None, ge=0)
    basis: Optional[str] = Field(default=None, max_length=2000)
    clear: bool = False


class DelayEventView(BaseModel):
    event_id: str
    link_id: str
    label: str
    title: str
    start_date: datetime
    end_date: Optional[datetime] = None
    open_ended: bool = False
    register_responsibility: Optional[str] = None
    register_critical: Optional[bool] = None
    # False when the hindrance has changed since it was linked.
    revision_current: bool = True
    counted: bool = False
    days: Optional[int] = None
    decision: EventDecision = Field(default_factory=EventDecision)


class OverlapView(BaseModel):
    overlap_id: str
    start_date: datetime
    end_date: datetime
    days: int
    # employer_contractor | neutral_contractor (D-16).
    kind: str = EMPLOYER_CONTRACTOR
    employer_event_ids: List[str] = Field(default_factory=list)
    contractor_event_ids: List[str] = Field(default_factory=list)
    neutral_event_ids: List[str] = Field(default_factory=list)
    finding: Optional[ConcurrencyFinding] = None
    days_to_eot: Optional[int] = None
    # Whether its days carry cost, and if not, what is missing (no automatic money).
    cost_established: bool = False
    cost_status: Optional[str] = None


class CostBasisRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    contractual_basis: str = Field(..., min_length=10, max_length=2000)
    causation: str = Field(..., min_length=10, max_length=2000)
    evidence_document_ids: List[str] = Field(..., min_length=1, max_length=50)


class CostReviewRequest(BaseModel):
    board_version: int = Field(..., ge=0)


def _cost_status(kind: str, finding: Optional[ConcurrencyFinding]) -> Optional[str]:
    """Why an overlap's days carry no cost yet, in plain words; None when they do or cannot."""
    if kind != EMPLOYER_CONTRACTOR:
        return "Neutral delay carries time only, never cost."
    if finding is None or finding.treatment not in COMPENSABLE_TREATMENTS:
        return None
    if finding.cost_established():
        return None
    cost = finding.cost_basis
    if cost is None:
        return "Time only until the contractual basis, causation and cost evidence are recorded and reviewed."
    if not cost.reviewed_by:
        return "Time only until a reviewer with determination authority, other than the person who recorded it, reviews the cost basis."
    return "Time only: the cost basis must be reviewed by someone other than the person who recorded it."


class LinkedRecordView(BaseModel):
    link_id: str
    target_type: str
    target_id: str
    label: str
    status: Optional[str] = None
    date: Optional[datetime] = None
    milestone_ref: Optional[str] = None
    granted_days: Optional[int] = None
    # Whether its granted days are counted as already granted, and if not, why.
    counted: bool = False
    not_counted_reason: Optional[str] = None
    revision_current: bool = True


class DelayPeriod(BaseModel):
    start_date: datetime
    end_date: datetime
    days: int


class NetEotView(BaseModel):
    low: int = 0
    high: int = 0
    # Set only when every overlap has a decided treatment.
    days: Optional[int] = None
    settled: bool = True
    previously_granted: int = 0
    employer_only_days: int = 0
    overlap_days_to_eot: int = 0
    neutral_days_included: int = 0


class DelayView(BaseModel):
    head_id: str
    method: Optional[str] = None
    as_of_date: Optional[datetime] = None
    unavailable_methods: Dict[str, str] = Field(default_factory=lambda: dict(UNAVAILABLE_METHODS))
    include_neutral: bool = False
    neutral_basis: Optional[str] = None
    events: List[DelayEventView] = Field(default_factory=list)
    overlaps: List[OverlapView] = Field(default_factory=list)
    records: List[LinkedRecordView] = Field(default_factory=list)
    employer_critical_days: int = 0
    contractor_critical_days: int = 0
    neutral_critical_days: int = 0
    contractor_only_days: int = 0
    net: NetEotView = Field(default_factory=NetEotView)
    compensable_periods: List[DelayPeriod] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    stale_links: int = 0


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def _enum(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _day(value: Any) -> Optional[date]:
    moment = _as_datetime(value)
    return moment.date() if moment else None


def _midnight(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)


def hindrance_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "reference": row.get("hindrance_ref") or row.get("delay_ref"),
        "title": row.get("title"),
        "start_date": _as_datetime(row.get("start_date")),
        "end_date": _as_datetime(row.get("end_date")),
        "responsibility": _enum(row.get("responsibility")) or None,
        "critical_path_impact": bool(row.get("critical_path_impact")),
        "archived": bool(row.get("archived_at")),
    }


def revision_of(snapshot: Dict[str, Any]) -> str:
    return canonical_hash(snapshot)


def register_proposal(snapshot: Dict[str, Any]) -> EventDecision:
    """What the hindrance register itself records, offered as a proposal."""
    responsibility = str(snapshot.get("responsibility") or "")
    owner = _REGISTER_OWNER.get(responsibility)
    critical = bool(snapshot.get("critical_path_impact"))
    reasons = []
    if owner:
        reasons.append(f"The hindrance register records responsibility as {responsibility}.")
    elif responsibility == "concurrent":
        reasons.append("The register records this event as concurrent; decide which party's event it is.")
    else:
        reasons.append("The register has not recorded who is responsible.")
    reasons.append(
        "It records an impact on the critical path."
        if critical
        else "It does not record an impact on the critical path."
    )
    return EventDecision(
        proposed_risk_owner=owner,  # type: ignore[arg-type]
        proposed_criticality="critical" if critical else "unproven",
        proposed_reason=" ".join(reasons),
        proposed_by="register",
    )


def engine_events(head: ClaimHead, links: List[ClaimHeadLink]) -> List[EngineEvent]:
    events: List[EngineEvent] = []
    for link in links:
        if link.target_type != "hindrance" or link.status != "active":
            continue
        start = _day(link.snapshot.get("start_date"))
        if start is None:
            continue
        decision = head.delay.event_decisions.get(link.target_id) or EventDecision()
        events.append(
            EngineEvent(
                event_id=link.target_id,
                start=start,
                end=_day(link.snapshot.get("end_date")),
                risk_owner=decision.risk_owner,
                criticality=decision.criticality,
            )
        )
    return events


def head_treatments(delay: HeadDelay) -> Dict[str, Treatment]:
    return {
        overlap_id: Treatment(finding.treatment, finding.days_to_eot, finding.cost_established())
        for overlap_id, finding in delay.findings.items()
    }


def head_facts(head: ClaimHead, links: List[ClaimHeadLink]) -> DelayFacts:
    """The facts for a head, with Neutral x Contractor overlaps when its neutral entitlement is recorded."""
    return compute_facts(engine_events(head, links), _day(head.delay.as_of_date), include_neutral=head.delay.include_neutral)


_KIND_LABEL = {EMPLOYER_CONTRACTOR: "Employer and Contractor", NEUTRAL_CONTRACTOR: "neutral and Contractor"}


def granted_days_of(link: ClaimHeadLink) -> tuple[int, bool, Optional[str]]:
    """(days, counted, reason-if-not) for a linked determination.

    The register's own rule decides (D-15, ``key_date_determination_rule``): a
    determination counts only if it is frozen, final, not superseded, and governs
    the milestone this head concerns. Status "superseded" is not a refusal: in the
    register it marks the superseding, operative determination.
    """
    snapshot = link.snapshot
    if snapshot.get("needs_milestone"):
        return 0, False, "This determination covers several milestones. Say which one this head concerns."
    days = snapshot.get("granted_days")
    result = str(snapshot.get("result") or "")
    if result not in GRANTING_RESULTS:
        return 0, False, f"Its result for this milestone is {result.replace('_', ' ') or 'not recorded'}."
    if "governs" not in snapshot:
        # A link recorded before 4B-21 carries no reading of the register's rule.
        return 0, False, "Re-link this determination: it was linked before the register's rule was applied here."
    if not snapshot.get("frozen_at"):
        return 0, False, "It is not frozen yet: only a frozen determination counts."
    if str(snapshot.get("status") or "") not in FINAL_DETERMINATION_STATUSES:
        return 0, False, f"Its status is {str(snapshot.get('status') or 'not recorded').replace('_', ' ')}, which is not final."
    if snapshot.get("superseded_by"):
        return 0, False, "A later determination supersedes it."
    if not snapshot.get("governs"):
        return 0, False, "Another determination governs this milestone in the key-date register."
    if not isinstance(days, int) or days < 0:
        return 0, False, "It records no granted days for this milestone."
    return days, True, None


def previously_granted(links: List[ClaimHeadLink]) -> int:
    return sum(
        granted_days_of(link)[0]
        for link in links
        if link.target_type == "eot_determination" and link.status == "active"
    )


def delay_blockers(head: ClaimHead, links: List[ClaimHeadLink]) -> List[str]:
    """What stands between this head's delay assessment and being complete.

    Empty for a head with no linked hindrance: a head need not have a delay
    assessment, but one that has started must be finished before the board locks.
    """
    events = engine_events(head, links)
    if not events:
        return []
    blockers: List[str] = []
    as_of = _day(head.delay.as_of_date)
    if head.delay.method is None:
        blockers.append(f"{head.head_id}: choose the delay analysis method")
    undecided = [event for event in events if event.risk_owner is None or event.criticality is None]
    if undecided:
        count = len(undecided)
        blockers.append(
            f"{head.head_id}: {count} delay event{'' if count == 1 else 's'} need a risk owner and a call on criticality"
        )
    facts = compute_facts(events, as_of, include_neutral=head.delay.include_neutral)
    if facts.reversed_event_ids:
        # C-06: an end before the start is a wrong date on the record, not a missing as-of date.
        count = len(facts.reversed_event_ids)
        blockers.append(
            f"{head.head_id}: {count} delay event{'' if count == 1 else 's'} end{'s' if count == 1 else ''} before "
            f"{'it starts' if count == 1 else 'they start'}; correct the dates on the register"
        )
    if facts.invalid_event_ids:
        blockers.append(
            f"{head.head_id}: set the date the analysis runs to; "
            f"{len(facts.invalid_event_ids)} event(s) have no end date"
        )
    open_ids = set(
        net_eot(facts, head_treatments(head.delay), include_neutral=head.delay.include_neutral).undecided_overlap_ids
    )
    for kind in (EMPLOYER_CONTRACTOR, NEUTRAL_CONTRACTOR):
        count = sum(1 for overlap in facts.overlaps if overlap.kind == kind and overlap.overlap_id in open_ids)
        if count:
            blockers.append(
                f"{head.head_id}: {count} overlap{'' if count == 1 else 's'} between {_KIND_LABEL[kind]} "
                "delay need a decided treatment"
            )
    if head.delay.include_neutral and not (head.delay.neutral_basis or "").strip():
        blockers.append(f"{head.head_id}: state the contractual basis for giving time for neutral events")
    return blockers


def build_view(
    head: ClaimHead, links: List[ClaimHeadLink], current_revisions: Optional[Dict[str, str]] = None
) -> DelayView:
    current_revisions = current_revisions or {}
    active = [link for link in links if link.status == "active" and link.head_id == head.head_id]
    as_of = _day(head.delay.as_of_date)
    events = engine_events(head, active)
    facts = compute_facts(events, as_of, include_neutral=head.delay.include_neutral)
    treatments = head_treatments(head.delay)
    granted = previously_granted(active)
    net = net_eot(facts, treatments, previously_granted=granted, include_neutral=head.delay.include_neutral)
    counted = set(facts.counted_event_ids)
    by_id = {event.event_id: event for event in events}

    def _is_current(link: ClaimHeadLink) -> bool:
        latest = current_revisions.get(link.id)
        return latest is None or latest == link.target_revision

    event_views: List[DelayEventView] = []
    record_views: List[LinkedRecordView] = []
    for link in active:
        if link.target_type == "hindrance":
            start = _as_datetime(link.snapshot.get("start_date"))
            if start is None:
                continue
            engine = by_id.get(link.target_id)
            end = engine.end or as_of if engine else None
            event_views.append(
                DelayEventView(
                    event_id=link.target_id,
                    link_id=link.id,
                    label=link.target_label,
                    title=str(link.snapshot.get("title") or link.target_label),
                    start_date=start,
                    end_date=_as_datetime(link.snapshot.get("end_date")),
                    open_ended=link.snapshot.get("end_date") is None,
                    register_responsibility=link.snapshot.get("responsibility"),
                    register_critical=link.snapshot.get("critical_path_impact"),
                    revision_current=_is_current(link),
                    counted=link.target_id in counted,
                    days=((end - engine.start).days + 1) if engine and end and end >= engine.start else None,
                    decision=head.delay.event_decisions.get(link.target_id) or EventDecision(),
                )
            )
        else:
            days, is_counted, reason = (
                granted_days_of(link) if link.target_type == "eot_determination" else (0, False, None)
            )
            record_views.append(
                LinkedRecordView(
                    link_id=link.id,
                    target_type=link.target_type,
                    target_id=link.target_id,
                    label=link.target_label,
                    status=link.snapshot.get("status"),
                    date=_as_datetime(link.snapshot.get("date")),
                    milestone_ref=link.snapshot.get("milestone_ref"),
                    granted_days=link.snapshot.get("granted_days"),
                    counted=is_counted,
                    not_counted_reason=reason,
                    revision_current=_is_current(link),
                )
            )
    event_views.sort(key=lambda item: (item.start_date, item.label))

    overlap_views = []
    for overlap in facts.overlaps:
        finding = head.delay.findings.get(overlap.overlap_id)
        overlap_views.append(
            OverlapView(
                overlap_id=overlap.overlap_id,
                start_date=_midnight(overlap.start),
                end_date=_midnight(overlap.end),
                days=overlap.days,
                employer_event_ids=list(overlap.employer_event_ids),
                contractor_event_ids=list(overlap.contractor_event_ids),
                neutral_event_ids=list(overlap.neutral_event_ids),
                kind=overlap.kind,
                finding=finding,
                days_to_eot=treatment_days(overlap, treatments[overlap.overlap_id]) if finding else None,
                cost_established=bool(finding and finding.cost_established()),
                cost_status=_cost_status(overlap.kind, finding),
            )
        )

    periods: List[DelayPeriod] = []
    run: List[date] = []
    for day in sorted(compensable_days(events, as_of, treatments, include_neutral=head.delay.include_neutral)):
        if run and (day - run[-1]).days != 1:
            periods.append(DelayPeriod(start_date=_midnight(run[0]), end_date=_midnight(run[-1]), days=len(run)))
            run = []
        run.append(day)
    if run:
        periods.append(DelayPeriod(start_date=_midnight(run[0]), end_date=_midnight(run[-1]), days=len(run)))

    return DelayView(
        head_id=head.head_id,
        method=head.delay.method,
        as_of_date=head.delay.as_of_date,
        include_neutral=head.delay.include_neutral,
        neutral_basis=head.delay.neutral_basis,
        events=event_views,
        overlaps=overlap_views,
        records=record_views,
        employer_critical_days=facts.employer_critical_days,
        contractor_critical_days=facts.contractor_critical_days,
        neutral_critical_days=facts.neutral_critical_days,
        contractor_only_days=facts.contractor_only_days,
        net=NetEotView(
            low=net.low,
            high=net.high,
            days=net.days,
            settled=net.settled,
            previously_granted=net.previously_granted,
            employer_only_days=net.employer_only_days,
            overlap_days_to_eot=net.overlap_days_to_eot,
            neutral_days_included=net.neutral_days_included,
        ),
        compensable_periods=periods,
        blockers=delay_blockers(head, active),
        stale_links=sum(1 for link in active if not _is_current(link)),
    )


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class ClaimDelayLab:
    """Links and delay decisions for a claim's heads. Edits go through the Bench's
    compare-and-set, so a delay decision and a head edit can never overwrite each other."""

    def __init__(self, db: Any, bench: Any) -> None:
        self.db = db
        self.bench = bench

    @property
    def _links(self) -> Any:
        return self.db[LINKS]

    async def links_for_claim(self, claim_id: str, include_removed: bool = False) -> List[ClaimHeadLink]:
        query: Dict[str, Any] = {"claim_id": str(claim_id)}
        if not include_removed:
            query["status"] = "active"
        docs = await self._links.find(query).to_list(length=5000)
        return sorted((ClaimHeadLink(**doc) for doc in docs), key=lambda link: link.created_at)

    async def _find_by_id(self, collection: str, record_id: str) -> Optional[Dict[str, Any]]:
        candidates: List[Any] = [record_id]
        if ObjectId.is_valid(record_id):
            candidates.append(ObjectId(record_id))
        for candidate in candidates:
            doc = await self.db[collection].find_one({"_id": candidate})
            if doc:
                return doc
        return None

    async def load_target(self, target_type: str, target_id: str) -> Optional[Dict[str, Any]]:
        """The record a link would point at, for the router to authorise."""
        collection = {"hindrance": HINDRANCES, "eot_submission": SUBMISSIONS, "eot_determination": DETERMINATIONS}.get(
            target_type
        )
        if collection is None:
            raise HTTPException(status_code=422, detail="A head can link to a hindrance, an EOT submission or an EOT determination")
        return await self._find_by_id(collection, target_id)

    async def _snapshot(self, target_type: str, record: Dict[str, Any], milestone_ref: Optional[str]) -> tuple[Dict[str, Any], str]:
        if target_type == "hindrance":
            snapshot = hindrance_snapshot(record)
            if snapshot["archived"]:
                raise HTTPException(status_code=422, detail="An archived hindrance cannot be linked")
            if snapshot["start_date"] is None:
                raise HTTPException(status_code=422, detail="This hindrance has no start date")
            return snapshot, f"Hindrance {snapshot['reference'] or record.get('_id')}"
        if target_type == "eot_submission":
            label = record.get("eot_reference") or record.get("revision_label") or str(record.get("_id"))
            return (
                {
                    "status": _enum(record.get("status")) or None,
                    "date": _as_datetime(record.get("contractor_submission_date")),
                    "revision_label": record.get("revision_label"),
                    "claim_cutoff_date": _as_datetime(record.get("claim_cutoff_date")),
                },
                f"EOT submission {label}",
            )
        items = await self.db[DETERMINATION_ITEMS].find({"determination_id": str(record.get("_id"))}).to_list(length=500)
        items = items or list(record.get("items") or [])
        chosen: Optional[Dict[str, Any]] = None
        if milestone_ref:
            chosen = next((item for item in items if str(item.get("milestone_ref")) == milestone_ref), None)
            if chosen is None:
                raise HTTPException(status_code=422, detail=f"This determination has no item for milestone {milestone_ref}")
        elif len(items) == 1:
            chosen = items[0]
        label = record.get("determination_reference") or str(record.get("_id"))
        # The register's rule (D-15), read over every determination on the same contract.
        siblings = await KeyDateRevisionService(self.db).list_determinations(
            record.get("organization_id"), str(record.get("project_id") or ""), str(record.get("contract_id") or "primary")
        )
        record_id = str(record.get("_id"))
        key_date_id = (chosen or {}).get("key_date_id")
        return (
            {
                "status": _enum(record.get("status")) or None,
                "date": _as_datetime(record.get("determination_date")),
                "frozen_at": _as_datetime(record.get("frozen_at")),
                "supersedes_determination_ids": sorted(str(item) for item in record.get("supersedes_determination_ids") or []),
                "superseded_by": superseded_by(siblings, record_id),
                "governs": bool(key_date_id) and governing_determination_id(siblings, str(key_date_id)) == record_id,
                "milestone_ref": (chosen or {}).get("milestone_ref"),
                "granted_days": (chosen or {}).get("granted_extension_days"),
                "result": _enum((chosen or {}).get("determination_result")) or None,
                "needs_milestone": chosen is None and len(items) > 1,
                "milestones": sorted(str(item.get("milestone_ref")) for item in items if item.get("milestone_ref")),
            },
            f"EOT determination {label}",
        )

    async def current_revisions(self, links: List[ClaimHeadLink]) -> Dict[str, str]:
        """For each active link, the revision of its record as it is now."""
        result: Dict[str, str] = {}
        for link in links:
            record = await self.load_target(link.target_type, link.target_id)
            if record is None:
                result[link.id] = "missing"
                continue
            try:
                snapshot, _label = await self._snapshot(link.target_type, record, link.detail.get("milestone_ref"))
            except HTTPException:
                result[link.id] = "unusable"
                continue
            result[link.id] = revision_of(snapshot)
        return result

    async def view(self, claim: Dict[str, Any], head_id: str) -> DelayView:
        bench = self.bench._require_bench(claim)
        head = _head(bench, head_id)
        links = [link for link in await self.links_for_claim(str(claim["_id"])) if link.head_id == head_id]
        return build_view(head, links, await self.current_revisions(links))

    async def blockers(self, claim: Dict[str, Any], bench: ClaimBench) -> List[str]:
        if not bench.heads:
            return []
        links = await self.links_for_claim(str(claim["_id"]))
        blockers: List[str] = []
        for head in bench.heads:
            blockers.extend(delay_blockers(head, [link for link in links if link.head_id == head.head_id]))
        return blockers

    # -------------------------------------------------------------- writes
    async def add_link(
        self, claim: Dict[str, Any], head_id: str, request: LinkCreateRequest, record: Dict[str, Any], user_id: Optional[str]
    ) -> DelayView:
        """Link a record the caller has been authorised to read. One active link per record and head."""
        if str(record.get("organization_id") or "") != str(claim.get("organization_id") or "") or str(
            record.get("project_id") or ""
        ) != str(claim.get("project_id") or ""):
            raise HTTPException(status_code=422, detail="Only a record of the claim's own project can be linked")
        bench = self.bench._require_bench(claim)
        _head(bench, head_id)
        self.bench._ensure_writable(claim, bench, request.board_version)
        existing = [link for link in await self.links_for_claim(str(claim["_id"])) if link.head_id == head_id]
        if len(existing) >= MAX_LINKS_PER_HEAD:
            raise HTTPException(status_code=422, detail=f"A head can hold at most {MAX_LINKS_PER_HEAD} links")
        if any(link.target_type == request.target_type and link.target_id == request.target_id for link in existing):
            raise HTTPException(status_code=409, detail="This record is already linked to this head")
        snapshot, label = await self._snapshot(request.target_type, record, request.milestone_ref)
        # 4B-23 (D-23): a record of another contract never supports this claim.
        ensure_same_contract(claim, record, label)
        link = ClaimHeadLink(
            organization_id=str(claim.get("organization_id") or ""),
            project_id=str(claim.get("project_id") or ""),
            claim_id=str(claim["_id"]),
            head_id=head_id,
            target_type=request.target_type,  # type: ignore[arg-type]
            target_id=request.target_id,
            target_revision=revision_of(snapshot),
            target_label=label,
            snapshot=snapshot,
            detail={"milestone_ref": request.milestone_ref} if request.milestone_ref else {},
            created_by=user_id,
        )
        try:
            await self._links.insert_one(link.model_dump(by_alias=True))
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail="This record is already linked to this head") from exc

        def _apply(fresh: ClaimBench) -> None:
            head = _head(fresh, head_id)
            if request.target_type == "hindrance" and request.target_id not in head.delay.event_decisions:
                head.delay.event_decisions[request.target_id] = register_proposal(snapshot)
            self.bench._unlock(fresh)

        try:
            await self.bench._change(claim, request.board_version, user_id, _apply)
        except HTTPException:
            # The board moved on between the check and the write: take the link back out.
            await self._links.delete_one({"_id": link.id})
            raise
        return await self.view(await self._reload(claim), head_id)

    async def remove_link(
        self, claim: Dict[str, Any], head_id: str, link_id: str, request: LinkRemoveRequest, user_id: Optional[str]
    ) -> DelayView:
        doc = await self._links.find_one({"_id": link_id, "claim_id": str(claim["_id"]), "head_id": head_id, "status": "active"})
        if not doc:
            raise HTTPException(status_code=404, detail="Link not found")
        link = ClaimHeadLink(**doc)
        replacement: Optional[ClaimHeadLink] = None
        if request.refresh:
            record = await self.load_target(link.target_type, link.target_id)
            if record is None:
                raise HTTPException(status_code=409, detail="The linked record no longer exists; remove the link instead")
            snapshot, label = await self._snapshot(link.target_type, record, link.detail.get("milestone_ref"))
            # Re-pinning reads the record as it is now; a record moved to another contract is refused.
            ensure_same_contract(claim, record, label)
            replacement = link.model_copy(
                update={
                    "id": str(uuid.uuid4()),
                    "target_revision": revision_of(snapshot),
                    "target_label": label,
                    "snapshot": snapshot,
                    "created_by": user_id,
                    "created_at": now_utc(),
                }
            )

        def _apply(fresh: ClaimBench) -> None:
            head = _head(fresh, head_id)
            if link.target_type == "hindrance":
                if replacement is None:
                    head.delay.event_decisions.pop(link.target_id, None)
                else:
                    # The record changed: keep the person's decision, refresh what the register proposes.
                    kept = head.delay.event_decisions.get(link.target_id) or EventDecision()
                    proposal = register_proposal(replacement.snapshot)
                    head.delay.event_decisions[link.target_id] = kept.model_copy(
                        update={
                            "proposed_risk_owner": proposal.proposed_risk_owner,
                            "proposed_criticality": proposal.proposed_criticality,
                            "proposed_reason": proposal.proposed_reason,
                            "proposed_by": "register",
                        }
                    )
            self.bench._unlock(fresh)

        # Board first: if someone else changed it, nothing about the link is touched.
        await self.bench._change(claim, request.board_version, user_id, _apply)
        await self._links.update_one(
            {"_id": link.id, "status": "active"},
            {
                "$set": {
                    "status": "removed",
                    "removed_by": user_id,
                    "removed_at": now_utc(),
                    "superseded_by_id": replacement.id if replacement else None,
                }
            },
        )
        if replacement is not None:
            await self._links.insert_one(replacement.model_dump(by_alias=True))
        fresh_claim = await self._reload(claim)
        await self._drop_orphan_findings(fresh_claim, head_id, user_id)
        return await self.view(await self._reload(claim), head_id)

    async def _reload(self, claim: Dict[str, Any]) -> Dict[str, Any]:
        return await self.db.claims.find_one({"_id": claim["_id"]}) or claim

    async def _drop_orphan_findings(self, claim: Dict[str, Any], head_id: str, user_id: Optional[str]) -> None:
        """A finding belongs to an overlap between two dates. When the events move,
        the old overlap is gone and its finding with it; it is not carried to a new one."""
        bench = self.bench._require_bench(claim)
        head = _head(bench, head_id)
        links = [link for link in await self.links_for_claim(str(claim["_id"])) if link.head_id == head_id]
        live = {overlap.overlap_id for overlap in head_facts(head, links).overlaps}
        if not set(head.delay.findings) - live:
            return

        def _apply(fresh: ClaimBench) -> None:
            target = _head(fresh, head_id)
            target.delay.findings = {key: value for key, value in target.delay.findings.items() if key in live}

        await self.bench._change(claim, bench.board_version, user_id, _apply)

    async def set_settings(
        self, claim: Dict[str, Any], head_id: str, request: DelaySettingsRequest, user_id: Optional[str]
    ) -> DelayView:
        if request.method is not None and request.method not in _VALID_METHODS:
            if request.method in UNAVAILABLE_METHODS:
                raise HTTPException(status_code=422, detail=f"Not available: {UNAVAILABLE_METHODS[request.method]}")
            raise HTTPException(status_code=422, detail="Unknown delay analysis method")

        def _apply(fresh: ClaimBench) -> None:
            delay = _head(fresh, head_id).delay
            if request.method is not None:
                delay.method = request.method  # type: ignore[assignment]
            if request.clear_as_of_date:
                delay.as_of_date = None
            elif request.as_of_date is not None:
                delay.as_of_date = request.as_of_date
            if request.include_neutral is not None:
                delay.include_neutral = request.include_neutral
            if request.neutral_basis is not None:
                delay.neutral_basis = request.neutral_basis.strip() or None
            self.bench._unlock(fresh)

        await self.bench._change(claim, request.board_version, user_id, _apply)
        fresh_claim = await self._reload(claim)
        await self._drop_orphan_findings(fresh_claim, head_id, user_id)
        return await self.view(await self._reload(claim), head_id)

    async def decide_event(
        self, claim: Dict[str, Any], head_id: str, event_id: str, request: EventDecisionRequest, user_id: Optional[str]
    ) -> DelayView:
        if not request.clear:
            if request.risk_owner is not None and request.risk_owner not in _VALID_OWNERS:
                raise HTTPException(status_code=422, detail="risk_owner must be employer, contractor or neutral")
            if request.criticality is not None and request.criticality not in _VALID_CRITICALITY:
                raise HTTPException(status_code=422, detail="criticality must be critical, not_critical or unproven")

        def _apply(fresh: ClaimBench) -> None:
            delay = _head(fresh, head_id).delay
            decision = delay.event_decisions.get(event_id)
            if decision is None:
                raise HTTPException(status_code=404, detail="This event is not linked to this head")
            if request.clear:
                decision.risk_owner, decision.criticality = None, None
                decision.decided_by, decision.decided_at = None, None
            else:
                if request.risk_owner is not None:
                    decision.risk_owner = request.risk_owner  # type: ignore[assignment]
                if request.criticality is not None:
                    decision.criticality = request.criticality  # type: ignore[assignment]
                decision.decided_by, decision.decided_at = user_id, now_utc()
            if request.note is not None:
                decision.note = request.note.strip() or None
            self.bench._unlock(fresh)

        await self.bench._change(claim, request.board_version, user_id, _apply)
        fresh_claim = await self._reload(claim)
        await self._drop_orphan_findings(fresh_claim, head_id, user_id)
        return await self.view(await self._reload(claim), head_id)

    async def decide_overlap(
        self, claim: Dict[str, Any], head_id: str, overlap_id: str, request: OverlapDecisionRequest, user_id: Optional[str]
    ) -> DelayView:
        bench = self.bench._require_bench(claim)
        head = _head(bench, head_id)
        links = [link for link in await self.links_for_claim(str(claim["_id"])) if link.head_id == head_id]
        overlaps = {o.overlap_id: o for o in head_facts(head, links).overlaps}
        overlap = overlaps.get(overlap_id)
        if overlap is None:
            raise HTTPException(status_code=404, detail="There is no such overlap on the events as now decided")
        finding: Optional[ConcurrencyFinding] = None
        if not request.clear:
            if request.treatment not in _VALID_TREATMENTS:
                raise HTTPException(status_code=422, detail="Unknown treatment")
            basis = (request.basis or "").strip()
            if len(basis) < 10:
                raise HTTPException(
                    status_code=422,
                    detail="State the basis for this treatment: the contract clause, or the method and reasoning relied on",
                )
            days: Optional[int] = None
            if request.treatment == "apportioned":
                if request.days_to_eot is None or request.days_to_eot > overlap.days:
                    raise HTTPException(
                        status_code=422,
                        detail=f"For an apportioned overlap, give the days counted towards the extension (0 to {overlap.days})",
                    )
                days = request.days_to_eot
            finding = ConcurrencyFinding(
                treatment=request.treatment,  # type: ignore[arg-type]
                days_to_eot=days,
                basis=basis,
                decided_by=user_id,
                decided_at=now_utc(),
                event_ids=sorted(overlap.employer_event_ids + overlap.contractor_event_ids + overlap.neutral_event_ids),
            )

        def _apply(fresh: ClaimBench) -> None:
            delay = _head(fresh, head_id).delay
            if finding is None:
                delay.findings.pop(overlap_id, None)
            else:
                delay.findings[overlap_id] = finding
            self.bench._unlock(fresh)

        await self.bench._change(claim, request.board_version, user_id, _apply)
        return await self.view(await self._reload(claim), head_id)


    async def _live_finding(
        self, claim: Dict[str, Any], head_id: str, overlap_id: str
    ) -> tuple[ClaimHead, ConcurrencyFinding]:
        bench = self.bench._require_bench(claim)
        head = _head(bench, head_id)
        links = [link for link in await self.links_for_claim(str(claim["_id"])) if link.head_id == head_id]
        overlap = {o.overlap_id: o for o in head_facts(head, links).overlaps}.get(overlap_id)
        if overlap is None:
            raise HTTPException(status_code=404, detail="There is no such overlap on the events as now decided")
        finding = head.delay.findings.get(overlap_id)
        if overlap.kind != EMPLOYER_CONTRACTOR:
            raise HTTPException(status_code=422, detail="Neutral delay carries time only, never cost")
        if finding is None or finding.treatment not in COMPENSABLE_TREATMENTS:
            raise HTTPException(
                status_code=422,
                detail="Only an overlap decided as carrying cost (time with cost, or not true concurrency) takes a cost basis",
            )
        return head, finding

    async def record_cost_basis(
        self,
        claim: Dict[str, Any],
        head_id: str,
        overlap_id: str,
        request: "CostBasisRequest",
        user_id: Optional[str],
        claim_documents: set,
    ) -> DelayView:
        """Record why an overlap's days carry cost (D-16). Any earlier review is withdrawn."""
        await self._live_finding(claim, head_id, overlap_id)
        evidence = sorted({str(item) for item in request.evidence_document_ids})
        unknown = [item for item in evidence if item not in claim_documents]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail="Cost evidence must be documents linked to this claim that you can see",
            )
        # 4B-23 (D-23): cost evidence of another contract never supports this claim.
        foreign = await contract_scope.evidence_conflicts(self.db, claim, [], evidence)
        if foreign:
            raise contract_scope.refusal(claim, foreign)
        cost = CostBasis(
            contractual_basis=request.contractual_basis.strip(),
            causation=request.causation.strip(),
            evidence_document_ids=evidence,
            recorded_by=user_id,
            recorded_at=now_utc(),
        )

        def _apply(fresh: ClaimBench) -> None:
            finding = _head(fresh, head_id).delay.findings.get(overlap_id)
            if finding is None:
                raise HTTPException(status_code=409, detail="The overlap's treatment changed; reload and retry")
            finding.cost_basis = cost
            self.bench._unlock(fresh)

        await self.bench._change(claim, request.board_version, user_id, _apply)
        return await self.view(await self._reload(claim), head_id)

    async def review_cost_basis(
        self, claim: Dict[str, Any], head_id: str, overlap_id: str, request: "CostReviewRequest", user_id: Optional[str]
    ) -> DelayView:
        """Accept a recorded cost basis. The router admits only a holder of the claim's
        determination authority by name; here, the reviewer must not be who recorded it."""
        _head_obj, finding = await self._live_finding(claim, head_id, overlap_id)
        cost = finding.cost_basis
        if cost is None:
            raise HTTPException(status_code=422, detail="Record the cost basis before it is reviewed")
        if not user_id or user_id == cost.recorded_by:
            raise HTTPException(status_code=403, detail="The cost basis must be reviewed by someone other than the person who recorded it")
        recorded_at = cost.recorded_at

        def _apply(fresh: ClaimBench) -> None:
            target = _head(fresh, head_id).delay.findings.get(overlap_id)
            if target is None or target.cost_basis is None or target.cost_basis.recorded_at != recorded_at:
                raise HTTPException(status_code=409, detail="The cost basis changed; reload and review it again")
            target.cost_basis.reviewed_by = user_id
            target.cost_basis.reviewed_at = now_utc()
            self.bench._unlock(fresh)

        await self.bench._change(claim, request.board_version, user_id, _apply)
        return await self.view(await self._reload(claim), head_id)

def _head(bench: ClaimBench, head_id: str) -> ClaimHead:
    for head in bench.heads:
        if head.head_id == head_id:
            return head
    raise HTTPException(status_code=404, detail="Head of claim not found")


__all__ = [
    "LINKS",
    "ClaimDelayLab",
    "DelaySettingsRequest",
    "DelayView",
    "EventDecisionRequest",
    "LinkCreateRequest",
    "LinkRemoveRequest",
    "OverlapDecisionRequest",
    "build_view",
    "delay_blockers",
    "engine_events",
    "granted_days_of",
    "head_treatments",
    "hindrance_snapshot",
    "previously_granted",
    "register_proposal",
    "revision_of",
]
