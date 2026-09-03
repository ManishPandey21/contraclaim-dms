# Improvement Plan for the Letter Drafting Process

## 1. Purpose

This document reviews the attached `Letter_Drafting_Plan.md` against the currently implemented ContraClaim DMS letter drafting backend and defines a complete implementation plan for improving the present system.

The target workflow remains:

```text
First Plan -> Then Validate -> Then Draft -> Then Review -> Then Issue
```

The attached plan also adds two important product requirements that are not yet fully represented in the current backend:

1. Reply Letter Mode: draft a response to an existing incoming letter.
2. Fresh Letter Mode: draft a new contractual letter without requiring an incoming letter.

## 2. Current Backend Implementation Snapshot

The current system has two drafting paths.

### 2.1 Legacy / Existing LangGraph Path

The existing workflow is still available through:

```text
POST /api/ai-assistant/langgraph/background
POST /api/ai-assistant/langgraph/strategy-plan
POST /api/ai-assistant/langgraph/draft
GET  /api/ai-assistant/langgraph/runs/{letter_id}
```

Main backend components:

```text
backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py
backend/rbac_backend/services/ai_service.py
backend/rbac_backend/services/letter_service.py
backend/rbac_backend/routers/ai_assistant.py
backend/rbac_backend/services/strategy_context_service.py
```

Implemented capabilities:

- Loads an existing letter by `letter_id`.
- Collects selected context documents.
- Retrieves contract clauses where available.
- Retrieves conversation chain and graph-linked letters.
- Generates background/strategy/draft output.
- Runs basic reviewer checks.
- Stores draft output and draft versions on the `letters` document.

Limitations:

- The graph class still mixes loading, retrieval, planning, drafting, review, graph sync, and persistence.
- It assumes an existing letter record; it does not model a true Fresh Letter initiation flow.
- It has no first-class planning sheet model, reply matrix model, or letter category model.
- It stores many drafting artifacts directly on the `letters` document.
- The reviewer is not yet strong enough for all source-fidelity requirements in the attached plan.

### 2.2 New V2 Drafting Subsystem

A backend v2 drafting subsystem has been started:

```text
backend/rbac_backend/models/letter_drafting.py
backend/rbac_backend/routers/letter_drafting.py
backend/rbac_backend/services/letter_drafting/
backend/rbac_backend/scripts/migrate_letter_draft_runs.py
```

Current v2 API routes:

```text
POST /api/letters/{letter_id}/drafting/runs
GET  /api/letters/{letter_id}/drafting/runs/{run_id}
GET  /api/letters/{letter_id}/drafting/runs/latest
POST /api/letters/{letter_id}/drafting/runs/{run_id}/accept-plan
POST /api/letters/{letter_id}/drafting/runs/{run_id}/accept-draft
```

Implemented v2 capabilities:

- New `letter_draft_runs` collection.
- Typed `DraftRun`, `DraftArtifact`, `SourceEvidence`, and `ValidationReport`.
- Prompt registry with versioned prompt records.
- Source ledger generation for current input, clauses, context documents, prior correspondence, graph thread, and comments.
- Deterministic validation for required sections, unsupported clause citations, source presence, placeholders, and learning-update control.
- Acceptance endpoints for plan and draft.
- Non-destructive migration helper for legacy draft/strategy fields.

Current v2 gaps:

- No dedicated Reply vs Fresh mode field.
- No letter category taxonomy.
- No incoming letter analysis artifact.
- No planning sheet schema.
- No point-wise reply matrix schema.
- No user confirmation state for extracted points or planning sheet.
- No revise-draft API actions such as make firmer, shorten, add clause reasoning.
- No export/issue API in the drafting module.
- No frontend integration yet.

## 3. Gap Analysis Against Attached Plan

