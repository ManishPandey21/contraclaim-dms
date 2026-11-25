# M1: Scoping built-in roles to their assignment (architecture and remediation ticket)

**Status:** this is the audit, the P-05 reading and the tests. Nothing is remediated yet. It is a separate workstream (owner decision, 2026-11-20).

**Priority:** High.

**Restrictions:**
- No production role, user or membership is changed.
- No broad RBAC migration happens before all three of:
  - P-05 has been read;
  - an authorised security review;
  - a separate owner approval.
- DEF-4 production activation stays blocked until this boundary is shown to be safe.

## 1. The defect

A user's built-in roles are stored as bare keys on the user document, for example `roles: ["orgadmin", "projectuser"]`. They are not bound to an organisation or a project.

`services/role_grants.py` gives a built-in grant no scope. It applies wherever the user's membership reaches. A user reaches an organisation through any of these:

- `organization_id`;
- the `organizations` list;
- an active `organization_memberships` row.

A user reaches a project through either of these:

- the `projects` list;
- an active `project_memberships` row.

Custom roles have been bounded to their own organisation or project since SEC-1. Built-in roles are not.

The checks that read role names inherit this. Each one asks "does the user hold `orgadmin`?" and never "in which organisation?". Examples:

- `ScopeService.has_organization_wide_scope`;
- `is_client_scope_allowed`;
- `build_scope_query`;
- the role-name rules in `PermissionService._decide` (`users:create`, `users:read`);
- `AuthorizationService` tiers;
- the subscription role sets in `routers/rbac_monetization.py`;
- `PolicyService._may_answer_at_organisation_level`.

## 2. Evidence (real MongoDB, `tests/integration/test_m1_cross_org_role_combinations_mongo.py`)

| Id | User | Reaches today | Should reach |
|---|---|---|---|
| M1-E1 | Org Admin of A, also listed in `organizations: [A, B]` | B's billing records, B's claims, B's full service state | A only |
| M1-E2 | Org Admin of A, with an active `organization_memberships` row for B | The same as M1-E1 | A only |
| M1-E3 | Project Admin of A1, and Org User whose primary organisation is B (member of A and B) | A's sibling project A2: claims and the full service state. Decision 4's project-tier cut does not apply, because `orguser` counts in A. A's billing records. | A1 as Project Admin; B as Org User |
| M1-E4 | Billing admin of A, listed in B | B's billing records and service state | A only |
| M1-E5 | Org User of A, a member of B only for a narrow custom role of B | B's billing records, through the global `orguser` key's legacy `organizations:read` | A as Org User; B for documents only |

How to read these rows:

- Each exposure is pinned by a test that asserts today's result, so a change is never silent.
- The remediation flips each test to a refusal.
- Pinning a result is not an acceptance of it.

These boundaries hold today, and each has a negative test that must stay green:

- single-organisation Org Admin, billing admin and Project Admin;
- a custom role of another organisation, held by a non-member;
- the user-read rule (`GET /api/users/{id}`), which is judged in the primary organisation;
- the permission cache, which never carries a grant from one organisation to another.

**Found while writing these tests, and fixed (`8517f27`):** with `BILLING_EXPLICIT_PERMISSIONS` off, a billing admin with no tie to B received every organisation's service state from `/plan-settings/effective-services`. That is a single-organisation exposure, not M1.

## 3. Audit: where a built-in role name is evaluated without an assignment scope

Counted by `grep` at `5ccd918`, excluding tests:

| File | Sites | What it decides |
|---|---:|---|
| `services/authorization_service.py` | 22 | User read, update and delete, and the user list (`check_user_access`, `build_user_query`), by tier |
| `routers/rbac_monetization.py` | 14 | Subscription and checkout role sets; plan-settings and effective-services views |
| `services/role_service.py` | 9 | Role creation and assignment fences (SEC-2 and SEC-3) |
| `services/scope_service.py` | 5 | `has_organization_wide_scope`, `is_client_scope_allowed`, superadmin |
| `services/letter_template_service.py` | 4 | Template administration |
| `core/security.py` | 3 | `build_scope_query`, row visibility |
| `services/smtp_settings_service.py`, `services/policy_service.py`, `routers/search.py`, `routers/roles.py` | 2 each | Settings administration, the project-tier rule, search scope, role routes |
| `services/reviewer_assignments.py`, `services/project_service.py`, `services/project_perspective.py`, `services/arbitration_drafting/approval_policy.py`, `routers/users.py`, `routers/tags.py`, `routers/deep_planning.py`, `models/user.py`, `core/permissions.py` | 1 each | Various tier checks |

