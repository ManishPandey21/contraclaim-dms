"""The bounded fallback ladder.

    Tier 0  deterministic extraction (native text layer / OCR)
              | gate: FAIL or INDETERMINATE
    Tier 1  configured vision-capable model, page or region
              | re-gate: still unacceptable
    Tier 2  configured higher-capability model
              | still unresolved, or unavailable
            HUMAN_REVIEW_REQUIRED   (terminal - never `completed`)

Three rules do the real work:

1. **Only FAIL and INDETERMINATE escalate.** NOT_CHECKABLE means nothing was
   verified, which is not the same as something being wrong, and paying a model
   to look at it is the 12-false-positives mistake in a new costume.

2. **Confidence may route, never accept.** A reconstruction is adopted only if
   it survives the *same* deterministic gate that rejected the original.

3. **Content must be anchored in the evidence.** Every non-numeric token the
   model emits has to already exist in the native/OCR/table text - it may be
   reordered and rejoined, but not invented - and every contractual value it
   emits must be corroborated. This is what stops a gate PASS from blessing a
   fluent novel narrative.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from ..models import ExtractedPage, PageSource
from ..quality.models import QualityVerdict, Verdict
from ..rasterizer import PageRasterizer, RasterizationError
from .evidence import assemble_evidence
from .ledger import InterventionLedger
from .models import (
    Corroboration,
    Evidence,
    FallbackOutcome,
    Intervention,
    ReconstructedValue,
    Reconstruction,
    ResolvedPage,
    Tier,
    ValueCorroboration,
)

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[A-Za-z]+")
_NUMERIC_TOKEN = re.compile(r"^-?[\d,]*\.?\d+$")

#: Checks that can corroborate a specific value. A generic structural pass
#: (e.g. reading order) says nothing about whether 1,900,000 is real.
_VALUE_BEARING_CHECKS = {"row_identity", "subtotal_identity"}


class ModelUnavailable(Exception):
    """Raised when no reconstruction model can be called."""


@runtime_checkable
class ReconstructionModel(Protocol):
    async def reconstruct(self, evidence: Evidence, tier: Tier) -> Reconstruction:
        ...


class NullReconstructionModel:
    """Declines every request. The safe default when nothing is configured."""

    async def reconstruct(self, evidence: Evidence, tier: Tier) -> Reconstruction:
        raise ModelUnavailable("No reconstruction model is configured")


def _normalize_number(raw: str) -> Optional[Decimal]:
    text = (raw or "").strip()
    if not _NUMERIC_TOKEN.match(text):
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _evidence_tokens(evidence: Evidence) -> set[str]:
    corpus = [evidence.native_text or "", evidence.ocr_text or ""]
    for table in evidence.tables or []:
        for row in table:
            corpus.extend(str(cell) for cell in row)
    return {word.lower() for chunk in corpus for word in _WORD.findall(chunk)}


def _evidence_literals(evidence: Evidence) -> set[str]:
    """Whole tokens, so a substring of a longer number cannot corroborate."""
    corpus = [evidence.native_text or "", evidence.ocr_text or ""]
    for table in evidence.tables or []:
        for row in table:
            corpus.extend(str(cell) for cell in row)
    literals: set[str] = set()
    for chunk in corpus:
        literals.update(token.strip(".,;:()") for token in chunk.split())
    return literals


def reconstruction_content_is_source_anchored(
    reconstruction: Reconstruction, evidence: Evidence
) -> bool:
    """Every word in the reconstruction must already exist in the evidence.

    Word order may change - that is the whole point of repairing a shredded
    reading order - but vocabulary may not grow. A model that introduces words
    the page never contained is writing, not reconstructing.
    """
    available = _evidence_tokens(evidence)
    if not available:
        # Nothing to anchor against: a page with no extracted text at all
        # cannot corroborate any narrative, so refuse rather than trust.
        return not _WORD.findall(reconstruction.text or "")

    produced = {word.lower() for word in _WORD.findall(reconstruction.text or "")}
    for table in reconstruction.tables or []:
        for row in table:
            produced.update(word.lower() for cell in row for word in _WORD.findall(str(cell)))

    return produced <= available


def classify_corroboration(
    value: ReconstructedValue, evidence: Evidence, post_verdict: QualityVerdict
) -> ValueCorroboration:
    """Class one reconstructed value as corroborated or not.

    Two things can corroborate: the exact literal already appearing as a whole
    token in the evidence, or a value-bearing check that passed on *this*
    specific value. A generic PASS elsewhere on the page corroborates nothing.
    """
    raw = (value.raw_value or "").strip()

    if raw and raw in _evidence_literals(evidence):
        return ValueCorroboration(
            value_id=value.value_id,
            raw_value=raw,
            status=Corroboration.CORROBORATED,
            check_name="source_literal",
            evidence_refs=["evidence.text"],
        )

    normalized = _normalize_number(raw)
    if normalized is not None:
        for check in post_verdict.checks or []:
            if (
                check.verdict is Verdict.PASS
                and check.name in _VALUE_BEARING_CHECKS
                and check.candidate_value is not None
                and check.candidate_value == normalized
                and check.evidence_refs
            ):
                return ValueCorroboration(
                    value_id=value.value_id,
                    raw_value=raw,
                    status=Corroboration.CORROBORATED,
                    check_name=check.name,
                    evidence_refs=list(check.evidence_refs),
                )

    return ValueCorroboration(
        value_id=value.value_id,
        raw_value=raw,
        status=Corroboration.UNCORROBORATED,
    )


class ExtractionFallbackLadder:
    def __init__(
        self,
        *,
        gate: Any,
        rasterizer: PageRasterizer,
        ledger: InterventionLedger,
        tier1: Optional[ReconstructionModel] = None,
        tier2: Optional[ReconstructionModel] = None,
        budget: Optional[int] = None,
    ) -> None:
        self.gate = gate
        self.rasterizer = rasterizer
        self.ledger = ledger
        self.tier1 = tier1
        self.tier2 = tier2
        self.budget = budget

    async def resolve(
        self,
        source: Path,
        page: ExtractedPage,
        verdict: QualityVerdict,
        *,
        document_id: str,
        tables: Optional[Sequence[Sequence[Sequence[str]]]] = None,
        region: Optional[Tuple[float, float, float, float]] = None,
    ) -> ResolvedPage:
        if not verdict.escalates:
            # Nothing is known to be wrong. Paying a model here is exactly the
            # false-escalation mistake this pipeline was built to avoid.
            return ResolvedPage(page=page, outcome=FallbackOutcome.UNCHANGED)

        tiers = [(Tier.TIER_1, self.tier1), (Tier.TIER_2, self.tier2)]
        spent = 0

        for tier, model in tiers:
            if model is None:
                continue
            if self.budget is not None and spent >= self.budget:
                break

            try:
                evidence = assemble_evidence(
                    source, page, verdict, self.rasterizer, region=region
                )
            except RasterizationError as exc:
                logger.warning(
                    "Could not assemble evidence for page %s: %s", page.number, exc
                )
                break

            spent += 1
            started = time.monotonic()
            try:
                reconstruction = await model.reconstruct(evidence, tier)
            except Exception as exc:  # noqa: BLE001 - every failure declines
                logger.warning("Tier %s reconstruction failed: %s", int(tier), exc)
                await self._record(
                    document_id,
                    page,
                    evidence,
                    tier,
                    reconstruction=None,
                    post_verdict=None,
                    outcome=FallbackOutcome.ESCALATED,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    note=str(exc)[:200],
                )
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            candidate = replace(
                page,
                text=reconstruction.text,
                source=PageSource.RECONSTRUCTED,
            )
            post_verdict = self.gate.assess(
                candidate, tables=reconstruction.tables or tables
            )
            corroborations = [
                classify_corroboration(value, evidence, post_verdict)
                for value in reconstruction.factual_values
            ]

            accepted = self._is_acceptable(
                reconstruction, evidence, post_verdict, corroborations
            )
            outcome = (
                FallbackOutcome.RESOLVED if accepted else FallbackOutcome.ESCALATED
            )

            await self._record(
                document_id,
                page,
                evidence,
                tier,
                reconstruction=reconstruction,
                post_verdict=post_verdict,
                outcome=outcome,
                latency_ms=latency_ms,
                corroborations=corroborations,
            )

            if accepted:
                return ResolvedPage(
                    page=candidate,
                    outcome=FallbackOutcome.RESOLVED,
                    tier_used=tier,
                    reconstruction=reconstruction,
                    post_verdict=post_verdict,
                    corroborations=corroborations,
                )

        # Exhausted. The original page survives untouched - nothing invented -
        # and the terminal state is recorded so it cannot read as completed.
        await self._record(
            document_id,
            page,
            None,
            Tier.TIER_2,
            reconstruction=None,
            post_verdict=None,
            outcome=FallbackOutcome.HUMAN_REVIEW_REQUIRED,
            latency_ms=0,
        )
        return ResolvedPage(page=page, outcome=FallbackOutcome.HUMAN_REVIEW_REQUIRED)

    @staticmethod
    def _is_acceptable(
        reconstruction: Reconstruction,
        evidence: Evidence,
        post_verdict: QualityVerdict,
        corroborations: Sequence[ValueCorroboration],
    ) -> bool:
        if post_verdict.escalates:
            return False
        if not (reconstruction.text or "").strip():
            return False
        if not reconstruction_content_is_source_anchored(reconstruction, evidence):
            return False
        return all(
            item.status is Corroboration.CORROBORATED for item in corroborations
        )

    async def _record(
        self,
        document_id: str,
        page: ExtractedPage,
        evidence: Optional[Evidence],
        tier: Tier,
        *,
        reconstruction: Optional[Reconstruction],
        post_verdict: Optional[QualityVerdict],
        outcome: FallbackOutcome,
        latency_ms: int,
        corroborations: Optional[Sequence[ValueCorroboration]] = None,
        note: str = "",
    ) -> None:
        corrections: List[dict] = []
        for item in corroborations or []:
            corrections.append(
                {
                    "before": "",
                    "after": item.raw_value,
                    "reason": note or "model reconstruction",
                    "value_id": item.value_id,
                    "corroboration": item.status.value,
                    "check_name": item.check_name,
                    "evidence_refs": list(item.evidence_refs),
                }
            )

        await self.ledger.record(
            Intervention(
                document_id=document_id,
                page_number=page.number,
                region_bbox=evidence.bbox if evidence else None,
                trigger="; ".join(
                    evidence.trigger_reasons if evidence else ["fallback exhausted"]
                ),
                tier=tier,
                model=reconstruction.model if reconstruction else "",
                model_version=reconstruction.model_version if reconstruction else "",
                prompt_version=reconstruction.prompt_version if reconstruction else "",
                evidence_sent=(
                    ("region" if evidence.is_region else "page") if evidence else "none"
                ),
                dpi=evidence.dpi if evidence else 0,
                text_sources=["native"] if evidence else [],
                confidence=reconstruction.confidence if reconstruction else 0.0,
                corrections=corrections,
                post_check=post_verdict.verdict.value if post_verdict else "",
                outcome=outcome,
                tokens=reconstruction.tokens if reconstruction else 0,
                cost_usd=reconstruction.cost_usd if reconstruction else 0.0,
                latency_ms=latency_ms,
            )
        )
