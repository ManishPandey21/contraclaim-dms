# Contraclaim DMS Production Readiness Audit Report

Audit date: 2026-05-19  
Repository root: `c:\SaaS\projectDMS`  
Scope reviewed: active React routes, FastAPI backend routers/services/models, MongoDB/index code, auth/RBAC, file/email/AI integrations, Docker/CI/deployment scripts, and supporting documentation.

> Important context: this repository currently has a dirty worktree with many modified and untracked files. This report audits the working tree as present on 2026-05-19, not a clean committed baseline.

---

## 1. Executive Summary

### Overall Assessment

Contraclaim DMS is a substantial application with real production-readiness work already present: FastAPI request logging, production config validation, disabled API docs in production, MongoDB replica-set checks, many Mongo indexes, secure-ish upload streaming, S3 abstractions, CI checks, Docker Compose production topology, health/readiness endpoints, and a growing frontend test suite.

It is **not production ready yet**. The biggest blocker is consistency: several frontend pages and hooks call backend endpoint families that are not mounted in `backend/rbac_backend/main.py`; auth is split between HttpOnly cookies and `localStorage` bearer tokens/role context; default seeded users still use `password`; several sensitive workflows lack a uniform permission/CSRF/step-up model; and frontend pages are large, unevenly tested, and only partially hardened for mobile/accessibility/error states.

### Launch Recommendation

**Current Recommendation:** Not production ready.

The repository can become conditionally production ready after critical fixes around endpoint mounting, auth/session hardening, seed data controls, authorization consistency, route-level QA, and deployment verification.

### Overall Production Readiness Score

| Category | Current Score | Estimated Score After Improvements |
|---|---:|---:|
| Overall Repository | 64/100 | 88/100 |

### Biggest Risks

- **Broken user workflows from unmounted routers:** `folder_structure.py`, `tasks.py`, `search.py`, `input_requests.py`, `email.py`, `concerns.py`, and `performance.py` define endpoints, but `main.py` does not mount them. Frontend code calls several of these paths.
- **Auth/session risk:** the backend now supports HttpOnly cookies, but the frontend still stores access tokens and role context in `localStorage`, and `ProtectedRoute` treats locally stored role/user keys as an authenticated context.
- **Default users:** `backend/rbac_backend/initial_data/default_users.py` creates accounts such as `superadmin@example.com` with password `password`.
- **Authorization inconsistency:** mature permission checks exist on documents/users/roles/projects, but many routers only require authentication and rely on service-level checks or incomplete scope checks.
- **Production verification gap:** CI exists, but full E2E, migration/restore rehearsal, browser responsive verification, and security regression tests are incomplete.

### Most Urgent Fixes

- Mount or remove every frontend-referenced backend router and align frontend service paths with actual backend paths.
- Remove `localStorage` bearer-token dependency; use HttpOnly cookie sessions plus CSRF protection for unsafe methods.
- Disable or gate default seed users in production and force password rotation.
- Normalize all routers through `get_current_user`, `PolicyService`, `require_permission`, and tenant scope checks.
- Add E2E coverage for login, organization/project scoping, upload, document viewer, sharing, letter drafting, contract upload/search/QA, and admin settings.

---

## 2. Scoring Methodology

Scores are assigned from 0 to 100.

| Score Range | Meaning |
|---|---|
| 90-100 | Production ready |
| 75-89 | Mostly ready with minor improvements |
| 60-74 | Needs important improvements |
| 40-59 | High risk, not production ready |
| 0-39 | Critical gaps, unsafe for production |

Scoring weights used:

| Dimension | Weight |
|---|---:|
| Security and tenant isolation | 25% |
| Functional completeness | 20% |
| Reliability and data integrity | 15% |
| UX, accessibility, responsiveness | 15% |
| Maintainability | 10% |
| Performance and scalability | 10% |
| Test/deployment readiness | 5% |

---

## 3. Repository Structure Overview

| Area | Path | Purpose | Production Notes |
|---|---|---|---|
| Frontend app | `client/src` | Vite React, shadcn/Radix UI, route pages, services, hooks | Lazy-loaded routes exist, but several pages call unmounted/mismatched endpoints. |
| Frontend routes | `client/src/routes.tsx` | SPA routing and protected layout | 39 active routes plus login/not-found. |
| Frontend services | `client/src/services` | Axios/fetch wrappers | API path consistency is uneven; some legacy endpoints are referenced. |
| Backend app | `backend/rbac_backend/main.py` | FastAPI app and mounted routers | Several router modules are not mounted. |
| Backend routers | `backend/rbac_backend/routers` | API endpoints | Auth/RBAC quality varies by router. |
| Backend services | `backend/rbac_backend/services` | Business logic, storage, auth, AI, email, document processing | Broad service layer, but many services need transaction/idempotency review. |
| Backend models | `backend/rbac_backend/models` | Pydantic models/schemas | Good coverage, but Mongo constraints are mostly application-side. |
| Database | `backend/rbac_backend/core/database.py` | Motor client and index creation | Many useful indexes; no formal migration tool. |
| Config | `.env.example`, `backend/rbac_backend/core/config.py`, `client/src/config/api.ts` | Runtime settings | Production validation exists; local `.env` exists in workspace and must remain untracked. |
| Deployment | `docker-compose.prod.yml`, `Dockerfile`, `config/httpd.conf`, `scripts/*` | Production Compose, Apache gateway, backup/restore scripts | Strong baseline; TLS, secrets manager, DR rehearsal still needed. |
| CI/CD | `.github/workflows/ci.yml` | Secret scan, backend/frontend tests, dependency scan, image scan | Good baseline; no deploy pipeline or E2E browser job. |
| Tests | `backend/rbac_backend/tests`, `client/src/pages/__tests__`, `client/src/tests` | Backend and frontend tests | 134 backend test files/items found, 25 frontend test files/items found; coverage is uneven. |
| Docs | `docs`, root `*.md` | Architecture, deployment, audits, runbooks | Rich but fragmented and sometimes stale. |
| Archives | `backend/archive`, `client/archive` | Old code | Should be excluded from production build/security surface or moved out of repo. |

---

## 4. Frontend Page-by-Page Audit

### Frontend Audit Summary

| Page / Route | File Path | Current Score | Improved Score | Priority |
|---|---|---:|---:|---|
| Landing `/` | `client/src/pages/LandingPage.tsx` | 62 | 82 | Medium |
| Login `/login` | `client/src/pages/LoginPage.tsx` | 66 | 88 | Critical |
| Overview `/overview` | `client/src/pages/Overview.tsx` | 55 | 78 | Medium |
| Dashboard `/dashboard` | `client/src/pages/Dashboard.tsx` | 70 | 88 | High |
| Organizations `/organizations` | `client/src/pages/OrganizationsPage.tsx` | 72 | 90 | High |
| Projects `/projects` | `client/src/pages/ProjectsPage.tsx` | 73 | 90 | High |
| Documents `/documents`, `/documents/legacy` | `client/src/pages/DocumentsPage.tsx` | 76 | 91 | Critical |
| Enhanced Search `/documentsearch` | `client/src/pages/EnhancedDocumentsPage.tsx` | 68 | 86 | High |
| Tags `/tags` | `client/src/pages/TagsPage.tsx` | 70 | 88 | Medium |
| Profile `/profile` | `client/src/pages/ProfilePage.tsx` | 73 | 88 | Medium |
| Users `/users` | `client/src/pages/UsersPage.tsx` | 72 | 90 | Critical |
| Permissions `/permissions` | `client/src/pages/PermissionsPage.tsx` | 70 | 90 | Critical |
| Settings `/settings` | `client/src/pages/SettingsPage.tsx` | 52 | 80 | High |
| Plan Settings `/plan-settings` | `client/src/pages/PlanSettingsPage.tsx` | 72 | 88 | High |
| Notifications `/notifications` | `client/src/pages/NotificationCenterPage.tsx` | 70 | 86 | Medium |
| Upload `/upload` | `client/src/pages/UploadPage.tsx` | 74 | 90 | Critical |
| Document Viewer `/documentviewer/:id` | `client/src/pages/DocumentViewerPage.tsx` | 75 | 91 | Critical |
| Share `/share/:id` | `client/src/pages/ShareDocumentPage.tsx` | 72 | 90 | Critical |
| Register `/register` | `client/src/pages/RegisterPage.tsx` | 66 | 88 | Critical |
| Folders `/folders` | `client/src/pages/FolderStructurePage.tsx` | 42 | 84 | Critical |
| Tasks `/tasks` | `client/src/pages/TasksPage.tsx` | 40 | 82 | Critical |
| Parties `/parties` | `client/src/pages/PartiesInvolvedPage.tsx` | 72 | 89 | High |
| Letters `/letters` | `client/src/pages/LetterWorkflowPage.tsx` | 74 | 90 | Critical |
| Letter Summary `/documents/summary/:id` | `client/src/pages/LetterSummaryPage.tsx` | 72 | 88 | High |
| Letter Input `/letters/:id/input` | `client/src/pages/LetterInputPage.tsx` | 71 | 88 | High |
| Strategic Plan `/letters/:id/strategic-plan`, `/strategy` | `client/src/pages/LetterStrategicPlanPage.tsx` | 73 | 89 | High |
| Letter Draft `/letters/:id/draft` | `client/src/pages/LetterDraftPage.tsx` | 75 | 90 | Critical |
| Letter Review `/letters/:id/review` | `client/src/pages/LetterReviewPage.tsx` | 68 | 86 | High |
| Letter Approval `/letters/:id/approval` | `client/src/pages/LetterApprovalPage.tsx` | 68 | 86 | High |
| Letter Completed `/letters/:id/completed` | `client/src/pages/LetterCompletedPage.tsx` | 65 | 84 | Medium |
| Letter Quality `/letter-quality` | `client/src/pages/LetterQualityDashboardPage.tsx` | 70 | 88 | High |
| Reports `/reports` | `client/src/pages/ReportsAnalyticsPage.tsx` | 66 | 86 | High |
| Letter Templates `/letter-templates` | `client/src/pages/LetterTemplatePage.tsx` | 72 | 89 | High |
| Template Editor `/letter-templates/:id/edit` | `client/src/pages/LetterTemplateEditorPage.tsx` | 72 | 89 | High |
| Representatives `/representatives` | `client/src/pages/RepresentativesPage.tsx` | 67 | 86 | Medium |
| Contracts `/contracts` | `client/src/pages/ContractsPage.tsx` | 58 | 82 | High |
| Contracts Upload `/contracts/upload` | `client/src/pages/ContractsUploadPage.tsx` | 75 | 90 | Critical |
| Contracts Search `/contracts/search` | `client/src/pages/ContractsSearchPage.tsx` | 73 | 89 | High |
| Contract QA `/contracts/qa` | `client/src/pages/ContractQAPage.tsx` | 70 | 88 | High |
| Reference `/reference/:id` | `client/src/pages/ReferencePage.tsx` | 73 | 89 | High |
| Health `/health` | `client/src/pages/HealthPage.tsx` | 78 | 92 | Medium |
| Not Found `*` | `client/src/pages/NotFound.tsx` | 70 | 85 | Low |

