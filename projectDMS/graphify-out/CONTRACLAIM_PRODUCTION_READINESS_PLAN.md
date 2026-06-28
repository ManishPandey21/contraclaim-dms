# Production Readiness Plan for ContraClaim DMS

Repository: `ManishPandey21/contraclaim-dms`  
Target outcome: raise production readiness from **6.3/10** to **9/10**.

## Current vs Target Score

| Area | Current | Target |
|---|---:|---:|
| Backend architecture | 7.5/10 | 9/10 |
| Frontend integration | 6/10 | 9/10 |
| Auth/RBAC/security | 7/10 | 9/10 |
| Documents/contracts/RAG | 7/10 | 9/10 |
| Razorpay/payments | 4.5/10 | 9/10 |
| Deployment/ops | 6.5/10 | 9/10 |
| Overall | 6.3/10 | 9/10 |

## Phase 1: Critical Payment Completion

Goal: make billing safe enough for real customers.

### Key Actions

- Wire `client/src/services/billing-api.ts` into `client/src/pages/SubscriptionManagementPage.tsx`.
- Allow users to start Razorpay subscription checkout directly from the subscription UI.
- Add payment success, pending, failure, and retry screens after Razorpay checkout return.
- Add backend Razorpay order APIs for one-time payments:
  - `POST /api/rbac-monetization/orders`
  - `POST /api/rbac-monetization/orders/{order_id}/verify`
  - `GET /api/rbac-monetization/orders/{order_id}`
- Keep signed Razorpay webhooks as the source of truth for payment state.
- Add browser-side payment verification for checkout return and order payments.
- Add receipt and invoice download APIs backed by `billing_records`.
- Add admin billing screens for subscriptions, billing records, webhook events, failed payments, usage, and invoice/receipt status.
- Make webhook processing idempotent, auditable, replay-safe, and visible to admins.

### Acceptance Criteria

- User can select a plan, create subscription checkout, complete payment, return to app, and see an active subscription.
- Failed payments show clear retry or update-payment-method actions.
- Admin can inspect webhook events, billing records, failed payments, and receipts.
- Duplicate webhook events do not duplicate subscriptions, usage, or billing records.

Expected score after phase: **7.1/10**

## Phase 2: API and Frontend Contract Closure

Goal: eliminate backend/frontend drift and unlinked critical features.

### Key Actions

- Generate OpenAPI from FastAPI during CI.
- Validate frontend service paths against backend OpenAPI.
- Add a typed frontend API contract layer for billing, documents, contracts, users, storage, and RAG endpoints.
- Review and classify all 77 APIs without obvious frontend references as:
  - frontend-used
  - backend-only
  - ops-only
  - deprecated
  - missing frontend workflow
- Integrate high-priority contract appraisal APIs into the contract appraisal/register UI.
- Integrate document vector search and document audit events into document/admin views.
- Integrate user lock/unlock and `/api/users/me` into admin/settings workflows.
- Expose RAG search, agent, and observability endpoints in an ops-gated AI diagnostics page.
- Keep webhooks backend-only, but expose webhook event records in admin billing diagnostics.
- Add consistent frontend handling for JSON errors, blob/download errors, auth expiry, permission denial, validation errors, and step-up-required responses.
- Deprecate or document duplicate APIs:
  - role APIs split between `roles.py` and `permissions.py`
  - legacy `/api/token` and `/api/logout`
  - legacy/new email route split

### Acceptance Criteria

- CI fails when frontend service routes do not match backend OpenAPI.
- Every mounted backend route is classified and documented.
- No critical or high backend feature remains without a frontend workflow or explicit backend-only rationale.

Expected score after phase: **7.8/10**

## Phase 3: Auth, RBAC, and Security Hardening

Goal: make authorization, tenancy, and secret handling production-grade.

### Key Actions

- Generate a backend/frontend permission parity report in CI.
- Require backend permission checks for all admin, billing, document, contract, RAG, storage, and user-management mutations.
- Add route-level tests for:
  - missing permissions
  - wrong tenant scope
  - project/org mismatch
  - expired session
  - CSRF failure
  - missing step-up
- Harden production config:
  - `AUTH_COOKIE_SECURE=true`
  - strict JWT secret validation
  - `CLAMAV_FAIL_OPEN=false`
  - strong Razorpay webhook secret when Razorpay is enabled
  - Mongo replica set required
  - production CORS allowlist required
- Add audit logging for:
  - billing changes
  - role and permission changes
  - user lock/unlock
  - document export/download
  - contract appraisal export
  - RAG admin actions
  - storage repair
- Remove secrets from logs.
- Ensure request IDs flow through backend logs and frontend error reports.

### Acceptance Criteria

- Security tests prove cross-tenant access fails.
- Sensitive mutations require RBAC and step-up where appropriate.
- Production config validation blocks insecure deployment.
- Audit events exist for all high-risk actions.

Expected score after phase: **8.3/10**

## Phase 4: Documents, Contracts, and AI/RAG Reliability

Goal: make document processing and AI workflows observable, recoverable, and supportable.

### Key Actions

