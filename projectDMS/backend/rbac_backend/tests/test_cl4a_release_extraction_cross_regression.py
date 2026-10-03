"""CL-4A / Contract Master v1 against the release extraction line (PR #25, PR #28).

The two lines were built apart. CL-4A binds every core register to the navbar
project, and Contract Master resolves evidence through an authorised
(organisation, project) scope token and the fail-closed per-document authority
lookup over the ObjectId-keyed ``documents`` record. The release line made an
extraction run cumulative across retries (PR #28) and withholds unusable
``(cid:N)`` text (PR #25). Neither line's suite exercises the other's code, so
each would stay green if the join broke. These tests drive both at once:

* a retried document keeps the OCR text an earlier attempt resolved, its page
  rows stay in the document's organisation and project, and CL-4A's document
  read gate still holds it to the navbar selection (B, C, D);
* Contract Master evidence for that document runs through the real scope
  factory and resolver: the pair is proven before authorisation, the
  ObjectId-keyed document resolves from the string id the instrument holds,
  and the instrument is evidence only in its own project and only while the
  extraction verdict is not adverse (A, E);
* a run that never resolves a page ends in human review and Contract Master
  drops the instrument (E, F);
* the contract ingest path, which shares the extraction engine, never
  publishes a ``(cid:N)``-dominated page as contract text, and publishes the
  OCR that replaces it (B, F).

Only OCR, OpenAI and graph publication are stubbed (``retry_harness``). The
selection is an ``ActiveScope`` built directly; validating a selection against
real memberships is proven by ``integration/test_cl4a_core_active_scope_mongo``,
which this merge does not change.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, cast

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.core.tenant_context import ActiveScope, TenantContextError
from rbac_backend.models.contract_document import (
    ApplicabilityLifecycleKind,
    ContractDocumentType,
    ContractScopeLevel,
    ProjectionStatus,
)
from rbac_backend.routers import documents as documents_router
from rbac_backend.services import contracts_ingest as contracts_ingest_module
from rbac_backend.services.contract_scope_resolver import (
    APPLICABILITY_COLLECTION,
    APPLICABILITY_EVENTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
    ContractScopeResolver,
    CurrentState,
    authorize_contract_scope,
)
from rbac_backend.services.extraction import ocrmypdf_runner as ocrmypdf_runner_module
from rbac_backend.services.publication_policy import resolve_document_authority
from rbac_backend.services.scope_service import ScopeService
from rbac_backend.tests.fixtures.pdf_builders import build_composite_font_pdf
from rbac_backend.tests.retry_harness import RetryHarness
from rbac_backend.tests.test_document_processing_jobs import make_document

# The harness seeds its Document through make_document; read the tenancy from
# it rather than restating it, so a fixture change fails here legibly.
_SEED = make_document(ObjectId())
ORG = str(_SEED.organization_id)
PROJECT = str(_SEED.project_id)
OTHER_PROJECT = f"{PROJECT}-other"
OTHER_ORG = f"{ORG}-other"
FOREIGN_PROJECT = "project-of-another-org"
CONTRACT = "primary"

NATIVE_THREE = "NATIVE PAGE THREE"
OCR_ONE = "OCR PAGE ONE"
OCR_TWO = "OCR PAGE TWO"
_TWO_SCANS_ONE_NATIVE: List[Optional[List[str]]] = [None, None, [NATIVE_THREE]]


async def _partial_then_recovered(tmp_path: Path, monkeypatch: Any) -> RetryHarness:
    """Attempt 1 resolves pages 1 and 3 and fails page 2; attempt 2 fixes it.

    Attempt 2's script has no answer for page 1, so had the retry re-OCR'd the
    page attempt 1 resolved, its text would come back empty.
    """
    return await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=_TWO_SCANS_ONE_NATIVE,
        script={
            1: {1: OCR_ONE, 2: RuntimeError("OCRmyPDF batch failed (exit=2)")},
            2: {2: OCR_TWO},
        },
    )


async def _never_recovers(tmp_path: Path, monkeypatch: Any) -> RetryHarness:
    """Page 2 fails on every attempt, so the run exhausts its allowance."""
    failure = RuntimeError("OCRmyPDF batch failed (exit=2)")
    return await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=_TWO_SCANS_ONE_NATIVE,
        script={attempt: {1: OCR_ONE, 2: failure} for attempt in range(1, 8)},
    )


async def _run_until_terminal(harness: RetryHarness, limit: int = 6) -> Dict[str, Any]:
    for _ in range(limit):
        await harness.run_attempt()
        job = await harness.job()
        if job.get("processing_state") in {"completed", "human_review_required"}:
            return job
    raise AssertionError(f"no terminal state after {limit} attempts: {job}")


def _selection(db: Any, project_id: Optional[str]) -> ActiveScope:
    return ActiveScope(db, None, ORG, project_id)


def _use_db(monkeypatch: Any, db: Any) -> None:
    async def _get_database() -> Any:
        return db

    monkeypatch.setattr(documents_router, "get_database", _get_database)


async def _seed_contract_master(db: Any, document_id: ObjectId) -> None:
    """One project-scoped instrument over the processed Document, as promotion leaves it.

    The instrument holds the Document id as a string - the form every
    consumer carries - while ``documents._id`` is an ObjectId.
    """
    await db.projects.insert_one({"_id": PROJECT, "organization_id": ORG})
    await db.projects.insert_one({"_id": OTHER_PROJECT, "organization_id": ORG})
    await db.projects.insert_one({"_id": FOREIGN_PROJECT, "organization_id": OTHER_ORG})
    await db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
        {
            "_id": "cd-1",
            "organization_id": ORG,
            "document_id": str(document_id),
            "scope_level": ContractScopeLevel.PROJECT.value,
            "scope_project_id": PROJECT,
            "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS.value,
            "classification_revision": 1,
            "projection_revision": 1,
            "projection_status": ProjectionStatus.CURRENT.value,
        }
    )
    await db[APPLICABILITY_COLLECTION].insert_one(
        {
            "_id": "app-1",
            "organization_id": ORG,
            "contract_document_id": "cd-1",
            "project_id": PROJECT,
            "contract_id": CONTRACT,
        }
    )
    await db[APPLICABILITY_EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-1",
            "event_id": "evt-1",
            "applicability_id": "app-1",
            "kind": ApplicabilityLifecycleKind.APPLIED.value,
            "effective_at": "2026-01-01",
        }
    )


class _Policy:
    """PolicyService's surface authorize_contract_scope uses, over the real ScopeService."""

    def __init__(self, db: Any) -> None:
        self.scope_service = ScopeService(db)
        self.authorized: List[Dict[str, Any]] = []

    async def authorize(self, user: Any, permission: str, **scope: Any) -> None:
        self.authorized.append({"permission": permission, **scope})


