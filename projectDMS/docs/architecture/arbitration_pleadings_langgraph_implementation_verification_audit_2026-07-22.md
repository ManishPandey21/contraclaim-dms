# Arbitration Pleadings LangGraph Implementation Verification Audit

> **2026-07-23 production re-verification addendum:** the P1 architecture item below has been implemented: authoritative evidence, analysis, merge, planning, drafting, validation, remediation, approval-commit, and export-creation commands are now owned by typed LangGraph node executors in `graph_commands.py` with immutable input hashes, leases, idempotent effect keys, retry classification, and run compare-and-set. Production infrastructure drills substantially passed, including Redis restart/duplicate/load, real-Mongo TTL resume/failover/isolated restore, Qdrant outage/recovery, S3 put/get/delete, and configured model/PDF and embedding/vector round trips. The current verdict remains **Implemented but not production accepted** because exhaustive every-node/gate kill recovery, authenticated browser acceptance, four-type legal sign-off, remaining external-outage/load tests, credential rotation, and tested code rollback are incomplete. See `docs/architecture/arbitration_langgraph_production_deployment_and_acceptance_2026-07-23.md`, which supersedes stale environment and action statuses in this retained historical audit.

**Audit date:** 2026-07-22
**Audited plan:** `docs/architecture/arbitration_pleadings_langgraph_workflow_audit_and_implementation_plan_2026-07-21.md`
**Repository working copy:** `C:\SaaS\projectDMS` at `ca712e2`
**Production deployment inspected read-only:** `410ae3e`
**Scope:** Baseline audit followed by an authorized corrective implementation and local re-verification. No production data, deployment, rollout scope, or production-acceptance switch was changed.

## Post-implementation re-verification — 2026-07-22

This addendum supersedes the baseline verdict and defect status recorded below. The original evidence is retained to show what was found before remediation. The current verdict is **Implemented but not production accepted**.

The P0 filing/approval/provenance bypasses found by the audit are now closed in the local implementation: matrix specialties are resolved from signed roles or server assignments with distinct-actor enforcement; case-linked generation, approval and export require the governed exact-hash receipt chain; every approved source is authoritatively rehydrated in case scope; and queued exports bind and recheck a complete immutable manifest. The implementation also adds pending/committed receipt recovery, conditional SoD/Counterclaim routing, substantive bounded evidence projections, semantic assertion/entity/clause controls, terminal cancellation, post-TTL checkpoint reconstruction, signed scope-bound acceptance receipts, active-run UI restoration, independent read-only shadow projection, and operational metrics/dashboard/alert assets.

Primary rollout remains prohibited. The official graph now performs valid bounded fan-out and cancellation routing, but domain commands are still invoked by `ArbitrationWorkflowService` before graph checkpoint synchronization rather than being fully owned by async graph nodes. More importantly, the required isolated real-Mongo every-node kill matrix, real Redis crash/outage/load drill, active-checkpoint TTL deletion against Mongo, Qdrant/object-storage/model outage tests, signed-in two-account browser flows, restore drill, and stakeholder legal sign-off were not available in this local environment. The formal acceptance receipt and selector fail closed until that evidence exists.

### Corrective action verification matrix

| Action-plan item | Current status | Implementation evidence | Re-verification | Remaining risk |
| --- | --- | --- | --- | --- |
| P0 reviewer-role and actor separation | Partially Verified | `approval_policy.py`: `enforce_matrix_reviewer_role`, `resolve_gate_role`; `case_workspace.py`: `review_matrix_row` | Same actor cannot approve legal and quantum specialties; weakened required-role payload rejected by server logic | Signed two-account JWT positive flow still requires isolated browser/API execution |
| P0 governed generation/approval/export | Partially Verified | `workflow_governance.py`; `service.py`: `generate`, `approve`, `export`; `case_workspace.py`: `_immutable_bundle_manifest` | Direct case-linked generation without an approved plan returns 409; exact committed gate receipts are required | Full signed HTTP success/prohibited matrix not rerun with two real accounts |
| P0 authoritative source rehydration | Verified | `case_workspace.py`: `_authoritative_evidence_manifest`, `_readiness_artifact_state`; `context.py`: `_document_index_sources` | Missing approved source blocks readiness; drift invalidates current approval | Real object-store deletion/outage remains unexercised |
| Immutable export effect and manifest | Verified | `case_workspace.py`: `_authorize_filing_bundle`, `execute_filing_bundle_export_job`, `_render_filing_bundle_zip` | Queue-then-matrix drift fails closed; effect key remains content-bound | Real Redis duplicate/crash drill remains pending |
| Graph-owned domain commands | Partially Verified | `langgraph_engine.py`: official fan-out, artifact-bound nodes, CAS checkpoint sync | Graph compiles and walks every gate; parallel fan-out collision fixed | Domain commands still execute in workflow service before checkpoint sync; every-node process-kill proof absent |
| Evidence analysis and pleading routing | Verified | `workflow_domain.py`: ten analysis branches, scoped document signals, opponent bindings, `_fanout_slot`; route maps | Serial/parallel hashes match; SoD without counterclaim and Counterclaim route execute distinctly | Analysis quality still needs stakeholder golden-corpus sign-off |
| Approval pending/commit recovery | Verified | `workflow_repository.py`: `record_approval`, `commit_approval`, `reconcile_pending_approvals` | Only a pending receipt referenced by a successful run CAS is finalized; orphan stays pending | Real Mongo process death at each boundary remains pending |
| Semantic validation/remediation controls | Partially Verified | `validator.py`; `workflow_validation.py` | Unsupported factual assertion, entity, amount, date and clause cases block; remediation preserves closed-world values | Deterministic sentence heuristics do not yet constitute a full structured claim-to-source span model |
| Cancellation and post-TTL resume | Partially Verified | `langgraph_engine.py`: `continue_or_cancel`, `cancelled`, `_recovery_state`, missing-checkpoint reconstruction | Cancellation ends with no queued node; empty saver reconstructs the next gate using IDs/hashes only | Active checkpoint deletion/restart against real Mongo remains pending |
| Formal production acceptance | Partially Verified | `acceptance.py`; `ArbitrationProductionAcceptanceRequest`; acceptance API and migration; `engines/policy.py` | Forged, expired and wrong-scope receipts are rejected; all 14 evidence hashes/four routes required | Independent stakeholder endorsement/browser evidence and production-like evidence corpus do not exist yet |
| Frontend active-run recovery | Verified | workflow list API/repository; `ArbitrationCaseWorkspacePage.tsx`; component test | Reload restores latest scoped active run and durable timeline | Signed-in browser E2E remains pending |
| Independent shadow and Section-14 telemetry | Verified | `shadow_candidate.py`; `observability.py`; `config/monitoring/*`; operations runbook | Shadow candidate makes no authoritative writes; metrics render; dashboard JSON validates | Dashboard/rules are deployable assets but were not provisioned or alert-fired here |
| Four-type golden acceptance | Partially Verified | updated construction fixture plus SoD/Counterclaim/Rejoinder route tests | All four route records exist and route-specific predecessor behavior executes locally | No signed-in browser export or stakeholder legal/grounding sign-off |

### Current environment suitability

| Intended environment/use | Current decision |
| --- | --- |
| Local development | Suitable |
| Test environment | Suitable with fail-closed rollout defaults |
| Isolated production-like testing | Suitable for the outstanding acceptance drills |
| Limited canary filing use | Not accepted |
| Primary production use | Not accepted |

### Current global acceptance criteria

| # | Criterion | Current evaluation | Reason |
| ---: | --- | --- | --- |
| 1 | Phase-0 bypasses closed | Passed locally | New server controls and negative service/component tests pass; two-account signed browser confirmation remains operational evidence |
| 2 | Immutable authoritative snapshots/hashes | Passed locally | Workflow inputs, evidence, opponents, matrices, plans, drafts and export manifests are hash-bound |
| 3 | No raw legal content in checkpoints/ops | Passed | Allowlist, size validation and redaction tests pass |
| 4 | Every-node/gate crash recovery without duplicates | Not tested | Requires isolated real Mongo process-kill harness |
| 5 | Four routes enforce required gates and predecessors | Passed locally | Route-specific local workflow tests pass |
| 6 | Rejoinder new-matter permission receipt | Not tested end to end | Mechanics exist; signed counsel flow remains pending |
| 7 | Export bound to immutable approved/current artifacts | Passed locally | Governed chain plus complete immutable manifest and drift test pass |
| 8 | No client/edit/generate self-approval | Passed locally | Semantic roles, server-controlled required roles, separation and route permissions fail closed |
| 9 | Deterministic auditable parallel merge | Passed locally | Serial/parallel artifact-set hashes match; source revisions persist |
| 10 | Bounded retry/recovery/no duplicate effects | Not tested on real infrastructure | Unit queue/effect tests pass, but Redis/Mongo crash acceptance is outstanding |
| 11 | Safe v2 fallback/replay | Not tested in production-like recovery | v2 remains available and no deprecation was made |
| 12 | Four golden pleadings pass legal/grounding review | Not tested | Route fixtures are not stakeholder acceptance evidence |
| 13 | Real dependency/load/restore/browser drills | Blocked by environment | No destructive production action was authorized; isolated acceptance environment was unavailable |
| 14 | Runbooks, metrics, dashboards and alerts | Passed locally | Runbook and deployable monitoring assets cover the required signals |

