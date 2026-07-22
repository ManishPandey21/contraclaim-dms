# Arbitration LangGraph Implementation Progress

**Started:** 2026-07-21
**Last verified / updated:** 2026-07-23
**Repository:** `C:\SaaS\projectDMS`
**Source report:** `docs/architecture/arbitration_pleadings_langgraph_workflow_audit_and_implementation_plan_2026-07-21.md`
**Production code deployment:** Deployed 2026-07-23 at ContraClaim commit `8b57c619de9f8c66e1629be40f3c1cd8ad635f44`; projectDMS equivalent `24ee3d03abbc10f214805727eaae3ffce852ae8d`
**Authoritative rollout:** Existing `arbitration_v2`; rollout `off`; production acceptance remains false

## 2026-07-23 deployment and re-verification

- The former Phase-1 architecture blocker is implemented: `ArbitrationGraphCommandExecutor` now owns authoritative graph-node commands for evidence binding, bounded analysis, deterministic merge, plan construction, draft generation, eight validations, bounded remediation, approval commit, and filing-export creation. Effects are input-hash bound, leased, idempotent, retry-classified, and protected by run compare-and-set. The workflow service coordinates gates/APIs and checkpoint recovery; it no longer hides the entire authoritative workflow behind one legacy adapter.
- The implementation and additive acceptance-governance migration were deployed from committed source. Backend and filing-export worker were rebuilt from `8b57c619`; migration `20260722_0004` was dry-run and applied, leaving all 13 migrations current.
- Production passed real Redis duplicate/lease/visibility/restart/40-job load, real Mongo checkpoint TTL/resume and rolling failover, Qdrant stop/degraded/recovery, S3 put/get/delete, configured OpenAI PDF file-input, configured embedding/Qdrant, backup checksum, isolated restore, and namespaced cleanup drills.
- Deployment uncovered and corrected a fan-out reconstruction dependency, Mongo `nofile=1024`/ping-only health weakness, and an incompatible model file-input path. The final tests use an authoritative snapshot during live fan-out, Mongo `nofile=64000` with replica-state health, and Responses API `input_file` with a valid PDF.
- Production acceptance remains withheld. Exhaustive process termination at every node/gate, authenticated two-account browser flows, counsel acceptance for SoC/SoD/Counterclaim/Rejoinder, S3/model outage and sustained end-to-end load, tested application rollback, alert exercises, historical ambiguous-row review, and rotation of credentials exposed during an operator diagnostic remain incomplete.
- The detailed deployment record, criterion-by-criterion decision, rollback procedure, and unresolved-only action plan are in `docs/architecture/arbitration_langgraph_production_deployment_and_acceptance_2026-07-23.md`.

## Status legend

- **implemented / unit-tested** — code exists and relevant local automated tests passed.
- **implemented / integration pending** — code exists, but real infrastructure or restart behavior was unavailable.
- **partial** — a safe subset exists; listed acceptance work remains.
- **not tested** — no acceptance claim.

## Current assessment against the source report

