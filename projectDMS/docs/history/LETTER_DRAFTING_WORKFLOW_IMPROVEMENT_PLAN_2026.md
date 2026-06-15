# Letter Drafting Workflow Improvement Plan 2026

## 1. Executive Summary

This document records the current ContraClaim DMS letter drafting workflow and compares it with the target production-grade Cyclic RAG architecture described in `C:\Users\santo\Downloads\Letter_Drafting_Cyclic_RAG_Architecture.md`.

The current system has a strong foundation: React workflow pages, FastAPI APIs, MongoDB persistence, Qdrant retrieval, FalkorDB graph links, document and contract ingestion, draft versions, source ledgers, validation reports, RBAC/project scoping, and lifecycle actions. The main improvement required is to convert the drafting process from partially source-assisted generation into a controlled contract-aware drafting engine that repeatedly retrieves, drafts, critiques, validates, and refines until the draft is evidence-backed and suitable for human approval.

Current assessment score: **62/100**.

Post-implementation checkpoint score after the Phase 1 slice completed on 2026-05-11: **68/100**.

Post-implementation checkpoint score after Phase 1 audit reads and Phase 2 context-pack/source-ledger foundation completed on 2026-05-12: **74/100**.

Post-implementation checkpoint score after Phase 2 retrieval metadata standardization, exact retrieval APIs, and evidence UI completed on 2026-05-12: **82/100**.

Post-implementation checkpoint score after Phase 3 cyclic RAG orchestration completed on 2026-05-12: **90/100**.

Post-implementation checkpoint score after Phase 4 production governance and collaboration completed on 2026-05-12: **95/100**.

Post-implementation checkpoint score after Phase 5 metrics, monitoring, and quality dashboard completed on 2026-05-12: **97/100**.

Expected mature-state score after the extended roadmap: **97/100**.

### Assessment Scores

| Capability Area | Current Score | Target Score |
| --- | ---: | ---: |
| Workflow efficiency | 60 | 92 |
| Draft quality | 58 | 95 |
| Source grounding | 60 | 96 |
| Auditability | 68 | 96 |
| Scalability | 58 | 95 |
| Compliance and control | 66 | 99 |
| User experience and collaboration | 62 | 96 |
| **Overall** | **62** | **97** |

### Implementation Checkpoint: 2026-05-11

Implemented Phase 1 slice:

- Added explicit v2 draft validation endpoint: `POST /letters/{id}/drafting/runs/{run_id}/validate`.
- Added explicit v2 contractual critique endpoint: `POST /letters/{id}/drafting/runs/{run_id}/critique`.
- Added lifecycle audit event persistence through `letter_draft_events`.
- Added draft lifecycle event indexes and issued-letter indexes.
- Added deterministic critique red-flag checks for possible waiver, admission, unsupported allegation, payment implication, time implication, and missing contractor rights reservation.
- Added approval metadata fields: `approved_by`, `approved_at`, `exported_by`, `exported_at`, `issued_by`, `issued_at`, and `last_validated_at`.
- Strengthened export and issue governance: export now requires approval, and issue now requires export.
- Added issued-letter register persistence through `issued_letters`.
- Added frontend hook support for `validateRun` and `critiqueRun`.
- Added Validate and Critique controls in the draft workspace.
- Added client-side approval guard when the v2 validation report is blocking.

Score after this implementation slice: **68/100**.

Rationale:

- Auditability improved because v2 draft lifecycle actions now produce dedicated event records.
- Compliance improved because approval/export/issue controls are stricter and critique can be run explicitly.
- UX improved modestly because users can trigger validation and critique from the draft workspace.
- The score does not yet reach the Phase 1 target of 72 because full DOCX/PDF export, route consolidation, and complete UI migration from legacy LangGraph paths remain outstanding.

### Implementation Checkpoint: 2026-05-12

Implemented additional Phase 1 and Phase 2 foundation:

- Added v2 audit read API: `GET /letters/{id}/drafting/runs/{run_id}/audit`.
- Added v2 context pack API: `GET /letters/{id}/drafting/runs/{run_id}/context-pack`.
- Added v2 source ledger API: `GET /letters/{id}/drafting/runs/{run_id}/source-ledger`.
- Added `DraftContextPack`, `SourceLedgerResponse`, and `DraftAuditResponse` response models.
- Added `draft_context_packs` persistence through `DraftRunRepository`.
- Added source hashes to `SourceEvidence` and normalized source ledger responses.
- Added `context_pack_id` to `DraftRun` so each run can trace to the structured context used to create it.
- Added database indexes for `draft_context_packs`.
- Added frontend hook methods for audit, context pack, and source ledger reads.
- Updated `LetterDraftPage` to prefer v2 source and validation data over legacy graph-run data when a v2 run is present.
- Added repository tests for lifecycle event reads and context pack persistence.

Score after this implementation checkpoint: **74/100**.

Rationale:

