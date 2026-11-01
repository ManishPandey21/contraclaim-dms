"""Human review of extraction pages the quality gate held back (Release 1.1.1).

Before this module, ``human_review_required`` was a dead end: the quality gate
withheld embeddings, purged stale vectors and graph contribution, and nothing
could ever release the document again short of reprocessing it into the same
verdict.

What a reviewer decides, and what it changes
--------------------------------------------

* The machine evidence is never rewritten. ``document_ocr_pages`` keeps its
  ``quality_verdict`` / ``needs_review`` / ``quality_checks`` and the canonical
  head keeps its manifest (``review_pages`` included). A human approval resolves
  the blocking condition; it does not claim the automated gate passed.
* Every decision is one append-only row in ``document_page_reviews``, bound to
  the exact evidence it was made on: ``extraction_run_id``,
  ``canonical_revision``, ``canonical_sha256`` and the page's own
  ``content_sha256``. A decision against any other evidence is refused (409),
  and a decision recorded against an older revision simply does not apply to
  a newer one - reprocessing can never inherit an approval.
* Concurrency is settled by a unique index on
  ``(document_id, canonical_revision, page_number, decision_version)``: two
  reviewers deciding the same page from the same state cannot both win.

Resolution
----------

When every blocking page of the CURRENT revision has an approval as its latest
decision, :meth:`ExtractionReviewService.resolve_review` (the single
``finalize_document_review`` entry point) moves the document out of the adverse
state, in this order, each step guarded so a crash leaves a resumable state:

1. re-read and re-verify the canonical evidence (rows reassemble to the head);
2. stamp ``review_resolution`` on the canonical head, guarded on the revision
   and checksum it approves (``canonical_revision`` is NOT incremented: the
   evidence text did not change, and the revision is what citations pin);
3. move the document ``human_review_required -> partially_processed`` with
   stage ``review_resolved_publication_pending``. ``partially_processed`` is
   consumable under the publication policy, which is exactly right: the content
   is now authoritative, and only indexing is outstanding.

:meth:`ExtractionReviewService.publish_resolved` then reuses the existing
publication paths - ``DatabaseService.create_embeddings_for_document`` for the
deferred vectors and ``DocumentService._publish_graph_and_evidence_from_current``
for graph and evidence - and ends in ``completed`` only when indexing finished,
otherwise in ``partially_processed`` with the failing stage named. It never
returns a document to human review for an operational failure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from fastapi import status
from pymongo.errors import DuplicateKeyError

from ..core.permissions import Permissions
from ..models.processing_state import ProcessingState
from ..utils.error_handler import DocumentError
from .canonical_evidence_service import (
    EVIDENCE_PUBLISHED,
    CanonicalEvidence,
    _read_canonical_evidence,
)
from .extraction_adapters.document_page_store import (
    DOCUMENT_EXTRACTION_HEADS,
    CanonicalEvidenceInconsistentError,
)
from .publication_policy import (
    document_id_candidates,
    held_by_contract_extraction,
)

logger = logging.getLogger(__name__)

DOCUMENT_PAGE_REVIEWS = "document_page_reviews"

ACTION_APPROVE = "approved_as_is"
ACTION_REJECT = "rejected"
ACTIONS = frozenset({ACTION_APPROVE, ACTION_REJECT})

#: Job/document stage while a resolved review waits for its indexing.
STAGE_PUBLICATION_PENDING = "review_resolved_publication_pending"
#: Same stage name `DocumentService._mark_indexing_incomplete` writes, so the
#: client already treats it as a settled, non-resuming verdict.
STAGE_INDEXING_INCOMPLETE = "indexing_incomplete"

REVIEW_REQUIRED = "required"
REVIEW_PUBLICATION_PENDING = "publication_pending"
REVIEW_RESOLVED = "resolved"
REVIEW_NOT_REQUIRED = "not_required"

#: ``vector_sync_status.sync_status`` values that mean indexing did not finish.
#: The same set `DocumentService` uses to refuse `completed`.
_UNFINISHED_VECTOR_SYNC = frozenset({"error", "mismatch", "pending", "deferred", "unknown"})

_MAX_COMMENT = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _conflict(message: str, **extra: Any) -> DocumentError:
    return DocumentError(message, status.HTTP_409_CONFLICT, details=extra or None)


def _actor_id(current_user: Any) -> Optional[str]:
    for attr in ("id", "user_id", "_id"):
        value = getattr(current_user, attr, None)
        if value:
            return str(value)
    return None


def _actor_label(current_user: Any) -> Optional[str]:
    for attr in ("email", "username"):
        value = getattr(current_user, attr, None)
        if value:
            return str(value)
    return None


def _ints(values: Any) -> List[int]:
    out: List[int] = []
    for value in values or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def blocking_pages(document: Mapping[str, Any], head: Mapping[str, Any]) -> List[int]:
    """Every page that must be resolved before the document may be released.

    The union of what the document's verdict named and what the canonical head
    recorded, so a page cannot drop out of review because one of the two
    writers missed it.
    """
    error = document.get("processing_error")
    named = _ints(error.get("pages")) if isinstance(error, Mapping) else []
    if isinstance(error, Mapping):
        named = sorted(
            set(named)
            | set(_ints(error.get("quality_review_pages")))
            | set(_ints(error.get("unresolved_pages")))
        )
    resolution = document.get("extraction_review")
    if isinstance(resolution, Mapping):
        # After resolution the verdict's own record moved here.
        machine = resolution.get("machine_verdict")
        if isinstance(machine, Mapping):
            named = sorted(set(named) | set(_ints(machine.get("pages"))))
    return sorted(
        set(named) | set(_ints(head.get("review_pages"))) | set(_ints(head.get("unresolved_pages")))
    )


def _failed_checks(checks: Any) -> List[Dict[str, Any]]:
    """The machine's reasons, short: never page text."""
    out: List[Dict[str, Any]] = []
    for check in checks or []:
        if not isinstance(check, Mapping):
            continue
        verdict = str(check.get("verdict") or "")
        if verdict in {"pass", "passed", "ok"}:
            continue
        out.append(
            {
                "name": check.get("name"),
                "verdict": verdict or None,
                "detail": (str(check.get("detail"))[:500] if check.get("detail") else None),
            }
        )
    return out


