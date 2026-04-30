# ProjectDMS (ContraClaim) — Improvement Sprint Plan

## Sprint Overview

| Sprint | Theme | Duration | Risk Level | Dependencies |
|--------|-------|----------|------------|--------------|
| **Sprint 0** | Quick Wins | Balance | Low | None |
| **Sprint 1** | Security & Auth Hardening | Balance | Critical | None |
| **Sprint 2** | Data Integrity & Backend Stability | Balance | High | Sprint 1 |
| **Sprint 3** | Frontend Consolidation & UX | 1 week | Medium | None |
| **Sprint 4** | AI & Workflow Engine | 1 week | Medium | Sprint 2 |
| **Sprint 5** | Infrastructure & DevOps | 1 week | Medium | Sprint 1 |
| **Sprint 6** | New Features — Phase 1 | 2 weeks | Low | Sprint 2, 3 |
| **Sprint 7** | New Features — Phase 2 | 2 weeks | Low | Sprint 4, 5 |
| **Sprint 8** | Testing, Polish & Release Prep | 1 week | Low | All |

---

## Sprint 0 — Quick Wins [COMPLETED]

All items implemented and verified.

| # | Fix | File(s) | Status |
|---|-----|---------|--------|
| 1 | Remove dev auth headers from frontend | `api.ts`, `enhanced-api.ts`, 6 more files | Balance |
| 2 | Raise 401 on JWTError (no fallback) | `security.py` | Balance |
| 3 | Remove dev header fallback from backend | `security.py` | Balance |
| 4 | Fix CORS — use `settings.CORS_ORIGINS` | `main.py` | Balance |
| 5 | Add security headers middleware | `main.py` | Balance |
| 6 | Enable rate limit enforcement (429) | `rate_limiter.py` | Balance |
| 7 | Fix `has_permission()` role query | `security.py` | Balance |
| 8 | Replace `print()` with `logger.debug()` | `security.py` | Balance |
| 9 | Fix RBACService malformed MongoDB queries | `rbac_service.py` | Balance |
| 10 | Fix tasks.py scope validation (OR→AND) | `tasks.py` | Balance |
| 11 | Fix requirements.txt version pins | `requirements.txt` | Balance |
| 12 | Add missing DB indexes (email, status, etc.) | `database.py` | Balance |
| 13 | Delete dead code (5 old files, 3 dup hooks) | Multiple | Balance |
| 14 | Fix orguser privilege escalation | `security.py` | Balance |
| 15 | Implement party/project access checks | `authorization_service.py` | Balance |
| 16 | Migrate sessions to Redis | `authentication_service.py` | Balance |
| 17 | Remove hardcoded login credentials | `LoginPage.tsx` | Balance |
| 18 | Create `.gitignore` | Root | Balance |

---

## Sprint 1 — Security & Auth Hardening [COMPLETED]

**Goal:** Eliminate all remaining auth/security vulnerabilities before any production exposure.
**Impact:** Blocks all other sprints for security-sensitive features.

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 1.1 | **Rotate all exposed credentials** | `.env` files | MANUAL — see credential rotation runbook below |
| 1.2 | **Persist account lockout in Redis** | `authentication_service.py` | Balance — Redis-backed with in-memory fallback; fixed `user_id` → `identifier` bug |
| 1.3 | **Add CSRF protection middleware** | `main.py` | Balance — `CSRFHeaderMiddleware` requires `X-Requested-With` or `Authorization` |
| 1.4 | **Move tokens from localStorage to httpOnly cookies** | `api.ts`, `auth.ts`, `auth.py`, `security.py` | Balance — httpOnly cookie set on login/refresh, cleared on logout; backend reads cookie first |
| 1.5 | **Add request ID middleware** | `main.py` | Balance — `RequestIDMiddleware` attaches `X-Request-ID` |
| 1.6 | **Audit all routers for `authorize_scope()` usage** | `projects.py`, `permissions.py`, `roles.py`, `authorization_service.py` | Balance — added `build_project_query()`, scoped GET /projects, restricted permissions/roles reads to admin |
| 1.7 | **Implement letter authorization checks** | `authorization_service.py`, `letters.py` | Balance — `check_letter_access()`, `check_letter_creation_permission()` |
| 1.8 | **Add `build_letter_query()` user scoping** | `authorization_service.py` | Balance — org/project scoped letter queries |
| 1.9 | **WebSocket auth verification** | `routers/ws.py` | Balance — JWT validated, connection closed with 4401 on failure |

### Credential Rotation Runbook (Task 1.1)

**Pre-production requirement** — rotate all secrets before any deployment:

1. **JWT SECRET_KEY** — Generate a new 64+ character random string (`openssl rand -hex 64`). Update in `.env` and all deployment configs. All existing sessions will be invalidated.
2. **AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY** — Rotate in AWS IAM console. Create a new key pair, update `.env`, then deactivate the old key.
3. **OPENAI_API_KEY** — Rotate in OpenAI dashboard (Settings → API Keys). Revoke old key after updating `.env`.
4. **SMTP_USERNAME / SMTP_PASSWORD** — Generate a new app password (Gmail: Security → App passwords). Update `.env`.
5. **QDRANT_API_KEY** (if used) — Rotate in Qdrant Cloud console.
6. **DATABASE_URL** — If MongoDB credentials were exposed, reset the password via `db.changeUserPassword()`.

**Verification:** After rotation, run `ALLOW_PLACEHOLDER_SECRETS=0 python -c "from rbac_backend.core.config import settings; print(settings.validate_no_placeholders())"` — should return an empty set.

**Dependencies:** None
**Validation:** Auth bypass integration test, penetration test of JWT + scope enforcement

---

## Sprint 2 — Data Integrity & Backend Stability

**Goal:** Fix data consistency issues, add transaction support, and stabilize core services.
**Impact:** Required before adding new features; prevents data corruption.

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 2.1 | **Add MongoDB transaction support** for composite operations | `database.py`, `user_service.py` | Balance — `transaction()` context manager with graceful degradation for standalone MongoDB |
| 2.2 | **Fix S3Service stub** — implement real S3 upload/download | `s3_service.py` | Balance — boto3 client with adaptive retry; local filesystem fallback for dev |
| 2.3 | **Add referential integrity checks** on delete | `project_service.py`, `organization_service.py` | Balance — `check_project_dependencies()` / `check_organization_dependencies()` block delete when refs exist; `force` flag to override |
| 2.4 | **Fix email duplicate race condition** | `user_service.py` | Balance — atomic transaction-based duplicate check + E11000 fallback for non-txn mode |
| 2.5 | **Replace in-memory cache with Redis** | `cache_service.py` | Balance — `HybridCache` (Redis primary + in-memory fallback); lazy connection; SCAN-based pattern delete |
| 2.6 | **Persist background job queue** | `background_jobs.py` | Balance — MongoDB `background_jobs` collection; `_persist_job()` upserts; `_recover_pending_jobs()` on startup |
| 2.7 | **Add job priority support** | `background_jobs.py` | Balance — `asyncio.PriorityQueue` with negated-priority `__lt__` ordering |
| 2.8 | **Implement bulk upload partial failure recovery** | `bulk_upload_service.py` | Balance — `retry_failed_uploads()` creates child jobs for failed documents only |
| 2.9 | **Fix timezone handling** — use timezone-aware UTC | 48 source files | Balance — all `datetime.utcnow()` → `datetime.now(timezone.utc)` across entire codebase |
| 2.10 | **Add retry logic to external API calls** | `retry.py`, `openai_service.py`, `email_service.py` | Balance — `async_retry()` + `@with_retry` decorator; applied to OpenAI and SMTP calls |

**Dependencies:** Sprint 1 (auth must be solid before data layer changes)
**Validation:** Transaction rollback tests, S3 integration test, bulk upload failure test

---

## Sprint 3 — Frontend Consolidation & UX

**Goal:** Reduce code duplication, standardize patterns, improve user experience.
**Impact:** Independent of backend sprints; can run in parallel with Sprint 2.

| # | Task | File(s) | Effort | Impact |
|---|------|---------|--------|--------|
| 3.1 | **Consolidate API patterns** — remove direct `fetch()`, use `api.ts` everywhere | `LoginPage.tsx`, `TagsPage.tsx`, `auth.ts` | M | HIGH — inconsistent error handling, no token refresh |
| 3.2 | **Split `enhanced-api.ts` (41KB)** into focused service modules | `enhanced-api.ts` → 5 files | L | HIGH — maintainability |
| 3.3 | **Split `DocumentsPage.tsx` (67KB)** into sub-components | `DocumentsPage.tsx` → 5-6 components | L | HIGH — God component |
| 3.4 | **Split `ContractsSearchPage.tsx` (47KB)** | `ContractsSearchPage.tsx` → 4 components | L | MEDIUM |
| 3.5 | **Split `useLetterWorkflow.ts` (19KB)** into smaller hooks | `useLetterWorkflow.ts` → 3-4 hooks | M | MEDIUM — God hook |
| 3.6 | **Add pagination** to Organizations, Projects, Users pages | 3 pages | M | HIGH — will break with 100+ items |
| 3.7 | **Add empty states** to all list pages | ~10 pages | S | MEDIUM — blank screens confuse users |
| 3.8 | **Replace `window.confirm()` token refresh** with silent refresh | `auth.ts` | S | MEDIUM — blocks UI thread |
| 3.9 | **Add debounce to search inputs** | `EnhancedDocumentsPage`, `ContractsSearchPage` | S | MEDIUM — fires on every keystroke |
| 3.10 | **Add unsaved changes indicators** to edit forms | Document viewer, metadata editor | S | LOW |
| 3.11 | **Standardize error handling** — create ErrorLogger utility | New file + all pages | M | MEDIUM |
| 3.12 | **Implement forgot password flow** | `LoginPage.tsx` + new backend endpoint | M | MEDIUM — link exists but non-functional |

