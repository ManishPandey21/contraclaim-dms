"""Stage B, deterministic: compose both summaries from the Fact Pack alone.

This renderer always runs. The optional model pass (:mod:`.presentation`) may
replace the wording of the narrative sections and the short summary; it can
never change a fact, and when its output fails validation this rendering is
what is stored. The summary degrades in style, never in accuracy.

Wording rules (pinned with ``GROUNDED_SUMMARY_PROMPT_VERSION``):

* a statement from the letter is quoted and attributed - "<party> states:" -
  never restated as an established fact;
* a value is shown as the letter writes it; a normalised form is added only
  when the text settles it (an ambiguous numeric date stays as written);
* uncertainty is a section of its own and is never dropped.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

from ...models.grounded_summary import (
    DETAILED_SECTIONS,
    SECTION_TITLES,
    GroundedFact,
    GroundedSummaryItem,
)
from .fact_pack import FactPack, weakest

SHORT_SUMMARY_LIMIT = 240
SECTION_ITEM_LIMIT = 12

#: Statement role -> the attribution the item text opens with.
ATTRIBUTION: Dict[str, str] = {
    "party_position": "{party} states",
    "request": "{party} requests",
    "instruction": "{party} instructs",
    "reservation": "{party} reserves",
    "claim": "{party} asserts",
}

#: Fact type -> detailed-summary section.
STATEMENT_SECTIONS: Dict[str, str] = {
    "background_statement": "background",
    "position_statement": "issues_positions",
    "request_statement": "requests_relief",
    "instruction_statement": "requests_relief",
    "reservation_statement": "claims_reservations",
    "claim_statement": "claims_reservations",
}

QUALIFICATION_TYPES = frozenset(
    {"page_qualification", "metadata_qualification", "date_qualification", "unlocated_metadata_clause"}
)


def item_from_facts(
    text: str, facts: Sequence[GroundedFact], *, role: Optional[str] = None, party: Optional[str] = None
) -> GroundedSummaryItem:
    """An item whose status, pages and spans are derived from its facts only."""
    pages: List[int] = []
    spans = []
    for fact in facts:
        for page in fact.page_numbers:
            if page not in pages:
                pages.append(page)
        spans.extend(fact.source_spans)
    return GroundedSummaryItem(
        text=text,
        role=(role or (facts[0].role if facts and facts[0].role else "source_fact")),  # type: ignore[arg-type]
        party=party,
        fact_ids=[fact.id for fact in facts],
        page_numbers=sorted(pages),
        verification_status=weakest(fact.verification_status for fact in facts),  # type: ignore[arg-type]
        source_spans=spans,
    )


def _statement_text(fact: GroundedFact) -> str:
    template = ATTRIBUTION.get(str(fact.role or "party_position"), "{party} states")
    lead = template.format(party=fact.party or "The author")
    return f"{lead} (as written): “{fact.quote or fact.value}”"


def _event_facts(pack: FactPack) -> List[GroundedFact]:
    """Position statements that state a date: the letter's own sequence."""
    dated_spans = [
        (span.start_char, span.end_char)
        for fact in pack.by_type("date")
        for span in fact.source_spans
    ]
    events = []
    for fact in pack.by_type("position_statement", "background_statement"):
        for span in fact.source_spans:
            if any(span.start_char <= start < span.end_char for start, _ in dated_spans):
                events.append(fact)
                break
    return events


def deterministic_sections(pack: FactPack) -> Dict[str, List[GroundedSummaryItem]]:
    sections: Dict[str, List[GroundedSummaryItem]] = {key: [] for key in DETAILED_SECTIONS}
    events = {fact.id for fact in _event_facts(pack)}
    for fact in pack.facts:
        section = STATEMENT_SECTIONS.get(fact.type)
        if section is None:
            continue
        if fact.id in events:
            section = "material_events"
        sections[section].append(item_from_facts(_statement_text(fact), [fact], party=fact.party))

    for fact in pack.by_type("clause"):
        text = f"The letter cites {fact.value}"
        if fact.quote:
            text += f": “{fact.quote}”"
        sections["contractual_basis"].append(item_from_facts(text, [fact], role="source_fact"))

    for fact in pack.by_type("letter_date"):
        sections["dates_periods"].append(
            item_from_facts(f"Letter date: {fact.value}", [fact], role="source_fact")
        )
    for fact in pack.by_type("date"):
        shown = str(fact.value)
        if fact.normalized_value:
            shown += f" ({fact.normalized_value})"
        else:
            shown += " (day/month order not settled by the text)"
        text = f"{shown} — “{fact.quote}”" if fact.quote else shown
        sections["dates_periods"].append(item_from_facts(text, [fact], role="source_fact"))

    for fact in pack.by_type("amount", "quantity"):
        text = str(fact.value)
        if fact.type == "amount" and fact.unit and fact.normalized_value is not None:
            text = f"{fact.unit} {fact.normalized_value:,} (written “{fact.value}”)"
        if fact.quote:
            text += f" — “{fact.quote}”"
        sections["amounts_quantities"].append(item_from_facts(text, [fact], role="source_fact"))

    for fact in pack.by_type("reference"):
        if fact.resolved_document_id:
            text = f"Reference stated in the letter: {fact.value} — linked DMS record"
        else:
            text = f"Reference stated in the letter: {fact.value} — not linked to a DMS record"
        sections["references"].append(item_from_facts(text, [fact], role="reference"))

    for fact in pack.facts:
        if fact.type in QUALIFICATION_TYPES:
            text = (
                f"Extracted metadata lists clause ‘{fact.value}’, which this revision's text does not state."
                if fact.type == "unlocated_metadata_clause"
                else str(fact.value)
            )
            sections["uncertainties"].append(item_from_facts(text, [fact], role="uncertainty"))

    for key, items in sections.items():
        if key != "uncertainties":
            sections[key] = items[:SECTION_ITEM_LIMIT]
    return sections


