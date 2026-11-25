"""Scoped, audited reviewer role assignments (plan 4B-07, D-06, AQ-2).

Who holds which reviewer role, where, and on whose word. Three rules decide
everything here, and each is stated in one place:

- **What an assignment confers** is ``core.permissions.reviewer_roles_from_assignments``.
  This module only loads the records and calls it.
- **Who may grant or revoke.** Platform scope: a Super Admin, and never for
  themselves. Organisation or project scope: someone who manages users and
  reaches the whole of that organisation. Nobody grants themselves a role at
  any scope.
- **Who may hold.** Organisation and project scope: an active member of that
  organisation or project. Global and service accounts hold nothing there.

Nothing here is read from a role name, an account type or Super Admin status:
a Super Admin approves a skill pack only if someone else assigned them the role.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Set

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from ..core.permissions import (
    REVIEWER_ROLE_KEYS,
    REVIEWER_SCOPE_ORGANIZATION,
    REVIEWER_SCOPE_PLATFORM,
    REVIEWER_SCOPE_PROJECT,
    Permissions,
    actor_role_names,
    reviewer_assignment_is_live,
    reviewer_roles_from_assignments,
)
from ..models.reviewer_assignment import (
    DEFAULT_EXPIRY_DAYS,
    MAX_EXPIRY_DAYS,
    REVIEWER_ROLE_ASSIGNMENTS,
    REVIEWER_ROLE_LABELS,
    LegacyReviewerRoleHolder,
    LegacyReviewerRoleReport,
    MyReviewerRoles,
    ReviewerAssignment,
    ReviewerAssignmentCreate,
    ReviewerAssignmentView,
)
from ..utils.datetime import now_utc
from .audit_event_service import AuditEventService
from .picker_service import GLOBAL_ROLE_KEYS, NON_PERSON_ACCOUNT_TYPES, display_name
from .policy_service import PolicyService
from .scope_service import ScopeService


def _refuse(code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": error_code, "user_message": message, "detail": message})


def _naive_utc(moment: datetime) -> datetime:
    """MongoDB returns naive UTC; store the same so comparisons never mix the two."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _user_lookup(user_id: str) -> Dict[str, Any]:
    values: List[Any] = [str(user_id)]
    if ObjectId.is_valid(str(user_id)):
        values.append(ObjectId(str(user_id)))
    return {"_id": {"$in": values}}


def _as_principal(doc: Dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(doc.get("_id")),
        roles=[str(role) for role in (doc.get("roles") or []) if role],
        organization_id=doc.get("organization_id"),
        organizations=list(doc.get("organizations") or []),
        projects=list(doc.get("projects") or []),
        account_type=doc.get("account_type"),
    )


def _state(doc: Dict[str, Any], now: datetime) -> str:
    if doc.get("revoked_at") is not None:
        return "revoked"
    return "active" if reviewer_assignment_is_live(doc, now) else "expired"


async def load_live_assignments(db: Any, user_id: str) -> List[Dict[str, Any]]:
    """A user's unrevoked assignments. Expiry is judged by the caller's clock."""
    if not user_id:
        return []
    return await db[REVIEWER_ROLE_ASSIGNMENTS].find({"user_id": str(user_id), "revoked_at": None}).to_list(length=500)


async def reviewer_roles_for(
    db: Any,
    user: Any,
    *,
    scope_type: str,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Set[str]:
    """The reviewer roles ``user`` holds for one scope, from live assignments only.

    Read on every request, so a revocation or an expiry takes effect on the next one.
    """
    user_id = str(getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else "") or "")
    assignments = await load_live_assignments(db, user_id)
    return reviewer_roles_from_assignments(
        assignments,
        user_id=user_id,
        scope_type=scope_type,
        organization_id=organization_id,
        project_id=project_id,
        now=now_utc(),
    )


