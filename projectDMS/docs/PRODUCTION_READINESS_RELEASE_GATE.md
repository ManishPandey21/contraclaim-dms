# Production Readiness Release Gate

Status: Phase 8 final production-readiness audit recorded; current launch-readiness score is 38/100 after closing the upload-antivirus gate (P0-005) in code and adding Org-Admin permission HTTP-boundary regression coverage (P0-006). Python dependency scan, live-integration, E2E, backup/restore, and sign-off blockers remain.

Current verdict: Not Ready for production.

This document is the tracked release-control checklist for moving Contraclaim DMS from the current hardening branch to a production candidate. Until every launch gate below is satisfied, feature work should be frozen except for production-readiness fixes, test fixes, security fixes, operational hardening, and documentation needed to prove readiness.

## Branch And Freeze Policy

- Active remediation branch created for Phase 0: `codex/production-readiness-phase-0`.
- Feature freeze rule: do not merge new product features into the production candidate until the critical launch blockers are closed or explicitly accepted by the release owner.
- Allowed changes during freeze:
  - failing-test fixes
  - security/RBAC fixes
  - migration/index discipline
  - E2E and integration test coverage
  - deployment, backup, restore, monitoring, and runbook hardening
  - documentation that records verified readiness evidence
- Disallowed changes during freeze:
  - new user-facing modules not needed for readiness
  - speculative refactors
  - mock-only implementations presented as production capability
  - production env relaxation to bypass validation

## Launch Gates

Production promotion is blocked until all gates are checked.

### Gate 1: CI And Local Test Baseline

- [x] `python -m pytest backend/rbac_backend/tests -q` passes with 0 failures.
- [x] Backend tests do not require live OpenAI, MongoDB Atlas Vector Search, Qdrant, FalkorDB, Redis, or network access unless explicitly marked as live integration tests.
- [x] `npm run lint` passes in `client`.
- [x] `npm test -- --run` passes in `client`.
- [x] `npm run build` passes in `client`.
- [ ] GitHub Actions passes for backend, frontend, dependency scans, and Docker image scans.

Current known baseline:

- Full backend suite observed locally after Phase 4 contract hardening: 480 passed, 7 skipped, 238 warnings.
- Backend LlamaIndex tests are now hermetic: `backend/rbac_backend/tests/test_llamaindex_service.py` monkeypatches service-level LlamaIndex/OpenAI/Mongo vector dependencies even when real packages are installed.
- Frontend lint observed locally: passed with `eslint . --max-warnings=0`.
- Frontend Vitest observed locally with CI heap setting: 58 passed, 3 skipped.
- Frontend Playwright E2E observed locally after Phase 5: 5 passed.
- Frontend build observed locally: passed. Build still reports a non-fatal stale Browserslist data warning.
- Frontend dependency audit observed locally after removing the vulnerable React PDF Viewer/PDF.js stack: `npm audit --audit-level=high` found 0 vulnerabilities.
- Python dependency audit observed locally after resolving initial AWS/OpenAI resolver conflicts: `pip-audit -r backend/rbac_backend/requirements.txt` still found 62 known vulnerabilities in 22 packages.
- Python audit remediation is not a small patch: safe completion requires a coordinated FastAPI/Starlette upgrade plus LangChain/LangGraph/Pydantic-AI package-family upgrades, followed by a full backend regression run.
- Local `pre-commit run --all-files --show-diff-on-failure` could not be executed because the active Windows Python environment does not have `pre_commit` installed. CI installs `pre-commit` before running the hook set, so final proof remains the GitHub Actions run.

### Gate 2: Live Integration Baseline

- [ ] Run live external tests with `RUN_EXTERNAL_INTEGRATION_TESTS=1` against staging.
- [ ] Verify OpenAI document round trip.
- [ ] Verify Qdrant vector round trip.
- [ ] Verify FalkorDB graph/vector round trip.
- [ ] Verify Redis queue/runtime-state paths.
- [ ] Record all required live integration env vars used for the run.

