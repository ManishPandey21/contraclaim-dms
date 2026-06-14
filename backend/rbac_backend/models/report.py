from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReportCategory(str, Enum):
    LETTERS = "letters"
    DOCUMENTS = "documents"
    TASKS = "tasks"


class ReportDefinition(BaseModel):
    id: str
    name: str
    description: str
    category: ReportCategory
    default_columns: List[str]
    metrics: List[str] = Field(default_factory=list)
    download_formats: List[str] = Field(default_factory=lambda: ["csv"])


class ReportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    report_id: str = Field(..., alias="reportId")
    start_date: Optional[datetime] = Field(default=None, alias="startDate")
    end_date: Optional[datetime] = Field(default=None, alias="endDate")
    organization_id: Optional[str] = Field(default=None, alias="organizationId")
    project_id: Optional[str] = Field(default=None, alias="projectId")
    limit: int = Field(default=200, ge=1, le=1000)
    # Linked chain report specific fields
    letter_no: Optional[str] = Field(default=None, alias="letterNo")
    chain_direction: Optional[str] = Field(default=None, alias="chainDirection")
    include_self: bool = Field(default=True, alias="includeSelf")
    direction: Optional[str] = Field(default=None, alias="direction")
    tags: Optional[List[str]] = Field(default=None, alias="tags")
    sub_tags: Optional[List[str]] = Field(default=None, alias="subTags")
    statuses: Optional[List[str]] = Field(default=None, alias="statuses")


class ReportPreview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    report_id: str = Field(..., alias="reportId")
    report_name: str = Field(..., alias="reportName")
    generated_at: datetime = Field(..., alias="generatedAt")
    columns: List[str]
    rows: List[Dict[str, Any]]
    metrics: Dict[str, Any]
    total_rows: int = Field(..., alias="totalRows")
