"""Phase 3 of the multi-agent letter-drafting workflow:

Legal / Contractual Risk Review Agent — flags admissions, waivers, entitlement
creation and stance reversals vs the previous position (flags only, never
blocks) — and locked-paragraph support for the Redraft Agent (instruction
block + verbatim survival verification).
"""

from __future__ import annotations

from rbac_backend.services.letter_drafting.legal_risk_reviewer import LegalRiskReviewer
from rbac_backend.services.letter_drafting.locked_text import (
    locked_instruction,
    verify_locked_paragraphs,
)


REVIEWER = LegalRiskReviewer()


# --------------------------------------------------------------------------- #
# Legal risk review: admissions / waivers / entitlement
# --------------------------------------------------------------------------- #
def test_admission_flagged_high():
    report = REVIEWER.review(
        "Further to your letter, we accept full liability for the delay to the works."
    )
    assert report.human_review_required is True
    categories = {f.category for f in report.flags}
    assert "admission" in categories
    flag = next(f for f in report.flags if f.category == "admission")
    assert flag.severity == "high"
    assert "liability" in flag.excerpt.lower()


def test_waiver_flagged():
    report = REVIEWER.review("In the interest of progress, we hereby waive our right to claim prolongation cost.")
    assert any(f.category == "waiver" and f.severity == "high" for f in report.flags)


def test_entitlement_creation_flagged():
    report = REVIEWER.review("We confirm the Employer is entitled to recover the amounts withheld.")
    assert any(f.category == "entitlement" for f in report.flags)


def test_clean_draft_produces_no_flags():
    report = REVIEWER.review(
        "We refer to your letter and confirm that the method statement was submitted "
        "on 17-11-2023 in accordance with Clause 8.4. Supporting records are enclosed."
    )
    assert report.flags == []
    assert report.human_review_required is False
    assert report.scanned_at is not None
    assert report.human_reviewed_at is None


# --------------------------------------------------------------------------- #
# Contradiction vs previous position (caution, both excerpts, never blocks)
# --------------------------------------------------------------------------- #
def test_stance_reversal_flagged_as_caution():
    report = REVIEWER.review(
        "Having reviewed the records, we agree to the deduction proposed.",
        previous_positions=["In our letter ref 2201 we reject the proposed deduction in full."],
    )
    contradiction = next((f for f in report.flags if f.category == "contradiction"), None)
    assert contradiction is not None
    assert contradiction.severity == "caution"
    assert "Previous position" in contradiction.excerpt
    assert "Draft" in contradiction.excerpt


def test_consistent_stance_not_flagged():
    report = REVIEWER.review(
        "We reject the proposed deduction for the reasons previously stated.",
        previous_positions=["We reject the proposed deduction in full."],
    )
    assert all(f.category != "contradiction" for f in report.flags)


def test_no_previous_position_no_contradiction():
    report = REVIEWER.review("We agree to the revised programme.", previous_positions=[])
    assert all(f.category != "contradiction" for f in report.flags)


# --------------------------------------------------------------------------- #
# Locked paragraphs
# --------------------------------------------------------------------------- #
LOCKED = (
    "Our position under Clause 8.4 remains unchanged and all rights are "
    "expressly reserved."
)


def test_locked_instruction_lists_blocks_verbatim():
    block = locked_instruction([LOCKED, "Second locked paragraph."])
    assert "LOCKED PARAGRAPHS" in block
    assert "[LOCKED 1]" in block and "[LOCKED 2]" in block
    assert LOCKED in block


def test_locked_instruction_empty_for_no_locks():
    assert locked_instruction([]) == ""
    assert locked_instruction(["   "]) == ""


def test_verify_passes_when_paragraph_survives_rewrapped():
    # Model re-wrapped the lines and changed case of surrounding text —
    # whitespace-normalized verbatim match must still pass.
    draft = (
        "Dear Sir,\n\nOur position under Clause 8.4\nremains unchanged and all "
        "rights are\nexpressly reserved.\n\nYours faithfully,"
    )
    assert verify_locked_paragraphs(draft, [LOCKED]) == []


def test_verify_reports_modified_paragraph():
    draft = (
        "Dear Sir,\n\nOur position under Clause 8.4 may be reconsidered subject "
        "to your response.\n\nYours faithfully,"
    )
    missing = verify_locked_paragraphs(draft, [LOCKED])
    assert missing == [LOCKED]


def test_verify_multiple_locks_partial_violation():
    kept = "The records enclosed with this letter are contemporaneous."
    draft = f"Intro.\n\n{kept}\n\nAltered ending."
    missing = verify_locked_paragraphs(draft, [kept, LOCKED])
    assert missing == [LOCKED]
