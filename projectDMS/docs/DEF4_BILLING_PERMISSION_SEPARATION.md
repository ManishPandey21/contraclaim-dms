# DEF-4: separating billing and subscription visibility from organisation and report viewing

Status: **design and migration plan only** (conditional owner approval, 2026-11-19). Nothing here changes code, roles, aliases, or production behaviour. The production aliases stay until all of the following hold:

- P-05 has quantified the impact;
- the authorised security review is complete;
- a separate release decision is recorded.

## 1. Today (observed, pinned by tests)

Evidence: `backend/rbac_backend/tests/integration/test_def4_alias_compatibility_mongo.py` (real HTTP) and `core/permissions.py::equivalent_permissions`.

| Held name | Built-in holders | Billing and subscription checks it passes by alias |
|---|---|---|
| `organizations:read` | superadmin, orgadmin, orguser, contractmgr_org, projectadmin, projectuser | `billing.plan.view`, `billing.invoice.view`, `billing.invoice.download`, `subscription.history.view` |
| `orgs:view` | none built-in (legacy spelling) | same as `organizations:read` |
| `reports:view` | reporter | `subscription.usage.view` |

**Effect.** An ordinary organisation or project member can read their own organisation's:

- billing records (`/api/rbac-monetization/billing/records/{org}`);
- billing review queue (`/api/rbac-monetization/billing/review-queue/{org}`);
- subscription history (`/api/rbac-monetization/billing/history/{org}`);
- invoice preview and plan list.

They are never given another organisation's (403).

**Not permission-gated at all (review, 2026-11-19).** `GET /api/rbac-monetization/subscriptions` and `GET /plan-settings` are scoped by membership and role name, not by any billing permission (`routers/rbac_monetization.py::_subscription_scope_filters`):

- `ORG_SUBSCRIPTION_VIEW_ROLES` includes orguser, reporter, doccontroller, settings_manager and contractmgr_org;
- `PROJECT_SUBSCRIPTION_VIEW_ROLES` includes projectuser.

Removing the aliases alone does not end ordinary members' subscription visibility; these routes must be re-gated too (step 4). Also gated by `billing.plan.view`: `/billing/summary/{org}`.

**Already in place.** None of the three names can be put on a role by anyone but Super Admin (403; SEC-2/DEF-4 fix).

**Client.** Since T-2 the client no longer expands aliases; it shows exactly the server's effective list, which today still includes the billing names for holders of `organizations:read`. Its alias table (`client/src/config/rolePermissions.ts::PERMISSION_ALIASES`) is now unused in production: only a sidebar test uses it. Removing it changes no client behaviour.

## 2. Target design

**Principle:** ordinary organisation or report viewing never implies any billing or subscription permission. Each billing or subscription view is its own permission and is granted explicitly.

### 2.1 Permissions

| Permission | Meaning | Exists today |
|---|---|---|
| `organizations:read` / `orgs:view` | read the organisation's profile and membership listing | yes (aliases change, see below) |
| `dms.report.view` / `reports:view` | read operational reports | yes (aliases change) |
| `billing.plan.view` | read the plan catalogue and the organisation's current plan | yes |
| `billing.invoice.view`, `billing.invoice.download` | read and download invoices | yes |
| `subscription.history.view` | read subscription history | yes |
| `subscription.usage.view` | read metered usage | yes |
| `billing.record.view` (new) | read billing records and the review queue (today gated as `billing.plan.view`) | no |

Alias changes in the target (`core/permissions.py` and the client mirror), applied together:

- `organizations:read` and `orgs:view`: remove `billing.plan.view`, `billing.invoice.view`, `billing.invoice.download`, `subscription.history.view`. Keep `dms.contract.catalogue.browse` (a separate review item, not billing).
- `reports:view`: remove `subscription.usage.view`. Keep `dms.report.view`.

Route changes:

- `/billing/records/{org}`, `/billing/review-queue/{org}` and the receipt download: gate on `billing.record.view` (receipts on `billing.invoice.download`) instead of `billing.plan.view`.
- `/billing/summary/{org}`: gate on `billing.record.view`.
- `/subscriptions` and `/plan-settings`: replace the role-name scoping (`ORG_/PROJECT_SUBSCRIPTION_VIEW_ROLES`) with an explicit `subscription.history.view` / `billing.plan.view` check in the named scope.
- `/plans` (catalogue, `billing_plan_view` allowance in `PolicyService`) stays on `billing.plan.view`.
- Wiring for `billing.record.view`:
  - add it to `CANONICAL_PERMISSIONS` (catalogue);
  - make it non-delegable like the other billing names (`role_service._ensure_permissions_assignable` refuses billing and subscription names for anyone but Super Admin);
  - give it the billing domain in `PolicyService` (`permission_domain`);
  - add it to the alias contract tests.

