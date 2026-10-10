"""Canonical evidence: the complete document survives independently of indexing.

Chunks and embeddings are retrieval aids. The complete extracted text of a
document - in page order, with every page's provenance and review state - must
be persisted before any indexing step runs, and must stay readable when the
embedding provider, the vector store or the LLM is unavailable.

The chain tests drive the real service -> processor -> engine -> page store
path (``retry_harness``); only OCR, OpenAI and graph publication are stubbed.
The read path is exercised through ``get_document_canonical_evidence`` with
the real ``PolicyService`` and ``ActiveScope``; only the permission and
entitlement seams are monkeypatched.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, List, Optional

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.core.security import CurrentUser
from rbac_backend.core.tenant_context import ActiveScope, TenantContextError
from rbac_backend.services.canonical_evidence_service import (
    EVIDENCE_NOT_BUILT,
    EVIDENCE_PUBLISHED,
    CanonicalEvidenceInconsistentError,
    get_document_canonical_evidence,
)
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.extraction.canonical import (
    PAGE_SEPARATOR,
    DuplicatePageError,
    assemble_canonical_document,
)
from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction_adapters.document_page_store import (
    CanonicalHeadConflictError,
    DOCUMENT_EXTRACTION_HEADS,
    DOCUMENT_OCR_PAGES,
    DocumentPageStore,
)
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.tests.retry_harness import FakeDb, RetryHarness

ORG_A, ORG_B = "org-A", "org-B"
A1, A2, B1 = "proj-A1", "proj-A2", "proj-B1"

NATIVE_ONE = "NATIVE PAGE ONE body text"
NATIVE_TWO = "NATIVE PAGE TWO body text"
NATIVE_THREE = "NATIVE PAGE THREE"
OCR_ONE = "OCR PAGE ONE"
OCR_TWO = "OCR PAGE TWO"
OCR_THREE = "OCR PAGE THREE"


def _principal(user_id: str, roles: List[str], org: Optional[str], projects: List[str] = ()) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        roles=roles,
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
    )


MEMBER_A1 = _principal("u-member-a1", ["projectuser"], ORG_A, [A1])
ADMIN_A1 = _principal("u-admin-a1", ["projectadmin"], ORG_A, [A1])
MEMBER_A2 = _principal("u-member-a2", ["projectuser"], ORG_A, [A2])
ORG_ADMIN_B = _principal("u-orgadmin-b", ["orgadmin"], ORG_B)
NO_VIEW_A1 = _principal("u-noview-a1", ["projectuser"], ORG_A, [A1])
SUPERADMIN = _principal("u-root", ["superadmin"], None)

GRANTS = {
    MEMBER_A1.id: {"dms.document.view"},
    ADMIN_A1.id: {"dms.document.view"},
    MEMBER_A2.id: {"dms.document.view"},
    ORG_ADMIN_B.id: {"dms.document.view"},
    NO_VIEW_A1.id: set(),
}


@pytest.fixture(autouse=True)
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(self, user_id: str, permission_name: str, **_kwargs: Any) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **_kwargs: Any):
        return True, "ok"

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


# --- helpers --------------------------------------------------------------


def _page(
    number: int,
    text: str,
    *,
    source: PageSource = PageSource.TEXT_LAYER,
    status: PageStatus = PageStatus.TEXT_LAYER,
    raw_text: Optional[str] = None,
    applied_repairs: Optional[list] = None,
    needs_review: bool = False,
    quality_verdict: Optional[str] = "pass",
) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=text,
        source=source,
        status=status,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
        raw_text=raw_text,
        applied_repairs=list(applied_repairs or []),
        needs_review=needs_review,
        quality_verdict=quality_verdict,
    )


async def _seed_document(db: FakeDb, *, org: str = ORG_A, project: str = A1, **extra: Any) -> str:
    document_id = ObjectId()
    await db.documents.insert_one(
        {"_id": document_id, "organization_id": org, "project_id": project, "filename": "x.pdf", **extra}
    )
    return str(document_id)


def _store(db: FakeDb, document_id: str, *, run: str = "run-1", org: str = ORG_A, project: str = A1) -> DocumentPageStore:
    return DocumentPageStore(
        db=db, document_id=document_id, organization_id=org, project_id=project, extraction_run_id=run
    )


async def _publish(store: DocumentPageStore, pages: List[ExtractedPage]) -> dict:
    await store.record_pages(pages)
    return await store.publish_canonical(
        expected=assemble_canonical_document(pages), pipeline_version="unified_v1", source_sha256="abc"
    )


def _selected(db: FakeDb, org: str = ORG_A, project: str = A1, user: Any = SUPERADMIN) -> ActiveScope:
    """The navbar selection the browser sends: the record's own project."""
    return ActiveScope(db, user, org, project)


