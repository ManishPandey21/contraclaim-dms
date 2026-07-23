from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict

from ...core.config import settings
from .repository import _collect, _jsonable
from .workflow_repository import artifact_hash


REQUIRED_ACCEPTANCE_CRITERIA = tuple(str(index) for index in range(1, 15))


async def resolve_server_backed_acceptance(
    db: Any,
    *,
    criteria: Dict[str, str],
    evidence_hashes: Dict[str, str],
    stakeholder_signoff_ids: list[str],
    organization_id: str,
    project_id: str,
) -> Dict[str, Any]:
    """Resolve externally asserted acceptance data to immutable server records.

    Request strings alone are not evidence. Each criterion must bind to a
    non-invalidated real-execution record in the authorized scope, and each
    stakeholder signoff must bind the exact evidence bundle to a distinct
    authenticated actor.
    """

    bundle_hash = artifact_hash(
        {
            "criteria": criteria,
            "evidence_hashes": {
                key: str(evidence_hashes[key]).lower()
                for key in sorted(evidence_hashes)
            },
            "organization_id": organization_id,
            "project_id": project_id,
        }
    )
    evidence_rows = await _collect(
        db.arbitration_acceptance_evidence.find(
            {
                "criterion": {"$in": list(REQUIRED_ACCEPTANCE_CRITERIA)},
                "organization_id": organization_id,
                "project_id": project_id,
                "status": "passed",
                "execution_mode": "real_execution",
                "invalidated_at": {"$exists": False},
            }
        )
    )
    evidence_by_criterion = {
        str(row.get("criterion") or ""): row
        for row in evidence_rows
        if str(row.get("evidence_hash") or "").lower()
        == str(evidence_hashes.get(str(row.get("criterion") or "")) or "").lower()
        and isinstance(row.get("executed_at"), datetime)
        and row.get("recorded_by")
    }
    missing_criteria = [
        criterion
        for criterion in REQUIRED_ACCEPTANCE_CRITERIA
        if criterion not in evidence_by_criterion
    ]
    if missing_criteria:
        return {
            "valid": False,
            "reason": "server_backed_real_execution_evidence_missing",
            "missing_criteria": missing_criteria,
            "bundle_hash": bundle_hash,
        }

    requested_signoffs = sorted(set(str(value) for value in stakeholder_signoff_ids if value))
    signoff_rows = await _collect(
        db.arbitration_acceptance_signoffs.find(
            {
                "_id": {"$in": requested_signoffs},
                "organization_id": organization_id,
                "project_id": project_id,
                "acceptance_bundle_hash": bundle_hash,
                "decision": "accepted",
                "invalidated_at": {"$exists": False},
            }
        )
    )
    valid_signoffs = [
        row
        for row in signoff_rows
        if isinstance(row.get("signed_at"), datetime)
        and row.get("actor_id")
        and row.get("actor_role")
    ]
    actors = {str(row["actor_id"]) for row in valid_signoffs}
    roles = {str(row["actor_role"]).lower() for row in valid_signoffs}
    legal_present = any("legal" in role or "counsel" in role for role in roles)
    operational_present = any(
        token in role
        for role in roles
        for token in ("security", "operations", "sre", "platform")
    )
    if (
        {str(row.get("_id")) for row in valid_signoffs} != set(requested_signoffs)
        or len(actors) < 2
        or not legal_present
        or not operational_present
    ):
        return {
            "valid": False,
            "reason": "bound_distinct_legal_and_operational_signoffs_missing",
            "bundle_hash": bundle_hash,
        }
    return {
        "valid": True,
        "bundle_hash": bundle_hash,
        "evidence_record_ids": sorted(
            str(evidence_by_criterion[key]["_id"])
            for key in REQUIRED_ACCEPTANCE_CRITERIA
        ),
        "signoff_receipt_ids": requested_signoffs,
        "signoff_actor_ids": sorted(actors),
        "signoff_roles": sorted(roles),
    }


def acceptance_payload(receipt: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: _jsonable(value)
        for key, value in receipt.items()
        if key not in {"receipt_hash", "server_signature", "_resolved_valid"}
    }


def acceptance_hash(receipt: Dict[str, Any]) -> str:
    raw = json.dumps(acceptance_payload(receipt), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sign_acceptance(receipt_hash: str) -> str:
    return hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        str(receipt_hash).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def verify_acceptance_receipt(
    receipt: Dict[str, Any] | None,
    *,
    receipt_id: str,
    receipt_hash: str,
    organization_id: str,
    project_id: str,
    now: datetime | None = None,
) -> bool:
    if not receipt or str(receipt.get("_id") or "") != str(receipt_id):
        return False
    expected_hash = acceptance_hash(receipt)
    if not hmac.compare_digest(expected_hash, str(receipt_hash or "")):
        return False
    if not hmac.compare_digest(sign_acceptance(expected_hash), str(receipt.get("server_signature") or "")):
        return False
    if receipt.get("status") != "accepted" or receipt.get("revoked_at"):
        return False
    criteria = receipt.get("criteria") or {}
    if any(criteria.get(key) != "passed" for key in REQUIRED_ACCEPTANCE_CRITERIA):
        return False
    evidence_hashes = receipt.get("evidence_hashes") or {}
    if any(
        not all(character in "0123456789abcdefABCDEF" for character in str(evidence_hashes.get(key) or ""))
        or len(str(evidence_hashes.get(key) or "")) != 64
        for key in REQUIRED_ACCEPTANCE_CRITERIA
    ):
        return False
    if len(set(str(value) for value in receipt.get("stakeholder_signoffs") or [] if value)) < 2:
        return False
    acceptance_bundle_hash = str(receipt.get("acceptance_bundle_hash") or "")
    if (
        len(acceptance_bundle_hash) != 64
        or not all(character in "0123456789abcdefABCDEF" for character in acceptance_bundle_hash)
    ):
        return False
    evidence_record_ids = {
        str(value) for value in receipt.get("evidence_record_ids") or [] if value
    }
    if len(evidence_record_ids) != len(REQUIRED_ACCEPTANCE_CRITERIA):
        return False
    signoff_ids = {
        str(value) for value in receipt.get("signoff_receipt_ids") or [] if value
    }
    if signoff_ids != {
        str(value) for value in receipt.get("stakeholder_signoffs") or [] if value
    }:
        return False
    if len({str(value) for value in receipt.get("signoff_actor_ids") or [] if value}) < 2:
        return False
    signoff_roles = {
        str(value).lower() for value in receipt.get("signoff_roles") or [] if value
    }
    if not any("legal" in role or "counsel" in role for role in signoff_roles):
        return False
    if not any(
        token in role
        for role in signoff_roles
        for token in ("security", "operations", "sre", "platform")
    ):
        return False
    if organization_id not in set(receipt.get("organization_ids") or []):
        return False
    project_ids = set(receipt.get("project_ids") or [])
    if project_ids and project_id not in project_ids:
        return False
    now = now or datetime.now(timezone.utc)
    expires_at = receipt.get("expires_at")
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > now