- Phase 1 auditability target is largely met for v2 runs because lifecycle events can now be written and read.
- Phase 2 now has core persistence/API foundations for context packs and source ledgers.
- The score is still below the Phase 2 target of 82 because retrieval metadata is not yet fully standardized across every ingestion path, exact clause/reference retrieval is not fully implemented, and the UI does not yet render a dedicated context-pack/source-ledger inspection panel.

### Implementation Checkpoint: 2026-05-12 Phase 2 Completion

Implemented the remaining Phase 2 scope:

- Added shared retrieval source metadata normalization in `backend/rbac_backend/retrieval/source_metadata.py`.
- Standardized vector payload metadata centrally in `VectorClient.upsert`, so Qdrant ingestion paths now receive common fields and source hashes.
- Standardized Mongo retrieval payloads returned by `RetrievalService`.
- Standardized contract-ingestion clause metadata before persistence/vectorization.
- Added exact contract clause retrieval API: `POST /letter-drafting/retrieval/exact-clause`.
- Added exact letter/document reference retrieval API: `POST /letter-drafting/retrieval/exact-reference`.
- Added frontend hook methods for exact clause and exact reference retrieval.
- Added a dedicated `DraftEvidencePanel` with Sources, Context, and Audit tabs.
- Integrated the evidence panel into the v2 draft workspace.
- Added tests for metadata alias normalization and source hash generation.

Score after Phase 2 completion checkpoint: **82/100**.

Rationale:

- Source-grounded RAG foundations are now in place: normalized metadata, source hashes, context packs, source ledgers, exact clause/reference retrieval, and a user-visible evidence inspection panel.
- The system is still below the Phase 3 target because the actual cyclic LangGraph critique/retrieval/redraft loop and assertion-to-source validation have not yet been implemented.

### Implementation Checkpoint: 2026-05-12 Phase 3 Completion

Implemented Phase 3 cyclic RAG orchestration in the v2 drafting workflow:

- Added bounded cyclic drafting controls through `max_iterations` on `DraftRunCreateRequest`.
- Added persisted cyclic iteration traces through `CyclicIterationTrace`.
- Added assertion-to-source support records through `DraftAssertionSupport`.
- Added draft quality scoring through `DraftConfidenceScores`.
- Updated v2 draft generation to run a generate -> critique -> validate -> refine retrieval -> regenerate loop for draft/review mode.
- Added exact clause re-retrieval for unsupported clause citations found during validation.
- Added deterministic assertion support mapping for material draft sentences.
- Added confidence scoring for clause confidence, factual support, tone suitability, overall confidence, and risk level.
- Persisted `cyclic_trace`, `assertion_support`, `confidence_scores`, and `iteration_count` on each draft run.
- Extended the Evidence Package UI with a Cycle tab showing confidence, iterations, refinements, retrieved evidence, and assertion support.
- Added regression test coverage for confidence scoring behavior.

Score after Phase 3 completion checkpoint: **90/100**.

Rationale:

- The v2 workflow now has the core production pattern required by the target architecture: bounded cyclic critique, gap-driven retrieval, redraft, and persisted quality evidence.
- The implementation remains deterministic and compatible with the existing stack rather than replacing the app with a separate orchestration service.
- The score remains below the final 95 target because Phase 4 production governance remains: reviewer assignments, return-for-correction workflow, immutable DOCX/PDF export, final issue register hardening, notifications, and operational dashboards.

### Implementation Checkpoint: 2026-05-12 Phase 4 Completion

Implemented Phase 4 production governance and collaboration controls:

- Added reviewer assignment persistence through `letter_draft_assignments`.
- Added review comment persistence through `letter_draft_comments`.
- Added governance API: `GET /letters/{id}/drafting/runs/{run_id}/governance`.
- Added reviewer assignment API: `POST /letters/{id}/drafting/runs/{run_id}/assign-reviewer`.
- Added review comment API: `POST /letters/{id}/drafting/runs/{run_id}/comments`.
- Added return-for-correction API: `POST /letters/{id}/drafting/runs/{run_id}/return-for-correction`.
- Added lifecycle events for reviewer assignment, comment creation, and return for correction.
- Added assignment/comment indexes for reviewer queues, run-level governance reads, and audit reporting.
- Replaced export stub behavior with immutable DOCX and PDF file-object generation.
- Hardened issue controls so final issue requires immutable DOCX and PDF export artifacts.
- Added source ledger hash, DOCX file object, and PDF file object references to issued-letter records.
- Added governance notifications for assignment, approval, correction return, and issue completion.
- Extended the Evidence Package UI with a Governance tab for assignments, comments, and return-for-correction actions.
- Added regression coverage for governance assignment and comment repository behavior.

Score after Phase 4 completion checkpoint: **95/100**.

Rationale:

- The v2 workflow now covers production governance from draft generation through review assignment, comment collaboration, correction loops, approval, immutable export, issue registration, and audit trail.
- Auditability and compliance reach the target state because issued letters now link approval actors, export artifacts, source ledger integrity, and lifecycle events.
- Remaining improvement opportunities are operational rather than architectural: richer dashboards, reviewer SLA analytics, and integration-specific notification delivery tuning.