The production decision is unchanged: do not enable `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED`, do not start or expand authoritative canary filing use, do not make LangGraph primary, do not deprecate `arbitration_v2`, and do not represent filing export as production accepted until criteria 4, 6, 10, 11, 12 and 13 have retained evidence.

### Corrective re-verification commands and results

| Command | Result |
| --- | --- |
| `python -m pytest backend/rbac_backend/tests/test_arbitration_drafting.py -q` | 124 passed; 57 deprecation warnings |
| `python -m pytest backend/rbac_backend/tests/test_arbitration_http_isolation.py backend/rbac_backend/tests/test_filing_export_queue.py -q -x` | 14 passed; 159 warnings |
| `python -m pytest backend/rbac_backend/tests/test_migration_runner.py -q -x` | 11 passed |
| `python -m pytest backend/rbac_backend/tests/test_observability.py -q -x` | 2 passed |
| `npx vitest run src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx` | 5 passed |
| `npm run build` | Passed; 3,881 modules transformed |
| `python -m compileall -q ...` and `python -m json.tool config/monitoring/arbitration-langgraph-dashboard.json` | Passed |

No real-Redis integration, destructive dependency outage, production data mutation, deployment, browser login, rollout change or acceptance-switch change was performed. Those are intentionally recorded as unresolved acceptance evidence rather than converted into mock-based passes.

## 1. Baseline Executive Verdict (superseded by the re-verification addendum)

### Baseline verdict before corrective implementation: Materially incomplete or unsafe

The implementation contains substantial Phase 0-6 scaffolding and several sound controls: server-selected engines, immutable hashed workflow records, effect keys, optimistic run transitions, official `StateGraph`, a MongoDB checkpointer, redacted checkpoint operations, versioned plans and drafts, bounded remediation, Redis export leases, and fail-closed rollout configuration. Those controls are not sufficient to accept the workflow as implemented end to end.

Three independently reproduced P0 defects prevent production use:

1. The active matrix-review API trusts the caller-supplied semantic `reviewer_role`. One user with `ARBITRATION_APPROVE` can submit the same row as `legal` and then `quantum` and satisfy both required roles. The frontend also presents and sends that claimed role. This defeats role-specific review governance.
2. Active standalone draft approval/export routes remain reachable from the production router and frontend. A controlled signed-JWT API test exported an approved immutable draft with a standalone readiness receipt but no workflow run, approved pleading-plan receipt, legal-review receipt, draft-approval gate receipt, or export-authorization gate receipt.
3. Approved matrix evidence is not uniformly rehydrated from an authoritative tenant/project-scoped source. A document row can be readiness-eligible from identifiers and approval state alone; a missing authoritative record produces an empty manifest entry instead of a failure. Draft context subsequently copies matrix snippets into the source ledger.

The official graph is also not the durable orchestration owner described by the plan. Its analysis nodes set completion booleans and its plan/draft nodes assert artifact IDs already created by `ArbitrationWorkflowService`. Most domain side effects occur outside the graph/checkpoint transaction boundary. Recovery, every-node replay, cancellation, and Phase-3 evidence fan-out therefore remain materially incomplete.

| Intended environment/use | Decision | Basis |
| --- | --- | --- |
| Local development | Suitable with warnings | Unit/component development is possible, but P0 paths must not be treated as governed filing workflow. |
| Test environment | Suitable for corrective testing | Fail-closed engine defaults and isolated test databases are required. |
| Production-like testing | Suitable only for non-authoritative drills | No real filing, approval, or production data; unresolved authorization/provenance defects must be contained. |
| Limited canary use | Not suitable | P0 bypasses and recovery gaps violate the plan's global acceptance gates. |
| Primary production use | Not suitable | Production has no acceptance samples and correctly remains v2/off/acceptance-false. |

## 2. Baseline Phase Verification Matrix

Statuses in this matrix are limited to the requested vocabulary. “Implemented but Not Exercised” means the code exists but the required real-infrastructure or end-to-end behavior was not demonstrated in this audit.

### Phase 0 — Containment and trustworthy baseline

| Requirement | Status | Evidence | Test performed | Result | Remaining risk |
| --- | --- | --- | --- | --- | --- |
| Manual and agent matrix rows default to needs-review, not approved/ready/verified | Verified | `models/arbitration_drafting.py:13-26,323-333`; `case_workspace.py:136-170,330,372` | Model/service tests plus signed-JWT create request | Privileged create payload returned 422; ordinary row remains `needs_review` | None identified for defaulting |
| Generic create/update APIs cannot modify privileged lifecycle fields | Verified | `models/arbitration_drafting.py:323-353`; `case_workspace.py:136-153,330,372` | Edit/generate signed-JWT POST/PATCH attempts | 422 for controlled fields | Review endpoints remain separately defective |
| `auto_approve` and equivalents are rejected | Verified | `models/arbitration_drafting.py:454-461` | Unit test and signed-JWT API request | 422 / validator exception | Unknown aliases still require schema discipline |
| Only authorized review endpoints approve rows | Partially Verified | Router requires approve permission; `case_workspace.py:402-558` performs state changes | Edit/generate direct review calls | Both received 403 | Semantic role is caller-controlled, so authorized approver can impersonate required specialties |
| Required reviewer roles are server-enforced | Failed | `case_workspace.py:417,438,465`; no `enforce_gate_role`; compare workflow path `workflow_service.py:742` | Same signed-JWT approver posted `legal`, then `quantum` | Same actor completed both roles and row became approved | P0 separation/competency bypass |
| Draft preparation copies only approved matrix revisions | Verified | `case_workspace.py:192-210,2418-2510`; `engines/v2.py:89-105` | Focused unit tests | Unapproved rows excluded | Approved-but-unproven source can still enter snapshot |
| Every selected source is authoritatively tenant-scoped and rehydrated | Failed | Direct references rehydrate in `context.py:202-247,333-408`; matrix sources are copied in `context.py:610-665`; manifest tolerates missing record in `case_workspace.py:915-955` | Source-path trace and missing-record scenario inspection | Selected-reference path is sound; approved matrix-source path is not | P0 provenance/tenant grounding defect |
| Readiness approval binds exact matrix revision-set hash | Verified | `case_workspace.py:668-710,712-778`; workflow uses `readiness_artifact_hash` | Unit tests and source trace | Exact hash stored and compared | Receipt write ordering is non-atomic |
| Source/matrix change invalidates stale readiness | Verified | `case_workspace.py:174-190,330-397,653-666`; `workflow_repository.py:248-314` | Unit tests | Case/workflow downstream fields invalidated | Cross-collection transaction is absent |
| Standalone approval/export blocked unless audited exception exists | Failed | Active routes `routers/arbitration_drafting.py:878-968`; service checks `service.py:563-697` | Controlled export-only signed-JWT request with no workflow receipts | PDF returned 200 and filing authorization was created | P0 governed-workflow bypass; no audited exception was required |
| Export requires approved immutable version, current readiness, citation/exhibit audit | Partially Verified | `service.py:629-697`; case bundle path `case_workspace.py:1738-1831` | Unit tests and direct export probe | These local conditions are checked | Plan/legal/export workflow receipts and full bundle manifest are not required |
| Rejoinder fields separately capture new matter and permission state/evidence/approval | Partially Verified | `models/arbitration_drafting.py:26,356-370`; `case_workspace.py:560-653` | Unit tests/source trace | Fields and permission receipt exist | Semantic reviewer role not enforced; receipt precedes row update |
| Section regeneration merges complete parent and preserves lineage | Verified | `service.py:482-560`; immutable version allocation in `repository.py:112-135` | Focused unit tests | Unaffected sections and parent lineage preserved | No production exercise |
| Negative authorization tests cover every former bypass | Failed | `test_arbitration_http_isolation.py`; `test_arbitration_drafting.py` | Inspected assertions and added no code | Edit/generate denials covered, but same-approver multi-role and workflow-less export bypass were not caught | Regression coverage incomplete |

### Phase 1 — Engine abstraction, snapshots, and effect ledger

