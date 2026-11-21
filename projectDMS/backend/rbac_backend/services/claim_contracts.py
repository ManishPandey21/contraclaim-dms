"""Which contract a claim is made under (plan 4B-18, D-23, AQ-1, C-05).

A project can hold several contracts. Key-date records, variations and
hindrances already say which one they belong to; a claim did not, so a record
from contract B could be linked to a claim under contract A.

The contract is the project's Contract Master entry, identified by the same
``contract_id`` those registers carry. The rule, in one place:

- **valid** contracts for a claim are the Contract Master entries of the
  claim's own project that the person may read;
- exactly one valid contract: it is selected, and shown;
- several: the person must choose, and nothing is guessed;
- none: the claim is created without one and says so;
- a contract named explicitly must be one of the valid ones;
- once set, a claim's contract is not changed. A claim with none (a legacy
  claim, or one created when none was identifiable) may be given one later.

Contract Master has no "withdrawn" state today, so that part of D-23 has
nothing to read; if one is added, this module is where it is checked.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from ..core.permissions import Permissions
from .policy_service import PolicyService


class ClaimContractOption(BaseModel):
    contract_id: str
    #: The name people know the contract by; falls back to its code, then its key.
    label: str


class ClaimContractOptions(BaseModel):
    """What the claim form needs: the contracts to choose from, and what happens if none is chosen."""

    options: List[ClaimContractOption] = Field(default_factory=list)
    #: The contract selected when the person chooses nothing: the only one there is.
    selected: Optional[str] = None
    #: True when several exist, so one must be chosen.
    must_choose: bool = False


def _refuse(code: int, error_code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=code, detail={"code": error_code, "user_message": message, "detail": message, **extra}
    )


def contract_label(row: Dict[str, Any]) -> str:
    for key in ("contract_name", "contract_code"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Main contract" if str(row.get("contract_id") or "") == "primary" else str(row.get("contract_id") or "")


async def valid_contracts(
    db: Any, policy: PolicyService, user: Any, *, organization_id: Optional[str], project_id: Optional[str]
) -> List[ClaimContractOption]:
    """Contract Master entries of this project that ``user`` may read. Empty when they may read none."""
    if not project_id:
        return []
    try:
        await policy.authorize(
            user,
            Permissions.CONTRACT_MASTER_VIEW,
            resource_type="contract_master",
            organization_id=organization_id,
            project_id=str(project_id),
            audit=False,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            raise
        return []
    query: Dict[str, Any] = {"project_id": str(project_id)}
    if organization_id:
        query["organization_id"] = str(organization_id)
    rows = await db.contract_master.find(
        query, {"contract_id": 1, "contract_name": 1, "contract_code": 1}
    ).to_list(length=200)
    seen: Dict[str, ClaimContractOption] = {}
    for row in sorted(rows, key=lambda item: str(item.get("contract_id") or "")):
        key = str(row.get("contract_id") or "").strip()
        if key and key not in seen:
            seen[key] = ClaimContractOption(contract_id=key, label=contract_label(row))
    return list(seen.values())


def describe(options: List[ClaimContractOption]) -> ClaimContractOptions:
    return ClaimContractOptions(
        options=options,
        selected=options[0].contract_id if len(options) == 1 else None,
        must_choose=len(options) > 1,
    )


def resolve_for_new_claim(
    requested: Optional[str], options: List[ClaimContractOption], *, must_choose: bool = True
) -> Optional[str]:
    """The contract a new claim is created under.

    ``must_choose=False`` is for a claim the system raises from a signal: there
    is no form to ask on, so with several contracts and none named the claim is
    created without one and shows "No contract identified".
    """
    valid = {item.contract_id for item in options}
    wanted = (requested or "").strip() or None
    if wanted is not None:
        if wanted not in valid:
            raise _refuse(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "claim_contract_not_valid",
                "That contract is not one of this project's contracts, or you cannot read it.",
            )
        return wanted
    if len(options) == 1:
        return options[0].contract_id
    if len(options) > 1 and must_choose:
        raise _refuse(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "claim_contract_required",
            "This project has more than one contract. Choose the contract this claim is made under.",
            options=[item.model_dump() for item in options],
        )
    return None


def resolve_for_existing_claim(
    claim: Dict[str, Any], requested: Optional[str], options: List[ClaimContractOption]
) -> Optional[str]:
    """Give a claim that has no contract one. A claim that has one keeps it."""
    current = (str(claim.get("contract_id") or "").strip()) or None
    wanted = (requested or "").strip() or None
    if wanted == current:
        return current
    if current is not None:
        raise _refuse(
            status.HTTP_409_CONFLICT,
            "claim_contract_fixed",
            "A claim's contract cannot be changed once it is set: the records linked to it belong to that contract.",
        )
    return resolve_for_new_claim(wanted, options)


__all__ = [
    "ClaimContractOption",
    "ClaimContractOptions",
    "contract_label",
    "describe",
    "resolve_for_existing_claim",
    "resolve_for_new_claim",
    "valid_contracts",
]
