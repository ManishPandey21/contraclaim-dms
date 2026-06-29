"""Versioned RBAC seed catalog metadata.

The permission and role seed files are executable data, so production upgrades
need a deterministic fingerprint that can be recorded alongside schema
migrations.  This module intentionally keeps the digest limited to stable seed
fields and excludes dynamic timestamps from role definitions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from ..core.permissions import CANONICAL_PERMISSIONS, CLIENT_DMS_PERMISSIONS
from .default_permissions import DEFAULT_PERMISSIONS
from .default_roles import DEFAULT_ROLES


SEED_CATALOG_ID = "rbac-permissions-and-roles"
SEED_CATALOG_VERSION = "2026.06.29"


def _permission_id(entry: Dict[str, Any]) -> str:
    return str(entry.get("_id") or entry.get("name") or "").strip()


def normalized_permission_catalog() -> List[Dict[str, str]]:
    """Return stable permission seed rows sorted by permission id."""

    normalized = []
    for entry in DEFAULT_PERMISSIONS:
        permission_id = _permission_id(entry)
        if not permission_id:
            continue
        normalized.append(
            {
                "id": permission_id,
                "label": str(entry.get("name") or permission_id),
            }
        )
    return sorted(normalized, key=lambda item: item["id"])


def normalized_role_catalog() -> List[Dict[str, Any]]:
    """Return stable role seed rows sorted by role id."""

    normalized = []
    for role in DEFAULT_ROLES:
        role_id = str(role.get("_id") or "").strip()
        if not role_id:
            continue
        normalized.append(
            {
                "id": role_id,
                "name": str(role.get("name") or role_id),
                "scope": str(role.get("scope") or ""),
                "is_system": bool(role.get("is_system", True)),
                "is_active": bool(role.get("is_active", True)),
                "permissions": sorted({str(permission) for permission in (role.get("permissions") or [])}),
            }
        )
    return sorted(normalized, key=lambda item: item["id"])


def seed_catalog_payload() -> Dict[str, Any]:
    """Return the deterministic payload used for catalog hashing."""

    return {
        "catalog_id": SEED_CATALOG_ID,
        "version": SEED_CATALOG_VERSION,
        "permissions": normalized_permission_catalog(),
        "roles": normalized_role_catalog(),
        "canonical_permissions": sorted({str(permission) for permission in CANONICAL_PERMISSIONS}),
        "client_dms_permissions": sorted({str(permission) for permission in CLIENT_DMS_PERMISSIONS}),
    }


def seed_catalog_digest(payload: Dict[str, Any] | None = None) -> str:
    """Return a stable SHA-256 digest for the current seed catalog."""

    source = payload if payload is not None else seed_catalog_payload()
    encoded = json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seed_catalog_validation_issues(payload: Dict[str, Any] | None = None) -> List[str]:
    """Validate role references and canonical permission coverage."""

    source = payload if payload is not None else seed_catalog_payload()
    permission_ids = {entry["id"] for entry in source["permissions"]}
    canonical = set(source["canonical_permissions"])
    issues: List[str] = []

    missing_canonical = sorted(canonical - permission_ids)
    if missing_canonical:
        issues.append(f"canonical permissions missing from initial seed catalog: {missing_canonical}")

    for role in source["roles"]:
        missing = sorted(set(role["permissions"]) - permission_ids)
        if missing:
            issues.append(f"role {role['id']} references permissions missing from seed catalog: {missing}")

    orgadmin = next((role for role in source["roles"] if role["id"] == "orgadmin"), None)
    if orgadmin:
        missing_client_dms = sorted(set(source["client_dms_permissions"]) - set(orgadmin["permissions"]))
        if missing_client_dms:
            issues.append(f"orgadmin seed missing Client DMS permissions: {missing_client_dms}")
    else:
        issues.append("orgadmin seed role is missing")

    return issues


def seed_catalog_record(now: datetime | None = None) -> Dict[str, Any]:
    """Build the Mongo document stored in ``seed_catalog_versions``."""

    payload = seed_catalog_payload()
    observed_at = now or datetime.now(timezone.utc)
    return {
        "_id": SEED_CATALOG_ID,
        "catalog_id": SEED_CATALOG_ID,
        "version": SEED_CATALOG_VERSION,
        "digest": seed_catalog_digest(payload),
        "permission_count": len(payload["permissions"]),
        "role_count": len(payload["roles"]),
        "canonical_permission_count": len(payload["canonical_permissions"]),
        "client_dms_permission_count": len(payload["client_dms_permissions"]),
        "validation_issues": seed_catalog_validation_issues(payload),
        "payload": payload,
        "updated_at": observed_at,
    }
