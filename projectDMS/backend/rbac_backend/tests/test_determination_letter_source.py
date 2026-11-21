"""4B-31: which determinations a letter may be issued from, and what it carries (pure)."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException

from rbac_backend.models.claim_findings import ClaimDetermination, HeadFinding, InformationRequest
from rbac_backend.services.claim_determinations import information_items, letter_source

CLAIM = {"_id": "c1", "claim_ref": "CLM-9", "title": "Late access", "organization_id": "o1", "project_id": "p1"}


def _det(status: str = "approved", origin: str = "authored", items: bool = True) -> ClaimDetermination:
    requests = (
        [InformationRequest(item="Programme", deadline=datetime(2026, 12, 9)),
         InformationRequest(item="Records", deadline=datetime(2026, 12, 1), consequence="Decided without them")]
        if items else []
    )
    return ClaimDetermination(
        claim_id="c1", origin=origin, issued_by="employer", status=status,
        heads=[HeadFinding(head_id="H1", head_title="Access", result="full", days=7, amount=None,
                           findings="Shown.", information_required=requests)],
    )


def _code(error: HTTPException) -> str:
    return error.detail["code"]


@pytest.mark.parametrize("status", ["draft", "returned", "in_review", "recorded"])
def test_a_determination_letter_needs_an_approved_determination(status: str) -> None:
    with pytest.raises(HTTPException) as refused:
        letter_source(CLAIM, _det(status=status), "determination")
    assert refused.value.status_code == 409
    assert _code(refused.value) in {"determination_letter_not_approved", "determination_letter_not_authored"}


def test_a_received_determination_starts_no_letter() -> None:
    for purpose in ("determination", "information_request"):
        with pytest.raises(HTTPException) as refused:
            letter_source(CLAIM, _det(status="recorded", origin="received"), purpose)  # type: ignore[arg-type]
        assert _code(refused.value) == "determination_letter_not_authored"


def test_a_request_for_information_needs_items_and_an_open_or_approved_determination() -> None:
    with pytest.raises(HTTPException) as refused:
        letter_source(CLAIM, _det(status="draft", items=False), "information_request")
    assert _code(refused.value) == "determination_no_information_items"
    for status in ("draft", "returned", "in_review", "approved"):
        assert letter_source(CLAIM, _det(status=status), "information_request").trigger["type"] == "information_request"


def test_items_are_earliest_deadline_first_and_the_letter_is_the_issuing_party() -> None:
    assert [entry["item"] for entry in information_items(_det())] == ["Records", "Programme"]
    source = letter_source(CLAIM, _det(), "determination")
    assert source.role == "employer"
    assert source.trigger["heads"][0]["days"] == 7
    assert "Records (head H1), by 2026-12-01. If not supplied: Decided without them" in source.text
    assert source.title == "Determination of claim CLM-9"
    rfi = letter_source(CLAIM, _det(), "information_request")
    assert "heads" not in rfi.trigger and "Finding:" not in rfi.text
    assert rfi.subject == "Request for information: Late access"
