# ContractDMS Full‑Stack Audit and Recommendations

Date: 2025-08-28

This document summarizes an audit of the ContractDMS codebase (backend and frontend), identifies issues/opportunities, proposes features, and provides a prioritized, PR-sized implementation plan with testing and CI/CD guidance.

---

## Overview &amp; Repo Map

### Stack and Architecture

- Frontend: React 18 + Vite 5 + TypeScript, shadcn/ui (Radix), React Router, TanStack Query, axios, vitest/testing-library.
- Backend: FastAPI, Python 3.x, Motor (AsyncIOMotorClient) for MongoDB, pydantic v2, passlib/bcrypt, python-jose (JWT), boto3 (S3), OCRmyPDF, OpenAI (AI assistant).
- Data: MongoDB “contraclaim” database via Motor. No explicit index creation logic found.
- Auth: JWT Bearer tokens; dev-mode header fallbacks (X-User-Id, X-User-Role(s), X-Org-Id, X-Proj-Id).
- AI: OpenAI powered endpoints for letters assistance, RAG upload/list/remove, draft generation, etc.
- Files/Storage: Local uploads/ plus S3 dependencies. Mixed usage risks without a unified abstraction.
- Tests: Pytest for backend; Vitest and jsdom for frontend.

High-level flow:

- React app calls API via axios (base URL resolved by Vite env/import.meta.env, runtime override, host-derived mapping, fallback “/api”).
- FastAPI app mounted in backend/rbac_backend/main.py under “/api” prefix; multiple routers for auth/users/roles/permissions/organizations/projects/parties/representatives/tags/concerns/letters/documents/folder_structure/input_requests/email/tasks/contracts/ai_assistant.
- MongoDB used for persistence, seed data on startup (orgs, projects, users).

### Repo Map (key areas)

- backend/rbac_backend/main.py: FastAPI app, CORS, router wiring, startup seeding.
- backend/rbac_backend/core/
  - config.py: Settings (currently includes hardcoded secrets; critical issue).
  - database.py: Motor connection lifecycle; exposes AsyncIOMotorDatabase via get_db.
  - security.py: CryptContext, OAuth2PasswordBearer, JWT utilities, permission checks, current user resolution (includes dev header fallback).
- backend/rbac_backend/models/: Pydantic models for Domain entities.
  - user.py, role.py, permission.py, organization.py, project.py, party.py, representative.py, tag.py, letter.py, document.py (+ variants), folder.py, concern.py, input_request.py, task.py, common.py.
- backend/rbac_backend/routers/: FastAPI routers.
  - auth.py, users.py, roles.py, permissions.py, organizations.py, projects.py, parties.py, representatives.py, tags.py, concerns.py, letters.py, documents.py (+ documents1.py), folder_structure.py, input_requests.py, email.py, tasks.py, contracts.py, ai_assistant.py (+ multiple copies/variants).
  - “New folder/” subtrees with duplicate/outdated router implementations.
- backend/rbac_backend/services/: Business logic modules (auth_service, document_service, letter_service, metadata, notifications, contracts_ingest, workflow, etc.).
- backend/rbac_backend/tests/: pytest suites: test_documents_listing.py, test_document_linking.py, test_letters_critical.py, test_similar_letter_validator.py, test_users.py, plus manual_share_test.py.
- client/
  - package.json: scripts (dev/build/test), dependencies/devDependencies; proxy set to https://app.contraclaim.com.
  - src/config/api.ts: API base URL resolution logic (VITE_API_BASE_URL, window.**API_BASE_URL**, host-derived, fallback /api).
  - src/services/api.ts: axios client with interceptors: ensures token validity, attaches Authorization and legacy headers, 401 auto-refresh via /api/refresh.
  - src/pages/: LoginPage, UsersPage, OrganizationsPage, PermissionsPage, DocumentsPage, Contracts\*, TagsPage, PartiesInvolvedPage, etc.
  - src/components/document-viewer/_, letter-workflow/_.
  - src/tests/setup.ts and vitest config files.

---

## Findings

Severity levels: Critical / High / Medium / Low

### Critical

