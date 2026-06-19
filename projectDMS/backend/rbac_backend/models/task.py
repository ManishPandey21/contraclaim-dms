from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid


class TaskComment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TaskCommentCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)


class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    status: Optional[str] = "open"  # open, in_progress, done
    priority: Optional[str] = "normal"  # low, normal, high
    assigned_to: Optional[str] = None  # user id
    due_date: Optional[datetime] = None
    document_id: Optional[str] = None  # linked document
    linked_claim_id: Optional[str] = None  # claim this task follows up on
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
    due_date: Optional[datetime] = None
    document_id: Optional[str] = None
    linked_claim_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class Task(TaskBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    comments: List[TaskComment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    class Config:
        populate_by_name = True
