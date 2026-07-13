# Contract Document Appraisal Report — Repo-Grounded Implementation Plan (v2)

**Product:** Contraclaim DMS
**Module:** Contracts → AI Contract Appraisal
**Audience:** Claude Code (implementation agent) + Manish (review/decisions)
**Supersedes:** `Contract_Document_Appraisal_Report_Integration_Plan.md` and `..._Final_Implementation_Specification.md`
**Why v2:** The original plan was written as if Contraclaim DMS were greenfield. It is not. The repo already ships ~80% of the proposed infrastructure (async ingest queue, clause-level extraction, a citation-enforced contract-QA engine, RBAC, audit, DOCX export, the Contracts UI). This version maps every requirement onto **what already exists** so the feature is built by *orchestration and reuse*, not reinvention.

---

## 0. How Claude Code should use this document

1. Read the "Repo reality check" (§2) first — it lists the exact files/services to reuse.
2. Implement phase by phase (§9). Each phase names the precise files to **create** and **edit**.
3. The core design rule: **the appraisal generator is a thin orchestrator over `RetrievalService.contract_iterative_qa`**, modelled on the existing `ClaimAssessmentService`. Do not build a new OCR / clause-index / RAG / LLM stack.
4. Follow existing conventions (Motor/MongoDB + Pydantic models aliased to `_id`, `PolicyService` authorization, `AuditEventService` audit, the Redis queue pattern). Do not introduce SQLAlchemy tables or a second job system.
5. Mirror existing tests in `backend/rbac_backend/tests/` for each new capability (scope isolation, citations, versioning).

---

## 1. Stack (verified from the repo)

| Layer | Reality | Source |
|---|---|---|
| Backend | FastAPI app `backend/rbac_backend`, routers mounted under `/api` | `main.py` |
| Persistence | **MongoDB via Motor** (`core/database.get_database()`), Pydantic models with `alias="_id"`, `ConfigDict(populate_by_name=True)` | `models/claim.py`, `services/claim_assessment_service.py` |
| Auth | `core/security.get_current_user` → `CurrentUser` | every router |
| Authorization | `PolicyService().authorize(user, "<perm>", resource_type=, organization_id=, project_id=)` + `.authorize_document(...)` | `routers/contracts.py` |
| Async jobs | Redis queue `ContractIngestQueue` (`enqueue` → worker → `contract_service.process_contract_ingest_job`), with inline `asyncio` fallback | `services/contract_ingest_queue.py`, `routers/contracts.py` |
| Document ingest / OCR / clause extraction | Already done at upload; produces clause-level chunks with `clause_number`, `clause_title`, `clause_type`, `parent_clause_number`, `toc_path`, `page_numbers`, `clause_tags` | `services/contracts_ingest.py`, `models/contract_models.py:ContractClauseChunk` |
| RAG + citations | **`RetrievalService.contract_iterative_qa(ContractQARequest, user)`** → `answer`, `citations`, `trace`; scope-isolated by org/project; `require_citations=True`, `max_iterations` | `retrieval/service.py`, `retrieval/models.py` |
| LLM access | `AIService`, `llm_config_service`, `pydantic_ai_service` (structured output), `openai_service`; LangGraph pipelines in `ai_workflows/langgraph/` | `services/ai_service.py` |
| Export | DOCX generation already used; XLSX via `ExportService` | `services/document_service.py`, `services/letter_drafting/service.py`, `services/export_service.py` |
| Audit | `AuditEventService.emit(action=, actor_id=, resource_type=, resource_id=, organization_id=, project_id=, after=)` | `services/audit_event_service.py` |
| Approvals / SLA | `approval_service`, `models/approval.py`; `sla_service` + APScheduler cron (digests, `run_sla_scan` @ 08:00) | `main.py`, `services/sla_service.py` |
| Downstream targets (Phase 3) | `claim_service`, `claim_assessment_service`, `letter_drafting/*`, `sla_service` already exist | — |
| Frontend | React + Vite (5173), shadcn/ui (`components/ui/*`), pages in `client/src/pages/` (`ContractsPage.tsx`), API clients in `client/src/services/` (`contracts-api.ts`), LangGraph display (`PlanViewer`, `StrategyPlanDisplay`), letter-workflow review/approval components | `client/src/...` |

