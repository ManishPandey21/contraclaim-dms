"""Minimal-evidence assembly and append-only intervention provenance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rbac_backend.services.extraction.fallback.evidence import assemble_evidence
from rbac_backend.services.extraction.fallback.ledger import InterventionLedger
from rbac_backend.services.extraction.fallback.models import (
    Corroboration,
    FallbackOutcome,
    Intervention,
    Tier,
)
from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
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
            inserted_id = "oid-1"

        return _Result()


class _FakeDb:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


def _page(number: int = 3, text: str = "native text") -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.MIXED_CONTENT,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=1,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def _verdict() -> QualityVerdict:
    return QualityVerdict(
        verdict=Verdict.FAIL,
        checks=[
            CheckResult(
                name="row_identity", verdict=Verdict.FAIL, detail="expected 1,000"
            )
        ],
        reasons=["row_identity: expected 1,000"],
    )


def test_page_evidence_carries_the_full_page_image(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    evidence = assemble_evidence(source, _page(), _verdict(), PageRasterizer(dpi=72))

    assert evidence.image_png.startswith(b"\x89PNG")
    assert evidence.is_region is False
    assert evidence.bbox is None


def test_region_evidence_is_preferred_when_a_bbox_is_given(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    full = assemble_evidence(source, _page(), _verdict(), PageRasterizer(dpi=72))
    region = assemble_evidence(
        source,
        _page(),
        _verdict(),
        PageRasterizer(dpi=72),
        region=(0.0, 0.0, 200.0, 200.0),
    )

    assert region.is_region is True
    assert region.bbox == (0.0, 0.0, 200.0, 200.0)
    assert len(region.image_png) < len(full.image_png)


def test_evidence_includes_existing_text_and_the_trigger(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    evidence = assemble_evidence(source, _page(), _verdict(), PageRasterizer(dpi=72))

    assert evidence.native_text == "native text"
    assert evidence.trigger_reasons == ["row_identity: expected 1,000"]


def test_evidence_never_includes_other_pages(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    evidence = assemble_evidence(
        source, _page(number=4), _verdict(), PageRasterizer(dpi=72)
    )

    # Only one image, and it is page 4's. Nothing about neighbouring pages.
    assert not hasattr(evidence, "document_text")
    assert evidence.page_number == 4


def test_evidence_records_the_dpi_it_was_rendered_at(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    evidence = assemble_evidence(source, _page(), _verdict(), PageRasterizer(dpi=72))

    assert evidence.dpi == 72


async def test_ledger_writes_the_full_provenance_record() -> None:
    db = _FakeDb()
    ledger = InterventionLedger(db=db)

    await ledger.record(
        Intervention(
            document_id="doc-1",
            page_number=3,
            region_bbox=None,
            trigger="row_identity: expected 1,000",
            tier=Tier.TIER_1,
            model="stub-vision",
            model_version="2026-08-01",
            prompt_version="v1",
            evidence_sent="page",
            dpi=150,
            text_sources=["native", "ocr"],
            confidence=0.82,
            corrections=[
                {
                    "before": "1 ,900,000",
                    "after": "1,900,000",
                    "reason": "split digit",
                    "value_id": "table-0-row-1-amount",
                    "corroboration": Corroboration.CORROBORATED.value,
                    "check_name": "subtotal_identity",
                    "evidence_refs": ["printed.subtotal", "peer.amounts"],
                }
            ],
            post_check=Verdict.PASS.value,
            outcome=FallbackOutcome.RESOLVED,
            tokens=1200,
            cost_usd=0.004,
            latency_ms=1830,
        )
    )

    record = db[InterventionLedger.COLLECTION].inserted[0]
    assert record["document_id"] == "doc-1"
    assert record["page_number"] == 3
    assert record["tier"] == 1
    assert record["model"] == "stub-vision"
    assert record["prompt_version"] == "v1"
    assert record["post_check"] == "pass"
    assert record["outcome"] == "resolved"
    assert record["corrections"][0]["corroboration"] == "corroborated"
    assert "recorded_at" in record


async def test_ledger_is_append_only() -> None:
    db = _FakeDb()
    ledger = InterventionLedger(db=db)
    collection = db[InterventionLedger.COLLECTION]

    for tier in (Tier.TIER_1, Tier.TIER_2):
        await ledger.record(
            Intervention(
                document_id="doc-1",
                page_number=3,
                region_bbox=None,
                trigger="text_density",
                tier=tier,
                model="stub",
                model_version="1",
                prompt_version="v1",
                evidence_sent="page",
                dpi=150,
                text_sources=["native"],
                confidence=0.5,
                corrections=[],
                post_check=Verdict.FAIL.value,
                outcome=FallbackOutcome.ESCALATED,
                tokens=10,
                cost_usd=0.0,
                latency_ms=5,
            )
        )

    assert len(collection.inserted) == 2
    assert [record["tier"] for record in collection.inserted] == [1, 2]


async def test_ledger_records_what_was_sent_for_a_region() -> None:
    db = _FakeDb()

    await InterventionLedger(db=db).record(
        Intervention(
            document_id="doc-1",
            page_number=5,
            region_bbox=(10.0, 20.0, 300.0, 400.0),
            trigger="reading_order",
            tier=Tier.TIER_1,
            model="stub",
            model_version="1",
            prompt_version="v1",
            evidence_sent="region",
            dpi=150,
            text_sources=["native"],
            confidence=0.9,
            corrections=[],
            post_check=Verdict.PASS.value,
            outcome=FallbackOutcome.RESOLVED,
            tokens=100,
            cost_usd=0.001,
            latency_ms=200,
        )
    )

    record = db[InterventionLedger.COLLECTION].inserted[0]
    assert record["evidence_sent"] == "region"
    assert record["region_bbox"] == [10.0, 20.0, 300.0, 400.0]
