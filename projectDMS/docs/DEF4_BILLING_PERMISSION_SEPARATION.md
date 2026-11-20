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

## 11. Owner acceptance and follow-ups (2026-11-20, sixth pass)

The owner acknowledged the DEF-4 implementation and the M2, M3 and L2 fixes as code-complete and CI-verified. **That is not production authorisation.**

**Decisions:**

- the target matrix in §4 is approved provisionally;
- `BILLING_EXPLICIT_PERMISSIONS` stays `false`;
- the legacy aliases stay unchanged until a separately approved removal phase.

**Activation stays blocked until all of these are done:**

1. P-05 is read and reviewed. This now includes `built_in_role_scope_impact` (M1).
2. Scoped grants are approved and applied in order.
3. Cross-organisation and mixed-role checks pass. M1 (`docs/M1_BUILT_IN_ROLE_SCOPING.md`) must first be shown safe.
4. The authorised security review is complete.
5. Two consecutive deployed staging E2E runs pass.
6. A separate production release decision is recorded.

| Item | Commit | What changed | Evidence |
|---|---|---|---|
| **M4:** global pricing | `8a45f3b` | Plan and add-on create/update need Super Admin or `platform.pricing.manage` on a platform role (no organisation, held by a user with no organisation). A tenant `billing.plan.manage` no longer suffices; it keeps scoped uses such as `POST /billing-records`. Step-up is kept. Policy decisions and every writer are audited, with before/after. An unknown id now returns 404. The advertised list and the client gating follow the same rule. | `test_m4_global_pricing_mongo.py` (7 tests); a reverted-gate mutant fails 3 |
| **L3:** subscription scope | `01cdb29`, `e633a0f` | `PUT /subscriptions/{id}` authorises on the stored scope. An omitted field means unchanged, and an equal value is a no-op. Any other `organization_id` or `project_id` (null, sibling or foreign project, another organisation, camelCase spellings) is refused with 400 for everyone. The service refuses scope changes from every caller and writes compare-and-set on both fields; a stale read or a concurrent change returns 409. `e633a0f` replaced a raw body read that the upload-route gate (rightly) flagged. | `test_l3_subscription_scope_integrity_mongo.py` (4 tests); 4 of 4 mutants killed |
| **Decision 4:** minimum disclosure | `5ccd918`, `8517f27`, `d0eb1db` | See below | `test_operational_minimum_disclosure_mongo.py` (13 tests, both flag states); 11 mutants killed in total |

### Decision 4, as implemented (both flag states; it narrows disclosure and activates nothing)

**Who it applies to.** A caller without **organisation-level standing** in an organisation gets only the operational view there. Organisation-level standing (`PolicyService.has_organisation_level_standing`) means one of:

- Super Admin;
- a member holding `orgadmin`, `orguser`, `contractmgr_org`, `doccontroller`, `reporter`, `settings_manager` or `contraclaim_billing_admin`;
- a member holding a custom role of that organisation, with no project, **that itself grants the view in question**. An unrelated organisation role (for example, one holding only `tags:read`) confers nothing: re-review N1, fixed in the N1 fix commit.

Callers without standing include Project Admin, Project User, a limited user, a plain user, and anyone holding only a custom project role. With the flag on, lists are first cut by the explicit grants. Project Admin and Project User are then cut further, even when they hold `subscription.status.view`.

**What the operational view contains:**

- service status;
- `dms_enabled` and `drafting_enabled`;
- `features` reduced to **boolean indicators**: a non-boolean value of the merged `entitlement_overrides` (price, limit, term) is dropped;
- expiry: `current_period_end`, `trial`, `trial_ends_at`;
- the caller's own projects only.

Plan code and name, add-ons, billing status and period, gateway ids, metadata and audit fields are not included. In `/plan-settings`, organisations and projects are cut to identity.

**Where it applies:**

- `/plan-settings/effective-services`;
- `/plan-settings`;
- `/subscriptions`;
- `/subscriptions/{id}`. With the flag on, this route is cut by the explicit history and status grants in scope.

**The financial routes.** With the flag off, `PolicyService` refuses the scoped organisation financial views to callers without standing:

- `billing.plan.view`, `billing.record.view`, `billing.invoice.view`, `billing.invoice.download`, `subscription.history.view`;
- this covers `/billing/summary`, `/billing/history`, `/billing/records`, `/billing/review-queue`, the receipt, subscription history and the invoice preview.

The legacy alias still grants the name, and organisation-level users keep it. The unscoped public `/plans` list is unaffected.

**The tenant billing admin.** With the flag off, `/plan-settings/effective-services` used to return every organisation's full state to any holder of `subscription.entitlement.manage`. It now returns only the holder's own organisations, as with the flag on.

**Changes to the flag-off matrix in §3:**

| Persona | Routes | Was | Now |
|---|---|---|---|
| Project Admin, Project User | billing records, review queue, summary, history, receipt, subscription history, invoice preview | allow | **deny** |
| Project Admin, Project User | subscription reads and effective services | full rows | operational view |
| Billing admin | effective services | every organisation | its own organisations |

All other cells are unchanged.

**Client.** `DocumentsPage` reads `drafting_enabled`, which is kept. The profile card shows "Not available" for the plan of project-level users, and still shows expiry.

### Independent review of this pass (agent; not the authorised security review)

The first round found no Critical issues, and M4, L3 and the M1 reading were sound.

| Finding | Severity | Resolution |
|---|---|---|
| **H1:** with the flag off, Project Admin and Project User still read the organisation financial routes through `organizations:read` (gateway ids, raw overrides, sibling projects, invoice lines). | High | **Fixed** (`d0eb1db`): the standing rule in `PolicyService`. |
| **M:** the cut was keyed on project role names, so it failed open for `limited_user`, `user` and custom project roles. With the flag on, the single read was uncut for a custom project role. | Medium | **Fixed** (`d0eb1db`): decided by standing, and by grants on the flag-on single read. |
| **Low 1:** `GET /plans/{code}` with an unknown code reseeds the default plans or add-ons (`$setOnInsert`, unaudited). It only restores a default deleted outside the API; there is no API delete. | Low, pre-existing | Recorded; not changed. |
| **Low 2:** `has_permission("platform.pricing.manage")` ignores scope, while `authorize` refuses a scoped call. Nothing calls it scoped, and a platform user has no organisation. | Low | Recorded. |
| **Low 3:** the client route `/admin/billing-catalog` still admits `system:admin`. This affects visibility only; the server refuses the write. | Low | Recorded. |
| **Low 4:** L3 silently drops other spellings (`organisation_id`, `org_id`) instead of refusing them. They are never written. | Low | Recorded. |
| **Low 5:** the M1 doc overstated what its test proves about "no writes". | Low | **Corrected.** |

**Re-review of `d0eb1db`** (real-MongoDB probe: 11 personas × 8 routes, both flag states). H1 and the Medium are closed. One new finding:

- **N1 (Medium):** any custom organisation role conferred standing, so a Project User given, say, a `tags:read` organisation role regained full billing through the `projectuser` alias.
  - **Fixed:** standing from a custom organisation role now requires that role to grant the financial view itself. Test `test_an_unrelated_organisation_role_gives_a_project_user_no_billing_standing`, both states; the mutant is killed.