| Requirement from Attached Plan | Current Status | Gap |
|---|---|---|
| Reply Letter Mode | Partly supported through existing letter context | Needs explicit `draft_type=reply` and incoming letter analysis |
| Fresh Letter Mode | Not first-class | Needs initiation without an incoming letter |
| Project / Contract Package selection | Partly supported by org/project | Needs contract package field and filtering |
| Letter Category | Not implemented in v2 | Add category enum and category-specific validation |
| Basic input gathering | Partly via `requirements` and `points` | Needs structured fields |
| Incoming letter extraction | Not first-class in drafting v2 | Add extraction artifact and confirmation workflow |
| Reply Planning Sheet | Not modeled | Add structured planning sheet |
| Point-wise reply matrix | Not modeled | Add matrix model and validator |
| Completeness check | Basic threshold exists | Needs mode/category-specific checks |
| Source Integrity Notes | Present in draft artifact | Needs structured source integrity fields |
| User review and refinement actions | Not implemented | Add revision endpoint and action types |
| Approval and issue status | Partly through accept draft | Needs issue/export/status integration |
| Audit trail | Draft runs persist history | Needs user edit/revision event tracking |

## 4. Target Backend Design

### 4.1 Drafting Modes

Extend `DraftRunCreateRequest` with:

```text
draft_type: reply | fresh
letter_category: claim_reply | eot_reply | variation | payment_ipc | advance_recovery | completion | ncr_quality | delay_progress | records_request | dispute | general
contract_package: string
purpose: string
desired_position: string
required_action: string
tone: firm | neutral | advisory | conciliatory | firm_contractual
timeline_days: integer | null
incoming_document_id: string | null
incoming_letter_id: string | null
clauses_to_consider: string[]
attachments: string[]
```

Rules:

- `draft_type=reply` must include `incoming_document_id` or `incoming_letter_id`.
- `draft_type=fresh` must include `purpose`, `background facts`, and `required_action`.
- Claim, dispute, payment, EOT, and variation categories must either provide clauses or retrieve clause evidence before drafting.

### 4.2 New Typed Artifacts

Add these models in `letter_drafting.py`.

#### IncomingLetterAnalysis

Fields:

```text
letter_no
letter_date
sender
recipient
subject
main_request
clauses_cited
amount_claimed
time_extension_requested
documents_submitted
action_requested
response_deadline
contractual_risk
extraction_confidence
confirmed_by_user
confirmed_at
```

#### PlanningSheet

Fields:

```text
draft_type
letter_category
letter_purpose
subject
recipient
sender_role
trigger_event
contractual_basis
factual_basis
previous_correspondence
recommended_position
required_action
timeline
tone
risk_level
rights_reservation_required
missing_inputs
user_confirmed
```

#### ReplyMatrixRow

Fields:

```text
incoming_point
proposed_reply
source_ids
clause_refs
risk_note
status: supported | needs_confirmation | unsupported
```

#### SourceIntegritySummary

Fields:

```text
documents_relied_upon
clauses_relied_upon
user_provided_facts
prior_correspondence_used
placeholders_requiring_confirmation
unsupported_points_excluded
warnings
```

### 4.3 DraftRun Enhancements

Extend `DraftRun` with:

```text
draft_type
letter_category
contract_package
incoming_analysis
planning_sheet
reply_matrix
source_integrity_summary
revision_of_run_id
revision_action
approval_status
issued_document_id
exported_file_id
```

Keep old fields for compatibility:

```text
plan
draft_artifact
validation_report
sources
warnings
trace
```

## 5. Target API Design

Keep the current v2 run APIs and add workflow-specific endpoints.

### 5.1 Start Drafting Session

```text
POST /api/letter-drafting/start
```

Purpose:

- Create an initial drafting session for Reply or Fresh mode.
- For Reply mode, attach incoming document/letter.
- For Fresh mode, create a draft shell if no letter exists yet.

Request:

```json
{
  "draft_type": "reply",
  "organization_id": "string",
  "project_id": "string",
  "contract_package": "KNPCC-05",
  "letter_category": "claim_reply",
  "sender_role": "engineer",
  "recipient": "Contractor",
  "incoming_document_id": "string"
}
```

Response:

```json
{
  "session_id": "string",
  "letter_id": "string",
  "next_step": "analyze_incoming_letter"
}
```

### 5.2 Analyze Incoming Letter

```text
POST /api/letters/{letter_id}/drafting/analyze-incoming
```

Purpose:

- Extract incoming letter facts, references, claims, clauses, risks, and requested action.

Output:

```text
IncomingLetterAnalysis
```

### 5.3 Confirm Extracted Points

```text
POST /api/letters/{letter_id}/drafting/confirm-analysis
```

Purpose:

- Save user-confirmed or user-edited extraction result.
- Planning cannot proceed in Reply mode until this is confirmed, unless user explicitly overrides.

