# Arbitration LangGraph Implementation Progress

**Started:** 2026-07-21
**Last verified / updated:** 2026-07-22
**Repository:** `C:\SaaS\projectDMS`
**Source report:** `docs/architecture/arbitration_pleadings_langgraph_workflow_audit_and_implementation_plan_2026-07-21.md`
**Production deployment:** Deployed 2026-07-22 at ContraClaim commit `cd1372d30ebe06211078c63d713172acfc63127e`
**Authoritative rollout:** Existing `arbitration_v2`; rollout `off`; production acceptance remains false

## Status legend

- **implemented / unit-tested** — code exists and relevant local automated tests passed.
- **implemented / integration pending** — code exists, but real infrastructure or restart behavior was unavailable.
- **partial** — a safe subset exists; listed acceptance work remains.
- **not tested** — no acceptance claim.

## Current assessment against the source report

- **Phase 0 implementation pending:** none identified in the current local source. P0-01 through P0-08 have code and negative unit-test coverage.
- **Phase 0 production-like acceptance:** migrations `20260721_0001`, `20260721_0002`, and corrective `20260722_0001` are applied and idempotent on production MongoDB; 99 production-image tests pass, including eight real-FastAPI two-tenant isolation tests. Live unauthenticated workflow/checkpoint/export probes return `401`. Authenticated browser testing with two real tenant accounts, real reviewer-role/step-up flows, and filing export artifacts remains pending, so Phase 0 is not fully production-accepted.
- **Next functional step completed on 2026-07-22:** paragraph responses now own draft-bound SoD/Rejoinder positions. Legacy defence/rejoinder collections receive server-owned, reviewable, read-only projections; the rejoinder LLM updates the authoritative response before projecting it; direct draft-bound duplicate creation and projection content edits return `409`.
- **Still pending in that cleanup:** a separately reviewed migration/backfill for historical defence/rejoinder rows that cannot be mapped unambiguously to an existing paragraph response.

## Implementation checklist

