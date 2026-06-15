# Contraclaim DMS Production-Readiness Audit

Audit date: 2026-05-19  
Repository path: `c:\SaaS\projectDMS`  
Scope reviewed: React/Vite frontend, FastAPI backend, MongoDB/Qdrant/FalkorDB/Redis integrations, Docker/Apache deployment, CI, scripts, docs, and tests.

## Executive Summary

Contraclaim DMS is a substantial, actively hardened application. It has a modern React frontend, a broad FastAPI API, MongoDB indexing, Redis-backed runtime state, cookie-based auth, upload limits, audit events, health checks, Docker Compose production wiring, and CI gates for tests, dependency scanning, secret scanning, and image scanning.

It is not production-ready yet. The main blockers are authorization consistency, session/CSRF hardening, database migration/index discipline, legacy duplicate APIs, dependency currency, stale documentation, and operational runbook gaps. The frontend builds, but still carries localStorage-derived role/scope state, large bundles, old duplicate pages, and inconsistent API/auth patterns.

Overall score before improvements: **63/100**  
Estimated score after roadmap completion: **88/100**

## Verification Performed

| Check | Result | Notes |
|---|---:|---|
| Backend compile | Pass | `python -m compileall -q backend\rbac_backend` completed successfully. |
| Frontend production build | Pass with warnings | `npm run build` completed in ~31s. Large chunks: `DocumentViewerPage` ~678 kB, main index ~542 kB, `Dashboard` ~411 kB, `LetterTemplateEditorPage` ~377 kB. |
| Frontend lint | Pass with warnings | `npm run lint` reported **77 warnings**, mainly hook dependency and Fast Refresh warnings. |
| Targeted backend tests | Pass | `pytest test_config_validation.py test_login.py -q`: **7 passed**. Full backend and frontend suites were not run in this audit pass. |
| Frontend dependency audit | Fail | `npm audit --audit-level=low`: **7 moderate vulnerabilities** through Vite/esbuild dev tooling. |
| Python dependency audit | Not run | `pip-audit` is not installed locally. CI includes it, but this local audit could not execute it. |
| Inventory | Reviewed | 56 frontend page TSX files, 105 frontend component TSX files, 42 backend router files, 90 backend service files, 37 backend model files, 33 backend test files, ~290 route decorators. |

## Current Strengths

- FastAPI docs are disabled automatically in production in `backend/rbac_backend/main.py`.
- Production configuration validation rejects placeholder secrets, localhost MongoDB, insecure auth cookies, dev headers, missing Redis runtime state, missing metrics token, and standalone MongoDB unless explicitly allowed in `backend/rbac_backend/core/config.py`.
- Auth cookies are HttpOnly and can be Secure/SameSite-configured through settings.
- Login rate limiting, account lockout, audit logging, and session invalidation exist.
- Upload handling includes MIME sniffing, upload concurrency guards, size limits, path normalization, and chunked contract upload support.
- MongoDB indexes are created for many scoped access patterns and large workflows.
- Docker production compose uses internal networks for data services, health checks, restart policies, log rotation, and required environment variables.
- CI includes Gitleaks, backend tests, frontend tests/build, dependency scans, and Trivy image scans.

## Critical Findings

### 1. Authorization Policy Is Split Across Too Many Systems

Evidence:
- `backend/rbac_backend/core/security.py` defines `require_permission`, `has_permission`, `require_roles`, `authorize_scope`, and `build_scope_query`.
- `backend/rbac_backend/services/authorization_service.py` has another permission layer.
- `backend/rbac_backend/services/policy_service.py` introduces a newer deny-by-default domain policy.
- `client/src/config/rolePermissions.ts` separately defines route-level role rules for UX.

Risk:
- This increases the chance that one API path is protected by one policy, another path by a different policy, and edge cases drift.
- Example bug: `backend/rbac_backend/routers/ai_assistant.py` calls `authorize_scope(current_user, "admin:read")` and `authorize_scope(current_user, "admin:write")`. The second positional argument is `organization_id`, not a permission. This is logically wrong and can deny valid admins or produce misleading scope checks.

