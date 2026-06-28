# ContraClaim DMS — Knowledge Tree & Backend↔Frontend Integration Audit

> Repository: `ManishPandey21/contraclaim-dms` · Generated: 2026-06-18
> Method: Graphify knowledge graph (`graphify-out/` — 15,010 nodes / 34,855 edges / 632 communities, commit `2a6b307b`) + live OpenAPI introspection (`/openapi.json`, **353 operations / 277 unique routes**) + source cross-reference of routers, services, models, and `client/src`.
> Scope: **read-only audit**. No code was modified, refactored, formatted, or committed. This file is the only artifact created.

---

## Part 0 — Executive Summary

| Dimension | Finding |
|---|---|
| Backend operations | **353** (277 unique routes) across ~45 routers, ~90 services, ~40 models |
| Frontend wiring | **~184 routes linked**, **~93 candidate-unlinked** (incl. ~15 backend-only by design: webhooks, health, internal, metrics) |
| Authorization | **Dual model** — `require_permission()` decorator (CRUD routers) + inline `PolicyService.authorize()` (claims, letters, drafting, appraisal, monetization). Both deny-by-default. |
| Payment | **Razorpay fully implemented** (`RazorpayGateway`, HMAC webhook verify); hosted-checkout redirect model. Frontend wired for plan/subscription/trial/upgrade/invoice-preview; **gaps in receipt download, payment-failure UX, admin plan/add-on CRUD**. |
| Tests | 57 test files incl. `test_rbac_monetization.py`, `test_billing_webhooks.py`, `test_route_inventory.py`. Coverage thin on AI/letter-drafting and contract-appraisal. |
| **Production readiness (overall)** | **6.5 / 10** — solid auth/RBAC/payment core; gaps in unlinked admin surfaces, receipt/failure UX, test depth, observability hardening. |

---

## Part 1 — Knowledge Tree

### 1.1 Backend modules (`backend/rbac_backend/`)
- **Entry:** `main.py` (FastAPI app, startup/shutdown, router registration), `worker.py` (queue consumers, background services).
- **Routers (~45):** auth/sso/users/permissions/roles/profiles; organizations/projects/parties/representatives/email_groups; documents/contracts/contract_appraisal/folder_structure/tags/search/storage_settings/storage_sync; claims/concerns/input_requests/tasks/sla/dashboard/reports; letters/letter_drafting/letter_templates/deep_planning; retrieval_engine/ai_assistant/rag_utils; rbac_monetization/billing_webhooks; health/performance/config_management/notifications/email/email_share/smtp_settings/contact/ws.
- **Services (~90):** AuthZ (`PolicyService`, `PermissionService`, `RoleService`, `authorization_service`, `scope_service`, `step_up_service`, `data_initialization`); Documents (`DocumentService`★, `DocumentProcessor`, `ocr_service`, `antivirus_service`, `metadata_processor_service`, `s3_service`, `bulk_upload_service`); Contracts/Claims (`contract_service`, `contract_ingest_queue`, `claim_service`, `approval_service`, `evidence_bundle_service`); AI/RAG (`openai_service`, `pydantic_ai_service`, `langchain_vector_service`, `falkordb_vector_service`, `llamaindex_service`); Letters (`letter_service`, `workflow`, `sla_service`); Billing (`payment_gateway`, `monetization_service`, `subscription_lifecycle_service`, `entitlement_service`, `billing_webhook_service`, `usage_metering_service`); Platform (`database_service`, `cache_service`, `runtime_state`, `background_jobs`, `observability`, `audit_event_service`).
- **Core/config:** `core/config.py` (Settings, `CRITICAL_FIELDS`, `validate_runtime_configuration()`), `core/database.py` (`get_database()`★), `core/security.py` (`get_current_user`, `require_permission`), `core/csrf.py`, `config/document_processing_config.py`★.
- **AI workflows:** `ai_workflows/langgraph/letter_pipeline.py` (gated by `LANGGRAPH_ENABLED`).

★ = graph "god nodes" (highest connectivity): DocumentService (202), DocumentProcessingConfig (152), ConfigNamespace (141), PolicyService (140), get_database (132), EnhancedApiService (122).

### 1.2 Frontend modules (`client/`)
- **~50 pages** (`src/pages/`): auth/landing, documents/contracts, claims/workflow/tasks/SLA, letters (11 pages), admin (users/permissions/orgs/projects/settings), billing (`PlanSettingsPage`, `SubscriptionManagementPage`).
- **17 component groups** (`src/components/`): auth, claims, contract-appraisal, dashboard, document-viewer, documents, langgraph, letter-template, letter-workflow, reports, search, settings, layout, routing, common, error-boundary, ui.
- **Service layer** (`src/services/`): `http.ts` (axios core + CSRF), `api.ts`, `enhanced-api.ts`★, `auth.ts`, `step-up.ts`, plus ~25 per-domain clients. 16 hooks.

### 1.3 API integration
Single-origin via Apache gateway (`config/httpd.conf`): `/api/`→`backend:8000`, `/`→client. `client/src/config/api.ts` `preferLocalProxy()` forces relative `/api`. HttpOnly cookie JWT (`cc_access_token`) + CSRF double-submit (`cc_csrf_token`). ~278 paths under `/api`; health at root `/health`. Websockets via `routers/ws.py`.