### Page: Landing `/`

**File Path(s):** `client/src/pages/LandingPage.tsx`

**Purpose:** Public marketing/contact entry page.

**Current Production Readiness Score:** 62/100

**Findings:**
- Public route is intentionally unprotected.
- Calls/contact behavior needs verification against `backend/rbac_backend/routers/contact.py`, which is mounted at `/api/contact`.
- Uses visual assets and large page content; performance budget and responsive screenshots were not verified in this audit.

**Risks:** Slow first paint, form spam without public rate limiting, and inconsistent public error handling.

**UX Review:** Loading state: partial. Empty state: not applicable. Error state: partial. Mobile responsiveness: needs verification. Accessibility: needs improvement.

**Security Review:** Contact endpoint is public; ensure IP/email rate limits and bot controls.

**Recommended Improvements:**
- Add contact endpoint rate limiting and anti-automation controls.
- Add frontend form submission success/failure tests.
- Validate mobile hero/text contrast and bundle size.

**Estimated Score After Improvements:** 82/100  
**Priority:** Medium

### Page: Login `/login`

**File Path(s):** `client/src/pages/LoginPage.tsx`, `client/src/hooks/use-auth.ts`, `client/src/services/auth.ts`, `client/src/services/session-api.ts`

**Purpose:** Authenticates users and initializes session/RBAC context.

**Current Production Readiness Score:** 66/100

**Findings:**
- Backend sets HttpOnly cookie, but frontend also persists `accessToken`, `user_roles`, `user_id`, `org_id`, and `proj_id` in `localStorage`.
- `useAuth.hasStoredSessionContext()` treats role/user localStorage keys as authentication context even without a valid token.
- “Forgot password?” is a dead `href="#"` interaction.
- Login form has basic zod validation and visible error state.

**Risks:** XSS token theft, stale client roles causing incorrect UX access, login confusion, and CSRF exposure once cookie auth is relied on for unsafe methods.

**UX Review:** Loading state: present on submit. Empty state: not applicable. Error state: present. Mobile responsiveness: partial. Accessibility: partial.

**Security Review:** High risk until `localStorage` token storage is removed and CSRF protection is added.

**Recommended Improvements:**
- Use HttpOnly secure cookies as the only session transport.
- Replace client-stored roles with `/api/me` or a signed short-lived session profile.
- Add CSRF token/header validation for cookie-authenticated unsafe methods.
- Implement password reset or remove the dead link.

**Estimated Score After Improvements:** 88/100  
**Priority:** Critical

### Page: Overview `/overview`

**File Path(s):** `client/src/pages/Overview.tsx`

**Purpose:** Navigation/summary landing page after login.

**Current Production Readiness Score:** 55/100

**Findings:** Mostly static card navigation, limited data integration, no loading/error needs, and no clear operational value beyond shortcuts.

**Risks:** Users land on a low-information page after login; route may hide authorization or data failures.

**Recommended Improvements:** Add scoped project/org context, recent work, alerts, and role-aware actions.

**Estimated Score After Improvements:** 78/100  
**Priority:** Medium

### Page: Dashboard `/dashboard`

**File Path(s):** `client/src/pages/Dashboard.tsx`, `client/src/services/dashboard-api.ts`, `backend/rbac_backend/routers/dashboard.py`

**Purpose:** Displays scoped DMS statistics.

**Current Production Readiness Score:** 70/100

**Findings:** Loading/error/empty patterns exist; backend endpoint `/api/dashboard/stats` is mounted and permission-gated with `documents:read`.

**Risks:** Aggregation accuracy depends on consistent document status fields; frontend should handle partial backend degradation.

**Recommended Improvements:** Add contract/letter separation, stale-data indicators, and API integration tests for scoped dashboard totals.

**Estimated Score After Improvements:** 88/100  
**Priority:** High

### Page: Organizations `/organizations`

**File Path(s):** `client/src/pages/OrganizationsPage.tsx`, `client/src/services/organizations-api.ts`, `backend/rbac_backend/routers/organizations.py`

**Purpose:** Organization CRUD and stats.

**Current Production Readiness Score:** 72/100

**Findings:** Backend is mounted and uses permission dependencies; page has loading/error/empty state patterns.

**Risks:** Delete/deactivate semantics need audit trail and impact preview before production.

**Recommended Improvements:** Add destructive-action step-up, dependency impact modal, and tests for tenant visibility.

**Estimated Score After Improvements:** 90/100  
**Priority:** High

### Page: Projects `/projects`

**File Path(s):** `client/src/pages/ProjectsPage.tsx`, `client/src/services/projects-api.ts`, `backend/rbac_backend/routers/projects.py`

**Purpose:** Project CRUD, filtering, and notification settings.

**Current Production Readiness Score:** 73/100

**Findings:** Backend routes are mounted and permission-gated for core CRUD; tests exist for organization filtering.

**Risks:** Project notification settings endpoints have auth but no explicit permission dependency in parsed route map.

**Recommended Improvements:** Add explicit permission checks for notification settings and destructive-action confirmation with audit entries.

**Estimated Score After Improvements:** 90/100  
**Priority:** High

### Page: Documents `/documents`, `/documents/legacy`

**File Path(s):** `client/src/pages/DocumentsPage.tsx`, `client/src/services/documents-api.ts`, `client/src/services/enhanced-api.ts`, `backend/rbac_backend/routers/documents.py`

**Purpose:** Main letters/documents library with filters, bulk actions, draft requests, references, comments, and exports.

**Current Production Readiness Score:** 76/100

**Findings:**
- Backend document routes are mounted and many endpoints use `require_permission`.
- Page is very large, at 2,219 lines, which raises maintainability risk.
- Tests exist for under-process guard and draft navigation.
- Upload/download/audit logic is materially better than average, including document access checks and audit events.

**Risks:** Complex UI state regressions, inconsistent metadata naming, possible performance issues on large lists, and partial optimistic-concurrency coverage.

**Recommended Improvements:**
- Split table, filters, bulk actions, share/draft flows, and metadata actions into tested components.
- Require pagination/virtualization across all list variants.
- Add E2E tests for upload -> process -> view -> share -> export.

**Estimated Score After Improvements:** 91/100  
**Priority:** Critical

### Page: Enhanced Search `/documentsearch`

**File Path(s):** `client/src/pages/EnhancedDocumentsPage.tsx`, `client/src/services/search-api.ts`, `backend/rbac_backend/routers/search.py`

**Purpose:** Advanced document search and filters.

**Current Production Readiness Score:** 68/100

**Findings:** Frontend service references `/search/documents`, `/search/suggestions`, `/search/popular`, `/search/track`, `/search/analytics`, `/search/semantic`, plus `/search/fulltext` and `/search/similar/{id}`. `search.py` is not mounted in `main.py`; even if mounted, `/search/fulltext`, `/search/documents/{id}`, and `/search/similar/{id}` are not defined in the parsed backend router.

**Risks:** Search UI can fail in production or silently use stale alternative APIs.

**Recommended Improvements:** Mount `search.router`, remove unsupported frontend endpoints, and add contract tests from `search-api.ts` to backend route inventory.

**Estimated Score After Improvements:** 86/100  
**Priority:** High

### Page: Tags `/tags`

**File Path(s):** `client/src/pages/TagsPage.tsx`, `client/src/services/tags-api.ts`, `backend/rbac_backend/routers/tags.py`

**Purpose:** Tag/subtag management.

**Current Production Readiness Score:** 70/100

**Findings:** Backend route is mounted and service-level permission calls exist; UI has loading/error patterns.

**Risks:** Tenant scoping and duplicate tag constraints need stronger database enforcement.

**Recommended Improvements:** Add unique scoped tag indexes, stronger validation, and route-level permission dependencies.

**Estimated Score After Improvements:** 88/100  
**Priority:** Medium

### Page: Profile `/profile`

**File Path(s):** `client/src/pages/ProfilePage.tsx`, `backend/rbac_backend/routers/profiles.py`

**Purpose:** User profile, photo, password changes.

**Current Production Readiness Score:** 73/100

**Findings:** Profile endpoints are mounted; password models enforce stronger password validation; profile photo upload requires file constraints review.

**Risks:** Profile photo storage and content-type checks need explicit security tests.

**Recommended Improvements:** Add upload validation tests and password-change audit events.

**Estimated Score After Improvements:** 88/100  
**Priority:** Medium

### Page: Users `/users`

