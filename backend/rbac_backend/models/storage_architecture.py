"""Immutable storage metadata models for document binary objects and versions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..utils.datetime import isoformat_z, now_utc


def _new_id() -> str:
    return str(ObjectId())


class StorageProviderLocation(BaseModel):
    provider: str = Field(...)
    path: Optional[str] = Field(default=None)
    url: Optional[str] = Field(default=None)
    status: str = Field(default="ok")
    primary: bool = Field(default=False)
    createdAt: datetime = Field(default_factory=now_utc)

    model_config = ConfigDict(json_encoders={datetime: isoformat_z})


class FileObject(BaseModel):
    id: str = Field(default_factory=_new_id, alias="_id")
    sha256: str = Field(...)
    size: int = Field(...)
    mime_type: str = Field(default="application/octet-stream")
    original_filename: str = Field(...)
    stored_filename: str = Field(...)
    storage_key: str = Field(...)
    primary_provider: str = Field(default="local")
    locations: List[StorageProviderLocation] = Field(default_factory=list)
    organization_id: str = Field(...)
    project_id: Optional[str] = Field(default=None)
    document_id: Optional[str] = Field(default=None)
    document_ids: List[str] = Field(default_factory=list)
    document_type: str = Field(default="document")
    upload_id: Optional[str] = Field(default=None)
    created_by: Optional[str] = Field(default=None)
    createdAt: datetime = Field(default_factory=now_utc)
    immutable: bool = Field(default=True)
    dedupe_of: Optional[str] = Field(default=None)

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={ObjectId: str, datetime: isoformat_z},
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("_id"), ObjectId):
            normalized = dict(data)
            normalized["_id"] = str(normalized["_id"])
            return normalized
        return data


class DocumentVersion(BaseModel):
    id: str = Field(default_factory=_new_id, alias="_id")
    document_id: str = Field(...)
    version_number: int = Field(default=1)
    file_object_id: Optional[str] = Field(default=None)
    metadata_snapshot: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="upload")
    is_current: bool = Field(default=True)
    created_by: Optional[str] = Field(default=None)
    createdAt: datetime = Field(default_factory=now_utc)

    model_config = ConfigDict(populate_by_name=True, json_encoders={datetime: isoformat_z})


class ContractAggregate(BaseModel):
    id: str = Field(default_factory=_new_id, alias="_id")
    organization_id: str = Field(...)
    project_id: Optional[str] = Field(default=None)
    title: str = Field(...)
    current_version_id: Optional[str] = Field(default=None)
    document_ids: List[str] = Field(default_factory=list)
    status: str = Field(default="active")
    created_by: Optional[str] = Field(default=None)
    createdAt: datetime = Field(default_factory=now_utc)
    updatedAt: datetime = Field(default_factory=now_utc)

    model_config = ConfigDict(populate_by_name=True, json_encoders={datetime: isoformat_z})


class ContractVersion(BaseModel):
    id: str = Field(default_factory=_new_id, alias="_id")
    contract_id: str = Field(...)
    document_id: str = Field(...)
    upload_id: str = Field(...)
    version_number: int = Field(default=1)
    file_object_id: Optional[str] = Field(default=None)
    sha256: Optional[str] = Field(default=None)
    filename: str = Field(...)
    status: str = Field(default="queued")
    ingestion_status: str = Field(default="queued")
    created_by: Optional[str] = Field(default=None)
    createdAt: datetime = Field(default_factory=now_utc)
    updatedAt: datetime = Field(default_factory=now_utc)

    model_config = ConfigDict(populate_by_name=True, json_encoders={datetime: isoformat_z})


class DocumentAuditEvent(BaseModel):
    id: str = Field(default_factory=_new_id, alias="_id")
    resource_type: str = Field(...)
    resource_id: str = Field(...)
    event_type: str = Field(...)
    actor_id: Optional[str] = Field(default=None)
    organization_id: Optional[str] = Field(default=None)
    project_id: Optional[str] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime = Field(default_factory=now_utc)

    model_config = ConfigDict(populate_by_name=True, json_encoders={datetime: isoformat_z})
