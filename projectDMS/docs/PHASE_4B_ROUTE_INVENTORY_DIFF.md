# Phase 4b route inventory diff (2026-11-18)

Exit criterion 5 ("route inventories regenerated") evidence, at HEAD `378076d` (the last pushed commit when generated; this document's commit adds no route).

## How it was generated

```bash
PYTHONPATH=backend python scripts/rbac_phase0_route_inventory.py --format json > inventory.json
python scripts/gate3_route_inventory.py --format json > gate3.json
```

at HEAD and, in separate worktrees, at two baselines: `6d65bc3` (merge-base with `main`, where Phase 4b began on this branch) and `2796053` (the last commit before JG-1 to JG-5 in this session).

## Backend API routes (`rbac_phase0_route_inventory.py`)

| Compared with | Routes | Added | Removed | Reclassified |
|---|---|---|---|---|
| `2796053` | 750 -> 751 | 1 (`POST /api/claims/start`, JG-1, `policy_service`) | 0 | 0 |
| `6d65bc3` | 675 -> 751 | 76 (56 `auth_only`, 16 `policy_service`, 2 `scope_only`, 2 `permission_only`) | 0 | 1 |

**No public route was added; no route was removed.** JG-5 added no route; its authorisation changes are inside services.

**The one reclassification**, `GET /api/skills/{skill_key}/versions/{version}` `policy_service` -> `auth_only`: commit `c6971ae` (4B-08/09) moved its `policy.authorize` call into the helper `_authorize_reader_or_reviewer` (`routers/skills.py:118`, which calls `policy.authorize`). The inventory matches source text in the route body, so it no longer sees the call. Not a weakening.

**`auth_only` is not "unprotected".** 56 new routes authorise inside their services (Claim Bench, determinations, escalation, reviewer assignments, skills). The inventory cannot tell, so `tests/integration/test_phase4b_route_authz_sweep_mongo.py` (new, in CI's real-Mongo step) calls **every one of the 76 routes** on the production app with a real replica set, with the smallest request each route's own OpenAPI schema accepts:

- a user of another organisation: never a 2xx except four routes open to any signed-in user and three reads of the caller's own project; never an identifier of the tenant under test in the body; never a 5xx;
- a member of the claim's own project holding no permission: never a 2xx except the four open routes (`/api/build`, `/api/claims/bench/availability`, `/api/reviewer-assignments/mine`, `/api/skills/review-queue`), each answering with the caller's own data;
- anonymous: 401 on all 76;
- **no request stopped at validation (422)**, so authorisation ran on every route.

A mutant removing the claim check from `GET /api/claims/{claim_id}/simulations` fails it.

## Frontend routes (`gate3_route_inventory.py`)

| Compared with | Routes | Added | Removed | Changed |
|---|---|---|---|---|
| `2796053` | 103 -> 103 | 0 | 0 | 0 |
| `6d65bc3` | 99 -> 103 | `/claims/:id/record`, `/reviewer-roles`, `/skill-reviews`, `/skills` | 0 | `/claims/:id` (page `ClaimDetailPage` -> `ClaimRoute`) |

`docs/GATE_3_ROUTE_INVENTORY.md` matches HEAD byte for byte. `docs/ROUTE_INVENTORY.md` (hand-maintained) was missing the four routes and the `/claims/:id` page change: updated. The permission mapping itself (`client/src/config/rolePermissions.ts`) already covered them and its tests pass (`routeInventory.test.ts`, `rolePermissions.sidebar.test.ts`: 23 passed).

**Open (Gate 3 bullet 8, not a route or permission change):** `/reviewer-roles`, `/skill-reviews` and `/skills` have no error-state (fault-injection) test ("MISSING - no fault-injection coverage yet" in the Gate 3 inventory).

## Gates run

`test_route_inventory.py`, `test_gate3_route_inventory.py`, `test_upload_route_inventory.py`, `test_gate2_required_inventory.py`: 73 passed. `test_ci_real_mongo_coverage.py`: 11 passed with the sweep listed.

## Raw backend diff

```
== since 2796053 (before JG-1..JG-5): 750 -> 751 routes; +1 -0 ~0
  classification of added: {'policy_service': 1}
  + POST /api/claims/start policy_service unsafe
  public/unauthenticated at HEAD side: []
== since merge-base 6d65bc3 (Phase 4b on this branch): 675 -> 751 routes; +76 -0 ~1
  classification of added: {'auth_only': 56, 'scope_only': 2, 'policy_service': 16, 'permission_only': 2}
  + GET /api/arbitration/drafts/{draft_id}/citations auth_only
  + GET /api/build auth_only
  + GET /api/claims/bench/availability auth_only
  + GET /api/claims/bench/signals scope_only
  + GET /api/claims/contract-options policy_service
  + GET /api/claims/notice/candidates policy_service
  + GET /api/claims/notice/requirements auth_only
  + GET /api/claims/{claim_id}/bench auth_only
  + GET /api/claims/{claim_id}/bench/delay/candidates scope_only
  + GET /api/claims/{claim_id}/bench/heads/{head_id}/delay auth_only
  + GET /api/claims/{claim_id}/bench/heads/{head_id}/quantum auth_only
  + GET /api/claims/{claim_id}/bench/steps/{step_id} auth_only
  + GET /api/claims/{claim_id}/determinations auth_only
  + GET /api/claims/{claim_id}/determinations/{determination_id} auth_only
  + GET /api/claims/{claim_id}/evidence-contract auth_only
  + GET /api/claims/{claim_id}/simulations auth_only
  + GET /api/claims/{claim_id}/strategy-note auth_only
  + GET /api/claims/{claim_id}/submission auth_only
  + GET /api/claims/{claim_id}/submission/export auth_only
  + GET /api/key-dates/recalculate/preview policy_service
  + GET /api/pickers/users policy_service
  + GET /api/projects/{project_id}/contractual-capacity permission_only
  + GET /api/reviewer-assignments auth_only
  + GET /api/reviewer-assignments/legacy-report auth_only
  + GET /api/reviewer-assignments/mine auth_only
  + GET /api/skills/review-queue auth_only
  + GET /api/skills/{skill_key}/versions/{version}/approvals auth_only
  + GET /api/skills/{skill_key}/versions/{version}/eval-runs auth_only
  + PATCH /api/claims/{claim_id}/bench/heads/{head_id} auth_only unsafe
  + PATCH /api/organizations/{organization_id}/default-contractual-capacity policy_service unsafe
  + PATCH /api/projects/{project_id}/contractual-capacity permission_only unsafe
  + POST /api/claims/bench/potential policy_service unsafe
  + POST /api/claims/escalation/preview auth_only unsafe
  + POST /api/claims/escalation/run auth_only unsafe
  + POST /api/claims/escalation/undo policy_service unsafe
  + POST /api/claims/notice/requirements policy_service unsafe
  + POST /api/claims/notice/requirements/{requirement_id}/verify policy_service unsafe
  + POST /api/claims/start policy_service unsafe
  + POST /api/claims/{claim_id}/bench/heads auth_only unsafe
  + POST /api/claims/{claim_id}/bench/heads/{head_id}/delay/links policy_service unsafe
  + POST /api/claims/{claim_id}/bench/heads/{head_id}/delay/links/{link_id}/remove auth_only unsafe
  + POST /api/claims/{claim_id}/bench/heads/{head_id}/delay/overlaps/{overlap_id}/cost-basis/review policy_service unsafe
  + POST /api/claims/{claim_id}/bench/heads/{head_id}/quantum/lines auth_only unsafe
  + POST /api/claims/{claim_id}/bench/heads/{head_id}/quantum/lines/{line_id}/remove auth_only unsafe
  + POST /api/claims/{claim_id}/bench/heads/{head_id}/remove auth_only unsafe
  + POST /api/claims/{claim_id}/bench/heads/{head_id}/test auth_only unsafe
  + POST /api/claims/{claim_id}/bench/lock auth_only unsafe
  + POST /api/claims/{claim_id}/bench/open auth_only unsafe
  + POST /api/claims/{claim_id}/bench/unlock auth_only unsafe
  + POST /api/claims/{claim_id}/determinations auth_only unsafe
  + POST /api/claims/{claim_id}/determinations/{determination_id}/approve auth_only unsafe
  + POST /api/claims/{claim_id}/determinations/{determination_id}/propose auth_only unsafe
  + POST /api/claims/{claim_id}/determinations/{determination_id}/reassign auth_only unsafe
  + POST /api/claims/{claim_id}/determinations/{determination_id}/record auth_only unsafe
  + POST /api/claims/{claim_id}/determinations/{determination_id}/return auth_only unsafe
  + POST /api/claims/{claim_id}/determinations/{determination_id}/revise auth_only unsafe
  + POST /api/claims/{claim_id}/determinations/{determination_id}/submit auth_only unsafe
  + POST /api/claims/{claim_id}/evidence-contract/confirmations policy_service unsafe
  + POST /api/claims/{claim_id}/evidence-contract/confirmations/{confirmation_id}/withdraw policy_service unsafe
  + POST /api/claims/{claim_id}/simulations auth_only unsafe
  + POST /api/claims/{claim_id}/strategy-note/generate auth_only unsafe
  + POST /api/claims/{claim_id}/submission/compose auth_only unsafe
  + POST /api/reviewer-assignments auth_only unsafe
  + POST /api/reviewer-assignments/{assignment_id}/revoke auth_only unsafe
  + POST /api/skills/approvals/{approval_id}/revoke policy_service unsafe
  + POST /api/skills/{skill_key}/versions/{version}/approvals auth_only unsafe
  + POST /api/skills/{skill_key}/versions/{version}/eval-runs policy_service unsafe
  + PUT /api/claims/{claim_id}/bench/heads/{head_id}/delay/events/{event_id} auth_only unsafe
  + PUT /api/claims/{claim_id}/bench/heads/{head_id}/delay/overlaps/{overlap_id} auth_only unsafe
  + PUT /api/claims/{claim_id}/bench/heads/{head_id}/delay/overlaps/{overlap_id}/cost-basis auth_only unsafe
  + PUT /api/claims/{claim_id}/bench/heads/{head_id}/delay/settings auth_only unsafe
  + PUT /api/claims/{claim_id}/bench/heads/{head_id}/elements/{element_key} auth_only unsafe
  + PUT /api/claims/{claim_id}/bench/heads/{head_id}/quantum/lines/{line_id} auth_only unsafe
  + PUT /api/claims/{claim_id}/determinations/{determination_id}/heads/{head_id} auth_only unsafe
  + PUT /api/claims/{claim_id}/strategy-note auth_only unsafe
  + PUT /api/claims/{claim_id}/submission/sections/{section_key} auth_only unsafe
  ~ GET /api/skills/{skill_key}/versions/{version} policy_service -> auth_only ['CurrentUser = Depends', 'current_user: CurrentUser', 'get_current_user', 'policy.authorize']
  public/unauthenticated at HEAD side: []
```
