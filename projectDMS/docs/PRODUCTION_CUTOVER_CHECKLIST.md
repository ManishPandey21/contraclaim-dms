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
| P1 | Owner authorisation for a production cutover | **GATE AUTHORISATION GIVEN 2026-09-20; WINDOW GRANT NOT GIVEN.** The release owner signed Gate 9 bullet 6 and disposed every open gate item (`docs/R_A9G_OWNER_DECISION_RECORD.md`). What is still missing is P2: the maintenance window is a separate owner grant, and nothing may run without it |
| P2 | Maintenance window booked, with a reserve | **NOT BOOKED — this is the one outstanding owner act.** Book **8 h** (not the older 5–6 h figure, which was sized for a deploy plus migrations and predates the role alignment, the Falkor Variant-A move, the password rotation and the ClamAV retirement): minimum **120-minute** execution budget, minimum **90-minute** protected recovery reserve, `LATEST_SAFE_STOP = WINDOW_START + 270 min`. See §2 and `docs/R_A9G_CUTOVER_PLAN.md` |
| P3 | Release branch reaches a deployable branch | **CLOSED 2026-09-20 (R-A9F)** — `release/contraclaim-rc1` fast-forwarded to the certified candidate `fe728b2` and pushed to both trusted remotes; PR #20 open, draft, unmerged |
| P4 | Readiness ≥ 85 for Gate 9 | **CLOSED 2026-09-20 (R-A9G-0)** — Gate 9 is **6/6** and the scorer reads **91/100 (raw 90.67, verdict "Ready")**. The threshold was met **before** b5 was ticked (85 / raw 85.33), which is the non-circularity condition; the move to 91 is four checkboxes and **no new measurement**. Re-derive with `scripts/production_readiness_score.py` rather than trusting this figure |
| P5 | FalkorDB `/data` cutover sequenced with the deploy | **PLANNED, NOT EXECUTED** — `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` |
| P6 | S3 failure-domain disposition confirmed | **OWNER-ACCEPTED DEBT** — confirmed at §8; the standing decisions are listed at the end of this file |
| P7 | Every open launch-gate bullet formally disposed | **CLOSED 2026-09-20 (R-A9G-0)** — all eleven open bullets are disposed in `docs/R_A9F_OPEN_GATE_MATRIX.md`: Gate 9 b1, b2, b5 and b6 are checked, and Gate 3 b3–b9 are **dated owner-accepted debt with a 2026-10-15 follow-up**. Six of the seven owner acts are recorded (`docs/R_A9G_OWNER_DECISION_RECORD.md`); only the window grant (P2) remains |
| P8 | ClamAV temporary override retired | **PLANNED for §13a** — production depends on the untracked `docker-compose.clamav-r-a8z.yml` today; it is retired **after** the release stack is running and verified, never before |

P4 is a Gate 9 condition, not a cutover condition. A deploy can technically
proceed below 85; **Gate 9 cannot close**, and the release is then deployed
without a completed final review. That is an owner decision, and it must be made
in writing before §3. It was made on 2026-09-20 and P4 is closed.

**What the owner's 2026-09-20 decisions did and did not authorise.** They closed
every *gate* precondition: the high-risk list is disposed, the blocker register
has no row parsing as Open, Gate 9 is signed, and the production role impact is
discharged with **no re-grant**. They did **not** authorise execution. P2 is the
binding constraint now, and it is a grant only the owner can make. Read
`docs/R_A9G_OWNER_DECISION_RECORD.md` before §1, because several steps below
exist only because of a decision recorded there.

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
- [ ] `LATEST_SAFE_STOP = WINDOW_END − 210 min` (120 execution + 90 recovery reserve),
      so a 4-hour window permits a stop no later than `WINDOW_START + 30 min` and leaves
      nothing for pre-outage revalidation.
