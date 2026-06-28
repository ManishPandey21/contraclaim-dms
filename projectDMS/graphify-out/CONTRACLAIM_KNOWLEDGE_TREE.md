# ContraClaim DMS Knowledge Tree

Repository: `ManishPandey21/contraclaim-dms`  
Local path inspected: `C:\Users\manish.p\Documents\Contraclaim-DMS`  
Date: 2026-06-18

## Graphify Status

- Requested Graphify graph build/query could not be completed in this local environment.
- No existing graph artifact was found: `.graphify` was absent.
- `Get-Command graphify` found no installed command.
- Direct commands failed:
  - `graphify .` -> `graphify` is not recognized as a cmdlet/function/script.
  - `graphify query "repository structure"` -> `graphify` is not recognized.
- Fallback attempts:
  - Sandboxed `npx --yes graphify .` failed because npm was in cache-only mode and no cached package existed.
  - Approved network `npx --yes graphify .` reached npm but failed with `could not determine executable to run`.
  - No repo-local `*graphify*` file or wrapper was found.
- This knowledge tree is therefore based on read-only local static inspection after the Graphify path was exhausted. No source code files were modified.

## Repository Root

```text
contraclaim-dms/
|-- backend/                         FastAPI backend package and Docker image
|-- client/                          Vite React frontend
|-- config/                          Reverse proxy and runtime config helpers
|-- docs/                            Historical architecture/audit notes
|-- services/graphiti/               Graphiti/FalkorDB sidecar service
|-- scripts/                         Deployment/bootstrap scripts
|-- docker-compose.yml               Local integrated stack
|-- docker-compose.prod.yml          Production-oriented stack
|-- docker-compose.mongo-replicaset.yml
|-- .env.example                     Root production env template
|-- .github/workflows/ci.yml         Backend/frontend/security CI
|-- pytest.ini                       Backend test discovery
```

## Backend Modules