**File Path(s):** `client/src/pages/UsersPage.tsx`, `backend/rbac_backend/routers/users.py`

**Purpose:** User administration, lock/unlock, user CRUD.

**Current Production Readiness Score:** 72/100

**Findings:** Backend routes use explicit permissions; user models include password validation.

**Risks:** Admin operations need step-up, role-assignment policy must be enforced on every create/update path, and default seed users are unsafe.

**Recommended Improvements:** Force step-up for lock/delete/role changes, remove production seed users, add privilege escalation tests.

**Estimated Score After Improvements:** 90/100  
**Priority:** Critical

### Page: Permissions `/permissions`

**File Path(s):** `client/src/pages/PermissionsPage.tsx`, `backend/rbac_backend/routers/permissions.py`, `backend/rbac_backend/routers/roles.py`

**Purpose:** Role and permission administration.

**Current Production Readiness Score:** 70/100

**Findings:** Both `roles.py` and `permissions.py` define role endpoints, and both are mounted. This creates duplicate `/api/roles` route families.

**Risks:** Route collision/maintenance drift, privilege escalation, and inconsistent UI behavior.

**Recommended Improvements:** Consolidate role endpoints into one router, add step-up, and add tests for forbidden role assignment.

**Estimated Score After Improvements:** 90/100  
**Priority:** Critical

### Page: Settings `/settings`

**File Path(s):** `client/src/pages/SettingsPage.tsx`, `client/src/components/settings/*`

**Purpose:** Application settings shell.

**Current Production Readiness Score:** 52/100

**Findings:** Page is currently shallow compared with available storage/SMTP/notification setting components.

**Risks:** Operators may assume configuration is complete when important settings are hidden elsewhere.

**Recommended Improvements:** Make settings the canonical admin console for storage, SMTP, notifications, security, and feature flags.

**Estimated Score After Improvements:** 80/100  
**Priority:** High

### Page: Plan Settings `/plan-settings`

**File Path(s):** `client/src/pages/PlanSettingsPage.tsx`, `client/src/services/plan-settings-api.ts`, `backend/rbac_backend/routers/rbac_monetization.py`

**Purpose:** Plan/package/effective service configuration.

**Current Production Readiness Score:** 72/100

**Findings:** Backend is mounted, but parsed route map shows auth without explicit permissions for monetization routes.

**Risks:** Billing/plan changes may be available to overly broad authenticated users unless service layer fully restricts.

**Recommended Improvements:** Add explicit `plans:*`, `billing:*`, and `entitlements:*` permissions plus audit trail.

**Estimated Score After Improvements:** 88/100  
**Priority:** High

### Page: Notifications `/notifications`

**File Path(s):** `client/src/pages/NotificationCenterPage.tsx`, `backend/rbac_backend/routers/notifications.py`

**Purpose:** Notification inbox/preferences/logs.

**Current Production Readiness Score:** 70/100

**Findings:** Routes are mounted and auth-protected. A test router is also mounted under `/api/notification-test`.

**Risks:** Test email endpoint exposure and delivery-log privacy must be checked in production.

**Recommended Improvements:** Gate test router to superadmin/non-production or remove it from production app.

**Estimated Score After Improvements:** 86/100  
**Priority:** Medium

### Page: Upload `/upload`

**File Path(s):** `client/src/pages/UploadPage.tsx`, `backend/rbac_backend/routers/documents.py`, `backend/rbac_backend/services/upload_streaming.py`

**Purpose:** Letter/document upload.

**Current Production Readiness Score:** 74/100

**Findings:** Backend has streaming upload, MIME sniffing, size limits, concurrency slots, and document access checks. Page is very large at 1,382 lines.

**Risks:** UI complexity, bulk upload failure recovery, and inconsistent metadata field validation.

**Recommended Improvements:** Add resumable upload progress, server-side metadata schema validation, and E2E tests for failed/large/invalid uploads.

**Estimated Score After Improvements:** 90/100  
**Priority:** Critical

### Page: Document Viewer `/documentviewer/:id`

**File Path(s):** `client/src/pages/DocumentViewerPage.tsx`, `client/src/components/document-viewer/*`, `backend/rbac_backend/routers/documents.py`

**Purpose:** View PDF/document details, metadata, references, enclosures.

**Current Production Readiness Score:** 75/100

**Findings:** Backend download route authorizes and avoids stored public URLs. Viewer has loading/error/empty patterns and a dedicated component set.

**Risks:** PDF worker is vendored in `client/public/pdf.worker.min.js`; dependency and CSP compatibility need maintenance. Metadata edits need concurrency conflict handling in UI.

**Recommended Improvements:** Add viewer E2E tests, revision conflict UI, and CSP/browser validation.

**Estimated Score After Improvements:** 91/100  
**Priority:** Critical

### Page: Share `/share/:id`

**File Path(s):** `client/src/pages/ShareDocumentPage.tsx`, `client/src/services/email-service.ts`, `backend/rbac_backend/routers/email_share.py`, `backend/rbac_backend/services/email_service.py`

**Purpose:** Share documents by email, with optional public token download.

**Current Production Readiness Score:** 72/100

**Findings:** Backend checks `documents:share` and document policy. Public share tokens are hashed at rest and expire, defaulting to 30 days.

**Risks:** Public download links are unauthenticated; they need shorter TTL defaults, revocation UI, download throttling, and recipient/audit visibility.

**Recommended Improvements:** Add token revocation, configurable TTL per share, rate limiting on public downloads, and recipient-scoped audit logs.

**Estimated Score After Improvements:** 90/100  
**Priority:** Critical

### Page: Register `/register`

**File Path(s):** `client/src/pages/RegisterPage.tsx`, `backend/rbac_backend/routers/users.py`

**Purpose:** Admin user registration.

**Current Production Readiness Score:** 66/100

**Findings:** Frontend is superadmin-gated, backend uses `users:create`. Page is large and role/account assignment is high impact.

**Risks:** Privilege escalation if role validation diverges; no mandatory step-up for creating powerful users.

**Recommended Improvements:** Require step-up for privileged roles, display generated permission impact, and add tests for disallowed role assignment.

**Estimated Score After Improvements:** 88/100  
**Priority:** Critical

### Page: Folders `/folders`

**File Path(s):** `client/src/pages/FolderStructurePage.tsx`, `backend/rbac_backend/routers/folder_structure.py`

**Purpose:** Folder tree and file operations.

**Current Production Readiness Score:** 42/100

**Findings:** `folder_structure.py` defines `/folder-structure` and `/upload-file`, but it is not mounted in `main.py`.

**Risks:** Page is functionally broken in production. If mounted, file/folder path operations need strict traversal tests.

**Recommended Improvements:** Mount the router or remove route, add path traversal tests, add explicit permissions.

**Estimated Score After Improvements:** 84/100  
**Priority:** Critical

### Page: Tasks `/tasks`

**File Path(s):** `client/src/pages/TasksPage.tsx`, `backend/rbac_backend/routers/tasks.py`

**Purpose:** Task management.

**Current Production Readiness Score:** 40/100

**Findings:** `tasks.py` defines `/tasks` CRUD but is not mounted in `main.py`.

**Risks:** Page is functionally broken in production.

**Recommended Improvements:** Mount router, add permissions/audit trail, or remove the page from active routes/sidebar.

**Estimated Score After Improvements:** 82/100  
**Priority:** Critical

### Page: Parties `/parties`

**File Path(s):** `client/src/pages/PartiesInvolvedPage.tsx`, `backend/rbac_backend/routers/parties.py`

**Purpose:** Parties/stakeholders management.

**Current Production Readiness Score:** 72/100

**Findings:** Backend router is mounted with auth and service-level authorization; UI has validation/error patterns.

**Risks:** Party/project association needs explicit route-level permission dependencies and uniqueness constraints.

**Recommended Improvements:** Add scoped unique indexes and permission dependencies.

**Estimated Score After Improvements:** 89/100  
**Priority:** High

### Page: Letters `/letters` and Letter Workflow Child Pages

**File Path(s):** `client/src/pages/LetterWorkflowPage.tsx`, `LetterInputPage.tsx`, `LetterStrategicPlanPage.tsx`, `LetterDraftPage.tsx`, `LetterReviewPage.tsx`, `LetterApprovalPage.tsx`, `LetterCompletedPage.tsx`, `backend/rbac_backend/routers/letters.py`, `backend/rbac_backend/routers/letter_drafting.py`

**Purpose:** Letter lifecycle, drafting, review, approval, issue/export, strategy planning.

**Current Production Readiness Score:** 72/100 average

**Findings:**
- Core `letters.py` and `letter_drafting.py` routers are mounted.
- Drafting v2 has audit/context/source-ledger/governance endpoints.
- Hooks still reference `/input-requests/...`, but `input_requests.py` is not mounted.

**Risks:** Broken input-request workflow, complex AI drafting state transitions, missing reviewer/approval permission granularity, and inconsistent source-grounding verification.

**Recommended Improvements:**
- Mount and harden `input_requests.py` or remove those client calls.
- Add role/state transition matrix tests for every drafting action.
- Add AI prompt/output trace redaction and retention policy.

**Estimated Score After Improvements:** 89/100  
**Priority:** Critical

### Page: Reports `/reports`

**File Path(s):** `client/src/pages/ReportsAnalyticsPage.tsx`, `backend/rbac_backend/routers/reports.py`

**Purpose:** Report preview/download.

**Current Production Readiness Score:** 66/100

**Findings:** Backend is mounted and auth-protected, but route map shows no explicit permission dependencies.

**Risks:** Sensitive data export exposure and expensive report generation.

**Recommended Improvements:** Add `reports:read/export` permissions, export audit events, pagination, and rate limits.

**Estimated Score After Improvements:** 86/100  
**Priority:** High

### Page: Letter Templates `/letter-templates`, `/letter-templates/:id/edit`