| Report recommendation | Status | Affected files | Migration | Tests / acceptance evidence |
|---|---|---|---|---|
| P0-01 server-owned matrix review state | implemented / unit-tested | model, case service, case UI | `20260721_0001` returns unverifiable rows to `needs_review` | Privileged create/update fields rejected; all new rows need review; direct UI status shortcut removed |
| P0-02 reject agent `auto_approve` | implemented / unit-tested | model, deterministic/LLM agents | covered by P0 migration | `auto_approve` and unknown options rejected; agent rows cannot approve themselves |
| P0-03 approved-only preparation and source rehydration | implemented / unit-tested | `case_workspace.py`, `context.py`, `service.py` | none | Only approved scoped matrix revisions are copied; labels/snippets are rebuilt server-side |
| P0-04 revision-bound readiness receipts | implemented / unit-tested | case service, draft service, workflow repository | both migrations add approval indexes | Receipt binds case/type/matrix/evidence hashes; source, matrix, case, paragraph, permission, and selection edits invalidate it |
| P0-05 standalone working drafts | implemented / unit-tested | draft service, router/API/UI | P0 migration returns unverifiable standalone approvals to review | Final approval, filing export, and bundles reject standalone drafts; preview is watermarked |
| P0-06 governed filing exports | implemented / unit-tested | draft/case services, router/API/UI | exact individual/bundle authorization indexes | Approved immutable version, readiness, validation, citation/exhibit audit and drift enforced; preview does not export status |
| P0-07 rejoinder permission semantics | implemented / unit-tested | model, agent, readiness, validator, UI | conservative field migration | New matter with required/unobtained permission blocks; dedicated scoped permission receipt endpoint added |
| P0-08 complete section regeneration | implemented / unit-tested | service, model, router, tests | lineage fields/index compatibility | Complete parent is merged, unaffected sections/ledger preserved, lineage and run type recorded |
| Phase 1 engine interface and v2 adapter | implemented / unit-tested | `engines/base.py`, `engines/v2.py`, selector | `20260721_0002` | Server-only selection, input/document/opponent/evidence/matrix snapshots and idempotent create |
| Snapshots, effects, approvals, events and plans | implemented / unit-tested; Mongo pending | workflow repository/domain/service | workflow/index migration | Exact hashes, optimistic CAS, effect keys, approval dedupe, immutable plans; real Mongo duplicate-race test pending |
| Atomic version allocation and lineage | implemented / unit-tested; Mongo pending | arbitration repository/service | counter seed and unique draft/version index | Concurrent fake-repository allocation advances from existing maximum; real Mongo contention pending |
| Official in-process LangGraph | implemented / production restart-tested | `langgraph_engine.py` | checkpoint/write indexes and TTL | Production has LangGraph `1.2.9` and MongoDB saver `0.4.0`; a synthetic allowlisted checkpoint interrupted, survived backend restart, resumed from Mongo, and completed |
| Durable workflow APIs and gates | implemented / production-image tested | models, router, workflow service, API client | workflow indexes | create `202`, state/events/resume/cancel/approve/fallback/checkpoints; stale CAS and real-FastAPI HTTP authorization/isolation fixture pass; authenticated browser pending |
| Pleading-specific routing | implemented / unit-tested | `workflow_domain.py`, v2/graph engines | matrix revision snapshots | SoC/SoD/Counterclaim/Rejoinder requirements; SoD/Rejoinder require immutable opponent versions |
| Parallel analysis and deterministic merge | implemented / unit-tested | workflow domain and graph | revision snapshots | Ten read-only branches; serial/parallel revision hash parity test passes |
| Material user questions | implemented / unit-tested | workflow domain/service, graph, API/UI | question and direction snapshots | Missing required artifacts pause before matrix approval; required answers are exact-ID checked and snapshotted |
| Pleading strategy artifact | implemented / unit-tested | model, workflow domain/repository/service/UI | plan unique indexes | Versioned plan contains required strategy/source fields and exact plan hash; plan approval gates generation |
| Parallel validation and bounded remediation | implemented / unit-tested | `workflow_validation.py`, existing validator | validation snapshots | Nineteen checks run read-only; unsafe automatic rewrite is refused; bounded cycle escalates to legal review |
| Reviewer-role matrix and separation | implemented / unit-tested | `approval_policy.py`, config, case/draft/workflow services | approval receipt indexes | Gate roles configurable; route permissions plus semantic roles; author/last-material-editor cannot approve readiness/plan/draft/export |
| Paragraph response authority and immutable pleading import | implemented / unit-tested; historical backfill pending | model/repository/service/router/UI, `paragraph_positions.py` | compatible document fields; legacy backfill not yet added | Paragraph response is authoritative; draft-bound defence/rejoinder rows are reviewable read-only projections; direct duplicates/edits are rejected; denial requires a reason; sources are scoped; unversioned imports block approval |
| Frontend workflow integration | implemented / unit/build-tested; browser pending | case workspace, drafting page, API client | none | Progress/current node/blockers/questions/gates/cancel/opponent version/paragraph editor/preview/filing controls; authenticated browser pending |
| Shadow and canary controls | implemented / unit-tested; telemetry environment pending | selector, workflow service, config | events index | Deterministic buckets, allowlists, shadow parity event with `authoritative_writes=false`, production acceptance switch, forced v2 |
| Failure recovery and operations | partial / production restart-tested | checkpoint engine, export worker, ops route/runbook | TTL/status indexes | Mongo checkpoint interrupt/restart/resume passed; backend/worker restart and full backup/checksums passed. Redis job kill/retry, load, and restore drill remain pending |

## Production-like acceptance status (2026-07-22)

| Gate | Status | Evidence / remaining work |
|---|---|---|
| Migration | **passed** | All three arbitration migrations recorded; reruns skip idempotently. Runtime-compatible checkpoint/write TTL indexes are present at 30 days |
| Authorization | **partial** | Unauthenticated case, workflow state, admin checkpoint, and export routes return `401`; real-FastAPI reviewer policy tests pass. Real authenticated reviewer-role and step-up browser flows remain |
| Restart and durable resume | **passed** | Backend and worker restarted healthy; a Mongo checkpoint paused at document selection, survived backend restart, resumed in a new process, and completed |
| Tenant isolation | **partial** | Eight production-image tests using the real FastAPI app and a two-tenant fixture pass. A live two-account cross-tenant browser/API exercise remains |
| Rollout safety | **passed** | Runtime remains `arbitration_v2`, rollout `off`, `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false` |

## Tests executed

| Command | Result | Classification |
|---|---|---|
| `python -m pytest backend/rbac_backend/tests/test_arbitration_drafting.py --disable-warnings -q` | 82 passed | local unit |
| Focused paragraph-authority/projection tests | 3 passed; included in the 82-test run | local unit |
| Focused new routing/policy/concurrency/validation tests | Passed and included in the 80-test final run | local unit |
| Earlier focused workflow foundation tests | 4 passed | local unit |
| Phase 0 focused tests | 6 passed | local unit |
| `python -m pytest backend/rbac_backend/tests/test_migration_runner.py -q` | 9 passed | migration-runner unit |
| `python -m pytest backend/rbac_backend/tests/test_observability.py -q` | 2 passed | monitoring unit |
| Production backend image: arbitration drafting + HTTP isolation + migration runner | 99 passed, 224 warnings | production-image acceptance |
| Synthetic Mongo checkpoint interrupt, backend restart, resume | paused at document selection; resumed to completed with a new checkpoint ID | production restart acceptance |
| Live unauthenticated arbitration route probes | case/workflow/checkpoint/export all `401` | production perimeter authorization |
| Full production backup `20260722-015547` | MongoDB plus five volume archives all passed SHA-256 verification | production rollback evidence |
| `npm test -- --run src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx src/pages/__tests__/ArbitrationDraftingPage.test.tsx` | 2 files / 9 tests passed | frontend component |
| `npm run build` from `client` | passed; 3,881 modules transformed | production client build |
| Python `compileall` over changed backend/migrations | passed | syntax/import compilation |
| LangGraph runtime probe | LangGraph `1.2.9`; MongoDB saver `0.4.0`; runtime available | production dependency parity |

