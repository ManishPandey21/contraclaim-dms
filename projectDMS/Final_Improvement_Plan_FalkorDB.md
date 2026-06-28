# Final Improvement Plan - FalkorDB Evidence Graph and Contract Timeline

> Validation date: 2026-06-28  
> Repository: `projectDMS`  
> Source reports reviewed:
> - `docs/architecture/end_to_end_audit.md`
> - `docs/architecture/falkordb_link_improvement.md`
> - `docs/architecture/reference_link_falkorDB_audit.md`
> - `C:\Users\santo\Downloads\Contraclaim DMS Improvement Overview.pdf`

## 1. Executive Summary

The validated direction remains correct: MongoDB must be the authoritative append-only evidence store, while FalkorDB should be a derived traversal/index layer. FalkorDB should improve evidence-chain querying and reporting, but it must not be the only persistence path for project event history, human decisions, or link audit history.

The repository now has the first implementation slice for this direction:

- Mongo-backed evidence graph models for `project_events`, `event_links`, and `ai_extractions`.
- Append-only event-link revision lifecycle for `ai_suggested`, `user_verified`, `rejected`, and `approved`.
- Evidence graph API routes for events, link suggestions, verification/rejection, and contract timeline retrieval.
- Permission catalog additions for evidence graph and contract timeline access.
- Versioned document metadata ingestion hook that creates extraction snapshots, project events, source-span backed suggestions, and idempotent graph links.
- `/contracts/timeline` frontend page with expanded filters, counters, linked records, confidence badges, Verify/Reject/Approve actions, source drill-throughs, and link history dialog.
- Domain register API/models/services for drawing references, delay events, and programme milestones.
- Downstream graph-link API that excludes unverified AI suggestions by default.
- Evidence graph backfill dry-run/mutation service and reconciliation report service for Mongo/Falkor operational hardening.

Phase 0 is implemented for the local repository baseline. Phase 1-6 now have the main backend foundations in place. Remaining work is concentrated in production validation, full HTTP authorization tests, richer resolver/LLM extraction, downstream workflow adoption, UI tests, and live FalkorDB sync jobs.

## 2. Validation Result

### Commands Run

| Check | Result |
| --- | --- |
| `python -m pytest backend\rbac_backend\tests\test_role_permission_catalog_drift.py backend\rbac_backend\tests\test_evidence_graph_service.py` | Passed. |
| `python -m pytest backend\rbac_backend\tests\test_evidence_graph_service.py backend\rbac_backend\tests\test_permission_catalog.py backend\rbac_backend\tests\test_role_permission_catalog_drift.py` | Passed: 14 tests. |
| `python -m pytest backend\rbac_backend\tests\test_route_inventory.py` | Passed: 4 tests. |
| `python -m pytest backend\rbac_backend\tests\test_permission_catalog.py backend\rbac_backend\tests\test_role_permission_catalog_drift.py backend\rbac_backend\tests\test_data_initialization.py` | Passed: 10 tests. |
| `python -m compileall backend\rbac_backend\models\evidence_graph.py backend\rbac_backend\services\evidence_graph_service.py backend\rbac_backend\routers\evidence_graph.py backend\rbac_backend\main.py` | Passed. |
| `python -m compileall backend\rbac_backend\models\permission.py backend\rbac_backend\initial_data\default_permissions.py backend\rbac_backend\initial_data\default_roles.py` | Passed. |
| `npm test -- --run src/config/__tests__/routeInventory.test.ts` | Passed: 2 tests. |
| `npm test -- --run src/config/__tests__/routeInventory.test.ts src/config/__tests__/rolePermissions.sidebar.test.ts` | Passed: 8 tests. |
| `npx eslint src/pages/ContractTimelinePage.tsx src/services/evidence-graph-api.ts src/routes.tsx src/config/rolePermissions.ts src/components/layout/Sidebar.tsx --max-warnings=0` | Passed. |
| `npx eslint src/pages/ContractTimelinePage.tsx src/services/evidence-graph-api.ts --max-warnings=0` | Passed. |
| `npm run build` | Passed. |
| `python -m compileall backend\rbac_backend\models\evidence_registers.py backend\rbac_backend\services\evidence_register_service.py backend\rbac_backend\services\evidence_graph_backfill_service.py backend\rbac_backend\services\evidence_graph_reconciliation_service.py backend\rbac_backend\routers\evidence_registers.py backend\rbac_backend\routers\evidence_graph.py backend\rbac_backend\main.py` | Passed. |
| `python -m pytest backend\rbac_backend\tests\test_evidence_graph_service.py backend\rbac_backend\tests\test_evidence_registers_backfill.py backend\rbac_backend\tests\test_route_inventory.py backend\rbac_backend\tests\test_key_dates.py` | Passed: 30 tests. |