async def _evidence(harness: RetryHarness, user: CurrentUser = SUPERADMIN):
    document = await harness.document()
    selection = _selected(harness.db, str(document["organization_id"]), str(document["project_id"]), user)
    return await get_document_canonical_evidence(
        harness.db, str(harness.document_id), current_user=user, selection=selection
    )


async def _head(harness: RetryHarness) -> Optional[dict]:
    return await harness.db[DOCUMENT_EXTRACTION_HEADS].find_one({"document_id": str(harness.document_id)})


# --- the one assembly rule ------------------------------------------------


def test_assembly_is_byte_identical_to_the_combined_text_already_persisted() -> None:
    pages = [_page(1, "alpha\n line"), _page(2, ""), _page(3, "gamma  ")]

    canonical = assemble_canonical_document(pages)

    # What the engine and processor have always written to documents.ocrText.
    assert canonical.text == "\n\n".join(page.text for page in pages)
    assert PAGE_SEPARATOR == "\n\n"


def test_assembly_orders_pages_deterministically_and_is_idempotent() -> None:
    pages = [_page(3, "three"), _page(1, "one"), _page(2, "two")]

    first = assemble_canonical_document(pages)
    second = assemble_canonical_document(list(reversed(pages)))

    assert first.text == "one\n\ntwo\n\nthree"
    assert first.text.encode() == second.text.encode()
    assert first.manifest() == second.manifest()
    assert first.sha256 == second.sha256


def test_duplicate_page_records_are_refused_not_silently_merged() -> None:
    with pytest.raises(DuplicatePageError) as raised:
        assemble_canonical_document([_page(1, "a"), _page(2, "b"), _page(2, "b again")])
    assert raised.value.page_numbers == [2]


def test_every_page_span_maps_back_to_its_source_page() -> None:
    pages = [_page(1, "first page"), _page(2, ""), _page(3, "third\npage"), _page(4, "x")]

    canonical = assemble_canonical_document(pages)

    assert [span.page_number for span in canonical.page_map] == [1, 2, 3, 4]
    for page, span in zip(pages, canonical.page_map):
        assert canonical.text[span.start : span.end] == page.text
        assert canonical.page_text(page.number) == page.text
    # An offset inside page 3 resolves to page 3; an empty page owns no offset.
    offset = canonical.text.index("third")
    assert canonical.page_at(offset) == 3
    assert canonical.page_map[1].char_count == 0
    # Spans are contiguous apart from the separator and cover the whole text.
    assert canonical.page_map[-1].end == len(canonical.text)


def test_a_review_page_keeps_its_text_and_reports_review_separately() -> None:
    pages = [_page(1, "clean"), _page(2, "doubtful table", needs_review=True, quality_verdict="fail")]

    canonical = assemble_canonical_document(pages)

    assert "doubtful table" in canonical.text
    assert canonical.review_pages == [2]
    # Reliability is not completeness: the content is all there.
    assert canonical.build_status == "complete"


