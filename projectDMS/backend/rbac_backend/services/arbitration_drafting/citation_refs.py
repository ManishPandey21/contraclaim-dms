"""One parser for every citation a pleading can make (plan D-17, 4B-35, C-11).

Before this module the validator recognised only ``[S3: ...]``, the governed
workflow also accepted ``[S3]`` and ``[SRC-4]``, and exhibit references
("Ex. C-12", "Exhibit R/3", "CW-1/4", "Annexure A-7") were not recognised at
all. A draft citing a missing exhibit read as "cites no sources" and was not
blocked on it. The validator, the workflow gate and the screens now read
citations through this module only.

Two kinds of reference:

- **source keys** into the draft's source ledger: ``[S3]``, ``[S3: label]``,
  ``[SRC-4]``, ``[SRC-4: label]``;
- **exhibit references** into the case's exhibit list, normalised to the
  exhibit id the case workspace assigns (``C-12``, ``R-3``, ``A-7``), or kept
  as written for witness exhibits (``CW-1/4``).

**Verified is an allow-list.** A cited source is verified only if its status is
one of ``CITATION_VERIFIED_STATUSES``. Anything else, including a missing,
unknown, ``rejected``, ``stale``, ``ai_suggested`` or ``selected`` status, is
unverified. (``selected`` is a user's choice of a document, not a review of it.
Both gates already treated it as unverified; only ledger membership counts it.)
"""

from __future__ import annotations

import re
from typing import Any, Dict

#: ``[S3]``, ``[S3: label]``, ``[SRC-4]``, ``[SRC-4: label]``.
SOURCE_REF_RE = re.compile(r"\[((?:S\d+)|(?:SRC-\d+))(?::[^\]]*)?\]")
#: The same reference repeated with only whitespace between: one citation, written twice.
ADJACENT_DUPLICATE_SOURCE_REF_RE = re.compile(r"(\[(?:(?:S\d+)|(?:SRC-\d+))(?::[^\]]*)?\])(?:\s+\1)+")

_SEP = r"\s*[-/]\s*"
#: "Ex. C-12", "Exh R/3", "Exhibit C 12", "Annexure A-7", "Annex. A/7", "Ann. A-7", "Ex. CW-1/4".
_LABELLED_EXHIBIT_RE = re.compile(
    r"\b(?:Ex(?:h(?:ibit)?)?|Annex(?:ure)?|Ann)\.?\s*"
    r"(?P<prefix>[A-Z]{1,3})(?:" + _SEP + r"|\s+)(?P<number>\d{1,4})(?:\s*/\s*(?P<sub>\d{1,4}))?\b",
    re.IGNORECASE,
)
#: Witness exhibits written bare: "CW-1/4", "RW1/12".
_WITNESS_EXHIBIT_RE = re.compile(r"\b(?P<prefix>CW|RW)\s*-?\s*(?P<number>\d{1,3})\s*/\s*(?P<sub>\d{1,4})\b")
#: Bare party exhibits: "C-12", "R-3", "C/12". Upper case only, so "c-12" in prose is not read as one.
_BARE_EXHIBIT_RE = re.compile(r"(?<![A-Za-z0-9/.-])(?P<prefix>[CR])\s*[-/]\s*(?P<number>\d{1,4})(?![\d/-])")

#: Ledger statuses that make a cited source admissible as it stands.
CITATION_VERIFIED_STATUSES = frozenset(
    {"approved", "edited_verified", "ready", "supported", "user_verified", "valid", "verified"}
)


def normalise_exhibit(prefix: str, number: str, sub: str | None = None) -> str:
    head = f"{prefix.upper()}-{int(number)}"
    return f"{head}/{int(sub)}" if sub else head


def source_refs(markdown: str) -> Dict[str, int]:
    """Each source key cited, with how many times."""
    uses: Dict[str, int] = {}
    for match in SOURCE_REF_RE.finditer(markdown or ""):
        uses[match.group(1)] = uses.get(match.group(1), 0) + 1
    return uses


def exhibit_refs(markdown: str) -> Dict[str, int]:
    """Each exhibit referred to, normalised, with how many times.

    Source keys are removed first, so a label inside ``[S3: Ex. C-12]`` counts
    as the source citation it is, not as a second, exhibit citation.
    """
    text = SOURCE_REF_RE.sub(" ", markdown or "")
    uses: Dict[str, int] = {}
    spans: list[tuple[int, int]] = []

    def _add(match: "re.Match[str]") -> None:
        if any(start <= match.start() < end for start, end in spans):
            return
        spans.append(match.span())
        key = normalise_exhibit(match.group("prefix"), match.group("number"), match.groupdict().get("sub"))
        uses[key] = uses.get(key, 0) + 1

    for pattern in (_LABELLED_EXHIBIT_RE, _WITNESS_EXHIBIT_RE, _BARE_EXHIBIT_RE):
        for match in pattern.finditer(text):
            _add(match)
    return uses


def has_citation(text: str) -> bool:
    """Whether a sentence cites anything: a source key or an exhibit."""
    return bool(SOURCE_REF_RE.search(text or "")) or bool(exhibit_refs(text))


def is_verified_status(value: Any) -> bool:
    return str(value or "").strip().lower() in CITATION_VERIFIED_STATUSES


__all__ = [
    "ADJACENT_DUPLICATE_SOURCE_REF_RE",
    "CITATION_VERIFIED_STATUSES",
    "SOURCE_REF_RE",
    "exhibit_refs",
    "has_citation",
    "is_verified_status",
    "normalise_exhibit",
    "source_refs",
]
