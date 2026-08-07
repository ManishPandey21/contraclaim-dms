# RBAC, Subscription and Entitlement Remediation — Verification Report

**Date:** 7 August 2026
**Scope:** H-04 / L-15 (semantic route authorization contract), M-11 (subscription
uniqueness), M-05 (plan-unavailable presentation), M-09 (shared effective-permission
contract)
**Branch:** `codex/arbitration-llm-drafting-p3` (working tree; nothing committed or pushed)
**Prior state:** substantial uncommitted work already existed for all four issues; this
pass re-verified it from scratch rather than trusting it.

---

## 0. Correction to the issue statement

The brief stated the live inventory contains **578 routes** and classifies **188 unsafe
routes as `auth_only`**, 20 `permission_only`, 13 `step_up_only`, 6 `scope_only`.

Rebuilt from the running application, none of those figures hold:

| Claim | Measured | Note |
|---|---|---|
| 578 routes | **579** | `scripts/rbac_phase0_route_inventory.py --format summary` |
| 188 unsafe `auth_only` | **13** | and the taxonomy itself differs |
| 20 `permission_only` | 1 | |
| 13 `step_up_only` | 0 | classification no longer emitted |
| 6 `scope_only` | 0 | classification no longer emitted |

More importantly, **`unsafe` in this inventory does not mean "insecure."** It is defined at
`scripts/rbac_phase0_route_inventory.py:112` as
`UNSAFE_METHODS = {"POST","PUT","PATCH","DELETE"}` — a non-idempotent HTTP method. The 333
"unsafe" routes are mutating routes, not unprotected ones. The brief's framing ("classifies
188 unsafe routes as `auth_only`" implying 188 exposed endpoints) reads the field as a
security verdict, which it never was.

This correction does not reduce the work: the real defect was different and, in evidentiary
terms, worse. See §1.

---

## 1. H-04 / L-15 — Semantic route authorization contract

### 1.1 The real defect: the contract's evidence was not evidence

Two independent problems, both found by reading the generator and tests rather than their
output:

**(a) Enforcement labels came from substring matching over a source blob.**
`_classify_source` concatenated the endpoint source with the source of up to 120
transitively-reachable callables (depth ≤ 5) and searched for literals such as
`"policy.authorize"`. Because `_route_source` expands *classes* too, constructing a
`PolicyService` pulled that class's entire body — which naturally contains `authorize` —
into the blob. Any route reaching such a construction was labelled `policy_service`
regardless of whether it ever called a gate.

Measured provenance of the marker across the 511 policy-classified routes:

| Marker located in | Routes |
|---|---:|
| the endpoint body itself | 193 |
| only in a deeper transitive helper | 318 |

Most of those 318 were legitimate dedicated gates (`_ensure_document_access`,
`_load_authorized`, `_authorize_contract_scope`, `_load_report`). But at least two were
credited purely because the `PolicyService` *class source* landed in the blob — e.g.
`POST /api/contracts/{document_id}/clauses/index`. That route is in fact properly gated (it
loads the document, derives org/project from stored data, then calls
`authorize_processing_run`) — but the contract said so **for the wrong reason**, which
means the label could not be trusted for any route.

**(b) The 301 "wrong-tenant tests" did not test routes.**
`test_route_policy_contract_denies_wrong_tenant` builds a `PolicyService` from stub
services (`_WrongTenantScopeService` returns `False` unconditionally), calls
`policy.authorize(...)` directly, and asserts a 403. The route function is never invoked;
`contract` is used only for its permission list and its name as a string. It is one unit
test of `PolicyService`, parametrized 301 times. A route that forgot to call
`authorize` entirely would still "pass" its wrong-tenant contract test.

Real HTTP-level tenant-isolation coverage before this pass: **one route**
(`GET /api/projects1` in `test_http_isolation.py`) plus the arbitration suite.

### 1.2 What was built

**Static evidence — `scripts/authz_call_graph.py` (new).**
Replaces substring matching with resolved-call-expression analysis and a least fixed point
over the application call graph: `gated(f)` iff `f` contains a `Call` resolving to an
authorization sink, or to another gated function. Because membership is derived from
*calls*, defining, importing, or constructing a gate never makes a caller gated.

