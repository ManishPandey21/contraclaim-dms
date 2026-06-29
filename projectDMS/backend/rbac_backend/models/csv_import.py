from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class CSVImportRow(BaseModel):
    row_number: int
    data: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    duplicate: bool = False


class CSVImportPreview(BaseModel):
    module: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    can_import: bool
    rows: List[CSVImportRow] = Field(default_factory=list)
    required_headers: List[str] = Field(default_factory=list)
    template_headers: List[str] = Field(default_factory=list)


class CSVImportResult(CSVImportPreview):
    imported_count: int = 0
    created_ids: List[str] = Field(default_factory=list)