def _span(head: Mapping[str, Any], page_number: int) -> Optional[Mapping[str, Any]]:
    for span in head.get("page_map") or []:
        if int(span.get("page_number") or 0) == int(page_number):
            return span
    return None


def _public_decision(row: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "id": str(row.get("_id")),
        "page_number": row.get("page_number"),
        "action": row.get("action"),
        "decision_version": row.get("decision_version"),
        "canonical_revision": row.get("canonical_revision"),
        "page_checksum": row.get("page_sha256"),
        "comment": row.get("comment"),
        "reviewed_by": row.get("reviewed_by"),
        "reviewed_by_label": row.get("reviewed_by_label"),
        "reviewed_at": row.get("reviewed_at"),
        "machine_verdict": row.get("machine_verdict"),
        "machine_needs_review": row.get("machine_needs_review"),
    }


class ExtractionReviewService:
    """The one domain entry point for extraction review. Routers stay thin."""

    def __init__(
        self,
        db: Any,
        *,
        policy: Any = None,
        database_service_factory: Optional[Callable[[], Any]] = None,
        document_service_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.db = db
        self._policy = policy
        self._database_service_factory = database_service_factory
        self._document_service_factory = document_service_factory

    # ------------------------------------------------------------------
    # dependencies
    # ------------------------------------------------------------------
    def _policy_service(self) -> Any:
        if self._policy is None:
            from .policy_service import PolicyService

            self._policy = PolicyService(self.db)
        return self._policy

    def _database_service(self) -> Any:
        if self._database_service_factory is not None:
            return self._database_service_factory()
        from ..config.document_processing_config import DocumentProcessingConfig
        from .database_service import DatabaseService

        service = DatabaseService(DocumentProcessingConfig())
        # Share this request's database rather than opening a second client
        # against configuration (tests and staging point it elsewhere).
        service._db = self.db
        return service

    def _document_service(self) -> Any:
        if self._document_service_factory is not None:
            return self._document_service_factory()
        from .document_service import DocumentService

        return DocumentService(self.db)

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    async def _find_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        for key in document_id_candidates(document_id):
            found = await self.db.documents.find_one({"_id": key})
            if found:
                return found
        return None

    async def _authorised_document(
        self, document_id: str, current_user: Any, selection: Any, permission: str
    ) -> Dict[str, Any]:
        """Selection first, then the policy - exactly GET /documents/{id}'s order."""
        if selection is None:
            raise DocumentError("An active scope selection is required", status.HTTP_400_BAD_REQUEST)
        selection.require_selection()
        document = await self._find_document(str(document_id))
        # Logical deletion: a deleted document is not found here, as everywhere.
        if not document or document.get("lifecycle_state") == "deleted":
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
        await selection.require_record(document, allow_unscoped=True)
        await self._policy_service().authorize_document(current_user, permission, document)
        return document

    async def _evidence(self, document: Dict[str, Any]) -> CanonicalEvidence:
        try:
            return await _read_canonical_evidence(self.db, document)
        except CanonicalEvidenceInconsistentError as exc:
            raise _conflict(
                "The extraction evidence for this document cannot be verified; it must be reprocessed",
                reason=exc.reason,
            ) from exc

    async def _latest_decisions(
        self, document_id: str, canonical_revision: Any
    ) -> Dict[int, Dict[str, Any]]:
        latest: Dict[int, Dict[str, Any]] = {}
        cursor = self.db[DOCUMENT_PAGE_REVIEWS].find(
            {"document_id": document_id, "canonical_revision": canonical_revision}
        )
        for row in await cursor.to_list(length=None):
            page = int(row.get("page_number") or 0)
            if page not in latest or int(row.get("decision_version") or 0) > int(
                latest[page].get("decision_version") or 0
            ):
                latest[page] = row
        return latest

    async def _can_review(self, current_user: Any, document: Mapping[str, Any]) -> bool:
        try:
            await self._policy_service().authorize_document(
                current_user, Permissions.DOCUMENT_REVIEW_EXTRACTION, document
            )
        except Exception:
            return False
        return True

    # ------------------------------------------------------------------
    # eligibility
    # ------------------------------------------------------------------
    async def _refuse_ineligible(self, document: Mapping[str, Any]) -> None:
        """Quarantine and contract ownership outrank any review decision."""
        if str(document.get("duplicate_status") or "") == "duplicate" or str(
            document.get("lifecycle_state") or ""
        ) in {"duplicate", "duplicate_review"}:
            raise _conflict("This document is quarantined as a duplicate; review cannot release it")
        if str(document.get("duplicate_status") or "") == "pending":
            raise _conflict("This document's duplicate check is still pending")
        if held_by_contract_extraction(document):
            raise _conflict("This document is held by contract extraction; reprocess it through the contract reindex")
        from .contract_source import is_contract_source

        if await is_contract_source(self.db, str(document["_id"]), document):
            raise _conflict("This document is a contract source; its extraction is reviewed through the contract path")

    def _bound_head(self, document: Mapping[str, Any], evidence: CanonicalEvidence) -> None:
        if evidence.status != EVIDENCE_PUBLISHED:
            raise _conflict(
                "This document has no canonical page evidence to review; reprocess it with page extraction"
            )
        job_id = document.get("processing_job_id")
        if job_id and evidence.extraction_run_id and str(evidence.extraction_run_id) != str(job_id):
            raise _conflict(
                "The published evidence does not belong to the document's latest run; reprocess it"
            )

    @staticmethod
    def review_state(document: Mapping[str, Any], head: Mapping[str, Any]) -> str:
        status_value = str(document.get("processing_status") or "")
        resolution = document.get("extraction_review") or {}
        resolved_here = (
            isinstance(resolution, Mapping)
            and resolution.get("status") == REVIEW_RESOLVED
            and resolution.get("canonical_revision") == head.get("canonical_revision")
            and resolution.get("canonical_sha256") == head.get("canonical_sha256")
        )
        if status_value == ProcessingState.HUMAN_REVIEW_REQUIRED.value:
            return REVIEW_REQUIRED
        if resolved_here and status_value == ProcessingState.PARTIALLY_PROCESSED.value:
            return REVIEW_PUBLICATION_PENDING
        if resolved_here:
            return REVIEW_RESOLVED
        return REVIEW_NOT_REQUIRED

    # ------------------------------------------------------------------
    # queue
    # ------------------------------------------------------------------
    async def list_queue(
        self, current_user: Any, selection: Any, *, skip: int = 0, limit: int = 50
    ) -> Dict[str, Any]:
        """Documents awaiting extraction review, bounded like the documents register."""
        from .authorization_service import AuthorizationService, AuthorizationError

        organization_id, project_id = await selection.list_filters(None, None)
        roles = {str(role).lower() for role in (getattr(current_user, "roles", None) or [])}
        policy = self._policy_service()
        await policy.authorize(
            current_user,
            Permissions.DOCUMENT_VIEW,
            resource_type="documents",
            organization_id=organization_id or getattr(current_user, "organization_id", None),
            project_id=project_id,
            audit=False,
        )
        try:
            query = await AuthorizationService().build_document_query(
                current_user, {"organization_id": organization_id, "project_id": project_id}
            )
        except AuthorizationError as exc:
            raise DocumentError(str(exc), status.HTTP_403_FORBIDDEN) from exc
        if selection.has_project and roles & {"orgadmin", "orguser", "superadmin"} and isinstance(
            query.get("project_id"), str
        ):
            # The register's CL-4A rule: the selected project plus the selected
            # organisation's organisation-level documents, never another project.
            query["project_id"] = {"$in": [query["project_id"], "", None]}
        query.update(
            {
                "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
                "lifecycle_state": {"$nin": ["deleted", "duplicate", "duplicate_review"]},
                "duplicate_status": {"$ne": "duplicate"},
            }
        )
        projection = {
            "filename": 1, "subject": 1, "letterNo": 1, "organization_id": 1, "project_id": 1,
            "processing_status": 1, "processing_error": 1, "processing_job_id": 1,
            "createdAt": 1, "created_at": 1, "updatedAt": 1,
        }
        bounded = min(max(int(limit or 50), 1), 200)
        total = await self.db.documents.count_documents(query)
        rows = (
            await self.db.documents.find(query, projection)
            .sort("updatedAt", -1)
            .skip(max(int(skip or 0), 0))
            .limit(bounded)
            .to_list(length=bounded)
        )
        items = []
        for row in rows:
            error = row.get("processing_error") if isinstance(row.get("processing_error"), Mapping) else {}
            pages = blocking_pages(row, {})
            items.append(
                {
                    "document_id": str(row["_id"]),
                    "filename": row.get("filename"),
                    "subject": row.get("subject"),
                    "letter_no": row.get("letterNo"),
                    "organization_id": row.get("organization_id"),
                    "project_id": row.get("project_id"),
                    "processing_status": row.get("processing_status"),
                    "review_pages": pages,
                    "first_review_page": pages[0] if pages else None,
                    "review_reason": error.get("message"),
                    "uploaded_at": row.get("createdAt") or row.get("created_at"),
                    "updated_at": row.get("updatedAt"),
                }
            )
        return {"items": items, "total": total, "skip": skip, "limit": bounded}

    # ------------------------------------------------------------------
    # detail
    # ------------------------------------------------------------------
    async def get_review(self, document_id: str, current_user: Any, selection: Any) -> Dict[str, Any]:
        document = await self._authorised_document(
            document_id, current_user, selection, Permissions.DOCUMENT_VIEW
        )
        return await self._review_payload(document, current_user)

    async def _review_payload(self, document: Dict[str, Any], current_user: Any) -> Dict[str, Any]:
        doc_id = str(document["_id"])
        evidence = await self._evidence(document)
        base: Dict[str, Any] = {
            "document_id": doc_id,
            "filename": document.get("filename"),
            "subject": document.get("subject"),
            "letter_no": document.get("letterNo"),
            "organization_id": document.get("organization_id"),
            "project_id": document.get("project_id"),
            "processing_status": document.get("processing_status"),
            "processing_error": document.get("processing_error"),
            "evidence_status": evidence.status,
            "can_review": await self._can_review(current_user, document),
        }
        if evidence.status != EVIDENCE_PUBLISHED:
            base.update({"review_state": REVIEW_NOT_REQUIRED, "reviewable": False, "pages": [], "blocking_pages": []})
            return base
        head = evidence.manifest
        state = self.review_state(document, head)
        pages = blocking_pages(document, head) if state != REVIEW_NOT_REQUIRED else []
        latest = await self._latest_decisions(doc_id, head.get("canonical_revision"))
        page_rows = []
        for number in pages:
            span = _span(head, number)
            try:
                page = evidence.page(number)
            except KeyError:
                page = None
            text = evidence.page_text(number) if page is not None else ""
            decision = latest.get(number)
            page_rows.append(
                {
                    "page_number": number,
                    "in_evidence": page is not None,
                    "source": page.source if page else None,
                    "status": page.status if page else None,
                    "page_class": page.page_class if page else None,
                    "machine_quality_verdict": page.quality_verdict if page else None,
                    "machine_needs_review": page.needs_review if page else None,
                    "machine_failed_checks": _failed_checks(page.quality_checks) if page else [],
                    "unresolved": bool(span.get("unresolved")) if span else True,
                    "text_withheld": page.text_withheld if page else False,
                    "error": page.error if page else None,
                    "table_count": len(page.tables) if page else 0,
                    "tables": page.tables if page else [],
                    "page_checksum": span.get("content_sha256") if span else None,
                    "char_count": len(text),
                    "text": text,
                    "approvable": self._approvable(page, span, text),
                    "decision": _public_decision(decision),
                }
            )
        base.update(
            {
                "review_state": state,
                "reviewable": state == REVIEW_REQUIRED,
                "extraction_run_id": evidence.extraction_run_id,
                "canonical_revision": head.get("canonical_revision"),
                "canonical_sha256": head.get("canonical_sha256"),
                "page_count": head.get("page_count"),
                "blocking_pages": pages,
                "unresolved_pages": [
                    n for n in pages if not (latest.get(n) or {}).get("action") == ACTION_APPROVE
                ],
                "pages": page_rows,
                "resolution": document.get("extraction_review"),
                "history": [
                    _public_decision(row)
                    for row in await self.db[DOCUMENT_PAGE_REVIEWS]
                    .find({"document_id": doc_id})
                    .sort([("reviewed_at", -1)])
                    .limit(50)
                    .to_list(length=50)
                ],
            }
        )
        return base

    @staticmethod
    def _approvable(page: Any, span: Optional[Mapping[str, Any]], text: str) -> bool:
        """Approve-as-is needs text a person can actually have inspected.

        An unresolved page (OCR failed, unrenderable) or one whose text was
        withheld has nothing to accept; approving it would publish a document
        with a silently missing page. Those need re-extraction, not approval.
        """
        if page is None or span is None:
            return False
        if span.get("unresolved") or page.text_withheld:
            return False
        return bool(text.strip())

    # ------------------------------------------------------------------
    # decisions
    # ------------------------------------------------------------------
    async def decide_page(
        self,
        document_id: str,
        page_number: int,
        *,
        action: str,
        canonical_revision: int,
        page_checksum: str,
        comment: Optional[str],
        expected_decision_version: Optional[int],
        current_user: Any,
        selection: Any,
    ) -> Dict[str, Any]:
        """Record one reviewer decision; resolve the review if it was the last."""
        if action not in ACTIONS:
            raise DocumentError("Unknown review action", status.HTTP_400_BAD_REQUEST)
        comment = (comment or "").strip() or None
        if comment and len(comment) > _MAX_COMMENT:
            raise DocumentError(f"Comment longer than {_MAX_COMMENT} characters", status.HTTP_400_BAD_REQUEST)
        if action == ACTION_REJECT and not comment:
            raise DocumentError("A rejection needs a comment saying what is wrong", status.HTTP_400_BAD_REQUEST)

        document = await self._authorised_document(
            document_id, current_user, selection, Permissions.DOCUMENT_REVIEW_EXTRACTION
        )
        await self._refuse_ineligible(document)
        doc_id = str(document["_id"])
        evidence = await self._evidence(document)
        self._bound_head(document, evidence)
        head = evidence.manifest
        state = self.review_state(document, head)

        if int(canonical_revision) != int(head.get("canonical_revision") or 0):
            raise _conflict(
                "The document was reprocessed since this page was loaded; reload and review the current evidence",
                current_revision=head.get("canonical_revision"),
            )
        span = _span(head, page_number)
        pages = blocking_pages(document, head)
        if span is None or int(page_number) not in pages:
            raise DocumentError("This page is not awaiting review", status.HTTP_404_NOT_FOUND)
        if str(page_checksum or "") != str(span.get("content_sha256") or ""):
            raise _conflict("This page's evidence changed since it was loaded; reload and review it again")

        latest = await self._latest_decisions(doc_id, head.get("canonical_revision"))
        current = latest.get(int(page_number))
        current_version = int((current or {}).get("decision_version") or 0)

        # Checked before the idempotent shortcut: a reviewer who loaded the
        # page before someone else decided it has not seen that decision, so a
        # matching action is not "the same decision again". Answering it 200
        # dropped their decision and comment while reporting success.
        if expected_decision_version is not None and int(expected_decision_version) != current_version:
            raise _conflict(
                "Another reviewer decided this page since it was loaded; reload it",
                current_decision_version=current_version,
            )

        resolution: Dict[str, Any]
        if current and current.get("action") == action:
            # Idempotent: the same decision again records nothing new.
            if state == REVIEW_REQUIRED and action == ACTION_APPROVE:
                resolution = await self._resolve_if_complete(document, evidence, current_user)
            else:
                resolution = {"resolved": state != REVIEW_REQUIRED, "state": state}
            return {"decision": _public_decision(current), "idempotent": True, "resolution": resolution}

        if state != REVIEW_REQUIRED:
            raise _conflict("This document's review is already resolved", review_state=state)

        try:
            page = evidence.page(int(page_number))
        except KeyError as exc:  # pragma: no cover - span exists, so the page does
            raise DocumentError("This page is not in the published evidence", status.HTTP_404_NOT_FOUND) from exc
        if action == ACTION_APPROVE and not self._approvable(
            page, span, evidence.page_text(int(page_number))
        ):
            raise _conflict(
                "This page has no extracted text to approve; it needs re-extraction, not approval"
            )

        now = _now()
        row = {
            "document_id": doc_id,
            "organization_id": document.get("organization_id"),
            "project_id": document.get("project_id"),
            "extraction_run_id": evidence.extraction_run_id,
            "canonical_revision": head.get("canonical_revision"),
            "canonical_sha256": head.get("canonical_sha256"),
            "page_number": int(page_number),
            "page_sha256": span.get("content_sha256"),
            # A copy of the machine's verdict at decision time, for audit
            # without a join. The page row itself is never written.
            "machine_verdict": page.quality_verdict,
            "machine_needs_review": bool(page.needs_review),
            "machine_failed_checks": [c.get("name") for c in _failed_checks(page.quality_checks)],
            "action": action,
            "comment": comment,
            "reviewed_by": _actor_id(current_user),
            "reviewed_by_label": _actor_label(current_user),
            "reviewed_at": now,
            "decision_version": current_version + 1,
            "supersedes": current.get("_id") if current else None,
        }
        try:
            result = await self.db[DOCUMENT_PAGE_REVIEWS].insert_one(row)
        except DuplicateKeyError as exc:
            raise _conflict("Another reviewer decided this page at the same time; reload it") from exc
        row["_id"] = result.inserted_id

        await self._audit(
            document,
            event_type=(
                "document.extraction_page_approved"
                if action == ACTION_APPROVE
                else "document.extraction_page_rejected"
            ),
            actor=current_user,
            metadata={
                "page_number": int(page_number),
                "canonical_revision": head.get("canonical_revision"),
                "canonical_sha256": head.get("canonical_sha256"),
                "extraction_run_id": evidence.extraction_run_id,
                "page_sha256": span.get("content_sha256"),
                "decision_id": str(row["_id"]),
                "decision_version": row["decision_version"],
                "action": action,
                "comment_present": bool(comment),
                "machine_verdict": page.quality_verdict,
                "old_status": document.get("processing_status"),
                "new_status": document.get("processing_status"),
            },
        )

        resolution = {"resolved": False, "state": REVIEW_REQUIRED}
        if action == ACTION_APPROVE:
            resolution = await self._resolve_if_complete(document, evidence, current_user)
        return {"decision": _public_decision(row), "idempotent": False, "resolution": resolution}

    async def _resolve_if_complete(
        self, document: Dict[str, Any], evidence: CanonicalEvidence, actor: Any
    ) -> Dict[str, Any]:
        head = evidence.manifest
        latest = await self._latest_decisions(str(document["_id"]), head.get("canonical_revision"))
        pages = blocking_pages(document, head)
        unresolved = [n for n in pages if (latest.get(n) or {}).get("action") != ACTION_APPROVE]
        if unresolved:
            return {"resolved": False, "state": REVIEW_REQUIRED, "unresolved_pages": unresolved}
        return await self.resolve_review(
            str(document["_id"]), expected_revision=int(head.get("canonical_revision") or 0), actor=actor
        )

    # ------------------------------------------------------------------
    # resolution (finalize_document_review)
    # ------------------------------------------------------------------
    async def finalize(
        self, document_id: str, *, canonical_revision: int, current_user: Any, selection: Any
    ) -> Dict[str, Any]:
        """Authorised resume: resolve (if every page is approved) and allow republication."""
        document = await self._authorised_document(
            document_id, current_user, selection, Permissions.DOCUMENT_REVIEW_EXTRACTION
        )
        await self._refuse_ineligible(document)
        return await self.resolve_review(
            str(document["_id"]), expected_revision=int(canonical_revision), actor=current_user
        )

    async def resolve_review(
        self, document_id: str, *, expected_revision: int, actor: Any
    ) -> Dict[str, Any]:
        """Leave ``human_review_required`` only when every blocking page is approved.

        Idempotent and resumable: a document already resolved for this
        revision answers the same result without writing anything.
        """
        document = await self._find_document(document_id)
        if not document or document.get("lifecycle_state") == "deleted":
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
        await self._refuse_ineligible(document)
        doc_id = str(document["_id"])
        evidence = await self._evidence(document)
        self._bound_head(document, evidence)
        head = evidence.manifest
        revision = head.get("canonical_revision")
        if int(expected_revision) != int(revision or 0):
            raise _conflict(
                "The document was reprocessed since the review began; review the current evidence",
                current_revision=revision,
            )

        state = self.review_state(document, head)
        if state in {REVIEW_PUBLICATION_PENDING, REVIEW_RESOLVED}:
            return {"resolved": True, "state": state, "canonical_revision": revision}
        if state != REVIEW_REQUIRED:
            raise _conflict("This document is not awaiting extraction review", review_state=state)

        pages = blocking_pages(document, head)
        latest = await self._latest_decisions(doc_id, revision)
        unresolved = []
        for number in pages:
            decision = latest.get(number)
            span = _span(head, number)
            if (
                not decision
                or decision.get("action") != ACTION_APPROVE
                or span is None
                or decision.get("page_sha256") != span.get("content_sha256")
                or decision.get("canonical_sha256") != head.get("canonical_sha256")
            ):
                unresolved.append(number)
        if not pages or unresolved:
            return {"resolved": False, "state": REVIEW_REQUIRED, "unresolved_pages": unresolved or pages}

        now = _now()
        decision_ids = [str(latest[n]["_id"]) for n in pages]
        resolution = {
            "status": REVIEW_RESOLVED,
            "extraction_run_id": evidence.extraction_run_id,
            "canonical_revision": revision,
            "canonical_sha256": head.get("canonical_sha256"),
            "approved_pages": pages,
            "decision_ids": decision_ids,
            "resolved_at": now,
            "resolved_by": _actor_id(actor),
        }
        # 1. The canonical head records which revision a person released. The
        #    machine manifest beside it is untouched; a new extraction run
        #    unsets this (DocumentPageStore.publish_canonical).
        head_write = await self.db[DOCUMENT_EXTRACTION_HEADS].update_one(
            {
                "document_id": doc_id,
                "canonical_revision": revision,
                "canonical_sha256": head.get("canonical_sha256"),
            },
            {"$set": {"review_resolution": resolution}},
        )
        if getattr(head_write, "matched_count", 1) == 0:
            raise _conflict("The document was reprocessed while the review was resolving; review it again")

        # 2. The document leaves the adverse state - guarded on the exact state
        #    and run it was decided against, so a reprocess that started in the
        #    meantime cannot be overwritten.
        machine_verdict = document.get("processing_error")
        guard: Dict[str, Any] = {
            "_id": document["_id"],
            "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        }
        job_id = document.get("processing_job_id")
        if job_id:
            guard["processing_job_id"] = job_id
        pending_error = {
            "message": "Human review resolved; publication pending",
            "stage": STAGE_PUBLICATION_PENDING,
            "terminal": False,
            "timestamp": now,
        }
        moved = await self.db.documents.update_one(
            guard,
            {
                "$set": {
                    "processing_status": ProcessingState.PARTIALLY_PROCESSED.value,
                    "processing_error": pending_error,
                    "extraction_review": {**resolution, "machine_verdict": machine_verdict},
                    "updatedAt": datetime.utcnow(),
                }
            },
        )
        if getattr(moved, "matched_count", 1) == 0:
            current = await self._find_document(doc_id) or {}
            if self.review_state(current, head) in {REVIEW_PUBLICATION_PENDING, REVIEW_RESOLVED}:
                return {"resolved": True, "state": self.review_state(current, head), "canonical_revision": revision}
            raise _conflict("The document changed while the review was resolving; reload it")
        if job_id:
            await self.db.document_processing_jobs.update_one(
                {"_id": job_id, "status": ProcessingState.HUMAN_REVIEW_REQUIRED.value},
                {
                    "$set": {
                        "status": ProcessingState.PARTIALLY_PROCESSED.value,
                        "stage": STAGE_PUBLICATION_PENDING,
                        "error": pending_error,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
        await self._audit(
            document,
            event_type="document.extraction_review_resolved",
            actor=actor,
            metadata={
                "canonical_revision": revision,
                "canonical_sha256": head.get("canonical_sha256"),
                "extraction_run_id": evidence.extraction_run_id,
                "approved_pages": pages,
                "decision_ids": decision_ids,
                "old_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
                "new_status": ProcessingState.PARTIALLY_PROCESSED.value,
            },
        )
        logger.info(
            "[extraction_review] Document %s review resolved at revision %s (pages %s); publication pending",
            doc_id,
            revision,
            pages,
        )
        return {"resolved": True, "state": REVIEW_PUBLICATION_PENDING, "canonical_revision": revision}

    # ------------------------------------------------------------------
    # publication after resolution
    # ------------------------------------------------------------------
    async def publish_resolved(self, document_id: str, *, expected_revision: int) -> Dict[str, Any]:
        """Run the publication the review held back, then settle the status truthfully.

        Reuses the existing writers; contains no embedding or graph logic of its
        own. Safe to call again: embeddings replace a document's points, graph
        writes MERGE, and the status writes are guarded.
        """
        document = await self._find_document(document_id)
        if not document or document.get("lifecycle_state") == "deleted":
            return {"published": False, "reason": "document_not_found"}
        doc_id = str(document["_id"])
        head = await self.db[DOCUMENT_EXTRACTION_HEADS].find_one({"document_id": doc_id}) or {}
        if int(head.get("canonical_revision") or 0) != int(expected_revision):
            return {"published": False, "reason": "revision_changed"}
        if self.review_state(document, head) != REVIEW_PUBLICATION_PENDING:
            return {"published": False, "reason": "not_pending", "state": self.review_state(document, head)}

        stages: Dict[str, str] = {}
        database_service = self._database_service()
        try:
            await database_service.create_embeddings_for_document(doc_id)
        except Exception as exc:
            logger.warning("[extraction_review] Embeddings failed for %s after review: %s", doc_id, exc)
            stages["embeddings"] = "failed"

        vector_outcome = await self._vector_outcome(doc_id)
        if vector_outcome:
            stages["vector_sync"] = vector_outcome

        stages.update(await self._publish_graph(document))

        settled = await self._settle(doc_id, int(expected_revision), document, stages)
        # The summary held while the document awaited review is generated now,
        # from the same revision and with the reviewer's approval on it. It is
        # derivative: its outcome never changes the settled status.
        try:
            from .grounded_summary.service import GroundedSummaryService

            await GroundedSummaryService(self.db).generate(doc_id, trigger="review_resolved")
        except Exception:
            logger.warning("[extraction_review] Grounded summary skipped for %s", doc_id, exc_info=True)
        return settled

    async def _vector_outcome(self, document_id: str) -> Optional[str]:
        """Mongo chunk rows and Qdrant points agree, or the stage did not finish."""
        try:
            row = await self.db.vector_sync_status.find_one({"document_id": document_id}) or {}
            sync_status = str(row.get("sync_status") or "unknown")
            if sync_status in _UNFINISHED_VECTOR_SYNC:
                return sync_status
            if sync_status in {"synced", "mongo_only"}:
                stored = await self.db.document_vectors.count_documents({"document_id": document_id})
                if stored != int(row.get("mongo_chunks") or 0):
                    return "mismatch"
                if sync_status == "synced" and int(row.get("qdrant_chunks") or -1) != stored:
                    return "mismatch"
            return None
        except Exception:
            logger.warning("[extraction_review] Could not verify vectors for %s", document_id, exc_info=True)
            return "unknown"

    async def _publish_graph(self, document: Dict[str, Any]) -> Dict[str, str]:
        """Graph, evidence and reference links, through the run's own writers."""
        from .metadata_integrity import metadata_from_document

        doc_id = str(document["_id"])
        service = self._document_service()
        current = await self._find_document(doc_id) or document
        metadata = metadata_from_document(current)
        upload_type = current.get("uploadType")
        failures: Dict[str, str] = {}
        try:
            authorised = await service._publish_graph_and_evidence_from_current(
                document_id=doc_id,
                extracted_payload=dict(current),
                metadata=metadata,
                metadata_source=current.get("metadata_source"),
                upload_type=upload_type,
            )
        except Exception:
            logger.warning("[extraction_review] Graph publication failed for %s", doc_id, exc_info=True)
            return {"graph": "failed"}
        if not authorised:
            return {"graph": "withheld"}

        stored_quality = current.get("metadata_quality")
        quality: Mapping[str, Any] = stored_quality if isinstance(stored_quality, Mapping) else {}
        references = current.get("reference") or []
        try:
            if quality.get("references_authoritative"):
                await service.reference_sync_service.sync_bidirectional(
                    document_id=doc_id,
                    references=references,
                    source="parser",
                    default_link_type="indirect",
                    clear_existing=True,
                )
            await service._sync_current_document_to_falkor(
                doc_id, metadata=metadata, upload_type=upload_type, raise_on_error=True
            )
        except Exception:
            logger.warning("[extraction_review] Reference/graph sync failed for %s", doc_id, exc_info=True)
            failures["references"] = "failed"
        return failures

    async def _settle(
        self, doc_id: str, revision: int, document: Mapping[str, Any], stages: Dict[str, str]
    ) -> Dict[str, Any]:
        now = datetime.utcnow()
        job_id = document.get("processing_job_id")
        guard: Dict[str, Any] = {
            "_id": document["_id"],
            "processing_status": ProcessingState.PARTIALLY_PROCESSED.value,
            "extraction_review.status": REVIEW_RESOLVED,
            "extraction_review.canonical_revision": revision,
        }
        if job_id:
            guard["processing_job_id"] = job_id
        if stages:
            detail = "; ".join(f"{stage}: {outcome}" for stage, outcome in sorted(stages.items()))
            error = {
                "message": (
                    f"Human review resolved, but publication did not finish ({detail}). "
                    "Retry publication once the cause is fixed."
                ),
                "stage": STAGE_INDEXING_INCOMPLETE,
                "stages": dict(stages),
                "timestamp": now,
                "terminal": True,
            }
            new_status = ProcessingState.PARTIALLY_PROCESSED.value
            doc_set: Dict[str, Any] = {"processing_error": error, "updatedAt": now}
            job_set: Dict[str, Any] = {
                "status": new_status, "stage": STAGE_INDEXING_INCOMPLETE, "error": error,
                "completed_at": now, "updated_at": now,
            }
            event = "document.extraction_review_publication_incomplete"
        else:
            new_status = ProcessingState.COMPLETED.value
            doc_set = {
                "processing_status": new_status,
                "processing_error": None,
                "processed_at": now,
                "updatedAt": now,
            }
            job_set = {
                "status": "completed", "stage": "completed", "error": None,
                "completed_at": now, "updated_at": now,
            }
            event = "document.extraction_review_published"
        moved = await self.db.documents.update_one(guard, {"$set": doc_set})
        if getattr(moved, "matched_count", 1) == 0:
            # Reprocessed (or settled by a concurrent publish) meanwhile: that
            # run owns the status now.
            return {"published": False, "reason": "state_changed", "stages": stages}
        if job_id:
            await self.db.document_processing_jobs.update_one({"_id": job_id}, {"$set": job_set})
        await self._audit(
            document,
            event_type=event,
            actor=None,
            metadata={
                "canonical_revision": revision,
                "old_status": ProcessingState.PARTIALLY_PROCESSED.value,
                "new_status": new_status,
                "stages": dict(stages),
            },
        )
        return {"published": not stages, "status": new_status, "stages": stages}

    # ------------------------------------------------------------------
    # audit
    # ------------------------------------------------------------------
    async def _audit(
        self, document: Mapping[str, Any], *, event_type: str, actor: Any, metadata: Dict[str, Any]
    ) -> None:
        """Mirror of the decision row in the audit stream. Never page text.

        The ``document_page_reviews`` row is the durable record; this stream
        is the cross-resource audit view and, like every DocumentAuditService
        emit, best effort.
        """
        from .document_audit_service import DocumentAuditService

        await DocumentAuditService(self.db).emit(
            resource_type="document",
            resource_id=str(document.get("_id")),
            event_type=event_type,
            actor_id=_actor_id(actor) if actor is not None else None,
            organization_id=document.get("organization_id"),
            project_id=document.get("project_id"),
            metadata=metadata,
        )


async def ensure_review_indexes(db: Any) -> None:
    """The concurrency guard and the read path. Called from startup indexes."""
    await db[DOCUMENT_PAGE_REVIEWS].create_index(
        [("document_id", 1), ("canonical_revision", 1), ("page_number", 1), ("decision_version", 1)],
        unique=True,
        name="page_review_decision_unique",
        background=True,
    )
    await db[DOCUMENT_PAGE_REVIEWS].create_index(
        [("document_id", 1), ("reviewed_at", -1)], name="page_review_history", background=True
    )
