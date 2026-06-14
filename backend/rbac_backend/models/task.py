from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid


class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    status: Optional[str] = "open"  # open, in_progress, done
    priority: Optional[str] = "normal"  # low, normal, high
    assigned_to: Optional[str] = None  # user id
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class TaskCreate(TaskBase):
    title: str


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class Task(TaskBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    class Config:
        populate_by_name = True