Recommendation:
- Make `PolicyService.authorize(...)` the single backend authorization entry point.
- Replace direct `authorize_scope` and legacy `AuthorizationService.require_permission` calls incrementally.
- Add route inventory tests asserting every non-public route declares auth, permission, and scope policy.
- Add regression tests for AI assistant admin endpoints.

Before score: **55/100**  
After score: **88/100**

### 2. Cookie Auth Lacks Explicit CSRF Protection

Evidence:
- Cookies are set in `backend/rbac_backend/routers/auth.py`.
- CORS allows credentials in `backend/rbac_backend/main.py`.
- No CSRF token/middleware was found in backend or frontend.

Risk:
- HttpOnly cookies reduce token theft from XSS, but state-changing APIs still need CSRF protection when cookie credentials are accepted.
- SameSite=Lax helps but is not a complete application-level CSRF control, especially with same-site subdomain risks and future configuration changes.

Recommendation:
- Add double-submit CSRF tokens or signed per-session CSRF tokens.
- Require the token on all unsafe methods except explicitly public endpoints.
- Validate `Origin`/`Referer` for browser requests.
- Keep `AUTH_COOKIE_SAMESITE=strict` unless cross-site SSO or embedding requirements force otherwise.

Before score: **60/100**  
After score: **90/100**

### 3. Duplicate Legacy Authentication Endpoints Remain Active

Evidence:
- Primary auth router exposes `/api/login`, `/api/refresh`, `/api/logout`, `/api/me`.
- Users router still exposes `/api/token`, `/api/logout`, and `/api/users/me`.
- `backend/rbac_backend/routers/users.py` uses a separate auth controller path and token/session behavior.

Risk:
- Two login/logout paths can diverge in cookie handling, session claims, refresh semantics, and audit behavior.
- Client behavior becomes brittle because some services use `/login` dev proxy paths, some call `/api/login`, and some old code still reads `localStorage.accessToken`.

Recommendation:
- Deprecate `/api/token` and duplicate `/api/logout`; return `410 Gone` after a compatibility window.
- Route all auth through `routers/auth.py`.
- Add API contract tests for login, refresh, logout, cookie clearing, and stale session rejection.

Before score: **62/100**  
After score: **90/100**

### 4. Frontend Still Uses LocalStorage for Roles, Scope, and Some Bearer Headers

Evidence:
- `client/src/pages/LoginPage.tsx` writes roles, user id, org id, and project id to `localStorage`.
- `client/src/hooks/useRBAC.ts`, `client/src/pages/HealthPage.tsx`, `client/src/pages/DocumentViewerPage.tsx`, `client/src/pages/ReferencePage.tsx`, and older utility pages read localStorage for role/scope/auth context.
- `client/src/services/api.ts` correctly avoids mirroring access tokens, but older code paths still build `Authorization: Bearer ${localStorage.getItem("accessToken")}` manually.

Risk:
- If any page or service accidentally trusts localStorage-derived scope, users can tamper with UI decisions.
- LocalStorage role state can get stale after role changes, causing bad UX or misleading route access.
- Manual fetch calls bypass the centralized axios refresh/error handling.

Recommendation:
- Keep localStorage only for harmless preferences such as timezone/date format.
- Use `/api/me` plus React Query cache for roles/scope.
- Move all API calls through `createHttpClient`.
- Delete old `client/src/utils/*Page.tsx`, `useLetterWorkflow1/2/3`, `UploadPage1`, `UploadPage_check`, and other duplicate/dormant pages once routes are confirmed unused.

Before score: **62/100**  
After score: **88/100**

### 5. Database Integrity Relies on Application Logic More Than Constraints

Evidence:
- `backend/rbac_backend/core/database.py` creates many indexes, but user email/username uniqueness is not indexed.
- Default user initialization checks existing users by username only.
- There is no visible Mongo migration framework; index creation is asynchronous and best-effort.

