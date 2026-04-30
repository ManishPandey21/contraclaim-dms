from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Literal, Dict, Any, Union

from bson import ObjectId
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator


ProviderId = Literal["local", "s3", "azure", "gcs", "custom"]
DocumentPathType = Literal["incoming", "outgoing", "contracts"]


class StorageProviderConfig(BaseModel):
    """Provider selection (credentials are supplied via environment, not stored here)."""

    id: ProviderId = Field(..., description="Provider identifier")
    enabled: bool = Field(default=True)
    primary: bool = Field(default=False)
    bucket: Optional[str] = Field(default=None, description="Bucket/container name (where applicable)")
    prefix: Optional[str] = Field(default=None, description="Optional key prefix")
    region: Optional[str] = Field(default=None)
    endpoint: Optional[str] = Field(default=None, description="Custom endpoint for S3-compatible or custom providers")
    extra: Dict[str, Any] = Field(default_factory=dict)


class StorageBasePaths(BaseModel):
    incoming: str = Field(default="/ORG/PROJ/incoming")
    outgoing: str = Field(default="/ORG/PROJ/outgoing")
    contracts: str = Field(default="/ORG/PROJ/contracts")


class OrganizationStorageSettings(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    org_id: str
    org_short_name: Optional[str] = Field(default=None, max_length=10, description="Uppercase short name")
    providers: List[StorageProviderConfig] = Field(default_factory=list)
    base_paths: StorageBasePaths = Field(default_factory=StorageBasePaths)
    updatedAt: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    @field_validator("id", mode="before")
    @classmethod
    def convert_objectid_to_str(cls, v):
        """Convert MongoDB ObjectId to string"""
        if isinstance(v, ObjectId):
            return str(v)
        return v

    @model_validator(mode="after")
    def _normalize_short(self):
        if self.org_short_name:
            self.org_short_name = self.org_short_name.strip().upper()
        return self


class ProjectStorageSettings(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    project_id: str
    org_id: str
    inherit_from_org: bool = Field(default=True)
    project_short_name: Optional[str] = Field(default=None, max_length=10)
    providers: Optional[List[StorageProviderConfig]] = Field(default=None)
    base_paths: Optional[StorageBasePaths] = Field(default=None)
    updatedAt: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    @field_validator("id", mode="before")
    @classmethod
    def convert_objectid_to_str(cls, v):
        """Convert MongoDB ObjectId to string"""
        if isinstance(v, ObjectId):
            return str(v)
        return v

    @model_validator(mode="after")
    def _normalize_short(self):
        if self.project_short_name:
            self.project_short_name = self.project_short_name.strip().upper()
        return self


class ResolvedStorageSettings(BaseModel):
    """Effective settings after applying inheritance."""

    org_id: str
    project_id: Optional[str] = None
    org_short_name: Optional[str] = None
    project_short_name: Optional[str] = None
    providers: List[StorageProviderConfig] = Field(default_factory=list)
    base_paths: StorageBasePaths = Field(default_factory=StorageBasePaths)