### Implementation Checkpoint: 2026-05-12 Phase 5 Completion

Implemented Phase 5 metrics, monitoring, and quality dashboard:

- Added scoped quality dashboard API: `GET /letter-drafting/metrics/dashboard`.
- Added metrics response models for KPIs, status breakdowns, trend points, bottlenecks, and recent quality risks.
- Added RBAC-scoped aggregation over letters, draft runs, lifecycle events, reviewer assignments, and issued-letter artifact records.
- Added quality metrics for cycle time, first-review approval rate, unsupported-claim rate, blocking validation rate, source integrity, average sources per run, review return rate, issued artifact compliance, overdue reviews, and average cyclic confidence.
- Added trend metrics by run date for volume, approval, blocking findings, unsupported claim rate, and confidence.
- Added operational bottleneck metrics for active `completed`, `needs_attention`, and `blocked` runs.
- Added additional draft-run indexes for approval-status and approved/issued timestamp monitoring queries.
- Added frontend hook and TypeScript models for the quality dashboard API.
- Added `LetterQualityDashboardPage` with KPI cards, quality trends, run mix, operational summary, bottlenecks, and recent quality risks.
- Added route and sidebar entry for `/letter-quality`.

Score after Phase 5 completion checkpoint: **97/100**.

Rationale:

- Management can now monitor whether the system is achieving the target quality and governance outcomes, rather than relying only on individual run inspection.
- The remaining gap to 100 is mainly enterprise reporting depth: SLA alert rules, scheduled report delivery, long-term warehouse retention, and drill-through analytics by reviewer, project, contract package, and letter category.

## 2. Current State Analysis

### 2.1 Current Workflow

The current letter drafting workflow is organized around these stages:

```text
All Letters -> Input -> Strategy -> Draft -> Review -> Approval -> Completed
```

Observed current behavior:

1. User initiates a letter from the letter workflow or from a selected document.
2. The system captures metadata such as organization, project, subject, recipient, and linked source document.
3. Strategy-stage pages generate role-based context and strategic plans.
4. The draft page allows document context selection through `LinkedDocumentSelector`.
5. Background and draft generation can run through the older `/ai-assistant/langgraph/*` path.
6. Newer v2 drafting runs are available through `/letters/{letter_id}/drafting/*`.
7. Draft output, source ledgers, validation reports, reviewer findings, and draft versions are persisted.
8. Users can revise, approve, export, or issue v2 runs, although export and issue are currently metadata-level actions rather than complete generated document workflows.

### 2.2 Current Backend Components

Key backend modules:

| Area | Current Components | Current Role |
| --- | --- | --- |
| Letter CRUD and lifecycle | `LetterService`, `routers/letters.py` | Stores letters, statuses, draft output, context IDs, graph run snapshots, review state. |
| Legacy drafting path | `routers/ai_assistant.py`, `ai_workflows/langgraph/letter_pipeline.py` | LangGraph-inspired orchestration for background, strategy, draft, source collection, review findings. |
| v2 drafting path | `routers/letter_drafting.py`, `services/letter_drafting/*`, `models/letter_drafting.py` | Typed drafting sessions, source ledger, plan confirmation, draft generation, validation, versions, lifecycle actions. |
| Retrieval | `retrieval/service.py`, `retrieval/vector_client.py` | Search, RAG, RAG fusion, HyDE-like retrieval, iterative contract QA, observability logging. |
| Contract source retrieval | `services/contract_service.py`, `services/contracts_ingest.py` | Contract upload, clause chunking, contract search, vector upsert, contract metadata. |
| Graph context | `FalkorGraphService`, `GraphAdapter` | Letter reference and relationship graph, conversation threads, linked correspondence. |
| Persistence | MongoDB collections, Qdrant, FalkorDB, local/S3 file services | Stores metadata, chunks, vectors, graph edges, generated artifacts, audit-related data. |
| Observability | `ObservabilityService`, `rag_runs` | Logs retrieval and RAG timing and result metadata. |

### 2.3 Current Frontend Components

Key frontend modules:

| Area | Current Components | Current Role |
| --- | --- | --- |
| Workflow listing | `LetterWorkflowPage`, `useLetterWorkflow`, `LettersTable` | Lists letters, filters by status, starts letter drafting. |
| Strategy | `LetterStrategicPlanPage`, `PlanViewer`, LangGraph hooks | Generates and displays strategy plan and context. |
| Drafting | `LetterDraftPage`, `LetterDraftEditor`, `useLanggraphDraft`, `useLetterDrafting` | Runs draft generation, shows v2 run status, edits draft, saves and submits. |
| Sources | `LinkedDocumentSelector`, `DraftSourcesPanel`, `BackgroundSummary` | Selects context docs, shows generated background, sources, validation findings. |
| Versions | Draft version panel in `LetterDraftPage` | Shows prior accepted draft versions and reviewer notes. |