Risk:
- Duplicate users, duplicate emails, duplicate document numbers, or partially migrated collections can appear under concurrency or manual imports.
- Best-effort indexes can fail silently after retry exhaustion.
- Production changes are harder to roll back or audit without versioned migrations.

Recommendation:
- Add versioned migrations with idempotent up/down or forward-only scripts.
- Add unique indexes for `users.email`, `users.username`, share token hash, relevant role/permission keys, and tenant-scoped business keys where required.
- Fail startup or readiness if required indexes are missing in production.
- Add migration runbooks and backup/restore validation before schema changes.

Before score: **58/100**  
After score: **86/100**

### 6. Public Share Links Need Stronger Governance

Evidence:
- Share tokens are random and hashed before storage in `backend/rbac_backend/services/email_service.py`.
- They have TTL and access count tracking.
- The public download endpoint is unauthenticated by design in `backend/rbac_backend/routers/email_share.py`.
- No revocation API, per-token rate limit, recipient binding, TTL index, or share-token index was found.

Risk:
- Long-lived public URLs can leak document contents until expiry.
- Missing revocation flow limits incident response after a mistaken share.
- Public download can become a scraping target.

Recommendation:
- Add TTL and unique indexes for `document_share_tokens`.
- Add revoke/list/audit APIs for document owners and admins.
- Add public endpoint rate limiting by IP and token hash.
- Consider one-time or recipient-bound links for sensitive documents.

Before score: **65/100**  
After score: **90/100**

### 7. Frontend Bundle and Rendering Risks Remain

Evidence:
- Vite reports large minified chunks above 500 kB.
- `LetterTemplateEditorPage.tsx` and `LetterTemplatePage.tsx` use `dangerouslySetInnerHTML`.
- Lint reports 77 hook/Fast Refresh warnings.
- The `LoginPage` is lazy-loaded in routes but statically imported by `ProtectedRoute`, preventing effective chunk splitting.

Risk:
- Large first-party chunks slow cold loads and degrade mobile performance.
- HTML preview rendering can become an XSS vector if templates/user content are not sanitized.
- Hook dependency warnings can cause stale data, missed refreshes, or runaway polling.

Recommendation:
- Add DOMPurify or equivalent sanitization at every rich HTML preview/render boundary.
- Split PDF viewer, dashboard charts, rich text editor, and document search into separate manual chunks.
- Fix hook dependency warnings, especially RBAC/sidebar/notification/workflow hooks.
- Add accessibility and responsive smoke tests for the top workflows.

Before score: **65/100**  
After score: **87/100**

### 8. Dependency and Image Hardening Needs Closure

Evidence:
- `npm audit` reports 7 moderate vulnerabilities via Vite/esbuild.
- Frontend Dockerfile uses `npm install` instead of `npm ci`.
- Docker images are version tags, not digest-pinned.
- Python dependency audit could not be run locally because `pip-audit` is missing.

Risk:
- Reproducibility and vulnerability tracking are weaker than production expectations.
- CI has dependency scanning, but local and release gates need documented pass/fail evidence before release.

Recommendation:
- Upgrade Vite/plugin/vitest stack or document a formal risk acceptance for dev-only advisories.
- Use `npm ci` in Docker builds.
- Pin base images by digest for production.
- Generate SBOMs and store dependency scan artifacts for each release.

Before score: **68/100**  
After score: **88/100**

## Frontend Page Scores

Scores are current-state estimates for active routes, based on code review, build output, lint output, auth/API patterns, validation, loading/error states, and production UX risk. "After" assumes the listed recommendations are applied.

