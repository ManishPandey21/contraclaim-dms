# Arbitration Pleadings Drafting Implementation Plan

## 1. Objective

Add an Arbitration Drafting module to Contraclaim DMS for preparing:

- Statement of Claim (SoC)
- Statement of Defence (SoD)
- Rejoinder / Reply to Defence
- Counterclaim, where applicable
- Saved arbitration drafts and draft versions

The feature must reuse existing project, contract, document, clause, correspondence, evidence graph, register, RAG, and AI drafting infrastructure. It must not invent facts, clauses, amounts, dates, or legal positions. Unsupported material must be marked as `[Evidence required]`.

## 2. Current Repo Alignment

### Existing Assets To Reuse

- **RBAC and route gating**
  - Backend: `PolicyService`, `build_scope_query`, canonical permissions in `backend/rbac_backend/core/permissions.py`, default permission seeds, and role permission services.
  - Frontend: `RoleGuard`, `useRBAC`, `client/src/config/rolePermissions.ts`, and sidebar filtering.
- **Letter drafting workflow**
  - Backend: `backend/rbac_backend/routers/letter_drafting.py`, `DraftRunService`, source ledger models, lifecycle audit events, governance comments, validation, and revision flow.
  - Frontend: `LetterDraftPage`, `useLetterDrafting`, existing workflow/editor patterns.
- **Contract intelligence / RAG**
  - Upload, clause extraction, hybrid search, re-ranking, and graph-augmented retrieval through `ContractService` and retrieval engine.
  - Clause library over `document_vectors` via contract appraisal service.
- **Contract appraisal lifecycle**
  - Job/report versioning, approval lock, regeneration as new version, review comments, audit events, and DOCX/PDF export in `AppraisalService`.
- **Evidence graph and domain registers**
  - `ProjectEvent`, `EventLink`, `AIExtraction`, timeline APIs, verified-link downstream access.
  - Drawing references, delay events, programme milestones, IPC bills, claims, variations, bank guarantees, key dates, and contract master.
- **Export stack**
  - `python-docx` and `reportlab` are already available for DOCX/PDF generation.

### Key Design Decision

MongoDB remains authoritative for arbitration drafts, source selections, versions, audit history, and cited evidence. FalkorDB and evidence graph traversal are used as derived evidence discovery/context layers, not as the source of truth.

## 3. Permissions And RBAC

Add dedicated permissions rather than overloading letter drafting permissions:

- `dms.arbitration.view`
- `dms.arbitration.create`
- `dms.arbitration.edit`
- `dms.arbitration.generate`
- `dms.arbitration.export`
- `dms.arbitration.approve`
- `dms.arbitration.audit`
- `dms.arbitration.admin`

Seed these in:

- `backend/rbac_backend/core/permissions.py`
- `backend/rbac_backend/models/permission.py`
- `backend/rbac_backend/initial_data/default_permissions.py`
- `backend/rbac_backend/initial_data/default_roles.py`
- `client/src/pages/PermissionsPage.tsx`
- `client/src/config/rolePermissions.ts`

Route access:

- `/arbitration` and `/arbitration/drafts`: `dms.arbitration.view`
- `/arbitration/claim`: `dms.arbitration.create`
- `/arbitration/defence`: `dms.arbitration.create`
- `/arbitration/rejoinder`: `dms.arbitration.create`
- `/arbitration/counterclaim`: `dms.arbitration.create`

Backend access:

- Read/list: `dms.arbitration.view`
- Create/update/save version: `dms.arbitration.create` or `dms.arbitration.edit`
- AI generation/regeneration: `dms.arbitration.generate`
- Export: `dms.arbitration.export`
- Approval/lock: `dms.arbitration.approve`
- Audit: `dms.arbitration.audit`
- Admin override: `dms.arbitration.admin`

All APIs must scope records by `organization_id` and `project_id` using `build_scope_query`.

## 4. Data Model

Add `backend/rbac_backend/models/arbitration_drafting.py`.

### Collections

#### `arbitration_drafts`

Authoritative current draft shell and workflow state.

Fields:

- `_id`
- `organization_id`
- `project_id`
- `contract_id`
- `draft_type`: `statement_of_claim`, `statement_of_defence`, `rejoinder`, `counterclaim`
- `party_role`: `claimant`, `respondent`
- `dispute_type`: `eot_delay`, `prolongation_cost`, `price_variation`, `variation_change_order`, `payment_dispute`, `termination`, `force_majeure`, `defect_dlp`, `bank_guarantee_retention`, `counterclaim`, `other`
- `title`
- `case_details`
- `tribunal_details`
- `arbitration_clause`
- `governing_law`
- `relief_sought`
- `manual_facts`
- `claim_amount`
- `currency`
- `interest_rate`
- `status`: `draft`, `generating`, `under_review`, `approved`, `exported`, `superseded`, `failed`
- `is_locked`
- `current_version`
- `latest_generation_run_id`
- `created_by`, `created_at`, `updated_by`, `updated_at`
- `approved_by`, `approved_at`
- `exported_by`, `exported_at`

#### `arbitration_draft_versions`

Immutable draft snapshots.

Fields:

- `_id`
- `draft_id`
- `version`
- `status`
- `sections`
- `full_markdown`
- `structured_output`
- `source_ledger`
- `missing_evidence`
- `paragraph_responses`
- `claim_heads`
- `annexures`
- `ai_prompt_version`
- `model`
- `generation_run_id`
- `created_by`, `created_at`

Approved versions are never overwritten. Regeneration creates a new version and may supersede the previous live draft.

#### `arbitration_selected_references`

User-selected and AI-discovered source references.

Fields:

- `_id`
- `draft_id`
- `source_type`: `letter`, `document`, `clause`, `drawing`, `payment_event`, `programme_milestone`, `key_date`, `delay_event`, `claim`, `variation`, `bank_guarantee`, `project_event`, `event_link`, `chronology_event`, `manual_fact`
- `source_id`
- `label`
- `citation`
- `snippet`
- `page_numbers`
- `clause_number`
- `letter_no`
- `event_date`
- `allowed_use`: `fact`, `clause`, `chronology`, `quantum`, `annexure`, `background`
- `selected_by`
- `created_at`

#### `arbitration_claim_heads`

Structured claim/defence heads.

Fields:

- `_id`
- `draft_id`
- `head_type`: `time`, `cost`, `variation`, `payment`, `defect`, `termination`, `setoff`, `interest`, `other`
- `description`
- `amount`
- `currency`
- `calculation_basis`
- `supporting_source_ids`
- `status`: `supported`, `partially_supported`, `evidence_required`

#### `arbitration_paragraph_responses`

SoD/Rejoinder paragraph response matrix.

Fields:

- `_id`
- `draft_id`
- `source_pleading_document_id`
- `source_pleading_type`: `statement_of_claim`, `statement_of_defence`, `counterclaim`
- `source_paragraph_number`
- `source_paragraph_text`
- `response_type`: `admit`, `deny`, `require_proof`, `part_admit_part_deny`, `not_admitted`, `misconceived`, `incorrect`, `misleading`
- `response_text`
- `response_reason`
- `supporting_source_ids`
- `missing_evidence`

#### `arbitration_generation_runs`

AI generation trace and job status.

Fields:

- `_id`
- `draft_id`
- `run_type`: `full_draft`, `section_regeneration`, `paragraph_response`, `evidence_refresh`
- `section_key`
- `status`: `queued`, `running`, `completed`, `failed`, `cancelled`
- `input_hash`
- `retrieval_queries`
- `source_ids`
- `prompt_version`
- `model`
- `raw_output`
- `parsed_output`
- `warnings`
- `error_message`
- `created_by`, `created_at`, `started_at`, `completed_at`

#### `arbitration_audit_events`

Use `AuditEventService` where possible. A dedicated collection is optional only if the existing audit event model cannot query draft lifecycle events efficiently.

## 5. Backend API

Add `backend/rbac_backend/routers/arbitration_drafting.py`.

### Draft CRUD

- `GET /api/arbitration/drafts`
  - Filters: `organization_id`, `project_id`, `contract_id`, `draft_type`, `party_role`, `dispute_type`, `status`, `q`, `skip`, `limit`
- `POST /api/arbitration/drafts`
  - Creates draft shell and selected references.