| Requirement | Status | Evidence | Test performed | Result | Remaining risk |
| --- | --- | --- | --- | --- | --- |
| `ArbitrationWorkflowEngine` interface exists | Verified | `engines/base.py:19-58` | Import/source trace | Protocol implemented | Interface does not force graph-owned side-effect semantics |
| Controlled `arbitration_v2` fallback remains available | Verified | `engines/v2.py`; `engines/policy.py`; `workflow_service.py:1130-1176` | Policy tests and production config inspection | v2 active; production default v2 | Retain until acceptance evidence exists |
| Run, snapshot, effect, approval, event, and plan repositories exist | Verified | `workflow_repository.py:28-349`; migration `v20260721_0002...py:13-29` | Migration tests and live index inspection | Collections/indexes present | Empty production collections provide no operational proof |
| Input/evidence/opponent/matrix/plan/draft artifacts are immutable and hash-bound | Partially Verified | `workflow_repository.py:92-111`; `workflow_domain.py:183-229,338-359`; `service.py` immutable versions | Unit tests/source trace | New workflow artifacts are hash-bound | Matrix provenance defect and direct legacy path weaken the invariant |
| Effect keys prevent duplicate rows/candidates/versions/approvals/exports | Partially Verified | `workflow_repository.py:118-180,319-349`; migration unique indexes; queue effect keys | Unit/concurrency tests | Repository-level idempotency works | Receipt-before-transition and export TOCTOU can orphan/misbind effects |
| State updates use CAS/optimistic concurrency | Verified | `workflow_repository.py:182-211,298-314` | Concurrent/stale state tests | Stale update returns 409 | Domain work may occur before final CAS |
| Draft version allocation is atomic | Verified | `repository.py:112-135`; unique counter/version indexes | Concurrent allocation test | Unique versions allocated | Production concurrency not exercised |
| Rollout policy supports off/shadow/canary/primary/forced-v2 | Verified | `engines/policy.py`; `core/config.py:210-247,560-621` | Config/policy tests and production config | Modes exist and fail closed | Acceptance receipt validation is syntactic, not persisted evidence |
| Client cannot select authoritative engine | Verified | Router request models omit authoritative engine; server selector in `workflow_service.py` | HTTP tests/source trace | Client hint ignored/not accepted | Admin force-v2 remains privileged path |
| Same idempotency key creates no duplicate authoritative effect | Verified | Unique run/effect indexes and repository handling | Unit and concurrent tests | Duplicate request returns same run/effect | Full crash-after-effect boundary not tested |

### Phase 2 — LangGraph skeleton and durable human gates

| Requirement | Status | Evidence | Test performed | Result | Remaining risk |
| --- | --- | --- | --- | --- | --- |
| Official `StateGraph` is used | Verified | `langgraph_engine.py:25,228-317` | Import/build tests | Official graph compiles | Graph is primarily a marker/checkpoint projection |
| Durable MongoDB checkpointer is used | Verified | `langgraph_engine.py:38,319-336`; production indexes | Local saver tests/live index inspection | Mongo saver configured | No production workflow checkpoint exists |
| Checkpoints contain IDs/hashes/counters/status/operational metadata only | Verified | allowlist and validator `langgraph_engine.py:45-156`; ops redaction | Unit redaction/size tests | Raw-content fields rejected | Real checkpoint sample absent in production |
| Raw evidence/opponent text/prompts/matrices/draft content excluded | Verified | same allowlist; redacted ops response `workflow_service.py:1200-1251` | Unit negative tests | Prohibited keys rejected | No live checkpoint document to sample |
| Durable document-selection interrupt | Partially Verified | graph gate `langgraph_engine.py:231`; resume API | InMemorySaver gate walk | Interrupt occurs locally | Every-node real-Mongo restart not done |
| Durable material-question interrupt | Partially Verified | `langgraph_engine.py:238`; workflow resume logic | InMemorySaver gate walk | Interrupt occurs locally | Real process restart not done |
| Durable matrix-review interrupt | Partially Verified | `langgraph_engine.py:239` | InMemorySaver gate walk | Interrupt occurs locally | Direct review route bypass and real restart gap |
| Durable readiness interrupt | Partially Verified | `langgraph_engine.py:240` | InMemorySaver gate walk | Interrupt occurs locally | Direct readiness role semantics defective |
| Durable plan-approval interrupt | Partially Verified | `langgraph_engine.py:242` | InMemorySaver gate walk | Interrupt occurs locally | Real restart not done |
| Durable legal-review interrupt | Partially Verified | `langgraph_engine.py:279` | InMemorySaver gate walk | Interrupt occurs locally | Browser/legal review not exercised |
| Durable final-draft approval interrupt | Partially Verified | `langgraph_engine.py:280` | InMemorySaver gate walk | Interrupt occurs locally | Standalone approval bypass remains |
| Create returns 202; state/events/resume/cancel/approval/force-v2 APIs work | Partially Verified | routes `routers/arbitration_drafting.py:229-320`; service methods | HTTP tests | Response/status/CAS paths work locally | No active-run listing/recovery UI; production collections empty |
| Resume enforces state version and returns 409 on stale request | Verified | `workflow_service.py:933-1074`; repository CAS | Duplicate/concurrent/stale tests | Stale resume rejected | Domain work may precede final CAS |
| Pre-side-effect CAS prevents replay/concurrent side effects | Partially Verified | effect acquire plus CAS in workflow service | Unit tests | Common duplicate requests contained | Approval receipt is written before final transition; graph does not own side effects |
| Checkpoint recovery is idempotent and cumulative | Partially Verified | `langgraph_engine.py:387-480`; sync markers in workflow service | InMemory/local tests | Projection can be resynchronized | No active-checkpoint TTL loss or every-node real-Mongo recovery |
| Cancellation is persisted and safely honored between nodes | Failed | v2 persists cancellation `engines/v2.py:189`; gate checks cancellation `langgraph_engine.py:183-190` | Test at `test_arbitration_drafting.py:3495-3535` | Test leaves graph `next` at `document_selection_gate` after cancellation | Static graph can remain queued; no active-node cancellation proof |
| Recoverable checkpoint-sync markers exist | Verified | `workflow_service.py:509-567`; run fields and ops health | Unit tests | Failure marker and resync path exist | No real sync outage/restart drill |
| Fallback reuses same immutable snapshot | Partially Verified | v2 fallback/run snapshot reuse in `workflow_service.py:1130-1176` | Unit tests | Hash reuse asserted | No production replay; direct legacy paths coexist |
| Force-v2 requires step-up and recorded reason | Verified | router dependencies; `workflow_service.py:1130-1176`; approval policy | Signed-JWT step-up tests | Missing/weak step-up rejected | Per-run production exercise absent |
| Ops checkpoint history is redacted/access-controlled | Verified | ops route and `workflow_service.py:1200-1251` | Admin/step-up/cross-tenant HTTP tests | Unauthorized users rejected; content redacted | Browser exercise absent |
| TTL, cleanup, indexes, retention, size limits configured | Partially Verified | `v20260722_0001_langgraph_checkpoint_ttl_compatibility.py`; config; validator | Migration/live index/size tests | 30-day TTL indexes installed | Post-TTL active-run recovery not demonstrated |
| Process kill/restart at every node and gate | Not Implemented | No such harness/test; production has zero runs/checkpoints | Test inventory and production DB count | Not performed | Blocks global criterion 4 |

### Phase 3 — Evidence fan-out and pleading-type routing