**Dependencies:** None (can parallel with Sprint 2)
**Validation:** Visual regression, all routes render, build succeeds

---

## Sprint 4 — AI & Workflow Engine

**Goal:** Replace mocks and stubs with real AI integrations; add workflow state machine.
**Impact:** Depends on stable data layer (Sprint 2).

| # | Task | File(s) | Effort | Impact |
|---|------|---------|--------|--------|
| 4.1 | **Replace AIService text similarity with vector search** | `ai_service.py` | L | HIGH — core AI feature uses `SequenceMatcher` instead of embeddings |
| 4.2 | **Fix LangGraph workflow** — remove hardcoded dummy data | `ai_workflows/langgraph.py` | M | HIGH — `background_summary` is hardcoded |
| 4.3 | **Replace AI mock delays** with real API calls | `AIAssistant.tsx` | M | MEDIUM — `setTimeout(resolve, 2000)` stubs |
| 4.4 | **Add letter status state machine** | `letter_service.py`, new `state_machine.py` | M | HIGH — no enforcement of valid transitions |
| 4.5 | **Add workflow transition audit logging** | `workflow.py`, `letter_service.py` | S | MEDIUM — no history of state changes |
| 4.6 | **Implement at least email notifications** | `notifications.py` | M | MEDIUM — 5 channels are TODO stubs |
| 4.7 | **Add AI request rate limiting and cost tracking** | `ai_service.py`, `config.py` | S | MEDIUM — no limits on OpenAI calls |
| 4.8 | **Add AI confidence scores to responses** | `ai_models.py`, `ai_service.py` | S | LOW |
| 4.9 | **Add graph run error recovery and timeout** | `langgraph.py` | M | MEDIUM — single failure kills workflow |

**Dependencies:** Sprint 2 (stable data layer + real S3)
**Validation:** AI draft generation test, workflow transition test, notification delivery test

---

## Sprint 5 — Infrastructure & DevOps

**Goal:** Production-ready deployment, monitoring, and reliability.
**Impact:** Required for production release; depends on auth fixes (Sprint 1).

| # | Task | File(s) | Effort | Impact |
|---|------|---------|--------|--------|
| 5.1 | **Add MongoDB to docker-compose** | `docker-compose.yml` | S | HIGH — missing from compose |
| 5.2 | **Add health checks** to all docker services | `docker-compose.yml` | S | HIGH — no restart on failure |
| 5.3 | **Add restart policies and resource limits** | `docker-compose.yml` | S | MEDIUM |
| 5.4 | **Pin FalkorDB to stable version** (not `edge`) | `docker-compose.yml` | S | MEDIUM — edge tag is unstable |
| 5.5 | **Create environment-specific configs** | `.env.development`, `.env.production`, `.env.staging` | M | HIGH — single .env for all envs |
| 5.6 | **Add secrets management** (env validation on startup) | `config.py` | M | HIGH — placeholder values silently used |
| 5.7 | **Add distributed rate limiting** via Redis sorted sets | `rate_limiter.py` | M | MEDIUM — in-memory only |
| 5.8 | **Add structured logging** (JSON format for production) | `main.py`, all services | M | MEDIUM |
| 5.9 | **Add APM / request tracing** integration | `main.py` middleware | M | LOW |
| 5.10 | **Configure MongoDB connection pooling** | `database.py` | S | MEDIUM |
| 5.11 | **Add Dockerfile and nginx config** for frontend | New files | M | HIGH — no frontend deployment config |

**Dependencies:** Sprint 1 (auth hardened first)
**Validation:** `docker-compose up` succeeds, health checks pass, logs in JSON format

---

## Sprint 6 — New Features Phase 1

**Goal:** High-value user-facing features that build on stabilized codebase.
**Impact:** Requires stable backend (Sprint 2) and clean frontend (Sprint 3).