Current skipped live-test evidence:

- `backend/rbac_backend/tests/integration/test_external_services_integration.py` skips live tests unless `RUN_EXTERNAL_INTEGRATION_TESTS=1`.
- The same test module skips again when required live integration env vars are missing.
- These skips are acceptable for normal unit CI, but production release requires a separate staging run with the live dependencies enabled.

### Gate 3: Browser E2E Coverage

- [ ] Login, logout, session refresh, and CSRF behavior.
- [ ] Org-Admin permission save/retrieve, including Client DMS permissions.
- [ ] Document upload, view, download, share, and authorization denial.
- [x] Contract upload, ingestion status, clause extraction, search, Q&A, and appraisal.
- [ ] Contract timeline link verify/reject.
- [ ] Chronology create, extract, verify/reject, export, and attach-to-arbitration flow.
- [ ] Arbitration draft create, generate, edit, save version, approve/return, export DOCX/PDF.
- [ ] Empty, loading, and API-error states for every production route.
- [ ] Desktop and mobile smoke coverage for primary workflows.

Current baseline:

- Playwright E2E is now present in `client/package.json`, `client/playwright.config.ts`, and `client/e2e/contract-workflows.spec.ts`.
- Local Playwright run covers mocked browser workflows for contract upload/progress, search success/empty/error, Q&A validation/cited answer, appraisal generation/report opening, and a mobile contract-search smoke check.
- Remaining Gate 3 items still need browser coverage before production promotion.

### Gate 4: Security And RBAC

- [x] `ALLOW_DEV_HEADERS=false` in production release config; production startup validation rejects `true`.
- [x] `RBAC_ENTITLEMENT_FAIL_OPEN=false` by default and in production release config; production startup validation rejects `true`.
- [x] `AUTH_COOKIE_SECURE=true` in production release config; production startup validation rejects `false`.
- [x] `SECRET_KEY` production startup validation requires a non-placeholder value of at least 32 characters.
- [x] Production CORS release config contains no localhost/dev origins; production startup validation rejects localhost/dev origins.
- [x] Org/project tenant isolation tests pass.
- [x] Permission seed drift tests pass.
- [ ] Production Org-Admin permissions are manually validated in staging/prod.
- [x] No real `.env` or secret files are tracked.
- [ ] Secret scan passes.

### Gate 5: Upload And Content Safety

- [x] `ANTIVIRUS_ENABLED=true` in production. Enforced by `Settings.validate_runtime_configuration`: production refuses to boot when antivirus is disabled unless `ANTIVIRUS_REQUIRED_IN_PRODUCTION=false` is set as a recorded override. Proven by `test_config_validation.py::test_production_validation_requires_antivirus_enabled` and `::test_production_validation_allows_explicit_antivirus_opt_out`.
- [x] `CLAMAV_FAIL_OPEN=false` in production. Enforced by startup validation; proven by `test_config_validation.py::test_production_validation_rejects_antivirus_fail_open`.
- [ ] Upload MIME, extension, size, and concurrency limits are validated.
- [x] Contract uploads fail closed when ClamAV is unavailable. `routers/contracts.py` and `routers/documents.py` reject with HTTP 400 when `scan_file` returns not-clean; `AntivirusService` returns not-clean when the daemon is unreachable with `fail_open=false` (`test_antivirus_service.py::test_scan_offline_fail_closed`, `::test_scan_clamav_error_fail_closed`). In production the scan branch is always active because antivirus is required.
- [ ] Sensitive extracted text is not logged.

### Gate 6: Database, Migrations, And Seeds

- [x] Versioned migration/index plan exists before production promotion.
- [ ] Fresh install path is tested against real MongoDB.
- [ ] Existing database upgrade path is tested against a staging copy.
- [x] Permission seeds are versioned and idempotent.
- [x] Backfill/migration entry points support dry-run before mutation.
- [x] Startup index creation is not the only production schema control.

### Gate 7: Deployment And Environment

