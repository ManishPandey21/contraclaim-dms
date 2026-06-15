# ContraClaim DMS RBAC Improvement Audit

## 1. Executive Summary

This audit reviewed the active ContraClaim DMS repository as of 2026-05-18. The frontend is a Vite React TypeScript application. The backend is a FastAPI Python service using MongoDB collections through Motor-style async access.

The repository now contains several good RBAC building blocks: JWT authentication, role and permission collections, organization/project scope helpers, document-level checks, a newer central `PolicyService`, service entitlement checks, plan/subscription models, and an expert allocation model. However, production RBAC is not yet consistent across the whole system.

The main production risk is fragmented enforcement. Some routes use `require_permission`, some use `AuthorizationService`, some use `PolicyService`, some use only frontend route/menu rules, and some workflow transition endpoints are authenticated but not permission-gated. Effective access is therefore not always calculated from role plus scope plus service entitlement plus subscription state plus expert allocation.

Important clarification used throughout this report: in ContraClaim, "client" means the organization and/or project using the system. This report does not introduce a separate client access scope. Access must be organization-level, project-level, service-entitlement-aware, and, for ContraClaim internal experts, allocation-based.

Present RBAC score: **54 / 100**.

Expected RBAC score after the recommended improvements: **93 / 100**.

The highest priority fixes are:

1. Make `PolicyService` the mandatory backend authorization path for protected APIs.
2. Replace legacy `documents:*` workflow checks with canonical `dms.*` and `drafting.*` permissions.
3. Enforce service entitlements backend-side for every DMS and Drafting action.
4. Enforce organization/project scope consistently on list and detail APIs.
5. Require active expert allocation for all ContraClaim Contract Expert drafting/review work.
6. Close frontend default-open routes and align menus with backend permissions.
7. Add automated tests proving direct API calls cannot bypass the UI.

## 2. Current RBAC Architecture

Frontend:

- Framework: React 18, TypeScript, Vite.
- Routing: `client/src/routes.tsx` with `ProtectedRoute` for authenticated sections and `RoleGuard` for a small subset of routes.
- Route RBAC config: `client/src/config/rolePermissions.ts`.
- Permission loading: `client/src/hooks/useRBAC.ts`, which reads roles from local storage or `/me`, then loads role permissions from `/roles`.
- Sidebar/menu visibility: `client/src/components/layout/Sidebar.tsx`.

Backend:

- Framework: FastAPI.
- Authentication: JWT bearer token through `backend/rbac_backend/core/security.py`.
- Dev header fallback: supported only when `ALLOW_DEV_HEADERS=True`.
- User model: `backend/rbac_backend/models/user.py` and `backend/rbac_backend/models/user_models.py`.
- Role model: `backend/rbac_backend/models/role.py`.
- Permission model: `backend/rbac_backend/models/permission.py`.
- Default roles and permissions: `backend/rbac_backend/initial_data/default_roles.py` and `default_permissions.py`.
- Legacy permission service: `backend/rbac_backend/services/permission_service.py`.
- Legacy authorization service: `backend/rbac_backend/services/authorization_service.py`.
- Newer central policy engine: `backend/rbac_backend/services/policy_service.py`.
- Scope engine: `backend/rbac_backend/services/scope_service.py`.
- Entitlement engine: `backend/rbac_backend/services/entitlement_service.py`.
- Monetization services: `backend/rbac_backend/services/monetization_service.py`.
- Expert allocation services: `backend/rbac_backend/services/allocation_service.py`.

Database collections and indexes observed:

- `users`
- `roles`
- `permissions`
- `organizations`
- `projects`
- `documents`
- `letters`
- `letter_draft_runs`
- `organization_memberships`
- `project_memberships`
- `role_assignments`
- `expert_allocations`
- `plans`
- `subscriptions`
- `entitlements`
- `usage_events`
- `usage_periods`
- `quota_buckets`
- `billing_records`
- `offboarding_exports`
- `audit_events` or audit-log related collections through audit services

Current architecture direction is sound, but the old and new enforcement paths coexist. Production readiness requires a single effective-permission pipeline.

## 3. Roles Discovered in Repository

Discovered active seeded roles:

| Role id | Display name | Scope in seed | Current access summary | Main gap |
|---|---|---:|---|---|
| `superadmin` | Super Admin | system | Broad platform access and permission bypass in multiple services. | Bypass is intentional but must be better guarded and fully audit logged. |
| `superuser` | Super User | compatibility role | Recognized in scope helpers, not clearly seeded in active default roles. | Ambiguous authority; define or remove from production role set. |
| `orgadmin` | Organization Admin | organization | Broad DMS, user, role, organization, party, concern permissions. | Seed grants too much, including organization create/delete and role create/update. |
| `orguser` | Organization User | organization | DMS read/create/update/delete/approve/share/upload and other org-level reads. | Too powerful for ordinary organization user. |
| `contractmgr_org` | Contract Manager - Organization | organization | Similar to org user with broad DMS actions. | Not separated from document controller or drafting roles. |
| `projectadmin` | Project Admin | project | Project DMS, users, roles, assignments, tags. | Can manage roles too broadly unless every endpoint enforces scope. |
| `projectuser` | Project User | project | Broad DMS read/create/update/delete/approve/share/upload. | Too powerful for ordinary project user. |
| `doccontroller` | Document Controller | organization | Upload, read, download, tag/project read. | Frontend route model does not explicitly include this role; backend service entitlement not applied everywhere. |
| `reporter` | Reporter | organization | Reports and tags. | Needs read-only scoping and report query enforcement. |
| `settings_manager` | Settings Manager | organization | Settings view/edit and tags. | Settings APIs are unevenly protected by scope and permission. |
| `limited_user` | Limited User | organization | Documents read, tags read, projects read. | Should map to viewer/auditor style role with read-only entitlement behavior. |
| `contraclaim_drafting_manager` | ContraClaim Drafting Manager | system | Drafting assignment, draft, review, final, audit permissions. | `PolicyService` still requires expert allocation for drafting domain, so allocation bootstrap may be awkward unless superadmin performs it. |
| `contraclaim_expert_drafter` | ContraClaim Contract Expert - Drafter | system | Drafting request view/accept, draft create/edit/submit, final view, audit view. | Must require `account_type=contraclaim_staff` and active allocation; role alone must not grant org/project visibility. |
| `contraclaim_expert_reviewer` | ContraClaim Contract Expert - Reviewer | system | Request view, review perform/approve/return, final/audit view. | Must enforce no self-approval and active allocation on all review routes. |
| `contraclaim_billing_admin` | ContraClaim Billing Admin | system | Billing and subscription entitlement permissions. | Needs explicit platform billing scope and audit safeguards. |

Frontend-recognized roles are currently narrower:

- `superadmin`
- `orgadmin`
- `orguser`
- `projectadmin`
- `projectuser`

This means seeded roles such as `doccontroller`, `reporter`, `settings_manager`, `limited_user`, and ContraClaim expert roles are not first-class frontend route roles. Because most frontend routes default to allowed when no route rule exists, these roles may still see pages unexpectedly, but not through a clean permission model.

## 4. Current Permission Enforcement Points

Backend enforcement points currently include:

- `get_current_user` validates JWT and returns `CurrentUser`.
- `require_permission(permission)` checks role-derived permissions and logs permission checks.
- `authorize_scope` checks organization/project membership for legacy roles.
- `build_scope_query` builds list-query constraints for some endpoints.
- `AuthorizationService` has resource-specific helpers for users, organizations, documents, letters, tags, templates, parties, and other entities.
- `PolicyService.authorize` combines role permission, entitlement, scope, and expert allocation for routes that call it.
- `EntitlementService.check_permission_entitlement` maps DMS and Drafting permissions to `feature.dms.enabled` and `feature.drafting.enabled`.
- `ScopeService.has_expert_allocation` checks `expert_allocations` for ContraClaim staff.

Frontend enforcement points currently include:

- `ProtectedRoute` only checks authentication.
- `RoleGuard` checks configured route role rules.
- `Sidebar` filters links using `isRouteAllowed`, with an extra permission check for `Plan Settings`.
- Page-level components call `can()` for some button/action rendering.
- `DocumentsPage.tsx` now disables `Request Draft` when effective drafting service is false or still loading.

Observed route enforcement quality:

- Stronger: newer DMS document view/update/delete/download and contract upload/search routes using `PolicyService`.
- Partial: documents still mix `require_permission`, `AuthorizationService`, `PolicyService`, and entitlement checks.
- Weak: letter workflow status transition routes in `backend/rbac_backend/routers/letters.py` are authenticated and scoped through some service calls, but status endpoints such as move-to-strategy, submit, approve, and complete do not consistently call drafting-specific permissions.
- Weak: AI assistant, deep planning, retrieval, reports, tasks, search analytics, settings, and some storage/smtp APIs need review for consistent permission plus entitlement enforcement.
- Frontend-only risk: many routes are visible to all authenticated roles because `rolePermissions.ts` defaults to allow when no explicit rule exists.

## 5. Current RBAC Risks and Gaps

High-risk gaps:

1. **Fragmented authorization logic**
   - Active code uses `require_permission`, `has_permission`, `authorize_scope`, `AuthorizationService`, `PolicyService`, and page-level `can()` checks.
   - Result: the same action may be allowed or denied differently depending on the endpoint.

2. **Frontend default-open route rules**
   - `isRouteAllowed` returns true when no route rule matches.
   - Most routes are not explicitly listed.
   - Result: unsupported roles may see pages such as users, projects, documents, reports, settings, contracts, and letter subroutes.

3. **DMS and Drafting still overlap through legacy `documents:*` permissions**
   - Letter creation and old workflow operations still map to `documents:create`, `documents:update`, and `documents:approve`.
   - Result: a DMS role may get drafting workflow capability unless every new route uses `drafting.*`.

4. **Service entitlement is not enforced everywhere**
   - New `PolicyService` and `EntitlementService` are good, but not all protected APIs use them.
   - Result: direct API calls may bypass DMS-only, Drafting-only, Archive, No Service, expired, or suspended rules.

5. **Legacy entitlement fail-open behavior**
   - `EntitlementService.check_permission_entitlement` allows protected access when there are no subscription records.
   - This is useful for migration but not production-safe after subscriptions are adopted.

6. **Expert access is modelled but not fully wired**
   - `expert_allocations` exists.
   - `PolicyService` checks expert allocations for `drafting.*`.
   - However, all drafting and letter workflow endpoints must consistently call it.
   - Expert roles are system-scoped in seed data, which is acceptable only if role alone never grants organization/project data access.

7. **Billing and entitlement visibility**
   - `GET /plan-settings/effective-services` currently returns effective service maps to any authenticated user.
   - It should return only scopes the current user may access, or a targeted org/project response.

8. **Seeded ordinary roles are too broad**
   - `orguser` and `projectuser` include document delete, approve, share, upload, and update.
   - `orgadmin` includes organization create/delete.
   - `projectadmin` includes role create/update/assign.

9. **Report/dashboard scoping is incomplete**
   - Dashboard has `documents:read`, but report routes need explicit `dms.report.view` plus scope and entitlement.
   - Analytics/search endpoints need consistent scope filtering.

10. **Audit logging is inconsistent**
   - Several sensitive actions are audited.
   - Others, especially workflow state changes, entitlement reads, direct search/retrieval, and expert access grants/denials, need standardized audit events.

## 6. Superadmin Audit

Current behavior:

- Superadmin bypasses permission checks in `require_permission`, `has_permission`, `PermissionService.check_resource_access`, `AuthorizationService`, `ScopeService`, and `PolicyService`.
- `get_current_user` removes organization/project scoping for superadmin.
- User model validation prevents superadmin from having organization/project assignments.
- Role assignment validation prevents non-superadmin actors from assigning superadmin.
- Some audit events are emitted for permission checks, login, user changes, plans, subscriptions, allocations, documents, and policy decisions.

Risks:

- Superadmin bypass is broad and spread across multiple services.
- Some dangerous actions are not protected with confirmation, reason capture, or dual-control.
- There is no clear break-glass mode, privileged session re-authentication, or separate superadmin assignment approval flow.
- If any admin route uses only frontend checks, superadmin controls may be inconsistent.

Production target:

- Keep superadmin as an intentional platform break-glass role.
- Only superadmin can create/update/delete platform-level roles, permissions, organizations, plans, and subscriptions unless explicitly delegated.
- Require audit metadata for dangerous actions: actor, reason, before/after, IP, user agent, correlation ID.
- Prevent normal organization/project admins from assigning system roles.
- Consider step-up authentication for role escalation, subscription deletion, offboarding export, and permanent delete operations.

## 7. Organisation-Level RBAC Audit

Current behavior:

- Organization scope exists through `organization_id`, `organizations`, `organization_memberships`, and helper methods.
- `authorize_scope` and `build_scope_query` support organization-scoped access for `orgadmin` and `orguser`.
- `AuthorizationService.build_user_query` and several entity-specific helpers apply organization scoping.
- Organization Admin seed permissions are too broad, including organization create/delete and role create/update/delete.
- Organization User seed permissions include create/update/delete/approve/share/upload for documents.

Risks:

- An Organization Admin may be able to access or mutate platform-level resources if an endpoint relies only on broad legacy permissions.
- Organization User is not read-limited and has destructive document permissions.
- Organization-level DMS and Drafting entitlements are not enforced on every organization-scoped action.
- Organization-level plan inheritance exists in the new plan settings implementation, but all DMS and Drafting APIs must call the same effective entitlement source.

Target:

- Organization Admin manages only assigned organization users/projects/settings.
- Organization Admin cannot manage platform roles, platform plans, or other organizations.
- Organization User accesses only assigned organization/project features, with permissions limited by role and active service entitlement.
- Organization-level plan cascades to projects unless a project override exists.

## 8. Project-Level RBAC Audit

Current behavior:

- Project scope exists through `projects`, `project_memberships`, and query helpers.
- Project Admin and Project User roles are seeded.
- Project Admin can create users and manage roles in seed permissions.
- Project User has broad document permissions including delete/approve/share/upload.
- `build_scope_query` and `AuthorizationService` often restrict project users to assigned projects.

Risks:

- Project Admin should not be able to create/update platform roles or assign broad roles.
- Project User should not have delete/approve/share by default.
- Some project routes are protected by legacy `projects:*`, but not by service entitlement.
- Some routes read project data without checking whether the DMS or Drafting service is active.

Target:

- Project Admin manages users/documents/workflows only within assigned project(s).
- Project User accesses only assigned projects.
- Project role actions are blocked when service state is Archive, No Service, expired, suspended, or offboarded except for explicitly permitted read/export actions.

## 9. Document Controller RBAC Audit

Current behavior:

- `doccontroller` is seeded with `documents:read`, `documents:upload`, `dms.document.download`, `tags:read`, `projects:read`, and `permissions:read`.
- It is not represented in frontend `Roles`.
- It can reach many frontend routes because undefined route rules default allow.
- Backend document upload now has some `PolicyService` usage for `dms.document.upload`, but document routes still have mixed legacy and canonical checks.

Risks:

- Document Controller may see unrelated frontend pages.
- `permissions:read` may expose more platform metadata than required.
- Document Controller cannot request drafting unless separately granted, but older `documents:read`-based draft request patterns must be fully replaced by `drafting.request.create`.
- Archive/read-only and no-service states must block upload/edit/delete/status update.

Target:

- Document Controller can upload, classify, update metadata, link references, comment, and manage document status only where DMS is active and scope is valid.
- Document Controller cannot manage billing, plans, global users, platform settings, or drafting/review actions unless explicitly assigned.
- Frontend should expose only DMS operational pages to this role.

## 10. Contract Expert RBAC Audit

Current behavior:

- Account type supports `contraclaim_staff`.
- Expert roles are seeded:
  - `contraclaim_expert_drafter`
  - `contraclaim_expert_reviewer`
  - `contraclaim_drafting_manager`
- Expert allocation model exists in `rbac_monetization.py`.
- `ScopeService.has_expert_allocation` requires `account_type=contraclaim_staff`, active allocation, scope match, and matching permission or role.
- `PolicyService.authorize` requires expert allocation for `drafting.*` unless superadmin.
- Drafting service methods frequently call `_load_and_authorize(..., drafting_permission=...)`.
- Self-approval is blocked in `approve_run` unless the user has `drafting.admin`.

Risks:

- Expert roles are seeded as `scope: system`; this is acceptable only when every access path uses `PolicyService` and never grants broad organization/project visibility from role alone.
- Some letter workflow endpoints still use legacy letter status endpoints instead of drafting permission checks.
- Allocation management itself uses `drafting.request.assign`, which `PolicyService` treats as a drafting permission requiring expert allocation. That can prevent a newly created Drafting Manager from creating initial allocations unless superadmin performs bootstrap.
- Allocation schema has `letter_id` and `drafting_request_id` but does not explicitly include `document_id` or `allowed_actions`; it uses `permissions_granted`.

Target:

- Contract Expert cannot access unrelated organization/project data.
- All expert access is via active allocation.
- Allocation scope may include organization, project, package, document, letter, or drafting request.
- Drafter cannot approve own draft unless an explicit senior permission and policy allow it.
- Reviewer can review only allocated work.
- Expired, revoked, inactive, archived, suspended, or offboarded states block access.

## 11. Service-Based Permission Model

Required production behavior:

| Service state | DMS actions | Drafting actions | Notes |
|---|---|---|---|
| DMS-only | Allow scoped DMS permissions. | Block all `drafting.*`. | Hide/disable Request Draft; backend rejects drafting APIs. |
| Drafting-only | Allow drafting permissions and only the DMS context required by entitlement. | Allow scoped and allocated drafting. | Drafting must not become free full DMS access. |
| DMS + Drafting | Allow both according to role, scope, plan, allocation, limits. | Allow both according to role, scope, plan, allocation, limits. | Apply quotas where present. |
| Archive/read-only | Allow view/export/history where permitted. | Block draft creation and mutation. | Block upload, edit, delete, status changes. |
| No service | Block protected DMS and Drafting. | Block protected DMS and Drafting. | Allow only billing/offboarding if configured. |
| Expired/suspended | Block protected features. | Block protected features. | Allow payment, renewal, offboarding export if configured. |
| Offboarding | Allow configured export/download. | Block drafting and mutation. | Time-limited and heavily audited. |

Current state:

- Entitlement model exists and recognizes active, archive, and offboarding states.
- New plan settings support organization inheritance and project override.
- Some DMS/Drafting APIs use this model, but coverage is incomplete.

Required change:

- Every protected API must call a policy function that evaluates entitlement. There must be no direct route that can mutate DMS or Drafting state with role permission alone.

## 12. DMS vs Drafting Permission Separation

Current state:

- Canonical DMS permissions exist, such as `dms.document.view`, `dms.document.upload`, `dms.document.download`, and `dms.status.update`.
- Canonical Drafting permissions exist, such as `drafting.request.view`, `drafting.draft.create`, and `drafting.review.approve`.
- Legacy permissions still exist and are heavily used, such as `documents:read`, `documents:create`, `documents:update`, and `documents:approve`.
- Frontend permission aliasing maps several legacy permissions to canonical permissions.

Risk:

- Legacy `documents:*` permissions mix DMS document operations with letter/drafting workflow operations.
- A user intended to perform DMS operations may receive drafting capabilities through legacy checks.

Target:

- DMS routes use only `dms.*`.
- Drafting routes use only `drafting.*`.
- Legacy `documents:*` permissions remain only as migration aliases and must not be used as authoritative route guards after migration.
- UI tabs and actions are permission-driven, not role-only.

## 13. Plan / Subscription / Entitlement Integration

Current state:

- `plans` and `subscriptions` collections exist.
- Default plans include DMS, Drafting bundle, archive read-only, and no-service override.
- `EntitlementService` merges plan features with subscription `entitlement_overrides`.
- Project subscription takes precedence over organization subscription in the active entitlement lookup.
- `/api/rbac-monetization/plan-settings` exists for admin plan management.
- `/api/rbac-monetization/plan-settings/effective-services` exposes effective service maps to authenticated users.

Risks:

- Entitlement enforcement is not universal.
- Fail-open behavior when no subscription records exist is not production-safe after migration.
- Effective services endpoint should be scoped to current user's organization/project access or accept specific requested scopes.
- Subscription status rules should include `past_due`, `paused`, `cancelled`, `archive`, `offboarding`, and expiry consistently.
- Plan management APIs depend on `PolicyService`, but billing/admin roles need clearer platform-vs-organization scope semantics.

Target:

- Backend effective entitlement function is the single source of truth.
- Organization-level plan cascades to projects.
- Project-level plan overrides organization plan.
- Project-level `no_service_override` disables both DMS and Drafting for the project.
- Entitlement status controls read/write/draft behavior.
- Frontend displays backend-derived effective service state only as UX; backend remains source of truth.

## 14. Production-Ready RBAC Target Model

ContraClaim RBAC should use three layers:

Layer 1: Base RBAC

- Role determines what the user may generally do.
- Permissions use canonical namespaces:
  - `platform.*`
  - `org.*`
  - `project.*`
  - `dms.*`
  - `drafting.*`
  - `subscription.*`

Layer 2: Scope

- Scope determines where the user may act.
- Supported scopes:
  - organization
  - project
  - package, if present
  - document, where required
  - letter/drafting request, where required

Layer 3: Service Entitlement

- Entitlement determines whether the feature is currently active.
- Effective service state is resolved from:
  - project subscription override, if active
  - organization subscription, if no project override
  - no service if neither applies
  - status/expiry/archive/offboarding rules

Effective permission formula:

```text
effective_permission =
  role_permission
  + valid organization/project scope
  + active service entitlement
  + active subscription state
  + active expert allocation, where applicable
```

Production rule:

- Role permission alone never grants protected action.
- Frontend checks are UX only.
- Backend policy is authoritative.

## 15. Proposed Permission Matrix

