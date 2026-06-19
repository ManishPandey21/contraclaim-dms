# Final Plan — Close the Integration Gap (SideBar ↔ Routes ↔ API ↔ Backend ↔ RBAC)

**Repository:** `ManishPandey21/contraclaim-dms` (published) · `projectDMS` (local working tree)
**Pass type:** Read-only audit + consolidated phase-wise plan. **No application code modified in this pass.**
**Audit basis:** `graphify-out/BACKEND_FRONTEND_INTEGRATION_AUDIT.md` (2026-06-18), `graphify-out/CONTRACLAIM_PRODUCTION_READINESS_PLAN.md`, the knowledge-tree audits, **reconciled against the current repo** (which has advanced materially since 2026-06-18: Razorpay adapter + idempotent webhooks, TLS, RBAC consolidation, SSO/OIDC, audit export, Claims, SLA, Approvals, Evidence Bundle, AI Assessment, and the Contract Appraisal v2 Phase 1–2).

---

## 1. Executive Summary

The single most important finding of this pass: **the backend is now materially ahead of the frontend and the navigation layer.** Over the last work cycles the backend gained complete, tenant-scoped, audited routers for **Claims, SLA/Time-bar, Approval workflows, Evidence Bundle export, AI Claim Assessment, and Contract Appraisal (generate → registers → clauses → review → approve → DOCX/PDF)** — all mounted in `main.py` and test-covered. The 2026-06-18 audit's backend criticals (webhook idempotency, TLS, RBAC consolidation, JWT typing) are largely closed.

But almost none of that surfaces to a user, and two structural problems make several finished features **dead on arrival**:

1. **`ProtectedRoute` is deny-by-default keyed on `ROUTE_PERMISSIONS`** (`client/src/config/rolePermissions.ts`). Any path absent from that map is redirected to `/overview`. `/claims` and `/sla` have **no entry**, so the finished Claims register and SLA tracker are **currently unreachable even by direct URL**. `/contracts/appraisal` is reachable only by accidentally inheriting the `/contracts` prefix — and therefore gates on the **wrong** permission (`dms.document.view` instead of `dms.contract.appraisal.view`).
2. **The SideBar (`client/src/components/layout/Sidebar.tsx`) has no links** for Claims, SLA, Contract Appraisal, Subscription Management, or Notifications, and **Tasks is commented out**. Users cannot navigate to any Phase 4 / appraisal capability.

The second-largest gap is **billing UX**: `client/src/services/billing-api.ts` ships a Razorpay checkout scaffold (`startSubscriptionCheckout` / `redirectToCheckout`) but **no page imports it** — `SubscriptionManagementPage.tsx` still only drives local lifecycle endpoints. So a user cannot start a real Razorpay subscription from the UI, there is no success/pending/failure/retry return screen, and no admin billing diagnostics. This matches audit finding **C1**, still open.

**Net:** this is primarily an **integration/navigation/permission-parity** program, not a "build new backend" program. The fastest readiness gains come from wiring what already exists and aligning permissions — then completing the billing UX and admin diagnostics.