**File Path(s):** `client/src/pages/LetterTemplatePage.tsx`, `client/src/pages/LetterTemplateEditorPage.tsx`, `client/src/components/letter-template/RichTextEditor.tsx`, `backend/rbac_backend/routers/letter_templates.py`

**Purpose:** Manage reusable drafting templates.

**Current Production Readiness Score:** 72/100

**Findings:** Routes are mounted and authenticated; rich text editor introduces content sanitization concerns.

**Risks:** Stored HTML/template injection into generated letters or emails.

**Recommended Improvements:** Sanitize template HTML server-side and add preview/sanitization tests.

**Estimated Score After Improvements:** 89/100  
**Priority:** High

### Page: Representatives `/representatives`

**File Path(s):** `client/src/pages/RepresentativesPage.tsx`, `backend/rbac_backend/routers/representatives.py`

**Purpose:** Representative CRUD under parties/orgs/projects.

**Current Production Readiness Score:** 67/100

**Findings:** Backend is mounted and auth-protected; explicit route permissions are not visible in parsed endpoint signatures.

**Risks:** Tenant leakage or unauthorized representative updates if service checks are incomplete.

**Recommended Improvements:** Add explicit permission dependencies and tenant-scope tests.

**Estimated Score After Improvements:** 86/100  
**Priority:** Medium

### Page: Contracts `/contracts`, `/contracts/upload`, `/contracts/search`, `/contracts/qa`

**File Path(s):** `client/src/pages/ContractsPage.tsx`, `ContractsUploadPage.tsx`, `ContractsSearchPage.tsx`, `ContractQAPage.tsx`, `client/src/services/contracts-api.ts`, `backend/rbac_backend/routers/contracts.py`, `backend/rbac_backend/routers/retrieval_engine.py`

**Purpose:** Contract upload, ingestion, search, download, and RAG/QA.

**Current Production Readiness Score:** 69/100 average

**Findings:** Upload/search/status/download endpoints are mounted. Retrieval `/api/v1/retrieval/contract-qa` and `/rag` are mounted. Contract page itself is less integrated than upload/search pages.

**Risks:** AI answer correctness, vector sync drift, large upload recovery, and contract document access authorization consistency.

**Recommended Improvements:** Add ingestion job dashboards, answer citations enforcement, contract upload E2E tests, and queue dead-letter visibility.

**Estimated Score After Improvements:** 88/100  
**Priority:** Critical for upload, High for search/QA

### Page: Reference `/reference/:id`

**File Path(s):** `client/src/pages/ReferencePage.tsx`, `backend/rbac_backend/routers/documents.py`

**Purpose:** Display parsed references and linked document details.

**Current Production Readiness Score:** 73/100

**Findings:** Document reference endpoints are mounted and permission-gated.

**Risks:** Reference syncing correctness and backlink consistency require deeper data integrity tests.

**Recommended Improvements:** Add graph/reference reconciliation test cases and UI state for stale/missing linked documents.

**Estimated Score After Improvements:** 89/100  
**Priority:** High

### Page: Health `/health`

**File Path(s):** `client/src/pages/HealthPage.tsx`, `backend/rbac_backend/routers/health.py`, `backend/rbac_backend/routers/storage_sync.py`

**Purpose:** Operational health view.

**Current Production Readiness Score:** 78/100

**Findings:** Frontend route is superadmin-gated. Backend readiness and metrics endpoints exist; `/metrics` supports `X-Metrics-Token`.

**Risks:** `/health/ready` exposes dependency error details publicly; `/health/observability` is public and exposes operational snapshots.

**Recommended Improvements:** Keep liveness public, restrict detailed readiness/observability to internal network or admin token, and redact error strings.

**Estimated Score After Improvements:** 92/100  
**Priority:** Medium

### Page: Not Found `*`

**File Path(s):** `client/src/pages/NotFound.tsx`

**Purpose:** Fallback route.

**Current Production Readiness Score:** 70/100

**Findings:** Basic fallback exists.

**Recommended Improvements:** Add authenticated-safe navigation and telemetry for unknown routes.

**Estimated Score After Improvements:** 85/100  
**Priority:** Low

---

## 5. Backend Module and API Audit

### Backend Audit Summary

| Backend Area | File Path | Current Score | Improved Score | Priority |
|---|---|---:|---:|---|
| App bootstrap/middleware | `backend/rbac_backend/main.py` | 68 | 90 | Critical |
| Auth/session | `routers/auth.py`, `core/security.py`, `services/authentication_service.py` | 76 | 92 | Critical |
| Users | `routers/users.py`, `services/user_service.py` | 73 | 90 | Critical |
| Roles/permissions | `routers/roles.py`, `routers/permissions.py`, `services/permission_service.py` | 70 | 90 | Critical |
| Documents | `routers/documents.py`, `services/document_service.py`, `services/file_object_service.py` | 78 | 92 | Critical |
| Contracts | `routers/contracts.py`, `services/contract_service.py`, `services/contracts_ingest.py` | 75 | 90 | Critical |
| Letters | `routers/letters.py`, `services/letter_service.py` | 72 | 89 | High |
| Letter drafting v2 | `routers/letter_drafting.py`, `services/letter_drafting/*` | 76 | 91 | Critical |
| AI assistant/deep planning | `routers/ai_assistant.py`, `routers/deep_planning.py` | 62 | 86 | High |
| Retrieval engine | `routers/retrieval_engine.py`, `retrieval/*` | 68 | 88 | High |
| Email sharing | `routers/email_share.py`, `services/email_service.py` | 72 | 90 | Critical |
| Email groups | `routers/email_groups.py` | 70 | 86 | Medium |
| Organizations/projects | `routers/organizations.py`, `routers/projects.py` | 74 | 90 | High |
| Parties/representatives | `routers/parties.py`, `routers/representatives.py` | 70 | 88 | High |
| Tags | `routers/tags.py` | 70 | 88 | Medium |
| Reports | `routers/reports.py`, `services/report_service.py` | 66 | 86 | High |
| Monetization/plans | `routers/rbac_monetization.py`, `services/monetization_service.py` | 62 | 86 | High |
| Storage settings | `routers/storage_settings.py`, `services/storage_settings_service.py` | 70 | 88 | High |
| SMTP settings | `routers/smtp_settings.py`, `services/smtp_settings_service.py` | 72 | 90 | High |
| Storage sync | `routers/storage_sync.py` | 78 | 92 | Medium |
| Health/observability | `routers/health.py`, `services/observability.py` | 74 | 90 | Medium |
| WebSocket notifications | `routers/ws.py`, `utils/notification_service.py` | 58 | 84 | High |
| Unmounted routers | `routers/search.py`, `tasks.py`, `folder_structure.py`, `input_requests.py`, `email.py`, `concerns.py`, `performance.py` | 35 | 85 | Critical |

### Backend Area: App Bootstrap and Middleware

**File Path(s):** `backend/rbac_backend/main.py`

**Purpose:** FastAPI app construction, CORS, request logging, router mounting, startup/shutdown.

**Current Production Readiness Score:** 68/100

**Findings:**
- API docs are disabled when `ENVIRONMENT=production`.
- CORS origins come from settings, but methods/headers are wildcard.
- Request IDs and slow request logging are implemented.
- Many routers are mounted, but several active frontend-referenced routers are not.

**Security Concerns:** Missing security headers at FastAPI layer; relying on Apache gateway for headers. Wildcard CORS methods/headers with credentials should be narrowed.

**Reliability Concerns:** Startup invokes config validation and background services, but mounted route inventory is not tested against frontend services.

**Recommended Improvements:** Add route inventory contract test, mount/remove orphan routers, narrow CORS, and add application-level security headers for non-Apache deployments.

**Estimated Score After Improvements:** 90/100  
**Priority:** Critical

### Backend Area: Auth and Session Management

**File Path(s):** `backend/rbac_backend/routers/auth.py`, `backend/rbac_backend/core/security.py`, `backend/rbac_backend/utils/rate_limiter.py`

**Purpose:** Login, refresh, logout, current user, step-up token, role normalization.

**Current Production Readiness Score:** 76/100

**Findings:**
- Login has IP/email rate limiting, account lock/disabled checks, audit logging, and session ID binding.
- Refresh validates active session and user.
- Cookie helper sets HttpOnly cookies, configurable secure/samesite/domain.
- Dev header fallback is disabled unless `ALLOW_DEV_HEADERS=True`; production validation rejects it.

**Security Concerns:**
- Frontend still stores bearer tokens in `localStorage`.
- No visible CSRF mechanism for cookie-authenticated unsafe methods.
- `OAuth2PasswordBearer(tokenUrl="/api/token")` does not match primary `/api/login`, though custom extraction handles actual requests.

**Reliability Concerns:** Rate limiter falls back to local process memory when Redis is unavailable, which is not horizontally consistent.

**Recommended Improvements:** Cookie-only sessions, CSRF tokens, Redis-required production rate limits, refresh-token/session rotation policy, and route tests for invalidated sessions.

**Estimated Score After Improvements:** 92/100  
**Priority:** Critical

### Backend Area: Documents and File Handling

**File Path(s):** `backend/rbac_backend/routers/documents.py`, `backend/rbac_backend/services/upload_streaming.py`, `backend/rbac_backend/utils/file_validation.py`, `backend/rbac_backend/services/file_object_service.py`

**Purpose:** Document CRUD, upload, processing, references, comments, bulk upload, downloads, audit events.

**Current Production Readiness Score:** 78/100

**Findings:**
- Many endpoints require `documents:*` permissions.
- Uploads are spooled with max-size enforcement, SHA-256, MIME sniffing, and executable signature checks.
- Downloads avoid stored public URLs and enforce document policy.
- Internal processing endpoint uses `verify_langgraph_token`.