| # | Feature | Effort | Impact | Description |
|---|---------|--------|--------|-------------|
| 6.1 | **Document version history** | L | HIGH | Track versions, diff view, rollback |
| 6.2 | **Auto-save for forms** | M | HIGH | Draft saving for letter composition, metadata editing |
| 6.3 | **Bulk operations on documents** | M | MEDIUM | Multi-select delete, tag, move, share |
| 6.4 | **Dark mode** | M | LOW | CSS variables, theme context, toggle |
| 6.5 | **Report scheduling** | M | MEDIUM | Recurring reports, email delivery |
| 6.6 | **Real-time notifications** (WebSocket) | M | HIGH | Live updates for workflow events |
| 6.7 | **Dynamic breadcrumbs** | S | LOW | Context-aware navigation breadcrumbs |

**Dependencies:** Sprint 2, 3
**Validation:** Feature acceptance tests, UX review

---

## Sprint 7 — New Features Phase 2

**Goal:** Advanced features that leverage the AI pipeline and graph database.
**Impact:** Requires working AI (Sprint 4) and infra (Sprint 5).

| # | Feature | Effort | Impact | Description |
|---|---------|--------|--------|-------------|
| 7.1 | **Semantic document search** | L | HIGH | Vector-based similarity search across all documents |
| 7.2 | **Contract lifecycle management** | L | HIGH | Status tracking (draft→signed→executed→expired), amendments |
| 7.3 | **AI-powered clause comparison** | L | MEDIUM | Compare clauses across contracts, flag deviations |
| 7.4 | **Knowledge graph visualization** | M | MEDIUM | Interactive graph UI for FalkorDB relationships |
| 7.5 | **Audit log dashboard** | M | HIGH | Who did what when — admin visibility |
| 7.6 | **Email template system** | M | MEDIUM | Customizable notification templates |
| 7.7 | **Multi-language support (i18n)** | L | LOW | Framework for internationalization |
| 7.8 | **Mobile-responsive overhaul** | M | MEDIUM | Optimize all pages for mobile viewports |

**Dependencies:** Sprint 4, 5
**Validation:** E2E tests, performance benchmarks

---

## Sprint 8 — Testing, Polish & Release Prep

**Goal:** Comprehensive test coverage, final polish, production deployment checklist.

| # | Task | Effort | Impact |
|---|------|--------|--------|
| 8.1 | **Auth bypass integration tests** | M | CRITICAL |
| 8.2 | **RBAC cross-tenant tests** | M | CRITICAL |
| 8.3 | **Rate limiting tests** | S | HIGH |
| 8.4 | **API endpoint documentation** (OpenAPI polish) | M | MEDIUM |
| 8.5 | **Frontend unit tests for hooks** | M | MEDIUM |
| 8.6 | **Load testing** (k6 or similar) | M | MEDIUM |
| 8.7 | **Accessibility audit** (ARIA, keyboard nav, contrast) | M | MEDIUM |
| 8.8 | **Production deployment runbook** | S | HIGH |
| 8.9 | **Database backup and restore procedure** | S | HIGH |
| 8.10 | **Performance profiling** (slow queries, large payloads) | M | MEDIUM |

**Dependencies:** All prior sprints
**Validation:** All tests green, deployment to staging succeeds, security scan passes

---

## Dependency Graph

```
Sprint 0 (Balance)
    ├── Sprint 1 (Security)
    │   ├── Sprint 2 (Data Integrity)
    │   │   ├── Sprint 4 (AI & Workflow)
    │   │   │   └── Sprint 7 (New Features P2)
    │   │   └── Sprint 6 (New Features P1)
    │   └── Sprint 5 (Infrastructure)
    │       └── Sprint 7 (New Features P2)
    └── Sprint 3 (Frontend) ← can parallel with Sprint 1 & 2
        └── Sprint 6 (New Features P1)

Sprint 8 (Testing) ← depends on all
```

## Effort Key
- **S** = Small (< 2 hours)
- **M** = Medium (2-8 hours)
- **L** = Large (1-3 days)

## Priority Summary

| Priority | Sprint | Items | Theme |
|----------|--------|-------|-------|
| P0 (Balance) | Sprint 0 | 18 | Quick Wins |
| P1 (Next) | Sprint 1 | 9 | Security |
| P1 (Parallel) | Sprint 3 | 12 | Frontend |
| P2 | Sprint 2 | 10 | Data Integrity |
| P2 | Sprint 5 | 11 | Infrastructure |
| P3 | Sprint 4 | 9 | AI & Workflow |
| P4 | Sprint 6 | 8 | New Features P1 |
| P4 | Sprint 7 | 8 | New Features P2 |
| P5 | Sprint 8 | 10 | Testing & Release |
