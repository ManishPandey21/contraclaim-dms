"""The fallback ladder: bounded tiers, re-verification, no fabrication.

Rules asserted here:
  * NOT_CHECKABLE never invokes a model
  * model confidence may route but never accept
  * a reconstruction must survive the SAME gate to be adopted
  * content must be anchored in the evidence - novel narrative is refused
  * an unresolved page ends HUMAN_REVIEW_REQUIRED, never resolved
  * every invocation leaves a ledger row
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from rbac_backend.services.extraction.fallback.ladder import (
    ExtractionFallbackLadder,
    ModelUnavailable,
    NullReconstructionModel,
    classify_corroboration,
)
from rbac_backend.services.extraction.fallback.ledger import InterventionLedger
from rbac_backend.services.extraction.fallback.models import (
    Corroboration,
    Evidence,
    FallbackOutcome,
    ReconstructedValue,
    Reconstruction,
    Tier,
)
from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.extraction.quality.models import (
    CheckResult,
    QualityVerdict,
    Verdict,
)
from rbac_backend.services.extraction.rasterizer import PageRasterizer
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf


class _FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> Any:
        self.inserted.append(document)

        class _Result:
            inserted_id = "oid"

        return _Result()


class _FakeDb:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


class _StubModel:
    def __init__(
        self,
        text: str,
        confidence: float = 0.95,
        name: str = "stub",
        factual_values: list[ReconstructedValue] | None = None,
    ) -> None:
        self.text = text
        self.confidence = confidence
        self.name = name
        self.factual_values = factual_values or []
        self.calls = 0

    async def reconstruct(self, evidence: Evidence, tier: Tier) -> Reconstruction:
        self.calls += 1
        return Reconstruction(
            text=self.text,
            factual_values=self.factual_values,
            confidence=self.confidence,
            model=self.name,
            model_version="1",
            prompt_version="v1",
        )


def _page(text: str, page_class: PageClass = PageClass.MIXED_CONTENT) -> ExtractedPage:
    return ExtractedPage(
        number=3,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=page_class,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def _fail_verdict() -> QualityVerdict:
    return QualityVerdict(
        verdict=Verdict.FAIL,
        checks=[
            CheckResult(name="text_density", verdict=Verdict.FAIL, detail="0 chars")
        ],
        reasons=["text_density: 0 chars"],
    )


def _not_checkable_verdict() -> QualityVerdict:
    return QualityVerdict(
        verdict=Verdict.NOT_CHECKABLE,
        checks=[CheckResult(name="row_identity", verdict=Verdict.NOT_CHECKABLE)],
    )


class _AlwaysNotCheckableGate:
    def assess(self, page, *, tables=None) -> QualityVerdict:
        return _not_checkable_verdict()


def empty_evidence() -> Evidence:
    return Evidence(
        page_number=1,
        image_png=b"",
        is_region=False,
        bbox=None,
        native_text="",
        ocr_text="",
        tables=[],
        trigger_reasons=[],
        dpi=150,
    )


def _ladder(
    db: _FakeDb, tier1: Any = None, tier2: Any = None
) -> ExtractionFallbackLadder:
    return ExtractionFallbackLadder(
        gate=ExtractionQualityGate(),
        rasterizer=PageRasterizer(dpi=72),
        ledger=InterventionLedger(db=db),
        tier1=tier1,
        tier2=tier2,
    )


async def test_not_checkable_never_calls_a_model(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    model = _StubModel("anything")

    resolved = await _ladder(db, tier1=model).resolve(
        source, _page("some text"), _not_checkable_verdict(), document_id="doc-1"
    )

    assert model.calls == 0
    assert resolved.outcome is FallbackOutcome.UNCHANGED
    assert db[InterventionLedger.COLLECTION].inserted == []


async def test_passing_verdict_never_calls_a_model(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    model = _StubModel("anything")

    resolved = await _ladder(db, tier1=model).resolve(
        source,
        _page("some text"),
        QualityVerdict(verdict=Verdict.PASS),
        document_id="doc-1",
    )

    assert model.calls == 0
    assert resolved.outcome is FallbackOutcome.UNCHANGED


async def test_tier1_success_resolves_and_is_ledgered(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    model = _StubModel("Recovered covering letter narrative without factual literals.")

    resolved = await _ladder(db, tier1=model).resolve(
        source,
        _page("factual literals without recovered covering letter narrative"),
        _fail_verdict(),
        document_id="doc-1",
    )

    assert model.calls == 1
    assert resolved.outcome is FallbackOutcome.RESOLVED
    assert resolved.tier_used is Tier.TIER_1
    assert resolved.page.source is PageSource.RECONSTRUCTED
    assert db[InterventionLedger.COLLECTION].inserted[0]["outcome"] == "resolved"


async def test_high_confidence_alone_does_not_accept_a_bad_reconstruction(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    # Confident, but still empty - the gate must reject it regardless.
    model = _StubModel("", confidence=0.99)

    resolved = await _ladder(db, tier1=model).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


async def test_post_gate_not_checkable_is_never_accepted(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    ladder = _ladder(db, tier1=_StubModel("model text"))
    ladder.gate = _AlwaysNotCheckableGate()

    resolved = await ladder.resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


async def test_pass_with_uncorroborated_reconstructed_number_is_rejected(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    model = _StubModel(
        "Amount 1,900,000",
        factual_values=[ReconstructedValue("v1", "number", "1,900,000", "text:7")],
    )

    resolved = await _ladder(_FakeDb(), tier1=model).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


def test_unrelated_pass_check_does_not_corroborate_a_reconstructed_number() -> None:
    value = ReconstructedValue("v1", "number", "1,900,000", "table-0-r1-c4")
    post = QualityVerdict(
        verdict=Verdict.PASS,
        checks=[CheckResult(name="reading_order", verdict=Verdict.PASS)],
    )

    result = classify_corroboration(value, empty_evidence(), post)

    assert result.status is Corroboration.UNCORROBORATED


def test_matching_value_specific_subtotal_check_corroborates() -> None:
    value = ReconstructedValue("v1", "number", "1,900,000", "table-0-r1-c4")
    post = QualityVerdict(
        verdict=Verdict.PASS,
        checks=[
            CheckResult(
                name="subtotal_identity",
                verdict=Verdict.PASS,
                candidate_value=Decimal("1900000"),
                evidence_refs=["printed.subtotal", "candidate.amount", "peer.amounts"],
            )
        ],
    )

    result = classify_corroboration(value, empty_evidence(), post)

    assert result.status is Corroboration.CORROBORATED
    assert result.check_name == "subtotal_identity"


def test_source_substring_is_not_exact_value_corroboration() -> None:
    value = ReconstructedValue("v1", "number", "1,900,000", "text:7")
    evidence = Evidence(
        page_number=1,
        image_png=b"",
        is_region=False,
        bbox=None,
        native_text="Amount 11,900,000",
        ocr_text="",
        tables=[],
        trigger_reasons=[],
        dpi=150,
    )

    result = classify_corroboration(value, evidence, _not_checkable_verdict())

    assert result.status is Corroboration.UNCORROBORATED


def test_exact_source_token_does_corroborate() -> None:
    value = ReconstructedValue("v1", "number", "1,900,000", "text:7")
    evidence = Evidence(
        page_number=1,
        image_png=b"",
        is_region=False,
        bbox=None,
        native_text="Mobilization 1,900,000 INR",
        ocr_text="",
        tables=[],
        trigger_reasons=[],
        dpi=150,
    )

    result = classify_corroboration(value, evidence, _not_checkable_verdict())

    assert result.status is Corroboration.CORROBORATED


async def test_novel_uncorroborated_narrative_is_rejected(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    resolved = await _ladder(
        _FakeDb(), tier1=_StubModel("Novel liability admission by the contractor")
    ).resolve(source, _page("covering letter"), _fail_verdict(), document_id="doc-1")

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


async def test_failed_tier1_escalates_to_tier2(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    tier1 = _StubModel("", confidence=0.9, name="small")
    tier2 = _StubModel("Recovered text at last, with real content.", name="large")

    resolved = await _ladder(db, tier1=tier1, tier2=tier2).resolve(
        source,
        _page("real content recovered at last with text"),
        _fail_verdict(),
        document_id="doc-1",
    )

    assert tier1.calls == 1
    assert tier2.calls == 1
    assert resolved.outcome is FallbackOutcome.RESOLVED
    assert resolved.tier_used is Tier.TIER_2
    assert len(db[InterventionLedger.COLLECTION].inserted) == 2


async def test_both_tiers_failing_ends_in_human_review(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()

    resolved = await _ladder(db, tier1=_StubModel(""), tier2=_StubModel("")).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED
    assert (
        db[InterventionLedger.COLLECTION].inserted[-1]["outcome"]
        == "human_review_required"
    )


async def test_unavailable_model_declines_rather_than_fabricating(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()

    resolved = await _ladder(db, tier1=NullReconstructionModel()).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED
    assert resolved.page.text == ""  # nothing invented


async def test_no_tiers_configured_ends_in_human_review(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()

    resolved = await _ladder(db).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


async def test_ladder_never_runs_more_than_two_tiers(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    tier1, tier2 = _StubModel(""), _StubModel("")

    await _ladder(db, tier1=tier1, tier2=tier2).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert tier1.calls == 1
    assert tier2.calls == 1


async def test_null_model_raises_model_unavailable() -> None:
    with pytest.raises(ModelUnavailable):
        await NullReconstructionModel().reconstruct(empty_evidence(), Tier.TIER_1)


async def test_original_page_survives_a_refused_reconstruction(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    original = _page("the original native text")

    resolved = await _ladder(
        _FakeDb(), tier1=_StubModel("Entirely invented replacement narrative")
    ).resolve(source, original, _fail_verdict(), document_id="doc-1")

    assert resolved.page.text == "the original native text"
    assert resolved.page.source is PageSource.TEXT_LAYER
