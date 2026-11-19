# DEF-4: billing and subscription permissions, separated from organisation and report viewing

**Status (2026-11-19).** Implemented behind `BILLING_EXPLICIT_PERMISSIONS`, which defaults to **off**; tested on real MongoDB; independently reviewed (agent, not the authorised security review). CI evidence is in the ticket register.

**Not activated, and nothing in production changed:**

- no alias removed;
- no billing role grant applied;
- no data migrated.

**With the flag off, authorisation is unchanged.** One catalogue side effect on deployment: the two new names enter the permission catalogue. `ensure_permission_catalog_and_superadmin` then inserts their permission documents and adds them to the `superadmin` role document, which already passes every billing check. The `*` advertisement of a wildcard holder lists them too.

**Activation requires all of the following:**

1. the P-05 reading (section 6);
2. the authorised security review;
3. staging evidence (two consecutive deployed runs);
4. a separate, recorded owner release decision.

## 1. Owner decisions (2026-11-19)

1. **Project Admin.**
   - Sees its assigned project's active subscription, service entitlements, feature availability and expiry.
   - Gets no organisation-wide billing records, invoices, payment details or financial history by default.
   - Cannot purchase, upgrade, downgrade, cancel or otherwise change commercially binding terms without explicit scoped authority.

   **Project User:** only the service availability needed to use the application.
2. **Effective services.** `/plan-settings/effective-services` stays available to authorised organisation and project users within their scope.
   - It returns only the service and feature availability each caller needs; no amounts, payment details, invoices, unrelated projects or hidden organisations.
   - It does not require billing-finance permissions to answer whether a feature is on.
3. **Organisation Admin and the billing administrator.**
   - Both are granted the billing and subscription view permissions explicitly before any alias is removed.
   - Viewing financial records stays separate from performing financial mutations.
   - No ordinary role gains financial permissions through `organizations:read`, `orgs:view` or `reports:view` in the target policy.

## 2. Endpoint audit (all methods)

The audit covers every route under `/api/rbac-monetization` (37 routes), plus `/api/billing/webhooks/{provider}`. Out of scope, as not billing (they matched the search patterns only):

- expert allocations (`drafting.request.assign`);
- the unrelated "plan" routes (deep planning, drafting plans).

Before (flag off, today):

| Endpoint group | Gate today | Finding |
|---|---|---|
| `GET /billing/records`, `/billing/review-queue`, `/billing/summary`, receipt | `billing.plan.view` in the organisation | Reached by `organizations:read` (every tenant built-in role) through the alias |
| `GET /billing/history`, `/subscriptions/{id}/history` | `subscription.history.view` | Same alias |
| `GET /subscriptions/{id}/invoice-preview` | `billing.invoice.view` | Same alias |
| `GET /subscriptions/{id}` | `subscription.usage.view` | Reached by `reports:view` (Reporter) |
| `GET /subscriptions`, `GET /plan-settings` | **Role-name lists** (`ORG_/PROJECT_SUBSCRIPTION_VIEW_ROLES`), with a fallback for any member | Every member, including a custom role holding no billing permission, gets **full rows**: billing status, billing period, payment-gateway subscription id |
| `GET /plan-settings/effective-services` | Membership; **any holder of `subscription.entitlement.manage` sees every organisation** | Cross-tenant read for the billing administrator role, wherever it is held |
| `POST /subscriptions/checkout` | Role names: `orgadmin` (organisation or project), `projectadmin` (own project), else `subscription.upgrade` | **A purchase by role name**, contrary to Decision 1 |
| Upgrade, downgrade, cancel, reactivate, change period, add-ons, trials, `PUT /subscriptions/{id}`, `POST /subscriptions` | Their own mutation names via `PolicyService.authorize` with step-up | Not reachable through the view aliases; administrator widening (`billing.plan.manage` / `subscription.entitlement.manage` pass any `billing.*` / `subscription.*` check) |
| `PUT /plan-settings/scope` | Super Admin only, with step-up | — |
| Plan and add-on CRUD, `POST /billing-records`, `POST /usage-events` | `billing.plan.manage` / `subscription.entitlement.manage` | — |
| `GET /plan-catalog`, `/plans/{code}`, `/add-ons` | Authentication only | Public product catalogue (prices of plans), not organisation data; unchanged |
| `POST /api/billing/webhooks/{provider}` | Provider signature | Unchanged |

## 3. Role × endpoint matrix (measured)

These are real HTTP responses from the production app on a disposable replica set, one persona per role in organisation A (`backend/rbac_backend/tests/integration/test_def4_billing_explicit_permissions_mongo.py` seeds them; the probe ran every route for every persona).