Recorded Lows from the re-review:

1. **M1-E3 residual:** standing uses global built-in role names. Already documented as M1.
2. **Pre-existing:** with the flag off, the billing admin and custom organisation billing roles get `scope_denied` on a **project-scoped** subscription's single read, history and invoice preview, because they are not assigned to the project. Org admin and org user are unaffected. This is not caused by this pass and is recorded for the owner.
3. **Flag on:** an explicit project-scoped `subscription.history.view` grant shows full detail for that project. Only the `projectadmin`/`projectuser` role names are cut further. This follows the explicit grant, so it is intended; **owner to confirm**.
4. **Flag on:** organisation users need the grant step (unchanged; by design).

**CI:** run 44718392602 on `a98aa94` (§12).


## 12. Security closure verification (owner instructions 2026-11-20, seventh pass)

**Starting point.** Branch `claude/zen-ptolemy-695liz`. HEAD `a98aa94` matched the remote and the working tree was clean. PR #114 is a draft, open and unmerged.

### Step 1: CI on `a98aa94` (run 44718392602)

| Job | Result |
|---|---|
| Backend lint and tests | Passed. Unit: 8,434 passed, 1,210 skipped, 4 xfailed. Exhibit confinement: 20 passed. Real MongoDB: 1,115 passed, and the every-suite-ran gate passed. |
| Frontend lint, tests, build | Passed. Unit: 1,015 passed, 3 skipped. Mocked E2E: 78 passed; 59 deployed-stack tests skipped. |
| Dependency scan | Passed: no known vulnerabilities, 56 checks. |
| Docker build and image scan | Passed. |
| Secret scan (Gitleaks) | **Failed:** "leaks found: 2", the two known historical findings (with the security owner). |

**Earlier heads:**

- `01cdb29` failed the upload-route gate; fixed by `e633a0f`.
- `5ccd918` and `10714db` failed the advertised-permission parity test; fixed by `a98aa94`.
- `10714db` also failed the contract-lease test; fixed in `9a47109` (see step 6).

CI success is not security approval.

### Step 2: N1 verdict (`d9f1011`): **holds**

Tested in `test_def4_standing_and_pricing_matrix_mongo.py`, both flag states, through the HTTP routes and through `PolicyService.has_organisation_level_standing` and `authorize` directly.

| Holder (Project User or Admin of A1, unless stated) | Flag off | Flag on |
|---|---|---|
| Plus a `tags:read` role of A | No standing, 403 | 403 |
| Plus an A role granting `billing.plan.view`, `subscription.history.view`, `billing.record.view` | Standing | Allowed (since F4) |
| Plus an A role granting `organizations:read` (legacy alias) | Standing | 403 (aliases never count with the flag on) |
| Plus an inactive A role | No standing | 403 |
| Plus a project-scoped A1 role | No standing | 403 |
| Plus a B role, not a member of B | Nothing in A or B | Nothing |
| Plus a B role, membership row for B revoked | Nothing | Nothing |
| Plus a B role, member of A and B | Nothing in A; B's billing in B | Nothing |
| `projectuser` and `orguser` together (mixed built-in) | Standing; global `orguser`, M1 | Per the explicit grants |
| Custom finance role only | Standing | Allowed |

**Mutants:** dropping the N1 filter fails the suite.

### Step 3: platform pricing verdict (`8a45f3b`, `a98aa94`): **consistent**

Eight personas: a platform user with the platform role; the same role held by a tenant; a tenant billing admin; a tenant custom role naming the permission; a tenant wildcard role; an Org Admin whose built-in role was edited to include it; a platform user with no role; a platform role holding only `*`.

For each persona, all six evaluators agree:

1. `PermissionService.user_has_permission`
2. `PolicyService.has_permission`
3. `PolicyService.authorize`
4. `require_permission`
5. the advertised permission list
6. `POST /plans` with a valid step-up

Only the platform user with the platform role, and Super Admin, created plans. Other behaviour:

- A scoped `authorize` call is refused even for Super Admin.
- Revoking the role, or the user joining an organisation, takes effect on the next check (no cache).

**Mutants:** 6 checked. 5 are killed. The sixth, `PolicyService.has_permission`'s own branch, is equivalent: the resolver applies the same rule.

### Step 4: what changes with the flag off (production impact)

These apply to existing users without activating DEF-4.

| Change | Who | Commit |
|---|---|---|
| Organisation financial views refused (billing summary, history, records, review queue, receipt, subscription history, invoice preview) | Users without organisation-level standing in that organisation: Project Admin, Project User, limited and plain users, custom project roles | `d0eb1db`, `d9f1011` |
| Subscription and service-state reads cut to status, boolean feature indicators and expiry (`/subscriptions`, `/subscriptions/{id}`, `/plan-settings`, `/plan-settings/effective-services`) | The same users | `5ccd918`, `d0eb1db`, `a8393ce` |
| Effective services limited to the caller's own organisations, and to projects where they stand | Tenant billing admins and custom entitlement managers | `8517f27`, `a8393ce` |
| Global plan and add-on catalogue writes refused | Tenant billing admins and any non-platform holder of `billing.plan.manage` or `*` | `8a45f3b`, `a98aa94` |
| `PUT /subscriptions/{id}` refuses changes to organisation or project | Every caller, Super Admin included | `01cdb29`, `e633a0f` |

**The P-05 reading** `flag_off_disclosure_impact` (read-only, counts and organisation ids only):

- `project_level_users_losing_financial_reads`, with a per-organisation breakdown;
- `project_level_users_given_the_operational_view`;
- `tenant_holders_losing_global_catalogue_writes`;
- `platform_pricing_holders` and `platform_pricing_names_on_tenant_roles`;
- `entitlement_managers_losing_cross_org_service_state`.

**Related readings already in P-05:**

- `billing_permission_impact`: legacy aliases, custom billing roles, wildcards;
- `built_in_role_scope_impact`: mixed built-in roles, dual-organisation users.

Constants are inlined, with parity tests. **Not read in production:** it needs AQ-11 authorised access.

### Step 5: independent review of the complete DEF-4 work (`a6dc116..a98aa94`; agent, not the authorised review)

No Critical or High finding.