**Security Concerns:** Public/token/internal flows need more explicit automated tests. MIME validation is good but not antivirus/malware scanning.

**Reliability Concerns:** Large service/router file increases risk; Mongo writes are not consistently transactional.

**Recommended Improvements:** Add antivirus scanning option, transaction boundaries for multi-collection updates, route-level E2E tests, and idempotency keys for upload/process actions.

**Estimated Score After Improvements:** 92/100  
**Priority:** Critical

### Backend Area: Contracts

**File Path(s):** `backend/rbac_backend/routers/contracts.py`, `backend/rbac_backend/services/contract_service.py`, `backend/rbac_backend/services/contract_ingest_queue.py`, `backend/rbac_backend/worker.py`

**Purpose:** Contract upload sessions/chunks, ingest queue, search/list/status/download.

**Current Production Readiness Score:** 75/100

**Findings:** Chunk/session model and Redis queue exist; production Compose separates `contract-worker`.

**Security Concerns:** Explicit permission names are less visible than document endpoints; ensure all contract operations map to DMS permissions.

**Reliability Concerns:** Dead-letter and retry visibility should be operationalized.

**Recommended Improvements:** Add worker dashboards, dead-letter replay, per-user quotas, and contract-specific RBAC tests.

**Estimated Score After Improvements:** 90/100  
**Priority:** Critical

### Backend Area: Letters and Letter Drafting

**File Path(s):** `backend/rbac_backend/routers/letters.py`, `backend/rbac_backend/routers/letter_drafting.py`, `backend/rbac_backend/services/letter_drafting/*`

**Purpose:** Letter lifecycle, drafting sessions, source ledger, governance, export/issue actions.

**Current Production Readiness Score:** 74/100

**Findings:** Mature workflow endpoints exist and tests are present. Some letter operations use transactions in comments/implementation sections.

**Security Concerns:** Many endpoints are authenticated but not all expose explicit permission dependencies in signatures.

**Reliability Concerns:** Workflow state machine needs complete transition tests and race-condition protection.

**Recommended Improvements:** Centralize letter state transition policy, add optimistic locking, add role/action matrix tests.

**Estimated Score After Improvements:** 90/100  
**Priority:** Critical

### Backend Area: AI Assistant, Deep Planning, Retrieval

**File Path(s):** `backend/rbac_backend/routers/ai_assistant.py`, `backend/rbac_backend/routers/deep_planning.py`, `backend/rbac_backend/routers/retrieval_engine.py`, `backend/rbac_backend/retrieval/*`

**Purpose:** Vector search, drafting, strategy planning, RAG, contract QA, observability logs.

**Current Production Readiness Score:** 65/100

**Findings:** Routes are mounted and auth-protected, but permission granularity varies. `ai_assistant.py` appears to call `authorize_scope(current_user, "admin:read")`, which does not match the `authorize_scope` signature and should be retested.

**Security Concerns:** Prompt/output logging, source leakage, and tenant scoping in vector search/RAG must be tested.

**Reliability Concerns:** AI calls require timeout/retry/fallback budgets and trace redaction.

**Recommended Improvements:** Add prompt/data redaction, tenant-filtered vector tests, strict citation/source validation, and cost/rate limits.

**Estimated Score After Improvements:** 87/100  
**Priority:** High

### Backend Area: Email Sharing and Notifications

**File Path(s):** `backend/rbac_backend/routers/email_share.py`, `backend/rbac_backend/routers/email_groups.py`, `backend/rbac_backend/routers/notifications.py`, `backend/rbac_backend/services/email_service.py`

**Purpose:** Recipient suggestions, email sharing, public share links, groups, notification center.

**Current Production Readiness Score:** 71/100

**Findings:** Share endpoint uses `documents:share` and document policy; public share token is hashed and expires. Test notification router is mounted.

**Security Concerns:** Public downloads need throttling and revocation UI. Test endpoints should be production-gated.

**Reliability Concerns:** Background email send is queued in FastAPI background task, not a durable queue.

**Recommended Improvements:** Durable email queue, share-token revocation, lower default TTL, recipient audit report, and rate limits.

**Estimated Score After Improvements:** 90/100  
**Priority:** Critical

### Backend Area: Admin Master Data

**File Path(s):** `organizations.py`, `projects.py`, `parties.py`, `representatives.py`, `tags.py`, `users.py`, `roles.py`, `permissions.py`

**Purpose:** Tenant, project, stakeholder, tag, user, and RBAC administration.

**Current Production Readiness Score:** 71/100

**Findings:** Core CRUD exists and many routes use permissions. Duplicate role routes exist in `roles.py` and `permissions.py`.

**Security Concerns:** Not every route has explicit permission dependencies; service checks need coverage.

**Reliability Concerns:** Soft-delete patterns vary across services.

**Recommended Improvements:** Consolidate role APIs, enforce soft-delete/restore consistently, add scoped uniqueness constraints and audit logs.

**Estimated Score After Improvements:** 89/100  
**Priority:** High

### Backend Area: Unmounted Routers

**File Path(s):** `search.py`, `tasks.py`, `folder_structure.py`, `input_requests.py`, `email.py`, `concerns.py`, `performance.py`

**Purpose:** Defined API features not active in the app.

**Current Production Readiness Score:** 35/100

**Findings:** These routers define endpoints but are not included in `main.py`. Frontend/hook code references `/folder-structure`, `/tasks`, `/search/*`, `/input-requests/*`, and `/email/send`-style paths.

**Security Concerns:** If mounted hastily, several require explicit permissions and path/file validation tests.

**Reliability Concerns:** Current production app will 404 these flows.

**Recommended Improvements:** Decide for each router: mount and harden, replace frontend calls, or delete/archive feature.

**Estimated Score After Improvements:** 85/100  
**Priority:** Critical

---

## 6. API Endpoint Matrix

The matrix below covers active mounted route families and known unmounted route families. Full endpoint definitions were parsed from `backend/rbac_backend/routers/*.py`; source files should be treated as the canonical inventory.

