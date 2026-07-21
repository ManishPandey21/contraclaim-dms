# Arbitration Pleadings LangGraph Operations Runbook

**Status:** Implemented behind server-authoritative rollout controls; production acceptance is disabled by default.
**Scope:** Statement of Claim, Statement of Defence, Counterclaim, and Rejoinder.
**Architecture:** Official LangGraph runs in the main backend. The experimental `services/langgraph` sidecar is not used.

## Source-of-truth model

| Legal artifact | Authoritative record | Read-only projections / consumers |
|---|---|---|
| Selected evidence | Scoped `arbitration_selected_references`, rehydrated from documents, letters, approved matrices, and other allowed server records | Source ledger, pleading plan, validation, exports |
| Chronology | Approved `arbitration_chronology_matrix` revision | Plan chronology coverage and generated pleading sections |
| Claim / counterclaim position and quantum | Approved claim, counterclaim, and quantum matrix revisions | Plan positions, relief, causation, and quantum sections |
| Defence / rejoinder paragraph position | `arbitration_paragraph_responses` bound to an immutable opponent pleading version | Defence/rejoinder matrix and drafting views should be treated as projections where paragraph IDs are present |
| Rejoinder new-matter permission | Dedicated permission receipt plus rejoinder row permission fields | Readiness, validation, plan, and export blockers |
| Readiness | Exact-hash approval receipt in `arbitration_workflow_approvals` | Case status and workflow gate state |
| Pleading strategy | Versioned exact-hash record in `arbitration_plans` | Draft generation and plan-review UI |
| Filing draft | Specifically approved immutable `arbitration_draft_versions` record | Preview and governed filing export |

Generic matrix create/update APIs cannot set approval, verification, readiness, permission, approver, or approval-time fields. Approval state is written only by dedicated review services. Paragraph response edits accept only response position, reason, approved selected-source IDs, and evidence-gap fields.

## Rollout configuration

| Setting | Safe default | Purpose |
|---|---:|---|
| `ARBITRATION_ENGINE_DEFAULT` | `arbitration_v2` | Server-selected authoritative engine |
| `ARBITRATION_ENGINE_ROLLOUT_MODE` | `off` | `off`, `shadow`, `canary`, `primary`, or `forced_v2` |
| `ARBITRATION_ENGINE_CANARY_PERCENT` | `0` | Deterministic tenant/project/request bucket percentage |
| `ARBITRATION_ENGINE_CANARY_TENANT_IDS` | empty | Explicit tenant allowlist |
| `ARBITRATION_ENGINE_CANARY_PROJECT_IDS` | empty | Explicit project allowlist |
| `ARBITRATION_ENGINE_GRAPH_VERSION` | `v1` | Persisted graph contract version |
| `ARBITRATION_ENGINE_STATE_SCHEMA_VERSION` | `1` | Minimal checkpoint state schema |
| `ARBITRATION_ENGINE_MAX_CHECKPOINT_BYTES` | `262144` | Fail-closed checkpoint size ceiling |
| `ARBITRATION_ENGINE_CHECKPOINT_RETENTION_DAYS` | `30` | Checkpoint TTL |
| `ARBITRATION_ENGINE_RETRY_BUDGET` | `3` | Validation/remediation ceiling |
| `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED` | `false` | Required before `primary` can select LangGraph |
| `ARBITRATION_REVIEWER_ROLE_MATRIX` | built-in policy | Optional `gate=role,role;gate=role` overrides |

The client-provided `requested_engine` is advisory telemetry only and cannot select the authoritative engine. `primary` silently fails safe to `forced_v2` until production acceptance is true.

## Deployment gates

Do not enable canary or primary until all of the following pass in a production-like environment:

1. `langgraph==1.2.9` and `langgraph-checkpoint-mongodb==0.4.0` import in the backend runtime.
2. Both arbitration migrations apply to a restored production-sized MongoDB copy and their indexes complete without duplicate-key failures.
3. Mongo checkpoint interrupt/restart/resume tests prove the last checkpoint is recovered and raw legal text is absent.
4. Redis worker process-kill/retry tests prove bundle failures raise and are retried without duplicate exports.
5. Cross-tenant/project HTTP tests and reviewer-role/step-up tests pass with the real application fixture.
6. Serial/parallel matrix revision hashes match on representative SoC, SoD, Counterclaim, and Rejoinder cases.
7. Shadow telemetry shows no authoritative candidate writes and acceptable artifact parity.
8. Backup/restore and TTL behavior are verified. Checkpoint TTL must not delete active-run state.
9. Authenticated browser tests cover document/version selection, material questions, every approval gate, paragraph responses, preview, filing export, cancellation, and fallback.

## Monitoring and audit

Monitor:

- workflow counts and age by `status`, `engine`, `rollout_mode`, `current_node`, tenant, and project;
- stale-state `409` rate and reviewer-role `403` rate;
- checkpoint age, size, count, and write errors;
- effect claims left in `claimed`, duplicate-key errors, and retry exhaustion;
- matrix/readiness/source drift rejections;
- validation blockers and remediation-cycle exhaustion;
- shadow revision parity and unexpected authoritative candidate effects;
- filing authorization, citation/exhibit audit failures, export duration, and bundle retry results;
- forced-v2 use and fallback-denied events after authoritative effects.

The operations checkpoint endpoint returns identifiers plus redacted hashes only. It requires arbitration-admin authorization. Workflow events, exact-hash approval receipts, immutable snapshots, effects, plans, draft lineage, and export authorizations form the audit chain.

## Recovery

1. Read the workflow state and event log; do not replay domain writes directly.
2. Inspect redacted checkpoint history through the operations endpoint.
3. If a run is paused, resume with its current `state_version`. A stale version must return `409`.
4. If a worker failed before completing an effect, retry the same effect key. A different input hash must be rejected.
5. Use force-v2 only with arbitration-admin permission and step-up authentication. Fallback is prohibited after a completed authoritative draft, approval, export, or equivalent effect.
6. If checkpoint storage is unavailable, keep rollout `off` or `forced_v2`; do not enable a fail-open graph path.

## Rollback

Application rollback is configuration-first:

1. Set `ARBITRATION_ENGINE_ROLLOUT_MODE=forced_v2` and `ARBITRATION_ENGINE_DEFAULT=arbitration_v2`.
2. Leave existing graph runs, snapshots, events, effects, plans, receipts, and checkpoints intact for audit and recovery.
3. Do not delete approved immutable versions or fabricate replacement approvals.
4. Roll back application code only after confirming the v2 path reads records produced during the compatibility period.
5. Migration downgrade functions remove only new indexes. They intentionally do not restore unverifiable historical approvals or statuses.
6. Restore MongoDB from the pre-migration backup only if index rollback is insufficient and the data-restoration impact has been approved.

## Current acceptance classification

- Phase 0 controls: implemented and locally unit-tested.
- Workflow foundation, deterministic routing, plans, validation, APIs, and frontend: implemented and locally unit/build-tested.
- Official LangGraph checkpoint lifecycle: implemented, but Mongo checkpoint runtime/restart testing is pending dependency-parity infrastructure.
- HTTP tenant isolation: existing tests were invoked but skipped because the local app fixture was unavailable.
- Redis recovery, external service integration, production-like load, backup/restore, and authenticated browser flows: not tested in this implementation task.
- Production deployment: not performed.
