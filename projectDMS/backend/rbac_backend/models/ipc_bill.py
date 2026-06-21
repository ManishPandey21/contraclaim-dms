"""IPC / Contractor Bill Register models (Contract Controls).

Records each Interim Payment Certificate (IPC) with the contractor-claimed,
engineer-verified, employer-approved and actually-paid views of the bill. Each
component (gross, deductions, recovery of advances, IT, GST, withholding,
penalties/LD) may itself be denominated in one or more contract currencies; the
service converts everything to the contract base currency for roll-ups.
Tenant-scoped by organization/project, consistent with the other registers.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IPCStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"                 # contractor submitted
    UNDER_VERIFICATION = "under_verification"
    VERIFIED = "verified"                   # engineer/GC verified
    APPROVED = "approved"                   # employer approved
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    REJECTED = "rejected"


class PaymentStructure(str, Enum):
    FULL = "full"        # 100%
    EIGHTY_TWENTY = "80_20"
    TWENTY = "20"
    PARTIAL = "partial"
    CUSTOM = "custom"


class CurrencyAmount(BaseModel):
    """One currency's portion of a component, with its award-fixed rate to base."""

    currency: str
    conversion_rate: float = 1.0  # 1 unit of `currency` = rate base units (fixed at award)
    amount: float = 0.0

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        cleaned = (value or "").strip().upper()
        if not cleaned:
            raise ValueError("currency code is required")
        return cleaned

    @field_validator("conversion_rate")
    @classmethod
    def _rate(cls, value: float) -> float:
        if value is None or float(value) <= 0:
            raise ValueError("conversion_rate must be greater than 0")
        return float(value)


class IPCComponents(BaseModel):
    """One perspective's bill breakdown. Each component is a list of currency
    amounts (per-component multi-currency). `net_payable` is derived by the
    service (gross minus all deductions), not entered here."""

    gross: List[CurrencyAmount] = Field(default_factory=list)
    deductions: List[CurrencyAmount] = Field(default_factory=list)
    recovery_of_advances: List[CurrencyAmount] = Field(default_factory=list)
    it_tax: List[CurrencyAmount] = Field(default_factory=list)        # income tax
    gst: List[CurrencyAmount] = Field(default_factory=list)
    withheld: List[CurrencyAmount] = Field(default_factory=list)
    penalties_ld: List[CurrencyAmount] = Field(default_factory=list)  # penalties / LD


class IPCRevision(BaseModel):
    revision_number: int
    status: Optional[str] = None
    remarks: Optional[str] = None
    changed_by: Optional[str] = None
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class IPCBillBase(BaseModel):
    ipc_number: Optional[str] = None
    ipc_period: Optional[str] = None        # e.g. "Jan 2026" / "2026-01"
    contract_id: Optional[str] = None
    contractor_name: Optional[str] = None
    base_currency: str = "INR"              # reporting currency (from Contract Master)
    payment_structure: PaymentStructure = PaymentStructure.FULL
    payment_percentage: Optional[float] = None  # e.g. 80, 20, 100

    # The four perspectives.
    contractor_claimed: IPCComponents = Field(default_factory=IPCComponents)
    engineer_verified: IPCComponents = Field(default_factory=IPCComponents)
    employer_approved: IPCComponents = Field(default_factory=IPCComponents)
    actually_paid: IPCComponents = Field(default_factory=IPCComponents)

    submission_date: Optional[datetime] = None
    verification_date: Optional[datetime] = None
    approval_date: Optional[datetime] = None
    payment_date: Optional[datetime] = None
    status: IPCStatus = IPCStatus.DRAFT
    remarks: Optional[str] = None
    letter_references: List[str] = Field(default_factory=list)
    linked_document_ids: List[str] = Field(default_factory=list)
    # Base-currency contract value, for percent-billed/approved (defaults from
    # Contract Master when omitted).
    original_contract_value: Optional[float] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class IPCBillCreate(IPCBillBase):
    project_id: str = Field(..., min_length=1)


class IPCBillUpdate(BaseModel):
    ipc_number: Optional[str] = None
    ipc_period: Optional[str] = None
    contract_id: Optional[str] = None
    contractor_name: Optional[str] = None
    base_currency: Optional[str] = None
    payment_structure: Optional[PaymentStructure] = None
    payment_percentage: Optional[float] = None
    contractor_claimed: Optional[IPCComponents] = None
    engineer_verified: Optional[IPCComponents] = None
    employer_approved: Optional[IPCComponents] = None
    actually_paid: Optional[IPCComponents] = None
    submission_date: Optional[datetime] = None
    verification_date: Optional[datetime] = None
    approval_date: Optional[datetime] = None
    payment_date: Optional[datetime] = None
    status: Optional[IPCStatus] = None
    remarks: Optional[str] = None
    letter_references: Optional[List[str]] = None
    linked_document_ids: Optional[List[str]] = None
    original_contract_value: Optional[float] = None


class IPCBill(IPCBillBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    current_revision: int = 0
    revisions: List[IPCRevision] = Field(default_factory=list)

    # Derived for responses (all in base currency).
    claimed_total_base: Optional[float] = None       # contractor gross claimed
    verified_total_base: Optional[float] = None       # engineer gross verified
    approved_total_base: Optional[float] = None       # employer gross approved
    net_payable_base: Optional[float] = None          # employer-approved net
    paid_base: Optional[float] = None                 # actually-paid net
    balance_payable_base: Optional[float] = None      # net payable - paid
    percent_billed: Optional[float] = None            # claimed / contract value
    percent_approved: Optional[float] = None          # approved / contract value

    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class IPCBillSummary(BaseModel):
    total_ipcs: int = 0
    base_currency: str = "INR"
    total_claimed_base: float = 0.0
    total_approved_base: float = 0.0
    total_net_payable_base: float = 0.0
    total_paid_base: float = 0.0
    total_balance_payable_base: float = 0.0
    cumulative_ipc_value_base: float = 0.0     # cumulative approved net
    percent_of_contract_billed: float = 0.0
    percent_of_contract_approved: float = 0.0
    pending_count: int = 0
    approved_count: int = 0
    paid_count: int = 0