| Requirement | Status | Evidence | Test performed | Result | Remaining risk |
| --- | --- | --- | --- | --- | --- |
| Bounded parallel read-only analysis for documents, chronology, clauses, jurisdiction, notices, position, quantum, expert, opponent paragraphs | Failed | `workflow_domain.py:16-46,231-360` loads/canonicalizes existing matrix rows; graph nodes only set booleans `langgraph_engine.py:232-236` | Parallel/serial hash test inspection | No evidence retrieval/analysis occurs in these graph branches | Phase-3 core behavior absent; no tenant semaphore |
| SoC, SoD, Counterclaim, Rejoinder use distinct required routes | Partially Verified | route maps `workflow_domain.py:40-59`; graph shares same nodes | Type-route unit tests | Matrix requirements vary | Static graph route is not materially type-specific; SoD always requires counterclaim matrix |
| SoD bound to immutable SoC version | Verified | opponent capture `workflow_domain.py:183-229` and dependency checks | Unit tests | Immutable SoC version required | Production exercise absent |
| Rejoinder bound to immutable SoC and SoD versions | Verified | same dependency capture and route rules | Unit tests | Both versions required | Production exercise absent |
| Counterclaim enforces jurisdiction, limitation, notice, quantum | Partially Verified | branch maps and matrix requirement maps | Unit/source inspection | Labels and plan mappings exist | No graph analysis generates or substantively enforces these legal dimensions |
| Opponent pleadings are selected/versioned with provenance | Verified | opponent snapshot capture `workflow_domain.py:183-229`; paragraph parsing/projections | Unit tests | Immutable provenance retained | Historical pasted/unversioned rows still require review |
| Paragraph responses and matrices reconcile deterministically | Verified | `paragraph_positions.py`; server projections | Unit tests | One authoritative response with read-only projections | Historical ambiguous rows remain |
| Parallel and serial analysis produce same artifact-set hash | Verified | `workflow_domain.py:231-360` | Unit parity test | Same hash for existing-row canonicalization | Does not validate actual parallel evidence analysis |
| Branch artifacts non-authoritative until merge/human review | Verified | snapshots marked non-authoritative and merged matrix snapshot | Unit/source trace | Merge controls authority | Inputs may already contain incorrectly approved rows |
| Merged row records source revision IDs, row revision ID, evidence status | Verified | `workflow_domain.py:71-137,286-337` | Unit assertions | Fields present | `missing` is derived syntactically, not authoritative lookup |
| Source revision change invalidates readiness/plan/legal/draft | Partially Verified | invalidation hooks and workflow snapshot drift checks | Unit tests | Common changes invalidate | Missing authoritative-record path and cross-collection atomicity remain |
| Historical ambiguous/unversioned rows stay missing/needs-review | Verified | canonical evidence-status logic `workflow_domain.py:71-137` | Unit tests | Conservative status retained | No production backfill evidence |
| Fan-out concurrency bounded per tenant/project | Not Implemented | fixed `asyncio.gather` at `workflow_domain.py:273`; no semaphore/quota | Source search | No bound found | Tenant noisy-neighbor/load risk |

### Phase 4 — Plan, drafting, validation, and remediation

| Requirement | Status | Evidence | Test performed | Result | Remaining risk |
| --- | --- | --- | --- | --- | --- |
| Versioned pleading plan precedes workflow drafting | Verified | `workflow_domain.py:361-438`; plan indexes; `workflow_service.py:800-875` | Unit workflow tests | Plan created/versioned before candidate in workflow path | Standalone generate route bypasses it |
| Plan contains issues, claims/defences, paragraph positions, admissions/denials, objections, relief, section/source mapping | Partially Verified | plan builder `workflow_domain.py:361-438` | Unit snapshot assertions | Schema keys/mappings exist | Content is projections from rows, not independently analyzed for completeness |
| Counsel approves exact plan hash | Verified | gate hash comparison `workflow_service.py:733-762` | Stale/wrong hash tests | Exact hash enforced in workflow | Claimed semantic role issue exists on direct case review, not this gate |
| Draft bound to approved plan and immutable evidence/matrix snapshots | Partially Verified | workflow generate effect key/hash `workflow_service.py:800-875` | Unit tests | Workflow path is bound | Standalone `/generate` bypasses plan receipt |
| Candidate is idempotent and versioned | Verified | effects, version counters, immutable version hash | Duplicate/concurrent tests | Same effect does not duplicate candidate | Crash after side effect before transition not real-infra tested |
| Separate citation/assertion/structure/new-matter/quantum/duplication/exhibit/source-drift artifacts | Verified | `workflow_validation.py:15-26,99-203`; service snapshots | Unit tests | Eight artifacts and set hash generated | Depth of assertion/entity checks is insufficient |
| Validation report/artifact-set hash deterministic | Verified | `workflow_validation.py:203-223`; workflow snapshots | Determinism tests | Stable hashes | Upstream content may be weakly grounded |
| Hard blockers cannot reach legal approval | Verified | `workflow_service.py:707-731` | Negative approval tests | 409 with blockers | Direct standalone approval uses different governance path |
| Edited blocked candidate cannot be approved outside controlled revision flow | Partially Verified | immutable versions/current hash checks | Unit tests | Workflow detects version drift | Legacy/direct paths coexist; DB-level immutability is convention/index based |
| Revised candidate is immutable, parent-linked, revalidated | Verified | remediation/version lineage in `workflow_service.py:396-499` | Unit tests | Parent and new validation recorded | Production exercise absent |
| Automatic remediation is capped and terminates to review | Verified | `workflow_validation.py:225-249`; config max cycles | Limit tests | Bound enforced | No live-model remediation run |
| Remediation cannot add facts/sources/amounts/dates/clauses/parties/relief | Partially Verified | `workflow_validation.py:251-267` only compares source/amount/date; actual remediation is duplicate-citation removal | Unit no-new-source/value tests | Current narrow remediation does not add content | General rule is not code-enforced for clauses/parties/relief if remediation expands later |
| No-new-source and no-new-value rules code-enforced/tested | Partially Verified | same code/tests | Unit tests | Source/amount/date covered | Entities, clauses, parties and relief not covered |
| Section regeneration preserves unaffected sections/lineage | Verified | `service.py:482-560` | Unit tests | Preserved | No workflow gate receipt required by direct route |
| Validation detects unsupported assertions semantically | Failed | `workflow_validation.py:108-123` reclassifies supplied blocker strings; `validator.py:32-135` checks citation/format tokens | Test/source inspection | No sentence-to-evidence entailment/support check | Unsupported factual assertions can escape token checks |
| Entity, amount, date, clause, party controls code-enforced | Partially Verified | amount/date extraction in validator/LLM reconcile; no full entity/party/clause/relief comparison | Negative test inspection | Amount/date partial | Legal factual drift remains possible |

### Phase 5 — Shadow, canary, infrastructure, and operational hardening

| Requirement | Status | Evidence | Test performed | Result | Remaining risk |
| --- | --- | --- | --- | --- | --- |
| Shadow comparison has no authoritative graph writes | Partially Verified | `_record_shadow_comparison` uses comparison snapshots/effects `workflow_service.py:192-239` | Unit tests | Authoritative selection remains v2 | Candidate uses same domain analysis; independence is weak |
| Canary is deterministic/server-controlled | Verified | `engines/policy.py` stable bucket and scopes | Policy tests | Client cannot select bucket | No production canary sample |
| Shadow compares evidence, rows, readiness, plan, citations, blockers, latency, interventions | Partially Verified | comparison builder `workflow_service.py:72-239` | Unit tests | Dimension hashes/latency recorded | Both sides reuse same domain artifacts, making parity partly tautological |
| Redis worker has atomic lease/heartbeat/visibility/bounded transient retry/dead-letter/effect idempotency | Verified | Lua and worker paths `filing_export_queue.py:72-390`; Mongo effect lease in case service | Unit queue tests and migration/index inspection | Mechanisms exist and local fake-processing tests pass | Actual export worker not crash-tested in this audit |
| Duplicate delivery/worker crash do not duplicate exports | Implemented but Not Exercised | effect indexes and lease completion logic | Unit tests only | Simulated duplicate protected | No process kill during real Mongo/object generation |
| Active/expired lease recovery | Implemented but Not Exercised | reaper/heartbeat Lua | Unit test manipulates lease expiry | Simulated recovery passes | No actual Redis/worker kill in this audit |
| Redis/Mongo/Qdrant/object/model outages classified correctly | Partially Verified | queue transient classifier and health checks | Config/unit tests; live readiness read-only | Redis/Mongo/Qdrant currently healthy | S3/model and workflow-node outage behavior not exercised; Qdrant is degraded-only health dependency |
| Backup, restore, cleanup, TTL tested | Partially Verified | runbook and migration tests; prior progress claims exist | This audit inspected live indexes only | TTL index verified | No isolated restore/cleanup drill was independently rerun; active checkpoint expiry test is invalid |
| Signed-JWT cross-tenant and step-up tests pass | Verified | `test_arbitration_http_isolation.py` plus broader security suites | 74 security tests and arbitration HTTP tests | Denials pass | Same-tenant semantic reviewer-role bypass remains |
| Browser role review/legal approval/resume/cancel/export works | Not Implemented | No browser E2E suite; only React component tests | Browser connection attempted | Automation runtime unavailable; no signed-in flow evidence | Blocks acceptance |
| All four pleading types have production-like samples | Failed | “fixture” test `test_arbitration_drafting.py:4227-4237` only checks JSON names and omits Counterclaim execution; production counts zero | Test inspection and live DB counts | No production samples | Blocks criteria 12/13 |
| Metrics, dashboards, alerts, runbooks meet Section 14 | Partially Verified | metrics in `services/observability.py:327-409`; runbook `docs/architecture/arbitration_langgraph_operations_runbook.md` | Source/config search | Core counters/gauges and recovery text exist | No Grafana/Prometheus dashboard or alert-rule provisioning found; several required node/lease/evidence metrics absent |
| `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED` and rollout controls fail closed | Verified | `core/config.py:560-621`; `engines/policy.py:127-157` | Config tests and production env inspection | Production is v2/off/false/0 | Receipt ID/hash are only syntactically checked |