**Also in scope:**

- **Role key aliases.** `CANONICAL_ROLE_ALIASES` and `normalize_role_name` turn a label into a built-in key, so an alias inherits the global reach.
- **Permission caching.** The cache keys each role's names together with their scope. A built-in grant is cached with scope `None` ("everywhere the user is"), so a scoped re-grant must invalidate it (`complete_grant_change`).
- **Membership records.** The reach comes from three user fields and two membership collections. The remediation must define which of them is authoritative.

## 4. Proposed architecture (for approval, not implemented)

1. **Role assignments, stored per scope.** Each assignment is stored as `{role_key, organization_id, project_id, granted_by, granted_at, status}`, in a `role_assignments` collection or as structured `roles` entries. Built-in roles are then evaluated like custom roles are today: per assignment, through `grant_applies`.
2. **One resolver.** `ScopeService` takes over the job of answering:
   - "organisation-wide in X?" becomes: holds an org-wide role assigned in X;
   - "project-tier in P?" becomes: holds a project-tier role assigned on P.

   Every site in §3 calls that resolver. No site reads `user.roles` directly. A static check enforces this, as `forbid-legacy-authz` does today.
3. **Platform roles.**
   - `superadmin` and `superuser` keep their platform meaning.
   - A tenant role with no organisation grants nothing, which is the same rule as the custom platform role.
   - The billing admin needs an explicit decision: is it a tenant role (assigned per organisation) or a platform role? M4 treats it as a tenant role.
4. **Compatibility window, behind a flag** (`BUILT_IN_ROLE_ASSIGNMENTS=off|shadow|on`).
   - **`shadow`:** evaluates both models and logs every disagreement, without enforcing the new one.
   - **`on`:** enforces the assignments.
   - **Bare keys:** while the flag is not `on`, they keep today's meaning.

## 5. Migration plan (each step needs its own approval)

1. **Read P-05 `built_in_role_scope_impact`** (read-only, already added):
   - affected users;
   - counts per organisation;
   - the proposed scoped assignments;
   - the assignments that need a decision.
2. **Owner decision for every assignment that needs one:** grant the role in the secondary organisation, or drop that reach. Nothing is inferred silently.
3. **Grant before revoke.** Write the assignments: one per built-in role in the primary organisation, one per project for project-tier roles, plus the approved secondary ones. Keep the bare keys, and invalidate the caches.
4. **`shadow` on staging, then in production.** Disagreements must be zero, or every remaining one explained.
5. **`on` on staging.** Two consecutive deployed E2E passes are required. After that comes a security review and a release decision.
6. **Remove the bare-key meaning.** This is a separate release.

**Rollback:**
- steps 3 and 4: delete the assignments and set the flag to `off`;
- step 5: set the flag back to `shadow`.

## 6. P-05 reading added (`scripts/claim_arbitration_readonly_readings.py`, `built_in_role_scope_impact`)

**Counts read**, for active users who are not platform users:

- users with built-in roles;
- users reaching more than one organisation;
- an org-wide role reaching a secondary organisation;
- org-wide and project-tier roles mixed;
- built-in and custom roles mixed;
- a billing admin reaching more than one organisation;
- projects outside the primary organisation;
- users with no primary organisation;
- unrecognised role keys.

**Outputs:**

- affected users, and affected users per organisation (organisation ids only, never names or emails);
- `proposed_scoped_assignments`, by `role@organisation` and `role@project`;
- `assignments_needing_a_decision`.

**Approximations:**

- role keys are compared lowercased, with no alias resolution; unrecognised keys are counted;
- a project with no readable organisation counts as outside the primary organisation.

**Checks:**