- [ ] `scripts/pre_deploy_readiness.sh` passes.
- [ ] `scripts/preflight.py` passes.
- [ ] `docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml config` succeeds without unresolved variables.
- [ ] `/health/live` passes.
- [ ] `/health/ready` passes.
- [ ] `/metrics` is enabled and token-gated.
- [ ] Worker, queue, Redis, Qdrant, FalkorDB, MongoDB, and storage health are verified.

Required production env groups:

- Runtime: `ENVIRONMENT`, `PUBLIC_BASE_URL`, `PUBLIC_API_BASE_URL`, `FRONTEND_URL`, `API_URL`.
- MongoDB: `DATABASE_URL`, `MONGODB_REPLICA_SET`, `MONGODB_DATABASE`.
- Auth/session: `SECRET_KEY`, `AUTH_COOKIE_SECURE`, `CORS_ORIGINS`, `ALLOW_DEV_HEADERS`, `RBAC_ENTITLEMENT_FAIL_OPEN`.
- Object storage: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET_NAME`, `AWS_REGION`.
- AI/retrieval: `OPENAI_API_KEY`, `OPENAI_MODEL`, `QDRANT_API_KEY`, `QDRANT_URL`, `FALKORDB_PASSWORD`, `FALKORDB_URL`, `FALKORDB_GRAPH_NAME`, `LANGGRAPH_API_TOKEN` when enabled.
- Redis/queue/runtime state: `REDIS_PASSWORD`, `APP_REDIS_URL`, `RUNTIME_STATE_REDIS_URL`, `CONTRACT_QUEUE_REDIS_URL`.
- Email/SMTP: `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, SMTP host/port/security settings, SMTP settings encryption key if enabled.
- Upload safety: `ANTIVIRUS_ENABLED`, `CLAMAV_HOST`, `CLAMAV_PORT`, `CLAMAV_FAIL_OPEN`, upload size/concurrency settings.
- Observability: `METRICS_ENABLED`, `METRICS_TOKEN`, log-level settings.
- Billing: `PAYMENT_PROVIDER` and provider-specific keys/webhook secrets if billing is enabled for launch.
- Backup: `BACKUP_ROOT`, `BACKUP_MAX_AGE_HOURS`, `BACKUP_REQUIRED_IN_PRODUCTION`, `BACKUP_REQUIRED_VOLUME_LABELS`, `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`.

### Gate 8: Backup, Restore, And Rollback

- [ ] MongoDB logical backup succeeds.
- [ ] Backend uploads backup succeeds.
- [ ] Qdrant backup or rebuild plan is verified.
- [ ] FalkorDB backup or Mongo-derived reconciliation/rebuild is verified.
- [ ] Redis backup requirement is explicitly accepted or tested.
- [ ] Full restore drill into staging/isolated environment succeeds.
- [ ] RPO and RTO are recorded.
- [ ] Rollback steps are documented and tested.

### Gate 9: Final Production Readiness Review

- [ ] Critical blockers closed.
- [ ] High severity risks fixed or explicitly accepted.
- [ ] Staging smoke test passes after deploy.
- [ ] Staging smoke test passes after restore drill.
- [ ] Readiness score target is 85 or higher.
- [ ] Release owner signs off.

Current score evidence:

- Current production launch-readiness score: **32/100**.
- Current verdict: **Not Ready**.
- Score target: **85/100**.
- Scoring script: `python scripts/production_readiness_score.py`.
- Final audit record: `docs/PRODUCTION_READINESS_FINAL_AUDIT.md`.

## Current Critical Blocker Register

