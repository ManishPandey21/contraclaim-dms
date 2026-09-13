# Production cutover checklist — `release/contraclaim-rc1`

**Status: PLAN ONLY. NOT EXECUTED. NOT AUTHORISED.** Writing this authorises
nothing. As of 2026-09-08 the release branch is local — not pushed, not merged,
not deployed — and production has never run it.

**This is the canonical ordering.** Where an older runbook disagrees, this file
wins; where it needs detail, it names the file that carries it. The two runbooks
in `.claude/context/contract-master/` (`PRODUCTION-DEPLOYMENT-RUNBOOK.md`,
`G32-PRODUCTION-MIGRATION-RUNBOOK.md`) are **untracked**, so they do not survive
a clone and their release facts (HEAD `130a5a8`, readiness 38/100, "1
release-introduced migration") are stale. Read them for the reasoning, take the
ordering from here, and re-derive every number.

---

## 0. Preconditions the checklist does not create

| # | Precondition | State on 2026-09-08 |
|---|---|---|
| P1 | Owner authorisation for a production cutover | **NOT GIVEN** |
| P2 | Maintenance window booked, with a reserve | **NOT BOOKED** — book 5–6 h, see §2 |
| P3 | Release branch reaches a deployable branch | **OPEN** — local only, not pushed to either remote |
| P4 | Readiness ≥ 85 for Gate 9 | **OPEN** — re-derive with `scripts/production_readiness_score.py`; a figure quoted here goes stale the moment a bullet is ticked, and this row carried the pre-R-A8S 68 for a phase after it stopped being true |
| P5 | FalkorDB `/data` cutover sequenced with the deploy | **PLANNED, NOT EXECUTED** — `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` |
| P6 | S3 failure-domain disposition confirmed | **OWNER-ACCEPTED DEBT** — confirmed at §8; the standing decisions are listed at the end of this file |

P4 is a Gate 9 condition, not a cutover condition. A deploy can technically
proceed below 85; **Gate 9 cannot close**, and the release is then deployed
without a completed final review. That is an owner decision, and it must be made
in writing before §3.

---

## 1. Release freeze and provenance

- [ ] `git -C <release worktree> status --porcelain` → **0 lines**.
- [ ] Record HEAD and tree SHAs in full. Neither may change after this point.
- [ ] Push the release branch to **both** remotes (`origin`, `contraclaim`). The
      feature branch alone does not reach production.
- [ ] Read the server's own `git branch --show-current` — **every time**. The
      runbook says `main`; production has sat on `codex/*` branches for long
      stretches. Neither answer is durable.
- [ ] Remember the two remotes' `main` branches have diverged and carry the same
      work under different SHAs, so `git branch --contains` answers "is this
      shipped?" with a confident **no** even when an equivalent commit is live.
      Compare subjects and content, or read the deployed artefact.

## 2. Maintenance time budget

- [ ] `EXPECTED_EXECUTION_BUDGET` is **120 minutes**, derived from R-A8M's
      59 m 23 s × 2, rounded up. **Do not plan with 59.**
- [ ] `LATEST_SAFE_STOP = WINDOW_END − 210 min`, so a 4-hour window permits a stop
      no later than `WINDOW_START + 30 min` and leaves nothing for pre-outage
      revalidation. **Book 5–6 hours.**
- [ ] Clients informed; hard recovery start agreed and recorded.

## 3. Final CI and security

- [ ] GitHub Actions green for backend, frontend, dependency scan and Docker
      image scan **on the pushed release branch**. Gate 1 bullet 6 is unearned
      until this run exists.
- [ ] Trivy on the exact CI policy: **0/0, exit 0** for every image that will be
      deployed, with the negative control at exit 1.
- [ ] Every image the window deploys rebuilt `docker build --pull` and
      `scripts/check_image_scan_freshness.py` **exit 0** — reports ≤ 24 h old,
      about the exact `ImageID`, 0 fixable CRITICAL/HIGH. Any fixable
      CRITICAL/HIGH is **NO-GO**. Policy: `docs/IMAGE_FRESHNESS_POLICY.md`.
- [ ] Backend image staleness re-derived **mechanically** against the release
      tree, and every affected image rebuilt and re-scanned before the outage.
- [ ] `scripts/evidence_secret_scan.py` CLEAN on the evidence set, with a
      non-zero measured secret count — a CLEAN with nothing hunted means nothing.

## 4. Pre-outage, with production still online

- [ ] `scripts/pre_deploy_readiness.sh` — 0 failures.
- [ ] `scripts/preflight.py` **inside the backend container**. On the host it
      fails with `No module named 'motor'`, which is an environment artefact and
      not a gate failure.
- [ ] Migration dry run **inside the backend container**:
      `migrate_database --list`, then `--fail-on-warning`.
- [ ] `docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml config`
      renders exit 0 with no unresolved variable. **Do not add the base
      `docker-compose.yml`** — `config` and `ps` still succeed with the wrong file
      set, and only `up` fails, on a missing `client/.env.development`.
- [ ] Verify the server checkout is byte-identical to the running containers
      (tree-wide `sha256sum` compare) before trusting any code read of production.

## 5. Fresh backups

- [ ] `bash scripts/production_backup.sh` — **expect it to stop at
      `falkordb-data`** while persistence is still at `/FalkorDB`. That is
      correct, fail-visible behaviour. Do not soften the check.
- [ ] Mongo logical backup written and `sha256sum -c` verified.
- [ ] Every other volume archive `VALID` under its declared contract.
- [ ] Off-site leg exit 0.

## 6. FalkorDB rescue

- [ ] `bash scripts/falkordb_rescue_archive.sh contraclaim-falkordb-1 <path>` —
      never hand-typed.
- [ ] `python scripts/validate_backup_archive.py <path> --profile redis-persistence`
      → `VALID`, entries at the archive **root**.
- [ ] `sha256sum` recorded.

## 7. Restore proof

- [ ] `bash scripts/falkordb_rescue_restore_drill.sh` → **8/8 PASS**, including
      the `FalkorDB/`-rooted and 89-byte negative controls.
- [ ] `docker commit contraclaim-falkordb-1 contraclaim/falkordb-rollback:<ts>`.

## 8. S3 decision, confirmed

- [ ] The owner confirms or revises the standing acceptance **before** the stop,
      not after — the question and its three options are in
      `docs/READINESS_CONVERGENCE.md` §6. Nothing here changes an AWS resource.

## 9. Time gates and the production stop

- [ ] Read the clock **from the server** (NTP-synced), not from a laptop.
- [ ] **GATE #1** — `scripts/check_maintenance_time_budget.py --emit-authorization`.
      Exit 0 GO / 2 NO-GO / 2 for anything undecidable.
- [ ] `OUTAGE_START` recorded.
- [ ] **GATE #2** — `--confirm`. It re-reads the clock and **recomputes**; a
      paused GO is refused. Nothing between this gate and the stop.
- [ ] `systemctl stop nginx`, then the application tier. Gate #2 sits before
      `stop nginx` because that is already production-mutating; a refusal there
      costs one commented cron line and nothing is stopped.

## 10. FalkorDB `/FalkorDB` → `/data`

- [ ] Execute `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` §4.7 – §4.14.
- [ ] **Variant A** (out-of-band container) unless the owner has accepted
      image-based rollback in writing. Variant B removes the original container
      object and its writable layer.
- [ ] The original container object still exists, stopped, id `de249ee3df20…`.

## 11. Graph parity

- [ ] `GRAPH.LIST` → `contraclaim`. **An empty list is a STOP, not a retry.**
- [ ] 156 nodes, 220 relationships, labels `Contract`, `ContractDocument`,
      `Clause`, `Letter` — re-derived before the stop and matched after.

## 12. Credential rotation

> **Ordering note.** This checklist lists rotation before the image deployment,
> and `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` §4.19 sequences it after
> the new backup has been taken, validated and restore-proved (§4.16 – §4.18).
> **The cutover document wins for everything between §10 and §12 here.** Rotating
> last means a rotation failure cannot be confused with a data-move failure, and
> it costs nothing: the consumers are recreated once either way.

- [ ] Rotate `FALKORDB_PASSWORD` **after** parity holds, so a rotation failure is
      not tangled with a data-move failure.
- [ ] Compose bakes environment at **create** time: every consumer of the derived
      `FALKORDB_URL` / `GRAPHITI_DB_URL` must be **recreated**, not restarted.
- [ ] Confirm no consumer is left on the old credential.

## 13. Release image deployment

- [ ] Rebuild only affected services; a frontend fix is not deployed until the
      `client` image is rebuilt. Confirm by fetching the hashed asset from the
      public edge and reading the compiled code — the chunk name changes on every
      content change, so the old name still being served means the build did not
      ship.
- [ ] `up -d --no-deps <service>`.
- [ ] **Do not relax the startup config gate** to make a deploy pass. It refuses
      placeholder secrets, dev CORS, insecure cookies, fail-open RBAC, and missing
      metrics token / Redis / backup config, and it is deliberate.

## 14. Production migrations

- [ ] `migrate_database --list` inside the backend container.
- [ ] `--apply --fail-on-warning`. **Exit 2 is expected on the first apply** and
      is computed after the migrations ran: read the JSON, confirm the only
      warnings are `20260721_0001`'s two known informational lines, and proceed.
      **Any other warning stops the deploy.**
- [ ] `20260906_0001 permission_name_unique` applied. It is applied to **staging
      only** today. It makes `permissions.name` unique, and the seeder races
      itself without it.
- [ ] There is **no migration rollback**. `runner.py` never calls a downgrade.
      Recovery is roll-forward: fix the cause and re-run `--apply`.

## 15. G32 production migration — SEPARATE AUTHORISATION

- [ ] **Not part of this cutover.** `G32-PRODUCTION-MIGRATION-RUNBOOK.md` requires
      four approvals of its own and its Stage 0 is recorded as blocked.
- [ ] **G32 production state: NOT CERTIFIED**, and stays that way unless it is
      separately authorised, executed and receipted.

## 16. Application startup

- [ ] Config gate passes (it runs first and fails closed).
- [ ] Both workers running; exactly **one** scheduler owner across every running
      service.
- [ ] The web tier is **not** an extraction worker.

## 17. Permissions and indexes

- [ ] Permission catalogue fully seeded, and the superadmin role holds it.
      Seeding is best-effort inside startup — it logs a warning and continues —
      so assert the catalogue rather than trusting the log.
- [ ] `uq_permissions_name` present, 0 duplicates.
- [ ] Task 7.6 page-evidence indexes and migration `20260814_0001` recorded.

## 18. Auth refresh smoke

- [ ] `POST /api/refresh` → **200**, exactly one `token_refresh` audit row, its
      user resolving to the signed-in account, its `resource_id` equal to the
      `session_id` in the reissued token. R-A8M measured 500 here for every user
      and R-A8Q settled it in a deployment; re-measure, do not cite.

## 19. Gate-style health

- [ ] `/health/live` 200, `/health/ready` ready with every dependency ok.
- [ ] `/metrics` — no token 401, wrong token 401, correct `X-Metrics-Token` 200.
- [ ] `rs0` 3/3 with exactly one PRIMARY.
- [ ] Qdrant `contracts` green, point count matched against the before-state.
- [ ] Redis db0/db1 key counts matched against the before-state.

## 20. Backup verification, after the cutover

- [ ] `bash scripts/production_backup.sh` → exit 0, and `falkordb-data` no longer
      stops the run.
- [ ] `python scripts/backup_status.py` → `falkordb-data` **`VALID`** — not
      `UNVERIFIED`, not `UNEVALUATED`.
- [ ] One disposable restore from the **new** production archive reproduces the
      graph inventory. Never restore into `contraclaim_falkordb_data`.

## 21. `post_deploy_verify.sh`

- [ ] 0 failures. In production mode the **edge check is now mandatory**
      (`docs/OPERATIONS.md` §6.1): `PUBLIC_BASE_URL` must be set, TLS, and a
      public DNS name, and an unset value is a failure rather than a skip.
- [ ] Note the historical trap: production has no `document-worker`, so the
      release copy of this script may need the server's own copy. Resolve which
      copy is authoritative **before** the window, not during it.

## 22. Rollback criteria

Roll back on any of:

- [ ] `GRAPH.LIST` empty or missing `contraclaim`, or a parity mismatch;
- [ ] a migration warning that is not `20260721_0001`'s two known lines;
- [ ] `/health/ready` not reaching ready;
- [ ] the public edge not answering;
- [ ] the new backup not validating or not restoring;
- [ ] the maintenance reserve reached — R-A8P stopped production 7 minutes inside
      the reserve, which is what the time gate now refuses.

Procedures: `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` §5 for the graph,
image rollback for the application (35 s each way, measured in R-A8M), and
roll-forward for migrations because there is no downgrade.

## 23. Observation

- [ ] ≥ 40 minutes, sampled every 5: container count, restart counts, edge 200,
      `/health/live`, `/health/ready`, `rs0` 3/3 one PRIMARY, Falkor `PONG`,
      Qdrant green.
- [ ] **0** backend exception signatures (`traceback`, `unhandled`, `CRITICAL`).
- [ ] **0** container `die` or `oom` events.
- [ ] Evidence frozen in a directory that is a **sibling** of any staging backup
      root, checksummed, and secret-scanned with a non-zero hunted count.
- [ ] The FalkorDB rollback artefacts retained until §5.4 of the cutover document
      is satisfied and the owner records acceptance.

---

## Standing owner decisions this checklist assumes

| Decision | State | Where |
|---|---|---|
| RPO 24 h / RTO 8 h, quarterly staging drill | **MADE 2026-09-08**, RECORDED not DEMONSTRATED | `docs/OPERATIONS.md` §5 |
| Shared production S3 bucket for documents and backups | **ACCEPTED as post-release debt** | `docs/S3_STORAGE_POSTURE_DEBT.md`, `docs/RPO_RTO_OWNER_DECISION.md` header |
| Gate 2 FalkorDB vector criterion withdrawn | **APPROVED for this release** | `docs/PRODUCTION_READINESS_RELEASE_GATE.md` |
| Gate 3 bullet 7 (arbitration) | **OPEN** | `docs/GATE_3_EXECUTION_PLAN.md` |
| Gate 3 bullet 8 (empty/loading/error states) | **OPEN** | `docs/GATE_3_EXECUTION_PLAN.md` |
| Falkor rollback variant (A out-of-band vs B image-based) | **OPEN** | `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` §3 |
| Staging SMTP sink for the Gate 3 share leg | **OPEN** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 3 |
