"""Generate the shared backend/frontend permission and entitlement contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from rbac_backend.core.permissions import (  # noqa: E402
    CANONICAL_PERMISSIONS,
    PERMISSION_COMPATIBILITY_ALIASES,
    PERMISSION_CONTRACT_VERSION,
)
from rbac_backend.services.entitlement_service import required_feature_keys  # noqa: E402


OPEN_ROUTES = {"/overview", "/security-terms", "/profile", "/notifications", "/legal-words"}

ROUTE_PERMISSIONS = {
    "/overview": [],
    "/security-terms": [],
    "/dashboard": ["dms.dashboard.view"],
    "/register": ["dms.user.manage"],
    "/organizations": ["dms.organization.view"],
    "/projects": ["dms.dashboard.view"],
    "/parties": ["parties:read"],
    "/representatives": ["representatives:read"],
    "/email-groups": ["email_groups:read"],
    "/upload": ["dms.document.upload"],
    "/documents": ["dms.document.view"],
    "/documentsearch": ["dms.document.view"],
    "/documentviewer": ["dms.document.view"],
    "/reference": ["dms.document.view"],
    "/share": ["dms.document.share"],
    "/letters": ["drafting.request.view"],
    "/letter-quality": ["drafting.request.view"],
    "/letter-templates": ["letter_templates:read"],
    "/contracts": ["dms.document.view"],
    "/contracts/viewer": ["dms.document.view"],
    "/contracts/clauses": ["dms.document.view"],
    "/contracts/appraisal": ["dms.contract.appraisal.view"],
    "/contracts/timeline": ["dms.contract.timeline.view"],
    "/contracts/master": ["dms.contract.master.view"],
    "/chronology": ["dms.chronology.view"],
    "/chronology/new": ["dms.chronology.create"],
    "/arbitration": ["dms.arbitration.view"],
    "/arbitration/cases": ["dms.arbitration.view"],
    "/arbitration/cases/new": ["dms.arbitration.create"],
    "/arbitration/claim": ["dms.arbitration.create"],
    "/arbitration/defence": ["dms.arbitration.create"],
    "/arbitration/rejoinder": ["dms.arbitration.create"],
    "/arbitration/counterclaim": ["dms.arbitration.create"],
    "/arbitration/drafts": ["dms.arbitration.view"],
    "/claims": ["dms.claim.view"],
    "/sla": ["dms.claim.view"],
    "/key-dates": ["dms.keydate.view"],
    "/variations": ["dms.variation.view"],
    "/bank-guarantees": ["dms.bankguarantee.view"],
    "/insurance": ["dms.insurance.view"],
    "/ipc-bills": ["dms.ipc.view"],
    "/concerns": ["dms.concern.view"],
    "/retrieval-console": ["dms.document.view"],
    "/folders": ["dms.document.view"],
    "/reports": ["dms.report.view"],
    "/tags": ["tags:read"],
    "/tasks": ["dms.task.view"],
    "/admin/billing-catalog": ["billing.plan.manage", "system:admin"],
    "/observability": ["dms.report.view", "system:admin"],
    "/health": ["system:admin"],
    "/users": ["dms.user.manage"],
    "/permissions": ["roles:read", "permissions:read"],
    "/plan-settings": ["billing.plan.view", "subscription.entitlement.manage"],
    "/subscription-management": ["billing.plan.view", "subscription.entitlement.manage", "subscription.upgrade"],
    "/billing/return": ["billing.plan.view", "subscription.entitlement.manage", "subscription.upgrade"],
    "/settings": [
        "settings.storage.view",
        "settings.smtp.view",
        "settings.notification.view",
        "settings.legal.view",
        "settings.prompt.view",
    ],
    "/notifications": [],
    "/legal-words": [],
    "/admin/legal-words": ["system:admin"],
    "/profile": ["profile:read"],
}

DRAFTING_COMMERCIAL_ROUTES = {"/letters", "/letter-quality", "/letter-templates"}
NON_COMMERCIAL_ROUTES = {
    "/overview",
    "/security-terms",
    "/notifications",
    "/legal-words",
    "/profile",
    "/admin/legal-words",
    "/admin/billing-catalog",
    "/observability",
    "/health",
    "/permissions",
    "/plan-settings",
    "/subscription-management",
    "/billing/return",
    "/settings",
}


def _features_for(path: str, permissions: list[str]) -> list[str]:
    if path in NON_COMMERCIAL_ROUTES:
        return []
    features = set()
    for permission in permissions:
        features.update(required_feature_keys(permission))
    if path in DRAFTING_COMMERCIAL_ROUTES:
        features.add("feature.drafting.enabled")
    else:
        features.add("feature.dms.enabled")
    return sorted(features)


def build_contract() -> dict:
    routes = {}
    for path, required_any in ROUTE_PERMISSIONS.items():
        routes[path] = {
            "required_any_permissions": required_any,
            "required_all_permissions": [],
            "required_all_features": _features_for(path, required_any),
            "open_authenticated": path in OPEN_ROUTES,
        }
    return {
        "version": PERMISSION_CONTRACT_VERSION,
        "canonical_permissions": sorted(CANONICAL_PERMISSIONS),
        "permission_aliases": {
            key: sorted(values)
            for key, values in sorted(PERMISSION_COMPATIBILITY_ALIASES.items())
        },
        "routes": routes,
    }


def render_typescript(contract: dict) -> str:
    payload = json.dumps(contract, indent=2, sort_keys=True)
    return (
        "/** Generated by scripts/generate_permission_contract.py. Do not edit. */\n"
        f"export const PERMISSION_CONTRACT = {payload} as const;\n\n"
        "export const PERMISSION_CONTRACT_VERSION = PERMISSION_CONTRACT.version;\n"
        "export const PERMISSION_ALIASES: Record<string, readonly string[]> = PERMISSION_CONTRACT.permission_aliases;\n"
        "export const ROUTE_ACCESS_RULES = PERMISSION_CONTRACT.routes;\n"
    )


def main() -> None:
    contract = build_contract()
    backend_target = BACKEND / "rbac_backend" / "contracts" / "permission_contract.json"
    frontend_target = ROOT / "client" / "src" / "config" / "generatedPermissionContract.ts"
    backend_target.parent.mkdir(parents=True, exist_ok=True)
    backend_target.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    frontend_target.write_text(render_typescript(contract), encoding="utf-8")
    print(f"Generated permission contract {PERMISSION_CONTRACT_VERSION}")


if __name__ == "__main__":
    main()
