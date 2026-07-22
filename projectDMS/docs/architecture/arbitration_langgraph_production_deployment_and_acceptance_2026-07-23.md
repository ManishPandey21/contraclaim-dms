# Arbitration LangGraph Production Deployment and Acceptance Record

**Deployment date:** 2026-07-23  
**Environment:** ContraClaim production (`/opt/contraclaim-dms/projectDMS`)  
**ContraClaim source commit:** `8b57c619de9f8c66e1629be40f3c1cd8ad635f44`  
**projectDMS equivalent commit:** `24ee3d03abbc10f214805727eaae3ffce852ae8d`  
**Decision:** **Implemented but not production accepted**  
**Authoritative engine:** `arbitration_v2`  
**Rollout:** `off`, primary percentage `0`, production acceptance `false`

## 1. Executive decision

The node-owned Arbitration Pleadings LangGraph implementation is committed, published to both repositories, deployed, migrated, and running behind fail-closed rollout controls. The backend and filing-export worker were rebuilt from ContraClaim commit `8b57c619de9f8c66e1629be40f3c1cd8ad635f44`; the deployed frontend includes the node-owned workflow/timeline changes. The official graph owns evidence binding, bounded analysis, deterministic matrix merge, planning, drafting, eight validation branches, bounded remediation, draft approval commit, and governed filing-export creation through typed state updates, immutable hashes, idempotent effect keys, leases, and compare-and-set transitions.

Production acceptance is **withheld**. There are no production arbitration cases, drafts, workflow runs, approvals, or exports from which to obtain four-pleading legal acceptance. The required authenticated browser journey and process-kill matrix at every node and human gate are incomplete. Actual Qdrant outage, Redis restart, Mongo replica failover, TTL expiry/resume, object-storage round trip, live model/PDF round trip, and isolated restore passed; actual S3-provider outage and model rate-limit/outage recovery were not induced. Credentials printed during an operator diagnostic must be rotated before acceptance.

Accordingly:

- LangGraph must not become primary.
- `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED` must remain `false`.
- rollout mode must remain `off` (or a more restrictive forced-v2 mode), with primary percentage `0`.
- `arbitration_v2` remains the active, supported fail-safe.
- no production-acceptance receipt or rollout scope may be created from this record.

## 2. Implementation and active-path evidence

| Responsibility | Active implementation | Control evidence | Verification status |
| --- | --- | --- | --- |
| Graph command ownership | `backend/rbac_backend/services/arbitration_drafting/graph_commands.py`: `ArbitrationGraphCommandExecutor.execute`, `_analyze_node`, `_merge_analysis`, `_build_plan`, `_generate_draft`, `_validate_branch`, `_merge_validation`, `_remediate`, `_commit_draft_approval`, `_create_filing_export` | Each authoritative node uses `_run_effect` for effect reservation, lease renewal, input-hash collision rejection, completion/failure recording, and run CAS | Implemented; focused local and production-image tests passed. Every-node process-kill acceptance remains incomplete. |
| Official graph and recovery | `langgraph_engine.py`: `build_arbitration_graph`, `command_or`, `analysis_replay_marker`, `_recovery_state`, `_checkpoint` | Official `StateGraph`, MongoDB saver, ID/hash-only state, durable interrupts, cumulative reconstruction, cancellation and replay markers | Implemented; real-Mongo TTL deletion and resume passed. Full node/gate kill matrix incomplete. |
| Immutable workflow records | `workflow_repository.py`: `create_snapshot`, `claim_effect`, `renew_effect`, `complete_effect`, `transition`, `record_approval`, `commit_approval`, `create_plan` | Unique indexes, immutable hashes, optimistic `state_version`, deterministic effect keys, pending/committed receipts | Implemented and tested for unit/concurrency paths; live four-route effects absent. |
| Server-owned orchestration | `workflow_service.py`: `create`, `approve_gate`, `resume`, `cancel`, `fallback`; `routers/arbitration_drafting.py` workflow routes | Client cannot choose authoritative engine; stale resume rejected; step-up force-v2; fallback bound to original snapshot and denied after authoritative effect | Implemented; signed-JWT production-image tests passed. Signed-in browser proof incomplete. |
| Governed domain/export path | `workflow_governance.py`, `case_workspace.py`, `service.py` | Exact-hash readiness/plan/validation/legal/draft chain, authoritative source rehydration, immutable export manifest | Implemented and negative tests passed; no real four-pleading export acceptance. |
| Redis filing export | `filing_export_queue.py`: `enqueue`, `requeue_orphaned_jobs`, `_process_job`, `_heartbeat` | Atomic move/lease token, heartbeat, visibility recovery, transient-only bounded retry, dead letter, deterministic job/effect keys | Real-Redis duplicate, expiry, restart and bounded-load drills passed. |
| Rollout governance | `core/config.py`, `engines/policy.py`, `acceptance.py` | Primary requires acceptance; server-side receipt and scope validation; v2 compatibility retained | Live settings verified fail-closed. |

