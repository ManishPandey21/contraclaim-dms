# Post-cutover operational assessment — 2026-11-15

**This document is the current source of truth for open operational items.** It
replaces the blocker list in pull request #20 (written 2026-09-09, closed
2026-11-15), which describes a state that no longer exists and must not be read
as current.

**Status of this assessment: repository review, plus one production reading.**
The one reading (2026-11-15, section 6) is the deployed checkout's branch and
commit. Every other statement about production below is either quoted from a
dated repository receipt or marked NOT VERIFIED. Section 6 is where fresh
production readings are recorded.

## 1. Two kinds of statement, kept apart

| Kind | What it is | How it is marked here |
|---|---|---|
| **Historical receipt** | What a dated document in this repository says was measured at the time, for example `docs/R_A9H_POST_PRODUCTION_CLOSURE.md` (2026-09-21) | "Receipt, <date>" |
| **Current production verification** | What the running system says when read today | Recorded only in section 6, with date, operator and output |

A receipt is evidence of the past. It is not evidence that the same thing is
true now: eight weeks have passed since the last production reading in this
repository, and the repository contains no record of any production change in
that period. Absence of a record is not a reading either. Nothing in sections 2
to 5 may be used to authorise a maintenance window or a deployment without the
matching reading in section 6.

## 2. Disposition of the September blockers (pull request #20)

Disposition vocabulary: **RESOLVED (historical)** — closed by a dated receipt;
**SUPERSEDED** — the statement described a state that later events replaced;
**ACCEPTED DEBT** — the owner accepted it in writing; **STILL OPEN** — no receipt
closes it.

| # | Item as written in #20 | Disposition | Basis |
|---|---|---|---|
| 1 | FalkorDB `/FalkorDB` → `/data` persistence move, sequenced with the deploy | **RESOLVED (historical)**, with a successor item still open | Receipt, 2026-09-21: live engine `contraclaim-falkordb-cutover` runs with `dir=/data` (R-A9H §4). The move was done by an out-of-band container, which created workstream W1 |
| 2 | Fresh validated rescue archive; previous Falkor container preserved for rollback | **RESOLVED (historical)**; retention **STILL OPEN** | Receipt, 2026-09-21: four rescue archives with checksums, two rollback images, original container preserved stopped (R-A9H §4, §6). They were to be retired after normalization; no receipt says they were |
| 3 | `FALKORDB_PASSWORD` rotation after parity | **RESOLVED (historical)**, then **STILL OPEN** again | Receipt, 2026-09-21: rotated; old credential answers `WRONGPASS` (R-A9H §7, §8). The same phase wrote the *current* credential into `/var/log/auth.log` and the journal (R-A9H §9), so a second rotation is owed. See W1 |
| 4 | Production migrations, including `20260906_0001` | **RESOLVED (historical)** for the 19-migration catalogue | The owner approved the production migration sequence on 2026-09-20 (`docs/R_A9G_OWNER_DECISION_RECORD.md`) and R-A9H records the deployment as successful. Three migrations added on `main` on 2026-11-14 are a new item: W3 |
| 5 | Production S3 shared bucket; window-only second copy recommended | **ACCEPTED DEBT** | `docs/S3_STORAGE_POSTURE_DEBT.md` §1; owner decision record item A6. Carried in the risk register, R3 |
| 6 | Post-deploy backup semantic validation | **RESOLVED (historical)** for FalkorDB and Redis; **STILL OPEN** for MongoDB and uploads | Receipt, 2026-09-21: canonical backups with a proven persistence flush and disposable restores (R-A9H §3, evidence 12 to 14). The same receipt records that `backup_status` checks MongoDB, uploads and the manifest for freshness only (R9H-7) and that nothing automatic reads the flush result (R9H-11) |
| 7 | "Production is not deployed from this branch and is unchanged" | **SUPERSEDED** | Receipt, 2026-09-21: production tracks `release/contraclaim-rc1` (R-A9H §2). **Read 2026-11-15:** the production checkout is on that branch at `34d664a` (2026-10-23), 67 commits behind the branch tip and 87 behind `main`. See R9 |
| 8 | Backend, contract-worker and document-worker images stale | **SUPERSEDED** | Receipt, 2026-09-21: certified images deployed (R-A9H §14). The release branch has moved since (last commit 2026-11-01). **Read 2026-11-15:** the checkout was moved to `34d664a` (2026-10-23), so production was updated at least once after the receipt; which commit the running images were built from is NOT VERIFIED |
| 9 | G32 production state: not certified | **STILL OPEN** | Receipt, 2026-09-21: not run, separate owner authorization required (R-A9H §17). Risk register, R2 |
| 10 | Readiness 74/100; Gate 9 final review 0/6 | **SUPERSEDED** | `docs/PRODUCTION_READINESS_RELEASE_GATE.md` records 91/100 and Gate 9 6/6 by owner decision on 2026-09-20 |
| 11 | Gate 3 browser E2E 1/9; 49 routes without error-state coverage | **ACCEPTED DEBT**, deadline passed | Gate 3 stands at 2/9. Six bullets were carried as owner-accepted debt with a follow-up dated 2026-10-15; no receipt shows that follow-up happened. Risk register, R1 |

