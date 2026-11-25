"""4B-35 (D-17, C-11): one citation parser, verified as an allow-list, exhibits first-class."""

from __future__ import annotations

import pytest

from rbac_backend.services.arbitration_drafting.citation_refs import (
    CITATION_VERIFIED_STATUSES,
    exhibit_refs,
    has_citation,
    source_refs,
)
from rbac_backend.services.arbitration_drafting.citation_states import classify_citations, is_unverified_source
from rbac_backend.services.arbitration_drafting.context import ledger_verification_status
from rbac_backend.services.arbitration_drafting.validator import ArbitrationDraftValidator

# --------------------------------------------------------------------------- #
# Format corpus
# --------------------------------------------------------------------------- #
SOURCE_CORPUS = [
    ("The notice was late [S3: Notice letter].", {"S3": 1}),
    ("The notice was late [S3].", {"S3": 1}),
    ("Per [SRC-4] and [SRC-4: same].", {"SRC-4": 2}),
    ("[S1: A] [S12: B] [S1]", {"S1": 2, "S12": 1}),
    ("No citation here, S3 or [Evidence required].", {}),
]


@pytest.mark.parametrize("text,expected", SOURCE_CORPUS)
def test_every_source_key_form_is_read(text: str, expected: dict) -> None:
    assert source_refs(text) == expected


EXHIBIT_CORPUS = [
    ("See Ex. C-12.", {"C-12": 1}),
    ("See Exh. C-12 and Exhibit C 12.", {"C-12": 2}),
    ("Exhibit R/3 shows the handover.", {"R-3": 1}),
    ("The witness produced CW-1/4.", {"CW-1/4": 1}),
    ("The witness produced CW1/4 and RW-2/10.", {"CW-1/4": 1, "RW-2/10": 1}),
    ("Annexure A-7 is the programme.", {"A-7": 1}),
    ("Annex. A/7 and Ann. A-7.", {"A-7": 2}),
    ("As shown at C-12 and R-3.", {"C-12": 1, "R-3": 1}),
    ("ex. c-012 is the same exhibit as Ex. C-12.", {"C-12": 2}),
]


@pytest.mark.parametrize("text,expected", EXHIBIT_CORPUS)
def test_every_exhibit_reference_form_is_read_and_normalised(text: str, expected: dict) -> None:
    assert exhibit_refs(text) == expected


NOT_EXHIBITS = [
    "The road NH-48 was closed.",
    "Dated 12/04/2026 and 2026-04-12.",
    "Clause 2.1 and Sub-Clause 8.4 apply.",
    "Grade c-12 concrete in lower-case prose.",
    "Invoice INV-2024/17 and form MB-3.",
    "Reference ABC/C-12/2026 is a letter number.",
]


@pytest.mark.parametrize("text", NOT_EXHIBITS)
def test_ordinary_references_are_not_read_as_exhibits(text: str) -> None:
    assert exhibit_refs(text) == {}


def test_an_exhibit_named_inside_a_source_label_is_not_counted_twice() -> None:
    assert exhibit_refs("The delay is admitted [S3: Ex. C-12, letter of 11 April].") == {}
    assert has_citation("The delay is admitted [S3: Ex. C-12].")


def test_has_citation_accepts_an_exhibit_alone() -> None:
    assert has_citation("The Respondent failed to hand over the Site (Ex. C-12).")
    assert not has_citation("The Respondent failed to hand over the Site.")


# --------------------------------------------------------------------------- #
# Verified is an allow-list
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status",
    [None, "", "unknown_new_state", "rejected", "stale", "ai_suggested", "selected", "needs_review", "authority_denied"],
)
def test_any_status_outside_the_allow_list_is_unverified(status) -> None:
    row = {"source_key": "S1", "verification_status": status}
    result = classify_citations("Fact [S1].", [row])
    assert result["items"][0]["state"] == "unverified", status
    assert is_unverified_source(row)


