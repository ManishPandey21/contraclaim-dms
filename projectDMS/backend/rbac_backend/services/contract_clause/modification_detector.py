"""Detect SCC / addendum / corrigendum modifications to base (GCC) clauses.

Per the spec this only creates *modification links* and flags
``human_review_required`` — it makes no legal conclusions (req 20).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

# A clause reference such as "Sub-Clause 8.4" or "Clause 13.3".
_CLAUSE_REF_RE = re.compile(
    r"(?:sub[-\s]?clause|clause)\s+(\d+(?:\.\d+)*)", re.IGNORECASE
)

# Keyword -> modification type. Checked in this priority order so that
# "deleted and replaced by" resolves to the net effect ("replace").
_MODIFICATION_KEYWORDS = [
    ("replace", ("replaced", "replace", "substituted", "substitute", "shall replace")),
    ("delete", ("deleted", "delete", "deletion", "omitted")),
    ("add", ("added", "add the following", "the following at the end", "inserted", "insert")),
    ("amend", ("amended", "amend", "amendment", "modified", "modify", "modification", "notwithstanding")),
]

ModificationType = str  # "replace" | "amend" | "add" | "delete"


@dataclass
class ModificationSignal:
    base_clause_no: str
    modification_type: ModificationType
    matched_phrase: str
    keyword: str
    # Never auto-decide the legal effect; a human must confirm.
    human_review_required: bool = True


def _classify(window: str) -> Optional[tuple[str, str]]:
    lowered = window.lower()
    for mod_type, keywords in _MODIFICATION_KEYWORDS:
        for keyword in keywords:
            if keyword in lowered:
                return mod_type, keyword
    return None


class ModificationDetector:
    """Phrase-based detector for clause modifications (req 19)."""

    @staticmethod
    def detect(text: str) -> List[ModificationSignal]:
        if not text:
            return []
        signals: List[ModificationSignal] = []
        seen: set[tuple[str, str]] = set()
        for match in _CLAUSE_REF_RE.finditer(text):
            base = match.group(1)
            window = text[max(0, match.start() - 90) : match.end() + 140]
            classified = _classify(window)
            if not classified:
                continue
            mod_type, keyword = classified
            dedupe_key = (base, mod_type)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            signals.append(
                ModificationSignal(
                    base_clause_no=base,
                    modification_type=mod_type,
                    matched_phrase=text[max(0, match.start() - 40) : match.end() + 80].strip(),
                    keyword=keyword,
                )
            )
        return signals

    @staticmethod
    def build_modification_link(
        *,
        signal: ModificationSignal,
        applicable_clause_id: str,
        modifier_document_type: Optional[str],
        base_document_type: str = "GCC",
    ) -> dict:
        """Structured modification link (spec section 10)."""
        return {
            "base_clause_no": signal.base_clause_no,
            "base_document_type": base_document_type,
            "modifier_document_type": modifier_document_type,
            "modification_type": signal.modification_type,
            "matched_phrase": signal.matched_phrase,
            "applicable_clause_id": applicable_clause_id,
            "human_review_required": True,
        }


__all__ = ["ModificationDetector", "ModificationSignal"]
