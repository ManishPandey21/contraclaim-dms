"""The factual-grounding check that runs after a Letter Bench reply is composed.

The first three cases are sentences gpt-4o wrote in the 2026-11-28 release
evaluation to justify a decided position, with nothing in the sources behind
them. They must never pass as supported.
"""

from __future__ import annotations

import pytest

from rbac_backend.models.letter_drafting import SourceEvidence
from rbac_backend.services.letter_drafting.grounding import Confirmation, GroundingInputs, analyse

INCOMING_DOC = "doc-incoming"
SENDER = (
    "We hereby give notice under Sub-Clause 20.2 that access to the Site was delayed by 31 days. "
    "Accordingly we claim an extension of time of 31 days under Sub-Clause 8.4."
)


def _src(source_id, text, source_type="context_document", **kw):
    use = "clause" if source_type == "contract_clause" else "fact"
    return SourceEvidence(source_id=source_id, source_type=source_type, allowed_use=use, label=kw.pop("label", source_id), text=text, **kw)


SOURCES = [
    _src("clause-20.2", "The claiming Party shall give a Notice of Claim as soon as practicable and not later than 28 days.",
         "contract_clause", clause_number="20.2", label="Sub-Clause 20.2"),
    _src("clause-8.4", "The Contractor shall be entitled to an extension of the Time for Completion if completion is delayed.",
         "contract_clause", clause_number="8.4", label="Sub-Clause 8.4"),
    _src("diary-0910", "Site diary 9 October 2026: the delay to site access recorded by the Engineer is 12 days.",
         document_id="doc-diary", page_numbers=[3], label="Site diary"),
    _src("incoming", SENDER, document_id=INCOMING_DOC, label="Incoming letter"),
]


def _one(sentence, **kw):
    inputs = GroundingInputs(sources=SOURCES, incoming_document_id=INCOMING_DOC, sender_texts=[SENDER], **kw)
    statements = analyse(sentence, inputs)
    assert len(statements) == 1, statements
    return statements[0]


@pytest.mark.parametrize(
    "sentence",
    [
        "The contract documents and site records do not substantiate the delay as claimed.",
        "Furthermore, the notice requirements under Sub-Clause 20.2 have not been met in accordance with the contractual stipulations.",
        "Our evaluation indicates that the design provided was in accordance with the contractual requirements and industry standards.",
    ],
)
def test_invented_justifications_from_the_real_model_are_unsupported(sentence) -> None:
    statement = _one(sentence)
    assert statement.status == "unsupported" and statement.material and statement.requires_confirmation


def test_requiring_substantiation_is_not_a_factual_claim() -> None:
    statement = _one(
        "The Contractor is requested to substantiate the alleged delay with contemporaneous records and a delay analysis."
    )
    assert statement.status == "not_applicable" and not statement.material


def test_a_placeholder_needs_confirmation() -> None:
    statement = _one("[CONFIRM: whether the site records support the alleged 31-day delay in access]")
    assert statement.classification == "requires_confirmation" and statement.requires_confirmation


def test_an_attributed_assertion_is_not_asserted_as_fact() -> None:
    statement = _one("The Contractor states that access to the Site was delayed by 31 days.")
    assert statement.classification == "contractor_assertion" and not statement.material


def test_the_senders_claim_restated_as_fact_is_unsupported() -> None:
    statement = _one("Access to the Site was delayed by 31 days.")
    assert statement.status in {"unsupported", "contradicted"} and statement.material


def test_a_fact_in_an_independent_document_is_supported_with_its_passage() -> None:
    statement = _one("The site diary of 9 October 2026 records a delay to site access of 12 days.")
    assert statement.status == "supported"
    assert statement.source_id == "diary-0910" and statement.document_id == "doc-diary" and statement.location == "p. 3"
    assert "12 days" in (statement.passage or "")


def test_a_number_that_disagrees_with_the_document_is_contradicted() -> None:
    statement = _one("The site diary records a delay to site access of 31 days.")
    assert statement.status == "contradicted" and "12" in (statement.contradiction or "")


def test_a_clause_requirement_matching_the_clause_text_is_supported() -> None:
    statement = _one("Sub-Clause 20.2 requires a Notice of Claim not later than 28 days.")
    assert statement.status == "supported"


def test_a_clause_outside_the_ledger_is_unsupported() -> None:
    statement = _one("Under Clause 99.9 the Contractor must give a further notice.")
    assert statement.status == "unsupported" and statement.material