| Route / Page | Before | Key Issues | Recommendation | After |
|---|---:|---|---|---:|
| `/` LandingPage | 72 | Public page is functional, but app docs still reference Lovable; contact flow depends on backend email config. | Replace generic copy/docs, add rate-limit UX, verify mobile hero, add contact error states. | 88 |
| `/login` LoginPage | 70 | Writes roles/scope to localStorage; static import defeats route lazy chunking; background carousel can hurt performance. | Use server session state only, remove static import from `ProtectedRoute`, optimize images. | 90 |
| `/overview` Overview | 75 | Lightweight page, low risk; needs role-specific empty/error states. | Add role-aware cards and API-backed loading/error states. | 88 |
| `/dashboard` Dashboard | 66 | Large ~411 kB chunk; likely chart-heavy; lint warnings in dashboard variants. | Split chart/vendor chunks, add API error boundaries and skeletons. | 86 |
| `/organizations` OrganizationsPage | 68 | Admin CRUD area; duplicate old utility page still carries localStorage bearer headers. | Remove stale utilities, add optimistic/error states and backend policy tests. | 86 |
| `/projects` ProjectsPage | 66 | Reads/writes org/project scope from localStorage; fallback logic is complex. | Drive scope from `/me`, simplify selection state, add scoped list tests. | 86 |
| `/documents` DocumentsPage | 67 | Large table workflow; manual auth/token remnants; many filters and state branches. | Centralize API calls, virtualize large tables, add empty/loading/error tests. | 87 |
| `/documentsearch` EnhancedDocumentsPage | 66 | Search UX exists but needs performance and RBAC consistency verification. | Add debounced query cancellation, backend pagination contracts, accessibility tests. | 86 |
| `/documents/legacy` DocumentsPage | 45 | Legacy alias keeps duplicate behavior surface alive. | Remove or redirect after migration. | 80 |
| `/tags` TagsPage | 66 | CRUD page with RBAC-sensitive tag hierarchy; lint/old code patterns present. | Add server-side permission tests and nested empty/error states. | 86 |
| `/profile` ProfilePage | 70 | Uses localStorage for timezone, acceptable; needs stronger upload validation UX for photo. | Add image size/type client checks and retry states. | 86 |
| `/users` UsersPage | 64 | High-risk admin page; duplicate auth models and role assignment complexity. | Enforce step-up for role/password/disable changes, add privilege escalation tests. | 88 |
| `/permissions` PermissionsPage | 58 | Critical RBAC page; hook warnings; duplicate roles endpoints exist in permissions and roles routers. | Consolidate role/permission APIs, add diff review and audit event UI. | 88 |
| `/settings` SettingsPage | 64 | Storage/SMTP/security settings are sensitive; needs step-up and stronger role checks. | Require step-up for secrets, mask values, add config audit logs. | 88 |
| `/plan-settings` PlanSettingsPage | 62 | Monetization/entitlement controls affect access; fail-open default exists in config. | Require admin permission, audit all changes, force fail-closed in production. | 86 |
| `/notifications` NotificationCenterPage | 72 | Functional notification UX; hook dependency warning. | Fix refresh dependencies, add unread synchronization tests. | 88 |
| `/upload` UploadPage | 65 | Complex single/bulk upload; client does limited validation; relies on local org/project state. | Add client file size/type precheck, resumable status UX, scoped org/project from server. | 88 |
| `/documentviewer/:id` DocumentViewerPage | 64 | Manual fetch with localStorage bearer headers; PDF viewer chunk ~678 kB; metadata/reference complexity. | Route all calls through axios client, split PDF worker/viewer, sanitize rendered fields. | 87 |
| `/share/:id` ShareDocumentPage | 64 | Public link governance and email attachment risks; hook dependency warnings. | Add revocation/list UI, per-token TTL display, recipient confirmation. | 88 |
| `/email-groups` EmailGroupsPage | 66 | Sensitive recipient management; hook dependency warning. | Add duplicate detection, domain validation, project-scoped tests. | 86 |
| `/register` RegisterPage | 60 | Superadmin-only user/org/project creation; several hook warnings. | Split into dedicated admin flows, require step-up, validate role assignment server-side. | 88 |
| `/folders` FolderStructurePage | 62 | Path-based operations can be risky; backend uses path route segments. | Add path normalization tests, prevent open-file exposure, add empty/loading states. | 84 |
| `/tasks` TasksPage | 68 | Basic workflow page; needs scoped RBAC and notification integration tests. | Add assignee validation, status transition audit, empty states. | 86 |
| `/parties` PartiesInvolvedPage | 68 | Domain CRUD page; representative relationships add integrity risk. | Add uniqueness rules, import/export validation, accessibility checks. | 86 |
| `/representatives` RepresentativesPage | 68 | Similar party relationship risks. | Add scoped duplicate checks, delete dependency warnings, tests. | 86 |
| `/letters` LetterWorkflowPage | 64 | Large workflow state; sessionStorage/localStorage prefill; multiple old hooks remain. | Remove old hooks, centralize workflow state, add end-to-end drafting tests. | 88 |
| `/documents/summary/:id` LetterSummaryPage | 63 | Manual auth headers, hook warnings, comment/reference rendering risks. | Use central API, sanitize text, add comments loading/error states. | 86 |
| `/letters/:id/input` LetterInputPage | 66 | Workflow form page; needs robust validation and role handling. | Server-driven allowed actions, autosave, conflict handling. | 87 |
| `/letters/:id/strategic-plan` and `/strategy` | 64 | Complex AI plan state; hook warnings. | Add stale-run protection, explicit approval locks, source trace validation. | 88 |
| `/letters/:id/draft` LetterDraftPage | 63 | Rich drafting UI and large chunk; AI output needs sanitization and auditability. | Split editor, sanitize output, add version compare and approval tests. | 88 |
| `/letters/:id/review` LetterReviewPage | 66 | Review workflow needs strict transition controls. | Backend state machine tests, reviewer assignment checks, audit UI. | 88 |
| `/letters/:id/approval` LetterApprovalPage | 66 | Approval is business-critical. | Require step-up/dual control where needed, immutable approval event logs. | 89 |
| `/letters/:id/completed` LetterCompletedPage | 74 | Lower-risk read-only completion page. | Add immutable issued-letter verification and export state. | 88 |
| `/letter-quality` LetterQualityDashboardPage | 66 | Reporting/dashboard; role and data quality sensitive. | Add metric definitions, role-scoped queries, loading/error states. | 86 |
| `/reports` ReportsAnalyticsPage | 65 | Export/download risk; many hook warnings. | Add report permission matrix, export audit, backend query limits. | 87 |
| `/letter-templates` LetterTemplatePage | 62 | Uses HTML preview rendering; template governance needed. | Sanitize previews, template versioning, approval/audit flow. | 88 |
| `/letter-templates/:id/edit` LetterTemplateEditorPage | 60 | Rich HTML editor, `dangerouslySetInnerHTML`, large chunk ~377 kB. | Add sanitizer, split editor chunk, version history, step-up for publish. | 88 |
| `/contracts` ContractsPage | 70 | Read/navigation page; lower risk than upload/search. | Add contract lifecycle empty/error states. | 86 |
| `/contracts/upload` ContractsUploadPage | 66 | Chunked upload UI; localStorage scope; polling cleanup warning. | Use server scope, fix polling cleanup, add resumable failure recovery. | 88 |
| `/contracts/search` ContractsSearchPage | 64 | Search/AI-heavy and ~34 kB chunk; needs query guardrails. | Add backend pagination/timeouts, relevance feedback, no-results states. | 87 |
| `/contracts/qa` ContractQAPage | 63 | AI Q&A can expose cross-tenant content if retrieval filters fail. | Enforce server-side org/project filters and add retrieval isolation tests. | 88 |
| `/reference/:id` ReferencePage | 63 | Manual auth headers and complex linking. | Central API client, source/target scope checks, bidirectional link tests. | 87 |
| `/health` HealthPage | 58 | Superadmin route in frontend, but uses localStorage role/auth headers; exposes operational actions. | Use server role state, step-up for repair actions, require metrics token where applicable. | 88 |
| `*` NotFound | 78 | Basic route. | Add app navigation affordance and telemetry. | 88 |