The former complete-workflow legacy adapter is not called from an authoritative LangGraph node. `ArbitrationWorkflowService` retains gate/API coordination and recovery synchronization; substantive graph effects are dispatched by `ArbitrationGraphCommandExecutor`. `arbitration_v2` remains a separate fallback engine and cannot overwrite an existing candidate, approval, or export.

## 3. Backup, migration, and deployment record

### 3.1 Pre-change backup

Backup set `20260723-001500-node-owned-predeploy` was created before production mutation under `/var/backups/contractdms`:

- Mongo archive: `mongo/contraclaim-20260723-001500-node-owned-predeploy.archive.gz`
- backend uploads, Qdrant data/snapshots, FalkorDB, and Redis volume archives
- protected environment copy with mode `0600`
- SHA-256 manifest: `manifests/checksums-20260723-001500-node-owned-predeploy.sha256`

All recorded checksums passed verification. Existing backups were preserved; no volume or Docker prune operation was performed.

### 3.2 Migration

Migration `v20260722_0004_arbitration_acceptance_governance.py` was dry-run first, produced no warnings, and was then applied. The production migration ledger reported all 13 migrations current. The migration is additive/index-oriented and retains v2 compatibility.

### 3.3 Deployment sequence

1. Published the implementation independently to ContraClaim and projectDMS.
2. Fast-forwarded production from the pre-change `410ae3e5` baseline to `8b57c619`.
3. Rendered Compose configuration and validated migration compatibility.
4. Rebuilt and recreated backend, frontend, and filing-export worker as applicable.
5. Rebuilt the worker again from `8b57c619` after the final valid-PDF integration fixture, ensuring source-image parity.
6. Rolled Mongo replica members one at a time after adding `nofile` soft/hard `64000` and replica-state-aware health checks.
7. Verified public health, backend readiness, database/queue connectivity, service logs, and worker status.

The source tree deployed to production is committed and published. No uncommitted local file was deployed.

## 4. Tests and drills performed

| Test or drill | Infrastructure | Actual result | Acceptance effect |
| --- | --- | --- | --- |
| Arbitration backend suite | Local committed worktree | 171 passed, 11 skipped | Code regression confidence; skipped real-infrastructure cases are not counted as passes. |
| Arbitration page tests | Local frontend | 5 passed | Component behavior only; not authenticated browser acceptance. |
| Frontend production build | Local frontend | Passed; 3,881 modules transformed | Build accepted. |
| Deployment configuration | Local | 6 passed | Includes Mongo descriptor limit and replica-state health assertions. |
| Signed-JWT isolation/step-up | Production backend image | 10 passed | Exercises actual deployed security code with a two-tenant test database; not two real production accounts. |
| Redis export integration | Production Redis | Passed before and after an actual Redis restart | 25 duplicate enqueues produced one job; expired lease recovered; 40 jobs across four workers produced exactly one effect each; queues and drill keys were cleaned. |
| Mongo checkpoint TTL/resume | Production Mongo/checkpointer | Passed | Actual TTL monitor removed expired terminal checkpoint; active workflow reconstruction resumed to the next gate without duplicating effects. |
| Qdrant outage/recovery | Production Qdrant | Passed | Backend readiness reported Qdrant degraded while stopped and recovered after restart. |
| OpenAI embedding/Qdrant round trip | Configured production services | Passed | Namespaced temporary collection written, searched, and deleted. |
| OpenAI PDF file-input round trip | Configured production model service | Passed after moving to Responses API `input_file` with `user_data` upload and a valid PDF fixture | Proves configured file extraction, not full legal drafting acceptance. |
| Object-storage round trip | Production S3 bucket | Passed | Namespaced object put/get/delete completed and was cleaned. Provider outage was not induced. |
| Mongo rolling restart/failover | Production replica set | Passed | Quorum retained; backend readiness remained available. |
| Isolated Mongo restore | Production host, isolated database | Passed after descriptor/health hardening | Archive restored with exit 0. Live: 152 collections, 13 migrations, 39 sampled documents, 3 checkpoint indexes. Predeploy restore: 150 collections, 12 migrations, 39 sampled documents, 3 checkpoint indexes. Isolated database was dropped after verification. |
| Redis cleanup | Production Redis | Passed | No ready, processing, or dead-letter residue from namespaced drills. |
| Browser workflow | In-app browser runtime | Blocked | Runtime failed before session creation with `failed to write kernel assets ... os error 3`; no browser acceptance claim. |
| Four-type legal review | Production | Not performed | Production has zero arbitration cases/drafts/runs/approvals/exports and no assigned legal reviewer evidence. |
| Every-node pre/during/post-effect kill matrix | Production-like real Mongo | Incomplete | TTL recovery and selected boundary tests passed, but every graph node and gate was not killed at all three timing boundaries. |

