"""Reference parsing helpers shared by document metadata and reference views."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..utils.date_parser import format_date_ddmmyyyy


DATE_MARKER_PATTERN = re.compile(
    r"(?is)(?P<prefix>.*?)\s*(?:[-\u2013\u2014]?\s*\b(?:dated|dtd|dt)\.?(?!\w))\s*[:\-]?\s*"
    r"(?P<date>\d{1,2}[.\/-]\d{1,2}[.\/-]\d{2,4})"
)

REFERENCE_LABEL_PATTERN = re.compile(
    r"(?i)\b(?:LOA|letter|ltr|reference|ref)\s*(?:no|number|#)\.?\s*[:\-]?"
)


def _clean_letter_number(prefix: str) -> str:
    """Remove prose labels while preserving the actual reference code."""

    candidate = (prefix or "").strip()
    matches = list(REFERENCE_LABEL_PATTERN.finditer(candidate))
    if matches:
        candidate = candidate[matches[-1].end() :]

    candidate = re.sub(r"\s+", " ", candidate).strip()
    candidate = candidate.strip(" \t\r\n:;,.")
    candidate = re.sub(r"^[\s:;,.()\[\]#\-\u2013\u2014]+", "", candidate).strip()
    candidate = re.sub(r"[\s:;,.#\-\u2013\u2014]+$", "", candidate).strip()
    return candidate


def parse_legacy_reference_text(value: str) -> Optional[Dict[str, str]]:
    """Parse legacy free-text references into structured letter/date fields."""

    raw = (value or "").strip()
    if not raw:
        return None

    match = DATE_MARKER_PATTERN.search(raw)
    if not match:
        return None

    letter_no = _clean_letter_number(match.group("prefix"))
    if not letter_no:
        return None

    raw_date = match.group("date").strip()
    formatted_date = format_date_ddmmyyyy(raw_date) or raw_date

    return {
        "letterNo": letter_no,
        "letter_no": letter_no,
        "date": formatted_date,
        "raw": raw,
    }


# --- Reading a References entry -----------------------------------------------
#
# An extracted reference list can only remove links when it is a complete
# answer. An entry that names no letter, or says it could not be read, makes
# the list incomplete; an entry that says "none" is no entry at all. Both
# extraction paths (the numbered report parser and the PydanticAI schema) use
# this one classifier so they cannot drift.

REFERENCE_NONE = "none"
REFERENCE_UNREADABLE = "unreadable"
REFERENCE_READABLE = "readable"

_EXPLICIT_NONE = re.compile(
    r"(null|'null'|\"null\"|not\s+found|none|nil|n/?a|not\s+applicable|-|--|–|—)",
    re.I,
)

#: The whole entry (punctuation removed) only says the reference was unread.
_UNREADABLE_WHOLE = re.compile(
    r"(?:illegible|unreadable|unclear|unknown|tbd|tba|"
    r"to\s+be\s+(?:confirmed|advised|determined)|not\s+(?:available|mentioned|specified)|"
    r"not\s+(?:legible|readable|clear|visible|clearly\s+(?:visible|legible|readable))|"
    r"(?:cannot|could\s+not|can\s*not)\s+be\s+(?:read|determined|identified)|"
    r"see\s+(?:attached|attachment|enclosure|enclosed|above|below))",
    re.I,
)

#: Uncertainty inside an otherwise code-shaped entry ("LTR-3??",
#: "LTR-[illegible] dated ..."): the number was not actually read.
_UNREADABLE_INSIDE = re.compile(r"\?\?|\?\s*$|\b(?:illegible|unreadable|unclear)\b", re.I)

#: A "?" inside a code-shaped token (one with a digit, "/" or "-"): "LTR-3?0",
#: "L?R-300", "KMRC/PKG?/12". Normalisation would turn the "?" into a
#: separator and the misread would look like a different letter, so the
#: token is unread. A "?" closing a prose word ("drawing?") is not.
_UNCERTAIN_CODE_TOKEN = re.compile(r"(?=\S*\?)(?=\S*[\d/\-])\S+")


def _entry_text(entry: Any) -> Tuple[str, bool]:
    """The letter-number text of an entry, and whether it is structured."""
    if isinstance(entry, Mapping):
        number = entry.get("letterNo") or entry.get("letter_no") or entry.get("code")
        if number not in (None, ""):
            return str(number), True
        # Structured, but no number: a date alone does not name a letter.
        raw = entry.get("raw") or entry.get("text")
        return (str(raw) if raw else ""), not bool(raw)
    return str(entry or ""), False


def classify_reference_entry(entry: Any) -> str:
    """``none``, ``unreadable`` or ``readable`` for one extracted entry."""
    text, structured_without_number = _entry_text(entry)
    stripped = text.strip().strip("[]()").strip()
    if not stripped:
        return REFERENCE_UNREADABLE if structured_without_number else REFERENCE_NONE
    if _EXPLICIT_NONE.fullmatch(stripped):
        return REFERENCE_NONE
    if _UNREADABLE_INSIDE.search(stripped) or _UNCERTAIN_CODE_TOKEN.search(stripped):
        return REFERENCE_UNREADABLE
    cleaned = re.sub(r"[^\w\s/]+", " ", stripped).strip()
    # Any letter or digit in any script; punctuation alone is not a number.
    if not re.search(r"[^\W_]", cleaned):
        return REFERENCE_UNREADABLE
    if _UNREADABLE_WHOLE.fullmatch(re.sub(r"\s+", " ", cleaned)):
        return REFERENCE_UNREADABLE
    return REFERENCE_READABLE


def readable_references(entries: Iterable[Any]) -> Tuple[List[Any], int]:
    """The readable entries, and how many entries could not be read.

    Entries that say "none" are dropped without counting as unread.
    """
    readable: List[Any] = []
    unreadable = 0
    for entry in entries or []:
        kind = classify_reference_entry(entry)
        if kind == REFERENCE_READABLE:
            readable.append(entry)
        elif kind == REFERENCE_UNREADABLE:
            unreadable += 1
    return readable, unreadable