### 1.4 Database models
MongoDB via Motor (async); `core/database.py` owns client + indexes; models are plain Pydantic persisted as raw docs keyed by `_id`. Families: identity/RBAC (`user`,`role`,`permission`,`organization`,`project`), documents (`document`,`document_metadata`,`document_vector`,`folder`,`tag`), contracts/claims (`contract_models`,`contract_appraisal`,`claim`,`concern`,`approval`,`input_request`,`task`,`party`,`representative`), letters/AI (`letter`,`letter_drafting`,`letter_template`,`langgraph_run`,`ai_models`), billing (`rbac_monetization`), comms (`notification`,`email_group`,`smtp_settings`).

### 1.5 Authentication & RBAC
`get_current_user()` resolves JWT cookie→`CurrentUser`; `routers/auth.py login()` issues token; SSO/OIDC via `sso.py`+`oidc_service`. Authorization is deny-by-default through **`PolicyService.authorize(user, permission, scope)`** (permission + entitlement + org/project scope) and/or `require_permission()` decorator. `step_up_service.require_step_up()` adds re-auth on sensitive ops. Seeded by `data_initialization` (90 permissions, 14 roles, dev users/orgs/projects). Audit via `AuditLogger`/`audit_event_service`.

### 1.6 Document upload & processing
`DocumentProcessor` pipeline: `FileService` → `AntivirusService` (ClamAV, `ANTIVIRUS_ENABLED`) → `OCRService` (ocrmypdf/tesseract) → `TextProcessingService` → AI metadata (`PydanticAIService`/`OpenAIService`, fallback legacy regex) → `DatabaseService`. Async ingest via Redis (`contract_ingest_queue` + `worker.py`). Storage S3/local (`s3_service`, `storage_key_builder`). Config centralised in `DocumentProcessingConfig`.

### 1.7 AI / RAG flow
`retrieval_engine.py` (`/api/v1/retrieval/*`): `search`, `rag`, `agent`, `contract-qa`, ingestion jobs, vector reconcile, observability — all scope-gated. Embeddings/vectors via `LangChainVectorService` (Qdrant) + `FalkorDBVectorService`; generation via `OpenAIService`/`pydantic_ai_service`. Letter drafting via `letter_service` + `ai_workflows/langgraph/letter_pipeline` (`LANGGRAPH_ENABLED`, `GRAPH_PROVIDER=direct_falkor`).

### 1.8 Razorpay payment / subscription flow
`services/payment_gateway.py`: `PaymentGatewayInterface` (ABC) with `NoOpPaymentGateway` (default), **`RazorpayGateway`** (primary, lazy SDK, HMAC `verify_webhook_signature`), `StripeGateway` (stub). `get_payment_gateway()` resolves `PAYMENT_PROVIDER`; settings `RAZORPAY_KEY_ID/KEY_SECRET/WEBHOOK_SECRET`. Lifecycle in `subscription_lifecycle_service`+`monetization_service`; webhooks in `billing_webhooks.py`→`billing_webhook_service` (signature verify, idempotent by provider event id, reconcile). Frontend: `billing-api.ts` (`startSubscriptionCheckout`→`checkout_url` redirect), `plan-settings-api.ts` (catalog/plans/trial/upgrade/invoice-preview), pages `SubscriptionManagementPage`/`PlanSettingsPage`/`RegisterPage`.

### 1.9 Deployment & environment configuration
`docker-compose.yml`: backend(:8000), client, contract-worker, mongo(8.0), qdrant, falkordb, redis, graphiti (profile `graph-experimental`), clamav (no arm64), gateway(httpd:80). Networks `data-net`/`service-net`/`edge-net`; volumes for each store; secret `config/secrets/qdrant_api_key`. `core/config.py validate_runtime_configuration()` rejects placeholder `CRITICAL_FIELDS` (`DATABASE_URL`,`SECRET_KEY`,`AWS_*`,`OPENAI_API_KEY`,`SMTP_*`); prod hardening skipped when `ENVIRONMENT=development`. Reference docs in `docs/history/`.

---

## Part 2 — API → Frontend Mapping Matrix

**Legend.** FE = linked in frontend (Y / N / **N\*** = backend-only by design). Permission column: `decorator` = `require_permission("…")`; `policy` = inline `PolicyService.authorize()`; `step-up` = also requires `require_step_up`; `auth` = authenticated only; `public` = unauthenticated. Priority for **unlinked** rows = integration urgency.

