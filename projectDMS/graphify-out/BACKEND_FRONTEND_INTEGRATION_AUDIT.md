# Backend, Frontend Integration, and Razorpay Audit

Repository: `ManishPandey21/contraclaim-dms`
Branch audited: `main`
Audit date: 2026-06-18
Scope: backend architecture, API contracts, database usage, auth, RBAC, file handling, document processing, AI/RAG, Razorpay payments, frontend integration, testing, security, and deployment readiness.

## Executive Summary

ContraClaim DMS has a substantial FastAPI backend, React/Vite frontend, tenant-aware RBAC services, document upload/storage pipelines, contract ingestion, AI/RAG endpoints, health checks, CI, and a first-pass monetization subsystem. The direction is sound, especially around cookie auth with CSRF, scoped retrieval endpoints, immutable file storage, and CI security scans.

The system is not production-ready for paid SaaS operation yet. The largest blocker is the Razorpay/payment flow: the backend has gateway abstractions, subscription checkout, and webhook reconciliation, but the frontend does not call checkout, there is no order/payment verification API, Razorpay plan IDs are conflated with internal plan codes, local subscription lifecycle actions bypass the gateway, invoice/receipt handling is incomplete, and webhook validation is too shallow for financial correctness.

The second major risk area is authorization consistency. `PolicyService`, `ScopeService`, and entitlement checks exist, but some older routers still use legacy scope helpers, role/permission routers are duplicated, platform permissions are not clearly seeded, and bulk-upload status lacks ownership checks. This creates a mixed authorization model where some paths are much stronger than others.

## Production Readiness Score

Overall score: 56 / 100

| Area | Score | Notes |
| --- | ---: | --- |
| Backend architecture | 68 | Good modular FastAPI structure, but legacy/simple routers and mixed patterns remain. |
| Authentication/session security | 72 | HttpOnly cookie, CSRF, refresh, logout/session checks exist. TTL/proxy/IP/JWT hardening gaps remain. |
| Authorization/RBAC/entitlements | 58 | Central policy service is strong, but duplicate role routes, fail-open defaults, and legacy checks reduce confidence. |
| Document/file handling | 63 | Streaming validation, storage abstraction, AV hook, and audit events exist. Bulk status and AV/default hardening gaps remain. |
| AI/RAG/document processing | 57 | Scoped retrieval engine is much stronger than older AI endpoints. Offline/fake fallbacks and ObjectId lookup risks need fixes. |
| Razorpay/payment readiness | 32 | Not ready for live billing. Checkout, plan mapping, lifecycle sync, invoices, verification, and failure handling are incomplete. |
| Frontend/backend contract alignment | 57 | Cookie/CSRF alignment is good. Payment checkout is missing and some document/contract endpoints are mismatched. |
| Testing/CI | 55 | CI exists with scans and some targeted tests. Payment, e2e, file, RAG, and deployment tests are not broad enough. |
| Deployment readiness | 48 | Docker stack exists, but exposed services, no TLS in compose, env drift, and payment env gaps block production use. |

## Architecture Snapshot

Backend entrypoint:
- `backend/rbac_backend/main.py`
- FastAPI app with `/api` routers for auth, users, documents, contracts, claims, letters, AI, retrieval, RBAC monetization, billing webhooks, reports, storage, health, and WebSocket routes.
- Startup validates runtime configuration, initializes MongoDB, runs index setup asynchronously, starts background services and contract queue workers depending on environment.

Core backend services:
- Auth/session/CSRF: `backend/rbac_backend/routers/auth.py`, `backend/rbac_backend/core/security.py`, `backend/rbac_backend/core/csrf.py`, `backend/rbac_backend/services/authentication_service.py`
- RBAC/policy: `backend/rbac_backend/core/permissions.py`, `backend/rbac_backend/services/permission_service.py`, `backend/rbac_backend/services/policy_service.py`, `backend/rbac_backend/services/scope_service.py`, `backend/rbac_backend/services/entitlement_service.py`
- Documents/files: `backend/rbac_backend/routers/documents.py`, `backend/rbac_backend/services/document_service.py`, `backend/rbac_backend/services/file_object_service.py`, `backend/rbac_backend/services/file_service.py`, `backend/rbac_backend/services/s3_service.py`, `backend/rbac_backend/services/antivirus_service.py`
- Contracts/RAG: `backend/rbac_backend/routers/contracts.py`, `backend/rbac_backend/routers/retrieval_engine.py`, `backend/rbac_backend/ingestion/pipeline.py`, `backend/rbac_backend/retrieval/service.py`
- Payments: `backend/rbac_backend/routers/rbac_monetization.py`, `backend/rbac_backend/routers/billing_webhooks.py`, `backend/rbac_backend/services/monetization_service.py`, `backend/rbac_backend/services/payment_gateway.py`, `backend/rbac_backend/services/billing_webhook_service.py`, `backend/rbac_backend/models/rbac_monetization.py`