- Hardcoded secrets in backend/rbac_backend/core/config.py
  - AWS_ACCESS_KEY_ID/SECRET, SMTP username/password, OpenAI API key, Assistant IDs are committed to source.
  - Action: Immediate rotation and removal from source; load via environment variables only.
- Multiple duplicate/outdated routers checked into repo
  - ai_assistant - 28082025.py, ai_assistant1.py, ai_assistant_copy.py, ai_assistant_old.py and “routers/New folder/\*” trees.
  - Risk of code drift, confusion, accidental import/exposure, and security issues.
- Unconditional dev header auth fallback (X-User-Id, etc.)
  - In production this can be dangerous if any intermediary injects headers. Must be disabled by default via env flag.

### High

- No structured logging or observability
  - Use of print statements; no request/response logging, correlation IDs, metrics/tracing. Hard to operate and investigate.
- No rate limiting or abuse protection
  - Sensitive endpoints (/token, file uploads, AI) are not protected.
- MongoDB indexes not explicitly created
  - Scalability/performance risk for users.email (unique), documents/search fields, roles/permissions, tags, etc.
- Inconsistent models and duplicate modules
  - documents.py vs document.py vs documents1.py and “New folder/\*” copies; unclear canonical data contract.
- Storage ambiguity
  - Local uploads and S3 dependencies exist without a unified abstraction; can cause inconsistent handling and security gaps.
- SMTP over raw creds (Gmail)
  - Secret exposure and reliability risks; provider APIs with key management recommended.
- AI endpoints lack guardrails
  - No explicit prompt/response redaction, quotas, cost controls, abuse prevention; potential for PII leakage.

### Medium

- OAuth2 tokenUrl path mismatch risk
  - OAuth2PasswordBearer(tokenUrl="/token") may be confusing when API routes are mounted at prefix “/api”; OpenAPI auth flow benefits from “/api/token”.
- JWT refresh model
  - Access token refresh re-issues tokens from existing identity without a distinct refresh token store/rotation/revocation capability.
- Backend requirements file encoding anomaly
  - backend/rbac_backend/requirements.txt appears with null/UTF-16-like characters. This is a DX issue and can break automated installs.
- Error handling and API envelopes
  - Some endpoints return bare dicts; error models not standardized for consistent API clients.
- Unused/leftover dependencies
  - beanie and other libs are present but not evidently used; increase surface and complexity.

### Low

- DX polish gaps
  - No enforced linters/formatters for backend (ruff/black/isort), no mypy; pre-commit hooks not configured.
- CORS: allowlist looks reasonable; ensure dev docs are clear.
- Swagger/OpenAPI polish
  - Security schemes, example requests, and consistent tags can be improved.

---

## Feature Ideas

### Now

- Secrets &amp; Config Hardening
  - Move all secrets to environment variables; add .env.example; implement secret scanning in CI.
- Role/Permission Admin UX
  - Role templates, permission grouping, search/filtering, export/import of RBAC configurations.
- Unified Storage Abstraction
  - StorageService interface with LocalFileStore and S3FileStore; presigned URLs for download; strict path policy.
- Document/Letter Linking Visualization
  - Graph view/backlinks browser; improve user navigation and related-context discovery.
- AI Assistant Guardrails
  - PII redaction, token/cost budgets, per-user/org quotas, feature-flag to disable in sensitive envs.

### Next

- Background Jobs
  - Queue (RQ/Celery/Arq) for OCR, vectorization, large uploads, email; progress status endpoints.
- Notification Center
  - In-app notifications, email templates, digests; audit logs for shares.
- Search Enhancements
  - Mongo text index + filters; optional vector search hybrid with embeddings; scoped by org/project/tags/date.
- Audit Logging
  - Security events (RBAC changes, logins), access trails, immutable store (e.g., WORM-bucket or append-only collection).

### Later

- Enterprise SSO and SCIM
  - OIDC/SAML integration, user provisioning.
- Multi-tenancy enforcement
  - Tenant discriminator + query filters or DB-per-tenant; strict isolation guarantees.
- Analytics Dashboards
  - Throughput, SLA adherence, pendency trends, workload heatmaps.

---

## Implementation Plan (PR-sized tasks)

Use small, focused PRs with clear acceptance criteria. The order below reflects risk reduction and platform hardening first.