## 5. Defects found and corrected during deployment

| Severity | Defect | Root cause | Correction | Regression evidence |
| --- | --- | --- | --- | --- |
| P1 | Live graph fan-out/TTL reconstruction failed before merged analysis | `analyze_quantum_expert` incorrectly expected the merged artifact that is produced after fan-out | `langgraph_engine.py` consumes the immutable input snapshot during live fan-out and uses the already merged artifact only for checkpoint reconstruction | `test_analysis_fanout_resume_consumes_snapshot_before_merged_artifact_exists`; real-Mongo TTL/resume passed. |
| P1 | First isolated restore crashed a Mongo member with WiredTiger `Too many open files` | Mongo containers inherited `nofile=1024`; ping-only health marked recovering members healthy | `docker-compose.mongo-replicaset.yml` applies `64000` to every member and health requires replica state PRIMARY or SECONDARY | Deployment config test; rolling restart and repeated isolated restore passed. |
| P1 | Configured PDF model round trip rejected uploaded document | Chat Completions file-part path and assistants-purpose upload did not match the configured API behavior | `openai_service.py` uses Responses API `input_file`, upload purpose `user_data`, `store=False`, and `output_text` | Configured live PDF integration passed with a generated valid PDF. |
| P2 | First replacement PDF smoke fixture was structurally invalid | Hand-authored minimal PDF omitted valid cross-reference/layout details | Integration fixture now creates a valid PDF with ReportLab | Configured live test passed. |

No unresolved code-level P0 approval, provenance, or export bypass was reproduced after the corrections. The credential exposure described below is an unresolved operational P0.

## 6. Global acceptance criteria

| # | Criterion | Decision | Evidence / blocker |
| ---: | --- | --- | --- |
| 1 | Phase-0 bypasses closed and negative-tested | Passed in code and API tests | Server-owned roles, actor separation, governance-chain and direct bypass tests passed; browser repetition remains part of criterion 13. |
| 2 | Immutable authoritative snapshots and hashes | Passed in code; not exercised for production legal matters | Snapshot/effect repositories and node contracts are active. Production contains no authoritative arbitration run. |
| 3 | No raw legal content in checkpoints/ops | Passed | Allowlist, size-limit, redaction and ops authorization tests passed. |
| 4 | Crash/restart at every node and gate without duplicate effects | **Failed acceptance** | Selected replay, real TTL, Redis, and Mongo restart drills passed; the exhaustive node/gate timing matrix is incomplete. |
| 5 | Four routes enforce predecessors and gates | Passed in automated tests; not legally accepted | SoC, SoD, Counterclaim, and Rejoinder route tests pass; no production cases. |
| 6 | Rejoinder permission receipt for required new matter | Passed in code; not legally exercised | Separate fields and exact-hash approval path exist; no reviewed production Rejoinder. |
| 7 | Export binds approved immutable version/current readiness/passing audits | Passed in code; no representative filing acceptance | Governed export and drift/duplicate tests pass. |
| 8 | No client/edit/generate self-approval | Passed in direct API tests; browser incomplete | Production-image signed-JWT negative tests passed. |
| 9 | Deterministic, auditable parallel merge | Passed in automated tests | Serial/parallel stable hash and source/row revision assertions pass. |
| 10 | Bounded transient retry without duplicate effects | Passed for Redis export; partial for the complete graph | Real Redis restart/expiry/load passed; exhaustive graph-node failure injection remains incomplete. |
| 11 | Safe v2 fallback using original snapshot | Passed in automated tests; production fallback drill limited | v2 active, graph overwrite denied after authoritative effects, immutable snapshot retained. |
| 12 | Four golden cases pass legal review | **Failed acceptance** | No representative production cases or legal reviewer receipts exist. |
| 13 | Real dependencies, load, backup/restore and authenticated browser pass | **Failed acceptance** | Mongo/Redis/Qdrant/S3 success/model success/restore drills passed; S3/model outage, sustained end-to-end load, and authenticated browser remain incomplete. |
| 14 | Runbooks cover operational recovery | Passed | Operations runbook covers pause, checkpoint, source drift, export lease, fallback, cleanup and rollback; this record adds the tested restore constraints. |

