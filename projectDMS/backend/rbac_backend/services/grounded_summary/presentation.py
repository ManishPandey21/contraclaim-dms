"""Stage B, optional model pass: reword narrative sections over the Fact Pack.

Off by default (``GROUNDED_SUMMARY_LLM_ENABLED``). When on, the model receives
the Fact Pack only - ids, labels, values and the quoted sentences, never the
document text - and must return JSON in which every item cites ``fact_ids``.

:func:`validate_presentation` is the boundary. Output is rejected as a whole
when an item cites no fact or an unknown one, carries a number the cited facts
do not contain, restates an attributed statement without attribution, or asserts
a conclusion ("established", "is entitled", "liable") the cited quotes do not.
A rejection is not an error: the deterministic rendering is stored instead and
the reason is recorded on ``generator.llm_rejection``.

The model never touches dates, amounts, clauses, references or uncertainty:
those sections are always the deterministic rendering.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple

from ...models.grounded_summary import GROUNDED_SUMMARY_PROMPT_VERSION, GroundedSummaryItem
from .fact_pack import FactPack, is_assertable
from .render import item_from_facts

#: Sections the model may word. Everything else is deterministic only.
LLM_SECTIONS: Tuple[str, ...] = (
    "background",
    "material_events",
    "issues_positions",
    "requests_relief",
    "claims_reservations",
)
STATEMENT_ROLES = frozenset({"party_position", "request", "instruction", "reservation", "claim"})
SHORT_SUMMARY_MAX = 280
ITEM_MAX = 700

PROMPT = f"""You format a grounded summary of one contract letter. Prompt version: {GROUNDED_SUMMARY_PROMPT_VERSION}.

You receive FACTS: a JSON list. Each fact has an id, a type, a value, a verification status, and for
statements made in the letter the party who makes them and the verbatim quote.

Rules - every one is checked by a program and a violation discards your whole answer:
1. Use only the supplied facts. Add nothing: no dates, clauses, amounts, names or events that are not in
   the cited facts' values or quotes.
2. Every item cites the ids of the facts it is built from in "fact_ids". No item without fact_ids.
3. A statement made in the letter stays attributed: write "<party> states/requests/asserts/reserves ...".
   Never turn "the Contractor states X" into "X occurred". Preserve who says what (Contractor states,
   Engineer instructs, Employer decides).
4. Do not infer entitlement, liability or merit. Never write that anything is established, proven,
   valid, justified or owed.
5. Facts with status "uncertain" stay uncertain: say so, do not resolve the ambiguity, do not drop
   qualifications.
6. Do not copy numbers into new forms; repeat them exactly as the fact gives them.

Return JSON only, this shape:
{{"short_summary": {{"text": "1-2 sentences, at most 240 characters", "fact_ids": ["..."]}},
  "executive_overview": {{"text": "...", "fact_ids": ["..."]}},
  "sections": {{"background": [{{"text": "...", "fact_ids": ["..."]}}],
               "material_events": [], "issues_positions": [], "requests_relief": [],
               "claims_reservations": []}}}}
