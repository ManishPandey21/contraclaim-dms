"""Types for the deterministic extraction quality gate.

The four verdicts exist because three are not enough. `NOT_CHECKABLE` is the
one that stops the bleeding: a structure whose roles could not be established
has produced no evidence either way, and treating that as failure is exactly
what generated 12 paid escalations on a correct document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class Verdict(str, Enum):
    #: A check ran and agreed.
    PASS = "pass"
    #: Nothing could be verified - no roles, no operands, no structure. This is
    #: not a failure and must never escalate.
    NOT_CHECKABLE = "not_checkable"
    #: A check ran and disagreed.
    FAIL = "fail"
    #: Checks ran and contradicted each other.
    INDETERMINATE = "indeterminate"


@dataclass
class CheckResult:
    name: str
    verdict: Verdict
    detail: str = ""
    candidate_value: Optional[Decimal] = None
    #: Which pieces of the document this check leaned on. Used to decide
    #: whether two checks are genuinely independent: if they share an operand
    #: beyond the candidate itself, they are one check wearing two hats.
    evidence_refs: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "verdict": self.verdict.value,
            "detail": self.detail,
            "candidate_value": (
                str(self.candidate_value) if self.candidate_value is not None else None
            ),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass
class NumericRepair:
    before: str
    after: str
    reason: str
    method: str
    confidence: float
    confirming_checks: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
            "method": self.method,
            "confidence": self.confidence,
            "confirming_checks": list(self.confirming_checks),
        }


@dataclass
class QualityVerdict:
    verdict: Verdict
    checks: List[CheckResult] = field(default_factory=list)
    repairs: List[NumericRepair] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    @property
    def escalates(self) -> bool:
        """Only FAIL and INDETERMINATE reach a paid model."""
        return self.verdict in {Verdict.FAIL, Verdict.INDETERMINATE}

    def to_record(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "checks": [check.to_record() for check in self.checks],
            "repairs": [repair.to_record() for repair in self.repairs],
            "reasons": list(self.reasons),
        }