- `GET /api/arbitration/drafts/{draft_id}`
- `PATCH /api/arbitration/drafts/{draft_id}`
  - Updates case details, manual facts, claim heads, selected references, editable content.
- `DELETE /api/arbitration/drafts/{draft_id}`
  - Soft-delete unless no generated versions exist.

### Evidence And Context

- `POST /api/arbitration/drafts/{draft_id}/evidence/search`
  - Search selected project sources using contract search, retrieval engine, evidence graph downstream links, clauses, documents, letters, and registers.
- `POST /api/arbitration/drafts/{draft_id}/evidence/refresh`
  - Rebuild source ledger from current selections and verified graph links.
- `GET /api/arbitration/drafts/{draft_id}/source-ledger`
  - Returns normalized source ledger with source hashes.

### AI Generation

- `POST /api/arbitration/drafts/{draft_id}/generate`
  - Generates full SoC/SoD/Rejoinder/Counterclaim.
- `POST /api/arbitration/drafts/{draft_id}/sections/{section_key}/regenerate`
  - Regenerates one section only.
- `POST /api/arbitration/drafts/{draft_id}/paragraph-responses/import-soc`
  - Imports SoC document text or selected document, extracts paragraphs, and creates SoD response matrix.
- `POST /api/arbitration/drafts/{draft_id}/paragraph-responses/import-defence`
  - Imports Statement of Defence text or selected document, extracts paragraphs, preliminary objections, quantum objections, legal defences, and counterclaim sections for rejoinder drafting.
- `POST /api/arbitration/drafts/{draft_id}/paragraph-responses/generate`
  - Generates admit/deny/require-proof/misconceived responses with citations.
- `GET /api/arbitration/drafts/{draft_id}/runs/{run_id}`

### Versions, Review, Audit

- `POST /api/arbitration/drafts/{draft_id}/versions`
  - Saves an immutable manual version.
- `GET /api/arbitration/drafts/{draft_id}/versions`
- `GET /api/arbitration/drafts/{draft_id}/versions/{version}`
- `POST /api/arbitration/drafts/{draft_id}/approve`
  - Locks current version.
- `POST /api/arbitration/drafts/{draft_id}/return-for-revision`
- `GET /api/arbitration/drafts/{draft_id}/audit`

### Export

- `GET /api/arbitration/drafts/{draft_id}/export/docx`
- `GET /api/arbitration/drafts/{draft_id}/export/pdf`

Exports must include headings, paragraph numbering, claim/quantum table, prayer for relief, citations, annexure list, and missing evidence notes where unresolved.

## 6. Backend Services

Add `backend/rbac_backend/services/arbitration_drafting/`.

### `repository.py`

Mongo persistence for drafts, versions, source references, claim heads, paragraph responses, generation runs, and audit lookups.

### `context.py`

Build an arbitration context pack by combining:

- Draft case details and manual facts.
- Selected letters/documents/references.
- Clause chunks from `document_vectors`.
- Contract search/RAG results from `ContractService`.
- Retrieval engine results for broader document evidence.
- Verified/approved evidence graph downstream links from `EvidenceGraphService.downstream_links`.
- Attached verified Chronology Builder packages/events from `docs/architecture/chronology_builder_integration_plan.md`.
- Timeline/project events for fallback chronology where no matter chronology package is attached.
- Claims, variations, key dates, IPC bills, BGs, drawings, delay events, programme milestones, and contract master.

Output a normalized source ledger compatible with the existing letter drafting `SourceEvidence` shape or an arbitration-specific extension.

### `generator.py`

Generate structured pleading sections.

Prompt requirements:

- Use only source ledger facts, selected manual facts, and cited contract clauses.
- Cite every factual/date/amount/clause assertion using source labels.
- Use `[Evidence required]` when support is missing.
- Separate facts, legal submissions, quantum, assumptions, and missing evidence.
- For SoD, respond paragraph-by-paragraph using `admit`, `deny`, `require_proof`, or `part_admit_part_deny`.
- For Rejoinder, read/import both the SoC and SoD, generate paragraph-wise replies to the SoD, and avoid repeating the whole SoC unless needed to rebut a specific defence.
- For Rejoinder, do not introduce a new claim unless the output clearly marks it for legal review.
- For Rejoinder, keep the tone concise, responsive, and focused on rebutting the Respondent's defence.
- Do not provide jurisdiction-specific legal advice beyond the supplied contract/governing law unless marked as an argument requiring lawyer review.