1. Repo Hygiene and Protections [S]

- Rationale: Prevent further leakage, improve collaboration.
- Steps:
  - Add CODEOWNERS.
  - Ensure .gitignore excludes secrets/artifacts.
  - Document branch protection rules (require status checks).
- Risks: None.
- Dependencies: None.
- Acceptance:
  - CODEOWNERS committed.
  - Branch protection doc added to repo.
  - .gitignore updated.

2. Secrets Removal and Rotation [S–M]

- Rationale: Hardcoded secrets are a critical risk.
- Steps:
  - Replace literals in core/config.py with env lookups only.
  - Create .env.example (no secrets).
  - Rotate AWS, SMTP, OpenAI credentials externally.
  - Purge history (git filter-repo or BFG) to remove exposed secrets if repo is public/shared; coordinate with platform.
- Risks: Misconfigured env could cause downtime.
- Dependencies: Ops ownership of secrets.
- Acceptance:
  - App boots with only env-driven settings.
  - Scans show no secrets in repo.
  - New keys in use.

3. Settings Refactor &amp; OAuth2 Paths [S]

- Rationale: Clarity and correctness for OpenAPI and deployments.
- Steps:
  - Strongly type Settings; split per env overrides (dev/test/prod).
  - Set OAuth2PasswordBearer(tokenUrl="/api/token") or build using api_prefix var to reflect real route.
  - Add SECURITY_ENV flags (DEV/TEST/PROD) and SECURITY_ALLOW_DEV_HEADERS default false.
- Risks: Minimal.
- Dependencies: OpenAPI consumers.
- Acceptance:
  - OpenAPI auth flow works from docs.
  - Dev header fallback gated by env.

4. Disable Dev Header Auth in PROD [S]

- Rationale: Prevent header spoofing.
- Steps:
  - In get_current_user, read SECURITY_ALLOW_DEV_HEADERS; if false, skip header fallback entirely.
  - Add logging of auth mode.
- Risks: Environments relying on legacy headers break; document dev usage.
- Dependencies: FE config for Bearer tokens.
- Acceptance:
  - In prod, only Bearer tokens accepted.

5. Structured Logging &amp; Request IDs [S]

- Rationale: Operability and traceability.
- Steps:
  - Add middleware to generate request_id (uuid4) for each request.
  - Use standard logging with JSON formatter in PROD; include ts, level, route, user, request_id.
  - Replace print with logger.
- Risks: Log volume; use INFO by default.
- Dependencies: Uvicorn logging config.
- Acceptance:
  - Logs contain request_id and useful context.

6. Basic Rate Limiting [S]

- Rationale: Mitigate brute force/abuse.
- Steps:
  - Introduce slowapi or equivalent; protect /token, uploads, AI endpoints; configurable limits.
- Risks: False positives; tune limits.
- Dependencies: Caching backend (in-memory to start).
- Acceptance:
  - 429 on exceeding limits; configurable.

7. MongoDB Indexes [S]

- Rationale: Performance and correctness.
- Steps:
  - Add startup index creation: users.email (unique), roles.\_id, permissions.\_id, documents searchable fields (title, dates), letters, tags, references arrays as relevant.
  - Document index plan.
- Risks: Background index build times; coordinate on prod.
- Dependencies: DB access.
- Acceptance:
  - db.list_indexes shows expected indexes.
  - Query p95 improves on seeded data.

8. Router Consolidation [M]

- Rationale: Reduce drift/attack surface.
- Steps:
  - Remove “routers/New folder/_” and duplicate ai_assistant_ copies; keep canonical files only.
  - Add route snapshot test to ensure no regressions.
  - Consider temporary aliases with deprecation warnings if FE depends on variant paths.
- Risks: Breaking FE expectations; add compatibility layer if required.
- Dependencies: FE routes.
- Acceptance:
  - Single version per router.
  - Tests green.

9. Storage Abstraction [M]

- Rationale: Consistent, secure file handling.
- Steps:
  - Create StorageService interface; implement Local and S3 backends; configure via STORAGE_BACKEND and bucket/region env.
  - Use presigned URLs for downloads (S3).
  - Migrate direct file IO to service.