- **Phase 0 implementation pending:** none identified in the current local source. P0-01 through P0-08 have code and negative unit-test coverage.
- **Phase 0 production-like acceptance:** migrations `20260721_0001`, `20260721_0002`, and corrective `20260722_0001` are applied and idempotent on production MongoDB; 99 production-image tests pass, including eight real-FastAPI two-tenant isolation tests. Live unauthenticated workflow/checkpoint/export probes return `401`. Authenticated browser testing with two real tenant accounts, real reviewer-role/step-up flows, and filing export artifacts remains pending, so Phase 0 is not fully production-accepted.
- **Phase 1 implementation and scoped acceptance completed on 2026-07-22:** engine contracts, the authoritative v2 adapter, immutable input/document/opponent/evidence/matrix snapshots, workflow/effect/approval/event/plan repositories, optimistic state versioning, atomic draft-version allocation, idempotency, and server-owned rollout policy are implemented. Atomic upserts and deterministic keyed run IDs now prevent concurrent duplicate workflow artifacts; draft-generation effects have one owner and reuse completed immutable output references.
- **Phase 2 implementation and local acceptance completed on 2026-07-22:** official `StateGraph` initialization is idempotent and recoverable after a checkpoint-before-run-update interruption; all durable human gates have an in-memory checkpoint progression test; checkpoint inputs fail closed on unknown, nested, raw, or oversized values; stale resume/approval/cancel/fallback requests are rejected before snapshots, receipts, or generation effects; resume routing uses exact gate names; cancellation is checkpointed without traversing downstream nodes; lagging checkpoints replay cumulative gate receipts and checkpoint outages persist a recoverable `pending` marker; and force-v2 preserves the current gate while binding the original immutable input snapshot. The case workspace now polls and renders the workflow event timeline. Full kill/restart-at-every-gate and TTL-expiry acceptance against production MongoDB remain pending.
- **Phase 3 implementation and local acceptance completed on 2026-07-22:** ten matrix/evidence analyses now have explicit typed matrix inputs and immutable non-authoritative branch artifacts; the artifact-set hash excludes random snapshot IDs and is identical for serial/parallel and equivalent-run execution; deterministic merge emits source-revision IDs, matrix-row revision IDs, and evidence status for every projected row; SoD and Rejoinder routes now require chronology; Counterclaim requires issue framing; immutable opponent versions include hashed paragraph parses; and any case, matrix, paragraph, permission, or selected-evidence dependency change invalidates downstream workflow receipts, advances the CAS version, and routes active post-review runs back to matrix review without deleting immutable artifacts.
- **Phase 4 implementation and local acceptance completed on 2026-07-22:** plans now include explicit issue, paragraph, claim, relief, section, and source-revision mappings with per-run versions and exact hashes; approved-plan generation remains a one-owner idempotent effect; eight official validation branches fan out into immutable branch/report/artifact-set snapshots bound to the exact candidate and dependency hashes; hard blockers route to human revision and cannot receive legal approval; legal review can rebind validation only to the latest immutable candidate; and the sole automatic remediation removes adjacent duplicate copies of the same existing citation while proving it added no source, amount, or date. The graph/state schema is now `phase4-v1`/`2`. Production-image, real-Mongo checkpoint compatibility/restart, load, and authenticated browser acceptance remain pending; LangGraph is not primary.
- **Phase 5 implementation and infrastructure drills substantially completed and re-verified on 2026-07-23:** the durable Redis filing-export queue has atomic leases, heartbeats, visibility recovery, transient-only bounded retry/dead-letter handling, deterministic effect keys, Mongo effect ownership, and idempotent completed-effect reuse. Production passed real-Redis duplicate/lease/recovery/load and restart, Qdrant degraded/recovery, signed-JWT two-account/step-up API, real-Mongo post-TTL resume/failover, isolated restore, S3 object, configured OpenAI PDF, configured embedding/Qdrant, and cleanup drills. Acceptance remains incomplete because there are no production arbitration samples for all four pleading types, authenticated browser/legal-review flows and stakeholder sign-off are absent, restart-at-every-node/gate is unproved, and S3/model outage plus sustained end-to-end load remain incomplete. `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false` and LangGraph is not primary.
- **Phase 6 cutover-control implementation is deployed; activation was re-evaluated and correctly withheld on 2026-07-23:** the runtime is `arbitration_v2`, rollout `off`, primary percentage `0`, and production acceptance `false`. No receipt was fabricated because the scoped health/sample/sign-off prerequisites are not met.
- **Phase 3 paragraph-position authority completed on 2026-07-22:** paragraph responses own draft-bound SoD/Rejoinder positions. Legacy defence/rejoinder collections receive server-owned, reviewable, read-only projections; the rejoinder LLM updates the authoritative response before projecting it; direct draft-bound duplicate creation and projection content edits return `409`.
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
| Phase 1 engine interface and v2 adapter | implemented / production-image and Mongo-tested | `engines/base.py`, `engines/v2.py`, selector | `20260721_0002` | Server-only selection; deterministic keyed run IDs; stable v2 state/hashes from identical immutable inputs; concurrent create returns one run and one snapshot set |
| Snapshots, effects, approvals, events and plans | implemented / production-image and Mongo-tested | workflow repository/domain/service | workflow/index migration | Atomic upserts, exact input-hash conflicts, one-owner effects, completed-output reuse, optimistic CAS, approval/plan dedupe; 12-way Mongo race stored one row of each kind |
| Atomic version allocation and lineage | implemented / production-image and Mongo-tested | arbitration repository/service | counter seed and unique draft/version index | Twelve concurrent real-Mongo allocations advanced uniquely from existing version 7 to versions 8-19 |
| Official in-process LangGraph | implemented / local gate-sequence and production restart-tested | `langgraph_engine.py` | checkpoint/write indexes and TTL | Core graph and optional Mongo capability are isolated; checkpoint state maps `_id` to `run_id`; duplicate create/recovery is idempotent; every human interrupt progresses under `InMemorySaver`; production document-selection interrupt survived backend restart and resumed from Mongo |
| Durable workflow APIs and gates | implemented / local and production-image tested | models, router, workflow service, API client | workflow indexes | create `202`, state/events/resume/cancel/approve/fallback/checkpoints; exact gate routing; pre-side-effect stale CAS; terminal-state guards; unknown question IDs rejected; authenticated browser pending |
| Pleading-specific routing | implemented / unit-tested | `workflow_domain.py`, v2/graph engines | opponent and matrix revision snapshots | SoC/SoD/Counterclaim/Rejoinder requirements; SoD/Rejoinder require immutable opponent versions with hashed paragraph parses; all routes require chronology and Counterclaim requires issue framing |
| Parallel analysis and deterministic merge | implemented / unit-tested | workflow domain, repository and graph | immutable branch/artifact-set/matrix snapshots | Ten typed read-only branches; serial/parallel and equivalent-run hashes match; branch snapshot IDs remain separate audit references |
| Source/matrix drift invalidation | implemented / unit-tested | case workspace, workflow repository/service | existing approval/event/run collections | Dependency edits invalidate matrix/readiness/plan/legal/draft/export receipts, increment state version, retain immutable artifacts, and rewind active downstream runs to matrix review |
| Material user questions | implemented / unit-tested | workflow domain/service, graph, API/UI | question and direction snapshots | Missing required artifacts pause before matrix approval; required answers are exact-ID checked and snapshotted |
| Pleading strategy artifact | implemented / unit-tested | model, workflow domain/repository/service/UI | plan unique indexes | Versioned plan contains required strategy/source fields and exact plan hash; plan approval gates generation |
| Node-owned plan, draft, validation and remediation graph | implemented / unit and production-infrastructure tested; full acceptance pending | `graph_commands.py`, `workflow_domain.py`, `workflow_service.py`, `workflow_validation.py`, `langgraph_engine.py`, model/API/UI | existing plan/effect/snapshot collections; graph schema `phase6-node-owned-v1`, state schema `2` | Versioned plan mappings; idempotent candidate generation; eight validation branches and immutable report/artifact set; blocker-enforced legal gate; latest-candidate revalidation; bounded citation dedupe with parent lineage and no-new-source/value proof |
| Reviewer-role matrix and separation | implemented / unit-tested | `approval_policy.py`, config, case/draft/workflow services | approval receipt indexes | Gate roles configurable; route permissions plus semantic roles; author/last-material-editor cannot approve readiness/plan/draft/export |
| Paragraph response authority and immutable pleading import | implemented / unit-tested; historical backfill pending | model/repository/service/router/UI, `paragraph_positions.py` | compatible document fields; legacy backfill not yet added | Paragraph response is authoritative; draft-bound defence/rejoinder rows are reviewable read-only projections; direct duplicates/edits are rejected; denial requires a reason; sources are scoped; unversioned imports block approval |
| Frontend workflow integration | implemented / unit/build-tested; browser pending | case workspace, drafting page, API client | none | Progress/current node/blockers/questions/gates/cancel/opponent version/paragraph editor/preview/filing controls, validation blockers/remediation state, latest-candidate revalidation, and polled workflow event timeline; authenticated browser pending |
| Shadow and canary controls | source complete / locally tested; production acceptance pending | selector, official graph shadow, workflow hardening/service, config, metrics | immutable shadow snapshots/events | Deterministic buckets and allowlists; global pause and tenant/project force-v2 overrides; official isolated graph execution; eight redacted parity dimensions; threshold health report; `authoritative_writes=false` |
| Durable filing-export delivery | implemented / production Redis-tested | `filing_export_queue.py`, case workspace, worker, config, Compose | `20260722_0003` unique effect and lease/status indexes | Atomic acquire/heartbeat/ack/fail/recover; visibility timeout; bounded dead letter; deterministic export/effect IDs; 40-job/4-worker load, duplicate delivery, expired lease recovery, Redis outage and actual stop/restart passed without duplicate effects |
| Failure recovery and operations | partial / production infrastructure-tested | checkpoint engine, filing export queue, health/checkpoint routes, metrics, runbook | checkpoint TTL plus export effect indexes | Redis kill/recovery/load, Mongo TTL/resume/failover/restore, Qdrant outage/recovery, S3 and configured model round trips, signed-JWT tenant/step-up tests, and exact cleanup passed. Every-node restart, browser/legal acceptance, provider-outage/load completion, all-pleading samples, and stakeholder receipt remain open |