| ID | Status | Blocker | Evidence | Required resolution |
| --- | --- | --- | --- | --- |
| P0-001 | Resolved locally | Backend suite is red | Original full backend run observed 6 failures in `test_llamaindex_service.py`; Phase 1 local rerun now reports 462 passed, 7 skipped | Confirm in GitHub Actions |
| P0-002 | Open | Live AI/vector/graph integrations are skipped by default | `RUN_EXTERNAL_INTEGRATION_TESTS=1` required for live tests | Add a staging live-integration release gate and capture results |
| P0-003 | Partially mitigated | Browser E2E incomplete | Phase 5 adds Playwright coverage for contract upload/search/Q&A/appraisal, but auth/session, document workflows, timeline, chronology, arbitration, and broad empty/error/mobile coverage remain uncovered | Expand E2E harness to all launch-critical workflows |
| P0-004 | Resolved locally | Migration discipline incomplete | Phase 3 adds `rbac_backend.migrations`, `schema_migrations` ledger, RBAC seed digesting, and `python -m rbac_backend.scripts.migrate_database` dry-run/apply support | Run against staging/fresh MongoDB and confirm in CI/release evidence |
| P0-005 | Resolved (code) | Upload antivirus can be disabled | Production startup now refuses to boot unless antivirus is enabled and fail-closed (`ANTIVIRUS_REQUIRED_IN_PRODUCTION` default true); `.env.example` sets `ANTIVIRUS_ENABLED=true`; upload routes reject not-clean files. Tests: `test_config_validation.py` (3 new), `test_antivirus_service.py` | Deploy ClamAV in staging and capture a live infected/clean scan as final Gate 5 proof |
| P0-006 | Mitigated (regression coverage added) | Production Org-Admin permission flow not yet validated | Service round trip covered by `test_role_permission_catalog_drift.py`; HTTP-boundary retrieve now covered by `test_org_admin_permissions_api.py`, reproducing the catalog-missing Client DMS permission failure through `GET /api/roles/{id}/permissions` | Manual save/retrieve validation in staging/prod remains for the Gate 4 box |
| P0-007 | Partially mitigated | Python dependency scan is red | `requests` bumped to 2.32.4 (CVE-2024-47081). Remaining ~60 advisories require a coordinated FastAPI/Starlette + LangChain/LangGraph/Pydantic-AI upgrade and a full backend regression run; `ecdsa` Minerva (CVE-2024-23342) is upstream won't-fix and unused in our HS256 path | Execute the framework/AI-stack upgrade, rerun `pip-audit` to green (or document accepted won't-fix), rerun backend tests and Docker build |
| P0-008 | Open | Final staging deploy, smoke, backup/restore, and release sign-off evidence are missing | Gate 9 score remains 0/8 and current readiness score is 32/100 | Complete staging deploy, smoke after deploy, smoke after restore, readiness-score rerun, and release owner sign-off |

## Current Readiness Score

The current production launch-readiness score is **38/100** against a target of
**85/100**. The score is generated from checked launch-gate evidence, not from
implementation intent or local-only assumptions.

Run:

```bash
python scripts/production_readiness_score.py
```

Current gate score summary:

| Gate | Score | Checked |
| --- | ---: | ---: |
| Gate 1: CI And Local Test Baseline | 12.50 / 15 | 5 / 6 |
| Gate 2: Live Integration Baseline | 0.00 / 10 | 0 / 6 |
| Gate 3: Browser E2E Coverage | 1.33 / 12 | 1 / 9 |
| Gate 4: Security And RBAC | 12.00 / 15 | 8 / 10 |
| Gate 5: Upload And Content Safety | 6.00 / 10 | 3 / 5 |
| Gate 6: Database, Migrations, And Seeds | 6.67 / 10 | 4 / 6 |
| Gate 7: Deployment And Environment | 0.00 / 10 | 0 / 7 |
| Gate 8: Backup, Restore, And Rollback | 0.00 / 10 | 0 / 8 |
| Gate 9: Final Production Readiness Review | 0.00 / 8 | 0 / 6 |

## Pending Blockers By Phase

