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
| `ARBITRATION_ENGINE_FORCE_V2_TENANT_IDS` | empty | Emergency tenant denylist; overrides canary/primary selection |
| `ARBITRATION_ENGINE_FORCE_V2_PROJECT_IDS` | empty | Emergency project denylist; overrides canary/primary selection |
| `ARBITRATION_ENGINE_ROLLOUT_PAUSED` | `false` | Global fail-safe; immediately selects v2 for new runs |
| `ARBITRATION_ENGINE_GRAPH_VERSION` | `phase4-v1` | Persisted graph contract version |
| `ARBITRATION_ENGINE_STATE_SCHEMA_VERSION` | `2` | Minimal checkpoint state schema |
| `ARBITRATION_ENGINE_MAX_CHECKPOINT_BYTES` | `262144` | Fail-closed checkpoint size ceiling |
| `ARBITRATION_ENGINE_CHECKPOINT_RETENTION_DAYS` | `30` | Checkpoint TTL |
| `ARBITRATION_ENGINE_RETRY_BUDGET` | `3` | Validation/remediation ceiling |
| `ARBITRATION_ENGINE_MIN_ACCEPTANCE_SAMPLE` | `20` | Minimum workflow and shadow samples before readiness can be reported |
| `ARBITRATION_ENGINE_MIN_SHADOW_PARITY_PERCENT` | `99` | Minimum redacted shadow-comparison parity |
| `ARBITRATION_ENGINE_MAX_FAILURE_RATE_PERCENT` | `2` | Workflow failure alert threshold |
| `ARBITRATION_ENGINE_MAX_FALLBACK_RATE_PERCENT` | `5` | Fallback alert threshold |
| `ARBITRATION_ENGINE_MAX_PAUSE_HOURS` | `72` | Stale human-interrupt warning threshold |
| `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED` | `false` | Required before `primary` can select LangGraph |
| `ARBITRATION_REVIEWER_ROLE_MATRIX` | built-in policy | Optional `gate=role,role;gate=role` overrides |

The client-provided `requested_engine` is advisory telemetry only and cannot select the authoritative engine. `primary` silently fails safe to `forced_v2` until production acceptance is true. Force-v2 tenant/project lists and the global pause switch take precedence over every rollout mode.

## Shadow contract and acceptance thresholds

In `shadow` mode, v2 remains authoritative. At each recorded workflow milestone, the candidate executes the official graph in an isolated in-memory checkpoint namespace and reuses the same immutable domain inputs for a serial parity projection. It cannot create an authoritative draft, approval, or export. The immutable `shadow_comparison` artifact and event contain only hashes, counts, statuses, durations, and these dimensions:

1. evidence set;
2. matrix rows;
3. readiness;
4. section coverage;
5. citation validity;
6. validation blockers;
7. output latency; and
8. human interventions and projected gate.

`GET /api/arbitration/cases/{case_id}/workflows/operations/health` is case-scoped and requires `ARBITRATION_ADMIN`. It reports identifier-free sample counts, failure/fallback/parity/source-drift rates, checkpoint-sync and stale-pause signals, and threshold alerts. It returns `insufficient_data` until both workflow and shadow samples meet the configured minimum; this is not acceptance.

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

Prometheus signals added for Phase 5 are `contractdms_arbitration_shadow_comparisons_total`, `contractdms_arbitration_shadow_latency_ms`, `contractdms_arbitration_workflow_alert`, and `contractdms_arbitration_workflow_fallbacks_total`. Labels are bounded and never include tenant, project, run, evidence, user-entered fallback text, or draft content.

The operations checkpoint endpoint returns identifiers plus redacted hashes only. It requires arbitration-admin authorization. Workflow events, exact-hash approval receipts, immutable snapshots, effects, plans, draft lineage, and export authorizations form the audit chain.

## Recovery

1. Read the workflow state and event log; do not replay domain writes directly.
2. Inspect redacted checkpoint history through the operations endpoint.
3. If a run is paused, resume with its current `state_version`. A stale version must return `409`.
4. If a worker failed before completing an effect, retry the same effect key. A different input hash must be rejected.
5. Use force-v2 only with arbitration-admin permission and step-up authentication. Fallback is prohibited after a completed authoritative draft, approval, export, or equivalent effect.
6. If checkpoint storage is unavailable, keep rollout `off` or `forced_v2`; do not enable a fail-open graph path.

### Shadow or canary incident

1. Set `ARBITRATION_ENGINE_ROLLOUT_PAUSED=true` for an immediate global stop on new graph selections, or add the affected tenant/project to the force-v2 lists.
2. Confirm the operations health report and Prometheus alert identify the same threshold breach.
3. Preserve runs, shadow artifacts, checkpoints, and events. Do not delete divergent evidence.
4. Existing graph runs remain recoverable from their immutable snapshot; use the step-up-protected per-run fallback only before an authoritative effect.
5. Resume rollout only after the failure, fallback, shadow parity, checkpoint-sync, and unresolved-source-drift signals return within threshold for a new acceptance window.

### Backup, restore, and checkpoint cleanup drill

1. Back up MongoDB, object storage, Qdrant, Redis, uploads, and configuration using the existing production backup procedure. Record artifact hashes and timestamps.
2. Restore to an isolated production-like environment; never test a destructive restore against the live primary data volumes.
3. Verify workflow runs, snapshots, effects, approvals, events, plans, checkpoint tuples, and checkpoint writes remain linked and tenant scoped.
4. Resume paused workflows at every Phase-4 graph node/gate and verify no duplicate authoritative effects.
5. Verify the runtime-compatible `created_at_1` TTL index exists on both checkpoint collections with the configured 30-day value.
6. Insert an expired terminal-run checkpoint and confirm TTL cleanup. Separately verify active paused checkpoints remain inside retention and can resume.
7. Record recovery point, recovery time, restored counts, TTL evidence, and operator sign-off in the acceptance record.

## Rollback

Application rollback is configuration-first:

1. Set `ARBITRATION_ENGINE_ROLLOUT_PAUSED=true`, `ARBITRATION_ENGINE_ROLLOUT_MODE=forced_v2`, and `ARBITRATION_ENGINE_DEFAULT=arbitration_v2`.
2. Leave existing graph runs, snapshots, events, effects, plans, receipts, and checkpoints intact for audit and recovery.
3. Do not delete approved immutable versions or fabricate replacement approvals.
4. Roll back application code only after confirming the v2 path reads records produced during the compatibility period.
5. Migration downgrade functions remove only new indexes. They intentionally do not restore unverifiable historical approvals or statuses.
6. Restore MongoDB from the pre-migration backup only if index rollback is insufficient and the data-restoration impact has been approved.

## Current acceptance classification

- Phase 0 controls: implemented and locally unit-tested.
- Workflow foundation, deterministic routing, plans, validation, APIs, and frontend: implemented and locally unit/build-tested.
- Phase-5 source hardening: implemented with an official isolated graph shadow, eight-dimension redacted parity artifacts, bounded metrics, threshold alerts, force-v2 scopes, a global pause, and a case-scoped health report.
- Official LangGraph checkpoint lifecycle: implemented; production restart evidence currently covers document selection only, not every Phase-4 node/gate.
- HTTP tenant isolation: production-image tests exist; authenticated two-account browser acceptance remains pending.
- Redis recovery, external service integration, production-like load, TTL expiry timing, backup/restore, and authenticated browser flows: not completed in this implementation task.
- Production deployment: not performed.