async def _evidence(db: Any, policy: _Policy, organization_id: str, project_id: str):
    token = await authorize_contract_scope(
        policy,
        SimpleNamespace(id="user-1"),
        permission="dms.contract.clause.read",
        organization_id=organization_id,
        project_id=project_id,
        contract_id=CONTRACT,
        audit=False,
    )
    return await ContractScopeResolver(db).resolve(token, CurrentState())


# --- B, C, D: a retried document keeps its text and its scope ---------------


async def test_retried_document_keeps_resolved_ocr_and_its_project_scope(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)

    assert await harness.run_attempt() is False
    assert await harness.run_attempt() is True

    document = await harness.document()
    pages = await harness.page_rows()

    # PR #28: attempt 1's OCR survives attempt 2, and the published text is the
    # whole run in page order.
    assert document["processing_status"] == "completed"
    full_text = harness.full_text()
    assert full_text.index(OCR_ONE) < full_text.index(OCR_TWO) < full_text.index(
        NATIVE_THREE
    )
    assert OCR_ONE in harness.embedding_text()

    # Every page row the run wrote or carried stays in the document's tenancy.
    assert [row["page_number"] for row in pages] == [1, 2, 3]
    assert {(row["organization_id"], row["project_id"]) for row in pages} == {
        (ORG, PROJECT)
    }
    assert {row["document_id"] for row in pages} == {str(harness.document_id)}

    # CL-4A's document read gate, over the id form a URL carries.
    _use_db(monkeypatch, harness.db)
    document_id = str(harness.document_id)
    await documents_router._hold_document(_selection(harness.db, PROJECT), document_id)

    with pytest.raises(TenantContextError) as other:
        await documents_router._hold_document(
            _selection(harness.db, OTHER_PROJECT), document_id
        )
    assert other.value.status_code == 403

    with pytest.raises(TenantContextError) as unselected:
        await documents_router._hold_document(_selection(harness.db, None), document_id)
    assert unselected.value.status_code == 400