Additional frontend concern: there are many duplicate or dormant pages (`Dashboard1`, `DashboardPage`, `UploadPage1`, `UploadPage_check`, `DocumentsPage_tranculated`, `DocumentViewerPageOriginal`, `PermissionsPageImproved`, old `useLetterWorkflow*` hooks, archive pages). These increase maintenance and security review cost. Remove or quarantine them outside `src` if not part of the active application.

## Backend/API Area Scores

| Backend Area | Before | Key Risks | Recommendation | After |
|---|---:|---|---|---:|
| Auth/session/login/logout/refresh | 68 | Duplicate auth routes, no explicit CSRF, mixed token/cookie semantics. | Single auth router, CSRF tokens, refresh/session contract tests. | 90 |
| User management | 62 | High privilege operations, missing unique email/username indexes, role assignment complexity. | Unique indexes, step-up, role assignment matrix tests. | 88 |
| Roles/permissions/RBAC | 55 | Multiple authorization systems and duplicate roles endpoints. | Consolidate on `PolicyService`, route inventory policy tests. | 88 |
| Organizations/projects/scope | 66 | Scope logic repeated in routes/services; ObjectId/string expansion inconsistencies. | Central scope service with tests for every role and mixed id type. | 86 |
| Documents API | 68 | Complex upload/download/reference/enclosure surface; manual policies mixed with new policy service. | Consolidate policies, add public/private download tests, add ETag/concurrency coverage. | 88 |
| Bulk document upload | 66 | Background processing, CSV encoding, temp file lifecycle, partial failure risk. | Durable job records, retry semantics, per-file idempotency keys, stronger CSV validation. | 87 |
| Contract upload/ingestion/search | 65 | Queue/worker dependency, chunking, external Marker/AI tooling, retrieval isolation. | Durable state machine, idempotent chunk commit, tenant retrieval tests. | 88 |
| Letter workflow | 64 | Complex state transitions and AI-generated content; approval governance incomplete. | Explicit workflow state machine, immutable audit, reviewer/approver policy tests. | 88 |
| Letter drafting v2 | 66 | Strong models exist, but many run states and export/issue actions are sensitive. | Enforce allowed transitions, source ledger verification, replay/idempotency tests. | 89 |
| AI assistant/deep planning | 58 | Misused `authorize_scope`, model config endpoints, prompt/template mutation risk. | Fix authorization, add prompt config step-up/audit, tenant-filter retrieval tests. | 88 |
| Retrieval engine/RAG/vector services | 63 | Multi-store consistency risk across Mongo/Qdrant/FalkorDB; query filters must be airtight. | Mandatory org/project filters, reconciliation SLO, vector deletion tests. | 87 |
| Search/reporting/dashboard | 64 | Export and analytics queries can become expensive or cross-scope. | Query caps, export audit, cached summaries, scoped aggregation tests. | 86 |
| Email/share/contact/groups | 65 | Public share governance, recipient validation, contact abuse risk. | Revocation APIs, token/IP rate limits, recipient domain policies, email delivery retries. | 88 |
| SMTP/storage settings | 67 | Secrets are encrypted, but settings changes need step-up and audit UX. | Dedicated secret key rotation, step-up, masked responses, audit events. | 89 |
| Notifications/websockets | 70 | Singleton service lifecycle and websocket auth need full coverage. | Authenticated WS tests, reconnect behavior, delivery dedupe. | 86 |
| Folder/file services | 63 | Path operations and file serving are security-sensitive. | More path traversal tests, object storage abstraction, malware scanning option. | 87 |
| Runtime state/cache/rate limiting | 70 | Redis fallback is in-memory per process; production requires Redis. | Fail production startup if Redis unavailable; monitor rate-limit backend. | 88 |
| Health/metrics/observability | 72 | Good health endpoints; some legacy performance endpoints overlap `/metrics`. | Standardize metrics path/auth, emit traces and structured logs. | 88 |
| Database/index management | 58 | No migration framework; async best-effort indexes; missing key unique indexes. | Versioned migrations, readiness index checks, restore drills. | 86 |
| Worker/background jobs | 64 | Queue exists, but retry/deadletter visibility and worker scaling need proof. | Deadletter dashboard, idempotency, worker concurrency tests. | 87 |
| Deployment/config/CI | 70 | Good baseline, but Docker reproducibility and release evidence gaps remain. | `npm ci`, digest pins, SBOMs, staged deploy evidence, full smoke tests. | 88 |
| Supporting microservices | 62 | Graphiti/Docling/LangGraph services are optional/experimental and less covered. | Separate readiness gates, auth between services, resource limits and retries. | 84 |