How to read the cells:

- **allow:** authorisation passed.
- **allow (400) / allow (404):** authorisation passed, and the request was then refused for its content (an upgrade to the same plan; a receipt that does not exist).
- **deny:** 403.
- Every route also refuses another organisation's data in every state (tests: cross-tenant cases).

#### Flag off: today's production behaviour

| Endpoint | Super Admin | Billing Admin | Org Admin | Org User | Project Admin | Project User | Contract Mgr | Reporter | Custom (billing.record.view) | Custom (organizations:read) |
|---|---|---|---|---|---|---|---|---|---|---|
| `GET /billing/records/{org}` | allow | allow | allow | allow | allow | allow | allow | **deny** | **deny** | allow |
| `GET /billing/review-queue/{org}` | allow | allow | allow | allow | allow | allow | allow | **deny** | **deny** | allow |
| `GET /billing/summary/{org}` | allow | allow | allow | allow | allow | allow | allow | **deny** | **deny** | allow |
| `GET /billing/history/{org}` | allow | allow | allow | allow | allow | allow | allow | **deny** | **deny** | allow |
| `GET /billing/records/{record}/receipt?organization_id={org}` | allow (404) | allow (404) | allow (404) | allow (404) | allow (404) | allow (404) | allow (404) | **deny** | **deny** | allow (404) |
| `GET /plans` | allow | allow | allow | allow | allow | allow | allow | **deny** | **deny** | allow |
| `GET /plan-catalog` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /plans/dms_enterprise` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /add-ons` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /subscriptions` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /subscriptions/{sub}` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | allow | **deny** | **deny** |
| `GET /subscriptions/{sub}/history` | allow | allow | allow | allow | allow | allow | allow | **deny** | **deny** | allow |
| `GET /subscriptions/{sub}/invoice-preview` | allow | allow | allow | allow | allow | allow | allow | **deny** | **deny** | allow |
| `GET /plan-settings` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /plan-settings/effective-services` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `POST /subscriptions/checkout` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/checkout (project scope)` | allow | **deny** | allow | **deny** | allow | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/upgrade` | allow (400) | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/downgrade` | allow (400) | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/change-period` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/add-ons/add` | allow (400) | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/start-trial` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `PUT /subscriptions/{sub}` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `PUT /plan-settings/scope` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /billing-records` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /usage-events` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/cancel` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/reactivate` | allow | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |

#### Flag on, before the grant step (why grant-before-revoke)

| Endpoint | Super Admin | Billing Admin | Org Admin | Org User | Project Admin | Project User | Contract Mgr | Reporter | Custom (billing.record.view) | Custom (organizations:read) |
|---|---|---|---|---|---|---|---|---|---|---|
| `GET /billing/records/{org}` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | allow | **deny** |
| `GET /billing/review-queue/{org}` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | allow | **deny** |
| `GET /billing/summary/{org}` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | allow | **deny** |
| `GET /billing/history/{org}` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /billing/records/{record}/receipt?organization_id={org}` | allow (404) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /plans` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /plan-catalog` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /plans/dms_enterprise` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /add-ons` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /subscriptions` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /subscriptions/{sub}` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /subscriptions/{sub}/history` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /subscriptions/{sub}/invoice-preview` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /plan-settings` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /plan-settings/effective-services` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `POST /subscriptions/checkout` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/checkout (project scope)` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/upgrade` | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/downgrade` | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/change-period` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/add-ons/add` | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/start-trial` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `PUT /subscriptions/{sub}` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `PUT /plan-settings/scope` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /billing-records` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /usage-events` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/cancel` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/reactivate` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |

#### Flag on, after the grant step (proposed target)

| Endpoint | Super Admin | Billing Admin | Org Admin | Org User | Project Admin | Project User | Contract Mgr | Reporter | Custom (billing.record.view) | Custom (organizations:read) |
|---|---|---|---|---|---|---|---|---|---|---|
| `GET /billing/records/{org}` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | allow | **deny** |
| `GET /billing/review-queue/{org}` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | allow | **deny** |
| `GET /billing/summary/{org}` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | allow | **deny** |
| `GET /billing/history/{org}` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /billing/records/{record}/receipt?organization_id={org}` | allow (404) | allow (404) | allow (404) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /plans` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /plan-catalog` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /plans/dms_enterprise` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /add-ons` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `GET /subscriptions` | allow | allow | allow | **deny** | allow | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /subscriptions/{sub}` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /subscriptions/{sub}/history` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /subscriptions/{sub}/invoice-preview` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /plan-settings` | allow | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `GET /plan-settings/effective-services` | allow | allow | allow | allow | allow | allow | allow | allow | allow | allow |
| `POST /subscriptions/checkout` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/checkout (project scope)` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/upgrade` | allow (400) | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/downgrade` | allow (400) | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/change-period` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/add-ons/add` | allow (400) | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/start-trial` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `PUT /subscriptions/{sub}` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `PUT /plan-settings/scope` | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /billing-records` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /usage-events` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/cancel` | allow | allow | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |
| `POST /subscriptions/{sub}/reactivate` | allow | allow (400) | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** | **deny** |

