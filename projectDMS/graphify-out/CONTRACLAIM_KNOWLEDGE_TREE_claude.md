# ContraClaim DMS — Knowledge Tree (Graphify-derived)

> Repository: `ManishPandey21/contraclaim-dms`
> Generated: 2026-06-18 · Method: built with **Graphify** then synthesized.
> Graph: `graphify-out/graph.json` — **15,010 nodes · 34,855 edges · 632 communities** · 811 files · 86% EXTRACTED / 14% INFERRED · $0 LLM cost (AST extraction).
> Built from commit `2a6b307b`. Refresh with `graphify update .`; query with `graphify query "<question>"`.

ContraClaim DMS is a contract/claims **Document Management System**: a FastAPI (Python 3.11) backend + Vite/React (TypeScript) frontend, backed by MongoDB (Motor), Qdrant (vectors), FalkorDB (graph), Redis (queue/cache), with an Apache `httpd` gateway as the single entry point. It adds an AI letter-drafting workflow, RAG over contracts, RBAC, and a pluggable payment/subscription layer.

**God nodes (most-connected core abstractions, from the graph):**
`DocumentService` (202 edges) · `DocumentProcessingConfig` (152) · `ConfigNamespace` (141) · `PolicyService` (140) · `get_database()` (132) · `EnhancedApiService` (122).

---

## 1. Backend modules

Root package: `backend/rbac_backend/`. Entrypoint `main.py` (FastAPI app, startup/shutdown hooks, router registration). Separate `worker.py` runs queue consumers and background services.

### 1.1 Routers (`routers/`, ~45 — HTTP surface, all mounted under `/api`)
- **Auth / identity:** [auth.py](backend/rbac_backend/routers/auth.py), `sso.py`, `users.py`, `permissions.py`, `roles.py`, `profiles.py`, `security_utils.py`
- **Org / tenancy:** `organizations.py`, `projects.py`, `parties.py`, `representatives.py`, `email_groups.py`
- **Documents & contracts:** `documents.py`, `contracts.py`, `contract_appraisal.py`, `folder_structure.py`, `tags.py`, `search.py`, `storage_settings.py`, `storage_sync.py`
- **Claims & workflow:** `claims.py`, `concerns.py`, `input_requests.py`, `tasks.py`, `sla.py`, `dashboard.py`, `reports.py`
- **Letters (AI drafting):** `letters.py`, `letter_drafting.py`, `letter_templates.py`, `deep_planning.py`
- **AI / retrieval:** [retrieval_engine.py](backend/rbac_backend/routers/retrieval_engine.py) (`rag()`, `search()`, `agent()`, `contract_qa()`, `create_ingestion_job()`, `reconcile_vectors()`), `ai_assistant.py`, `rag_utils.py`
- **Billing:** `rbac_monetization.py`, `billing_webhooks.py`
- **Platform:** `health.py`, `performance.py`, `config_management.py`, `notifications.py`, `email.py`, `email_share.py`, `smtp_settings.py`, `contact.py`, `ws.py` (websockets), `error_handler.py`

