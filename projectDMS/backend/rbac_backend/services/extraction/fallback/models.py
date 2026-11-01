"""Types for the extraction fallback ladder.

Two ideas carry most of the weight here.

`Corroboration` is how the anti-fabrication rule is enforced in data rather
than in a prompt: every contractual value a model emits is classed either
`CORROBORATED` (it matches a token already in the evidence, or an independent
arithmetic identity confirms it) or `UNCORROBORATED` (read from the image
alone, with nothing to check it against). Uncorroborated values persist as
unverified and never become authoritative.

`FallbackOutcome` keeps "we could not fix this" distinguishable from "we fixed
it". `HUMAN_REVIEW_REQUIRED` is a real outcome, not an error path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class Tier(int, Enum):
    TIER_1 = 1
    TIER_2 = 2


class Corroboration(str, Enum):
    CORROBORATED = "corroborated"
    UNCORROBORATED = "uncorroborated"


class FallbackOutcome(str, Enum):
    #: The ladder did not run, or produced nothing to adopt.
    UNCHANGED = "unchanged"
    #: A reconstruction passed the same deterministic checks.
    RESOLVED = "resolved"
    #: This tier failed; a higher tier may still try.
    ESCALATED = "escalated"
    #: Terminal. Automated recovery is exhausted or impossible.
    HUMAN_REVIEW_REQUIRED = "human_review_required"


@dataclass(frozen=True)
class ReconstructedValue:
    value_id: str
    kind: str
    raw_value: str
    location: str = ""


@dataclass(frozen=True)
class ValueCorroboration:
    value_id: str
    raw_value: str
    status: Corroboration
    check_name: str = ""
    evidence_refs: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "value_id": self.value_id,
            "raw_value": self.raw_value,
            "status": self.status.value,
            "check_name": self.check_name,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass
class Evidence:
    """The least material that can answer the question.

    Deliberately has no document-wide field: the model sees one page or one
    region, never its neighbours. That is a cost control, a data-minimisation
    control, and a precision control at once.
    """

    page_number: int
    image_png: bytes
    is_region: bool
    bbox: Optional[Tuple[float, float, float, float]]
    native_text: str
    ocr_text: str
    tables: List[List[List[str]]]
    trigger_reasons: List[str]
    dpi: int


@dataclass
class Reconstruction:
    text: str
    tables: List[List[List[str]]] = field(default_factory=list)
    factual_values: List[ReconstructedValue] = field(default_factory=list)
    confidence: float = 0.0
    model: str = ""
    model_version: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def tokens(self) -> int:
        return int(self.input_tokens) + int(self.output_tokens)


@dataclass
class ResolvedPage:
    page: Any
    outcome: FallbackOutcome
    tier_used: Optional[Tier] = None
    reconstruction: Optional[Reconstruction] = None
    post_verdict: Any = None
    corroborations: List[ValueCorroboration] = field(default_factory=list)


@dataclass
class Intervention:
    document_id: str
    page_number: int
    region_bbox: Optional[Tuple[float, float, float, float]]
    trigger: str
    tier: Tier
    model: str
    model_version: str
    prompt_version: str
    evidence_sent: str
    dpi: int
    text_sources: Sequence[str]
    confidence: float
    corrections: Sequence[Dict[str, Any]]
    post_check: str
    outcome: FallbackOutcome
    tokens: int
    cost_usd: float
    latency_ms: int

    def to_record(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "page_number": self.page_number,
            "region_bbox": list(self.region_bbox) if self.region_bbox else None,
            "trigger": self.trigger,
            "tier": int(self.tier),
            "model": self.model,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "evidence_sent": self.evidence_sent,
            "dpi": self.dpi,
            "text_sources": list(self.text_sources),
            "confidence": self.confidence,
            "corrections": [dict(item) for item in self.corrections],
            "post_check": self.post_check,
            "outcome": self.outcome.value,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
        }
