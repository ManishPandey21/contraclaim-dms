"""Detect (a)/(b) and (i)/(ii) sub-items inside a clause and build their
parent-child numbering (req 10/11).

Legal drafting convention handled here: a clause's lettered items (a),(b),(c)…
are first-level sub-items; roman items (i),(ii),(iii)… nest under the current
lettered item. The single tokens i/v/x are ambiguous (letter vs roman) and are
resolved by whether they continue the running letter sequence (…h,(i) -> letter;
otherwise roman).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

# Marker at the start of a line: "(a)", "(iv)", "(ii)" followed by whitespace/EOL.
_MARKER_RE = re.compile(r"^[ \t]*\(([a-zA-Z]{1,5})\)(?=\s|$)", re.MULTILINE)

# Unambiguously roman tokens (single i/v/x are resolved contextually below).
_MULTI_ROMAN = {
    "ii", "iii", "iv", "vi", "vii", "viii", "ix",
    "xi", "xii", "xiii", "xiv", "xv",
}
_AMBIGUOUS = {"i", "v", "x"}


@dataclass
class SubItem:
    marker: str          # "a", "i", "ii"
    clause_no: str       # "8.4(a)", "8.4(a)(i)"
    parent_no: str       # "8.4" or "8.4(a)"
    kind: str            # "letter" | "roman"
    text: str
    char_start: int      # offset within the clause text
    char_end: int


def _classify(token: str, last_letter: Optional[str]) -> str:
    token = token.lower()
    if token in _MULTI_ROMAN:
        return "roman"
    if token in _AMBIGUOUS:
        # Continues the running letter sequence? (…(h),(i) -> letter)
        if last_letter is not None and len(last_letter) == 1 and ord(token) == ord(last_letter) + 1:
            return "letter"
        return "roman"
    if len(token) == 1 and token.isalpha():
        return "letter"
    return "letter"


def detect_subitems(text: str, parent_no: str, *, min_markers: int = 2) -> List[SubItem]:
    """Return the ordered sub-items of a clause.

    At least ``min_markers`` markers are required so a stray "(a)" inside prose
    is not mistaken for a sub-item list.
    """
    if not text or not parent_no:
        return []
    markers = [(m.start(), m.group(1)) for m in _MARKER_RE.finditer(text)]
    if len(markers) < min_markers:
        return []

    classified = []
    last_letter: Optional[str] = None
    current_letter_no: Optional[str] = None
    for pos, raw in markers:
        token = raw.lower()
        kind = _classify(token, last_letter)
        if kind == "letter":
            last_letter = token
            clause_no = f"{parent_no}({token})"
            current_letter_no = clause_no
            parent = parent_no
        else:
            base = current_letter_no or parent_no
            clause_no = f"{base}({token})"
            parent = base
        classified.append((pos, token, kind, clause_no, parent))

    items: List[SubItem] = []
    for idx, (pos, token, kind, clause_no, parent) in enumerate(classified):
        end = classified[idx + 1][0] if idx + 1 < len(classified) else len(text)
        items.append(
            SubItem(
                marker=token,
                clause_no=clause_no,
                parent_no=parent,
                kind=kind,
                text=text[pos:end].strip(),
                char_start=pos,
                char_end=end,
            )
        )
    return items


__all__ = ["SubItem", "detect_subitems"]