@pytest.mark.parametrize("status", sorted(CITATION_VERIFIED_STATUSES))
def test_each_allow_listed_status_is_verified(status: str) -> None:
    row = {"source_key": "S1", "verification_status": status.upper()}
    assert classify_citations("Fact [S1].", [row])["items"][0]["state"] == "verified"


def test_user_supplied_evidence_is_unverified_whatever_its_status() -> None:
    row = {"source_key": "S1", "verification_status": "approved", "is_user_supplied": True}
    assert classify_citations("Fact [S1].", [row])["items"][0]["state"] == "unverified"


def test_bare_and_src_keys_are_checked_against_the_ledger() -> None:
    result = classify_citations("A [S1]. B [SRC-2].", [{"source_key": "S1", "verification_status": "approved"}])
    states = {item["source_key"]: item["state"] for item in result["items"]}
    assert states == {"S1": "verified", "SRC-2": "unresolved"}


# --------------------------------------------------------------------------- #
# Exhibits resolve against the case exhibits in the ledger
# --------------------------------------------------------------------------- #
LEDGER = [
    {"source_key": "S1", "verification_status": "approved", "exhibit_id": "C-12", "label": "Notice letter"},
    {"source_key": "S2", "verification_status": "needs_review", "exhibit_id": "C-13"},
]


def test_exhibits_are_verified_unverified_or_unresolved_by_the_case_exhibit_list() -> None:
    result = classify_citations("See Ex. C-12, Exhibit C/13 and Annexure A-7.", LEDGER)
    exhibits = {item["exhibit_id"]: item for item in result["items"] if item["kind"] == "exhibit"}
    assert exhibits["C-12"]["state"] == "verified" and exhibits["C-12"]["ledger_key"] == "S1"
    assert exhibits["C-13"]["state"] == "unverified"
    assert exhibits["A-7"]["state"] == "unresolved"
    assert exhibits["A-7"]["source_key"] == "Ex. A-7"
    assert result["counts"]["unresolved"] == 1


def _validator_report(markdown: str, ledger: list) -> dict:
    context = {
        "source_ledger": ledger,
        "draft": {"case_id": "case-1", "draft_type": "statement_of_defence"},
        "matrix_context": {},
    }
    return ArbitrationDraftValidator().validation_report(context, markdown)


def test_a_draft_citing_a_missing_exhibit_is_blocked_by_name() -> None:
    """C-11: before 4B-35 this draft read as citing no sources and was not blocked on the exhibit."""
    report = _validator_report("The Respondent failed to hand over the Site as recorded in Ex. C-99.", LEDGER)
    blockers = report["approval_blockers"]
    assert any("not in the case's exhibit list: Ex. C-99" in item for item in blockers), blockers


def test_a_cited_exhibit_counts_as_a_same_sentence_citation() -> None:
    report = _validator_report("The Respondent failed to hand over the Site as recorded in Ex. C-12.", LEDGER)
    assert not any("factual assertions without a same-sentence source citation" in item for item in report["approval_blockers"])
    assert not any("exhibit list" in item for item in report["approval_blockers"])


# --------------------------------------------------------------------------- #
# Ledger status defaults (C-11): never "approved" by default
# --------------------------------------------------------------------------- #
def test_a_ledger_row_with_no_status_is_unverified_not_approved() -> None:
    assert ledger_verification_status({}, "approval_status") == "unverified"


def test_an_approval_outranks_a_non_approval_field_read_first() -> None:
    # Expert alignment and issues carried their own state first; the approval decides.
    assert ledger_verification_status({"alignment_status": "aligned", "approval_status": "approved"}, "approval_status") == "approved"
    assert ledger_verification_status({"status": "open", "approval_status": "approved"}, "approval_status", "status") == "approved"


def test_a_negative_status_wins_over_an_approval() -> None:
    assert ledger_verification_status({"approval_status": "approved", "status": "stale"}, "approval_status") == "stale"


def test_a_review_status_is_reported_as_it_is() -> None:
    assert ledger_verification_status({"approval_status": "needs_review"}, "approval_status") == "needs_review"