def test_a_failed_page_is_represented_and_the_build_is_not_called_complete() -> None:
    pages = [
        _page(1, "kept"),
        _page(2, "", source=PageSource.EMPTY, status=PageStatus.OCR_FAILED, quality_verdict=None),
        _page(3, "also kept"),
    ]

    canonical = assemble_canonical_document(pages)

    assert canonical.text == "kept\n\n\n\nalso kept"
    assert canonical.unresolved_pages == [2]
    assert canonical.build_status == "partial"
    assert canonical.page_map[1].status == "ocr_failed"
    assert canonical.page_map[1].char_count == 0


# --- store: persisted manifest, raw vs canonical, retry/reprocess ----------


async def test_deterministic_repair_keeps_raw_auditable_and_canonical_corrected() -> None:
    db = FakeDb()
    document_id = await _seed_document(db)
    repair = {"kind": "numeric_split_digit", "before": "1 9,292,171", "after": "19,292,171"}
    page = _page(
        1,
        "Claim amount 19,292,171 INR",
        raw_text="Claim amount 1 9,292,171 INR",
        applied_repairs=[repair],
    )

    await _publish(_store(db, document_id), [page])
    evidence = await get_document_canonical_evidence(db, document_id, current_user=SUPERADMIN, selection=_selected(db))

    assert "19,292,171" in evidence.text
    assert "1 9,292,171" not in evidence.text
    (page_evidence,) = evidence.pages
    assert page_evidence.original_text == "Claim amount 1 9,292,171 INR"
    assert page_evidence.applied_repairs == [repair]
    assert evidence.manifest["page_map"][0]["has_original_text"] is True
    assert evidence.manifest["page_map"][0]["repair_count"] == 1


async def test_publishing_the_same_evidence_twice_is_byte_identical() -> None:
    db = FakeDb()
    document_id = await _seed_document(db)
    pages = [_page(1, "one"), _page(2, "two")]
    store = _store(db, document_id)

    first = await _publish(store, pages)
    second = await _publish(store, pages)

    assert first["canonical_sha256"] == second["canonical_sha256"]
    assert first["page_map"] == second["page_map"]
    assert second["canonical_revision"] == first["canonical_revision"] + 1
    rows = await db[DOCUMENT_OCR_PAGES].find({"document_id": document_id}).to_list(None)
    assert sorted(row["page_number"] for row in rows) == [1, 2]


async def test_reprocessing_one_page_updates_the_canonical_representation() -> None:
    db = FakeDb()
    document_id = await _seed_document(db)
    store = _store(db, document_id)
    before = await _publish(store, [_page(1, "one"), _page(2, "", status=PageStatus.OCR_FAILED, source=PageSource.EMPTY)])

    after = await _publish(store, [_page(1, "one"), _page(2, "two recovered", source=PageSource.OCR, status=PageStatus.OCR_COMPLETED)])
    evidence = await get_document_canonical_evidence(db, document_id, current_user=SUPERADMIN, selection=_selected(db))

    assert before["build_status"] == "partial"
    assert after["build_status"] == "complete"
    assert evidence.text == "one\n\ntwo recovered"
    assert evidence.text.count("two recovered") == 1
    assert evidence.canonical_revision == 2


async def test_publish_refuses_when_persisted_rows_disagree_with_the_assembled_text() -> None:
    db = FakeDb()
    document_id = await _seed_document(db)
    store = _store(db, document_id)
    pages = [_page(1, "one"), _page(2, "two")]
    await store.record_pages(pages)
    in_memory = assemble_canonical_document([_page(1, "one"), _page(2, "two EDITED")])

    with pytest.raises(CanonicalEvidenceInconsistentError):
        await store.publish_canonical(expected=in_memory, pipeline_version="unified_v1", source_sha256=None)
    assert await db[DOCUMENT_EXTRACTION_HEADS].find_one({"document_id": document_id}) is None