Two evidence strengths are recorded, not collapsed:

- `resolved` — the callee resolved to a known sink (strongest).
- `call_name` — a call to a gate-named method (`authorize`, `authorize_document`,
  `require_permission`, `require_step_up`) whose receiver did not resolve statically. This
  tier is necessary because helpers such as
  `arbitration_drafting._load_case_and_authorize(..., policy)` take **unannotated**
  parameters, so `policy.authorize_document` cannot be resolved by type. It is still a real
  call site — a docstring or an uninvoked class body never matches.

Tenant scoping (`build_scope_query`, `check_organization_access`,
`check_project_access`) is tracked separately from permission enforcement, since scoping
alone is not an authorization decision.

**Contract fields.** `route_control_manifest.json` gained `gate_evidence`, `gate_sink`,
`gate_via`, `gate_exception_reason` for all 579 routes.

**Narrow, written exceptions.** `INLINE_GUARD_ROUTES` in the inventory script holds the
five routes that enforce inline rather than via a reusable gate. Each entry quotes the
enforcing expression; each was individually read and verified:

| Route | Enforcement verified in source |
|---|---|
| `POST /api/notification-test/email` | 403 unless `target_user_id == current_user.id` or `_is_notification_admin` |
| `GET /api/rbac-monetization/plan-settings` | superadmin → global; otherwise filtered by `_subscription_scope_filters`, 403 when scope empty |
| `GET /api/search/analytics` | 403 unless `superadmin` in roles (analytics are not tenant-taggable) |
| `GET /api/search/suggestions` | tenant-scoped via `ScopeService.client_organization_ids/client_project_ids`; unknown role or empty scope → `[]` |
| `POST /api/v1/admin/vector/reconcile` | 403 unless `superadmin` in roles |

Three reusable platform-admin gates (`ai_assistant._require_platform_admin`,
`storage_sync._require_superadmin`, `_require_superadmin_user`) were registered as sinks —
they are unconditional deny-by-default role checks that raise 403, so calling one is
genuine evidence.

**Behavioural evidence — `backend/rbac_backend/tests/test_route_authz_gate_evidence.py`
(new, 6 tests).**

- `test_every_route_reaches_a_gate_or_carries_a_written_exception` — the CI gate. No route
  may be gate-less *and* unexplained.
- `test_inline_guard_exceptions_are_live_and_not_stale` — an exception naming a dead route,
  or a route that now reaches a real gate, fails the build. Prevents the allowlist rotting
  into a permanent bypass.
- `test_manifest_gate_evidence_matches_live_analysis` — adding, removing or weakening a
  route's authorization fails CI until the manifest is regenerated.
- `test_step_up_routes_have_step_up_call_evidence` — a route declaring `step_up_required`
  must actually call the step-up gate.
- `test_unauthenticated_requests_never_succeed` — drives **every** mounted route through
  the ASGI app with no credentials; none may answer 2xx. Path parameters are filled with a
  deliberately non-existent id and the database is replaced by an object that raises on
  attribute access, so an unauthenticated request that reaches storage is visible.
- `test_unauthenticated_sweep_mostly_returns_401_or_403` — pins that ≥95% of the
  non-public surface is rejected *by the authentication layer* (401/403) rather than by
  request validation (422), so the sweep cannot silently decay into proving nothing.

### 1.3 Result

| Measure | Before | After |
|---|---:|---:|
| Routes with a contract entry | 579 | 579 |
| Routes with call-path gate evidence | not measured | **545** (309 `resolved` + 236 `call_name`) |
| Routes with no gate evidence | — | 34 |
| …of which carry a written justification | — | **34** (5 inline-guard + 29 public/non-tenant) |
| **Unexplained routes** | unknown | **0** |
| Routes proven to reject unauthenticated access behaviourally | 1 | **579** |

Final manifest distribution (`route_control_manifest.json`, 579 routes, all `status:
complete`):