| Method(s) | Endpoint / Route Family | Purpose | Auth Required | Role/Permission Required | Validation Present | Error Handling | Current Score | Improved Score | Priority |
|---|---|---|---|---|---|---|---:|---:|---|
| POST | `/api/login` | Login | No | None | Yes | Good | 78 | 92 | Critical |
| POST | `/api/refresh`, `/api/logout` | Session refresh/logout | Yes | Active session | Partial | Good | 76 | 92 | Critical |
| GET | `/api/me` | Current user | Yes | Authenticated | Yes | Good | 78 | 90 | Critical |
| POST | `/api/step-up` | Sensitive-action token | Yes | Authenticated | Yes | Good | 76 | 92 | High |
| CRUD | `/api/users`, `/api/users/{id}` | User admin | Yes | `users:*` | Partial | Good | 73 | 90 | Critical |
| POST | `/api/users/{id}/lock`, `/unlock` | Account lock admin | Yes | `users:update`, service lock permission | Partial | Good | 70 | 90 | Critical |
| CRUD | `/api/roles`, `/api/roles/{id}` | Role admin | Yes | `roles:*` | Partial | Partial | 68 | 90 | Critical |
| CRUD | `/api/permissions`, `/api/permissions/{id}` | Permission admin | Yes | `permissions:*` / `roles:superuser` | Partial | Partial | 70 | 90 | Critical |
| POST/GET/PUT/DELETE | `/api/organizations`, `/api/organizations/{id}` | Organization CRUD | Yes | `organizations:*` | Partial | Good | 74 | 90 | High |
| POST/GET/PUT/DELETE | `/api/projects`, `/api/projects/{id}` | Project CRUD | Yes | `projects:*` | Partial | Good | 74 | 90 | High |
| GET/PATCH | `/api/projects/{id}/notification-settings` | Project notification config | Yes | Not explicit in route map | Partial | Partial | 64 | 86 | High |
| GET/POST/PUT/DELETE | `/api/parties...` | Party CRUD/association | Yes | Service-level | Partial | Partial | 70 | 88 | High |
| GET/POST/PUT/DELETE | `/api/representatives...` | Representative CRUD | Yes | Service-level | Partial | Partial | 67 | 86 | Medium |
| GET/POST/PUT/DELETE | `/api/tags`, `/api/tags/{id}`, `/api/subtags/{id}` | Tag/subtag admin | Yes | Service-level | Partial | Partial | 70 | 88 | Medium |
| GET/POST/PUT/DELETE | `/api/documents`, `/api/documents/{id}` | Document CRUD | Yes | `documents:*` | Good | Good | 80 | 92 | Critical |
| GET | `/api/documents/{id}/download`, `/api/documents/download-all` | Document download/bulk zip | Yes | Policy/permission service | Good | Good | 78 | 92 | Critical |
| GET/POST/DELETE | `/api/documents/{id}/references`, `/linked`, `/sync-references` | References/links | Yes | `documents:read/update` | Partial | Good | 76 | 90 | High |
| GET/POST/DELETE | `/api/documents/{id}/enclosures` | Enclosure management | Yes | `documents:read/update` | Good | Good | 78 | 91 | High |
| POST/GET | `/api/documents/bulk-upload`, `/status`, `/template` | Bulk upload | Yes | `documents:upload` | Good | Good | 76 | 90 | Critical |
| POST | `/api/internal/documents/{id}/process` | Internal processing | Token | LangGraph token | Partial | Good | 72 | 90 | High |
| POST/GET | `/api/contracts/upload-session`, `/upload-multipart`, `/upload-chunk`, `/status`, `/list`, `/search`, `/{id}/download` | Contract upload/search/download | Yes | Policy/service | Good | Good | 75 | 90 | Critical |
| GET/POST/PUT/PATCH | `/api/letters`, `/api/letters/{id}`, `/submit`, `/approve`, `/complete`, `/strategy/*` | Letter lifecycle | Yes | Service-level | Partial | Partial | 72 | 89 | High |
| POST/GET | `/api/letters/{id}/drafting/*` | Drafting sessions/actions/audit/governance | Yes | Service-level | Good | Good | 76 | 91 | Critical |
| GET/POST/PUT/DELETE | `/api/letter-templates` | Letter templates | Yes | Authenticated/service-level | Partial | Partial | 72 | 89 | High |
| POST/GET/DELETE/PUT | `/api/ai-assistant/*` | Search/generate/config/runs | Mixed | Auth for most; health public | Partial | Partial | 62 | 86 | High |
| POST/GET | `/api/deep-planning/*` | Draft generation/history/analyze | Yes | Scope checks | Partial | Partial | 62 | 86 | High |
| POST/GET | `/api/v1/retrieval/*`, `/api/v1/ingestion/jobs`, `/api/v1/admin/vector/reconcile` | Retrieval/RAG/admin reconcile | Yes | Not explicit in route map | Partial | Partial | 68 | 88 | High |
| GET/POST/PUT/DELETE | `/api/email/groups` | Email groups | Yes | Service-level | Partial | Partial | 70 | 86 | Medium |
| GET/POST | `/api/email/suggestions`, `/resolve-recipients`, `/share-document` | Email suggestions/share | Yes | `documents:share` on share | Good | Good | 74 | 90 | Critical |
| GET | `/api/email/public-share/{token}/download` | Public shared document download | No | Opaque token | Partial | Good | 66 | 86 | Critical |
| GET/PATCH/POST | `/api/notifications*` | Notification center/preferences/actions | Yes | Authenticated | Partial | Partial | 70 | 86 | Medium |
| GET/POST | `/api/notification-test/email` | Test notification email | Yes | Authenticated | Partial | Partial | 58 | 84 | High |
| GET/POST | `/api/reports`, `/api/reports/preview`, `/download` | Report preview/download | Yes | Not explicit in route map | Partial | Partial | 66 | 86 | High |
| GET/PUT/POST | `/api/storage-settings*`, `/api/settings/storage/*` | Storage settings | Yes | Scope checks | Partial | Partial | 70 | 88 | High |
| GET/POST/PUT | `/api/smtp-settings/*` | SMTP settings/test | Yes | Scope checks | Partial | Partial | 72 | 90 | High |
| GET/POST | `/api/storage-sync/*` | Storage/vector reconciliation | Yes | Superadmin dependency | Good | Good | 78 | 92 | Medium |
| GET | `/health`, `/health/live`, `/health/ready`, `/health/observability` | Health/readiness | No | None | Good | Good | 72 | 90 | Medium |
| GET | `/metrics` | Prometheus metrics | Token if configured | `X-Metrics-Token` | Good | Good | 76 | 90 | Medium |
| WS | `/ws/notifications` | WebSocket notifications | Token or dev fallback | Auth token/dev headers | Partial | Partial | 58 | 84 | High |
| CRUD | `/api/folder-structure`, `/api/upload-file` | Folder/file management | Not mounted | Not mounted | Partial | Not active | 35 | 85 | Critical |
| CRUD | `/api/tasks` | Task management | Not mounted | Not mounted | Partial | Not active | 35 | 82 | Critical |
| GET/POST | `/api/search/*` | Search/suggestions/analytics/semantic | Not mounted | Not mounted | Partial | Not active | 35 | 86 | Critical |
| GET/POST | `/api/input-requests/*` | Letter input requests | Not mounted | Not mounted | Partial | Not active | 35 | 86 | Critical |
| POST/GET | `/api/send-document-share`, `/api/send-notification`, `/api/templates`, `/api/send-history` | Legacy email router | Not mounted | Not mounted | Partial | Not active | 35 | 82 | Medium |
| CRUD | `/api/concerns` | Concerns module | Not mounted | Not mounted | Partial | Not active | 35 | 82 | Medium |
| GET/POST | `/api/performance/*` | Performance admin | Not mounted | Not mounted | Partial | Not active | 35 | 84 | Medium |

---

## 7. Security Audit

| Security Area | Finding | Risk Level | Current State | Recommended Fix | Priority |
|---|---|---|---|---|---|
| Authentication | Frontend stores `accessToken` in `localStorage`. | High | Cookie auth exists but localStorage remains active. | Move to HttpOnly cookie-only sessions; remove bearer token persistence. | Critical |
| Authentication | `useAuth` treats local role/user keys as session context. | High | Client can show protected UI based on stale localStorage. | Make auth state depend on `/api/me` only. | Critical |
| CSRF | Cookie-authenticated unsafe methods have no visible CSRF token validation. | High | `withCredentials: true` is enabled. | Add CSRF double-submit or synchronizer token for POST/PUT/PATCH/DELETE. | Critical |
| Authorization | Some routers use explicit `require_permission`; others only use auth/service checks. | High | Inconsistent enforcement. | Standardize route dependencies and service policy checks. | Critical |
| Authorization | Duplicate role endpoints in `roles.py` and `permissions.py`. | Medium | Both mounted. | Consolidate one role router and add regression tests. | Critical |
| Endpoint Exposure | Several frontend-called routers are unmounted. | Critical | Causes 404/broken workflows. | Mount/harden or remove features. | Critical |
| Seed Users | Default users use password `password`, including superadmin. | Critical | `default_users.py` can create weak privileged accounts. | Disable in production; require generated one-time passwords and forced reset. | Critical |
| Public Links | Public share links are unauthenticated and default to 30-day TTL. | High | Token is hashed and expires but no visible revocation UI/throttling. | Add revocation, shorter default TTL, rate limits, and recipient-scoped audit. | Critical |
| File Uploads | MIME sniffing and size checks exist but no malware scan. | Medium | PDF/image/text/docx checks are present. | Add ClamAV/vendor malware scanning and quarantine. | High |
| Secrets | Local `.env` exists in workspace; not tracked by `git ls-files`, but must be protected. | Medium | `.env.example` is tracked; `.env` untracked/local. | Keep `.env` ignored; add secret scan pre-commit/CI enforcement. | High |
| CORS | Credentials enabled with wildcard methods/headers. | Medium | Origins validated by settings. | Restrict methods/headers to required list. | Medium |
| Security Headers | Apache gateway sets CSP/HSTS/etc.; FastAPI app does not. | Medium | Good if gateway is always used. | Add app-level headers or enforce gateway-only deployment. | Medium |
| Metrics/Health | Detailed readiness/observability endpoints public. | Medium | `/metrics` supports token; health details are public. | Public liveness only; restrict detailed endpoints to internal/admin. | Medium |
| Dependencies | `npm audit --audit-level=high --omit=dev` found 0 prod vulnerabilities. | Low | Python audit not run locally in this pass; CI has `pip-audit`. | Run `pip-audit` in CI and before release. | Medium |
| Logging | Request IDs and slow logging exist. | Low | Good baseline. | Add structured JSON logs and trace correlation. | Medium |
| Data Privacy | AI/RAG trace retention/redaction not fully verified. | High | Multiple AI services exist. | Redact prompts, sources, and generated text where required; define retention. | High |

---

## 8. Database and Data Integrity Audit

| Database Area | Finding | Risk | Recommended Fix | Priority |
|---|---|---|---|---|
| Mongo connection | Production config rejects localhost DB and requires replica set unless explicitly overridden. | Low | Keep this check and test it in deployment. | High |
| Indexes | `core/database.py` creates many scoped indexes for users, documents, letters, drafting, contracts, audit events, etc. | Medium | Add migration-managed index creation with rollout safety. | High |
| Migrations | No formal migration tool found. | High | Add controlled migrations for indexes, schema changes, backfills, and rollback. | Critical |
| Constraints | Many uniqueness rules are application-side; only some unique indexes exist. | Medium | Add scoped unique indexes for users/email, tags, parties, roles, projects where required. | High |
| Transactions | Some letter operations mention transactions, but most Mongo writes are not consistently transactional. | Medium | Use sessions for multi-collection operations and idempotency keys. | High |
| Soft Delete | Soft-delete fields vary (`deleted`, `is_deleted`, `deleted_at`). | Medium | Standardize soft-delete schema and query helpers. | High |
| Audit Trail | `document_audit_events` and `audit_events` indexes exist; document actions emit audit events. | Medium | Extend audit events to admin, billing, sharing, reports, AI, and settings. | High |
| Backups | Backup/restore scripts exist in `scripts/*`. | Medium | Schedule backups, encrypt them, and run restore drills. | Critical |
| Sensitive Data | SMTP settings have encryption service/tests, but key handling must be production-verified. | High | Store encryption keys in a secrets manager and rotate. | Critical |
| Vector Data | Qdrant/Falkor sync status and reconciliation exist. | Medium | Add scheduled reconciliation dashboards and alerts. | High |
| Seed Data | Default users/passwords are unsafe for production. | Critical | Environment-gate seed users and force credential bootstrap. | Critical |

---

## 9. Performance and Scalability Audit