async def test_publish_refuses_a_run_missing_one_of_its_pages() -> None:
    db = FakeDb()
    document_id = await _seed_document(db)
    store = _store(db, document_id)
    await store.record_pages([_page(1, "one")])

    with pytest.raises(CanonicalEvidenceInconsistentError):
        await store.publish_canonical(
            expected=assemble_canonical_document([_page(1, "one"), _page(2, "two")]),
            pipeline_version="unified_v1",
            source_sha256=None,
        )


async def test_a_stale_head_is_detected_on_read_never_served() -> None:
    db = FakeDb()
    document_id = await _seed_document(db)
    await _publish(_store(db, document_id), [_page(1, "one"), _page(2, "two")])
    # A page row rewritten after the manifest was built (a crash between the
    # row write and the head write) must not be served as the published text.
    await db[DOCUMENT_OCR_PAGES].update_one(
        {"document_id": document_id, "page_number": 2}, {"$set": {"raw_text": "two CHANGED"}}
    )

    with pytest.raises(CanonicalEvidenceInconsistentError):
        await get_document_canonical_evidence(db, document_id, current_user=SUPERADMIN, selection=_selected(db))


async def test_large_document_is_neither_truncated_on_write_nor_on_read() -> None:
    db = FakeDb()
    document_id = await _seed_document(db)
    page_count, body = 1200, "x" * 2500
    pages = [_page(n, f"[page {n}] {body}") for n in range(1, page_count + 1)]
    expected = assemble_canonical_document(pages)

    started = time.perf_counter()
    manifest = await _publish(_store(db, document_id), pages)
    evidence = await get_document_canonical_evidence(db, document_id, current_user=SUPERADMIN, selection=_selected(db))
    elapsed = time.perf_counter() - started

    assert manifest["page_count"] == page_count
    assert manifest["canonical_char_count"] == len(expected.text) > 3_000_000
    assert evidence.text == expected.text
    assert evidence.text.endswith(f"[page {page_count}] {body}")
    # Cheap relative to OCR: generous ceiling, only catches a pathology.
    assert elapsed < 30


async def test_a_document_without_canonical_evidence_says_so_explicitly() -> None:
    db = FakeDb()
    document_id = await _seed_document(db, ocrText="legacy text", ocr_text_kind="source")

    evidence = await get_document_canonical_evidence(db, document_id, current_user=SUPERADMIN, selection=_selected(db))

    assert evidence.status == EVIDENCE_NOT_BUILT
    assert evidence.text is None
    assert evidence.pages == []
    # Offered, labelled, never presented as canonical.
    assert evidence.legacy_text == "legacy text"
    assert evidence.legacy_text_kind == "source"


async def test_evidence_rows_from_another_tenant_are_never_served() -> None:
    db = FakeDb()
    document_id = await _seed_document(db, org=ORG_A, project=A1)
    # Page rows written under another organisation for the same id.
    await _publish(_store(db, document_id, org=ORG_B, project=B1), [_page(1, "foreign")])

    with pytest.raises(CanonicalEvidenceInconsistentError):
        await get_document_canonical_evidence(db, document_id, current_user=SUPERADMIN, selection=_selected(db))


# --- tenant isolation on the read path --------------------------------------


async def _published_in(db: FakeDb, org: str, project: str) -> str:
    # The real ScopeService checks project-to-organisation ownership.
    for project_id, organization_id in ((A1, ORG_A), (A2, ORG_A), (B1, ORG_B)):
        if not await db.projects.find_one({"_id": project_id}):
            await db.projects.insert_one({"_id": project_id, "organization_id": organization_id})
    document_id = await _seed_document(db, org=org, project=project)
    await _publish(_store(db, document_id, org=org, project=project), [_page(1, f"evidence of {project}")])
    return document_id