| Dimension | Values |
|---|---|
| `gate_evidence` | `resolved` 309, `call_name` 236, `none` 34 |
| `enforcement` | `central_policy` 510, `platform_admin_guard` 21, `legacy_permission` 8, `authenticated_non_tenant` 32, `public` 7, `provider_signature` 1 |
| `authentication_mode` | `session` 571, `public` 7, `provider_signature` 1 |
| `entitlement_requirement` | `required` 502, `not_commercial` 77 |
| `step_up_required` | 56 |
| mutating (`unsafe`) routes | 333 |

### 1.4 Defects the behavioural sweep found

**Three routes answered 200 with no credentials while the contract claimed they required a
session:** `GET /api/csrf-token`, `GET /api/profiles/health`, `GET /api/ai-assistant/health`.

All three were read: the health endpoints return static literals
(`{"status":"ok"}`, `{"status":"healthy","service":"ai-assistant","timestamp":...}`) with no
tenant or user data, and the CSRF endpoint must be reachable before login to issue the token
the login POST requires. So the endpoints are correct and the **contract was wrong**. They
were moved to `PUBLIC_OR_EXTERNAL_ROUTES` with written rationales, and their duplicate
`NON_TENANT_ROUTE_RATIONALES` entries removed. Nothing was made more permissive — the
contract now states what the system actually does.

**A step-up analyzer bug in my own first implementation.** Eleven routes declaring
`step_up_required` reported no step-up path. Root cause was mine, not the application's:
`_search` returned on the first resolved sink, so a route calling both
`Depends(require_permission(...))` and `await require_step_up(...)` (e.g.
`delete_project`, `projects.py:600`) was recorded as `step_up=False`. Step-up and scope are
now computed across the whole function body, and dependency-applied step-up is merged even
when the body already carries the permission gate.

---

## 2. M-09 — Shared effective-permission contract

### 2.1 Verified already in place

`scripts/generate_permission_contract.py` emits both
`backend/rbac_backend/contracts/permission_contract.json` and
`client/src/config/generatedPermissionContract.ts` from backend source
(`CANONICAL_PERMISSIONS`, `PERMISSION_COMPATIBILITY_ALIASES`,
`PERMISSION_CONTRACT_VERSION`, `required_feature_keys`).
`test_permission_contract_generation.py` fails CI if either checked-in artifact drifts from
regeneration. `client/src/config/rolePermissions.ts` now imports `ROUTE_ACCESS_RULES` and
`PERMISSION_ALIASES` from the generated module instead of maintaining its own.

### 2.2 Defect found: role-name normalization had three divergent sources

`ROLE_ALIASES` was defined independently in `core/security.py`,
`services/authorization_service.py` and `services/permission_service.py`. Measured drift:

| Module | Entries (before) |
|---|---:|
| `core.security` | 25 |
| `services.authorization_service` | 13 |
| `services.permission_service` | 34 |
| union | 40 |

**27 of the 40 keys were missing from at least one module.** Where a key existed in more
than one map the target always agreed, so the drift was purely *absence*:
`authorization_service` knew none of the `org-*`/`proj-*` short forms; only
`permission_service` knew the British `organisation*` spellings. The two normalizer
implementations also differed — `permission_service._normalize_role_name` lacked the
punctuation-stripping fallback the other two had.

Consequence: the same stored role string normalized differently depending on which module
ran first, so a legitimately assigned role could fail to resolve and be silently dropped
(`if not role: continue`) and denied. This is the mechanism behind the previously recorded
"orgadmin has `projects:read` but the account still gets 403" incident.

### 2.3 Fix

- `CANONICAL_ROLE_ALIASES` and `normalize_role_name` / `normalize_role_names` added to
  `backend/rbac_backend/core/permissions.py` — the contract module — as the union of the
  three maps (safe: no key had conflicting targets).
- All three modules now re-export the canonical map and delegate normalization. Their
  `ROLE_ALIASES` names are retained as documented aliases for backward compatibility.