- Risks: Migration complexity.
- Dependencies: boto3 creds; permissions.
- Acceptance:
  - Upload/download work identically across backends.

10. Email Service Hardening [S]

- Rationale: Reliability and security.
- Steps:
  - Move SMTP secrets to env; consider provider (SES/SendGrid) with API keys.
  - Add retry/backoff; templates; from-address policy; DKIM/SPF docs.
- Risks: Provider integration churn.
- Dependencies: Provider account.
- Acceptance:
  - Emails sent via provider; secrets externalized.

11. AI Assistant Guardrails [M]

- Rationale: Safety, cost, and compliance.
- Steps:
  - Add PII redaction hooks, token/cost budget per request and per user/org, rate limit, structured logging of metadata (no content).
  - Feature flag to disable AI in certain envs.
- Risks: User expectations; costs.
- Dependencies: OpenAI keys.
- Acceptance:
  - Guardrails enforced; logs redact PII.

12. Auth Refresh Model Upgrade [M]

- Rationale: Security best practices.
- Steps:
  - Introduce httpOnly secure refresh cookies, rotation, and revocation store; keep current flow behind feature flag for compatibility.
  - Update FE auth client accordingly.
- Risks: Backward compatibility.
- Dependencies: FE changes; storage (Redis/Mongo).
- Acceptance:
  - Short-lived access; rotated refresh; revocation works.

13. API Contracts &amp; Error Envelopes [S]

- Rationale: FE stability and DX.
- Steps:
  - Standardize success/error models; unify pagination.
  - Replace raw dict returns; add OpenAPI examples.
- Risks: Minor FE adjustments.
- Dependencies: Routers and models.
- Acceptance:
  - OpenAPI validates with consistent schemas.

14. Tests Expansion [M]

- Rationale: Confidence and velocity.
- Steps:
  - Backend unit tests for services/validators, integration tests with httpx + pytest-asyncio and a test DB.
  - FE unit/render tests for pages/hooks; expand vitest coverage.
  - e2e with Playwright for core flows (login, CRUD, upload/view, linking, RBAC).
- Risks: Time investment.
- Dependencies: CI resources.
- Acceptance:
  - Coverage thresholds: backend ≥70%, frontend ≥60% initial.

15. Observability (Optional) [M]

- Rationale: Deep insights.
- Steps:
  - Add OpenTelemetry tracing/metrics or Prometheus exporter; dashboards.
- Risks: Complexity.
- Dependencies: Collector/Prometheus.
- Acceptance:
  - Traces visible locally; basic metrics scraped.

16. CI/CD Pipeline [M]

- Rationale: Quality and repeatability.
- Steps:
  - GitHub Actions: lint/typecheck/test for BE/FE; Docker multi-stage builds; image scan (Trivy); Dependabot.
  - Deployment job placeholders (container registry push; environment rollout).
- Risks: Build time.
- Dependencies: Registry/infra.
- Acceptance:
  - Passing pipeline; artifacts produced.

17. Docs &amp; Runbooks [S]

- Rationale: Team onboarding and ops.
- Steps:
  - Update README; env var docs; local dev scripts; Postman collection refresh; runbooks (rotate keys, restore DB, index management).
- Risks: None.
- Dependencies: Prior tasks.
- Acceptance:
  - Teammate can bootstrap locally < 15 minutes following docs.

Checklist (summary)

- [ ] Remove secrets; rotate keys.
- [ ] Gate dev header fallback behind env.
- [ ] Fix OAuth2 tokenUrl path and settings scheme.
- [ ] Add structured logging + request IDs.
- [ ] Introduce rate limiting.
- [ ] Create MongoDB indexes.
- [ ] Remove duplicate routers and “New folder/\*”.
- [ ] Implement StorageService (Local/S3) + presigned URLs.
- [ ] Harden Email service; consider provider API.
- [ ] Add AI guardrails (PII/cost/rate).
- [ ] Upgrade auth refresh model (rotating refresh).
- [ ] Standardize API models/errors.
- [ ] Expand test coverage (unit/integration/e2e).
- [ ] Add observability (optional).
- [ ] Set up CI/CD pipeline.
- [ ] Update docs/runbooks.