- The built-in keys are inlined so the reading runs on old images. A parity test keeps them equal to `role_grants.BUILT_IN_ROLE_KEYS`.
- **No writes:** guaranteed by the `ReadOnlyCollection`/`ReadOnlyDatabase` allow-list the reading runs through (reads only; `aggregate` without `$out`/`$merge`). The real-MongoDB test also checks that the document counts are unchanged and that no names leak.

**Status: not read in production** (pending AQ-11 access).

## 7. Gates before any M1 change reaches production

1. P-05 is read and reviewed, including this reading.
2. An owner decision for every assignment counted as `assignments_needing_a_decision`.
3. An authorised security review of the design and the code.
4. The M1-E1 to M1-E5 tests are flipped to negative tests and pass, along with the existing negative tests.
5. Two consecutive deployed staging E2E passes with the flag `on`.
6. A separate production release decision.

DEF-4 activation depends on steps 1 to 4: its guarantees hold for single-role users only until then.

## 8. Shadow evaluator (workstream 5, `e661b92`, owner instructions 2026-11-21)

**Status:** implemented, shadow only, default off. No enforcement, no role migration, no re-grant. `BUILT_IN_ROLE_ASSIGNMENTS=on` is refused at startup.

**Flag:** `BUILT_IN_ROLE_ASSIGNMENTS` = `off` (default; the evaluator is never called) | `shadow`.

**What shadow does.** After every decision `PolicyService.authorize` *allowed* (Super Admin excluded), `services/role_scope_shadow.py` evaluates the same request under the §4 model and counts the comparison. It runs after the decision; errors are swallowed in the module and again in the hook, so a response never changes.

**Proposed model evaluated** (no data is written to represent it):

- an organisation-tier built-in role (`orgadmin`, `orguser`, the billing administrator, every other tenant built-in key) is assigned in the user's primary organisation;
- a project-tier role (`projectadmin`, `projectuser`) is assigned on each project the user is on (`projects` or an active `project_memberships` row) *inside* the primary organisation;
- explicit rows in `role_assignments` (`user_id`, `role_key`, `organization_id`, `project_id`, `status: active`) add assignments - none exist; they are how an owner decision to keep a secondary reach would be represented;
- `superadmin` / `superuser` keep their platform meaning; custom roles keep their SEC-1 scope.

Proposed decision = the existing decision allowed it **and** some held role grants the permission (by name or one-hop equivalent) and is assigned where the request is. It is narrower by construction, so the only divergence is `would_deny`.

**Recorded (privacy-safe):** daily counters in `rbac_shadow_observations` keyed by day, permission, outcome (`agree` / `would_deny`), the sorted built-in role keys held, and the request's relation to the user (`primary_organisation`, `secondary_organisation`, `project_in_primary_organisation`, `project_outside_primary_organisation`, `unscoped`). No user, organisation or project id, name or email. P-05 `built_in_role_shadow_divergence` summarises them read-only.

**Coverage, stated.** Only `PolicyService.authorize` allow decisions are compared. The other role-name sites of §3 (`build_scope_query` row visibility, `AuthorizationService` tiers, the subscription role sets, role-management fences and others) are **not** shadowed. A zero divergence count therefore covers the policy gate only; the remaining sites need their own shadow or a static migration to the single resolver (§4 step 2) before `on` can be considered.

**Tests (real HTTP, real MongoDB):** `test_m1_shadow_evaluator_mongo.py` (8)

| Case | Result |
|---|---|
| Responses with `off` and `shadow` for six personas × four requests | identical; no user, role or assignment written |
| Dual-organisation users (`organizations` list, membership row) - M1-E1/E2 | `would_deny` orgadmin @ secondary organisation |
| Billing admin of A in B - M1-E4 | `would_deny` |
| Project Admin of A1 + Org User of B - M1-E3 | `would_deny` orguser+projectadmin @ project outside primary |
| Org User of A with a custom role of B - M1-E5 | `would_deny` orguser @ secondary |
| Explicit `role_assignments` row (legacy reach kept) | `agree` |
| Project membership rows inside / outside the primary organisation | `agree` / `would_deny` |
| Disabled account | refused before any evaluation; nothing recorded |
| Role change + `complete_grant_change` (cache invalidation) | evaluated against the new roles; no stale divergence |
| Identifiers in observations; evaluator failure | none; failure changes no response and records nothing |
| P-05 reading | read-only; counts only |