---

## 2. Repo reality check — what to REUSE vs BUILD

**Already exists → reuse, do not rebuild:**

- OCR / text extraction / page-wise content → the contract ingest pipeline runs at upload (`contracts_ingest`, `contract_ingest_queue`). Appraisal consumes its output.
- Clause indexing / Clause Library → clause chunks already carry full clause metadata and are searchable via `POST /api/contracts/search`. **Do not create a new `contract_clause_index` collection.** Expose a read-only Clause Library view over existing chunks/search.
- Vector / semantic retrieval with tenant isolation → `RetrievalService` (see `test_retrieval_engine_scope_isolation.py`).
- Citation-enforced, no-hallucination generation → `contract_iterative_qa` already returns citations + trace and answers "Information not found" when unsupported. This satisfies AI guardrails §11 and confidence/citation requirements §12–13 of the original spec **for free**.
- Background jobs → the Redis queue pattern; reuse it for the appraisal job.
- RBAC, audit, approvals, DOCX export, scheduler → all present.

**Genuinely new → build:**

- A small set of MongoDB collections to persist the appraisal report, registers, and job status.
- An `contract_appraisal` service package that orchestrates retrieval section-by-section (mirror `ClaimAssessmentService`).
- A new router `routers/contract_appraisal.py` + endpoints.
- Frontend "AI Contract Appraisal" tab + report viewer with citation → source navigation.
- Register-population logic (obligations / risks / key dates) from structured output.

---

## 3. Key deviations from the original spec (the actual improvements)

1. **Generation engine = existing `contract_iterative_qa`, invoked per report section**, not a single mega-prompt. Each of the 22 sections becomes a focused, cited question (mirrors `claim_assessment_service.build_assessment_query`). This gives per-section citations, tenant isolation, and "Not found in uploaded documents" behaviour without new AI code, and keeps each LLM call within context limits for large multi-volume contracts.
2. **Drop `contract_clause_index` and `contract_prompt_versions` collections.** Clause data already lives in the vector store; prompt versioning is handled by a versioned constant in `config/prompts.py` plus `ai_prompt_version`/`ai_model_version` fields stored on each report (the repo already stores model version on AI outputs).
3. **Correct the tenant model.** The repo scopes by `organization_id` + `project_id`; there is **no first-class `contract_id` package entity**. A "contract package" = a set of contract `document_id`s under an (org, project). The appraisal is scoped by `(organization_id, project_id)` and an explicit `document_ids` selection — matching how `ClaimAssessmentService` builds `SearchFilters(org_id, project_id, metadata={"uploadType":"contract"})`. (Optionally introduce a lightweight `contract_packages` collection later; not required for MVP.)
4. **Reuse the Redis queue**, not a new job framework. Add an `appraisal` job type (separate queue name or reuse with a `job_type` field) using the same hash-status semantics, with the same inline `asyncio` fallback when Redis is disabled.
5. **RBAC via concrete `PolicyService` permission strings** (e.g. `contracts.appraisal.generate`), not the abstract role-matrix in the original spec. Map them in the existing roles/permissions seed data.
6. **Endpoints live on the existing `/api/contracts` surface** and follow existing naming. Because there is no `contract_id` path entity, appraisal is addressed by `report_id` + query-scoped org/project, consistent with current routers.
7. **Export reuses the existing DOCX path**; PDF only if a generator is already wired (verify `requirements.txt`) — otherwise ship DOCX in MVP and add PDF in Phase 2.
8. **Phase 3 has real homes already:** wire appraisal output into `claim_service`/`claim_assessment_service`, `letter_drafting` context builders, and key-date alerts into the existing APScheduler + `sla_service`.

---

## 4. Architecture / data flow (reusing components)