### 2.1 Auth / Identity / Profiles
| Method · Route | Backend | Purpose | Model | Perm | FE | FE / Suggested · Priority |
|---|---|---|---|---|---|---|
| POST /api/login | auth.py→auth_service | Email+password login, sets cookies | User | public | Y | services/auth.ts |
| POST /api/token | users.py→user_service | Legacy OAuth2 form login | User | public | N | **Dead/dup of /login — deprecate** · Low |
| POST /api/refresh | auth.py | Refresh access token | — | auth | Y | http.ts |
| POST /api/logout | users.py | Clear session | — | auth | Y | auth.ts |
| GET /api/csrf-token | core/csrf | Issue CSRF token | — | auth | Y | http.ts (template URL) |
| POST /api/step-up | auth.py→step_up_service | Issue step-up token (recent-auth) | — | auth | Y | step-up.ts |
| GET /api/me | auth.py | Current user (lite) | User | auth | Y | auth.ts |
| GET /api/users/me | users.py | Current user profile | User | auth | Y | session-api.ts |
| GET /api/profiles/me · PUT | profiles.py→user_service | Read/update own profile | User | auth(self) | Y | ProfilePage |
| POST /api/profiles/change-password | profiles.py | Change own password | User | auth(self) | Y | ProfilePage |
| POST /api/profiles/photo | profiles.py | Upload avatar | User | auth(self) | Y | ProfilePage |
| GET /api/profiles/health | profiles.py | Health probe | — | public | N\* | n/a |
| GET /api/auth/sso/login · callback | sso.py→oidc_service | SSO/OIDC handshake | User | public | N\* | redirect-based (LoginPage) |

*Note:* three overlapping "me" endpoints (`/me`, `/users/me`, `/profiles/me`) — see Duplicates (§4.4).

### 2.2 Users / Roles / Permissions (admin RBAC)
| Method · Route | Backend | Purpose | Model | Perm | FE | FE / Suggested · Priority |
|---|---|---|---|---|---|---|
| GET/POST /api/users | users.py→user_service | List / create users | User | decorator `users:read/create` | Y | UsersPage |
| GET/PUT/DELETE /api/users/{id} | users.py | Read/update/delete user | User | decorator `users:*` | Y | UsersPage |
| POST /api/users/{id}/lock · unlock | users.py | Lock/unlock account | User | decorator `users:lock/unlock` step-up | N | **Add lock/unlock action to UsersPage** · High |
| GET/POST /api/roles, /{id} CRUD | roles.py→role_service | Role CRUD | Role | decorator `roles:*` step-up | Y | PermissionsPage |
| GET/POST/DELETE /api/roles/{id}/permissions/{pid} | roles.py | Attach/detach permission | Role/Permission | decorator `platform.role.manage` | Y | PermissionsPage |
| GET/POST /api/permissions, /{id} CRUD | permissions.py→permission_service | Permission CRUD | Permission | decorator `platform.permission.manage` step-up | Y | PermissionsPage |
| POST /api/permissions/check | permissions.py | Check a permission | — | auth | Y | hooks/usePermissions |

### 2.3 Organizations / Projects / Parties / Representatives
| Method · Route | Backend | Purpose | Model | Perm | FE | FE / Suggested · Priority |
|---|---|---|---|---|---|---|
| GET/POST /api/organizations, /{id} CRUD | organizations.py→organization_service | Org CRUD | Organization | decorator `organizations:*` step-up | Y | OrganizationsPage |
| GET /api/organizations/{id}/stats | organizations.py | Org stats | Organization | decorator `organizations:read` | N | **Surface on OrganizationsPage detail** · Medium |
| POST /api/organizations/{id}/validate | organizations.py | Validate org data | Organization | decorator | N | **Inline validation pre-save** · Low |
| GET/POST /api/projects, /{id} CRUD | projects.py→project_service | Project CRUD | Project | decorator `projects:*` step-up | Y | ProjectsPage |
| POST /api/projects/{id}/deactivate | projects.py | Deactivate project | Project | decorator `projects:update` | Y | ProjectsPage |
| GET/PATCH /api/projects/{id}/notification-settings | projects.py | Project notif prefs | Project | decorator | Y | SettingsPage |
| GET /api/projects/{id}/representatives | projects.py | Project reps | Representative | auth | Y | ProjectsPage |
| GET /api/projects1 | projects.py | **Simplified list (legacy)** | Project | auth | N | **Dead/dup of /projects — remove** · Low |
| GET/POST /api/parties, /{id} CRUD | parties.py→party_service | Party CRUD | Party | decorator `parties:*` | Y | PartiesInvolvedPage |
| GET /api/parties/external | parties.py | External parties | Party | decorator `parties:read` | N | **Filter on PartiesInvolvedPage** · Low |
| POST /api/parties/{id}/projects/{pid} | parties.py | Associate party↔project | Party | decorator | N | **Add associate action** · Medium |
| GET/POST /api/representatives, /{id}, party/org/project reps | representatives.py→representative_service | Representative CRUD + associations | Representative | mostly `auth` (only 1 decorator) | Partly | RepresentativesPage — **missing-perm risk, §4.5** |