```text
backend/rbac_backend/
|-- main.py
|   |-- Creates FastAPI app
|   |-- Adds CORS middleware
|   |-- Registers all API routers under /api, /api/v1, /ws, /health
|   |-- Startup/shutdown lifecycle connects DB, creates indexes, initializes services
|
|-- worker.py
|   |-- Separate worker process for background services and Redis-backed contract ingest queue
|
|-- core/
|   |-- config.py                    Settings, env validation, production hardening checks
|   |-- database.py                  Motor client, DB connection, collection indexes
|   |-- security.py                  JWT/cookie auth, current-user dependency, permission dependency
|   |-- csrf.py                      Double-submit CSRF cookie/header validation
|   |-- permissions.py               Canonical DMS/drafting/billing permission constants and aliases
|
|-- routers/
|   |-- auth.py                      /api/login, /api/refresh, /api/logout, /api/csrf-token, /api/me, /api/step-up
|   |-- users.py                     /api/users, /api/users/me, lock/unlock, legacy /api/token
|   |-- sso.py                       /api/auth/sso/login, /api/auth/sso/callback
|   |-- organizations.py             Organization CRUD
|   |-- projects.py                  Project CRUD, project representatives
|   |-- roles.py, permissions.py     RBAC role/permission administration
|   |-- documents.py                 Document CRUD, upload, download, processing, references, comments, bulk upload
|   |-- contracts.py                 Contract upload/session/chunking/search/download
|   |-- contract_appraisal.py        Contract appraisal jobs, reports, registers, exports, review comments
|   |-- retrieval_engine.py          /api/v1 ingestion, search, RAG, contract QA, agent, observability
|   |-- letters.py                   Letter workflow, conversation tree, strategy transitions
|   |-- letter_drafting.py           Drafting runs, analysis, plan, draft, critique, governance, issue/export
|   |-- input_requests.py            Letter input request/response flow
|   |-- ai_assistant.py              AI letter search/draft/LangGraph config/runs/prompts
|   |-- deep_planning.py             Deep planning draft/history/document analysis
|   |-- claims.py                    Claims CRUD, assessments, evidence bundle, approvals
|   |-- sla.py                       SLA upcoming/breached views
|   |-- dashboard.py                 Dashboard stats
|   |-- search.py                    Document search/suggestions/popular/semantic/analytics
|   |-- email_groups.py              Email group CRUD and recipient resolution
|   |-- email.py, email_share.py     Email sending and public share token download
|   |-- parties.py                   Parties CRUD
|   |-- representatives.py           Representatives CRUD and scope assignment
|   |-- concerns.py                  Concern register CRUD
|   |-- reports.py                   Audit export and report preview/download
|   |-- tags.py                      Tags/subtags CRUD
|   |-- tasks.py                     Tasks and comments
|   |-- folder_structure.py          Folder tree and file upload/open helpers
|   |-- storage_settings.py          Org/project storage settings and resolution
|   |-- storage_sync.py              Storage sync/resync/reconcile/status
|   |-- smtp_settings.py             Org/project SMTP settings and tests
|   |-- rbac_monetization.py         Plan catalog, plan settings, subscriptions, billing, usage
|   |-- billing_webhooks.py          /api/billing/webhooks/{provider}
|   |-- performance.py               Health/metrics/jobs/cache endpoint stats
|   |-- health.py                    /health, /health/live, /health/ready, /metrics
|   |-- ws.py                        /ws websocket router
|
|-- services/
|   |-- authentication_service.py    Login/session primitives
|   |-- authorization_service.py     Resource authorization helpers
|   |-- policy_service.py            Central policy checks across DMS/drafting/billing
|   |-- permission_service.py        Permission lookup/equivalence checks
|   |-- role_service.py              Role CRUD/permission assignment support
|   |-- scope_service.py             Organization/project scoping
|   |-- entitlement_service.py       Subscription entitlement checks
|   |-- step_up_service.py           Recent-password step-up for sensitive actions
|   |-- user_service.py              User CRUD/business rules
|   |-- organization_service.py      Organization business logic
|   |-- project_service.py           Project business logic
|   |-- document_service.py          Document persistence, updates, processing hooks
|   |-- file_service.py              Local upload storage and chunk merge helpers
|   |-- file_object_service.py       Immutable file object, dedupe, versions, provider materialization
|   |-- upload_streaming.py          Stream UploadFile to temp file, size limit, SHA-256, MIME sniff
|   |-- antivirus_service.py         ClamAV TCP INSTREAM scanning
|   |-- s3_service.py                S3 upload/download/presigned URL helper
|   |-- storage_settings_service.py  Org/project storage provider resolution
|   |-- document_processor.py        OCR/text/metadata/embedding persistence pipeline
|   |-- ocr_service.py               OCR preprocessing service
|   |-- text_processing_service.py   Text extraction/legacy parsing support
|   |-- metadata_processor_service.py, metadata.py
|   |-- bulk_upload_service.py       Bulk document import orchestration
|   |-- document_bulk_download_service.py
|   |-- reference_sync_service.py    Document reference synchronization
|   |-- document_linking_service.py  Linked document relationships
|   |-- document_audit_service.py    Document audit events
|   |-- contract_service.py          Contract upload/session/ingest/search/download business logic
|   |-- contracts_ingest.py          Contract clause extraction, chunking, vector record writes
|   |-- contract_ingest_queue.py     Redis queue for contract ingestion
|   |-- contract_graph_service.py    Contract graph integration
|   |-- falkor_graph_service.py      FalkorDB graph writes/queries
|   |-- falkordb_vector_service.py   FalkorDB vector-related support
|   |-- openai_service.py            OpenAI wrapper
|   |-- pydantic_ai_service.py       Pydantic AI integration
|   |-- llamaindex_service.py        LlamaIndex integration
|   |-- langchain_vector_service.py  LangChain vector integration
|   |-- letter_service.py            Letter workflow logic
|   |-- letter_drafting/*            Drafting service, repo, prompts, planning, validator, generator
|   |-- template_service.py, letter_template_service.py
|   |-- claim_service.py, claim_assessment_service.py
|   |-- approval_service.py          Approval workflow records
|   |-- evidence_bundle_service.py   Claims evidence export
|   |-- notification_service.py / notifications.py
|   |-- email_service.py, email_group_service.py
|   |-- smtp_settings_service.py
|   |-- monetization_service.py      Plan/subscription/billing business logic
|   |-- payment_gateway.py           No-op/Razorpay/Stripe gateway abstraction
|   |-- billing_webhook_service.py   Idempotent webhook event persistence and subscription state updates
|   |-- usage_metering_service.py    Usage/quotas
|   |-- subscription_lifecycle_service.py
|   |-- performance_service.py, performance_monitor.py
|   |-- observability.py             Service-side observability helpers
|   |-- background_jobs.py           Background task bootstrap
|
|-- ingestion/
|   |-- service.py                   Ingestion job orchestration entry
|   |-- pipeline.py                  Idempotent chunk/embed/upsert pipeline
|   |-- chunker.py, chunk_ids.py     Chunk generation and stable IDs
|   |-- enrichment.py                Chunk enrichment
|   |-- models.py                    IngestionOptions, IngestionJob, Chunk
|
|-- retrieval/
|   |-- service.py                   Search, RAG, iterative contract QA
|   |-- dependencies.py              Dependency factory for retrieval services
|   |-- embeddings.py                Embedding client
|   |-- vector_client.py             Qdrant client with in-memory fallback behavior
|   |-- generator.py                 LLM generator
|   |-- models.py                    Search/RAG/ContractQA models
|   |-- reconcile.py                 Vector reconciliation
|   |-- source_metadata.py           Source payload normalization
|
|-- agents/
|   |-- service.py                   Retrieval-agent conversation flow
|   |-- models.py                    AgentRequest, AgentResponse, conversation/message models
|
|-- graph/
|   |-- graph_adapter.py             Knowledge graph adapter
|   |-- graph_ingestion_service.py   Graph ingestion
|
|-- ai_workflows/
|   |-- langgraph.py                 LangGraph wrapper
|   |-- tools.py                     AI workflow tool definitions
|   |-- langgraph/letter_pipeline.py Letter drafting graph pipeline
|
|-- models/
|   |-- auth_models.py, user.py, user_models.py
|   |-- organization.py, project.py
|   |-- role.py, permission.py
|   |-- document.py, documents.py, document_metadata.py, document_vector.py
|   |-- storage_architecture.py      FileObject, DocumentVersion, ContractAggregate, ContractVersion
|   |-- storage_settings.py
|   |-- contract_models.py, contract_appraisal.py
|   |-- claim.py, approval.py
|   |-- letter.py, letter_drafting.py, letter_template.py, langgraph_run.py
|   |-- ai_models.py
|   |-- party.py, representative.py, concern.py
|   |-- tag.py, task.py, notification.py, email_models.py, email_group.py
|   |-- smtp_settings.py, performance_models.py, report.py
|   |-- rbac_monetization.py         Plans, subscriptions, billing/usage DTOs
|
|-- tests/
    |-- Backend test suite for auth, CSRF, RBAC, tenant isolation, documents,
        retrieval, LangGraph, billing webhooks, config validation, claims,
        contract appraisal, observability, upload notifications, etc.
```

## Frontend Modules

