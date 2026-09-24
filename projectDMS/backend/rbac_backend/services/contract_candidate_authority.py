"""Who may act on one reconciliation candidate, and at what scope.

The reconciliation routes authorise against ``?organization_id=`` with no project,
and ``PolicyService`` answers that for any member of the organisation - a Project
Admin of A included. Acting on a candidate of project B, or on one no project owns,
is a different question, and this module is where it is answered.

**The anchor.** A candidate's trustworthy project is established from exactly two
fields, both written by trusted code and both re-checked here:

* ``candidate.project_id`` - written only by an authorised scope decision
  (``ContractUploadScopeService.create_candidate``, which proves the project is in
  the organisation first). ``materialise_inventory`` never writes it.
* the canonical Document's own ``project_id`` - the field the Documents register
  authorises that very record by.

Everything else is evidence, never authority: ``session_evidence.project_id`` (a copy
of a TTL'd upload session, captured precisely because it is unverifiable later),
``scope_hint``, and any adjudicated ``scope_state``. The two fields must agree when
both are present, and the anchor must be a project of the candidate's organisation;
otherwise there is no anchor, which never means "any project" - it means the
candidate is organisation-owned for authorisation purposes.

**The decision.**

* An anchored candidate is authorised at ``(organization_id, anchor)`` through the
  ordinary ``PolicyService.authorize`` - so the Project Admin of A is refused B by the
  same scope rule that refuses them B's documents.
* An unanchored candidate, and any act that creates organisation-wide authority
  (adjudicating ``ORG_SCOPE_CONFIRMED``, promoting an organisation-scope candidate,
  materialising the organisation's inventory), additionally needs organisation-wide
  scope (``ScopeService.has_organization_wide_scope``). A permission cannot answer
  this: Project Admin's seeded role carries ``dms.admin``, which satisfies every
  ``dms.*`` check, and legacy aliases hand ``dms.contract.catalogue.browse`` to
  Project User - so ``ORG_TIER_ONLY_PERMISSIONS`` does not survive to the policy.

A refusal here is a 403: the candidate is inside the caller's organisation, so its
existence is no secret from them. Foreign and missing candidates were already a 404
before this module is reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Union

from fastapi import HTTPException, status

from .contract_migration_reconciliation import (
    RECONCILIATION_COLLECTION,
    CandidateNotFound,
    scoped_candidate_filter,
)
from .scope_service import ScopeService

__all__ = [
    "CandidateAuthority",
    "authorize_candidate",
    "caller_may_see_candidate",
    "candidate_project_anchor",
    "load_canonical_document",
    "require_organization_wide_scope",
]

ORGANIZATION_SCOPE_REQUIRED = "Not authorized: organization_scope_required"


@dataclass(frozen=True)
class CandidateAuthority:
    """A candidate the caller was authorised for, and the project that anchored it."""

    candidate: Dict[str, Any]
    project_id: Optional[str]


def _as_id(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


async def load_canonical_document(db: Any, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The candidate's canonical Document, however its ``_id`` is stored.

    Inventory records ``str(document["_id"])``, and production Documents are
    ObjectId-keyed, so an exact string match finds nothing for a real upload. The
    lookup accepts both spellings of this ONE id and nothing wider.
    """
    document_id = _as_id(candidate.get("canonical_document_id"))
    if not document_id:
        return None
    return await db["documents"].find_one({"_id": ScopeService.object_id_query(document_id)})


def _document_organization(document: Dict[str, Any]) -> str:
    return _as_id(document.get("organization_id") or document.get("organizationId"))


async def candidate_project_anchor(
    db: Any, candidate: Dict[str, Any], document: Optional[Dict[str, Any]]
) -> Optional[str]:
    """The trustworthy project of this candidate, or ``None``. See the module doc."""
    organization_id = _as_id(candidate.get("organization_id"))
    stored = _as_id(candidate.get("project_id"))
    documented = ""
    if document is not None and _document_organization(document) == organization_id:
        documented = _as_id(document.get("project_id") or document.get("projectId"))
    if stored and documented and stored != documented:
        # Two trusted writers disagree: neither is believed.
        return None
    anchor = stored or documented
    if not anchor or not organization_id:
        return None
    if not await ScopeService(db).project_belongs_to_organization(
        project_id=anchor, organization_id=organization_id
    ):
        return None
    return anchor


async def require_organization_wide_scope(
    policy: Any, current_user: Any, *, permission: str, organization_id: str, audit: bool = True
) -> None:
    """``permission`` over the whole organisation, not only assigned projects."""
    await policy.authorize(
        current_user,
        permission,
        resource_type="contract_reconciliation",
        organization_id=organization_id,
        project_id=None,
        audit=audit,
    )
    if not await policy.scope_service.has_organization_wide_scope(
        current_user, organization_id=organization_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ORGANIZATION_SCOPE_REQUIRED)


async def authorize_candidate(
    policy: Any,
    current_user: Any,
    *,
    db: Any,
    candidate_id: str,
    organization_id: str,
    permission: str,
    organization_wide: Union[bool, Callable[[Dict[str, Any]], bool]] = False,
    selection: Any = None,
) -> CandidateAuthority:
    """Load the candidate inside ``organization_id``, then authorise at its anchor.

    ``organization_wide`` is for acts that create organisation-wide authority, which
    need organisation scope whatever the anchor. It may be a predicate over the
    loaded candidate (promotion: only an organisation-scope candidate).

    ``selection`` is the request's validated navbar selection (CL-4A). The
    candidate is held to it exactly as a record is: an anchored candidate must be
    in the selected project, an unanchored one in the selected organisation
    (403 ``context_forbidden``, superadmin included) - after the scoped load, so a
    foreign or missing id is still the 404, and before the policy is asked.
    """
    candidate = await db[RECONCILIATION_COLLECTION].find_one(
        scoped_candidate_filter(candidate_id, organization_id)
    )
    if candidate is None:
        raise CandidateNotFound()
    document = await load_canonical_document(db, candidate)
    anchor = await candidate_project_anchor(db, candidate, document)
    if selection is not None:
        await selection.require_record(
            {"project_id": anchor, "organization_id": candidate.get("organization_id")},
            allow_unscoped=True,
        )

    wide = organization_wide(candidate) if callable(organization_wide) else organization_wide
    if anchor is None or wide:
        await require_organization_wide_scope(
            policy, current_user, permission=permission, organization_id=str(organization_id)
        )
    else:
        await policy.authorize(
            current_user,
            permission,
            resource_type="contract_reconciliation_candidate",
            resource_id=candidate_id,
            organization_id=str(organization_id),
            project_id=anchor,
        )
    return CandidateAuthority(candidate=candidate, project_id=anchor)


async def caller_may_see_candidate(
    policy: Any,
    current_user: Any,
    *,
    db: Any,
    candidate: Dict[str, Any],
    organization_wide: bool,
    selected_project: Optional[str] = None,
) -> bool:
    """Row visibility for the review queue: bounded to the caller's projects.

    With a project selected, a row anchored elsewhere is not listed, whoever asks;
    an unanchored row stays visible to organisation-wide scope only.
    """
    document = await load_canonical_document(db, candidate)
    anchor = await candidate_project_anchor(db, candidate, document)
    if selected_project and anchor is not None and anchor != selected_project:
        return False
    if organization_wide:
        return True
    if anchor is None:
        return False
    return await policy.scope_service.is_client_scope_allowed(
        current_user,
        organization_id=_as_id(candidate.get("organization_id")),
        project_id=anchor,
    )
