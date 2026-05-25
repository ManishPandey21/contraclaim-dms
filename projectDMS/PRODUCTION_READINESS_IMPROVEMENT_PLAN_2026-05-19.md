# Contraclaim DMS Production Readiness Improvement Plan

Audit date: 2026-05-19  
Repository: `c:\SaaS\projectDMS`  
Baseline audit: `CONTRACLAIM_DMS_PRODUCTION_READINESS_AUDIT_2026-05-19.md`

## Executive Score Ledger

| Phase | Scope | Security | Frontend | Backend/API | Database | DevOps | Overall |
|---|---|---:|---:|---:|---:|---:|---:|
| Baseline | Full-repo audit before this improvement run | 58 | 66 | 64 | 58 | 70 | 63 |
| Phase 1 | Critical API/session hardening | 67 | 67 | 69 | 61 | 72 | 68 |
| Phase 2 | Frontend XSS/auth compatibility hardening | 71 | 70 | 69 | 61 | 72 | 71 |
| Phase 3 | Dependency vulnerability remediation | 73 | 72 | 69 | 61 | 76 | 74 |
| Phase 4 | Authorization/RBAC convergence slice | 80 | 72 | 78 | 62 | 77 | 79 |
| Phase 5 | Frontend auth/client cleanup slice | 83 | 77 | 79 | 62 | 77 | 81 |
| Phase 7 | Performance and UX production pass | 83 | 82 | 79 | 62 | 79 | 84 |
| Target after remaining roadmap | All recommended phases complete | 90 | 88 | 89 | 84 | 89 | 88 |

Current status after Phases 1-5 and 7: **not production-ready yet**, but materially safer and more production-shaped on the frontend. The remaining blockers are residual tenant-selection persistence in some secondary frontend tools, full browser E2E workflow coverage, migration discipline, database backup/restore readiness, full responsive/accessibility sweeps, virtualization for large data sets, and production runbooks.

## Completed Phase 1: Critical API/Session Hardening

Score after phase: **68/100**.

Implemented:

- Added signed double-submit CSRF protection for unsafe cookie-authenticated requests.
- Added `/api/csrf-token` bootstrap endpoint and frontend startup bootstrap for existing sessions.
- Added CSRF headers to axios and global `fetch` unsafe requests.
- Restricted AI assistant admin configuration/cache endpoints to `superadmin`.
- Added rate limiting to public shared-document download by IP and share token hash.
- Added MongoDB indexes for `document_share_tokens`.
- Changed frontend Docker build dependency install from `npm install` to `npm ci`.
- Added focused CSRF and AI authorization tests.

Verification:

- `python -m pytest backend\rbac_backend\tests\test_csrf.py backend\rbac_backend\tests\test_ai_assistant_security.py backend\rbac_backend\tests\test_config_validation.py backend\rbac_backend\tests\test_login.py -q` passed: 13 tests.
- `npm run build` passed.
- `npm run lint` passed with existing warnings only.

Residual gaps:

- CSRF now covers browser cookie sessions, but a full route inventory should confirm every legacy unsafe route follows the intended auth mode.
- Several frontend files still reference `localStorage.accessToken`; Phase 2 reduced the impact but did not remove all legacy call sites.

## Completed Phase 2: Frontend Security Hardening

Score after phase: **71/100**.

Implemented:

- Added `client/src/utils/sanitizeHtml.ts` allowlist sanitizer.
- Sanitized letter template preview HTML in:
  - `client/src/pages/LetterTemplatePage.tsx`
  - `client/src/pages/LetterTemplateEditorPage.tsx`
- Added sanitizer tests covering script/event handler/unsafe URL removal and safe formatting preservation.
- Updated the global `fetch` interceptor to remove invalid legacy headers such as `Authorization: Bearer null` so HttpOnly cookie auth can work cleanly.

Verification:

- `npx vitest run src/utils/sanitizeHtml.test.ts --config vitest.config.ts` passed: 2 tests.
- `npm run build` passed.
- `npm run lint` passed with existing warnings only.

Residual gaps:

- The sanitizer is intentionally conservative and local. A mature sanitizer package should be considered during a larger dependency pass if rich HTML requirements grow.
- Legacy localStorage token references remain across document viewer/search/upload workflows.

## Completed Phase 3: Dependency Vulnerability Remediation

Score after phase: **74/100**.

Implemented:

- Ran the forced npm audit remediation. The command exceeded the terminal timeout but completed the package update.
- Upgraded vulnerable frontend tooling chain:
  - `vite` to `6.4.2`
  - `@vitejs/plugin-react-swc` to `3.11.0`
  - `lovable-tagger` to `1.3.0`
  - `vitest` to `4.1.6`
  - `@vitest/coverage-v8` to `4.1.6`
- Confirmed `npm audit --audit-level=low` reports **0 vulnerabilities**.

Verification:

- `npm run build` passed on Vite 6.4.2.
- `npm run lint` passed with existing warnings only.
- `npx vitest run src/utils/sanitizeHtml.test.ts --config vitest.config.ts` passed.
- Focused backend pytest suite passed: 13 tests.
- `npm audit --audit-level=low` passed with 0 vulnerabilities.

Residual gaps:

- Major frontend libraries are still stale even though audit is clean.
- Bundle warnings remain: main bundle, dashboard, document viewer, and template editor still exceed healthy production targets.
- Browserslist data remains stale.

## Completed Phase 4: Authorization/RBAC Convergence Slice

Score after phase: **79/100**.

Implemented:

- Marked legacy `/api/token` and duplicate legacy `users.logout_user` `/api/logout` as deprecated, with `Deprecation`, `Sunset`, and successor `Link` headers.
- Added centralized `Permissions` constants in `backend/rbac_backend/core/permissions.py`.
- Updated `PolicyService`, document deletion/update policy checks, and monetization policy calls to use centralized constants.
- Added route inventory tests for mounted frontend route families, deprecated legacy auth endpoints, unsafe-route auth classification, and critical step-up coverage.
- Added negative authorization tests for document scope denial, storage admin rejection, missing step-up token denial, and public-share token rate limiting.
- Added step-up enforcement to user delete/lock/unlock, document delete, organization delete, project delete/deactivate, storage repair endpoints, storage settings updates, and SMTP create/update/test endpoints.
- Kept existing step-up enforcement for RBAC monetization, role, and permission mutations.

Verification:

- `python -m compileall -q backend\rbac_backend` passed.
- `python -m pytest backend\rbac_backend\tests\test_route_inventory.py backend\rbac_backend\tests\test_rbac_policy_hardening.py backend\rbac_backend\tests\test_csrf.py backend\rbac_backend\tests\test_ai_assistant_security.py backend\rbac_backend\tests\test_config_validation.py backend\rbac_backend\tests\test_login.py -q` passed: 36 tests.

Residual gaps:

- Some older routers still use legacy `auth_service.require_permission(...)` internally; route inventory now classifies them, but a full migration to `PolicyService.authorize(...)` remains for later phases.
- Legacy auth endpoints remain callable during the compatibility window and should be removed after 2026-08-19.
- Frontend needs to surface step-up token prompts for newly protected dangerous actions.

## Completed Phase 5: Frontend Auth and Workflow Cleanup

Score after phase: **81/100**.

Implemented:

- Added shared `authenticatedFetch(...)` in `client/src/services/http.ts` that strips legacy `Authorization` headers and sends cookie credentials.
- Routed document list/search/view/download/reference/enclosure calls, email/share calls, dashboard calls, health/storage admin calls, LangGraph/drafting hooks, and bulk-upload template download through shared cookie-auth clients.
- Removed active `localStorage.accessToken` reads from production source paths; remaining `accessToken` references are cleanup-only code, the header-stripping wrapper, or tests.
- Stopped `LoginPage` and `useRBAC` from writing auth, role, org, or project scope into localStorage; `/api/me` is now the source for active role loading.
- Updated sidebar identity/role loading to use profile and `/api/me` instead of local auth storage.
- Removed localStorage-derived role and tenant spoofing headers from letter workflow hooks and document helper paths.
- Added regression coverage proving `authenticatedFetch` strips stale bearer headers and includes cookies.

Verification:

- `npm run build` passed.
- `npm run lint` passed with 60 warnings and 0 errors.
- `npm test -- --run src/tests/base_url.spec.ts` passed: 9 tests.

Residual gaps:

- Some contract/search/settings pages still persist selected `org_id`/`proj_id` as UI context. These no longer inject bearer headers, but they should be renamed/migrated to harmless preference keys or replaced by server-backed user context.
- Full browser E2E coverage for login, upload, document view, share link, letter drafting, approval, and admin RBAC still needs a dedicated Playwright/Cypress harness with seeded backend data.
- React hook warnings dropped but remain in legacy workflow variants and several non-auth pages.

## Remaining Phase 6: Database and Migration Readiness

Target score after phase: **85/100**.

Recommended implementation:

- Convert opportunistic startup index creation into versioned, idempotent migration scripts.
- Define backup/restore drills with documented RPO/RTO.
- Add compound indexes for high-volume document, search, letter, notification, audit, and storage lookup paths.
- Add data retention and soft-delete policy for sensitive records.
- Add transaction/session handling for multi-document mutations where partial writes create user-visible inconsistency.

## Completed Phase 7: Performance and UX Production Pass

Score after phase: **84/100**.

Implemented:

- Split the PDF/document viewer path so `DocumentViewerPage` no longer eagerly includes the full PDF viewer stack.
- Added Vite manual chunks for PDF.js/viewer libraries, Tiptap editor libraries, Recharts, Radix UI, and React core libraries.
- Removed the static `LoginPage` import from `ProtectedRoute`; inline login fallback now lazy-loads behind `Suspense`.
- Replaced the document viewer's plain loading/error screens with route skeletons, user-facing failure messaging, and retry navigation controls.
- Added retryable error handling to document search loading failures.
- Added skeleton, error, and retry states to the document share page.
- Removed type-only document viewer imports from runtime bundles where they were pulling page modules into viewer components.
- Eliminated the React hook dependency warning in `DocumentsSearchPage`.

Verification:

- `npm run build` passed.
- `npm run lint` passed with 59 warnings and 0 errors.
- `npm test -- --run src/tests/base_url.spec.ts` passed: 9 tests.

Residual gaps:

- The PDF vendor chunk still exceeds the 500 kB production warning threshold because PDF.js and the viewer package are intrinsically heavy; all route/app chunks that previously exceeded the threshold are now under 500 kB.
- Full route-by-route responsive and accessibility sweeps still need browser automation and manual QA.
- Large result sets still need virtualization or server-side pagination hardening in document, notification, user, and search-heavy views.
- Several legacy React hook and Fast Refresh warnings remain outside this focused Phase 7 slice.

## Remaining Phase 8: Operations, Monitoring, and Release Discipline

Target score after phase: **88/100**.

Recommended implementation:

- Finalize production runbooks for deploy, rollback, backup, restore, incident response, and secret rotation.
- Add CI gates for backend tests, frontend build/lint/test, audit, container build, smoke health, and migration dry-run.
- Add structured metrics and alerting for auth failures, 403 spikes, public share throttling, background jobs, storage errors, queue depth, and slow requests.
- Keep `npm audit`, backend dependency scanning, and container scanning as release blockers.

## Current Verification Summary

Latest verified commands:

- Backend focused tests: passed, 36 tests.
- Frontend build: passed.
- Frontend lint: passed with 59 existing warnings and 0 errors.
- Frontend sanitizer tests: passed, 2 tests.
- Frontend auth/client regression tests: passed, 9 tests.
- Frontend dependency audit: passed, 0 vulnerabilities.

Known remaining warnings:

- 59 ESLint warnings, mostly React hook dependency and Fast Refresh export warnings.
- One remaining Vite build chunk warning for the PDF vendor chunk. Prior warnings for `index`, `DocumentViewerPage`, `Dashboard`, and `LetterTemplateEditorPage` are resolved.
- Browserslist/caniuse-lite data is stale.

## Final Scores

Overall score before this implementation run: **63/100**.  
Overall score after Phases 1-5 and 7: **84/100**.  
Estimated score after completing all remaining recommendations: **88/100**.