"""


class PresentationModel(Protocol):
    """One JSON completion. Raises on any provider failure."""

    model_name: str

    async def complete(self, system: str, user: str) -> Tuple[str, Dict[str, Any]]:
        ...


class PresentationRejected(ValueError):
    pass


#: The facts the model may cite: what it is allowed to word (header, the
#: letter's statements, the clauses and headline amounts a short summary may
#: name). Dates, tables, references and qualifications are rendered without
#: it, so it is never shown them - which also keeps its input well below the
#: size of the document itself.
MODEL_FACT_TYPES = frozenset(
    {
        "letter_number", "letter_date", "subject", "from_party", "to_party",
        "background_statement", "position_statement", "request_statement",
        "instruction_statement", "reservation_statement", "claim_statement",
        "clause", "amount",
    }
)
#: Header facts the model may see only when the text states them: a value
#: that is uncertain or not checkable is never handed to it to word.
_HEADER_TYPES = frozenset({"letter_number", "letter_date", "subject", "from_party", "to_party"})
#: Quotes only travel with statements; a clause or amount is cited by value.
_QUOTED_TYPES = frozenset(t for t in MODEL_FACT_TYPES if t.endswith("_statement"))


def model_facts(pack: FactPack) -> List[Any]:
    return [
        fact
        for fact in pack.facts
        if fact.type in MODEL_FACT_TYPES
        and not (fact.type == "amount" and not fact.unit)
        and not (fact.type in _HEADER_TYPES and not is_assertable(fact))
    ]


def fact_pack_payload(pack: FactPack) -> List[Dict[str, Any]]:
    """What the model sees: the citable facts only. Never page text."""
    payload = []
    for fact in model_facts(pack):
        row: Dict[str, Any] = {
            "id": fact.id,
            "type": fact.type,
            "label": fact.label,
            "value": fact.value,
            "status": fact.verification_status,
        }
        if fact.normalized_value not in (None, ""):
            row["normalized_value"] = fact.normalized_value
        if fact.unit:
            row["unit"] = fact.unit
        if fact.party:
            row["party"] = fact.party
        if fact.role:
            row["role"] = fact.role
        if fact.type in _QUOTED_TYPES and fact.quote and fact.quote != fact.value:
            row["quote"] = fact.quote
        if fact.page_numbers:
            row["pages"] = fact.page_numbers
        payload.append(row)
    return payload


def build_user_message(pack: FactPack) -> str:
    return "FACTS:\n" + json.dumps(
        fact_pack_payload(pack), ensure_ascii=False, default=str, separators=(",", ":")
    )


_NUMBER = re.compile(r"\d+(?:[.,/\-]\d+)*")
_ATTRIBUTION = re.compile(
    r"\b(?:states?|stated|alleges?|alleged|asserts?|asserted|claims?|claimed|contends?|requests?|"
    r"requested|reserves?|reserved|says|submits?|notif(?:y|ies|ied)|instructs?|instructed|seeks?|"
    r"sought|according\s+to|writes|maintains?|records?|refers?|asks?|directs?|calls?\s+(?:on|upon)|"
    r"puts?\s+on\s+record|informs?|advises?)\b",
    re.IGNORECASE,
)
_CONCLUSION = re.compile(
    r"\b(?:is|was|are|were|been|be)\s+(?:established|proven|proved|valid|justified|owed|due\s+and\s+payable)\b|"
    r"\bit\s+is\s+(?:clear|evident|established)\b|\bliable\b|\bis\s+entitled\b",
    re.IGNORECASE,
)


def _digits(text: str) -> str:
    return re.sub(r"[^\d]", "", text)


def _fact_corpus(facts: List[Any]) -> str:
    parts: List[str] = []
    for fact in facts:
        for value in (fact.label, fact.value, fact.normalized_value, fact.quote, fact.party):
            if value not in (None, ""):
                parts.append(str(value))
    return " ".join(parts)


def _check_item(raw: Any, pack: FactPack, where: str) -> Tuple[str, List[Any]]:
    if not isinstance(raw, Mapping):
        raise PresentationRejected(f"{where}: not an object")
    text = str(raw.get("text") or "").strip()
    ids = raw.get("fact_ids")
    if not text:
        raise PresentationRejected(f"{where}: empty text")
    if len(text) > ITEM_MAX:
        raise PresentationRejected(f"{where}: text longer than {ITEM_MAX} characters")
    if not isinstance(ids, list) or not ids:
        raise PresentationRejected(f"{where}: cites no fact")
    facts = []
    citable = {fact.id for fact in model_facts(pack)}
    for fact_id in ids:
        fact = pack.get(str(fact_id)) if str(fact_id) in citable else None
        if fact is None:
            raise PresentationRejected(f"{where}: cites unknown fact {fact_id!r}")
        facts.append(fact)
    corpus = _fact_corpus(facts)
    corpus_digits = _digits(corpus)
    for number in _NUMBER.findall(text):
        if number not in corpus and _digits(number) not in corpus_digits:
            raise PresentationRejected(f"{where}: number {number!r} is not in the cited facts")
    if any(fact.role in STATEMENT_ROLES for fact in facts) and not _ATTRIBUTION.search(text):
        raise PresentationRejected(f"{where}: an attributed statement is restated without attribution")
    for match in _CONCLUSION.finditer(text):
        if match.group(0).lower() not in corpus.lower():
            raise PresentationRejected(f"{where}: asserts a conclusion ({match.group(0)!r}) no fact states")
    return text, facts


def _require_assertable(facts: List[Any], where: str) -> None:
    """The short summary and the overview state facts plainly: no qualified ones."""
    for fact in facts:
        if not is_assertable(fact):
            raise PresentationRejected(
                f"{where}: cites {fact.id!r}, whose status ({fact.verification_status}) may not be stated as fact"
            )


@dataclass
class Presentation:
    short_summary: Optional[str]
    executive_overview: Optional[str]
    overview_fact_ids: List[str]
    sections: Dict[str, List[GroundedSummaryItem]]


def validate_presentation(raw_text: str, pack: FactPack) -> Presentation:
    try:
        data = json.loads(raw_text)
    except (TypeError, ValueError) as exc:
        raise PresentationRejected(f"not JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise PresentationRejected("not a JSON object")

    short: Optional[str] = None
    if data.get("short_summary") is not None:
        short, facts = _check_item(data["short_summary"], pack, "short_summary")
        _require_assertable(facts, "short_summary")
        if len(short) > SHORT_SUMMARY_MAX:
            raise PresentationRejected("short_summary longer than allowed")

    overview: Optional[str] = None
    overview_ids: List[str] = []
    if data.get("executive_overview") is not None:
        overview, facts = _check_item(data["executive_overview"], pack, "executive_overview")
        _require_assertable(facts, "executive_overview")
        overview_ids = [fact.id for fact in facts]

    sections: Dict[str, List[GroundedSummaryItem]] = {}
    raw_sections = data.get("sections") or {}
    if not isinstance(raw_sections, Mapping):
        raise PresentationRejected("sections is not an object")
    for key, items in raw_sections.items():
        if key not in LLM_SECTIONS:
            raise PresentationRejected(f"section {key!r} may not be written by the model")
        if not isinstance(items, list):
            raise PresentationRejected(f"section {key!r} is not a list")
        built = []
        for index, item in enumerate(items):
            text, facts = _check_item(item, pack, f"{key}[{index}]")
            party = next((fact.party for fact in facts if fact.party), None)
            role = next((fact.role for fact in facts if fact.role in STATEMENT_ROLES), None)
            built.append(item_from_facts(text, facts, role=role or "source_fact", party=party))
        sections[key] = built
    return Presentation(short, overview, overview_ids, sections)


class OpenAIPresentationModel:
    """JSON-mode chat completion. Raises on any failure; no fallback inside."""

    def __init__(self, api_key: str, model_name: str, timeout: float = 60.0) -> None:
        from openai import AsyncOpenAI  # type: ignore

        self.model_name = model_name
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def complete(self, system: str, user: str) -> Tuple[str, Dict[str, Any]]:
        completion = await self._client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=2500,
        )
        usage = getattr(completion, "usage", None)
        choice = completion.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise PresentationRejected("model output truncated at the token limit")
        return choice.message.content or "", {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        }


__all__ = [
    "LLM_SECTIONS",
    "OpenAIPresentationModel",
    "PROMPT",
    "Presentation",
    "PresentationModel",
    "PresentationRejected",
    "build_user_message",
    "fact_pack_payload",
    "validate_presentation",
]