- `backend/rbac_backend/tests/test_role_alias_single_source.py` (new, 12 tests) fails if any
  module reintroduces a private table or private rule, asserts all normalizers agree on
  every alias plus unknown/empty/`None` input, and asserts **unknown roles are never
  escalated** (`admin`, `root`, `owner`, `orgadminx`, `sa` must not become
  `superadmin`/`orgadmin`/…).

**Behaviour change, stated explicitly:** consolidation is not purely internal. A user whose
stored role is e.g. `organisation-admin` was previously dropped by
`authorization_service` and denied; that role now resolves to `orgadmin` in every module.
This restores an intended permission rather than granting a new one, and no user gains a
role they were not already assigned — but it *does* change authorization outcomes for
affected role spellings and should be reviewed before deployment.

---

## 3. M-11 — Subscription duplicates and uniqueness

### 3.1 Verified already in place

`migrations/v20260806_0001_subscription_current_scope_unique.py` implements deterministic
reconciliation: current subscriptions are grouped by
`current_subscription_scope_key`; the winner is chosen by status priority
(`active` 3 > `pilot` 2 > `trial` 1), then `updated_at`, `created_at`, `_id` — total and
reproducible. Losers are cancelled with `duplicate_of_subscription_id`,
`reconciliation_reason`, `reconciled_at/by`, and a `subscription_history` record preserving
the prior status. Current rows with no organisation scope fail closed (cancelled with
`missing_organization_scope`). Stale `current_scope_key` values on non-current rows are
unset. A partial unique index is then created:

```
name: subscription_one_current_per_scope
keys: [("current_scope_key", 1)]
unique: True
partialFilterExpression: {"current_scope_key": {"$type": "string"}}
```

Dry-run support is genuine — every mutation is behind `if not dry_run`.

### 3.2 Gaps found and closed

**No `downgrade`.** Every other migration in this repository defines one; this one did not,
and the brief explicitly requires rollback support. Added: drops the unique index and unsets
`current_scope_key` / `scope_key_backfilled_at` / `scope_key_backfilled_by`. It deliberately
does **not** resurrect cancelled duplicates — doing so would recreate exactly the conflicting
state the application now rejects, and the winner/loser choice cannot be safely un-made once
billing has observed it. This follows the repository's own precedent
(`v20260721_0001`: "rollback never restores unverifiable approvals"). Each cancellation
remains individually reversible by an operator via `duplicate_of_subscription_id` and the
`subscription_history` record, and the migration returns that as a warning.

**Test coverage was 2 tests.** Now 7 — added idempotency (re-running mutates nothing and
writes no new history), a post-apply dry run reporting zero work left, downgrade behaviour,
downgrade dry-run, and a scope-key collision test proving conflicting scopes derive an
identical key (which is what makes the partial unique index an actual invariant rather than
a decoration) while different org/project/package scopes do not.

### 3.3 Status — `PARTIAL`, with production `BLOCKED`

| Acceptance item | Status |
|---|---|
| Duplicate patterns and causes identified | Met |
| Deterministic, auditable reconciliation rules | Met |
| Idempotent migration with dry-run, verification, rollback | Met (rollback added this pass) |
| Reconcile test/staging data; prove no conflicting current subscription remains | **PARTIAL** — proven against the in-memory fixture only |
| Unique index created and concurrent writes tested against it | **PARTIAL** — the index *definition* and key-collision semantics are tested; no test exercises real MongoDB duplicate-key rejection under concurrency |
| Fail-closed for unresolved ambiguity | Met |
| Production reconciliation and index | **BLOCKED** |

**Why `PARTIAL` / `BLOCKED`:** no MongoDB was reachable in this environment
(`localhost:27017` refused connections throughout). The fake collection in the test suite
does not enforce uniqueness, so it cannot prove that Mongo rejects a concurrent conflicting
insert — only that the two writes would collide on the same key, which is the precondition.

**Exact next action:** run against a real instance —

```bash
python -m rbac_backend.migrations.runner --version 20260806_0001 --dry-run
```

