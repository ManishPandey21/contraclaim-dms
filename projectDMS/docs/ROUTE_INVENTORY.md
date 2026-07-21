# Route Inventory (Frontend Permission Map)

**Generated/maintained for Phase 0.** This document maps user-facing frontend routes to page components and frontend permission gates. Backend `PolicyService` remains authoritative; these guards only control frontend navigation and route visibility.

The guard test `client/src/config/__tests__/routeInventory.test.ts` fails when a `<Route>` in `client/src/routes.tsx` is not public, open, or covered by `ROUTE_PERMISSIONS`. The sidebar parity test `client/src/config/__tests__/rolePermissions.sidebar.test.ts` fails when a sidebar link does not resolve to a route permission mapping.

## Classification

- Public: unauthenticated route.
- Open: any authenticated user.
- Mapped: gated by `ROUTE_PERMISSIONS`, with exact match preferred over prefix match.
- Prefix: covered by a base route permission such as `/letters`, `/claims`, `/key-dates`, `/contracts`, or `/letter-templates`.

## Inventory

| Route | Page | Permission Gate | Sidebar | Notes |
| --- | --- | --- | --- | --- |
| `/` | LandingPage | Public | No | Marketing entry. |
| `/login` | LoginPage | Public | No | Authentication. |
| `*` | NotFound | Public | No | Catch-all. |
| `/overview` | Overview | Open | Yes | Authenticated landing page. |
| `/dashboard` | Dashboard | `dms.dashboard.view` | Yes | |
| `/organizations` | OrganizationsPage | `organizations:read` | Yes | |
| `/projects` | ProjectsPage | `projects:read` | Yes | |
| `/documents` | DocumentsPage | `dms.document.view` | Yes | Letters library. |
| `/documentsearch` | EnhancedDocumentsPage | `dms.document.view` | Yes | |
| `/tags` | TagsPage | `tags:read` | No | |
| `/profile` | ProfilePage | Open | Footer | |
| `/users` | UsersPage | `users:read` | Yes | |
| `/permissions` | PermissionsPage | `roles:read` | Yes | |
| `/settings` | SettingsPage | `settings:view` | Yes | |
| `/plan-settings` | PlanSettingsPage | `subscription.entitlement.manage` | Yes | |
| `/subscription-management` | SubscriptionManagementPage | `subscription.entitlement.manage` or `subscription.upgrade` | Yes | RoleGuard. |
| `/billing/return` | BillingReturnPage | `subscription.entitlement.manage` or `subscription.upgrade` | No | Hosted checkout return. |
| `/notifications` | NotificationCenterPage | Open | Yes | |
| `/upload` | UploadPage | `dms.document.upload` | Yes | |
| `/documentviewer/:id` | DocumentViewerPage | `dms.document.view` | No | Prefix mapping through `/documentviewer`. |
| `/share/:id` | ShareDocumentPage | `documents:share` | No | Prefix mapping through `/share`. |
| `/email-groups` | EmailGroupsPage | `email_groups:read` | Yes | |
| `/register` | RegisterPage | `users:create` | Yes | RoleGuard. |
| `/folders` | FolderStructurePage | `dms.document.view` | Yes | |
| `/tasks` | TasksPage | `tasks:read` or `dms.document.view` | Yes | RoleGuard. |
| `/parties` | PartiesInvolvedPage | `parties:read` | Yes | |
| `/letters` | LetterWorkflowPage | `drafting.request.view` | Yes | RoleGuard. |
| `/documents/summary/:id` | LetterSummaryPage | `dms.document.view` | No | Prefix mapping through `/documents`. |
| `/letters/:id/input` | LetterInputPage | `drafting.request.view` | No | Prefix mapping through `/letters`. |
| `/letters/:id/strategic-plan` | LetterStrategicPlanPage | `drafting.request.view` | No | Prefix mapping through `/letters`. |
| `/letters/:id/strategy` | LetterStrategicPlanPage | `drafting.request.view` | No | Prefix mapping through `/letters`. |
| `/letters/:id/draft` | LetterDraftPage | `drafting.request.view` | No | Prefix mapping through `/letters`. |
| `/letters/:id/review` | LetterReviewPage | `drafting.request.view` | No | Prefix mapping through `/letters`. |
| `/letters/:id/approval` | LetterApprovalPage | `drafting.request.view` | No | Prefix mapping through `/letters`. |
| `/letters/:id/completed` | LetterCompletedPage | `drafting.request.view` | No | Prefix mapping through `/letters`. |
| `/letter-quality` | LetterQualityDashboardPage | `drafting.request.view` | Yes | |
| `/reports` | ReportsAnalyticsPage | `reports:view` | Yes | |
| `/claims` | ClaimsRegisterPage | `dms.claim.view` or `dms.document.view` | Yes | RoleGuard. |
| `/claims/:id` | ClaimDetailPage | `dms.claim.view` or `dms.document.view` | No | Prefix mapping through `/claims`; RoleGuard. |
| `/sla` | SLATrackerPage | `dms.claim.view` or `dms.document.view` | Yes | RoleGuard. |
| `/key-dates` | KeyDateRegisterPage | `dms.keydate.view` or `dms.document.view` | Yes | RoleGuard. |
| `/key-dates/:id` | KeyDateDetailPage | `dms.keydate.view` or `dms.document.view` | No | Prefix mapping through `/key-dates`; RoleGuard. |
| `/variations` | VariationRegisterPage | `dms.variation.view` or `dms.document.view` | Yes | RoleGuard. |
| `/bank-guarantees` | BankGuaranteeRegisterPage | `dms.bankguarantee.view` or `dms.document.view` | Yes | RoleGuard. |
| `/ipc-bills` | IPCBillRegisterPage | `dms.ipc.view` or `dms.document.view` | Yes | RoleGuard. |
| `/concerns` | ConcernsPage | `concerns:read` | Yes | RoleGuard. |
| `/admin/billing-catalog` | BillingCatalogPage | `billing.plan.manage` or `system:admin` | Yes | RoleGuard. |
| `/retrieval-console` | RetrievalConsolePage | `dms.document.view` | Yes | RoleGuard. |
| `/observability` | ObservabilityPage | `reports:view` or `system:admin` | Yes | RoleGuard. |
| `/letter-templates` | LetterTemplatePage | `letter_templates:read` | Yes | |
| `/letter-templates/:id/edit` | LetterTemplateEditorPage | `letter_templates:read` | Yes | Prefix mapping through `/letter-templates`. |
| `/representatives` | RepresentativesPage | `representatives:read` | No | |
| `/contracts` | ContractsPage | `dms.document.view` | No | Contract hub. |
| `/contracts/upload` | ContractsUploadPage | `dms.document.view` | Yes | Prefix mapping through `/contracts`. |
| `/contracts/search` | ContractsSearchPage | `dms.document.view` | Yes | Prefix mapping through `/contracts`. |
| `/contracts/qa` | ContractQAPage | `dms.document.view` | Yes | Prefix mapping through `/contracts`. |
| `/contracts/timeline` | ContractTimelinePage | `dms.contract.timeline.view` or `dms.evidence_graph.view` or `dms.document.view` | Yes | RoleGuard; evidence graph timeline. |
| `/contracts/appraisal` | ContractAppraisalPage | `dms.contract.appraisal.view` or `dms.document.view` | Yes | RoleGuard. |
| `/contracts/master` | ContractMasterPage | `dms.contract.master.view` or `dms.document.view` | Yes | RoleGuard. |
| `/arbitration` | ArbitrationDraftingPage | `dms.arbitration.view` or `dms.document.view` | No | RoleGuard; arbitration drafting shell. |
| `/arbitration/claim` | ArbitrationDraftingPage | `dms.arbitration.create` or `dms.arbitration.view` or `dms.document.view` | Yes | Draft Statement of Claim. |
| `/arbitration/defence` | ArbitrationDraftingPage | `dms.arbitration.create` or `dms.arbitration.view` or `dms.document.view` | Yes | Draft Statement of Defence. |
| `/arbitration/rejoinder` | ArbitrationDraftingPage | `dms.arbitration.create` or `dms.arbitration.view` or `dms.document.view` | Yes | Draft Rejoinder / Reply to Defence. |
| `/arbitration/counterclaim` | ArbitrationDraftingPage | `dms.arbitration.create` or `dms.arbitration.view` or `dms.document.view` | Yes | Draft Counterclaim. |
| `/arbitration/drafts` | ArbitrationDraftingPage | `dms.arbitration.view` or `dms.document.view` | Yes | Saved arbitration drafts. |
| `/arbitration/drafts/:draftId` | ArbitrationDraftingPage | `dms.arbitration.view` or `dms.document.view` | No | Draft detail, generation, paragraph import, and export. |
| `/reference/:id` | ReferencePage | `dms.document.view` | No | Prefix mapping through `/reference`. |
| `/health` | HealthPage | `system:admin` | Yes | RoleGuard. |