| Phase | Pending blockers |
| --- | --- |
| Phase 0 | None currently recorded. |
| Phase 1 | GitHub Actions remote run; Python dependency audit; Docker image scans. |
| Phase 2 | Secret scan/gitleaks; production Org-Admin Client DMS permission save/retrieve validation. |
| Phase 3 | Fresh Mongo migration dry-run/apply; staging-copy migration dry-run/apply; Bash syntax validation on a host with Bash. |
| Phase 4 | Live staging proof with OCR, ClamAV, OpenAI, Qdrant, FalkorDB, Redis, and storage enabled. |
| Phase 5 | Browser E2E for auth/session/CSRF, Org-Admin permissions, document workflows, timeline, chronology, arbitration, and broad route states; live staging browser run. |
| Phase 6 | Live LLM-backed arbitration drafting proof; arbitration browser E2E for create/import/generate/regenerate/export/approval. |
| Phase 7 | Bash syntax validation; staging backup execution, offsite S3 sync, `/health/operations` scrape, restore drill, RPO/RTO, and rollback proof. |
| Phase 8 | Critical blockers still open; readiness score below target; release owner sign-off missing. |

## Evidence Capture Template

Use this format for each release-gate run.

```text
Date:
Branch/commit:
Environment:
Command/check:
Result:
Evidence link or log path:
Owner:
Follow-up issue:
```

## Phase 0 Acceptance Status

- [x] Production-readiness branch created.
- [x] Feature freeze/release-gate policy documented.
- [x] Launch gates defined.
- [x] Required production env groups confirmed from `docker-compose.prod.yml` and `.env.example`.
- [x] Current failing backend test baseline documented.
- [x] Current skipped live integration baseline documented.
- [x] One tracked readiness checklist exists: this file.

## Phase 1 Acceptance Status

- [x] Backend unit/API suite is green locally.
- [x] Backend LlamaIndex tests no longer depend on real OpenAI/Mongo vector-store implementations.
- [x] Frontend lint is green locally.
- [x] Frontend Vitest is green locally and no longer hangs on `LetterDraftPage.basic-render.test.tsx`.
- [x] Frontend production build is green locally.
- [x] Frontend dependency audit is green locally.
- [x] `git diff --check` passes.
- [ ] GitHub Actions remote run has not yet been observed.
- [ ] Python dependency audit is still red and tracked as P0-007.
- [ ] Docker image scans have not yet been observed locally.

## Phase 2 Acceptance Status

- [x] Entitlement checks now fail closed by default: `RBAC_ENTITLEMENT_FAIL_OPEN` default changed to `false`.
- [x] All canonical Client DMS permissions are entitlement-scoped via `CLIENT_DMS_PERMISSIONS`, including timeline, chronology, arbitration, claims, registers, evidence graph, and contract appraisal permissions.
- [x] Archive/read-only subscriptions deny newer DMS write/admin permissions such as evidence-graph verification while still allowing read/export style permissions.
- [x] Root production `.env.example` and CI env declare `RBAC_ENTITLEMENT_FAIL_OPEN=false`.
- [x] Production `.env.example` declares `CLAMAV_FAIL_OPEN=false`.
- [x] Backend local `.env.example` is explicitly marked `ENVIRONMENT=development`, with dev headers and entitlement fail-open disabled.
- [x] Local backend security/RBAC validation passed: `python -m pytest backend/rbac_backend/tests/test_config_validation.py -q`.
- [x] Local RBAC hardening validation passed: `python -m pytest backend/rbac_backend/tests/test_rbac_policy_hardening.py -q`.
- [x] Local tenant and permission drift validation passed: `python -m pytest backend/rbac_backend/tests/test_permission_catalog.py backend/rbac_backend/tests/test_role_permission_catalog_drift.py backend/rbac_backend/tests/test_tenant_isolation.py -q`.
- [x] Full local backend suite passed after the fail-closed change: 470 passed, 7 skipped, 238 warnings.
- [x] Frontend route-permission parity passed: `npx vitest run src/config/__tests__/rolePermissions.sidebar.test.ts`.
- [x] `git ls-files` confirms local `.env` files are not tracked.
- [x] `git diff --check` passes.
- [ ] Local gitleaks secret scan not run because `gitleaks` is not installed in the active environment; CI secret-scan job remains the release proof.
- [ ] Production Org-Admin permission save/retrieve flow still requires staging/prod manual validation.