```text
client/src/
|-- main.tsx                         React/Vite bootstrap
|-- App.tsx                          BrowserRouter wrapper
|-- routes.tsx                       Protected route table
|
|-- config/
|   |-- api.ts                       VITE_API_BASE_URL/runtime/host-derived API base; joinApiUrl()
|   |-- features.ts                  VITE_LANGGRAPH_ENABLED feature flag
|   |-- rolePermissions.ts           UX-level route permission map
|   |-- contract-categories.ts       Contract search/appraisal category metadata
|
|-- services/
|   |-- http.ts                      authenticatedFetch + Axios client, credentials include, CSRF header
|   |-- api.ts                       Shared Axios instance and refresh/session-expiry flow
|   |-- enhanced-api.ts              Broad typed API facade
|   |-- auth.ts, session-api.ts      Login/logout/refresh/session helpers
|   |-- documents-api.ts             Document list/detail/download helper
|   |-- contracts-api.ts             Contract upload/search/RAG/QA/appraisal clients
|   |-- claims-api.ts                Claims/approval/evidence clients
|   |-- letter-workflow-api.ts       Letter workflow clients
|   |-- plan-settings-api.ts         Plan catalog/settings/subscription/invoice clients
|   |-- billing-api.ts               Checkout API wrapper and redirectToCheckout()
|   |-- organizations-api.ts, projects-api.ts
|   |-- search-api.ts, dashboard-api.ts, audit-api.ts
|   |-- storage-settings-api.ts, smtp-settings-api.ts
|   |-- email-service.ts, email-groups-api.ts
|   |-- tasks-api.ts, tags-api.ts, sla-api.ts, step-up.ts, strategy.ts
|
|-- hooks/
|   |-- useRBAC.ts                   Loads current user roles and role permissions
|   |-- useHasPermission.ts          Permission check helper
|   |-- use-auth.ts                  Auth state/session hook
|   |-- useStepUp.tsx                Step-up confirmation UX
|   |-- useLetterWorkflow.ts         Letter workflow API/state hook
|   |-- useLetterDrafting.ts         Drafting v2 API hook
|   |-- useLanggraphDraft.ts         LangGraph draft/background hook
|   |-- useLanggraphStrategyPlan.ts  LangGraph strategy plan hook
|   |-- useLetterGraphRuns.ts        LangGraph run fetch hook
|   |-- useAIAssistant.ts/js         AI assistant hook
|
|-- components/
|   |-- auth/ProtectedRoute.tsx      Authenticated-route and permission gate
|   |-- auth/RoleGuard.tsx           Route-specific permission guard
|   |-- layout/*                     Main layout, navbar, sidebar, route skeleton
|   |-- ui/*                         shadcn/Radix UI components
|   |-- document-viewer/*            Viewer, metadata, references, enclosures
|   |-- documents/BulkOperations.tsx
|   |-- langgraph/*                  Strategy plan display, graph status
|   |-- letter-workflow/*            Drafting/approval/input/review components
|   |-- contract-appraisal/*         Appraisal registers and clause library
|   |-- settings/*                   Storage, SMTP, prompts, notifications
|   |-- dashboard/*, reports/*, search/*
|
|-- pages/
|   |-- LoginPage.tsx, RegisterPage.tsx
|   |-- Overview.tsx, Dashboard.tsx, HealthPage.tsx
|   |-- OrganizationsPage.tsx, ProjectsPage.tsx, UsersPage.tsx, PermissionsPage.tsx
|   |-- DocumentsPage.tsx, EnhancedDocumentsPage.tsx, UploadPage.tsx
|   |-- DocumentViewerPage.tsx, ReferencePage.tsx, ShareDocumentPage.tsx
|   |-- FolderStructurePage.tsx, TagsPage.tsx, TasksPage.tsx
|   |-- ContractsPage.tsx, ContractsUploadPage.tsx, ContractsSearchPage.tsx
|   |-- ContractQAPage.tsx, ContractAppraisalPage.tsx
|   |-- LetterWorkflowPage.tsx, LetterInputPage.tsx, LetterStrategicPlanPage.tsx
|   |-- LetterDraftPage.tsx, LetterReviewPage.tsx, LetterApprovalPage.tsx
|   |-- LetterCompletedPage.tsx, LetterSummaryPage.tsx, LetterQualityDashboardPage.tsx
|   |-- LetterTemplatePage.tsx, LetterTemplateEditorPage.tsx
|   |-- ClaimsRegisterPage.tsx, SLATrackerPage.tsx, ReportsAnalyticsPage.tsx
|   |-- PartiesInvolvedPage.tsx, RepresentativesPage.tsx
|   |-- PlanSettingsPage.tsx, SubscriptionManagementPage.tsx
|   |-- SettingsPage.tsx, NotificationCenterPage.tsx, EmailGroupsPage.tsx
|   |-- NotFound.tsx
|
|-- types/
    |-- api.ts, langgraph.ts, letterDrafting.ts, strategyPlan.ts
```

Notes:

- `client/src/pages/bulk-upload-service-fixed.py` is a backend-style Python artifact inside the frontend pages directory; it is not part of the normal React module tree.
- `client/package.json` contains Vite/Vitest scripts and a `"proxy": "https://api.contraclaim.com"` field, while runtime API base resolution is mainly handled in `client/src/config/api.ts`.

## API Integration Tree

```text
Frontend API base
|-- client/src/config/api.ts
|   |-- VITE_API_BASE_URL
|   |-- window.__API_BASE_URL__
|   |-- web.contraclaim.com -> https://api.contraclaim.com/api
|   |-- fallback /api
|
|-- client/src/services/http.ts
|   |-- credentials: include
|   |-- strips Authorization when cookie auth is used
|   |-- injects X-CSRF-Token from cc_csrf_token cookie on unsafe requests
|   |-- fetches /api/csrf-token when needed
|
|-- client/src/services/api.ts
    |-- shared Axios instance
    |-- refresh/session expiry handling
```

### Backend Route Families and Frontend Consumers