| Id | Severity | Location | Scenario | Introduced? | Correction | Regression test | Release impact |
|---|---|---|---|---|---|---|---|
| F1 | Medium | `routers/rbac_monetization.py` `_minimise_plan_settings`; `services/monetization_service.py` `get_plan_settings` | With the flag off, a user whose projects resolve to no organisation (a deleted project, or a project of a missing organisation) got every organisation, plus unminimised gateway ids and negotiated overrides from `/plan-settings` | Fail-open minimiser: yes. Unfiltered organisation list: pre-existing | **Fixed** (`a8393ce`): no resolved organisation means none; every organisation, project, row and state is judged in its own organisation | `test_f1_plan_settings_fails_closed_without_a_resolvable_organisation` (both states); 2 mutants killed | Was a blocker; fixed |
| F2 | Low | `routers/rbac_monetization.py` effective-services widening | With the flag off, a project user holding a B entitlement role saw sibling projects in A (operational view) | Partly (the narrowing was incomplete) | **Fixed** (`a8393ce`): widening is per organisation where the caller stands | `test_f2_...`; mutant killed | Fixed |
| F3 | Low/Medium | `services/monetization_service.py` `SUBSCRIPTION_SCOPE_FIELDS`; `PUT /subscriptions/{id}` | An entitlement manager can change `package_id`, which entitlement lookup matches on, including promoting it to null | Pre-existing; outside L3's organisation/project decision | **Owner decision 2026-11-20: approved.** Fixed in `8eefa27` (§13) | `test_f3_subscription_package_scope_mongo.py` | Fixed |
| F4 | Low | `services/policy_service.py` `_may_answer_at_organisation_level`; the router cut | With the flag on, a custom organisation finance role was cancelled by also holding `projectuser` (the flag-off N1 rule admits it) | Yes | **Fixed** (`a8393ce`): a custom role of that organisation granting the permission answers for it | `test_f4_...` (both states); 2 mutants killed | Fixed (affected activation only) |
| F5 | Low (test gap) | M4 scoped refusal; `_platform_held` treatment of `*` | Two mutants survived | n/a | **Tests added** | `test_f5_...` | Closed |

Also from the review:

- **Not exercised:** Redis-on cache propagation after grant or revoke, and concurrent `PUT` races beyond the compare-and-set. The new explicit, standing and platform checks bypass the cache by construction.
- **Run here:** `test_security_redis_mongo.py` against real Redis: 11 passed.

### Step 6: verification

**Local runs:**

- matrix suite: 11 passed;
- related real-MongoDB set (DEF-4, M4, L3, decision 4, M1, effective permissions, permission check): 82 passed;
- related unit tests: 997 passed, 1 skipped;
- Redis suite: 11 passed;
- mypy gate passed; ruff no worse than HEAD.

**Mutation testing in this pass:** 11 killed, 1 equivalent.

**The contract-lease failure (CI run 44713207170) was investigated, not dismissed:**

- **Cause:** a test defect. The simulated partition stayed up for the retry ingests, so the final ingest self-fenced whenever it ran past the patched 500 ms window.
- **Reproduction:** deterministic, by delaying that ingest.
- **Fix:** `9a47109` ends the partition with the stopped ingest. All assertions are unchanged, and no product code changed.
- **Check:** the full file passed 3 times locally.

**Full local suites and CI on the final head:** recorded below.

### Stage matrix (seventh pass)

| Item | Implemented | Locally verified | CI-verified | Security-approved | Production-accepted |
|---|---|---|---|---|---|
| M4: global pricing (platform role or Super Admin), every evaluator consistent | Yes | Yes | Yes, `a98aa94` (run 44718392602); matrix pending CI | No | No |
| L3: subscription organisation/project scope, compare-and-set | Yes | Yes | Yes, `a98aa94` | No | No |
| Decision 4: minimum disclosure, standing rule, N1; F1, F2, F4 fixes | Yes | Yes | N1 yes (`a98aa94`); F1/F2/F4 pending CI | No | No |
| DEF-4 flag-on policy (flag `false`) | Yes | Yes | Yes | No | No: blocked (below) |
| P-05 readings: billing, M1, flag-off impact | Yes | Yes | Billing and M1 yes; flag-off impact pending CI | n/a | **Not read in production** |
| M1: per-assignment built-in roles | Audit, tests, design only | Yes | Yes | No | No |
| F3: `package_id` on `PUT` | Not changed | — | — | — | Owner decision |

### Remaining security blockers before any release

1. **P-05 production reading**, including all three impact readings. It needs AQ-11 authorised read-only access.
2. **M1:** built-in roles are global (exposures E1 to E5). The remediation must be shown safe before DEF-4 activation.
3. **F3** owner decision.
4. **Authorised security review**, including Gitleaks (2 findings) and the picomatch R5 assessment.
5. Two consecutive deployed staging E2E runs.
6. JG-5(f) legal sign-off and expert-labelled golden sets (other Phase 4b gates).
7. A written owner acceptance and release decision.

**Not done, by instruction:**

- DEF-4 not activated, roles and aliases unchanged;
- no deployment, migration or merge;
- rollout off, skills unpublished, Phase 5 not started.


## 13. F3: package scope, and the final CI evidence for F1 to F5 (owner decision 2026-11-20, eighth pass)

### CI evidence for F1, F2, F4 and F5

CI run 44733660126 on `652d4e6` (fixes in `a8393ce`):

| Job | Result |
|---|---|
| Backend lint and tests | Passed. Unit: 8,435 passed, 1,221 skipped, 4 xfailed. Exhibit confinement: 20 passed. Real MongoDB: 1,126 passed; the every-suite-ran gate passed. |
| Frontend | Passed. 1,015 passed, 3 skipped (128 files). Mocked E2E: 78 passed; 59 deployed-stack tests skipped. |
| Dependency scan | Passed: 56, no known vulnerabilities. |
| Docker build and image scan | Passed. |
| Gitleaks | **Failed:** "leaks found: 2", the known findings. |

**Locally on `a8393ce`:**

- backend unit: 8,409 passed, 0 failed, 135 skipped (environment-dependent), 4 xfailed;
- real MongoDB, all 86 CI modules with Redis on: 1,126 passed, 0 failed, 0 skipped.

### F3: what changed (`8eefa27`)

**Why `package_id` is scope.** `EntitlementService._active_subscription` matches a subscription on organisation, project **and** package. Changing a stored `package_id` therefore moves the entitlement:

- an organisation-wide subscription made package-only stops matching organisation and project checks;
- a package subscription promoted with `null` starts matching them.

**Writers audited.** These write `subscriptions.package_id`:

| Writer | Writes `package_id`? |
|---|---|
| `MonetizationService.update_subscription` (`PUT /subscriptions/{id}` and the lifecycle operations: upgrade, downgrade, cancel, reactivate, change period, add-ons, trial conversion) | The only writer on an existing subscription. The lifecycle operations never pass it. **Now protected.** |
| `SubscriptionLifecycleService` (trial expiry, renewal) | No: status and period fields only |
| `BillingWebhookService` (payment callbacks, mismatch review) | No: status, billing status, review flag |
| `update_plan_settings_scope` (`PUT /plan-settings/scope`, Super Admin) | Cancels by scope query; inserts new rows with `package_id: None` |
| `create_subscription` | At creation only, from the body, authorised on the organisation and project in the payload; the package is not validated (pre-existing; see below) |
| Checkout | Never writes the field; a checkout subscription has no `package_id` (correction: the F3 commit message said it sets it) |
| `initial_data/demo_seed.py` | Development seed only |
| Migrations, imports, scripts | None write it |

**The rule.** `SUBSCRIPTION_SCOPE_FIELDS` is now organisation, project and package. For an ordinary update:

- an omitted field is unchanged;
- the stored value (with `None` and `""` read as the same "none", as entitlement resolution reads them) is a no-op;
- anything else is refused with 400 for everyone, Super Admin included. That covers explicit null, the empty string, another organisation's package, an unknown package, and the spellings `packageId` and `package`.