Mutants 9/10 killed singly; removing the module's error swallow alone is equivalent behind the hook's own guard, and removing both is killed.

**Next steps (each needs approval):** run `shadow` on staging, read P-05 `built_in_role_shadow_divergence` and `built_in_role_scope_impact` together, extend shadowing to the §3 sites (or migrate them to the single resolver first), owner decision per `assignments_needing_a_decision`, then the §5 plan.

## 9. Enforcement mode `on` (remediation, 2026-11-25)

### Authority and status

- **Authority.** The security owner's decision S-4 (private review record, 2026-11-25) rejected M1 for production release and approved its remediation for implementation and verification. The owner then directed the fix to start the same day.
- **Status.**
  - Implemented behind `BUILT_IN_ROLE_ASSIGNMENTS=on`. The default stays `off`, and activation needs a separate owner decision.
  - No role, user, membership or assignment is written.
  - Nothing is activated or migrated.

### The rule under `on` (`services/built_in_role_scope.py`)

- **Where a tenant built-in role counts.** A tenant built-in role (`orgadmin`, `orguser`, `projectadmin`, `projectuser`, the billing administrator, the reviewer keys and every other tenant built-in key) counts in the user's **primary organisation** (`users.organization_id`) and nowhere else, except where an explicit `role_assignments` row puts it.
  - An organisation row puts the role at organisation level in that organisation.
  - A project row puts a project-tier key (`projectadmin`, `projectuser`) on that project only. A project row never makes an organisation-tier key count anywhere.
- **Platform roles.** `superadmin` and `superuser` keep their platform meaning.
- **Custom roles.** Unchanged (SEC-1).
- **No primary organisation.** A tenant built-in key held by a user with no primary organisation counts nowhere.
- **Inside the primary organisation.** The meaning is unchanged, which the test "primary organisation keeps its meaning" compares against `off`. M1 is about reach into *other* organisations, and that is what `on` removes.

### Sites changed (each behaves exactly as before unless the flag is `on`)

| Site | Change under `on` |
|---|---|
| `PermissionService.scoped_grants` | A built-in role's permissions become grants scoped to its assignments, instead of unscoped. Its role name counts only if it is assigned somewhere. The permission cache records the mode, so a grant is never served across a mode change |
| `PermissionService.holds_organisation_level_custom_role`, `core.security.permission_scope_query` | A built-in assignment scope is never mistaken for a custom organisation role |
| `ScopeService.has_organization_wide_scope`, `is_client_scope_allowed` | The organisation-wide role must be counted in that organisation (`ScopeService.role_names_in`) |
| `PolicyService.has_organisation_level_standing` (billing standing, decision 4) | The organisation-level role must be assigned there |
| `rbac_monetization`: checkout scope with `BILLING_EXPLICIT_PERMISSIONS` off; effective-services widening for the billing administrator | The role must be assigned in the target organisation |
| `AuthorizationService._collect_user_org_ids` | For a holder of a tenant built-in role, the tier checks reason over the primary organisation only. Explicit assignment rows are not honoured here, which fails closed. A user with custom roles only is unchanged |
| `RoleService.can_view_role` | Org Admin and Project Admin see roles only of organisations where they are assigned |
| `routers/search.py` (search and suggestions) | Organisation-wide search only where an organisation-wide role is assigned |
| `ProjectPerspectiveService._is_organisation_admin` | `orgadmin` must be assigned in that organisation |
| `reviewer_assignments.arbitration_reviewer_roles` | A bare reviewer key on the role list answers a case's gates only in the primary organisation, or through an explicit row. Live reviewer assignments are unchanged |

`build_scope_query` and `authorize_scope` already bound the organisation and project tiers to the primary organisation, and are unchanged.

### Tests (real HTTP, real MongoDB)

`tests/integration/test_m1_enforcement_mongo.py` has 11 tests.