| Feature | Backend API | Backend files | Frontend files |
|---|---|---|---|
| Auth/session | `POST /api/login`, `POST /api/refresh`, `POST /api/logout`, `GET /api/csrf-token`, `GET /api/me`, `POST /api/step-up` | `backend/rbac_backend/routers/auth.py`, `backend/rbac_backend/core/security.py`, `backend/rbac_backend/core/csrf.py`, `backend/rbac_backend/services/step_up_service.py` | `client/src/pages/LoginPage.tsx`, `client/src/services/auth.ts`, `client/src/services/session-api.ts`, `client/src/services/http.ts`, `client/src/hooks/use-auth.ts`, `client/src/hooks/useStepUp.tsx` |
| SSO | `GET /api/auth/sso/login`, `GET /api/auth/sso/callback` | `backend/rbac_backend/routers/sso.py`, `backend/rbac_backend/services/oidc_service.py` | Login/auth surfaces |
| Users | `/api/users`, `/api/users/me`, `/api/users/{id}`, lock/unlock | `backend/rbac_backend/routers/users.py`, `backend/rbac_backend/services/user_service.py` | `client/src/pages/UsersPage.tsx`, `client/src/services/enhanced-api.ts`, `client/src/hooks/useRBAC.ts` |
| RBAC | `/api/roles`, `/api/permissions`, `/api/permissions/check` | `routers/roles.py`, `routers/permissions.py`, `services/policy_service.py`, `core/permissions.py` | `client/src/pages/PermissionsPage.tsx`, `client/src/config/rolePermissions.ts`, `client/src/hooks/useRBAC.ts`, `components/auth/*` |
| Orgs/projects | `/api/organizations`, `/api/projects` | `routers/organizations.py`, `routers/projects.py` | `client/src/pages/OrganizationsPage.tsx`, `ProjectsPage.tsx`, `services/organizations-api.ts`, `services/projects-api.ts` |
| Documents | `/api/documents`, `/api/documents/{id}`, `/download`, `/process`, `/references`, `/comments`, `/bulk-upload`, `/export`, `/download-all` | `routers/documents.py`, `services/document_service.py`, `file_object_service.py`, `upload_streaming.py` | `DocumentsPage.tsx`, `UploadPage.tsx`, `DocumentViewerPage.tsx`, `ReferencePage.tsx`, `ShareDocumentPage.tsx`, `FolderStructurePage.tsx`, `services/documents-api.ts` |
| Contracts | `/api/contracts/upload-session`, `/upload-multipart`, `/upload-chunk`, `/status`, `/list`, `/search`, `/{document_id}/download` | `routers/contracts.py`, `services/contract_service.py`, `services/contracts_ingest.py` | `ContractsPage.tsx`, `ContractsUploadPage.tsx`, `ContractsSearchPage.tsx`, `services/contracts-api.ts` |
| Retrieval/RAG | `/api/v1/ingestion/jobs`, `/api/v1/retrieval/search`, `/rag`, `/contract-qa`, `/agent`, `/observability/*`, `/admin/vector/reconcile` | `routers/retrieval_engine.py`, `retrieval/service.py`, `ingestion/pipeline.py`, `agents/service.py` | `ContractQAPage.tsx`, `contracts-api.ts`, LangGraph/drafting hooks |
| AI assistant/LangGraph | `/api/ai-assistant/*`, `/api/ai-assistant/langgraph/*` | `routers/ai_assistant.py`, `services/ai_service.py`, `services/openai_service.py`, `ai_workflows/langgraph/*` | `hooks/useAIAssistant.ts`, `useLanggraphDraft.ts`, `useLanggraphStrategyPlan.ts`, `components/langgraph/*`, `HealthPage.tsx` |
| Letters/drafting | `/api/letters`, `/api/letters/{id}`, `/api/letters/{id}/drafting/*`, `/api/letter-drafting/*`, `/api/input-requests/*` | `routers/letters.py`, `routers/letter_drafting.py`, `routers/input_requests.py`, `services/letter_service.py`, `services/letter_drafting/*` | `LetterWorkflowPage.tsx`, `LetterInputPage.tsx`, `LetterStrategicPlanPage.tsx`, `LetterDraftPage.tsx`, `LetterReviewPage.tsx`, `LetterApprovalPage.tsx`, `hooks/useLetterWorkflow.ts`, `hooks/useLetterDrafting.ts` |
| Claims/SLA | `/api/claims`, `/api/claims/{id}/assess`, approval/evidence, `/api/sla/*` | `routers/claims.py`, `routers/sla.py`, `services/claim_service.py`, `claim_assessment_service.py`, `sla_service.py` | `ClaimsRegisterPage.tsx`, `SLATrackerPage.tsx`, `services/claims-api.ts`, `services/sla-api.ts` |
| Email/share/groups | `/api/email/*`, `/api/email/legacy/*`, `/api/email/groups/*` | `routers/email_share.py`, `routers/email.py`, `routers/email_groups.py` | `ShareDocumentPage.tsx`, `EmailGroupsPage.tsx`, `services/email-service.ts`, `email-groups-api.ts` |
| Billing/plans | `/api/rbac-monetization/*`, `/api/billing/webhooks/{provider}` | `routers/rbac_monetization.py`, `routers/billing_webhooks.py`, `services/monetization_service.py`, `payment_gateway.py`, `billing_webhook_service.py` | `PlanSettingsPage.tsx`, `SubscriptionManagementPage.tsx`, `services/plan-settings-api.ts`, `services/billing-api.ts` |
| Storage/settings | `/api/settings/storage/*`, `/api/storage-settings/*`, `/api/storage-sync/*`, `/api/smtp-settings/*` | `routers/storage_settings.py`, `storage_sync.py`, `smtp_settings.py`, `services/storage_settings_service.py`, `smtp_settings_service.py` | `SettingsPage.tsx`, `HealthPage.tsx`, `components/settings/*`, `storage-settings-api.ts`, `smtp-settings-api.ts` |
| Reports/tasks/tags/search | `/api/reports`, `/api/audit/export`, `/api/tasks`, `/api/tags`, `/api/search/*` | `routers/reports.py`, `tasks.py`, `tags.py`, `search.py` | `ReportsAnalyticsPage.tsx`, `TasksPage.tsx`, `TagsPage.tsx`, `EnhancedDocumentsPage.tsx`, corresponding service modules |

## Database Models and Collections

### Database Layer

```text
backend/rbac_backend/core/database.py
|-- Motor AsyncIOMotorClient
|-- database selected by MONGODB_DATABASE
|-- create_indexes() covers tenant scopes, lookup fields, status filters, text search, and idempotency keys
```

### Core Domain Collections