```
Contract docs uploaded (existing)
   → contract_ingest_queue → contracts_ingest  (OCR, clause chunks, vector index)  [EXISTS]
        │
        ▼
POST /api/contracts/appraisal/generate
   → create contract_appraisal_jobs doc (status=queued)
   → enqueue on Redis queue (or inline asyncio fallback)         [reuse ContractIngestQueue pattern]
        │  worker:
        ▼
AppraisalService.run(job):
   completeness check (which doc types present)                  [new, small]
   for each report section:
       build focused cited question                              [mirror claim_assessment_service]
       RetrievalService.contract_iterative_qa(request, user)     [EXISTS]  → answer + citations + trace
   assemble full_report_markdown + structured_output JSON
   derive overall_risk_rating + confidence                       [from section confidences]
   persist contract_appraisal_reports (status=draft, version=n, is_locked=false)
   AuditEventService.emit("contract_appraisal.generated", ...)   [EXISTS]
        │
        ▼
Human review (comments / edit) → approve → is_locked=true        [reuse approval patterns]
        │
        ▼
create-registers → contract_obligations / contract_risks / contract_key_dates  [from structured_output]
export DOCX (and PDF if available)                               [reuse docx path]
```

---

## 5. Data model (MongoDB / Pydantic — minimal, repo-native)

Conventions: Pydantic `BaseModel`, `id: str = Field(default_factory=lambda: str(uuid4()), alias="_id")`, `model_config = ConfigDict(populate_by_name=True)`, persist with `.model_dump(by_alias=True)`, every doc carries `organization_id` + `project_id`. Place models in `models/contract_appraisal.py`.

**Collections to create:**

- `contract_appraisal_jobs` — `organization_id, project_id, document_ids[], requested_by, status (queued|running|generating|completed|failed|cancelled), current_step, progress, error_message, report_id?, ai_model_version, ai_prompt_version, started_at, completed_at, created_at, updated_at`. (Redis holds live queue state; this Mongo doc is the durable record the UI polls — mirror `StatusResponse`.)
- `contract_appraisal_reports` — `organization_id, project_id, job_id, document_ids[], report_version, status (draft|under_review|approved|superseded|rejected), generated_by_ai, ai_model_version, ai_prompt_version, document_completeness_status, missing_documents[], executive_summary, full_report_markdown, structured_output{}, citations[], overall_risk_rating, confidence_score, reviewed_by, approved_by, approved_at, is_locked, created_at, updated_at`.
- `contract_obligations`, `contract_risks`, `contract_key_dates` — as in the original spec §6.4–6.6 (keep the fields, including `source_quote`, `clause_reference`, `page_number`, `confidence_score`, `verification_status`), each scoped by org/project and linked by `report_id`.
- `contract_appraisal_review_comments` — as original spec §6.7.

**Do NOT create:** `contract_clause_index` (reuse vector store), `contract_prompt_versions` (use `config/prompts.py` constant + version fields), a separate appraisal audit collection (reuse `AuditEventService`).

---

## 6. Backend implementation

**New files:**

- `models/contract_appraisal.py` — Pydantic models + request/response schemas (mirror `models/contract_models.py` + `models/claim.py`).
- `services/contract_appraisal/__init__.py`
- `services/contract_appraisal/prompts.py` — versioned section question-builders (mirror `claim_assessment_service._TYPE_FOCUS` and `config/prompts.py`). Export `APPRAISAL_PROMPT_VERSION = "1.0"`.
- `services/contract_appraisal/generator.py` — `AppraisalGenerator` that loops sections, calls `RetrievalService.contract_iterative_qa`, assembles markdown + structured JSON + aggregate confidence.
- `services/contract_appraisal/service.py` — `AppraisalService`: job lifecycle, persistence, register creation, versioning, approval. Mirror `ClaimAssessmentService` (build request → invoke engine → persist answer+citations+trace → audit).
- `services/contract_appraisal/repository.py` — Motor CRUD for the new collections (mirror `letter_drafting/repository.py`).
- `routers/contract_appraisal.py` — endpoints (below).

**Files to edit:**

- `main.py` — import and `app.include_router(contract_appraisal.router, prefix="/api", tags=["contract-appraisal"])`.
- `services/contract_ingest_queue.py` (or a sibling `appraisal_queue.py`) — add appraisal job enqueue/worker, or add a `job_type` discriminator. Reuse retry/dead-letter semantics. Wire start/stop in `main.py` startup/shutdown behind a settings flag.
- `core/config` (`config/settings.py`) — add `APPRAISAL_QUEUE_ENABLED`, model/prompt version defaults, generation rate-limit.
- roles/permissions seed (`initial_data/…` + `services/permission_service`/`role_service`) — register new permissions.
- `config/prompts.py` — add appraisal system prompt + disclaimer constant.