### Phase 6 — Controlled primary rollout

| Requirement | Status | Evidence | Test performed | Result | Remaining risk |
| --- | --- | --- | --- | --- | --- |
| Primary cannot enable before all global criteria | Failed | selector checks configured health/sample fields, not all 14 criteria; receipt is not persisted | Policy/source trace | Some fail-closed checks exist, but P0/P1 criteria are not independently attested | Operator can supply syntactically valid receipt values without immutable acceptance record |
| Acceptance switch is server-side/fail-closed | Verified | config validation and selector | Tests/live config | False blocks primary | Formal evidence binding remains weak |
| Tenant/project rollout scopes recorded | Partially Verified | config scopes and run selector metadata/index `v20260722_0002...py` | Policy tests/live empty scopes | Selection scope can be recorded per run | No production scope/expansion history |
| Formal production-acceptance receipt exists | Not Implemented | only env ID/SHA fields `core/config.py:233-234,598-603`; no receipt repository/collection validation | Repository/index search | No receipt in production config or DB | Phase-6 authorization lacks immutable acceptance evidence |
| Expansion is gradual and auditable | Implemented but Not Exercised | primary percentage/scope and workflow events | Policy tests | Controls exist | No rollout occurred, so auditability unproven |
| Per-run/per-tenant forced-v2 remains available | Verified | force scopes, force API, v2 compatibility | Tests/live config | Available and step-up guarded | No production fallback sample |
| Fallback cannot overwrite graph candidate/approved draft | Partially Verified | fallback guard/effect checks in workflow service | Unit tests | Common terminal effects block fallback | Direct legacy routes and real crash boundaries not exercised |
| v2 replay retained for required period | Verified | compatibility mode/until controls; production `active` | Config inspection | v2 retained | Evidence retention period not yet started |
| Legacy orchestration not removed prematurely | Verified | active `arbitration_v2`, standalone service/router | Route/import/live config trace | Still present | Its direct approval/export endpoints are unsafe bypasses |
| v2 deprecation supported by four-type production evidence | Failed | production workflow/sample collections all zero | Read-only Mongo count | No evidence | v2 must not be deprecated |

## 3. Expected-Result Verification

The plan's phase status notes overstate completion when read as operational acceptance:

| Plan-stated expected result | Actual verification |
| --- | --- |
| Phase 0 bypasses closed | **False.** Generic lifecycle payload bypasses are closed, but semantic reviewer-role impersonation, workflow-less standalone approval/export, and missing authoritative matrix-source rehydration remain. |
| Phase 1 controlled engine/snapshots/effects | **Partly true.** Foundations are present and locally tested; they do not make direct legacy routes governed, and some writes occur before final CAS. |
| Phase 2 official durable workflow | **Partly true.** Official `StateGraph` and Mongo saver exist, but the graph records markers around domain work performed elsewhere. Every-node/gate recovery and active post-TTL restoration are not proven. |
| Phase 3 evidence fan-out/type routing complete | **False.** The “fan-out” hashes already-existing rows; it does not perform the required source analyses. No per-tenant concurrency bound exists. |
| Phase 4 plan/draft/validation/remediation complete | **Partly true.** Workflow-path plan/version/validation mechanics work locally. Standalone generate/approval/export bypass them, and unsupported-assertion/entity enforcement is incomplete. |
| Phase 5 operational acceptance drills complete | **Not independently verified and not sufficient.** Code/tests cover queue mechanisms, but this audit did not reproduce destructive infrastructure drills. Default integration tests are skipped; browser, S3/model, real export crash, and four-type samples are absent. |
| Phase 6 cutover controls complete | **Partly true, correctly inactive.** Fail-closed config works, but no formal persisted acceptance receipt or production samples exist; not all global criteria are machine-enforced. |

## 4. End-to-End Workflow Results

No end-to-end production workflow was executed because the production database contains zero arbitration cases/workflows/drafts and the audit was prohibited from modifying production data. The following results combine active route tracing, signed-JWT isolated API probes, focused tests, and read-only production inspection.

### Statement of Claim

`Intake → Source Selection → Analysis → Matrix Review → Readiness → Plan → Draft → Validation → Legal Review → Approval → Export`

- Intake/case, matrices, readiness, plan, immutable draft and validation components exist.
- Source selection is authoritative for explicit selected references but not for approved matrix rows.
- Analysis graph nodes are markers; domain “fan-out” canonicalizes existing rows.
- Matrix role separation can be defeated by claimed `reviewer_role`.
- Workflow legal/final/export gates exist, but active standalone draft/export routes bypass plan/legal/export receipts.
- **Result:** Partially implemented; not safe for canary or filing.

### Statement of Defence

- Immutable SoC opponent version capture and paragraph-response projection exist.
- Required route incorrectly includes `counterclaim-matrix` for every SoD at `workflow_domain.py:44`, so a defence without a counterclaim can be blocked or forced to create an irrelevant matrix.
- The same evidence, role, standalone approval and export defects apply.
- **Result:** Partially implemented with a route defect; no production sample.

### Counterclaim

- A separate counterclaim matrix and jurisdiction/limitation/notice/quantum mappings exist.
- The graph does not actually analyze those dimensions; it hashes existing rows.
- The named “all pleading fixtures” test does not execute a Counterclaim case, and production contains no sample.
- **Result:** Partially implemented and materially unverified.

### Rejoinder

- Immutable SoC/SoD dependencies, paragraph positions, new-matter flags, permission-required/obtained/evidence fields, and a permission receipt exist.
- Permission receipt write ordering is non-atomic, and the direct permission path does not enforce a server-derived semantic reviewer role.
- The same source grounding and export bypasses apply.
- **Result:** Partially implemented; permission semantics are not production accepted.

## 5. Authorization and Bypass Results

Tests below used an isolated in-memory application/database with signed JWTs; no production data was changed.

| Attempt | Identity/permission | Endpoint/path | Result | Verdict |
| --- | --- | --- | --- | --- |
| Create row with approved lifecycle fields | Edit-only and generate-only | Matrix create | HTTP 422 | Blocked |
| Patch privileged lifecycle fields | Edit-only and generate-only | Matrix update | HTTP 422 | Blocked |
| Agent `auto_approve=true` | Generate | Agent run | HTTP 422 | Blocked |
| Approve matrix row | Edit-only | Review endpoint | HTTP 403 | Blocked |
| Approve matrix row | Generate-only | Review endpoint | HTTP 403 | Blocked |
| Approve readiness | Edit-only/generate-only | `/approve-readiness` | HTTP 403 | Blocked |
| Approve draft | Edit-only/generate-only | `/drafts/{id}/approve` | HTTP 403 | Blocked |
| Export draft | Edit-only/generate-only | `/drafts/{id}/export/pdf` | HTTP 403 | Blocked |
| Satisfy two matrix reviewer specialties with one actor | One user with approve permission | Same row reviewed as `legal`, then `quantum` | First response partially approved; second response approved; both roles recorded for same user | **Bypass succeeded (P0)** |
| Export without workflow plan/legal/final/export receipts | Export-authorized user; approved immutable draft plus standalone readiness only | `/drafts/draft-1/export/pdf` | HTTP 200 PDF; filing authorization created | **Bypass succeeded (P0)** |
| Cross-organisation access to workflow state/events/ops | Signed user in another organisation | Workflow APIs | HTTP 403/404 in isolation tests | Blocked locally |
| Cross-project access | Signed user outside project | Workflow APIs | HTTP 403/404 in isolation tests | Blocked locally |
| Force-v2/checkpoint operations without step-up | Admin permission without current step-up | Operations APIs | Rejected in tests | Blocked locally |
| Reuse stale approval after artifact drift | Approver | Workflow approval | 409 in tests | Blocked in workflow path |
| Reuse export authorization for a different draft | Export user | Queue effect key/version checks | Unit tests reject common mismatch | Locally blocked; full bundle TOCTOU remains |

The frontend hiding/showing controls is not treated as security evidence. Backend permissions block several negative paths, but the two successful P0 bypasses are backend behaviors and therefore remain exploitable by direct API requests.

## 6. Checkpoint, Restart, and Recovery Results

### Node/gate matrix