## Phase 3 Acceptance Status

- [x] Added versioned MongoDB migration package: `backend/rbac_backend/migrations`.
- [x] Added applied-migration ledger support through the `schema_migrations` collection.
- [x] Added migration CLI: `python -m rbac_backend.scripts.migrate_database`.
- [x] Migration CLI supports `--list`, dry-run by default, `--apply`, `--target`, and `--fail-on-warning`.
- [x] Added startup-index baseline migration that runs `rbac_backend.core.database.ensure_indexes` as a controlled production schema step.
- [x] Added deterministic RBAC seed catalog metadata in `backend/rbac_backend/initial_data/seed_catalog.py`.
- [x] Added RBAC seed catalog digest persistence through `seed_catalog_versions`.
- [x] Added explicit `roles:assign` initial permission seed because `projectadmin` references it.
- [x] `scripts/pre_deploy_readiness.sh` now checks that the migration runner exists and can enforce a migration dry-run with `RUN_MIGRATION_DRY_RUN=true REQUIRE_MIGRATION_DRY_RUN=true`.
- [x] Operations runbook documents migration dry-run/apply commands and the deploy dry-run gate.
- [x] Focused local validation passed: `python -m pytest backend/rbac_backend/tests/test_migration_runner.py backend/rbac_backend/tests/test_permission_catalog.py backend/rbac_backend/tests/test_startup_indexes.py -q` -> 12 passed.
- [x] Existing seed regression validation passed: `python -m pytest backend/rbac_backend/tests/test_data_initialization.py -q` -> 2 passed.
- [x] Python compile check passed for the migration and seed catalog modules.
- [x] Migration CLI import/help validation passed from `backend`: `python -m rbac_backend.scripts.migrate_database --help`.
- [ ] Migration dry-run/apply against a fresh MongoDB database remains required for release evidence.
- [ ] Migration dry-run/apply against a staging copy of an existing database remains required for release evidence.
- [ ] Bash syntax validation for shell scripts could not run locally on this Windows host unless WSL or another Bash runtime is available.

## Phase 4 Acceptance Status

- [x] Contract upload-session, multipart upload, chunk upload, upload status, contract list, and contract search routes now run central `PolicyService` authorization checks for the resolved organization/project scope.
- [x] Upload denial tests prove multipart and chunk upload authorization happens before reading or rewinding uploaded bytes.
- [x] Status/list/search denial tests prove scoped contract metadata is not returned and service queries are not executed after policy denial.
- [x] Numbered contract headings such as `1 General Conditions`, `1.1 Notices`, and `1.1.1 Method of Service` now preserve clause number, title, level, and parent hierarchy during extraction.
- [x] Contract ingestion payload tests cover searchable/grounded clause metadata: organization/project, document/upload id, clause id/number/title/type/level/parent, TOC path, page numbers, tags, checksum, and enriched text.
- [x] Contract Q&A regression covers Mongo fallback retrieval, full-clause expansion across split chunks, stable clause citation rewriting, and merged page citations.
- [x] Existing graph-aware contract search tests pass for Falkor-derived graph candidate assembly and clause-seed extraction.
- [x] Existing contract appraisal tests pass, including citation-backed generation, completeness checks, lifecycle, versioning, scope isolation, approve/reject/regenerate, export, and register creation.
- [x] Focused local Phase 4 validation passed: `python -m pytest backend/rbac_backend/tests/test_contract_clause_extraction.py -q`.
- [x] Focused local Phase 4 validation passed: `python -m pytest backend/rbac_backend/tests/test_contract_upload_security.py backend/rbac_backend/tests/test_contract_graph_retrieval.py backend/rbac_backend/tests/test_contract_appraisal.py backend/rbac_backend/tests/test_retrieval_engine_basics.py backend/rbac_backend/tests/test_retrieval_engine_scope_isolation.py -q`.
- [x] Full local backend suite passed after Phase 4 changes: 480 passed, 7 skipped, 238 warnings.
- [x] Backend compile check passed: `python -m compileall -q backend/rbac_backend`.
- [x] Frontend route-permission parity passed: `npx vitest run src/config/__tests__/rolePermissions.sidebar.test.ts`.
- [x] Browser E2E for contract upload/progress, search, Q&A, and appraisal is now covered with a mocked backend; live ingestion/extraction proof remains tracked under Gates 2, 5, and 7.
- [ ] Live staging proof with OCR/ClamAV/OpenAI/Qdrant/FalkorDB/Redis enabled remains missing and is still tracked under Gates 2, 5, and 7.