## Database Readiness

Before score: **58/100**  
After score: **86/100**

Findings:
- MongoDB is used without an explicit migration/versioning framework.
- Index coverage is broad but not complete for identity and public share integrity.
- Required indexes are created asynchronously after startup; failures are logged but not fatal.
- Mongo production validation requires replica set configuration, which is a strong baseline.
- There is no documented restore verification tied to release gates, although backup/restore scripts exist.
- Audit collections exist, but not every critical business action is clearly routed through immutable audit events.

Recommendations:
- Add migrations under `backend/rbac_backend/migrations` or an external tool such as Mongock-style scripts.
- Add unique indexes for users, share tokens, role/permission identifiers, and tenant-scoped document identifiers where business rules require uniqueness.
- Add TTL indexes for share tokens, temporary upload sessions, stale sessions, and transient jobs.
- Add production readiness checks that verify required indexes and fail `/health/ready` if missing.
- Run backup and restore rehearsals as a release gate.

## Security Readiness

Before score: **58/100**  
After score: **89/100**

Findings:
- Good: password hashing uses bcrypt; auth cookies are HttpOnly; dev headers are disabled by default; production config rejects insecure cookie and placeholder settings.
- Gap: no explicit CSRF protection for cookie-authenticated unsafe requests.
- Gap: RBAC logic is duplicated and policy drift is likely.
- Gap: frontend stores role/scope context in localStorage.
- Gap: public share links lack visible revocation and rate limiting.
- Gap: HTML preview rendering needs sanitization around `dangerouslySetInnerHTML`.
- Gap: `RBAC_ENTITLEMENT_FAIL_OPEN` defaults to true, though production validation rejects it. This is safe in production if validation runs, but risky in staging/dev and tests.
- Gap: dependency vulnerability audit currently fails for frontend dev tooling.