async def platform_reviewer_roles(db: Any, user: Any) -> Set[str]:
    """Roles that may sign off a system skill pack. Platform assignments, nothing else."""
    return await reviewer_roles_for(db, user, scope_type=REVIEWER_SCOPE_PLATFORM)


async def arbitration_reviewer_roles(
    db: Any, user: Any, *, organization_id: Optional[str], project_id: Optional[str]
) -> Set[str]:
    """Reviewer roles that answer an arbitration gate on one case.

    Two sources, and no other:

    - live assignments for the case's project or organisation;
    - the user's own role list, exactly as the gates read it before assignments
      existed, so case work that relied on hand-made roles keeps working (D-06).
      The role list is never consulted for platform authority. Which cases a
      user can reach at all is decided by the route's own authorisation, before
      any gate role is read; this function does not widen it.
    """
    scope = REVIEWER_SCOPE_PROJECT if project_id else REVIEWER_SCOPE_ORGANIZATION
    assigned = await reviewer_roles_for(
        db, user, scope_type=scope, organization_id=organization_id, project_id=project_id
    )
    legacy = actor_role_names(user)
    from . import built_in_role_scope

    if built_in_role_scope.enforced():
        # M1 (BUILT_IN_ROLE_ASSIGNMENTS=on): a bare reviewer key on the role list is a built-in
        # role and counts only where it is assigned (the primary organisation, or an explicit
        # role_assignments row); reviewer_role_assignments above are already scoped.
        case_org = organization_id
        if not case_org and project_id:
            case_org = await built_in_role_scope.project_organisation(db, str(project_id))
            if not case_org:
                return assigned
        legacy = await built_in_role_scope.role_names_in(db, user, legacy, case_org)
    return assigned | legacy