### 1.2 Services (`services/`, ~90 — business logic; the bulk of the system)
- **AuthZ / RBAC:** [policy_service.py](backend/rbac_backend/services/policy_service.py) (`PolicyService`), `permission_service.py` (`PermissionService`), `role_service.py` (`RoleService`), `authorization_service.py`, `rbac_service.py`, `scope_service.py`, `step_up_service.py`, `data_initialization.py` (seeds permissions/roles/users/orgs/projects)
- **Documents / storage:** [document_service.py](backend/rbac_backend/services/document_service.py) (`DocumentService` — god node), `document_processor.py` (`DocumentProcessor`), `metadata_processor_service.py`, `ocr_service.py`, `antivirus_service.py`, `text_processing_service.py`, `file_service.py`/`file_object_service.py`, `s3_service.py`, `folder_service.py`, `bulk_upload_service.py`, `upload_streaming.py`, `upload_limits.py`, `document_bulk_download_service.py`, `document_linking_service.py`, `document_audit_service.py`
- **Contracts / claims:** `contract_service.py`, `contract_categorizer.py`, `contracts_ingest.py`, `contract_ingest_queue.py`, `contract_graph_service.py`, `claim_service.py`, `claim_assessment_service.py`, `concern_service.py`, `approval_service.py`, `evidence_bundle_service.py`, `input_request_service.py`
- **AI / RAG:** `openai_service.py` (`OpenAIService`), `pydantic_ai_service.py`, `langchain_vector_service.py`, `llamaindex_service.py`, `falkordb_vector_service.py`, `falkor_graph_service.py`, `ai_service.py`, `llm_config_service.py`, `conversation_service.py`, `strategy_context_service.py`
- **Letters / workflow:** `letter_service.py`, `letter_template_service.py`, `template_service.py`, `workflow.py`, `sla_service.py`, `report_service.py`
- **Billing / monetization:** `payment_gateway.py`, `monetization_service.py`, `subscription_lifecycle_service.py`, `entitlement_service.py`, `allocation_service.py`, `usage_metering_service.py`, `billing_webhook_service.py`
- **Platform / infra:** `database_service.py`, `cache_service.py`, `runtime_state.py`, `background_jobs.py`, `data_sync.py`, `observability.py`, `performance_monitor.py`/`performance_service.py`, `audit_event_service.py`, `notifications.py`, `email_service.py`/`email_group_service.py`, `smtp_settings_service.py`, `storage_settings_service.py`, `storage_key_builder.py`, `setup_service.py`

### 1.3 Core & config
- `core/`: [config.py](backend/rbac_backend/core/config.py) (Pydantic Settings, `CRITICAL_FIELDS`, `validate_runtime_configuration()`), [database.py](backend/rbac_backend/core/database.py) (`connect()`, `get_database()`, index management), [security.py](backend/rbac_backend/core/security.py) (`get_current_user()`, `require_permission()`, `CurrentUser`), `csrf.py`, `errors.py`, `permissions.py`, `constants.py`, `templates.py`, `email.py`
- `config/`: `settings.py`, `document_processing_config.py` (`DocumentProcessingConfig` — god node), `openai_config.py`, `prompts.py`, `config-adapter.py`

### 1.4 AI workflows (`ai_workflows/`)
- `langgraph/letter_pipeline.py` — LangGraph letter-drafting pipeline (gated by `LANGGRAPH_ENABLED`, default off), `langgraph.py`, `tools.py`

---

## 2. Frontend modules

Root: `client/` (Vite + React + TypeScript, shadcn/ui — see `components.json`). Entry `client/src/main.tsx`.

### 2.1 Pages (`src/pages/`, ~50)
- **Auth/landing:** `LoginPage`, `RegisterPage`, `LandingPage`, `Index`, `Overview`, `Dashboard`, `NotFound`
- **Documents/contracts:** `DocumentsPage`, `EnhancedDocumentsPage`, `DocumentViewerPage`, `UploadPage`, `ContractsPage`, `ContractsUploadPage`, `ContractsSearchPage`, `ContractAppraisalPage`, `ContractQAPage`, `FolderStructurePage`, `ShareDocumentPage`, `TagsPage`
- **Claims/workflow:** `ClaimsRegisterPage`, `WorkflowPage`, `TasksPage`, `SLATrackerPage`, `PartiesInvolvedPage`, `RepresentativesPage`, `ReportsAnalyticsPage`
- **Letters (AI):** `LetterWorkflowPage`, `LetterInputPage`, `LetterDraftPage`, `LetterStrategicPlanPage`, `LetterReviewPage`, `LetterApprovalPage`, `LetterCompletedPage`, `LetterSummaryPage`, `LetterQualityDashboardPage`, `LetterTemplatePage`, `LetterTemplateEditorPage`
- **Admin/billing:** `UsersPage`, `PermissionsPage`, `OrganizationsPage`, `ProjectsPage`, `SettingsPage`, `ProfilePage`, `PlanSettingsPage`, `SubscriptionManagementPage`, `NotificationCenterPage`, `EmailGroupsPage`, `ReferencePage`, `HealthPage`