Reading the target table:

- **Financial reads** (records, review queue, summary, history, invoices, the full `/plan-settings` and full `/subscriptions` rows) are limited to Super Admin, Billing Admin and Org Admin, plus a custom role holding the exact name.
- **Project Admin** sees `/subscriptions` as status rows for its own project only: no `billing_status`, `billing_period` or gateway ids, and not the organisation's or a sibling project's row.
- **Effective services** stay open to every member for their own scope, cut to what each may see.
- **Commercially binding mutations** are limited to Super Admin and Billing Admin. Project-scope checkout is refused for the billing administrator too, because it has no organisation-wide scope and is not on the project; that scope rule already exists.

## 4. Permission model

| Name | Kind | Meaning |
|---|---|---|
| `billing.record.view` **(new)** | financial view | billing records, review queue, summary |
| `billing.invoice.view`, `billing.invoice.download` | financial view | invoice preview; receipts |
| `subscription.history.view` | financial view | subscription history; full `/subscriptions` rows; full `/plan-settings` |
| `subscription.usage.view` | financial view | a single subscription record |
| `billing.plan.view` | financial view | plan list with organisation context |
| `subscription.status.view` **(new)** | operational | subscription status, entitlements, features and expiry, without financial fields |
| `subscription.upgrade`, `.downgrade`, `.cancel`, `.addon.manage`, `.trial.manage` | mutation | commercially binding changes |
| `subscription.entitlement.manage`, `billing.plan.manage` | platform administration | unchanged |

**Flag-on rule** (`PolicyService.has_permission`, its batch form `PolicyService.explicit_billing_scopes` used by the list and effective-services routes, and the same exclusion in `get_effective_permission_names`):

- A `billing.*` or `subscription.*` check passes only through an explicit grant of that name on a role that applies in the request's scope.
- It does not pass through `organizations:read`, `orgs:view` or `reports:view`, a wildcard, or an administrator name. A view never implies a mutation, and a mutation never implies a view.
- Super Admin's bypass is unchanged.
- At organisation level, or with no scope, a project-tier user (Project Admin, Project User) counts only with organisation-wide scope. A built-in project role's grant is not bound to a project, and must not reach the organisation's records or sibling projects.
  - This is applied in `PolicyService`, so every route uses it, checkout included.
  - Proven with Project Admin's role granted financial views and `subscription.upgrade`: organisation-level reads and checkout are refused, and the project's own history and checkout are allowed.

**Response shaping (flag on):**

| Data | Fields |
|---|---|
| Status row | `id`, `organization_id`, `project_id`, `plan_code`, `status`, `starts_at`, `ends_at`, `current_period_end`, `trial`, `trial_ends_at`, `active_add_ons` |
| Effective services, operational | `source`, `status`, `dms_enabled`, `drafting_enabled`, `features` |
| Effective services, status | operational, plus `plan_code`, `plan_name`, `current_period_end`, `trial`, `trial_ends_at`, `active_add_ons`, `inherited_from_organization_id` |
| Effective services, financial | the full state |

**Target grants:** `backend/rbac_backend/services/billing_permission_plan.py::TARGET_ROLE_GRANTS`, one definition used by the tests, P-05 and the migration plan; nothing applies it.

| Role | Granted in the grant step |
|---|---|
| `orgadmin` | all financial views + `subscription.status.view` (no mutations) |
| `contraclaim_billing_admin` | all financial views + status + the five subscription mutations (it already holds `billing.plan.manage`, `subscription.entitlement.manage`) |
| `projectadmin` | `subscription.status.view` |
| `orguser`, `projectuser`, `contractmgr_org`, `reporter`, `doccontroller`, `settings_manager`, `limited_user` | nothing |
| custom roles | nothing automatically; re-granted individually where P-05 shows a need, by Super Admin (billing names are non-delegable) |

## 5. Implementation (flag off changes nothing)