### 5.4 Prepare Planning Sheet

```text
POST /api/letters/{letter_id}/drafting/prepare-plan
```

Purpose:

- Retrieve knowledge base sources.
- Build planning sheet.
- Build reply matrix for Reply mode.
- Run completeness checks.

Output:

```text
DraftRun with mode=strategy
PlanningSheet
ReplyMatrixRow[]
SourceEvidence[]
ValidationReport
```

### 5.5 Confirm Planning Sheet

```text
POST /api/letters/{letter_id}/drafting/runs/{run_id}/confirm-plan
```

Purpose:

- Mark planning sheet as user-confirmed.
- Drafting should require a confirmed plan.

### 5.6 Generate Draft

```text
POST /api/letters/{letter_id}/drafting/generate-draft
```

Purpose:

- Generate draft only from confirmed plan and source ledger.
- Persist structured draft artifact and source integrity summary.

### 5.7 Revise Draft

```text
POST /api/letters/{letter_id}/drafting/runs/{run_id}/revise
```

Allowed actions:

```text
make_firmer
make_more_polite
add_contractual_reasoning
add_clause_reference
make_short
make_detailed
convert_to_employer_submission
convert_to_contractor_letter
regenerate
custom_instruction
```

Rules:

- Revision must preserve same source ledger unless user adds new sources.
- Any new clause reference must be validated against clause sources.
- Every revision creates a new `DraftRun` with `revision_of_run_id`.

### 5.8 Approve, Export, and Issue

```text
POST /api/letters/{letter_id}/drafting/runs/{run_id}/approve
POST /api/letters/{letter_id}/drafting/runs/{run_id}/export
POST /api/letters/{letter_id}/drafting/runs/{run_id}/issue
```

Purpose:

- Approve the final draft.
- Generate DOCX/PDF.
- Update DMS status and correspondence chain.

## 6. Backend Service Improvements

### 6.1 DraftInputValidator

Create:

```text
backend/rbac_backend/services/letter_drafting/input_validator.py
```

Responsibilities:

- Validate Reply mode mandatory fields.
- Validate Fresh mode mandatory fields.
- Validate category-specific requirements.
- Return blocking vs placeholder-permitted missing items.

Critical blocking examples:

- Missing project.
- Missing contract package.
- Missing recipient.
- Missing subject.
- Missing purpose.
- Missing incoming letter in Reply mode.
- Missing trigger event in Fresh mode.
- Missing contractual basis for claim/payment/dispute/EOT categories.

Placeholder-permitted examples:

- Exact date.
- Exact letter number.
- Attachment list.
- Name/designation.
- Timeline.

### 6.2 IncomingLetterAnalyzer

Create:

```text
backend/rbac_backend/services/letter_drafting/incoming_analyzer.py
```

Responsibilities:

- Read incoming document text and metadata.
- Extract structured incoming letter analysis.
- Identify points that need user confirmation.
- Identify clauses cited by the sender.
- Identify requested action and response deadline.

### 6.3 PlanningSheetBuilder

Create:

```text
backend/rbac_backend/services/letter_drafting/planning.py
```

Responsibilities:

- Build `PlanningSheet`.
- Build `ReplyMatrixRow[]` for Reply mode.
- Build category-specific risk note.
- Enforce no direct draft without plan.
- Produce missing-input questions when blocked.

### 6.4 SourceRetriever Improvements

Current source retrieval is in:

```text
backend/rbac_backend/services/letter_drafting/context.py
```

Improve it to support:

- Mandatory same project and contract package filtering.
- Optional sender/recipient filters.
- Optional date range filter.
- Same clause filter.
- Letter chain ID / conversation ID priority.
- Similar approved letters as style references only.
- Separate source use: fact, clause, history only, style continuity, comment.

### 6.5 DraftGenerator Improvements

Current generator:

```text
backend/rbac_backend/services/letter_drafting/generator.py
```

Improve it to:

- Require confirmed plan for draft mode.
- Use category-specific drafting structure.
- Generate reply letter with point-wise matrix coverage.
- Generate fresh letter with trigger-event discipline.
- Always output structured draft letter and source integrity summary.
- Exclude unsupported points rather than inventing facts.

### 6.6 DraftValidator Improvements

Current validator:

```text
backend/rbac_backend/services/letter_drafting/validator.py
```