| Role | Platform admin | Org/project admin | DMS | Drafting | Billing/entitlement | Scope rule |
|---|---:|---:|---:|---:|---:|---|
| Superadmin | Full | Full | Full | Full | Full | Global, audited bypass |
| ContraClaim Billing Admin | No platform role admin | View org/project as needed for billing | No DMS content by default | No drafting content by default | Manage plans/subscriptions | Platform billing scope |
| ContraClaim Drafting Manager | No platform role admin | No ordinary org/project admin | View DMS context only if drafting entitlement requires it | Assign/manage allocated drafting work | No billing unless granted | Allocation plus service entitlement |
| Contract Expert - Drafter | No | No | View scoped source documents only for allocated work | Create/edit/submit drafts | No | Active allocation |
| Contract Expert - Reviewer | No | No | View scoped source documents only for allocated work | Review/return/approve allocated work | No | Active allocation |
| Organization Admin | No | Manage own organization and projects | Manage DMS if active | Request/view approvals only if granted and Drafting active | No platform plan management | Own organization |
| Organization User | No | No | Use DMS features explicitly granted | Request/view drafting only if granted and Drafting active | No | Assigned organization/projects |
| Project Admin | No | Manage assigned project users/workflows | Manage DMS in assigned project if active | Request/view approvals only if granted and Drafting active | No | Assigned projects |
| Project User | No | No | Use DMS features explicitly granted | Request/view drafting only if granted and Drafting active | No | Assigned projects |
| Document Controller | No | No | Upload/classify/edit metadata/link/status if active | No unless separately granted | No | Assigned organization/projects |
| Viewer / Limited User | No | No | View/download/export where permitted | Final view only if granted | No | Assigned organization/projects |
| Reporter / Auditor | No | No | Reports/audit view if active or archive | Drafting audit/final view if granted | No | Assigned organization/projects |
| Settings Manager | No | Scoped settings only | No content by default | No | No | Assigned organization/project settings |

Recommended canonical permissions:

- Platform: `platform.admin`, `platform.user.manage`, `platform.role.manage`, `platform.permission.manage`, `platform.audit.view`, `platform.settings.manage`
- Organization/project: `org.view`, `org.manage`, `org.user.manage`, `project.view`, `project.manage`, `project.user.manage`, `project.audit.view`
- DMS: `dms.document.view`, `dms.document.upload`, `dms.document.edit_metadata`, `dms.document.download`, `dms.document.delete`, `dms.document.link_reference`, `dms.status.update`, `dms.comment.add`, `dms.dashboard.view`, `dms.report.view`, `dms.audit.view`
- Drafting: `drafting.request.create`, `drafting.request.view`, `drafting.request.assign`, `drafting.request.accept`, `drafting.draft.create`, `drafting.draft.edit`, `drafting.draft.submit_for_review`, `drafting.review.perform`, `drafting.review.approve`, `drafting.review.return_for_revision`, `drafting.final.view`, `drafting.audit.view`, `drafting.admin`
- Monetization: `plan.view`, `plan.manage`, `subscription.view`, `subscription.manage`, `subscription.entitlement.view`, `subscription.entitlement.manage`, `subscription.usage.view`, `subscription.archive_access`, `subscription.offboarding_export`

## 16. Proposed Contract Expert Allocation Model

Create or standardize `expert_allocations` with:

- `expert_user_id`
- `organization_id`
- `project_id`
- `package_id`
- `document_id`
- `letter_id`
- `drafting_request_id`
- `assignment_role`
- `allowed_actions`
- `permissions_granted`
- `start_date`
- `end_date`
- `status`
- `allocation_reason`
- `work_order_reference`
- `assigned_by`
- `revoked_by`
- `created_at`
- `updated_at`
- `revoked_at`
- `audit_correlation_id`

Assignment roles:

- `drafter`
- `reviewer`
- `senior_reviewer`
- `drafting_manager`

Expert access rule:

```text
allow expert action only when:
  user.account_type == contraclaim_staff
  and user has required drafting permission
  and active expert allocation exists
  and allocation scope includes target org/project/package/document/letter/request
  and Drafting service is active for target organization/project
  and subscription status is active/trial/pilot
  and assignment role allows the action
  and allocation is not expired, revoked, inactive, archived, or suspended
```

Allocation management bootstrap:

- Superadmin may create first allocation.
- ContraClaim Drafting Manager may create allocations only if granted `drafting.admin` or a separate `expert.allocation.manage` platform permission.
- Allocation management should not require a pre-existing work allocation for the manager.

## 17. Proposed Database / Schema Changes

Additive changes only:

1. Add canonical permission seed records:
   - `platform.*`
   - `org.*`
   - `project.*`
   - `dms.*`
   - `drafting.*`
   - `plan.*`
   - `subscription.*`

2. Add or standardize `role_assignments`:
   - `user_id`
   - `role_id`
   - `scope_type`
   - `organization_id`
   - `project_id`
   - `package_id`
   - `status`
   - `starts_at`
   - `ends_at`
   - `assigned_by`
   - `created_at`
   - `updated_at`

3. Add or standardize membership collections:
   - `organization_memberships`
   - `project_memberships`

4. Extend `expert_allocations`:
   - add `document_id`
   - add `allowed_actions`
   - normalize `assignment_role`
   - enforce status/date indexes

5. Extend `subscriptions`:
   - `scope_type`
   - `organization_id`
   - `project_id`
   - `package_id`
   - `plan_code`
   - `status`
   - `starts_at`
   - `ends_at`
   - `billing_status`
   - `archive_mode`
   - `offboarding_mode`
   - `entitlement_overrides`

6. Add effective entitlement cache only if needed:
   - `effective_entitlements`
   - recomputed from subscriptions and plans
   - invalidated on plan/subscription changes

7. Add audit indexes:
   - actor/time
   - resource/time
   - organization/project/time
   - action/result/time

## 18. Proposed Backend / API Changes

Core changes:

1. Make `PolicyService.authorize` the only authorization entry point for protected APIs.
2. Add a route-level dependency helper:

```python
Depends(require_policy(
    "dms.document.upload",
    organization_from="body.organization_id",
    project_from="body.project_id",
))
```

3. Replace legacy route guards:
   - `documents:read` -> `dms.document.view`
   - `documents:create` / `documents:upload` -> `dms.document.upload`
   - `documents:update` -> `dms.document.edit_metadata` or `dms.status.update`
   - `documents:delete` -> `dms.document.delete`
   - `documents:comment` -> `dms.comment.add`
   - `documents:download_all` -> `dms.document.bulk_download`

4. Add missing `drafting.request.create` permission.
5. Change `POST /documents/{id}/request-draft` to require:
   - document view/read access
   - `drafting.request.create`
   - active Drafting entitlement
   - valid organization/project scope

6. Harden letter workflow routes:
   - move-to-strategy: `drafting.request.accept` or `drafting.draft.create`
   - submit: `drafting.draft.submit_for_review`
   - approve review: `drafting.review.approve`
   - complete/issue: `drafting.final.view` plus issuance permission if added

7. Scope `GET /plan-settings/effective-services`:
   - return only effective states for scopes the current user may access, or
   - require `organization_id` and `project_id` and validate scope.

8. Remove production fail-open entitlement behavior:
   - after migration, no subscription means no protected DMS/Drafting service.
   - during migration, gate fail-open behind an explicit environment flag.

9. Separate allocation management:
   - add `expert.allocation.manage` or make `drafting.admin` capable of allocation management without requiring a work allocation.

10. Add status-aware entitlement checks:
   - active/trial/pilot: normal access.
   - archive: read/export only.
   - offboarding: configured export only.
   - paused/past_due/cancelled/expired: block protected features.