**Other spellings.** Organisation and project spellings (`organisation_id`, `org_id`, `orgId`, `organization`, `projectId`, `project`) are refused the same way. Earlier review Low 4: some of them used to be dropped silently.

**Concurrency.** The service compares the stored scope with the scope the route authorised (`expected_scope` must name all three; anything else is a `TypeError`). It then writes compare-and-set on all three fields: a stale read or a concurrent package change is a 409, and nothing is written.

**Effects verified:**

- **Entitlement resolution:** identical before and after refused attempts. A direct write of the same change shows it would have moved the entitlement.
- **Subscription history and audit:** a refused attempt writes no `subscription.updated` audit event and no history entry. A permitted update's audit `after` keeps the package.
- **Invoices, billing records and service availability:** no package is ever changed by an update, so they read unchanged scope. Billing records carry organisation and project and never write the package.

**Tests:** `test_f3_subscription_package_scope_mongo.py` (5 tests, in CI). They cover the organisation, project and package boundaries, custom organisation roles, mixed project roles, invalid package references, and stale and concurrent writes. **Mutants: 5 of 5 killed** (spellings, route check, compare-and-set, service refusal, stale-read check). The L3 suite now passes the full scope.

### Package transfer: design only (not implemented; needs separate approval)

**Shape.** A dedicated `POST /subscriptions/{id}/transfer-package` with `{target_package_id, reason}`:

1. Step-up, with a new action `subscription.package.transfer`.
2. Authority in **both** the source scope and the target package's scope. This needs a new explicit permission, `subscription.package.transfer`, non-delegable and not covered by any alias.
3. The target package must exist, be active and belong to the subscription's organisation and project. No organisation or project change, ever.
4. A mandatory free-text reason.
5. One transaction: compare-and-set on the full stored scope, a `subscription_history` entry (`change_type: package_transfer`), and an audit event with before and after.
6. Entitlement re-evaluation: compute the effective services of the source and target scopes before and after. Refuse if the target scope already has an active subscription; that conflict is resolved by the owner, never silently.
7. Off by default behind a flag; never through the ordinary update.

### Remaining authorisation gaps (recorded, not changed)

| Gap | Severity | Note |
|---|---|---|
| `POST /subscriptions` and checkout authorise on the payload's organisation and project, not on the package at creation | Low, pre-existing | Package ownership is not modelled. Owner decision whether creation should validate it. |
| Entitlement lookup (`_active_subscription`) does not filter on `status` | Info, pre-existing | Status is checked downstream. Recorded for the authorised review. |
| M1: built-in roles are global (E1 to E5) | High (workstream) | Blocks DEF-4 activation |
| Unknown plan or add-on codes reseed defaults on read, unaudited | Low, pre-existing | Recorded |
| The client billing-catalogue route also admits `system:admin` (visibility only) | Low | Recorded |

### Independent review of `8eefa27` (agent; not the authorised review)

**Verdict:** no way found to change or clear a stored package on an existing subscription. F3 is sound, with no High finding. Probes ran against real MongoDB through the production app.

| # | Finding | Severity | Origin | Resolution |
|---|---|---|---|---|
| 1 | Case and separator variants (`package_ID`, `PackageId`, `ProjectID`, `Organization_Id`) were ignored with a 200 instead of refused. Nothing was written. | Low | Introduced | **Fixed:** keys are compared ignoring case, `_`, `-`, `.` and spaces. Tests added; the mutant is killed. |
| 2 | No test covered a stored package that is absent, `""` or an ObjectId. Behaviour was correct, but a compare-and-set built from normalised values would have broken updates silently. | Low (test gap) | Introduced with the tests | **Tests added:** `test_stored_package_shapes_missing_empty_and_objectid`. The normalised-filter mutant is killed. |
| 3 | Entitlement resolution (`_active_subscription`) ignores `status` and picks the most recently updated subscription. An ordinary update to a cancelled subscription in the same scope can switch a scope's entitlement off; a package row does not outrank a project row. | **Medium** | Pre-existing | **Owner decision.** Recommended: filter to `active`, `trial` and `pilot` (plus offboarding states if they must resolve), and order by specificity. |
| 4 | Package-only subscriptions never grant anything (no caller passes a package), but `/plan-settings` shows an active one as the scope's effective service. | Low/Info | Pre-existing | Recorded |
| 5 | Creation does not validate the package (packages are not modelled). | Low/Info | Pre-existing | Recorded |
| 6 | The `subscription.updated` audit event has no top-level `package_id` (it is in before/after) and writes `project_id` as `"None"`; `update_subscription` writes no history row. | Info | Pre-existing | Recorded |
| 7 | `payment_gateway_subscription_id` is writable through `PUT /subscriptions/{id}`. The webhook resolves a subscription by that id, so a manager who learns another subscription's gateway id could redirect its payment events, activation included. | **Medium (security)** | Pre-existing | **Owner decision, recommended before release:** treat it as protected, written only by checkout and the gateway. |

**CI:**

| Run | Head | Result |
|---|---|---|
| 44763971619 | `aa55a22` (F3 code) | Backend: 8,435 unit, 20 exhibit, 1,131 real-MongoDB. Frontend, dependency scan and Docker green. |
| 44765102151 | `f09d9be` (F3 review fixes) | Backend: 8,435 unit, 1,227 skipped, 4 xfailed, 20 exhibit, 1,132 real-MongoDB. Frontend: 1,015 unit, 78 mocked E2E. Dependency scan (56) and Docker green. |

Gitleaks failed in both runs, on the 2 known findings. Locally on `f09d9be`: 1,132 real-MongoDB passed, 0 failed, across 87 modules with Redis on.


## 14. Entitlement resolution (owner decision 2026-11-20)

**Commits:** `5740b06`; review fixes `53c78e6`; redesign after re-review `d37824e` (supersession withdrawn); second re-review fixes `b9ab8b3`; third review fixes `b51de70`, `6fc0705`.

**The defect.** `EntitlementService._active_subscription` took any subscription of the scope in its period, whatever its status, and preferred the most recently updated row. Two consequences:

- An ordinary edit of a cancelled subscription could switch a scope's entitlement off.
- A package row did not outrank a project row.

**The rule now** (`services/subscription_resolution.py`, one pure function used by enforcement and by the `/plan-settings` display):

- **Tiers:** package (when one is named), then project, then organisation. The first tier holding a *governing* subscription decides. `updated_at` is never consulted.
- **Status classes:**

| Class | Statuses | Effect |
|---|---|---|
| eligible | `active`, `pilot`, unexpired `trial` | Service per plan |
| restricted | `archive` (read-only), `offboarding` (export-only) | Semantics unchanged; no drafting |
| blocking | `past_due`, `paused` | Deny, **no fallback** (as before: such a project row already shadowed the organisation) |
| unknown | Any other status | Deny (fail closed) |
| invalid period | An unreadable `starts_at`, `ends_at` or trial end | Deny (fail closed) |
| not governing | `pending`, `cancelled`, expired trial, not started, ended | Never shadows anything |

