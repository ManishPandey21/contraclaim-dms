# Chronology Builder Integration Plan

## 1. Objective

Add a Chronology Builder module to Contraclaim DMS that builds verified matter chronologies from uploaded project documents and linked registers, then feeds those chronologies into Statement of Claim, Statement of Defence, Rejoinder / Reply to Defence, and counterclaim drafting.

This is not a replacement for the existing Contract Timeline. The existing `/contracts/timeline` page is a project evidence graph timeline backed by `ProjectEvent` and `EventLink`. The Chronology Builder should sit above it as a matter-level, pleading-aware chronology and evidence-mapping workspace.

## 2. Current Repo Fit

The repo already has the main foundation needed for chronology integration:

- Uploaded documents, contract text, clause extraction, vector chunks, hybrid search, and Q&A/appraisal retrieval paths.
- Evidence graph models and APIs: `ProjectEvent`, `EventLink`, `AIExtraction`, `/api/project-events`, `/api/event-links`, and `/api/contracts/timeline`.
- Domain registers for drawings, delay events, programme milestones, IPC/payment data, key dates, claims, variations, and bank guarantees.
- Arbitration drafting models, source ledger, draft versions, paragraph responses, exports, and RBAC under `dms.arbitration.*`.
- Frontend route/RBAC patterns through `RoleGuard`, `rolePermissions.ts`, and sidebar filtering.

The missing piece is a curated chronology package that can be created per project/contract/dispute, verified by users, linked to source evidence, and injected into arbitration pleadings as structured factual context.

## 3. Design Decision

MongoDB remains authoritative for chronology records, verification status, edits, audit history, and export metadata.

FalkorDB/evidence graph remains a derived traversal and relationship layer. Verified chronology events should sync into the evidence graph as `ProjectEvent` nodes and `EventLink` relationships, but chronology verification and audit history must remain in MongoDB.

Chronology generation must be source-grounded. AI can suggest events, but final chronology events used for drafting should be `verified` or `edited_verified` unless the user explicitly enables unverified events in review mode.

## 4. Web App Placement

Add a sidebar section named `Chronology Builder`, positioned near Contract Timeline and Arbitration Drafting.

Recommended frontend routes:

- `/chronology` - list saved chronologies.
- `/chronology/new` - create chronology package wizard.
- `/chronology/:chronologyId` - chronology workspace.
- `/chronology/:chronologyId/review` - AI suggestion review and verification queue.
- `/chronology/:chronologyId/presentation` - tribunal/hearing presentation view.

Recommended route permissions:

- `dms.chronology.view`
- `dms.chronology.create`
- `dms.chronology.edit`
- `dms.chronology.verify`
- `dms.chronology.export`
- `dms.chronology.admin`

Permissions should be seeded in the same places as arbitration permissions:

- `backend/rbac_backend/core/permissions.py`
- `backend/rbac_backend/models/permission.py`
- `backend/rbac_backend/initial_data/default_permissions.py`
- `backend/rbac_backend/initial_data/default_roles.py`
- `client/src/pages/PermissionsPage.tsx`
- `client/src/config/rolePermissions.ts`

## 5. User Workflow

1. User selects organization, project, contract, dispute/claim package, party perspective, and chronology type.
2. User selects source material from all uploaded documents and existing registers.
3. System extracts draft dated events with source citations, page/paragraph references, clause references, responsible party, impact, confidence, and pleading relevance.
4. User reviews AI suggestions and marks each event as verified, edited, rejected, duplicate, or needs review.
5. User filters and groups the chronology by issue, claim head, party, event type, clause, document type, confidence, and verification status.
6. Verified chronology events become available in arbitration drafting as first-class source ledger entries.
7. User exports the chronology as Word, Excel, PDF, evidence index, or pleading-ready chronology section.

## 6. Data Model

Add `backend/rbac_backend/models/chronology.py`.

### `matter_chronologies`

Stores each chronology package.

Fields:

- `_id`
- `organization_id`
- `project_id`
- `contract_id`
- `matter_id`
- `claim_id`
- `title`
- `chronology_type`: `general_dispute`, `eot_delay`, `variation`, `payment`, `termination`, `force_majeure`, `defect_dlp`, `bank_guarantee_retention`, `counterclaim`, `other`
- `party_perspective`: `claimant`, `respondent`, `neutral`
- `status`: `draft`, `extracting`, `review`, `verified`, `exported`, `archived`, `failed`
- `selected_source_ids`
- `selected_source_types`
- `settings`
- `summary_counts`
- `created_by`, `created_at`, `updated_by`, `updated_at`

### `matter_chronology_events`

Stores chronology entries.

Fields:

- `_id`
- `chronology_id`
- `organization_id`
- `project_id`
- `contract_id`
- `matter_id`
- `claim_id`
- `event_date`
- `event_end_date`
- `date_text`
- `date_type`: `exact`, `approximate`, `inferred`, `range`, `undated`
- `title`
- `description`
- `source_document_id`
- `source_document_name`
- `source_page`
- `source_paragraph`
- `source_spans`
- `letter_no`
- `from_party`
- `to_party`
- `contract_clauses`
- `issue_tags`
- `claim_heads`
- `responsible_party`
- `supports_party`: `claimant`, `respondent`, `neutral`, `both`, `unknown`
- `event_classification`: `admitted_fact`, `disputed_fact`, `notice`, `breach`, `mitigation`, `delay`, `payment`, `variation`, `contractual_trigger`, `evidence_only`, `other`
- `impact_type`: `time`, `cost`, `scope`, `quality`, `compliance`, `legal`, `none`, `unknown`
- `impact_days`
- `impact_amount`
- `confidence_score`
- `verification_status`: `ai_suggested`, `verified`, `edited_verified`, `rejected`, `duplicate`, `needs_review`
- `manual_notes`
- `legal_relevance`
- `pleading_use`: `soc_background`, `soc_breach`, `soc_quantum`, `sod_defence`, `sod_objection`, `rejoinder_reply`, `counterclaim`, `annexure`, `none`
- `related_event_ids`
- `related_document_ids`
- `project_event_id`
- `event_link_ids`
- `ai_extraction_id`
- `annexure_no`
- `duplicate_of_event_id`
- `created_by`, `created_at`, `updated_by`, `updated_at`

### `matter_chronology_event_revisions`

Append-only history for each chronology event.

Fields:

- `_id`
- `chronology_id`
- `event_id`
- `revision`
- `action`: `created`, `edited`, `verified`, `rejected`, `marked_duplicate`, `restored`, `linked`, `exported`
- `before`
- `after`
- `note`
- `created_by`
- `created_at`

### `matter_chronology_exports`

Stores export jobs and generated files.

Fields:

- `_id`
- `chronology_id`
- `export_type`: `docx`, `xlsx`, `pdf`, `soc_section`, `sod_section`, `rejoinder_section`, `evidence_index`, `hearing_bundle`
- `status`
- `filters`
- `file_id`
- `created_by`
- `created_at`

## 7. Backend APIs

Add `backend/rbac_backend/routers/chronology.py`.

Chronology CRUD:

- `GET /api/chronologies`
- `POST /api/chronologies`
- `GET /api/chronologies/{chronology_id}`
- `PATCH /api/chronologies/{chronology_id}`
- `DELETE /api/chronologies/{chronology_id}`

Extraction and review:

- `POST /api/chronologies/{chronology_id}/extract`
- `GET /api/chronologies/{chronology_id}/events`
- `POST /api/chronologies/{chronology_id}/events`
- `PATCH /api/chronologies/{chronology_id}/events/{event_id}`
- `POST /api/chronologies/{chronology_id}/events/{event_id}/verify`
- `POST /api/chronologies/{chronology_id}/events/{event_id}/reject`
- `POST /api/chronologies/{chronology_id}/events/{event_id}/mark-duplicate`
- `POST /api/chronologies/{chronology_id}/events/{event_id}/link`
- `GET /api/chronologies/{chronology_id}/events/{event_id}/revisions`