# --- A, E: Contract Master evidence over the processed document -------------


async def test_contract_master_evidence_resolves_the_retried_document_in_its_project(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    policy = _Policy(harness.db)

    await harness.run_attempt()
    # Between attempts the run is partial and last-known-good stands; the
    # checkpoint must not have turned it into an adverse verdict.
    mid = await resolve_document_authority(harness.db, str(harness.document_id))
    assert (mid.consumable, mid.reason) == (True, "in_flight_last_known_good")

    assert await harness.run_attempt() is True
    # Promoted once extracted, as the migration does. From here the general
    # pipeline no longer writes this document (see the governed test below).
    await _seed_contract_master(harness.db, harness.document_id)

    decision = await resolve_document_authority(harness.db, str(harness.document_id))
    assert (decision.consumable, decision.reason) == (True, "settled")

    resolved = await _evidence(harness.db, policy, ORG, PROJECT)
    assert [i.document_id for i in resolved.instruments] == [str(harness.document_id)]
    assert resolved.eligible_document_ids == frozenset({str(harness.document_id)})
    assert {(i.organization_id, i.project_id) for i in resolved.instruments} == {
        (ORG, PROJECT)
    }

    # Another project of the same organisation is a valid pair with no evidence.
    sibling = await _evidence(harness.db, policy, ORG, OTHER_PROJECT)
    assert sibling.instruments == () and sibling.eligible_document_ids == frozenset()

    # A pair naming another organisation's project is refused before the
    # policy is even asked, so it cannot be audited as an allow.
    asked = len(policy.authorized)
    with pytest.raises(HTTPException) as refused:
        await _evidence(harness.db, policy, ORG, FOREIGN_PROJECT)
    assert refused.value.status_code == 403
    assert len(policy.authorized) == asked


async def test_unrecoverable_page_goes_to_review_and_leaves_contract_evidence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _never_recovers(tmp_path, monkeypatch)
    policy = _Policy(harness.db)

    job = await _run_until_terminal(harness)
    await _seed_contract_master(harness.db, harness.document_id)
    document = await harness.document()
    assert job["processing_state"] == "human_review_required"
    assert document["processing_status"] == "human_review_required"

    # The page an earlier attempt resolved is still on record for the reviewer.
    pages = {row["page_number"]: row for row in await harness.page_rows()}
    assert (pages[1]["raw_text"], pages[1]["status"]) == (OCR_ONE, "ocr_completed")

    decision = await resolve_document_authority(harness.db, str(harness.document_id))
    assert (decision.consumable, decision.reason) == (False, "adverse_quality_judgement")

    resolved = await _evidence(harness.db, policy, ORG, PROJECT)
    assert resolved.instruments == ()
    assert resolved.eligible_document_ids == frozenset()

    _use_db(monkeypatch, harness.db)
    with pytest.raises(TenantContextError) as other:
        await documents_router._hold_document(
            _selection(harness.db, OTHER_PROJECT), str(harness.document_id)
        )
    assert other.value.status_code == 403


async def test_a_governed_document_is_not_reprocessed_by_the_general_pipeline(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Once an instrument governs the Document, a general extraction job is
    closed at once with nothing written: it would rewrite the evidence rows
    under a CURRENT projection. It is not retried into dead-letter either."""
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await _seed_contract_master(harness.db, harness.document_id)
    before = await harness.document()

    assert await harness.run_attempt() is False
    job = await harness.job()
    assert (job["status"], job["stage"]) == ("dead_lettered", "skipped_governed_contract")
    assert job["error"]["terminal"] is True
    assert await harness.document() == before, "the refused job wrote to the document"
    assert await harness.page_rows() == []


# --- B, F: the contract ingest path and (cid:N) text ------------------------

_CLAUSE_BODY = [
    "Clause 14.3 Application for Interim Payment Certificates shall be made",
    "monthly in the form approved by the Engineer with supporting documents",
    "and the Engineer shall within twenty eight days issue the certificate.",
]
_OCR_CLAUSE = "Clause 14.3 Application for Interim Payment Certificates (OCR)"


class _FakeContractDbService:
    def __init__(self) -> None:
        self.batches: List[Dict[str, Any]] = []
        self.pages: List[List[Dict[str, Any]]] = []

    async def upsert_ocr_batch(self, **kwargs: Any) -> str:
        self.batches.append(dict(kwargs))
        return f"batch-{len(self.batches)}"

    async def upsert_ocr_pages(self, records: List[Dict[str, Any]]) -> None:
        self.pages.append(list(records))


class _ScriptedContractOcr:
    """Stands in for OcrMyPdfRunner inside contract ingest."""

    requested: List[List[int]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        type(self).requested.append(list(page_numbers))
        return {page: _OCR_CLAUSE for page in page_numbers}


async def _noop(**_: Any) -> None:
    return None


def _contract_ingestor(
    tmp_path: Path, monkeypatch: Any, db_service: Any, *, ocr_enabled: bool
):
    from rbac_backend.config.document_processing_config import (
        DocumentProcessingConfig,
    )

    monkeypatch.setattr(contracts_ingest_module, "BASE_UPLOAD_PATH", tmp_path)
    config = DocumentProcessingConfig()
    config.ocr_enabled = ocr_enabled
    ingestor = contracts_ingest_module.ContractIngestor.__new__(
        contracts_ingest_module.ContractIngestor
    )
    ingestor.processing_config = config
    ingestor.db_service = db_service
    ingestor.usage_metering_service = cast(Any, SimpleNamespace(check_and_record=_noop))
    return ingestor


_CID_PLACEHOLDER = re.compile(r"\(cid:\d+\)")


def _record_strings(records: Sequence[Dict[str, Any]]) -> List[str]:
    return [value for record in records for value in record.values() if isinstance(value, str)]


async def _extract(ingestor: Any, source: Path, document_id: str):
    return await ingestor._extract_pdf_pages_with_ocr_batches(
        source,
        upload_id=f"upload-{document_id}",
        document_id=document_id,
        organization_id=ORG,
        project_id=PROJECT,
    )


async def test_contract_ingest_withholds_a_cid_page_when_nothing_can_replace_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = build_composite_font_pdf(tmp_path / "contract.pdf", composite_lines=_CLAUSE_BODY)
    ingestor = _contract_ingestor(
        tmp_path, monkeypatch, _FakeContractDbService(), ocr_enabled=False
    )

    parsed, records = await _extract(ingestor, source, "contract-doc-1")

    # The layer was detected and refused - not silently lost: the page is
    # recorded as needing OCR that is switched off, with the reason.
    assert [(r["page_number"], r["status"]) for r in records] == [(1, "ocr_disabled")]
    assert "(cid:N)" in str(records[0]["error"])
    # ...and none of it is the text clauses, chunks and evidence are cut from.
    assert parsed.text == ""
    assert not any(_CID_PLACEHOLDER.search(v) for v in _record_strings(records))
    assert {(r["organization_id"], r["project_id"]) for r in records} == {(ORG, PROJECT)}


async def test_contract_ingest_publishes_the_ocr_that_replaces_a_cid_page(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _ScriptedContractOcr.requested = []
    monkeypatch.setattr(ocrmypdf_runner_module, "OcrMyPdfRunner", _ScriptedContractOcr)
    source = build_composite_font_pdf(tmp_path / "contract.pdf", composite_lines=_CLAUSE_BODY)
    ingestor = _contract_ingestor(
        tmp_path, monkeypatch, _FakeContractDbService(), ocr_enabled=True
    )

    parsed, records = await _extract(ingestor, source, "contract-doc-2")

    assert _ScriptedContractOcr.requested == [[1]]
    assert [(r["page_number"], r["status"]) for r in records] == [(1, "ocr_completed")]
    assert _OCR_CLAUSE in parsed.text
    assert not _CID_PLACEHOLDER.search(parsed.text)
    assert not any(_CID_PLACEHOLDER.search(v) for v in _record_strings(records))


async def test_contract_ingest_keeps_a_readable_page_with_one_unmapped_glyph(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The control: withholding is for CID-dominated pages, not any placeholder."""
    source = build_composite_font_pdf(
        tmp_path / "contract.pdf", text_lines=_CLAUSE_BODY, composite_lines=["*"]
    )
    ingestor = _contract_ingestor(
        tmp_path, monkeypatch, _FakeContractDbService(), ocr_enabled=False
    )

    parsed, records = await _extract(ingestor, source, "contract-doc-3")

    assert [(r["page_number"], r["status"]) for r in records] == [(1, "text_layer")]
    assert "Interim Payment Certificates" in parsed.text