Frontend entrypoint:
- `client/src/App.tsx`, `client/src/main.tsx`, `client/src/routes.tsx`
- API config: `client/src/config/api.ts`, `client/src/services/http.ts`, `client/src/services/api.ts`, `client/src/services/enhanced-api.ts`
- Payments/subscriptions: `client/src/pages/PlanSettingsPage.tsx`, `client/src/pages/SubscriptionManagementPage.tsx`, `client/src/services/plan-settings-api.ts`
- Auth/RBAC: `client/src/services/session-api.ts`, `client/src/services/auth.ts`, `client/src/hooks/useRBAC.ts`, `client/src/config/rolePermissions.ts`, `client/src/components/auth/ProtectedRoute.tsx`, `client/src/components/auth/RoleGuard.tsx`

## Critical Findings

### C1. Razorpay payment flow is not end-to-end production-ready

Evidence:
- Backend checkout route exists at `POST /api/rbac-monetization/subscriptions/checkout` in `backend/rbac_backend/routers/rbac_monetization.py`.
- Checkout logic in `backend/rbac_backend/services/monetization_service.py` creates a gateway customer and subscription, then inserts a local `pending` subscription.
- Razorpay adapter in `backend/rbac_backend/services/payment_gateway.py` calls `subscription.create({"plan_id": plan_code, ...})`.
- Internal plan codes in `backend/rbac_backend/services/monetization_service.py` are values like `dms_starter`, `dms_professional`, `contract_correspondence_desk`, and `claims_commercial_desk`, not Razorpay `plan_...` IDs.
- Frontend `client/src/services/plan-settings-api.ts` does not expose `subscriptions/checkout`.
- `client/src/pages/SubscriptionManagementPage.tsx` only calls local lifecycle endpoints such as `upgradeSubscription`, `downgradeSubscription`, `cancelSubscription`, `reactivateSubscription`, `changeBillingPeriod`, `addSubscriptionAddon`, `removeSubscriptionAddon`, and `getInvoicePreview`.

Risk:
- Live Razorpay subscription creation will likely fail unless internal plan codes happen to match Razorpay plan IDs.
- Even if checkout succeeds, the frontend has no user-facing checkout start, redirect, callback, payment status, or retry flow.
- Paid entitlements can diverge from actual Razorpay state.

Recommendation:
- Add explicit gateway fields to plans, for example `gateway_provider`, `gateway_plan_id_monthly`, `gateway_plan_id_quarterly`, `gateway_plan_id_annual`.
- Add frontend checkout screen/action that calls `POST /api/rbac-monetization/subscriptions/checkout` and handles the returned `checkout_url`.
- Add a post-checkout success/failure status page that polls backend subscription/payment state.
- Treat subscriptions as pending until verified by a valid Razorpay webhook or payment verification endpoint.

### C2. Subscription lifecycle endpoints mutate local state without calling Razorpay

Evidence:
- `MonetizationService.upgrade_subscription`, `downgrade_subscription`, `change_billing_period`, `cancel_subscription`, `reactivate_subscription`, `convert_trial`, `add_addon`, and `remove_addon` in `backend/rbac_backend/services/monetization_service.py` update MongoDB and history locally.
- Razorpay gateway methods exist for `cancel_subscription`, `reactivate_subscription`, `update_subscription`, and `create_invoice` in `backend/rbac_backend/services/payment_gateway.py`, but the service layer does not call them for lifecycle changes.
- `SubscriptionManagementPage.tsx` presents these actions as billing actions to the user.

Risk:
- A user/admin can cancel, upgrade, downgrade, reactivate, or add paid add-ons locally while Razorpay continues the old billing schedule.
- Entitlements may activate without payment, or payment may continue after local cancellation.

Recommendation:
- Convert lifecycle endpoints into commands that call Razorpay first or create a pending billing change record.
- Apply entitlement changes only after gateway confirmation or a controlled manual-billing approval.
- Add reconciliation jobs that compare local subscriptions with Razorpay subscription status.

### C3. Webhook reconciliation is signature-checked but financially incomplete

Evidence:
- Webhook route: `POST /api/billing/webhooks/{provider}` in `backend/rbac_backend/routers/billing_webhooks.py`.
- Signature verification exists in `RazorpayGateway.verify_webhook_signature`.
- `BillingWebhookService._reconcile` in `backend/rbac_backend/services/billing_webhook_service.py` matches only by `payment_gateway_subscription_id`.
- It sets subscription state and inserts `billing_records`, but does not validate amount, currency, expected plan, billing period, invoice ID, receipt ID, order ID, or customer ID against local expected values.
- `billing_webhook_events` unique index in `backend/rbac_backend/core/database.py` is on `event_id` only, not `(provider, event_id)`.
- Minimal event row is stored; raw payload is not retained in a secure audit table.

Risk:
- Incorrect or partial payment events can activate subscriptions.
- Provider event ID collisions across providers are possible.
- Financial audit trail is insufficient for dispute handling.

Recommendation:
- Store normalized and raw webhook payloads with redaction/encryption where needed.
- Make idempotency unique on `(provider, event_id)`.
- Validate subscription/customer/amount/currency/plan/period before activating entitlements.
- Store gateway invoice ID, payment ID, receipt URL, invoice URL, and failure reason.

### C4. Production deployment exposes stateful services in the compose stack