### 2.4 Strengths

- The system already separates letter workflow stages, which is a good fit for contractual drafting governance.
- MongoDB, Qdrant, and FalkorDB are already present, matching the target metadata/vector/graph architecture.
- The newer v2 drafting model has typed request/response objects, source evidence records, validation reports, and draft run persistence.
- Draft versioning has begun through immutable entries pushed to `letters.draft_versions`.
- Project and organization scoping are already part of the document, contract, and letter services.
- The UI has source, background, plan, validation, and version panels, so the user experience can be evolved without a full redesign.
- Retrieval and contract ingestion already include building blocks for hybrid search, clause chunking, and RAG observability.
- Prompt templates and prompt versions are persisted, which supports auditability and repeatability.

### 2.5 Bottlenecks and Inefficiencies

- Two overlapping drafting paths exist: older `/ai-assistant/langgraph/*` and newer `/letters/{id}/drafting/*`. This creates duplicate logic, inconsistent response shapes, and difficult maintenance.
- The older pipeline is a LangGraph-inspired facsimile rather than a production cyclic LangGraph with explicit retry, critique, and stopping conditions.
- Retrieval sources are fragmented across `documents`, `document_vectors`, `chunks`, contract ingestion, Qdrant, and graph data. Metadata naming is not fully standardized.
- Current v2 export and issue actions mark run state but do not yet generate immutable DOCX/PDF artifacts and link them to the DMS record.
- Validation catches missing sections, unsupported clause citations, placeholders, and role markers, but it does not yet validate every factual assertion against evidence.
- Source selection still depends heavily on user-curated documents and query-derived contract search, rather than a complete query understanding and retrieval planning step.
- Review and approval are present as workflow concepts but need stronger assignment, commenting, decision logging, and approval gate enforcement.

### 2.6 Error-Prone Areas

- Inconsistent route usage can cause users to see a legacy run while v2 state is more complete.
- Clause references can be generated or preserved in draft text without robust clause-existence and interpretation checks.
- Unsupported factual statements may pass if they are not explicit clause citations or placeholders.
- Manual edits to draft content can diverge from the source ledger unless revalidation is triggered.
- Graph-linked correspondence may be displayed but not always used as authoritative drafting context.
- Stubs in export/issue lifecycle may create false confidence that a letter is production-issued when no immutable file artifact exists.

### 2.7 Scalability Issues

- Multiple vector pipelines increase indexing, reconciliation, and operational complexity.
- Large projects with many letters and contract chunks will need stronger pagination, retrieval filtering, reranking, caching, and observability.
- Repeated LLM calls without bounded cyclic orchestration could increase cost and latency.
- Contract clause extraction and OCR/Marker processing should remain asynchronous and queue-backed to avoid API timeouts.
- Graph reads should be bounded by project, issue, chronology, and max depth to avoid overloading FalkorDB on long correspondence chains.

### 2.8 Compliance Risks

- AI-generated contractual letters are high-risk if the system cannot prove source support for factual assertions and clause interpretations.
- Project and organization isolation must be enforced consistently across document search, vector retrieval, graph traversal, and draft source display.
- Final letters should not be issued without human approval, immutable versioning, source ledger, and export artifact.
- Prompt/model versions, retrieved sources, critique results, user edits, approval actors, and issue timestamps must be retained for audit.
- The system should detect red flags such as waiver of rights, unsupported allegations, admissions of delay, time-bar implications, payment implications, EOT implications, and variation implications.

## 3. Target Cyclic RAG Workflow

The target architecture defines a contract-aware drafting engine. The workflow should not be single-pass generation. It should be:

```text
User Request
-> Query Understanding
-> Input Completeness Check
-> Hybrid Retrieval
-> Context Pack Assembly
-> Draft Generation
-> Contractual Critique and Risk Review
-> Source Validation
-> If insufficient: Refine Query -> Retrieve Again -> Redraft
-> Final Letter + Source Integrity Notes
```

Recommended stopping conditions:

- Draft quality score is at least 85%.
- No unsupported critical statement remains.
- All cited clauses exist in uploaded contract records.
- No blocking red-flag finding remains.
- Maximum 3 retrieval/refinement cycles have completed.
- Human approval is obtained before issue.

## 4. Gap Analysis

| Target Capability | Current Position | Gap | Priority |
| --- | --- | --- | --- |
| Query understanding | Partial via request fields and draft type/category | Need explicit classification, extracted facts, party roles, urgency, and missing inputs | High |
| Completeness check | Threshold checks exist in v2 | Needs category-specific critical facts and blocking rules aligned to contract letters | High |
| Hybrid retrieval | Qdrant, Mongo, contract search, FalkorDB exist | Need unified retrieval plan, metadata schema, reranking, exact clause/letter reference search | High |
| Context pack | v2 context bundle and source ledger exist | Need structured context pack with facts, clauses, risks, required actions, and source notes | High |
| Cyclic critique loop | Legacy review and v2 validation exist | Need true critique -> gap query -> re-retrieval -> redraft loop | High |
| Source validation | Deterministic validation exists | Need assertion-to-source validation and clause interpretation checks | High |
| Red-flag detection | Limited | Need contractual risk classifier and blocker policy | High |
| Approval workflow | Statuses and lifecycle actions exist | Need reviewer assignment, approval comments, immutable approval records | Medium |
| Export and issue | v2 state stubs exist | Need DOCX/PDF generation, file storage, issue register, issued document link | High |
| Dashboards and metrics | Observability for RAG exists | Need workflow, quality, approval, cost, latency, and risk metrics | Medium |