- [ ] **R-A9F recommendation to the owner for the R-A9G window: 8 hours**, with a
      **minimum 120-minute execution budget** and a **minimum 90-minute recovery reserve**.
      An 8-hour window puts `LATEST_SAFE_STOP` at `WINDOW_START + 270 min`, which is the
      first budget that leaves real slack for pre-outage revalidation. The earlier "book
      5–6 hours" was sized for a deploy plus migrations; R-A9G additionally carries the
      explicit role alignment, the FalkorDB `/FalkorDB` → `/data` Variant-A cutover with an
      out-of-band engine, a `FALKORDB_PASSWORD` rotation, the ClamAV override retirement,
      backup verification, restore verification and observation.
- [ ] The time gate is **mechanical**, not a judgement:
      `scripts/check_maintenance_time_budget.py --recovery-reserve-minutes 90
      --execution-budget-minutes 120`. R-A8P stopped production 7 minutes inside the
      reserve; the gate exists so that cannot happen again. Run it **before the stop** and
      **again immediately before the stop command**.
- [ ] Clients informed; hard recovery start agreed and recorded.
- [ ] **Do not schedule this automatically.** The window is granted by the owner, in
      writing, together with the Gate 9 approvals in `docs/R_A9F_OPEN_GATE_MATRIX.md`.

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
- [ ] `docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml -f docker-compose.clamav-r-a8z.yml config`
      **The ClamAV override belongs in every production compose command** until §13a retires
      it. The running `clamav` container's own
      `com.docker.compose.project.config_files` label carries three files; a two-file render
      is a different service. `config` and `ps` succeed either way, so the error only
      appears at `up`.
      renders exit 0 with no unresolved variable. **Do not add the base
      `docker-compose.yml`** — `config` and `ps` still succeed with the wrong file
      set, and only `up` fails, on a missing `client/.env.development`.
- [ ] Verify the server checkout is byte-identical to the running containers
      (tree-wide `sha256sum` compare) before trusting any code read of production.

## 5. Fresh backups

**The pre-deploy backup contract.** The deploy does not start until *all* of these hold,
and each is a measured artefact rather than a green log line:

| Artefact | Required state |
|---|---|
| Mongo logical backup | written, `sha256sum -c` verified against its own manifest |
| FalkorDB rescue archive | **fresh in this window**, semantically `VALID` under the `redis-persistence` profile, entries at the archive **root** |
| Qdrant volume archive | `VALID` under its declared contract |
| Redis volume archive | `VALID` under its declared contract |
| Uploads / `application-volume` | `VALID` — an **empty** uploads volume must not abort the run (F-A8W-B1, fixed in R-A8X) |
| Checksums and manifests | every archive's checksum recorded and verified; the manifest matches the archive it names |
| Off-site leg | exit 0 |

**"Valid" has exactly one definition here.** Freshness, a glob that matches, and a
plausible archive size have each certified an unrestorable archive in this programme.
`scripts/validate_backup_archive.py` — which reads the archive's structure against a named
profile — is the only definition. Do not substitute a file listing for it.

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

## 13a. ClamAV temporary-override retirement

Production depends on the **untracked** `docker-compose.clamav-r-a8z.yml`, applied in R-A8Z
because the deployed production source predates the ClamAV fix. The release source already
carries that configuration in the tracked compose: **ClamAV 1.4.6, `egress-net` so FreshClam
can reach the update path, the persistent `clamav_db` volume, 3 GiB, and the signature
freshness healthcheck**. Retiring the override is therefore a *deletion of a duplicate*, not
a configuration change — but only once that has been proved.

**Static equivalence is already proven, offline, in R-A9F (2026-09-20).** The override's
`clamav` service block and the release `docker-compose.prod.yml` `clamav` service block were
compared line by line. They differ in **exactly one line**: the release block ends with
`logging: *default-logging` and the override does not. That is the difference the override's
own header declares and explains - a YAML anchor cannot cross files, and the base compose
already sets logging - so the two **render** the same service. Image digest, `FRESHCLAM_CHECKS`,
the 3 GiB memory limit, the full freshness healthcheck, `service-net` + `egress-net`, and the
`clamav_db` volume are byte-identical, and the release compose declares `clamav_db` at the top
level. Override sha256 `8ab39508677f956d386c06d051247631554ee21f634ac97f51be8c4708b91e42`.
Evidence: `15-clamav-override-equivalence.txt` in the R-A9F evidence set.

