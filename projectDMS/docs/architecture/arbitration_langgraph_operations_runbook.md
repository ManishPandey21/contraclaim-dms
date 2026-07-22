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
| `ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID` | empty | Operator-controlled identifier for the signed Phase-6 acceptance record |
| `ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256` | empty | SHA-256 binding engine decisions to the immutable acceptance record |
| `ARBITRATION_ENGINE_PRIMARY_PERCENT` | `0` | Deterministic primary percentage, independent of canary scope |
| `ARBITRATION_ENGINE_PRIMARY_TENANT_IDS` | empty | Explicit primary tenant allowlist |
| `ARBITRATION_ENGINE_PRIMARY_PROJECT_IDS` | empty | Explicit primary project allowlist |
| `ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY` | `true` | Fail closed unless the scoped acceptance window is cutover-eligible |
| `ARBITRATION_ENGINE_MIN_PLEADING_TYPE_SAMPLE` | `1` | Minimum accepted sample for each of SoC, SoD, Counterclaim, and Rejoinder |
| `ARBITRATION_ENGINE_ACCEPTANCE_WINDOW_RUNS` | `500` | Most-recent scoped runs used for cutover readiness |
| `ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE` | `active` | `active`, `read_replay_only`, or reserved fail-closed `retired` state |
| `ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL` | empty | ISO date through which v2 read/replay compatibility is retained |
| `ARBITRATION_REVIEWER_ROLE_MATRIX` | built-in policy | Optional `gate=role,role;gate=role` overrides |
| `FILING_EXPORT_QUEUE_ENABLED` | `false` (`true` in production Compose) | Fail-closed switch for durable filing exports |
| `FILING_EXPORT_QUEUE_REDIS_URL` | application Redis URL | Dedicated Redis database/connection for export delivery |
| `FILING_EXPORT_QUEUE_NAME` | `arbitration_filing_export_queue` | Ready list |
| `FILING_EXPORT_QUEUE_PROCESSING_NAME` | `arbitration_filing_export_processing` | Leased in-flight list |
| `FILING_EXPORT_QUEUE_DEADLETTER_NAME` | `arbitration_filing_export_deadletter` | Exhausted/non-transient jobs |
| `FILING_EXPORT_QUEUE_VISIBILITY_TIMEOUT_SECONDS` | `300` | Lease expiry before recovery may redeliver |
| `FILING_EXPORT_QUEUE_HEARTBEAT_SECONDS` | `20` | Active worker lease renewal interval |
| `FILING_EXPORT_QUEUE_MAX_RETRIES` | `3` | Bounded transient retry count |
| `FILING_EXPORT_QUEUE_METADATA_TTL_SECONDS` | `604800` | Completed job metadata retention |
| `START_FILING_EXPORT_QUEUE_WORKERS` | `false` (`true` on contract worker) | Ensures only worker processes consume exports |

The client-provided `requested_engine` is advisory telemetry only and cannot select the authoritative engine. `primary` fails safe to `forced_v2` until production acceptance, receipt, scoped health, and pleading coverage are valid. Force-v2 tenant/project lists and the global pause switch take precedence over every rollout mode. Every decision stores the policy version, reason, deterministic decision hash, acceptance-receipt hash, and v2 compatibility mode; tenant and project identifiers are not copied into the decision audit event.

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

`GET /api/arbitration/cases/{case_id}/workflows/operations/health` requires `ARBITRATION_ADMIN`. The case supplies the authorized tenant/project scope; the report evaluates that scope's bounded acceptance window and emits no identifiers. It reports sample counts, failure/fallback/parity/source-drift rates, checkpoint-sync and stale-pause signals, per-pleading coverage, receipt/retention validity, cutover blockers, and threshold alerts. It returns `insufficient_data` until both workflow and shadow samples meet the configured minimum; readiness is a necessary gate, not acceptance by itself.

## Phase-6 controlled primary procedure

