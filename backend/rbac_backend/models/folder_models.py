from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class FolderItem(BaseModel):
    """Represents a folder or file within the hierarchy."""

    id: Optional[str] = None
    name: str
    path: str
    type: str = Field(..., pattern="^(folder|file)$")
    size: Optional[int] = None
    file_extension: Optional[str] = None
    created_at: Optional[str] = None
    children: List["FolderItem"] = Field(default_factory=list)


class CreateFolderRequest(BaseModel):
    """Payload for creating folders."""

    name: str
    path: str
    organization_id: str
    project_id: Optional[str] = None
    type: str = Field(default="folder", pattern="^(folder|file)$")


class FolderResponse(BaseModel):
    """Response after creating folder."""

    message: str
    folder_id: Optional[str] = None
    path: str


class UploadFileRequest(BaseModel):
    """Metadata for uploaded file."""

    name: str
    path: str
    file_extension: str
    organization_id: str
    project_id: Optional[str] = None


class UploadFileResponse(BaseModel):
    """Response after file upload."""

    message: str
    file_id: Optional[str] = None
    path: str
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