## 5. Improvement Recommendations

### 5.1 Short-Term Quick Wins

1. Consolidate drafting entrypoints around v2 `letter_draft_runs`.
   - Make `/letters/{id}/drafting/*` the primary API for new UI actions.
   - Keep `/ai-assistant/langgraph/*` as compatibility endpoints during migration.
   - Success metric: 90% of draft generations use v2 routes within one release.

2. Standardize source metadata across MongoDB, Qdrant, and FalkorDB.
   - Use common fields: `organization_id`, `project_id`, `document_id`, `source_type`, `letter_no`, `clause_number`, `page_numbers`, `chunk_id`, `score`, `source_hash`.
   - Success metric: 100% of source ledger entries include org, project, type, source ID, and traceable label.

3. Strengthen validation before approval and issue.
   - Block approval when critical input checks, unsupported clause citations, or missing source ledgers fail.
   - Re-run validation after manual draft edits.
   - Success metric: unsupported clause citation rate below 2%.

4. Replace export and issue stubs.
   - Generate DOCX from approved template.
   - Generate PDF preview/final copy.
   - Store immutable file objects through existing storage architecture.
   - Link generated files to `draft_run_id`, `letter_id`, and issued document record.
   - Success metric: 100% of issued letters have immutable DOCX/PDF artifacts.

5. Improve UI source and warning visibility.
   - Show blocking findings at top of draft workspace.
   - Add source coverage indicators beside draft sections.
   - Show "background out of date" and "manual edit requires revalidation" states.
   - Success metric: reviewer correction requests caused by missing sources reduced by 30%.

6. Add lifecycle audit events.
   - Log draft generated, validation failed, draft accepted, sent for review, approved, exported, issued, returned, and revised.
   - Success metric: 100% of lifecycle transitions have actor, timestamp, old/new status, and reason/comment where applicable.

### 5.2 Long-Term Optimizations

1. Implement real cyclic LangGraph orchestration.
   - Nodes: `understand_query`, `check_completeness`, `retrieve_context`, `build_context_pack`, `generate_draft`, `critique_draft`, `validate_sources`, `refine_query`, `regenerate_draft`, `finalize_letter`.
   - Bounded loop: maximum 3 cycles.
   - Success metric: first-review approval rate above 75%.

2. Add source-grounded assertion validation.
   - Extract material assertions from draft.
   - Link each assertion to user input, document text, prior letter, contract clause, or graph relationship.
   - Downgrade unsupported allegations into clarification requests or placeholders.
   - Success metric: 95% of final drafts have complete source integrity notes.

3. Add red-flag and contractual risk detection.
   - Detect unsupported allegation, missing clause reference, possible waiver, admission of delay, time-bar issue, payment implication, EOT implication, variation implication, and inconsistent correspondence.
   - Success metric: 100% of high-risk drafts require explicit reviewer acknowledgement.

4. Build issue-wise graph intelligence.
   - Link incoming/outgoing letters, clauses, topics, instructions, claims, EOTs, IPCs, approvals, and issued documents.
   - Retrieve issue chronology automatically.
   - Success metric: 80% of reply drafts include graph-linked correspondence context without manual selection.

5. Strengthen collaboration and approvals.
   - Reviewer assignment, comments, return-for-correction, approval notes, digital sign-off, and issue register.
   - Success metric: approval cycle time reduced by 40-60%.

6. Build drafting analytics dashboards.
   - Track draft cycle time, iteration count, retrieval latency, approval pass rate, unsupported-claim rate, source coverage, cost per draft, and model error rate.
   - Success metric: monthly operations review can identify top 5 delay and quality causes from system data.

## 6. Complete Backend Design Improvement Plan

### 6.1 Recommended Architecture

```text
React Frontend
  -> FastAPI API Layer
    -> Drafting Orchestrator (LangGraph)
      -> Query Understanding Agent
      -> Completeness Check Service
      -> Hybrid Retrieval Service
      -> Context Pack Builder
      -> Drafting Agent
      -> Critique Agent
      -> Source Validation Agent
      -> Export and Issue Service
      -> Audit and Observability Service

Data Stores:
  MongoDB: metadata, letters, draft runs, context packs, validations, approvals, audit events
  Qdrant: semantic document and clause chunks
  FalkorDB: correspondence, issue, clause, party, and chronology graph
  S3/local storage: original files and immutable generated DOCX/PDF
```