Pull request #20 itself: **keep closed.** GitHub marked it merged on 2026-11-15
because its commits reached `main` through #109. Its description was never
updated after 2026-09-09 and is a historical record only.

## 3. Open workstreams

Each workstream lists what has to be read from production before anything is
authorised. `scripts/post_cutover_readonly_checks.sh` takes most of these
readings and changes nothing; the rest are named.

### W1 — FalkorDB normalization and credential rotation

**Last receipt (2026-09-21, R-A9H §4, §7 to §9, §18).** The live engine is an
out-of-band container with no healthcheck and unbounded logs. The compose
service `falkordb` is the preserved original: stopped, `restart: no`, stale data
path, dead credential, and still configured with the `falkordb` network alias.
The current credential is in two host log stores. Normalization (R-A9I) was
planned for 2026-09-22 at the earliest; `docs/HINDRANCE_STAGING_CERTIFICATION_PLAN.md`
records it as not started, and no later document records it as done.

**Read before authorising a window:**

1. Live topology: exactly one running container answers `falkordb`; which one;
   whether it is the compose service (normalized) or the out-of-band container
   (temporary). *Script section B.*
2. Persistence directory is `/data`; graph list and node and relationship
   counts. *Script section B.*
3. The `.env` credential is the one the live engine accepts. *Script section B.*
4. Whether the current credential is still in `/var/log/auth.log` and the
   journal. *Not in the script, deliberately.* Use the root-shell method in
   R-A9H §9. Never pass the value to `sudo` or `grep` on a command line: that is
   how it got into the log.
5. A canonical backup from the last 24 hours whose manifest records
   `persistence_flush.falkordb = ok`. *Script section E reads the manifest.*
6. **Restore evidence:** a disposable restore from that same archive reproducing
   the live counts read in step 2. A manifest is not restore evidence. R-A9H
   evidence 12 to 14 is the template.
7. Rollback artefacts still present: the original container, the two rollback
   images, `.env.bak-ra9g-a2`. *Script section B lists names.*

**Standing prohibition.** Do not run a blanket `docker compose up -d`, `start`
or `restart`. `backend`, `contract-worker`, `document-worker` and
`document-worker-canary` each depend on `falkordb`, so an `up` naming any of
them without `--no-deps` starts the stale original and two containers then
answer to `falkordb`. This holds until section 6 records a normalized topology.

**To authorise:** readings 1 to 7 recorded in section 6, then owner acceptance
and a short window, then the eleven-step sequence in R-A9H §4 with the
credential re-rotation at its step 6.

### W2 — Third-party container image exception

**Last receipt.** MongoDB (273 fixable CRITICAL/HIGH), FalkorDB (148), Qdrant
(84) and httpd (51) were carried under an exception that **expired on
2026-10-15** and is not automatically renewable
(`docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md`, "R-A9E cutover exception").
Those counts were measured on 2026-09-16. A re-scan was due on 2026-10-06; the
repository holds no record of it.

**Repository fact, checked 2026-11-15.** `docker-compose.prod.yml` and
`docker-compose.mongo-replicaset.yml` on `main` still pin all four excepted
digests:

| Image | Pinned digest |
|---|---|
| `mongo:8.0` | `sha256:ffa440e8…06d28e6` |
| `falkordb/falkordb:v4.0.8` | `sha256:af5f2aa0…8b4edb8` |
| `qdrant/qdrant:v1.12.5` | `sha256:05fecce7…5f08aae` |
| `httpd:2.4` | `sha256:393435ee…0d6fc742` |

So on the repository's evidence the exception has lapsed with nothing
remediated, and by that document's own terms the blocker is live again. What
production actually runs is NOT VERIFIED.

**Read before any decision:**

1. Running image digests against the four above. *Script section C.*
2. A fresh Trivy scan (CRITICAL and HIGH, without `--ignore-unfixed`) of the
   image ids the running containers use. *Script section C with `RUN_TRIVY=1`.*
   The September counts are not a substitute.