### 2.4 Documents
| Method · Route | Backend | Purpose | Model | Perm | FE | FE / Suggested · Priority |
|---|---|---|---|---|---|---|
| GET/POST /api/documents | documents.py→document_service | List / create document | Document | decorator `dms.document.view`/`documents:create` | Y | documents-api.ts |
| GET/PUT/DELETE /api/documents/{id} | documents.py | Read/update/delete | Document | decorator `documents:*` | Y | DocumentsPage |
| GET /api/documents/{id}/download | documents.py→s3_service | Download file | Document | decorator | Y | DocumentViewerPage |
| POST /api/documents/{id}/process · /internal/.../process | documents.py→DocumentProcessor | (Re)run pipeline / internal worker | Document | decorator step-up / internal | partial | **N\*** internal; **add "reprocess" button** · Medium |
| GET /api/documents/export | documents.py | Export listing | Document | decorator | N | **Export button on DocumentsPage** · Medium |
| GET /api/documents/vector-search | documents.py→langchain_vector_service | Vector search | DocumentVector | decorator | N | **Wire to search UI** · High |
| POST /api/documents/bulk-upload, GET template, GET {job}/status | documents.py→bulk_upload_service | Bulk upload + job status | Document | decorator `documents:upload` | partial | UploadPage — **template/status not wired** · Medium |
| GET /api/documents/download-all | documents.py | Zip all project docs | Document | decorator | Y | DocumentsPage |
| POST /api/documents/link, GET/{id}/linked | documents.py→document_linking_service | Link related docs | Document | decorator | Y | document-viewer |
| GET/POST/DELETE /api/documents/{id}/references | documents.py→reference_sync_service | Manage references | Document | decorator | Y | document-viewer |
| POST /api/documents/{id}/sync-references | documents.py | Trigger reference sync | Document | decorator | N | **Background; optional manual trigger** · Low |
| GET/POST /api/documents/{id}/comments | documents.py | Doc comments | Document | decorator `documents:comment` | Y | document-viewer |
| GET/POST/DELETE /api/documents/{id}/enclosures | documents.py | Enclosure mgmt | Document | decorator | Y | document-viewer |
| GET /api/documents/{id}/audit-events | documents.py→document_audit_service | Doc audit trail | Document | decorator | N | **Audit tab in DocumentViewer** · Medium |
| POST /api/documents/{id}/request-draft | documents.py | Request letter draft from doc | Letter | decorator | Y | letter-workflow |

### 2.5 Contracts & Contract Appraisal
| Method · Route | Backend | Purpose | Model | Perm | FE | FE / Suggested · Priority |
|---|---|---|---|---|---|---|
| GET /api/contracts/list, status | contracts.py→contract_service | List uploads / status | ContractModels | auth | Y | contracts-api.ts |
| POST /api/contracts/search | contracts.py | Search contracts | ContractModels | auth | Y | ContractsSearchPage |
| POST /api/contracts/upload-session, upload-chunk, upload-multipart | contracts.py→contracts_ingest | Chunked/multipart upload | ContractModels | auth | Y | ContractsUploadPage |
| GET /api/contracts/{id}/download | contracts.py | Download contract | ContractModels | auth | Y | ContractsPage |
| GET/POST /api/contracts/appraisal (+generate, jobs, {id} CRUD, approve/reject, citations, export docx/pdf, regenerate, review-comments, create-registers) | contract_appraisal.py→contract_service+report_service | Full appraisal lifecycle (22 ops) | ContractAppraisal | policy (appraisal perms) | mostly Y | ContractAppraisalPage — **citations, job-cancel, create-registers not wired** · Medium |
| GET /api/contracts/clauses, key-dates, obligations, risks (+PUT {item}) | contract_appraisal.py | Extracted clause/date/obligation/risk registers | ContractAppraisal | policy | N | **Registers tab in ContractAppraisalPage** · High |

### 2.6 Claims & Workflow
| Method · Route | Backend | Purpose | Model | Perm | FE | FE / Suggested · Priority |
|---|---|---|---|---|---|---|
| GET/POST /api/claims, /{id} CRUD | claims.py→claim_service | Claim CRUD | Claim | policy `claim.*` | Y | claims-api.ts / ClaimsRegisterPage |
| POST /api/claims/{id}/submit-for-review, assess, assign, approve, return, status | claims.py→approval_service+claim_assessment_service | Claim state machine | Claim | policy (scoped) | Y | ClaimsRegisterPage / WorkflowPage |
| GET /api/claims/{id}/assessments, approval | claims.py | Assessment & approval views | Claim | policy | Y | WorkflowPage |
| GET /api/claims/{id}/evidence-bundle | claims.py→evidence_bundle_service | Export evidence bundle | Claim | policy `claim.evidence_exported` | Y | ClaimsRegisterPage |
| GET/POST /api/concerns, /{id} CRUD | concerns.py→concern_service | Concern CRUD | Concern | decorator `concerns:*` | N | **ConcernsPage missing (data via WorkflowPage only?)** · High |
| GET/POST /api/input-requests/letter/{id} (+respond, close, suggested-key-points) | input_requests.py→input_request_service | Info requests on letters | InputRequest | decorator | partial | LetterInputPage — **suggested-key-points not wired** · Medium |
| GET /api/input_requests | input_requests.py | **Legacy list** | InputRequest | auth | N | **Dead/legacy — remove** · Low |
| GET/POST /api/tasks, /{id} CRUD, comments | tasks.py→workflow | Task CRUD + comments | Task | policy | Y | TasksPage |
| GET /api/sla/upcoming, breached | sla.py→sla_service | SLA deadline views | Task/Letter | auth | Y | SLATrackerPage |
| GET /api/dashboard/stats | dashboard.py | Dashboard KPIs | mixed | decorator | N | **Wire Dashboard tiles (uses mock?)** · High |
| GET/POST /api/reports (+preview, download), /api/audit/export | reports.py→report_service+audit_export | Reports & audit export | Report | policy `dms.report.view` | Y | ReportsAnalyticsPage |