That is a *source* comparison. The render comparison below still runs in the window, because
a render also resolves environment and the base file, and only the render is what `up` uses.

- [ ] **Before the deploy, prove equivalence by rendering, not by reading.** With the release
      checkout in place, compare the two renders of the `clamav` service:
      `docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml -f docker-compose.clamav-r-a8z.yml config`
      against
      `docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml config`.
      The `clamav` service must be **identical** in image digest, networks, volumes, memory
      limit, healthcheck and environment. Any difference is a STOP: it means the release
      compose is not in fact equivalent and the override is still load-bearing.
- [ ] **Redact before the render reaches evidence.** `docker compose config` resolves
      environment values, so the output carries live secrets. Redact by **value**, never by
      key name, before the file is written into the evidence directory.
- [ ] **Keep the override in every compose command through the deploy.** Do not remove the
      file, and do not drop it from the command, until this section's final step.
- [ ] **Retire only after the release stack is running and verified** — that is, after §16
      startup, §19 gate-style health and §21 `post_deploy_verify.sh` are green, including
      `CLAMAV_READINESS=OK` and the live antivirus check (clean accepted, EICAR rejected,
      loaded signature age within the maximum).
- [ ] **Retire in one step, then re-verify.** Drop `-f docker-compose.clamav-r-a8z.yml` from
      the command and run `up -d --no-deps clamav`. Then confirm: the container's
      `com.docker.compose.project.config_files` label now names **two** files; the image is
      still `clamav/clamav:1.4.6@sha256:71fbb76b…`; the **`clamav_db` volume is the same
      volume**, not a new one (a fresh volume means the signature database was discarded and
      clamd will serve nothing until FreshClam completes); `clamd` reports loaded signatures
      and their age; and `post_deploy_verify.sh` passes the antivirus check again.
- [ ] **Do not delete the override file before that verification.** Move it aside only after
      the two-file render is running and verified; if anything fails, re-adding it is the
      rollback.
- [ ] Tags get rebuilt — the ClamAV pin is a **digest**, and the freshness rule in
      `docs/CLAMAV_SIGNATURE_FRESHNESS.md` still applies after retirement.

## 14. Production migrations

- [ ] The exact expected ledger state, catalogue, orphan rows and pending set are frozen in
      `docs/R_A9F_PRODUCTION_MANIFEST.md`, "Production migration manifest". Compare against
      it; do not discover it during the window.
- [ ] `migrate_database --list` inside the backend container.
- [ ] `--apply --fail-on-warning`. **Exit 2 is expected on the first apply** and
      is computed after the migrations ran: read the JSON, confirm the only
      warnings are `20260721_0001`'s two known informational lines, and proceed.
      **Any other warning stops the deploy.**
- [ ] `20260906_0001 permission_name_unique` applied. It is applied to **staging
      only** today. It makes `permissions.name` unique, and the seeder races
      itself without it.
- [ ] Role-contract alignment (R-A9D, owner decision 2) - an explicit operation,
      **not** in the migration catalogue, so `migrate_database` never runs it. After
      the migrations, inside the backend container, run
      `python -m rbac_backend.scripts.align_role_contract` and compare its
      `proposed_additions` for `orgadmin` and `projectadmin` with the owner-reviewed
      diff frozen in `docs/R_A9F_ROLE_ALIGNMENT_CUTOVER_DIFF.md`; `unapproved_additions_total` must be **0** and
      `warnings` empty (a deactivated or organisation-bound `orgadmin`/`projectadmin`
      document is a STOP). After `--apply`, `align_role_contract --apply` must report
      `second_apply_is_noop: true`.
- [ ] **F-A9D-1 (owner decision, R-A9E): the operation also REMOVES exactly
      `billing.plan.manage` from `orgadmin` and `projectadmin`**, because it gates the
      platform-wide plan catalogue with no tenant scope and is outside the release role
      contract. Check the report's three classes: `proposed_additions` (canonical),
      `proposed_removals` (must be exactly `["billing.plan.manage"]` for each of the two
      roles, and `unapproved_removals_total` must be **0**), and
      `retained_outside_contract` — every other production-only permission
      (`billing.plan.view`, `roles:assign`, `drafting.*`) is **preserved and reported**.
      The decision is deliberately NOT generalised to other production-only permissions
      or to other roles: `contractmgr_org`, `settings_manager`, `limited_user`,
      `projectuser` and `orguser` are never touched.