- Add user/admin status views for:
  - document OCR
  - antivirus scan
  - S3/local storage
  - vector indexing
  - contract ingestion
  - RAG jobs
  - graph sync
- Add retry, cancel, and reprocess controls for:
  - failed document processing
  - failed contract ingestion
  - failed vector sync
  - failed contract appraisal jobs
- Add RAG diagnostics showing:
  - source documents
  - citations
  - retrieval scores
  - model used
  - token/error metadata
  - failed run reason
- Surface contract appraisal citations, obligations, risks, and key dates in the contract appraisal UI.
- Add safeguards for:
  - missing vectors
  - failed embeddings
  - stale chunks
  - graph outage
  - Qdrant outage
  - OpenAI outage
  - worker queue delay
- Move or remove `client/src/pages/bulk-upload-service-fixed.py` after confirming no runtime usage.

### Acceptance Criteria

- A failed upload, processing, or RAG job shows clear status and retry path.
- Admin can reconcile storage/vector state without direct database access.
- Contract appraisal outputs include visible citations and editable obligation/risk/key-date registers.
- AI/RAG failures are logged, traceable, and do not leave users stuck in silent pending states.

Expected score after phase: **8.7/10**

## Phase 5: Testing, CI, and Release Gates

Goal: make regressions hard to ship.

### Key Actions

- Add backend tests for:
  - Razorpay subscription checkout
  - order creation and verification
  - webhook signature variants
  - duplicate webhook idempotency
  - failed/halted subscription transitions
  - invoice/billing reconciliation
  - RBAC and tenant isolation
- Add frontend tests for:
  - subscription checkout redirect
  - payment success/failure/retry
  - invoice preview/download
  - admin billing screens
  - permission-denied and step-up flows
  - document processing failure/retry
  - RAG diagnostics and contract appraisal registers
- Add E2E happy paths:
  - register/login
  - upload document
  - process document
  - ingest contract
  - run appraisal/RAG
  - create subscription payment
  - admin verifies billing/webhook state
- Add CI release gates for:
  - backend tests
  - frontend tests
  - typecheck
  - production build
  - dependency audit
  - container build
  - OpenAPI contract check
  - permission parity check
  - production config validation

### Acceptance Criteria

- Main branch cannot merge if API contract, RBAC parity, payment tests, or build checks fail.
- Payment and document/RAG critical flows have automated coverage.
- Test fixtures include tenant isolation and realistic billing states.

Expected score after phase: **9.0/10**

## Phase 6: Deployment, Observability, and Production Runbooks

Goal: make the system operable after launch.

### Key Actions

- Create production runbooks for:
  - deploy/rollback
  - secret rotation
  - Razorpay webhook recovery
  - failed payment recovery
  - Mongo backup/restore
  - Qdrant/FalkorDB backup/restore
  - worker queue recovery
  - ClamAV outage
  - OpenAI/RAG outage
- Add health dashboards for:
  - backend
  - worker
  - Mongo
  - Redis
  - Qdrant
  - FalkorDB
  - Graphiti
  - ClamAV
  - S3/local storage
  - Razorpay webhook latency
  - queue depth
- Add alerting for:
  - failed webhooks
  - stuck ingestion jobs
  - vector reconciliation drift
  - failed payments
  - auth spikes
  - 5xx rates
  - slow API routes
  - storage errors
- Verify production ingress for `/api`, `/api/v1`, `/health`, and `/ws`.
- Run staging load and smoke tests before production cutover.

### Acceptance Criteria

- A fresh production deployment can be validated from documented steps.
- Rollback and backup restore are tested in staging.
- Critical operational failures generate alerts with actionable runbook links.
- Production readiness score remains **9/10** after deployment drills pass.

Expected score after phase: **9.0/10**

## Public API and Interface Additions

- Add Razorpay order APIs for one-time payments and verification.
- Add receipt/invoice download APIs if not already present behind `billing_records`.
- Add admin-readable webhook event and billing diagnostics APIs if existing records are not exposed cleanly.
- Add OpenAPI artifact generation in CI.
- Add frontend route contract checks.
- Add permission parity output as a CI artifact.

## Test Plan

Required test categories:

- Unit tests for payment gateway adapters, webhook parsing, subscription state transitions, RBAC policy checks, and config validation.
- Integration tests for billing records, webhooks, document processing, contract ingestion, vector sync, and RAG responses.
- Frontend component/service tests for billing, admin screens, document processing states, contract appraisal, and permission handling.
- E2E tests for login, upload, process, appraise/RAG, subscribe/pay, receipt, and admin billing verification.
- Deployment tests for production config, health endpoints, proxy routes, background workers, and backup/restore drills.

## Assumptions and Defaults

- Razorpay subscriptions are the primary billing model.
- Razorpay orders are implemented only for one-time payments, not as a replacement for subscriptions.
- Backend remains the source of truth for payment state through signed webhooks.
- Frontend payment return verification is added for UX confirmation, not as the sole trusted payment authority.
- Backend RBAC remains authoritative; frontend guards are only navigation and UX helpers.
- Unused duplicate APIs are deprecated before removal unless tests prove no consumer exists.
- The 9/10 score requires all critical and high items to be complete, tested, documented, and verified in staging.