Arbitration integration:

- `GET /api/chronologies/{chronology_id}/pleading-context`
- `POST /api/arbitration/drafts/{draft_id}/attach-chronology`
- `GET /api/arbitration/drafts/{draft_id}/chronology-context`

Exports:

- `GET /api/chronologies/{chronology_id}/export/docx`
- `GET /api/chronologies/{chronology_id}/export/xlsx`
- `GET /api/chronologies/{chronology_id}/export/pdf`
- `GET /api/chronologies/{chronology_id}/export/evidence-index`

All APIs must scope by `organization_id` and `project_id` through the existing RBAC/scope helpers.

## 8. Extraction Pipeline

Create `backend/rbac_backend/services/chronology/`.

Recommended services:

- `ChronologyService` - CRUD, filtering, revisions, verification lifecycle.
- `ChronologyExtractionService` - document/register source extraction.
- `ChronologySourceResolver` - normalizes selected documents, clauses, letters, registers, and existing project events.
- `ChronologyDeduplicationService` - merges duplicate or near-duplicate events.
- `ChronologyEvidenceGraphSyncService` - syncs verified chronology events to evidence graph.
- `ChronologyExportService` - Word, Excel, PDF, and evidence index exports.
- `ChronologyPleadingContextService` - builds SoC/SoD/Rejoinder chronology context.

Extraction should run in three layers:

1. Rule extraction:
   - Dates, date ranges, letter numbers, reference numbers, clause numbers, page/paragraph references, sender/recipient, document date, subject, and basic document type.
2. AI event extraction:
   - Event title, factual description, why it matters, issue category, responsible party, support direction, impact, claim head, legal relevance, and confidence.
3. RAG/source verification:
   - Attach exact source chunk, page, paragraph, clause, and document reference. If source support is missing, mark the event `needs_review` and include `[Evidence required]` in downstream drafting.

Use existing `AIExtraction` with `schema_version = "chronology.v1"` where possible. Add a `chronology_id` field to parsed output or metadata rather than creating a separate extraction collection unless querying becomes difficult.

Extraction must be idempotent by:

- `chronology_id`
- `source_document_id`
- `content_hash`
- `schema_version`

## 9. Evidence Graph Integration

Verified chronology events should create or update derived evidence graph records:

- Create a `ProjectEvent` for each verified chronology event.
- Create `EventLink` relationships to source documents, clauses, letters, drawings, payment events, key dates, delay events, programme milestones, claims, variations, and bank guarantees.
- Preserve the chronology event as the authoritative source by storing `chronology_id` and `chronology_event_id` in `ProjectEvent.metadata`.
- Keep `EventLink` append-only; verification changes must create revisions, not overwrite prior AI-suggested links.

This lets the existing `/contracts/timeline` page show verified chronology-derived events while the Chronology Builder keeps the pleading-specific review, grouping, and audit trail.

## 10. Frontend UX

Add `client/src/pages/ChronologyBuilderPage.tsx` and `client/src/services/chronology-api.ts`.

Core UI:

- Chronology list with project, contract, type, party perspective, status, event counts, and last updated date.
- Create chronology wizard:
  - Case details
  - Party perspective
  - Chronology type
  - Source document/register selection
  - Extraction settings
  - Review queue
- Review workspace:
  - Table view and timeline view
  - Event detail side panel
  - Source excerpt preview
  - Clause/document/register links
  - Verify, edit, reject, duplicate, and link actions
- Filters:
  - Date range
  - Issue category
  - Claim head
  - Responsible party
  - Supports claimant/respondent/neutral
  - Event classification
  - Source type
  - Contract clause
  - Key date/milestone
  - Confidence
  - Verification status
- Arbitration side panel:
  - Shows whether a chronology is attached to a draft
  - Shows which events will feed SoC, SoD, Rejoinder, or counterclaim sections
  - Flags missing evidence