| Case | Result under `on` |
|---|---|
| M1-E1, E2, E4: B's billing records and review queue; B's claims; B's service state | 403, 403, operational state only |
| M1-E3: sibling project A2 claims; A's billing; A2's state | 403; 403; operational only |
| M1-E5: B's billing records; B's claims | 403; 403 |
| Primary organisation: Org Admin (single and dual), billing administrator, Project Admin | Every answer and every effective state identical to `off` |
| Explicit assignment rows | Organisation row: B reachable. Project row: that project only. Organisation-tier key on a project row: nothing. Revoked row: nothing |
| No primary organisation | Nothing granted |
| Super Admin | Unchanged |
| Scope service | B membership kept; B organisation-wide refused |
| Permission cache | `off` → `on` → `off` answers change with the mode; nothing stale |
| AuthorizationService, role visibility, search, reviewer keys | Bounded to the assignment |
| Writes | None to users, roles, memberships or assignments |

The pinned exposures in `test_m1_cross_org_role_combinations_mongo.py` keep asserting today's behaviour with the flag `off`, which is the default.

### Residual and stated limits

- **Not yet a release condition met.** Every §5 step is still open: P-05, an owner decision for each `assignments_needing_a_decision`, assignments written grant-before-revoke, `shadow` on staging, `on` on staging with two deployed passes, a security review of this code, and a release decision. Until `on` is activated, M1 remains as it is today in production.
- **Shadow is now incomplete.** The shadow evaluator (§8) compares the policy gate only. It does not shadow the other sites listed above, so a zero shadow count does not prove `on` is safe for those sites.
- **Letter templates.** `LetterTemplateService._resolve_scope` takes the organisation from the payload for an Org Admin, so for creation it is not bounded by membership or assignment. This was found while auditing and has not been confirmed through the route. It is recorded for the security owner, not changed under the scope freeze.

### Verification and audit (owner instructions 2026-11-25, second round)

**Letter templates: confirmed and fixed.** This finding is outside M1. It was pre-existing, needed no membership in B, and is a proven cross-tenant write.

- **Reproduced through the real API at `e44fd79`.**
  - An Org Admin of A, who is not a member of B, created an organisation template in B (**200**).
  - A Project Admin created a template on its own project labelled with organisation B (**200**).
  - Read, update, delete and list of B's templates were already refused.
- **Fix.** `LetterTemplateService._authorize_new_template_scope` now checks two things before a template is stored:
  - a project template's project must belong to the template's organisation, for everyone including Super Admin;
  - the write rule of `check_letter_template_access` must pass for the new template's scope.
- **Tests.** `tests/integration/test_letter_template_tenant_isolation_mongo.py` (2 tests) covers:
  - create in B, refused 403, with nothing stored;
  - read, update and delete of B's templates, refused 403;
  - list, which shows no B template;
  - a list filter naming B, refused 403;
  - a Super Admin control, which reads them (200).
- **Scope of the router.** The template router has no duplicate, share or publish routes. No other router creates templates.

**Rate-limit 429 in `test_m4_global_pricing_mongo`: root cause.**

- **Cause.** With no Redis configured, `RateLimiter` keeps its counters in a class-level dictionary shared by the whole process. The test environment seeds users with fixed ids. In CI's single-process real-MongoDB step, earlier modules' role mutations as `org_admin` therefore count against the role-mutation window (20 per 60 s). A later test then gets 429 instead of the 403 it asserts. It depends on order and timing.
- **Reproduced locally.** `test_security_remediation_mongo.py` followed by `test_m4_global_pricing_mongo.py` gave the 429.
- **Not a security fault.** The request is still refused, but the assertion goes unverified.
- **Fix, in the test environment only.** `_env` clears the in-process counters when each fresh environment starts. The same pair now passes. Production limits are unchanged.

**Audit items.**