Security recommendations:
- Add CSRF middleware and tests.
- Make backend policy deny-by-default and centralized.
- Add route-level authorization inventory tests for all ~290 endpoints.
- Add step-up authentication for role, permission, SMTP, storage, plan, template publish, delete, and approve actions.
- Add public share revocation, token TTL index, IP/token rate limiting, and audit UI.
- Add DOMPurify before all rich HTML preview/render paths.
- Enforce dependency scan pass or signed risk acceptance before release.

## DevOps and Deployment Readiness

Before score: **70/100**  
After score: **88/100**

Findings:
- Production compose is stronger than development compose: required secrets, internal networks, health checks, and log rotation are present.
- Apache reverse proxy sets useful security headers and CSP.
- TLS is not terminated in the provided compose stack; HSTS is set by Apache even though compose exposes HTTP port 80.
- Dockerfiles are non-root, but frontend uses `npm install` and global `serve`; base images are not digest-pinned.
- CI is comprehensive on paper, but local verification only ran compile/build/lint/targeted tests.
- Release runbook exists, but README/client README are stale and contain encoding artifacts and Lovable boilerplate.

Recommendations:
- Put TLS termination and certificate renewal in the production deployment architecture, or document that an external load balancer owns TLS/HSTS.
- Use `npm ci` and immutable lockfile installs in Docker.
- Pin image digests and generate SBOMs.
- Store CI artifacts for audit: test reports, vulnerability reports, image scan results, build provenance.
- Add blue/green or rolling deployment scripts with rollback automation.
- Clean and consolidate docs into one authoritative operator guide.