Improve it to check:

- Draft has required sections for selected category.
- Reply draft addresses every supported incoming letter point.
- Fresh draft includes trigger event.
- Clause citations exist in `SourceEvidence` with `allowed_use=clause`.
- Dates and amounts are either sourced or placeholdered.
- No Learning Update unless finalized/approved.
- Prior correspondence is not used as contract clause evidence.
- Draft does not include unsupported AI assumptions.

### 6.7 LetterVersionService

Current accept-draft logic is in:

```text
backend/rbac_backend/services/letter_drafting/repository.py
```

Improve by creating:

```text
backend/rbac_backend/services/letter_drafting/versioning.py
```

Responsibilities:

- Store user edits.
- Store AI revisions.
- Store final approved draft.
- Link incoming letter / outgoing letter.
- Track issue/export status.

## 7. Data Persistence Plan

### 7.1 Collections

Use:

```text
letter_draft_runs
prompt_templates
letters
documents
```

Add if needed:

```text
letter_drafting_sessions
letter_draft_events
```

### 7.2 letter_draft_runs

Each run should store:

```text
run_id
letter_id
draft_type
mode
letter_category
contract_package
status
role
recipient_focus
inputs
incoming_analysis
planning_sheet
reply_matrix
context_bundle
sources
plan
draft_artifact
source_integrity_summary
validation_report
revision_of_run_id
revision_action
warnings
trace
started_at
completed_at
created_by
```

### 7.3 letter_draft_events

Event types:

```text
session_started
incoming_analyzed
analysis_confirmed
plan_generated
plan_confirmed
draft_generated
draft_revised
draft_approved
draft_exported
draft_issued
draft_blocked
```

## 8. Prompt Improvements

### 8.1 Planning Prompt

Prompt objective:

- Never draft.
- Produce planning sheet only.
- Identify missing information.
- Build reply matrix for Reply mode.
- Return blocked status where required.

### 8.2 Drafting Prompt

Prompt objective:

- Draft only from confirmed planning sheet.
- Use source ledger.
- Use category-specific sequence.
- Use placeholders for minor missing items.
- Exclude unsupported assertions.

Mandatory sequence:

```text
Reference -> Facts -> Contract Clause -> Analysis -> Decision -> Action Required -> Reservation of Rights
```

### 8.3 Revision Prompt

Prompt objective:

- Revise existing draft using same source ledger.
- Do not introduce new facts.
- Preserve source integrity notes.
- Record revision action.

## 9. UI / Frontend Integration Plan

Backend is the priority, but the current frontend should later add these screens:

1. Drafting Mode Selection.
2. Project / Contract / Category Selection.
3. Reply Letter Input Screen.
4. Fresh Letter Input Screen.
5. Incoming Letter Analysis Confirmation.
6. Planning Sheet Review.
7. Draft Review and Revision Actions.
8. Approval / Export / Issue Screen.

The frontend should use the v2 APIs rather than the legacy LangGraph endpoints.

## 10. Migration Plan

### Phase M1: Preserve Legacy Fields

Do not delete existing draft fields on `letters`.

Fields to preserve:

```text
strategy_plan
draft_output
draft_versions
draft_sources
reviewer_findings
graph_status
graph_run_id
```

### Phase M2: Copy Legacy Data into letter_draft_runs

Use existing script:

```text
backend/rbac_backend/scripts/migrate_letter_draft_runs.py
```

Dry run:

```bash
python backend/rbac_backend/scripts/migrate_letter_draft_runs.py
```

Apply:

```bash
python backend/rbac_backend/scripts/migrate_letter_draft_runs.py --apply
```

### Phase M3: Switch Reads to v2

After migration:

- Latest strategy should come from latest `letter_draft_runs` strategy run.
- Latest draft should come from latest accepted draft run.
- Legacy fields remain fallback only.

## 11. Implementation Phases

### Phase 1: Data Model Expansion

Implement:

- `draft_type`
- `letter_category`
- `contract_package`
- `incoming_analysis`
- `planning_sheet`
- `reply_matrix`
- `source_integrity_summary`
- `revision_of_run_id`
- `revision_action`

Acceptance criteria:

- Existing tests pass.
- New models validate Reply and Fresh payloads.
- Existing v2 routes still work.

### Phase 2: Reply Letter Mode

Implement:

- Incoming letter analysis endpoint.
- User confirmation endpoint.
- Reply planning sheet.
- Point-wise reply matrix.
- Reply-specific completeness check.

Acceptance criteria:

- Reply mode blocks if no incoming letter is selected.
- Reply matrix is generated before draft.
- Draft validator confirms every supported incoming point is addressed.

### Phase 3: Fresh Letter Mode

Implement:

- Fresh drafting session creation.
- Fresh letter mandatory input validation.
- Trigger event enforcement.
- Fresh letter planning sheet.

Acceptance criteria:

- Fresh mode works without incoming letter.
- Fresh mode blocks if subject, purpose, recipient, trigger event, or required action is missing.
- Fresh draft clearly states trigger event.

### Phase 4: Category-Specific Validation

Implement category rule profiles:

```text
claim_reply
eot_reply
variation
payment_ipc
advance_recovery
completion
ncr_quality
delay_progress
records_request
dispute
general
```

Acceptance criteria:

- Claim/payment/dispute/EOT categories require contractual basis.
- Completion category checks for completion/TOC trigger.
- Records request category requires requested records and timeline.
- NCR/quality category requires non-compliance facts.

### Phase 5: Revision and Review Actions

Implement:

- `revise` endpoint.
- Revision action enum.
- Draft version lineage.
- Same-source-ledger enforcement.

Acceptance criteria:

- Each revision creates a new run.
- Revision references original run.
- Revision cannot introduce unsupported clause references.

### Phase 6: Approval, Export, and Issue

Implement:

- `approve` endpoint.
- `export` endpoint for DOCX/PDF.
- `issue` endpoint.
- DMS status update.
- Correspondence chain linking.

Acceptance criteria:

- Approved draft becomes immutable version.
- Issued letter updates status to `Replied` or `Issued`.
- Reply mode links outgoing reply to incoming letter.

### Phase 7: Observability and Audit

Implement:

- `letter_draft_events`.
- Timing per stage.
- User edit history.
- Source ledger audit.
- Prompt version audit.

Acceptance criteria:

- Every drafting action creates an event.
- Every AI output records prompt version and model.
- Every accepted draft can be traced to sources.

## 12. Test Plan

### Unit Tests

Add tests for:

- Reply vs Fresh input validation.
- Category-specific validation.
- Incoming letter analysis parser.
- Planning sheet builder.
- Reply matrix generation.
- Trigger event enforcement.
- Clause validation.
- Placeholder detection.
- Source integrity summary.
- Revision action handling.

### Integration Tests

Add tests for:

- Reply mode full flow.
- Fresh mode full flow.
- Blocked reply due to missing incoming letter.
- Blocked fresh draft due to missing trigger event.
- Claim reply blocked without contractual basis.
- Draft revision preserves source ledger.
- Approve draft creates immutable version.
- Issue reply links outgoing letter to incoming letter.

### Regression Tests

Add tests for:

- No cross-project source retrieval.
- No cross-contract contamination.
- No unsupported clause citation.
- No Learning Update unless finalized.
- Legacy LangGraph endpoints still work during transition.

## 13. Recommended Final Backend Workflow

```text
1. User starts drafting session.
2. Backend records draft_type, project, contract package, sender role, category.
3. If Reply mode, backend analyzes incoming letter.
4. User confirms or edits incoming letter analysis.
5. Backend retrieves scoped project/contract sources.
6. Backend prepares planning sheet.
7. If Reply mode, backend prepares point-wise reply matrix.
8. Backend validates completeness.
9. If blocked, backend returns missing inputs.
10. User confirms planning sheet.
11. Backend generates draft.
12. Backend validates draft against sources.
13. User revises or approves.
14. Backend stores final accepted draft version.
15. Backend exports and issues final letter.
16. Backend links outgoing letter and updates workflow status.
```

## 14. Priority Recommendation

The highest-value next implementation should be:

1. Add `draft_type`, `letter_category`, and structured input fields.
2. Add Reply mode incoming analysis and confirmation.
3. Add PlanningSheet and ReplyMatrix models.
4. Enforce no draft generation without a confirmed plan.
5. Add Fresh mode trigger-event validation.
6. Add revision endpoint for user review actions.

This sequence directly aligns the current v2 backend with the attached plan while preserving the existing drafting run architecture already implemented.
