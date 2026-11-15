"""Summary and Key Reply Points mean the same thing on every extraction path.

Two LLM prompts are live: the numbered report prompt
(``OpenAIService._expanded_extraction_prompt``, used for whole-file and
OCR-text extraction on legacy_v0 and unified_v1 alike) and the PydanticAI
prompt plus output schema (primary when ``use_pydantic_ai`` is on). Both take
their definitions of these two fields from ``models.document_metadata``.

These tests pin the contract (what is asked for, and what is forbidden), not
the exact prose.
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace

import pytest

from rbac_backend.models.document_metadata import (
    KEY_REPLY_POINTS_EXTRACTION_INSTRUCTION,
    METADATA_EXTRACTION_PROMPT_VERSION,
    SUMMARY_EXTRACTION_INSTRUCTION,
)
from rbac_backend.retrieval.correspondence_payload import ADVISORY_FIELDS, DESCRIPTIVE_FIELDS
from rbac_backend.services.openai_service import OpenAIService
from rbac_backend.services.pydantic_ai_service import PydanticAIService
from rbac_backend.services.text_processing_service import TextProcessingService


def _report_prompt() -> str:
    return OpenAIService._expanded_extraction_prompt(SimpleNamespace())  # type: ignore[arg-type]


def _pydantic_prompt() -> str:
    service: PydanticAIService = SimpleNamespace()  # type: ignore[assignment]
    return PydanticAIService._build_expanded_prompt(service, "LETTER TEXT", {})


def _item(prompt: str, number: int) -> str:
    match = re.search(rf"^{number}\) .*$", prompt, flags=re.M)
    assert match, f"item {number} missing"
    return match.group(0)


def _parser() -> TextProcessingService:
    return TextProcessingService(SimpleNamespace(chunk_size=1000, chunk_overlap=100))


# --- the definitions -----------------------------------------------------------


def test_summary_definition_asks_for_detailed_chronological_grounded_summary() -> None:
    text = SUMMARY_EXTRACTION_INSTRUCTION.lower()
    for required in (
        "sufficient detail",
        "background",
        "sequence of material events",
        "chronology",
        "dates",
        "strictly on the letter",
        "do not add external information",
        "do not supply dates or events the letter does not state",
    ):
        assert required in text, required
    # The old RAG-shaped brief (a short summary with contractual implication)
    # invited legal analysis the letter never states.
    assert "4-6 line" not in text
    assert "contractual implication" not in text


def test_summary_definition_is_bounded_for_the_shared_output_budget() -> None:
    # Summary shares one 4096-token report budget with Full Content (the
    # letter itself) and the tags after it; an open-ended "in detail" brief
    # measurably crowds them out. Detail is kept, expansion is bounded.
    text = SUMMARY_EXTRACTION_INSTRUCTION.lower()
    assert "summarise in detail" not in text
    assert "avoiding repetition" in text
    assert "material event" in text
    assert "one sentence each" in text


def test_key_reply_points_definition_is_exhaustive_grounded_and_not_a_reply() -> None:
    text = KEY_REPLY_POINTS_EXTRACTION_INSTRUCTION.lower()
    assert "every point" in text
    for matter in (
        "issue",
        "allegation",
        "request",
        "instruction",
        "demand",
        "question",
        "rejection",
        "criticism",
        "responsibility attribution",
        "contractual position",
        "claim",
        "reservation",
        "deadline",
        "commitment",
    ):
        assert matter in text, matter
    for response in ("acknowledgement", "clarification", "substantiation", "response"):
        assert response in text, response
    assert "grounded in something the letter actually states" in text
    for forbidden in (
        "do not draft the reply",
        "do not decide contractual entitlement",
        "do not propose counterarguments",
        "do not add facts or clauses the letter does not state",
        "not quotations or facts",
    ):
        assert forbidden in text, forbidden


# --- every active prompt carries them --------------------------------------------


@pytest.mark.parametrize("prompt_factory", [_report_prompt, _pydantic_prompt], ids=["report", "pydantic_ai"])
def test_every_active_prompt_uses_the_shared_definitions(prompt_factory) -> None:
    prompt = prompt_factory()
    assert SUMMARY_EXTRACTION_INSTRUCTION in _item(prompt, 22)
    assert KEY_REPLY_POINTS_EXTRACTION_INSTRUCTION in _item(prompt, 24)
    assert "4-6 line" not in prompt
    assert "vector search/RAG, mentioning issue" not in prompt


def test_report_prompt_keeps_the_labels_the_parser_and_payload_guard_match() -> None:
    prompt = _report_prompt()
    # The parser matches item number AND label; the correspondence payload
    # guard refuses text carrying the item-24 heading. Neither may move.
    assert _item(prompt, 22).startswith("22) Summary: [")
    assert _item(prompt, 24).startswith(
        "24) Key Reply Points - Points to be Addressed While Responding: ["
    )
    # A numbered chronology inside item 22 would be split by the parser.
    assert "do not number the lines" in _item(prompt, 22)


def test_pydantic_output_schema_descriptions_use_the_same_definitions() -> None:
    # The schema model is built inside __init__ only when an API key and
    # pydantic_ai are present, so the contract is pinned on the source.
    source = inspect.getsource(PydanticAIService.__init__)
    for field, constant in (
        ("summary_points", "SUMMARY_EXTRACTION_INSTRUCTION"),
        ("summary_text", "SUMMARY_EXTRACTION_INSTRUCTION"),
        ("key_reply_points", "KEY_REPLY_POINTS_EXTRACTION_INSTRUCTION"),
    ):
        declaration = re.search(rf"{field}: .*?Field\((.*?)\n            \)", source, flags=re.S)
        assert declaration, field
        assert f"description={constant}" in declaration.group(1) or (
            f"+ {constant}" in declaration.group(1)
        ), field


def test_prompt_version_was_bumped() -> None:
    assert METADATA_EXTRACTION_PROMPT_VERSION == "existing_document_metadata.v4"


# --- source/analysis separation is unchanged -------------------------------------


def test_reply_points_stay_advisory_and_summary_stays_descriptive() -> None:
    assert "key_reply_points" in ADVISORY_FIELDS
    assert "key_reply_points" not in DESCRIPTIVE_FIELDS
    assert "summary" in DESCRIPTIVE_FIELDS


# --- parser fixture: chronology and every reply point survive ---------------------

CHRONOLOGICAL_REPORT = (
    "1) Date: 14-10-2024\n"
    "2) Letter No.: EMP/VIA/2024/118\n"
    "3) From (Company): Employer\n"
    "4) To (Company): Contractor\n"
    "5) Subject: Delay to viaduct pier P12 foundations\n"
    "22) Summary: - On 01-08-2024 the Engineer issued the revised pier drawings.\n"
    "- 23-08-2024: the Contractor raised RFI-44 on the pile cap levels.\n"
    "- 12.09.2024 the Engineer answered RFI-44.\n"
    "- 2 October 2024: work on pier P12 stopped for utility diversion.\n"
    "24) Key Reply Points - Points to be Addressed While Responding: "
    "- The Employer alleges the Contractor delayed mobilisation at P12.\n"
    "- The Employer requests a recovery programme within 7 days.\n"
    "- The Employer rejects the Contractor's hindrance notice of 03-10-2024.\n"
    "- The Employer reserves the right to levy liquidated damages.\n"
    "25) Full Content: The letter body.\n"
)


def test_parsed_summary_keeps_every_event_its_date_and_the_order() -> None:
    parsed = _parser().parse_extraction_report(CHRONOLOGICAL_REPORT)
    assert parsed.summary is not None
    lines = parsed.summary.splitlines()
    assert lines == [
        "- On 01-08-2024 the Engineer issued the revised pier drawings.",
        "- 23-08-2024: the Contractor raised RFI-44 on the pile cap levels.",
        "- 12.09.2024 the Engineer answered RFI-44.",
        "- 2 October 2024: work on pier P12 stopped for utility diversion.",
    ]


def test_parsed_key_reply_points_keep_every_distinct_point() -> None:
    parsed = _parser().parse_extraction_report(CHRONOLOGICAL_REPORT)
    assert len(parsed.key_reply_points) == 4
    joined = " ".join(parsed.key_reply_points)
    for point in ("mobilisation", "recovery programme", "hindrance notice", "liquidated damages"):
        assert point in joined, point
    # Reply points never leak into the summary, and the header is untouched.
    assert "liquidated damages" not in (parsed.summary or "")
    assert parsed.letter_no == "EMP/VIA/2024/118"
    assert parsed.subject == "Delay to viaduct pier P12 foundations"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("- Engineer issued drawings.", "- Engineer issued drawings."),
        ("* Engineer issued drawings.", "- Engineer issued drawings."),
        ("1. Engineer issued drawings.", "- Engineer issued drawings."),
        ("2) Engineer issued drawings.", "- Engineer issued drawings."),
        ("Engineer issued drawings.", "- Engineer issued drawings."),
        ("- 01-08-2024: Engineer issued drawings.", "- 01-08-2024: Engineer issued drawings."),
        ("01.08.2024 Engineer issued drawings.", "- 01.08.2024 Engineer issued drawings."),
        ("- 2024: a difficult year.", "- 2024: a difficult year."),
        ("- 12.08.2024 Engineer issued drawings", "- 12.08.2024 Engineer issued drawings"),
        ("- 12/08/2024 Engineer issued drawings", "- 12/08/2024 Engineer issued drawings"),
        ("- 2024-08-12 Engineer issued drawings", "- 2024-08-12 Engineer issued drawings"),
        ("3. 12/08/2024 Engineer issued drawings", "- 12/08/2024 Engineer issued drawings"),
        ("(4) 2024-08-12 Engineer issued drawings", "- 2024-08-12 Engineer issued drawings"),
    ],
)
def test_summary_line_markers_are_stripped_but_leading_dates_are_kept(line: str, expected: str) -> None:
    assert _parser()._summary_from_block(line) == expected


@pytest.mark.parametrize(
    "event",
    [
        "01-08-2024: Engineer issued drawings",
        "12.08.2024 Engineer issued drawings",
        "12/08/2024 Engineer issued drawings",
        "2024-08-12 Engineer issued drawings",
    ],
)
def test_a_date_led_summary_line_survives_the_full_report_parse_exactly(event: str) -> None:
    report = (
        "1) Date: 14-10-2024\n"
        "2) Letter No.: EMP/VIA/2024/118\n"
        "5) Subject: Delay\n"
        f"22) Summary: - {event}\n"
        "- On 23-08-2024 the Contractor raised RFI-44\n"
        "25) Full Content: The letter body.\n"
    )
    parsed = _parser().parse_extraction_report(report)
    assert parsed.summary == f"- {event}\n- On 23-08-2024 the Contractor raised RFI-44"


def test_the_fallback_summary_extractor_keeps_leading_dates_too() -> None:
    text = "Summary: - 01-08-2024: Engineer issued drawings\n- 12/08/2024 RFI raised\nKey Words: x"
    assert _parser()._extract_summary(text) == (
        "- 01-08-2024: Engineer issued drawings\n- 12/08/2024 RFI raised"
    )
