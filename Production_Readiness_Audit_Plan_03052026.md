# Production Readiness Audit and Improvement Plan - 03 May 2026

## Executive Summary

Overall production readiness score: **4/10**.

The codebase has a workable SaaS foundation: FastAPI modular routers, MongoDB metadata storage, Redis-backed contract ingestion, local/S3 document storage, Docker Compose deployment files, audit/logging utilities, and a recently improved document storage architecture. However, it is not yet ready for high-traffic production use.

The highest-risk blockers are exposed secrets, incomplete health/readiness support, weak CI/CD, non-enforced rate limiting, in-memory sessions/cache/background jobs, memory-heavy upload paths, limited zero-downtime deployment design, and concurrency gaps around document updates and large uploads.

## Key Strengths

- FastAPI code is organized into routers, services, models, config, auth, storage, and queue modules.
- MongoDB indexes exist and have been expanded for document storage/versioning collections.
- Contract ingestion uses a Redis queue rather than doing all processing synchronously in request handlers.
- Document and contract storage now has a better target model: `file_objects`, `document_versions`, `contracts`, `contract_versions`, and `document_audit_events`.
- Docker Compose includes service networks, volumes, log rotation, and health checks for several dependency containers.
- Startup validates placeholder values for critical settings.
- Authorization dependencies and permission checks exist across major document routes.

## Critical Issues and Risks

### 1. Secrets Exposure

Real `.env` files with API keys, SMTP credentials, AWS credentials, JWT secrets, and Qdrant keys are present in the workspace. These credentials must be treated as compromised.

Impact:

- Account/API abuse.
- Data exfiltration from S3 or third-party APIs.
- Unauthorized production access if reused.
- Compliance and incident-response risk.

### 2. Broken or Incomplete Backend Health Checks

`docker-compose.yml` checks `http://localhost:8000/health`, but the active backend entrypoint `backend/rbac_backend/main.py` does not define a root `/health` endpoint or mount the `performance` router that defines one.

Impact:

- Containers may be marked unhealthy even when running.
- Deployment automation cannot distinguish starting, ready, degraded, and failed states.
- Zero-downtime rollout cannot be trusted.

### 3. Rate Limiting Is Not Enforced

`backend/rbac_backend/utils/rate_limiter.py` records counters and logs some limit exceedance, but does not reliably block requests.

Impact:

- Login brute-force protection is weaker than expected.
- Upload, AI, search, and report endpoints can be abused.
- A single user or script can degrade service for all tenants.

### 4. In-Memory Sessions, Lockouts, Cache, and Background Jobs

`AuthenticationService` stores sessions, failed attempts, and lockouts in memory. General background jobs use an in-process `asyncio.Queue`. Cache is process-local.

Impact:

- State is lost on restart.
- Multiple backend replicas will behave inconsistently.
- Logout/session invalidation does not reliably work across replicas.
- Background jobs can be lost during deploys.

### 5. Upload Paths Are Memory-Heavy

Several upload flows read whole files into memory:

- General document upload uses `await file.read()`.
- Multipart contract upload reads each file fully.
- Chunk finalization reads the merged file fully via `read_bytes()`.
- Processing copies duplicate file bytes on disk.

Impact:

- High memory usage under concurrent uploads.
- API worker lag and possible OOM kills.
- Poor user experience for large documents/contracts.
- Increased storage and I/O cost.

### 6. Docker Production Setup Is Insufficient

`docker-compose.prod.yml` uses `deploy.replicas`, which normal `docker compose` ignores unless using Swarm. There is no clear image tagging, registry flow, migration step, readiness gate, or rollback strategy.

Impact:

- Production deployments may not actually scale.
- Rollbacks depend on manual intervention.
- Release state is hard to reproduce.

### 7. MongoDB Deployment Is Single-Node

MongoDB is deployed as a single local container volume in Compose. No replica set, managed backup, PITR, TLS, auth hardening, or operational monitoring is evident.

Impact:

- Single point of failure.
- Data loss risk.
- No production-grade failover.
- Limited ability to support high concurrency and read scaling.

### 8. Observability Is Incomplete

The codebase has logging and some performance-related services, but no consistent structured request logs, request IDs, distributed tracing, metrics endpoint, Prometheus/OpenTelemetry integration, Sentry, or alerting.

Impact:

- Production incidents will be hard to diagnose.
- Queue buildup, upload latency, S3 failures, and Mongo slow queries may go unnoticed.
- SLA/SLO tracking is not possible.

### 9. Multi-User Concurrency Controls Are Weak

Document metadata updates generally follow last-write-wins behavior. There is no clear optimistic concurrency or ETag handling.

