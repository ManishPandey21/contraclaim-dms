"""Legal / Contractual Risk Review Agent (multi-agent workflow Phase 3).

Deterministic rule pack that flags language in a draft which may create
unintended admissions, waivers, entitlements, or contradictions with the
position taken in previous correspondence.

Hard rules honoured:
- Flags only — this agent NEVER blocks a run and never rewrites text.
- No legal conclusions: contradiction detection is a "possible" signal with
  both excerpts attached for the human reviewer.
- The human decides the contractual position; the system records evidence.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional, Pattern, Tuple

from ...models.letter_drafting import LegalRiskFlag, LegalRiskReport

_ADMISSION_PATTERNS: List[Tuple[Pattern[str], str]] = [
    (
        re.compile(
            r"\bwe\s+(?:hereby\s+)?(?:accept|admit|acknowledge)\s+"
            r"(?:full\s+|our\s+|the\s+)?(?:liability|responsibility|fault|default|breach)",
            re.IGNORECASE,
        ),
        "Accepts liability/responsibility on the sender's behalf.",
    ),
    (
        re.compile(r"\bwe\s+are\s+(?:fully\s+|solely\s+)?(?:liable|responsible|at\s+fault)\b", re.IGNORECASE),
        "States the sender is liable/responsible.",
    ),
    (
        re.compile(
            r"\b(?:the\s+)?delay\s+(?:was|is|has\s+been)\s+caused\s+by\s+(?:us|our)\b",
            re.IGNORECASE,
        ),
        "Attributes the delay to the sender.",
    ),
    (
        re.compile(r"\bwe\s+agree\s+to\s+bear\s+(?:all\s+)?(?:the\s+)?costs?\b", re.IGNORECASE),
        "Commits the sender to bear costs.",
    ),
]

_WAIVER_PATTERNS: List[Tuple[Pattern[str], str]] = [
    (
        re.compile(r"\bwe\s+(?:hereby\s+)?waive\b", re.IGNORECASE),
        "Waives a right or claim.",
    ),
    (
        re.compile(
            r"\bwe\s+(?:forgo|forego|relinquish|abandon)\s+(?:our\s+)?(?:rights?|claims?|entitlements?)\b",
            re.IGNORECASE,
        ),
        "Gives up rights, claims or entitlements.",
    ),
    (
        re.compile(
            r"\bwe\s+(?:shall|will)\s+not\s+(?:claim|pursue|seek|raise)\b",
            re.IGNORECASE,
        ),
        "Commits not to pursue a claim or remedy.",
    ),
]

_ENTITLEMENT_PATTERNS: List[Tuple[Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:you|the\s+(?:contractor|employer|engineer|client))\s+(?:are|is)\s+entitled\s+to\b",
            re.IGNORECASE,
        ),
        "Concedes an entitlement to the other party.",
    ),
    (
        re.compile(
            r"\bentitlement\s+(?:is|stands|has\s+been)\s+(?:established|confirmed|granted|accepted)\b",
            re.IGNORECASE,
        ),
        "Declares an entitlement established without cited source.",
    ),
    (
        re.compile(r"\bwe\s+(?:grant|concede)\b", re.IGNORECASE),
        "Grants or concedes a position.",
    ),
]

_ACCEPT_STANCE = re.compile(
    r"\bwe\s+(?:accept|agree\s+to|agree\s+with|grant|concede|approve)\b", re.IGNORECASE
)
_REJECT_STANCE = re.compile(
    r"\bwe\s+(?:reject|dispute|deny|refute|decline|do\s+not\s+(?:accept|agree))\b|"
    r"\bis\s+(?:rejected|denied|disputed|refuted)\b",
    re.IGNORECASE,
)

_EXCERPT_WIDTH = 160


def _excerpt(text: str, start: int, end: int) -> str:
    lo = max(0, start - 40)
    hi = min(len(text), end + 80)
    snippet = " ".join(text[lo:hi].split())
    return snippet[:_EXCERPT_WIDTH]


class LegalRiskReviewer:
    """Rule-based legal/contractual risk review over a draft letter."""

    def review(
        self,
        draft_text: str,
        previous_positions: Optional[List[str]] = None,
    ) -> LegalRiskReport:
        flags: List[LegalRiskFlag] = []
        text = draft_text or ""
        counter = 0

        def _scan(patterns: List[Tuple[Pattern[str], str]], category: str, severity: str) -> None:
            nonlocal counter
            for pattern, explanation in patterns:
                for match in pattern.finditer(text):
                    counter += 1
                    flags.append(
                        LegalRiskFlag(
                            flag_id=f"{category}-{counter}",
                            category=category,  # type: ignore[arg-type]
                            severity=severity,  # type: ignore[arg-type]
                            excerpt=_excerpt(text, match.start(), match.end()),
                            explanation=explanation
                            + " Confirm this is the intended contractual position.",
                        )
                    )

        _scan(_ADMISSION_PATTERNS, "admission", "high")
        _scan(_WAIVER_PATTERNS, "waiver", "high")
        _scan(_ENTITLEMENT_PATTERNS, "entitlement", "high")

        contradiction = self._contradiction_flag(text, previous_positions or [], counter + 1)
        if contradiction:
            flags.append(contradiction)

        return LegalRiskReport(
            flags=flags,
            human_review_required=bool(flags),
            scanned_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _contradiction_flag(
        draft_text: str, previous_positions: List[str], flag_no: int
    ) -> Optional[LegalRiskFlag]:
        """Possible stance reversal vs the previous position (caution only)."""
        prev_text = " ".join(p for p in previous_positions if p)
        if not prev_text or not draft_text:
            return None
        draft_accepts = bool(_ACCEPT_STANCE.search(draft_text))
        draft_rejects = bool(_REJECT_STANCE.search(draft_text))
        prev_accepts = bool(_ACCEPT_STANCE.search(prev_text))
        prev_rejects = bool(_REJECT_STANCE.search(prev_text))

        reversal = (prev_rejects and draft_accepts and not draft_rejects) or (
            prev_accepts and draft_rejects and not draft_accepts
        )
        if not reversal:
            return None
        prev_match = _REJECT_STANCE.search(prev_text) or _ACCEPT_STANCE.search(prev_text)
        draft_match = _ACCEPT_STANCE.search(draft_text) or _REJECT_STANCE.search(draft_text)
        return LegalRiskFlag(
            flag_id=f"contradiction-{flag_no}",
            category="contradiction",
            severity="caution",
            excerpt=(
                f"Previous position: \"{_excerpt(prev_text, prev_match.start(), prev_match.end())}\" | "
                f"Draft: \"{_excerpt(draft_text, draft_match.start(), draft_match.end())}\""
            ),
            explanation=(
                "The draft's stance appears to reverse the position taken in previous "
                "correspondence. Verify this change is intended before issue."
            ),
        )


__all__ = ["LegalRiskReviewer"]