- **Fallback, an explicit rule:** a cancelled or never-paid project subscription falls back to the organisation subscription. That is the meaning plan-settings "inherit" already gives a cancelled override.
- **Several governing subscriptions in one tier: the most recently activated governs.** `governing_since` is stamped when a row *becomes* governing: create, webhook activation from a non-governing status, an update into a governing status, and the plan-settings insert. An ordinary metadata edit never stamps it. Selection takes the latest `governing_since`; ties go to the larger id. Nothing is cancelled: a trial over a paid plan governs until it expires, then the paid plan governs again; a future-dated replacement governs from its start.
- **Ambiguity:** two or more governing rows in the deciding tier **none of which** carries `governing_since` (rows written before 2026-11-20) cannot be ordered safely. They deny (`ambiguous_subscriptions`) and are shown as "ambiguous". P-05 counts them.
- **Supersession withdrawn (`d37824e`).** `53c78e6` cancelled the older governing row on every activation. The re-review found that non-convergent under concurrency and harmful: a trial cancelled the paid plan it overlay, a refused or failed activation could still cancel, and cancellation is not reversible by the system (R1 to R3). It was removed; no row is cancelled by selection or by activation. The double billing an upgrade checkout can cause (old plan still active and billed) is an owner decision, recorded below, not a resolution concern.
- **Lifecycle changes only on the subscription enforcement selects (`b9ab8b3`, tightened in `b51de70`).** Upgrade, downgrade, billing-period change and add-on add/remove are refused with **409**, before the gateway is called, unless:
  - the row's status is `active`, `pilot` or `trial` (an upgrade no longer lifts `past_due`, `paused`, `archive` or `offboarding`: R6-4); and
  - `EntitlementService.resolve_subscription` - the same query and rule enforcement uses - selects this very row for its scope (outranked, ended, expired-trial, unorderable-tier and invisible rows are refused: N1, R6-2, R6-9). A row not yet started is judged at its start date (R6-1).

  The write is then compare-and-set on the status, plan, billing period, add-ons and `governing_since` the decision was made on, so a concurrent cancel or activation is refused, never revived or re-stamped (R6-3). The 409 names only a subscription of the same organisation and scope.
- **The same gates everywhere:**
  - `check_permission_entitlement`;
  - `effective_features`;
  - `get_subscription_summary`;
  - `assert_quota_available`, which background metering calls directly.
- **Evidence:** the `policy.authorize` audit metadata records the selected subscription, tier, class, reason and candidates. The candidate list is capped at 10, with a total.

**Data handling:**

- Dates are stored as BSON dates by create and update. Before, they were ISO strings, which the old date query never matched.
- A project id stored as an ObjectId is matched.
- Statuses are compared stripped.

**Tests:** `test_entitlement_resolution_mongo.py`, 38 tests. They cover:

- the review reproduction;
- precedence;
- every class and the fallback rule;
- periods, trial expiry and invalid periods;
- archive and offboarding;
- ambiguity;
- display parity;
- concurrent edits;
- the audit evidence;
- an upgrade checkout through a signed webhook (new plan governs, old one untouched);
- latest-activation ordering, ties, trial overlay and expiry, metadata edits not reordering;
- concurrent plan-settings writes converging on one governing row;
- the feature, summary and quota gates;
- ObjectId project ids and padded statuses;
- lifecycle changes on an outranked, cancelled, pending, ended, expired-trial, blocked, restricted, unorderable or invisible row refused before the gateway (service and HTTP 409); a scheduled row judged at its start;
- the decided-state compare-and-set, with the concurrent change landing before and after the write's own read;
- the P-05 reading, period-aware.