async def test_same_project_member_reads_the_evidence() -> None:
    db = FakeDb()
    document_id = await _published_in(db, ORG_A, A1)

    for user in (MEMBER_A1, ADMIN_A1):
        evidence = await get_document_canonical_evidence(
            db, document_id, current_user=user, selection=ActiveScope(db, user, ORG_A, A1)
        )
        assert evidence.status == EVIDENCE_PUBLISHED
        assert evidence.text == "evidence of proj-A1"


async def test_foreign_project_member_is_refused() -> None:
    db = FakeDb()
    document_id = await _published_in(db, ORG_A, A1)

    with pytest.raises(HTTPException) as raised:
        await get_document_canonical_evidence(
            db, document_id, current_user=MEMBER_A2, selection=ActiveScope(db, MEMBER_A2, ORG_A, A1)
        )
    assert raised.value.status_code == 403


async def test_project_user_cannot_widen_scope_through_the_selection() -> None:
    db = FakeDb()
    document_id = await _published_in(db, ORG_A, A1)

    # Selected A2 (their own project), asks for an A1 record.
    with pytest.raises(TenantContextError) as raised:
        await get_document_canonical_evidence(
            db, document_id, current_user=MEMBER_A2, selection=ActiveScope(db, MEMBER_A2, ORG_A, A2)
        )
    assert raised.value.status_code == 403


async def test_foreign_organisation_is_refused() -> None:
    db = FakeDb()
    document_id = await _published_in(db, ORG_A, A1)

    with pytest.raises(HTTPException) as raised:
        await get_document_canonical_evidence(
            db, document_id, current_user=ORG_ADMIN_B, selection=ActiveScope(db, ORG_ADMIN_B, ORG_A, A1)
        )
    assert raised.value.status_code == 403


async def test_member_without_view_permission_is_refused() -> None:
    db = FakeDb()
    document_id = await _published_in(db, ORG_A, A1)

    with pytest.raises(HTTPException) as raised:
        await get_document_canonical_evidence(
            db, document_id, current_user=NO_VIEW_A1, selection=ActiveScope(db, NO_VIEW_A1, ORG_A, A1)
        )
    assert raised.value.status_code == 403


async def test_superadmin_is_still_bounded_by_an_explicit_selection() -> None:
    db = FakeDb()
    document_id = await _published_in(db, ORG_A, A1)

    allowed = await get_document_canonical_evidence(
        db, document_id, current_user=SUPERADMIN, selection=ActiveScope(db, SUPERADMIN, ORG_A, A1)
    )
    assert allowed.status == EVIDENCE_PUBLISHED
    with pytest.raises(TenantContextError) as raised:
        await get_document_canonical_evidence(
            db, document_id, current_user=SUPERADMIN, selection=ActiveScope(db, SUPERADMIN, ORG_B, B1)
        )
    assert raised.value.status_code == 403


async def test_unknown_document_is_not_found_and_reveals_nothing() -> None:
    db = FakeDb()
    with pytest.raises(Exception) as raised:
        await get_document_canonical_evidence(db, str(ObjectId()), current_user=SUPERADMIN, selection=_selected(db))
    assert getattr(raised.value, "http_status", None) == 404


async def test_anonymous_caller_is_refused() -> None:
    db = FakeDb()
    document_id = await _published_in(db, ORG_A, A1)
    with pytest.raises(HTTPException) as raised:
        await get_document_canonical_evidence(
            db, document_id, current_user=None, selection=ActiveScope(db, None, ORG_A, A1)
        )
    assert raised.value.status_code == 401


# --- the real chain: evidence precedes indexing ---------------------------


