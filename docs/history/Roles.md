# RBAC Reference (Project DMS)

## Scope and sources
- Backend RBAC is implemented across `backend/rbac_backend/core`, `backend/rbac_backend/services`, `backend/rbac_backend/models`, and `backend/rbac_backend/routers`.
- Frontend RBAC is implemented in `client/src/config`, `client/src/hooks`, `client/src/components/auth`, and page-level guards in `client/src/pages`.
- Active API routers are wired in `backend/rbac_backend/main.py`. Archived RBAC code exists under `backend/archive` and is not wired.

## Identity and auth flow (backend)
- `backend/rbac_backend/core/security.py` defines JWT auth, the `CurrentUser` model, and the dev header fallback.
- `get_current_user` resolves identity via JWT first (email in `sub`), then optional dev headers if `ALLOW_DEV_HEADERS=True`.
- Role normalization (aliases) is applied in JWT flows via `ROLE_ALIASES`, but dev headers only lowercase roles (no alias mapping).
- `get_current_active_user` blocks disabled users.

## Role model and normalization
- Role schema: `backend/rbac_backend/models/role.py`
  - Fields: `name`, `description`, `permissions`, `scope` (system/organization/project), `organization_id`, `project_id`, `is_system`, `is_active`, timestamps.
- Role aliases (normalization):
  - Backend: `backend/rbac_backend/core/security.py` and `backend/rbac_backend/services/permission_service.py`.
  - Frontend: `client/src/hooks/useRBAC.ts`.
- System role keys: `superadmin`, `superuser` (see `backend/rbac_backend/services/role_service.py`).

## Role-based user constraints
Defined in `backend/rbac_backend/models/user.py`:
- `superadmin` cannot have `organization_id`, `organizations`, or `projects`.
- `superuser` must have `organizations`; `organization_id` must be one of them.
- `orgadmin`/`orguser` require `organization_id`.
- `projectadmin`/`projectuser` require `organization_id` and at least one project.

## Role assignment rules
Two layers enforce role assignment:

1) Core validation (`backend/rbac_backend/core/security.py` -> `validate_role_assignment`)
- `superadmin`: may assign any role.
- `orgadmin`: cannot assign `superadmin` or `orgadmin`.
- `projectadmin`: cannot assign `superadmin`, `orgadmin`, or `projectadmin`.
- `orguser` / `projectuser`: cannot assign roles.

2) Users controller enforcement (`backend/rbac_backend/routers/users.py`)
- `_validate_user_context` and `_enforce_role_assignment_scope` add org/project scope checks:
  - Non-superadmin must assign within their own org.
  - Project-scoped roles require project assignments.
  - Org/project admins cannot assign roles outside their org/projects.

## Role management constraints
`backend/rbac_backend/services/role_service.py`:
- Only `superadmin` can create or manage system roles (`is_system=True` or `scope=system`).
- `orgadmin` can only manage organization-scoped roles in their org.
- `projectadmin` can only manage project-scoped roles in their projects.
- Reserved keys (e.g., `orgadmin`, `projectadmin`) are blocked for non-superadmin creation.

## Default roles (initial data)
Seeded in `backend/rbac_backend/initial_data/default_roles.py`:
- superadmin (system)
  - permissions: docs:view, docs:create, docs:edit, docs:delete, docs:approve, docs:share, docs:upload, docs:comment, tags:read, tags:create, tags:update, tags:delete, projects:view, users:read, users:create, users:update, users:delete, roles:read, roles:create, roles:update, roles:delete, permissions:read, orgs:view, parties:read, parties:create, parties:update, parties:delete, representatives:create, representatives:update, representatives:delete, concerns:read, concerns:create, concerns:update, concerns:delete
- orgadmin (organization)
  - permissions: same as superadmin plus orgs:create, orgs:edit, orgs:delete
- orguser (organization)
  - permissions: docs:view/create/edit/delete/approve/share/upload/comment, tags:read, projects:view, users:read, permissions:read, orgs:view, parties:read/update, representatives:update, concerns:read/update
- contractmgr_org (organization)
  - permissions: same as orguser
- projectadmin (project)
  - permissions: docs:view/create/edit/delete/approve/share/upload/comment, tags:read/create/update/delete, projects:view, users:read/create/update, roles:read/create/update/assign, permissions:read, orgs:view, parties:read, concerns:read
- projectuser (project)
  - permissions: docs:view/create/edit/delete/approve/share/upload/comment, tags:read, projects:view, users:read, permissions:read, orgs:view, parties:read/update, representatives:update, concerns:read/update
- doccontroller (organization)
  - permissions: docs:view, docs:upload, tags:read, projects:view, permissions:read
- reporter (organization)
  - permissions: reports:view, reports:create, tags:read
- settings_manager (organization)
  - permissions: settings:view, settings:edit, tags:read
- limited_user (organization)
  - permissions: docs:view, tags:read, projects:view

Additional default roles in `backend/rbac_backend/services/role_service.py`:
- superadmin: permissions ["*"] (wildcard)
- orgadmin: users/documents/projects CRUD subset
- user: documents read/create/update and profile read/update