Impact:

- Users can overwrite each other's metadata changes.
- Concurrent uploads/updates can produce inconsistent state.
- Collaboration workflows are fragile under real load.

## High-Priority Recommendations

1. Rotate all exposed secrets and remove real `.env` files from Git history if committed.
2. Add working `/health/live` and `/health/ready` endpoints to the active FastAPI app.
3. Replace in-memory rate limiting with Redis-backed enforcement.
4. Move sessions, lockouts, and refresh/session state to Redis or MongoDB.
5. Move general background jobs out of the API process into a durable worker queue.
6. Refactor upload paths to stream bytes instead of reading full files into memory.
7. Separate API, worker, scheduler, and ingestion services.
8. Use managed MongoDB or a properly configured replica set with backups and monitoring.
9. Add structured JSON logging, request IDs, metrics, tracing, and exception monitoring.
10. Add optimistic concurrency for document updates.

## Scaling and Concurrency Strategy

### API Layer

- Run FastAPI as stateless replicas behind a load balancer.
- Do not run CPU-heavy OCR, embedding, or document parsing inside API workers.
- Add request body limits, request timeouts, and upload-specific throttling at the gateway.
- Use Redis for shared session state, rate limits, cache invalidation, and job coordination.

### MongoDB

- Use managed MongoDB Atlas or a production replica set.
- Enable auth, TLS, daily backups, PITR, and slow query monitoring.
- Add compound indexes for frequent tenant-scoped queries:
  - `organization_id`, `project_id`, `createdAt`
  - `organization_id`, `project_id`, `uploadType`, `status`
  - `organization_id`, `project_id`, `lifecycle_state`, `createdAt`
  - `file_object_id`, `current_version_id`, `contract_id`
- Avoid unbounded `.to_list(length=None)` in request paths.
- Use idempotency records for upload and multi-collection write workflows.

### Background Processing

- Keep contract ingestion queue but run workers separately from API containers.
- Move document OCR/background jobs to Redis-backed Celery, RQ, Arq, Dramatiq, or equivalent.
- Add dead-letter queues, retry backoff, job timeout, and queue depth metrics.
- Limit OCR/embedding concurrency with worker-level semaphores.

### Upload Performance

- Short term: stream files to disk/object storage in chunks while computing SHA-256.
- Medium term: eliminate duplicate temp copies where `file_object_id` can materialize on workers.
- Long term: implement browser direct-to-S3 multipart upload using pre-signed URLs.
- Store chunk manifests in MongoDB or Redis with atomic status updates.
- Add per-user and per-organization concurrent upload limits.

### Multi-User Collaboration

- Add document metadata version fields and optimistic concurrency checks.
- Require clients to submit `version` or `updatedAt` when updating metadata.
- Return conflict responses instead of silently overwriting.
- Record all create/update/delete/download events in immutable audit events.
- Consider document-level edit locks for high-conflict workflows.
- Push upload and job progress through WebSocket/SSE backed by Redis pub/sub.

## Deployment Best Practices

### Docker

- Use non-root users in backend and frontend containers.
- Pin base image versions instead of relying on broad tags.
- Use multi-stage backend builds where native dependencies are large.
- Add container-level health checks.
- Do not mount development `.env` files in production.
- Do not expose MongoDB, Redis, Qdrant, or FalkorDB ports publicly.

### CI/CD

- Run backend unit tests, frontend tests, lint, type checks, secret scan, dependency scan, and container scan.
- Build immutable images tagged by commit SHA.
- Push images to a registry.
- Deploy the exact tested image to staging and production.
- Run smoke tests after deployment.
- Block promotion if readiness checks fail.

### Zero-Downtime Deployment

- Use rolling deployment or blue/green deployment.
- Keep DB/storage migrations backward compatible.
- Run migration/backfill jobs separately from app startup.
- Use readiness probes to remove unhealthy pods/containers from rotation.
- Define rollback to previous image tag.
- Drain workers gracefully before shutdown.

### Environment Management

- Maintain separate development, staging, and production environment files or secret scopes.
- Store secrets in a secret manager.
- Validate required environment variables on startup.
- Fail production startup if any critical placeholder/default value is detected.
- Document every required env var and its expected format.

## Phase-Wise Improvement Plan

## Implementation Status

Completed in the first implementation pass:

- Added active backend health endpoints:
  - `/health`
  - `/health/live`
  - `/health/ready`