### `validator.py`

Deterministic validation before saving generated output:

- No clause citation unless present in clause library/source ledger.
- No amount/date unless present in selected evidence or manual facts.
- Every paragraph with a factual assertion has at least one source or `[Evidence required]`.
- SoD paragraph responses map to imported SoC paragraph numbers.
- Rejoinder paragraph replies map to imported SoD paragraph numbers.
- Rejoinder output does not add new claim heads unless marked as requiring legal review.
- Approved/locked drafts cannot be edited or overwritten.

### `exporter.py`

Build DOCX/PDF using the existing `python-docx` and `reportlab` style from contract appraisal, extended for:

- Caption/cover page
- Paragraph numbering
- Annexure table
- Claim/quantum table
- Prayer for relief
- Citation-preserving legal format

### `service.py`

Coordinates RBAC assumptions, state transitions, context building, generation runs, version creation, approval, audit events, and exports.

## 7. Pleading Structures

### Statement of Claim

Required sections:

1. Caption / Cover Page
2. Introduction and Executive Summary
3. Parties
4. Jurisdiction and Arbitration Agreement
5. Factual Background
6. Legal Claims / Causes of Action
7. Damages, Causation, Mitigation and Quantum
8. Prayer for Relief
9. List of Relied-Upon Documents / Annexures

### Statement of Defence

Required sections:

1. Caption / Cover Page
2. Introduction and Overview
3. Preliminary Objections, if applicable
4. Paragraph-by-Paragraph Response to SoC
5. Respondent's Factual Background
6. Legal Defences on Merits
7. Quantum Challenge
8. Counterclaim, if applicable
9. Prayer for Relief
10. List of Relied-Upon Documents / Annexures

### Rejoinder / Reply to Defence

Required sections:

1. Caption / Cover Page
   - Same arbitration case details, tribunal/institution, parties, and case reference used in the SoC and SoD.
2. Introduction and Scope of Rejoinder
   - State that the rejoinder is filed in response to the Statement of Defence.
   - Clarify that the Claimant denies the Respondent's defences except where expressly admitted.
   - Confirm that the Claimant maintains the claims, reliefs, and legal position stated in the Statement of Claim.
3. Response to Preliminary Objections
   - Reply to jurisdictional objections.
   - Reply to admissibility objections.
   - Reply to limitation/time-bar objections.
   - Reply to objections based on notice, pre-arbitration procedure, maintainability, or waiver.
4. Paragraph-by-Paragraph Reply to the Statement of Defence
   - For each paragraph of the SoD, generate a response using: `Admitted`, `Denied`, `Not admitted / Respondent put to strict proof`, or `Misconceived / incorrect / misleading`.
   - Where denial is made, provide brief reasons and supporting document references.
5. Claimant's Clarified Factual Position
   - Reaffirm the Claimant's version of facts.
   - Correct misleading factual assertions made in the SoD.
   - Link facts to chronology, letters, notices, meeting records, drawings, programme milestones, payment records, and contract clauses.
6. Reply to Legal Defences
   - Respond to defences such as no breach, force majeure, hardship, Claimant's alleged prior breach, waiver, estoppel, acquiescence, set-off, failure to mitigate, contractual bar, and notice bar.
   - Map each reply to contract clauses, correspondence, and applicable legal position.
7. Reply to Quantum Objections
   - Defend the claim amount and calculation methodology.
   - Reply to objections on causation, remoteness, mitigation, duplication, speculative claim, incorrect rates, or unsupported cost.
   - Refer to claim tables, IPC/payment records, variation records, expert reports, cost records, and supporting annexures.
   - Mark missing items as `[Evidence required]`.
8. Reply to Counterclaim, if any
   - If the SoD includes a counterclaim, generate a structured defence to counterclaim covering preliminary objections, factual response, legal response, quantum response, and prayer for dismissal of counterclaim.
9. Reaffirmation of Reliefs
   - Reaffirm the prayers made in the Statement of Claim.
   - Add consequential relief arising from the Respondent's defence only where legally permissible and clearly supported.