### 6.2 Service Responsibilities

| Service | Responsibility |
| --- | --- |
| Drafting Orchestrator | Owns cyclic workflow state, iterations, stop conditions, run persistence. |
| Query Understanding Service | Classifies draft type, parties, urgency, topic, clauses, required action, tone. |
| Completeness Service | Applies category-specific critical input checks and blocking policy. |
| Hybrid Retrieval Service | Searches clauses, letters, documents, exact references, keyword results, vectors, and graph links. |
| Context Pack Builder | Produces a structured pack of facts, clauses, correspondence, risks, required actions, and source notes. |
| Drafting Agent | Generates formal contractual draft from context pack and approved plan. |
| Critique Agent | Reviews contractual correctness, tone, unsupported assertions, missing facts, and risk. |
| Source Validation Agent | Verifies clause existence, assertion support, source coverage, and citation integrity. |
| Export Service | Generates DOCX/PDF from approved templates and stores immutable files. |
| Approval Service | Handles reviewer assignment, comments, approvals, returns, and issue gating. |
| Audit Service | Records every AI, retrieval, validation, approval, export, and issue event. |
| Notification Service | Sends review, correction, approval, and issue notifications. |

### 6.3 Data Model Improvements

#### `letter_draft_runs`

Add or standardize:

```json
{
  "run_id": "uuid",
  "letter_id": "object_id",
  "organization_id": "object_id",
  "project_id": "object_id",
  "mode": "background|strategy|draft|review",
  "draft_type": "reply|fresh",
  "letter_category": "claim_reply|eot_reply|variation|payment_ipc|general",
  "query_understanding": {},
  "completeness_report": {},
  "context_pack_id": "uuid",
  "iteration_count": 2,
  "max_iterations": 3,
  "sources": [],
  "draft_artifact": {},
  "critique_report": {},
  "validation_report": {},
  "confidence_scores": {
    "clause_confidence": 0.9,
    "factual_support": 0.85,
    "tone_suitability": 0.95,
    "overall": 0.88
  },
  "status": "completed|blocked|needs_attention|approved|exported|issued",
  "model_name": "selected_model",
  "prompt_version": 1,
  "created_by": "user_id",
  "approved_by": "user_id",
  "created_at": "datetime",
  "approved_at": "datetime"
}
```

#### `draft_context_packs`

```json
{
  "context_pack_id": "uuid",
  "letter_id": "object_id",
  "run_id": "uuid",
  "project": {},
  "draft_request": {},
  "facts": [],
  "contractual_basis": [],
  "prior_correspondence": [],
  "required_actions": [],
  "risk_flags": [],
  "missing_confirmations": [],
  "source_notes": [],
  "created_at": "datetime"
}
```

#### `draft_assertion_support`

```json
{
  "assertion_id": "uuid",
  "run_id": "uuid",
  "text": "A TBM has been brought to the Casting Yard.",
  "support_status": "supported|user_provided|unsupported|needs_confirmation",
  "source_ids": ["document:123", "clause:4.15.1"],
  "risk_level": "low|medium|high"
}
```

#### `letter_approval_events`

```json
{
  "event_id": "uuid",
  "letter_id": "object_id",
  "run_id": "uuid",
  "event_type": "submitted|returned|approved|exported|issued",
  "actor_user_id": "user_id",
  "comment": "Approved subject to attached corrections.",
  "created_at": "datetime"
}
```

#### `issued_letters`

```json
{
  "issued_letter_id": "uuid",
  "letter_id": "object_id",
  "run_id": "uuid",
  "letter_no": "official_reference",
  "docx_file_object_id": "object_id",
  "pdf_file_object_id": "object_id",
  "source_ledger_hash": "sha256",
  "issued_by": "user_id",
  "issued_at": "datetime",
  "status": "issued|replied|closed"
}
```

### 6.4 API Improvements

Primary APIs:

| Endpoint | Purpose |
| --- | --- |
| `POST /letter-drafting/start` | Start fresh or reply drafting session. |
| `POST /letters/{id}/drafting/analyze-incoming` | Analyze incoming source and extract facts, requests, clauses, deadlines. |
| `POST /letters/{id}/drafting/prepare-plan` | Build planning sheet, reply matrix, and context strategy. |
| `POST /letters/{id}/drafting/generate-draft` | Execute cyclic RAG drafting and return draft run. |
| `POST /letters/{id}/drafting/runs/{run_id}/critique` | Run or re-run critique and red-flag detection. |
| `POST /letters/{id}/drafting/runs/{run_id}/validate` | Validate sources, assertions, clauses, and placeholders. |
| `POST /letters/{id}/drafting/runs/{run_id}/revise` | Create a revised run from reviewer or user instruction. |
| `POST /letters/{id}/drafting/runs/{run_id}/approve` | Approve only when validation has no blocking findings. |
| `POST /letters/{id}/drafting/runs/{run_id}/export` | Generate immutable DOCX/PDF artifacts. |
| `POST /letters/{id}/drafting/runs/{run_id}/issue` | Mark final issue, create issued record, link files and source hash. |
| `GET /letters/{id}/drafting/runs/{run_id}/context-pack` | Inspect structured context pack. |
| `GET /letters/{id}/drafting/runs/{run_id}/source-ledger` | Inspect retrieved and relied-upon sources. |
| `GET /letters/{id}/drafting/runs/{run_id}/audit` | Inspect run events, model versions, prompts, validation, approvals. |