Because criteria 4, 12, and 13 have not passed in full, primary activation is prohibited.

## 7. Security, authorization, and operational limitations

### 7.1 Confirmed controls

- direct unauthenticated workflow/checkpoint/export requests fail with `401`;
- edit/generate-only approval attempts and cross-tenant/project reads are covered by backend negative tests;
- stale/concurrent state versions are compare-and-set protected and stale resumes return `409`;
- force-v2 and checkpoint inspection require privileged authorization and step-up;
- approval/export receipts are exact-artifact scoped and invalid after drift;
- fallback is rejected once a graph candidate, approval, export, or equivalent authoritative effect exists.

### 7.2 Remaining security evidence

The signed-JWT suite used the deployed application code but not two independently signed-in production accounts. Browser-hidden actions, role separation, project switching, step-up prompts, full legal approval, and export must still be repeated through a functioning authenticated browser and direct production-like API clients.

### 7.3 Credential rotation blocker

An operator diagnostic emitted production environment secrets into command output during this deployment session. Those values are not reproduced here. All potentially exposed credentials and tokens must be inventoried, rotated, deployed, and smoke-tested, with a separate security sign-off. Until rotation is complete, this is a **P0 operational security blocker** to production acceptance.

## 8. Rollback and restore procedure

Rollback is configuration-first and preserves audit evidence:

1. Keep `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false`, rollout `off` or `forced_v2`, primary percentage `0`, and default engine `arbitration_v2`.
2. Stop only new graph selection; do not delete graph runs, checkpoints, snapshots, effects, plans, receipts, candidates, or exports.
3. Redeploy the last approved application commit through the normal fast-forward/revert workflow. Do not reset the production working tree or deploy uncommitted files.
4. Re-run the migration ledger. Additive arbitration indexes may remain in place while v2 runs; use migration downgrades only after index-use review.
5. Rebuild backend, frontend, and worker from the selected committed revision; restart one Mongo replica at a time only if database configuration changed.
6. Verify `/health`, `/health/ready`, Mongo PRIMARY/SECONDARY state, Redis ping and queue lengths, Qdrant status, S3 authorization, checkpoint indexes, worker logs, and public frontend availability.
7. Restore data only when corruption/loss is confirmed and an authorized recovery owner approves it. Restore the verified archive to an isolated database first, compare counts/indexes/hashes, then schedule the live restore with writes stopped.

The isolated data-restore portion was exercised successfully. A full application code downgrade was not performed because the deployed services were healthy; therefore code rollback remains a documented, unexercised procedure rather than a passed drill.

## 9. Remaining action plan

1. **P0 — Rotate exposed credentials.** Inventory every value present in the diagnostic, rotate at each provider, update protected production configuration, restart affected services, and retain security approval without recording secret values.
2. **P1 — Complete exhaustive node/gate recovery.** In an isolated clone using real Mongo and Redis, kill the API before, during-before-effect, and after-effect-before-checkpoint for every graph node and human gate; assert one matrix row, candidate/version, approval and export effect.
3. **P1 — Complete authenticated two-account browser acceptance.** Use separate author/reviewer accounts and a second tenant/project for every action and direct-API bypass across all four pleading routes.
4. **P1 — Obtain four-type legal acceptance.** Load reviewed representative SoC, SoD, Counterclaim, and Rejoinder matters; retain exact-hash legal decisions, observations, grounding checks and filing-output approvals.
5. **P1 — Complete remaining external-failure/load drills.** Induce namespaced S3/object-storage failure, configured model timeout/rate-limit/outage, sustained bounded end-to-end concurrency, and cancellation during active processing; verify recovery classification and effect uniqueness.
6. **P1 — Test application rollback.** Exercise the committed-code rollback procedure in an isolated production clone and record RTO/RPO, service health, v2 replay compatibility, and artifact preservation.
7. **P2 — Complete historical data review.** Audit ambiguous legacy defence/rejoinder projections and unversioned source rows; backfill only deterministic mappings and leave all others explicitly `missing`/`needs_review`.
8. **P2 — Provision and exercise monitoring assets.** Confirm production dashboard/rules are loaded and fire/recover every critical arbitration alert without high-cardinality or legal-content labels.

Only after these actions and all 14 criteria pass may an authorized operator create the formal acceptance receipt and consider a narrowly scoped primary rollout. Deprecation of `arbitration_v2` is a later, separately evidenced decision.