### 2.2 Component groups (`src/components/`)
`auth/`, `claims/`, `contract-appraisal/`, `dashboard/`, `document-viewer/`, `documents/`, `langgraph/`, `letter-template/`, `letter-workflow/`, `reports/`, `search/`, `settings/`, `layout/`, `routing/`, `common/`, `error-boundary/`, `ui/` (shadcn primitives). Plus 16 hooks in `src/hooks/`.

### 2.3 Frontend service layer (`src/services/`)
`http.ts` (axios/fetch core), `api.ts` (base `api` client), `enhanced-api.ts` (`EnhancedApiService` — god node), `auth.ts`, `session-api.ts`, `step-up.ts`, plus per-domain clients: `documents-api.ts`, `contracts-api.ts`, `claims-api.ts`, `projects-api.ts`, `organizations-api.ts`, `tags-api.ts`, `tasks-api.ts`, `sla-api.ts`, `search-api.ts`, `dashboard-api.ts`, `audit-api.ts`, `letter-workflow-api.ts`, `strategy.ts`, `email-groups-api.ts`, `email-service.ts`, `smtp-settings-api.ts`, `storage-settings-api.ts`, `billing-api.ts`, `plan-settings-api.ts`.

---

## 3. API integration

- **Single entry point:** the Apache gateway ([config/httpd.conf](config/httpd.conf)) serves the built frontend and reverse-proxies `/api/ → backend:8000/api/`, so browser and API share one origin (`http://localhost` in the local stack).
- **Client URL resolution:** `client/src/config/api.ts` `preferLocalProxy()` returns a relative `/api` when page and API are same-host different-origin → forces traffic through the gateway. Build-time base via `VITE_API_BASE_URL`.
- **Transport/security:** HttpOnly cookie JWT (`cc_access_token`) + CSRF double-submit (`cc_csrf_token`, see `core/csrf.py`). The frontend `http.ts`/`api.ts` attach the CSRF header; `step-up.ts` handles step-up re-auth challenges.
- **Surface:** ~278 OpenAPI paths under `/api/...` (e.g. `/api/claims`, `/api/login`, `/api/ai-assistant/*`, `/api/retrieval/*`). Health lives at root `/health/ready` (not under `/api`).
- **Realtime:** `routers/ws.py` for websocket push (notifications/workflow).

---

## 4. Database models

Persistence is **MongoDB via Motor** (async). `core/database.py` owns the client (`connect()`, `get_database()` — god node, 132 edges) and index creation; models are plain Pydantic classes (not Beanie ODM) persisted as raw documents keyed by Mongo `_id`.

Models (`models/`):
- **Identity/RBAC:** `user.py` (`User`), `role.py` (`Role`), `permission.py` (`Permission`), `auth_models.py`, `user_models.py`, `organization.py`, `project.py`
- **Documents:** `document.py`, `documents.py`, `document_metadata.py`, `document_vector.py`, `folder.py`/`folder_models.py`, `storage_architecture.py`, `storage_settings.py`, `tag.py`
- **Contracts/claims:** `contract_models.py`, `contract_appraisal.py`, `claim.py`, `concern.py`, `approval.py`, `input_request.py`, `task.py`, `party.py`, `representative.py`, `report.py`
- **Letters/AI:** `letter.py`, `letter_drafting.py`, `letter_template.py`, `langgraph_run.py`, `ai_models.py`
- **Billing:** `rbac_monetization.py` (`AccountType`, `PlanBase/PlanCreate`, `SubscriptionCreate/Update`, `AddOn*`, `BillingPeriod`, `SubscriptionChangeType`, trial/upgrade/cancel request DTOs)
- **Comms/misc:** `notification.py`, `email_group.py`, `email_models.py`, `smtp_settings.py`, `performance_models.py`, `common.py`

---

## 5. Authentication & RBAC