### 2.7 Letters & AI Drafting
| Method · Route | Backend | Purpose | Model | Perm | FE | FE / Suggested · Priority |
|---|---|---|---|---|---|---|
| GET/POST /api/letters, /{id} CRUD | letters.py→letter_service | Letter CRUD | Letter | policy `drafting.request.*` | Y | letter-workflow-api.ts |
| POST /api/letters/{id}/submit, approve, complete, assign-drafter, reparent, move-to-strategy | letters.py | Letter lifecycle | Letter | policy | Y | LetterWorkflow/Approval pages |
| GET/PUT /api/letters/{id}/context-documents | letters.py | Context doc mgmt | Letter/Document | policy | Y | LetterInputPage |
| POST /api/letters/{id}/strategy/context, PATCH strategy-role | letters.py→strategy_context_service | Strategy context | Letter | policy | Y | LetterStrategicPlanPage |
| GET /api/letters/{id}/chain, tree | letters.py→conversation_service | Conversation chain/tree | Letter | policy | N | **Thread view in LetterWorkflow** · Medium |
| POST /api/letter-drafting/start, retrieval/exact-clause, exact-reference | letter_drafting.py→letter_service | Drafting session + exact retrieval | LetterDrafting | policy `drafting.*` | partial | LetterDraftPage — **exact-clause/reference not wired** · Medium |
| GET /api/letter-drafting/metrics/dashboard | letter_drafting.py | Quality metrics | — | policy `drafting.audit.view` | Y | LetterQualityDashboardPage |
| POST .../drafting/runs (+latest, {run}, accept-plan/draft, approve, assign-reviewer, audit, comments, confirm-analysis/plan, context-pack, critique, export, governance, issue, return-for-correction, revise, source-ledger, validate, analyze-incoming, generate-draft, prepare-plan) | letter_drafting.py→letter_service+langgraph | Full draft-run governance (23 ops) | LangGraphRun/LetterDrafting | policy | mostly Y | letter-workflow components — **a few run sub-views unwired** · Medium |
| GET/POST /api/letter-templates, /{id} CRUD | letter_templates.py→letter_template_service | Template CRUD | LetterTemplate | decorator | Y | LetterTemplateEditorPage |
| GET/PUT/POST /api/ai-assistant/* (generate-draft, prompts, langgraph config/draft/background/strategy-plan/runs, search-letters, stats, cache, health) | ai_assistant.py→ai_service | AI drafting helpers (14 ops) | AiModels | policy (5)/auth | partial | langgraph components — **prompts admin, stats, cache not wired** · Medium |
| POST /api/deep-planning/analyze-document, generate-draft, GET history | deep_planning.py | Deep planning (rate-limited) | — | policy | N | **DeepPlanning page/panel missing** · Medium |

### 2.8 Retrieval / RAG / Search
| Method · Route | Backend | Purpose | Model | Perm | FE | FE / Suggested · Priority |
|---|---|---|---|---|---|---|
| POST /api/v1/retrieval/search, rag, agent, contract-qa | retrieval_engine.py→RetrievalService | Vector search / RAG / agent / QA | DocumentVector | policy (scope) | partial | ContractQAPage — **agent & generic search unwired** · High |
| POST /api/v1/ingestion/jobs, GET {job} | retrieval_engine.py→contracts_ingest | Ingestion jobs | — | policy | N | **Admin ingestion monitor** · Medium |
| POST /api/v1/admin/vector/reconcile | retrieval_engine.py | Reconcile vectors | DocumentVector | policy | N | **Admin maintenance panel** · Low |
| POST /api/v1/observability/analytics, GET logs | retrieval_engine.py→observability | RAG analytics/logs | — | policy | N | **Admin observability view** · Low |
| GET /api/search/documents, suggestions, popular, analytics; POST semantic, track | search.py→search_service | Doc search + telemetry | Document | **thin authz (auth)** | partial | search components — **semantic/analytics unwired; §4.5** · Medium |

### 2.9 Storage / Folders / Tags / Email / Notifications
| Method · Route | Backend | Purpose | Model | Perm | FE | FE / Suggested · Priority |
|---|---|---|---|---|---|---|
| GET/POST/DELETE /api/folder-structure (+open-file), POST /api/upload-file | folder_structure.py→folder_service | Folder tree + upload | Folder | auth | partial | FolderStructurePage — **open-file/upload-file unwired** · Low |
| GET/POST /api/tags, /{id}, subtags CRUD | tags.py→tag_service | Tag/subtag CRUD | Tag | decorator `tags:*` | Y | TagsPage |
| GET/POST/PUT/DELETE /api/email/groups | email_groups.py→email_group_service | Email group CRUD + resolve | EmailGroup | decorator | N | **EmailGroupsPage exists — verify wiring** · Medium |
| POST /api/email/share-document, resolve-recipients, GET suggestions, public-share/{token}/download | email.py→email_service | Share docs by email | — | decorator/public | partial | ShareDocumentPage — **suggestions/resolve unwired** · Low |
| POST /api/email/legacy/* (4) | email_share.py | **Legacy email send/history/templates** | — | auth | N | **Dead/legacy — remove** · Low |
| GET/PATCH /api/notifications (+unread-count, read-all, mark-all-read, preferences, {id}/read, {id}/action, delivery-logs) | notifications.py→notifications service | Notification center | Notification | **auth only (self-scoped)** | partial | NotificationCenterPage — **delivery-logs/action unwired** · Medium |
| POST /api/notification-test/email | notifications.py | Test email send | — | auth/admin | N | **Admin settings only** · Low |
| GET/POST/PUT/test /api/smtp-settings/org|project | smtp_settings.py→smtp_settings_service | SMTP config + test | SmtpSettings | step-up | Y | smtp-settings-api.ts / SettingsPage |
| GET/PUT /api/settings/storage/* and /api/storage-settings/* (alias) | storage_settings.py→storage_settings_service | Storage config (+ alias set) | StorageSettings | step-up | partial | storage-settings-api.ts — **alias routes dup, §4.4** · Low |
| POST /api/storage-sync/* (reconcile, reconcile-files, resync-bulk, resync-doc), GET status | storage_sync.py | Storage/vector reconcile | DocumentVector | step-up | N | **Admin maintenance panel** · Low |

### 2.10 Platform / Health / Performance
| Method · Route | Backend | Purpose | Perm | FE | Note |
|---|---|---|---|---|---|
| GET /health, /health/live, /ready, /observability, /metrics | health.py | Liveness/readiness/metrics | public | N\* | infra probes (gateway/monitoring) |
| GET/POST /api/performance/* (8: cache, endpoints, health, jobs, metrics, slow-queries, cache/clear, job/{id}/cancel) | performance.py→performance_monitor | Perf telemetry & job control | decorator (7) | N | **Admin "System Health" page missing** · Medium |
| POST /api/contact | contact.py | Public contact form | public | Y | LandingPage |

### 2.11 Billing / Monetization — see dedicated deep-dive §3

---

## Part 3 — Razorpay / Payment Deep-Dive

**Architecture:** pluggable gateway (`PaymentGatewayInterface`) with `NoOpPaymentGateway` (default, `PAYMENT_PROVIDER=noop`), **`RazorpayGateway`** (primary), `StripeGateway` (stub). Hosted-checkout model: backend provisions a pending subscription and returns `checkout_url`; the browser is redirected; Razorpay auto-charges on schedule; **webhooks reconcile state**. `RAZORPAY_KEY_ID/KEY_SECRET/WEBHOOK_SECRET` in `core/config.py`.

| # | Flow stage | Backend | Frontend | Status |
|---|---|---|---|---|
| 1 | **Plan selection** | `GET /rbac-monetization/plan-catalog`, `/plans`, `/plans/{code}` | `plan-settings-api.ts` → PlanSettingsPage, SubscriptionManagementPage, RegisterPage | ✅ Wired |
| 2 | **Subscription creation** | `POST /subscriptions/checkout` (hosted) + `POST /subscriptions/start-trial`; raw `POST /subscriptions` (admin) | `billing-api.ts startSubscriptionCheckout`→redirect; `startTrial` | ✅ Wired (raw create unused) |
| 3 | **Order creation** | N/A — Razorpay **subscription** model (no separate order); auto-charge | — | ⚠️ By design; no order endpoint/UI. If one-time payments needed later, no path exists. |
| 4 | **Payment verification** | Webhook HMAC `verify_webhook_signature` (server-side, deny-by-default) | none (no client-side `order_id+signature` handshake) | ⚠️ Server-verified only. **No post-redirect "payment success/return" page** to confirm/await activation. |
| 5 | **Webhook handling** | `POST /api/billing/webhooks/{provider}` → `billing_webhook_service` (signature verify, idempotent by event id, reconcile subscription) | none (correct) | ✅ Backend complete; tested (`test_billing_webhooks.py`) |
| 6 | **Invoice / receipt** | `GET /subscriptions/{id}/invoice-preview`, `/billing/history/{org}`, `/billing/summary/{org}`, `POST /billing-records` | `getInvoicePreview`, billing history/summary | ⚠️ Preview wired; **no downloadable invoice/receipt (PDF) endpoint or UI**; `POST /billing-records` (manual) unwired |
| 7 | **Payment failure handling** | webhook reconciles to failed/past-due status | none | ❌ **No frontend failure UX** (declined card, redirect-cancel, past-due banner/retry) |
| 8 | **Admin billing/subscription view** | `GET /subscriptions` (list), `/subscriptions/{id}`, `/{id}/history`, `/billing/summary`, plan/add-on CRUD | SubscriptionManagementPage (self/org); **plan CRUD (`POST/PUT /plans`), add-on CRUD (`POST /add-ons`,`PUT /add-ons/{id}`), expert-allocations, billing-records, usage-events not wired** | ⚠️ Customer view ✅; **admin plan/add-on/allocation management UI missing** |

**Monetization endpoints wired (frontend):** plan-catalog, plans/{code}, plan-settings(+scope, effective-services), add-ons (list), subscriptions (list/get/checkout/start-trial/convert-trial/upgrade/downgrade/cancel/reactivate/change-period/add-ons add+remove/history/invoice-preview), billing/history, billing/summary.
**Monetization endpoints NOT wired:** `POST /subscriptions` (raw), `POST/PUT /plans`, `POST /add-ons` + `PUT /add-ons/{id}`, `GET/POST/PUT /expert-allocations`, `POST /billing-records`, `POST /usage-events`, `POST /billing/webhooks/{provider}` (N\*).

---

## Part 4 — Findings

### 4.1 Unlinked backend APIs (high-value)
- **Contract registers:** `clauses`, `key-dates`, `obligations`, `risks` (GET+PUT) — no UI. *High.*
- **Vector/semantic search:** `GET /documents/vector-search`, `POST /search/semantic`, `POST /v1/retrieval/search|agent` — partial/none. *High.*
- **Dashboard:** `GET /dashboard/stats` not wired (Dashboard likely on placeholder data). *High.*
- **Concerns:** full CRUD unlinked (no `ConcernsPage`). *High.*
- **User lock/unlock:** admin action unwired. *High.*
- **Admin plan/add-on/allocation CRUD** (monetization). *High.*
- Medium: org stats, document export, audit-events tab, bulk-upload template/status, appraisal citations/create-registers, letter chain/tree, deep-planning, ingestion monitor, performance/system-health page, email groups verify.
- Low/admin: storage-sync maintenance, vector reconcile, observability logs, parties/external, folder open-file.

### 4.2 Frontend screens missing for existing backend features
| Missing screen/component | Backing APIs | Priority |
|---|---|---|
| **Contract Registers tab** (clauses/dates/obligations/risks) | `/contracts/{clauses,key-dates,obligations,risks}` | High |
| **Concerns management page** | `/api/concerns/*` | High |
| **Admin: Plans & Add-ons management** | `/rbac-monetization/{plans,add-ons,expert-allocations}` | High |
| **Payment failure / return page** | post-checkout reconcile, webhook status | High |
| **System Health / Performance admin** | `/api/performance/*` | Medium |
| **RAG/Agent console** | `/v1/retrieval/agent`, `/observability/*` | Medium |
| **Deep Planning panel** | `/api/deep-planning/*` | Medium |
| **Invoice/Receipt download** | (no backend endpoint yet) | Medium |

### 4.3 Backend/frontend route or payload mismatches
- **Checkout return contract:** `CheckoutResponse.checkout_url` is `string | null`; frontend `redirectToCheckout` only acts when non-null but has **no else/error branch** → silent no-op if backend returns null (e.g. NoOp provider). *Mismatch/UX gap.*
- **Mark-all-read duplication:** frontend may call `PATCH /notifications/mark-all-read` while backend also exposes `POST /notifications/read-all` (two endpoints, same intent) — verify the client uses one consistently.
- **Storage settings aliases:** `/api/settings/storage/*` vs `/api/storage-settings/*` are functional duplicates; frontend mixes — risk of divergent behavior.
- No hard request-schema mismatches detected via OpenAPI, but **payload typing is not shared** (frontend re-declares interfaces) → drift risk.

### 4.4 Duplicate / dead APIs
- `POST /api/token` (legacy OAuth2) ↔ `POST /api/login` — **dead duplicate**.
- `GET /api/projects1` ↔ `GET /api/projects` — **dead duplicate**.
- `GET /api/input_requests` (legacy) ↔ `/api/input-requests/*` — **legacy**.
- `POST /api/email/legacy/*` (4) ↔ `/api/email/*` — **legacy**.
- `/api/settings/storage/*` ↔ `/api/storage-settings/*` (alias set, 5+5) — **intentional dup**, consolidate.
- `POST /storage-sync/reconcile` ↔ `POST /v1/admin/vector/reconcile` — overlapping reconcile.
- `POST /notifications/read-all` ↔ `PATCH /notifications/mark-all-read` — overlapping.
- `/me` vs `/users/me` vs `/profiles/me` — three "current user" endpoints.

### 4.5 Missing / weak permissions
- **`representatives.py`** — 9 routes, only **1** `require_permission`; mostly `auth`-only. Representatives are PII → **needs `representatives:*` (or PolicyService) on create/update/delete**. *High.*
- **`search.py`** — 6 routes, **thin authz** (2 policy refs); semantic/document search may cross org/project scope. **Add scope enforcement.** *High.*
- **`notifications.py`** — `auth`-only (self-scoped, acceptable) but `notification-test/email` and `{id}/action` should be admin/owner-checked. *Medium.*
- **`folder_structure.py`, `sla.py`, `tasks.py` (decorator=0)** — `tasks` uses PolicyService (ok); confirm `folder_structure`/`sla` enforce project scope. *Medium.*
- **`ai_assistant.py`** — 14 routes, 5 policy refs; prompts-admin/cache/stats should be admin-gated. *Medium.*

### 4.6 Missing tests
- 57 test files; **good**: `test_rbac_monetization.py`, `test_billing_webhooks.py`, `test_rbac_policy_hardening.py`, `test_route_inventory.py`.
- **Thin/absent:** `letter_drafting` (23 ops) governance flow, `contract_appraisal` (22 ops) lifecycle, `retrieval_engine`/RAG, `ai_assistant`, `representatives` authz, `search` scope. **No end-to-end Razorpay (real-provider) integration test** (only NoOp/unit). *Add contract tests + authz negative tests.*

### 4.7 Production-readiness gaps
- Razorpay **receipt/invoice PDF** generation absent; **payment-failure UX** absent.
- **Representatives & search authorization** under-enforced (PII/scope leakage risk).
- **Dead/legacy endpoints** increase attack surface — remove.
- **Shared client/server types** missing → payload drift.
- **Observability:** `/metrics`, `/health/observability`, RAG logs exist but **no dashboards wired** (per `docs/history/Phase6_*`).
- **Frontend env baking:** `VITE_API_BASE_URL` correct, but **no documented prod build/runtime override test**.
- Earlier build audit: `requirements.txt` had unresolved deps (pydantic-ai extras, missing `ratelimit/pandas/requests/python-magic`) and `Dockerfile` lacked `libmagic1` — **fix before prod image build** (already validated in sandbox).

---

## Part 5 — Phased Remediation Plan (with production-readiness rating)

> Baseline today: **6.5 / 10**.

### Phase 1 — Security & correctness hardening *(1–2 wks)* → target **7.5 / 10**
1. Enforce authz on `representatives.py` (PII) and scope on `search.py`/semantic search.
2. Remove dead/legacy endpoints (`/token`, `/projects1`, `/input_requests`, `/email/legacy/*`); consolidate storage-settings aliases & notification mark-all-read.
3. Fix `redirectToCheckout` null/error branch; add backend regression test for null `checkout_url`.
4. Land build fixes (requirements + `libmagic1`) into the repo Dockerfiles.
5. Add negative authz tests (each role × protected route) leveraging `test_route_inventory.py`.
**Gate:** no `auth`-only mutations on PII; dead routes gone; CI authz matrix green.

### Phase 2 — Payment completeness *(1–2 wks)* → target **8.0 / 10**
1. **Payment failure/return page**: post-checkout landing that polls subscription status; past-due banner + retry.
2. **Invoice/receipt**: backend PDF endpoint (`/subscriptions/{id}/invoice` → `report_service`) + download UI; wire `POST /billing-records`.
3. Real-provider **Razorpay sandbox integration test** (order/subscription + webhook signature).
4. Verify webhook idempotency + reconciliation under replay/late events.
**Gate:** end-to-end trial→checkout→webhook→active→invoice on Razorpay test keys; failure path visible in UI.

### Phase 3 — Admin & unlinked high-value surfaces *(2–3 wks)* → target **8.5 / 10**
1. Admin **Plans & Add-ons & Expert-allocations** management UI.
2. **Contract Registers tab** (clauses/key-dates/obligations/risks) + inline edit.
3. **Concerns page**, **Dashboard stats** wiring, **user lock/unlock** action.
4. **System Health/Performance** admin page; ingestion/observability monitors.
**Gate:** ≥90% of non-internal backend routes reachable from UI; admin can manage catalog without DB access.

### Phase 4 — AI/RAG depth & UX *(2–3 wks)* → target **9.0 / 10**
1. RAG/Agent console (`/v1/retrieval/*`), deep-planning panel, vector/semantic search UI.
2. Letter-drafting run sub-views (governance, source-ledger, critique) fully wired.
3. Contract-appraisal citations/create-registers/job-cancel.
4. Contract + e2e tests for drafting and appraisal lifecycles.
**Gate:** AI flows exercised by tests; no unguarded AI admin ops.

### Phase 5 — Production operations *(1–2 wks)* → target **9.5 / 10**
1. Shared TypeScript types generated from OpenAPI (kill payload drift).
2. Observability dashboards + alerting wired (`/metrics`, RAG logs).
3. Prod deploy rehearsal: secrets, TLS (`httpd-tls.conf`), replica-set Mongo, Qdrant/Falkor backups, zero-downtime deploy.
4. Load/perf test upload & RAG paths.
**Gate:** runbook + dashboards live; prod config validation passes with real secrets; restore-from-backup tested.

| Phase | Focus | Readiness after |
|---|---|---|
| — | Baseline | 6.5 |
| 1 | Security/correctness | 7.5 |
| 2 | Payment completeness | 8.0 |
| 3 | Admin/unlinked surfaces | 8.5 |
| 4 | AI/RAG depth | 9.0 |
| 5 | Prod operations | 9.5 |

---

## Appendix — Method & caveats
- Endpoint inventory from live `GET /openapi.json` (353 ops / 277 routes). Frontend linkage by static path-literal matching across `client/src` (template/param-normalized) — **heuristic**: a handful of dynamically-built URLs may be mis-flagged as unlinked; verify before deletion. Permission column reflects the enforcement *mechanism* + known permission families (`PolicyService.authorize` permission strings are often passed via constants, so some are characterized at router level, not per-row). Graph relationships tagged `INFERRED` are leads, not facts. No code changed.