## Production-like acceptance status (2026-07-23)

| Gate | Status | Evidence / remaining work |
|---|---|---|
| Migration | **passed** | Arbitration acceptance-governance migration `20260722_0004` was dry-run and applied; all 13 repository migrations are current. Runtime-compatible checkpoint/write TTL indexes are present at 30 days; filing-export effect and lease indexes are present |
| Authorization | **partial** | Ten production-image signed-JWT tests passed with two distinct tenant accounts, cross-tenant denials, real user lookup and step-up verification. A real signed-in browser and legal reviewer exercise remains |
| Restart and durable resume | **partial** | Backend/worker restart, Mongo failover, selected checkpoint replay, actual TTL deletion, and post-TTL resume passed. The required pre/during/post-effect process kill at every graph node and gate remains incomplete |
| Tenant isolation | **passed for API fixture / browser pending** | Ten signed-JWT production-image tests use separate organization/project memberships and deny cross-account access; browser acceptance remains open |
| Redis export durability | **passed** | Real Redis duplicate, active lease, expired visibility recovery, exactly-once effect, 40-job/4-worker load, outage health, actual Redis stop/restart persistence, and exact cleanup passed |
| External outage and integration | **partial / blocked** | Qdrant actual outage/degraded/recovery and embedding/vector round trip passed; S3 put/get/delete passed; configured OpenAI PDF extraction passed. Actual S3-provider and model timeout/rate-limit/outage plus sustained cross-service load remain unproved. The separate FalkorDB vector adapter still expects unavailable `FT.*` commands; arbitration acceptance uses Qdrant and does not count that adapter as a substitute |
| Checkpoint TTL | **passed for exercised path** | Both checkpoint collections have 30-day `created_at_1` TTL; an expired terminal marker was deleted and an active workflow resumed after TTL. Every-node expiry/restart coverage remains open |
| Isolated restore and cleanup | **passed** | Backup `20260723-001500-node-owned-predeploy` passed checksums and restored into an isolated database after Mongo descriptor/health hardening. Live/restored collection, migration, sampled-document and checkpoint-index counts were compared; the exact isolated database and namespaced Redis/Qdrant/S3 artifacts were removed |
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
| Phase 1 production-image regression after atomic idempotency hardening | 105 passed, 231 warnings | disposable production-image acceptance |
| Phase 1 temporary Mongo replica-set concurrency probe | 12 concurrent callers produced one run/snapshot/effect/event/approval/plan; one effect owner; allocated versions 8-19 uniquely | real Mongo contention acceptance; temporary database removed |
| Phase 2 focused durable-gate and recovery regression | 10 passed | local official StateGraph/InMemorySaver plus service-unit acceptance |
| Phase 2 full arbitration backend regression | 97 passed, 57 warnings | local backend regression |
| Phase 3 focused fan-out/merge/opponent-parse/drift regression | 5 passed, 92 deselected | local workflow-domain and repository acceptance |
| Phase 3 full arbitration backend regression | 97 passed, 57 warnings | local backend regression |
| Phase 4 focused plan/validation/remediation/revision regression | 4 passed, 97 deselected | local workflow and safety acceptance |
| Phase 4 full arbitration backend regression | 101 passed, 57 warnings | local backend regression |
| Phase 4 affected frontend component regression | 2 files / 10 tests passed | local workflow UI acceptance |
| Phase 4 production client build | passed; 3,881 modules transformed | local production build |
| Phase 5 focused selector/shadow/health/metrics regression | 12 passed, 100 deselected | local source hardening acceptance |
| Phase 5 arbitration drafting plus observability regression | 112 passed, 57 warnings | local backend regression |
| Phase 5 configuration and deployment-config regression | 21 passed | local settings/production guard acceptance |
| Phase 5 case-scoped operations HTTP isolation test | skipped: local real-app fixture unavailable because the separate letter runtime lacks the MongoDB checkpoint plugin | production-image rerun required |
| Synthetic Mongo checkpoint interrupt, backend restart, resume | paused at document selection; resumed to completed with a new checkpoint ID | production restart acceptance |
| Live unauthenticated arbitration route probes | case/workflow/checkpoint/export all `401` | production perimeter authorization |
| Full production backup `20260722-015547` | MongoDB plus five volume archives all passed SHA-256 verification | production rollback evidence |
| `npm test -- --run src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx src/pages/__tests__/ArbitrationDraftingPage.test.tsx` | 2 files / 10 tests passed | frontend component, including workflow event timeline |
| `npm run build` from `client` | passed; 3,881 modules transformed | production client build |
| Python `compileall` over changed backend/migrations | passed | syntax/import compilation |
| LangGraph runtime probe | LangGraph `1.2.9`; MongoDB saver `0.4.0`; runtime available | production dependency parity |
| Filing-export focused backend regression | 167 focused tests passed locally; production-image Redis/HTTP subset passed | queue, workflow, authorization and migration regression |
| Real Redis filing-export integration | duplicate enqueue, active/expired lease behavior, exactly-once effects, 40 jobs on 4 workers, outage and cleanup passed | production Redis acceptance |
| Actual Redis container stop/restart | readiness failed closed for Redis, persisted probes survived, worker retried, readiness recovered, exact probes deleted | production crash/recovery drill |
| Signed-JWT two-account/step-up suite | 10 passed | production-image authorization and tenant isolation |
| Real Mongo TTL drill | passed in 36.85 seconds | terminal expiry plus active post-TTL resume; namespaced teardown |
| Isolated restore `20260722-171932` | 13,047 documents restored, 0 failed; disposable container and volume removed | production backup/restore drill |
| Qdrant outage/recovery | backend remained ready with explicit `degraded`; returned to `ok` after restart | production external partial-outage drill |
| Qdrant live vector round trip | passed | live external integration |
| OpenAI live PDF round trip | passed using Responses API `input_file`, `user_data` upload and a valid generated PDF | configured production model integration |
| S3 live object round trip | passed put/get/delete with a namespaced key and cleanup | configured production object-storage integration; provider outage remains pending |
| FalkorDB vector round trip | separate adapter still lacks the RediSearch `FT.*` commands expected by `FalkorDBVectorService`; arbitration's configured Qdrant path passed | non-acceptance architecture mismatch requiring separate ownership decision |

