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

from ..core.permissions import Permissions
from ..models.contract_upload_scope import (
    ContractUploadScope,
    OrganizationScopeUpload,
    ProjectScopeUpload,
)
from .contract_migration_reconciliation import (
    RECONCILIATION_COLLECTION,
    ScopeClassificationState,
    TypeClassificationState,
    candidate_identity,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES",
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
        """Record the scope decision against a canonical Document.

        ``selected_contract_id`` is captured as operational context only. It is
        never turned into applicability: the uploader chose a place to work, not
        a legal fact about which contract this instrument governs.
        """
        if isinstance(scope, ProjectScopeUpload):
            await self._require_project_anchor(scope.project_id, organization_id)
            scope_state = ScopeClassificationState.PROJECT_SCOPE_CONFIRMED
            project_id: Optional[str] = scope.project_id
        else:
            # An affirmative organisation decision by an authorised actor IS
            # confirmation - unlike the legacy corpus, where the same state was
            # an absence nobody chose.
            scope_state = ScopeClassificationState.ORG_SCOPE_CONFIRMED
            project_id = None

        candidate_id = candidate_identity(
            module=module, canonical_document_id=canonical_document_id
        )
        await self._db[RECONCILIATION_COLLECTION].update_one(
            {"_id": candidate_id},
            {
                "$set": {
                    "candidate_id": candidate_id,
                    "canonical_document_id": canonical_document_id,
                    "organization_id": organization_id,
                    "module": module,
                    "scope_state": scope_state.value,
                    "scope_level": scope.scope_level,
                    "project_id": project_id,
                    # Type is per file and nobody has decided it yet.
                    "type_state": TypeClassificationState.TYPE_UNKNOWN.value,
                    # Operational context. Never applicability.
                    "selected_contract_id": selected_contract_id,
                    "uploaded_by": actor_id,
                    "scope_recorded_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        return UploadedCandidate(
            candidate_id=candidate_id,
            canonical_document_id=canonical_document_id,
            organization_id=organization_id,
            scope_level=scope.scope_level,
            project_id=project_id,
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
        """One scope decision, applied identically to every file in the batch."""
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
        project = await self._db["projects"].find_one(
            {"_id": project_id, "organization_id": organization_id}
        )
        if project is None:
            raise UnknownProjectAnchor(
                f"project {project_id} does not exist in organisation "
                f"{organization_id}; the request is rejected rather than widened "
                "to organisation scope"
            )