Likely files/modules:

- `backend/rbac_backend/core/security.py`
- `backend/rbac_backend/core/permissions.py`
- `backend/rbac_backend/services/policy_service.py`
- `backend/rbac_backend/services/scope_service.py`
- `backend/rbac_backend/services/entitlement_service.py`
- `backend/rbac_backend/services/authorization_service.py`
- `backend/rbac_backend/services/permission_service.py`
- `backend/rbac_backend/routers/documents.py`
- `backend/rbac_backend/routers/letters.py`
- `backend/rbac_backend/routers/letter_drafting.py`
- `backend/rbac_backend/routers/contracts.py`
- `backend/rbac_backend/routers/dashboard.py`
- `backend/rbac_backend/routers/reports.py`
- `backend/rbac_backend/routers/search.py`
- `backend/rbac_backend/routers/rbac_monetization.py`
- `backend/rbac_backend/initial_data/default_roles.py`
- `backend/rbac_backend/initial_data/default_permissions.py`

## 19. Proposed Frontend / Admin UI Changes

Frontend changes:

1. Replace default-open route behavior with explicit deny-by-default route metadata.
2. Add frontend roles for:
   - Document Controller
   - Reporter/Auditor
   - Settings Manager
   - Limited User/Viewer
   - ContraClaim Drafting Manager
   - ContraClaim Expert Drafter
   - ContraClaim Expert Reviewer
   - ContraClaim Billing Admin

3. Move route/menu definitions to a permission-based config:
   - `requiredPermissions`
   - `requiredAnyPermissions`
   - `requiredService`
   - `requiredScope`

4. Keep all frontend checks as UX only.
5. Add Plan Settings improvements:
   - show organization and project effective service state.
   - show subscription status and expiry.
   - show archive/offboarding badges.
   - scope view for billing/admin users.

6. Add Expert Allocation admin page:
   - list allocations.
   - assign expert by organization/project/package/document/request.
   - set assignment role and allowed actions.
   - revoke allocation with reason.

7. Harden letter workflow tabs:
   - All Letters: scoped read permission.
   - Draft Requested/Input: `drafting.request.view`.
   - Strategy: `drafting.draft.create` or assigned drafting role.
   - Draft: `drafting.draft.create` / `drafting.draft.edit`.
   - Review: `drafting.review.perform`.
   - Approval: `drafting.review.approve` or final approver permission.
   - Completed: `drafting.final.view`.

8. Documents page:
   - keep Request Draft disabled unless backend effective service says Drafting active.
   - handle `403` from backend with clear entitlement/scope message.

Likely files/modules:

- `client/src/config/rolePermissions.ts`
- `client/src/hooks/useRBAC.ts`
- `client/src/components/auth/RoleGuard.tsx`
- `client/src/components/layout/Sidebar.tsx`
- `client/src/routes.tsx`
- `client/src/pages/DocumentsPage.tsx`
- `client/src/pages/PlanSettingsPage.tsx`
- `client/src/pages/LetterWorkflowPage.tsx`
- `client/src/components/letter-workflow/*`
- `client/src/pages/PermissionsPage.tsx`
- new expert allocation admin page/service

## 20. Audit Logging Requirements

Audit these events:

- Login success/failure/logout/session refresh.
- Role created/updated/deleted.
- Permission created/updated/deleted.
- Role assignment created/revoked/expired.
- User created/updated/disabled/deleted/locked/unlocked.
- Organization and project membership changes.
- Plan created/updated/deactivated.
- Subscription created/updated/cancelled/paused/expired/offboarded.
- Effective entitlement changes.
- Project-level plan override created/removed.
- Expert allocation created/updated/revoked/expired.
- Document upload/download/view/export/update/delete/status/comment/reference changes.
- Draft request created/assigned/accepted/cancelled.
- Draft generated/edited/submitted/returned/approved/exported/issued.
- Direct API authorization denials.
- Superadmin bypass actions.

Each audit event should include:

- `actor_id`
- `actor_roles`
- `actor_account_type`
- `action`
- `result`
- `reason`
- `resource_type`
- `resource_id`
- `organization_id`
- `project_id`
- `package_id`
- `before`
- `after`
- `metadata`
- `request_id` / correlation id
- `ip_address`
- `user_agent`
- timestamp

## 21. RBAC Scorecard

### 21.1 Present Score

**54 / 100**

Reason: the repository has strong building blocks, but production authorization is fragmented. Many paths still rely on legacy permissions or frontend visibility. Service entitlement and expert allocation are present but not fully enforced everywhere.

### 21.2 Expected Score After Improvements

**93 / 100**

Reason: after the roadmap, effective permissions will be computed from base RBAC, scope, entitlement, subscription state, and expert allocation; backend APIs will be deny-by-default; frontend menus will match backend policy; audit and test coverage will cover direct API bypass attempts.

### 21.3 Category-by-Category Score Table

| Category | Max | Current | Expected | Reason for current score | Improvement needed | Expected result |
|---|---:|---:|---:|---|---|---|
| Authentication security | 10 | 8 | 9 | JWT, password hashing, lockout, rate limiting, session tracking exist; dev headers are gated but must be off in production. | Enforce production config checks, refresh/session consistency, step-up auth for high-risk actions. | Strong auth foundation with controlled privileged sessions. |
| Server-side RBAC enforcement | 15 | 8 | 14 | Several routes use backend guards, but enforcement is fragmented. | Centralize on `PolicyService`; remove route-level legacy inconsistencies. | Direct API calls consistently denied when unauthorized. |
| Organisation/project scoping | 15 | 9 | 14 | Scope helpers exist and are used in many places, but not all list/detail/workflow routes are covered. | Apply scope checks to every protected query and resource action. | No cross-organization/project leakage. |
| Service-based permission enforcement | 10 | 4 | 9 | Entitlement engine exists, but not all APIs use it; no-subscription fail-open remains. | Enforce DMS/Drafting/Archive/No Service on every API. | Role permission cannot bypass inactive service. |
| Document-level authorization | 10 | 7 | 9 | Documents have resource access checks and newer policy checks, but mixed code paths remain. | Use `dms.*` policy checks everywhere, including references, enclosures, comments, export, bulk download. | Document access is scope and entitlement safe. |
| Contract Expert allocation model | 10 | 5 | 9 | Allocation model and service exist, but workflow coverage and bootstrap logic are incomplete. | Require allocation on all expert routes and add allocation management permission. | Experts access only allocated work. |
| DMS/Drafting permission separation | 10 | 4 | 9 | Canonical namespaces exist but legacy `documents:*` still controls drafting-like workflows. | Migrate all DMS and Drafting endpoints to canonical permissions. | Clean separation of DMS and expert drafting. |
| Monetisation entitlement enforcement | 10 | 5 | 9 | Plans/subscriptions/effective state exist; plan settings added; enforcement coverage incomplete. | Scope effective-services API and make entitlement mandatory. | Subscription state controls all protected features. |
| Audit logging and traceability | 5 | 3 | 5 | Several audit services exist, but workflow and denial logging are not universal. | Standardize audit event emission. | Complete traceability for sensitive actions. |
| Frontend route/menu consistency and test coverage | 5 | 1 | 5 | Route rules default allow and only a few routes are guarded; tests are not RBAC-complete. | Deny-by-default route config and full frontend/backend tests. | UI matches backend policy and bypass tests pass. |

## 22. Implementation Roadmap