## 11. Arbitration Drafting Integration

Extend `ArbitrationContextBuilder` so attached chronologies become first-class source ledger rows.

Recommended source ledger row shape:

- `source_type`: `chronology_event`
- `source_id`: chronology event id
- `allowed_use`: `chronology`, `fact`, `quantum`, `annexure`, or `background`
- `label`: event title
- `citation`: event date plus source document/page/paragraph
- `snippet`: verified event description and source excerpt
- `page_numbers`
- `clause_number`
- `letter_no`
- `source_hash`
- `metadata`: chronology id, event classification, claim head, supports party, confidence, verification status

Only `verified` and `edited_verified` chronology events should be included by default. Add an explicit review-mode option to include `ai_suggested` or `needs_review` events, visibly marked as unverified.

### Statement of Claim

Use verified claimant-supporting and neutral chronology events to generate:

- Factual background
- Sequence of events
- Notice chronology
- Breach chronology
- Delay, variation, payment, termination, or force majeure chronology
- Claim-wise factual narrative
- Evidence table and annexure index
- Missing evidence alerts

### Statement of Defence

Use verified respondent-supporting and neutral chronology events to generate:

- Respondent counter-chronology
- Claimant delay/default chronology
- Notice non-compliance chronology
- Mitigation/failure chronology
- Contradictions between pleaded facts and contemporaneous records
- Absence-of-evidence points where a claim is unsupported

### Rejoinder / Reply to Defence

Use verified chronology events with imported SoC and SoD paragraph matrices to generate:

- Response to preliminary objections
- Paragraph-wise replies to SoD assertions
- Claimant's clarified factual position
- Reply to legal defences
- Reply to quantum objections
- Reply to counterclaim, if any
- Reaffirmation of reliefs
- Updated annexure list

For rejoinder, the context service should compute:

- SoC pleaded event version
- SoD disputed/alternative event version
- Document-supported chronology event
- Whether the fact is admitted, disputed, unsupported, or contradicted
- Missing source references requiring `[Evidence required]`

The drafting generator must not introduce a new claim through chronology data unless the output clearly marks it for legal review.

## 12. Export Integration

Chronology exports should include:

- Chronology table in Word.
- Chronology table in Excel.
- PDF chronology summary.
- Issue-wise chronology.
- Claim-head-wise chronology.
- Evidence index.
- Annexure list.
- Pleading-ready SoC chronology section.
- Pleading-ready SoD counter-chronology section.
- Pleading-ready Rejoinder comparison table.
- Hearing/tribunal presentation chronology.

Exports must preserve source citations, page/paragraph references, clause references, verification status, and annexure numbers.

## 13. Phase-Wise Implementation

### Phase 0: Alignment

- Add this plan to architecture docs.
- Add route inventory entries for chronology routes.
- Add `dms.chronology.*` permission catalog entries and default seeds.
- Define Pydantic request/response contracts.
- Confirm whether chronology will attach to arbitration drafts by `chronology_id` or through selected references. Recommended: both, with `chronology_id` as the package-level link and selected event references for fine control.

### Phase 1: Backend Foundation

- Add chronology models, indexes, services, and router.
- Implement CRUD, scoped list/filter, event create/update, verification, rejection, duplicate marking, and revision history.
- Add audit events for creation, extraction, edits, verification, rejection, export, and arbitration attachment.
- Add tenant isolation tests and RBAC tests.

### Phase 2: Extraction From Uploaded Documents

- Build source resolver for uploaded documents, document vectors, contract clauses, letters, registers, and existing project events.
- Add rule-based extraction for dates, references, clauses, parties, and page/paragraph metadata.
- Add AI chronology extraction using `AIExtraction` schema `chronology.v1`.
- Add idempotency by chronology/source/content hash/schema version.
- Add duplicate detection and needs-review classification when source support is weak.

### Phase 3: Chronology UI

- Add chronology sidebar item, routes, API service, and pages.
- Build wizard, review queue, event table, timeline view, source preview, filters, and summary counters.
- Add verify/edit/reject/duplicate/link actions.
- Add empty, loading, error, and permission-denied states.

