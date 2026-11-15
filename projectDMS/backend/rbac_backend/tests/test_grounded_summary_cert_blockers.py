"""Release 1.2 staging certification blockers (P0, P1) for the grounded summary.

P0: a header or reference value the canonical text does not state must never be
rendered as a plain fact - not in the short summary, not in the executive
overview, not in a factual section. It belongs in the uncertainty section,
qualified, and it stays in the Fact Pack for audit.

P1: letterhead lines glued to a page's first sentence must not cost that
sentence, and a clause list ("8.4.1 and 17.1") must keep every clause.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from rbac_backend.services.grounded_summary.fact_pack import split_sentences
from rbac_backend.services.grounded_summary.service import compose
from rbac_backend.tests.retry_harness import FakeDb
from rbac_backend.tests.test_grounded_summary import FakeModel, _facts_of, _pack, _page, _seed

PLANTED_SENDER = "Zenith Fabricated Holdings Pvt Ltd"
PLANTED_RECIPIENT = "Orion Planted Authority"
PLANTED_REFERENCE = "ZFH/PLANTED/2099/777"

FACTUAL_SECTIONS = (
    "background",
    "material_events",
    "issues_positions",
    "contractual_basis",
    "requests_relief",
    "claims_reservations",
    "dates_periods",
    "amounts_quantities",
    "references",
)


def _factual_texts(detailed: Any) -> List[str]:
    return [item.text for key in FACTUAL_SECTIONS for item in getattr(detailed, key)]


def _planted_reference_list() -> List[Dict[str, Any]]:
    return [
        {"letterNo": "GLM/KNP/UPMRC/2022/2327", "date": "24-12-2022"},
        {"letterNo": "GC/KNP/GLM/2023/118", "date": "03-02-2023"},
        {"letterNo": PLANTED_REFERENCE, "date": "01-01-2099"},
    ]


# --- P0: uncertain header values ----------------------------------------------------


async def test_planted_uncertain_sender_is_not_asserted_anywhere_but_uncertainty() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db, **{"from": PLANTED_SENDER}))
    assert pack.by_type("from_party")[0].verification_status == "uncertain"

    short, detailed = await compose(pack, evidence)

    assert PLANTED_SENDER not in (short or "")
    assert PLANTED_SENDER not in (detailed.executive_overview or "")
    assert not any(PLANTED_SENDER in text for text in _factual_texts(detailed))
    qualified = [item for item in detailed.uncertainties if PLANTED_SENDER in item.text]
    assert qualified and all(item.verification_status == "uncertain" for item in qualified)
    # Still on record for audit.
    assert any(f.value == PLANTED_SENDER for f in detailed.facts)


async def test_planted_uncertain_recipient_is_not_asserted_anywhere_but_uncertainty() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db, to=PLANTED_RECIPIENT))
    assert pack.by_type("to_party")[0].verification_status == "uncertain"

    short, detailed = await compose(pack, evidence)

    assert PLANTED_RECIPIENT not in (short or "")
    assert PLANTED_RECIPIENT not in (detailed.executive_overview or "")
    assert not any(PLANTED_RECIPIENT in text for text in _factual_texts(detailed))
    assert any(PLANTED_RECIPIENT in item.text for item in detailed.uncertainties)


async def test_human_edited_sender_the_text_does_not_state_is_qualified_not_asserted() -> None:
    db = FakeDb()
    pack, evidence = await _pack(
        db, await _seed(db, **{"from": PLANTED_SENDER, "human_edited_fields": ["from"]})
    )
    assert pack.by_type("from_party")[0].verification_status == "not_checkable"

    short, detailed = await compose(pack, evidence)

    assert PLANTED_SENDER not in (short or "")
    assert PLANTED_SENDER not in (detailed.executive_overview or "")
    assert not any(PLANTED_SENDER in text for text in _factual_texts(detailed))
    assert any(PLANTED_SENDER in item.text for item in detailed.uncertainties)


async def test_uncertain_letter_date_is_not_asserted_in_the_overview() -> None:
    db = FakeDb()
    # 23 June is never written in the text.
    pack, evidence = await _pack(db, await _seed(db, date="2024-06-23T00:00:00"))
    assert pack.by_type("letter_date")[0].verification_status == "uncertain"

    _, detailed = await compose(pack, evidence)

    assert "23 June 2024" not in (detailed.executive_overview or "")


# --- P0: uncertain references -------------------------------------------------------


async def test_planted_uncertain_reference_is_not_counted_or_listed_as_stated() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db, reference=_planted_reference_list()))
    planted = [f for f in pack.by_type("reference") if PLANTED_REFERENCE in str(f.value)]
    assert planted and planted[0].verification_status == "uncertain"

    short, detailed = await compose(pack, evidence)

    overview = detailed.executive_overview or ""
    assert "refers to 2 earlier documents" in overview, overview
    assert "3 earlier" not in overview
    assert PLANTED_REFERENCE not in (short or "")
    assert not any(PLANTED_REFERENCE in item.text for item in detailed.references)
    assert any(PLANTED_REFERENCE in item.text for item in detailed.uncertainties)
    # Provenance keeps it.
    assert any(PLANTED_REFERENCE in str(f.value) for f in detailed.facts if f.type == "reference")


async def test_verified_sender_and_references_still_render_normally() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))

    short, detailed = await compose(pack, evidence)

    assert short and short.startswith("Letter from Gulermak-Sam India Kanpur Metro JV to General Consultant, UPMRC")
    overview = detailed.executive_overview or ""
    assert "from Gulermak-Sam India Kanpur Metro JV" in overview
    assert "dated 22 June 2024" in overview
    assert "refers to 2 earlier documents" in overview
    assert len(detailed.references) == 2
    assert not any("Gulermak" in item.text or "2327" in item.text for item in detailed.uncertainties)


# --- P0: the model pass cannot re-introduce them ------------------------------------------


async def test_the_model_is_never_shown_an_uncertain_header_value() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db, **{"from": PLANTED_SENDER}))
    sender_id = pack.by_type("from_party")[0].id
    seen: Dict[str, Any] = {}

    def reply(user: str) -> str:
        seen["facts"] = _facts_of(user)
        return json.dumps(
            {"short_summary": {"text": f"Letter from {PLANTED_SENDER}.", "fact_ids": [sender_id]}}
        )

    short, detailed = await compose(pack, evidence, model=FakeModel(reply))

    assert not any(PLANTED_SENDER in str(fact.get("value")) for fact in seen["facts"])
    assert not any(PLANTED_SENDER in str(fact.get("party") or "") for fact in seen["facts"])
    assert PLANTED_SENDER not in (short or "")
    assert detailed.generator.mode == "deterministic"


# --- P1: letterhead glued to the first sentence ------------------------------------------


async def _pack_for_pages(pages: List[str], **fields: Any):
    db = FakeDb()
    extracted = [_page(n, text) for n, text in enumerate(pages, start=1)]
    return await _pack(db, await _seed(db, pages=extracted, **fields))


LETTERHEAD_PAGE_TWO = (
    "GULERMAK-SAM INDIA KANPUR METRO JV\n"
    "Site Office: Nayaganj, Kanpur 208001\n"
    "Tel +91 512 0000000 Email jv@example.invalid\n"
    "In view of the above, the Contractor requests the Engineer's Representative to determine and approve "
    "the additional cost under GCC Clause 17.1 at the earliest.\n"
    "The remaining heads will follow separately."
)


async def test_letterhead_lines_do_not_cost_the_first_request_on_a_page() -> None:
    pack, _ = await _pack_for_pages(["Covering page with nothing to classify.", LETTERHEAD_PAGE_TWO])

    requests = [f for f in pack.by_type("request_statement") if 2 in f.page_numbers]
    assert requests, [f.value for f in pack.facts if f.type.endswith("_statement")]
    quote = requests[0].quote or ""
    assert quote.startswith("In view of the above, the Contractor requests")
    assert "Site Office" not in quote and "GULERMAK" not in quote


DELAY_NOTICE_PAGE = (
    "Gulermak-Sam India Kanpur Metro JV\n"
    "Kanpur Metro Rail Project, Package KNPCC-06\n"
    "Letter No: GLM/KNP/UPMRC/2024/5001\n"
    "Date: 12.03.2024\n"
    "To: The Engineer's Representative, UPMRC\n"
    "Sub: Notice of delay in casting of roof slab at Chunniganj Station\n"
    "Dear Sir,\n"
    "We hereby notify you that the casting of the roof slab at Chunniganj Station has been delayed due to the "
    "non-availability of the approved shuttering drawings.\n"
    "The delay is continuing and its effect on the programme is being assessed."
)


async def test_header_block_and_salutation_do_not_cost_the_opening_delay_notice() -> None:
    pack, _ = await _pack_for_pages([DELAY_NOTICE_PAGE])

    statements = [f for f in pack.facts if f.type.endswith("_statement")]
    notice = [f for f in statements if "casting of the roof slab" in (f.quote or "")]
    assert notice, [f.quote for f in statements]
    assert (notice[0].quote or "").startswith("We hereby notify you")
    assert notice[0].page_numbers == [1]


def test_letterhead_is_split_from_body_prose_with_offsets_intact() -> None:
    class _Page:
        def __init__(self, number: int, start: int, end: int) -> None:
            self.page_number, self.start, self.end = number, start, end

    class _Evidence:
        def __init__(self, text: str) -> None:
            self.text = text
            self.pages = [_Page(1, 0, len(text))]

    text = LETTERHEAD_PAGE_TWO
    sentences = split_sentences(_Evidence(text))  # type: ignore[arg-type]
    body = [s for s in sentences if s.text.startswith("In view of the above")]
    assert body, [s.text for s in sentences]
    assert text[body[0].start : body[0].end] == body[0].text
    # Prose wrapped across lines inside one sentence stays one sentence.
    wrapped = "We request the Engineer to\ndetermine the claim under Clause 17.1 of GCC without further delay."
    joined = split_sentences(_Evidence(wrapped))  # type: ignore[arg-type]
    assert len(joined) == 1


# --- P1: clause lists --------------------------------------------------------------------


def _clause_numbers(pack: Any) -> List[str]:
    return [str(f.normalized_value) for f in pack.by_type("clause")]


async def _clauses_in(sentence: str) -> List[str]:
    pack, _ = await _pack_for_pages([f"{sentence}\n"], contractual_clauses=[])
    return _clause_numbers(pack)


async def test_and_joined_sub_clause_list_keeps_both_clauses() -> None:
    numbers = await _clauses_in(
        "The Contractor gives notice under sub-clause 8.4.1 and 17.1 of the conditions of contract for this delay."
    )
    assert "8.4.1" in numbers and "17.1" in numbers


async def test_comma_and_list_keeps_every_clause() -> None:
    numbers = await _clauses_in(
        "The Contractor reserves its rights under Clauses 8.4.1, 17.1 and 20.1 of the contract in full."
    )
    assert {"8.4.1", "17.1", "20.1"} <= set(numbers)


async def test_slash_joined_gcc_list_keeps_both_clauses() -> None:
    numbers = await _clauses_in("The Contractor relies on GCC 8.4.1 / 17.1 for the extension sought here.")
    assert "8.4.1" in numbers and "17.1" in numbers


async def test_read_with_keeps_both_clauses() -> None:
    numbers = await _clauses_in(
        "The claim is made under Clause 8.4.1 read with Clause 17.1 of the General Conditions of Contract."
    )
    assert "8.4.1" in numbers and "17.1" in numbers


async def test_clause_lists_do_not_truncate_or_invent_numbers() -> None:
    numbers = await _clauses_in(
        "The Contractor gives notice under sub-clause 8.4.1 and 17.1 of the contract, as on 12 days of delay."
    )
    assert "8.4" not in numbers
    assert sorted(set(numbers)) == ["17.1", "8.4.1"]


async def test_one_clause_under_two_spellings_is_listed_once() -> None:
    numbers = await _clauses_in(
        "We request the Engineer to determine the claim under Clause 17.1 of GCC and Sub-Clause 17.1 of the contract."
    )
    assert numbers.count("17.1") == 1


async def test_a_figure_or_date_after_a_clause_is_not_read_as_another_clause() -> None:
    numbers = await _clauses_in(
        "The Contractor claims under Clause 17.1 and 2.5 lakhs is due, see Clause 8.4 and 12.03.2024 notice, "
        "for Clause 20.1 and 14 days."
    )
    assert sorted(set(numbers)) == ["17.1", "20.1", "8.4"]


async def test_the_same_number_under_different_conditions_stays_two_clauses() -> None:
    numbers = await _clauses_in(
        "The Contractor relies on Clause 17.1 of GCC as amended by Clause 17.1 of the Special Conditions here."
    )
    assert numbers.count("17.1") == 2
