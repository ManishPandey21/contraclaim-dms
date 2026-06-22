"""IPC / Contractor Bill Register models (Contract Controls).

Records each Interim Payment Certificate (IPC) as a real certificate would be
filled, organised function-first rather than perspective-first:

  * line_items  — one row per BOQ/scope item with the contractor-claimed,
                  engineer/GC-verified and employer-approved gross amounts
                  side by side (each line in one currency).
  * deductions  — recoveries of advances, statutory deductions, withholding
                  and penalties/LD, captured per perspective
                  (claimed / verified / approved). Each line carries an
                  optional master ``category`` and free-text ``description``.
  * payments    — discrete actual payments made (date, reference, amount).

Every monetary line carries its award-fixed conversion rate; the service
converts everything to the contract base currency for roll-ups. Tenant-scoped
by organization/project, consistent with the other registers.
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


def _clean_currency(value: str) -> str:
    cleaned = (value or "").strip().upper()
    if not cleaned:
        raise ValueError("currency code is required")
    return cleaned


def _clean_rate(value: float) -> float:
    if value is None or float(value) <= 0:
        raise ValueError("conversion_rate must be greater than 0")
    return float(value)


class CurrencyAmount(BaseModel):
    """One deduction/recovery line: an amount in a currency, with the rate to
    base. `category` carries the master code (advance / deduction type) and
    `description` a free-text reason. Both optional."""

    currency: str
    conversion_rate: float = 1.0  # 1 unit of `currency` = rate base units (fixed at award)
    amount: float = 0.0
    category: Optional[str] = None     # master code (advance / deduction type)
    description: Optional[str] = None  # free-text reason / detail

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return _clean_currency(value)

    @field_validator("conversion_rate")
    @classmethod
    def _rate(cls, value: float) -> float:
        return _clean_rate(value)


class IPCLineItem(BaseModel):
    """One BOQ/scope line with the three estimate perspectives side by side."""

    description: Optional[str] = None
    currency: str = "INR"
    conversion_rate: float = 1.0
    claimed: float = 0.0
    verified: float = 0.0
    approved: float = 0.0

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return _clean_currency(value)

    @field_validator("conversion_rate")
    @classmethod
    def _rate(cls, value: float) -> float:
        return _clean_rate(value)


class IPCPerspectiveDeductions(BaseModel):
    """The deduction breakdown for one perspective. `net = gross - sum(these)`.

    Surfaced in the editor across three tabs: recoveries/withholding/penalties,
    statutory deductions (Income Tax, Labour Cess via the deduction master) and
    GST."""

    recovery_of_advances: List[CurrencyAmount] = Field(default_factory=list)
    withheld: List[CurrencyAmount] = Field(default_factory=list)
    penalties_ld: List[CurrencyAmount] = Field(default_factory=list)  # penalties / LD
    deductions: List[CurrencyAmount] = Field(default_factory=list)    # IT, Labour Cess, ...
    gst: List[CurrencyAmount] = Field(default_factory=list)


# The deduction components that reduce gross to net, in roll-up order.
DEDUCTION_COMPONENTS = (
    "recovery_of_advances", "withheld", "penalties_ld", "deductions", "gst",
)


class DeductionsByPerspective(BaseModel):
    contractor_claimed: IPCPerspectiveDeductions = Field(default_factory=IPCPerspectiveDeductions)
    engineer_verified: IPCPerspectiveDeductions = Field(default_factory=IPCPerspectiveDeductions)
    employer_approved: IPCPerspectiveDeductions = Field(default_factory=IPCPerspectiveDeductions)


class IPCPaymentRecord(BaseModel):
    """One actual payment made against the certificate."""

    payment_date: Optional[datetime] = None
    reference: Optional[str] = None     # cheque / NEFT / instrument reference
    method: Optional[str] = None        # NEFT / RTGS / cheque ...
    currency: str = "INR"
    conversion_rate: float = 1.0
    amount: float = 0.0

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return _clean_currency(value)

    @field_validator("conversion_rate")
    @classmethod
    def _rate(cls, value: float) -> float:
        return _clean_rate(value)


class IPCRevision(BaseModel):
    revision_number: int
    status: Optional[str] = None
    remarks: Optional[str] = None
    changed_by: Optional[str] = None
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class IPCBillBase(BaseModel):
    ipc_number: Optional[str] = None
    ipc_date: Optional[datetime] = None         # certificate date
    ipc_period: Optional[str] = None            # optional label, e.g. "Jan 2026"
    period_from: Optional[datetime] = None
    period_to: Optional[datetime] = None
    contract_id: Optional[str] = None
    contractor_name: Optional[str] = None
    approver: Optional[str] = None              # e.g. "Employer's Representative"
    base_currency: str = "INR"                  # reporting currency (from Contract Master)
    payment_structure: PaymentStructure = PaymentStructure.FULL
    payment_percentage: Optional[float] = None  # e.g. 80, 20, 100

    # Function-first body.
    line_items: List[IPCLineItem] = Field(default_factory=list)
    deductions: DeductionsByPerspective = Field(default_factory=DeductionsByPerspective)
    payments: List[IPCPaymentRecord] = Field(default_factory=list)

    # Status workflow dates (set as the certificate progresses; not on the Header tab).
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
    ipc_date: Optional[datetime] = None
    ipc_period: Optional[str] = None
    period_from: Optional[datetime] = None
    period_to: Optional[datetime] = None
    contract_id: Optional[str] = None
    contractor_name: Optional[str] = None
    approver: Optional[str] = None
    base_currency: Optional[str] = None
    payment_structure: Optional[PaymentStructure] = None
    payment_percentage: Optional[float] = None
    line_items: Optional[List[IPCLineItem]] = None
    deductions: Optional[DeductionsByPerspective] = None
    payments: Optional[List[IPCPaymentRecord]] = None
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
    total_deductions_base: Optional[float] = None     # employer-approved deductions
    net_payable_base: Optional[float] = None          # approved gross - approved deductions
    paid_base: Optional[float] = None                 # sum of payment records
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