async def test_digital_multi_page_document_is_complete_and_ordered(tmp_path: Path, monkeypatch: Any) -> None:
    harness = await RetryHarness.create(
        tmp_path, monkeypatch, page_lines=[[NATIVE_ONE], [NATIVE_TWO], [NATIVE_THREE]], script={}
    )

    await harness.run_attempt()
    evidence = await _evidence(harness)

    assert evidence.status == EVIDENCE_PUBLISHED
    assert evidence.manifest["build_status"] == "complete"
    assert [page.page_number for page in evidence.pages] == [1, 2, 3]
    positions = [evidence.text.index(marker) for marker in (NATIVE_ONE, NATIVE_TWO, NATIVE_THREE)]
    assert positions == sorted(positions)
    assert {page.source for page in evidence.pages} == {"text_layer"}
    assert evidence.publication_consumable is True
    # Exactly what was persisted as the document's text.
    assert evidence.text == harness.full_text()


async def test_ocr_document_pages_appear_in_canonical_text(tmp_path: Path, monkeypatch: Any) -> None:
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=[None, None],
        script={1: {1: OCR_ONE, 2: OCR_TWO}},
    )

    await harness.run_attempt()
    evidence = await _evidence(harness)

    assert evidence.text.startswith(OCR_ONE) and OCR_TWO in evidence.text
    assert [page.source for page in evidence.pages] == ["ocr", "ocr"]


async def test_mixed_document_has_each_page_exactly_once(tmp_path: Path, monkeypatch: Any) -> None:
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=[None, [NATIVE_TWO], None],
        script={1: {1: OCR_ONE, 3: OCR_THREE}},
    )

    await harness.run_attempt()
    evidence = await _evidence(harness)

    for marker in (OCR_ONE, NATIVE_TWO, OCR_THREE):
        assert evidence.text.count(marker) == 1
    assert [page.source for page in evidence.pages] == ["ocr", "text_layer", "ocr"]
    for page in evidence.pages:
        assert evidence.page_text(page.page_number).strip()


async def test_failed_page_is_truthful_and_other_pages_remain(tmp_path: Path, monkeypatch: Any) -> None:
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=[None, None, [NATIVE_THREE]],
        script={1: {1: OCR_ONE, 2: RuntimeError("OCRmyPDF batch failed (exit=2)")}},
    )

    await harness.run_attempt()
    evidence = await _evidence(harness)

    assert evidence.manifest["build_status"] == "partial"
    assert evidence.manifest["unresolved_pages"] == [2]
    assert OCR_ONE in evidence.text and NATIVE_THREE in evidence.text
    failed = evidence.page(2)
    assert failed.status == "ocr_failed"
    assert failed.error
    assert evidence.page_text(2) == ""


async def test_page_retry_does_not_duplicate_and_updates_canonical(tmp_path: Path, monkeypatch: Any) -> None:
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=[None, None, [NATIVE_THREE]],
        script={1: {1: OCR_ONE, 2: RuntimeError("OCRmyPDF batch failed (exit=2)")}, 2: {2: OCR_TWO}},
    )

    await harness.run_attempt()
    first = await _head(harness)
    await harness.run_attempt()
    evidence = await _evidence(harness)

    assert first["build_status"] == "partial"
    assert evidence.manifest["build_status"] == "complete"
    for marker in (OCR_ONE, OCR_TWO, NATIVE_THREE):
        assert evidence.text.count(marker) == 1
    assert evidence.text.index(OCR_ONE) < evidence.text.index(OCR_TWO) < evidence.text.index(NATIVE_THREE)
    assert evidence.canonical_revision == first["canonical_revision"] + 1
    assert evidence.extraction_run_id == first["extraction_run_id"]
    assert evidence.text == harness.full_text()


async def test_review_page_text_is_kept_while_vectors_stay_withheld(tmp_path: Path, monkeypatch: Any) -> None:
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=[[NATIVE_ONE], [NATIVE_TWO]],
        script={},
        escalate_pages={2},
    )

    await harness.run_attempt()
    evidence = await _evidence(harness)

    # Whole-document embedding withholding is unchanged ...
    assert harness.last_save()["skip_embeddings"] is True
    # ... but the complete canonical text exists, review page included.
    assert NATIVE_TWO in evidence.text
    assert evidence.manifest["review_pages"] == [2]
    assert evidence.page(2).needs_review is True
    assert evidence.manifest["build_status"] == "complete"
    # Served for review, but flagged: a drafting consumer must not rely on it.
    assert evidence.publication_consumable is False


