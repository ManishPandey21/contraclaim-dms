"""Promotion: one adjudicated candidate becomes one authoritative instrument.

All-or-nothing, in a single transaction. The reason this is one ticket and not
five write tickets is that every intermediate state is unsafe:

* an instrument with no classification fact has a type nobody asserted;
* a project-scope instrument with no applicability is an instrument that
  governs nothing and yet answers as though it might;
* an applicability with no instrument is a legal statement about a document the
  catalogue does not contain;
* a receipt written outside the transaction can survive a rollback, and then
  claims a promotion that did not happen.

So for project scope the transaction includes the **first applicability and its
APPLIED event**. Organisation scope is valid with *zero* applicability — an
organisation-owned instrument is not applicable to any one contract until
somebody says so, and manufacturing an applicability to make the shapes match
would invent the only fact the corpus never contains.

**The fingerprint is revalidated immediately before commit.** The reconciliation
snapshot was taken at inventory, possibly weeks earlier; if the underlying
document changed since, the operator adjudicated something else. The snapshot is
evidence of what was reviewed, never a token authorising a write.

**Blocked documents split by axis.** Quarantine (duplicate/deleted) is
``INVALID`` and never promotes — the subsystem that said "this is not real" must
not be overruled by a migration. Adverse *quality* (human review required) does
promote: the instrument exists, and evidence is denied by the ordinary
publication gate rather than by refusing to catalogue it.

Legacy source fields are never touched. Promotion adds authority; it does not
rewrite the history it was derived from.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pymongo.errors import DuplicateKeyError, OperationFailure

from ..models.contract_document import ContractDocumentType, ProjectionStatus
from .contract_candidate_authority import (
    CandidateAnchor,
    load_canonical_document,
    resolve_candidate_anchor,
)
from .contract_document_store import (
    APPLICABILITY_COLLECTION,
    APPLICABILITY_EVENTS_COLLECTION,
    CLASSIFICATION_FACTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
)
from .contract_migration_reconciliation import (
    RECONCILIATION_COLLECTION,
    CandidateNotFound,
    ScopeClassificationState,
    TypeClassificationState,
    scoped_candidate_filter,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PROMOTION_RECEIPTS_COLLECTION",
    "AlreadyPromoted",
    "CandidateNotFound",
    "ContractPromotionService",
    "NotPromotable",
    "PromotionReceipt",
    "RevalidationRequired",
]

PROMOTION_RECEIPTS_COLLECTION = "contract_promotion_receipts"

#: Quarantine states. These documents are not real, so they never become
#: instruments. Distinct from adverse *quality*, which does promote.
QUARANTINE_LIFECYCLE_STATES = frozenset({"duplicate", "deleted"})


class NotPromotable(Exception):
    """This candidate cannot become an authoritative instrument."""


class AlreadyPromoted(Exception):
    """This candidate is already an instrument. A second promotion is a conflict.

    Deterministic instrument and receipt ids make a retry collide rather than fork;
    the collision is the answer, reported as such instead of as a server error.
    """


#: MongoDB's WriteConflict: a concurrent transaction on the same documents won.
_WRITE_CONFLICT = 112


class RevalidationRequired(Exception):
    """The source changed since the operator reviewed it.

    Raised with nothing written. The adjudication describes a document that no
    longer exists in that form, so honouring it would attach a human decision to
    content the human never saw.
    """


@dataclass(frozen=True)
class PromotionReceipt:
    """Deterministic link from candidate to authoritative record."""

    receipt_id: str
    candidate_id: str
    contract_document_id: str
    scope_level: str
    project_id: Optional[str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _contract_document_id(candidate_id: str) -> str:
    """One candidate promotes to one instrument, always the same one.

    Deterministic so a retry converges rather than minting a second instrument
    for the same document.
    """
    return f"contract-document:{candidate_id}"


class ContractPromotionService:
    """Promotes one adjudicated candidate, atomically or not at all."""

    def __init__(self, db: Any, client: Any = None) -> None:
        self._db = db
        self._client = client

    async def promote(
        self,
        candidate_id: str,
        *,
        organization_id: str,
        actor_id: str,
        contract_id: Optional[str] = None,
        effective_from: Optional[str] = None,
        expected_scope_state: Optional[str] = None,
    ) -> PromotionReceipt:
        """Make an adjudicated candidate authoritative, in one transaction.

        ``organization_id`` is the organisation the caller was authorised for.
        The candidate is loaded inside it, so a candidate of another
        organisation is indistinguishable from one that does not exist.

        ``expected_scope_state`` is the scope the caller was authorised against.
        The route decides whether organisation-wide scope is needed from it, so a
        re-adjudication landing in between must not change what gets written.
        """
        candidate = await self._db[RECONCILIATION_COLLECTION].find_one(
            scoped_candidate_filter(candidate_id, organization_id)
        )
        if candidate is None:
            raise CandidateNotFound()
        if candidate.get("promoted"):
            raise AlreadyPromoted(f"candidate {candidate_id} is already promoted")
        if expected_scope_state is not None and candidate.get("scope_state") != expected_scope_state:
            raise RevalidationRequired(
                f"candidate {candidate_id} was re-adjudicated after it was authorised; "
                "nothing is written"
            )

        self._require_adjudicated(candidate)
        document = await self._require_promotable_document(candidate)
        await self._revalidate_fingerprint(candidate, document)
        anchor = await resolve_candidate_anchor(self._db, candidate, document)

        scope_level, project_id = self._resolve_scope(candidate, anchor)
        if scope_level == "project" and not contract_id:
            raise NotPromotable(
                f"candidate {candidate_id} is project-scoped, so promotion must "
                "name the contract its first applicability attaches to; an "
                "instrument that governs nothing must not exist"
            )

        plan = self._build_writes(
            candidate,
            document=document,
            scope_level=scope_level,
            project_id=project_id,
            contract_id=contract_id,
            effective_from=effective_from,
            actor_id=actor_id,
        )
        # The marker write inside the transaction is a compare-and-set: scoped by
        # the organisation the route authorised, and by the decision that was read
        # here. A re-adjudication, or a concurrent promotion, between this read
        # and the commit matches nothing and aborts the whole transaction.
        plan["marker_filter"] = {
            **scoped_candidate_filter(candidate_id, organization_id),
            "scope_state": candidate.get("scope_state"),
            "type_state": candidate.get("type_state"),
            "contract_document_type": candidate.get("contract_document_type"),
            "promoted": {"$ne": True},
        }

        if self._client is not None:
            try:
                async with await self._client.start_session() as session:
                    async with session.start_transaction():
                        await self._apply(plan, session=session)
            except DuplicateKeyError as exc:
                # The deterministic instrument or receipt already exists.
                raise AlreadyPromoted(f"candidate {candidate_id} is already promoted") from exc
            except OperationFailure as exc:
                if getattr(exc, "code", None) == _WRITE_CONFLICT:
                    raise AlreadyPromoted(
                        f"candidate {candidate_id} is being promoted concurrently"
                    ) from exc
                raise
        else:
            # No client means no transaction is available. Refuse rather than
            # write half of it: a partially promoted instrument is the exact
            # state this ticket exists to make impossible.
            raise NotPromotable(
                "promotion requires a transactional session; without one the five "
                "writes cannot be made atomic and a half-promoted instrument "
                "could survive"
            )

        return PromotionReceipt(
            receipt_id=plan["receipt"]["_id"],
            candidate_id=candidate_id,
            contract_document_id=plan["instrument"]["_id"],
            scope_level=scope_level,
            project_id=project_id,
        )

    # -- preconditions ------------------------------------------------------- #

    def _require_adjudicated(self, candidate: Dict[str, Any]) -> None:
        scope_state = candidate.get("scope_state")
        if scope_state == ScopeClassificationState.INVALID.value:
            raise NotPromotable(
                f"candidate {candidate.get('_id')} is INVALID, which is terminal"
            )
        if scope_state not in (
            ScopeClassificationState.ORG_SCOPE_CONFIRMED.value,
            ScopeClassificationState.PROJECT_SCOPE_CONFIRMED.value,
        ):
            raise NotPromotable(
                f"candidate {candidate.get('_id')} has scope {scope_state}; an "
                "ambiguous or unresolved scope performs no authoritative writes"
            )
        if candidate.get("type_state") != TypeClassificationState.TYPE_RESOLVED.value:
            raise NotPromotable(
                f"candidate {candidate.get('_id')} has no resolved type; a "
                "suggestion is not a classification"
            )

    async def _require_promotable_document(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        document_id = candidate.get("canonical_document_id")
        # Both spellings of this one id: inventory stores str(_id), and production
        # Documents are ObjectId-keyed.
        document = await load_canonical_document(self._db, candidate)
        if document is None:
            raise NotPromotable(f"canonical document {document_id} does not exist")

        document_org = document.get("organization_id") or document.get("organizationId")
        if str(document_org or "") != str(candidate.get("organization_id") or ""):
            # Candidate org == document org == instrument org, or nothing. The
            # inventory reads documents inside one organisation, so this fires
            # only on corrupt or hand-edited rows - and then an instrument owned
            # by the candidate's organisation would govern a document it does
            # not own. Refused, never repaired; the message names neither
            # organisation.
            raise NotPromotable(
                f"canonical document {document_id} is not owned by the candidate's "
                "organisation; refusing to promote across a tenancy boundary"
            )

        lifecycle = str(document.get("lifecycle_state") or "")
        duplicate = str(document.get("duplicate_status") or "")
        if lifecycle in QUARANTINE_LIFECYCLE_STATES or duplicate == "duplicate":
            # Quarantine, not quality. A migration must not overrule the
            # subsystem that decided this document is not real.
            raise NotPromotable(
                f"document {document_id} is quarantined ({lifecycle or duplicate}); "
                "quarantined documents are INVALID and never promote"
            )
        return document

    async def _revalidate_fingerprint(
        self, candidate: Dict[str, Any], document: Dict[str, Any]
    ) -> None:
        recorded = candidate.get("source_fingerprint")
        if recorded is None:
            return
        current = document.get("checksum") or document.get("sha256")
        if current is not None and str(current) != str(recorded):
            raise RevalidationRequired(
                f"candidate {candidate.get('_id')} was adjudicated against "
                f"fingerprint {recorded}, but the document now reads {current}; "
                "nothing is written - the operator reviewed different content"
            )

    def _resolve_scope(self, candidate: Dict[str, Any], anchor: CandidateAnchor):
        """The instrument's scope, and for project scope its project.

        Project scope takes the candidate's trustworthy anchor
        (``contract_candidate_authority``): ``candidate.project_id`` when an
        authorised scope decision wrote it, else the canonical Document's own
        project - the path every materialised legacy candidate takes, since
        inventory never writes ``candidate.project_id``. Never a session or scope
        hint. A conflicted anchor never promotes, at either scope: the candidate
        points outside its organisation, at a missing or inactive project, or two
        trusted fields disagree.
        """
        if anchor.conflict:
            raise NotPromotable(
                f"candidate {candidate.get('_id')} has no trustworthy project anchor "
                "(its project fields disagree, or name a project that is not an "
                "active project of this organisation); nothing is written"
            )
        if candidate.get("scope_state") == ScopeClassificationState.PROJECT_SCOPE_CONFIRMED.value:
            if not anchor.project_id:
                raise NotPromotable(
                    f"candidate {candidate.get('_id')} claims project scope with no "
                    "project anchor"
                )
            return "project", anchor.project_id
        return "organization", None

    # -- the writes ---------------------------------------------------------- #

    def _build_writes(
        self,
        candidate: Dict[str, Any],
        *,
        document: Dict[str, Any],
        scope_level: str,
        project_id: Optional[str],
        contract_id: Optional[str],
        effective_from: Optional[str],
        actor_id: str,
    ) -> Dict[str, Any]:
        candidate_id = str(candidate["_id"])
        instrument_id = _contract_document_id(candidate_id)
        document_id = str(candidate["canonical_document_id"])
        document_type = ContractDocumentType(candidate["contract_document_type"])
        now = _now()

        plan: Dict[str, Any] = {
            "instrument": {
                "_id": instrument_id,
                "organization_id": candidate["organization_id"],
                "document_id": document_id,
                "document_version_id": str(
                    document.get("current_version_id") or document_id
                ),
                "contract_document_type": document_type.value,
                "scope_level": scope_level,
                "project_id": project_id,
                # Revision 1 with a PENDING projection: the instrument is
                # authoritative immediately and evidence-capable only once its
                # projection is built.
                "classification_revision": 1,
                "projection_status": ProjectionStatus.PENDING.value,
                "promoted_at": now,
                "promoted_by": actor_id,
            },
            "classification_fact": {
                "_id": uuid.uuid4().hex,
                "contract_document_id": instrument_id,
                "contract_document_type": document_type.value,
                "revision": 1,
                "basis": "migration_adjudicated",
                "actor_id": candidate.get("adjudicated_by") or actor_id,
                "asserted_at": now,
            },
            "receipt": {
                "_id": f"promotion:{candidate_id}",
                "candidate_id": candidate_id,
                "contract_document_id": instrument_id,
                "canonical_document_id": document_id,
                "scope_level": scope_level,
                "project_id": project_id,
                "promoted_at": now,
                "promoted_by": actor_id,
            },
            "candidate_id": candidate_id,
        }

        if scope_level == "project":
            applicability_id = f"applicability:{instrument_id}:{contract_id}"
            plan["applicability"] = {
                "_id": applicability_id,
                "organization_id": candidate["organization_id"],
                "project_id": project_id,
                "contract_id": contract_id,
                "contract_document_id": instrument_id,
            }
            plan["applicability_event"] = {
                "_id": uuid.uuid4().hex,
                "applicability_id": applicability_id,
                "kind": "APPLIED",
                # Unknown stays unknown. Substituting today's date, or the
                # promotion date, would manufacture a legal start.
                "effective_at": effective_from,
                "recorded_at": now,
                "actor_id": actor_id,
            }
        return plan

    async def _apply(self, plan: Dict[str, Any], *, session: Any) -> None:
        await self._db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
            plan["instrument"], session=session
        )
        await self._db[CLASSIFICATION_FACTS_COLLECTION].insert_one(
            plan["classification_fact"], session=session
        )
        if "applicability" in plan:
            await self._db[APPLICABILITY_COLLECTION].insert_one(
                plan["applicability"], session=session
            )
            await self._db[APPLICABILITY_EVENTS_COLLECTION].insert_one(
                plan["applicability_event"], session=session
            )
        # Inside the transaction, deliberately: a receipt that survives a
        # rollback claims a promotion that never happened.
        await self._db[PROMOTION_RECEIPTS_COLLECTION].insert_one(
            plan["receipt"], session=session
        )
        marked = await self._db[RECONCILIATION_COLLECTION].update_one(
            plan["marker_filter"],
            {"$set": {"promoted": True, "promoted_at": plan["receipt"]["promoted_at"]}},
            session=session,
        )
        if getattr(marked, "matched_count", 0) != 1:
            # The candidate changed, left the authorised scope, or was promoted
            # between the read and the commit. Raising aborts the transaction, so
            # no instrument or receipt survives for a candidate never marked.
            raise RevalidationRequired(
                f"candidate {plan['candidate_id']} changed during promotion; nothing "
                "was written"
            )
