"""Exact-preservation primitives for scoped letter revisions.

The model is never asked to reproduce frozen or otherwise unselected content.
Only selected section text is sent for editing, and the final letter is rebuilt
from the source draft by replacing those selected spans deterministically.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence

from ...models.letter_drafting import FrozenDraftSection


_SECTION_SEPARATOR = re.compile(r"((?:\r?\n[ \t]*){2,})")
_PROTECTED_ANCHOR_PATTERNS = (
    re.compile(
        r"\b(?:clause|section|sub-clause|subclause|article)\s+[0-9A-Za-z][0-9A-Za-z.\-()]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:[$€£₹]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|INR|AED|SAR|QAR)\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Z0-9]{2,}(?:[/_-][A-Z0-9]{2,})+\b"),
)


@dataclass(frozen=True)
class DraftSection:
    index: int
    token_index: int
    content: str


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def split_draft_sections(draft_text: str) -> tuple[List[str], List[DraftSection]]:
    """Split a draft into editable blocks while retaining every separator.

    The returned token list can be joined to reproduce ``draft_text`` exactly.
    Section indices address only non-empty content tokens; blank-line separators
    are never model-editable.
    """

    tokens = _SECTION_SEPARATOR.split(draft_text or "")
    sections: List[DraftSection] = []
    for token_index in range(0, len(tokens), 2):
        content = tokens[token_index]
        if not content or not content.strip():
            continue
        sections.append(
            DraftSection(
                index=len(sections),
                token_index=token_index,
                content=content,
            )
        )
    return tokens, sections


def section_map(draft_text: str) -> Dict[int, DraftSection]:
    return {section.index: section for section in split_draft_sections(draft_text)[1]}


def freeze_sections(
    draft_text: str,
    section_indices: Sequence[int],
    *,
    frozen_by: str | None = None,
) -> List[FrozenDraftSection]:
    available = section_map(draft_text)
    requested = sorted(set(int(index) for index in section_indices))
    unknown = [index for index in requested if index not in available]
    if unknown:
        raise ValueError(f"Unknown draft section indices: {unknown}")
    return [
        FrozenDraftSection(
            section_index=index,
            content=available[index].content,
            content_hash=content_hash(available[index].content),
            frozen_by=frozen_by,
        )
        for index in requested
    ]


def verify_frozen_sections(
    draft_text: str,
    frozen: Iterable[FrozenDraftSection],
) -> List[int]:
    available = section_map(draft_text)
    violations: List[int] = []
    for item in frozen:
        section = available.get(item.section_index)
        if (
            section is None
            or section.content != item.content
            or content_hash(section.content) != item.content_hash
        ):
            violations.append(item.section_index)
    return violations


def sanitize_section_replacement(text: str) -> str:
    """Keep one stable top-level section per model result.

    Single newlines (including list formatting) are preserved. Blank-line
    separators are collapsed so later section indices cannot drift.
    """

    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:text|markdown)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return re.sub(r"(?:\r?\n[ \t]*){2,}", "\n", cleaned).strip()


def protected_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    for pattern in _PROTECTED_ANCHOR_PATTERNS:
        anchors.update(match.group(0).casefold() for match in pattern.finditer(text or ""))
    return anchors


def protected_anchor_changes(source_text: str, revised_text: str) -> Dict[str, List[str]]:
    source = protected_anchors(source_text)
    revised = protected_anchors(revised_text)
    return {
        "removed": sorted(source - revised),
        "introduced": sorted(revised - source),
    }


def apply_section_edits(
    draft_text: str,
    replacements: Dict[int, str],
) -> str:
    """Replace only selected content tokens and preserve everything else."""

    tokens, sections = split_draft_sections(draft_text)
    available = {section.index: section for section in sections}
    unknown = sorted(set(replacements) - set(available))
    if unknown:
        raise ValueError(f"Unknown draft section indices: {unknown}")
    for index, replacement in replacements.items():
        cleaned = sanitize_section_replacement(replacement)
        if not cleaned:
            raise ValueError(f"Edited draft section {index} is empty")
        tokens[available[index].token_index] = cleaned
    return "".join(tokens)


def preserved_sections(
    draft_text: str,
    editable_section_indices: Sequence[int],
) -> List[FrozenDraftSection]:
    editable = {int(index) for index in editable_section_indices}
    return [
        FrozenDraftSection(
            section_index=section.index,
            content=section.content,
            content_hash=content_hash(section.content),
        )
        for section in split_draft_sections(draft_text)[1]
        if section.index not in editable
    ]


__all__ = [
    "DraftSection",
    "apply_section_edits",
    "content_hash",
    "freeze_sections",
    "preserved_sections",
    "protected_anchor_changes",
    "protected_anchors",
    "sanitize_section_replacement",
    "section_map",
    "split_draft_sections",
    "verify_frozen_sections",
]