- **`core/config.py`:** `BILLING_EXPLICIT_PERMISSIONS: bool = False`.
- **`core/permissions.py`:**
  - the new names (catalogue and billing domain);
  - `LEGACY_BILLING_VIEW_ALIASES`;
  - `is_billing_permission`.
- **`services/permission_service.py`:**
  - `user_has_explicit_permission(..., exclude=)`;
  - the advertised list applies the flag-on rule, so the client does not offer what the policy refuses.
- **`services/policy_service.py`:** the flag-on explicit rule.
- **`routers/rbac_monetization.py`** (flag on only):
  - record, review-queue and summary gate on `billing.record.view`; receipts on `billing.invoice.download`;
  - checkout requires `subscription.upgrade` in scope, with no role-name path;
  - `/subscriptions`: full rows with `subscription.history.view`, status rows with `subscription.status.view`, otherwise 403;
  - `/plan-settings`: `subscription.history.view` in the organisation, otherwise 403;
  - `/plan-settings/effective-services`: scoped for everyone but Super Admin and cut per scope (the cross-tenant read is closed).
- **Not changed:**
  - the alias tables;
  - built-in role documents;
  - `initial_data/default_roles.py`;
  - any migration.

## 6. P-05 read-only impact assessment

`scripts/claim_arbitration_readonly_readings.py`, P-05 → `billing_permission_impact`. It reads only, through the read-only guard, and runs on images that predate DEF-4: the target grants are inlined, and a unit test keeps them equal to the plan module. It reports counts and organisation ids, never names or emails:

- active users; users relying on a legacy alias for at least one billing view they do not hold explicitly; of those, how many keep billing after the grant step and how many lose it (at least in part);
- wildcard holders, who lose billing under the flag;
- `losing_billing_by_role_type`, `keeping_billing_by_role_type`, `losing_billing_by_organization` (**per-organisation affected-user counts**);
- Project Admins whose role-name checkout path ends;
- holders of `subscription.entitlement.manage` (today's cross-tenant effective-services readers);
- custom roles holding the legacy view names, and custom roles holding explicit billing names;
- `grant_step_names_missing_per_built_in_role`, the exact size of the grant step.

Proof: the test runs it on the seeded database. It changes nothing, contains no names, and its counts match the matrix.

**Not yet read in production.** An unread count is never zero.

## 7. Grant-before-revoke migration plan (none of it run)

1. **Read** P-05 `billing_permission_impact` in production. Record per-organisation `losing_billing_by_organization`, and decide for each affected custom-role holder whether they need billing access.
2. **Approve:**
   - the authorised security review of this design and code;
   - the owner's release decision, which names the organisations, any custom re-grants, and the window.
3. **Grant** (additive, idempotent, reversible by removing the added names):
   - a governed migration adds `TARGET_ROLE_GRANTS` to the stored built-in role documents and to `initial_data/default_roles.py`;
   - approved custom re-grants are made by Super Admin through the role API (step-up, audited).

   With the flag still off, this changes no one's access: everyone it reaches already had the views through the aliases. Re-read P-05: `of_whom_lose_billing_after_the_grant_step` should then match the decision.
4. **Staging:** turn the flag on in staging and run two consecutive deployed E2E runs plus the role matrix probe. Keep the client billing pages in mind, since they read the server's effective list.
5. **Production activation:** set `BILLING_EXPLICIT_PERMISSIONS=true` in the release window. No code change.
6. **Alias removal:** a separate, later release. Remove the billing members of the `organizations:read`, `orgs:view` and `reports:view` alias groups, and the now-unused client alias table. Only after a stable period with the flag on.

**Rollback:**

- **Activation (step 5):** set `BILLING_EXPLICIT_PERMISSIONS=false` and restart. Behaviour returns to today's exactly, because the aliases are still in place.
- **Grants (step 3):** leave them. They are harmless with the flag off. To remove them, take the added names off the role documents (the migration's down step).
- **Alias removal (step 6):** a code revert and redeploy, planned as a release.

## 8. Tests and evidence

`backend/rbac_backend/tests/integration/test_def4_billing_explicit_permissions_mongo.py` (real HTTP, real MongoDB; in CI's real-Mongo step):

- **Flag off:** today's behaviour, the aliases and Project Admin's role-name checkout included; cross-tenant denial.
- **Flag on, without grants:** the aliases no longer reach billing; Super Admin unchanged; a custom role holding the exact name counts in its own organisation only.
- **Flag on, with grants:**
  - the matrix for Org Admin, Billing Admin, Org User, Project Admin, Project User, Contract Manager and Reporter;
  - invoices and history;
  - Project Admin sees status rows for its project only, with no financial fields and no organisation or sibling-project rows;
  - full `/plan-settings` for Org Admin only.
- **Effective services:** scoped and minimal per role; the billing administrator sees only its organisation (previously every organisation).
- **Mutations:** Project Admin and Org Admin refused checkout, upgrade and cancel. The billing administrator is allowed at organisation level, and refused at project scope and in another organisation.
- **Advertised list:** drops alias-only billing names under the flag, and lists the granted ones.
- **P-05 reading:** counts, no names, no writes.

**Mutation testing:** 9 mutants, all killed (the review's M2 and M3 tests also fail on the pre-review commit):

- no flag rule in `PolicyService`;
- `exclude` ignored;
- no project-tier organisation rule (two variants);
- role-name checkout kept;
- full rows instead of status;
- effective services unminimised;
- effective services unscoped;
- records on the old name.

## 9. Open items

- **Client pages** (`PlanSettingsPage`, `SubscriptionManagementPage`) were not changed. Under the flag they receive 403 where the server now refuses; a UI pass to hide them is part of staging step 4.
- **Plan catalogue and add-on list** remain readable by any authenticated user. They are product pricing, not organisation data; confirm in the release decision.
- **Wildcard (`*`) custom roles** no longer reach billing under the flag (least privilege); P-05 counts their holders.

## 10. Independent review (agent, 2026-11-19; not the authorised security review)

No Critical or High issue. Flag-off paths are code-identical to today, and with the flag on the alias, wildcard and administrator-widening bypasses are closed.

| Finding | Severity | Resolution |
|---|---|---|
| **M2:** the project-tier rule existed only in the router helper, so every `policy.authorize` route (records, history, checkout and so on) skipped it. Latent: it bites if `projectadmin` is ever granted a financial view or mutation. | Medium, introduced | **Fixed:** moved into `PolicyService`. Test `test_flag_on_a_project_role_grant_never_reaches_organisation_level`. |
| **M3:** per-scope evaluation read the user and roles once per organisation and project (thousands of queries for large organisations). | Medium, introduced | **Fixed:** one user read, one grants load and one project query per permission (`explicitly_held_scopes`). Test bounds grant loads at 2 per request with 42 projects. |
| **L2:** the P-05 reading did not run on old images, and its counts were biased (partial explicit holders, wildcards, a `deleted` flag the application ignores). | Low | **Fixed:** grants inlined (parity test); counted per view relied on; wildcard holders counted; only `is_active: false`. Role-reference aliases and custom-role scope remain approximations, stated in the docstring. |
| **L1:** "flag off changes nothing" was inaccurate (catalogue seeding of the two names). | Low | **Corrected** in the status note above. |
| **M1:** built-in role names are global. A user who is Project Admin in A and Org User in B (and a member of both) passes the organisation-wide check in A, and an Org Admin of A who is merely a member of B reaches B. | Medium, pre-existing scope model | **Not changed.** DEF-4's guarantees hold for single-role users only. Per-organisation built-in roles are a separate change (owner decision). P-05 should be extended to count dual-role users before activation. |
| **M4:** `contraclaim_billing_admin` (assignable to tenant users) holds `billing.plan.manage`, which `PolicyService` allows unscoped. A tenant's billing administrator can therefore edit the **global** plan and add-on catalogue (`POST/PUT /plans`, `/add-ons`). | Medium, pre-existing | **Not changed.** Owner decision needed: restrict plan and add-on CRUD to Super Admin, or make the billing administrator a platform-only role. The plan module's docstring has been corrected. |
| **L3:** `PUT /subscriptions/{id}` authorises against the stored scope only. A project-scoped holder of `subscription.entitlement.manage` can set `project_id` to null or to another project. | Low-Medium, pre-existing | **Not changed;** recorded for a follow-up (validate the new scope as well). |
| **Info:** operational `features` include `entitlement_overrides` (possibly negotiated terms); status rows include `current_period_end`. | Info | Recorded; decide in the release review whether to cut further. |
| **Info:** a project-tier user holding an explicit custom organisation-level grant is refused at organisation level everywhere under the flag (consistent since the M2 fix). | Info | — |

**CI.** CI run 44584757939 on `73c9b41` (code identical at `698effd`): backend (8,432 unit; real-Mongo 81 modules, 1,080 passed, every module ran), frontend (1,014 unit; 78 mocked E2E), dependency scan and Docker green; secret scan red (same two findings). Earlier run 44582778268 on `2401094` (before the review fixes) also green except the secret scan.
