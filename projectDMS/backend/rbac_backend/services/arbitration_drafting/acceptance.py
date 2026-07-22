from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict

from ...core.config import settings
from .repository import _jsonable


REQUIRED_ACCEPTANCE_CRITERIA = tuple(str(index) for index in range(1, 15))


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
