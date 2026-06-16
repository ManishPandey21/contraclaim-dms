from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional, Dict, Any
from datetime import datetime

from ..core.database import get_db
from ..core.security import get_current_user, CurrentUser
from ..models.task import Task, TaskCreate, TaskUpdate, TaskComment, TaskCommentCreate

router = APIRouter()


def _authorize_task_access(task: Dict[str, Any], current_user: CurrentUser) -> None:
    """Deny cross-tenant access to a task for non-superadmin users."""
    if "superadmin" in (current_user.roles or []):
        return
    roles = set(current_user.roles or [])
    allowed_projects = [str(p) for p in getattr(current_user, "projects", []) or [] if p]
    org_ok = (not task.get("organization_id")) or (
        task.get("organization_id") == getattr(current_user, "organization_id", None)
    )
    proj_ok = (not task.get("project_id")) or (str(task.get("project_id")) in allowed_projects)
    if {"projectadmin", "projectuser"} & roles and not proj_ok:
        raise HTTPException(status_code=403, detail="Not authorized for this task")
    if {"orgadmin", "orguser"} & roles and not org_ok:
        raise HTTPException(status_code=403, detail="Not authorized for this task")

def build_scope_query(current_user: CurrentUser) -> Dict[str, Any]:
    """
    Builds an organization/project scope filter for non-superadmin users.
    """
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

@router.get("/tasks", response_model=List[Task])
async def list_tasks(
    status_filter: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    List tasks with optional filters. Non-superadmin users are scoped to their organization/project.
    """
    try:
        query: Dict[str, Any] = build_scope_query(current_user)

        if status_filter:
            query["status"] = status_filter
        if assigned_to:
            query["assigned_to"] = assigned_to
        if organization_id:
            query["organization_id"] = organization_id
        if project_id:
            query["project_id"] = project_id

        tasks = await db.tasks.find(query).skip(skip).limit(limit).to_list(length=limit)
        return [Task(**t) for t in tasks]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list tasks: {str(e)}")

@router.post("/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreate,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Create a new task. Organization/project scoping should be provided by client; if omitted,
    default to current user's org/project when available.
    """
    try:
        payload = body.model_dump(exclude_unset=True)
        # Default org/project to user's context if not supplied
        if not payload.get("organization_id") and getattr(current_user, "organization_id", None):
            payload["organization_id"] = current_user.organization_id
        if not payload.get("project_id") and getattr(current_user, "project_id", None):
            payload["project_id"] = current_user.project_id

        # Basic validations
        if not payload.get("title"):
            raise HTTPException(status_code=400, detail="Task title is required")

        # Persist
        task = Task(**payload)
        res = await db.tasks.insert_one(task.model_dump(by_alias=True))
        created = await db.tasks.find_one({"_id": res.inserted_id})
        if not created:
            raise HTTPException(status_code=500, detail="Failed to create task")
        return Task(**created)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")

@router.get("/tasks/{task_id}", response_model=Task)
async def get_task(
    task_id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get a single task by id with scope/authorization checks.
    """
    try:
        task = await db.tasks.find_one({"_id": task_id})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Scope check for non-superadmin
        if "superadmin" not in current_user.roles:
            roles = set(current_user.roles or [])
            allowed_projects = [str(p) for p in getattr(current_user, "projects", []) or [] if p]
            org_ok = (not task.get("organization_id")) or (task.get("organization_id") == getattr(current_user, "organization_id", None))
            proj_ok = (not task.get("project_id")) or (str(task.get("project_id")) in allowed_projects)
            if {"projectadmin", "projectuser"} & roles and not proj_ok:
                raise HTTPException(status_code=403, detail="Not authorized to view this task")
            if {"orgadmin", "orguser"} & roles and not org_ok:
                raise HTTPException(status_code=403, detail="Not authorized to view this task")

        return Task(**task)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task: {str(e)}")

@router.put("/tasks/{task_id}", response_model=Task)
async def update_task(
    task_id: str,
    body: TaskUpdate,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Update a task by id. Non-superadmin users must be in the same org/project context.
    """
    try:
        existing = await db.tasks.find_one({"_id": task_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")

        # Scope check
        if "superadmin" not in current_user.roles:
            roles = set(current_user.roles or [])
            allowed_projects = [str(p) for p in getattr(current_user, "projects", []) or [] if p]
            org_ok = (not existing.get("organization_id")) or (existing.get("organization_id") == getattr(current_user, "organization_id", None))
            proj_ok = (not existing.get("project_id")) or (str(existing.get("project_id")) in allowed_projects)
            if {"projectadmin", "projectuser"} & roles and not proj_ok:
                raise HTTPException(status_code=403, detail="Not authorized to update this task")
            if {"orgadmin", "orguser"} & roles and not org_ok:
                raise HTTPException(status_code=403, detail="Not authorized to update this task")

        update_data = body.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.utcnow()

        updated = await db.tasks.find_one_and_update(
            {"_id": task_id},
            {"$set": update_data},
            return_document=True
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Task not found after update")

        return Task(**updated)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update task: {str(e)}")

@router.post("/tasks/{task_id}/comments", response_model=Task, status_code=status.HTTP_201_CREATED)
async def add_task_comment(
    task_id: str,
    body: TaskCommentCreate,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Append a comment to a task. Tenant-scoped like the other task routes."""
    try:
        existing = await db.tasks.find_one({"_id": task_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")
        _authorize_task_access(existing, current_user)

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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add comment: {str(e)}")


@router.delete("/tasks/{task_id}", response_model=dict)
async def delete_task(
    task_id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Delete a task by id. Non-superadmin users must be in the same org/project context.
    """
    try:
        existing = await db.tasks.find_one({"_id": task_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")

        # Scope check
        if "superadmin" not in current_user.roles:
            roles = set(current_user.roles or [])
            allowed_projects = [str(p) for p in getattr(current_user, "projects", []) or [] if p]
            org_ok = (not existing.get("organization_id")) or (existing.get("organization_id") == getattr(current_user, "organization_id", None))
            proj_ok = (not existing.get("project_id")) or (str(existing.get("project_id")) in allowed_projects)
            if {"projectadmin", "projectuser"} & roles and not proj_ok:
                raise HTTPException(status_code=403, detail="Not authorized to delete this task")
            if {"orgadmin", "orguser"} & roles and not org_ok:
                raise HTTPException(status_code=403, detail="Not authorized to delete this task")

        await db.tasks.delete_one({"_id": task_id})
        return {"message": "Task deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete task: {str(e)}")