### Phase 0 Validation - Baseline Alignment

Implemented and validated:

- `ROUTE_INVENTORY.md` was refreshed for the current route set, including billing, claims, key dates, variation, BG, IPC, concerns, retrieval, observability, contract master, contract appraisal, and contract timeline routes.
- Frontend route exists in `client/src/routes.tsx`.
- Sidebar navigation includes Contract Timeline.
- Frontend route permissions include:
  - `dms.contract.timeline.view`
  - `dms.evidence_graph.view`
  - fallback `dms.document.view`
- Backend canonical permissions include:
  - `dms.evidence_graph.view`
  - `dms.evidence_graph.verify`
  - `dms.evidence_graph.manage`
  - `dms.contract.timeline.view`
- `backend/rbac_backend/models/permission.py` appends any missing canonical permissions to the model catalog.
- `backend/rbac_backend/initial_data/default_permissions.py` appends any missing canonical permissions to the initial-data catalog.
- `backend/rbac_backend/initial_data/default_roles.py` explicitly grants all Client DMS permissions to `orgadmin`, `superadmin`, `projectadmin`, and `contractmgr_org`.
- Permission catalog, role permission, route inventory, sidebar parity, and data initialization tests pass.

Remaining Phase 0 work outside local repository execution:

- Validate role UI save/retrieve behavior in deployed production for Organization-Admin after seed migration.
- Production validation requires an accessible browser/API session against `web.contraclaim.com`; browser automation tools were not available in this Codex session.

### Phase 1 Validation - Evidence Graph Foundation

Implemented and validated:

- `backend/rbac_backend/models/evidence_graph.py` defines:
  - `ProjectEvent`
  - `EventLink`
  - `AIExtraction`
  - canonical evidence entity types
  - relation types from the audit, screenshots, and PDF
  - link statuses: `ai_suggested`, `user_verified`, `rejected`, `approved`
- `backend/rbac_backend/services/evidence_graph_service.py` implements:
  - project event create/get/list
  - AI extraction create with idempotency
  - suggested link creation
  - append-only verify/reject/approve revision creation
  - latest-link projection
  - timeline aggregation
  - link history reads
  - relation validation
  - timeline pagination metadata
- `backend/rbac_backend/routers/evidence_graph.py` exposes:
  - `GET/POST /api/project-events`
  - `GET /api/project-events/{event_id}`
  - `GET /api/event-links`
  - `POST /api/event-links/suggest`
  - `GET /api/event-links/{link_group_id}/history`
  - `POST /api/event-links/{link_group_id}/verify`
  - `POST /api/event-links/{link_group_id}/reject`
  - `POST /api/event-links/{link_group_id}/approve`
  - `GET /api/contracts/timeline`
- `backend/rbac_backend/core/database.py` registers indexes for project events, event links, and AI extractions.
- `backend/rbac_backend/main.py` registers the evidence graph router.
- Unit tests validate append-only decisions, approval, history, latest-link projection, timeline status counts, filter behavior, and extraction idempotency.
- Backend route inventory tests validate that the evidence graph API routes remain mounted.

Remaining Phase 1 work:

- Add full request/response API tests with dependency overrides for authorization and cross-tenant denial.
- Replace offset pagination with cursor pagination if production timelines become large.

### Phase 2 Validation - AI Extraction and Suggestions

Implemented and validated:

- `DocumentService.process_document_async` calls `EvidenceGraphService.ingest_document_metadata(...)` after existing document/Falkor ingestion.
- Existing metadata output is persisted into `ai_extractions`.
- Extraction records are idempotent by source document id, content hash, and schema version.
- Document-backed project events are created from uploaded document metadata.
- Suggested links are created for:
  - contract clauses
  - document references
  - drawing-like references
  - payment references
  - milestone references
  - delay references
- Unresolved drawing/delay/programme/payment references can be represented as `unresolved_reference` until dedicated registers exist.
- Extraction schema versioning is now advanced to `evidence_graph.v2`.
- Parsed extraction metadata includes claim type, delay responsibility, payment status, location, package, source spans, drawing references, payment references, milestone/key-date references, and delay references.
- Reprocessing the same document with the same content hash and schema version is idempotent and does not duplicate suggestions.

Remaining Phase 2 work:

- Replace the lightweight regex/metadata bridge with a dedicated LLM extraction schema when prompt/model execution is enabled.
- Add resolver services that match references against existing documents, contract clauses in `document_vectors`, claims, key dates, variations, bank guarantees, IPC bills, and contract master.
- Add upload integration tests through the full document-processing endpoint, not only service-level ingestion.
- Add conflict handling for repeated extraction with changed content hash or newer schema version.

### Phase 3 Validation - Contract Intelligence Timeline UI

Implemented and validated:

- `client/src/pages/ContractTimelinePage.tsx` renders the timeline workflow.
- `client/src/services/evidence-graph-api.ts` calls the timeline, decision, approval, and history API routes.
- Summary counters display:
  - timeline events
  - graph links
  - awaiting review
  - verified
  - approved
  - rejected
- Filters currently implemented:
  - event type
  - link status
  - claim type
  - clause
  - drawing
  - key date
  - delay responsibility
  - payment status
  - location
  - date from
  - date to
  - package
  - party
- Timeline cards show event details, linked records, relation/status/confidence badges, Verify/Reject/Approve actions, source drill-through actions, and link history.
- ESLint and production build pass for the timeline route and API service.
- Route inventory test passes.

Remaining Phase 3 work:

- Add UI tests for filter behavior, confidence badges, empty/loading/error states, and Verify/Reject actions.
- Add visual verification across desktop and mobile viewports after data fixtures exist.
- Replace the current simple source drill-through mapping with entity-specific detail routes as Phase 4 registers are added.

## 3. Consolidated Remaining Implementation Plan

### Phase 0 Status - Baseline and Permission Alignment

Repository implementation is complete:

1. Route inventory was refreshed from the current frontend route set.
2. Frontend route permissions, sidebar paths, backend permission constants, permission model defaults, initial-data permission seeds, and default role seeds are aligned.
3. Default DMS admin roles are explicitly granted the Client DMS canonical permission set.
4. CI-style tests now fail on route inventory drift, sidebar route drift, canonical permission catalog drift, and Org Admin seed drift.

Remaining external validation:

1. Re-run production seed migration/deployment.
2. Validate Organization-Admin permission save/retrieve behavior in production with a live browser/API session.

Acceptance:

- Organization-Admin can be granted all Client DMS permissions and retrieve them correctly.
- Route inventory and permission sources match in CI.
- New evidence graph permissions are explicit in saved roles.

### Phase 1 Status - Evidence Graph API Maturity

Implemented:

1. Explicit historical revision endpoint:
   - `GET /api/event-links/{link_group_id}/history`
2. Approval endpoint:
   - `POST /api/event-links/{link_group_id}/approve`
3. Stable sorting and offset pagination metadata for timeline and event links.
4. Relation validation rules for important source/target compatibility cases.
5. Service tests for append-only history, approval, latest revision projection, timeline aggregation, filtering, and extraction idempotency.
6. Backend route inventory coverage for the evidence graph endpoints.

Remaining:

1. Full HTTP API tests with auth/policy dependency overrides for create/list/filter, cross-tenant denial, and permission denial.
2. Audit event search UI/API for evidence graph resources.
3. Cursor pagination if timeline volume requires it.