def _join(values: Iterable[str]) -> str:
    items = list(values)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def executive_overview(pack: FactPack) -> Optional[str]:
    """One paragraph of header facts and counts - nothing interpreted."""
    header = pack.header
    if not any(header.values()):
        return None
    parts = ["Letter"]
    if header.get("letterNo"):
        parts.append(str(header["letterNo"]))
    if header.get("date"):
        parts.append(f"dated {header['date']}")
    if header.get("from"):
        parts.append(f"from {header['from']}")
    if header.get("to"):
        parts.append(f"to {header['to']}")
    sentence = " ".join(parts)
    if header.get("subject"):
        sentence += f", subject “{header['subject']}”"
    sentence += "."
    counts = []
    requests = len(pack.by_type("request_statement", "instruction_statement"))
    if requests:
        counts.append(f"{requests} request/instruction statement{'s' if requests != 1 else ''}")
    claims = len(pack.by_type("claim_statement", "reservation_statement"))
    if claims:
        counts.append(f"{claims} claim/reservation statement{'s' if claims != 1 else ''}")
    clauses = [str(f.value) for f in pack.by_type("clause")][:4]
    if clauses:
        counts.append("cites " + _join(clauses))
    references = len(pack.by_type("reference"))
    if references:
        counts.append(f"refers to {references} earlier document{'s' if references != 1 else ''}")
    if counts:
        sentence += " The letter contains " + _join(counts) + "."
    uncertain = len([f for f in pack.facts if f.type in QUALIFICATION_TYPES])
    if uncertain:
        sentence += f" {uncertain} qualification{'s' if uncertain != 1 else ''} apply (see Uncertainty)."
    return sentence


def overview_fact_ids(pack: FactPack) -> List[str]:
    return [
        fact.id
        for fact in pack.facts
        if fact.type in {"letter_number", "letter_date", "from_party", "to_party", "subject"}
    ]


def short_summary(pack: FactPack) -> Optional[str]:
    """``What is this letter about?`` from header facts, <= 240 characters."""
    header = pack.header
    subject = header.get("subject")
    sender, recipient = header.get("from"), header.get("to")
    if not (subject or sender):
        return None
    lead = "Letter"
    if sender:
        lead += f" from {sender}"
    if recipient:
        lead += f" to {recipient}"
    if subject:
        room = SHORT_SUMMARY_LIMIT - len(lead) - len(" regarding “”.")
        topic = subject if len(subject) <= room else subject[: max(room - 1, 0)].rstrip() + "…"
        text = f"{lead} regarding “{topic}”."
    else:
        text = lead + "."
    clauses = [str(f.value) for f in pack.by_type("clause")][:2]
    if clauses:
        tail = f" Cites {_join(clauses)}."
        if len(text) + len(tail) <= SHORT_SUMMARY_LIMIT:
            text += tail
    return text[:SHORT_SUMMARY_LIMIT]


_STATUS_MARK = {
    "verified": "verified",
    "source_stated": "as stated",
    "human_approved": "human-approved",
    "not_checkable": "not checkable",
    "uncertain": "uncertain",
}


def render_markdown(overview: Optional[str], sections: Dict[str, List[GroundedSummaryItem]]) -> str:
    lines: List[str] = []
    if overview:
        lines += ["**Executive overview**", "", overview, ""]
    for key in DETAILED_SECTIONS:
        items = sections.get(key) or []
        if not items:
            continue
        lines += [f"**{SECTION_TITLES[key]}**", ""]
        for item in items:
            pages = ", ".join(str(p) for p in item.page_numbers)
            marker = f" [p.{pages}]" if pages else ""
            lines.append(f"- {item.text}{marker} _({_STATUS_MARK[item.verification_status]})_")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


__all__ = [
    "ATTRIBUTION",
    "QUALIFICATION_TYPES",
    "SHORT_SUMMARY_LIMIT",
    "STATEMENT_SECTIONS",
    "deterministic_sections",
    "executive_overview",
    "item_from_facts",
    "overview_fact_ids",
    "render_markdown",
    "short_summary",
]