| Item | Finding |
|---|---|
| Direct role-name checks that bypass the resolver | **Changed under `on`:** the §9 table plus templates. **Already bounded to the primary organisation:** `build_scope_query`, `authorize_scope`, `smtp_settings_service`, `deep_planning`, user role assignment (`routers/users.py`), `check_user_access`. **Gated by an assignment-scoped call:** tag creation (`check_organization_access`). **Arbitration gates:** every caller of `resolve_gate_role` and `enforce_gate_role` passes `arbitration_reviewer_roles` (scoped). The bare role list is used only when no roles are passed, and no caller does that |
| WebSocket | No role or organisation decision; authentication only |
| Search, AI retrieval | Search is scoped under `on`. Retrieval uses `has_organization_wide_scope` (scoped) |
| Exports and downloads | Go through `PolicyService.authorize`, whose permission grants are scoped |
| Who can write `role_assignments` | **Nothing in the application.** It has no route or service writer; only an index is created. Rows can come only from an approved migration |
| Primary organisation manipulation | Only a Super Admin can change a user's `organization_id` or `organizations` (`_enforce_update_stays_in_scope`). Such a change is an authority change: the cache is invalidated before the write. There is no self-service route that edits them |
| Revocation and caches | The only authorisation cache is the Redis permission cache, with a TTL of 3,600 s. Each entry records the M1 mode and is ignored under another mode. Role, membership and organisation changes invalidate it (`begin_authority_change`). **Requirement:** a migration that writes or revokes `role_assignments` rows must call `begin_authority_change` and `complete_grant_change` for each user. Otherwise a cached grant can outlive a revoked row by up to the TTL. There is no in-process authorisation cache |
| Flag transitions across workers | Each worker reads its own setting. A cache entry made under one mode is never served under another, so during a rolling change each worker answers by its own mode with no stale crossover. The two modes can differ between workers until all have restarted. Plan activation as a full restart, not a rolling one |
| Fail-closed | A read error on `role_assignments` propagates. The permission check turns errors into a refusal, and the scope checks into a request failure, never a grant. An unknown flag value is refused at start-up. The flag defaults to `off` (legacy) only when unset |

## 10. Assignment migration, staging validation and rollback (plan; nothing run)

**Inputs, each required before any write:**

1. **The P-05 reading** `built_in_role_scope_impact`, read-only, on production. Its outputs: `affected_users`, `proposed_scoped_assignments` and `assignments_needing_a_decision`.
2. **An owner decision for every `assignments_needing_a_decision` row**, keyed by user, role and organisation or project:
   - **keep:** write an explicit row;
   - **drop:** no row, so the reach ends when `on` is activated.
3. **Approval of this plan** by the security owner.

**Migration: grant before revoke, idempotent, dry run first.**

- **Writes.** For each kept secondary reach, the migration writes one `role_assignments` row: `{user_id, role_key, organization_id | project_id, status: "active", granted_by: "<migration id>", granted_at, decision_ref}`.
- **Never touched.** Bare role keys stay on the user, and no user document changes. The primary organisation needs no row.
- **Before each write:**
  - check the row is new, by a unique key on user, role, organisation and project;
  - check the user is still a member there;
  - check the key is a tenant built-in;
  - check a project row carries a project-tier key only.
- **Per user.** Call `begin_authority_change` and `complete_grant_change` around the write, so no cached grant outlives it.
- **Dry run.** Prints counts only: rows that would be written, rows already present, rows refused and why.
- **Apply.** Runs only with separate approval, on staging first.

**Staging validation (flag order: `off` → `shadow` → `on`):**

1. Restore a copy of production into staging. Read P-05 there and confirm it matches production.
2. Write the approved assignments on staging (dry run, then apply).
3. **`shadow`:** run typical traffic, plus the staging E2E journey. Read `built_in_role_shadow_divergence`. Every remaining `would_deny` must be an intended drop. The shadow covers the policy gate only, so also exercise the §9 sites, using `test_m1_enforcement_mongo.py` against the staging stack's personas.
4. **`on`,** applied by a **full restart** of every backend and worker, not a rolling one. Then two consecutive deployed E2E passes, with no skipped or retried tests.
5. Hand over to the security owner for review of the code and the staging evidence. Then the release decision.

**Rollback:**

| Step | Rollback |
|---|---|
| `on` | Set the flag to `shadow` or `off` and restart. Cache entries are mode-bound, so no `on` grant is served afterwards |
| Assignments | Rows are additive and are ignored with the flag `off`. To remove them, set `status: "revoked"` per row and call the authority-change hooks per user. Nothing else is touched |
| Bare keys | Unchanged, so there is no rollback to do. Removing the bare-key meaning is §5 step 6, a separate release |
