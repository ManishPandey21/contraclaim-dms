"""4B-49 (AQ-12): every path to dms.document.review_extraction, against the real PolicyService.

Each persona is a real user whose principal is built from a real access token
(``principal_from_access_token``), on a database seeded with the real permission
and role catalogue (``initialize_permissions``, ``initialize_roles``). For each one
this test checks that:

- the classifier reading P-15 uses (``review_extraction_grant_paths``) agrees with
  ``PolicyService.has_permission`` (does the check pass?) and with
  ``PolicyService.has_explicit_grant`` (is it held by name, or by the catalogue id the
  Permissions page saves?);
- the paths are the pinned CURRENT behaviour - including the inheritance AQ-12 is
  about, which this ticket records and does not close;
- the live review route (``GET /api/documents/{id}/extraction-review``) offers
  review (``can_review``) exactly to the personas the check admits;
- reading P-15 counts the same users, by role and path, without naming anyone.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set). Run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, FrozenSet, List

import pytest
from pymongo import MongoClient

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env, _uri
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    DOC_IN_ID,
    ORG_A,
    PROJ_A1,
    _token,
)

pytestmark = pytest.mark.integration

PERMISSION = "dms.document.review_extraction"
READER = Path(__file__).resolve().parents[4] / "scripts" / "claim_arbitration_readonly_readings.py"
REVIEW = f"/api/documents/{DOC_IN_ID}/extraction-review"


def _reader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("claim_arbitration_readonly_readings_p15", READER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Custom roles: one for each way a role document can carry the grant.
CUSTOM_ROLES: Dict[str, Dict[str, Any]] = {
    "rx_named": {"permissions": ["dms.document.view", PERMISSION]},
    "rx_wildcard": {"permissions": ["*"]},
    "rx_dms_admin": {"permissions": ["dms.admin", "dms.document.view"]},
    "rx_named_inactive": {"permissions": ["dms.document.view", PERMISSION], "is_active": False},
}

#: persona -> (role references, extra user fields, paths expected TODAY).
PERSONAS: Dict[str, tuple] = {
    "rx_superadmin": (["superadmin"], {"organization_id": None, "projects": []}, {"superadmin", "dms_admin"}),
    "rx_orgadmin": (["orgadmin"], {}, {"named", "dms_admin"}),
    "rx_contractmgr": (["contractmgr_org"], {}, {"dms_admin"}),
    "rx_projectadmin": (["projectadmin"], {}, {"named", "dms_admin"}),
    "rx_orguser": (["orguser"], {}, set()),
    "rx_projectuser": (["projectuser"], {}, set()),
    "rx_doccontroller": (["doccontroller"], {}, set()),
    # A legacy spelling resolves one hop to its canonical role, and so to its grants.
    "rx_alias_orgadmin": (["organization-admin"], {}, {"named", "dms_admin"}),
    # A system alias keeps the Super Admin key without reaching the system document.
    "rx_alias_superadmin": (["super-admin"], {"organization_id": None, "projects": []}, {"superadmin"}),
    # Super User has no role document: no path.
    "rx_superuser": (["superuser"], {"organizations": [ORG_A]}, set()),
    "rx_custom_named": (["rx_named"], {}, {"named"}),
    "rx_custom_wildcard": (["rx_wildcard"], {}, {"wildcard"}),
    "rx_custom_dms_admin": (["rx_dms_admin"], {}, {"dms_admin"}),
    # A deactivated role grants nothing, named or not.
    "rx_custom_inactive": (["rx_named_inactive"], {}, set()),
    # The user document's own `permissions` field is not read by the check.
    "rx_user_level_only": (["orguser"], {"permissions": [PERMISSION]}, set()),
    # A service account is judged by its roles like anyone else: no shortcut, no exclusion.
    "rx_service_contractmgr": (["contractmgr_org"], {"account_type": "system_service"}, {"dms_admin"}),
}

#: Personas whose review offer is checked through the live route (client scope, org A).
ROUTE_PERSONAS = (
    "rx_superadmin", "rx_orgadmin", "rx_contractmgr", "rx_projectadmin", "rx_orguser", "rx_projectuser",
    "rx_alias_orgadmin", "rx_custom_named", "rx_custom_wildcard", "rx_custom_dms_admin", "rx_custom_inactive",
    "rx_user_level_only",
)


async def _seed_personas(db: Any, spellings: List[str]) -> Dict[str, tuple]:
    personas = dict(PERSONAS)
    roles = dict(CUSTOM_ROLES)
    # Every other spelling the permission catalogue makes a grant (label, ObjectId), read from
    # the seeded catalogue rather than assumed.
    for index, spelling in enumerate(sorted(spellings)):
        role_id = f"rx_spelling_{index}"
        roles[role_id] = {"permissions": ["dms.document.view", spelling]}
        personas[f"rx_custom_spelling_{index}"] = ([role_id], {}, {"named_by_permission_document"})
    for role_id, spec in roles.items():
        await db.roles.insert_one(
            {"_id": role_id, "name": role_id, "description": "4B-49 test role", "scope": "organization",
             "organization_id": ORG_A, "is_active": spec.get("is_active", True), "permissions": spec["permissions"]}
        )
    for persona, (references, extra, _) in personas.items():
        await db.users.insert_one(
            {"_id": f"user-{persona}", "email": f"{persona}@example.com", "username": persona, "disabled": False,
             "account_type": "client_user", "organizations": [], "roles": references,
             "organization_id": ORG_A, "projects": [PROJ_A1], **extra}
        )
    return personas


def test_every_path_to_extraction_review_is_classified_as_the_policy_decides(monkeypatch) -> None:
    async def scenario() -> None:
        from rbac_backend.core.role_reference import resolve_role_reference
        from rbac_backend.core.security import principal_from_access_token
        from rbac_backend.services.policy_service import PolicyService

        reader = _reader()
        async with _env(monkeypatch) as env:
            catalogue = await env.db.permissions.find({}).to_list(length=None)
            spellings = reader.permission_document_references(catalogue, PERMISSION)
            personas = await _seed_personas(env.db, sorted(spellings))
            roles = await env.db.roles.find({}).to_list(length=None)
            policy = PolicyService(env.db)

            observed: Dict[str, FrozenSet[str]] = {}
            for persona, (references, _, expected) in personas.items():
                principal = await principal_from_access_token(_token(persona), env.db)
                assert principal is not None, persona
                paths = reader.review_extraction_grant_paths(references, roles, spellings, resolve_role_reference)
                observed[persona] = paths
                effective = await policy.has_permission(principal, PERMISSION)
                explicit = await policy.has_explicit_grant(principal, PERMISSION)
                assert effective is bool(paths), (persona, paths, effective)
                assert explicit is bool(paths & set(reader.NAMED_PATHS)), (persona, paths, explicit)
                assert paths == frozenset(expected), (persona, sorted(paths), sorted(expected))

            for persona in ROUTE_PERSONAS:
                headers = env.headers(persona)
                if persona == "rx_superadmin":
                    headers["X-Org-Id"] = ORG_A
                response = await env.client.get(REVIEW, headers=headers)
                if persona == "rx_custom_inactive":
                    # Its only role is deactivated: it may not even view the document.
                    assert response.status_code == 403, (persona, response.status_code)
                    continue
                assert response.status_code == 200, (persona, response.status_code, response.text[:300])
                assert response.json()["can_review"] is bool(observed[persona]), (persona, observed[persona])

            # Reading P-15 on the same database, through the read-only guard.
            client = MongoClient(_uri(), serverSelectionTimeoutMS=5_000)
            try:
                row = next(r for r in reader.collect(client[env.db.name], environ={}) if r["reading"] == "P-15")
            finally:
                client.close()
            assert row["status"] == "read", row
            text = json.dumps(row, default=str)
            for persona in personas:
                assert persona not in text and f"user-{persona}" not in text
            for custom in ("rx_named", "rx_wildcard", "rx_dms_admin", "rx_spelling"):
                assert custom not in text, custom

            users = await env.db.users.find({}).to_list(length=None)
            expected_by_path: Dict[str, int] = {}
            by_name = inherited = 0
            for user in users:
                paths = reader.review_extraction_grant_paths(user.get("roles") or [], roles, spellings, resolve_role_reference)
                if paths & set(reader.NAMED_PATHS):
                    by_name += 1
                elif paths:
                    inherited += 1
                    for path in paths:
                        expected_by_path[path] = expected_by_path.get(path, 0) + 1
            assert row["users"]["by_name"] == by_name
            assert row["users"]["only_by_inheritance"] == inherited
            assert row["only_by_inheritance_by_path"] == expected_by_path
            assert row["only_by_inheritance_by_seed_role"]["contractmgr_org"] == 2
            assert row["only_by_inheritance_by_account_type"]["system_service"] == 1
            assert row["only_by_inheritance_holding_custom_roles"] == 2  # rx_wildcard, rx_dms_admin
            assert row["users"]["by_name_through_a_catalogue_id_only"] == len(spellings)
            assert row["role_documents_by_path"]["named"] >= 3  # orgadmin, projectadmin, rx_named

    asyncio.run(scenario())
