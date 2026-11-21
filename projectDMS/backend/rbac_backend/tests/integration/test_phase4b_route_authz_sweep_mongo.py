"""Every route Phase 4b added, swept for authorisation over real HTTP and RBAC.

The route inventory (`scripts/rbac_phase0_route_inventory.py`) classifies 56 of the
76 routes added since the Phase 4b merge-base (`6d65bc3`) as `auth_only`: their
bodies name no permission because they authorise inside a service. The inventory
reads source text, so it cannot say whether those services refuse. This does, on
the production app with a real MongoDB replica set:

- a user of another organisation never gets a 2xx, a 5xx or another tenant's data;
- a member of the claim's own project who holds no permission never gets a 2xx,
  except on the routes listed in OPEN_TO_ANY_SIGNED_IN_USER, each with its reason;
- an anonymous caller gets 401 everywhere.

A route that answers 422 to the empty body sent here stopped at request validation
before its authorisation ran; the sweep counts those and asserts the count, so the
coverage it claims is the coverage it has (unsafe routes with required bodies are
covered by their own suites).

Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, Tuple

import pytest

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

#: (method, path, inventory classification) for every route added since 6d65bc3,
#: generated from the inventory diff of 2026-11-18 (docs/PHASE_4B_TICKET_REGISTER.md).
PHASE_4B_ROUTES: Tuple[Tuple[str, str, str], ...] = (
    ("GET", "/api/arbitration/drafts/{draft_id}/citations", "auth_only"),
    ("GET", "/api/build", "auth_only"),
    ("GET", "/api/claims/bench/availability", "auth_only"),
    ("GET", "/api/claims/bench/signals", "scope_only"),
    ("GET", "/api/claims/contract-options", "policy_service"),
    ("GET", "/api/claims/notice/candidates", "policy_service"),
    ("GET", "/api/claims/notice/requirements", "auth_only"),
    ("GET", "/api/claims/{claim_id}/bench", "auth_only"),
    ("GET", "/api/claims/{claim_id}/bench/delay/candidates", "scope_only"),
    ("GET", "/api/claims/{claim_id}/bench/heads/{head_id}/delay", "auth_only"),
    ("GET", "/api/claims/{claim_id}/bench/heads/{head_id}/quantum", "auth_only"),
    ("GET", "/api/claims/{claim_id}/bench/steps/{step_id}", "auth_only"),
    ("GET", "/api/claims/{claim_id}/determinations", "auth_only"),
    ("GET", "/api/claims/{claim_id}/determinations/{determination_id}", "auth_only"),
    ("GET", "/api/claims/{claim_id}/evidence-contract", "auth_only"),
    ("GET", "/api/claims/{claim_id}/simulations", "auth_only"),
    ("GET", "/api/claims/{claim_id}/strategy-note", "auth_only"),
    ("GET", "/api/claims/{claim_id}/submission", "auth_only"),
    ("GET", "/api/claims/{claim_id}/submission/export", "auth_only"),
    ("GET", "/api/key-dates/recalculate/preview", "policy_service"),
    ("GET", "/api/pickers/users", "policy_service"),
    ("GET", "/api/projects/{project_id}/contractual-capacity", "permission_only"),
    ("GET", "/api/reviewer-assignments", "auth_only"),
    ("GET", "/api/reviewer-assignments/legacy-report", "auth_only"),
    ("GET", "/api/reviewer-assignments/mine", "auth_only"),
    ("GET", "/api/skills/review-queue", "auth_only"),
    ("GET", "/api/skills/{skill_key}/versions/{version}/approvals", "auth_only"),
    ("GET", "/api/skills/{skill_key}/versions/{version}/eval-runs", "auth_only"),
    ("PATCH", "/api/claims/{claim_id}/bench/heads/{head_id}", "auth_only"),
    ("PATCH", "/api/organizations/{organization_id}/default-contractual-capacity", "policy_service"),
    ("PATCH", "/api/projects/{project_id}/contractual-capacity", "permission_only"),
    ("POST", "/api/claims/bench/potential", "policy_service"),
    ("POST", "/api/claims/escalation/preview", "auth_only"),
    ("POST", "/api/claims/escalation/run", "auth_only"),
    ("POST", "/api/claims/escalation/undo", "policy_service"),
    ("POST", "/api/claims/notice/requirements", "policy_service"),
    ("POST", "/api/claims/notice/requirements/{requirement_id}/verify", "policy_service"),
    ("POST", "/api/claims/start", "policy_service"),
    # Added after the diff: claim-type confirmation (owner decision 2026-11-18).
    ("POST", "/api/claims/{claim_id}/type", "policy_service"),
    # Added after the diff: delay-evidence snapshots (owner decision 2026-11-18).
    ("GET", "/api/claims/{claim_id}/evidence-snapshots", "policy_service"),
    ("POST", "/api/claims/{claim_id}/evidence-snapshots/{snapshot_id}/review", "policy_service"),
    ("POST", "/api/claims/{claim_id}/bench/heads", "auth_only"),
    ("POST", "/api/claims/{claim_id}/bench/heads/{head_id}/delay/links", "policy_service"),
    ("POST", "/api/claims/{claim_id}/bench/heads/{head_id}/delay/links/{link_id}/remove", "auth_only"),
    ("POST", "/api/claims/{claim_id}/bench/heads/{head_id}/delay/overlaps/{overlap_id}/cost-basis/review", "policy_service"),
    ("POST", "/api/claims/{claim_id}/bench/heads/{head_id}/quantum/lines", "auth_only"),
    ("POST", "/api/claims/{claim_id}/bench/heads/{head_id}/quantum/lines/{line_id}/remove", "auth_only"),
    ("POST", "/api/claims/{claim_id}/bench/heads/{head_id}/remove", "auth_only"),
    ("POST", "/api/claims/{claim_id}/bench/heads/{head_id}/test", "auth_only"),
    ("POST", "/api/claims/{claim_id}/bench/lock", "auth_only"),
    ("POST", "/api/claims/{claim_id}/bench/open", "auth_only"),
    ("POST", "/api/claims/{claim_id}/bench/unlock", "auth_only"),
    ("POST", "/api/claims/{claim_id}/determinations", "auth_only"),
    ("POST", "/api/claims/{claim_id}/determinations/{determination_id}/approve", "auth_only"),
    ("POST", "/api/claims/{claim_id}/determinations/{determination_id}/propose", "auth_only"),
    ("POST", "/api/claims/{claim_id}/determinations/{determination_id}/reassign", "auth_only"),
    ("POST", "/api/claims/{claim_id}/determinations/{determination_id}/record", "auth_only"),
    ("POST", "/api/claims/{claim_id}/determinations/{determination_id}/return", "auth_only"),
    ("POST", "/api/claims/{claim_id}/determinations/{determination_id}/revise", "auth_only"),
    ("POST", "/api/claims/{claim_id}/determinations/{determination_id}/submit", "auth_only"),
    ("POST", "/api/claims/{claim_id}/evidence-contract/confirmations", "policy_service"),
    ("POST", "/api/claims/{claim_id}/evidence-contract/confirmations/{confirmation_id}/withdraw", "policy_service"),
    ("POST", "/api/claims/{claim_id}/simulations", "auth_only"),
    ("POST", "/api/claims/{claim_id}/strategy-note/generate", "auth_only"),
    ("POST", "/api/claims/{claim_id}/submission/compose", "auth_only"),
    ("POST", "/api/reviewer-assignments", "auth_only"),
    ("POST", "/api/reviewer-assignments/{assignment_id}/revoke", "auth_only"),
    ("POST", "/api/skills/approvals/{approval_id}/revoke", "policy_service"),
    ("POST", "/api/skills/{skill_key}/versions/{version}/approvals", "auth_only"),
    ("POST", "/api/skills/{skill_key}/versions/{version}/eval-runs", "policy_service"),
    ("PUT", "/api/claims/{claim_id}/bench/heads/{head_id}/delay/events/{event_id}", "auth_only"),
    ("PUT", "/api/claims/{claim_id}/bench/heads/{head_id}/delay/overlaps/{overlap_id}", "auth_only"),
    ("PUT", "/api/claims/{claim_id}/bench/heads/{head_id}/delay/overlaps/{overlap_id}/cost-basis", "auth_only"),
    ("PUT", "/api/claims/{claim_id}/bench/heads/{head_id}/delay/settings", "auth_only"),
    ("PUT", "/api/claims/{claim_id}/bench/heads/{head_id}/elements/{element_key}", "auth_only"),
    ("PUT", "/api/claims/{claim_id}/bench/heads/{head_id}/quantum/lines/{line_id}", "auth_only"),
    ("PUT", "/api/claims/{claim_id}/determinations/{determination_id}/heads/{head_id}", "auth_only"),
    ("PUT", "/api/claims/{claim_id}/strategy-note", "auth_only"),
    ("PUT", "/api/claims/{claim_id}/submission/sections/{section_key}", "auth_only"),
)

PARAMS = {
    "claim_id": CLAIM, "head_id": "H1", "determination_id": "det-x", "line_id": "line-x", "link_id": "link-x",
    "overlap_id": "ov-x", "event_id": "ev-x", "step_id": "step-x", "element_key": "event",
    "section_key": "introduction", "skill_key": "indian-infra-claims", "version": "1.0.0",
    "approval_id": "appr-x", "assignment_id": "asg-x", "confirmation_id": "conf-x", "requirement_id": "req-x",
    "project_id": PROJ_A1, "organization_id": ORG_A, "draft_id": "draft-x", "snapshot_id": "snap-x",
}


def _path(template: str) -> str:
    return re.sub(r"\{([a-z_]+)\}", lambda m: PARAMS[m.group(1)], template)


#: Values for required query parameters and body fields, by name; anything else by type.
NAMED_VALUES: Dict[str, Any] = {
    "project_id": PROJ_A1, "organization_id": ORG_A, "claim_id": CLAIM, "claim_ids": [CLAIM],
    "board_version": 1, "version": 1, "head_id": "H1", "reason": "Authorisation sweep.",
    "claim_type": "eot", "role_key": "legal_reviewer", "reviewer_role": "legal_reviewer",
    "permission": "dms.claim.view", "action": "withdraw",
}

#: Bodies a route's schema alone cannot describe (cross-field rules).
ROUTE_BODIES: Dict[Tuple[str, str], Any] = {
    ("POST", "/api/reviewer-assignments"): {
        "user_id": "user-bare_member", "role_key": "legal_reviewer", "scope_type": "project",
        "organization_id": ORG_A, "project_id": PROJ_A1, "reason": "Authorisation sweep.",
    },
}

#: Routes any signed-in user may call, and why. Each answers with the caller's own data
#: or none; the sweep asserts no other tenant's or person's identifiers in the body.
OPEN_TO_ANY_SIGNED_IN_USER = {
    ("GET", "/api/build"): "the backend's commit id (JG-6)",
    ("GET", "/api/claims/bench/availability"): "whether the Claim Bench is on for the caller's own selection",
    ("GET", "/api/reviewer-assignments/mine"): "the caller's own reviewer roles",
    ("GET", "/api/skills/review-queue"): "skill reviews addressed to the caller's own roles",
}

#: Reads a foreign administrator may make in THEIR OWN organisation: the sweep calls them
#: under the foreign project, so the answer is about that project, never this one.
OWN_TENANT_READS = {
    ("GET", "/api/claims/bench/signals"),
    ("GET", "/api/claims/notice/candidates"),
    ("GET", "/api/claims/notice/requirements"),
}

#: Identifiers of the tenant under test that must never appear in a foreign answer.
TENANT_MARKERS = (ORG_A, PROJ_A1, CLAIM)


def _value(name: str, schema: Dict[str, Any], spec: Dict[str, Any], depth: int = 0) -> Any:
    if name in NAMED_VALUES:
        return NAMED_VALUES[name]
    if "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    for combinator in ("anyOf", "oneOf", "allOf"):
        if combinator in schema:
            options = [item for item in schema[combinator] if item.get("type") != "null"]
            return _value(name, options[0] if options else {}, spec, depth)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind, fmt = schema.get("type"), schema.get("format")
    if kind == "object" or "properties" in schema:
        if depth > 4:
            return {}
        required = schema.get("required") or []
        return {key: _value(key, sub, spec, depth + 1) for key, sub in (schema.get("properties") or {}).items() if key in required}
    if kind == "array":
        count = max(1, int(schema["minItems"])) if schema.get("minItems") else 0
        return [_value(name, schema.get("items") or {}, spec, depth + 1) for _ in range(count)]
    if kind == "integer":
        return max(1, int(schema.get("minimum") or 0))
    if kind == "number":
        return max(1.0, float(schema.get("minimum") or 0))
    if kind == "boolean":
        return False
    if fmt == "date":
        return "2026-03-02"
    if fmt == "date-time":
        return "2026-03-02T00:00:00"
    text = "Authorisation sweep value"
    return text[: int(schema.get("maxLength") or len(text))].ljust(int(schema.get("minLength") or 0), "x")


def _request(method: str, template: str, spec: Dict[str, Any]) -> Tuple[Dict[str, Any], Any]:
    """The smallest request the route's own schema accepts: required query parameters and body."""
    operation = spec["paths"][template][method.lower()]
    query = {
        item["name"]: _value(item["name"], item.get("schema") or {}, spec)
        for item in operation.get("parameters") or []
        if item.get("in") == "query" and item.get("required")
    }
    body = None
    content = ((operation.get("requestBody") or {}).get("content") or {}).get("application/json")
    if content:
        body = _value("body", content.get("schema") or {}, spec)
    elif method != "GET":
        body = {}
    return query, body


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _sweep(env: Any, persona: str, project: str) -> Dict[Tuple[str, str], Any]:
    from rbac_backend.main import app

    spec = app.openapi()
    out: Dict[Tuple[str, str], Any] = {}
    for method, template, _ in PHASE_4B_ROUTES:
        query, body = _request(method, template, spec)
        body = ROUTE_BODIES.get((method, template), body)
        out[(method, template)] = await env.call(method, persona, _path(template), project=project, json=body, params=query)
    return out