- Mounted the health router in the active FastAPI app.
- Updated Docker backend health check to use `/health/ready`.
- Updated Docker service dependencies so the frontend and gateway wait for a healthy backend.
- Enforced the existing rate limiter with HTTP 429 responses instead of logging only.
- Added a `check_client_limit` compatibility method used by performance health routes.
- Shared in-process limiter buckets across controller instances as an immediate baseline.
- Added CI secret scanning with Gitleaks.
- Updated backend and frontend Docker images to run as non-root users.
- Added production runtime validation for `ENVIRONMENT=production`.
- Added request ID middleware and request completion/error logs.
- Disabled FastAPI docs/OpenAPI automatically in production unless non-production mode is used.
- Added deployment baseline documentation in `docs/Production_Deployment_Baseline.md`.
- Expanded CI into separate backend, frontend, dependency scan, secret scan, Docker build, and image scan jobs.
- Added Dependabot configuration for Python, npm, Docker, and GitHub Actions.
- Added Docker build-context hardening through `.dockerignore` files.
- Added release runbook and health smoke-test script.
- Added shared Redis runtime state support for sessions, lockouts, rate limiting, and cache.
- Added runtime Redis production validation.
- Added `contract-worker` process entrypoint and Docker Compose service.
- Added Phase 3 runtime state documentation in `docs/Phase3_Runtime_State.md`.
- Added bounded-memory upload spooling, incremental SHA-256, path-based provider writes, and upload concurrency limits.
- Refactored document, enclosure, contract multipart, and contract chunk finalization flows to avoid full-file request memory buffers where practical.
- Added Phase 4 upload performance documentation in `docs/Phase4_Upload_Storage_Performance.md`.
- Added MongoDB production client hardening with pooling, timeouts, retry writes, database selection, and production replica-set validation.
- Added asynchronous index creation with retry and expanded tenant-scoped MongoDB indexes for document, contract, file object, audit, upload session, ingest job, letter, and bulk upload workloads.
- Added a self-managed MongoDB replica-set Compose overlay for non-Atlas deployments.
- Added MongoDB backup/restore scripts and Phase 5 operations runbook in `docs/Phase5_MongoDB_Production_Hardening.md`.
- Added lightweight Prometheus-compatible metrics, observability health endpoint, slow-request logs, and Phase 6 incident response runbook.
- Added optional document optimistic concurrency with revision headers, document audit history endpoint, comment audit events, and Phase 7 collaboration runbook.

Still pending from the roadmap:

- Rotate and purge exposed credentials from history.
- Replace in-process rate limiting with Redis-backed distributed limiting.
- Move sessions, account lockouts, cache, and general background jobs to durable distributed storage.
- Refactor upload paths to stream large files and support direct-to-S3 multipart upload.
- Split API, worker, scheduler, and ingestion processes.
- Add full structured logging, metrics, tracing, dashboards, and alerting.
- Add optimistic concurrency controls for collaborative document updates.

## Phase 0 - Emergency Security Stabilization

Target: **Immediate**

Goal: remove the highest-risk production blockers before any public deployment.

Tasks:

- [ ] Rotate exposed OpenAI, AWS, SMTP, Qdrant, JWT, LangGraph, and other API credentials.
- [ ] Remove real `.env` files from Git history if they were committed.
- [x] Confirm `.gitignore` blocks all secret and local data files.
- [x] Add Gitleaks or TruffleHog to CI.
- [ ] Disable public access to MongoDB, Redis, Qdrant, and FalkorDB ports in production infrastructure.
- [x] Set `ALLOW_DEV_HEADERS=false` by default and reject it in production startup validation.
- [x] Reject localhost/dev CORS origins in production startup validation.

Verification:

- Secret scanner passes.
- Old credentials no longer work.
- Production runtime uses only secret manager or secure deployment variables.
- External port scan shows only intended HTTP/HTTPS ports exposed.

Current status: **partially implemented**. Repo-side guardrails are in place, but Phase 0 cannot be considered complete until exposed credentials are rotated and any committed secret history is purged.

## Phase 1 - Health, Configuration, and Deployment Baseline

Target: **Week 1**

Goal: make deployments observable and safely restartable.

Tasks:

- [x] Add `/health/live` endpoint for process liveness.
- [x] Add `/health/ready` endpoint checking MongoDB, Redis queue, local storage config, and runtime configuration.
- [x] Update Docker health checks to use readiness endpoint.
- [x] Add startup config validation for production-specific requirements.
- [x] Add request ID logging middleware.
- [x] Add graceful shutdown handling for currently running background services and contract queue consumers.
- [x] Add clear deployment baseline documentation for production.

Verification:

- Docker health checks pass only when dependencies are available.
- Failed Mongo/Redis connection causes readiness failure.
- Logs include request ID, user ID where safe, method, path, status code, latency.
- Rolling restart does not lose in-flight HTTP requests.