```text
Identity and tenancy
|-- users
|-- organizations
|-- projects
|-- role_assignments
|-- organization_memberships
|-- project_memberships

DMS documents and storage
|-- documents
|-- file_objects
|-- document_versions
|-- document_audit_events
|-- bulk_upload_jobs
|-- storage_reconciliation_runs

Contracts and retrieval
|-- contracts
|-- contract_versions
|-- contract_upload_sessions
|-- contract_ingest_jobs
|-- ingestion_jobs
|-- chunks
|-- document_vectors
|-- vector_sync_status
|-- rag_runs
|-- agent_conversations
|-- agent_messages

Letters and drafting
|-- letters
|-- letter_draft_runs
|-- letter_draft_events
|-- letter_draft_assignments
|-- letter_draft_comments
|-- draft_context_packs
|-- issued_letters
|-- prompt_templates

External parties and workflow
|-- parties
|-- representatives
|-- tags
|-- tasks
|-- concerns
|-- claims
|-- claim_assessments
|-- approvals
|-- sla_rules

Contract appraisal
|-- contract_appraisal_jobs
|-- contract_appraisal_reports
|-- contract_appraisal_review_comments
|-- contract_obligations
|-- contract_risks
|-- contract_key_dates

Email, notifications, settings
|-- email_logs
|-- email_groups
|-- document_share_tokens
|-- notifications / notification preference and delivery models
|-- smtp_settings
|-- ai_style_profiles

Billing and monetization
|-- expert_allocations
|-- plans
|-- subscriptions
|-- entitlements
|-- usage_events
|-- usage_periods
|-- quota_buckets
|-- billing_records
|-- billing_webhook_events
|-- offboarding_exports
|-- audit_events
```

### Model Files

```text
backend/rbac_backend/models/
|-- document.py                      Document, DocumentUpdate, processing/bulk/reference DTOs
|-- storage_architecture.py          FileObject, DocumentVersion, ContractAggregate, ContractVersion
|-- contract_models.py               Contract upload/search DTOs
|-- contract_appraisal.py            AppraisalJob, AppraisalReport, registers, review comments
|-- rbac_monetization.py             Plan, Subscription, add-ons, billing/usage DTOs
|-- role.py, permission.py           RBAC DTOs
|-- user.py, auth_models.py          User/auth DTOs
|-- letter.py, letter_drafting.py    Letter workflow and drafting DTOs
|-- ai_models.py, langgraph_run.py   AI assistant/LangGraph DTOs
|-- claim.py, approval.py            Claims and approval DTOs
|-- organization.py, project.py      Tenant hierarchy DTOs
```

## Authentication and RBAC

```text
Authentication flow
|-- Frontend LoginPage.tsx
|-- client/src/services/auth.ts
|-- POST /api/login
|-- backend/rbac_backend/routers/auth.py
|-- backend/rbac_backend/services/authentication_service.py
|-- JWT access token created in core/security.py
|-- HttpOnly auth cookie: cc_access_token by default
|-- CSRF cookie/header pair: cc_csrf_token and X-CSRF-Token
|-- GET /api/me returns CurrentUser context
|-- POST /api/refresh refreshes auth cookie
|-- POST /api/logout clears auth and CSRF cookies
```

```text
Authorization flow
|-- Backend current user
|   |-- core/security.py:get_current_user()
|   |-- accepts Authorization bearer, auth cookie, and optional dev headers when allowed
|
|-- Permission model
|   |-- core/permissions.py
|   |-- DMS permissions: dms.document.*, dms.dashboard.view, dms.claim.*, dms.contract.appraisal.*, dms.admin
|   |-- Drafting permissions: drafting.request.*, drafting.draft.*, drafting.review.*, drafting.admin
|   |-- Billing permissions: billing.*, subscription.*
|   |-- legacy aliases: documents:read, users:create, system:admin, etc.
|
|-- Central policy
|   |-- services/policy_service.py
|   |-- services/authorization_service.py
|   |-- services/permission_service.py
|   |-- services/entitlement_service.py
|   |-- services/scope_service.py
|
|-- Sensitive action hardening
|   |-- POST /api/step-up
|   |-- services/step_up_service.py
|   |-- frontend useStepUp.tsx
|   |-- used for subscription/plan/cancel/trial/add-on and similar sensitive actions
```

```text
Frontend route gating
|-- components/auth/ProtectedRoute.tsx
|-- components/auth/RoleGuard.tsx
|-- hooks/useRBAC.ts
|-- config/rolePermissions.ts
|   |-- /documents -> dms.document.view
|   |-- /upload -> dms.document.upload
|   |-- /letters -> drafting.request.view
|   |-- /plan-settings -> subscription.entitlement.manage
|   |-- /subscription-management -> subscription.entitlement.manage or subscription.upgrade
|   |-- /health -> system:admin
```

## Document Upload and Processing

```text
Single document upload
|-- Frontend
|   |-- UploadPage.tsx
|   |-- DocumentsPage.tsx
|   |-- services/documents-api.ts
|
|-- API
|   |-- POST /api/documents
|   |-- GET /api/documents
|   |-- GET /api/documents/{id}
|   |-- PUT /api/documents/{id}
|   |-- DELETE /api/documents/{id}
|   |-- POST /api/documents/{id}/process
|   |-- GET /api/documents/{id}/download
|
|-- Backend processing path
|   |-- routers/documents.py
|   |-- services/upload_streaming.py
|       |-- streams UploadFile to temp file
|       |-- enforces upload size
|       |-- calculates SHA-256
|       |-- sniffs MIME via utils/file_validation.py
|   |-- optional services/antivirus_service.py ClamAV scan
|   |-- services/storage_key_builder.py builds storage key
|   |-- services/storage_settings_service.py resolves org/project providers
|   |-- services/file_object_service.py stores bytes/path, dedupes by hash, creates file_object
|   |-- services/file_service.py stores local file/chunks
|   |-- services/s3_service.py stores to S3 when configured
|   |-- services/document_service.py creates/updates Document
|   |-- file_object_service.attach_document_version() creates document_versions record
|   |-- optional document_service.process_document_async()
|   |-- services/document_processor.py runs OCR/text/metadata/embedding persistence
```