def test_every_route_phase_4b_added_refuses_outsiders_and_ungranted_members(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.main import app

            mounted = {(m.upper(), path) for path, ops in app.openapi()["paths"].items() for m in ops}
            assert {(m, p) for m, p, _ in PHASE_4B_ROUTES} <= mounted, "every swept route is mounted"
            assert (await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open")).status_code == 200
            # A member of the claim's own project holding no permission at all.
            await _add_actor(env.db, "bare_member", "role-bare-member", [])
            foreign = await _sweep(env, "foreign_org_admin", PROJ_B1)
            bare = await _sweep(env, "bare_member", PROJ_A1)
            validated_only = []
            for key in foreign:
                f, b = foreign[key], bare[key]
                for who, response in (("foreign", f), ("bare", b)):
                    assert response.status_code < 500, (who, key, response.status_code, response.text[:300])
                    if response.status_code == 422:
                        validated_only.append((who, key))
                if 200 <= f.status_code < 300:
                    assert key in OPEN_TO_ANY_SIGNED_IN_USER or key in OWN_TENANT_READS, ("foreign", key, f.text[:300])
                    assert not any(marker in f.text for marker in TENANT_MARKERS), ("foreign leak", key, f.text[:300])
                if 200 <= b.status_code < 300:
                    assert key in OPEN_TO_ANY_SIGNED_IN_USER, ("ungranted member", key, b.text[:300])
                    assert "user-org_admin" not in b.text, ("another person's data", key, b.text[:300])
            # What this sweep could not exercise: requests refused at validation before authorisation.
            assert validated_only == [], validated_only
            anonymous = []
            for method, template, _ in PHASE_4B_ROUTES:
                response = await env.client.request(method, _path(template), headers={"X-Proj-Id": PROJ_A1})
                anonymous.append((method, template, response.status_code))
            assert all(status == 401 for *_, status in anonymous), [a for a in anonymous if a[2] != 401]

    _run(scenario())