The first frontend test command used incorrect file paths and returned “No test files found”; it was corrected to the `src/pages/__tests__` paths and passed.
The repository `.venv` could not collect the HTTP tests because it lacks FastAPI. The production backend image supplied dependency parity and completed the combined 99-test suite.
For the Phase 2 gap-closure run, the host Python could not collect route-inventory/real-FastAPI tests because the separate letter runtime imports the unavailable MongoDB checkpoint plugin, and the local Docker daemon was not running. The new arbitration suite still exercised core StateGraph behavior with `InMemorySaver`; production-image and production-Mongo acceptance remain explicitly pending.

## API changes

- `POST /api/arbitration/cases/{case_id}/workflows` (`202 Accepted`, idempotency key supported)
- `GET /api/arbitration/cases/{case_id}/workflows/{run_id}/state`
- `GET /api/arbitration/cases/{case_id}/workflows/{run_id}/events`
- `POST /api/arbitration/cases/{case_id}/workflows/{run_id}/resume`
- `POST /api/arbitration/cases/{case_id}/workflows/{run_id}/cancel`
- `POST /api/arbitration/cases/{case_id}/workflows/{run_id}/approvals/{gate}`
- `POST /api/arbitration/cases/{case_id}/workflows/{run_id}/force-v2` (admin plus step-up)
- `GET /api/arbitration/cases/{case_id}/workflows/operations/health` (case-scoped admin, identifier-free rollout signals/alerts)
- `GET /api/arbitration/operations/workflows/{run_id}/checkpoints` (admin, redacted)
- `PATCH /api/arbitration/drafts/{draft_id}/paragraph-responses/{response_id}`
- Separate preview and governed filing export endpoints; dedicated rejoinder permission endpoint.