async def test_indexing_failure_never_prevents_canonical_evidence(tmp_path: Path, monkeypatch: Any) -> None:
    """Qdrant down + embedding provider down + no LLM: evidence still stands."""
    harness = await RetryHarness.create(
        tmp_path, monkeypatch, page_lines=[None, [NATIVE_TWO]], script={1: {1: OCR_ONE}}
    )

    from rbac_backend.tests import retry_harness

    async def _vector_store_down(self, **kwargs: Any) -> int:
        raise RuntimeError("Qdrant unavailable: connection refused; embedding provider unavailable")

    async def _no_llm(self, text: str, **kwargs: Any) -> str:
        raise RuntimeError("no LLM API key configured")

    monkeypatch.setattr(retry_harness.FakeDatabaseService, "save_document_data", _vector_store_down)
    monkeypatch.setattr(retry_harness._OpenAI, "process_text", _no_llm)

    await harness.run_attempt()
    evidence = await _evidence(harness)

    assert evidence.status == EVIDENCE_PUBLISHED
    assert OCR_ONE in evidence.text and NATIVE_TWO in evidence.text
    assert [page.page_number for page in evidence.pages] == [1, 2]
    assert evidence.page(1).source == "ocr"


async def test_ai_metadata_failure_leaves_canonical_text_available(tmp_path: Path, monkeypatch: Any) -> None:
    harness = await RetryHarness.create(tmp_path, monkeypatch, page_lines=[[NATIVE_ONE]], script={})

    from rbac_backend.tests import retry_harness

    async def _no_llm(self, text: str, **kwargs: Any) -> str:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(retry_harness._OpenAI, "process_text", _no_llm)

    await harness.run_attempt()
    evidence = await _evidence(harness)

    assert NATIVE_ONE in evidence.text
    assert evidence.text == harness.full_text()


async def test_chain_records_integrity_metadata(tmp_path: Path, monkeypatch: Any) -> None:
    harness = await RetryHarness.create(tmp_path, monkeypatch, page_lines=[[NATIVE_ONE], [NATIVE_TWO]], script={})

    await harness.run_attempt()
    head = await _head(harness)
    evidence = await _evidence(harness)

    assert head["pipeline_version"] == "unified_v1"
    assert head["engine_version"]
    assert head["source_sha256"] and len(head["source_sha256"]) == 64
    assert head["organization_id"] == str((await harness.document())["organization_id"])
    assert head["page_count"] == 2
    assert head["canonical_char_count"] == len(evidence.text)
    assert head["canonical_sha256"] == evidence.manifest["canonical_sha256"]
    assert head["built_at"] is not None
    # The manifest never carries page text.
    assert NATIVE_ONE not in repr(head)


async def test_a_new_run_replaces_the_head_and_keeps_the_old_rows() -> None:
    db = FakeDb()
    document_id = await _seed_document(db)
    await _publish(_store(db, document_id, run="job-1"), [_page(1, "first extraction")])

    manifest = await _publish(_store(db, document_id, run="job-2"), [_page(1, "second extraction")])
    evidence = await get_document_canonical_evidence(db, document_id, current_user=SUPERADMIN, selection=_selected(db))

    assert manifest["extraction_run_id"] == "job-2"
    assert evidence.extraction_run_id == "job-2"
    assert evidence.text == "second extraction"
    assert evidence.canonical_revision == 2
    # Versioning, not replacement: the earlier run's evidence is still there.
    old = await db[DOCUMENT_OCR_PAGES].find({"document_id": document_id, "extraction_run_id": "job-1"}).to_list(None)
    assert [row["raw_text"] for row in old] == ["first extraction"]