## Phase 0 Guardrails

- Add every new route to `ROUTE_PERMISSIONS`, mark it public/open in the route inventory test, or intentionally cover it by an existing prefix.
- Add every user-facing sidebar route to `rolePermissions.sidebar.test.ts`.
- Add every new backend-enforced permission to `backend/rbac_backend/core/permissions.py`.
- The permission catalogs in `backend/rbac_backend/models/permission.py` and `backend/rbac_backend/initial_data/default_permissions.py` append any missing canonical permissions at import time.
- `backend/rbac_backend/initial_data/default_roles.py` explicitly grants all Client DMS permissions to the default `orgadmin` role during seeding, so Organization Admin permission save/retrieve does not depend on legacy aliases.

## LangGraph drafting API governance

The drafting frontend does not gain a new route. It continues to use the
existing `/letters/:id/draft` permission gate while the backend provides a
versioned asynchronous contract behind the server-side rollout policy:

| API route | Permission / protection | Behaviour |
| --- | --- | --- |
| `POST /api/letters/{letter_id}/drafting/runs` | `drafting.draft.create`, scoped rate limit, `Idempotency-Key` | Legacy v2 returns 200; enabled v3 returns 202 and a poll URL. |
| `GET /api/letters/{letter_id}/drafting/runs/{run_id}/state` | `drafting.request.view` | Minimal polling state and question list; no checkpoint payload. |
| `POST /api/letters/{letter_id}/drafting/runs/{run_id}/resume` | `drafting.draft.create`, optimistic state version | Accepts versioned answers or a strategy confirmation. |
| `POST /api/letters/{letter_id}/drafting/runs/{run_id}/cancel` | `drafting.draft.create`, optimistic state version | Cooperative cancellation request. |
| `POST /api/letters/{letter_id}/drafting/runs/{run_id}/force-v2` | `drafting.workflow.force_v2` plus step-up | Only before approval/export/issued effects. |
| `GET /api/ops/letter-drafting/runs/{run_id}/checkpoints` | `drafting.workflow.checkpoints` plus step-up | Operations-only, paginated and redacted checkpoint diagnostics. |

`test_route_inventory.py` treats the force-v2 route as critical and verifies its
step-up protection. The server policy, not `VITE_LANGGRAPH_ENABLED`, chooses
the engine.
