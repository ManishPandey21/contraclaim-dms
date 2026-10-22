from datetime import datetime
from typing import List, Dict, Any
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo import ReturnDocument

from ..core.security import (
    require_permission,
    get_current_user,
    build_scope_query,
    _expand_object_ids,
)
from ..core.database import get_db
from ..models.project import Project, ProjectStats
from ..models.representative import Representative
from ..models.notification import (
    ProjectNotificationSubscriptionResponse,
    ProjectNotificationSubscriptionUpdate,
)
from ..services.project_service import ProjectService
from ..services.permission_service import PermissionService
from ..services.authorization_service import AuthorizationService
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up

router = APIRouter()
permission_service = PermissionService()

# A "letter" is a correspondence upload; contract uploads live on the same
# collection but are counted separately elsewhere and must not inflate the card.
LETTER_UPLOAD_TYPE_PATTERN = r"^(incoming|outgoing)$"
# Mirrors DocumentService.list_documents so the card count matches exactly what
# the Letters Library shows when the user clicks through.
HIDDEN_LIFECYCLE_STATES = ["deleted", "duplicate_review", "duplicate"]

async def _find_by_id(coll, id_str: str):
    # Standard MongoDB IDs are 24-character hex strings
    if len(id_str) == 24:
        try:
            # Try finding as ObjectId first
            doc = await coll.find_one({"_id": ObjectId(id_str)})
            if doc:
                return doc
        except Exception:
            pass
    
    # Fallback to searching as a raw string if ObjectId fails or doesn't match
    return await coll.find_one({"_id": id_str})

async def _ensure_project_access(
    current_user,
    project_id: str,
    permission: str,
    db=None,
):
    canonical_permission = (
        "dms.dashboard.view" if permission == "projects:read" else "dms.project.manage"
    )
    organization_id = getattr(current_user, "organization_id", None)
    if db is not None:
        project = await _find_by_id(db.projects, project_id)
        if project:
            organization_id = str(project.get("organization_id") or organization_id or "")
    await PolicyService(db).authorize(
        current_user,
        canonical_permission,
        resource_type="project",
        resource_id=project_id,
        organization_id=organization_id,
        project_id=project_id,
    )

    allowed = await permission_service.check_resource_access(
        getattr(current_user, "id", None),
        permission,
        "project",
        project_id,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this project",
        )


def _project_subscription_response(doc: Dict[str, Any]) -> ProjectNotificationSubscriptionResponse:
    payload = dict(doc)
    payload.pop("_id", None)
    return ProjectNotificationSubscriptionResponse(**payload)


@router.post("/projects", response_model=Project)
async def create_project(
    project: Project,
    db = Depends(get_db),
    current_user = Depends(get_current_user),
    _: None = Depends(require_permission("projects:create")),
):
    # Only superadmin may create projects
    await AuthorizationService().require_role(current_user, "superadmin")
    await PolicyService(db).authorize(
        current_user,
        "dms.project.manage",
        resource_type="project",
        organization_id=project.organization_id,
    )
    organization = await _find_by_id(db.organizations, project.organization_id)
    if not organization:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Organization with id {project.organization_id} not found",
        )
    payload = project.model_dump(by_alias=True, exclude_none=True)
    # Always let Mongo generate the _id to keep it as an ObjectId
    payload.pop("_id", None)
    # payload["organization_id"] = str(organization.get("_id", project.organization_id))
    # If your DB uses ObjectIds for foreign keys, use this:
    try:
        payload["organization_id"] = ObjectId(str(organization["_id"]))
    except:
        payload["organization_id"] = str(organization["_id"])
    new_project = await db.projects.insert_one(payload)
    created_project = await db.projects.find_one({"_id": new_project.inserted_id})
    if not created_project:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create project",
        )
    # Normalize identifiers for the response model
    created_project["_id"] = str(created_project.get("_id", new_project.inserted_id))
    if isinstance(created_project.get("organization_id"), ObjectId):
        created_project["organization_id"] = str(created_project["organization_id"])
    return Project(**created_project)


@router.get("/projects1", response_model=List[Project])
async def read_projects_simple(
    db = Depends(get_db),
    current_user = Depends(get_current_user),
    _: None = Depends(require_permission("projects:read")),
):
    # Retained intentionally: this is the minimal direct-db endpoint that the
    # HTTP-level tenant-isolation regression suite (tests/test_http_isolation.py,
    # the H10/C1 work) exercises to prove build_scope_query enforces org scope at
    # the HTTP boundary. It is not a true duplicate of /projects (which paginates
    # via ProjectService and is harder to mock), so it stays as the test vehicle.
    selected_org = getattr(current_user, "organization_id", None)
    await PolicyService(db).authorize(
        current_user,
        "dms.dashboard.view",
        organization_id=str(selected_org) if selected_org else None,
        resource_type="project_collection",
        audit=False,
    )
    scope_filter = build_scope_query(
        current_user,
        organization_id=None,
        project_id=None,
        org_field="organization_id",
        project_field=None,
    )
    projects = await db.projects.find(scope_filter or {}).to_list(100)
    return [Project(**proj) for proj in projects]