```text
Bulk upload
|-- Frontend: UploadPage.tsx, bulk upload controls in document pages
|-- API:
|   |-- POST /api/documents/bulk-upload
|   |-- GET /api/documents/bulk-upload/{job_id}/status
|   |-- GET /api/documents/bulk-upload/template
|-- Backend:
|   |-- routers/documents.py bulk controller paths
|   |-- services/bulk_upload_service.py
|   |-- bulk_upload_jobs collection
|   |-- CSV required columns include filename, upload_type/uploadType, letter_no, date, ocr_enabled
```

```text
Document viewer/linking/reference features
|-- DocumentViewerPage.tsx
|-- ReferencePage.tsx
|-- ShareDocumentPage.tsx
|-- document-viewer components
|-- APIs:
|   |-- GET/POST/DELETE /api/documents/{id}/references
|   |-- POST /api/documents/{id}/sync-references
|   |-- POST /api/documents/link
|   |-- GET /api/documents/{id}/linked
|   |-- GET/POST /api/documents/{id}/comments
|   |-- GET/POST/DELETE /api/documents/{id}/enclosures
```

## AI and RAG Flow

```text
General ingestion/RAG
|-- API
|   |-- POST /api/v1/ingestion/jobs
|   |-- GET /api/v1/ingestion/jobs/{job_id}
|   |-- POST /api/v1/retrieval/search
|   |-- POST /api/v1/retrieval/rag
|   |-- POST /api/v1/retrieval/contract-qa
|   |-- POST /api/v1/retrieval/agent
|   |-- GET /api/v1/observability/logs
|   |-- POST /api/v1/observability/analytics
|   |-- POST /api/v1/admin/vector/reconcile
|
|-- Backend packages
|   |-- ingestion/pipeline.py
|       |-- creates/updates ingestion_jobs
|       |-- chunks text via ingestion/chunker.py
|       |-- embeds through retrieval/embeddings.py
|       |-- upserts vectors through retrieval/vector_client.py
|       |-- persists chunks collection
|       |-- updates vector_sync_status
|       |-- logs rag_runs/observability
|   |-- retrieval/service.py
|       |-- search(): vector or Mongo retrieval
|       |-- rag(): retrieval plus LLM answer with citations
|       |-- contract_iterative_qa(): iterative clause-focused QA with critique/refinement
|   |-- retrieval/vector_client.py
|       |-- Qdrant collection management/search/upsert/delete/list
|       |-- in-memory fallback behavior for local/test contexts
|   |-- retrieval/generator.py
|       |-- LLM generation
|   |-- agents/service.py
|       |-- conversational retrieval agent
|
|-- Data stores
|   |-- MongoDB: ingestion_jobs, chunks, document_vectors, rag_runs, agent_conversations, agent_messages
|   |-- Qdrant: configured by QDRANT_URL/QDRANT_API_KEY/QDRANT_COLLECTION
|   |-- FalkorDB/Graphiti: graph services and sidecar
```

```text
Contract-specific ingestion/search
|-- Frontend
|   |-- ContractsUploadPage.tsx
|   |-- ContractsSearchPage.tsx
|   |-- ContractQAPage.tsx
|   |-- services/contracts-api.ts
|
|-- API
|   |-- POST /api/contracts/upload-session
|   |-- POST /api/contracts/upload-multipart
|   |-- POST /api/contracts/upload-chunk
|   |-- GET /api/contracts/status
|   |-- GET /api/contracts/list
|   |-- POST /api/contracts/search
|   |-- GET /api/contracts/{document_id}/download
|   |-- POST /api/v1/retrieval/contract-qa
|
|-- Backend
|   |-- services/contract_service.py
|       |-- upload sessions and chunk limits
|       |-- OCR/text preparation
|       |-- calls contracts_ingest.create_contract_ingestor()
|       |-- hybrid lexical/vector search and reranking
|   |-- services/contracts_ingest.py
|       |-- extracts contract sections/clauses
|       |-- chunks long clauses
|       |-- writes document_vectors and contract_ingest_jobs
|       |-- optionally indexes Qdrant
|   |-- services/contract_ingest_queue.py
|       |-- Redis queue for async ingestion
|   |-- services/contract_graph_service.py and graph/*
|       |-- graph-side enrichment/relationships
```

```text
Letter drafting AI/LangGraph
|-- Frontend
|   |-- LetterWorkflowPage.tsx
|   |-- LetterStrategicPlanPage.tsx
|   |-- LetterDraftPage.tsx
|   |-- hooks/useLetterDrafting.ts
|   |-- hooks/useLanggraphDraft.ts
|   |-- hooks/useLanggraphStrategyPlan.ts
|   |-- components/langgraph/*
|
|-- APIs
|   |-- /api/letters/{letter_id}/drafting/runs
|   |-- /api/letters/{letter_id}/drafting/analyze-incoming
|   |-- /api/letters/{letter_id}/drafting/prepare-plan
|   |-- /api/letters/{letter_id}/drafting/generate-draft
|   |-- /api/letters/{letter_id}/drafting/runs/{run_id}/validate
|   |-- /api/ai-assistant/langgraph/draft
|   |-- /api/ai-assistant/langgraph/background
|   |-- /api/ai-assistant/langgraph/strategy-plan
|   |-- /api/ai-assistant/langgraph/config
|   |-- /api/ai-assistant/langgraph/runs/{letter_id}
|
|-- Backend
|   |-- routers/letter_drafting.py
|   |-- services/letter_drafting/*
|   |-- ai_workflows/langgraph/letter_pipeline.py
|   |-- models/letter_drafting.py
|   |-- models/ai_models.py
|   |-- collections: letter_draft_runs, letter_draft_events, draft_context_packs, prompt_templates
```

## Razorpay Payment and Subscription Flow

```text
Configuration
|-- backend/rbac_backend/core/config.py
|   |-- PAYMENT_PROVIDER defaults to noop
|   |-- RAZORPAY_KEY_ID
|   |-- RAZORPAY_KEY_SECRET
|   |-- RAZORPAY_WEBHOOK_SECRET
|   |-- STRIPE_WEBHOOK_SECRET
|
|-- .env.example and docker-compose.prod.yml
|   |-- payment provider and Razorpay env slots are defined for production stack
|
|-- backend/rbac_backend/requirements.txt
|   |-- razorpay==1.4.2 is present
```