1. Complete every deployment gate below for all four pleading types and preserve the evidence as an immutable acceptance record.
2. Obtain operator, security, and legal stakeholder sign-off. Record a stable receipt ID and the SHA-256 of the exact accepted artifact.
3. Set a future `ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL`; keep compatibility mode `active` while any tenant/project can still be routed to v2 or require fallback writes.
4. Configure one explicit tenant or project allowlist entry, or a small non-zero primary percentage. Keep health enforcement enabled.
5. Confirm the scoped operations-health response has `primary_cutover.eligible=true` before setting production acceptance true and rollout mode to `primary`.
6. Expand one bounded scope at a time. Stop on any blocker or P0/P1 event using the global pause or force-v2 scope, preserving runs and checkpoints.
7. Move to `read_replay_only` only after every new-run scope is LangGraph and the agreed parity/recovery window has passed. In this state, new v2 creation and graph-to-v2 fallback writes are rejected, while historical v2 records remain readable/replayable.
8. Treat `retired` as a separate deprecation decision after the retention date and audit/legal approval. Production startup rejects it during primary rollout because v2 read/replay removal is not enabled by this implementation.

Production startup rejects primary rollout unless LangGraph is the default, acceptance is true, the receipt and explicit scope are valid, health enforcement is enabled, and the v2 compatibility date is current. The repository defaults remain rollout `off`, acceptance `false`, empty primary scope, and v2 `active`.

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

### Filing-export worker recovery

1. Check ready, processing, and dead-letter lengths plus worker logs. Do not manually enqueue a different job ID for the same export.
2. A worker atomically moves a job to processing and owns it with a lease token. Heartbeats extend only the matching token; stale workers cannot acknowledge or fail another delivery.
3. If the worker or Redis connection dies, restart Redis/worker and let visibility recovery move only expired processing jobs back to ready. Active leases must not be stolen.
4. Retry the same deterministic effect key. Mongo's unique effect record and deterministic export ID make a completed artifact reusable; an acknowledgement failure after the Mongo effect commits must not regenerate the file.
5. Inspect dead-letter metadata before replay. Retry only infrastructure-transient failures; authorization, readiness, citation, exhibit, drift, or legal blockers require corrective human action.
6. Cleanup drills must use namespaced keys/records and delete only those exact artifacts. Never flush a Redis database or remove a production data volume.

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
- Phase-6 source controls are deployed. Primary activation remains blocked by incomplete all-pleading samples, external integration, every-node recovery, browser/legal acceptance, and stakeholder sign-off.
- Official LangGraph checkpoint lifecycle is implemented; production restart evidence covers document selection and active post-TTL resume, not every Phase-4 node/gate.
- Ten production-image signed-JWT two-account/step-up tests pass; a real signed-in browser and separated legal-review workflow remain pending.
- Redis duplicate/lease/visibility recovery, actual Redis stop/restart, bounded 40-job load, Qdrant outage/recovery, TTL expiry/resume, isolated restore, and cleanup passed in production.
- Live Qdrant vector round trip passes. Live OpenAI PDF extraction and FalkorDB vector storage fail; S3/model outage and sustained end-to-end load remain pending.
- Production runtime remains `arbitration_v2`, rollout `off`, primary `0`, and acceptance `false`.
## Monitoring assets

Provision `config/monitoring/arbitration-langgraph-dashboard.json` in Grafana and
load `config/monitoring/arbitration-langgraph-alerts.yml` into Prometheus. The
dashboard intentionally uses bounded labels only; run, tenant, evidence and
draft identifiers remain in the access-controlled audit repositories rather
than metrics. Treat any critical rollout alert, evidence ratio below 1, terminal
export block, unresolved checkpoint sync, or invalid acceptance receipt as a
fail-closed condition. Do not enable or expand primary rollout until the formal
acceptance receipt resolves for the tenant/project scope.