## Phase 5 Acceptance Status

- [x] Added Playwright E2E runner and scripts: `npm run test:e2e` and `npm run test:e2e:ui`.
- [x] Added local Playwright config with Vite web-server startup, Chromium project, traces/screenshots/videos retained on failure, and CI-safe retries.
- [x] Added mocked browser E2E suite for contract upload/progress, search success/empty/error, contract Q&A validation/cited answer, contract appraisal generation/report opening, and mobile search usability.
- [x] Added stable `data-testid` hooks to the critical contract upload, search, Q&A, and appraisal controls without changing user-facing copy or layout.
- [x] CI frontend job now installs Chromium with `npx playwright install --with-deps chromium` and runs `npm run test:e2e`.
- [x] Playwright artifacts are ignored via `.gitignore` and `client/.gitignore`.
- [x] Local Playwright validation passed: `npm run test:e2e -- --project=chromium` -> 5 passed.
- [x] Local targeted ESLint passed for the new E2E spec, Playwright config, and edited contract pages.
- [x] Full frontend lint passed: `npm run lint`.
- [x] Full frontend Vitest suite passed: 58 passed, 3 skipped.
- [x] Frontend build passed: `npm run build`.
- [x] Frontend dependency audit passed after adding Playwright: `npm audit --audit-level=high` found 0 vulnerabilities.
- [ ] Remaining browser E2E coverage is still required for login/logout/session refresh/CSRF, Org-Admin permissions, document upload/view/download/share/denial, timeline verify/reject, chronology, arbitration drafting, and broad route empty/loading/error states.
- [ ] Live staging browser run with real backend, OCR, ClamAV, OpenAI, Qdrant, FalkorDB, Redis, and storage remains required before production promotion.

## Phase 6 Acceptance Status

- [x] Arbitration generation now uses a stable input hash built from legal inputs, source hashes, claim heads, paragraph responses, section key, prompt version, and source policy.
- [x] Repeated generation with the same input hash reuses the latest version instead of creating duplicate immutable versions.
- [x] Generation runs now record retrieval queries and richer parsed output metadata, including source policy, source hashes, source count, missing-evidence count, validation warnings, approval blockers, and legal-review requirement.
- [x] Source ledger rows now carry verification status, source quality flags, evidence strength, and context warnings for manual facts or unverified AI graph links.
- [x] Paragraph-level SoD/rejoinder denial responses now include supporting citations when mapped, or `[Evidence required]` when support is missing.
- [x] Manual draft versions now receive the same validation report as generated versions.
- [x] Approval now blocks versions with legal drafting safety blockers such as unknown source citations or unsupported amounts/dates, while preserving missing-evidence warnings for review.
- [x] Invalid section regeneration keys are rejected with an explicit allowed-section list.
- [x] Arbitration draft detail UI now displays validation status, source quality flags, missing evidence, legal safety warnings, approval blockers, and section-level regeneration controls.
- [x] Focused backend validation passed: `python -m pytest backend/rbac_backend/tests/test_arbitration_drafting.py -q` -> 8 passed.
- [x] Backend arbitration plus route-inventory validation passed: `python -m pytest backend/rbac_backend/tests/test_arbitration_drafting.py backend/rbac_backend/tests/test_route_inventory.py -q` -> 12 passed.
- [x] Backend compile check passed for arbitration drafting models/services.
- [x] Frontend targeted lint passed for `ArbitrationDraftingPage.tsx` and `arbitration-drafting-api.ts`.
- [x] Frontend route-permission parity passed: `npm test -- --run src/config/__tests__/rolePermissions.sidebar.test.ts`.
- [x] Frontend production build passed: `npm run build`.
- [ ] Live LLM-backed drafting remains intentionally disabled in this deterministic hardening slice; any future LLM layer must preserve the same source-ledger, validator, versioning, and approval-blocker contract.
- [ ] Browser E2E for arbitration create/import/generate/regenerate/export/approval remains required before production promotion.

