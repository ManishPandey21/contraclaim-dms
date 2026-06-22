"""IPC line-item category masters (advance types + deduction types).

User-manageable catalogs that populate the IPC editor dropdowns:
  * advance   — Mobilisation / Material / Plant advances, Price escalation,
                Work-done payment, Advance against work done. Recoveries are
                booked against one of these.
  * deduction — statutory deductions (Income Tax, Labour Cess, ...).

Catalogs are org-wide by default (``project_id`` is null) and a project may add
or override entries by setting ``project_id``; the service merges org + project
by ``code`` with the project entry winning. Tenant-scoped like the registers.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IPCCategoryKind(str, Enum):
    ADVANCE = "advance"      # advance / payment types recoveries are made against
    DEDUCTION = "deduction"  # statutory deductions


# Canonical defaults seeded per organization on first use.
DEFAULT_CATEGORIES: dict[str, list[tuple[str, str]]] = {
    "advance": [
        ("mobilisation_advance", "Mobilisation Advance"),
        ("material_advance", "Material Advance"),
        ("plant_advance", "Plant Advance"),
        ("price_escalation", "Price Escalation Payment"),
        ("work_done_payment", "Work Done Payment"),
        ("advance_against_work_done", "Advance Against Work Done"),
    ],
    "deduction": [
        ("income_tax", "Income Tax"),
        ("labour_cess", "Labour Cess"),
    ],
}


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return cleaned


class IPCCategoryBase(BaseModel):
    kind: IPCCategoryKind
    code: str
    name: str
    description: Optional[str] = None
    active: bool = True
    sort_order: int = 0
    organization_id: Optional[str] = None
    project_id: Optional[str] = None  # null = org-wide; set = project add/override

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        cleaned = _slugify(value)
        if not cleaned:
            raise ValueError("code is required")
        return cleaned

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("name is required")
        return cleaned


class IPCCategoryCreate(IPCCategoryBase):
    pass


class IPCCategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None


class IPCCategory(IPCCategoryBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    is_default: bool = False  # seeded default (informational; still editable)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)