Acceptance:

- Tests prove append-only history and latest-revision projection at service level; HTTP authorization tests remain.
- A reviewer can inspect the complete decision history for a graph link.

### Phase 2 Status - Rich AI Extraction Pipeline

Implemented:

1. Versioned metadata extraction schema bridge for document class, dates, party, package, location, clauses, drawings, IPC/payment, milestones/key dates, delay references, claim type, delay responsibility, payment status, and document references.
2. Source spans are captured for key extracted references.
3. Idempotent reprocessing by document id, content hash, and schema version.
4. Unresolved and register-backed placeholder targets are routed through `event_links`.

Remaining:

1. Implement resolver services for:
   - documents/letters
   - `document_vectors` contract clauses
   - claims
   - key dates
   - variations
   - bank guarantees
   - IPC bills
   - contract master
2. Replace regex/metadata bridge with a dedicated LLM extractor when model execution is enabled.
3. Add conflict handling for changed content hash or newer schema version.

Acceptance:

- Ingesting representative metadata creates a timeline event plus AI link suggestions for clauses, drawings, IPC/payment, milestone/delay references, and related documents.
- Reprocessing the same document with the same hash/schema is idempotent at service level.
- New schema versions can supersede older extraction snapshots without deleting history.

### Phase 3 Status - Timeline Productization

Implemented:

1. Missing filter controls and backend query support for claim type, clause, drawing, key date, delay responsibility, payment status, and location.
2. Link-group history dialog.
3. Source drill-through actions for currently known route families.
4. Verify, Reject, and Approve actions.
5. Frontend route/sidebar tests, lint, and production build validation.

Remaining:

1. Entity-specific source detail routes for Phase 4 registers.
2. Bulk Verify/Reject for selected AI suggestions.
3. Timeline fixtures and UI behavior tests.
4. Browser verification for desktop and mobile after representative seed data exists.

Acceptance:

- Users can browse a project chronologically, filter by contractual issue, verify/reject AI suggestions, and open linked source records.
- The UI clearly distinguishes AI-suggested, verified, rejected, and approved links.

### Phase 4 Status - Domain Register Completion

Implemented:

1. Added Drawing References models/service/API with drawing number, title, revision, status, discipline, location, issue/received details, supersession, and links to letters, variations, and delay events.
2. Added Delay Events models/service/API with start/end/duration, responsibility, cause, location, programme activity, critical path impact, float consumed, claimed/assessed days, entitlement, status, and evidence/document links.
3. Added Programme Milestones models/service/API with `milestone_type`, planned/forecast/actual dates, package, discipline, location, status, linked key date, and linked documents.
4. New register creation emits a `project_event` and an `event_link` so these entities can participate in the evidence graph immediately.
5. Key Date start-date precedence now follows the validated order:
   - Contract Master start/LOA date
   - project start date
   - explicit user-provided date
6. IPC bills remain the payment register base; evidence graph backfill creates `payment_event` timeline records from IPC bills when needed.

Remaining:

1. Build frontend register pages/detail drill-throughs for drawings, delay events, and programme milestones.
2. Add richer deduplication and import workflows for drawing transmittals/programmes.
3. Add HTTP authorization tests for the register endpoints.

Acceptance:

- Drawing, delay, and programme entities exist independently and link through `event_links`.
- Contractual key dates are not mixed with ordinary programme activities.
- Key Date calculations are traceable to their selected start-date source.

### Phase 5 Status - Downstream Use

Implemented:

1. Added `EvidenceGraphService.downstream_links(...)` to return only latest `user_verified` and `approved` graph links by default.
2. Added `GET /api/evidence-graph/downstream-links` for downstream consumers.
3. Added optional `include_ai_suggested=true` review mode; rejected links remain excluded.
4. Added tests proving AI suggestions are not silently treated as accepted downstream evidence.

Remaining:

1. Refactor Claim Appraisal to consume the downstream graph-link API/service.
2. Refactor Letter Drafting to cite verified graph evidence and show unverified suggestions only in review mode.
3. Refactor Determination and Arbitration Bundle workflows to include linked clauses, letters, drawings, IPCs, milestones, delay events, variations, BGs, extraction records, and link audit trail.

