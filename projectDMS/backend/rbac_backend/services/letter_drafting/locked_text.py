"""Locked-paragraph support for the Redraft Agent (Phase 3).

Human-approved paragraphs are locked so the AI does not change them: they are
injected into the revision instruction as immutable blocks, and after
generation each locked paragraph is verified to survive verbatim (whitespace-
normalized, since models re-wrap lines). Violations are surfaced, never
silently repaired.
"""

from __future__ import annotations

import re
from typing import List


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def locked_instruction(locked_paragraphs: List[str]) -> str:
    """Instruction block appended to a revision request."""
    blocks = [p.strip() for p in locked_paragraphs if p and p.strip()]
    if not blocks:
        return ""
    numbered = "\n\n".join(
        f"[LOCKED {idx}]\n{block}" for idx, block in enumerate(blocks, start=1)
    )
    return (
        "LOCKED PARAGRAPHS — the following paragraphs are human-approved and MUST "
        "be reproduced VERBATIM in the revised letter, without any change to their "
        "wording, order of sentences, figures, or clause references:\n\n"
        f"{numbered}"
    )


def verify_locked_paragraphs(draft_text: str, locked_paragraphs: List[str]) -> List[str]:
    """Return the locked paragraphs that do NOT survive in the draft.

    Comparison is verbatim modulo whitespace (models legitimately re-wrap
    lines); any wording change counts as a violation.
    """
    haystack = _normalize(draft_text)
    missing: List[str] = []
    for paragraph in locked_paragraphs:
        needle = _normalize(paragraph)
        if needle and needle not in haystack:
            missing.append(paragraph)
    return missing


__all__ = ["locked_instruction", "verify_locked_paragraphs"]