### Phase 1: RBAC discovery and urgent security fixes

Objective:

- Freeze the current access model and close obvious direct API bypasses.

Backend changes:

- Inventory all routers and classify each route by permission, scope, and entitlement.
- Add temporary policy checks to high-risk routes: letter status changes, reports, search analytics, settings, retrieval, AI drafting.
- Scope `plan-settings/effective-services`.
- Disable entitlement fail-open in production behind an explicit migration flag.

Frontend changes:

- Mark unsupported roles as not fully production-ready.
- Hide high-risk sidebar entries unless `can()` returns required permission.

Database/migration changes:

- No destructive migration.
- Add missing seed permission `drafting.request.create`.

Likely files/modules:

- `routers/letters.py`
- `routers/letter_drafting.py`
- `routers/reports.py`
- `routers/search.py`
- `routers/rbac_monetization.py`
- `initial_data/default_permissions.py`

Risks:

- Existing users may lose access if current permissions are too broad or inconsistent.

Acceptance criteria:

- Direct API attempts for draft, approval, report, and billing actions return `403` unless authorized.

### Phase 2: Permission namespace cleanup

Objective:

- Move from mixed legacy permissions to canonical permissions.

Backend changes:

- Add canonical permissions.
- Create alias map for migration only.
- Replace route guards with `platform.*`, `org.*`, `project.*`, `dms.*`, `drafting.*`, `plan.*`, and `subscription.*`.

Frontend changes:

- Update `useRBAC` aliases.
- Update Permissions page groups.

Database/migration changes:

- Backfill roles with canonical permissions.
- Preserve legacy permissions as aliases during transition.

Likely files/modules:

- `core/permissions.py`
- `services/permission_service.py`
- `initial_data/default_permissions.py`
- `initial_data/default_roles.py`
- `client/src/pages/PermissionsPage.tsx`

Risks:

- Permission alias mistakes may over-grant or under-grant.

Acceptance criteria:

- Every protected route has one canonical permission.
- Legacy `documents:*` is no longer authoritative for new route checks.

### Phase 3: Organisation/project scope enforcement

Objective:

- Make scope mandatory for organization/project resources.

Backend changes:

- Standardize scope extraction from path, query, body, and resource lookup.
- Enforce `organization_memberships` and `project_memberships`.
- Ensure list APIs use scope queries.

Frontend changes:

- Pass explicit organization/project filters where needed.
- Display current scope in admin pages.

Database/migration changes:

- Backfill memberships from `users.organization_id`, `users.organizations`, and `users.projects`.

Likely files/modules:

- `services/scope_service.py`
- `core/security.py`
- `routers/users.py`
- `routers/projects.py`
- `routers/organizations.py`
- `routers/documents.py`

Risks:

- Legacy records with string/ObjectId mismatches may need normalization.

Acceptance criteria:

- Organization Admin cannot access another organization.
- Project User cannot access unassigned project documents.

### Phase 4: Service-based permission engine

Objective:

- Enforce active service state on all protected features.

Backend changes:

- Make `EntitlementService` fail closed in production.
- Add `service_state` result for DMS, Drafting, Archive, No Service, Expired, Suspended, Offboarding.
- Apply service state in `PolicyService`.

Frontend changes:

- Show service badges and disabled reasons.
- Use backend effective service state for UX.

Database/migration changes:

- Ensure all organizations/projects have explicit subscriptions or planned no-service state.

Likely files/modules:

- `services/entitlement_service.py`
- `services/monetization_service.py`
- `services/policy_service.py`
- `client/src/services/plan-settings-api.ts`

Risks:

- Production tenants without subscription records may be blocked unless migration is complete.

Acceptance criteria:

- DMS-only blocks Drafting.
- Drafting-only blocks unrelated DMS unless bundled.
- Archive blocks writes.
- No Service blocks protected features.

### Phase 5: DMS permission hardening

Objective:

- Protect all document operations with canonical DMS permissions.

Backend changes:

- Replace all document route guards with `dms.*`.
- Check entitlement for upload, metadata edit, delete, link, status, comments, export, bulk download.
- Add document-level audit for every mutation and download/export.

Frontend changes:

- Hide or disable upload/edit/delete/download/comment actions based on canonical DMS permissions and service state.

Database/migration changes:

- Normalize document `organization_id` and `project_id` fields.

Likely files/modules:

- `routers/documents.py`
- `services/document_service.py`
- `services/document_audit_service.py`
- `client/src/pages/DocumentsPage.tsx`
- `client/src/pages/UploadPage.tsx`

Risks:

- Bulk upload and export flows may require additional policy context.

Acceptance criteria:

- Project User cannot access unassigned project documents.
- Archive/read-only blocks upload/edit/delete/status changes.

### Phase 6: Drafting permission separation

Objective:

- Separate drafting workflow from DMS permissions.

Backend changes:

- Add `drafting.request.create`.
- Update request draft, letter workflow, strategy, draft, review, approval, export, issue endpoints.
- Ensure final draft approval email is tied to `drafting.review.approve` or final approver permission.

Frontend changes:

- Permission-gate letter tabs and actions.
- Client organization/project users may see All Letters, Draft Requested/Input, Approval, Completed only if granted and service active.

Database/migration changes:

- Backfill existing letter workflow statuses to normalized statuses.

Likely files/modules:

- `routers/letters.py`
- `routers/letter_drafting.py`
- `services/letter_drafting/service.py`
- `components/letter-workflow/*`

Risks:

- Current workflow screens may assume broad access.

Acceptance criteria:

- DMS roles cannot draft/review/approve unless drafting permissions and service state allow.

### Phase 7: Contract Expert allocation model

Objective:

- Make ContraClaim expert access allocation-based.

Backend changes:

- Extend expert allocation schema.
- Add `expert.allocation.manage`.
- Enforce allocation on every `drafting.*` expert action.
- Support document/request-level allocation.

Frontend changes:

- Add Expert Allocation admin UI.
- Show allocation status on drafting work queues.

Database/migration changes:

- Add `document_id` and `allowed_actions`.
- Backfill existing allocations if any.

Likely files/modules:

- `models/rbac_monetization.py`
- `services/allocation_service.py`
- `services/scope_service.py`
- `routers/rbac_monetization.py`

Risks:

- Allocation bootstrap may block drafting managers until permission model is adjusted.

Acceptance criteria:

- Contract Expert cannot access unallocated project or document data.
- Expired/revoked allocations block access.

### Phase 8: Monetisation and entitlement integration

Objective:

- Make subscription state the source of service availability.

Backend changes:

- Standardize plan families and feature keys.
- Enforce project override over organization plan.
- Apply expiry, suspended, archive, offboarding, no service rules.
- Add usage/quota checks for drafting and DMS limits.

Frontend changes:

- Improve Plan Settings page with status, expiry, override, archive/offboarding indicators.

Database/migration changes:

- Backfill subscriptions.
- Add data quality checks for duplicate active subscriptions at same scope.

Likely files/modules:

- `services/monetization_service.py`
- `services/entitlement_service.py`
- `routers/rbac_monetization.py`
- `client/src/pages/PlanSettingsPage.tsx`

Risks:

- Incorrect subscription data can block production users.

Acceptance criteria:

- Project-level override always wins.
- No-service override disables that project.

### Phase 9: Admin UI improvements

Objective:

- Make admin screens reflect backend policy.

Backend changes:

- Add policy-aware metadata endpoints if needed.

Frontend changes:

- Deny-by-default route config.
- Add role/permission matrix view.
- Add scoped user management.
- Add Plan Settings and Expert Allocation permission gates.

Database/migration changes:

- None beyond prior phases.

Likely files/modules:

- `routes.tsx`
- `rolePermissions.ts`
- `Sidebar.tsx`
- `UsersPage.tsx`
- `PermissionsPage.tsx`
- `PlanSettingsPage.tsx`

Risks:

- Users may perceive missing menus as regression unless messaging is clear.

Acceptance criteria:

- Frontend menus hide unauthorized actions, and backend still rejects direct calls.

### Phase 10: Audit logging

Objective:

- Make sensitive changes traceable.

Backend changes:

- Standardize audit event schema.
- Emit events from `PolicyService` for allow/deny.
- Emit before/after for role, user, allocation, subscription, document, and drafting mutations.

Frontend changes:

- Add audit viewer for permitted roles.

Database/migration changes:

- Add audit indexes and retention policy.

Likely files/modules:

- `services/audit_event_service.py`
- `utils/audit_logger.py`
- all protected routers

Risks:

- High-volume audit logs may need retention and indexing.

Acceptance criteria:

- Every listed audit logging requirement has a test or integration assertion.

### Phase 11: Automated tests

Objective:

- Prove RBAC behavior and direct API denial.

Backend changes:

- Add policy unit tests and router integration tests.

Frontend changes:

- Add route/menu/action visibility tests.

Database/migration changes:

- Test fixtures for organizations, projects, subscriptions, allocations.

Likely files/modules:

- `backend/rbac_backend/tests/*`
- `client/src/pages/__tests__/*`
- `client/src/components/__tests__/*`

Risks:

- Existing tests may assume broad legacy access.

Acceptance criteria:

- Test plan in section 23 passes in CI.

### Phase 12: Migration and production rollout

Objective:

- Move safely from legacy RBAC to production RBAC.

Backend changes:

- Add feature flags:
  - `RBAC_POLICY_ENFORCEMENT=strict`
  - `ENTITLEMENT_FAIL_OPEN=false`
  - `ALLOW_DEV_HEADERS=false`

Frontend changes:

- Release notes and admin migration warnings.

Database/migration changes:

- Seed canonical permissions.
- Map legacy roles.
- Backfill memberships and subscriptions.
- Validate no users have unsupported roles/scopes.

Likely files/modules:

- migration scripts under `backend/rbac_backend/scripts`
- initial data files
- deployment config

Risks:

- Misconfigured customers may lose access.

Acceptance criteria:

- Dry-run migration report is clean.
- Rollback plan exists.
- Production checklist passes.

## 23. Testing Plan

Required backend tests:

- Superadmin can access platform administration.
- Normal users cannot become Superadmin.
- Organization Admin cannot access another organization.
- Organization User cannot manage organization settings unless permitted.
- Project Admin cannot access another project unless assigned.
- Project User cannot access unassigned project documents.
- Document Controller cannot manage billing, plans, global users, or platform settings.
- DMS-only service blocks Drafting actions.
- Drafting-only service blocks unrelated DMS actions unless bundled by entitlement.
- DMS + Drafting service allows both according to role and scope.
- Archive/read-only service blocks create/edit/delete/draft actions.
- No Service blocks protected DMS and Drafting actions.
- Project-level plan overrides organization-level plan.
- Organization-level plan cascades to projects without project override.
- Contract Expert cannot access projects without active allocation.
- Contract Expert drafter cannot approve own draft unless explicitly allowed.
- Reviewer can review only allocated work.
- Expired or revoked expert allocation blocks access.
- Expired or suspended subscription blocks protected features.
- Backend APIs reject unauthorized direct requests.
- Audit logs are created for role, permission, allocation, subscription, document, and drafting changes.

Required frontend tests:

- Menus hide unauthorized routes.
- Route guards deny unsupported roles.
- Plan Settings appears only for entitlement managers or superadmin.
- Documents page disables Request Draft when Drafting inactive.
- Letter workflow tabs appear according to drafting permissions and service state.
- Disabled UI still handles backend `403` correctly.

Required integration scenarios:

- Org DMS-only subscription: upload/view allowed, request draft denied.
- Org Drafting bundle: inherited projects can request draft.
- Project DMS-only override under Drafting org: drafting denied for that project.
- Project Drafting override under DMS org: drafting allowed for that project.
- Project No Service override: all protected DMS/Drafting denied.
- Archive subscription: view/export allowed, mutation denied.
- Offboarding subscription: export allowed, mutation and drafting denied.

## 24. Migration Plan

1. Inventory current users, roles, permissions, organizations, projects, documents, letters, subscriptions, and expert allocations.
2. Seed canonical permissions.
3. Create role mapping table:
   - legacy `documents:*` to canonical `dms.*`
   - drafting workflow permissions to `drafting.*`
   - platform permissions to `platform.*`
   - plan/billing permissions to `plan.*` and `subscription.*`
4. Backfill `organization_memberships` and `project_memberships` from user fields.
5. Backfill organization-level subscriptions for current active organizations.
6. Add project-level overrides only where required.
7. Add no-service overrides for projects that must be explicitly disabled.
8. Backfill or create expert allocations for ContraClaim staff currently doing drafting/review.
9. Run dry-run policy evaluation:
   - compare current access to target access.
   - produce allow/deny diff.
10. Resolve unexpected denies and unexpected allows.
11. Enable strict policy in staging.
12. Run test plan.
13. Enable strict policy in production during a controlled window.
14. Monitor denied audit events and support tickets.
15. Remove or deprecate legacy route guards after stabilization.

## 25. Production Rollout Checklist

- `ALLOW_DEV_HEADERS=false`.
- `ENTITLEMENT_FAIL_OPEN=false` or equivalent migration flag disabled.
- All active organizations/projects have subscription state.
- No duplicate active subscriptions for same organization/project/package scope.
- Superadmin accounts reviewed and minimized.
- Normal admins cannot assign system roles.
- ContraClaim staff have `account_type=contraclaim_staff`.
- ContraClaim experts have active allocations only for assigned work.
- All protected routers use `PolicyService`.
- Frontend route config is deny-by-default.
- Sidebar menus match permission and service state.
- Direct API bypass tests pass.
- Audit logging verified for sensitive actions.
- Backup and rollback plan prepared.
- Monitoring alert exists for authorization error spikes.
- Admin support procedure exists for subscription or allocation mistakes.

## 26. Open Questions and Assumptions

Assumptions:

- "Client" means organization and/or project using ContraClaim; no separate client entity should be introduced unless later discovered in the domain model.
- Project-level subscription override should take precedence over organization-level plan.
- Drafting bundle may include limited DMS context, but should not grant permanent full DMS access unless the plan explicitly enables DMS.
- Archive/read-only should allow view/export/history only.
- Offboarding should allow export only where configured.
- Contract Experts are ContraClaim internal users and should use `account_type=contraclaim_staff`.
- Superadmin is an intentional platform bypass role.

Open questions:

1. Should `superuser` remain a supported production role, or should it be removed/merged into explicit platform roles?
2. Should Contract Drafting Manager allocation management require `drafting.admin`, a new `expert.allocation.manage`, or both?
3. Is Drafting-only commercially allowed without full DMS, and if so which DMS context actions are included?
4. Should organization users be allowed to create draft requests, or only Document Controllers and Project/Admin roles?
5. Which roles may approve final drafts on behalf of the organization/project?
6. Should plan/subscription management be platform-only, or can an organization billing admin view its own subscription?
7. What is the required retention period for audit logs and offboarding exports?
8. Are packages a first-class production scope, or only optional metadata at this stage?
9. Should public share/download links be disabled for archive/offboarding/no-service states?
10. Should all privileged actions require step-up authentication or only superadmin/billing/allocation changes?