## Permission model and naming
`backend/rbac_backend/models/permission.py`:
- Permission names must be `resource:action`.
- Action levels: read, create, update, delete, admin.
- Categories: user_management, document_management, project_management, role_management, system_administration, email_management, audit_management.

## Default permission catalogs

1) Canonical list in `backend/rbac_backend/models/permission.py` (DEFAULT_PERMISSIONS):
- users:read/create/update/delete
- documents:read/create/update/delete/approve/share/upload/comment
- tags:read/create/update/delete
- letter_templates:read/create/update/delete
- projects:read/create/update/delete/assign
- organizations:read/create/update/delete
- roles:read/create/update/delete/assign/superuser
- emails:send/send_notifications/view_templates/view_history
- profile:read/update
- system:admin

2) Initial seed list in `backend/rbac_backend/initial_data/default_permissions.py`:
- users:read/create/update/delete
- roles:read/create/update/delete
- permissions:read
- docs:view/create/edit/delete/approve/share/upload/comment
- tags:read/create/update/delete
- projects:view/create/edit/delete/assign
- orgs:view/create/edit/delete
- parties:read/create/update/delete
- representatives:read/create/update/delete
- concerns:read/create/update/delete
- reports:view/create
- settings:view/edit

3) Frontend permission groups (UI)
- `client/src/pages/PermissionsPage.tsx` defines groups for documents, projects, users/roles, organizations.
- `client/src/constants/entityPermissions.ts` defines organizations/projects CRUD permissions.

## Permission aliasing and special cases
`backend/rbac_backend/services/permission_service.py`:
- Aliases map legacy names to canonical names, for example:
  - organizations:read -> orgs:view
  - projects:read -> projects:view
  - documents:read -> docs:view / letters:view
  - documents:upload -> docs:upload / letters:upload
  - tags:read -> docs:view / documents:read
- Wildcard permission "*" grants all active permissions.
- Special cases:
  - users:create is granted for roles in {superadmin, orgadmin, projectadmin}.
  - users:read is granted for roles in {orgadmin, orguser, projectadmin, projectuser}.

## Permission enforcement and scope (backend)

Core dependency
- `backend/rbac_backend/core/security.py` -> `require_permission`:
  - Uses `PermissionService.user_has_permission`.
  - Superadmin bypass.
  - Audit logs permission checks.

Authorization service
- `backend/rbac_backend/services/authorization_service.py`:
  - Centralized `require_permission` wrapper (uses PermissionService).
  - Query builders for organizations, users, documents, tags, parties, email groups, letter templates.
  - NOTE: `check_letter_access`, `check_letter_creation_permission`, `check_party_access`, and `check_project_access` are stubs (no enforcement).

Scope helpers
- `authorize_scope` and `build_scope_query` in `backend/rbac_backend/core/security.py`:
  - superadmin: global access.
  - orgadmin/orguser: org scope (and optionally project subset if assigned).
  - projectadmin/projectuser: project scope in their org.

Resource-level checks
- `PermissionService.check_resource_access` enforces membership for documents, projects, organizations.

## Backend endpoints and required permissions
Permissions referenced by routers (from `backend/rbac_backend/routers`):
- users:create/read/update/delete/lock/unlock -> `backend/rbac_backend/routers/users.py`
- roles:read/create/update/delete/assign -> `backend/rbac_backend/routers/roles.py` and `backend/rbac_backend/routers/permissions.py`
- roles:superuser -> permission CRUD in `backend/rbac_backend/routers/permissions.py`
- permissions:read -> permission list in `backend/rbac_backend/routers/permissions.py`
- organizations:read/create/update/delete -> `backend/rbac_backend/routers/organizations.py`
- projects:read/create/update/delete -> `backend/rbac_backend/routers/projects.py`
- documents:read/create/update/delete/comment/upload/share -> `backend/rbac_backend/routers/documents.py` and `backend/rbac_backend/routers/email_share.py`
- tags:read/create/update/delete -> `backend/rbac_backend/routers/tags.py`
- parties:read/create/update/delete -> `backend/rbac_backend/routers/parties.py`
- concerns:read/create/update/delete -> `backend/rbac_backend/routers/concerns.py`
- input_requests:read/create/respond/update/admin -> `backend/rbac_backend/routers/input_requests.py`
- letter_templates:read/create/update/delete -> `backend/rbac_backend/routers/letter_templates.py`
- email_groups:read/create/update/delete -> `backend/rbac_backend/routers/email_groups.py`
- emails:send/send_notifications/view_templates/view_history -> `backend/rbac_backend/routers/email.py`
- performance:admin/superadmin -> `backend/rbac_backend/routers/performance.py`