| Node or gate | Local graph exercised | Real Mongo checkpoint restart | Kill before/during/after effect | Duplicate-effect proof | Result |
| --- | --- | --- | --- | --- | --- |
| Validate intake / input snapshot | Yes, marker path | No | No | Repository unit only | Implemented but Not Exercised |
| Document selection gate | InMemorySaver | No in this audit | No | Resume unit only | Partially Verified |
| Document analysis | Boolean marker only | No | No | Not applicable to real analysis | Failed requirement |
| Chronology analysis | Boolean marker only | No | No | Not applicable to real analysis | Failed requirement |
| Clause/jurisdiction/notice analysis | Boolean marker only | No | No | Not applicable to real analysis | Failed requirement |
| Position analysis | Boolean marker only | No | No | Not applicable to real analysis | Failed requirement |
| Quantum/expert analysis | Boolean marker only | No | No | Not applicable to real analysis | Failed requirement |
| Deterministic merge | Existing rows hashed locally | No | No | Effect-key unit tests | Partially Verified |
| Material questions gate | InMemorySaver | No | No | Stale-resume test | Partially Verified |
| Matrix review gate | InMemorySaver | No | No | Approval unique index | Partially Verified |
| Readiness gate | InMemorySaver | No | No | Receipt/hash unit tests | Partially Verified |
| Build plan / plan gate | Artifact marker + service | No | No | Plan unique indexes | Partially Verified |
| Draft generation | Artifact marker + service | No | No | Candidate effect/version unit tests | Partially Verified |
| Eight validation branches | Completion markers + service | No | No | Snapshot key tests | Partially Verified |
| Remediation | Artifact marker + service | No | No | Effect/version unit tests | Partially Verified |
| Legal review gate | InMemorySaver | No | No | Approval index/test | Partially Verified |
| Final approval gate | InMemorySaver | No | No | Approval index/test | Partially Verified |
| Export authorization gate | InMemorySaver | No | No | Queue/effect unit tests | Partially Verified |
| Cancellation during processing | Status persistence test | No | No | No | Failed: test leaves next gate queued |

### Specific recovery scenarios

| Scenario | Evidence/result |
| --- | --- |
| Duplicate resume | Local CAS/idempotency tests pass; no real-process race test. |
| Concurrent resume | One local caller wins and stale caller conflicts; domain work before final CAS remains a risk. |
| Stale state version | Verified 409 locally. |
| Expired Redis lease | Simulated by directly changing lease timestamp; no real worker/Redis kill in this audit. |
| Checkpoint TTL expiry | Default test is skipped. Its logic expires a separate marker while retaining the active checkpoint, so it does not prove post-TTL recovery. |
| Snapshot drift after long pause | Hash-drift checks exist and local tests reject stale approvals; no long-duration real-infrastructure test. |
| Model timeout/rate limit | Classifiers/retry settings exist; no live-model test in this audit. |
| Redis restart | Not independently performed; production Redis currently healthy. |
| Mongo restart | Not performed; would mutate availability of production infrastructure. |
| Qdrant outage | Not performed; production readiness currently reports Qdrant healthy. |
| Object storage outage | Not tested. |
| Worker crash during actual export | Not tested. Integration test substitutes a Redis counter for actual export processing. |
| Cancellation during active node | Not proven; graph nodes lack active domain work and cancellation test retains next node. |

## 7. Infrastructure and Operational Results

| Area | Evidence | Audit result |
| --- | --- | --- |
| Production deployment | Deployed commit `410ae3e`; arbitration paths match local audit commit | Source inspected is representative for arbitration code |
| Backend/readiness | Live `/health/ready` returned ready; Mongo/Redis/config/local storage OK; Qdrant/FalkorDB/ClamAV reported OK | Health only; explicitly not treated as workflow proof |
| MongoDB | Three-node replica set healthy; Phase 0-6 migrations applied; required unique/TTL indexes present | Infrastructure available; all relevant arbitration workflow/checkpoint/export collections had zero records |
| Redis worker | Worker container up; queue configured with leases/heartbeat/visibility/retries | No actual production export or crash drill reproduced |
| Qdrant | Live readiness OK | No workflow evidence; outage classification not independently exercised |
| Object storage | Config/path code present | S3 outage and immutable bundle object behavior not tested |
| Model calls | LLM/deterministic generation paths exist | No live model/PDF grounding test; blocks acceptance |
| Filing-export manifest | Authorization hash covers case/draft/readiness/citation fields at `case_workspace.py:1767-1800`; worker rebuilds current bundle at `case_workspace.py:1609-1699,1824-1831` | Matrix/exhibit/current bundle bytes are not fully frozen in the queued effect; TOCTOU risk |
| Backup/restore | Runbook describes procedure; prior progress report claims drills | Not independently rerun; claims are not accepted as current proof |
| TTL/cleanup | TTL indexes live at 2,592,000 seconds | Active-run recovery after checkpoint deletion unproven |
| Metrics | Arbitration counters/gauges in `services/observability.py:327-409` | Partial Section-14 coverage |
| Dashboards/alerts | No repository Grafana/Prometheus dashboard or rule containing arbitration metric names found | Not implemented as deployable controls |
| Runbook | `docs/architecture/arbitration_langgraph_operations_runbook.md` covers pause, checkpoint, drift, lease, fallback and export recovery | Useful but contains completion claims not independently substantiated |
| Rollout controls | Production default v2, mode off, acceptance false, primary 0, empty receipt/scope | Correct fail-closed state; do not enable |

## 8. Test Execution Summary

### Commands and results

| Command/suite | Passed | Failed/errors | Skipped | Notes |
| --- | ---: | ---: | ---: | --- |
| Initial focused backend invocation from the wrong import root | 0 | 1 collection error | 0 | `ModuleNotFoundError: backend.rbac_backend`; corrected with repository `PYTHONPATH` |
| Arbitration, queue, TTL, config, migration focused pytest suite | 160 | 0 | 2 | 216 warnings; 65.67s |
| Auth token, CORS step-up, deployment config, RBAC policy, tenant isolation pytest suite | 74 | 0 | 0 | 159 warnings; 11.56s |
| Frontend ArbitrationDraftingPage and ArbitrationCaseWorkspacePage Vitest | 10 | 0 | 0 | 2 files; 21.74s |
| Frontend production build | Build passed | 0 | N/A | 3,881 modules; browserslist/dynamic-import warnings |
| Controlled signed-JWT API bypass probe | Expected-denial cases passed | 2 governance bypasses reproduced | N/A | Same-actor multi-role approval and workflow-less filing export |

Exact repeatable invocations used for the focused automated suites (PowerShell, repository root):

```powershell
$env:PYTHONPATH='C:\SaaS\projectDMS'
.\backend\.venv\Scripts\python.exe -m pytest `
  backend/rbac_backend/tests/test_arbitration_drafting.py `
  backend/rbac_backend/tests/test_arbitration_http_isolation.py `
  backend/rbac_backend/tests/test_filing_export_queue.py `
  backend/rbac_backend/tests/test_filing_export_queue_integration.py `
  backend/rbac_backend/tests/test_arbitration_checkpoint_ttl_integration.py `
  backend/rbac_backend/tests/test_config_validation.py `
  backend/rbac_backend/tests/test_migration_runner.py -q

.\backend\.venv\Scripts\python.exe -m pytest `
  backend/rbac_backend/tests/test_auth_token_hardening.py `
  backend/rbac_backend/tests/test_cors_step_up.py `
  backend/rbac_backend/tests/test_deployment_config.py `
  backend/rbac_backend/tests/test_rbac_policy_hardening.py `
  backend/rbac_backend/tests/test_tenant_isolation.py -q

Set-Location client
npm test -- --run src/pages/__tests__/ArbitrationDraftingPage.test.tsx src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx
npm run build
```

Read-only production inspection used SSH, Docker/Compose status and logs, backend `/health/ready`, Mongo migration/index/count queries, and deployed Git source comparison. Secrets were not recorded in this report. No stop/restart, outage, TTL deletion, restore, cleanup, export, or data-writing command was run against production.

Focused backend files executed:

```text
backend/rbac_backend/tests/test_arbitration_drafting.py
backend/rbac_backend/tests/test_arbitration_http_isolation.py
backend/rbac_backend/tests/test_filing_export_queue.py
backend/rbac_backend/tests/test_filing_export_queue_integration.py
backend/rbac_backend/tests/test_arbitration_checkpoint_ttl_integration.py
backend/rbac_backend/tests/test_config_validation.py
backend/rbac_backend/tests/test_migration_runner.py
backend/rbac_backend/tests/test_auth_token_hardening.py
backend/rbac_backend/tests/test_cors_step_up.py
backend/rbac_backend/tests/test_deployment_config.py
backend/rbac_backend/tests/test_rbac_policy_hardening.py
backend/rbac_backend/tests/test_tenant_isolation.py
```

Skipped tests and acceptance impact:

1. Real Redis queue integration skipped because its opt-in Redis URL was not configured. This blocks independent acceptance of real duplicate/crash/visibility behavior.
2. Checkpoint TTL integration skipped because `RUN_ARBITRATION_TTL_INTEGRATION` was not set. Even enabled, the test does not delete the active checkpoint and therefore cannot satisfy post-TTL resume acceptance.
3. No browser E2E suite exists. Browser connection for a signed-in production flow was unavailable in the audit environment. This blocks authenticated legal-review/resume/cancel/export acceptance.
4. No real Mongo/Qdrant/S3/live-model every-node outage suite was found. This blocks criteria 4 and 13.

Test-quality findings:

- The unit suites meaningfully assert many prohibited paths, hashes, unique effects, state versions and redaction.
- `test_filing_export_queue_integration.py` uses real Redis only when opted in, but replaces actual export generation with a Redis `INCR`; it cannot prove Mongo effect ownership or output uniqueness.
- `test_arbitration_checkpoint_ttl_integration.py` keeps the active workflow checkpoint and expires a separate marker; it does not test recovery after active checkpoint TTL deletion.
- The cancellation test explicitly observes the next gate still queued after cancellation.
- The “all pleading production-like fixtures” assertion checks names in JSON and does not execute Counterclaim.
- Frontend tests are component/API-mock tests, not signed-in browser workflows.

## 9. Baseline Defects and Gaps

### P0 — Security, approval, provenance, or export bypass

#### P0-1 — Caller can impersonate required matrix reviewer roles

- **Affected:** `backend/rbac_backend/services/arbitration_drafting/case_workspace.py:402-558`, especially `review_matrix_row` at line 402 and caller role extraction at line 417; frontend `client/src/pages/ArbitrationCaseWorkspacePage.tsx:484-502,1533`.
- **Root cause:** The direct case-workspace review service trusts `payload.reviewer_role`; it does not derive the role from server-side RBAC via `enforce_gate_role`.
- **Reproduction:** Authenticate one user with approve permission; approve one row as `legal`; submit the same row as `quantum`.
- **Expected:** Server rejects a role the user is not assigned and enforces distinct actors if required by policy.
- **Actual:** The same actor completed both roles and the row became approved.
- **Recommended correction:** Apply server-side gate/role policy to matrix types, bind receipts to actual role assignments, and enforce distinct actors for multi-role matrices where configured.
- **Regression test:** Signed-JWT direct API test proving one actor cannot claim both required roles; positive test with two independently authorized accounts.

#### P0-2 — Standalone draft/export APIs bypass workflow governance

- **Affected:** `backend/rbac_backend/routers/arbitration_drafting.py:809-818,878-886,927-968`; `services/arbitration_drafting/service.py:371-389,563-607,629-697`; frontend `ArbitrationDraftingPage.tsx:399-416,557-576`.
- **Root cause:** Active legacy endpoints validate local draft/readiness/citation state but do not require an approved plan receipt, legal-review receipt, workflow final-approval receipt, or export-authorization receipt; no audited exception is required.
- **Reproduction:** Create an approved immutable draft and standalone readiness receipt without a workflow run; call `/api/arbitration/drafts/{id}/export/pdf` as an export-authorized user.
- **Expected:** 409/403 unless a governed workflow receipt chain or explicit audited exception is present.
- **Actual:** HTTP 200 PDF and a filing authorization record.
- **Recommended correction:** Make governed workflow export the only filing path; retain preview-only legacy output or require a scoped, step-up, immutable exception receipt.
- **Regression test:** Direct API tests for generate/approve/export without each required receipt, including modified frontend payloads.

#### P0-3 — Approved matrix evidence is not obligatorily rehydrated

- **Affected:** `case_workspace.py:915-955,2061-2072`; `context.py:610-665`.
- **Root cause:** Readiness checks identifiers/approval state; manifest creation tolerates a missing authoritative source; context copies matrix snippets rather than resolving the source record and revision in tenant/project scope.
- **Reproduction:** Keep an approved document matrix row after its authoritative source is missing/deleted/out of scope; calculate readiness and build draft context.
- **Expected:** Readiness/drafting fails closed and invalidates downstream approvals.
- **Actual:** Row can remain readiness-eligible and contributes a matrix-derived source ledger entry.
- **Recommended correction:** Resolve every row source/revision through a strict tenant/project-scoped registry and fail readiness on missing/drifted records.
- **Regression test:** Missing, deleted, cross-tenant, cross-project, and revision-drift source tests through direct readiness and draft APIs.

### P1 — Workflow integrity, durability, recovery, or completeness

#### P1-1 — StateGraph is not the domain orchestration owner

- **Affected:** `langgraph_engine.py:228-282,387-480`; `workflow_service.py:640-1074`.
- **Root cause:** Graph nodes write flags or require prebuilt artifact IDs; service code performs analysis, plan, generation, validation and effects outside graph-node execution.
- **Reproduction:** Trace `ArbitrationLangGraphEngine.create_workflow` through `super()` and checkpoint sync; inspect analysis node lambdas.
- **Expected:** Each graph node owns a bounded domain command with effect/CAS/checkpoint recovery semantics.
- **Actual:** Graph is a durable control-flow projection around a sequential service.
- **Recommended correction:** Move domain commands behind idempotent graph nodes while retaining repositories/services as domain primitives.
- **Regression test:** Kill/restart before, during and after every node effect using real Mongo; assert one authoritative effect.

#### P1-2 — Phase-3 fan-out does not analyze evidence and is unbounded per tenant

- **Affected:** `workflow_domain.py:16-46,231-360`; `langgraph_engine.py:232-236`.
- **Root cause:** Branches filter/canonicalize existing rows; fixed `asyncio.gather` has no tenant/project semaphore.
- **Reproduction:** Start from selected documents with no prebuilt matrices; run graph analysis.
- **Expected:** Read-only document/chronology/clause/jurisdiction/notice/position/quantum/expert/opponent artifacts.
- **Actual:** No new substantive analysis; only boolean markers and hashes of existing rows.
- **Recommended correction:** Implement bounded branch services with immutable source-revision inputs and deterministic merge.
- **Regression test:** Golden source corpus for all four types, serial/parallel hash parity, tenant concurrency cap assertion.

#### P1-3 — SoD always requires a counterclaim matrix

- **Affected:** `workflow_domain.py:44`.
- **Root cause:** Static required-matrix map conflates defence and optional counterclaim.
- **Reproduction:** Prepare a SoD responding to a SoC with no counterclaim.
- **Expected:** Defence route requires defence/opponent matrices; counterclaim route/gates only when asserted.
- **Actual:** `counterclaim-matrix` is mandatory.
- **Recommended correction:** Conditional route based on immutable counterclaim decision.
- **Regression test:** SoD with and without counterclaim.

#### P1-4 — Approval receipts can be orphaned before state transition

- **Affected:** `workflow_service.py:749-762,790-922`; `case_workspace.py:597-635,744-778`.
- **Root cause:** Receipt insert occurs before downstream side effect/final CAS, without a transaction or pending/commit protocol.
- **Reproduction:** Force downstream plan/generation/CAS failure after receipt insertion.
- **Expected:** Receipt and state/effect commit atomically or receipt remains explicitly pending/invalid.
- **Actual:** A durable receipt may exist without corresponding state transition.
- **Recommended correction:** Transactional outbox/commit marker or CAS-reserved approval effect finalized after successful transition.
- **Regression test:** Inject failure after receipt insert and assert no valid approval remains.

#### P1-5 — Export effect does not bind complete bundle inputs

- **Affected:** `case_workspace.py:1609-1699,1767-1800,1824-1831`.
- **Root cause:** Authorization/effect hash omits complete matrix/exhibit bytes and worker rebuilds current case state later.
- **Reproduction:** Queue export, then change a bundle matrix/exhibit before worker execution.
- **Expected:** Worker emits the exact immutable authorized manifest or refuses drift.
- **Actual:** Effect key can remain unchanged while generated bundle content changes.
- **Recommended correction:** Persist an immutable export manifest with hashes for every file/artifact and verify it before publish.
- **Regression test:** Queue-then-drift tests for matrix, exhibit, metadata and source files.

#### P1-6 — Cancellation and post-TTL recovery are not proven

- **Affected:** `langgraph_engine.py:183-190`; `test_arbitration_drafting.py:3495-3535`; `test_arbitration_checkpoint_ttl_integration.py:30-99`.
- **Root cause:** Cancellation is checked at gates; TTL test preserves active checkpoint.
- **Reproduction:** Cancel at an active/static edge; delete the actual active checkpoint before resume.
- **Expected:** Terminal cancel before next side effect; safe snapshot-based reconstitution after allowed TTL condition.
- **Actual:** Test leaves next gate queued; no missing-active-checkpoint recovery proof.
- **Recommended correction:** Cancellation router on every edge/node boundary and explicit missing-checkpoint reconstruction policy.
- **Regression test:** Real-Mongo cancel/TTL restart matrix.