review the reported `duplicate_scopes` / `invalid_current_rows` / `stale_scope_keys`
counts, take a backup of the `subscriptions` and `subscription_history` collections, then
re-run without `--dry-run` and confirm
`db.subscriptions.getIndexes()` contains `subscription_one_current_per_scope`. Production
execution additionally requires operational approval and execution authority, neither of
which was granted here. Nothing was run against production.

---

## 4. M-05 — Plan-unavailable and upgrade presentation

### 4.1 Verified already in place

`ProtectedRoute.tsx` implements the presentation contract in the correct precedence order,
and each state is distinct:

| Condition | Presentation |
|---|---|
| Not authenticated | inline `LoginPage` |
| Security terms outstanding / status unreadable | redirect to `/security-terms` (fails closed on error) |
| RBAC still loading | `RouteSkeleton` |
| RBAC error with no roles | inline `LoginPage` |
| **Permission denied** | `AccessDenied` ("Access unavailable") |
| Entitlement loading on a commercial route | `RouteSkeleton` |
| **Plan does not include the feature** | `PlanUnavailable` ("Not included in the current plan") |
| **Entitlement service failure** | `PlanUnavailable` ("Plan status unavailable") + retry |
| Non-commercial route, entitlement service down | renders normally (not blocked) |

`useEntitlements` fails closed (`hasFeature` returns `false` whenever `error` is set),
rejects a `contract_version` mismatch against `PERMISSION_CONTRACT_VERSION`, and refreshes
on `tenant-context-changed` / `auth-state-changed`.

Backend enforcement is independent of all of this: 502 of 579 routes carry
`entitlement_requirement: required`, so a direct URL or API call is checked server-side
regardless of what the UI renders.

### 4.2 Gap found and closed

The suite verified plan-unavailable, fail-closed, and non-commercial pass-through, but **not
M-05's headline acceptance item** — that document-view permission cannot expose or activate
unrelated commercial capabilities. The existing mock granted every permission
(`can: () => true`), so it could not express the case.

`ProtectedRoute.entitlements.test.tsx` extended from 3 to 9 tests with a permission-set-driven
mock:

- For each of `/claims`, `/contracts/appraisal`, `/arbitration/cases`, `/letters`: a user
  holding **only** `dms.document.view` is denied **even with the plan granting every
  feature** (`hasFeature` → `true`). This isolates the permission check — if the page
  rendered, permission, not plan, would be what failed. Each asserts `AccessDenied` is shown
  positively, and asserts the upgrade prompt is *not* shown, so a user is never told to buy
  something they already have.
- A "still works" case: the same user still reaches `/documents`.
- A separation case: correct permission + missing feature ⇒ `PlanUnavailable`, on the same
  route that produced `AccessDenied` above.

### 4.3 Residual gap

The module/route/navigation inventory is enforced through `ROUTE_ACCESS_RULES` (generated)
and the sidebar parity test. **Deep links and per-action controls inside pages** (buttons,
menu items) are not individually inventoried against the presentation contract; route-level
entry is. Backend authorization remains the enforcing layer for both.

---

## 5. Test, build and lint results

| Gate | Result |
|---|---|
| `test_route_authz_gate_evidence.py` (new) | **6 passed** (≈170 s — the unauthenticated sweep drives all 579 routes) |
| `test_role_alias_single_source.py` (new) | **12 passed** |
| `test_subscription_current_scope_migration.py` | **7 passed** (was 2) |
| `test_http_isolation.py` | **4 passed** (was 3 failing — see §6) |
| Contract/manifest/drift suites | **316 passed** (`test_permission_contract_generation`, `test_route_control_manifest`, `test_role_permission_catalog_drift`, `test_rbac_resolution_regressions`) |
| Backend regression set (route/manifest/inventory/policy/tenant/step-up) | **385 passed** |
| Full backend suite | **1562 passed, 7 skipped, 8 failed** (confirmed run, 451s) — all 8 pre-existing: 2 need a live MongoDB, 6 are the prior work's scope narrowing (§6) |
| Frontend unit/component | **321 passed, 3 skipped** after fixing 1 stale expectation |
| `ProtectedRoute.entitlements.test.tsx` | **9 passed** (was 3) |
| TypeScript `tsc --noEmit` | **clean, exit 0** |
| Production build (`npm run build`) | **passed** — client + SSR + 7 prerendered public pages |
| ESLint (`--max-warnings=0`) | **red — pre-existing, see §6** |

