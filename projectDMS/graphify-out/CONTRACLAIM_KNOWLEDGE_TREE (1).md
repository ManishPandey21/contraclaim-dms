# ContraClaim DMS — Knowledge Tree

> Generated via Graphify AST extraction (683 code files parsed) + deep manual analysis.  
> Repository: `ManishPandey21/contraclaim-dms`  
> Stack: **FastAPI (Python 3.10+) + React/Vite (TypeScript) + MongoDB + Qdrant + FalkorDB + Redis**

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Backend — Core Infrastructure](#2-backend--core-infrastructure)
3. [Backend — API Routers](#3-backend--api-routers)
4. [Backend — Services Layer](#4-backend--services-layer)
5. [Database Models (MongoDB)](#5-database-models-mongodb)
6. [RBAC — Roles, Permissions & Authorization](#6-rbac--roles-permissions--authorization)
7. [Document Flow](#7-document-flow)
8. [AI / RAG Flow](#8-ai--rag-flow)
9. [Letter Drafting AI Flow (LangGraph)](#9-letter-drafting-ai-flow-langgraph)
10. [Razorpay / Payment & Subscription Flow](#10-razorpay--payment--subscription-flow)
11. [Frontend](#11-frontend)
12. [Frontend — API Integration Layer](#12-frontend--api-integration-layer)
13. [Deployment & Infrastructure](#13-deployment--infrastructure)
14. [Observability & Monitoring](#14-observability--monitoring)
15. [Ancillary Services](#15-ancillary-services)

---

## 1. System Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        Edge Layer                            │
│  Apache HTTPD (gateway:80) ←→ Client (React/Vite :3000)     │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP Reverse Proxy
┌───────────────────────────▼──────────────────────────────────┐
│               Backend (FastAPI :8000)                        │
│  rbac_backend — Python 3.10+, Uvicorn, 47 Routers           │
└──┬──────────┬──────────┬────────────────────┬───────────────┘
   │          │          │                    │
   ▼          ▼          ▼                    ▼
MongoDB    Qdrant     FalkorDB           Redis
(data)   (vectors)  (knowledge         (queues +
                      graph)           runtime state)
```

**Key design decisions:**
- RBAC-first: every route guarded by `authorization_service.py`
- Dual-write vector storage: MongoDB chunks + Qdrant vectors
- Graph knowledge layer: FalkorDB (production) / Graphiti (experimental)
- Asynchronous contract ingestion via Redis-backed queue
- Payment gateway is plug-in: NoOp → Razorpay → Stripe stub

---

## 2. Backend — Core Infrastructure

### Entry Points
| File | Purpose |
|------|---------|
| `backend/rbac_backend/main.py` | FastAPI app factory; registers all routers, CORS, CSRF, request-context middleware, startup/shutdown lifecycle |
| `backend/rbac_backend/worker.py` | Standalone contract queue worker (separate Docker container) |

### `core/` — Application Foundation
| File | Purpose |
|------|---------|
| `core/config.py` | `Settings` (pydantic-settings); 80+ env vars; hard production-safety validation on startup |
| `core/database.py` | Motor (AsyncIO MongoDB) client factory; connection pooling; replica-set enforcement |
| `core/security.py` | JWT token issue/verify, cookie auth, `CurrentUser` dependency injection, step-up auth |
| `core/permissions.py` | Permission-check FastAPI dependencies, decorator helpers |
| `core/csrf.py` | Double-submit CSRF cookie validation for unsafe HTTP methods |
| `core/errors.py` | Standardized HTTP error shapes; maps service exceptions → HTTP codes |
| `core/templates.py` | Jinja2-based email template rendering (HTML + plain-text) |

### Startup Sequence
```
startup_event()
  ├─ settings.validate_runtime_configuration()   # refuses placeholder secrets
  ├─ start_background_services()                 # Redis pub/sub, notification fanout
  ├─ start_contract_ingest_queue()               # Redis list-based durable queue
  └─ APScheduler CronJobs
       ├─ send_daily_digests  (09:00 daily)
       ├─ send_weekly_digests (Sunday 09:00)
       └─ run_sla_scan        (08:00 daily)
```

---

## 3. Backend — API Routers

All routers mount under `/api`.

| Router | Tag | Key Endpoints |
|--------|-----|---------------|
| `auth.py` | auth | POST /login, POST /logout, POST /refresh, GET /me |
| `sso.py` | sso | OIDC authorize, callback, user provisioning |
| `users.py` | users | CRUD, lock/unlock, invite, bulk actions |
| `profiles.py` | profiles | GET/PUT /me/profile, avatar upload |
| `organizations.py` | organizations | CRUD, stats, validation |
| `projects.py` | projects | CRUD, assign users |
| `roles.py` | roles | CRUD, assign permissions, system roles |
| `permissions.py` | permissions | CRUD, permission matrix |
| `documents.py` | documents | Single/bulk upload, list, update, delete, share, approve, comment, download, OCR trigger |
| `contracts.py` | contracts | Chunked upload, categorization, ingest queue status |
| `search.py` | search | Full-text + semantic search |
| `ai_assistant.py` | ai-assistant | Contract QA, iterative RAG |
| `retrieval_engine.py` | retrieval-engine | Low-level retrieval, ingestion job status |
| `deep_planning.py` | deep-planning | Strategy context, evidence bundle |
| `letters.py` | letters | Letter CRUD, approve, send, reference sync |
| `letter_drafting.py` | letter-drafting | Start/resume draft session, stream output |
| `letter_templates.py` | letter-templates | Template CRUD |
| `input_requests.py` | input-requests | Create/respond to evidence input requests |
| `claims.py` | claims | Claims register CRUD |
| `concerns.py` | concerns | Concerns management |
| `contract_appraisal.py` | contract-appraisal | Appraisal workflows |
| `parties.py` | parties | Contract party CRUD |
| `representatives.py` | representatives | Party representative management |
| `tasks.py` | tasks | Task management with assignment |
| `tags.py` | tags | Tag taxonomy (hierarchical, color-coded) |
| `folder_structure.py` | folder-structure | Virtual folder tree |
| `email.py` | email-legacy | Legacy email send |
| `email_share.py` | email | Document share via email |
| `email_groups.py` | email-groups | Distribution group management |
| `notifications.py` | notifications | In-app notifications, mark read, digest |
| `reports.py` | reports | Analytics report generation |
| `dashboard.py` | dashboard | Aggregated stats |
| `rbac_monetization.py` | rbac-monetization | Plan CRUD, subscription lifecycle, expert allocation |
| `billing_webhooks.py` | billing | Razorpay/Stripe webhook receiver |
| `sla.py` | sla | SLA deadline tracking |
| `performance.py` | performance | Performance monitoring |
| `health.py` | — | /health, /health/ready (liveness + readiness) |
| `storage_sync.py` | storage | S3/local sync, presigned URLs |
| `storage_settings.py` | storage-settings | Per-org storage config |
| `smtp_settings.py` | smtp-settings | Per-org SMTP config |
| `ws.py` | — | /ws WebSocket for real-time notifications |
| `contact.py` | contact | Public contact form |

---

## 4. Backend — Services Layer

### Identity & Auth
| Service | File | Responsibility |
|---------|------|---------------|
| `AuthenticationService` | `authentication_service.py` | Login, JWT issue, cookie management, step-up |
| `AuthService` | `auth_service.py` | Thin wrapper for session validation |
| `OIDCService` | `oidc_service.py` | OpenID Connect SSO flow |
| `StepUpService` | `step_up_service.py` | Re-authentication for high-privilege actions |

### RBAC & Authorization
| Service | File | Responsibility |
|---------|------|---------------|
| `AuthorizationService` | `authorization_service.py` | Central permission enforcement (57k bytes — RBAC engine) |
| `RBACService` | `rbac_service.py` | Role-to-user assignment helpers |
| `RoleService` | `role_service.py` | Role CRUD, system role seed, hierarchy |
| `PermissionService` | `permission_service.py` | Permission CRUD, matrix computation |
| `PolicyService` | `policy_service.py` | Attribute-based policy evaluation |
| `ScopeService` | `scope_service.py` | Org/project scope resolution |
| `EntitlementService` | `entitlement_service.py` | Plan-feature gate checks |

### Document & File Management
| Service | File | Responsibility |
|---------|------|---------------|
| `DocumentService` | `document_service.py` | Full document lifecycle — 76k bytes |
| `FileObjectService` | `file_object_service.py` | File validation, MIME check, antivirus dispatch |
| `FileService` | `file_service.py` | Local/S3 file read/write abstraction |
| `S3Service` | `s3_service.py` | AWS S3 presigned URLs, upload/download |
| `BulkUploadService` | `bulk_upload_service.py` | CSV-driven bulk upload with per-file job tracking |
| `OCRService` | `ocr_service.py` | Marker CLI integration: PDF → text |
| `AntivirusService` | `antivirus_service.py` | ClamAV scan via TCP socket |
| `DocumentLinkingService` | `document_linking_service.py` | Cross-document reference graph management |
| `DocumentBulkDownloadService` | `document_bulk_download_service.py` | ZIP generation for bulk downloads |
| `FolderService` | `folder_service.py` | Virtual folder CRUD |
| `DocumentAuditService` | `document_audit_service.py` | Document-level audit events |

### Contract Ingestion Pipeline
| Service | File | Responsibility |
|---------|------|---------------|
| `ContractService` | `contract_service.py` | Chunked upload sessions, contract CRUD — 51k bytes |
| `ContractsIngest` | `contracts_ingest.py` | Full ingestion: OCR → chunk → embed → graph — 69k bytes |
| `ContractIngestQueue` | `contract_ingest_queue.py` | Redis-list durable queue with retry/dead-letter |
| `ContractCategorizerService` | `contract_categorizer.py` | LLM-based contract type classification |
| `ContractGraphService` | `contract_graph_service.py` | Graph node creation for contracts |
| `MetadataProcessorService` | `metadata_processor_service.py` | AI-driven metadata extraction |
| `TextProcessingService` | `text_processing_service.py` | Text normalization, keyword extraction |

### AI / Retrieval
| Service | File | Responsibility |
|---------|------|---------------|
| `RetrievalService` | `retrieval/service.py` | Core RAG: vector search, HYDE, RAG-Fusion, iterative QA — 42k bytes |
| `IngestionPipeline` | `ingestion/pipeline.py` | Idempotent 5-stage pipeline: extract → chunk → embed → enrich → index |
| `AIService` | `ai_service.py` | OpenAI Assistants API wrapper |
| `OpenAIService` | `openai_service.py` | Direct Chat Completions calls |
| `LLMConfigService` | `llm_config_service.py` | Per-org LLM model/prompt config |
| `LangchainVectorService` | `langchain_vector_service.py` | LangChain Qdrant integration |
| `LlamaIndexService` | `llamaindex_service.py` | LlamaIndex document index |
| `PydanticAIService` | `pydantic_ai_service.py` | Structured output extraction |
| `ConversationService` | `conversation_service.py` | Multi-turn conversation state management |
| `StrategyContextService` | `strategy_context_service.py` | Evidence aggregation for letter strategy |

### Letter Drafting (AI Workflow)
| Service | File | Responsibility |
|---------|------|---------------|
| `LetterDraftingService` | `letter_drafting/service.py` | LangGraph-backed drafting orchestrator — 102k bytes |
| `DraftingGenerator` | `letter_drafting/generator.py` | LLM draft generation with streaming |
| `DraftingContext` | `letter_drafting/context.py` | Context assembly from RAG + graph |
| `IncomingAnalyzer` | `letter_drafting/incoming_analyzer.py` | Analyze incoming letters for response strategy |
| `DraftingPlanning` | `letter_drafting/planning.py` | Strategic response plan generation |
| `LetterService` | `letter_service.py` | Letter CRUD, approval workflow, send — 48k bytes |
| `TemplateService` | `template_service.py` | Letter template rendering with variable substitution |

### Knowledge Graph
| Service | File | Responsibility |
|---------|------|---------------|
| `FalkorGraphService` | `falkor_graph_service.py` | Cypher queries on FalkorDB |
| `GraphIngestionService` | `graph/graph_ingestion_service.py` | Extract entities/relations → graph |
| `GraphAdapter` | `graph/graph_adapter.py` | Pluggable adapter: direct_falkor or graphiti |

### Monetization & Billing
| Service | File | Responsibility |
|---------|------|---------------|
| `MonetizationService` | `monetization_service.py` | Plan/subscription business logic — 56k bytes |
| `SubscriptionLifecycleService` | `subscription_lifecycle_service.py` | Trial/upgrade/downgrade/cancel |
| `UsageMeteringService` | `usage_metering_service.py` | Per-event usage tracking |
| `BillingWebhookService` | `billing_webhook_service.py` | Webhook events → subscription state |
| `PaymentGateway` | `payment_gateway.py` | Abstract gateway + Razorpay + Stripe + NoOp |
| `AllocationService` | `allocation_service.py` | Expert drafter/reviewer allocation |

### Communications & Notifications
| Service | File | Responsibility |
|---------|------|---------------|
| `EmailService` | `email_service.py` | SMTP delivery, daily/weekly digest, share emails — 28k bytes |
| `SMTPSettingsService` | `smtp_settings_service.py` | Per-org SMTP config with encrypted credentials |
| `NotificationsService` | `notifications.py` | In-app notification fanout (WebSocket + Redis pub/sub) |
| `EmailGroupService` | `email_group_service.py` | Distribution group management |

### Infrastructure Services
| Service | File | Responsibility |
|---------|------|---------------|
| `ReportService` | `report_service.py` | Analytics reports — 44k bytes |
| `CacheService` | `cache_service.py` | Redis-backed response caching |
| `BackgroundJobs` | `background_jobs.py` | Redis pub/sub subscriber, notification dispatcher |
| `SLAService` | `sla_service.py` | SLA deadline scanning and breach detection |
| `AuditEventService` | `audit_event_service.py` | Structured audit log emission |
| `PerformanceMonitorService` | `performance_monitor.py` | p50/p95/p99 latency tracking |

---

## 5. Database Models (MongoDB)

### Collections Map

| Collection | Model File | Key Fields |
|-----------|-----------|------------|
| `users` | `models/user.py` | email, password_hash, organization_id, role_ids, is_active, is_locked |
| `organizations` | `models/organization.py` | name, panNumber, gstNumber, billingEnabled, adminEmail |
| `projects` | `models/project.py` | name, organization_id, members[], status |
| `documents` | `models/document.py` | filename, uploadType (incoming/outgoing/contract), letterNo, status, ocrText, full_text, references[], storage_locations[] |
| `document_vectors` | `models/document_vector.py` | chunk_id, clause_number, clause_title, embedding, page_numbers[] |
| `chunks` | `ingestion/models.py` | chunk_id, document_id, text_original, text_enriched, content_hash, embedding_model |
| `ingestion_jobs` | `ingestion/models.py` | job_id, stage (extracting/chunking/embedding/enriching/indexing/done/failed), progress |
| `vector_sync_status` | (ingestion pipeline) | document_id, sync_status (synced/mismatch), mongo_chunks, qdrant_chunks |
| `letters` | `models/letter.py` | letter_no, subject, to, from, status, drafting_request_id, approved_by |
| `letter_templates` | `models/letter_template.py` | name, body_template, variables[], category |
| `letter_drafting_requests` | `models/letter_drafting.py` | status (input/planning/drafting/review/approval/completed), assigned_drafter, assigned_reviewer |
| `roles` | `models/role.py` | name, permissions[], is_system, organization_id |
| `permissions` | `models/permission.py` | name (resource:action), category, is_system |
| `plans` | `models/rbac_monetization.py` | code, family (dms_saas/drafting_bundle), pricing_tiers, default_limits |
| `subscriptions` | `models/rbac_monetization.py` | plan_code, status, billing_period, payment_gateway_subscription_id, trial_ends_at |
| `subscription_history` | `models/rbac_monetization.py` | change_type, from_plan_code, to_plan_code, changed_by |
| `expert_allocations` | `models/rbac_monetization.py` | expert_user_id, assignment_role (drafter/reviewer/senior_reviewer/drafting_manager) |
| `tags` | `models/tag.py` | name, color, parent_id, aliases[], organization_id |
| `parties` | `models/party.py` | name, type, contact_info, project_id |
| `claims` | `models/claim.py` | claim_no, description, amount, status |
| `concerns` | `models/concern.py` | concern_no, type, linked_documents[], status |
| `notifications` | `models/notification.py` | user_id, type, read, channel (in_app/email/ws) |
| `approval_records` | `models/approval.py` | document_id, approver_id, status, comments |
| `smtp_settings` | `models/smtp_settings.py` | host, port, encrypted_password, from_email |

### FalkorDB Knowledge Graph Schema
```
Nodes:   Document | Contract | Party | Clause | Letter | Entity
Edges:   REFERENCES | RESPONDS_TO | CITES | INVOLVES | CONTAINS_CLAUSE
```

---

## 6. RBAC — Roles, Permissions & Authorization

### Permission Categories (10)
```
user_management       document_management   project_management
role_management       system_administration  email_management
audit_management      drafting_management   billing_management
subscription_management
```

### Permission Name Formats
- **Legacy**: `resource:action` — e.g., `documents:read`, `users:delete`
- **Canonical dotted**: `dms.document.view`, `draft.request.create`

### Permission Levels
```
read → create → update → delete → admin
```

### System Default Permissions (70+)
| Group | Permissions |
|-------|------------|
| `users:*` | read, create, update, delete, lock, unlock |
| `documents:*` | read, create, update, delete, approve, share, upload, comment |
| `roles:*` + `permissions:*` | full RBAC administration + `roles:superuser` |
| `organizations:*`, `projects:*` | tenant management |
| `parties:*`, `representatives:*`, `concerns:*` | project entity management |
| `tags:*`, `letter_templates:*` | content management |
| `emails:*`, `email_groups:*` | communication management |
| `input_requests:*` | evidence gathering |
| `dms.document.*`, `draft.request.*` | DMS canonical permissions |
| `system:admin` | full superadmin |
| `performance:admin/superadmin` | monitoring access |

### Authorization Flow
```
HTTP Request
  → JWT cookie auth (core/security.py)
  → CurrentUser dependency injection
  → Router permission decorator / dependency
  → AuthorizationService.check_permission()
       ├─ Resolve user's roles (org-scoped + project-scoped)
       ├─ Expand roles → permissions set
       ├─ EntitlementService: plan feature gate
       └─ PolicyService: attribute-based checks
  → 403 Forbidden | proceed
```

### RBAC Role Hierarchy
```
System Admin (roles:superuser)
  └─ OrgAdmin
       └─ ProjectAdmin
            ├─ Drafter (drafting_management)
            ├─ Reviewer (drafting_management)
            ├─ Senior Reviewer
            ├─ Drafting Manager
            └─ OrgUser (read-only)
```

### Plan-Based Entitlement Gate
- `RBAC_ENTITLEMENT_FAIL_OPEN=true` → bypass on entitlement error (dev)
- `RBAC_ENTITLEMENT_FAIL_OPEN=false` → deny on entitlement error (prod)

---

## 7. Document Flow

### Single Document Upload
```
Client: POST /api/documents/upload (multipart)
  → FileObjectService.validate()          # MIME check, antivirus (ClamAV)
  → UploadStreamingService.stream()       # chunked write, size limits
  → S3Service.upload_to_s3()             # primary storage
  → DocumentService.create_record()      # MongoDB insert
  → BackgroundJob (async):
       ├─ OCRService.run_marker()         # Marker CLI: PDF → full_text
       ├─ MetadataProcessorService()      # AI: dates, parties, letter_no
       └─ IngestionPipeline.process()    # chunk → embed → index (Qdrant)
```

### Bulk Upload (CSV + Files)
```
Client: POST /api/documents/bulk-upload
  → BulkUploadService.initiate()
       ├─ Validate CSV → CSVValidationResult
       ├─ Match CSV rows → file list
       ├─ Create BulkUploadStatus record
       └─ Per-file async loop:
            ├─ FileObjectService.validate()
            ├─ DocumentService.create_record()
            └─ Queue OCR + ingestion job
GET /api/documents/bulk-status/{job_id} → BulkUploadStatus (progress %)
```

### Contract Upload (Chunked)
```
Client: POST /api/contracts/upload/session          → session_id
        POST /api/contracts/upload/chunk/{session}  (5MB chunks)
        POST /api/contracts/upload/complete/{session}
          → ContractIngestQueue.enqueue(contract_id)
               → Redis LPUSH contract_ingest_queue

contract-worker (separate Docker container):
  → ContractIngestQueue.dequeue()  # BRPOP
  → ContractsIngest.process()
       ├─ OCRService: PDF → text
       ├─ ContractCategorizerService: classify contract type
       ├─ MetadataProcessorService: extract clauses, parties, dates
       ├─ IngestionPipeline:
       │    ├─ chunk_text() → Chunk[]
       │    ├─ EmbeddingClient.embed() → vectors[]
       │    ├─ ChunkEnricher.enrich() (neighborhood strategy)
       │    ├─ MongoDB: chunks collection (bulk_write upsert)
       │    └─ VectorClient.upsert() → Qdrant
       └─ FalkorGraphService.create_contract_nodes()
```

### Document Storage Architecture
```
StorageLocation (per document):
  ├─ provider: "local"  → UPLOADS_DIR/org_id/project_id/filename
  └─ provider: "s3"     → s3://BUCKET_NAME/org_id/project_id/filename

StorageSettings (per org): primary_provider = local | s3
```

### Ingestion Pipeline Stages (idempotent)
```
EXTRACTING (5%)
  → CHUNKING (15%)     [sentence-level, configurable overlap]
  → EMBEDDING (40%)    [skip if content_hash unchanged]
  → ENRICHING (55%)    [neighborhood context strategy, optional]
  → INDEXING (75%)     [MongoDB chunks + Qdrant upsert + prune stale]
  → DONE (100%)
```

---

## 8. AI / RAG Flow

### Vector Store Architecture
```
Document Text
  → chunk_text() [sentence-level, configurable chunk_size + overlap]
  → EmbeddingClient.embed()
       [sentence-transformers/all-MiniLM-L6-v2 OR OpenAI text-embedding-ada-002]
  → VectorClient.upsert()
       ├─ Qdrant "contracts" collection (1536-dim Cosine — primary)
       └─ MongoDB chunks (dual-write fallback)
```

### Search Strategies
| Strategy | Description |
|----------|-------------|
| `SIMPLE` | Single vector query → Qdrant ANN search |
| `HYDE` | Generate hypothetical answer → embed it → search |
| `RAG_FUSION` | 4 rewrites (original + legal/timeline/clause) → RRF k=60 fusion |
| `MONGO` | Lexical BM25-lite (coverage + TF saturation + phrase bonus) fallback |

### Backend Auto-Resolution
```
AUTO → VectorClient.is_healthy() → QDRANT
     → else                      → MONGO (lexical BM25-lite)
```

### RAG Request Flow
```
POST /api/retrieval-engine/rag
  → RetrievalService.rag(request)
       ├─ search() → ranked chunks (SearchResponse)
       ├─ _assemble_context() [12,000 char budget, top-down]
       ├─ _build_rag_prompt()
       ├─ LLMGenerator.generate() → answer text
       └─ Build Citation[] with document metadata
  ← RagResponse { answer, citations[], strategy_used, timings{} }
```

### Contract Iterative QA
```
POST /api/retrieval-engine/contract-qa
  → RetrievalService.contract_iterative_qa()
       For iteration 1..max_iterations:
         ├─ _extract_clause_hints() (GCC/SCC regex)
         ├─ _retrieve_contract_evidence()
         │    ├─ search() for each query variant
         │    └─ _expand_contract_clause_results()
         │         (reconstruct full clause text by chunk_index order)
         ├─ _build_citation_map() [C1, C2... labels]
         ├─ _build_iterative_prompt() [SCC > GCC precedence rule]
         ├─ LLMGenerator.generate() → draft
         ├─ _enforce_citations() [guardrail: strip uncited sentences]
         └─ _critique_and_refine() → new query refinements
       ← ContractQAResponse { answer, citations[], trace[], timings{} }
```

### Observability
All RAG runs logged to `observability_runs` MongoDB collection:
- `run_type`: search_qdrant / search_mongo / rag_qdrant / contract_iterative_qa / ingestion
- `breakdown_ms`: per-stage timing dict
- Raw queries redacted in production (`OBSERVABILITY_STORE_RAW_QUERIES=false`)

---

## 9. Letter Drafting AI Flow (LangGraph)

### Letter Lifecycle States
```
INPUT → PLANNING → DRAFTING → REVIEW → APPROVAL → COMPLETED
```

### LangGraph Workflow (LANGGRAPH_ENABLED=true)
```
POST /api/letter-drafting/start
  → LetterDraftingService
       ├─ InputValidator.validate()         # check required fields + constraints
       ├─ IncomingAnalyzer.analyze()
       │    └─ LLM: parse incoming letter for response strategy
       ├─ DraftingContext.assemble()
       │    ├─ RAG: retrieve relevant contracts/letters (RetrievalService)
       │    ├─ FalkorGraphService: graph-linked letter chain
       │    └─ StrategyContextService: evidence bundle
       ├─ DraftingPlanning.plan()
       │    └─ LLM (LANGGRAPH_PLAN_MODEL=grok-4-1-fast): strategic plan
       ├─ DraftingGenerator.generate() — streaming SSE
       │    └─ LLM (LANGGRAPH_DRAFTER_MODEL=gpt-4o): full letter draft
       └─ DraftingValidator.validate()      # quality, citations, length checks

POST /api/letter-drafting/{id}/review
  → LLM Reviewer (LANGGRAPH_REVIEWER_MODEL=gpt-4o-mini)
  → Structured feedback + suggested edits

POST /api/letter-drafting/{id}/approve
  → RBAC: requires drafting_management permission
  → LetterService.create_approved_letter()
  → NotificationsService.notify_stakeholders()
```

### Deep Planning (Evidence-First Strategy)
```
POST /api/deep-planning/strategy
  → StrategyContextService.build_context()
       ├─ Retrieve relevant documents (RAG)
       ├─ Fetch linked letters (graph traversal)
       ├─ Fetch claims, concerns, contract clauses
       └─ Return structured StrategyContext

POST /api/deep-planning/evidence-bundle
  → EvidenceBundleService.assemble()
       └─ ZIP + metadata for legal review
```

### AI Models Used
| Role | Model | Config Key |
|------|-------|-----------|
| Strategic Planner | grok-4-1-fast | `LANGGRAPH_PLAN_MODEL` |
| Letter Drafter | gpt-4o | `LANGGRAPH_DRAFTER_MODEL` |
| Quality Reviewer | gpt-4o-mini | `LANGGRAPH_REVIEWER_MODEL` |
| General | gpt-4o-mini | `LANGGRAPH_MODEL` |

---

## 10. Razorpay / Payment & Subscription Flow

### Payment Gateway Architecture
```
PaymentGatewayInterface (Abstract Base Class)
  ├─ NoOpPaymentGateway    [default; PAYMENT_PROVIDER=noop]
  │    └─ All ops local-only; no external calls; dev/manual billing
  ├─ RazorpayGateway       [PAYMENT_PROVIDER=razorpay]
  │    └─ Lazy import razorpay SDK; INR billing
  └─ StripeGateway         [PAYMENT_PROVIDER=stripe] — STUB ONLY
```

### Razorpay Event Normalization Map
| Razorpay Raw Event | Normalized Type |
|--------------------|----------------|
| `subscription.charged`, `payment.captured`, `order.paid` | `payment.succeeded` |
| `subscription.activated`, `subscription.authenticated` | `subscription.activated` |
| `subscription.cancelled`, `subscription.completed` | `subscription.cancelled` |
| `subscription.halted`, `subscription.pending` | `subscription.halted` |
| `payment.failed` | `payment.failed` |
| (anything else) | `ignored` |

### Webhook Security
- Signature verification: HMAC-SHA256 over raw payload body
- Secret: `RAZORPAY_WEBHOOK_SECRET` env var
- Deny-by-default: returns `False` if secret or signature is missing

### Subscription Lifecycle
```
Plan Families:
  dms_saas | drafting_bundle | archive | offboarding | no_service

Billing Periods:
  monthly | quarterly | semi_annual | annual

Subscription Status FSM:
  pending → trial → pilot → active → past_due → paused →
  cancelled → offboarding → archive

API Lifecycle Operations:
  POST .../subscriptions/trial/start     → StartTrialRequest
  POST .../subscriptions/{id}/convert    → ConvertTrialRequest
  POST .../subscriptions/{id}/upgrade    → UpgradeDowngradeRequest
  POST .../subscriptions/{id}/downgrade
  POST .../subscriptions/{id}/add-on     → AddOnActionRequest (add/remove)
  POST .../subscriptions/{id}/cancel     → CancelSubscriptionRequest
  POST .../subscriptions/{id}/reactivate
```

### Webhook Processing Flow
```
POST /api/billing/webhook (Razorpay → backend)
  → BillingWebhookService.process()
       ├─ RazorpayGateway.verify_webhook_signature()  [HMAC-SHA256]
       ├─ RazorpayGateway.parse_webhook_event()       [normalize to WebhookEvent]
       └─ Handle by event_type:
            payment.succeeded    → billing_status = "paid"
            payment.failed       → billing_status = "failed"; notify org admin
            subscription.activated → SubscriptionStatus.ACTIVE
            subscription.cancelled → SubscriptionStatus.CANCELLED
            subscription.halted    → SubscriptionStatus.PAST_DUE
```

### Pricing Model
- Prices in **paise** (INR × 100) via `price_minor` integer field
- Per-period tiers: `{"monthly": 2500000, "quarterly": 7000000, "annual": 25000000}`
- Discounts: `{"quarterly": 6.7%, "annual": 16.7%}`
- Add-ons: type = `feature | capacity | support`; billed per `billing_cadence`

### Expert Allocation (Drafting Bundles)
```
AssignmentRole: drafter | reviewer | senior_reviewer | drafting_manager
AssignmentStatus: active | inactive | expired | revoked

Expert Allocation ties:
  expert_user_id → organization_id → project_id → drafting_request_id → letter_id
```

---

## 11. Frontend

### Tech Stack
| Item | Technology |
|------|-----------|
| Framework | React 18 + React Router v6 |
| Build | Vite |
| Styling | Tailwind CSS + shadcn/ui |
| State | React Context + custom hooks |
| Forms | Zod validation schemas |
| HTTP | Fetch + cookie-based auth (http.ts) |
| Testing | Vitest + Testing Library |

### Route Map
| Route | Page | Access |
|-------|------|--------|
| `/` | `LandingPage` | Public |
| `/login` | `LoginPage` | Public |
| `/register` | `RegisterPage` | RoleGuard (admin) |
| `/overview` | `Overview` | Authenticated |
| `/dashboard` | `Dashboard` | Authenticated |
| `/organizations` | `OrganizationsPage` | Authenticated |
| `/projects` | `ProjectsPage` | Authenticated |
| `/documents` | `DocumentsPage` | Authenticated |
| `/documentsearch` | `EnhancedDocumentsPage` | Authenticated |
| `/upload` | `UploadPage` | Authenticated |
| `/documentviewer/:id` | `DocumentViewerPage` | Authenticated |
| `/share/:id` | `ShareDocumentPage` | Authenticated |
| `/tags` | `TagsPage` | Authenticated |
| `/folders` | `FolderStructurePage` | Authenticated |
| `/tasks` | `TasksPage` | Authenticated |
| `/parties` | `PartiesInvolvedPage` | Authenticated |
| `/letters` | `LetterWorkflowPage` | RoleGuard |
| `/letters/:id/input` | `LetterInputPage` | Authenticated |
| `/letters/:id/strategic-plan` | `LetterStrategicPlanPage` | Authenticated |
| `/letters/:id/draft` | `LetterDraftPage` | Authenticated |
| `/letters/:id/review` | `LetterReviewPage` | Authenticated |
| `/letters/:id/approval` | `LetterApprovalPage` | Authenticated |
| `/letters/:id/completed` | `LetterCompletedPage` | Authenticated |
| `/letter-quality` | `LetterQualityDashboardPage` | Authenticated |
| `/letter-templates` | `LetterTemplatePage` | Authenticated |
| `/letter-templates/:id/edit` | `LetterTemplateEditorPage` | Authenticated |
| `/contracts` | `ContractsPage` | Authenticated |
| `/contracts/upload` | `ContractsUploadPage` | Authenticated |
| `/contracts/search` | `ContractsSearchPage` | Authenticated |
| `/contracts/qa` | `ContractQAPage` | Authenticated |
| `/contracts/appraisal` | `ContractAppraisalPage` | Authenticated |
| `/claims` | `ClaimsRegisterPage` | Authenticated |
| `/sla` | `SLATrackerPage` | Authenticated |
| `/reports` | `ReportsAnalyticsPage` | Authenticated |
| `/users` | `UsersPage` | Authenticated |
| `/permissions` | `PermissionsPage` | Authenticated |
| `/plan-settings` | `PlanSettingsPage` | Authenticated |
| `/subscription-management` | `SubscriptionManagementPage` | RoleGuard |
| `/notifications` | `NotificationCenterPage` | Authenticated |
| `/email-groups` | `EmailGroupsPage` | Authenticated |
| `/representatives` | `RepresentativesPage` | Authenticated |
| `/reference/:id` | `ReferencePage` | Authenticated |
| `/profile` | `ProfilePage` | Authenticated |
| `/settings` | `SettingsPage` | Authenticated |
| `/health` | `HealthPage` | RoleGuard (admin) |

### Component Architecture
```
src/
├─ App.tsx / main.tsx / routes.tsx
├─ components/
│  ├─ layout/   MainLayout, RouteSkeleton (Suspense fallback)
│  ├─ auth/     ProtectedRoute, RoleGuard
│  ├─ documents/              Document list, viewer, uploader
│  ├─ letter-workflow/        Letter state machine UI
│  ├─ letter-template/        Template editor
│  ├─ contract-appraisal/     Appraisal form
│  ├─ search/                 Search facets and results
│  ├─ claims/                 Claims register UI
│  ├─ dashboard/              Charts, stats widgets
│  ├─ reports/                Report viewer
│  ├─ langgraph/              LangGraph streaming output UI
│  ├─ settings/               Settings panels
│  ├─ common/                 Button, Modal, Table, etc.
│  └─ ui/                     shadcn/ui re-exports
├─ services/                  API client modules
├─ contexts/                  AuthContext, NotificationContext
├─ hooks/                     useAuth, usePermission, useSearch
├─ models/                    TypeScript API shape interfaces
├─ schemas/                   Zod validation schemas
├─ types/                     Global TypeScript types
└─ utils/                     Date formatting, file helpers
```

---

## 12. Frontend — API Integration Layer

All calls routed through `services/http.ts` (attaches auth cookie, handles 401 redirect).

| Service File | Backend Endpoints | Key Functions |
|-------------|------------------|---------------|
| `auth.ts` | `/api/auth/*` | login, logout, getMe, refreshToken |
| `enhanced-api.ts` (52k) | Multiple | Main omnibus API client; covers most entities |
| `contracts-api.ts` | `/api/contracts/*` | Chunked upload, list, QA, search, appraisal |
| `documents-api.ts` | `/api/documents/*` | Upload, list, delete, download |
| `claims-api.ts` | `/api/claims/*` | CRUD, bulk operations |
| `search-api.ts` | `/api/search/*` + `/api/retrieval-engine/*` | Semantic search, RAG queries |
| `plan-settings-api.ts` | `/api/rbac-monetization/*` | Plan CRUD, subscription management, add-ons |
| `billing-api.ts` | `/api/billing/*` | Webhook test, invoice retrieval |
| `tags-api.ts` | `/api/tags/*` | Tag CRUD, hierarchy |
| `organizations-api.ts` | `/api/organizations/*` | Org CRUD |
| `projects-api.ts` | `/api/projects/*` | Project CRUD |
| `email-groups-api.ts` | `/api/email-groups/*` | Group management |
| `email-service.ts` | `/api/email/*` | Send, share |
| `tasks-api.ts` | `/api/tasks/*` | Task CRUD |
| `dashboard-api.ts` | `/api/dashboard/*` | Stats aggregation |
| `audit-api.ts` | `/api/reports/audit` | Audit log fetch |
| `storage-settings-api.ts` | `/api/storage-settings/*` | Storage config |
| `smtp-settings-api.ts` | `/api/smtp-settings/*` | SMTP config |
| `sla-api.ts` | `/api/sla/*` | SLA status |
| `session-api.ts` | `/api/auth/session/*` | Session management |
| `step-up.ts` | `/api/auth/step-up` | Re-auth for sensitive actions |
| `letter-workflow-api.ts` | `/api/letter-drafting/*` + `/api/letters/*` | Letter drafting state machine |

---

## 13. Deployment & Infrastructure

### Docker Compose Services
| Service | Image / Source | Port | Networks |
|---------|---------------|------|----------|
| `backend` | `./backend/Dockerfile` | `8000:8000` | service-net, data-net |
| `contract-worker` | same Dockerfile, `python -m rbac_backend.worker` | — | service-net, data-net |
| `client` | `./client/Dockerfile` | `3000:5173` | edge-net, service-net |
| `gateway` | `httpd:2.4` (Apache) | `80:80` | edge-net, service-net |
| `mongo` | `mongo:8.0` | `27018:27017` | data-net |
| `qdrant` | `qdrant/qdrant:latest` | `6333:6333` | data-net |
| `falkordb` | `falkordb/falkordb:latest` | `6380:6379` | data-net |
| `redis` | `redis:7-alpine` | `6379:6379` | service-net |
| `clamav` | `clamav/clamav:latest` | `3310:3310` | service-net |
| `graphiti` *(experimental)* | `./services/graphiti` | `8080:8080` | service-net, data-net |

### Network Isolation
```
edge-net    → client ↔ gateway (public internet boundary)
service-net → gateway ↔ backend ↔ redis ↔ clamav
data-net    → backend ↔ mongo ↔ qdrant ↔ falkordb
```

### Production Compose (`docker-compose.prod.yml`)
- MongoDB 3-node replica set (mongo1/mongo2/mongo3)
- Nginx with SSL termination (replaces Apache HTTPD)
- Named Docker volumes with external backing
- Environment-specific secrets management

### Production Safety Guards (enforced at startup)
| Setting | Requirement |
|---------|------------|
| `ALLOW_DEV_HEADERS` | Must be `false` |
| `RBAC_ENTITLEMENT_FAIL_OPEN` | Must be `false` |
| `SECRET_KEY` | ≥ 32 characters |
| `AUTH_COOKIE_SECURE` | Must be `true` |
| `DATABASE_URL` | Must not contain `localhost` |
| MongoDB | Must use replica set (unless `MONGODB_ALLOW_STANDALONE_PRODUCTION=true`) |
| CORS origins | No localhost/127.0.0.1 origins |
| `LANGGRAPH_API_TOKEN` | Required when `LANGGRAPH_ENABLED=true` |
| Redis URL | Required |
| `METRICS_TOKEN` | Required when `METRICS_ENABLED=true` |
| `CLAMAV_FAIL_OPEN` | Must be `false` in antivirus-enabled prod |
| `OBSERVABILITY_STORE_RAW_QUERIES` | Must be `false` |

### Health Checks
| Service | Check Method |
|---------|-------------|
| Backend | `GET /health/ready` → checks MongoDB, Qdrant, FalkorDB, Redis |
| Qdrant | TCP port 6333 probe |
| FalkorDB | `redis-cli -h localhost ping` |
| Redis | `redis-cli -h localhost ping` |
| MongoDB | `mongosh --eval "db.adminCommand('ping')"` |
| ClamAV | `clamdscan --ping` |

---

## 14. Observability & Monitoring

### Logging
- Rotating file handler: `logs/bulk_upload.log` (10MB × 5 backups)
- Structured request log fields: `request_id`, `trace_id`, `method`, `path`, `status_code`, `duration_ms`
- Slow request warning at `SLOW_REQUEST_THRESHOLD_MS=2000ms`

### Distributed Tracing (opt-in)
- OpenTelemetry (`OTEL_ENABLED=true`)
- OTLP exporter to `OTEL_EXPORTER_OTLP_ENDPOINT`
- `current_trace_id()` injected into every request log line

### Metrics
- `ObservabilityRegistry` tracks per-path request counts + latencies
- Prometheus-compatible scrape endpoint (protected by `METRICS_TOKEN`)

### RAG Observability (`observability_runs` collection)
```json
{
  "run_type": "contract_iterative_qa",
  "org_id": "...", "project_id": "...",
  "strategy": "RAG_FUSION",
  "query": "[REDACTED in prod]",
  "retrieved": [{"chunk_id": "...", "score": 0.85}],
  "breakdown_ms": {
    "vector_search_ms": 45,
    "generation_ms": 1200,
    "total_ms": 1260
  },
  "user_id": "..."
}
```

### Performance Monitor
`PerformanceMonitorService` — sliding window p50/p95/p99 per endpoint, exposed via `GET /api/performance`.

### Error Tracking
- Sentry DSN integration (`SENTRY_DSN`) for unhandled exception capture

---

## 15. Ancillary Services

### `services/langgraph/` — LangGraph Microservice
- Separate FastAPI service for long-running letter drafting graph workflows
- Not in default Docker Compose; used via API when `LANGGRAPH_ENABLED=true`

### `services/graphiti/` — Graphiti Knowledge Graph (Experimental)
- Temporal knowledge graph on top of FalkorDB + Qdrant
- Activated via `--profile graph-experimental` in Docker Compose
- Enabled with `GRAPH_PROVIDER=graphiti` (default: `direct_falkor`)

### `services/docling/` — Docling Document Processor (NOT DEPLOYED)
- Standalone OCR microservice not registered in any Compose file
- Production OCR uses `ocr_service.py` (Marker CLI) instead

### Marker CLI (Production OCR)
```
MARKER_ENABLED=true
MARKER_CMD=marker
MARKER_OUTPUT_DIR=uploads/marker
```
Shells out to `marker` CLI; produces markdown/text from PDF.

### SMTP / Email
- Global default: `smtp.gmail.com:587` STARTTLS
- Per-org: configurable `SmtpSettings`; credentials encrypted with Fernet key
- APScheduler digest jobs: daily 09:00 + weekly Sunday 09:00

### SLA Tracker
- `sla_service.run_sla_scan()` runs daily at 08:00 via APScheduler
- Checks document dates against SLA deadlines
- Emits in-app notifications on breach/warning

---

## Cross-Cutting Concerns

| Concern | Implementation |
|---------|---------------|
| **Authentication** | JWT in `cc_access_token` httpOnly cookie |
| **CSRF** | Double-submit cookie validation for unsafe methods |
| **Rate Limiting** | IP-based + email-based login throttle via Redis counters |
| **CORS** | Configurable origins; localhost blocked in production |
| **Antivirus** | ClamAV (optional, `ANTIVIRUS_ENABLED`); `fail_open=true` in dev |
| **Secret Safety** | Startup validation rejects all placeholder values |
| **Data Isolation** | Every query scoped by `organization_id` + `project_id` |
| **Deduplication** | Ingestion checks `content_hash` before re-embedding |
| **Idempotency** | Vector upserts; MongoDB `ReplaceOne` with `upsert=True` |
| **WebSocket** | Real-time push via `/ws` + Redis pub/sub fanout |
| **Pagination** | `page` + `limit` + `has_next` / `has_prev` on all list endpoints |