#### P1-7 — Validation does not enforce semantic assertion/entity support

- **Affected:** `workflow_validation.py:99-203`; `validator.py:32-135`; `llm_generator.py:202-222`.
- **Root cause:** Validation is token/format and upstream-warning based; no deterministic claim-to-source support map for sentences/entities/clauses/parties/relief.
- **Reproduction:** Add a fluent unsupported fact using known citation tokens and already-seen amount/date formats.
- **Expected:** Hard unsupported-assertion blocker.
- **Actual:** It can evade current checks.
- **Recommended correction:** Generate structured assertions with source spans and enforce closed-world entity/value/relief sets.
- **Regression test:** Adversarial unsupported fact/entity/clause/party/relief cases.

#### P1-8 — Formal production acceptance is not persisted or cryptographically resolved

- **Affected:** `core/config.py:233-234,598-603`; `engines/policy.py:127-157`; `workflow_hardening.py:177-232`.
- **Root cause:** Receipt fields are environment strings; health sample count includes runs without proving completed legal/grounding acceptance.
- **Reproduction:** Configure a non-empty receipt ID and any 64-hex hash with otherwise synthetic health records.
- **Expected:** Selector resolves an immutable signed acceptance record covering all 14 criteria and four pleading types.
- **Actual:** Syntax/config checks can pass without such repository evidence.
- **Recommended correction:** Persist signed acceptance receipt with criterion evidence hashes, approvers, scope and expiration; selector must resolve it.
- **Regression test:** Missing, forged, stale, wrong-scope and incomplete receipt tests.

### P2 — Quality, usability, observability, or maintainability

#### P2-1 — Frontend workflow state is not recovered after page reload

- **Affected:** `client/src/pages/ArbitrationCaseWorkspacePage.tsx:330` and workflow action/state code.
- **Root cause:** Active workflow is held in component state; no active-run list or load-on-mount restoration was found.
- **Expected/actual:** Backend run survives, but the user's timeline/controls can disappear on refresh.
- **Correction/test:** Add scoped active-run query/recovery UI and reload E2E test.

#### P2-2 — Shadow parity is not independent

- **Affected:** `workflow_service.py:192-239`.
- **Root cause:** Authoritative and candidate comparisons reuse the same domain analysis/readiness/artifacts.
- **Expected/actual:** Independent implementations should reveal divergence; shared inputs/outputs can produce tautological parity.
- **Correction/test:** Run graph-owned candidate analysis in a non-authoritative namespace and compare normalized outputs.

#### P2-3 — Operational telemetry is incomplete

- **Affected:** `services/observability.py:327-409`; no arbitration dashboard/rule assets found.
- **Root cause:** Metrics focus on totals/latest values; required node attempts/retry reasons/queue lag/lease expiry/checkpoint age/resume counts/evidence ratios/human wait/export blocks are incomplete.
- **Correction/test:** Add bounded metrics, deployable dashboard and alert rules; scrape and alert integration tests.

#### P2-4 — Counterclaim/golden/browser coverage is mislabeled

- **Affected:** `test_arbitration_drafting.py:4227-4237`; frontend component tests.
- **Root cause:** Fixture-name assertion is treated as workflow evidence; no browser E2E.
- **Correction/test:** Execute four production-like golden cases through signed-in UI/API with legal grounding assertions.

### P3 — Minor improvement

#### P3-1 — Test invocation is import-root sensitive and warning-heavy

- **Affected:** backend test environment/configuration.
- **Root cause:** Running from `backend` with inconsistent module path caused collection failure; focused suites emitted 375 warnings.
- **Correction/test:** Document one canonical command and reduce deprecation/resource warnings so failures remain visible.

## 10. Baseline Global Acceptance Criteria

| # | Criterion | Evaluation | Evidence |
| ---: | --- | --- | --- |
| 1 | All Phase-0 bypasses closed with negative tests | Failed | P0-1 through P0-3 |
| 2 | Every authoritative run has immutable input/evidence/opponent snapshots/hashes | Not tested | Production has zero runs; direct legacy paths are not workflow-snapshot bound |
| 3 | No raw evidence/draft in checkpoints/ops | Passed | Checkpoint allowlist/redaction tests and code review |
| 4 | Crash/restart every node/gate without duplicate effects | Failed | No every-node real-Mongo harness; graph does not own domain effects |
| 5 | Four routes enforce predecessors/matrices/chronology/readiness/plan/legal/final gates | Failed | SoD counterclaim defect and active standalone generate/approve/export bypass |
| 6 | New matter requires separate permission receipt | Not tested | Local receipt mechanics exist; no end-to-end Rejoinder and receipt ordering issue |
| 7 | Export bound to approved immutable version/current readiness/passing citation/exhibit audit | Failed | Partial checks exist, but workflow receipt chain and complete immutable bundle manifest absent |
| 8 | No client/edit/generate path can self-approve | Failed | Edit/generate permissions denied, but claimed semantic reviewer role and direct legacy governance bypass remain |
| 9 | Parallel merge deterministic and source revisions auditable | Passed | Deterministic existing-row merge tests; note that substantive analysis is absent |
| 10 | Retry bounded/transient/observable/no duplicate authoritative effects | Not tested | Queue code/unit tests exist; real worker/export crash and all-node recovery absent |
| 11 | v2 fallback reuses snapshot, records reason, cannot overwrite graph draft | Not tested | Local tests only; no production graph run/effect |
| 12 | Golden SoC/SoD/Counterclaim/Rejoinder pass legal/grounding review | Failed | Counterclaim not executed; no production samples |
| 13 | Real Mongo/Redis/Qdrant/S3/model/load/restore/browser tests pass production-like | Blocked by environment | Mongo/Redis/Qdrant currently healthy, but destructive outage/browser/S3/model tests unavailable and prohibited on production |
| 14 | Runbooks cover pause/checkpoint/drift/lease/fallback/export recovery | Passed | Operations runbook contains all named procedures |

Only criteria 3, 9 and 14 pass this audit. Criterion 13 is environment-blocked; the remaining criteria fail or lack evidence. “Not tested” is not acceptance.

## 11. Production Readiness Decision

| Decision | Answer |
| --- | --- |
| May LangGraph become primary? | **No.** |
| May `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED` be enabled? | **No.** Keep false. |
| May canary begin or expand? | **No authoritative canary.** Continue only isolated non-filing shadow/corrective tests after containing P0 paths. |
| May v2 be deprecated? | **No.** It is the active fail-safe and there is no four-type production evidence. |
| Is filing export production safe? | **No.** Direct workflow bypass and incomplete immutable bundle binding are P0/P1 blockers. |

Production is currently in the correct fail-closed state: `arbitration_v2`, rollout off, primary percentage zero, production acceptance false, no acceptance receipt, and no rollout scope. This audit did not change those controls.

## 12. Final Action Plan

Following the corrective implementation, only unresolved items are listed.

1. **P1 architecture:** Move the remaining substantive analysis/plan/generation/validation commands from `ArbitrationWorkflowService` into async, idempotent StateGraph node executors with effect reservation and run CAS on both sides of every authoritative side effect. Retain `arbitration_v2` unchanged as fallback.
2. **P1 recovery acceptance:** In an isolated production-like environment, run real-Mongo process termination before/during/after every graph node and human gate, delete the active checkpoint after TTL, resume, and prove one matrix/candidate/version/approval/export effect.
3. **P1 infrastructure acceptance:** Complete real Redis kill/visibility/duplicate recovery, Mongo/Qdrant/object-storage/model outage classification, sustained load, TTL cleanup, backup/restore and isolated cleanup drills. Retain logs, metrics, effect histories and output hashes.
4. **P1 authorization acceptance:** Execute signed-JWT and signed-in browser workflows with two independently authorized accounts across organisation/project boundaries for SoC, SoD, Counterclaim and Rejoinder, including step-up, separation of duties, resume, cancellation, legal approval and export.
5. **P1 legal acceptance:** Run four production-like golden matters through counsel review and retain immutable stakeholder sign-offs for grounding, plan coverage, validation, new matter, quantum, citations, exhibits and filing output.
6. **P2 validation quality:** Replace the remaining heuristic assertion test with a structured claim-to-source-span artifact and independently verified closed-world entity/party/clause/value/relief mapping.

Until items 1-5 are evidenced and independently re-audited, authoritative canary filing, primary rollout, the production-acceptance switch, v2 deprecation and a “production safe” export claim remain prohibited.