## Phase 2 - CI/CD and Release Discipline

Target: **Week 1-2**

Goal: every deployable artifact is tested, scanned, tagged, and reproducible.

Tasks:

- [x] Expand GitHub Actions to run full backend test suite.
- [x] Add frontend build and test job.
- [x] Add lint checks for Python/pre-commit and frontend ESLint.
- [x] Add dependency vulnerability scanning.
- [x] Add Docker image build and scan.
- [ ] Build and push immutable images tagged by commit SHA.
- [ ] Add staging deployment workflow.
- [x] Add post-deploy health smoke-test script.
- [x] Add rollback procedure using previous image tag in `docs/Release_Runbook.md`.

Verification:

- Main branch cannot merge if tests/scans fail.
- Staging deploy runs from CI, not manual local build.
- Production release references an exact image digest or SHA tag.
- Rollback can be executed and verified in staging.

Current status: **partially implemented**. CI validation, scanning, Docker build validation, Dependabot, smoke-test script, and release runbook are in place. Registry push and staging deployment workflows remain pending because they require target registry, environment, and deployment credentials.

## Phase 3 - Durable Runtime State

Target: **Week 2-3**

Goal: make the API stateless and safe to horizontally scale.

Tasks:

- [x] Move auth sessions to Redis or MongoDB with TTL.
- [x] Move failed login attempts and account lockouts to Redis.
- [x] Replace in-memory rate limiter with Redis-backed token bucket/sliding window.
- [x] Replace in-memory cache for shared data with Redis.
- [ ] Move general background jobs from `asyncio.Queue` to a durable queue.
- [x] Split contract ingestion deployment into:
  - API service
  - contract ingestion worker service
- [ ] Split document worker and scheduler services after general background jobs become durable.

Verification:

- Two API replicas share login/session/rate-limit state.
- Restarting one API replica does not invalidate all sessions.
- Submitted background jobs survive API restart.
- Queue depth and failed jobs are visible.

Current status: **partially implemented**. Sessions, lockouts, rate limits, and cache are Redis-backed when runtime Redis is configured. Contract queue workers can run separately from API containers. General document background jobs still need a named durable job queue before they can survive API restarts.

## Phase 4 - Upload and Storage Performance

Target: **Week 3-5**

Goal: support many concurrent users uploading large documents/contracts without API lag.

Tasks:

- [x] Refactor general document upload to stream chunks instead of full `file.read()`.
- [x] Refactor contract multipart upload to stream content while hashing.
- [x] Refactor chunk finalization to avoid full `read_bytes()` of merged file.
- [x] Compute SHA-256 incrementally during upload.
- [x] Remove unnecessary duplicate processing copies where workers can use `file_object_id`.
- [ ] Add upload idempotency keys.
- [x] Add per-user and per-org concurrent upload limits.
- [ ] Add upload progress events through Redis-backed WebSocket/SSE.
- [ ] Add direct-to-S3 multipart upload design and implementation.

Verification:

- Multiple concurrent large uploads do not spike API memory linearly.
- Upload cancellation and retry are safe.
- Chunked uploads can resume.
- Duplicate upload requests do not create inconsistent document/version records.
- Upload latency and failure rate are measurable.

Current status: **partially implemented**. Existing API-compatible upload paths now use bounded-memory spooling and path-based storage writes. Direct-to-S3 multipart upload, resumable manifests, idempotency keys, and live upload progress events remain as long-term improvements.

## Phase 5 - Database Production Hardening

Target: **Week 4-6**

Goal: make MongoDB reliable, recoverable, and performant under tenant-scoped load.

Tasks:

- [x] Move to MongoDB Atlas or production replica set.
- [ ] Enable authentication, TLS, PITR-equivalent backup workflow, monitoring, and alerts.
- [x] Review high-volume document/contract/upload queries and add compound indexes.
- [ ] Add slow query logging dashboard.
- [x] Add initial data retention policy for audit events, temp jobs, sessions, and upload sessions.
- [ ] Add idempotent migration/backfill execution strategy.
- [ ] Avoid unbounded query result loading in request paths.
- [x] Add self-managed replica-set scaffolding for deployments that will not use MongoDB Atlas.
- [x] Add MongoDB logical backup and restore scripts.

Verification:

- Backup restore drill succeeds.
- Slow queries are visible.
- Query plans use expected indexes.
- Load test confirms acceptable P95/P99 latency for common document queries.