Evidence:
- `docker-compose.yml` exposes MongoDB `27017`, Qdrant `6333/6334`, FalkorDB `6379`, and Redis `6381` to the host.
- Mongo in compose has no username/password.
- Redis/FalkorDB have no password in compose defaults.
- Gateway `config/httpd.conf` listens on HTTP port 80 and sets HSTS despite TLS not being terminated in that container.

Risk:
- If this compose topology is used outside a protected local network, databases and queues are exposed.
- HSTS without actual TLS termination at this layer can create false confidence.

Recommendation:
- For production, remove host port exposure for Mongo, Redis, FalkorDB, and Qdrant.
- Require authenticated MongoDB and Redis/FalkorDB passwords.
- Put TLS termination at a real edge proxy/load balancer and document that boundary.
- Add a production compose/Kubernetes manifest separate from local development.

## High Priority Findings

### H1. Payment configuration is not enforced for Razorpay production

Evidence:
- Payment settings exist in `backend/rbac_backend/core/config.py`: `PAYMENT_PROVIDER`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`.
- `validate_runtime_configuration()` enforces many production settings but does not require Razorpay keys when `PAYMENT_PROVIDER=razorpay`.
- `.env.example` does not document `PAYMENT_PROVIDER` or Razorpay keys.
- Default `PAYMENT_PROVIDER` is `noop`.

Risk:
- Production could silently run with no-op billing or missing webhook secrets.

Recommendation:
- If `PAYMENT_PROVIDER=razorpay` in production, fail startup unless key ID, key secret, webhook secret, and gateway plan mappings are present.
- Add payment variables to `.env.example`.
- Add readiness checks for payment provider configuration.

### H2. No Razorpay order creation or payment signature verification API exists

Evidence:
- Backend uses Razorpay subscriptions and webhook verification, but no endpoint was found for:
  - Razorpay order creation
  - `razorpay_payment_id`, `razorpay_order_id`, `razorpay_signature` verification
  - frontend payment callback reconciliation
- Frontend has no Razorpay script/component or success/failure callback screen.

Risk:
- If the product intends to use Razorpay Checkout order flow, it is missing.
- If the product intends to use hosted subscription short URLs only, frontend still needs checkout start, return, status, failure, and retry screens.

Recommendation:
- Decide between Razorpay subscription hosted checkout and order-based checkout.
- Implement the chosen flow fully and remove unused assumptions.
- For hosted subscription checkout, add return URL/status handling and webhook-only entitlement activation.
- For order checkout, add server-side order creation and signature verification.

### H3. Duplicate `/api/roles` routes and unclear platform permissions

Evidence:
- `backend/rbac_backend/main.py` includes both `roles.router` and `permissions.router`.
- `backend/rbac_backend/routers/roles.py` defines `/roles`.
- `backend/rbac_backend/routers/permissions.py` also defines `/roles`.
- Both files are labeled simplified routers to get the UI working.
- Role mutations call `PolicyService.authorize(..., "platform.role.manage")`.
- Permission mutations call `PolicyService.authorize(..., "platform.permission.manage")`.
- `backend/rbac_backend/core/permissions.py` has many defaults, but these platform permission names are not clearly aligned with default seeded roles.

Risk:
- FastAPI route ordering and OpenAPI output can be confusing or inconsistent.
- Frontend `client/src/services/enhanced-api.ts` and `client/src/hooks/useRBAC.ts` depend on `GET /api/roles`; duplicate implementations increase regression risk.
- Scoped org/project role administration may be impossible if `platform.role.manage` is not granted.

Recommendation:
- Consolidate roles into one router.
- Consolidate permissions into one router.
- Seed and document all platform permissions used by backend policy checks.
- Add route contract tests for `GET/POST/PUT/DELETE /api/roles` and `/api/permissions`.

### H4. Bulk upload job status can leak cross-tenant job information

Evidence:
- `DocumentController.get_bulk_upload_status` in `backend/rbac_backend/routers/documents.py` fetches by `job_id` and returns it.
- The function comment says only "basic security check"; no actual user/org/project ownership validation is performed before returning status.
- Frontend `client/src/pages/UploadPage.tsx` polls `enhancedApi.getBulkUploadStatus(response.job_id)`.

Risk:
- Anyone with a valid session and a guessed/leaked `job_id` can read bulk-upload progress and filenames/errors from another tenant.

Recommendation:
- Store `organization_id`, `project_id`, and `created_by` on bulk upload jobs.
- Authorize status reads through `PolicyService.authorize` using the job scope.
- Return 404 instead of 403 where appropriate to avoid job ID probing.

### H5. Ingestion pipeline likely fails for normal Mongo ObjectId document IDs

Evidence:
- `IngestionPipeline._load_document` in `backend/rbac_backend/ingestion/pipeline.py` queries `{"_id": document_id}` and then `{"id": document_id}`.
- It does not convert ObjectId-shaped strings into `ObjectId(document_id)`.
- Normal document records created by Mongo commonly use ObjectId `_id`.

Risk:
- `/api/v1/ingestion/jobs` may create jobs that fail at processing with "Document not found".
- RAG indexing can silently fail for real uploaded documents.

Recommendation:
- Use the same lookup helper pattern used elsewhere, trying `ObjectId(value)` when valid.
- Add ingestion tests for ObjectId-backed documents.

### H6. AI/RAG has silent degraded-output fallbacks

Evidence:
- `EmbeddingClient` in `backend/rbac_backend/retrieval/embeddings.py` falls back to deterministic 64-dimensional embeddings when OpenAI is unavailable.
- `LLMGenerator` in `backend/rbac_backend/retrieval/generator.py` returns a deterministic "Context-based draft" fallback if LLM generation fails.
- Qdrant vector size defaults to 1536 in config/env, while fake embeddings are 64 dimensions.

Risk:
- Production can return low-quality or misleading AI answers instead of failing visibly.
- Vector writes can fail due to dimension mismatch.

Recommendation:
- In production, fail closed when embeddings or LLM generation are unavailable.
- Keep deterministic fallbacks test/dev-only.
- Add a readiness check for embedding model dimension vs Qdrant collection dimension.

### H7. Older AI assistant endpoints bypass central policy/entitlement checks

Evidence:
- `backend/rbac_backend/routers/ai_assistant.py` uses legacy `authorize_scope` from `core/security.py`.
- Newer retrieval endpoints in `backend/rbac_backend/routers/retrieval_engine.py` use `PolicyService` and require non-empty org/project scope.
- `get_langgraph_run(letter_id)` authorizes against the current user's default org/project, not the actual letter/run scope before fetching.

Risk:
- Drafting and AI features may bypass subscription entitlement enforcement.
- Letter run metadata may be exposed if the service layer does not re-check scope.

Recommendation:
- Migrate `ai_assistant.py` routes to `PolicyService.authorize`.
- Resolve the target letter/run first, then authorize against its actual organization/project.
- Add cross-tenant tests for all AI and LangGraph routes.

### H8. Contract/document frontend has endpoint mismatches

Evidence:
- `client/src/services/documents-api.ts` calls `GET /api/contracts/download` with `upload_id` or `file_path`.
- `backend/rbac_backend/routers/contracts.py` exposes `GET /api/contracts/{document_id}/download`.
- `backend/rbac_backend/routers/documents.py` exposes `GET /api/documents/{id}/download`.
- `client/src/pages/DocumentsPage.tsx` has a reference row navigation to `/document/${ref.id}`, but `client/src/routes.tsx` defines `/documentviewer/:id`, `/reference/:id`, and no `/document/:id`.

Risk:
- Downloads and reference navigation can fail in production UI paths.

Recommendation:
- Remove or fix stale client methods.
- Add frontend integration tests for document download, contract download, references, and viewer navigation.

### H9. Entitlement model has fail-open and add-on inconsistencies

Evidence:
- `RBAC_ENTITLEMENT_FAIL_OPEN` defaults to `true` in `backend/rbac_backend/core/config.py`.
- Production validation requires it to be false, which is good, but local/non-production behavior can mask missing subscription data.
- `EntitlementService` checks active subscriptions and plan features, but add-on features are not consistently merged into `check_permission_entitlement` based on inspected flow.
- Frontend hides drafting actions using `GET /api/rbac-monetization/plan-settings/effective-services`, but backend must remain authoritative.

Risk:
- Missing subscription records can accidentally allow service usage in non-production and tests.
- Paid add-on features can appear in UI but not authorize consistently, or vice versa.

Recommendation:
- Add unit tests for every billed feature and add-on.
- Make entitlement fail-open test-only, not a normal development default.
- Ensure effective features and authorization checks use the same merge logic.

### H10. File validation and antivirus defaults are not production-safe

Evidence:
- `ANTIVIRUS_ENABLED=false` and `CLAMAV_FAIL_OPEN=true` defaults in `backend/rbac_backend/core/config.py`.
- `docker-compose.yml` includes a `clamav` service, but backend defaults do not enable it.
- `validate_spooled_upload` and `utils/file_validation.py` perform magic-byte checks, but DOCX validation relies heavily on extension plus ZIP signature.
- Bulk upload persists uploaded files via `BulkUploadService.persist_upload_files` before each file is individually validated by `_process_single_file`.

Risk:
- Malicious or malformed files can be persisted temporarily.
- Production deployments can accidentally run without AV scanning.

Recommendation:
- Enable AV in production and set `CLAMAV_FAIL_OPEN=false`.
- Validate file type before durable temp persistence where feasible.
- Deep-validate DOCX ZIP structure.
- Add malware/EICAR and malformed-file tests.

## Medium Priority Findings

### M1. Auth token TTL ignores configuration

Evidence:
- `ACCESS_TOKEN_EXPIRE_MINUTES` exists in `backend/rbac_backend/core/config.py`.
- `AuthController.login_user` and `refresh_token` in `backend/rbac_backend/routers/auth.py` hardcode `timedelta(minutes=60)` and `expires_in=3600`.

Risk:
- Operators cannot shorten/extend token lifetime through config.

Recommendation:
- Use `settings.ACCESS_TOKEN_EXPIRE_MINUTES` consistently.

### M2. Login rate limiting uses direct client IP

Evidence:
- `POST /api/login` path derives `client_ip` from `request.client.host` in `backend/rbac_backend/routers/auth.py`.

Risk:
- Behind a proxy or gateway, all users may share one IP bucket unless proxy handling is configured elsewhere.

Recommendation:
- Add trusted proxy middleware/configuration and consume forwarded headers only from trusted proxies.

### M3. JWT validation lacks issuer/audience checks

Evidence:
- `get_current_user` in `backend/rbac_backend/core/security.py` decodes HS256 tokens using secret and algorithm.
- Token type is checked, but no issuer or audience claim is validated.

Risk:
- Same-secret tokens from adjacent internal systems could be accepted.

Recommendation:
- Add `iss` and `aud` claims and validate them.

### M4. Mongo index creation is asynchronous and non-fatal

Evidence:
- `connect()` in `backend/rbac_backend/core/database.py` starts `ensure_indexes_with_retry` as a background task.
- After retries, `ensure_indexes_with_retry` logs and returns without failing startup.

Risk:
- Unique constraints and TTL indexes may be missing while the app serves traffic.
- Payment webhook idempotency, share-token uniqueness, and job uniqueness can be weaker than expected.

Recommendation:
- For production, run indexes/migrations as a blocking deployment step.
- Make critical unique indexes mandatory before readiness succeeds.

### M5. Database lacks key uniqueness constraints for subscriptions

Evidence:
- `backend/rbac_backend/core/database.py` indexes subscriptions on `(organization_id, project_id, package_id, status)` but not as unique.
- No unique active subscription constraint per org/project/family/plan was found.

Risk:
- Duplicate active subscriptions can exist for one scope.
- Entitlement resolution may choose an arbitrary active subscription.

Recommendation:
- Define the business invariant and enforce it with partial unique indexes where MongoDB supports it.

### M6. Error handling is inconsistent and sometimes leaks internals

Evidence:
- Several routers catch broad `Exception` and return generic 500, while others include `str(e)` in response details, for example parts of `backend/rbac_backend/routers/permissions.py`.
- `billing_webhooks.py` does not wrap unknown provider `ValueError` into a clean 400.

Risk:
- Clients receive inconsistent error shapes.
- Some internal details may leak.

Recommendation:
- Standardize error response schema across backend.
- Convert known service exceptions to typed HTTP errors.

### M7. Frontend uses multiple API wrapper styles

Evidence:
- `client/src/services/api.ts` uses Axios with cookie/CSRF.
- `client/src/services/enhanced-api.ts` uses `authenticatedFetch`.
- Some helper exports in `enhanced-api.ts` use raw `axios.post/get/put/delete(joinApiUrl(...))` rather than the shared `api` client.

Risk:
- CSRF, credentials, refresh, timeout, and error handling can diverge by feature.

Recommendation:
- Route all API calls through one or two approved clients only.
- Add lint or wrapper tests to prevent raw Axios/fetch calls for authenticated APIs.

### M8. S3 encryption settings appear undocumented in typed settings

Evidence:
- `backend/rbac_backend/services/s3_service.py` references optional encryption/KMS-style settings via `getattr(settings, ...)`.
- These fields were not present in the inspected `Settings` model in `backend/rbac_backend/core/config.py`.

Risk:
- Operators may set S3 encryption env vars that are ignored by Pydantic because `extra="ignore"`.

Recommendation:
- Add explicit settings for S3 SSE/KMS configuration and document them in `.env.example`.

### M9. Root/backend dependency manifests are inconsistent

Evidence:
- Root `package.json` has unrelated dependencies (`index`, `llama`).
- `backend/requirements.txt` and `backend/rbac_backend/requirements.txt` diverge.
- Docker uses `backend/rbac_backend/requirements.txt`.

Risk:
- Developers and CI can install different dependency graphs.

Recommendation:
- Remove stale root package metadata or document its purpose.
- Keep a single backend dependency source or generate one from the other.

## Positive Findings

- `client/src/services/http.ts` and `client/src/services/api.ts` align well with backend cookie auth. They strip stale Authorization headers, send credentials, add `X-CSRF-Token`, and retry once through `/api/refresh`.
- `backend/rbac_backend/core/csrf.py` implements signed double-submit CSRF for unsafe cookie-authenticated requests.
- `backend/rbac_backend/routers/retrieval_engine.py` requires non-empty organization/project scope and re-authorizes contract QA against the resolved document scope.
- `backend/rbac_backend/services/upload_streaming.py` enforces streaming size limits and MIME validation before general document storage.
- `backend/rbac_backend/services/file_object_service.py` centralizes immutable file objects and validates local download paths through `assert_local_path_allowed`.
- `backend/rbac_backend/routers/health.py` has liveness/readiness endpoints and protects detailed observability data in production.
- `.github/workflows/ci.yml` includes secret scanning, backend tests, frontend lint/tests/build, dependency scans, and Docker image scans.
- Tests exist for tenant isolation and billing webhook idempotency/signatures: `backend/rbac_backend/tests/test_tenant_isolation.py`, `backend/rbac_backend/tests/test_billing_webhooks.py`.

## Backend/API Review

### Authentication and session management

Relevant files:
- `backend/rbac_backend/routers/auth.py`
- `backend/rbac_backend/core/security.py`
- `backend/rbac_backend/core/csrf.py`
- `client/src/services/session-api.ts`
- `client/src/services/auth.ts`
- `client/src/services/http.ts`

Assessment:
- Strong direction: HttpOnly auth cookie, CSRF cookie/header, refresh route, logout, session invalidation through runtime state, and frontend stripping legacy Authorization headers.
- Gaps: hardcoded 60-minute token TTL, no JWT issuer/audience, proxy IP handling not explicit, and `LoginResponse` still returns `access_token` even though the frontend avoids storing it.

Recommended actions:
- Use configured token TTL.
- Add issuer/audience claims.
- Keep returning token only if non-browser/API consumers require it; otherwise consider a browser-specific response without bearer token.
- Add trusted proxy handling for login rate limits.

### Authorization, RBAC, and tenant isolation

Relevant files:
- `backend/rbac_backend/services/policy_service.py`
- `backend/rbac_backend/services/scope_service.py`
- `backend/rbac_backend/services/permission_service.py`
- `backend/rbac_backend/services/entitlement_service.py`
- `backend/rbac_backend/routers/roles.py`
- `backend/rbac_backend/routers/permissions.py`
- `client/src/hooks/useRBAC.ts`
- `client/src/config/rolePermissions.ts`

Assessment:
- The central `PolicyService` is the right architecture because it combines permission, scope, entitlement, and audit decisions.
- The codebase still has older authorization paths and duplicated routers.
- Frontend route checks are useful UX only, but backend must be the source of truth.

Recommended actions:
- Make every protected backend route go through `PolicyService` or a clearly equivalent dependency.
- Remove duplicate role routes.
- Add route-level tests for all route permissions listed in `client/src/config/rolePermissions.ts`.
- Document canonical permissions and legacy aliases.

### Database usage

Relevant files:
- `backend/rbac_backend/core/database.py`
- `backend/rbac_backend/models/rbac_monetization.py`
- `backend/rbac_backend/ingestion/pipeline.py`
- `backend/rbac_backend/services/file_object_service.py`

Assessment:
- MongoDB indexing is extensive and covers many tenant/filter paths.
- Production replica-set validation is present.
- Index creation being background/non-fatal is risky for unique constraints.
- Several lookup paths mix string IDs and ObjectIds.

Recommended actions:
- Add a migration/index command that blocks deployment until critical indexes exist.
- Normalize ID lookup helpers and use them across ingestion, file objects, AI, documents, and contracts.
- Add partial unique indexes for active subscriptions once business rules are finalized.

### File handling and document processing

Relevant files:
- `backend/rbac_backend/routers/documents.py`
- `backend/rbac_backend/routers/contracts.py`
- `backend/rbac_backend/services/file_object_service.py`
- `backend/rbac_backend/services/file_service.py`
- `backend/rbac_backend/services/upload_streaming.py`
- `backend/rbac_backend/utils/file_validation.py`
- `backend/rbac_backend/services/antivirus_service.py`

Assessment:
- Single-file and contract upload paths are more robust than many early-stage DMS systems: streaming size caps, MIME checks, storage abstraction, audit events, and path validation exist.
- Bulk upload status authorization and production AV defaults need work.
- `FileObjectService.attach_document_version` queries `file_objects` by raw string `_id` before trying ObjectId, unlike `materialize_to_temp`; this can fail to back-link file objects to documents.

Recommended actions:
- Fix ObjectId lookup in `attach_document_version`.
- Add ownership authorization to bulk status.
- Enable strict AV for production.
- Add large-file, malformed-file, path traversal, zip/DOCX, and S3/local fallback tests.

### AI/RAG and document intelligence

Relevant files:
- `backend/rbac_backend/routers/retrieval_engine.py`
- `backend/rbac_backend/retrieval/service.py`
- `backend/rbac_backend/retrieval/embeddings.py`
- `backend/rbac_backend/retrieval/generator.py`
- `backend/rbac_backend/ingestion/pipeline.py`
- `backend/rbac_backend/routers/ai_assistant.py`
- `client/src/pages/ContractQAPage.tsx`
- `client/src/services/contracts-api.ts`

Assessment:
- The newer retrieval engine is tenant-aware and better aligned with production security.
- Contract QA frontend calls `/api/v1/retrieval/contract-qa` with org/project/document filters and requires completed contracts, matching backend intent.
- Older AI assistant routes are weaker because they use legacy scope helpers and do not consistently enforce entitlement policy.
- Offline AI fallbacks should not be active in production.

Recommended actions:
- Fail closed for LLM/embedding outages in production.
- Migrate old AI endpoints to central policy.
- Fix ObjectId document loading in ingestion.
- Add RAG tenant-isolation and no-raw-query tests.

## Backend/Frontend Integration Gaps

| Gap | Frontend evidence | Backend evidence | Impact |
| --- | --- | --- | --- |
| Payment checkout unused | `client/src/services/plan-settings-api.ts` has no checkout method; `SubscriptionManagementPage.tsx` never starts checkout | `POST /api/rbac-monetization/subscriptions/checkout` exists | Users cannot initiate real Razorpay payment from UI. |
| Local subscription actions presented as billing actions | `SubscriptionManagementPage.tsx` calls upgrade/downgrade/cancel/reactivate/add-on endpoints | `MonetizationService` mutates local DB only | UI can imply paid changes that Razorpay does not know about. |
| Invoice UI is only preview | `getInvoicePreview()` and modal in `SubscriptionManagementPage.tsx` | `get_invoice_preview` computes local preview | No legal invoice/receipt download flow. |
| Contract download wrapper mismatch | `client/src/services/documents-api.ts` calls `/contracts/download` | Backend has `/contracts/{document_id}/download` | Download paths can fail. |
| Reference navigation mismatch | `DocumentsPage.tsx` navigates to `/document/${ref.id}` | Routes define `/documentviewer/:id` and `/reference/:id` | Reference clicks can land on 404. |
| Role data dependency | `useRBAC.ts` loads all roles through `enhancedApi.getRoles()` | Duplicate `/api/roles` routers | Route guards can break if backend route shape changes. |
| Bulk job polling lacks server ownership check | `UploadPage.tsx` polls by `job_id` | `get_bulk_upload_status` returns job by ID | Cross-tenant job status leakage. |
| API wrapper fragmentation | `api.ts`, `http.ts`, `enhanced-api.ts`, raw Axios helpers | Backend requires cookies/CSRF | Some calls may miss shared behavior. |

## Razorpay/Payment Integration Gaps

### Subscription/plans

Current state:
- Plans are stored locally with internal codes in `MonetizationService.DEFAULT_PLANS`.
- There is no explicit Razorpay plan ID mapping in `PlanBase` or `PlanCreate`.
- Pricing is stored as INR minor units in Mongo.

Missing:
- Razorpay plan IDs per billing period.
- Plan sync/reconciliation tool.
- Tax/GST metadata, billing address, customer legal name, and invoice recipient data.
- Unique active subscription constraints.

### Order/checkout creation

Current state:
- Backend has `POST /api/rbac-monetization/subscriptions/checkout`.
- It creates Razorpay customer/subscription and returns `checkout_url`.

Missing:
- Frontend call to checkout.
- User checkout success/failure screens.
- Server-side callback/status endpoint.
- If using Razorpay Orders, missing order creation and signature verification endpoints.

### Payment verification

Current state:
- Webhook signature verification exists.
- Webhook updates subscription state.

Missing:
- Amount, plan, currency, customer, invoice, and period validation.
- Raw event persistence for audit.
- Provider-scoped idempotency.
- Manual reconciliation/admin replay tools.

### Webhook handling

Current state:
- `POST /api/billing/webhooks/{provider}` is unauthenticated and signature-verified.
- Unknown events are ignored.
- Duplicate event IDs are no-op.

Missing:
- Clean 400 for unknown providers.
- Replay-safe raw payload storage.
- Failure/dunning notifications.
- Reconciliation of `subscription.halted`, `payment.failed`, retries, cancellation at period end, and subscription completion.

### Invoice/receipt flow

Current state:
- `GET /api/rbac-monetization/subscriptions/{id}/invoice-preview` computes a local preview.
- Webhook inserts minimal `billing_records`.

Missing:
- Razorpay invoice IDs, receipt IDs, invoice PDFs/URLs, paid receipt URLs.
- Frontend invoices list and download screen.
- Tax/GST handling.
- Refund/credit note handling.

### Keys/secrets

Current state:
- Settings exist for Razorpay keys.
- `.env.example` does not document them.
- Production config validation does not enforce them when Razorpay is selected.

Missing:
- Startup enforcement for live payment provider.
- Key rotation guidance.
- Separate test/live mode guardrails.

## Security Concerns

Critical/high:
- Payment state can diverge from Razorpay.
- Stateful services are exposed by `docker-compose.yml`.
- Bulk upload job status lacks tenant ownership checks.
- Old AI assistant routes do not consistently use central policy/entitlement gates.
- AV is disabled and fail-open by default.

Medium:
- Hardcoded session TTL.
- No JWT issuer/audience.
- Proxy IP handling for login rate limits is unclear.
- Some routers expose internal exception text.
- Background index creation can leave unique constraints absent.
- Frontend API calls are fragmented across wrappers.

## Testing Gaps

Existing:
- CI in `.github/workflows/ci.yml` runs secret scan, backend tests, frontend lint/tests/build, dependency scan, and Docker image scan.
- Tenant isolation tests in `backend/rbac_backend/tests/test_tenant_isolation.py`.
- Billing webhook tests in `backend/rbac_backend/tests/test_billing_webhooks.py`.

Missing or insufficient:
- Razorpay sandbox checkout integration tests.
- Frontend checkout/callback/subscription management tests.
- Webhook amount/currency/plan mismatch tests.
- Payment lifecycle tests for cancel/reactivate/upgrade/downgrade/add-on sync.
- End-to-end browser tests for auth, upload, document download, contract QA, plan settings, and subscription management.
- RAG cross-tenant tests for all old and new AI endpoints.
- File security tests for AV, malformed DOCX/PDF, path traversal, large files, duplicate uploads, and S3/local fallback.
- Startup/readiness tests requiring critical indexes.
- Load/concurrency tests for upload, retrieval, and webhook idempotency.

## Deployment Readiness

Ready-ish:
- Dockerfiles exist for backend/client.
- Compose stack exists for backend, client, Mongo, Qdrant, FalkorDB, Redis, ClamAV, and gateway.
- Health endpoints exist.
- CI builds and scans Docker images.

Not ready:
- Compose exposes internal stateful services.
- No production TLS termination manifest.
- Payment environment is not documented/enforced.
- Mongo auth/replica set in compose is local-dev style.
- Redis/FalkorDB auth is absent by default.
- Backend runtime starts background workers inside API container depending env; production worker topology needs explicit separation.
- Root/backend dependency manifests are inconsistent.
- Index creation is not a deployment gate.

## Phase-wise Improvement Plan

### Critical priority

1. Complete Razorpay integration before accepting live payments.
   - Add Razorpay plan ID mapping to plan model and admin UI/API.
   - Wire frontend checkout to `POST /api/rbac-monetization/subscriptions/checkout`.
   - Add payment status/success/failure screens.
   - Activate entitlements only after verified webhook/payment confirmation.

2. Make billing lifecycle gateway-aware.
   - Cancel/reactivate subscriptions through Razorpay.
   - Treat upgrade/downgrade/add-ons as pending billing changes until gateway confirmation.
   - Add reconciliation jobs and admin repair tools.

3. Harden webhook financial validation.
   - Validate amount, currency, plan, period, customer, and subscription.
   - Store raw/normalized webhook payloads securely.
   - Make idempotency unique on `(provider, event_id)`.

4. Secure production deployment topology.
   - Remove exposed Mongo/Redis/Falkor/Qdrant ports from production.
   - Require database/queue auth.
   - Document TLS termination and production edge proxy settings.

5. Fix cross-tenant bulk upload status access.
   - Store owner/scope on jobs.
   - Authorize status reads with `PolicyService`.

### High priority

1. Consolidate RBAC routers.
   - Remove duplicate `/api/roles` definitions.
   - Align role/permission CRUD with seeded platform permissions.

2. Migrate legacy AI assistant routes to `PolicyService`.
   - Resolve resources first, then authorize against actual org/project.
   - Add entitlement checks for drafting/AI features.

3. Fix ObjectId lookup issues.
   - `IngestionPipeline._load_document`
   - `FileObjectService.attach_document_version`
   - Any AI/agent document loaders that query `_id` as raw strings.

4. Make AI fallbacks production-safe.
   - Disable fake embeddings and fake LLM answers in production.
   - Add readiness checks for OpenAI and Qdrant vector dimensions.

5. Fix frontend/backend API mismatches.
   - Replace `/contracts/download` client call with real backend route.
   - Fix `/document/${id}` navigation.
   - Add contract tests for document and contract download flows.

6. Enforce Razorpay env validation.
   - Add payment variables to `.env.example`.
   - Fail production startup when Razorpay is selected but keys/secrets/mappings are missing.

### Medium priority

1. Standardize API error envelopes.
   - Use consistent `detail`, code, request ID, and user-safe message.
   - Avoid returning raw exception strings.

2. Centralize frontend API access.
   - Use the shared Axios/fetch clients only.
   - Add lint rules or tests to prevent raw authenticated Axios calls.

3. Make critical indexes deployment-gated.
   - Add a migration/index command.
   - Make readiness fail if critical unique/TTL indexes are missing.

4. Strengthen auth.
   - Use configured token TTL.
   - Add JWT issuer/audience.
   - Add trusted proxy support for rate limiting.

5. Improve file security.
   - Enable ClamAV in production.
   - Add deeper DOCX validation.
   - Validate bulk files before durable temp persistence where feasible.

6. Add invoice and receipt data model fields.
   - Gateway invoice ID
   - Gateway payment ID
   - Receipt number
   - Invoice/receipt URL
   - Tax/GST metadata
   - Failure reason

### Low priority

1. Clean stale/development artifacts.
   - Remove or document root `package.json`.
   - Consolidate backend requirements files.
   - Remove comments like "get the UI working" from production routers after refactor.

2. Improve observability.
   - Add structured JSON logs across all services.
   - Include request ID in error responses and audit events.
   - Add dashboard panels for webhook failures, queue depth, RAG failures, upload failures, and auth lockouts.

3. Improve operator docs.
   - Add payment runbook.
   - Add deployment hardening guide.
   - Add environment variable matrix by environment.
   - Add backup/restore and disaster recovery docs for Mongo, S3, Qdrant, FalkorDB, and Redis.

## Go/No-Go Recommendation

Recommendation: No-go for live paid production launch.

The DMS can continue in controlled pilot/internal environments if payment provider mode remains `noop` or manual billing, production CORS/cookie/Redis settings are enforced, and tenant isolation tests pass. Do not enable Razorpay-backed paid plans until checkout, verification, webhook reconciliation, invoices, lifecycle synchronization, and frontend payment screens are completed and tested against Razorpay sandbox.

Minimum go-live criteria:
- Razorpay checkout and webhook flow passes sandbox e2e tests.
- Payment lifecycle actions are gateway-synchronized or explicitly marked manual/pending.
- Bulk upload status is tenant-scoped.
- Duplicate role routes are consolidated.
- Production deployment does not expose databases/queues publicly.
- Critical indexes are guaranteed before serving traffic.
- AI/RAG production fallbacks fail closed instead of returning fake outputs.