Other RBAC-protected endpoints:
- `backend/rbac_backend/routers/projects.py` uses `PermissionService.check_resource_access` for project-level access.
- `backend/rbac_backend/routers/documents.py` uses `AuthorizationService.build_document_query` and `check_document_access` (scope-based).
- `backend/rbac_backend/routers/contracts.py` uses `authorize_scope` (no explicit permission checks).
- `backend/rbac_backend/routers/storage_settings.py` uses `authorize_scope` (role scope).
- `backend/rbac_backend/routers/letters.py` uses `AuthorizationService.check_letter_access` (currently a stub).
- `backend/rbac_backend/routers/representatives.py` delegates to `AuthorizationService` for party/org/project access (party/project checks are stubs).

## Frontend RBAC

Role and route gating
- Roles are defined in `client/src/config/rolePermissions.ts`.
- Role-based routing uses `RoleGuard` in `client/src/components/auth/RoleGuard.tsx`.
- Routes gated by role: `/register`, `/letters`, `/health` (superadmin-only by default).
- Sidebar uses `isRouteAllowed` to show/hide navigation links.

Permission checks in UI
- `useRBAC` (`client/src/hooks/useRBAC.ts`) loads roles from localStorage or `/me`, fetches `/roles`, and collects permissions for those roles.
- `useHasPermission` (`client/src/hooks/useHasPermission.ts`) calls `/permissions/check` for server-side permission checks.
- Pages using permission checks:
  - `client/src/pages/ProjectsPage.tsx` (projects CRUD)
  - `client/src/pages/OrganizationsPage.tsx` (organizations CRUD)
  - `client/src/pages/RegisterPage.tsx` (org/project create/update)
  - `client/src/pages/UsersPage.tsx` (users CRUD)
  - `client/src/pages/DocumentsPage.tsx` (documents delete)
  - `client/src/pages/TagsPage.tsx` (tags CRUD)

Frontend permission management
- `client/src/pages/PermissionsPage.tsx` and `client/src/pages/PermissionsPageImproved.tsx` manage role permissions via `/roles` and `/roles/{role}/permissions`.

## Known gaps and improvements
1) Align permission naming and defaults
- Multiple catalogs exist (model defaults, initial_data defaults, UI lists) with mismatched names (docs:* vs documents:*; orgs:* vs organizations:*; projects:view vs projects:read).
- Consolidate into one canonical catalog and update seeds + UI.

2) Add missing permissions to canonical list
- Permissions referenced by routers but missing from `models/permission.DEFAULT_PERMISSIONS` include: permissions:read, parties:*, concerns:*, email_groups:*, input_requests:*, performance:*, representatives:*, reports:*, settings:*, users:lock/unlock.

3) Fix superuser handling
- `authorize_scope` and `build_scope_query` do not recognize `superuser`; superuser requests can be denied unexpectedly.
- Add explicit superuser handling in scope logic and permission checks.

4) Fix incorrect authorize_scope usage
- `backend/rbac_backend/routers/ai_assistant.py` and `backend/rbac_backend/routers/deep_planning.py` call `authorize_scope(current_user, "letter:write")` which treats "letter:write" as an org id.
- Replace with `require_permission` or call `authorize_scope` with real org/project ids from the request.

5) Replace permissive stubs
- `AuthorizationService.check_letter_access`, `check_letter_creation_permission`, `check_party_access`, `check_project_access` are no-ops. Implement real scope checks or enforce permissions consistently.

6) Normalize frontend permissions with aliases
- `useRBAC` reads raw role permission strings and does not apply alias mapping. Roles seeded with docs:* will not match UI checks for documents:*.
- Add alias normalization in frontend or migrate role permissions to canonical names.

7) Cleanup dev headers usage
- `client/src/services/enhanced-api.ts` always attaches X-User-* headers even when JWT is present.
- Limit dev headers to no-token flows to avoid confusing behavior.

8) Data initialization mismatch
- `DataInitializer.initialize_permissions` uses `Permission` model but the initial permission seed lacks required fields (category/resource/action). This likely fails.
- Either enrich initial seeds or build permissions using `PermissionService._build_permission_from_doc`.

9) Notification RBAC query bugs
- `backend/rbac_backend/services/rbac_service.py` and `backend/rbac_backend/utils/notification_service.py` build Mongo queries with empty keys (e.g., `query[""]`).
- Fix queries to use proper `$or` / `$in` operators.

10) Unify permission checking pathways
- The codebase uses `core.security.require_permission`, `AuthorizationService.require_permission`, and a debug `has_permission` helper.
- Standardize on one path and remove debug `print` statements in `core/security.has_permission`.

## Rollout & regression checklist
- Run seed/init: verify `initialize_permissions` and `initialize_roles` succeed with canonical names (no missing fields).
- Smoke auth scope: superadmin (global), superuser (assigned orgs/projects), orgadmin/orguser, projectadmin/projectuser across `/users`, `/projects`, `/documents`, `/tags`, `/email-groups`, `/parties`, `/letters` (AI assistant/deep planning).
- Permission enforcement: `/permissions`, `/roles`, `/performance`, `/input_requests`, `/representatives`, `/reports`, `/settings`, `users:lock/unlock`.
- Notification flow: emit + list/unread + mark read/all read.
- Frontend: `useRBAC` permissions normalized, dev headers only when no JWT, permissions UI reflects canonical names; guarded routes/buttons still behave.