### Phase 4: Evidence Graph Sync

- Sync verified chronology events to `ProjectEvent`.
- Append `EventLink` revisions for source documents, clauses, registers, claims, and related chronology events.
- Add reconciliation to detect chronology events not represented in graph or graph links pointing to rejected chronology events.
- Keep graph sync derived and reversible.

### Phase 5: Arbitration Drafting Integration

- Add `chronology_ids` and/or selected chronology event references to arbitration draft payloads.
- Extend `ArbitrationContextBuilder` to pull verified chronology events into `source_ledger`.
- Extend SoC, SoD, Rejoinder, and counterclaim generators to consume chronology context.
- Add section-wise regeneration for chronology-heavy sections.
- Add missing-evidence alerts when selected chronology events lack verified source backing.

### Phase 6: Exports And Presentation

- Add DOCX, XLSX, PDF, evidence index, annexure list, and pleading-section exports.
- Add tribunal/hearing presentation mode.
- Add annexure numbering and cross-reference consistency checks.

### Phase 7: Hardening

- Add backfill from existing project events and verified event links into draft chronology packages where useful.
- Add performance indexes and pagination for large chronologies.
- Add background-job support for long extraction/export runs.
- Add CI checks for route/permission drift and chronology-arbitration integration.

## 14. Test Plan

Backend unit tests:

- Chronology model validation.
- Date type and event classification validation.
- Append-only event revision history.
- Verify, edit, reject, duplicate, and restore actions.
- Idempotent extraction by source document/content hash/schema version.
- Source ledger conversion for verified chronology events.

Backend API tests:

- Chronology CRUD with org/project scoping.
- Event filtering by date, issue, claim head, support direction, source type, and status.
- RBAC denial for unauthorized users.
- Cross-tenant denial.
- Extraction creates AI-suggested events with source spans.
- Verification syncs to evidence graph.
- Rejection does not feed arbitration context.

Integration tests:

- Uploaded document -> extracted chronology events -> user verification -> evidence graph sync.
- Verified chronology -> arbitration source ledger -> generated SoC section.
- Verified chronology -> imported SoC/SoD paragraph matrix -> generated Rejoinder response.
- Chronology export preserves citations and annexure references.

Frontend tests:

- Route permission gating.
- Chronology wizard navigation.
- Source selection and extraction trigger.
- Review queue actions.
- Filters and summary counters.
- Attach chronology to arbitration draft.
- Export button states.

Graph tests:

- Verified chronology event creates expected `ProjectEvent`.
- Event links are append-only.
- Rejected/duplicate chronology events are excluded from graph sync.
- Reconciliation reports missing or stale derived graph records.

## 15. MVP Acceptance Criteria

- User can create a chronology package for a project/contract/dispute.
- User can select uploaded documents and existing registers as sources.
- System extracts dated draft events with citations, page/paragraph references, confidence, and issue tags.
- User can verify, edit, reject, or mark events as duplicate.
- Verified events sync to the evidence graph.
- Verified chronology events appear in arbitration drafting source ledger.
- SoC generation can use chronology for factual background, breach/notice/delay/payment narrative, and annexure index.
- SoD generation can use chronology for respondent counter-chronology and notice/mitigation/contradiction arguments.
- Rejoinder generation can compare SoC, SoD, and document-supported chronology events.
- Word, Excel, and PDF chronology exports are available.

## 16. Risks And Controls

- Hallucinated facts: require source spans; mark unsupported points as `[Evidence required]`.
- Date ambiguity: store `date_text` and `date_type`, not just normalized date.
- Duplicate events: include duplicate detection and manual merge.
- Pleading misuse: include only verified events by default.
- Tenant leakage: enforce org/project scope on every query and test it.
- Graph drift: treat graph records as derived and reconcile them against Mongo chronology records.
- Legal overreach: mark new claims or unsupported legal inferences for legal review.