```text
Backend payment gateway abstraction
|-- services/payment_gateway.py
|   |-- PaymentGatewayInterface
|   |-- NoOpPaymentGateway
|   |-- RazorpayGateway
|   |-- StripeGateway stub
|   |-- get_payment_gateway()
|
|-- RazorpayGateway
|   |-- create_customer()
|   |-- create_subscription()
|       |-- calls razorpay.Client(...).subscription.create()
|       |-- uses plan_code as Razorpay plan_id
|       |-- total_count derived from billing_period
|       |-- returns short_url as checkout_url when provider supplies it
|   |-- update_subscription()
|       |-- notes Razorpay does not support in-place plan swaps
|   |-- cancel_subscription()
|   |-- reactivate_subscription()
|   |-- create_invoice()
|   |-- charge_invoice()
|       |-- reports asynchronous webhook-based charges for Razorpay
|   |-- verify_webhook_signature()
|       |-- HMAC-SHA256 compare against X-Razorpay-Signature style header
|   |-- parse_webhook_event()
|       |-- maps subscription.charged/payment.captured/order.paid to payment.succeeded
|       |-- maps subscription.activated/authenticated to subscription.activated
|       |-- maps subscription.cancelled/completed to subscription.cancelled
|       |-- maps subscription.halted/pending to subscription.halted
|       |-- maps payment.failed to payment.failed
```

```text
Plan/subscription APIs
|-- router: backend/rbac_backend/routers/rbac_monetization.py
|-- service: backend/rbac_backend/services/monetization_service.py
|-- models: backend/rbac_backend/models/rbac_monetization.py
|
|-- GET /api/rbac-monetization/plan-catalog
|-- GET /api/rbac-monetization/plans
|-- GET /api/rbac-monetization/plans/{plan_code}
|-- GET /api/rbac-monetization/plan-settings
|-- GET /api/rbac-monetization/plan-settings/effective-services
|-- PUT /api/rbac-monetization/plan-settings/scope
|-- POST /api/rbac-monetization/plans
|-- PUT /api/rbac-monetization/plans/{plan_id}
|-- GET/POST/PUT /api/rbac-monetization/add-ons
|-- GET /api/rbac-monetization/subscriptions
|-- GET /api/rbac-monetization/subscriptions/{subscription_id}
|-- POST /api/rbac-monetization/subscriptions
|-- POST /api/rbac-monetization/subscriptions/checkout
|-- PUT /api/rbac-monetization/subscriptions/{subscription_id}
|-- POST /api/rbac-monetization/subscriptions/{subscription_id}/upgrade
|-- POST /api/rbac-monetization/subscriptions/{subscription_id}/downgrade
|-- POST /api/rbac-monetization/subscriptions/{subscription_id}/cancel
|-- POST /api/rbac-monetization/subscriptions/{subscription_id}/reactivate
|-- POST /api/rbac-monetization/subscriptions/{subscription_id}/change-period
|-- POST /api/rbac-monetization/subscriptions/{subscription_id}/add-ons/add
|-- POST /api/rbac-monetization/subscriptions/{subscription_id}/add-ons/remove
|-- GET /api/rbac-monetization/subscriptions/{subscription_id}/history
|-- GET /api/rbac-monetization/subscriptions/{subscription_id}/invoice-preview
|-- POST /api/rbac-monetization/subscriptions/start-trial
|-- POST /api/rbac-monetization/subscriptions/{subscription_id}/convert-trial
|-- GET /api/rbac-monetization/billing/summary/{organization_id}
|-- GET /api/rbac-monetization/billing/history/{organization_id}
|-- POST /api/rbac-monetization/usage-events
|-- POST /api/rbac-monetization/billing-records
```

```text
Webhook flow
|-- API: POST /api/billing/webhooks/{provider}
|-- router: backend/rbac_backend/routers/billing_webhooks.py
|-- service: backend/rbac_backend/services/billing_webhook_service.py
|-- gateway: backend/rbac_backend/services/payment_gateway.py
|-- collection: billing_webhook_events
|-- related collections:
|   |-- subscriptions
|   |-- billing_records
|   |-- entitlements
|   |-- usage_events / usage_periods / quota_buckets
```

```text
Frontend billing screens/components
|-- PlanSettingsPage.tsx
|   |-- plan settings per organization/project
|   |-- uses useStepUp("subscription.entitlement.manage")
|   |-- services/plan-settings-api.ts
|
|-- SubscriptionManagementPage.tsx
|   |-- subscription list, active subscription, plan changes, cancel/reactivate,
|       trial conversion, billing period changes, add-ons, history, invoice preview
|   |-- uses useStepUp() for sensitive subscription operations
|   |-- services/plan-settings-api.ts
|
|-- services/billing-api.ts
|   |-- startSubscriptionCheckout()
|   |-- POST /api/rbac-monetization/subscriptions/checkout
|   |-- redirectToCheckout(checkout)
```

## Deployment and Environment Configuration

```text
Local compose stack: docker-compose.yml
|-- backend
|   |-- ./backend Dockerfile
|   |-- env_file backend/.env
|   |-- port 8000
|   |-- depends on mongo, qdrant, falkordb, redis
|
|-- worker
|   |-- python -m rbac_backend.worker
|   |-- same backend env
|
|-- client
|   |-- ./client Dockerfile
|   |-- frontend build/serve container
|
|-- mongo
|   |-- mongo:8.0
|
|-- qdrant
|   |-- qdrant/qdrant:latest
|   |-- config/secrets/qdrant_api_key
|
|-- falkordb
|   |-- falkordb/falkordb:latest
|
|-- redis
|   |-- redis:7-alpine
|
|-- graphiti
|   |-- ./services/graphiti
|
|-- clamav
|   |-- clamav/clamav:latest
|
|-- gateway
|   |-- httpd:2.4
|   |-- config/httpd.conf
|   |-- proxies /api/ to backend:8000/api/
```