async def test_failing_to_publish_evidence_fails_the_attempt_before_any_indexing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await RetryHarness.create(tmp_path, monkeypatch, page_lines=[[NATIVE_ONE]], script={})

    async def _head_write_fails(self, **kwargs: Any) -> dict:
        raise RuntimeError("mongo write concern timeout")

    monkeypatch.setattr(DocumentPageStore, "publish_canonical", _head_write_fails)

    completed = await harness.run_attempt()

    assert completed is False
    # Nothing downstream ran on evidence that could not be published.
    assert harness.saves() == []
    assert await _head(harness) is None


async def test_a_call_without_a_selection_is_refused_not_served_unscoped() -> None:
    db = FakeDb()
    document_id = await _published_in(db, ORG_A, A1)

    with pytest.raises(Exception) as raised:
        await get_document_canonical_evidence(db, document_id, current_user=SUPERADMIN, selection=None)
    assert getattr(raised.value, "http_status", None) == 400


async def test_a_deterministic_publish_mismatch_is_terminal_not_retried(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await RetryHarness.create(tmp_path, monkeypatch, page_lines=[[NATIVE_ONE], [NATIVE_TWO]], script={})

    async def _rows_disagree(self, **kwargs: Any) -> dict:
        raise CanonicalEvidenceInconsistentError(self.document_id, "persisted rows differ", pages=[2])

    monkeypatch.setattr(DocumentPageStore, "publish_canonical", _rows_disagree)

    await harness.run_attempt()
    job = await harness.job()

    assert job["processing_state"] == "human_review_required"
    assert job["status"] != "retrying"
    assert harness.saves() == []


async def test_a_concurrent_head_move_is_refused_not_overwritten() -> None:
    db = FakeDb()
    document_id = await _seed_document(db)
    store = _store(db, document_id)
    pages = [_page(1, "one")]
    await _publish(store, pages)
    original_find_one = db[DOCUMENT_EXTRACTION_HEADS].find_one

    async def _stale_read(query: dict, *args: Any, **kwargs: Any):
        head = await original_find_one(query, *args, **kwargs)
        # Another worker publishes between this read and the guarded write.
        await db[DOCUMENT_EXTRACTION_HEADS].update_one(
            {"document_id": document_id}, {"$set": {"canonical_revision": 99}}
        )
        return head

    db[DOCUMENT_EXTRACTION_HEADS].find_one = _stale_read

    with pytest.raises(CanonicalHeadConflictError):
        await store.publish_canonical(
            expected=assemble_canonical_document(pages), pipeline_version="unified_v1", source_sha256=None
        )


async def test_soft_deleted_document_evidence_is_retained_but_not_served() -> None:
    """Owner decision: deletion is logical; evidence is kept, never read."""
    db = FakeDb()
    document_id = await _published_in(db, ORG_A, A1)
    # Positive control: readable before deletion.
    before = await get_document_canonical_evidence(
        db, document_id, current_user=ADMIN_A1, selection=ActiveScope(db, ADMIN_A1, ORG_A, A1)
    )
    assert before.status == EVIDENCE_PUBLISHED

    # What DocumentService.delete_document writes.
    await db.documents.update_one(
        {"_id": ObjectId(document_id)}, {"$set": {"lifecycle_state": "deleted"}}
    )

    for user in (ADMIN_A1, SUPERADMIN):
        with pytest.raises(Exception) as raised:
            await get_document_canonical_evidence(
                db, document_id, current_user=user, selection=ActiveScope(db, user, ORG_A, A1)
            )
        # Same answer as GET /documents/{id} for a soft-deleted document.
        assert getattr(raised.value, "http_status", None) == 404
    # Retained, not purged.
    assert await db[DOCUMENT_OCR_PAGES].find({"document_id": document_id}).to_list(None)
    assert await db[DOCUMENT_EXTRACTION_HEADS].find_one({"document_id": document_id})