## Migrations and rollback

- `v20260721_0001_arbitration_phase0_containment.py`: removes unverifiable historical approval/readiness semantics, corrects rejoinder permission data, returns unsafe standalone statuses to review, and adds containment indexes.
- `v20260721_0002_arbitration_workflow_foundation.py`: seeds version counters and adds workflow, snapshot, effect, approval, event, plan, checkpoint TTL, immutable version, and exact export indexes.
- `v20260722_0001_langgraph_checkpoint_ttl_compatibility.py`: replaces the custom checkpoint TTL index name with the runtime-compatible default and adds the same 30-day TTL to checkpoint writes. This corrected a production-discovered Mongo code-85 collision during saver initialization.
- `v20260722_0002_arbitration_phase6_scope_index.py`: adds the bounded tenant/project/time query index required by scoped cutover health.
- `v20260722_0003_arbitration_filing_export_effects.py`: adds the unique export-effect key and status/lease indexes used by durable export ownership and recovery.
- P0/P1 downgrades drop their new indexes and intentionally do not recreate unverifiable approvals or invented identities/timestamps. The TTL compatibility downgrade restores the prior named checkpoint TTL index and removes the write TTL index.
- Operational rollback is configuration-first: set the rollout pause, rollout mode to `forced_v2`, and default to `arbitration_v2`; preserve graph artifacts for audit. See the operations runbook.