Reconciled production-readiness estimate: **~7.0/10** (up from the audit's 6.3, due to backend hardening), held back from higher mainly by frontend integration, billing UX, and CI contract gates.

---

## 2. Current Integration Status by Feature

| Feature | Backend | Frontend page | API service | SideBar link | Route reachable | Status |
|---|---|---|---|---|---|---|
| **Claims register** | ✅ full (CRUD, status, assign/submit/approve/return, assess, evidence-bundle) | ✅ `ClaimsRegisterPage` + approval/assessment dialogs | ✅ `claims-api.ts` | ❌ none | ❌ **blocked** (`/claims` missing from `ROUTE_PERMISSIONS`) | **Backend done, UI unreachable** |
| **SLA / Time-bar** | ✅ `/sla/upcoming`, `/sla/breached`, daily scan | ✅ `SLATrackerPage` | ✅ `sla-api.ts` | ❌ none | ❌ **blocked** (`/sla` missing) | **Backend done, UI unreachable** |
| **Contract Appraisal** | ✅ generate/job/report/approve/reject/regenerate/registers/clauses/export(docx,pdf)/comments | ✅ `ContractAppraisalPage` + registers + clause library | ✅ `contracts-api.ts` (appraisal fns) | ❌ none | ⚠️ reachable via `/contracts` prefix, **wrong permission** | **Partially integrated** |
| **Tasks** | ✅ CRUD + comments (ownership/scope checks) | ⚠️ `TasksPage` (tasks tab API-backed; other tabs mock) | ✅ `tasks-api.ts` | ❌ commented out | ✅ (`/tasks` → `tasks:read`) | **Partially integrated, no nav** |
| **Billing / subscription** | ✅ checkout endpoint, lifecycle, webhooks (idempotent, signature-verified) | ⚠️ `SubscriptionManagementPage`, `PlanSettingsPage` (local lifecycle only) | ⚠️ `billing-api.ts` checkout **unused**; `plan-settings-api.ts` | ✅ Plan Settings; ❌ Subscription Mgmt | ⚠️ subscription-mgmt RoleGuarded | **Not production-ready (C1 open)** |
| Documents / Letters library | ✅ | ✅ | ✅ | ✅ | ✅ | Integrated |
| Contracts (upload/search/QA) | ✅ | ✅ | ✅ | ✅ (3 links) | ✅ | Integrated |
| Reports & Analytics | ✅ `reports.py` + `/audit/export` | ✅ `ReportsAnalyticsPage` | ✅ | ✅ | ✅ | Integrated (claims/SLA/appraisal data not yet surfaced) |
| Dashboard | ✅ `dashboard.py` | ✅ | ✅ | ✅ | ✅ | Integrated (no claims/SLA/task widgets) |
| Notifications | ✅ `notifications.py` + WS | ✅ `NotificationCenterPage` | ✅ | ❌ none | ✅ (`/notifications` open) | UI exists, no nav |
| System Health / Ops | ✅ `health.py` | ✅ `HealthPage` | ✅ | ✅ (admin) | ✅ RoleGuard | Integrated |
| SSO / OIDC | ✅ `sso.py` | login flow | ✅ | n/a | n/a | Integrated (verifiable parts) |

---

## 3. SideBar Integration Inventory (matrix)

> Legend for **Status**: FI = Fully integrated · PI = Partially integrated · UI-NB = UI exists, backend not wired · BE-UI = Backend exists, UI incomplete · PERM = permission mismatch · NOSVC = missing service wrapper · NOPAGE = missing page · DUP = duplicate/legacy · BE-only = backend-only by design · HIDE = should be hidden until done.

### 3a. Links currently rendered in `Sidebar.tsx`

| SideBar Label | Route | Page | API Service | Backend Router | Permission (FE map) | Status | Gap | Priority | Recommended Fix |
|---|---|---|---|---|---|---|---|---|---|
| Overview | `/overview` | Overview | — | dashboard | `[]` (open) | FI | — | — | — |
| Dashboard | `/dashboard` | Dashboard | enhanced-api | dashboard | `dms.dashboard.view` | FI | No claims/SLA/task widgets | P3 | Add cross-module widgets (Phase 6) |
| Regisration *(typo)* | `/register` | RegisterPage | users-api | users | `users:create` | FI | Label typo | P3 | Rename "User Registration" |
| Organizations | `/organizations` | OrganizationsPage | — | organizations | `organizations:read` | FI | — | — | — |
| Projects | `/projects` | ProjectsPage | — | projects | `projects:read` | FI | — | — | — |
| Add Stakeholders | `/parties` | PartiesInvolvedPage | — | parties | `parties:read` | FI | — | — | — |
| Email Groups | `/email-groups` | EmailGroupsPage | — | email-groups | `email_groups:read` | FI | — | — | — |
| Upload Letters | `/upload` | UploadPage | enhanced-api | documents | `dms.document.upload` | PI | Bulk status cross-tenant (H4) | P2 | Scope bulk-status by owner |
| Letters Library | `/documents` | DocumentsPage | documents-api | documents | `dms.document.view` | PI | `/document/:id` nav mismatch (H8) | P2 | Fix nav + download wrapper |
| Search Letters | `/documentsearch` | EnhancedDocumentsPage | — | search/documents | `dms.document.view` | FI | — | — | — |
| Letter Drafting | `/letters` | LetterWorkflowPage | — | letter_drafting | `drafting.request.view` | FI | Entitlement gating (H9) | P4 | Verify add-on entitlement parity |
| Letter Quality | `/letter-quality` | LetterQualityDashboardPage | — | letter_drafting | `drafting.request.view` | FI | — | — | — |
| Letter Templates | `/letter-templates` | LetterTemplatePage | — | letter_templates | `letter_templates:read` | FI | — | — | — |
| Create Template | `/letter-templates/new/edit` | LetterTemplateEditorPage | — | letter_templates | (prefix) | FI | — | — | — |
| Upload Contract | `/contracts/upload` | ContractsUploadPage | contracts-api | contracts | `dms.document.view` (prefix) | FI | — | — | — |
| Search Clauses | `/contracts/search` | ContractsSearchPage | contracts-api | contracts | (prefix) | FI | — | — | — |
| Contract Q&A | `/contracts/qa` | ContractQAPage | contracts-api | retrieval-engine | (prefix) | FI | — | — | — |
| Folder Structure | `/folders` | FolderStructurePage | — | folder-structure | `dms.document.view` | FI | — | — | — |
| Reports & Analytics | `/reports` | ReportsAnalyticsPage | — | reports | `reports:view` | FI | — | — | — |
| System Health | `/health` | HealthPage | — | health | `system:admin` | FI | — | — | — |
| Users | `/users` | UsersPage | — | users | `users:read` | FI | — | — | — |
| Permissions | `/permissions` | PermissionsPage | enhanced-api | roles+permissions | `roles:read` | PI | Dup `/api/roles` (H3) | P5 | Consolidate role routers |
| Plan Settings | `/plan-settings` | PlanSettingsPage | plan-settings-api | rbac-monetization | `subscription.entitlement.manage` | PI | Admin-only plan CRUD | P4 | Confirm scope + entitlement |
| Settings | `/settings` | SettingsPage | — | smtp/storage settings | `settings:view` | FI | — | — | — |

### 3b. Built features with routes/pages but **missing from SideBar** (the core gap)

| Intended Label | Route | Page | API Service | Backend Router | Backend Permission | Status | Gap | Priority | Recommended Fix |
|---|---|---|---|---|---|---|---|---|---|
| **Claims** | `/claims` | ClaimsRegisterPage | claims-api | claims | `dms.claim.view` | **BE-UI / blocked** | No `ROUTE_PERMISSIONS` entry → `ProtectedRoute` redirects; no SideBar link | **P1** | Add `/claims` perm map + SideBar link |
| **SLA Tracker** | `/sla` | SLATrackerPage | sla-api | sla | `dms.claim.view` | **BE-UI / blocked** | Same as Claims | **P1** | Add `/sla` perm map + SideBar link |
| **Contract Appraisal** | `/contracts/appraisal` | ContractAppraisalPage | contracts-api | contract-appraisal | `dms.contract.appraisal.view` | **PERM** | Inherits `/contracts` perm (wrong) | **P1** | Add explicit `/contracts/appraisal` perm map + SideBar link |
| **Tasks** | `/tasks` | TasksPage | tasks-api | tasks | (ownership only; no perm) | **PI / no nav** | Commented out in SideBar; backend has no permission gate | **P1/P2** | Un-comment link; add backend task permission |
| **Subscription** | `/subscription-management` | SubscriptionManagementPage | plan-settings/billing-api | rbac-monetization | `subscription.entitlement.manage` + `subscription.upgrade` | **UI-NB (checkout)** | No SideBar link; checkout unwired | **P1/P4** | Add link; wire checkout (Phase 4) |
| **Notifications** | `/notifications` | NotificationCenterPage | notifications-api | notifications | open | **FI / no nav** | No SideBar link | **P1** | Add link (bell) |
| Contracts hub | `/contracts` | ContractsPage | contracts-api | contracts | `dms.document.view` | FI / no nav | Only sub-pages linked | P2 | Optional parent link/group |

---

## 4. Deep Audit — Claims

**Files:** `ClaimsRegisterPage.tsx`, `ClaimApprovalDialog.tsx`, `ClaimAssessmentDialog.tsx`, `claims-api.ts`; `routers/claims.py`, `services/{claim_service,claim_assessment_service,approval_service,evidence_bundle_service}.py`, `models/{claim,approval}.py`, `core/permissions.py`.

| Workflow | Backend | Frontend | Verdict |
|---|---|---|---|
| Claim creation | ✅ `POST /claims` | ✅ create dialog | **Integrated** |
| Register listing + filters | ✅ `GET /claims` scoped | ✅ table + type/status filters | **Integrated** |
| Claim detail view | ⚠️ `GET /claims/{id}` exists | ❌ **no dedicated detail page** (register row only) | **Gap** |
| Editing | ✅ `PUT /claims/{id}` | ✅ edit dialog | Integrated |
| Status workflow | ✅ `POST /claims/{id}/status` | ✅ inline dropdown | Integrated |
| Submit for review / assign / approve / return | ✅ approval endpoints (no self-approval) | ✅ `ClaimApprovalDialog` | Integrated |
| AI assessment | ✅ `POST /claims/{id}/assess` (cited) | ✅ `ClaimAssessmentDialog` + source ledger | Integrated |
| Evidence bundle export | ✅ `GET /claims/{id}/evidence-bundle` (ZIP) | ✅ row export | Integrated |
| SLA linkage | ✅ claims feed SLA scan | ❌ no SLA badge on claim row | **Gap** |
| Task linkage | ❌ no "create task from claim" | ❌ | **Gap** |
| Document/letter linkage | ⚠️ model has `linked_document_ids`/`linked_letter_ids` | ❌ no link picker UI | **Gap** |
| Reports/dashboard linkage | ⚠️ data exists | ❌ no claims widgets/report | **Gap** |
| RBAC + tenant scope | ✅ PolicyService + `build_scope_query` everywhere | ✅ (UX) but route blocked | **Route fix needed** |
| Error/loading states | n/a | ✅ toasts + disabled states | OK |
| Tests | ✅ `test_claims.py`, `test_approvals.py`, `test_claim_assessment.py` | ❌ no FE tests | **Gap** |

**Missing items / steps:** (1) `ClaimDetailPage` at `/claims/:id` (header, linked-correspondence panel, status timeline, comments, audit trail). (2) SLA badge on register rows (reuse `sla-api`). (3) Document/letter link-picker on the claim form. (4) "Create task from claim" action. (5) Claims summary on Reports/Dashboard. (6) `/claims` (+`/claims/:id`) entries in `ROUTE_PERMISSIONS` and a SideBar link. (7) FE tests for the register + dialogs.

---

## 5. Deep Audit — Contract Appraisal

**Files:** `ContractAppraisalPage.tsx`, `components/contract-appraisal/{AppraisalRegisters,ClauseLibrary}.tsx`, `contracts-api.ts`; `routers/contract_appraisal.py`, `services/contract_appraisal/*`, `models/contract_appraisal.py`.

| Capability | Backend | Frontend | Verdict |
|---|---|---|---|
| Generate appraisal (job) | ✅ `POST /contracts/appraisal/generate` (async) | ✅ generate + poll | Integrated |
| Job status | ✅ `GET …/jobs/{id}` | ✅ progress bar | Integrated |
| View report | ✅ `GET …/{report_id}` | ✅ sectioned viewer | Integrated |
| Edit draft | ✅ `PUT …/{id}` (locked→409) | ⚠️ no inline edit UI | **Gap** |
| Approve / reject | ✅ endpoints | ✅ buttons | Integrated |
| Regenerate (new version) | ✅ never overwrites | ✅ button | Integrated |
| Export PDF / DOCX | ✅ both (reportlab/python-docx) | ✅ both buttons | Integrated |
| Review comments | ✅ POST/GET | ✅ add comment | Integrated (no resolve/thread UI) |
| Citations | ✅ per-section + `/citations` | ✅ source ledger | Integrated |
| Create registers | ✅ idempotent `create-registers` | ✅ panel button | Integrated |
| Clause register/library | ✅ `GET /contracts/clauses` | ✅ `ClauseLibrary` | Integrated |
| Obligation/Risk/Key-date registers | ✅ list + `PUT` verify | ✅ tables + verify | Integrated |
| Inline edit of register items | ✅ `PUT …` (status/severity/owner) | ⚠️ only verification toggle | **Gap** |
| Task from obligation/risk/key-date | ❌ | ❌ | **Gap** |
| Claim from risk/entitlement finding | ❌ | ❌ | **Gap** |
| Contract/citation → source viewer | ⚠️ citation has doc/clause/page | ❌ click-through to document page | **Gap** |
| RBAC permissions | ✅ `dms.contract.appraisal.*` | ⚠️ route on wrong perm | **Perm fix** |
| Tests | ✅ `test_contract_appraisal.py` (P1+P2) | ❌ | **Gap** |

**SideBar / tabs recommendation:** add **Contract Appraisal** as a top-level link (or a tab inside a Contracts group: Upload · Search Clauses · Q&A · **Appraisal**). Add page tabs: *Report · Registers · Clause Library*. Wire **"Create task"** (from obligation/risk/key-date) and **"Raise claim"** (from a risk/entitlement finding) actions — these are the highest-value cross-module links and connect Appraisal → Tasks → Claims.

---

## 6. Deep Audit — Billing / Razorpay

**Files:** `SubscriptionManagementPage.tsx`, `PlanSettingsPage.tsx`, `billing-api.ts`, `plan-settings-api.ts`; `routers/{rbac_monetization,billing_webhooks}.py`, `services/{monetization_service,payment_gateway,billing_webhook_service,subscription_lifecycle_service}.py`.

| Item | State | Verdict |
|---|---|---|
| Plan catalog display | ✅ PlanSettingsPage | OK |
| Plan selection | ✅ | OK |
| Subscription checkout (backend) | ✅ `POST …/subscriptions/checkout` (step-up gated) | OK |
| **Checkout (frontend)** | ⚠️ `billing-api.startSubscriptionCheckout` exists but **no page imports it** | **C1 — open** |
| Razorpay hosted redirect | ⚠️ helper exists, unused | **Open** |
| Success / pending / failure callback page | ❌ none | **Open** |
| Retry payment / update method | ❌ | **Open** |
| Invoice preview | ✅ `get_invoice_preview` | OK (preview only) |
| Receipt / tax invoice download | ❌ | **Open** |
| Billing records view | ⚠️ records inserted by webhook; no UI | **Open** |
| Webhook idempotency | ✅ idempotent + signature-verified (W3.2) | **Closed vs audit C3** |
| Webhook financial validation (amount/currency/plan/period) | ⚠️ partial | **Partial** |
| Failed-payment admin queue | ❌ | **Open** |
| Usage / quota view | ⚠️ entitlement effective-services exists | **Partial** |
| Plan / add-on admin CRUD | ✅ backend | OK |
| Expert allocation UI | ⚠️ backend `expert_allocations` | **Partial** |
| Gateway-aware upgrade/downgrade/cancel/reactivate | ⚠️ wiring started (W3.3); verify lifecycle calls gateway | **Partial (C2)** |
| Entitlement only after verified payment/webhook | ⚠️ webhook-driven; verify no FE-success activation | **Must verify** |
| Sandbox tests | ⚠️ `test_billing_webhooks.py` exists; no checkout/e2e | **Partial** |

**Verdict:** billing is **not production-ready for live payments**. Backend plumbing is solid post-W3, but the **UI never starts checkout**, there is **no return/verify/retry screen**, **no receipt download**, and **no admin billing diagnostics**. Do not enable Razorpay live plans until Phase 4 closes these and entitlement activation is provably webhook-gated.

---

## 7. Deep Audit — Tasks

**Files:** `TasksPage.tsx`, `tasks-api.ts`; `routers/tasks.py`, task models/services.

| Item | State | Verdict |
|---|---|---|
| Create / assign / comment / status / due / priority | ✅ backend CRUD+comments; ✅ FE tasks tab wired | Integrated (tasks tab) |
| Linked document/claim/contract/appraisal ref | ⚠️ model supports some refs; not surfaced cross-module | **Gap** |
| My / Project / Overdue views | ⚠️ single list; no view tabs | **Gap** |
| Dashboard widgets | ❌ | **Gap** |
| Notification integration | ⚠️ notifications exist; tasks not wired to emit | **Gap** |
| RBAC / project-org scope | ⚠️ `_authorize_task_access` (ownership/scope) — **not PolicyService, no permission gate** | **Parity gap** |
| Other TasksPage tabs (users/documents/workflows) | ⚠️ mock/prototype | **Gap** |
| Tests | ✅ `test_tasks.py` | OK (backend) |

**Recommendation:** Tasks should be **both** a standalone SideBar module **and** embedded as a panel/action inside **Claims, Contract Appraisal, Documents, and Letters** (create-task-from-X). Standalone gives "My/Project/Overdue" management; embedded gives in-context follow-ups (the high-value path: appraisal risk → task; claim deadline → task). Backend should adopt a `dms.task.*` permission family via PolicyService to match the frontend `tasks:read` gate.

---

## 8. SideBar UX & Permission Strategy

**Visibility decision per link** = `authenticated` AND `ROUTE_PERMISSIONS[path]` satisfied AND (where paid) `entitlement` AND (where applicable) `production-ready flag`. Backend remains authoritative; the SideBar filter + `ProtectedRoute` are UX only. Every new route **must** be added to `ROUTE_PERMISSIONS` **and** the SideBar together (otherwise it is unreachable, exactly the current Claims/SLA bug).

**Recommended structure** (grouped):

| Group | Link | Route | Required permission | Entitlement | Unauthorized behavior |
|---|---|---|---|---|---|
| **Work** | Dashboard | `/dashboard` | `dms.dashboard.view` | — | hide |
| | Documents (Letters Library) | `/documents` | `dms.document.view` | — | hide |
| | Search | `/documentsearch` | `dms.document.view` | — | hide |
| **Contracts** | Upload Contract | `/contracts/upload` | `dms.document.upload` | — | hide |
| | Search Clauses | `/contracts/search` | `dms.document.view` | — | hide |
| | Contract Q&A | `/contracts/qa` | `dms.document.view` | (RAG add-on) | upgrade prompt |
| | **Contract Appraisal** | `/contracts/appraisal` | `dms.contract.appraisal.view` | (appraisal add-on) | upgrade prompt |
| **Claims** | **Claims Register** | `/claims` | `dms.claim.view` | — | hide |
| | **SLA / Time-bar** | `/sla` | `dms.claim.view` | — | hide |
| **Letters** | Letter Drafting | `/letters` | `drafting.request.view` | (drafting add-on) | upgrade prompt |
| | Letter Quality / Templates | `/letter-quality`, `/letter-templates` | as today | — | hide |
| **Tasks** | **Tasks** | `/tasks` | `dms.task.view` (new) | — | hide |
| **Insights** | Reports & Analytics | `/reports` | `reports:view` | — | hide |
| | **Notifications** | `/notifications` | open | — | show |
| **Admin** | Organizations/Projects/Users/Permissions | as today | as today | — | hide |
| | Plan Settings | `/plan-settings` | `subscription.entitlement.manage` | — | hide |
| | **Subscription** | `/subscription-management` | `subscription.entitlement.manage` + `subscription.upgrade` | — | hide |
| **Ops** | System Health | `/health` | `system:admin` | — | hide |
| | Settings | `/settings` | `settings:view` | — | hide |

For paid modules, prefer **show-with-upgrade-prompt** (drives conversion) over hard-hide; for unimplemented modules prefer **hide** behind a feature flag.

---

## 9. Phase-wise Implementation Plan

| Phase | Objective | Features Covered | Files to Modify | Backend Changes | Frontend Changes | Tests Required | Acceptance Criteria | Expected Improvement |
|---|---|---|---|---|---|---|---|---|
| **0 — Baseline audit & route inventory** | Single source of truth for links↔routes↔API↔perms↔gaps | All | *this file*; add `ROUTE_INVENTORY.md` + CI artifact | none | none | route-inventory guard test (exists) extended to assert every `<Route>` has a `ROUTE_PERMISSIONS` entry | Inventory committed; CI lists unmapped routes | No score change unless CI artifact added |
| **1 — SideBar route & permission alignment** | Make every finished feature reachable & correctly gated | Claims, SLA, Appraisal, Tasks, Subscription, Notifications | `rolePermissions.ts`, `Sidebar.tsx`, `routes.tsx` | add `dms.task.*` perms (optional this phase) | add `/claims`,`/sla`,`/contracts/appraisal`,`/claims/:id` to `ROUTE_PERMISSIONS`; add SideBar links (Claims, SLA, Contract Appraisal, Tasks, Subscription, Notifications); RoleGuard the new routes | FE route-permission parity test; render test that each visible link resolves | All visible links map to valid routes/pages; unauthorized hidden; FE perms == backend perms | **+0.4 → ~7.4** |
| **2 — Claims & Tasks workflow completion** | Claims operational end-to-end; Tasks cross-linked | Claims detail, links, SLA badge; Tasks views, notifications, cross-create | claims pages, `claims-api`, tasks pages, `tasks.py` | `dms.task.*` via PolicyService; task↔claim link fields; notification emit on task assign/overdue | `ClaimDetailPage`; doc/letter link-picker; SLA badge; "create task from claim"; My/Project/Overdue task views | claim detail + link tests; task scope/permission tests; FE smoke | Create→assign→track→close claim tasks; claim detail shows links + SLA | **+0.4 → ~7.8** |
| **3 — Contract Appraisal workflow completion** | Appraisal usable end-to-end + actionable findings | inline register edit, citation→source, cross-create | appraisal page/components, `contract_appraisal.py` | task-from-finding + claim-from-risk endpoints (or reuse) | inline edit (status/severity/owner); citation click-through to document page; page tabs | endpoint + FE tests for register edit & cross-create | Risks/obligations/key-dates editable & actionable; tasks/claims generated from findings | **+0.3 → ~8.1** |
| **4 — Billing / Razorpay production completion** | Real checkout + verified entitlement + admin diagnostics | checkout, return/retry, receipts, admin billing | `SubscriptionManagementPage`, new pages, `billing-api`, `rbac_monetization.py`, `billing_webhook_service.py` | order API + verify; `(provider,event_id)` idempotency; amount/plan/currency validation; receipt/invoice download; admin billing/webhook/failed-payment APIs | wire checkout + redirect; success/pending/failure/retry pages; receipts list; admin billing diagnostics | sandbox checkout + webhook mismatch + idempotency + lifecycle tests | Plan→checkout→return→active; failed→retry; admin inspects records; entitlement only post-webhook | **+0.5 → ~8.6** |
| **5 — API/frontend contract closure & CI gates** | Stop drift; classify every route | all routers/services | CI workflow, `ROUTE_INVENTORY.md`, parity script | emit OpenAPI in CI; consolidate dup `/api/roles` | typed contract layer; classify 77 unreferenced APIs | OpenAPI contract check; permission-parity check | CI fails on API/permission drift; every route classified | **+0.2 → ~8.8** |
| **6 — Observability, admin diagnostics, production readiness** | Operable + monitorable | ingestion/vector/RAG/storage/webhook/queue diagnostics | dashboard/admin pages, `dashboard.py`, ops routers | retry/reprocess endpoints; job/queue status APIs | ops-gated diagnostics page; dashboard widgets (claims/SLA/tasks); retry controls | ops diagnostics + retry tests | Admin monitors jobs/queues/webhooks; failed jobs retryable | **+0.2 → ~9.0** |

---

## 10. Expected Improvement After Each Phase

| After Phase | Readiness | Headline outcome |
|---|---:|---|
| 0 | ~7.0 | Complete, committed map of links/routes/APIs/perms/gaps (+ CI inventory). |
| 1 | ~7.4 | **Every finished feature reachable and correctly permissioned** (Claims/SLA/Appraisal/Tasks visible). |
| 2 | ~7.8 | Claims end-to-end with detail + links + SLA; Tasks cross-linked & notified. |
| 3 | ~8.1 | Appraisal findings editable & actionable; Appraisal→Tasks/Claims wired. |
| 4 | ~8.6 | Real Razorpay checkout, verified entitlement, admin billing diagnostics. |
| 5 | ~8.8 | CI blocks API/permission drift; all routes classified. |
| 6 | ~9.0 | Observable, recoverable ops; production-ready. |

---

## 11. Files Created / Updated (this pass)

- **Created:** `Final_Plan_Implementation_Close_Gap.md` (this document).
- **No application code modified** (read-only planning pass per instructions).
- **Recommended next-pass artifacts** (Phase 0/5): `ROUTE_INVENTORY.md`, a CI permission-parity script, OpenAPI artifact step.

---

## 12. Risks & Assumptions

- **Assumption:** the published `contraclaim-dms` mirrors local `projectDMS` (kept in lockstep this cycle); audit verified against local.
- **Risk — route deny-by-default:** because `ProtectedRoute` redirects unmapped paths, adding a SideBar link without a `ROUTE_PERMISSIONS` entry silently fails. Always change `rolePermissions.ts` + `Sidebar.tsx` together (enforced by the Phase 0 CI inventory test).
- **Risk — permission name drift:** backend uses `dms.claim.*` / `dms.contract.appraisal.*`; the user's `can()` set must include these (seeded roles). Verify role seeds grant the new families or they'll be hidden.
- **Risk — billing money-safety:** never activate paid entitlement from a frontend success redirect; entitlement must be webhook/verify-gated (Phase 4 acceptance).
- **Risk — Tasks authz:** backend tasks use ad-hoc ownership checks, not PolicyService; a `dms.task.*` migration is needed for parity and is a behavior change to test carefully.
- **Assumption:** Razorpay **subscriptions** are the primary model; **orders** added only for one-time payments (per readiness plan §Assumptions).

---

## 13. Recommended First Implementation Phase

**Start with Phase 1 (SideBar route & permission alignment).** It is the highest value-to-effort move: it makes already-finished, already-tested backend features (Claims, SLA, Contract Appraisal, Tasks) **actually reachable and correctly gated**, with no backend risk and a small, well-contained frontend change set (`rolePermissions.ts`, `Sidebar.tsx`, `routes.tsx`). It also unblocks Phases 2–3, which build on those now-reachable pages. Pair it with the Phase 0 CI inventory test so the "unmapped route = dead route" class of bug cannot recur.

Concrete Phase 1 change set:
1. `rolePermissions.ts` — add `/claims: [dms.claim.view]`, `/claims/:id`, `/sla: [dms.claim.view]`, explicit `/contracts/appraisal: [dms.contract.appraisal.view]`, and (if adopting) `/tasks: [dms.task.view]`.
2. `Sidebar.tsx` — add links: **Claims**, **SLA Tracker**, **Contract Appraisal**, **Tasks** (un-comment), **Subscription**, **Notifications**; group under Claims / Contracts / Tasks / Admin.
3. `routes.tsx` — wrap the new feature routes in `RoleGuard` for consistent UX fallback.
4. Tests — extend the route-inventory guard to assert every `<Route>` path exists in `ROUTE_PERMISSIONS`; add a render test that each visible SideBar link resolves to a mounted route.