---

## 6. Pre-existing failures — proven not caused by this pass

> **Correction.** An earlier run in this session used `pytest -x`, which stops at the first
> failure and reported "837 passed, 1 failed". That understated the picture. The complete
> run was **1561 passed, 7 skipped, 9 failed**. Of those nine, one was an order-dependent
> flake in the drift test, now fixed (§6.2), leaving **8 failed / 1562 passed**. All eight
> are analysed below and none is caused by this pass.

### 6.1 Six scope tests broken by the prior uncommitted work (proven at clean HEAD)

`test_progressive_scope_narrowing.py` (2), `test_task_query_tenant_scope.py` (1),
`test_tenant_isolation.py` (2), `test_project_stats.py` (1).

**Proof.** A detached worktree at HEAD (`e5e11b6`) was created and the three tracked files
run there with the same interpreter: **48 passed**. `test_project_stats.py` does not exist at
HEAD — it is an untracked file added by the prior work — so its failure is prior-work by
definition.

**Mechanism.** `git diff HEAD -- backend/rbac_backend/core/security.py` shows the prior work
*added* this block to `build_scope_query`:

```python
if organization_id is None and roles & {"superadmin", "superuser"}:
    selected_organization_id = getattr(current_user, "organization_id", None)
    if selected_organization_id:
        organization_id = str(selected_organization_id)
```

A superadmin/superuser who has a selected organisation is now narrowed to it, so
`build_scope_query(user) == {}` yields `{'organization_id': 'org-A'}`, and
`test_superuser_is_scoped_not_denied` sees `{'org-A'}` where it expects
`{'org-A','org-B'}`. `test_project_stats` fails with `403 scope_denied` from the same
`is_client_scope_allowed` collection-level rule described in F-1.

**My role-alias consolidation is not implicated.** `build_scope_query` never calls
`ROLE_ALIASES` or `_normalize_roles_list` — it lowercases roles inline
(`roles = set([str(r).lower() for r in current_user.roles or []])`). Independently, the role
strings these tests use (`superadmin`, `superuser`, `projectuser`) normalize identically
under the old and new maps, so the normalizer output is byte-identical either way.

**This needs your decision — see F-5.**

### 6.2 A pre-existing order-dependent flake in the drift test (fixed)

`test_checked_in_route_control_manifest_matches_live_application` passed in isolation but
failed in the full run. A probe comparing the checked-in manifest against a freshly built
one, after the polluting suites had imported, isolated the difference to **two fields on one
route**:

```
/api/contact [submit_contact_request] scope_sources:        checked=['organization_id','project_id'] live=[]
/api/contact [submit_contact_request] target_load_evidence: checked=True                             live=False
```

Both come from the **pre-existing** `_route_source` blob logic, not from the new analyzer —
`gate_evidence` was byte-identical under both orderings, so the call-graph analysis is
order-stable. Mechanism: the blob for `/api/contact` traverses into `EmailService` (the route
takes `email_service: EmailService = Depends(...)`), and a suite in
`test_letter_drafting_* / test_metadata_*` patches that service, shrinking the traversal.

**Fix.** For a public / provider-signature route both fields are meaningless — such a route
has no tenant target, `wrong_tenant_test` is already `not_applicable:public_or_external`,
and `target_load_required` is already `False`, so neither value is consumed. They are now
pinned to `()` / `False` for public routes, which is semantically correct and makes the
manifest deterministic.

Separately, an order-dependence I *had* introduced was also fixed: sink identity is now
derived from **source text** (declared receiver class name + called attribute) rather than
from live objects via `getattr`/module globals, because suites monkeypatch both
`PolicyService.authorize` and the `PolicyService` name inside router namespaces. A static
analyzer must not change its answer because a test rebound a name.

