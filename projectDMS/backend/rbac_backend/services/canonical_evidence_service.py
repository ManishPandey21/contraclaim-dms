"""Authorised read access to a document's canonical evidence.

The one internal entry point for any workflow that needs a document's complete
extracted text - drafting, claim preparation, SOC/SOD/Rejoinder, chronology,
Matter Context Packs. It answers from page evidence in Mongo alone: no Qdrant,
no embedding provider, no LLM, and never a reconstruction from vector hits.

Source of truth: the ``document_ocr_pages`` rows of the run named by the
document's ``document_extraction_heads`` entry. The head carries a manifest
(page map, per-page checksums, the canonical checksum, counts, pipeline
version, source checksum) written by ``DocumentPageStore.publish_canonical``.
Every read reassembles the text from the rows with the single assembly rule in
``extraction/canonical.py`` and verifies it against that manifest; any
disagreement raises instead of serving text whose provenance cannot be vouched
for.

Access is bounded exactly like ``GET /documents/{id}``: the navbar selection
(when given) holds the record, then ``PolicyService.authorize_document`` with
``dms.document.view`` decides. There is deliberately no unscoped variant.

Acceptance criteria this interface is designed to keep (not implemented here):

* A Matter Context Pack loads the complete text of a target letter, its
  referenced letters and relevant enclosures by document id through this
  function - never by assembling top-k vector chunks.
* A future chronology event cites ``document_id`` + ``page_number`` + a
  canonical character span (``CanonicalEvidence.page_at`` /
  ``page_text``), checked against ``canonical_sha256`` and
  ``canonical_revision`` so a citation made against an older revision is
  detectable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import status

from ..core.permissions import Permissions
from ..utils.error_handler import DocumentError
from .extraction.canonical import (
    DuplicatePageError,
    assemble_canonical_document,
)
from .extraction_adapters.document_page_store import (
    DOCUMENT_EXTRACTION_HEADS,
    DOCUMENT_OCR_PAGES,
    CanonicalEvidenceInconsistentError,
    MalformedPageRecordError,
    from_document_page_record,
)
from .publication_policy import is_consumable

logger = logging.getLogger(__name__)

__all__ = [
    "EVIDENCE_NOT_BUILT",
    "EVIDENCE_PUBLISHED",
    "CanonicalEvidence",
    "CanonicalEvidenceInconsistentError",
    "PageEvidence",
    "get_document_canonical_evidence",
]

#: A head exists and its rows verified against the manifest.
EVIDENCE_PUBLISHED = "published"
#: No canonical evidence has been published for this document: it predates
#: this store, or ran the legacy pipeline (no page evidence at all).
EVIDENCE_NOT_BUILT = "not_built"


@dataclass(frozen=True)
class PageEvidence:
    """One source page: where it sits in the canonical text, and its evidence."""

    page_number: int
    start: int
    end: int
    status: str
    source: str
    page_class: str
    quality_verdict: Optional[str]
    quality_checks: List[Dict[str, Any]]
    needs_review: bool
    text_withheld: bool
    error: Optional[str]
    #: What extraction read before a deterministic repair, or the unusable
    #: text that was withheld. None when it equals the canonical page text.
    original_text: Optional[str]
    applied_repairs: List[Dict[str, Any]]
    tables: List[List[List[str]]]
    batch_id: Optional[str]


@dataclass(frozen=True)
class CanonicalEvidence:
    document_id: str
    organization_id: Optional[str]
    project_id: Optional[str]
    status: str
    #: ``publication_policy.is_consumable`` for the document. Evidence is
    #: served for review even when this is False (human review, quarantine,
    #: deletion); a drafting/claim consumer must honour it before relying on
    #: the text as authoritative.
    publication_consumable: bool
    text: Optional[str] = None
    pages: List[PageEvidence] = field(default_factory=list)
    manifest: Dict[str, Any] = field(default_factory=dict)
    extraction_run_id: Optional[str] = None
    canonical_revision: Optional[int] = None
    #: False when the document's recorded checksum differs from the bytes the
    #: evidence was extracted from; None when either side is unknown.
    source_matches_document: Optional[bool] = None
    #: For EVIDENCE_NOT_BUILT only: the document's stored ``ocrText`` and its
    #: label. Offered so a caller can decide, never presented as canonical:
    #: it has no page map and, unlabelled, may be an extraction report.
    legacy_text: Optional[str] = None
    legacy_text_kind: Optional[str] = None

    def page(self, page_number: int) -> PageEvidence:
        for page in self.pages:
            if page.page_number == page_number:
                return page
        raise KeyError(page_number)

    def page_text(self, page_number: int) -> str:
        page = self.page(page_number)
        return (self.text or "")[page.start : page.end]

    def page_at(self, offset: int) -> Optional[int]:
        for page in self.pages:
            if page.start <= offset < page.end:
                return page.page_number
        return None


async def _find_document(db: Any, document_id: str) -> Optional[Dict[str, Any]]:
    candidates: List[Any] = [document_id]
    try:
        candidates.append(ObjectId(document_id))
    except Exception:
        pass
    return await db.documents.find_one({"_id": {"$in": candidates}})


async def get_document_canonical_evidence(
    db: Any,
    document_id: str,
    *,
    current_user: Any,
    selection: Any = None,
    policy: Any = None,
) -> CanonicalEvidence:
    """The complete canonical evidence of one document the caller may view."""
    from .policy_service import PolicyService

    if selection is not None:
        selection.require_selection()
    document = await _find_document(db, str(document_id))
    if not document:
        raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
    if selection is not None:
        await selection.require_record(document, allow_unscoped=True)
    await (policy or PolicyService(db)).authorize_document(
        current_user, Permissions.DOCUMENT_VIEW, document
    )
    return await _read_canonical_evidence(db, document)


def _scope_value(value: Any) -> Optional[str]:
    return str(value) if value else None


async def _read_canonical_evidence(db: Any, document: Dict[str, Any]) -> CanonicalEvidence:
    """Assemble and verify. Only ever reached after authorisation."""
    document_id = str(document["_id"])
    organization_id = _scope_value(document.get("organization_id") or document.get("organizationId"))
    project_id = _scope_value(document.get("project_id") or document.get("projectId"))

    head = await db[DOCUMENT_EXTRACTION_HEADS].find_one({"document_id": document_id})
    if not head or not head.get("canonical_sha256"):
        return CanonicalEvidence(
            document_id=document_id,
            organization_id=organization_id,
            project_id=project_id,
            status=EVIDENCE_NOT_BUILT,
            publication_consumable=is_consumable(document),
            legacy_text=document.get("ocrText"),
            legacy_text_kind=document.get("ocr_text_kind"),
        )

    def _inconsistent(reason: str, **details: Any) -> CanonicalEvidenceInconsistentError:
        logger.error(
            "[canonical_evidence] document %s run %s: %s %s",
            document_id,
            head.get("extraction_run_id"),
            reason,
            details,
        )
        return CanonicalEvidenceInconsistentError(document_id, reason, **details)

    # The evidence must belong to the tenant the caller was authorised for.
    if _scope_value(head.get("organization_id")) != organization_id or (
        _scope_value(head.get("project_id")) != project_id
    ):
        raise _inconsistent("evidence scope differs from the document's scope")

    expected_pages = [int(n) for n in head.get("page_numbers") or []]
    wanted = set(expected_pages)
    records = [
        record
        for record in await db[DOCUMENT_OCR_PAGES]
        .find(
            {
                "document_id": document_id,
                "extraction_run_id": head.get("extraction_run_id"),
            }
        )
        .to_list(length=None)
        if int(record.get("page_number") or 0) in wanted
    ]
    for record in records:
        if _scope_value(record.get("organization_id")) != organization_id:
            raise _inconsistent("page row scope differs from the document's scope")
    try:
        pages = [from_document_page_record(record) for record in records]
        canonical = assemble_canonical_document(pages)
    except (DuplicatePageError, MalformedPageRecordError) as exc:
        raise _inconsistent("page rows cannot be assembled", error=str(exc)) from exc
    if canonical.page_numbers != expected_pages:
        raise _inconsistent(
            "page rows missing for the published run",
            expected_pages=expected_pages,
            persisted_pages=canonical.page_numbers,
        )
    if canonical.sha256 != head.get("canonical_sha256"):
        raise _inconsistent(
            "page rows changed after the manifest was published",
            expected_sha256=head.get("canonical_sha256"),
            persisted_sha256=canonical.sha256,
        )

    by_number = {page.number: page for page in pages}
    page_evidence = [
        PageEvidence(
            page_number=span.page_number,
            start=span.start,
            end=span.end,
            status=span.status,
            source=span.source,
            page_class=span.page_class,
            quality_verdict=span.quality_verdict,
            quality_checks=list(by_number[span.page_number].quality_checks),
            needs_review=span.needs_review,
            text_withheld=span.text_withheld,
            error=by_number[span.page_number].error,
            original_text=by_number[span.page_number].raw_text,
            applied_repairs=list(by_number[span.page_number].applied_repairs),
            tables=by_number[span.page_number].tables,
            batch_id=by_number[span.page_number].batch_id,
        )
        for span in canonical.page_map
    ]

    recorded_source = document.get("sha256")
    source_sha = head.get("source_sha256")
    manifest = {key: value for key, value in head.items() if key != "_id"}
    return CanonicalEvidence(
        document_id=document_id,
        organization_id=organization_id,
        project_id=project_id,
        status=EVIDENCE_PUBLISHED,
        publication_consumable=is_consumable(document),
        text=canonical.text,
        pages=page_evidence,
        manifest=manifest,
        extraction_run_id=head.get("extraction_run_id"),
        canonical_revision=head.get("canonical_revision"),
        source_matches_document=(
            recorded_source == source_sha if recorded_source and source_sha else None
        ),
    )