@router.get("/projects", response_model=List[Project])
async def read_projects(
    organization_id: str | None = None,
    db = Depends(get_db),
    current_user = Depends(get_current_user),
    _: None = Depends(require_permission("projects:read")),
):
    try:
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection failed")

        selected_org = organization_id or getattr(current_user, "organization_id", None)
        await PolicyService(db).authorize(
            current_user,
            "dms.dashboard.view",
            organization_id=str(selected_org) if selected_org else None,
            resource_type="project_collection",
            audit=False,
        )
        service = ProjectService()
        # Deactivated projects must never reach the header selector. Absent flag
        # means active so pre-migration rows keep working.
        filters: Dict[str, Any] = {"is_active": {"$ne": False}}
        if organization_id:
            filters["organization_id"] = organization_id
        # Apply scope
        scope_filter = build_scope_query(
            current_user,
            organization_id=organization_id,
            project_id=None,
            org_field="organization_id",
            project_field=None,
        )
        if scope_filter:
            filters.update(scope_filter)

        projects, total_count = await service.get_projects_paginated(filters, {"skip": 0, "limit": 500})
        
        # Log the raw data for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Retrieved {len(projects)} projects from service, total: {total_count}")
        if projects:
            logger.info(f"Sample project structure: {projects[0]}")

        serialized: List[Project] = []
        for doc in projects:
            try:
                # The service now returns _id as string, so we can use it directly
                payload = dict(doc)
                # Ensure _id is present and is a string
                if "_id" in payload:
                    payload["_id"] = str(payload["_id"])
                else:
                    # Fallback if _id is missing
                    payload["_id"] = str(ObjectId())
                    logger.warning(f"Project missing _id, generated: {payload['_id']}")
                
                serialized.append(Project(**payload))
            except Exception as e:
                logger.error(f"Failed to serialize project {doc}: {e}")
                continue

        logger.info(f"Successfully serialized {len(serialized)} projects")
        return serialized

    except HTTPException:
        raise
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in read_projects: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {exc}")


def _normalize_id_list(value: Any) -> List[str]:
    """Flatten an assignment field into clean, de-duplicated string ids.

    User documents are inconsistent: `projects` is usually a list of ids, but
    some legacy rows hold a single comma-joined string. Both shapes must resolve
    to the same set of ids or project-scoped users are miscounted.
    """
    if value is None:
        return []
    raw_items = value if isinstance(value, (list, tuple, set)) else [value]
    ids: List[str] = []
    for item in raw_items:
        if item is None:
            continue
        for part in str(item).split(","):
            cleaned = part.strip()
            if cleaned and cleaned not in ids:
                ids.append(cleaned)
    return ids


