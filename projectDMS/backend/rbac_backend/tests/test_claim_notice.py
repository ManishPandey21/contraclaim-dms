"""Claim notice assessment and status lifecycle.

The notice period used here is never read from the executed contract, so every
result must say where its period came from, and a late result against a
typical figure must stay labelled as assumed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from rbac_backend.models.claim import (
    ALLOWED_STATUS_TRANSITIONS,
    Claim,
    ClaimCreate,
    ClaimStatus,
    allowed_next_statuses,
)
from rbac_backend.models.project import Project
from rbac_backend.routers.claims import _read_model, _require_status_transition
from rbac_backend.services.claims_notice import assess_notice
from rbac_backend.services.sla_service import compute_sla_items

NOW = datetime(2026, 11, 15)


def _claim(**fields) -> dict:
    return {"_id": "c1", "title": "Delayed handover", "type": "eot", "status": "submitted", **fields}


# --------------------------------------------------------------------------- #
# Notice assessment
# --------------------------------------------------------------------------- #
def test_notice_served_after_a_typical_period_is_late_but_labelled_assumed() -> None:
    result = assess_notice(
        _claim(event_date=datetime(2026, 3, 2), notice_date=datetime(2026, 4, 11)), now=NOW
    )
    assert result is not None
    assert result.state == "served_late" and result.served_late_by_days == 12
    assert result.period_days == 28 and result.due_date == datetime(2026, 3, 30)
    assert result.period_source == "assumed_default" and result.period_verified is False
    assert result.basis == "event" and result.days_remaining is None


def test_notice_on_the_last_day_is_in_time() -> None:
    result = assess_notice(
        _claim(event_date=datetime(2026, 3, 2), notice_date=datetime(2026, 3, 30, 23, 59)), now=NOW
    )
    assert result.state == "served_in_time" and result.served_late_by_days is None


def test_awareness_date_takes_precedence_over_event_date() -> None:
    result = assess_notice(
        _claim(
            event_date=datetime(2026, 3, 2),
            awareness_date=datetime(2026, 3, 20),
            notice_date=datetime(2026, 4, 11),
        ),
        now=NOW,
    )
    assert result.basis == "awareness" and result.basis_date == datetime(2026, 3, 20)
    assert result.state == "served_in_time"  # 22 days from awareness


def test_an_organisation_rule_is_reported_as_such_and_still_not_as_verified() -> None:
    result = assess_notice(
        _claim(event_date=datetime(2026, 3, 2), notice_date=datetime(2026, 3, 20)),
        now=NOW,
        organisation_windows={"eot": 14},
    )
    assert result.period_days == 14 and result.period_source == "organisation_rule"
    assert result.period_verified is False
    assert result.state == "served_late" and result.served_late_by_days == 4


def test_a_rule_for_another_claim_type_does_not_apply() -> None:
    result = assess_notice(
        _claim(event_date=datetime(2026, 3, 2)), now=NOW, organisation_windows={"variation": 7}
    )
    assert result.period_days == 28 and result.period_source == "assumed_default"


@pytest.mark.parametrize(
    "today, state, remaining",
    [
        (datetime(2026, 3, 10), "open", 20),
        (datetime(2026, 3, 23), "due_soon", 7),
        (datetime(2026, 3, 30), "due_soon", 0),
        (datetime(2026, 3, 31), "overdue", -1),
    ],
)
def test_outstanding_notice_counts_down_to_the_due_day(today, state, remaining) -> None:
    result = assess_notice(_claim(event_date=datetime(2026, 3, 2)), now=today)
    assert (result.state, result.days_remaining) == (state, remaining)
    assert result.served_on is None and result.served_late_by_days is None


def test_no_date_to_run_from_gives_no_assessment() -> None:
    assert assess_notice(_claim(notice_date=datetime(2026, 4, 11)), now=NOW) is None
    assert assess_notice(_claim(), now=NOW) is None


def test_a_settled_claim_with_no_recorded_notice_is_not_chased() -> None:
    assert assess_notice(_claim(status="closed", event_date=datetime(2026, 3, 2)), now=NOW) is None
    served = assess_notice(
        _claim(status="closed", event_date=datetime(2026, 3, 2), notice_date=datetime(2026, 4, 11)),
        now=NOW,
    )
    assert served.state == "served_late"  # what happened stays on the record


def test_timezone_aware_and_string_dates_are_compared_by_calendar_day() -> None:
    result = assess_notice(
        _claim(
            event_date="2026-03-02T00:00:00Z",
            notice_date=datetime(2026, 3, 30, 18, 0, tzinfo=timezone.utc),
        ),
        now=NOW,
    )
    assert result.state == "served_in_time"


def test_unknown_claim_type_falls_back_to_the_general_period() -> None:
    result = assess_notice({"status": "draft", "type": "novel", "event_date": datetime(2026, 3, 2)}, now=NOW)
    assert result.period_days == 28 and result.period_source == "assumed_default"


def test_time_bar_tracker_runs_from_awareness_when_recorded() -> None:
    items = compute_sla_items(
        [_claim(event_date=datetime(2026, 3, 2), awareness_date=datetime(2026, 3, 20))],
        now=datetime(2026, 4, 1),
    )
    time_bar = next(item for item in items if item["kind"] == "time_bar")
    assert time_bar["due_date"] == datetime(2026, 4, 17)


# --------------------------------------------------------------------------- #
# Read model
# --------------------------------------------------------------------------- #
class _Windows:
    def __init__(self, windows=None) -> None:
        self.windows = windows or {}

    async def for_claim(self, claim: dict) -> dict:
        return self.windows

    async def requirement_for(self, claim: dict):
        return None


async def test_claim_read_carries_the_assessment_and_the_allowed_moves() -> None:
    claim = _claim(event_date=datetime(2026, 3, 2), notice_date=datetime(2026, 4, 11))
    read = await _read_model(claim, ["d1"], _Windows())
    assert read.notice_assessment.state == "served_late"
    assert read.linked_document_ids == ["d1"]
    assert read.allowed_status_transitions == allowed_next_statuses("submitted")
    assert ClaimStatus.DRAFT not in read.allowed_status_transitions


async def test_stored_derived_fields_are_ignored_and_recomputed() -> None:
    claim = _claim(
        event_date=datetime(2026, 3, 2),
        notice_assessment={"state": "served_in_time"},
        allowed_status_transitions=["draft"],
    )
    read = await _read_model(claim, [], _Windows())
    # No notice is recorded, so the stored "served in time" cannot survive.
    assert read.notice_assessment.state != "served_in_time" and read.notice_assessment.served_on is None
    assert ClaimStatus.DRAFT not in read.allowed_status_transitions


def test_derived_fields_are_not_part_of_the_stored_claim() -> None:
    stored = Claim(**ClaimCreate(title="A", awareness_date=datetime(2026, 3, 20)).model_dump()).model_dump()
    assert stored["awareness_date"] == datetime(2026, 3, 20)
    assert "notice_assessment" not in stored and "allowed_status_transitions" not in stored


# --------------------------------------------------------------------------- #
# Status lifecycle
# --------------------------------------------------------------------------- #
def test_every_status_has_a_transition_entry() -> None:
    assert set(ALLOWED_STATUS_TRANSITIONS) == set(ClaimStatus)


@pytest.mark.parametrize(
    "current, target",
    [("draft", "notified"), ("notified", "submitted"), ("submitted", "under_review"),
     ("under_review", "disputed"), ("rejected", "disputed"), ("agreed", "closed"),
     ("closed", "disputed"), ("draft", "draft")],
)
def test_lifecycle_moves_are_allowed(current: str, target: str) -> None:
    _require_status_transition({"status": current}, ClaimStatus(target))


@pytest.mark.parametrize(
    "current, target",
    [("closed", "draft"), ("agreed", "draft"), ("draft", "agreed"), ("draft", "disputed"),
     ("notified", "agreed"), ("submitted", "draft"), ("agreed", "rejected")],
)
def test_other_moves_are_refused_and_the_refusal_lists_what_is_allowed(current: str, target: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_status_transition({"status": current}, ClaimStatus(target))
    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert f"is {current} cannot be moved to {target}" in detail
    for allowed in allowed_next_statuses(current):
        assert allowed.value in detail


def test_a_claim_with_no_stored_status_is_treated_as_a_draft() -> None:
    _require_status_transition({}, ClaimStatus.NOTIFIED)
    with pytest.raises(HTTPException):
        _require_status_transition({"status": None}, ClaimStatus.AGREED)


def test_an_unrecognised_stored_status_allows_no_move() -> None:
    assert allowed_next_statuses("archived-legacy") == []


# --------------------------------------------------------------------------- #
# Project working perspective
# --------------------------------------------------------------------------- #
def test_project_perspective_is_unset_until_stated_and_rejects_other_values() -> None:
    assert Project(name="NH-48", organization_id="org-A").contractual_capacity is None
    assert Project(name="NH-48", organization_id="org-A", contractualCapacity="engineer").contractual_capacity == "engineer"
    stored = Project(name="NH-48", organization_id="org-A", contractual_capacity="contractor")
    assert stored.model_dump(by_alias=True, exclude_unset=True, exclude_none=True)["contractual_capacity"] == "contractor"
    with pytest.raises(ValidationError):
        Project(name="NH-48", organization_id="org-A", contractual_capacity="subcontractor")