**To close:** either remediation in the order R-A9H §16 gives (httpd, then
MongoDB, then FalkorDB after W1, then Qdrant under its own plan), or a new
written, dated owner exception that cites the fresh scan. One of the two is
required; the lapsed exception cannot be relied on.

### W3 — Letter Bench production readiness

**State on `main`, 2026-11-15.** The Letter Bench and three migrations
(`20261114_0001 skill_registry`, `20261114_0002 seed_system_skills`,
`20261114_0003 letter_bench_indexes`) are merged. The feature is off by default
(`LETTER_BENCH_ROLLOUT_MODE=off`). Nothing has been deployed.

**What has been measured, and where.** All 22 migrations were applied to an
empty MongoDB in a cloud development container on 2026-11-14
(`docs/GATE_6_FRESH_INSTALL_EVIDENCE.md`). That is a fresh-install reading. It is
not an upgrade-path reading and it was not taken on staging or production.

**Runbook:** `docs/LETTER_BENCH_STAGING_CHECKS.md`, with the staging suite
`client/e2e/staging/letter-bench-api.spec.ts`. Neither has been run on staging.

**Required before deployment:**

1. **The three migrations are unapplied in production.** *Script section D:*
   each must read as absent or `pending`. If any reads otherwise, stop: something
   deployed them outside this record.
2. **Upgrade path on a production-derived staging copy.** Follow
   `docs/GATE_6_UPGRADE_PATH_PROCEDURE.md`: restore a current production archive
   into an emptied staging database, `--list`, dry run with `--fail-on-warning`,
   apply, re-apply as a no-op. Then read the Bench collections and indexes back.
   The unique indexes are created on collections that should be empty in
   production; confirm that on the copy rather than assume it.
3. **Real language model.** Every Bench test so far used a fake model. Run the
   analysis, evaluation and drafting steps against the configured production
   model on staging, in `canary` mode for one tenant, and review the output
   against the three skill packs. This spends model budget and needs approval.
4. **Real document authority.** The Bench has only run against test doubles for
   `DraftContextBuilder` and for role and allocation data. On staging, with real
   contract documents and real role assignments, confirm: clause citations
   resolve to real sources; a user without drafting allocation is refused; a
   user in another organisation cannot open a board.
5. **Role overlay wording** for the engineer and employer roles approved by
   someone with authority over contractual tone.
6. **Deployment mechanics.** The Bench's own rollback note says "restart the
   backend". On production that must be `up -d --no-deps backend` until W1 is
   closed.

Sequence: W1 before any backend recreate that is not `--no-deps`; W3 steps 1 to
5 before the migrations are applied to production; canary before primary.

### W4 — Keep this assessment current

When a reading is taken, add it to section 6 and, if it changes a disposition,
change the row in section 2 or the workstream in section 3 in the same commit.
Do not edit the September receipts: they record what was true when they were
written.

## 4. Operational risk register

Items that are known, not scheduled to close in a specific window, and must not
be lost.