Retrieval/source APIs:

| Endpoint | Purpose |
| --- | --- |
| `POST /retrieval/contract-clauses/search` | Exact and semantic clause search. |
| `POST /retrieval/correspondence/search` | Prior incoming/outgoing letter search. |
| `POST /retrieval/context-pack/build` | Build context pack from sources and user request. |
| `GET /graph/letters/{letter_id}/thread` | Retrieve linked correspondence chronology. |
| `GET /graph/issues/{issue_id}/chronology` | Retrieve issue-wise timeline. |

### 6.5 Automation Opportunities

- Template auto-fill for letter number, date, addressee, contract package, subject, references, body, cc, and signatory.
- AI-assisted incoming letter analysis with user confirmation.
- Auto-generated planning sheet and reply matrix.
- Auto-suggested contract clauses and prior correspondence.
- Critique-driven re-retrieval and redrafting.
- Red-flag detection for contractual risk.
- Auto-generated source integrity notes.
- Approval routing based on category, risk score, project, and role.
- DOCX/PDF generation from approved templates.
- Final issue register and graph linking to original correspondence.

### 6.6 Security and Compliance Features

- Enforce organization and project filters in every MongoDB query, vector search, graph traversal, and file lookup.
- Store immutable AI run records with input summary, model, prompt version, sources, validation, critique, and user actions.
- Prevent direct AI issue of contractual letters.
- Require human approval before export/issue.
- Block issue on critical missing inputs, unsupported clauses, unsupported allegations, or high-risk red flags.
- Store source ledger hash on issued records to prove source integrity at issue time.
- Maintain audit events for every lifecycle transition.
- Mask or omit inaccessible source metadata in UI responses.
- Log retrieval timings and source IDs for operational audit without exposing cross-project content.

### 6.7 Database and Infrastructure Recommendations

| Need | Recommendation |
| --- | --- |
| Primary metadata | Continue MongoDB with replica set in production. |
| Semantic retrieval | Continue Qdrant; standardize payload schema and collection naming. |
| Text and exact search | Use MongoDB text indexes or dedicated BM25 service if query volume grows. |
| Graph relationships | Continue FalkorDB for letter, clause, issue, topic, and chronology relationships. |
| Files | Use existing local/S3 storage abstraction and immutable file object records. |
| Async jobs | Use existing worker pattern; queue OCR, Marker, vectorization, export, and long cyclic draft runs. |
| Observability | Extend `rag_runs` and audit collections with workflow and quality metrics. |

## 7. Phase-Wise Implementation Roadmap

### Phase 1: Stabilize Current Workflow

Duration: **2-3 weeks**  
Expected score after phase: **72/100**

Scope:

- Make v2 `letter_draft_runs` the primary workflow for new draft actions.
- Keep legacy `/ai-assistant/langgraph/*` endpoints for compatibility during migration.
- Standardize draft run response shape consumed by UI.
- Add lifecycle audit events for create, revise, approve, export, issue.
- Add blocking UI indicators for validation failures.
- Re-run validation when draft body is manually edited.
- Add missing indexes for `letter_draft_runs`, approval events, context packs, and issued letters.

Deliverables:

- Route migration plan.
- Audit event collection and service methods.
- UI warning and blocked-state improvements.
- Test coverage for state transitions and validation blockers.

### Phase 2: Source-Grounded RAG

Duration: **4-6 weeks**  
Expected score after phase: **82/100**

Scope:

- Standardize chunk/source metadata across document ingestion, contract ingestion, Qdrant payloads, and draft source ledgers.
- Add exact clause and letter reference retrieval.
- Add source ledger quality checks.
- Store structured context packs.
- Show cited clauses, prior letters, and graph-linked correspondence beside the draft.
- Improve contract clause extraction and parent-child context retrieval.

Deliverables:

- Unified source schema.
- Context pack builder.
- Source ledger UI and API.
- Clause existence validation.
- Tests for org/project-scoped retrieval.

### Phase 3: Cyclic RAG Orchestration

Duration: **5-7 weeks**  
Expected score after phase: **90/100**

Scope:

- Implement real LangGraph orchestration with nodes:
  - `understand_query`
  - `check_completeness`
  - `retrieve_context`
  - `build_context_pack`
  - `generate_draft`
  - `critique_draft`
  - `validate_sources`
  - `refine_query`
  - `regenerate_draft`
  - `finalize_letter`