**AuthN:** `core/security.py` — `get_current_user()` resolves the JWT cookie into a `CurrentUser`; `routers/auth.py` `login()` (JSON `{email,password}`) issues the token and sets cookies; `routers/sso.py` + `oidc_service.py`/`authentication_service.py` provide SSO/OIDC. `step_up_service.py` (`require_step_up()`) enforces re-auth for sensitive actions; `utils/rate_limiter.py` throttles auth.

**AuthZ (deny-by-default):**
- `core/security.py` `require_permission()` dependency guards routes.
- `PolicyService` (god node, 140 edges) is the central decision point — every claims-router action (`create_claim`, `approve_claim`, `assess_claim`, `assign_claim_reviewer`, `return_claim`, `set_claim_status`, `list_claims`, …) references it. The retrieval engine uses `_authorize_scope()` / `_require_scope_values()` for scope enforcement.
- `PermissionService.user_has_permission()` resolves a user's roles → permissions; `RoleService` manages role docs.
- **Seeding:** `data_initialization.py` (`DataInitializer.initialize_all_data()`) loads `initial_data/` defaults: 90 permissions, 14 roles, dev users, orgs, projects. Roles store Mongo `_id`; the model field is `id` (mapped on read/write).
- **Audit:** `utils/audit_logger.py` (`AuditLogger`) + `audit_event_service.py` record privileged operations.

---

## 6. Document upload & processing

Pipeline orchestrated by `DocumentProcessor` (`services/document_processor.py`, created via `create_document_processor()`), which composes:
`FileService` → `AntivirusService` (ClamAV, gated by `ANTIVIRUS_ENABLED`) → `OCRService` (ocrmypdf/tesseract) → `TextProcessingService` → metadata extraction via `PydanticAIService`/`OpenAIService` (with `MetadataProcessorService`, falling back to legacy regex when AI is disabled) → `DatabaseService` persistence.

- **Upload paths:** `routers/documents.py`, `routers/contracts.py`, `bulk_upload_service.py`, `upload_streaming.py`, `upload_limits.py`; storage to S3/local via `s3_service.py` / `file_object_service.py` / `storage_key_builder.py`.
- **Async ingest:** `contract_ingest_queue.py` (Redis queue) + `worker.py` consume ingest jobs; `contracts_ingest.py`/`contract_categorizer.py` classify; `document_audit_service.py` and `storage_sync.py` reconcile.
- **Config:** `DocumentProcessingConfig` (god node, 152 edges) centralizes feature flags (`use_pydantic_ai`, `pydantic_ai_model`, OCR/embedding toggles).

---

## 7. AI / RAG flow

Entry router `retrieval_engine.py` exposes `search()`, `rag()` (`RagRequest`→`RagResponse`), `agent()`, `contract_qa()`, `create_ingestion_job()`/`get_ingestion_job()`, `list_logs()`, `reconcile_vectors()`, `analytics()` — all scope-gated via `PolicyService`.

- **Embeddings/vectors:** `langchain_vector_service.py` (`LangChainVectorService`) and `falkordb_vector_service.py` (`FalkorDBVectorService`) generate/store embeddings; vector store is **Qdrant** (`QDRANT_URL`), with FalkorDB as graph/alt vector backend. `llamaindex_service.py` provides LlamaIndex retrieval.
- **LLM:** `openai_service.py` (`OpenAIService`) and `pydantic_ai_service.py` (Agent + `OpenAIModel`) drive generation/metadata; `llm_config_service.py` selects backend/model.
- **Letter drafting:** `letter_service.py` + `ai_workflows/langgraph/letter_pipeline.py` (LangGraph) implement the strategy→draft→review→approve flow; `GRAPH_PROVIDER=direct_falkor` and `LANGGRAPH_ENABLED` toggle the graph path. Graphiti is an experimental, off-by-default service.
- **Conversation/context:** `conversation_service.py`, `strategy_context_service.py`, `contract_graph_service.py`.

---

## 8. Razorpay payment / subscription flow