- [ ] The alignment writes role documents directly. An ADDITION can only make a cached
      decision briefly more restrictive, but the F-A9D-1 REMOVAL is a revocation, so the
      operation carries the D4-B contract for it: it reads the role's holders (by id and
      by legacy spelling), announces the authority change before the write and
      invalidates after it, and **refuses to change the role at all** if the holders
      cannot be read or the announcement cannot be made (reported as a warning, exit 2).
      Run it while the application tier is still stopped, as this checklist orders it,
      and there is nothing cached to revoke in the first place.
- [ ] System-role audit (`EXPECTED_SUPERADMIN_HOLDERS=2`) passes again after the
      migrations and the startup seeder.
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

**The post-deploy backup contract.** A deployment is **not complete** because health
endpoints answer 200. It is complete when the deployed release can produce a backup that a
restore can consume. All four must hold:

1. the canonical production backup runs **from the release's own script, with no manual
   step** — an operator replaying a failed script by hand is diagnostic evidence, not
   certification (the R-A8X rule, from F-A8W-B1);
2. **every** artefact is produced — nothing skipped, nothing empty-and-counted;
3. **semantic validation passes** for each artefact under its declared profile;
4. `backup_status.py` reports healthy — `falkordb-data` **`VALID`**, not `UNVERIFIED`,
   not `UNEVALUATED`.

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
- [ ] Note the historical trap: production has no `document-worker` **today**, so the
      release copy of this script may need the server's own copy. Resolve which
      copy is authoritative **before** the window, not during it.
- [ ] **This cutover changes that fact.** The release production compose declares
      `document-worker` and `document-worker-canary` with **no profile**, so both start as
      new production services (`docs/R_A9F_PRODUCTION_MANIFEST.md` section 4). After
      startup they must exist, and the section 16 assertion — exactly **one** scheduler
      owner across every running service, and the web tier not acting as an extraction
      worker — must be re-checked with them running, not with the pre-cutover service set.
      `graphiti` stays behind the `graph-experimental` profile and does **not** start.

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

**All seven R-A9G owner acts except the window grant were recorded on 2026-09-20. The
verbatim record is `docs/R_A9G_OWNER_DECISION_RECORD.md`; the rows below are renderings of
it.**