**Mutants:** 17 of 17 killed on `5740b06`/`53c78e6`; 9 of 9 on `b9ab8b3` (status guard, outranked check, unorderable check, scheduled-row allowance, guard placement in upgrade and add-on removal, the route's 409, P-05 period and stamp parity). 7 of 8 on `b51de70`/`6fc0705` (eligible-only status, start-date judgement, selected-must-be-own, outranked and ambiguous branches, the `now` pass-through, the compare-and-set filter); the survivor, the in-process pre-check before the filter, is equivalent (the filter carries the same values and raises the same 409).

**Independent review of `5740b06`:**

| # | Finding | Severity | Origin | Resolution |
|---|---|---|---|---|
| F1 | An upgrade checkout, create or reactivation left two governing rows, so the tier was locked out | High | Introduced (ambiguity rule) | **Fixed** in `d37824e` by latest-activation selection (the `53c78e6` supersession was withdrawn) |
| F2 | Concurrent plan-settings writes left several active rows | Medium | Race pre-existing; lockout new | **Fixed** |
| F3 | Unreadable dates read as unbounded; API dates stored as strings | Medium | Introduced / pre-existing | **Fixed** |
| F4 | Features, summary and quota ignored the status class | Medium | Pre-existing | **Fixed** |
| F5 | ObjectId project ids not matched by enforcement | Low | Pre-existing | **Fixed** |
| F6 | Status whitespace inconsistent | Low | Introduced | **Fixed** |
| F7 | The client profile card still picks the newest row | Low, display only | Pre-existing | Recorded |
| F8 | Uncapped audit candidates; evidence held on the instance | Low/Info | Introduced | Cap **fixed**; instance pattern recorded |

**Second independent re-review (of `d37824e`):** the read-side rule is sound and fixes R1 to R3 (10 rounds of concurrent activations, plan-settings writes and creates: no tier locked out, display equal to enforcement, nothing cancelled).

| # | Finding | Severity | Origin | Resolution |
|---|---|---|---|---|
| N1 | Plan, period and add-on changes on an outranked row changed the plan and the gateway price without changing entitlement | Medium | Introduced (`d37824e`) | **Fixed** in `b9ab8b3` (409 before the gateway) |
| N2 | Upgrading a cancelled or pending row revived it over the current plan | Low | Pre-existing, newly reachable | **Fixed** in `b9ab8b3` |
| N3 | `governing_since` comes from the application host clock; skew could order two near-simultaneous activations wrongly | Low | Introduced | Recorded (negligible with NTP; fix would stamp server time with `$$NOW`) |
| N4 | Cancelling the newest row brings back an older active one (which may still be billing); a trial overlays a paid plan for its duration | Low | By design | Recorded; listings should mark the governing row (with F7) |
| N5 | P-05 ambiguity count ignored periods and counted an unreadable stamp | Low | Introduced | **Fixed** in `b9ab8b3` |

**Third independent review (of `b9ab8b3`; in-memory probes, real MongoDB not used by the reviewer):**

| # | Finding | Severity | Origin | Resolution |
|---|---|---|---|---|
| R6-1 | A not-yet-started row stamped earlier than the current one could be re-priced, then never govern | Low-Medium | Introduced | **Fixed** `b51de70` (judged at its start) |
| R6-2 | Ended rows and expired trials whose own tier held nothing passed the guard | Low | Introduced (N1 residual) | **Fixed** `b51de70` (must be the selected row) |
| R6-3 | Check-then-act: a concurrent cancel was revived and re-stamped by the upgrade | Low-Medium | Introduced window | **Fixed** `b51de70` (decided-state compare-and-set); residual below |
| R6-4 | Upgrade set `active` on `past_due`, `paused`, `archive`, `offboarding` without payment | Medium | Pre-existing | **Fixed** `b51de70` (eligible statuses only) |
| R6-5 | `convert_trial` converts an expired or outranked trial to paid `active` with no payment and unvalidated add-ons | Low-Medium | Pre-existing | **Open: owner decision** (route conversion through checkout) |
| R6-6 | `reactivate_subscription` ignores the period and outranks the current plan; both gateway subscriptions bill | Low | Pre-existing | **Open: owner decision** (with the double-billing decision) |
| R6-7 | Plan settings cancel rows locally without `_gateway_cancel`, so the old gateway subscription keeps billing | Low | Pre-existing | **Open: owner decision** |
| R6-8 | `PUT /subscriptions/{id}` changes plan, period and add-ons with no guard and no gateway call | Info | Pre-existing | **Open** (admin override; refuse plan fields there or apply the guard) |
| R6-9 | Organisation stored as an ObjectId: the guard saw no tier-mates | Low | Introduced | **Fixed** `b51de70` |

**Residual of R6-3:** the gateway is changed before the local write (so a gateway failure leaves local state untouched). A concurrent change landing between the two is refused locally after the gateway price has moved; that pair needs manual reconciliation. Not reversible automatically; recorded for the re-billing design.

**Production impact:**

- **Two or more governing rows in one tier with none stamped (data already in production):** denied (`ambiguous_subscriptions`) until a person resolves them, or until one of them is re-activated and so stamped. Where at least one is stamped, the latest governs.
- **Plan, period or add-on changes aimed at a non-governing, blocked or restricted row** now answer 409 instead of succeeding silently (a past-due customer must settle before upgrading; an archived or offboarding scope cannot be upgraded back to service without reactivation). A client or operator script that upgraded an old row must target the governing one (listed in the policy audit evidence) or reactivate first.
- **A past-due or paused project:** still denied, without falling back (unchanged).
- **Rows with string or unreadable dates:** now governed by their real dates, or denied.

P-05 `subscription_security_impact` counts each case. **Read it before release.**

**CI (§14 and §15 code):** run 44791457632 on `0cb99f0` (final code): backend 8,436 passed; real MongoDB 1,196 passed across 89 modules, none skipped; frontend, dependency scan and Docker passed; Gitleaks red on the 2 known findings. Local quiet worktree at `6fc0705`: real MongoDB 1,196 passed; backend 9,616 passed, 0 failed. Not security approval.

## 15. Gateway identity protection (owner decision 2026-11-20, immediate)

**Commits:** `da322ae`, review fixes `7cabfdf`, re-review fixes `f64ac9f`.

**The defect.** The webhook finds a payment event's subscription by `payment_gateway_subscription_id`. An entitlement manager could write that id through `PUT /subscriptions/{id}`, and also through `POST /subscriptions`, a second path found in the writer audit. A tenant user who learned another subscription's gateway id could therefore redirect its payment events, activation included.

**Writers after the fix:**

| Writer | Behaviour now |
|---|---|
| Checkout | The only binder, from the gateway's response. Records provenance (`payment_gateway_bound_by`, `payment_gateway_bound_at`). Refuses an id already bound (409). No-op gateway ids are unique per call. |
| Ordinary create and update | Refuse to set, replace or clear the gateway subscription id, customer id or provider, for everyone. Any unrecognised key is refused (400), never dropped. |
| Lifecycle service, plan settings, migrations, seeds | Never write it |

**Webhook, in order:**

1. The provider path is canonicalised and allowlisted (otherwise 404).
2. The signature is verified (deny by default).
3. Idempotency on the event id **and** on a SHA-256 of the signed body, so a replay under a new event-id header is a duplicate.
4. The gateway id must name exactly one subscription, of this provider, bound by checkout or legacy.
5. The event's customer and the checkout notes' organisation and project must match where both are present.
6. **Order:** an event older than the last applied (with no high-water mark yet, the last processed event of that gateway id stands in) does not change status.
7. **Activation allowlist:** a webhook sets `active` only from `pending`, `trial`, `active` or `past_due`.
8. **One gateway payment, one effect.**

Refusals are audited. A refused **payment** is still written to `billing_records` as `refused_<reason>` with `requires_admin_review`, so the financial trail is never lost. A refused event keeps a redacted payload (identity, amount, status, notes; no contact, card or VPA data).

**Migration `20261124_0001` (registered, not run in any environment).** Partial unique indexes on:

- `subscriptions.payment_gateway_subscription_id`;
- `billing_webhook_events` (provider, body hash);
- `billing_records` (provider, gateway payment id, event type).

With duplicates present, a dry run warns (so `--fail-on-warning` stops a deploy) and an apply raises: nothing is recorded, and it stays pending. **It blocks later migrations until duplicates are resolved** (re-review R5). Run the dry run per environment first; no-op environments with pre-fix deterministic ids will report duplicates.

**Tests:** `test_gateway_identity_protection_mongo.py`, 26 tests. They cover:

- HTTP refusals across spellings, roles and Super Admin;
- that the victim's events land only on the victim;
- ambiguous, foreign and non-checkout bindings;
- signature and replay, including a case-changed provider path;
- stale and out-of-order events, and the activation status matrix;
- refused payments recorded;
- the payment dedupe;
- customer and notes ownership;
- unknown keys;
- redaction;
- checkout uniqueness, and a concurrent race with the index;
- the migration through the runner.

**Mutants:** 20 of 21 killed (9 on `da322ae`, 8 on `7cabfdf`, 4 on `f64ac9f`). The survivor is equivalent: the service refuses the same change with the same 400.

**Independent reviews:**

| # | Finding | Severity | Origin | Resolution |
|---|---|---|---|---|
| F1 | A mixed-case provider path replayed a signed body | Medium-High | Introduced | **Fixed** |
| F2 | Events applied without order | Medium | Pre-existing | **Fixed** (order, activation allowlist, payment dedupe) |
| F3 | A migration with duplicates was recorded as applied without its index | Medium | Introduced | **Fixed** (raises) |
| F4 | Webhook ownership was not checked | Low-Medium | Pre-existing | **Fixed** (customer, notes) |
| F5 | Exotic key spellings were dropped | Low | Introduced | **Fixed** (unknown keys refused) |
| F6 | Unknown provider returned 500; refused events were lost | Low | Pre-existing | **Fixed** (404; payload kept) |
| R1 | Refused payments left no billing record | Medium | Introduced | **Fixed** |
| R2 | Payments re-activated paused, offboarding and archived subscriptions | Medium | Pre-existing | **Fixed** (allowlist) |
| R3 | No order baseline before the first new event; dedupe could race | Low | Introduced | **Fixed** (baseline, unique index) |
| R4 | Refused payload kept PII | Low | Introduced | **Fixed** (redacted) |
| R5 | The migration blocks later migrations while duplicates exist | Info | Intended | Documented |

**Legacy bindings.** Bindings without provenance were written before the fix, possibly through the old `PUT`. They are still trusted by the webhook. P-05 counts them. Before release, classify each from the audit trail (`subscription.checkout_started`: checkout; `subscription.created` or `updated`: unverified) and refuse the unverified ones. That is an owner decision.

**Gateway re-binding: design only. Not implemented; needs separate authorisation.**

- **Endpoint:** `POST /subscriptions/{id}/gateway-binding` with `{gateway_subscription_id, reason}`.
- **Authority:** a new non-delegable permission, `billing.gateway.rebind`, held by Super Admin or a platform billing operator only. Never a tenant role.
- **Step-up:** action `billing.gateway.rebind`.
- **Before binding, the target id must be:**
  - fetched from the gateway and shown to belong to this subscription's customer and organisation (notes);
  - unbound elsewhere (the unique index).
- **In one transaction:**
  - compare-and-set on the old binding;
  - set the new binding with `payment_gateway_bound_by: "rebind"` and an approval reference;
  - write a history row and an audit event with before and after and the reason.
- **Afterwards:** a webhook sanity probe, and admin review of refused events for the old id.
- **Second person:** the change is approved by someone other than the requester (four-eyes).

## 16. Subscription lifecycle and payment integrity: R6-5 to R6-8 (owner decision 2026-11-21)

**Status, kept separate:** implemented and locally verified on the branch; CI on the exact head is recorded in the ticket register; independent cold review recorded below; **not security-approved, not production-accepted**. Nothing was deployed, migrated, re-granted, rebound, refunded or cancelled at any gateway.

Each fix was reproduced on real MongoDB at the commit before it (outputs kept with the session's evidence), then fixed in its own commit.

### 16.1 R6-8 - `PUT /subscriptions/{id}` (`1b731f6`)

- **Defect (reproduced at `9e1a6bb`):** Super Admin, a tenant billing administrator and a custom role with `subscription.entitlement.manage` could change plan, period, add-ons, status (reviving a cancelled subscription), `billing_status` ("paid" without payment), dates, trial and renewal flags with 200 and no gateway call; a tenant billing administrator could grant `entitlement_overrides`.
- **Allowlist now:** `admin_note` (new, non-commercial), `cancellation_reason`; `entitlement_overrides` for Super Admin only.
- **Refused with 400, naming the workflow:** `plan_code`, `billing_period`, `active_add_ons`, `status`, `billing_status`, `starts_at`, `ends_at`, `current_period_start/end`, `trial`, `trial_ends_at`, `pilot`, `auto_renew`, `cancelled_at`, `governing_since` - under any spelling (`planCode`, `Billing-Status`, `activatedAt` ...), even unchanged, even beside an allowed field. Scope (L3/F3) and gateway identity (§15) refusals, step-up, authorisation, compare-and-set and audit are unchanged. The service-level update is unchanged; no client uses the route.
- **Tests:** `test_r6_8_subscription_update_allowlist_mongo.py` (5; four personas, cross-organisation, aliases, concurrency); 6/6 mutants killed.

### 16.2 R6-5 - trial conversion (`4a3e6ce`)

- **Defect (reproduced at `1b731f6`):** any row whose status read `trial` - expired or outranked included - became `active` with `billing_status=active`, unvalidated add-ons, no payment and no gateway call; a tenant billing administrator could do it.
- **Rules now:**
  - only a running trial (status `trial`, period eligible) that enforcement selects for its scope converts (else 409, unchanged);
  - plan active, period supported, add-ons existing, active, compatible, named once (400);
  - **priced plan:** a checkout for the trial's scope (`converts_trial_id`); the trial keeps governing; the signed webhook's payment confirmation activates the paid subscription, which then governs, and closes the trial (compare-and-set, replay-safe, history, audit). One checkout per trial (claim by compare-and-set; repeats return the same checkout); a gateway failure marks the claim failed (audited, 502) and can be retried. Priced add-ons are refused here (checkout does not charge them);
  - **free plan:** converted in place with `billing_status = "not_billed"`;
  - **no-charge conversion of a priced plan:** Super Admin only (authority separate from trial management), reason of at least 10 characters, `billing_status = "no_charge_override"`, audited with the list price;
  - `billing_status = "active"` is set only by payment confirmation.
- **Tests:** `test_r6_5_trial_conversion_mongo.py` (11); 14/15 mutants killed, the survivor equivalent (the claim compare-and-set answers a repeat the same way).

### 16.3 R6-6 - reactivation (`58455e7`)

- **Defect (reproduced at `4a3e6ce`):** any `cancelled` row became active with `billing_status=active` - an immediately cancelled one (period over) and an old plan while a replacement governed and billed included, re-stamped to outrank the new plan; six concurrent requests each resumed the gateway; a gateway failure was 500.
- **Rules now** (before the gateway is asked): only `cancelled`, never a trial closed by its paid conversion; paid period recorded, readable and not over (else "start a new purchase"); nothing else governs its scope and no replacement checkout is pending there; one reactivation at a time (claim); a gateway-bound subscription resumes only when the gateway confirms an active subscription (failure 502, other status 409, both audited for review); unbound ones resume as `billing_status = "manual"`.
- **Older subscriptions:** one never cancelled still governs when the newer one is cancelled (it never stopped billing - P-05 counts the overlap); a cancelled one resumes only through this operation.
- **Tests:** `test_r6_6_reactivation_mongo.py` (8); 12/12 mutants killed.

### 16.4 R6-7 - plan-settings cancellation (`5f95334`)

- **Defect (reproduced at `58455e7`):** the scope's subscriptions were cancelled with one local `update_many`; the gateway was never told (a paid gateway subscription kept billing); a gateway outage still answered 200; nothing recorded the intent.
- **Durable operation** (`services/subscription_operations.py`, `subscription_operations` and `subscription_operation_locks`; uniqueness by `_id`, so no migration):
  1. intent recorded first under the `Idempotency-Key` (a completed operation is not repeated; the same key with another request is 409); scope leased (a concurrent change is 409);
  2. affected subscriptions listed;
  3. each gateway-bound one cancelled through the provider API at the end of the paid cycle (no further charge, no refund);
  4. result per subscription: confirmed / failed (exception class) / not bound;
  5. local cancellation only where confirmed or unbound, compare-and-set on status (changed meanwhile: left alone, flagged);
  6. anything unresolved: no replacement installed, `needs_review`, audited, 502 with the operation id; a retry continues and never repeats a confirmed call;
  7. `GET /plan-settings/operations` and `POST /plan-settings/operations/{id}/retry` (Super Admin, step-up, scope authorisation), including an operation interrupted mid-run (lapsed lease).
- The replacement has an operation-derived id (installed once) and `billing_status = "plan_settings"`. Gateway cancellation is never marked complete because a MongoDB update succeeded.
- **Tests:** `test_r6_7_plan_settings_cancellation_mongo.py` (7); 14/14 mutants killed.

### 16.5 Gateway/database consistency for lifecycle changes (`80186b7`)

Upgrade, downgrade, period change and cancel change the gateway first and the database second. Each is now a durable operation recorded before the gateway call with the exact subscription, the state before and the target:

- gateway failure: `failed` (nothing changed anywhere), 502, retryable, not a review item;
- database write refused after a gateway change (R6-3 residual): `needs_review`, listed and audited, never reported as done;
- `Idempotency-Key`: a repeat of a completed change answers without reaching the gateway; another request under the same key is 409; a retry after review does not repeat the confirmed call.

No cross-system atomicity is claimed. **Tests:** `test_gateway_db_consistency_mongo.py` (5); 10/10 mutants killed.

### 16.6 Legacy gateway bindings (P-05 `payment_integrity_impact`, `a4cd266`)

Read-only counts by organisation; no identifiers leave the reading; nothing is trusted, rebound, deleted or disabled by it.

| Class | Rule | Proposed remediation (needs owner approval; not implemented) | Rollback | Financial reconciliation |
|---|---|---|---|---|
| verified_checkout | bound by checkout and a `checkout_started` audit event names it | none | - | none |
| verified_provider_reconciliation | a person recorded `payment_gateway_reconciled_at` after checking the provider | none | - | as recorded |
| conflicting | id bound twice, recorded as bound outside checkout, or written by an ordinary create/update | per binding: a person compares the provider's customer, notes and payments with each candidate; the wrong binding is removed through the re-binding workflow (§15, four-eyes); the webhook already refuses these | restore the binding from the audit `before` | payments since the binding are re-attributed by a person; no automatic refund |
| unverified | none of the above | provider reconciliation per binding, then record `payment_gateway_reconciled_at`; until then optionally refuse webhook activation for them (a flag, owner decision) | clear the flag | none until reconciled |

Also counted: duplicate payment groups (the migration's unique index refuses to build over them: resolve first), payments refused for review, overlapping governing scopes and gateway-bound overlaps, cancelled gateway-bound subscriptions without gateway evidence (pre-R6-7 plan-settings cancellations: check the provider - they may still bill), governing rows without activation dates, open operations, conversions awaiting payment, no-charge conversions.

### 16.7 Checkout replacement safeguards - design only, needs separate owner authorisation

Two subscriptions can bill one entitlement scope when a checkout buys a new plan while the old one keeps billing (the overlap P-05 counts). The safeguard, reviewed across checkout, upgrade, downgrade, cancellation and webhook flows:

- **Exact identity:** checkout takes `replaces_subscription_id`; it must name a subscription of the same organisation, project and package that enforcement selects now and that is gateway-bound or explicitly unbound. Anything else is refused - an unrelated subscription is never touched.
- **Durable operation** (the §16.4 store): `replacement` with an idempotency key, the source and target ids, before/after states.
- **Order:** the target is created pending; on the signed payment confirmation of the target, the source is cancelled at the gateway at the end of its paid cycle, the result recorded, and only then cancelled locally (compare-and-set on its state). A gateway failure or a changed source: `needs_review`, never auto-retried into a refund.
- **Retry:** idempotent per step; confirmed gateway calls never repeated; lease for crash recovery.
- **Reconciliation:** a periodic read-only comparison of provider status with local status for bound subscriptions, producing review items (no automatic writes).
- **Never:** automatic refunds, cancellation of a subscription not named, cross-organisation replacement, claims of atomicity.
- **Not implemented:** no replacement or re-binding endpoint was added.

### 16.8 Independent cold review of the R6 commits (review 7) and its fixes

The reviewer was given the code, not these conclusions. Within their own routes the five commits held (100 tests passed), but sibling routes still let a tenant billing administrator - who holds `subscription.entitlement.manage`, which the policy accepts for every `subscription.*` action - obtain paid service without payment. Every finding below was reproduced on real MongoDB at the commit before its fix (outputs kept with the session's evidence) and fixed in its own commit.

| # | Finding | Severity | Origin | Fix |
|---|---|---|---|---|
| F1 | `POST /subscriptions` created an active, "paid" enterprise subscription to 2099 with add-ons and overrides | Critical | Pre-existing | `53ed2a6`: Super Admin only (no client uses it); plan validated; `billing_status=active` refused (default `manual`) |
| F2 | Upgrade made a running trial `active` / `billing_status=active` | High | Pre-existing | `53ed2a6`: plan, period and add-on changes refuse trials; upgrade writes no status or billing status |
| F3 | A priced add-on was added and never charged | High | Pre-existing | `53ed2a6`: priced add-ons only by Super Admin, as manually invoiced, audited for review |
| F4 | 100-year trials, repeatedly, over an existing subscription | High | Pre-existing | `53ed2a6`: capped at the plan's trial length (Super Admin may extend, audited); one trial per scope; never over a governing subscription |
| R8-1 / R6-1 | A converted (or cancelled) trial became reactivatable after `PUT` rewrote `cancellation_reason` | Medium | Introduced (R6-6 with R6-8) | `b7165f0`: a trial is never reactivated, decided on fields `PUT` cannot write |
| R5-1 | Conversion plus checkout for one scope: both activate, both bill | Medium | Introduced | `b7165f0`: checkout and conversion refuse while a bound purchase for the scope awaits payment |
| R5-2 / R6-2 | A crashed "starting" claim blocked conversion or reactivation for ever; a created checkout was orphaned | Low | Introduced | `b7165f0`: claims older than 10 minutes are taken over; the orphan checkout is adopted |
| R5-3 | An unpriced period sold at the monthly price | Low | Pre-existing | `b7165f0`: only periods the plan prices |
| R6-3 | A replacement activated during the gateway resume was not re-checked | Low | Introduced | `b7165f0`: re-check after the resume; refusal audited for reconciliation |
| R7-1 | Leases never renewed: a slow operation could lose its scope and keep calling the gateway (4 cancels for 2 subscriptions) | Medium | Introduced | `b7d59a9`: renewal before every item, fenced on holding the scope; a loser stops and is listed |
| R7-2 | A row changed meanwhile stayed unresolved for ever | Low | Introduced | `b7d59a9`: re-decided on retry from its current state |
| R7-3 | Retrying a lifecycle operation through the plan-settings route was a 500 | Low | Introduced | `b7d59a9`: 409 with guidance |
| R7-4 | Operation ids of different kinds shared one hash namespace | Info | Introduced | `b7d59a9`: kind in the hash |
| R7-5 / G-2 | The client sent a fresh key per call, so a retry started a new operation | Low | Introduced | `b7d59a9`: one key per change until it succeeds |
| G-1 | Lifecycle changes did not take the scope lease | Low | Introduced | `b7d59a9`: they do |
| G-3 | A gateway timeout was recorded as "nothing changed" | Low | Introduced | `b7d59a9`: an unknown outcome is listed, audited and retried by the same key |

Tests: `test_payment_bypass_closure_mongo.py` (5), `test_lifecycle_review7_mongo.py` (6), and additions to the R6-7 and consistency suites; mutants 12/12, 10/10 and 8/8. Two earlier tests changed for stated reasons: a checkout test needed its own scope (R5-1), and an upgrade-by-checkout webhook now carries the plan's real price (the money check had been silently skipped because no plan was seeded).