### 2.2 Who holds what (proposed)

| Role | Billing and subscription grants in the target |
|---|---|
| superadmin | all (unchanged; Super Admin bypass) |
| *(note)* | Today `orgadmin` holds **no** billing or subscription permission explicitly: everything it sees comes through the `organizations:read` alias. `contraclaim_billing_admin` holds `billing.plan.manage`, `billing.plan.view`, `subscription.archive_access`, `subscription.entitlement.manage`, `subscription.offboarding_export`, `subscription.usage.view`. Step 3 below must therefore grant orgadmin explicitly before any alias is removed. |
| orgadmin | `billing.plan.view`, `billing.invoice.view`, `billing.invoice.download`, `billing.record.view`, `subscription.history.view`, `subscription.usage.view` |
| contraclaim_billing_admin | the same, plus its existing manage permissions |
| orguser, projectadmin, projectuser, contractmgr_org, reporter | none |
| custom roles | only what is put on them explicitly. Billing and subscription names, and anything satisfying them through an alias, are already non-delegable for everyone but Super Admin (`role_service._ensure_permissions_assignable`), so step 3 re-grants on custom roles are Super Admin actions (or an approved migration). |

## 3. Migration plan (each step separately approved; none run yet)

1. **Measure (P-05 extension, read-only).** For each organisation, count:
   - users who today reach each billing route only through `organizations:read` / `orgs:view` / `reports:view`, split by role type (built-in or custom);
   - custom roles that hold those names;
   - whether any role already holds the explicit billing names.

   No PII, as with P-05. Add the counts to `scripts/claim_arbitration_readonly_readings.py` and run it in production read-only.
2. **Decide.** Record the per-organisation impact and the release decision: who keeps billing visibility (default: orgadmin and billing admin only). Get security review sign-off.
3. **Grant before revoking (forward migration, idempotent).**
   - Add the explicit billing names to the built-in roles in the target table (`initial_data/default_roles.py` and a migration that updates stored built-in role documents).
   - For every custom role or user that the decision says must keep billing visibility, add the explicit names through an audited, scoped re-grant: the role API with step-up, or an approved migration listing role ids, never a blanket grant.
   - After this step nothing has changed for anyone: the aliases still apply.
4. **Introduce `billing.record.view` and re-gate every billing and subscription read** behind a setting (`BILLING_EXPLICIT_PERMISSIONS`, default off; with it off, behaviour is unchanged):
   - records, review queue, receipt and `/billing/summary/{org}`: `billing.record.view` (receipts `billing.invoice.download`);
   - `/subscriptions` and `/plan-settings`: explicit `subscription.history.view` / `billing.plan.view` in the named scope instead of `ORG_/PROJECT_SUBSCRIPTION_VIEW_ROLES`. **Decide first:** this removes the project-level subscription visibility projectadmin and projectuser have today, and the target table gives them nothing;
   - `/plan-settings/effective-services`: role-scoped today and possibly read for feature gating. **Decide** whether it stays role-scoped (it shows which services are on, not billing data) or is re-gated.

   Route tests both ways (setting off: today's behaviour; on: the target table).
5. **Remove the aliases (the breaking step).** One change set covering:
   - the server alias tables;
   - the now-unused client alias table (cleanup only);
   - flipping the setting on;
   - updating `test_def4_alias_compatibility_mongo.py`, so the "ordinary members read billing" test becomes "ordinary members are refused";
   - the alias contract tests.

   Staging first (two consecutive deployed runs), then production in a release window.
6. **Rollback.** Planned as a release, not a toggle:
   - the alias groups are code constants, so restoring them is a code revert and redeploy;
   - only the route re-gating from step 4 sits behind the setting.

   The explicit grants from step 3 stay; they are additive and harmless. No data migration needs reversing.

## 4. Tests required before step 5

- Real HTTP, per route (records, review queue, receipt, summary, history, `/subscriptions`, `/plan-settings`, and `/plan-settings/effective-services` as decided), for each role in the target table: allowed or refused, own and foreign organisation.
- Effective-permission agreement (`test_effective_permissions_mongo.py`) with the new alias groups: the advertised list drops the billing names for ordinary members.
- Client: billing pages hidden for ordinary members (server list without billing names) and shown for orgadmin and billing admin.
- Alias contract (`test_permission_alias_contract.py`): no billing name reachable from `organizations:read`, `orgs:view` or `reports:view`.
- Delegation: a non-holder cannot put the new names on a role.

## 5. Dependencies

- **Before steps 3 and 5:** P-05 (and its extension in step 1), the authorised security review, and a recorded release decision.
- **Before step 5:** two consecutive staging E2E passes.
- **Does not depend on, and does not change:** DEF-5, the claim and arbitration rollout, and skill publication.