The first frontend test command used incorrect file paths and returned “No test files found”; it was corrected to the `src/pages/__tests__` paths and passed.
The repository `.venv` could not collect the HTTP tests because it lacks FastAPI. The production backend image supplied dependency parity and completed the combined 99-test suite.

## API changes

- `POST /api/arbitration/cases/{case_id}/workflows` (`202 Accepted`, idempotency key supported)
- `GET /api/arbitration/cases/{case_id}/workflows/{run_id}/state`
- `GET /api/arbitration/cases/{case_id}/workflows/{run_id}/events`
- `POST /api/arbitration/cases/{case_id}/workflows/{run_id}/resume`
- `POST /api/arbitration/cases/{case_id}/workflows/{run_id}/cancel`
- `POST /api/arbitration/cases/{case_id}/workflows/{run_id}/approvals/{gate}`
- `POST /api/arbitration/cases/{case_id}/workflows/{run_id}/force-v2` (admin plus step-up)
- `GET /api/arbitration/operations/workflows/{run_id}/checkpoints` (admin, redacted)
- `PATCH /api/arbitration/drafts/{draft_id}/paragraph-responses/{response_id}`
- Separate preview and governed filing export endpoints; dedicated rejoinder permission endpoint.

## Migrations and rollback

- `v20260721_0001_arbitration_phase0_containment.py`: removes unverifiable historical approval/readiness semantics, corrects rejoinder permission data, returns unsafe standalone statuses to review, and adds containment indexes.
- `v20260721_0002_arbitration_workflow_foundation.py`: seeds version counters and adds workflow, snapshot, effect, approval, event, plan, checkpoint TTL, immutable version, and exact export indexes.
- `v20260722_0001_langgraph_checkpoint_ttl_compatibility.py`: replaces the custom checkpoint TTL index name with the runtime-compatible default and adds the same 30-day TTL to checkpoint writes. This corrected a production-discovered Mongo code-85 collision during saver initialization.
- P0/P1 downgrades drop their new indexes and intentionally do not recreate unverifiable approvals or invented identities/timestamps. The TTL compatibility downgrade restores the prior named checkpoint TTL index and removes the write TTL index.
- Operational rollback is configuration-first: set rollout mode to `forced_v2` and default to `arbitration_v2`; preserve graph artifacts for audit. See the operations runbook.

## Unresolved risks and deviations

- Real MongoDB version-allocation contention, Redis job kill/retry, Qdrant/S3/model integration, load, TTL-expiry timing, restore drill, and authenticated browser tests remain required before canary acceptance.
- The production-image two-tenant fixture passes, but it is not a substitute for a live exercise with two authenticated tenant accounts and real organization/project memberships.
- New draft-bound editable duplication is closed: paragraph responses materialize server-owned defence/rejoinder projections, and projection content cannot be changed through generic matrix APIs. Historical legacy rows remain unmigrated where a paragraph-response mapping is ambiguous; a data audit and conservative backfill are still required.
- The frontend exposes immutable opponent draft/version IDs and document IDs, but a rich page-level document preview/selector and visual version diff remain partial; evidence snippets and version history remain available in their existing views.
- Counterclaim routing and mandatory matrix evidence are implemented; the deterministic counterclaim agent remains conservative and will not infer an independent cause of action without reviewed inputs.
- Primary rollout and removal of v2 were deliberately not performed. `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false` is the fail-safe.

## Next action

Acceptance next: run real authenticated reviewer-role/step-up and two-account tenant-isolation browser/API flows, Redis export-job kill/retry with duplicate-export checks, version-allocation contention, load, TTL-expiry, and a restore drill. Code next: inventory historical draft-bound defence/rejoinder rows, backfill only exact paragraph-number/version matches into projections, and leave ambiguous rows quarantined for legal review. After those gates, consider a tenant/project shadow rollout; do not enable primary.
