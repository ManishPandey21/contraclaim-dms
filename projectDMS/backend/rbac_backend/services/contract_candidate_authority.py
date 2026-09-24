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
  authorises that very record by (``DocumentUpdate`` cannot change it).

Everything else is evidence, never authority: ``session_evidence.project_id`` (a copy
of a TTL'd upload session, captured precisely because it is unverifiable later),
``scope_hint``, and any adjudicated ``scope_state``.

Three outcomes, kept apart on purpose:

* **anchored** - a named project that exists, is active, and belongs to the
  candidate's organisation, with the two fields agreeing where both are present;
* **unanchored** - neither field names a project (an organisation-level Document);
* **conflicted** - a project is named but fails validation: the fields disagree, the
  project is missing, inactive or another organisation's, or the Document itself is
  not the candidate organisation's. ``classify_scope`` calls the cross-organisation
  case INVALID; a conflict is never quietly treated as organisation-owned, so it
  can be adjudicated (to INVALID, say) only with organisation-wide scope, and it
  never promotes.

**The decision.**

* An anchored candidate is held to the navbar selection as a record of that project
  and authorised at ``(organization_id, anchor)`` through ``PolicyService.authorize``
  - so the Project Admin of A is refused B by the same scope rule that refuses them
  B's documents.
* An unanchored or conflicted candidate, and any act that creates or overrides
  organisation-wide authority, is held to the selected organisation only (it belongs
  to no project, so no project selection is demanded - as CL-4A's organisation-level
  contract uploads) and needs organisation-wide scope
  (``ScopeService.has_organization_wide_scope``). A permission cannot answer this:
  Project Admin's seeded role carries ``dms.admin``, which satisfies every ``dms.*``
  check, and a legacy alias hands ``dms.contract.catalogue.browse`` to Project User,
  so ``ORG_TIER_ONLY_PERMISSIONS`` does not survive to the policy.

A refusal here is a 403: the candidate is inside the caller's organisation, so its
existence is no secret from them. Foreign and missing candidates are the 404 before
any of this runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from fastapi import HTTPException, status

from ..core.tenant_context import SELECTION_REQUIRED, TenantContextError
from .contract_migration_reconciliation import (
    RECONCILIATION_COLLECTION,
    CandidateNotFound,
    scoped_candidate_filter,
)
from .publication_policy import resolve_canonical_document
from .scope_service import ScopeService

__all__ = [
    "CandidateAnchor",
    "CandidateAuthority",
    "authorize_candidate",
    "candidate_project_anchor",
    "load_canonical_document",
    "require_organization_wide_scope",
    "require_selected_organization",
    "resolve_candidate_anchor",
    "visible_candidates",
]

ORGANIZATION_SCOPE_REQUIRED = "Not authorized: organization_scope_required"


@dataclass(frozen=True)
class CandidateAnchor:
    """``project_id`` when anchored; ``conflict`` when a named project failed."""

    project_id: Optional[str] = None
    conflict: bool = False


@dataclass(frozen=True)
class CandidateAuthority:
    """A candidate the caller was authorised for, and what anchored it."""

    candidate: Dict[str, Any]
    anchor: CandidateAnchor

    @property
    def project_id(self) -> Optional[str]:
        return self.anchor.project_id


def _as_id(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _organization_of(row: Mapping[str, Any]) -> str:
    return _as_id(row.get("organization_id") or row.get("organizationId"))


def _project_of(row: Optional[Mapping[str, Any]]) -> str:
    if not row:
        return ""
    return _as_id(row.get("project_id") or row.get("projectId"))


async def load_canonical_document(
    db: Any, candidate: Mapping[str, Any], *, session: Any = None
):
    """The candidate's canonical Document, through the one identity resolver.

    Inventory records ``str(document["_id"])`` and production Documents are
    ObjectId-keyed; ``resolve_canonical_document`` tries each spelling of this one
    id, in a fixed order, and nothing wider.
    """
    return await resolve_canonical_document(
        db, _as_id(candidate.get("canonical_document_id")), session=session
    )


def _anchor_from(
    candidate: Mapping[str, Any],
    document: Optional[Mapping[str, Any]],
    projects: Mapping[str, Mapping[str, Any]],
) -> CandidateAnchor:
    """Pure: the anchor from the candidate, its Document and the named project rows."""
    organization_id = _as_id(candidate.get("organization_id"))
    stored = _as_id(candidate.get("project_id"))
    documented = ""
    if document is not None:
        if _organization_of(document) != organization_id:
            # The pointer leaves the organisation. Nothing it says is believed.
            return CandidateAnchor(conflict=True)
        documented = _project_of(document)
    if stored and documented and stored != documented:
        return CandidateAnchor(conflict=True)
    named = stored or documented
    if not named:
        return CandidateAnchor()
    project = projects.get(named)
    if (
        not organization_id
        or project is None
        or project.get("is_active") is False
        or _organization_of(project) != organization_id
    ):
        return CandidateAnchor(conflict=True)
    return CandidateAnchor(project_id=named)


async def _projects_by_id(
    db: Any, project_ids: Iterable[str]
) -> Dict[str, Dict[str, Any]]:
    wanted = sorted({pid for pid in project_ids if pid})
    if not wanted:
        return {}
    keys: List[Any] = []
    for pid in wanted:
        keys.extend(ScopeService.object_id_query(pid)["$in"])
    rows = await db["projects"].find({"_id": {"$in": keys}}).to_list(length=None)
    return {str(row["_id"]): row for row in rows}


async def resolve_candidate_anchor(
    db: Any, candidate: Mapping[str, Any], document: Optional[Mapping[str, Any]]
) -> CandidateAnchor:
    """The anchor of one candidate. See the module doc for the three outcomes."""
    projects = await _projects_by_id(
        db, [_as_id(candidate.get("project_id")), _project_of(document)]
    )
    return _anchor_from(candidate, document, projects)


async def candidate_project_anchor(
    db: Any, candidate: Mapping[str, Any], document: Optional[Mapping[str, Any]]
) -> Optional[str]:
    """The trustworthy project, or ``None`` (unanchored or conflicted)."""
    return (await resolve_candidate_anchor(db, candidate, document)).project_id


async def require_organization_wide_scope(
    policy: Any,
    current_user: Any,
    *,
    permission: str,
    organization_id: str,
    audit: bool = True,
) -> None:
    """``permission`` over the whole organisation, not only assigned projects.

    Scope first: were it after an audited authorize, a refusal here would sit in
    the audit trail as "allow".
    """
    if not await policy.scope_service.has_organization_wide_scope(
        current_user, organization_id=organization_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ORGANIZATION_SCOPE_REQUIRED
        )
    await policy.authorize(
        current_user,
        permission,
        resource_type="contract_reconciliation",
        organization_id=organization_id,
        project_id=None,
        audit=audit,
    )


def require_selected_organization(selection: Any, organization_id: Any) -> None:
    """An organisation-level write states its scope: a selected organisation (CL-4A).

    Reads may run with nothing selected and are bounded by the policy; a write
    with nothing selected is 400 ``selection_required``. (That the selection is
    THIS organisation is ``require_organization``.)
    """
    if selection is not None and not getattr(selection, "organization_id", None):
        raise TenantContextError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=SELECTION_REQUIRED,
            message="Select an organisation in the navbar to continue.",
        )


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
    """Load the candidate inside ``organization_id``, hold it, then authorise it.

    ``organization_wide`` is for acts that create or override organisation-wide
    authority; it may be a predicate over the loaded candidate.

    ``selection`` is the request's validated navbar selection (CL-4A). After the
    scoped load - so a foreign or missing id is still the 404 - an anchored
    candidate is held as a record of its project (400 ``selection_required`` with
    nothing selected, 403 ``context_forbidden`` for another project, superadmin
    included) and an organisation-level one to the selected organisation.
    """
    candidate = await db[RECONCILIATION_COLLECTION].find_one(
        scoped_candidate_filter(candidate_id, organization_id)
    )
    if candidate is None:
        raise CandidateNotFound()
    document = await load_canonical_document(db, candidate)
    anchor = await resolve_candidate_anchor(db, candidate, document)

    if selection is not None:
        if anchor.project_id:
            await selection.require_project(
                anchor.project_id, candidate.get("organization_id")
            )
        else:
            require_selected_organization(selection, candidate.get("organization_id"))
            await selection.require_organization(candidate.get("organization_id"))

    wide = (
        organization_wide(candidate)
        if callable(organization_wide)
        else organization_wide
    )
    if anchor.project_id is None or wide:
        await require_organization_wide_scope(
            policy,
            current_user,
            permission=permission,
            organization_id=str(organization_id),
        )
    else:
        await policy.authorize(
            current_user,
            permission,
            resource_type="contract_reconciliation_candidate",
            resource_id=candidate_id,
            organization_id=str(organization_id),
            project_id=anchor.project_id,
        )
    return CandidateAuthority(candidate=candidate, anchor=anchor)


async def visible_candidates(
    policy: Any,
    current_user: Any,
    *,
    db: Any,
    rows: Sequence[Dict[str, Any]],
    organization_wide: bool,
    selected_project: Optional[str] = None,
) -> List[Tuple[Dict[str, Any], CandidateAnchor]]:
    """Row visibility for the review queue, in three queries rather than 2N.

    Each visible row comes back with its anchor, so the queue can say what acting
    on it needs (its project selected, or organisation scope) and why a conflicted
    one will never promote.

    Organisation-wide scope sees every row; anyone else sees the rows anchored to a
    project they are assigned to. With a project selected, a row anchored elsewhere
    is not listed whoever asks. Unanchored and conflicted rows are organisation
    business: visible to organisation-wide scope only.
    """
    if not rows:
        return []
    document_keys: List[Any] = []
    for row in rows:
        document_keys.extend(
            ScopeService.object_id_query(_as_id(row.get("canonical_document_id")))[
                "$in"
            ]
        )
    documents = {
        str(doc["_id"]): doc
        for doc in await db["documents"]
        .find({"_id": {"$in": document_keys}})
        .to_list(length=None)
    }
    by_row = [
        (row, documents.get(_as_id(row.get("canonical_document_id")))) for row in rows
    ]
    projects = await _projects_by_id(
        db,
        [
            pid
            for row, doc in by_row
            for pid in (_as_id(row.get("project_id")), _project_of(doc))
        ],
    )
    assigned = (
        set()
        if organization_wide
        else await policy.scope_service.client_project_ids(current_user)
    )

    visible: List[Tuple[Dict[str, Any], CandidateAnchor]] = []
    for row, document in by_row:
        anchor = _anchor_from(row, document, projects)
        if (
            selected_project
            and anchor.project_id
            and anchor.project_id != selected_project
        ):
            continue
        if organization_wide or (anchor.project_id and anchor.project_id in assigned):
            visible.append((row, anchor))
    return visible