async def _count_letters_by_project(db, project_ids: List[str]) -> Dict[str, Dict[str, int]]:
    """One aggregation returning incoming/outgoing letter counts per project."""
    if not project_ids:
        return {}

    pipeline: List[Dict[str, Any]] = [
        {
            "$match": {
                "project_id": {"$in": _expand_object_ids(project_ids)},
                # $nin also matches documents where the field is absent, so
                # pre-lifecycle rows keep counting as active.
                "lifecycle_state": {"$nin": HIDDEN_LIFECYCLE_STATES},
                "uploadType": {
                    "$regex": LETTER_UPLOAD_TYPE_PATTERN,
                    "$options": "i",
                },
            }
        },
        {
            "$group": {
                # project_id is stored as a string, but normalize defensively so
                # any ObjectId-typed rows group under the same key.
                "_id": {"$toString": "$project_id"},
                "total": {"$sum": 1},
                "incoming": {
                    "$sum": {
                        "$cond": [
                            {"$eq": [{"$toLower": "$uploadType"}, "incoming"]},
                            1,
                            0,
                        ]
                    }
                },
                "outgoing": {
                    "$sum": {
                        "$cond": [
                            {"$eq": [{"$toLower": "$uploadType"}, "outgoing"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
    ]

    counts: Dict[str, Dict[str, int]] = {}
    async for row in db.documents.aggregate(pipeline):
        project_id = str(row.get("_id") or "")
        if not project_id:
            continue
        counts[project_id] = {
            "total": int(row.get("total") or 0),
            "incoming": int(row.get("incoming") or 0),
            "outgoing": int(row.get("outgoing") or 0),
        }
    return counts


async def _count_members_by_project(db, project_orgs: Dict[str, str]) -> Dict[str, int]:
    """Count active users with access to each project.

    Access mirrors build_scope_query: a user with explicit project assignments
    is a member of exactly those projects; a user with none but an organisation
    assignment (orgadmin / orguser / superuser) has access to every project in
    that organisation. Superadmins are platform-wide and are not counted as
    members of any individual project.
    """
    project_ids = list(project_orgs.keys())
    if not project_ids:
        return {}

    org_ids = sorted({org for org in project_orgs.values() if org})
    or_clauses: List[Dict[str, Any]] = [
        {"projects": {"$in": _expand_object_ids(project_ids)}}
    ]
    if org_ids:
        expanded_orgs = _expand_object_ids(org_ids)
        or_clauses.append({"organization_id": {"$in": expanded_orgs}})
        or_clauses.append({"organizations": {"$in": expanded_orgs}})

    query: Dict[str, Any] = {
        "$and": [
            {"$or": or_clauses},
            {"is_active": {"$ne": False}},
            {"disabled": {"$ne": True}},
        ]
    }
    projection = {
        "roles": 1,
        "projects": 1,
        "organization_id": 1,
        "organizations": 1,
    }

    counts: Dict[str, int] = {project_id: 0 for project_id in project_ids}
    async for user in db.users.find(query, projection):
        roles = {str(role).strip().lower() for role in (user.get("roles") or [])}
        if "superadmin" in roles:
            continue

        assigned_projects = _normalize_id_list(user.get("projects"))
        if assigned_projects:
            # Explicit assignments restrict access, exactly as build_scope_query
            # does - never fall through to organisation-wide membership here.
            for project_id in assigned_projects:
                if project_id in counts:
                    counts[project_id] += 1
            continue

        user_orgs = set(
            _normalize_id_list(user.get("organization_id"))
            + _normalize_id_list(user.get("organizations"))
        )
        if not user_orgs:
            continue
        for project_id, org_id in project_orgs.items():
            if org_id and org_id in user_orgs:
                counts[project_id] += 1

    return counts


@router.get("/projects/stats", response_model=List[ProjectStats])
async def read_project_stats(
    organization_id: str | None = None,
    db = Depends(get_db),
    current_user = Depends(get_current_user),
    _: None = Depends(require_permission("projects:read")),
):
    """Live letter and team-member counts for every project the caller can list.

    Scoped with the same filter as GET /projects, so a project the caller cannot
    see never contributes a count. Costs two database round trips regardless of
    how many project cards are rendered.
    """
    try:
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection failed")

        selected_org = organization_id or getattr(current_user, "organization_id", None)
        await PolicyService(db).authorize(
            current_user,
            "dms.dashboard.view",
            organization_id=str(selected_org) if selected_org else None,
            resource_type="project_statistics",
            audit=False,
        )
        filters: Dict[str, Any] = {"is_active": {"$ne": False}}
        if organization_id:
            filters["organization_id"] = organization_id
        scope_filter = build_scope_query(
            current_user,
            organization_id=organization_id,
            project_id=None,
            org_field="organization_id",
            project_field=None,
        )
        if scope_filter:
            filters.update(scope_filter)

        service = ProjectService()
        projects, _total = await service.get_projects_paginated(
            filters, {"skip": 0, "limit": 500}
        )

        project_orgs: Dict[str, str] = {}
        for doc in projects:
            project_id = str(doc.get("_id") or "")
            if not project_id:
                continue
            org_value = doc.get("organization_id")
            project_orgs[project_id] = str(org_value) if org_value is not None else ""

        if not project_orgs:
            return []

        letter_counts = await _count_letters_by_project(db, list(project_orgs.keys()))
        member_counts = await _count_members_by_project(db, project_orgs)

        stats: List[ProjectStats] = []
        for project_id in project_orgs:
            letters = letter_counts.get(project_id) or {}
            stats.append(
                ProjectStats(
                    project_id=project_id,
                    letterCount=int(letters.get("total") or 0),
                    incomingCount=int(letters.get("incoming") or 0),
                    outgoingCount=int(letters.get("outgoing") or 0),
                    teamSize=member_counts.get(project_id, 0),
                )
            )
        return stats

    except HTTPException:
        raise
    except Exception as exc:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Error in read_project_stats: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {exc}")


@router.get("/projects/{project_id}", response_model=Project)
async def read_project(
    project_id: str,
    db = Depends(get_db),
    current_user = Depends(get_current_user),
    _: None = Depends(require_permission("projects:read")),
):
    await _ensure_project_access(current_user, project_id, "projects:read", db)
    project = await _find_by_id(db.projects, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return Project(**project)


@router.get(
    "/projects/{project_id}/notification-settings",
    response_model=ProjectNotificationSubscriptionResponse,
)
async def read_project_notification_settings(
    project_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    await _ensure_project_access(current_user, project_id, "projects:read", db)
    project = await _find_by_id(db.projects, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    existing = await db.project_notification_subscriptions.find_one(
        {"project_id": project_id, "user_id": current_user.id}
    )
    if existing:
        return _project_subscription_response(existing)

    now = datetime.utcnow()
    doc = {
        "project_id": project_id,
        "organization_id": str(project.get("organization_id")) if project.get("organization_id") else current_user.organization_id,
        "user_id": current_user.id,
        "subscribed": True,
        "event_settings": {},
        "role_default_source": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.project_notification_subscriptions.update_one(
        {"project_id": project_id, "user_id": current_user.id},
        {"$setOnInsert": doc},
        upsert=True,
    )
    return _project_subscription_response(doc)


@router.patch(
    "/projects/{project_id}/notification-settings",
    response_model=ProjectNotificationSubscriptionResponse,
)
async def update_project_notification_settings(
    project_id: str,
    payload: ProjectNotificationSubscriptionUpdate,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    await _ensure_project_access(current_user, project_id, "projects:read", db)
    project = await _find_by_id(db.projects, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    now = datetime.utcnow()
    update_doc = payload.model_dump(exclude_unset=True, exclude_none=True)
    update_doc["updated_at"] = now
    await db.project_notification_subscriptions.update_one(
        {"project_id": project_id, "user_id": current_user.id},
        {
            "$set": update_doc,
            "$setOnInsert": {
                "project_id": project_id,
                "organization_id": str(project.get("organization_id")) if project.get("organization_id") else current_user.organization_id,
                "user_id": current_user.id,
                "role_default_source": None,
                "created_at": now,
            },
        },
        upsert=True,
    )
    saved = await db.project_notification_subscriptions.find_one(
        {"project_id": project_id, "user_id": current_user.id}
    )
    return _project_subscription_response(saved or {})


@router.put("/projects/{project_id}", response_model=Project)
async def update_project(
    project_id: str,
    project_update: Project,
    db = Depends(get_db),
    current_user = Depends(get_current_user),
    _: None = Depends(require_permission("projects:update")),
):
    await _ensure_project_access(current_user, project_id, "projects:update", db)
    if project_update.organization_id:
        organization = await _find_by_id(db.organizations, project_update.organization_id)
        if not organization:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Organization with id {project_update.organization_id} not found",
            )
    existing = await _find_by_id(db.projects, project_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    update_doc = project_update.model_dump(
        by_alias=True,
        exclude_unset=True,
        exclude_none=True,
    )
    update_doc.pop("_id", None)
    updated_project = await db.projects.find_one_and_update(
        {"_id": existing["_id"]},
        {"$set": update_doc},
        return_document=ReturnDocument.AFTER,
    )
    if not updated_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return Project(**updated_project)


@router.delete("/projects/{project_id}", response_model=dict)
async def delete_project(
    project_id: str,
    request: Request,
    db = Depends(get_db),
    current_user = Depends(get_current_user),
    _: None = Depends(require_permission("projects:delete")),
):
    await require_step_up(request, current_user, action="projects.delete")
    await _ensure_project_access(current_user, project_id, "projects:delete", db)
    existing = await _find_by_id(db.projects, project_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    deleted_project = await db.projects.find_one_and_delete({"_id": existing["_id"]})
    if not deleted_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return {"message": "Project deleted successfully"}


# NOTE: GET /projects/{project_id}/representatives lives in representatives.py
# (registered first, so this copy was dead/shadowed) — removed to clear the
# duplicate operation ID.

@router.post("/projects/{project_id}/deactivate", response_model=dict)
async def deactivate_project(
    project_id: str,
    request: Request,
    db = Depends(get_db),
    current_user = Depends(get_current_user),
    _: None = Depends(require_permission("projects:delete")),
):
    await require_step_up(request, current_user, action="projects.deactivate")
    # Strict superadmin-only check
    roles = getattr(current_user, "roles", []) or []
    if "superadmin" not in [str(r).lower() for r in roles]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmin can deactivate projects")
    try:
        await _ensure_project_access(current_user, project_id, "projects:delete", db)
        service = ProjectService()
        ok = await service.delete_project(project_id, current_user)
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        return {"message": "Project deactivated successfully"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal server error: {exc}")