class ReviewerAssignmentService:
    def __init__(self, db: Any, policy: Optional[PolicyService] = None) -> None:
        self.db = db
        self.policy = policy or PolicyService(db=db)
        self.scope = ScopeService(db)
        self.audit = AuditEventService(db)

    @property
    def _collection(self) -> Any:
        return self.db[REVIEWER_ROLE_ASSIGNMENTS]

    # ------------------------------------------------------------------ scope
    async def _project_organization(self, project_id: str) -> str:
        project = await self.db.projects.find_one(_user_lookup(project_id), {"organization_id": 1})
        organization_id = (project or {}).get("organization_id")
        if not organization_id:
            raise _refuse(status.HTTP_404_NOT_FOUND, "project_not_found", "That project could not be found.")
        return str(organization_id)

    async def _resolve_scope(
        self, scope_type: str, organization_id: Optional[str], project_id: Optional[str]
    ) -> tuple[Optional[str], Optional[str]]:
        """The organisation and project a scope names, read from the project where there is one."""
        if scope_type == REVIEWER_SCOPE_PLATFORM:
            return None, None
        if scope_type == REVIEWER_SCOPE_PROJECT:
            owner = await self._project_organization(str(project_id))
            if organization_id and str(organization_id) != owner:
                raise _refuse(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "reviewer_scope_mismatch",
                    "That project does not belong to that organisation.",
                )
            return owner, str(project_id)
        return str(organization_id), None

    async def _require_authority(self, actor: Any, scope_type: str, organization_id: Optional[str]) -> None:
        """Whether ``actor`` may grant, revoke or list at this scope."""
        if scope_type == REVIEWER_SCOPE_PLATFORM:
            if not self.scope.is_superadmin(actor):
                raise _refuse(
                    status.HTTP_403_FORBIDDEN,
                    "reviewer_platform_grant_forbidden",
                    "Only a Super Admin can grant or withdraw a platform reviewer role.",
                )
            return
        # An administrator of that organisation, for that organisation only. A
        # Project Admin manages users too, so the permission alone is not enough.
        await self.policy.authorize(
            actor,
            Permissions.USER_MANAGE,
            resource_type="reviewer_role_assignment",
            organization_id=organization_id,
            audit=False,
        )
        if not await self.scope.has_organization_wide_scope(actor, organization_id=organization_id):
            raise _refuse(
                status.HTTP_403_FORBIDDEN,
                "reviewer_grant_needs_organisation_admin",
                "Only an administrator of that organisation can grant or withdraw this reviewer role.",
            )

    async def _eligible_holder(
        self, user_id: str, scope_type: str, organization_id: Optional[str], project_id: Optional[str]
    ) -> Dict[str, Any]:
        doc = await self.db.users.find_one(_user_lookup(user_id))
        if not doc or doc.get("disabled") is True:
            raise _refuse(status.HTTP_404_NOT_FOUND, "reviewer_user_not_found", "That person could not be found.")
        principal = _as_principal(doc)
        if str(principal.account_type or "").lower() in NON_PERSON_ACCOUNT_TYPES:
            raise _refuse(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "reviewer_user_not_a_person",
                "A reviewer role is held by a person, not by a service account.",
            )
        if scope_type == REVIEWER_SCOPE_PLATFORM:
            return doc
        is_global = bool({role.lower() for role in principal.roles} & GLOBAL_ROLE_KEYS)
        member = not is_global and await self.scope.is_client_scope_allowed(
            principal, organization_id=organization_id, project_id=project_id
        )
        if not member:
            raise _refuse(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "reviewer_user_not_a_member",
                "That person is not a member of that organisation or project.",
            )
        return doc

    @staticmethod
    def _expiry(scope_type: str, requested: Optional[datetime], now: datetime) -> Optional[datetime]:
        if requested is None:
            return None if scope_type == REVIEWER_SCOPE_PLATFORM else now + timedelta(days=DEFAULT_EXPIRY_DAYS)
        expires_at = _naive_utc(requested)
        if expires_at <= now:
            raise _refuse(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "reviewer_expiry_in_the_past", "The end date must be in the future."
            )
        if scope_type != REVIEWER_SCOPE_PLATFORM and expires_at > now + timedelta(days=MAX_EXPIRY_DAYS):
            raise _refuse(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "reviewer_expiry_too_long",
                "An organisation or project reviewer role runs for at most twelve months. Grant it again after that.",
            )
        return expires_at

    # ---------------------------------------------------------------- actions
    async def grant(self, payload: ReviewerAssignmentCreate, actor: Any) -> ReviewerAssignmentView:
        actor_id = str(getattr(actor, "id", "") or "")
        if not actor_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        organization_id, project_id = await self._resolve_scope(
            payload.scope_type, payload.organization_id, payload.project_id
        )
        await self._require_authority(actor, payload.scope_type, organization_id)
        if str(payload.user_id) == actor_id:
            raise _refuse(
                status.HTTP_403_FORBIDDEN,
                "reviewer_self_grant",
                "You cannot grant a reviewer role to yourself. Another administrator must do it.",
            )
        holder = await self._eligible_holder(payload.user_id, payload.scope_type, organization_id, project_id)
        now = _naive_utc(now_utc())
        key = {
            "user_id": str(holder["_id"]),
            "role_key": payload.role_key,
            "scope_type": payload.scope_type,
            "organization_id": organization_id,
            "project_id": project_id,
        }
        # A lapsed grant no longer holds the slot, so the role can be granted again.
        await self._collection.update_many(
            {**key, "active": True, "expires_at": {"$ne": None, "$lte": now}}, {"$unset": {"active": ""}}
        )
        assignment_id = uuid.uuid4().hex
        doc: Dict[str, Any] = {
            "_id": assignment_id,
            **key,
            "granted_by": actor_id,
            "reason": payload.reason,
            "granted_at": now,
            "expires_at": self._expiry(payload.scope_type, payload.expires_at, now),
            "revoked_at": None,
            "revoked_by": None,
            "revoke_reason": None,
            # Present only while the grant holds its slot; the unique index reads it.
            "active": True,
        }
        try:
            await self._collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise _refuse(
                status.HTTP_409_CONFLICT,
                "reviewer_assignment_exists",
                "That person already holds this reviewer role at this scope.",
            ) from exc
        await self.audit.emit(
            action="reviewer_assignment.granted",
            actor_id=actor_id,
            resource_type="reviewer_role_assignment",
            resource_id=assignment_id,
            organization_id=organization_id,
            project_id=project_id,
            reason=payload.reason,
            after={name: value for name, value in doc.items() if name != "active"},
            metadata={"role_key": payload.role_key, "scope_type": payload.scope_type, "user_id": key["user_id"]},
        )
        return (await self._views([doc]))[0]

    async def revoke(self, assignment_id: str, reason: str, actor: Any) -> ReviewerAssignmentView:
        actor_id = str(getattr(actor, "id", "") or "")
        before = await self._collection.find_one({"_id": str(assignment_id)})
        if not before:
            raise _refuse(status.HTTP_404_NOT_FOUND, "reviewer_assignment_not_found", "That assignment could not be found.")
        try:
            await self._require_authority(actor, before["scope_type"], before.get("organization_id"))
        except HTTPException as exc:
            if exc.status_code == status.HTTP_403_FORBIDDEN and before["scope_type"] != REVIEWER_SCOPE_PLATFORM:
                # Do not tell another tenant's administrator that the record exists.
                raise _refuse(
                    status.HTTP_404_NOT_FOUND, "reviewer_assignment_not_found", "That assignment could not be found."
                ) from exc
            raise
        if before.get("revoked_at") is None:
            now = _naive_utc(now_utc())
            await self._collection.update_one(
                {"_id": before["_id"], "revoked_at": None},
                {
                    "$set": {"revoked_at": now, "revoked_by": actor_id, "revoke_reason": reason},
                    "$unset": {"active": ""},
                },
            )
            after = await self._collection.find_one({"_id": before["_id"]})
            await self.audit.emit(
                action="reviewer_assignment.revoked",
                actor_id=actor_id,
                resource_type="reviewer_role_assignment",
                resource_id=str(before["_id"]),
                organization_id=before.get("organization_id"),
                project_id=before.get("project_id"),
                reason=reason,
                before={name: value for name, value in before.items() if name != "active"},
                after={name: value for name, value in (after or {}).items() if name != "active"},
                metadata={
                    "role_key": before.get("role_key"),
                    "scope_type": before.get("scope_type"),
                    "user_id": before.get("user_id"),
                },
            )
            before = after or before
        return (await self._views([before]))[0]

    async def list(
        self,
        actor: Any,
        *,
        scope_type: str,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        include_inactive: bool = False,
    ) -> List[ReviewerAssignmentView]:
        organization_id, project_id = await self._resolve_scope(scope_type, organization_id, project_id)
        await self._require_authority(actor, scope_type, organization_id)
        query: Dict[str, Any] = {"scope_type": scope_type}
        if scope_type != REVIEWER_SCOPE_PLATFORM:
            query["organization_id"] = organization_id
        if scope_type == REVIEWER_SCOPE_PROJECT:
            query["project_id"] = project_id
        if not include_inactive:
            query["revoked_at"] = None
        docs = await self._collection.find(query).sort("granted_at", -1).to_list(length=1000)
        views = await self._views(docs)
        return views if include_inactive else [view for view in views if view.state == "active"]

    async def mine(self, actor: Any) -> MyReviewerRoles:
        actor_id = str(getattr(actor, "id", "") or "")
        now = now_utc()
        live = [doc for doc in await load_live_assignments(self.db, actor_id) if reviewer_assignment_is_live(doc, now)]
        return MyReviewerRoles(
            platform=sorted(
                reviewer_roles_from_assignments(
                    live, user_id=actor_id, scope_type=REVIEWER_SCOPE_PLATFORM, now=now
                )
            ),
            assignments=await self._views(live),
        )

    async def legacy_report(self, actor: Any, *, organization_id: Optional[str]) -> LegacyReviewerRoleReport:
        """People whose role list names a reviewer role. Read-only; converts nothing."""
        if organization_id:
            await self._require_authority(actor, REVIEWER_SCOPE_ORGANIZATION, str(organization_id))
            query: Dict[str, Any] = {
                "$or": [{"organization_id": str(organization_id)}, {"organizations": str(organization_id)}]
            }
        else:
            await self._require_authority(actor, REVIEWER_SCOPE_PLATFORM, None)
            query = {}
        query["roles"] = {"$in": sorted(REVIEWER_ROLE_KEYS)}
        docs = await self.db.users.find(
            query, {"roles": 1, "full_name": 1, "name": 1, "display_name": 1, "username": 1, "first_name": 1, "last_name": 1}
        ).to_list(length=2000)
        now = now_utc()
        holders: List[LegacyReviewerRoleHolder] = []
        counts: Dict[str, int] = {}
        for doc in docs:
            user_id = str(doc["_id"])
            live = await load_live_assignments(self.db, user_id)
            if organization_id:
                covered = reviewer_roles_from_assignments(
                    live,
                    user_id=user_id,
                    scope_type=REVIEWER_SCOPE_ORGANIZATION,
                    organization_id=str(organization_id),
                    now=now,
                )
            else:
                covered = reviewer_roles_from_assignments(
                    live, user_id=user_id, scope_type=REVIEWER_SCOPE_PLATFORM, now=now
                )
            for role in sorted({str(item).strip().lower() for item in doc.get("roles") or []} & REVIEWER_ROLE_KEYS):
                counts[role] = counts.get(role, 0) + 1
                holders.append(
                    LegacyReviewerRoleHolder(
                        user_id=user_id, user_name=display_name(doc), role_key=role, has_assignment=role in covered
                    )
                )
        return LegacyReviewerRoleReport(organization_id=organization_id, holders=holders, counts_by_role=counts)

    # ------------------------------------------------------------------ views
    async def _views(self, docs: Iterable[Dict[str, Any]]) -> List[ReviewerAssignmentView]:
        rows = list(docs)
        ids = sorted({str(row.get("user_id")) for row in rows if row.get("user_id")})
        lookup: List[Any] = list(ids) + [ObjectId(value) for value in ids if ObjectId.is_valid(value)]
        names: Dict[str, str] = {}
        if lookup:
            async for user in self.db.users.find(
                {"_id": {"$in": lookup}},
                {"full_name": 1, "name": 1, "display_name": 1, "username": 1, "first_name": 1, "last_name": 1},
            ):
                names[str(user["_id"])] = display_name(user)
        now = now_utc()
        views: List[ReviewerAssignmentView] = []
        for row in rows:
            record = ReviewerAssignment(**{name: value for name, value in row.items() if name != "active"})
            views.append(
                ReviewerAssignmentView(
                    id=record.id,
                    user_id=record.user_id,
                    user_name=names.get(record.user_id, "Unnamed user"),
                    role_key=record.role_key,
                    role_label=REVIEWER_ROLE_LABELS.get(record.role_key, record.role_key),
                    scope_type=record.scope_type,
                    organization_id=record.organization_id,
                    project_id=record.project_id,
                    granted_by=record.granted_by,
                    reason=record.reason,
                    granted_at=record.granted_at,
                    expires_at=record.expires_at,
                    revoked_at=record.revoked_at,
                    revoke_reason=record.revoke_reason,
                    state=_state(row, now),
                )
            )
        return views


__all__ = [
    "ReviewerAssignmentService",
    "arbitration_reviewer_roles",
    "load_live_assignments",
    "platform_reviewer_roles",
    "reviewer_roles_for",
]