| Area | Issue | Impact | Recommendation | Priority |
|---|---|---|---|---|
| Frontend bundle | Large pages: `DocumentsPage.tsx` 2,219 lines, `DocumentsSearchPage.tsx` 1,697, `ContractsSearchPage.tsx` 1,613, `UploadPage.tsx` 1,382. | Slow maintainability and possible bundle chunks. | Split into route-level and component-level chunks; run bundle visualizer. | High |
| Frontend state | Many pages manage complex local state. | Regression risk. | Move repeated table/filter/form patterns into tested components/hooks. | High |
| API performance | Request logging and slow threshold exist. | Good baseline. | Add per-endpoint histograms, DB query timing, and alerting. | Medium |
| Database queries | Many indexes exist, but wildcard text index on documents can be expensive. | Index/storage growth. | Benchmark search workloads and replace broad wildcard text search where needed. | High |
| Uploads | Streaming upload and chunking exist. | Good baseline. | Add resumability, retry UX, and queue backpressure metrics. | High |
| Background jobs | Contract worker exists; email uses FastAPI background task. | Email delivery can be lost on process restart. | Move email sending to durable queue. | Critical |
| Caching | Redis runtime state exists; cache strategy is unclear across endpoints. | Inconsistent performance. | Define cache policy for dashboard/report/search and invalidation. | Medium |
| Pagination | Several list endpoints/pages support filters, but full pagination consistency not verified. | Large tenant slowdown. | Enforce server-side pagination for every list/export preview. | High |
| Rate limiting | Login and user rate limiter exist; not uniform across public/share/report/AI endpoints. | Abuse/cost risk. | Add route-family rate limits, especially AI, public share, reports, uploads. | Critical |
| Horizontal scaling | Redis-backed rate/runtime state is required in production settings. | Good direction. | Verify all in-memory fallbacks are disabled/fail-closed in production. | High |

---

## 10. Testing Audit

| Area | Current Test Coverage | Missing Tests | Recommended Tests | Priority |
|---|---|---|---|---|
| Backend APIs | 134 backend test files/items found under `backend/rbac_backend/tests`. | Route inventory, every mounted endpoint, unmounted frontend path detection. | Generate OpenAPI/route inventory and compare with frontend service paths. | Critical |
| Frontend pages | 25 frontend test files/items found. | Most large pages lack full interaction tests. | Component tests plus Playwright E2E for core workflows. | Critical |
| Auth flows | Login tests exist; config validation tests exist. | Cookie-only, CSRF, logout invalidation, session concurrency. | Security regression tests for session invalidation and CSRF. | Critical |
| RBAC | RBAC hardening tests exist. | Full route/action matrix for all roles. | Parametrized tenant/role tests per endpoint. | Critical |
| Upload/download | Document tests exist. | Malware/invalid file, large file, interrupted upload, S3 fallback. | Upload/download integration tests with mocked storage. | Critical |
| Database logic | Some concurrency/document tests exist. | Migrations, unique constraints, restore validation. | Migration dry-run and transaction tests. | High |
| AI/RAG | Retrieval and LangGraph prompt tests exist. | Tenant leakage, source citation enforcement, cost/rate limits. | Golden-set RAG tests and redaction tests. | High |
| E2E flows | Not found as a production-grade suite. | Login to document workflows, contracts, share, letter approval. | Playwright suite in CI against Compose. | Critical |
| Security tests | Secret scan and dependency scan in CI. | CSRF, IDOR, public token abuse, file traversal. | OWASP-style API tests. | Critical |

Verification performed during this audit:

- `npm audit --audit-level=high --omit=dev` in `client`: **0 production vulnerabilities found**.
- Full backend/frontend test suites were not executed in this audit pass; CI is configured to run them.

---

## 11. DevOps and Deployment Readiness

| Area | Current State | Gap | Recommendation | Priority |
|---|---|---|---|---|
| Environment variables | `.env.example`, client `.env.example`, and production Compose required vars exist. | Local `.env` exists in workspace; secrets manager not integrated. | Use managed secrets, keep `.env` out of repo, rotate real credentials. | Critical |
| Build process | Client and backend Dockerfiles exist; CI builds both. | Client Dockerfile uses `npm install`, not `npm ci`. | Use deterministic `npm ci`; pin image digests for release. | Medium |
| Docker | `docker-compose.prod.yml` has backend, client, worker, Qdrant, FalkorDB, Redis, Apache gateway. | Mongo is external, TLS not in gateway Compose, no resource limits. | Add resource limits, TLS termination docs/config, external Mongo runbook. | High |
| CI/CD | CI has secret scan, backend tests, frontend lint/test/build, dependency scans, image scans. | No deploy job, no E2E, no migration/backup verification. | Add staging deploy, Playwright E2E, migration dry-run, restore drill job. | Critical |
| Logging | Request logging and Docker JSON rotation exist. | No central log aggregation configured. | Ship logs to central system with JSON fields and request IDs. | High |
| Monitoring | Metrics endpoint and observability registry exist. | Alerting dashboards not configured. | Add Prometheus/Grafana or managed APM alerts. | High |
| Health checks | Backend/client/gateway/Redis/Qdrant/Falkor healthchecks exist. | Detailed health endpoints public. | Split public liveness and private readiness. | Medium |
| Rollback process | Docs/scripts exist but not verified in audit. | No automated rollback pipeline. | Document and rehearse rollback per service and DB migration. | Critical |
| Backups | Mongo/Qdrant/volume backup scripts exist. | Restore drill evidence missing. | Schedule encrypted backups and quarterly restore test. | Critical |
| Security headers | Apache gateway sets CSP/HSTS/etc. | Only applies when gateway is used. | Enforce gateway for prod or add app headers. | Medium |
| Docs | Many production docs exist. | Fragmented and duplicative. | Consolidate into one release runbook. | Medium |

---

## 12. Phase-Wise Production Readiness Plan

### Phase 1: Critical Stabilization

Goal: Fix issues that block production deployment.

| Task | Area | Priority | Expected Impact | Estimated Complexity |
|---|---|---|---|---|
| Mount/harden or remove `search`, `tasks`, `folder_structure`, `input_requests`, `email`, `concerns`, `performance` routers. | Backend/frontend | Critical | Restores broken workflows and route integrity. | Medium |
| Add frontend-service-to-backend-route contract test. | Testing | Critical | Prevents future 404 regressions. | Medium |
| Remove `localStorage` access-token flow and stale role auth context. | Auth/frontend | Critical | Reduces XSS/session risk. | Medium |
| Add CSRF protection for cookie-authenticated unsafe methods. | Security | Critical | Prevents cross-site write attacks. | Medium |
| Disable production seed users and rotate default credentials. | Security/data | Critical | Removes privileged default account risk. | Low |
| Gate detailed health/observability/test endpoints. | Security/ops | High | Reduces operational data exposure. | Low |
| Consolidate duplicate roles APIs. | RBAC | Critical | Reduces privilege-management drift. | Medium |

### Phase 2: Reliability and Data Integrity

Goal: Improve backend safety, validation, database reliability, and operational correctness.

| Task | Area | Priority | Expected Impact | Estimated Complexity |
|---|---|---|---|---|
| Add migration/index management tool. | Database | High | Safer schema/index rollout. | Medium |
| Standardize soft-delete fields and query helpers. | Database | High | Prevents deleted-data leakage. | Medium |
| Add transaction/idempotency boundaries for multi-collection writes. | Backend | High | Reduces partial writes. | High |
| Expand audit logging to admin/settings/reports/sharing/AI actions. | Security | High | Improves traceability. | Medium |
| Add durable email queue. | Backend | Critical | Prevents lost outbound email on restart. | Medium |
| Add public-share revocation/throttling. | Security | Critical | Reduces data leakage risk. | Medium |
| Add malware scanning/quarantine option. | File uploads | High | Safer document ingestion. | Medium |

### Phase 3: UX, Performance, and Scalability

Goal: Improve user experience, frontend quality, page performance, and scalable behavior.

| Task | Area | Priority | Expected Impact | Estimated Complexity |
|---|---|---|---|---|
| Split large pages into components/hooks. | Frontend | High | Better maintainability and testability. | Medium |
| Add consistent loading/empty/error states. | Frontend | High | Better user trust under failure. | Medium |
| Run responsive browser verification for all routes. | Frontend QA | High | Catches mobile/tablet layout failures. | Medium |
| Enforce server-side pagination on all lists. | Backend/frontend | High | Handles large tenants. | Medium |
| Add AI/report/upload rate limits and quotas. | Security/performance | Critical | Controls abuse and cost. | Medium |
| Add dashboard/report/search caching strategy. | Performance | Medium | Reduces DB load. | Medium |

### Phase 4: Testing and CI/CD

Goal: Add automated validation and deployment confidence.

| Task | Area | Priority | Expected Impact | Estimated Complexity |
|---|---|---|---|---|
| Add Playwright E2E suite for critical workflows. | Testing | Critical | Validates real user paths. | High |
| Add RBAC matrix tests for every endpoint/action. | Security testing | Critical | Prevents tenant/permission regressions. | High |
| Add CSRF/IDOR/file traversal tests. | Security testing | Critical | Covers high-risk attack paths. | Medium |
| Add Compose-based staging smoke tests. | CI/CD | High | Validates images and routing. | Medium |
| Add migration dry-run and restore drill jobs. | CI/CD/DB | Critical | Reduces deployment/DR risk. | High |
| Make `npm ci` and lockfile validation mandatory. | CI/CD | Medium | Deterministic frontend builds. | Low |

### Phase 5: Final Production Hardening

Goal: Prepare the system for safe real-world launch.

| Task | Area | Priority | Expected Impact | Estimated Complexity |
|---|---|---|---|---|
| Configure central logging, metrics dashboards, and alerts. | Observability | High | Operational readiness. | Medium |
| Configure TLS, HSTS, CSP validation, and external WAF/rate limits. | Security/edge | High | Safer public exposure. | Medium |
| Rehearse backup/restore and rollback. | Operations | Critical | Launch confidence. | Medium |
| Perform final dependency/container scans. | Security | High | Reduces known CVE exposure. | Low |
| Run full regression on staging with production-like data. | QA | Critical | Final launch gate. | High |
| Launch via internal beta/limited rollout with monitoring window. | Release | High | Controlled risk. | Medium |

---

## 13. Final Production Readiness Scorecard