## Documentation Readiness

Before score: **55/100**  
After score: **84/100**

Findings:
- There are many useful documents, including production setup, TLS/CSP, phase plans, release runbook, and prior audits.
- Root and backend README files contain encoding issues and stale references.
- Client README is still generic Lovable boilerplate.
- Multiple audit/planning docs overlap and may contradict current code.

Recommendations:
- Replace README files with current local development, test, and deployment instructions.
- Create one `docs/production-readiness.md` index that links authoritative docs only.
- Move historical audits into `docs/archive/`.
- Add API catalog, environment variable catalog, operational runbook, incident runbook, backup/restore drill, and tenant/RBAC model documentation.

## Phase-Wise Roadmap

### Phase 0: Release Freeze and Baseline

Target score: **68/100**

- Freeze feature work except production-readiness fixes.
- Record full CI status from a clean branch.
- Run full backend tests, full frontend tests, `pip-audit`, `npm audit`, Gitleaks, and Trivy.
- Decide which existing untracked/generated files belong in source control.
- Create a route/API inventory and mark each endpoint public/authenticated/admin/internal.

### Phase 1: Security Blockers

Target score: **76/100**

- Add CSRF protection for cookie-auth unsafe methods.
- Remove or deprecate duplicate `/api/token` and duplicate `/api/logout`.
- Fix AI assistant admin authorization misuse.
- Add step-up auth for dangerous admin actions.
- Add DOM sanitization for template/editor previews.
- Add public share revocation, TTL index, and rate limiting.

### Phase 2: Authorization Consolidation

Target score: **81/100**

- Standardize all backend routes on `PolicyService`.
- Replace scattered `authorize_scope`/legacy permission calls.
- Add route inventory tests requiring auth and policy declarations.
- Add tenant isolation tests for documents, contracts, letters, retrieval, reports, and search.
- Remove client reliance on localStorage for role/scope decisions.

### Phase 3: Database and Data Integrity

Target score: **84/100**

- Add versioned Mongo migrations.
- Add missing unique/TTL indexes.
- Add readiness checks for required indexes.
- Add backup/restore drill automation.
- Add data integrity reconciliation for documents, files, vectors, shares, and audit events.

### Phase 4: Frontend Hardening and UX Completeness

Target score: **86/100**

- Remove duplicate/dormant pages and old hooks.
- Centralize API calls through axios client.
- Fix lint hook warnings.
- Add loading, empty, error, and permission-denied states across all active routes.
- Add accessibility checks and responsive smoke tests for login, upload, documents, viewer, letters, contracts, settings, and admin pages.
- Split large chunks and lazy-load PDF/editor/chart-heavy code.

### Phase 5: DevOps, Observability, and Release Operations

Target score: **88/100**

- Use reproducible Docker builds and digest-pinned images.
- Add SBOMs and signed release artifacts.
- Make dependency vulnerability scans mandatory or risk-accepted.
- Add distributed tracing/error tracking.
- Define production SLOs for auth, upload, search, AI drafting, queues, and downloads.
- Implement staged deployments with rollback scripts and smoke tests.

## Final Scores

| Category | Before | After Roadmap |
|---|---:|---:|
| Frontend production readiness | 66 | 87 |
| Backend/API production readiness | 64 | 88 |
| Database/data integrity | 58 | 86 |
| Security | 58 | 89 |
| DevOps/deployment | 70 | 88 |
| Observability/operations | 68 | 88 |
| Test coverage/release confidence | 61 | 86 |
| Documentation | 55 | 84 |
| Overall | **63** | **88** |

## Go/No-Go Assessment

Current status: **No-Go for production handling real customer documents.**

The application is close enough that a focused hardening sprint can move it into staging readiness, but it should not be promoted to production until CSRF protection, authorization consolidation, public share governance, database migration/index controls, dependency audit closure, and full CI/test evidence are complete.