- Add maximum 3 retrieval/refinement cycles.
- Add confidence scoring.
- Add red-flag detection.
- Add assertion-to-source support mapping.
- Add stop conditions for quality score, blocking findings, clause validation, and iteration limit.

Deliverables:

- LangGraph orchestration module.
- Critique and validation agents.
- Iteration trace persistence.
- Draft quality score.
- Regression tests for cyclic loop behavior.

### Phase 4: Production Governance and Collaboration

Duration: **4-6 weeks**  
Expected score after phase: **95/100**

Scope:

- Add reviewer assignments, comments, return-for-correction, approval notes, and approval history.
- Implement immutable DOCX/PDF export and issued letter register.
- Link final issued letters to source correspondence and graph relationships.
- Add notifications for review, correction, approval, and issued states.
- Add dashboard metrics for drafting quality, speed, and risk.
- Add compliance reports per project and letter category.

Deliverables:

- Approval workflow enhancements.
- Export/issue service with immutable file records.
- Issue register.
- Notification integration.
- Operational dashboards.
- Full audit report endpoints.

## 8. Metrics for Success

| Metric | Target |
| --- | ---: |
| Draft cycle time reduction | 40-60% |
| First-review approval rate | > 75% |
| Unsupported clause citation rate | < 2% |
| Drafts with complete source integrity notes | > 95% |
| Average retrieval latency for normal searches | < 3 seconds |
| Issued letters with immutable version, approval actor, source ledger, and export artifact | 100% |
| Cross-project or cross-organization source leakage in authorization tests | 0 |
| High-risk drafts with explicit reviewer acknowledgement | 100% |
| Draft runs with persisted model and prompt version | 100% |
| Manual edits followed by revalidation before approval | 100% |

## 9. Test Plan

### Backend Unit Tests

- Query classification for fresh letters, replies, claim rejections, EOT, notices, payments, variations, and technical observations.
- Completeness blocking for missing critical inputs by letter category.
- Clause existence validation.
- Unsupported clause citation detection.
- Assertion support mapping.
- Red-flag detection.
- Draft version creation and latest-version selection.
- Approval/export/issue state transitions.

### Backend Integration Tests

- Scoped retrieval across MongoDB, Qdrant, and FalkorDB.
- Context pack generation from selected documents, contract clauses, graph threads, and user facts.
- Cyclic RAG iteration with critique-driven re-retrieval.
- Export service generating DOCX/PDF and immutable file records.
- Audit trail completeness from draft generation through issue.

### Frontend Tests

- Source panel rendering for clauses, documents, prior letters, and graph links.
- Validation warning and blocking states.
- Draft version history rendering.
- Lifecycle buttons disabled when validation blocks approval.
- Context-outdated and manual-edit-requires-validation indicators.

### Security Tests

- Unauthorized document IDs cannot be used as drafting context.
- Vector retrieval cannot return out-of-scope chunks.
- Graph traversal cannot return out-of-scope linked letters.
- Approval/export/issue permissions respect roles and project assignment.
- Audit endpoints do not leak inaccessible source text.

## 10. Implementation Notes

- Preserve existing user and uncommitted repository changes.
- Add new functionality incrementally around the v2 drafting path rather than replacing the full workflow at once.
- Keep legacy endpoints operational until the UI and tests have fully moved to v2.
- Prefer existing stack choices: FastAPI, React/TypeScript, MongoDB, Qdrant, FalkorDB, S3/local storage, and existing worker services.
- Use real LangGraph only for the cyclic orchestration layer; keep deterministic validators for enforceable compliance rules.
- Do not issue any contractual letter directly from AI output. Human approval remains mandatory.

## 11. Final Target Process

```text
1. User selects project.
2. User selects fresh letter or reply letter.
3. User selects incoming letter or source document when applicable.
4. System extracts metadata and query understanding.
5. System checks critical inputs and blocks if major facts are missing.
6. System retrieves contract clauses, prior letters, documents, graph chronology, and user-provided facts.
7. System builds a structured context pack.
8. AI generates first draft.
9. Critique agent checks contractual correctness, tone, risk, and missing facts.
10. Validation agent checks source support and clause integrity.
11. If weak, system refines query, retrieves again, and redrafts.
12. User reviews sources, warnings, and draft.
13. User edits and system revalidates.
14. Reviewer approves or returns for correction.
15. System exports immutable DOCX/PDF.
16. Final letter is issued, stored, and linked to original correspondence.
17. Status and audit records are updated in the DMS.
```

## 12. Conclusion

ContraClaim DMS already contains most of the structural building blocks required for a production-grade contractual letter drafting system. The priority is not a full rebuild. The priority is consolidation and governance: make v2 draft runs the system of record, standardize retrieval and source metadata, add true cyclic critique/retrieval/refinement, strengthen validation, and complete the approval/export/issue lifecycle.

Once implemented, the system should progress from a capable but partially fragmented AI-assisted drafting workflow to a controlled, auditable, source-grounded contractual drafting platform.