### 6.3 Two backend tests require a live MongoDB.
`test_org_admin_permissions_api.py::test_org_admin_retrieve_returns_full_client_dms_permission_set`
and `::test_org_admin_retrieve_serializes_catalog_missing_permissions` fail with
`localhost:27017 ... WinError 10061 No connection could be made`. Environmental. CI provides
a `mongo:8.0` service, so these pass there.

### 6.4 ESLint

**ESLint `--max-warnings=0` was already red before this session.** 11 warnings, 0 errors,
across 5 files. None is a file this pass touched. Decisive evidence:
`client/src/pages/PermissionsPage.tsx` is **unchanged since HEAD** (`git diff --quiet HEAD`)
and still emits warnings, so the gate was failing at HEAD. The other four
(`ContractViewerPage`, `ContractsSearchPage`, `EmailGroupsPage`, `ReportsAnalyticsPage`)
were modified by the prior uncommitted work, not by this pass.

### 6.5 test_http_isolation.py

**`test_http_isolation.py` was failing on arrival** (3 of 4 tests), caused by the prior
uncommitted work, not by this pass — which touches no runtime code on that path. Three
distinct causes, fixed in order:

1. `EntitlementService._collection_has_subscriptions` reached `db.subscriptions`, absent
   from the fixture. Entitlement is now neutralized explicitly in the fixture — mirroring
   how the file already neutralizes the permission gate — so the suite fails only for scope
   bugs. Entitlement has its own suites.
2. `ScopeService` reads `db.organization_memberships` / `db.project_memberships`. Added as
   empty collections so the test users are scoped solely by their own fields, which is what
   the suite varies. `_FakeCursor.to_list` also had to accept `length` as a keyword.
3. Two assertions encoded a contract the system no longer implements — see below.

---

## 7. Findings requiring a product decision

**F-1 — Project-scoped users cannot perform any collection-level read (medium).**
`ScopeService.is_client_scope_allowed` (`scope_service.py:103`) returns `False` for
`projectadmin`/`projectuser` whenever `project_id` is absent. `GET /api/projects1`
authorizes at collection level with `project_id=None`, so a project-scoped user receives 403
and never reaches the `build_scope_query` narrowing directly below, which would have
correctly restricted the list to their own projects.

