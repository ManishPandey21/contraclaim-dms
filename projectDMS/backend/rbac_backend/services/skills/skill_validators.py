"""Deterministic checks derived from the skills' own rules.

The packs state rules a prompt can only ask for. These validators enforce
them and report every finding against an assertion id, so the Bench can show
the problem on the card it belongs to:

- letters skill: "Never leave an assertion unanswered", "Always cite the exact
  sub-clause", "Reservations: explicitly reserve all rights and remedies".
- variations skill: "Always state which limb of the hierarchy is being used
  and why".

They add to, and never replace, the existing unsupported-clause validator.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel

from ...models.letter_bench import PositionBoard, SkillStep
from ...models.letter_drafting import ValidationFinding

_ANY_CLAUSE_NUMBER = re.compile(r"\b\d+[A-Za-z]?(?:\.\d+[A-Za-z]?)+(?:\([a-z0-9]{1,3}\))*|\bclause\s+\d+", re.IGNORECASE)
_SUB_CLAUSE_NUMBER = re.compile(r"\b\d+[A-Za-z]?(?:\.\d+[A-Za-z]?)+")
_RESERVATION = re.compile(r"\breserv(?:e|es|ed|ing|ation)\b", re.IGNORECASE)
VARIATION_SCHEMA_KEY = "variation_evaluation.v1"
_VALUED_CLASSES = {"instructed_variation", "value_engineering", "constructive_variation"}


class SkillFinding(BaseModel):
    level: Literal["warning", "error"]
    code: str
    message: str
    assertion_id: Optional[str] = None
    validator: str

    def as_validation_finding(self) -> ValidationFinding:
        return ValidationFinding(
            level=self.level,
            code=self.code,
            message=self.message,
            evidence=f"assertion:{self.assertion_id}" if self.assertion_id else None,
        )


def every_assertion_answered(board: PositionBoard) -> List[SkillFinding]:
    answered: Dict[str, str] = {
        paragraph.assertion_id: paragraph.text
        for paragraph in board.reply_paragraphs
        if paragraph.assertion_id
    }
    findings: List[SkillFinding] = []
    for assertion in board.assertions:
        if not (answered.get(assertion.assertion_id) or "").strip():
            findings.append(
                SkillFinding(
                    level="error",
                    code="assertion_unanswered",
                    message=f"{assertion.assertion_id} has no reply. Every assertion must be answered.",
                    assertion_id=assertion.assertion_id,
                    validator="every_assertion_answered",
                )
            )
    return findings


def exact_sub_clause_cited(board: PositionBoard) -> List[SkillFinding]:
    by_id = {assertion.assertion_id: assertion for assertion in board.assertions}
    findings: List[SkillFinding] = []
    for paragraph in board.reply_paragraphs:
        assertion = by_id.get(paragraph.assertion_id or "")
        if assertion is None or not assertion.clause_refs or not paragraph.text.strip():
            continue
        if not _ANY_CLAUSE_NUMBER.search(paragraph.text):
            findings.append(
                SkillFinding(
                    level="warning",
                    code="sub_clause_not_cited",
                    message=(
                        f"The reply to {assertion.assertion_id} cites no clause although the "
                        f"sender relies on {', '.join(assertion.clause_refs)}."
                    ),
                    assertion_id=assertion.assertion_id,
                    validator="exact_sub_clause_cited",
                )
            )
        elif any("." in ref for ref in assertion.clause_refs) and not _SUB_CLAUSE_NUMBER.search(
            paragraph.text
        ):
            findings.append(
                SkillFinding(
                    level="warning",
                    code="sub_clause_not_exact",
                    message=(
                        f"The reply to {assertion.assertion_id} cites a clause without its "
                        "exact sub-clause number."
                    ),
                    assertion_id=assertion.assertion_id,
                    validator="exact_sub_clause_cited",
                )
            )
    return findings


def reservation_of_rights(board: PositionBoard) -> List[SkillFinding]:
    if not board.reply_paragraphs:
        return []
    if any(_RESERVATION.search(paragraph.text or "") for paragraph in board.reply_paragraphs):
        return []
    return [
        SkillFinding(
            level="error",
            code="reservation_of_rights_missing",
            message="The reply does not reserve rights and remedies.",
            validator="reservation_of_rights",
        )
    ]


def valuation_limb_stated(steps: Iterable[SkillStep]) -> List[SkillFinding]:
    findings: List[SkillFinding] = []
    for step in steps:
        if step.output_schema_key != VARIATION_SCHEMA_KEY or step.status != "completed" or step.stale:
            continue
        output = step.output or {}
        if output.get("classification") not in _VALUED_CLASSES:
            continue
        limb = output.get("valuation_limb")
        justified = bool(str(output.get("valuation_justification") or "").strip())
        if limb in (None, "", "not_determined") or not justified:
            targets: List[Optional[str]] = list(step.target_assertion_ids) or [None]
            for assertion_id in targets:
                findings.append(
                    SkillFinding(
                        level="error",
                        code="valuation_limb_not_stated",
                        message=(
                            "The variation evaluation does not state which limb of the "
                            "valuation hierarchy is used and why."
                        ),
                        assertion_id=assertion_id,
                        validator="valuation_limb_stated",
                    )
                )
    return findings


def run_skill_validators(
    board: PositionBoard,
    steps: Iterable[SkillStep],
    enabled: Optional[Iterable[str]] = None,
) -> List[SkillFinding]:
    """Run the validators named by the chain's skill manifests (all when None)."""
    wanted = set(enabled) if enabled is not None else None
    steps = list(steps)
    checks = {
        "every_assertion_answered": lambda: every_assertion_answered(board),
        "exact_sub_clause_cited": lambda: exact_sub_clause_cited(board),
        "reservation_of_rights": lambda: reservation_of_rights(board),
        "valuation_limb_stated": lambda: valuation_limb_stated(steps),
    }
    findings: List[SkillFinding] = []
    for name, check in checks.items():
        if wanted is None or name in wanted:
            findings.extend(check())
    return findings


__all__ = [
    "SkillFinding",
    "every_assertion_answered",
    "exact_sub_clause_cited",
    "reservation_of_rights",
    "run_skill_validators",
    "valuation_limb_stated",
]