```text
Production compose stack: docker-compose.prod.yml
|-- Requires DATABASE_URL, MONGODB_REPLICA_SET, SECRET_KEY, CORS_ORIGINS
|-- Sets AUTH_COOKIE_SECURE=true
|-- Requires OPENAI_API_KEY, AWS credentials, Redis/FalkorDB/Qdrant secrets
|-- Exposes backend, worker, qdrant, falkordb, redis, graphiti, clamav, httpd gateway
|-- Uses persistent volumes:
|   |-- backend_uploads
|   |-- backend_logs
|   |-- qdrant_data
|   |-- qdrant_snapshots
|   |-- falkordb_data
|   |-- redis_data
```

```text
Mongo production option
|-- docker-compose.mongo-replicaset.yml
|   |-- mongo1, mongo2, mongo3
|   |-- replica set rs0
|   |-- scripts/mongo/init-replica-set.sh
```

```text
Key backend env groups from backend/rbac_backend/core/config.py and .env.example
|-- Database: DATABASE_URL, MONGODB_DATABASE, MONGODB_REPLICA_SET, pool/timeouts
|-- Auth/security: SECRET_KEY, AUTH_COOKIE_NAME, AUTH_COOKIE_SECURE, AUTH_COOKIE_SAMESITE, AUTH_COOKIE_DOMAIN
|-- CORS: CORS_ORIGINS
|-- Redis/runtime: APP_REDIS_URL, RUNTIME_STATE_REDIS_URL, CONTRACT_QUEUE_REDIS_URL
|-- Storage: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3 settings, local upload paths
|-- AI: OPENAI_API_KEY, OPENAI_MODEL
|-- Retrieval: QDRANT_URL/API key/collection/vector size/distance
|-- Graph: FALKORDB_URL/HOST/PORT/GRAPH_NAME/PASSWORD, GRAPH_PROVIDER
|-- LangGraph: LANGGRAPH_ENABLED, LANGGRAPH_API_TOKEN, model/prompt/timeout settings
|-- Payment: PAYMENT_PROVIDER, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
|-- OIDC: OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, OIDC_REDIRECT_URI
|-- Antivirus: ANTIVIRUS_ENABLED, ClamAV host/port, fail-open controls
|-- Observability: metrics and tracing settings
```

```text
Frontend env
|-- client/.env.example
|   |-- VITE_API_BASE_URL
|   |-- VITE_LANGGRAPH_ENABLED=false
|
|-- client/src/config/api.ts
|   |-- supports build-time env, runtime window override, and host-derived API URL
```

```text
CI/CD
|-- .github/workflows/ci.yml
|   |-- backend-checks:
|       |-- Mongo service
|       |-- install backend/rbac_backend/requirements.txt
|       |-- python -m compileall
|       |-- pytest backend/rbac_backend/tests -q
|   |-- frontend-checks:
|       |-- npm install
|       |-- lint
|       |-- tests
|       |-- build
|   |-- security checks:
|       |-- pip-audit
|       |-- frontend dependency scan
|       |-- Docker image build/scan
```

## Cross-Cutting Observability, Errors, and Tests

```text
Error handling
|-- backend/rbac_backend/utils/error_handler.py
|-- backend/rbac_backend/utils/exceptions.py
|-- backend/rbac_backend/services/exceptions.py
|-- backend/rbac_backend/routers/error_handler.py
|-- backend/rbac_backend/tests/test_error_responses.py
```

```text
Logging/observability
|-- backend/rbac_backend/services/observability.py
|-- backend/rbac_backend/observability/service.py
|-- backend/rbac_backend/utils/pipeline_logging.py
|-- /api/v1/observability/logs
|-- /api/v1/observability/analytics
|-- /health, /health/ready, /health/observability, /metrics
```

```text
Backend tests
|-- pytest.ini -> backend/rbac_backend/tests
|-- coverage areas visible from filenames:
|   |-- auth/login/token hardening/CSRF/SSO
|   |-- RBAC matrix/policy/tenant isolation
|   |-- documents listing/export/references/processing/concurrency/update
|   |-- retrieval basics/quality/scope isolation/vector sync
|   |-- billing webhooks/RBAC monetization
|   |-- claims/approvals/SLA/tasks/notifications
|   |-- config validation/observability/tracing
```

```text
Frontend tests
|-- client/src/pages/__tests__/
|   |-- LoginPage.login.test.tsx
|   |-- Dashboard.test.tsx
|   |-- DocumentsPage.under-process-guard.test.tsx
|   |-- DocumentsPage.request-draft-navigate.test.tsx
|   |-- LetterWorkflowPage.integration.test.tsx
|   |-- LetterSummaryPage.test.tsx
|   |-- LetterDraftPage.basic-render.test.tsx
|   |-- ProjectsPage.organization-filter.test.tsx
|   |-- ReferencePage.parsed-references.test.tsx
```

## High-Level System Flow

```text
User browser
|-- React Router protected routes
|-- API client with cookie credentials and CSRF header
|
v
HTTP gateway / Vite proxy / direct API URL
|
v
FastAPI backend
|-- Auth middleware/dependencies
|-- PolicyService and entitlement checks
|-- Router-specific controllers/services
|
v
MongoDB
|-- tenant-scoped business collections
|-- audit, usage, billing, retrieval, workflow records
|
+-- Local/S3 storage
|   |-- immutable file_objects
|   |-- document_versions
|
+-- Redis
|   |-- runtime state
|   |-- contract ingest queue
|
+-- Qdrant
|   |-- vector search for chunks/contracts
|
+-- FalkorDB/Graphiti
|   |-- knowledge graph sidecar/integration
|
+-- OpenAI/LangGraph/Pydantic AI/LlamaIndex
|   |-- embeddings, generation, drafting, planning, RAG
|
+-- Razorpay
    |-- hosted subscription checkout
    |-- subscription/payment webhooks
    |-- billing records and entitlements
```
