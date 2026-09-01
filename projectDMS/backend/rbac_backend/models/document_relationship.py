"""Canonical relationships between DMS Documents and application entities."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentRelationshipInput(BaseModel):
    document_id: str = Field(..., min_length=1)
    relationship_role: str = Field(..., min_length=1)
    description: Optional[str] = Field(default=None, max_length=1000)
    document_version_id: Optional[str] = None


class DocumentRelationshipBatchRequest(BaseModel):
    links: List[DocumentRelationshipInput] = Field(..., min_length=1, max_length=100)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class DocumentRelationship(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    organization_id: str
    project_id: str
    target_type: str
    target_id: str
    parent_type: Optional[str] = None
    parent_id: Optional[str] = None
    document_id: str
    document_version_id: Optional[str] = None
    relationship_role: str
    description: Optional[str] = None
    source: str = "user"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    removed_at: Optional[datetime] = None
    removed_by: Optional[str] = None
    removal_reason: Optional[str] = None
    frozen_at: Optional[datetime] = None
    frozen_by: Optional[str] = None
    supersedes_link_id: Optional[str] = None
    authority_snapshot: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(default=1, alias="_revision")

    model_config = ConfigDict(populate_by_name=True)


class DocumentRelationshipView(DocumentRelationship):
    document: Optional[Dict[str, Any]] = None
    target_label: Optional[str] = None
    target_route: Optional[str] = None


class DocumentRelationshipListResponse(BaseModel):
    links: List[DocumentRelationshipView] = Field(default_factory=list)


class DocumentRelationshipRemovalRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)
    expected_revision: int = Field(..., ge=1)


class DocumentRelationshipFreezeRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class DocumentRelationshipResponse(BaseModel):
    link: DocumentRelationshipView