Effort tags: S (0.5–2 d), M (3–7 d), L (8+ d)

---

## Test &amp; QA Plan

- Unit Tests (backend)
  - Security: password hashing, JWT encode/decode, permission checks.
  - Services: StorageService (Local/S3 mocked), EmailService (mock provider), Document/Letter services, metadata validation.
  - Validators: User role constraints, document updates, input requests.
- Integration Tests (backend)
  - Async tests with httpx/pytest-asyncio hitting a test FastAPI app wired to a separate test DB.
  - Flows: auth/login/token refresh, roles/permissions checks, CRUD for core entities, upload/download, contract ingest status, AI endpoints behind test flag/mocked OpenAI client.
  - DB isolation: unique db name per run, teardown cleanup.
- Frontend Tests
  - Unit/render: pages (Login, Users, Documents, Contracts), hooks (auth, API), components (DocumentViewer, LetterWorkflow).
  - API client tests (axios interceptors behavior for token refresh/401).
- E2E Tests
  - Playwright: login/logout, RBAC-gated pages, create/edit entities, upload/view, link references, search/filter flows, contracts upload/search.
  - Use seeded fixtures for deterministic tests.
- Security and Abuse
  - Ensure header spoofing is blocked in PROD mode.
  - Brute force/login rate limits verified.
  - JWT tamper tests; CSRF not applicable to pure bearer flow (for cookie-based refresh, add CSRF token).
- Performance Baseline
  - Measure p95 latency for key endpoints pre/post index creation on a seeded dataset.

Coverage targets (initial)

- Backend: ≥70% lines/functions, with focus on services/routers.
- Frontend: ≥60% lines/functions; critical flows.

---

## Tooling &amp; CI/CD Suggestions

- Backend tooling:
  - ruff + black + isort; mypy (pydantic v2 plugin).
  - pre-commit hooks to enforce formatting/linting.
  - pytest with coverage; OpenAPI schema validation in CI.
- Frontend tooling:
  - eslint + typescript strict; vitest coverage threshold; bundle-size check (rollup-plugin-visualizer).
  - Type-safe API wrapper or OpenAPI client generation (optional).
- CI:
  - GitHub Actions with matrix (Python 3.11/3.12; Node 20).
  - Cache pip/npm; run unit/integration tests; upload coverage.
  - Build Docker images; scan with Trivy/Snyk; Dependabot for npm/pip.
- Deployment:
  - Env-only config (12-factor).
  - Docker-compose or k8s manifests/Helm; health and readiness probes.
  - Rollouts with health checks and rollback guidance.

---

## Effort Estimates (Summary)

- Small (0.5–2 days):
  - Repo hygiene, Secrets removal &amp; rotation (implementation portion), Settings refactor, Disable dev headers in prod, Structured logging, Rate limiting, MongoDB indexes, Email secrets hardening, API error envelopes, Docs/runbooks.
- Medium (3–7 days):
  - Router consolidation, Storage abstraction, AI guardrails, Auth refresh model upgrade, Tests expansion, Observability, CI/CD.
- Large (8+ days):
  - Enterprise SSO/SCIM, full multi-tenancy enforcement, analytics dashboards.

---

## Notable Details Observed

- backend/rbac_backend/core/config.py includes hardcoded keys/secrets: AWS, SMTP, OpenAI. Do not reuse; rotate and remove from source immediately.
- oauth2 tokenUrl is “/token” while all routers are mounted at “/api”; set to “/api/token” or compute from api_prefix.
- axios adds legacy demo headers (X-User-Id, X-User-Role, etc.), while backend reads lowercase header names; Starlette normalizes headers case-insensitively so they currently match. Still, disable header fallback in production by default.
- backend/rbac_backend/requirements.txt appears to have encoding anomalies (null-separated). Replace with a clean, UTF-8 file to avoid CI/install issues.
- Multiple duplicate routers and “New folder/\*” code should be removed to reduce risk.

---

## Next Steps

- Implement “Critical” then “High” items according to the plan above, starting with secrets rotation and removal, env-gating dev auth headers, index creation, and router consolidation.
- Establish CI checks early (lint/tests/build) to keep velocity while refactoring.
