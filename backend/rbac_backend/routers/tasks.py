from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import get_current_user, CurrentUser
from ..models.task import Task, TaskCreate, TaskUpdate, TaskComment, TaskCommentCreate
from ..services.policy_service import PolicyService

router = APIRouter()
logger = logging.getLogger(__name__)


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def build_scope_query(current_user: CurrentUser) -> Dict[str, Any]:
    """Organization/project scope filter for list endpoints (non-superadmin)."""
    roles = set(current_user.roles or [])
    if "superadmin" in current_user.roles:
        return {}

    org_id = getattr(current_user, "organization_id", None)
    projects = [str(p) for p in getattr(current_user, "projects", []) or [] if p]

    if {"orgadmin", "orguser"} & roles:
        if not org_id:
            return {"_id": {"$in": []}}
        return {"organization_id": org_id}

    if {"projectadmin", "projectuser"} & roles:
        if not projects:
            return {"_id": {"$in": []}}
        q: Dict[str, Any] = {"project_id": {"$in": projects}}
        if org_id:
            q["organization_id"] = org_id
        return q

    # Default deny for unknown roles
    return {"_id": {"$in": []}}


async def _load_authorized(task_id: str, permission: str, db, current_user, policy) -> Dict[str, Any]:
    task = await db.tasks.find_one({"_id": task_id})
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    await policy.authorize_document(current_user, permission, task, resource_type="task")
    return task


async def _maybe_notify_assignment(db, task: Dict[str, Any], actor_id: Optional[str]) -> None:
    """Best-effort in-app notification to a newly assigned user. Never fatal."""
    assignee = task.get("assigned_to")
    if not assignee or assignee == actor_id:
        return
    try:
        from ..dependencies import get_notification_service
        from ..models.notification import (
            NotificationContext,
            NotificationPriority,
            NotificationType,
        )

        notification_service = await get_notification_service(db)
        await notification_service.emit(
            NotificationType.APPROVAL_ASSIGNED,
            str(task.get("_id")),
            "tasks",
            context=NotificationContext.PROJECT,
            include_users=[assignee],
            actor_id=actor_id,
            priority=NotificationPriority.NORMAL,
            data={
                "title": "Task assigned to you",
                "message": f"You were assigned the task '{task.get('title') or task.get('_id')}'.",
                "task_id": str(task.get("_id")),
                "organization_id": task.get("organization_id"),
                "project_id": task.get("project_id"),
            },
            resource_link="/tasks",
            dedupe_key=f"task-assign:{task.get('_id')}:{assignee}",
        )
    except Exception:  # pragma: no cover - notifications are best-effort
        logger.debug("Task assignment notification skipped", exc_info=True)


@router.get("/tasks", response_model=List[Task])
async def list_tasks(
    status_filter: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    linked_claim_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """List tasks. Permission-gated and scoped to the caller's org/project."""
    # Default to the caller's own organization when no explicit scope is given,
    # so "list my tasks" authorizes against the user's scope rather than denying.
    await policy.authorize(
        current_user,
        Permissions.TASK_VIEW,
        resource_type="tasks",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id,
        audit=False,
    )
    query: Dict[str, Any] = build_scope_query(current_user)
    if status_filter:
        query["status"] = status_filter
    if assigned_to:
        query["assigned_to"] = assigned_to
    if organization_id:
        query["organization_id"] = organization_id
    if project_id:
        query["project_id"] = project_id
    if linked_claim_id:
        query["linked_claim_id"] = linked_claim_id

    tasks = await db.tasks.find(query).skip(skip).limit(limit).to_list(length=limit)
    return [Task(**t) for t in tasks]


@router.post("/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Create a task. Org/project default to the caller's context when omitted."""
    payload = body.model_dump(exclude_unset=True)
    if not payload.get("organization_id") and getattr(current_user, "organization_id", None):
        payload["organization_id"] = current_user.organization_id
    if not payload.get("project_id") and getattr(current_user, "project_id", None):
        payload["project_id"] = current_user.project_id
    if not payload.get("title"):
        raise HTTPException(status_code=400, detail="Task title is required")

    await policy.authorize(
        current_user,
        Permissions.TASK_CREATE,
        resource_type="task",
        organization_id=payload.get("organization_id"),
        project_id=payload.get("project_id"),
    )

    task = Task(**payload)
    res = await db.tasks.insert_one(task.model_dump(by_alias=True))
    created = await db.tasks.find_one({"_id": res.inserted_id})
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create task")
    await _maybe_notify_assignment(db, created, getattr(current_user, "id", None))
    return Task(**created)


@router.get("/tasks/{task_id}", response_model=Task)
async def get_task(
    task_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    task = await _load_authorized(task_id, Permissions.TASK_VIEW, db, current_user, policy)
    return Task(**task)


@router.put("/tasks/{task_id}", response_model=Task)
async def update_task(
    task_id: str,
    body: TaskUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    existing = await _load_authorized(task_id, Permissions.TASK_EDIT, db, current_user, policy)
    update_data = body.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.utcnow()
    updated = await db.tasks.find_one_and_update(
        {"_id": task_id}, {"$set": update_data}, return_document=True
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found after update")
    # Notify only when the assignee actually changed.
    if "assigned_to" in update_data and update_data.get("assigned_to") != existing.get("assigned_to"):
        await _maybe_notify_assignment(db, updated, getattr(current_user, "id", None))
    return Task(**updated)


@router.post("/tasks/{task_id}/comments", response_model=Task, status_code=status.HTTP_201_CREATED)
async def add_task_comment(
    task_id: str,
    body: TaskCommentCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Append a comment to a task. Permission-gated and tenant-scoped."""
    await _load_authorized(task_id, Permissions.TASK_EDIT, db, current_user, policy)
    author_name = " ".join(
        part for part in (
            getattr(current_user, "first_name", None),
            getattr(current_user, "last_name", None),
        ) if part
    ).strip() or getattr(current_user, "username", None) or getattr(current_user, "email", None)

    comment = TaskComment(
        text=body.text,
        author_id=getattr(current_user, "id", None),
        author_name=author_name,
    )
    updated = await db.tasks.find_one_and_update(
        {"_id": task_id},
        {"$push": {"comments": comment.model_dump()}, "$set": {"updated_at": datetime.utcnow()}},
        return_document=True,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found after comment")
    return Task(**updated)


@router.delete("/tasks/{task_id}", response_model=dict)
async def delete_task(
    task_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_authorized(task_id, Permissions.TASK_DELETE, db, current_user, policy)
    await db.tasks.delete_one({"_id": task_id})
    return {"message": "Task deleted successfully"}