| ID | Risk | Standing | Source | What would close it |
|---|---|---|---|---|
| R1 | **Gate 3 browser and error-state coverage.** Gate 3 is 2 of 9. Document, contract, timeline, chronology and arbitration flows, empty and loading states, and desktop and mobile smoke have no deployed-browser evidence. Error states are untested for the routes listed in `docs/GATE_3_ROUTE_INVENTORY.md`, including the three Letter Bench routes added on 2026-11-14 | Owner-accepted debt, follow-up dated 2026-10-15, **overdue** | Gate document, Gate 3; `docs/R_A9G_OWNER_DECISION_RECORD.md`; R-A9H §17 | Deployed-browser runs per `docs/GATE_3_EXECUTION_PLAN.md`, or a re-dated owner acceptance |
| R2 | **G32 certification gap.** The G32 production migration has not been run or certified | Open; separate owner authorization with four approvals required | R-A9H §17 | The G32 runbook, after W1 (both touch the graph; do not interleave) |
| R3 | **Shared S3 bucket.** Production documents and the off-site backup copy share the bucket `contraclaim`. A bucket-level loss, lifecycle rule or credential compromise reaches both | Owner-accepted debt | `docs/S3_STORAGE_POSTURE_DEBT.md` §1; decision record A6 | A separate backup bucket, as staging already has |
| R4 | **Backup content is not checked for MongoDB and uploads**, and a failed persistence flush is visible only in the cron log and the manifest | Open | R-A9H §18, R9H-7 and R9H-11 | Backend change wiring the flush result and content checks into `backup_status` |
| R5 | **Recovery time is recorded, not demonstrated.** RPO 24 h and RTO 8 h have not been proven on production-sized data | Owner-accepted debt | Decision record A7 | A timed restore of a production-sized archive |
| R6 | **Out-of-band containers on the production host**, two of them carrying the dead FalkorDB credential, plus rollback images and scratch files | Open; owner declined cleanup in R-A9H | R-A9H §14, §18 (R9H-4 to R9H-8) | Owner-authorised cleanup after W1 |
| R7 | **`main` and production have diverged.** `main` now carries the Letter Bench; production tracked `release/contraclaim-rc1` on 2026-09-21. Which branch production should track is undecided | Open | R-A9H §15 step 4 | An owner decision, recorded here |
| R9 | **Production is behind its own release branch.** Read 2026-11-15: checkout at `34d664a` (2026-10-23); `release/contraclaim-rc1` is at `614890f` (2026-11-01), 67 commits ahead (52 non-merge, 45 files). Not deployed: tenant-scope read hardening (#90), user-create organisation scope (#92), letter-tree reparent authorization (#93), arbitration exhibit ownership (#94), the zod, pymongo/dnspython, pyasn1 and follow-redirects advisories (#91, #105, #107), and an arbitration rejoinder fix (#104). No migration and no compose file differs across that range; `requirements.txt` and `package.json` do, so closing it is an image rebuild with no database step | Open; four of the eight are authorization fixes | `git rev-list 34d664a..origin/release/contraclaim-rc1`, section 6 | A certified deploy of the release tip, recreating services with `--no-deps` while W1 is open. Confirm first which commit the running images carry |
| R8 | **Live AI, vector and graph integration unproven on production** (P0-002), and document extraction has not run live since 2026-08-24 | Owner-accepted debt, follow-up dated 2026-10-15, **overdue** | R-A9H §17 backlog items 3 and 5 | Staging or production proof runs; model spend needs approval |

## 5. How to take the production readings

On the production host, from the deployed checkout:

```bash
cd /opt/contraclaim-dms/projectDMS && bash scripts/post_cutover_readonly_checks.sh
```

The script is on `main`, not on `release/contraclaim-rc1`. If the production
checkout does not have it, copy the one file to the host and run it with
`PROJECT_DIR=/opt/contraclaim-dms/projectDMS`. Do not switch the production
checkout to another branch to get it.

It only reads: `docker ps`, `inspect`, and `exec` of `redis-cli` read commands,
`getent` and `migrate_database --list`. It starts, stops and recreates nothing,
places no credential on a command line, and writes one report directory under
`/tmp`. Add `RUN_TRIVY=1` for the image scan.

It has been run against a local simulation of the production container layout.
It has not been run on production.

Not covered by the script, and still required: the credential-in-logs check
(W1 step 4), the disposable restore (W1 step 6), the staging upgrade-path run
(W3 step 2) and the real-model and real-document runs (W3 steps 3 and 4).

## 6. Current production verification log

Add one row per reading; attach or quote the report, scanned by value with
`scripts/evidence_secret_scan.py` first.

| Date (UTC) | Operator | What was read | Result | Report |
|---|---|---|---|---|
| 2026-11-15 | Release owner, over SSH from their own machine | Deployed checkout: host name, branch, last commit. Command: `ssh contraclaim "hostname; cd /opt/contraclaim-dms/projectDMS && git branch --show-current && git log -1 --format='%h %cs %s'"` | Host `vps-5dec80c1`; branch `release/contraclaim-rc1`; commit `34d664a`, 2026-10-23, "Merge pull request #88 from ManishPandey21/feat/grounded-summary-prompt-v4". Compared in the repository: an ancestor of the release tip `614890f`, 67 commits behind it, 87 behind `main` `48b1e4c`. **This is the checkout, not the running images**: the image revision label was not read | Output pasted into the working session; three lines, no secret |
| — | — | Everything else: container inventory, FalkorDB topology and credential, image digests, migration ledger, backups (`scripts/post_cutover_readonly_checks.sh`) | NOT YET RUN | — |
| — | — | Phase 4b readings P-01 to P-12: claims, arbitration drafts, reviewer roles, skills, perspective and usage (section F of the same script, reader `scripts/claim_arbitration_readonly_readings.py`; plan `docs/superpowers/specs/2026-11-16-claim-bench-phase-4b-hardening.md` §8.1) | **NOT YET RUN.** Blocker recorded 2026-11-16: the Phase 4b implementation session has no access to production or staging (AQ-11). Until a reading is taken it is "Not read", never zero, and no migration, backfill, seed or compatibility switch may rely on it | — |