## Phase 7 Acceptance Status

- [x] Added backup freshness service: `backend/rbac_backend/services/operations_health.py`.
- [x] Added token-gated `/health/operations` endpoint for backup freshness and operational health reporting.
- [x] Added Prometheus backup gauges: `contractdms_backup_health`, `contractdms_backup_latest_age_seconds`, `contractdms_backup_missing_artifacts`, and `contractdms_backup_unhealthy_artifacts`.
- [x] Added production backup settings to backend config and production validation: `BACKUP_ROOT`, `BACKUP_MAX_AGE_HOURS`, `BACKUP_REQUIRED_IN_PRODUCTION`, `BACKUP_REQUIRED_VOLUME_LABELS`, `BACKUP_S3_BUCKET`, and `BACKUP_S3_PREFIX`.
- [x] Wired production Compose to pass backup settings and mount `BACKUP_ROOT` read-only into backend and worker containers.
- [x] Added standalone backup freshness checker: `scripts/backup_status.py`.
- [x] `scripts/production_backup.sh` now writes a completion manifest, latest manifest, and SHA-256 checksum file.
- [x] `scripts/mongo_backup.sh` now accepts a shared `STAMP` so production backup artifacts align.
- [x] Restore scripts now validate gzip/tar archive integrity before restore.
- [x] `scripts/post_deploy_verify.sh` now sends `METRICS_TOKEN` to `/health/observability`, checks `/health/operations`, and verifies backup freshness.
- [x] `scripts/pre_deploy_readiness.sh` now checks backup scripts, backup S3 configuration, backup freshness, and the current operations/release-gate docs.
- [x] `scripts/smoke_health.py` can optionally include `/health/operations` via `SMOKE_CHECK_OPERATIONS=true`.
- [x] Rewrote `docs/OPERATIONS.md` with clean deployment, monitoring, backup, restore drill, alerting, scheduler, and security operations guidance.
- [x] Focused backend validation passed: `python -m pytest backend/rbac_backend/tests/test_operations_health.py backend/rbac_backend/tests/test_observability.py backend/rbac_backend/tests/test_config_validation.py -q` -> 15 passed.
- [x] Python compile check passed for operations health, observability, health router, backup status, smoke health, and preflight scripts.
- [ ] Bash syntax validation could not run locally because this Windows host maps `bash` to WSL and no WSL distribution is installed.
- [ ] Actual staging backup execution, offsite S3 sync, `/health/operations` scrape, and restore drill remain required before production promotion.

## Phase 8 Acceptance Status

- [x] Added deterministic readiness scoring script: `scripts/production_readiness_score.py`.
- [x] Recorded final production-readiness audit: `docs/PRODUCTION_READINESS_FINAL_AUDIT.md`.
- [x] Current launch-readiness score calculated from launch-gate evidence: **32/100**.
- [x] Current verdict remains **Not Ready**.
- [x] Pending blockers are recorded by phase.
- [x] Critical blocker register includes final staging deploy/smoke/restore/sign-off gap as `P0-008`.
- [x] Score script compile validation passed: `python -m py_compile scripts/production_readiness_score.py`.
- [x] Score script execution passed: `python scripts/production_readiness_score.py`.
- [ ] Critical blockers remain open.
- [ ] Readiness score remains below the 85/100 target.
- [ ] Release owner sign-off is not recorded.
