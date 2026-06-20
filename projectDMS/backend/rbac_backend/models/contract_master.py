"""Contract Master models (Contract Controls).

One authoritative per-contract record of value + dates + BG-validity rules that the
Key Date, Variation and Bank Guarantee registers read from. Keyed by
(organization_id, project_id, contract_id). Baselines (original value, original
completion) are never overwritten; only the current/revised values move.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

# Per-BG-type validity rule: the required-up-to date = base(basis) + offset_days.
# basis: "completion" (effective completion date) or "dlp_end" (completion + DLP).
# A "default" entry covers any BG type not explicitly listed.
DEFAULT_BG_VALIDITY_RULES: Dict[str, Dict[str, Any]] = {
    "performance": {"basis": "dlp_end", "offset_days": 0},
    "additional_performance": {"basis": "dlp_end", "offset_days": 0},
    "retention": {"basis": "dlp_end", "offset_days": 0},
    "security_deposit": {"basis": "dlp_end", "offset_days": 0},
    "mobilisation_advance": {"basis": "completion", "offset_days": 0},
    "plant_advance": {"basis": "completion", "offset_days": 0},
    "other": {"basis": "completion", "offset_days": 0},
    "default": {"basis": "completion", "offset_days": 0},
}


class ContractMasterBase(BaseModel):
    contract_id: str = "primary"  # package key; default single contract per project
    contract_name: Optional[str] = None
    contract_code: Optional[str] = None
    client_name: Optional[str] = None
    contractor_name: Optional[str] = None
    engineer_name: Optional[str] = None
    currency: str = "INR"
    original_contract_value: Optional[float] = None
    current_contract_value: Optional[float] = None
    contract_start_date: Optional[datetime] = None
    original_completion_date: Optional[datetime] = None
    revised_completion_date: Optional[datetime] = None
    defect_liability_period_days: Optional[int] = None
    reporting_period: Optional[str] = None
    bg_validity_rules: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class ContractMasterCreate(ContractMasterBase):
    project_id: str = Field(..., min_length=1)


class ContractMasterUpdate(BaseModel):
    contract_name: Optional[str] = None
    contract_code: Optional[str] = None
    client_name: Optional[str] = None
    contractor_name: Optional[str] = None
    engineer_name: Optional[str] = None
    currency: Optional[str] = None
    original_contract_value: Optional[float] = None
    contract_start_date: Optional[datetime] = None
    original_completion_date: Optional[datetime] = None
    defect_liability_period_days: Optional[int] = None
    reporting_period: Optional[str] = None
    bg_validity_rules: Optional[Dict[str, Dict[str, Any]]] = None


class ReviseCompletionRequest(BaseModel):
    revised_completion_date: datetime
    remarks: Optional[str] = None


class ContractMaster(ContractMasterBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    # Derived for responses.
    effective_completion_date: Optional[datetime] = None
    dlp_end_date: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class BGRequiredDate(BaseModel):
    bg_type: str
    basis: str
    offset_days: int
    required_up_to: Optional[datetime] = None
