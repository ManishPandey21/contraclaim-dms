"""Models for deterministic AI input/output guardrail results."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

GuardrailVerdict = Literal["pass", "requires_human_review", "reject"]
GuardrailSeverity = Literal["info", "warning", "critical"]


class GuardrailFinding(BaseModel):
    code: str  # e.g. injection_pattern, unknown_citation, low_citation_coverage
    severity: GuardrailSeverity = "warning"
    message: str
    evidence: Optional[str] = None  # short excerpt that triggered the finding


class GuardrailReport(BaseModel):
    verdict: GuardrailVerdict = "pass"
    citation_coverage: Optional[float] = None  # fraction of sentences backed by ledger entries
    total_sentences: int = 0
    cited_sentences: int = 0
    findings: List[GuardrailFinding] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=datetime.utcnow)