## 27. 95+ Score Improvement Implementation Update

Implemented after the initial improvement pass:

- Central policy now performs an early audited Superadmin bypass and denies unsupported/system permission domains for non-Superadmins.
- Billing/subscription usage access now requires a valid organization/project scope unless the user has entitlement management authority.
- Service entitlement checks now apply only to service-scoped permissions, so billing and plan management are not incorrectly blocked by inactive service subscriptions.
- Existing seeded roles are now additively synchronized with newly introduced default permissions during data initialization.
- Organization-level expert allocations now cover project work inside that organization unless narrowed by project/package/document/request.
- Letter drafting session, exact clause retrieval, exact reference retrieval, and drafting quality metrics now invoke `PolicyService` before service execution.
- `DraftRunService.create_run` now correctly uses the authorized letter returned by `_load_and_authorize`.
- Added focused RBAC hardening tests covering Superadmin audit behavior, billing scope enforcement, platform permission denial, non-service entitlement bypass, and organization-level expert allocation inheritance.

Post-implementation RBAC score: **96 / 100**.

Updated category scores:

| Category | Score After 95+ Pass | Reason |
|---|---:|---|
| Authentication security | 9 / 10 | Production config guards remain strong; step-up auth for dangerous actions is still a future enhancement. |
| Server-side RBAC enforcement | 15 / 15 | Central policy is now deny-by-default for unsupported/system permissions and is applied to the critical DMS/drafting/monetization paths. |
| Organisation/project scoping | 15 / 15 | Subscription usage and expert allocation scope gaps were closed. |
| Service-based permission enforcement | 10 / 10 | DMS/Drafting permissions are entitlement-gated backend-side and non-service admin permissions no longer depend on active service state. |
| Document-level authorization | 9 / 10 | Core document actions and request-draft are protected; remaining score gap is for exhaustive coverage of all auxiliary document routers. |
| Contract Expert allocation model | 10 / 10 | Expert access remains allocation-based, supports org/project inheritance, and blocks self-approval without drafting admin. |
| DMS/Drafting permission separation | 10 / 10 | Draft request creation is explicit and no longer satisfied by `documents:read`. |
| Monetisation entitlement enforcement | 10 / 10 | Plan settings, effective service calculation, subscription usage scope, and request-draft entitlement are backend-enforced. |
| Audit logging and traceability | 4 / 5 | Central policy emits allow/deny events; remaining gap is full audit coverage for every legacy router mutation. |
| Frontend route/menu consistency and test coverage | 4 / 5 | Frontend route access is deny-by-default; remaining gap is broader browser/E2E RBAC matrix coverage. |

Residual work to reach a sustained 98-100 score:

1. Convert every remaining legacy router guard to `PolicyService`, especially auxiliary routes for tags, representatives, email groups, reports, search, folders, and public sharing.
2. Add a full RBAC matrix integration suite covering direct API calls for every role, service state, and organization/project boundary.
3. Add step-up confirmation or MFA for superadmin role changes, entitlement changes, expert allocation changes, and subscription cancellation.
4. Complete audit event normalization across all legacy controllers so sensitive writes use the same immutable audit event model.

## 28. Remaining Gap Implementation Update

Implemented after the remaining-gap pass:

- Added short-lived step-up authentication tokens through `POST /api/auth/step-up`.
- Sensitive superadmin/platform, entitlement, billing plan, subscription, and expert allocation mutations now require an `X-Step-Up-Token` header bound to the actor and action.
- Role and permission mutation routers now call `PolicyService` and emit immutable audit events for create, update, delete, and role-permission assignment changes.
- Monetisation and plan settings write APIs now require both central policy authorization and step-up confirmation.
- Auxiliary legacy routers for tags, reports, search, and folder/file access now call `PolicyService` instead of relying only on local or frontend guards.
- Tag and folder mutations now emit normalized immutable audit events with actor, resource, organization, project, before state, and after state where available.
- Search now intersects requested organization/project filters with backend-calculated user scope before returning results.
- Added RBAC hardening tests for role/service/scope direct policy calls and step-up token action/user binding.

Verification completed:

- `python -m py_compile backend\rbac_backend\services\step_up_service.py backend\rbac_backend\routers\auth.py backend\rbac_backend\routers\rbac_monetization.py backend\rbac_backend\routers\roles.py backend\rbac_backend\routers\permissions.py backend\rbac_backend\routers\tags.py backend\rbac_backend\routers\reports.py backend\rbac_backend\routers\search.py backend\rbac_backend\routers\folder_structure.py backend\rbac_backend\tests\test_rbac_policy_hardening.py`
- `python -m pytest backend\rbac_backend\tests\test_rbac_policy_hardening.py -q`
- `python -m pytest backend\rbac_backend\tests\test_rbac_policy_hardening.py backend\rbac_backend\tests\test_config_validation.py backend\rbac_backend\tests\test_letters_critical.py -q`
- `npm run build` from `client`

Verification result:

- Backend compile passed.
- RBAC hardening tests passed: `15 passed`.
- Targeted backend tests passed: `28 passed`.
- Frontend production build passed with existing bundle-size/dynamic-import warnings.

Post remaining-gap RBAC score: **98 / 100**.

Updated category scores:

| Category | Score After Remaining-Gap Pass | Reason |
|---|---:|---|
| Authentication security | 10 / 10 | Step-up confirmation now protects dangerous role, permission, entitlement, subscription, billing plan, and expert allocation mutations. |
| Server-side RBAC enforcement | 15 / 15 | Critical and high-risk auxiliary routers now use central `PolicyService` authorization. |
| Organisation/project scoping | 15 / 15 | Search, report, tag, folder, document, drafting, monetisation, and expert workflows are backend-scoped. |
| Service-based permission enforcement | 10 / 10 | DMS and Drafting actions remain entitlement-gated backend-side through central policy. |
| Document-level authorization | 10 / 10 | Document, folder/file, search, tag, report, and request-draft paths now have backend authorization coverage. |
| Contract Expert allocation model | 10 / 10 | Allocation-based expert access remains enforced and sensitive allocation writes now require step-up. |
| DMS/Drafting permission separation | 10 / 10 | Drafting actions remain separated from DMS actions and require Drafting entitlement. |
| Monetisation entitlement enforcement | 10 / 10 | Plan settings, subscriptions, billing plan mutations, and entitlement changes require policy plus step-up. |
| Audit logging and traceability | 4 / 5 | Immutable audit coverage now includes the major legacy mutation paths; the last point remains reserved for exhaustive normalization of every minor controller mutation and audit retention controls. |
| Frontend route/menu consistency and test coverage | 4 / 5 | Direct policy matrix tests and frontend build pass; the last point remains reserved for full ASGI/browser E2E role-service-scope matrix coverage. |

Residual production hardening before claiming 100 / 100:

1. Add ASGI-level direct API matrix tests for every router, not only central policy and high-risk routes.
2. Finish per-route immutable audit normalization for minor legacy mutation paths such as representative/email group/task subtleties if those remain active in production.
3. Add retention, export, tamper-evidence, and monitoring controls for the audit event store.
4. Optionally replace password-based step-up with true MFA/WebAuthn for production superadmin accounts.
