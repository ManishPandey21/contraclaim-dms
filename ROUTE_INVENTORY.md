# Route Inventory (frontend ↔ permission map)

**Generated/maintained for Phase 0.** Source of truth for *which app route maps to which page and which permission*. The guard test `client/src/config/__tests__/routeInventory.test.ts` runs in CI (vitest) and **fails the build if any `<Route>` in `routes.tsx` lacks a `ROUTE_PERMISSIONS` mapping** (or an explicit public/open classification). Pair every new route with a `ROUTE_PERMISSIONS` entry **and** (if user-facing) a SideBar link — see `client/src/config/rolePermissions.ts` and `client/src/components/layout/Sidebar.tsx`.

> Frontend guards (`ProtectedRoute`, `RoleGuard`, SideBar filter) are **UX only**. Backend `PolicyService` remains authoritative.

## Classification legend
- **Open** — any authenticated user (`/overview`, `/profile`, `/notifications`).
- **Public** — unauthenticated (`/`, `/login`, `*`).
- **Mapped** — gated by `ROUTE_PERMISSIONS[path]` (exact or prefix).

## Inventory

| Route | Page | Permission (frontend gate) | SideBar | Notes |
|---|---|---|---|---|
| `/` | LandingPage | Public | — | marketing |
| `/login` | LoginPage | Public | — | |
| `*` | NotFound | Public | — | |
| `/overview` | Overview | Open | ✅ | |
| `/dashboard` | Dashboard | `dms.dashboard.view` | ✅ | |
| `/register` | RegisterPage | `users:create` | ✅ | RoleGuard |
| `/organizations` | OrganizationsPage | `organizations:read` | ✅ | |
| `/projects` | ProjectsPage | `projects:read` | ✅ | |
| `/parties` | PartiesInvolvedPage | `parties:read` | ✅ | |
| `/representatives` | RepresentativesPage | `representatives:read` | — | |
| `/email-groups` | EmailGroupsPage | `email_groups:read` | ✅ | |
| `/upload` | UploadPage | `dms.document.upload` | ✅ | |
| `/documents` | DocumentsPage | `dms.document.view` | ✅ | |
| `/documentsearch` | EnhancedDocumentsPage | `dms.document.view` | ✅ | |
| `/documentviewer/:id` | DocumentViewerPage | `dms.document.view` (prefix) | — | |
| `/documents/summary/:id` | LetterSummaryPage | `dms.document.view` (prefix) | — | |
| `/reference/:id` | ReferencePage | `dms.document.view` (prefix) | — | |
| `/share/:id` | ShareDocumentPage | `documents:share` (prefix) | — | |
| `/tags` | TagsPage | `tags:read` | — | |
| `/folders` | FolderStructurePage | `dms.document.view` | ✅ | |
| `/letters` | LetterWorkflowPage | `drafting.request.view` | ✅ | RoleGuard |
| `/letters/:id/*` | Letter* pages | `drafting.request.view` (prefix) | — | input/strategy/draft/review/approval/completed |
| `/letter-quality` | LetterQualityDashboardPage | `drafting.request.view` | ✅ | |
| `/letter-templates` | LetterTemplatePage | `letter_templates:read` | ✅ | |
| `/letter-templates/:id/edit` | LetterTemplateEditorPage | (prefix) | ✅ | |
| `/contracts` | ContractsPage | `dms.document.view` | — | hub |
| `/contracts/upload` | ContractsUploadPage | (prefix) | ✅ | |
| `/contracts/search` | ContractsSearchPage | (prefix) | ✅ | |
| `/contracts/qa` | ContractQAPage | (prefix) | ✅ | |
| `/contracts/appraisal` | ContractAppraisalPage | `dms.contract.appraisal.view` + `dms.document.view` | ✅ | RoleGuard · explicit (Phase 1) |
| `/claims` | ClaimsRegisterPage | `dms.claim.view` + `dms.document.view` | ✅ | RoleGuard · added Phase 1 |
| `/sla` | SLATrackerPage | `dms.claim.view` + `dms.document.view` | ✅ | RoleGuard · added Phase 1 |
| `/tasks` | TasksPage | `tasks:read` + `dms.document.view` | ✅ | RoleGuard · `dms.task.*` planned |
| `/reports` | ReportsAnalyticsPage | `reports:view` | ✅ | |
| `/notifications` | NotificationCenterPage | Open | ✅ | |
| `/health` | HealthPage | `system:admin` | ✅ | RoleGuard |
| `/users` | UsersPage | `users:read` | ✅ | |
| `/permissions` | PermissionsPage | `roles:read` | ✅ | |
| `/plan-settings` | PlanSettingsPage | `subscription.entitlement.manage` | ✅ | |
| `/subscription-management` | SubscriptionManagementPage | `subscription.entitlement.manage` + `subscription.upgrade` | ✅ | RoleGuard |
| `/settings` | SettingsPage | `settings:view` | ✅ | |
| `/profile` | ProfilePage | Open | footer | |

## How the guard works
`routeInventory.test.ts` regex-extracts every `<Route path="...">` from `routes.tsx`, absolutizes the path, and asserts it is `Public`, `Open`, or covered by a `ROUTE_PERMISSIONS` key (exact or `startsWith(base + "/")` prefix). A new unmapped route fails the test with the offending path listed.