| Decision | State | Where |
|---|---|---|
| RPO 24 h / RTO 8 h, quarterly staging drill | **MADE 2026-09-08**, RECORDED not DEMONSTRATED | `docs/OPERATIONS.md` §5 |
| Shared production S3 bucket for documents and backups | **ACCEPTED as post-release debt** | `docs/S3_STORAGE_POSTURE_DEBT.md`, `docs/RPO_RTO_OWNER_DECISION.md` header |
| Gate 2 FalkorDB vector criterion withdrawn | **APPROVED for this release** | `docs/PRODUCTION_READINESS_RELEASE_GATE.md` |
| Gate 3 bullet 7 (arbitration) | **ACCEPTED DEBT (owner, 2026-09-20), follow-up 2026-10-15, and prioritised for first post-release closure; explicitly NOT superseded** | `docs/R_A9F_OPEN_GATE_MATRIX.md`; `docs/GATE_3_EXECUTION_PLAN.md` |
| Gate 3 bullet 8 (empty/loading/error states) | **error-state half SUPERSEDED and guarded (decision 8-C); remainder ACCEPTED DEBT (owner, 2026-09-20), follow-up 2026-10-15, with the structural guards and the generated route denominator still mandatory** | `docs/R_A9F_OPEN_GATE_MATRIX.md`; `docs/GATE_3_EXECUTION_PLAN.md` |
| Falkor rollback variant (A out-of-band vs B image-based) | **DECIDED 2026-09-20 (R-A9F): VARIANT A**, **re-confirmed by the owner 2026-09-20 (R-A9G-0)**. Variant B withdrawn for this cutover and not to be substituted for convenience during the window | `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` §3, "OWNER DECISION, 2026-09-20" |
| Staging SMTP sink for the Gate 3 share leg | **DISPOSED (R-A9F): NOT REQUIRED for the initial cutover.** It is a staging configuration and a sub-dependency of Gate 3 b3, not an independent gate item; it changes nothing in production. Carried with b3 | `docs/R_A9F_OPEN_GATE_MATRIX.md`; `docs/GATE_3_EVIDENCE_MATRIX.md` row 3 |
| Legacy `organization-admin`/`project-admin` references resolve one hop to `orgadmin`/`projectadmin` | **APPROVED (R-A9D)** | `docs/AUTHZ.md` "Role references" |
| No re-grant of alias-fan-out permissions to `projectuser`; `orgadmin`/`projectadmin` aligned to the release contract by the explicit `align_role_contract` operation (§14) | **APPROVED (R-A9D)** | `docs/AUTHZ.md` "Role documents are aligned" |
| F-A9B-2 legal-words admin requires system authority | **CLOSED IN CODE (R-A9D)** | `docs/AUTHZ.md` "System administration is nobody's alias" |
| F-A9D-1 production `orgadmin`/`projectadmin` documents store `billing.plan.manage` (platform plan catalogue) outside the release contract | **DECIDED (R-A9E): REMOVE it from those two roles only; every other production-only permission is preserved and reported.** Re-confirmed by the owner 2026-09-20 | §14; `docs/AUTHZ.md` "Role documents are aligned" |
| The 3 production roles / 4 users whose fan-out-only authority R-A9B removed (`custom#2`, `custom#3`, `custom#11`) | **REVIEWED AND DISCHARGED (owner, 2026-09-20): NO RE-GRANT.** That authority came only from the permission-alias fan-out defect, is not in the canonical release role contract, and its removal is **intended**. **No manual pre-cutover re-grant is required.** If an operational need appears after cutover, grant the specific permission explicitly through normal role administration — never by restoring fan-out | `docs/R_A9G_OWNER_DECISION_RECORD.md` §7; R-A9B receipt §16 |
| High-risk acceptance list A1–A9 | **A1–A8 ACCEPTED as bounded residual risk for this initial release only, each with a 2026-10-15 follow-up (A8 has none: it is bounded by the engine staying non-primary). A9 DISCHARGED. Not permanent waivers** | `docs/R_A9G_OWNER_DECISION_RECORD.md` §2; `docs/R_A9F_OPEN_GATE_MATRIX.md` |
| P0-002 (combined live AI/vector/graph integration proof) | **FORMALLY DISPOSED (owner, 2026-09-20) as `Accepted debt - Owner approved for initial cutover`, follow-up 2026-10-15. NOT technically closed** — the proof has still never been captured | `docs/PRODUCTION_READINESS_RELEASE_GATE.md` register; `docs/R_A9G_OWNER_DECISION_RECORD.md` §4 |
| Gate 9 closure and release sign-off | **SIGNED 2026-09-20 by Release Owner.** Bullets 1, 2, 5 and 6 ticked; Gate 9 6/6; scorer 91/100 raw 90.67. The +5.33 is four checkboxes, not a new measurement — the sealed R-A9E staging evidence still measures 85 / raw 85.33 | `docs/R_A9G_OWNER_DECISION_RECORD.md` §3–§6 |
| G32 production state migration | **OUT OF SCOPE for this cutover, re-confirmed by the owner 2026-09-20.** NOT RUN, NOT CERTIFIED; separate owner authorisation and its own four approvals | §15; `docs/R_A9G_CUTOVER_PLAN.md` |
| Third-party image fixable CRITICAL/HIGH (MongoDB, FalkorDB, Qdrant, httpd) | **EXCEPTED (R-A9E) until 2026-10-15 and ACCEPTED by the owner on 2026-09-20**; remediation window 2026-10-06 → 2026-10-15, httpd first; mandatory 2026-10-06 review trigger; post-cutover owner **Release Owner**. Not permanent acceptance and not renewed by silence | `docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md` "R-A9E cutover exception" |
