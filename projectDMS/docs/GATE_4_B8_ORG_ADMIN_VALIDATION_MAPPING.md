# Gate 4 bullet 8 — literal requirement mapping

**Bullet:** "Production Org-Admin permissions are manually validated in staging/prod."

**Status: NOT EARNED — UNTICKED.** R-A8W Stage B measured it against staging on
2026-09-13 and it failed on F-A8W-B3 (a foreign organisation read by id answered
500). R-A8X fixed that defect offline; an offline fix earns nothing, so the bullet
stays unticked until a real staging execution of the updated spec passes on a
release that contains the fix.

**R-A8Z Stage B (2026-09-14, run `R-A8Z-STAGEB-20260914T143311Z`, release `566a01a`): still NOT EARNED.**
The updated spec ran against the deployed staging stack over TLS under
`CONTRACLAIM_STAGING_E2E=1`: 2 passed, 1 failed, 8 skipped. The failure is
**F-A8Z-B1, a spec/fixture defect**: `fixtures.ts::openProtectedPage` waits on
`getByRole("heading", { name: "Security, Privacy & Anti-Piracy Terms" })`, and the
deployed terms page renders that text twice (the `h1` page title and an `h2` inside
the scrollable terms), so Playwright strict mode refuses the locator; file-level
serial mode then skipped every Gate 4 test. A diagnostic re-run of the unchanged
file selecting only this block passed 7 of 8 and failed row 9 on the same locator.
Row by row (with independent HTTP probes and a database/audit read-back): rows 1-8,
10, 11, 13 and 14 PASS - row 4 answered 403 with a `scope_denied` audit row, so
F-A8W-B3 is closed on a deployed stack, and row 7's header + foreign-id probe that
answered 500 in R-A8W answered 403; row 9 FAIL (not measured past F-A8Z-B1); row 12
INCONCLUSIVE - the run-owned org1 account was refused its own control read and write
even holding `projects:read` / `projects:update`, so the refusal measured was not the
foreign-organisation check (proj1 stayed org1). This mapping named `dms.project.manage`
for row 12, which `routers/projects.py` does not check (F-A8Z-B2). Observation
F-A8Z-B3: `DELETE /api/roles/{id}` soft-deletes and `GET` still answers 200 for the
deleted role. Re-earned by fixing the locator (and re-tracing row 12's control) and
running the spec again on staging; an offline fix earns nothing.

**R-A9A (2026-09-15, offline): spec repaired, bullet still NOT EARNED.** What
changed, and what it does not buy:

* **F-A8Z-B1 fixed in the harness.** The terms heading is located by the page's
  semantic contract - the one level-1 heading with that exact name
  (`client/e2e/staging/security-terms.ts::securityTermsHeading`) - not by element
  order. The acceptance code moved to that module unchanged in behaviour (consent
  box, "Accept and Continue", POST observed on the wire), so the mocked suite
  `client/e2e/security-terms-acceptance.spec.ts` now runs the same code against the
  page with an active version titled exactly like the page. Red first: the mocked
  suite reproduced the R-A8Z strict-mode error verbatim; restoring the ambiguous
  locator turns it red again. That suite is component evidence and is excluded
  from deployed-stack runs.
* **Serial mode is scoped to the Gate 3 bullet 2 block.** A bullet-2 failure can no
  longer skip Gate 4 tests (guard: `test_serial_mode_cannot_skip_the_gate4_tests`).
* **F-A8Z-B2 root cause: a harness seed defect, not an authorization defect.** The
  R-A8Z seed wrote the probe role's NAME into `users.roles`; the role was inserted
  with a generated ObjectId, and `PermissionService` resolves `users.roles` by
  `_id`. The account therefore held no permission at all, which is why re-seeding
  the role with `projects:read` / `projects:update` changed nothing. The default
  roles work because their `_id` is the role key (`"orgadmin"`). Reproduced offline
  in `test_gate4_row12_project_control.py::test_a_role_referenced_by_name_grants_nothing_which_was_F_A8Z_B2`.
* **Row 12 is now automated with the org admin as its own control**: the default
  `orgadmin` role receives the full `CLIENT_DMS_PERMISSIONS` merge
  (`initial_data/default_roles.py`), which includes `dms.dashboard.view` and
  `dms.project.manage`. The earlier statement that the default org-admin role
  lacks `dms.project.manage` was wrong.
* **Positive controls added to rows 6, 8 and 13** (own project by id, unfiltered
  user list, own user and a run-owned own-organisation role by id), and a teardown
  for the Gate 4 block, which now creates one run-owned role.
* The spec lists **12 tests** (3 Gate 3 bullet 2 + 9 Gate 4 bullet 8).

**R-A9B (2026-09-15, offline): authorization semantics changed, bullet still NOT
EARNED.** F-A9A-1 and F-A9A-2 are fixed in `PermissionService`,
`core/permissions.py::equivalent_permissions` and `PolicyService.has_permission`
(see the closure section below), and the same fan-out was found to make the default
org admin a billing/subscription administrator (F-A9B-1, fixed). The default
`orgadmin` role holds every permission rows 1-14 exercise **directly**
(`CLIENT_DMS_PERMISSIONS` merge plus its stored `organizations:*`, `roles:*`,
`users:*`), so no row's expected result changes. What does change is the code
under measurement, so **the staging run of record must execute against the final
R-A9B HEAD**; a run against an earlier release measures authorization that no
longer ships.

### Row 12, traced from source

| Field | Value |
|---|---|
| ENDPOINT | `/api/projects/{project_id}` (`routers/projects.py::read_project`, `::update_project`) |
| METHOD | `GET` (control read, read-back), `PUT` (control write, re-parent) |
| REQUIRED PERMISSION | GET: dependency `require_permission("projects:read")` (satisfied by `dms.dashboard.view`), then `PolicyService.authorize("dms.dashboard.view", project scope)`. PUT: dependency `require_permission("projects:update")` (satisfied by `dms.project.manage`), then `PolicyService.authorize("dms.project.manage", project scope)`, then `check_resource_access` membership. Scope passes when the project is in the caller's projects, or the caller is `orgadmin`/`orguser` in the project's organisation. No step-up. |
| CONTROL RESOURCE | `E2E_STAGING_PROJECT_ID` (proj1, organisation `E2E_STAGING_ORG_ID`), as the org admin |
| CONTROL EXPECTED | `GET` 200 naming the own organisation; `PUT {name: <unchanged>, organization_id: <own>}` 200 |
| FOREIGN RESOURCE | the same project with `organization_id: E2E_FOREIGN_ORG_ID` in the body (re-parent into org2) |
| FOREIGN EXPECTED | exactly 403 `Moving a project to another organization requires a superadmin` (refused before the target organisation is looked up), read-back still names the own organisation and the unchanged name |

Offline proof of the control shape through the real dependency chain (only the
subscription entitlement and the audit writers stubbed):
`test_gate4_row12_project_control.py` - control passes with `allow client_scope`
decisions for both policy permissions, the re-parent is refused by its own check,
a foreign project by id is refused `scope_denied`. Mutation: stripping every held
permission that satisfies `projects:update` turns the control into a gate refusal
(`Missing required permission: projects:update`) and the three control tests red.

## What "manually validated" is taken to mean

Carried forward, not re-made: `READINESS_CONVERGENCE.md` §7 records that one
execution of `client/e2e/staging/org-admin-permissions.spec.ts` is the measurement
for both Gate 3 bullet 2 and this bullet, and the R-A8W owner decision added that
the run must be **reviewed row by row by a person (or Claude acting as reviewer)
with an independent probe and a database/audit read-back**, which is what
"manually" buys over an automated green. The bullet's wording does not name the
properties; the owner decision for R-A8W does: org-admin behaviour, project and
organisation scope, foreign-tenant denial, and selector / direct-API behaviour.

A refusal row counts only when a **positive control** from the same account on
the same route answered 200 in the same run. Without it a 403 may be the
permission gate, which is exactly what R-A8Z row 12 measured.

## Mapping (rebuilt in R-A9A from the corrected spec and current source)

Fixture of record (R-A8Z seed, unchanged): org admin `orgadmin@example.com` (role
`orgadmin`, organisation org1, projects [proj1]); org1 projects proj1, proj3;
foreign org2 with proj2, an initializer user and a run-owned organisation role;
active `dms_enterprise` subscriptions for org1 and org2; terms NOT pre-accepted.

**Mandatory fixture preflight (R-A9A adversarial review), a database read-back
before the spec runs, recorded with the run:** `E2E_FOREIGN_ORG_ID` exists and
differs from `E2E_STAGING_ORG_ID`; `E2E_FOREIGN_PROJECT_ID`, `E2E_FOREIGN_USER_ID`
and `E2E_FOREIGN_ROLE_ID` exist, are active, and name `E2E_FOREIGN_ORG_ID` as
their organisation; `E2E_STAGING_PROJECT_ID` names `E2E_STAGING_ORG_ID` with a
string `organization_id` (row 12's control writes it back as a string); the staging
`orgadmin` role document holds `dms.dashboard.view`, `dms.project.manage` and
`organizations:update`. Why: `GET /api/organizations/{id}` runs the access check
before the lookup, so a wrong foreign organisation id is refused 403 exactly like a
real one, and rows 3, 7 and 8 filter "foreign" by that id. The spec asserts the
foreign ids differ from the fixture ids, and requires **exactly 403** on every
direct-id refusal - users, roles and projects answer 404 for an id that does not
exist, so 403 there also proves the object exists - but only the database can
prove which organisation a foreign object belongs to.

| Row | Literal requirement | Automated assertion (spec test) | Manual validation | Fixture | Expected | Status before staging |
|---|---|---|---|---|---|---|
| 1 | Org-admin saves and retrieves role permissions, incl. Client DMS (shared with Gate 3 b2) | `a Client DMS permission granted through the API is read back`; `the Client DMS group is offered, and a grant saved in the browser survives a reload` (terms accepted through the page, Permissions tab, Save Changes + step-up, server read-back `[dms.document.share]`, ticked after reload) | DB: the run role's `organization_id` is org1; audit `role.updated` by the org admin | run-owned org1 role `g3-<run>-permissions` | 200; read-back equals the grant; ticked after reload | R-A8Z: API half PASS, browser half not reached (F-A8Z-B1). Locator fixed offline. **OPEN** |
| 2 | A permission write needs step-up | `the permission write is refused without a step-up token` (control: the step-up write in row 1 answers 200) | none | same role | 403 without step-up | R-A8Z PASS. **OPEN** (needs the run of record) |
| 3 | The admin sees its own organisation and no other | `the organisation list holds the org admin's own organisation and no other` | audit: every `policy.authorize allow` for the admin names org1 | org1, org2 | 200, ids = [org1] | R-A8Z diagnostic + manual PASS. **OPEN** |
| 4 | Foreign organisation by id is refused | `a foreign organisation addressed directly is refused` (control: own organisation by id 200) | body names no foreign field; `scope_denied` audit row for org2 | org2 (preflight: exists) | exactly 403 (R-A9A; the access check precedes the lookup, so this alone does not prove existence) | R-A8Z PASS (F-A8W-B3 closed on a deployed stack). **OPEN** |
| 5 | Visible projects are the admin's own, non-empty | `every visible project belongs to the org admin's organisation` | none | proj1, proj3 | 200, non-empty, all org1 | R-A8Z diagnostic PASS. **OPEN** |
| 6 | Foreign project by id is refused | `a foreign project, or a foreign-organisation filter, leaks nothing` (**control added R-A9A**: own project by id 200) | none | proj1 (control), proj2 | control 200; foreign exactly 403 (404 = missing fixture) | R-A8Z diagnostic PASS without an in-test control. **OPEN** |
| 7 | Selector: a foreign organisation's rows by parameter leak nothing | same test, `?organization_id=<foreign>` (control: row 5's unfiltered list) | by hand: `X-Org-Id: org2` header on a list, and with `GET /api/organizations/org2` | org2 | 200 with no foreign object, or 403/404; never 401/500 | R-A8Z diagnostic + manual PASS. **OPEN** |
| 8 | No foreign role or user is visible | `roles and users of a foreign organisation are not visible` (control: role list 200; **added R-A9A**: unfiltered user list 200) | none | seeded org2 role; `users?organization_id=org2` | no foreign rows; filter 200-empty or 403/404 | R-A8Z diagnostic PASS. **OPEN** |
| 9 | Admin pages in the browser receive no foreign object and never force a logout | `navigating the admin pages surfaces no foreign data and no forced logout` (controls, tightened after the R-A9A review: each page stays on itself, shows its own `h1` and no "Access unavailable" card, and makes its own API call AFTER navigation - the navbar's organisation/project calls no longer count - and every response body is parsed before the leak assertion) | none | org admin, terms accepted through the page | no 401, no foreign object | R-A8Z FAIL (not measured, F-A8Z-B1). Fixed offline. **OPEN** |
| 10 | A role write naming a foreign organisation does not land in it | not asserted (the spec is read-only against the foreign tenant) | by hand: POST a run-tagged role naming org2 (control: same POST naming org1 200) → stored in org1; org2 roles changed 0 | org admin | 200, stored in org1 (Disposition A below) | R-A8Z manual PASS. **OPEN** |
| 11 | A user write naming a foreign organisation does not move the user | `the org admin cannot move itself into a foreign organisation` (control: `GET /api/users/me` 200 naming org1) | DB: the users organisation map hash unchanged | org admin's own user | exactly 403; `me` still org1 | R-A8Z diagnostic + manual PASS. **OPEN** |
| 12 | A project write naming a foreign organisation does not re-parent it | **new in R-A9A**: `the org admin can update its own project, and cannot move it into a foreign organisation` (control GET 200 and unchanged PUT 200, then re-parent exactly 403, then read-back) | preflight: the staging `orgadmin` role document holds `dms.dashboard.view` and `dms.project.manage`; DB: proj1 `organization_id` and `name` before = after; audit: `policy.authorize allow` for `dms.project.manage` on proj1 before the refusal | E2E_STAGING_PROJECT_ID (proj1), org2 | control 200/200; re-parent 403; proj1 still org1 | R-A8Z INCONCLUSIVE (F-A8Z-B2, harness seed defect). Control proven offline. **OPEN** |
| 13 | Foreign user and foreign role by id are refused | `a foreign user and a foreign role addressed directly are refused` (**controls added R-A9A**: own user by id 200, run-owned org1 role by id 200) | none | E2E_FOREIGN_USER_ID, E2E_FOREIGN_ROLE_ID; run-owned `g3-<run>-g4-own-role-control` | controls 200; foreign exactly 403 (404 = missing fixture) | R-A8Z diagnostic + manual PASS without in-test controls. **OPEN** |
| 14 | Foreign organisation update by id is refused | not asserted (a regression would write to the foreign tenant) | by hand: **control** `PUT /api/organizations/org1` with its unchanged name → 200 (the org admin holds `organizations:update`, and `check_organization_access` allows `orgadmin` on its own organisation); then `PUT /api/organizations/org2` unchanged → 403; org2 `updated_at` unchanged | org1, org2 | control 200; foreign 403 | R-A8Z manual 403 **without the own-organisation control** - re-measure with it. **OPEN** |

The bullet is ticked only when every row is PASS with evidence from one staging
run of record. A green Playwright run alone is not enough.

## Role soft delete (F-A8Z-B3) — disposition

Read from source (`services/role_service.py`, `routers/roles.py`):
`delete_role` is a documented soft delete (`is_active=False`, `deleted_at`,
`deleted_by`, document kept); `get_roles_paginated` filters `is_active != False`,
so the role leaves every listing; `get_role_by_id` does not filter, and
`GET /api/roles/{id}` stays behind `roles:read` and `can_view_role` (tenant scope);
the router emits `role.deleted` with the before-image.

**Disposition A - intentional soft delete; direct historical lookup allowed** inside
the caller's scope. Not changed for Gate 4, whose literal requirement does not
touch deletion. Pinned by `test_role_soft_delete_contract.py`.

## Authorization defects found while tracing - found R-A9A, closed offline in R-A9B

* **F-A9A-1 - a soft-deleted role still granted its permissions.** Nothing on the
  permission path read `is_active`, and `delete_role` neither detached users nor
  invalidated their cache. **Fixed:** every resolver loads roles through
  `PermissionService._load_role`; an inactive role contributes no permission,
  wildcard or role name. `delete_role` drops holders' cached grants and sets
  `user_jwt_min_iat`. The cache key moved to `permission_cache_key` (`user_perms:v2:`)
  so pre-fix entries are never read.

  The R-A9B adversarial review found two gaps in the first cut, both now fixed:
  * **System roles granted by name.** A soft-deleted system role stored under its
    key (`superadmin`, `orgadmin`) still granted by name, because `CurrentUser.roles`
    is built from `users.roles` strings. The principal now drops inactive
    references.
  * **Cache race.** A check in flight could write a pre-deletion grant back after
    the invalidation. Cached grants now carry `computed_at` and are refused unless
    newer than `user_jwt_min_iat`.

  The disposition-A historical read is unchanged. Pinned by
  `test_role_soft_delete_contract.py`.
* **F-A9A-2 - a shared legacy alias made unrelated permissions equivalent.**
  **Fixed as a class:** `equivalent_permissions` is the only alias expansion. It is
  one hop in either direction between a canonical and a legacy name it declares,
  and never transitive. `PermissionService`'s reverse table and
  `PolicyService.has_permission`'s second expansion are gone. `dms.task.manage` no
  longer satisfies `dms.project.manage`, and it still passes the legacy
  `projects:update` dependency. A role storing `projects:update` keeps what that
  name gated. Pinned by `test_permission_alias_contract.py` (matrix over every
  shared alias) and the route-level tests in `test_gate4_row12_project_control.py`.
* **F-A9B-1 - the default org admin was a billing and subscription administrator
  (same mechanism).** `dms.admin` shares `system:admin` with `billing.plan.manage` and
  `subscription.*`. Through the real `PolicyService.authorize` chain on the R-A9A
  tree, the default `orgadmin` was allowed:
  * `billing.plan.manage` with no tenant scope (`POST/PUT /api/plans`, the
    platform-wide catalogue, behind step-up);
  * `subscription.entitlement.manage` in its own organisation;
  * `subscription.upgrade` in its own organisation.

  Conversely, a role holding only `billing.plan.manage` satisfied `dms.admin`, and
  through it every `dms.*` check. Foreign-organisation scope still refused. The
  same code runs in production today. **Fixed** by the F-A9A-2 change.
* **F-A9B-2 - CLOSED in R-A9D** (owner decision: global legal-words administration
  requires system authority). `system:admin` is no longer any canonical's legacy name,
  backend and client, in either direction; pinned by
  `test_system_admin_authority_contract.py`. No Gate 4 b8 row gates on `system:admin`.
  The observation as recorded in R-A9B follows. Eight canonicals declare `system:admin` as
  their legacy name, so every holder of one passes `require_permission("system:admin")`.
  That covers `dms.admin` (default `orgadmin`, `contractmgr_org`, `projectadmin`),
  `billing.plan.manage` (`contraclaim_billing_admin`), `subscription.entitlement.manage`
  and `subscription.upgrade/downgrade/cancel/trial.manage/addon.manage`.
  That dependency is the only gate on `/api/admin/legal-words`, and legal words are
  global (no organisation field). Conversely, a role that stores the legacy
  `system:admin` passes `billing.plan.manage` and `subscription.entitlement.manage`,
  and the billing branch allows those with no tenant scope. That matches the declared
  meaning of `system:admin`: it is non-delegable, and the one production role storing
  it also holds both directly. Each of these is one declared hop, and each was true
  before R-A9B. Whether these holders should administer platform-wide legal words is
  an owner decision.

No row verdict above changes: the org admin holds the row permissions directly. The
staging run must still execute on the R-A9B HEAD.

## Foreign-write semantics (F-A8W-B5)

R-A8W observed `POST /api/roles` from an org admin with `organization_id` naming a
foreign organisation return 200, and the role stored in the **admin's own**
organisation. No foreign write happened.

The contract, read from source rather than from what feels cleaner:

* `routers/roles.py::_create_role_policy_scope` authorises an org admin's role
  creation against `current_user.organization_id`, **not** the requested value:
  `if "orgadmin" in roles: return (str(current_user.organization_id), None)`.
* `services/role_service.py::create_role`, org-admin branch, then **overwrites**
  the payload: `scope = "organization"; organization_id = str(org_id);
  project_id = None`. A project admin is clamped the same way to its own
  organisation.
* Only the superadmin branch honours `role_data.organization_id`.
* `RoleCreate` declares `organization_id` as an optional field with no validator,
  `docs/AUTHZ.md` says nothing about rejecting a body scope, and neither the
  Gate 4 bullet nor the R-A8W owner decision states that a foreign scope in a
  body must be refused.

**Disposition: A — correct, deliberate scope clamping** by the source's own
design, implemented twice (authorisation and persistence). Isolation holds. It is
recorded as a **product/owner question, not a defect**: a client that sends a
foreign `organization_id` gets a success that silently means something else, and
a 403/422 would be more honest. Changing it is a behaviour change to a live API
and is not made in R-A8X.

## What re-earns the bullet

One staging execution of the corrected spec on a release containing the R-A9A
harness fix, **12/12** (a deployed-stack run is forced to one worker and no
retries by `playwright.config.ts`, because the Gate 3 and Gate 4 blocks both clean
up by run tag and a retried pass is not evidence), the fixture preflight above
recorded, and all 14 rows above reviewed by hand against the database and
the policy audit log, each refusal row with its positive control, recorded here
requirement by requirement.
