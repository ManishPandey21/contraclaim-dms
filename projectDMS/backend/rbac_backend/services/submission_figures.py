"""Figures, clauses and reservations in submission text, read strictly (plan 4B-28, C-16).

A skill may rewrite a submission section, but only within the record. The old
check compared bare digit strings against the whole record, so it accepted:

- amounts in words ("forty-five crore") and scaled amounts ("INR 10 lakh") whose
  digits happened to appear elsewhere, in a date or a day count (borrowed digits);
- a figure moved from one head to another (cross-head swap);
- "rights are not reserved", because the reservation check was the substring "reserv";
- and it missed "Cl. 8.4" and "Article 8.4", while reading "Clause 2026" as a clause.

Here every figure is typed: money (with its scale applied), days, or a plain
number. A figure is acceptable only if the record has the same value of the
same type, and, where a paragraph is about one head, only if that head's own
record has it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Set, Tuple

SCALES = {
    "thousand": Decimal(1_000),
    "lakh": Decimal(100_000),
    "lakhs": Decimal(100_000),
    "lac": Decimal(100_000),
    "lacs": Decimal(100_000),
    "crore": Decimal(10_000_000),
    "crores": Decimal(10_000_000),
    "cr": Decimal(10_000_000),
    "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
}
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORD = "|".join(sorted([*_UNITS, *_TENS, "hundred", *SCALES, "and"], key=len, reverse=True))

_CURRENCY = r"(?:INR|Rs\.?|₹|USD|\$)"
_DIGITS = r"\d[\d,]*(?:\.\d+)?"
_SCALE = r"(?:thousand|lakhs?|lacs?|crores?|cr|million|billion)\b"
#: A number written in digits, optionally preceded by a currency and followed by a scale or "day(s)".
_DIGIT_FIGURE = re.compile(
    rf"(?P<currency>{_CURRENCY}\s*)?(?<![A-Za-z\d.,])(?P<number>{_DIGITS})(?:\s*(?P<scale>{_SCALE}))?(?:\s*(?P<days>days?)\b)?",
    re.IGNORECASE,
)
#: A number written in words, e.g. "forty-five crore", "one hundred and forty six days".
_WORD_FIGURE = re.compile(
    rf"(?P<currency>{_CURRENCY}\s*)?\b(?P<words>(?:(?:{_WORD})[\s-]+)*(?:{_WORD}))\b(?:\s*(?P<days>days?)\b)?",
    re.IGNORECASE,
)
#: Clause references in the forms pleadings use. A four-digit year is not a clause number.
CLAUSE_RE = re.compile(
    r"\b(?:Sub-?\s?Clause|Clause|Cl\.?|Article|Art\.)\s*(?P<ref>[0-9]+(?:\.[0-9]+)*[A-Za-z]?(?:\([a-z0-9]+\))*)",
    re.IGNORECASE,
)
_YEAR = re.compile(r"^(19|20)\d\d$")
HEAD_RE = re.compile(r"\bH(\d{1,3})\b")

_RESERVE = re.compile(r"\breserv(?:e|es|ed|ing|ation)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:not|no|never|without|waive[sd]?|waiving|abandon(?:s|ed)?)\b|n't\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")


@dataclass(frozen=True)
class Figure:
    kind: str  # "money" | "days" | "number"
    value: Decimal

    def label(self) -> str:
        text = format(self.value.normalize(), "f")
        return {"money": f"amount {text}", "days": f"{text} days"}.get(self.kind, text)


def _decimal(text: str) -> Optional[Decimal]:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def words_to_number(words: str) -> Optional[Decimal]:
    """English number words, with Indian and Western scales, as a value. None if not a number."""
    tokens = [token for token in re.split(r"[\s-]+", words.lower()) if token and token != "and"]
    if not tokens or all(token in SCALES for token in tokens):
        return None
    total, current = Decimal(0), Decimal(0)
    for token in tokens:
        if token in _UNITS:
            current += _UNITS[token]
        elif token in _TENS:
            current += _TENS[token]
        elif token == "hundred":
            current = (current or Decimal(1)) * 100
        elif token in SCALES:
            total += (current or Decimal(1)) * SCALES[token]
            current = Decimal(0)
        else:
            return None
    return total + current


def figures_in(text: str) -> Set[Figure]:
    """Every figure in ``text``, typed, with scales applied. Clause numbers are not figures."""
    clean = CLAUSE_RE.sub(" ", text or "")
    found: Set[Figure] = set()
    spans: List[Tuple[int, int]] = []
    for match in _DIGIT_FIGURE.finditer(clean):
        value = _decimal(match.group("number"))
        if value is None:
            continue
        spans.append(match.span())
        scale = (match.group("scale") or "").lower()
        if scale:
            value *= SCALES[scale]
        if match.group("days"):
            kind = "days"
        elif match.group("currency") or scale:
            kind = "money"
        else:
            kind = "number"
        found.add(Figure(kind, value))
    for match in _WORD_FIGURE.finditer(clean):
        if any(start <= match.start() < end for start, end in spans):
            continue
        value = words_to_number(match.group("words"))
        if value is None:
            continue
        has_scale = any(token in SCALES for token in re.split(r"[\s-]+", match.group("words").lower()))
        if match.group("days"):
            kind = "days"
        elif match.group("currency") or has_scale:
            kind = "money"
        else:
            # "one of the events": a number word with no unit is prose, not a figure.
            continue
        found.add(Figure(kind, value))
    return found


def _comparable(figure: Figure, record: Set[Figure]) -> bool:
    """A money or day figure needs the same value and type; a plain number any figure of that value."""
    if figure in record:
        return True
    if figure.kind == "number":
        return any(item.value == figure.value for item in record)
    return False


def heads_named(text: str) -> Set[str]:
    return {f"H{number}" for number in HEAD_RE.findall(text or "")}


def head_figures(record: Iterable[str]) -> Dict[str, Set[Figure]]:
    """Figures the record states for each head, from record paragraphs that name exactly one head."""
    by_head: Dict[str, Set[Figure]] = {}
    for paragraph in record:
        heads = heads_named(paragraph)
        if len(heads) == 1:
            by_head.setdefault(next(iter(heads)), set()).update(figures_in(paragraph))
    return by_head


def invented_figures(proposed: Iterable[str], record: Iterable[str]) -> List[str]:
    """Figures in the proposed text the record does not state, for the head the paragraph is about."""
    record_list = list(record)
    everything: Set[Figure] = set()
    for paragraph in record_list:
        everything |= figures_in(paragraph)
    per_head = head_figures(record_list)
    problems: List[str] = []
    for paragraph in proposed:
        heads = heads_named(paragraph)
        scope = per_head.get(next(iter(heads))) if len(heads) == 1 else None
        for figure in sorted(figures_in(paragraph), key=lambda item: (item.kind, item.value)):
            if figure.kind in ("money", "days") and scope is not None:
                if not _comparable(figure, scope):
                    problems.append(f"{figure.label()} is not on the record for {next(iter(heads))}")
            elif not _comparable(figure, everything):
                problems.append(f"{figure.label()} is not on the record")
    return list(dict.fromkeys(problems))


def clauses_in(text: str) -> Set[str]:
    return {match.group("ref") for match in CLAUSE_RE.finditer(text or "") if not _YEAR.match(match.group("ref"))}


def reserves_rights(text: str) -> bool:
    """Whether some sentence reserves rights without negating it."""
    for sentence in _SENTENCE.split(text or ""):
        if _RESERVE.search(sentence) and not _NEGATION.search(sentence):
            return True
    return False


__all__ = [
    "CLAUSE_RE",
    "Figure",
    "clauses_in",
    "figures_in",
    "head_figures",
    "heads_named",
    "invented_figures",
    "reserves_rights",
    "words_to_number",
]