Current status: **partially implemented**. The application now enforces production replica-set configuration, uses hardened MongoDB client settings, creates expanded indexes with retry, and includes self-managed replica-set plus backup/restore scaffolding. Operational completion still requires deploying the replica set with auth/TLS, scheduling off-host backups, running a restore drill, adding monitoring/alerts, and removing remaining unbounded request-path query loads.

## Phase 6 - Observability and Incident Response

Target: **Week 5-7**

Goal: operators can detect, diagnose, and recover from production issues.

Tasks:

- [x] Add Prometheus or OpenTelemetry metrics.
- [ ] Add tracing for API requests, Mongo operations, S3 calls, queue jobs, OCR, and embeddings.
- [ ] Add Sentry or equivalent exception monitoring.
- [ ] Add dashboards for:
  - request rate/error rate/latency
  - upload throughput and failures
  - queue depth and dead-letter count
  - Mongo slow queries
  - worker CPU/memory
  - S3 errors and latency
- [ ] Add alerting for critical thresholds.
- [x] Create incident runbooks.

Verification:

- Synthetic failure is visible in dashboards.
- Alerts fire for queue backlog, high error rate, and readiness failure.
- Runbooks identify owner, rollback step, and recovery step.

Current status: **partially implemented**. The backend exposes `/metrics` and `/health/observability`, records request latency/error metrics, logs slow requests, and counts document audit events. Production completion still requires wiring metrics into Prometheus/Grafana or equivalent, adding alerts, adding distributed tracing/exception monitoring, and creating dashboards for queues, MongoDB, S3, uploads, and workers.

## Phase 7 - Collaboration Safety and Auditability

Target: **Week 6-8**

Goal: make multi-user document workflows safe, auditable, and predictable.

Tasks:

- [x] Add optimistic concurrency to document metadata updates.
- [x] Return conflict errors when stale clients update documents.
- [ ] Add document edit locks where needed.
- [x] Expose document version/audit history to authorized users.
- [x] Ensure major create/update/delete/download/upload/comment events write immutable audit events.
- [ ] Add field-level merge strategy for metadata where practical.
- [x] Add focused tests for concurrent updates.
- [ ] Add end-to-end tests for concurrent uploads.

Verification:

- Two users editing the same document cannot silently overwrite each other.
- Audit trail reconstructs document lifecycle.
- Concurrent upload/update tests pass.

Current status: **partially implemented**. Document update/delete now support optional revision headers and return 409 for stale writes. Existing clients remain backward compatible when they do not send revision headers. Document audit history is exposed through a read-authorized endpoint. Remaining work is frontend conflict UX, field-level merge strategy, optional edit locks, and broader end-to-end concurrent upload/update tests.

## Phase 8 - Production Load Testing and Launch Readiness

Target: **Week 8+**

Goal: validate the system under realistic load before production cutover.

Tasks:

- Define target concurrency and data volume assumptions.
- Run load tests for:
  - login
  - document list/search
  - single document upload
  - bulk upload
  - contract chunked upload
  - contract ingestion queue
  - download
- Measure P50/P95/P99 latency.
- Measure API memory under concurrent large uploads.
- Test rolling deploy during active uploads.
- Test worker crash and job retry.
- Test Mongo primary failover if using replica set.
- Perform disaster recovery restore drill.

Verification:

- Load test meets agreed SLOs.
- No data loss during deploy/restart tests.
- Rollback succeeds.
- Backup restore succeeds.
- Production launch checklist is signed off.

## Quick Wins

- Add working active backend health endpoints.
- Enforce Redis-backed rate limiting.
- Rotate exposed credentials.
- Add secret scanning to CI.
- Stop exposing internal data service ports.
- Add request ID logging.
- Split contract workers from API runtime.
- Stream uploads to disk/object storage in chunks.

## Long-Term Improvements

- Direct-to-S3 multipart uploads.
- Managed MongoDB replica set with PITR.
- Kubernetes/ECS deployment with rolling updates and autoscaling.
- Durable distributed queue for all background jobs.
- Full OpenTelemetry tracing.
- Optimistic concurrency and collaboration conflict handling.
- Formal document retention, archive, and lifecycle policies.

## Production Readiness Exit Criteria

The repository should not be considered production-ready until all of the following are true:

- No real secrets exist in the repository or image layers.
- CI blocks merges on test, lint, type, secret, dependency, and container scan failures.
- `/health/live` and `/health/ready` are accurate and used by deployment.
- API containers are stateless and horizontally scalable.
- Sessions, rate limits, cache, and jobs are durable/distributed.
- Uploads are streamed and safe under concurrent large-file load.
- MongoDB is replicated, backed up, monitored, and secured.
- Observability dashboards and alerts exist.
- Rollback procedure is tested.
- Concurrent document update behavior is explicitly controlled.
