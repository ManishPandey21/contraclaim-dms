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