10. Updated List of Documents / Annexures
   - Include additional documents relied upon in the rejoinder.
   - Maintain clean annexure numbering and cross-references.

Rejoinder drafting logic:

- The AI must read/import both the Statement of Claim and Statement of Defence.
- The AI must generate paragraph-wise replies to the SoD.
- The AI must not introduce a new claim unless clearly marked for legal review.
- The AI must not invent facts, clauses, amounts, dates, or legal admissions.
- Missing documents or proof must be marked as `[Evidence required]`.
- The draft must remain concise, responsive, and focused on rebutting the SoD rather than repeating the entire SoC.
- Section-wise regeneration must be available for preliminary objections, paragraph-wise replies, quantum reply, and reply to counterclaim.

### Counterclaim

Use SoC structure but mark the party position as counterclaimant and include set-off/recoupment if selected.

## 8. Frontend Plan

### Routes

Add pages:

- `/arbitration`
- `/arbitration/claim`
- `/arbitration/defence`
- `/arbitration/rejoinder`
- `/arbitration/counterclaim`
- `/arbitration/drafts`
- `/arbitration/drafts/:draftId`

Add sidebar section:

- Arbitration Drafting
  - Draft Statement of Claim
  - Draft Statement of Defence
  - Draft Rejoinder / Reply to Defence
  - Draft Counterclaim
  - Saved Arbitration Drafts

Use a `Landmark`, `Scale`, or `FileText` lucide icon consistently with current sidebar styling.

### Wizard

Implement `ArbitrationDraftWizardPage`:

1. Case details
2. Party role and pleading type
3. Nature of claim/defence
4. Select documents and references
5. Claim heads / defence grounds
6. Generate draft
7. Edit, save, export

### Editor View

Implement:

- Rich text or structured markdown editor.
- Section navigation and section-level regenerate buttons.
- Side panel for selected clauses, linked letters, documents, event links, annexures, claim heads, paragraph responses, and missing evidence alerts.
- Status and version controls.
- Export DOCX/PDF buttons.

Prefer existing UI primitives from `components/ui`, existing page layout style, and the letter drafting editor pattern. Avoid introducing a new editor dependency unless the current stack lacks basic editing support.

### Frontend Service

Add `client/src/services/arbitration-drafting-api.ts` with typed functions for all APIs.

## 9. Phase-Wise Delivery

### Phase 0: Baseline And Permission Alignment

Deliverables:

- Add arbitration permissions to backend canonical permissions, default seeds, default roles, permissions UI, and frontend route permission map.
- Add route inventory entries.
- Add sidebar placeholders and routes behind RBAC.
- Add API skeleton/router registration.

Validation:

- Permission catalog drift tests pass.
- Route inventory test passes.
- Org Admin can be granted arbitration permissions and see routes.

### Phase 1: Backend Persistence And CRUD

Deliverables:

- Models, repository, service, router.
- Draft CRUD, source-reference CRUD, claim-head CRUD, version save/list/get.
- Audit events for create/update/version/approve/export.
- Tenant isolation and locked-draft guards.

Validation:

- API tests for create/list/get/update/version.
- Cross-tenant denial tests.
- Approved draft edit denial test.

### Phase 2: Evidence Context And Source Ledger

Deliverables:

- Arbitration context builder.
- Evidence search endpoint.
- Source ledger builder using selected references, contract clauses, retrieval engine, verified graph links, and domain registers.
- Missing evidence detector.

Validation:

- Unit tests for source ledger normalization and source hashing.
- Integration test that selected clauses/letters/register rows appear as cited sources.
- Test that unverified AI graph links are excluded by default unless review mode requests them.

### Phase 3: AI Generation

Deliverables:

- Prompt templates and generator.
- SoC, SoD, rejoinder, and counterclaim generation.
- SoC import and paragraph response matrix for SoD.
- SoC and SoD import for Rejoinder, including preliminary objection, legal defence, quantum objection, and counterclaim extraction.
- Section-level regeneration, including Rejoinder preliminary objections, paragraph-wise replies, quantum reply, and reply to counterclaim.
- Deterministic validator for citations, dates, amounts, and missing evidence.

Validation:

- Generated facts without evidence are replaced or flagged as `[Evidence required]`.
- SoD paragraph responses preserve paragraph numbers.
- Rejoinder paragraph replies preserve SoD paragraph numbers and never silently add new claims.
- Section regeneration creates a new version/run without overwriting approved content.

### Phase 4: Frontend Wizard And Saved Drafts

Deliverables:

- Sidebar module and routes.
- Wizard pages.
- Source selection UI across letters, documents, clauses, graph links, registers, and manual facts.
- Saved drafts list and draft detail page.
- Side panel for citations, annexures, and missing evidence.

Validation:

- Frontend tests for route access, wizard navigation, draft save, evidence selection, and saved drafts.
- Manual integration validation against local backend.

### Phase 5: Export, Review, Approval

Deliverables:

- DOCX/PDF exports.
- Approval lock and return-for-revision.
- Review comments.
- Export audit events.
- Legal formatting polish: cover page, numbering, annexures, quantum table, prayer.

Validation:

- Export tests confirm non-empty DOCX/PDF and required headings.
- Approved draft cannot be edited.
- Export preserves citations and annexure list.

### Phase 6: Hardening

Deliverables:

- Idempotent generation input hashes.
- Better prompt observability.
- Dashboard metrics for drafts by status, missing evidence, and export count.
- Backfill optional saved arbitration references from existing claims/disputes.
- Production deployment notes and DB index creation script.

Validation:

- Full backend test suite slice.
- Frontend test slice.
- RBAC matrix validation.
- Manual end-to-end flow: create SoC, generate, save version, import SoC, generate SoD, export DOCX/PDF.

## 10. Database Indexes

Create indexes:

- `arbitration_drafts`: `(organization_id, project_id, contract_id, draft_type, status)`
- `arbitration_drafts`: `(organization_id, project_id, created_at)`
- `arbitration_draft_versions`: `(draft_id, version)` unique
- `arbitration_selected_references`: `(draft_id, source_type, source_id)`
- `arbitration_claim_heads`: `(draft_id, head_type)`
- `arbitration_paragraph_responses`: `(draft_id, source_paragraph_number)`
- `arbitration_generation_runs`: `(draft_id, created_at)`

Migration note: this repo currently uses Mongo collections without a full migration framework. Add index creation either to the database startup/index utility or a repeatable admin script with idempotent `create_index` calls.

## 11. Test Plan

Backend:

- Model validation for draft types, statuses, selected references, paragraph responses, and claim heads.
- CRUD scoped list/get/update tests.
- RBAC allow/deny tests.
- Cross-tenant denial tests.
- Versioning and approved-lock tests.
- Source ledger tests for clauses, documents, letters, verified graph links, and registers.
- AI generation validator tests with mocked LLM output.
- SoC paragraph import and SoD response tests.
- Rejoinder tests for SoC+SoD import, SoD paragraph-wise replies, preliminary-objection replies, quantum-objection replies, and counterclaim defence.
- DOCX/PDF export smoke tests.

Frontend:

- Sidebar and route permission tests.
- Wizard navigation tests.
- Evidence selection tests.
- Generate/save/version/export interaction tests with mocked API.
- Empty/loading/error states.
- Missing evidence alert rendering.

Manual validation:

- Generate SoC from selected claim, clauses, letters, payment events, delay event, and key dates.
- Import generated SoC and generate SoD paragraph responses.
- Import SoC and SoD, generate a Rejoinder, verify paragraph-wise replies to the SoD, and confirm `[Evidence required]` appears where proof is missing.
- Regenerate only quantum section.
- Approve and verify edit lock.
- Export DOCX/PDF and inspect citations/annexures.

## 12. API Documentation Notes

Each endpoint must document:

- Required permission.
- Scope behavior.
- Request/response schema.
- Locked draft behavior.
- Versioning behavior.
- Whether AI generation is synchronous or job-based.
- Export content type and filename.

## 13. Implementation Order

Implementation should start with Phase 0 and stop after each phase for validation. The safest first executable slice is:

1. Add permissions and route inventory.
2. Add backend models/repository/router with CRUD and no AI generation.
3. Add sidebar and saved drafts shell.
4. Add source ledger/context builder.
5. Add generation with mocked tests before live LLM use.
6. Add exports and review/approval workflow.