**Endpoints** (scope via body/query `organization_id` + `project_id`, addressed by `report_id`):

```
POST   /api/contracts/appraisal/generate          # body: org, project, document_ids[] → returns job
GET    /api/contracts/appraisal/jobs/{job_id}     # poll status/progress
POST   /api/contracts/appraisal/jobs/{job_id}/cancel
GET    /api/contracts/appraisal                    # list reports for (org, project)
GET    /api/contracts/appraisal/{report_id}
PUT    /api/contracts/appraisal/{report_id}        # edit draft (authorised)
POST   /api/contracts/appraisal/{report_id}/approve
POST   /api/contracts/appraisal/{report_id}/reject
POST   /api/contracts/appraisal/{report_id}/regenerate    # new version, never overwrite
POST   /api/contracts/appraisal/{report_id}/create-registers
GET    /api/contracts/appraisal/{report_id}/export/docx
GET    /api/contracts/appraisal/{report_id}/export/pdf     # if generator available
GET    /api/contracts/appraisal/{report_id}/citations
POST   /api/contracts/appraisal/{report_id}/review-comments
GET    /api/contracts/appraisal/{report_id}/review-comments
# Registers (read/update) + read-only Clause Library over existing search:
GET    /api/contracts/obligations  | PUT .../{id}
GET    /api/contracts/risks        | PUT .../{id}
GET    /api/contracts/key-dates    | PUT .../{id}
GET    /api/contracts/clauses       # thin wrapper over existing contract search/chunks
```

Every handler: `current_user = Depends(get_current_user)` → `PolicyService().authorize(...)` → service call → `AuditEventService.emit(...)`. Use the `@handle_exceptions` decorator and `ContractError` like `routers/contracts.py`.

---

## 7. AI generation design (the core)

Mirror `services/claim_assessment_service.py` exactly, generalised to report sections:

1. For each of the 22 sections, `prompts.py` provides a focused question (e.g. for "Time, Milestones & Delay": *"Identify commencement, time for completion, sectional completion, delay damages, EOT clauses and notice/time-bar requirements. State SCC vs GCC precedence. Cite the clauses relied on; if absent say 'Not found in uploaded documents'."*).
2. Build `ContractQARequest(query=…, filters=SearchFilters(org_id, project_id, metadata={"uploadType":"contract","document_type":"contract"}), require_citations=True, max_iterations=3)` — restricting to the selected `document_ids` where supported by `SearchFilters`.
3. `answer, citations, trace = await retrieval_service.contract_iterative_qa(request, user)`.
4. Convert each section's answer to markdown; collect citations into the report's `citations[]`; populate `structured_output` for register-bearing sections (obligations, risks, key dates, claim/variation triggers).
5. **Confidence & guardrails come from the engine**: items with no citation or "Information not found" → `verification_status="requires_human_review"`. Aggregate `confidence_score` from section/citation coverage; map to `overall_risk_rating` thresholds (spec §12).
6. Append the standard disclaimer (spec §26) to `full_report_markdown`.
7. Store `ai_model_version` (from `llm_config_service`) and `ai_prompt_version` (`APPRAISAL_PROMPT_VERSION`) on the report.

This guarantees: analyses only uploaded, tenant-scoped contract docs; cites document/clause/page; flags missing/low-confidence; no legal opinion (prompt instruction) — satisfying original spec §11–13 by construction.

---

## 8. Frontend

- **New page/tab:** add "AI Contract Appraisal" to `client/src/pages/ContractsPage.tsx` using existing `components/ui/tabs.tsx`. New components under `client/src/components/contract-appraisal/`.
- **Report viewer:** reuse `components/langgraph/PlanViewer.tsx` / `StrategyPlanDisplay.tsx` styling for section navigation; render `full_report_markdown`; clicking a citation opens the source document at the page (reuse `components/document-viewer/`).
- **Review/approval:** reuse `components/letter-workflow/` (`LetterReviewComponent`, `LetterApprovalComponent`, `StatusBadge`) patterns.
- **Job progress:** poll `GET /jobs/{job_id}` with `components/ui/progress.tsx` (mirror existing contract upload status polling in `services/contracts-api.ts`).
- **Registers:** `components/ui/table.tsx` for obligations/risks/key-dates; risk summary via `components/ui/card.tsx` + `badge.tsx`.
- **API client:** extend `client/src/services/contracts-api.ts` with appraisal calls.