## Unresolved risks and deviations

- Redis job kill/retry, bounded queue load, Qdrant outage/recovery, TTL expiry, isolated restore, and signed-JWT two-account/step-up API acceptance are complete. The signed-JWT fixture is not a substitute for a live signed-in browser and legal-review exercise.
- Live OpenAI file-input extraction, S3 object round trip, and Qdrant embedding/vector integration pass. The separate FalkorDB vector adapter still expects unavailable RediSearch commands. S3 failure/recovery, live model timeout/rate-limit outage, and sustained cross-service capacity remain required.
- New draft-bound editable duplication is closed: paragraph responses materialize server-owned defence/rejoinder projections, and projection content cannot be changed through generic matrix APIs. Historical legacy rows remain unmigrated where a paragraph-response mapping is ambiguous; a data audit and conservative backfill are still required.
- The frontend exposes immutable opponent draft/version IDs and document IDs, but a rich page-level document preview/selector and visual version diff remain partial; evidence snippets and version history remain available in their existing views.
- Counterclaim routing and mandatory matrix evidence are implemented; the deterministic counterclaim agent remains conservative and will not infer an independent cause of action without reviewed inputs.
- Phase 3 analysis artifacts explicitly mark unversioned or absent source references as `needs_review`/`missing`; historical matrix data is not silently upgraded, so production data quality review and conservative source-version backfill remain necessary.
- Typed fan-out and deterministic merge are locally covered, but production-like bounded-concurrency/load tests against MongoDB, Qdrant, S3, and live model limits remain part of Phase 5 acceptance.
- Graph schema `phase6-node-owned-v1` has official Mongo checkpoint TTL/resume and selected recovery coverage. Restart at every plan/draft/validation/remediation node timing boundary, sustained bounded-load behavior, and authenticated legal-review/revision browser flows remain acceptance work.
- Phase 5 infrastructure evidence now covers Redis duplicate/crash recovery, bounded queue load, a Qdrant partial outage, signed-JWT two-account/step-up API tests, TTL expiry/resume, restore, and cleanup. It does not cover every-node Mongo restart, authenticated browser/legal flows, S3/model outages or sustained end-to-end concurrency.
- Phase 6 source controls are present, but cutover is not accepted: no acceptance receipt should be configured and no primary scope should be enabled until the Phase-5 evidence matrix, operator recovery proof, and legal/security sign-off are complete.
- Primary rollout and removal of v2 were deliberately not performed. `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false` is the fail-safe.
- Phase 2 code now enforces safe checkpoint and gate semantics and real Mongo TTL/resume/failover paths passed, but its source-report acceptance statement is not yet fully satisfied because process termination was not exercised before, during, and after every graph node and human gate.

## Next action

Rotate the credentials exposed during the deployment diagnostic, then complete the isolated every-node/gate kill matrix, authenticated two-account browser flows, reviewed SoC/SoD/Counterclaim/Rejoinder legal-acceptance cases, remaining S3/model outage and sustained end-to-end load tests, tested application rollback, monitoring alert exercises, and conservative historical-row review. Only after all 14 criteria pass may an authorized operator create the immutable operator/security/legal receipt and consider a bounded primary scope. Keep `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false`; do not enable primary before those gates pass.