def test_a_valid_citation_alone_does_not_make_a_sentence_supported() -> None:
    statement = _one("Sub-Clause 8.4 entitles the Employer to recover liquidated damages for every day.")
    assert statement.status != "supported"


def test_a_reviewer_confirmation_with_a_basis_is_recorded() -> None:
    sentence = "The Engineer inspected the drainage works on 2 November 2026 and found them incomplete."
    statement = _one(sentence, confirmations=[Confirmation(text=sentence, basis="Inspection report IR-22, seen by reviewer", confirmed_by="u7")])
    assert statement.status == "user_confirmed" and statement.confirmation_basis.startswith("Inspection report")


def test_an_explicit_user_input_supports_a_fact() -> None:
    statement = _one(
        "The Engineer issued instruction EI-14 for the revised drainage on 3 September.",
        user_inputs=["The Engineer issued instruction EI-14 for the revised drainage on 3 September."],
    )
    assert statement.status == "user_confirmed"


def test_the_incoming_letters_own_reference_and_date_are_supported() -> None:
    sender = "Ref: NCW/EOT/118   Date: 02-11-2026\n" + SENDER
    inputs = GroundingInputs(sources=SOURCES, incoming_document_id=INCOMING_DOC, sender_texts=[sender])
    [statement] = analyse("We refer to your letter NCW/EOT/118 dated 2026-11-02.", inputs)
    assert statement.status == "supported" and not statement.material


def test_a_fabricated_correspondence_reference_is_unsupported() -> None:
    sender = "Ref: NCW/EOT/118   Date: 02-11-2026\n" + SENDER
    inputs = GroundingInputs(sources=SOURCES, incoming_document_id=INCOMING_DOC, sender_texts=[sender])
    [statement] = analyse("As stated in your letter NCW/EOT/097 dated 14-10-2026, the works were suspended.", inputs)
    assert statement.status == "unsupported" and statement.material and "NCW/EOT/097" in (statement.contradiction or "").upper()


def test_misstating_what_the_sender_claimed_is_caught() -> None:
    statement = _one("The Contractor claims an extension of time of 45 days.")
    assert statement.status == "unsupported" and statement.material


def test_saying_the_sender_cited_no_clause_is_checked_against_the_letter() -> None:
    # Written by gpt-4o in the 30-letter evaluation; the letter does cite 20.2 and 8.4.
    statement = _one("The Contractor has not cited any specific contractual clause that supports this claim.")
    assert statement.status == "contradicted" and statement.material and "20.2" in (statement.contradiction or "")
    no_incoming = [s for s in SOURCES if s.document_id != INCOMING_DOC]
    inputs = GroundingInputs(sources=no_incoming, incoming_document_id=INCOMING_DOC, sender_texts=["We claim idle plant costs."])
    [ok] = analyse("The Contractor has not cited any contractual clause for this claim.", inputs)
    assert ok.status == "supported" and not ok.material


def test_the_drafting_sides_own_argument_is_a_position() -> None:
    inputs = GroundingInputs(sources=SOURCES, incoming_document_id=INCOMING_DOC, sender_texts=[SENDER], drafting_role="employer")
    [own] = analyse("The Employer contends that the deduction of delay damages is in accordance with the Contract.", inputs)
    assert own.classification == "proposed_argument" and not own.material


def test_reported_speech_of_the_other_party_is_an_attribution() -> None:
    inputs = GroundingInputs(sources=SOURCES, incoming_document_id=INCOMING_DOC, sender_texts=[SENDER], drafting_role="employer")
    [other] = analyse("The Contractor states that access to the Site was delayed by 31 days.", inputs)
    assert other.classification == "contractor_assertion" and not other.material


@pytest.mark.parametrize(
    "sentence",
    [
        "However, the Employer finds that the information provided is insufficient to evaluate this claim.",
        "The Contractor has not provided sufficient information to support this request.",
        "We propose a joint inspection to ascertain the nature and cause of the defect.",
        "We confirm our intention to attend the meeting.",
    ],
)
def test_assessments_of_the_submission_and_proposals_are_not_facts(sentence) -> None:
    statement = _one(sentence)
    assert not statement.material, statement


def test_framing_lines_and_positions_are_not_facts() -> None:
    inputs = GroundingInputs(sources=SOURCES, incoming_document_id=INCOMING_DOC, sender_texts=[SENDER])
    statements = analyse(
        "To: Northwind Civil Works\nSubject: Reply\n\nThe Employer does not accept that an extension of time is due.\n\nYours faithfully,",
        inputs,
    )
    assert [s.status for s in statements] == ["not_applicable"]