---

## 9. Phased plan (mapped to files)

**Phase 1 — MVP report generation**
Create: `models/contract_appraisal.py`, `services/contract_appraisal/{prompts,generator,service,repository}.py`, `routers/contract_appraisal.py`. Edit: `main.py` (router + queue wiring), `config/prompts.py`, settings, permissions seed. Frontend: appraisal tab, generate button, job-progress poll, markdown viewer, DOCX export.
Deliverable: upload (existing) → generate → cited draft report → review/edit → approve/lock → export DOCX.

**Phase 2 — Structured intelligence**
Add `create-registers` → populate `contract_obligations`/`contract_risks`/`contract_key_dates` from `structured_output`; confidence display + citation→source viewer; read-only Clause Library over existing search; PDF export (if generator available); register UI tables.

**Phase 3 — Deep integration (homes already exist)**
Feed approved appraisal into `claim_service`/`claim_assessment_service` and `letter_drafting` context builders (clause refs, notice requirements, records). Notice/key-date deadline alerts via the existing APScheduler cron + `sla_service` scan pattern.

**Phase 4 — Dashboards & alerts**
Extend `routers/dashboard.py` with contract risk score, open/overdue obligations, upcoming key dates, pending-approval and low-confidence counts (read from the new collections). Portfolio risk via aggregation across (org, project).

---

## 10. Security & tenant isolation

- Every query includes `organization_id` + `project_id`; retrieval restricted via `SearchFilters` (validated by `test_retrieval_engine_scope_isolation.py` — add an equivalent test for appraisal).
- Authorize with `PolicyService` on every endpoint; new permissions: `contracts.appraisal.{generate,view,edit,approve,reject,export,create_registers}`.
- Approved reports `is_locked=true`; regeneration always inserts a new `report_version`, never overwrites (enforce in `repository.py`).
- Audit every action via `AuditEventService.emit`.
- Rate-limit generation (settings flag) to protect LLM spend.

---

## 11. Testing (mirror existing tests in `backend/rbac_backend/tests/`)

- `test_contract_appraisal_scope_isolation.py` — Org A cannot retrieve Org B docs during generation (model on `test_retrieval_engine_scope_isolation.py`).
- `test_contract_appraisal_citations.py` — every register item has a citation or `requires_human_review`; unsupported facts → "Not found in uploaded documents" (model on `test_claim_assessment.py`).
- `test_contract_appraisal_versioning.py` — approve v1 → regenerate → v1 stays locked/unchanged, v2 created.
- `test_contract_appraisal_endpoint.py` — RBAC denial paths, job lifecycle (model on `test_deep_planning_endpoint.py`).
- Export test — DOCX contains sections, citations, disclaimer (model on `test_export.py`).

---

## 12. Acceptance criteria (repo-specific)

Upload+ingest (existing) feeds appraisal · generate runs as a queued job with visible progress · report cites document/clause/page via `contract_iterative_qa` · missing→"Not found", low-confidence→"Requires Human Review" · review/edit/approve/lock + new-version-on-regenerate · obligations/risks/key-dates populated from `structured_output` · DOCX export with disclaimer · tenant isolation + RBAC + audit verified by tests.

---

## 13. Open decisions for Manish

1. **Contract "package" scope:** confirm appraisal is scoped by `(organization_id, project_id, document_ids[])` for MVP (recommended), or do you want a first-class `contract_packages` collection now?
2. **PDF export in MVP** or DOCX-only first? (Depends on whether a PDF generator is already in `requirements.txt`.)
3. **Generation model:** which model via `llm_config_service` (cost vs quality), and the per-report generation rate limit?
4. **Approver roles:** confirm which existing roles get `contracts.appraisal.approve`.
5. **Section granularity vs cost:** 22 separate cited retrievals per report is highest-fidelity but more LLM calls — acceptable, or batch related sections?
```
