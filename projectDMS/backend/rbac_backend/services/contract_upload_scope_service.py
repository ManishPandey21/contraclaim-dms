"""Upload scope: authorised by capability, recorded as a candidate.

Two things this refuses to do, both of which the current upload path does.

**It never tests a role name.** Organisation-scope upload is authorised by
capabilities held at a resource scope — ``DOCUMENT_UPLOAD`` plus
``CONTRACT_MASTER_MANAGE`` plus ``CONTRACT_CATALOGUE_BROWSE``. A role-name test
is a copy of the permission model that drifts from it silently, and the two
disagree the moment somebody adds a role.

**It creates nothing authoritative.** An upload produces a canonical Document
and a scope-resolved *reconciliation candidate*. No instrument, no classification
fact, no applicability aggregate, no ``APPLIED`` event, no evidence readiness. A
contract selected in the upload form is operational context — it says where the
uploader was, not that the document legally applies to that contract. Inferring
applicability from it would manufacture the one fact the corpus never contains.

Batch uploads share **one** scope decision, because scope is a decision the
uploader makes once; type stays per file, because it is a property of each
document.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, Tuple

from fastapi import status
from pymongo.errors import DuplicateKeyError

from ..core.permissions import Permissions
from ..models.contract_upload_scope import (
    ContractUploadScope,
    OrganizationScopeUpload,
    ProjectScopeUpload,
)
from ..utils.error_handler import ContractError
from .publication_policy import resolve_canonical_document
from .scope_service import ScopeService
from .contract_migration_reconciliation import (
    RECONCILIATION_COLLECTION,
    ScopeClassificationState,
    TypeClassificationState,
    candidate_identity,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES",
    "CandidateScopeConflict",
    "ContractUploadScopeService",
    "ScopeAuthorisationDenied",
    "UnknownProjectAnchor",
    "UploadedCandidate",
]

#: All three are required together. Upload alone lets any uploader mint an
#: organisation-wide instrument; catalogue browse without master manage lets
#: somebody create what they can see but may not govern.
ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES: Tuple[str, ...] = (
    Permissions.DOCUMENT_UPLOAD,
    Permissions.CONTRACT_MASTER_MANAGE,
    Permissions.CONTRACT_CATALOGUE_BROWSE,
)


class ScopeAuthorisationDenied(Exception):
    """The actor may not create a document at the requested scope."""


class CandidateScopeConflict(ContractError):
    """The document already has a candidate this upload may not redefine.

    Candidate identity is deterministic (module + canonical document), so a
    retried or repeated upload reaches the existing row. Creation is insert-only:
    an existing row keeps its organisation, scope, project anchor and every later
    decision (type, adjudication, fingerprint, promotion), and a request for a
    different scope - or from another organisation - is refused rather than
    written or pretended. The message names no organisation.
    """

    def __init__(self, candidate_id: str) -> None:
        super().__init__(
            f"candidate {candidate_id} is already recorded with a scope this upload "
            "does not match; nothing was changed",
            status.HTTP_409_CONFLICT,
        )


class UnknownProjectAnchor(Exception):
    """The named project does not exist in this organisation.

    Raised rather than downgraded to organisation scope: a project the server
    cannot find is a bad request, and answering it by widening the scope would
    turn a typo into an organisation-wide document.
    """


@dataclass(frozen=True)
class UploadedCandidate:
    """What an upload produces. Deliberately not an instrument."""

    candidate_id: str
    canonical_document_id: str
    organization_id: str
    scope_level: str
    project_id: Optional[str]


class ContractUploadScopeService:
    """Authorises a scope decision and records it durably."""

    def __init__(
        self,
        db: Any,
        *,
        has_capability: Callable[..., Awaitable[bool]],
    ) -> None:
        self._db = db
        # A port, not a role lookup. Whatever satisfies it must answer
        # "does this actor hold this capability at this resource scope".
        self._has_capability = has_capability

    async def authorise(
        self,
        scope: ContractUploadScope,
        *,
        actor_id: str,
        organization_id: str,
    ) -> None:
        """Check capabilities at the resource scope named by the request."""
        if isinstance(scope, OrganizationScopeUpload):
            required = ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES
            resource_scope = {"organization_id": organization_id, "project_id": None}
        elif isinstance(scope, ProjectScopeUpload):
            required = (Permissions.DOCUMENT_UPLOAD,)
            resource_scope = {
                "organization_id": organization_id,
                "project_id": scope.project_id,
            }
        else:  # pragma: no cover - the union has exactly two members
            raise ScopeAuthorisationDenied(f"unsupported scope {type(scope).__name__}")

        missing = []
        for capability in required:
            granted = await self._has_capability(
                actor_id=actor_id, capability=capability, **resource_scope
            )
            if not granted:
                missing.append(str(capability))

        if missing:
            raise ScopeAuthorisationDenied(
                f"actor {actor_id} lacks {', '.join(missing)} at the requested "
                "scope; organisation-scope upload is authorised by capability at a "
                "resource scope, never by role name"
            )

    async def create_candidate(
        self,
        scope: ContractUploadScope,
        *,
        canonical_document_id: str,
        organization_id: str,
        actor_id: str,
        selected_contract_id: Optional[str] = None,
        module: str = "contracts",
    ) -> UploadedCandidate:
        """Record the scope decision against a canonical Document - once.

        ``selected_contract_id`` is captured as operational context only. It is
        never turned into applicability: the uploader chose a place to work, not
        a legal fact about which contract this instrument governs.

        Insert-only. ``candidate.project_id`` is a trust anchor
        (``contract_candidate_authority``), so an existing row is never rewritten:
        the write is ``$setOnInsert`` under a filter pinned to the organisation,
        and the answer is read back from what is stored. An identical replay
        returns the stored scope; anything else is ``CandidateScopeConflict``.
        """
        # Normalised once: the anchor check, the filter, the stored field and the
        # answer all use the same string.
        organization_id = str(organization_id)
        canonical_document_id = str(canonical_document_id)
        candidate_id = candidate_identity(
            module=module, canonical_document_id=canonical_document_id
        )

        # The Document must be this organisation's. Without it an organisation
        # could claim another's deterministic candidate id first - and, creation
        # being insert-only, keep it: the owner's own upload would then conflict
        # and its inventory would skip the id. Same refusal for "missing" and
        # "someone else's", so the answer discloses neither.
        document = await resolve_canonical_document(self._db, canonical_document_id)
        document_org = str(
            (document or {}).get("organization_id") or (document or {}).get("organizationId") or ""
        )
        if document is None or document_org != organization_id:
            raise CandidateScopeConflict(candidate_id)
        document_project = str(
            document.get("project_id") or document.get("projectId") or ""
        )

        if isinstance(scope, ProjectScopeUpload):
            await self._require_project_anchor(scope.project_id, organization_id)
            if document_project and document_project != str(scope.project_id):
                # The Document already belongs to another project; the anchor
                # would be conflicted from birth.
                raise CandidateScopeConflict(candidate_id)
            scope_state = ScopeClassificationState.PROJECT_SCOPE_CONFIRMED
            project_id: Optional[str] = str(scope.project_id)
        else:
            # An affirmative organisation decision by an authorised actor IS
            # confirmation - unlike the legacy corpus, where the same state was
            # an absence nobody chose.
            scope_state = ScopeClassificationState.ORG_SCOPE_CONFIRMED
            project_id = None

        pinned = {"_id": candidate_id, "organization_id": organization_id}
        try:
            await self._db[RECONCILIATION_COLLECTION].update_one(
                pinned,
                {
                    # Only ever on insert. _id and organization_id come from the
                    # filter, so they cannot be rewritten either.
                    "$setOnInsert": {
                        "candidate_id": candidate_id,
                        "canonical_document_id": canonical_document_id,
                        "module": module,
                        "scope_state": scope_state.value,
                        "scope_level": scope.scope_level,
                        "project_id": project_id,
                        # Type is per file and nobody has decided it yet.
                        "type_state": TypeClassificationState.TYPE_UNKNOWN.value,
                        # Operational context of the first upload. Never
                        # applicability, and never overwritten by a later one.
                        "selected_contract_id": selected_contract_id,
                        "uploaded_by": actor_id,
                        "scope_recorded_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError as exc:
            key = (getattr(exc, "details", None) or {}).get("keyPattern")
            if key not in (None, {"_id": 1}):
                raise
            # Either an identical create won a race to insert this id in this
            # organisation (the pinned upsert does not converge on its own, since
            # its filter is not the unique key), or the id exists under another
            # organisation. Re-read with the SAME pinned filter - never wider -
            # and let the comparison below decide; no row here means the id is
            # held elsewhere.
            if await self._db[RECONCILIATION_COLLECTION].find_one(pinned) is None:
                raise CandidateScopeConflict(candidate_id) from exc

        stored = await self._db[RECONCILIATION_COLLECTION].find_one(pinned)
        if stored is None:  # pragma: no cover - deleted between write and read
            raise CandidateScopeConflict(candidate_id)
        stored_project = str(stored.get("project_id") or "") or None
        if (
            str(stored.get("canonical_document_id") or "") != canonical_document_id
            or stored.get("module") != module
            or stored.get("scope_level") != scope.scope_level
            or stored_project != project_id
            # A later decision (AMBIGUOUS, INVALID, the other scope) stands; the
            # upload's scope is not the candidate's scope any more.
            or stored.get("scope_state") != scope_state.value
        ):
            # Same organisation, a different decision already recorded (or none,
            # on a materialised legacy row): the stored anchor stands, and the
            # caller is told rather than handed the scope they asked for.
            raise CandidateScopeConflict(candidate_id)
        return UploadedCandidate(
            candidate_id=candidate_id,
            canonical_document_id=str(stored["canonical_document_id"]),
            organization_id=str(stored["organization_id"]),
            scope_level=str(stored["scope_level"]),
            project_id=stored_project,
        )

    async def create_batch_candidates(
        self,
        scope: ContractUploadScope,
        *,
        canonical_document_ids: Sequence[str],
        organization_id: str,
        actor_id: str,
        selected_contract_id: Optional[str] = None,
    ) -> Tuple[UploadedCandidate, ...]:
        """One scope decision, applied identically to every file in the batch.

        Each file goes through ``create_candidate`` - the one insert-only write -
        so a batch can no more rewrite an existing candidate than a single upload
        can. Not atomic: files before a conflicting one are recorded, and the
        conflict is raised for the caller to report.
        """
        created = []
        for document_id in canonical_document_ids:
            created.append(
                await self.create_candidate(
                    scope,
                    canonical_document_id=document_id,
                    organization_id=organization_id,
                    actor_id=actor_id,
                    selected_contract_id=selected_contract_id,
                )
            )
        return tuple(created)

    async def _require_project_anchor(self, project_id: str, organization_id: str) -> None:
        # Production projects are ObjectId-keyed with an ObjectId organisation;
        # match both spellings of each, and only an active project.
        owner = ScopeService.object_id_query(str(organization_id))
        project = await self._db["projects"].find_one(
            {
                "_id": ScopeService.object_id_query(str(project_id)),
                "$or": [{"organization_id": owner}, {"organizationId": owner}],
                "is_active": {"$ne": False},
            }
        )
        if project is None:
            raise UnknownProjectAnchor(
                f"project {project_id} does not exist in organisation "
                f"{organization_id}; the request is rejected rather than widened "
                "to organisation scope"
            )
