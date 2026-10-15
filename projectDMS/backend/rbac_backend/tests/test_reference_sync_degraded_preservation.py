"""A degraded, partial or uncertain reference extraction never deletes links.

Every correspondence reference exists in three places: the forward link on
the citing document (``references``), the backlink on the cited document
(``referencedBy``) and the FalkorDB ``CITES`` edge owned by the citing
document. The graph edge is a projection of the forward links (the Falkor
writer reconciles the owner's edges to ``references``), so the three layers
move together or not at all.

Only an *authoritative* reference result may remove a link. ``[]`` is not
"cites nothing" unless the extraction explicitly said so; a reference result
read from incomplete source text, a failed or absent References item, an AI
stage failure, or the deterministic OCR fallback is "unknown" and must leave
all three layers untouched.

These tests drive ``DocumentService.process_document_async`` (single upload,
reprocess and retry all enter there) with a fake processor, the real parser,
the real ``ReferenceSyncService`` and the real Falkor payload builder over an
in-memory Falkor double that models owner-scoped reconciliation.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import pytest
from bson import ObjectId

from rbac_backend.models.document_metadata import build_parsed_metadata
from rbac_backend.services import reference_sync_service as reference_sync_module
from rbac_backend.services.database_service import DatabaseService
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.falkor_graph_service import normalize_letter_code
from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.metadata_integrity import (
    assess_metadata_quality,
    source_text_complete_signal,
)
from rbac_backend.services.pydantic_ai_service import PydanticAIService
from rbac_backend.services.reference_parser import (
    REFERENCE_NONE,
    REFERENCE_READABLE,
    REFERENCE_UNREADABLE,
    classify_reference_entry,
)
from rbac_backend.services.text_processing_service import TextProcessingService
from rbac_backend.tests.test_correspondence_metadata_integrity import _real_parser_processor
from rbac_backend.tests.test_document_references import FakeDatabase, _make_document_dict

LETTER_A = "ABC/CORR/2024/118"
B, C, D = "LTR-200", "LTR-300", "LTR-400"
DATED = {B: "12.07.2024", C: "15.07.2024", D: "20.07.2024"}
DATE_OF = {code: datetime.strptime(value, "%d.%m.%Y") for code, value in DATED.items()}

REPORT_TEMPLATE = """1) Date: 05-08-2024
2) Letter No.: {letter_no}
3) From (Company): Alpha Constructions Ltd
4) To (Company): Metro Rail Corporation
5) Subject: Delay in handover of Station Box land
{references_item}7) Asset Type: Station
18) Key Words: land handover, delay, EOT
22) Summary: Contractor notifies continued delay in land handover.
23) Contractual Clauses: GCC 8.4
25) Full Content: The full letter text.
"""


def _report(refs: Optional[Sequence[str]], *, explicit_none: bool = False) -> str:
    """A numbered extraction report. ``refs=None`` omits the References item."""
    if refs is None:
        item = ""
    elif explicit_none:
        item = "6) References: null\n"
    else:
        item = "6) References:\n" + "".join(f"- {ref} dated {DATED[ref]}\n" for ref in refs)
    return REPORT_TEMPLATE.format(letter_no=LETTER_A, references_item=item)


def _parse(report: str) -> Any:
    return TextProcessingService(
        SimpleNamespace(chunk_size=1000, chunk_overlap=100)
    ).parse_extraction_report(report)


# --- Falkor double --------------------------------------------------------------


class FakeFalkor:
    """Owner-scoped edge store with the real writer's reconcile semantics.

    ``FalkorGraphService.upsert_letter_with_refs`` MERGEs every desired edge
    and, with ``cleanup=True``, deletes the owner's edges from the source
    letter whose target is not in the desired set.
    """

    enabled = True

    def __init__(self) -> None:
        self.edges: Set[Tuple[str, str, str, str]] = set()
        self.deleted: List[Tuple[str, str, str, str]] = []

    def upsert_letter_with_refs(
        self,
        letter: Dict[str, Any],
        references: Sequence[Dict[str, Any]],
        *,
        cleanup: Optional[bool] = None,
        owner_document_id: Optional[str] = None,
    ) -> None:
        owner = str(owner_document_id or "")
        if references and not owner:
            raise AssertionError("the real writer refuses ownerless edges")
        src = normalize_letter_code(letter["normCode"])
        # The real writer skips a self-citation.
        desired = {
            (ref["normCode"], ref["type"]) for ref in references if ref["normCode"] != src
        }
        for dst, rel in desired:
            self.edges.add((owner, src, dst, rel))
        if cleanup and owner:
            stale = {
                edge
                for edge in self.edges
                if edge[0] == owner and edge[1] == src and (edge[2], edge[3]) not in desired
            }
            self.deleted.extend(sorted(stale))
            self.edges -= stale

    def cites(self, owner: ObjectId) -> List[str]:
        return sorted(dst for o, _s, dst, rel in self.edges if o == str(owner) and rel == "CITES")


# --- Harness -------------------------------------------------------------------


class _Processor:
    def __init__(
        self,
        metadata: Any,
        *,
        metadata_source: str,
        partial_failures: Optional[Dict[str, Any]],
        extraction_completeness: Optional[str],
        pages_human_review: Sequence[int],
    ) -> None:
        self.metadata = metadata
        self.metadata_source = metadata_source
        self.partial_failures = dict(partial_failures or {})
        self.extraction_completeness = extraction_completeness
        self.pages_human_review = list(pages_human_review)

    async def process_document(self, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            success=True,
            metadata=self.metadata,
            processing_time=0.1,
            chunks_created=1,
            processed_path="/processed/letter.pdf",
            metadata_source=self.metadata_source,
            partial_failures=dict(self.partial_failures),
            extraction_completeness=self.extraction_completeness,
            pages_human_review=list(self.pages_human_review),
            publishable=not self.pages_human_review,
        )


class World:
    """Letter A, cited letters B, C, D, and letter E that cites A."""

    def __init__(self, monkeypatch, tmp_path: Path) -> None:
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.a = ObjectId()
        self.e = ObjectId()
        self.ids = {B: ObjectId(), C: ObjectId(), D: ObjectId()}
        docs = [
            _make_document_dict(
                _id=self.a,
                letterNo=LETTER_A,
                letterNoNormalized=normalize_letter_code(LETTER_A),
                summary=None,
                keywords=None,
            ),
            _make_document_dict(
                _id=self.e, letterNo="E/OUT/7", letterNoNormalized=normalize_letter_code("E/OUT/7")
            ),
        ]
        for code, oid in self.ids.items():
            docs.append(
                _make_document_dict(
                    _id=oid,
                    letterNo=code,
                    letterNoNormalized=normalize_letter_code(code),
                    date=DATE_OF[code],
                )
            )
        self.service = DocumentService(FakeDatabase(docs))
        self.falkor = FakeFalkor()
        self.service.graph_ingestion.falkor = self.falkor

        async def _noop(**_kwargs: Any) -> None:
            return None

        monkeypatch.setattr(self.service.graph_ingestion, "ingest_document", _noop)

    async def process(
        self,
        metadata: Any,
        *,
        metadata_source: str = "openai_text_legacy_regex",
        partial_failures: Optional[Dict[str, Any]] = None,
        extraction_completeness: Optional[str] = None,
        pages_human_review: Sequence[int] = (),
    ) -> Dict[str, Any]:
        processor = _Processor(
            metadata,
            metadata_source=metadata_source,
            partial_failures=partial_failures,
            extraction_completeness=extraction_completeness,
            pages_human_review=pages_human_review,
        )
        self.monkeypatch.setattr(
            "rbac_backend.services.document_service.create_document_processor",
            lambda: processor,
        )
        source = self.tmp_path / "letter.pdf"
        source.write_bytes(b"%PDF-1.4\n")
        await self.service.process_document_async(
            str(self.a),
            file_path=str(source),
            organization_id="org-1",
            project_id="proj-1",
            upload_type="incoming",
        )
        return await self.doc(self.a)

    async def doc(self, oid: ObjectId) -> Dict[str, Any]:
        db = await self.service._get_db()
        return await db.documents.find_one({"_id": oid})

    async def state(self) -> Dict[str, List[str]]:
        """The three layers, as cited letter codes."""
        by_id = {str(oid): code for code, oid in self.ids.items()}
        a = await self.doc(self.a)
        forward = sorted(by_id[str(ref["documentId"])] for ref in a.get("references") or [])
        backlinks = []
        for code, oid in self.ids.items():
            target = await self.doc(oid)
            backlinks.extend(
                code for ref in target.get("referencedBy") or [] if ref["documentId"] == str(self.a)
            )
        falkor_codes = {normalize_letter_code(code): code for code in self.ids}
        return {
            "forward": forward,
            "backlinks": sorted(backlinks),
            "falkor": sorted(falkor_codes[n] for n in self.falkor.cites(self.a)),
        }


def _all(*codes: str) -> Dict[str, List[str]]:
    return {"forward": list(codes), "backlinks": list(codes), "falkor": list(codes)}


async def _seed_bc(world: World) -> None:
    await world.process(_parse(_report([B, C])))
    assert await world.state() == _all(B, C), "fixture: A -> B, C on all three layers"


# --- Degraded results -----------------------------------------------------------


def _reference_field_failure() -> Any:
    """Usable metadata whose References item was present but unreadable."""
    report = _report([B, C])
    report = report.replace(f"- {B} dated {DATED[B]}", "- illegible").replace(
        f"- {C} dated {DATED[C]}", "- ???"
    )
    parsed = _parse(report)
    assert parsed.subject and parsed.letter_no == LETTER_A
    assert parsed.references == [] and "references" in parsed.field_failures
    return parsed


DEGRADED_EMPTY_CASES = {
    # name: (metadata factory, process kwargs)
    "references_item_absent": (lambda: _parse(_report(None)), {}),
    "references_item_unreadable": (_reference_field_failure, {}),
    "references_field_failure_recorded": (
        lambda: build_parsed_metadata(
            {"subject": "S", "letter_no": LETTER_A, "references": []},
            field_failures={"references": "reference parsing failed: ValueError"},
        ),
        {},
    ),
    "ocr_fallback": (
        lambda: _parse(_report([], explicit_none=True)),
        {"metadata_source": "ocr_fallback_regex"},
    ),
    "ai_stage_failure_record": (
        # Any AI stage failure record. The output-cap (truncation) work writes
        # this shape; its writer is not on this base, so this pins the
        # decision the record must produce, not the writer.
        lambda: _parse(_report([], explicit_none=True)),
        {
            "partial_failures": {
                "ai_extraction": {
                    "stage": "ocr_text_extraction",
                    "incomplete": True,
                    "reason": "max_output_tokens",
                }
            }
        },
    ),
    "unified_v1_partial_source_text": (
        # Source text missing pages: an explicit "null" read from part of the
        # letter is not "this letter cites nothing".
        lambda: _parse(_report([], explicit_none=True)),
        {"extraction_completeness": "partial"},
    ),
    "pydantic_input_truncated": (
        lambda: _parse(_report([], explicit_none=True)),
        {
            "metadata_source": "pydantic_ai",
            "partial_failures": {
                "source_text_truncated": {"stage": "pydantic_ai_input", "chars_sent": 12000}
            },
        },
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(DEGRADED_EMPTY_CASES))
async def test_degraded_empty_result_preserves_all_three_layers(
    monkeypatch, tmp_path, case
) -> None:
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    factory, kwargs = DEGRADED_EMPTY_CASES[case]
    deleted_before = len(world.falkor.deleted)

    stored = await world.process(factory(), **kwargs)

    assert await world.state() == _all(B, C), f"{case}: a degraded run deleted links"
    assert len(world.falkor.deleted) == deleted_before, "degraded run deleted a CITES edge"
    assert stored["metadata_quality"]["references_authoritative"] is False
    assert [r.get("letterNo") for r in stored["reference"]] == [B, C], "stored list replaced"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"partial_failures": {"ai_extraction": {"stage": "whole_file_extraction"}}},
        {"extraction_completeness": "partial"},
        {"extraction_completeness": "partial", "pages_human_review": [2]},
    ],
    ids=["ai_stage_failure", "partial_source", "partial_source_needs_review"],
)
async def test_degraded_partial_result_does_not_drop_the_unrecovered_link(
    monkeypatch, tmp_path, kwargs
) -> None:
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)

    stored = await world.process(_parse(_report([B])), **kwargs)

    assert await world.state() == _all(B, C), "partial [B] replaced [B, C]"
    assert stored["metadata_quality"]["references_authoritative"] is False
    # And a later manual "Sync references" cannot replay the partial list.
    assert [r.get("letterNo") for r in stored["reference"]] == [B, C]


@pytest.mark.asyncio
async def test_partly_unreadable_reference_list_does_not_drop_the_unread_link(
    monkeypatch, tmp_path
) -> None:
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    report = _report([B]).replace(
        f"- {B} dated {DATED[B]}\n", f"- {B} dated {DATED[B]}\n- [illegible]\n"
    )
    parsed = _parse(report)
    assert [r["letterNo"] for r in parsed.references] == [B]

    stored = await world.process(parsed)

    assert await world.state() == _all(B, C)
    assert stored["metadata_quality"]["references_not_authoritative_because"] == [
        "references_field_failed"
    ]


@pytest.mark.parametrize(
    "entry,unreadable",
    [
        ("illegible", True),
        ("???", True),
        ("[unreadable]", True),
        ("Not clearly visible", True),
        ("cannot be determined", True),
        ("See attached", True),
        ("LTR-200 dated 12.07.2024", False),
        ("Contract Agreement dated 01.01.2020", False),
        ("Unknown Contractor letter no. UC/12", False),
        ("MoM of meeting held on 03.03.2024", False),
    ],
)
def test_only_uncertainty_markers_count_as_unreadable_entries(entry, unreadable) -> None:
    parsed = _parse(_report([B]).replace(f"- {B} dated {DATED[B]}\n", f"- {entry}\n"))
    assert ("references" in parsed.field_failures) is unreadable, parsed.references
    assert bool(parsed.references) is not unreadable


@pytest.mark.asyncio
async def test_degraded_skip_is_recorded_without_document_content(monkeypatch, tmp_path) -> None:
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    stored = await world.process(_parse(_report([B])), extraction_completeness="partial")

    record = stored["processing_metadata"]["partial_failures"]["reference_sync"]
    assert record["action"] == "preserved_existing"
    assert record["reason"] == "reference_extraction_not_authoritative"
    assert record["causes"] == ["source_text_incomplete"]
    assert B not in repr(record) and "Station Box" not in repr(record)
    assert "source_text_incomplete" in stored["metadata_quality"]["warnings"]


# --- Authoritative results still synchronise -------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("completeness", [None, "complete"], ids=["legacy_v0", "unified_v1"])
async def test_authoritative_changed_set_replaces_on_all_three_layers(
    monkeypatch, tmp_path, completeness
) -> None:
    world = World(monkeypatch, tmp_path)
    await world.process(_parse(_report([B, C])), extraction_completeness=completeness)
    assert await world.state() == _all(B, C)

    stored = await world.process(_parse(_report([B, D])), extraction_completeness=completeness)

    assert await world.state() == _all(B, D)
    assert any(edge[2] == normalize_letter_code(C) for edge in world.falkor.deleted)
    assert stored["metadata_quality"]["references_authoritative"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("completeness", [None, "complete"], ids=["legacy_v0", "unified_v1"])
async def test_explicit_none_is_authoritative_zero_and_clears(
    monkeypatch, tmp_path, completeness
) -> None:
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)

    stored = await world.process(
        _parse(_report([], explicit_none=True)), extraction_completeness=completeness
    )

    assert await world.state() == _all()
    assert stored["metadata_quality"]["references_authoritative"] is True
    assert stored["reference"] == []


@pytest.mark.asyncio
async def test_reprocessing_sequence_is_idempotent(monkeypatch, tmp_path) -> None:
    world = World(monkeypatch, tmp_path)
    await world.process(_parse(_report([B, C])))
    assert await world.state() == _all(B, C)

    for _ in range(2):
        await world.process(_parse(_report(None)))
        await world.process(_parse(_report([B])), extraction_completeness="partial")
        assert await world.state() == _all(B, C)

    await world.process(_parse(_report([B, D])))
    assert await world.state() == _all(B, D)

    # No duplicate links, backlinks or edges accumulated on the way.
    a = await world.doc(world.a)
    assert len(a["references"]) == 2
    for code in (B, D):
        target = await world.doc(world.ids[code])
        assert len([r for r in target["referencedBy"] if r["documentId"] == str(world.a)]) == 1
    assert len([e for e in world.falkor.edges if e[0] == str(world.a)]) == 2


# --- Manual links and unresolved references --------------------------------------


@pytest.mark.asyncio
async def test_manual_link_survives_degraded_and_authoritative_parser_runs(
    monkeypatch, tmp_path
) -> None:
    world = World(monkeypatch, tmp_path)
    await world.service.reference_sync_service.sync_bidirectional(
        document_id=str(world.a),
        references=[{"documentId": str(world.ids[D]), "letterNo": D}],
        source="manual",
        default_link_type="direct",
    )
    await world.process(_parse(_report([B, C])))
    await world.process(_parse(_report(None)))
    await world.process(_parse(_report([], explicit_none=True)))

    a = await world.doc(world.a)
    assert [(r["documentId"], r["source"]) for r in a["references"]] == [
        (str(world.ids[D]), "manual")
    ]
    assert (await world.state())["backlinks"] == [D]


@pytest.mark.asyncio
async def test_unresolved_reference_is_kept_through_degraded_runs(monkeypatch, tmp_path) -> None:
    world = World(monkeypatch, tmp_path)
    report = _report([B]).replace(
        f"- {B} dated {DATED[B]}\n",
        f"- {B} dated {DATED[B]}\n- FUT/9 dated 01.08.2024\n",
    )
    first = await world.process(_parse(report))
    db = await world.service._get_db()
    queued = await db.reference_sync_queue.find({"document_id": str(world.a)}).to_list(None)
    assert [e["status"] for e in queued] == ["pending"]
    assert any((r.get("letterNo") == "FUT/9") for r in first["reference"])

    second = await world.process(_parse(_report(None)))

    assert second["reference"] == first["reference"]
    queued = await db.reference_sync_queue.find({"document_id": str(world.a)}).to_list(None)
    assert [e["status"] for e in queued] == ["pending"]


# --- The sync service itself: only an authoritative call removes -----------------


@pytest.mark.asyncio
async def test_non_authoritative_sync_never_removes_a_link(monkeypatch, tmp_path) -> None:
    """``clear_existing=False`` used to bucket-replace any non-empty list."""
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)

    summary = await world.service.reference_sync_service.sync_bidirectional(
        document_id=str(world.a),
        references=[{"letterNo": B}],
        source="parser",
        clear_existing=False,
    )
    await world.service.sync_reference_graph(str(world.a))

    assert summary["removed_targets"] == 0
    assert await world.state() == _all(B, C)


@pytest.mark.asyncio
async def test_manual_resync_after_degraded_run_keeps_links(monkeypatch, tmp_path) -> None:
    from rbac_backend.routers.documents import controller_sync_references

    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    await world.process(_parse(_report([B])), extraction_completeness="partial")

    async def _allow(*_args: Any, **_kwargs: Any) -> None:
        return None

    controller = SimpleNamespace(
        document_service=world.service, policy_service=SimpleNamespace(authorize_document=_allow)
    )
    result = await controller_sync_references(controller, str(world.a), SimpleNamespace(id="u"))

    assert result["sync"]["removed_targets"] == 0
    assert await world.state() == _all(B, C)


def test_every_parser_sync_call_derives_clear_existing_from_authority() -> None:
    """No production caller may hard-code a parser bucket replace."""
    root = Path(reference_sync_module.__file__).resolve().parents[1]
    offenders: List[str] = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "sync_bidirectional"
            ):
                continue
            for kw in node.keywords:
                if kw.arg == "clear_existing" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    # The one allowed literal sits inside an `if references_authoritative:` guard.
    allowed = {"services/document_service.py"}
    assert {o.split(":")[0].replace("\\", "/") for o in offenders} <= allowed, offenders


# --- The authority decision: field-specific -------------------------------------


def test_unrelated_field_failure_keeps_references_authoritative() -> None:
    parsed = build_parsed_metadata(
        {"subject": "S", "letter_no": LETTER_A, "references": [{"letterNo": B}]},
        field_failures={"summary": "summary failed validation"},
    )
    quality = assess_metadata_quality(parsed, metadata_source="openai_text_legacy_regex")
    assert quality["degraded"] is True  # visible...
    assert quality["references_authoritative"] is True  # ...but not about references


def test_reference_field_failure_makes_references_non_authoritative() -> None:
    parsed = build_parsed_metadata(
        {"subject": "S", "letter_no": LETTER_A, "summary": "fine", "references": [{"letterNo": B}]},
        field_failures={"references": "references item present but no reference could be read"},
    )
    quality = assess_metadata_quality(parsed, metadata_source="openai_text_legacy_regex")
    assert quality["references_authoritative"] is False
    assert "references_field_failed" in quality["references_not_authoritative_because"]


@pytest.mark.parametrize(
    "completeness,review,expected",
    [
        (None, [], None),  # legacy_v0: no completeness signal
        ("complete", [], True),
        ("partial", [], False),
        ("complete", [3], False),
    ],
)
def test_source_text_complete_signal(completeness, review, expected) -> None:
    assert source_text_complete_signal(completeness, review) is expected


def test_ai_stage_failure_is_not_authoritative_even_with_a_references_item() -> None:
    parsed = _parse(_report([B]))
    quality = assess_metadata_quality(
        parsed,
        metadata_source="openai_text_legacy_regex",
        partial_failures={"ai_extraction": {"stage": "ocr_text_extraction", "incomplete": True}},
    )
    assert quality["references_authoritative"] is False


# --- Uncertain entries: one classifier for both extraction paths ----------------


@pytest.mark.parametrize(
    "entry,expected",
    [
        ("illegible", REFERENCE_UNREADABLE),
        ("???", REFERENCE_UNREADABLE),
        ("[unreadable]", REFERENCE_UNREADABLE),
        ("Not clearly visible", REFERENCE_UNREADABLE),
        ("To be confirmed", REFERENCE_UNREADABLE),
        ("TBD", REFERENCE_UNREADABLE),
        ("See attached", REFERENCE_UNREADABLE),
        ({"letterNo": "LTR-3??", "date": "15-07-2024"}, REFERENCE_UNREADABLE),
        ({"letterNo": "LTR-[illegible]"}, REFERENCE_UNREADABLE),
        ({"letterNo": None, "date": "15-07-2024"}, REFERENCE_UNREADABLE),
        ("null", REFERENCE_NONE),
        ("—", REFERENCE_NONE),
        ({"letterNo": "LTR-200", "date": "12-07-2024"}, REFERENCE_READABLE),
        ("Contract Agreement dated 01.01.2020", REFERENCE_READABLE),
        ("Unknown Contractor letter no. UC/12", REFERENCE_READABLE),
        ({"letterNo": "LTR-3?"}, REFERENCE_UNREADABLE),
        # A "?" inside a code token is an unread character, wherever it sits.
        ("Letter No. KMRC/PKG?/12", REFERENCE_UNREADABLE),
        ({"letterNo": "LTR-3?0", "date": "15-07-2024"}, REFERENCE_UNREADABLE),
        ("L?R-300", REFERENCE_UNREADABLE),
        # ...but not a "?" that closes a prose word.
        ("Ref: query on drawing? No. D/1", REFERENCE_READABLE),
        ("पत्र १२३", REFERENCE_READABLE),  # Devanagari
    ],
)
def test_reference_entry_classifier(entry, expected) -> None:
    assert classify_reference_entry(entry) == expected


@pytest.mark.parametrize(
    "entry,unreadable",
    [
        ("illegible", True),
        ("LTR-3?? dated 15.07.2024", True),
        ("LTR-200 dated 12.07.2024", False),
        ("MoM of meeting held on 03.03.2024", False),
    ],
)
def test_report_parser_uses_the_classifier(entry, unreadable) -> None:
    parsed = _parse(_report([B]).replace(f"- {B} dated {DATED[B]}\n", f"- {entry}\n"))
    assert ("references" in parsed.field_failures) is unreadable, parsed.references
    assert bool(parsed.references) is not unreadable


def test_em_dash_references_item_is_authoritative_zero() -> None:
    parsed = _parse(_report([B]).replace(f"6) References:\n- {B} dated {DATED[B]}\n", "6) References: —\n"))
    assert parsed.references == [] and "references" not in parsed.field_failures


# --- PydanticAI: the real output schema ------------------------------------------


@pytest.fixture
def metadata_model() -> Any:
    config = DocumentProcessingConfig()
    config.use_pydantic_ai = True
    service = PydanticAIService(config)
    assert service._metadata_model is not None, "PydanticAI schema must build in the test env"
    return service, service._metadata_model


def _pydantic_quality(service: Any, model: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    parsed = service._to_parsed_metadata(model.model_validate(payload), fallback_text="")
    return assess_metadata_quality(parsed, metadata_source="pydantic_ai")


HEADER = {"subject": "S", "letterNo": LETTER_A, "date": "05-08-2024"}


def test_pydantic_omitted_references_are_not_authoritative(metadata_model) -> None:
    service, model = metadata_model
    quality = _pydantic_quality(service, model, dict(HEADER))
    assert quality["references_authoritative"] is False
    assert quality["references_not_authoritative_because"] == ["references_field_failed"]


@pytest.mark.parametrize("value", [None, []], ids=["explicit_null", "explicit_empty"])
def test_pydantic_explicit_none_is_authoritative_zero(metadata_model, value) -> None:
    service, model = metadata_model
    assert _pydantic_quality(service, model, {**HEADER, "references": value})[
        "references_authoritative"
    ] is True


@pytest.mark.parametrize(
    "references",
    [
        [{"letterNo": B}, {"letterNo": "illegible"}],
        [{"letterNo": B}, {"letterNo": None, "date": "15-07-2024"}],
        [{"letterNo": "???"}],
    ],
    ids=["illegible_entry", "date_only_entry", "question_marks"],
)
def test_pydantic_uncertain_entries_are_not_authoritative(metadata_model, references) -> None:
    service, model = metadata_model
    quality = _pydantic_quality(service, model, {**HEADER, "references": references})
    assert quality["references_authoritative"] is False


def test_pydantic_clean_entries_are_authoritative(metadata_model) -> None:
    service, model = metadata_model
    quality = _pydantic_quality(
        service, model, {**HEADER, "references": [{"letterNo": B, "date": "12-07-2024"}]}
    )
    assert quality["references_authoritative"] is True


def test_pydantic_schema_does_not_default_references_to_an_empty_list() -> None:
    """A defaulted ``[]`` makes an omitted field indistinguishable from zero."""
    source = inspect.getsource(PydanticAIService.__init__)
    tree = ast.parse(textwrap.dedent(source))
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "references":
            found = ast.unparse(node)
    assert found is not None
    assert "default_factory=list" not in found and "Optional" in found, found


# --- An authoritative list with an unresolved entry ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [f"LTR-3OO dated {DATED[C]}", "LTR-3OO", f"Our letter dated {DATED[C]}"],
    ids=["ocr_confusion_with_date", "ocr_confusion_alone", "same_date_no_number"],
)
async def test_unresolved_misread_does_not_delete_the_link_it_may_name(
    monkeypatch, tmp_path, entry
) -> None:
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    report = _report([B]).replace(
        f"- {B} dated {DATED[B]}\n", f"- {B} dated {DATED[B]}\n- {entry}\n"
    )
    stored = await world.process(_parse(report))

    assert stored["metadata_quality"]["references_authoritative"] is True
    assert await world.state() == _all(B, C)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [f"LTR-3?0 dated {DATED[C]}", "LTR-3?0", f"LTR-?00 dated {DATED[C]}", "L?R-300"],
    ids=["mid_digit_with_date", "mid_digit", "lead_digit_with_date", "in_prefix"],
)
async def test_question_mark_inside_a_number_never_deletes_a_link(
    monkeypatch, tmp_path, entry
) -> None:
    """Normalisation turns "LTR-3?0" into "ltr-3-0", which matches nothing,
    so a list carrying it must not be allowed to replace [B, C]."""
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    report = _report([B]).replace(
        f"- {B} dated {DATED[B]}\n", f"- {B} dated {DATED[B]}\n- {entry}\n"
    )
    stored = await world.process(_parse(report))

    assert await world.state() == _all(B, C)
    assert stored["metadata_quality"]["references_authoritative"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    ["FUT/9 dated 01.08.2024", f"EXT/HQ/55 dated {DATED[C]}"],
    ids=["other_date", "same_date_with_its_own_number"],
)
async def test_an_unrelated_unresolved_entry_still_lets_a_real_change_through(
    monkeypatch, tmp_path, entry
) -> None:
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    report = _report([B]).replace(
        f"- {B} dated {DATED[B]}\n", f"- {B} dated {DATED[B]}\n- {entry}\n"
    )
    summary_runs = [await world.process(_parse(report))]

    assert await world.state() == _all(B), "a real change was withheld"
    assert summary_runs[0]["metadata_quality"]["references_authoritative"] is True


# --- Truncated input: what was read is linked, nothing is removed ---------------


TRUNCATED = {
    "metadata_source": "pydantic_ai",
    "partial_failures": {
        "source_text_truncated": {"stage": "pydantic_ai_input", "chars_sent": 12000}
    },
}


@pytest.mark.asyncio
async def test_truncated_input_links_what_it_read_and_removes_nothing(
    monkeypatch, tmp_path
) -> None:
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)

    stored = await world.process(_parse(_report([B, D])), **TRUNCATED)

    assert await world.state() == _all(B, C, D)
    assert [r.get("letterNo") for r in stored["reference"]] == [B, C, D]
    record = stored["processing_metadata"]["partial_failures"]["reference_sync"]
    assert record["action"] == "added_only"
    assert record["causes"] == ["source_text_truncated"]


@pytest.mark.asyncio
async def test_truncated_input_links_a_first_upload(monkeypatch, tmp_path) -> None:
    world = World(monkeypatch, tmp_path)
    await world.process(_parse(_report([B, C])), **TRUNCATED)
    assert await world.state() == _all(B, C)


@pytest.mark.asyncio
async def test_other_non_authoritative_causes_add_nothing(monkeypatch, tmp_path) -> None:
    """A partial source drops pages from the middle; its entries are doubtful."""
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    stored = await world.process(_parse(_report([B, D])), extraction_completeness="partial")
    assert await world.state() == _all(B, C)
    assert stored["processing_metadata"]["partial_failures"]["reference_sync"]["action"] == (
        "preserved_existing"
    )


def test_bulk_writer_merges_a_truncated_list_into_the_stored_one() -> None:
    doc_id = ObjectId()
    fake_db = FakeDatabase(
        [_make_document_dict(_id=doc_id, reference=[{"letterNo": B}, {"letterNo": C}])]
    )
    parsed = _parse(_report([B, D]))
    quality = assess_metadata_quality(
        parsed,
        metadata_source="pydantic_ai",
        partial_failures=TRUNCATED["partial_failures"],
    )
    asyncio.run(
        DatabaseService(SimpleNamespace())._upsert_document_metadata(
            fake_db, str(doc_id), "/tmp/x.pdf", parsed, "ocr", metadata_quality=quality
        )
    )
    persisted = asyncio.run(fake_db.documents.find_one({"_id": doc_id}))
    assert [r.get("letterNo") for r in persisted["reference"]] == [B, C, D]


# --- Header fields from incomplete source ---------------------------------------


@pytest.mark.asyncio
async def test_partial_source_does_not_rewrite_the_letter_number(monkeypatch, tmp_path) -> None:
    """A number read from part of the letter is often a cited letter's number.

    Replacing A's number with it broke every link that resolves A by number
    (E -> A was removed on E's next authoritative sync) and stranded A's
    graph edges under the wrong source letter.
    """
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    await world.service.reference_sync_service.sync_bidirectional(
        document_id=str(world.e),
        references=[{"letterNo": LETTER_A}],
        source="parser",
        clear_existing=True,
    )

    misread = _parse(
        REPORT_TEMPLATE.format(
            letter_no=B, references_item=f"6) References:\n- {B} dated {DATED[B]}\n"
        )
    )
    stored = await world.process(misread, extraction_completeness="partial")

    assert stored["letterNo"] == LETTER_A
    assert stored["letterNoNormalized"] == normalize_letter_code(LETTER_A)
    assert stored["metadata_quality"]["source_degraded"] is True
    summary = await world.service.reference_sync_service.sync_bidirectional(
        document_id=str(world.e),
        references=[{"letterNo": LETTER_A}],
        source="parser",
        clear_existing=True,
    )
    assert summary["removed_targets"] == 0
    e_doc = await world.doc(world.e)
    assert [r["documentId"] for r in e_doc["references"]] == [str(world.a)]
    assert {edge[1] for edge in world.falkor.edges if edge[0] == str(world.a)} == {
        normalize_letter_code(LETTER_A)
    }


# --- Bulk / processor-side writer -------------------------------------------------


def test_bulk_writer_does_not_store_a_list_read_from_incomplete_source() -> None:
    """Bulk upload persists through ``DatabaseService`` and never calls the
    sync service; it must still not store a partial list, or a later manual
    sync would replay it."""
    doc_id = ObjectId()
    fake_db = FakeDatabase(
        [
            _make_document_dict(
                _id=doc_id, letterNo=LETTER_A, reference=[{"letterNo": B}, {"letterNo": C}]
            )
        ]
    )
    parsed = _parse(REPORT_TEMPLATE.format(letter_no=B, references_item=f"6) References:\n- {B} dated {DATED[B]}\n"))
    quality = assess_metadata_quality(
        parsed, metadata_source="openai_text_legacy_regex", source_text_complete=False
    )
    asyncio.run(
        DatabaseService(SimpleNamespace())._upsert_document_metadata(
            fake_db, str(doc_id), "/tmp/x.pdf", parsed, "ocr", metadata_quality=quality
        )
    )
    persisted = asyncio.run(fake_db.documents.find_one({"_id": doc_id}))
    assert persisted["reference"] == [{"letterNo": B}, {"letterNo": C}]
    assert persisted["letterNo"] == LETTER_A
    assert persisted["metadata_quality"]["references_authoritative"] is False


def _run_processor(
    processor: Any, tmp_path: Path, *, extraction: Any, review: Sequence[int] = ()
) -> Dict[str, Any]:
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    result = asyncio.run(
        processor._extract_and_persist(
            input_path=source,
            original_path=str(source),
            processed_path=source,
            raw_ocr_text="OCR text of the letter",
            extraction=extraction,
            pages_human_review=list(review),
            path_structure="org/proj",
            upload_type="incoming",
            document_id="doc-1",
            skip_embeddings=False,
            start_time=0.0,
        )
    )
    assert result.success is True
    saved = processor.database_service.saved
    assert saved, "the processor-side writer was not called"
    return {"result": result, "quality": saved[-1]["metadata_quality"]}


@pytest.mark.parametrize(
    "completeness,review,authoritative",
    [
        (None, (), True),  # legacy_v0
        ("complete", (), True),
        ("partial", (), False),
        ("complete", (2,), False),
    ],
    ids=["legacy_v0", "unified_complete", "unified_partial", "unified_needs_review"],
)
def test_processor_writer_gets_the_source_completeness_decision(
    tmp_path, completeness, review, authoritative
) -> None:
    processor = _real_parser_processor(_report([B]))
    extraction = (
        None
        if completeness is None
        else SimpleNamespace(
            pages=[], withheld_pages=[], completeness=SimpleNamespace(value=completeness)
        )
    )
    run = _run_processor(processor, tmp_path, extraction=extraction, review=review)
    assert run["quality"]["references_authoritative"] is authoritative


def test_processor_records_a_truncated_pydantic_input(tmp_path) -> None:
    processor = _real_parser_processor(_report([B]))
    parsed = _parse(_report([B]))

    async def _extract(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=parsed,
            debug={"input_truncated": {"chars_sent": 12000, "chars_total": 30000}},
            raw_result=None,
        )

    processor.pydantic_ai_service = SimpleNamespace(is_enabled=True, extract_metadata=_extract)
    run = _run_processor(processor, tmp_path, extraction=None)

    assert run["result"].metadata_source == "pydantic_ai"
    assert run["result"].partial_failures["source_text_truncated"]["chars_sent"] == 12000
    assert run["quality"]["references_authoritative"] is False
    assert run["quality"]["references_not_authoritative_because"] == ["source_text_truncated"]
    # The header sits in the part the model read: header fields stand, and
    # the record is not flagged to the drafter as partially extracted.
    assert run["quality"]["source_degraded"] is False
    assert run["quality"]["status"] == "complete_with_warnings"


# --- Reaper ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reaper_resolving_one_queued_entry_keeps_every_other_link(
    monkeypatch, tmp_path
) -> None:
    world = World(monkeypatch, tmp_path)
    await _seed_bc(world)
    db = await world.service._get_db()
    await db.reference_sync_queue.insert_one(
        {
            "document_id": str(world.a),
            "reference_key": "ltr-400",
            "status": "pending",
            "reference": {"letterNo": D},
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }
    )

    await world.service.reference_sync_service.drain_reference_queue()
    await world.service.sync_reference_graph(str(world.a))

    assert await world.state() == _all(B, C, D)