Acceptance:

- The safe downstream graph surface exists and defaults to verified evidence only.
- Full workflow adoption remains to be completed module-by-module.

### Phase 6 Status - Hardening, Backfill, and Falkor Reconciliation

Implemented:

1. Added `EvidenceGraphBackfillService` with dry-run and mutating modes for:
   - documents
   - claims
   - key dates
   - variations
   - bank guarantees
   - IPC bills
   - drawing references
   - delay events
   - programme milestones
2. Added APIs:
   - `POST /api/evidence-graph/backfill/dry-run`
   - `POST /api/evidence-graph/backfill`
   - `POST /api/evidence-graph/reconcile`
3. Backfill creates missing `project_events` and verified source links without deleting or overwriting existing graph history.
4. Added `EvidenceGraphReconciliationService` to report Mongo graph counts, verified/approved Falkor sync candidates, AI suggestions, rejected links, and deleted/soft-deleted document link risks.
5. Added database indexes for the new register collections.
6. Added tests for register graph emission, backfill dry-run/mutation, reconciliation reporting, route inventory, and key-date precedence.

Remaining:

1. Add document-reference and contract-clause-specific backfill/resolver passes.
2. Implement live FalkorDB sync and stale edge cleanup jobs from verified/approved Mongo graph links.
3. Add scheduled reconciliation checks and broader tenant-isolation tests.

Acceptance:

- Existing production data can be backfilled safely from Mongo registers.
- FalkorDB can be reconciled from MongoDB once the live sync job is attached.
- CI now protects the new route inventory and core graph/register service behavior.

## 4. Test Plan

### Implemented Tests

- Permission catalog drift tests.
- Evidence graph service unit tests for append-only verify/reject behavior.
- Evidence graph service unit tests for latest-link projection and timeline counters.
- Evidence graph service unit tests for downstream verified/approved default filtering.
- Evidence register service tests for drawing, delay, and programme records emitting project events and graph links.
- Backfill service tests for dry-run reporting and mutating project-event/link creation.
- Reconciliation service tests for Mongo graph counts and deleted-document link reporting.
- Key Date service tests continue to pass after Contract Master/project/explicit start-date precedence update.
- Backend route inventory tests for evidence graph, backfill, reconcile, and register endpoints.
- Frontend route inventory test.
- Frontend lint for timeline implementation files.
- Python compile check for evidence graph modules.
- Python compile check for evidence register, backfill, reconciliation, and updated routers.

### Required Additional Tests

- API tests for:
  - project event create/list/filter
  - event link suggest/list/history
  - verify/reject/approve revision creation
  - timeline aggregation
  - cross-tenant denial
  - permission denial
- Integration tests for:
  - document upload to `AIExtraction`
  - document upload to `ProjectEvent`
  - document upload to `EventLink` suggestions
  - extraction idempotency by document/hash/schema
- UI tests for:
  - timeline filters
  - summary counters
  - confidence/status badges
  - Verify/Reject actions
  - empty/loading/error states
  - link history drawer
- Backfill tests for:
  - conflict reporting
  - idempotent repeated runs
  - tenant isolation
- Falkor tests for:
  - derived sync
  - stale edge cleanup
  - deleted document exclusion
  - Mongo/Falkor reconciliation reports

## 5. Definition of Done

This improvement is complete when:

1. Every timeline event and graph link is tenant-scoped and permission-gated.
2. AI-suggested links are never trusted until verified or approved.
3. Verify, reject, and approve actions append revisions and preserve full audit history.
4. Existing documents and registers can be backfilled into the evidence graph.
5. Drawing, delay, programme milestone, key date, IPC/payment, variation, claim, and BG records can all participate in graph links.
6. Downstream claim, drafting, determination, and bundle workflows use verified graph evidence.
7. FalkorDB is rebuildable from MongoDB and never acts as the sole source of truth.
8. CI protects route/permission drift, graph integrity, extraction idempotency, and tenant isolation.