| Area | Current Score | Score After Improvements | Notes |
|---|---:|---:|---|
| Frontend | 68/100 | 88/100 | Good breadth, but large pages, broken endpoint references, and uneven UX/accessibility. |
| Backend | 70/100 | 89/100 | Strong baseline, but router inconsistency and policy gaps remain. |
| Database | 69/100 | 88/100 | Many indexes and replica-set checks; needs migrations/restore drills/constraints. |
| Security | 61/100 | 89/100 | Auth hardening underway, but localStorage tokens, CSRF, seed users, and public endpoints are blockers. |
| Testing | 62/100 | 87/100 | Solid unit/integration start; missing E2E and route-contract security tests. |
| DevOps | 72/100 | 90/100 | Good CI/Docker baseline; deployment automation and observability need completion. |
| Documentation | 76/100 | 88/100 | Extensive docs, but fragmented and partially stale. |
| Overall | 64/100 | 88/100 | Not production ready now; realistic path to readiness after critical phases. |

---

## 14. Launch Readiness Checklist

- [ ] Authentication and authorization verified
- [ ] Role-based access control verified for every endpoint/action
- [ ] All critical security issues resolved
- [ ] Frontend service paths match mounted backend routes
- [ ] All API inputs validated
- [ ] CSRF protection implemented for cookie-auth unsafe methods
- [x] Default seed credentials removed or production-gated
- [ ] Error handling standardized
- [x] Logging implemented with request IDs and structured fields
- [ ] Monitoring configured
- [ ] Error tracking configured
- [ ] Database backups configured
- [ ] Database restore process tested
- [ ] Environment variables secured in secrets manager
- [ ] Secrets removed from repository and local `.env` protected
- [ ] CI/CD pipeline passing
- [ ] Unit tests passing
- [ ] Integration tests passing
- [ ] E2E tests passing
- [x] Production build verified
- [ ] Docker/deployment setup verified
- [x] Health checks implemented and detailed checks access-controlled
- [ ] Rollback plan documented and rehearsed
- [ ] Final regression test completed

Implementation update on 2026-05-19:

- Mounted previously unmounted route families for search, tasks, folder structure, input requests, concerns, performance, and legacy email under safer prefixes.
- Added `backend/rbac_backend/tests/test_route_inventory.py` to prevent regression of frontend-referenced backend routes.
- Production-gated default seed users in `backend/rbac_backend/initial_data/default_users.py`; production seed users now require explicit `ALLOW_DEFAULT_SEED_USERS=true` and a non-default `DEFAULT_SEED_USER_PASSWORD`.
- Removed central frontend reliance on `localStorage.accessToken` in the Axios API interceptor, refresh flow, login flow, `useAuth`, and `useRBAC`. Some legacy pages/components still read localStorage directly and remain follow-up work.
- Redacted detailed readiness errors unless the metrics token is supplied, and restricted observability health details in production.
- Verified `python -m compileall -q backend/rbac_backend`, targeted pytest route/config tests, `npm run build`, and `npm audit --audit-level=high --omit=dev`.

---

## 15. Final Recommendations

### Can this repository go to production now?

No.

### Must Fix Before Launch

- Fix mounted backend router inventory versus frontend route/service calls.
- Remove localStorage token/session trust and add CSRF protection.
- Disable production seed users and rotate any default credentials.
- Normalize authorization and tenant scoping across all routers.
- Add E2E coverage for login, upload, document view/share, letter drafting, contract upload/search/QA, and admin RBAC.
- Restrict public detailed health/observability/test endpoints.
- Prove backup/restore and rollback.

### Can Be Improved After Launch

- Component-level frontend refactors of very large pages.
- Advanced analytics dashboards.
- Additional AI quality evaluation harnesses.
- More granular performance tuning after real traffic metrics.

### Remaining Risks

- AI/RAG source grounding and data leakage require continuous testing.
- Mongo schema drift can occur without migration discipline.
- Public share links remain sensitive even with token hashing and expiry.
- Large document workloads may expose vector sync and queue bottlenecks.

### Safest Launch Strategy

Use a staged rollout:

1. Stabilize route/auth/RBAC blockers in a staging branch.
2. Deploy to staging with production-like Mongo/Qdrant/Falkor/Redis topology.
3. Run automated E2E and security tests plus manual UAT for admin, document, letter, contract, and sharing flows.
4. Rehearse backup/restore and rollback.
5. Launch as an internal beta for one organization/project.
6. Monitor logs, metrics, queues, public share downloads, AI costs, and error rates for a fixed window before expanding.

---

## Appendix A: Files Audited

| File Path | Area | Audited | Notes |
|---|---|---|---|
| `client/src/routes.tsx` | Frontend routing | Yes | Source for active route inventory. |
| `client/src/pages/*.tsx` | Frontend pages | Yes | Active route pages scored; unused/archive pages noted. |
| `client/src/services/*.ts` | Frontend API | Yes | Endpoint mismatch found. |
| `client/src/hooks/use-auth.ts` | Auth | Yes | localStorage session risk. |
| `client/src/services/auth.ts` | Auth | Yes | localStorage bearer token persistence. |
| `client/src/components/auth/ProtectedRoute.tsx` | Auth/RBAC | Yes | Client-side route gate only. |
| `client/src/config/rolePermissions.ts` | RBAC UX | Yes | Frontend rules are UX only. |
| `backend/rbac_backend/main.py` | Backend app | Yes | Router inventory source. |
| `backend/rbac_backend/core/config.py` | Config/security | Yes | Strong production validation present. |
| `backend/rbac_backend/core/security.py` | Auth/RBAC | Yes | JWT/cookie/dev header/scope helpers. |
| `backend/rbac_backend/core/database.py` | Database | Yes | Index and replica-set checks reviewed. |
| `backend/rbac_backend/routers/*.py` | API routes | Yes | Mounted and unmounted routes reviewed. |
| `backend/rbac_backend/services/*.py` | Services | Yes | Representative service risks reviewed. |
| `backend/rbac_backend/services/letter_drafting/*` | Letter drafting | Yes | Workflow service area reviewed. |
| `backend/rbac_backend/models/*.py` | Models | Yes | Password/user/document/settings patterns reviewed. |
| `backend/rbac_backend/tests` | Tests | Yes | Inventory counted; full test run not executed. |
| `client/src/pages/__tests__`, `client/src/tests` | Frontend tests | Yes | Inventory counted; full test run not executed. |
| `.github/workflows/ci.yml` | CI/CD | Yes | Good baseline with scans/tests/build. |
| `docker-compose.prod.yml` | Deployment | Yes | Strong Compose baseline. |
| `client/Dockerfile`, `backend/Dockerfile` | Deployment | Yes | Non-root users; frontend should use `npm ci`. |
| `config/httpd.conf` | Gateway/security headers | Yes | CSP/HSTS/security headers present. |
| `scripts/*backup*`, `scripts/*restore*`, `scripts/pre_deploy_readiness.sh`, `scripts/post_deploy_verify.sh` | Ops | Yes | Need restore drill evidence. |
| `.env.example`, `client/.env.example`, `backend/rbac_backend/.env.example` | Env docs | Yes | Good examples; placeholders present. |
| `.env` | Local secrets | Limited | File exists locally but is not tracked by `git ls-files`; contents were not copied into report. |
| `backend/archive`, `client/archive` | Archive | Limited | Not active runtime, but should be excluded from production scope. |

---

## Appendix B: Unverified Items

| Item | Reason Unable to Verify | Required Follow-Up |
|---|---|---|
| Full backend test pass | Audit scope prioritized source review; test suite not run. | Run `pytest backend/rbac_backend/tests -q`. |
| Full frontend test/build pass | Audit scope prioritized source review; only npm audit was run. | Run `npm ci`, `npm test -- --run`, `npm run build`. |
| Python dependency vulnerabilities | `pip-audit` was not run locally in this pass. | Run CI dependency scan or `pip-audit -r backend/rbac_backend/requirements.txt`. |
| Browser responsiveness | No Playwright/browser screenshots were run. | Add browser verification for all routes. |
| Actual production env values | Local `.env` contents not reported for safety. | Validate via secrets manager and staging deployment. |
| Database restore | Scripts exist but no restore execution evidence in audit. | Perform restore drill and document RTO/RPO. |
| External services | Qdrant/Falkor/OpenAI/SMTP/S3 not live-tested. | Run staging smoke tests. |
| AI output quality | Source code reviewed, not evaluated with golden datasets. | Build evaluation dataset for retrieval/drafting. |

---

## Appendix C: Critical Findings Summary

| ID | Finding | Area | Risk Level | Recommended Fix | Status |
|---|---|---|---|---|---|
| C-001 | Frontend references unmounted backend routers/endpoints. | Frontend/backend | Critical | Mount/harden or remove mismatched features; add route contract tests. | Partially addressed |
| C-002 | Access tokens and role context stored in localStorage. | Auth/frontend | High | Use HttpOnly cookie-only sessions and server-derived auth state. | Partially addressed |
| C-003 | No visible CSRF protection for cookie-auth unsafe methods. | Security | High | Add CSRF token validation. | Open |
| C-004 | Default seed users use password `password`, including superadmin. | Security/data | Critical | Disable production seed users and force rotation/bootstrap. | Addressed |
| C-005 | Duplicate role endpoint families are mounted. | RBAC | High | Consolidate roles/permissions routers. | Open |
| C-006 | Several routers have auth but no explicit route-level permissions. | Authorization | High | Normalize `require_permission`/`PolicyService` usage. | Open |
| C-007 | Public share download links need revocation/throttling and shorter TTL. | Data sharing | High | Add share management controls and rate limits. | Open |
| C-008 | Detailed health/observability endpoints expose operational details publicly. | DevOps/security | Medium | Restrict detailed endpoints to internal/admin access. | Addressed |
| C-009 | No formal Mongo migration system. | Database | High | Add migration/versioning tool and release checks. | Open |
| C-010 | No production-grade E2E suite for core flows. | Testing | Critical | Add Playwright/Compose E2E in CI. | Open |