This is deliberate progressive scope narrowing and it is fail-closed, so **I did not change
it** — weakening it is exactly what the brief prohibits. But the rule is shared, so it
applies to *every* route that authorizes with `project_id=None`, and the user-visible effect
is that project-scoped users cannot list projects at all. The two stale tests were rewritten
to pin the current stricter contract (403 + `scope_denied`, and orphan users denied rather
than receiving an empty 200 — an empty list is indistinguishable from "this tenant has no
projects"). Whether the collection-level denial is the intended product behaviour is a
decision for you, not a defect I should silently resolve.

**F-5 — Six security tests are red in the working tree because prior uncommitted work
changed scope semantics without updating them (high — blocks a clean CI run).**
Evidence in §6.1: they pass at HEAD and fail in the tree, and the diff shows the added
narrowing block. Two readings are possible and they lead to opposite fixes:

- *The narrowing is intended.* Then all six tests encode a superseded contract and should be
  re-pinned to the stricter behaviour, exactly as the two `test_http_isolation.py`
  assertions were in §6.4.
- *The narrowing is too broad.* Then `build_scope_query` and `is_client_scope_allowed` need
  correcting — note `test_superuser_is_scoped_not_denied` is named for the proposition that
  a superuser should be **scoped, not denied**, and it now sees only the selected
  organisation rather than all assigned ones.

I did not rewrite these six, because rewriting a security test to match new behaviour is
only correct once someone confirms the new behaviour is the intended contract. I re-pinned
the two `test_http_isolation.py` assertions because their new behaviour is unambiguously
stricter and fail-closed (403 instead of a misleading empty 200); the superuser cases are
not — narrowing a superuser to one organisation *reduces* what an operator can see and could
be an operational regression rather than a hardening.

**F-2 — `gate_evidence: call_name` covers 236 routes (low, tracked).**
These are proven call sites but the receiver could not be resolved statically, almost always
because a helper takes an unannotated parameter (e.g.
`_load_case_and_authorize(case_id, permission, db, current_user, policy)`). Adding type
annotations to those helper signatures would promote them to `resolved` and tighten the
evidence. Mechanical, low-risk, not done here to keep this diff reviewable.

**F-3 — Case-sensitive superadmin checks in inline guards (low).**
`retrieval_engine.py:262` and `search.py:394` test `"superadmin" not in (current_user.roles or [])`
without normalizing case. This fails *closed* (an unnormalized role is denied), so it is not
a vulnerability, but it is inconsistent with `normalize_role_name` now that §2 provides one.

**F-4 — The 301 `policy_contract:` wrong-tenant parametrizations remain shallow.**
They are retained because they do assert `PolicyService` denies and audits a wrong-tenant
decision, but they are not per-route evidence. The new gate-evidence suite plus the
all-route unauthenticated sweep are what actually cover the routes. Genuine per-route
wrong-tenant HTTP tests (two seeded tenants, real cross-tenant resource ids) exist only for
`/api/projects1` and the arbitration surface. Extending that pattern is the highest-value
remaining work on H-04.

---

## 8. Completion status against the brief

| Criterion | Status |
|---|---|
| Every live route has an explicit semantic contract | **Met** — 579/579, all `status: complete` |
| No unexplained unsafe classification remains | **Met** — 0 routes lack both gate evidence and a written justification |
| Applicable negative tests pass | **PARTIAL** — unauthenticated proven for all 579; insufficient-permission and wrong-tenant proven per-route only for `/api/projects1` + arbitration (F-4) |
| Subscription duplicates zero in every migrated environment; verified unique index present | **BLOCKED for production**, **PARTIAL** elsewhere — no reachable MongoDB (§3.3) |
| Every commercial module follows the plan/permission matrix | **Met at route level**; per-action deep links not individually inventoried (§4.3) |
| Backend and frontend permissions generated from one contract, CI prevents drift | **Met** — and role aliases consolidated from 3 sources to 1 this pass |
| All relevant tests, builds, migrations pass without skipped security-critical cases | **PARTIAL** — see §5/§6; 2 environmental DB failures, ESLint gate pre-existing red |
| Production-only actions marked | **Met** — §3.3 |

**Not done, by instruction:** nothing was committed, pushed, or deployed, and no production
record was read, reconciled, or altered.

---

## 9. Files changed

**New**
- `scripts/authz_call_graph.py` — AST call-graph authorization analyzer
- `scripts/authz_gate_report.py` — CLI report of routes lacking a gate call path
- `backend/rbac_backend/tests/test_route_authz_gate_evidence.py` — static + behavioural gate suite
- `backend/rbac_backend/tests/test_role_alias_single_source.py` — role-normalization single-source guard
- `docs/architecture/RBAC_SUBSCRIPTION_REMEDIATION_2026-08-07.md` — this report

**Modified**
- `scripts/rbac_phase0_route_inventory.py` — gate-evidence fields, `INLINE_GUARD_ROUTES`, three health/CSRF routes reclassified public, duplicate non-tenant entries removed
- `backend/rbac_backend/route_control_manifest.json` — regenerated with gate evidence
- `backend/rbac_backend/core/permissions.py` — `CANONICAL_ROLE_ALIASES`, `normalize_role_name(s)`
- `backend/rbac_backend/core/security.py`, `services/authorization_service.py`,
  `services/permission_service.py` — delegate to the canonical map
- `backend/rbac_backend/migrations/v20260806_0001_subscription_current_scope_unique.py` — `downgrade`
- `backend/rbac_backend/tests/test_subscription_current_scope_migration.py` — 5 new tests, fixture extended
- `backend/rbac_backend/tests/test_http_isolation.py` — fixture repaired, 2 assertions re-pinned to the current contract
- `client/src/components/auth/__tests__/ProtectedRoute.entitlements.test.tsx` — 6 new tests
- `client/src/config/__tests__/rolePermissions.sidebar.test.ts` — stale `/letters` feature expectation corrected