Pluggable gateway in `services/payment_gateway.py`: abstract `PaymentGatewayInterface` (ABC) with concrete adapters — the default **`NoOpPaymentGateway`** records actions locally (e.g. `noop_cust_*`, `noop_sub_*`, `noop_inv_*`) without contacting a provider; **Razorpay** is the intended production adapter (`razorpay==1.4.2`). Selected by `PAYMENT_PROVIDER`.

- **Lifecycle:** `subscription_lifecycle_service.py` + `monetization_service.py` manage trial → active → upgrade/downgrade → cancel, using `rbac_monetization.py` models (`SubscriptionCreate/Update`, `StartTrialRequest`, `ConvertTrialRequest`, `UpgradeDowngradeRequest`, `ChangeBillingPeriodRequest`, `CancelSubscriptionRequest`, `AddOn*`, `PlanCreate`, `BillingPeriod`).
- **Entitlements/usage:** `entitlement_service.py`, `allocation_service.py`, `usage_metering_service.py` enforce per-plan limits (users, storage) tied to `AccountType`.
- **Webhooks:** `routers/billing_webhooks.py` → `billing_webhook_service.py` (`WebhookEvent`, `PaymentGatewayInterface`) verify and apply provider events. Endpoint pattern `/api/billing/webhooks/{provider}`.
- **Frontend:** `SubscriptionManagementPage.tsx`, `PlanSettingsPage.tsx` via `billing-api.ts` / `plan-settings-api.ts`.

---

## 9. Deployment & environment configuration

**Orchestration:** `docker-compose.yml` services — `backend` (:8000, `/health/ready`), `client` (Vite build → static serve), `contract-worker` (queue consumer), `mongo` (8.0), `qdrant`, `falkordb`, `redis`, `graphiti` (profile `graph-experimental`, off by default), `clamav` (no arm64 image), `gateway` (`httpd:2.4`, :80). Networks segmented: `data-net`, `service-net`, `edge-net`. Named volumes: `mongo_data`, `qdrant_data`, `qdrant_snapshots`, `falkordb_data`, `redis_data`. Secret: `config/secrets/qdrant_api_key`.

**Gateway:** [config/httpd.conf](config/httpd.conf) reverse-proxies `/api/`→backend and `/`→client; `httpd-tls.conf.example` for TLS.

**Images:** `backend/Dockerfile` (python:3.11-slim; needs `libmagic1` for python-magic), `client/Dockerfile` (node:20-alpine multi-stage).

**Config validation:** `core/config.py` `validate_runtime_configuration()` rejects blank/placeholder values for `CRITICAL_FIELDS` (`DATABASE_URL`, `SECRET_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET_NAME`, `OPENAI_API_KEY`, `SMTP_USERNAME`, `SMTP_PASSWORD`). Production-only hardening (secure cookies, replica-set Mongo) is skipped when `ENVIRONMENT=development`. Key env: `DATABASE_URL`, `QDRANT_URL`/`QDRANT_API_KEY`, Redis/FalkorDB hosts, `GRAPH_PROVIDER`, `LANGGRAPH_ENABLED`, `ANTIVIRUS_ENABLED`, `PAYMENT_PROVIDER`, `AUTH_COOKIE_SECURE`, `CORS_ORIGINS`.

**Reference docs:** `docs/history/DEPLOYMENT_GUIDE.md`, `Production_Readiness_Audit_Plan_03052026.md`, `FALKORDB_PLAN.md`, `LANGGRAPH_GRAPHITI_AUDIT.md`, `Phase6_Observability_Incident_Response.md`.

---

## How to explore further (Graphify)

```bash
graphify query "How does claim approval enforce permissions?"   # BFS context
graphify explain "PolicyService"                                # node + neighbors
graphify path "DocumentService" "get_database()"                # shortest path
graphify update .                                               # refresh after code changes
graphify label . --backend ollama --model llama3.2:3b          # name the 632 communities
```

*Edges are typed `EXTRACTED` (AST-certain) or `INFERRED` (heuristic, avg confidence ~0.52) — treat INFERRED relationships as leads to verify, not facts.*
