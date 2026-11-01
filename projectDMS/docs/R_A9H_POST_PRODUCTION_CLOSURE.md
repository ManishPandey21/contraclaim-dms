# R-A9H — post-production closure receipt

**Phase:** R-A9H, post-production closure and operational hardening. No new release, no
feature work, no G32, no third-party upgrade, no PR merge.
**Executed:** 2026-09-21, 00:23Z → 05:30Z, against production `contraclaim.com`.
**Evidence:** `/var/backups/contraclaim-stg-evidence/R-A9H-20260921T040058Z/` on the
production host, sealed by `SHA256SUMS` (§20).

| | |
|---|---|
| **DEPLOYMENT (R-A9G Attempt 2)** | **SUCCESS** — re-derived from the running system in this phase, not copied from the R-A9G record |
| **POST-PRODUCTION CLOSURE (R-A9H)** | **INCOMPLETE — one named item.** FalkorDB compose normalization (§4) is designed but **deferred by owner decision**: the frozen runbook's retention rule does not permit it before 2026-09-22. Everything else in scope is closed or scheduled below |
| **POST_PRODUCTION_OBSERVATION** | **CLEAN** — 30 m 29 s, 7 samples (§19) |

Source/runtime state and sealed evidence outrank this file. Every figure below was read from
production during R-A9H; where a figure is inherited from R-A9G it says so.

---

## 1. Starting production state (read-only, 2026-09-21T00:24Z)

| Item | Measured |
|---|---|
| Checkout | `/opt/contraclaim-dms/projectDMS`, branch `release/contraclaim-rc1`, HEAD `1f5b8690b196eb74a044f6a5843daa0d39c48e4b`, tree `922c6e5d9caede18725362a97256205a1fb3ee6f`, 0 tracked-dirty files (33 untracked operator artefacts, pre-existing) |
| Running images | backend, contract-worker, document-worker `sha256:52fd95afb62f36b77521b0926ec4cafd13235b029fcbc2351f06d942db9a23fb`; client `sha256:c7ccd59ec4d4f5a369d55d095773a90533a2079d786da0f1f2d0dff98d4e00f8` — the certified ids, unchanged at the end of R-A9H |
| Restarts / unhealthy | 0 restarts on every release container; 0 unhealthy |
| Health | `/health/live` 200, `/health/ready` 200, edge `https://contraclaim.com/health` 200, web root 200 |
| Mongo `rs0` | `mongo1` PRIMARY, `mongo2` SECONDARY, `mongo3` SECONDARY |
| Qdrant | healthy; one collection `contracts`, status green, **2,092 points** |
| Redis | authenticated `PONG`, `DBSIZE` 3 |
| ClamAV | 1.4.6, healthy, loaded daily 28129, persistent `clamav_db` |
| FalkorDB | live engine `contraclaim-falkordb-cutover` (`bd67a8ba439c`), authenticated `PONG`, `dir=/data`, `appendonly yes`, `GRAPH.LIST` = `contraclaim`, **156 nodes / 220 relationships** |
| Labels | exactly `Clause`, `Contract`, `ContractDocument`, `Letter` — `Contract` 1, `ContractDocument` 1, `Clause` 3, `Letter` 151 |
| Relationship types | `CITES` 215, `HAS_CLAUSE` 3, `HAS_DOCUMENT` 1, `HAS_SUBCLAUSE` 1 |
| nginx / TLS | active, `nginx -t` OK (as root); certificates valid to 2026-11-22 (web/app), 2026-11-24 (api), 2026-12-07 (apex) |
| Cron | `/etc/cron.d/contraclaim-backup`: 01:30 IST (20:00Z) `backup_offsite_s3.sh`, 01:45 IST `backup_status.py` |
| Backup status | `ok`; latest set `20260921-030258-RA9G-A2-POSTMOVE` |
| Disk | `/` 65 % (126 G of 193 G); backup root 11 G |

Two early probe misreads, recorded so nobody repeats them: `/api/health/live` is **404 by
design** (health is mounted at `/health/*`), and `nginx -t` as `ubuntu` fails on
certificate-read permission — as root it passes.

## 2. Release / HEAD verification

Every R-A9G expected property was re-derived and holds: HEAD/tree as above, certified image
ids running, Falkor on `/data`, parity 156 / 220 with the four canonical labels, migrations
**19 applied / 2 applied-not-in-catalogue / 0 pending** (R-A9G evidence `27-migrations.txt`),
`Organization-Admin` 135 and `Project-Admin` 118 permissions without `billing.plan.manage`
(`29-role-verification.txt`), ClamAV override retired (two-file label), G32 not run.

**HEAD moved during R-A9H** — by owner decision (§3), in three commits on
`release/contraclaim-rc1`, each fast-forwarded onto production:
`fffd7a0` (backup flush fix, tree `bf693997…`), `e6054a6` (review fixes, tree
`07bc001d751dea9c5bc8de2edca7e5c4de1e4838`), and the documentation commit that adds this
file. The final HEAD and tree are recorded in the evidence (`19-final-checkout.txt`). The two
code commits touch host-side scripts and tests only, the third touches `docs/` and `CLAUDE.md`
only; each deploy guard refused anything else. **No image was rebuilt or retagged; the running
images are still the certified ids and no container restarted.**

## 3. Canonical backup flush — defect D1, fixed and deployed

**Defect (confirmed, not inferred).** `scripts/production_backup.sh` flushed FalkorDB with
`docker compose … exec -T falkordb … BGSAVE || true`. With the compose `falkordb` service
stopped (Variant A), that printed `service "falkordb" is not running` and `|| true` swallowed
it. Reproduced read-only in R-A9H. Every backup since the cutover, including R-A9G's post-move
canonical backup, skipped the Falkor flush and exited 0.

**Fix (`fffd7a0`).** `scripts/lib/redis_flush.sh` resolves the engine by compose service, then
by network alias; counts a flush only when `LASTSAVE` advances; reads the password inside the
container, never from a host argv. `production_backup.sh` records `persistence_flush` in the
manifest and exits **3** when a flush is unproven (every artefact still written);
`backup_offsite_s3.sh` carries 3 through its S3 sync. `scripts/lib/retention.sh` forwards the
paths `find -delete` could not remove (D2). 11 behavioural tests; 8 fail on the old script.

**Result — the exact cron command, run once under `flock` at 2026-09-21T04:04:22Z:**

| Check | Result |
|---|---|
| Exit | **0** |
| `FLUSH falkordb` | **ok** — container `bd67a8ba439c` (the live engine), `LASTSAVE 1789940258 → 1789963468` |
| `FLUSH redis` | ok — `LASTSAVE 1789962340 → 1789963467` |
| Archives, stamp `20260921-093422` | `falkordb-data` **VALID** (dump.rdb + appendonlydir), `redis-data` **VALID**, `qdrant-data` **VALID** (436 entries), `qdrant-snapshots` **VALID** (empty is valid), `backend-uploads` **VALID** (486 files) |
| Mongo | `contraclaim-20260921-093422.archive.gz`, 19,522,545 B, `gzip -t` OK |
| Checksums | `checksums-20260921-093422.sha256` — 6/6 recompute OK |
| Manifest | `backup-20260921-093422.json`, parses, `persistence_flush` `{redis: ok, falkordb: ok}`, `latest.json` identical |
| Retention | no warning |
| Off-site | `aws s3 sync` complete; all 9 objects for the stamp present under `contraclaim/backups/vps-5dec80c1/` |
| `backup_status.py` | **ok**, exit 0 |

**Review fixes (`e6054a6`) and a second proof.** Code review of `fffd7a0` found that the
resolver took the first match — so in the exact double-`falkordb` hazard it could flush and
certify the stale original — that the alias scan was not scoped to this compose project, that
`redis-cli -a` still exposes the password in the container process's world-readable
`/proc/<pid>/cmdline`, and that the secret-leak test never planted the value it hunted. Now:
every holder is collected and exactly one is required (two → `FAILED … refusing to pick one`);
the scan is limited to `contraclaim_*` networks; `REDISCLI_AUTH` replaces `-a`; the test plants
sentinels and has a positive control. 13 tests in the new module, 42 across both backup
modules. Deployed, then the cron command run again at **2026-09-21T04:44:07Z**: exit **0**,
`FLUSH falkordb: ok (container bd67a8ba439c, LASTSAVE 1789963468 → 1789965855)`, redis ok,
stamp `20260921-101407`, all five archives VALID, 6/6 checksums, manifest
`persistence_flush {ok, ok}`, off-site sync complete, `backup_status` ok, and a second
disposable restore reproduced 156 / 220 with identical per-label and per-type counts
(evidence 10c, 12b, 13b, 14b).

## 4. FalkorDB topology — current state and the normalization plan

### Current topology (measured)

| Object | State |
|---|---|
| `contraclaim-falkordb-cutover` `bd67a8ba439c` | **live engine**, out of band (no compose labels), `unless-stopped`, `dir=/data`, sole holder of the `falkordb` alias on `contraclaim_data-net` (`172.18.0.5`), `REDIS_ARGS` carries the current password. **No healthcheck and no log rotation** (json-file, unbounded) — Variant-A options not re-supplied when R-A9G recreated it |
| `contraclaim-falkordb-1` `de249ee3df20` | **preserved original**, compose service `falkordb`, `Exited (0)`, `restart: no`, still configured with the `falkordb` alias, `dir=/FalkorDB`, the **pre-rotation** password |
| Volume `contraclaim_falkordb_data` | mounted by both at `/data`; holds the live persistence |
| Rollback images | `contraclaim/falkordb-rollback:20260920T211831Z-A2` (`18bc5548982d`), `…:20260920T200635Z` (`c92db008e1a1`) |
| Rescue archives | four in `/var/backups/contraclaim/` (`RA9G-live`, `RA9G-quiesced`, `RA9G-A2-live`, `RA9G-A2-quiesced`) with `.sha256` |

**Hazard, wider than R-A9G recorded.** Not only a blanket `docker compose up -d` is unsafe:
`backend`, `contract-worker`, `document-worker` and `document-worker-canary` each declare
`depends_on: falkordb: service_healthy`, so an `up` that names any of them **without
`--no-deps`** starts the original too — two containers then answer to `falkordb`, one with
stale data and a dead credential. A host reboot is safe (restart policies ignore
`depends_on`; the original is `restart: no`). The warning is now in the Falkor cutover doc's
status banner and the deployment guide.

### Why it was not executed in R-A9H

`PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` §5.4 bundles adoption into compose with retiring
the original, because `docker compose up -d --no-deps falkordb` recreates — removes —
`contraclaim-falkordb-1`. §5.4 permits that only after **all** of: parity held and
re-verified after rotation (met); **one full nightly** `production_backup.sh` exit 0 with
`falkordb-data` VALID (not yet — the R-A9H run was manual); a disposable restore from a
**nightly** archive (not yet); **24 h** clean observation (ends 2026-09-21T21:30Z); owner
acceptance (not given). Executing now would destroy the rollback object before its acceptance
point, which is an R-A9H stop condition. **Owner decision 2026-09-21: defer.**

### Normalization plan (to execute in the next phase)

Preconditions, each recorded in writing:

1. The **2026-09-21T20:00Z** nightly (stamp `20260922-013001`) exits 0 with `falkordb-data`
   VALID and `persistence_flush.falkordb = ok` in its manifest.
2. A disposable restore from **that nightly archive** reproduces the live counts at the time
   (156 / 220 / 4 labels unless the graph legitimately grew — compare to live, not to history).
3. Clean observation from 2026-09-20T21:29:50Z through at least 2026-09-21T21:30Z.
4. Owner acceptance recorded, and a **short maintenance window** granted: the graph is
   unavailable from `SHUTDOWN SAVE` until the compose engine is healthy (expected ≈ 1 min), and
   the three consumers are recreated if the credential is re-rotated (step 6).

Sequence (compose file set: the two tracked files only):

1. Time gate; fresh canonical backup through the cron command; `FLUSH falkordb: ok`; sha256;
   disposable restore PASS (R-A9H evidence 12–14 is the template).
2. Back up `.env` to `.env.bak-ra9i` (mode 600) — the rollback source for this step.
3. `docker update --restart=no contraclaim-falkordb-cutover`, then **authenticated
   `SHUTDOWN SAVE` with the password expanded inside the container**; require `Exited (0)`.
4. `docker rename contraclaim-falkordb-cutover contraclaim-falkordb-cutover-retired`. It stays
   stopped, `restart: no`, as the rollback for this step until acceptance.
5. `docker compose … up -d --no-deps falkordb`. This removes `contraclaim-falkordb-1`
   (`de249ee3df20`) — its writable layer survives in the rollback image and the validated,
   restore-proven quiesced rescue archive — and creates the compose engine on the same volume
   with `--dir /data`, the `.env` credential, the compose healthcheck and log rotation.
6. **Recommended, owner to decide: re-rotate `FALKORDB_PASSWORD` in the same window** (closes the §9 exposure at no extra outage,
   because step 5 already restarts the engine): write the new value into `.env` with a script
   that never places it on an argv, before step 5, then after step 5
   `up -d --no-deps backend contract-worker document-worker`.
7. Verify: container healthy; `CONFIG GET dir` = `/data`; `PONG` on the `.env` credential and
   `WRONGPASS` on both previous credentials; `GRAPH.LIST`; nodes, relationships, per-label and
   per-type counts equal to step 1; **exactly one** container answers `falkordb` on
   `data-net`, and `getent hosts falkordb` in the backend resolves to it; every consumer's
   `FALKORDB_URL` carries the new credential.
8. `/health/live`, `/health/ready`, edge 200; `post_deploy_verify.sh` 0 failures.
9. Cron-command backup: `FLUSH falkordb: ok` **resolved through compose** (not the alias
   fallback); disposable restore from that archive PASS.
10. Observation ≥ 30 min.
11. Remove the "temporary topology" banners from the Falkor cutover doc and the deployment guide.

Rollback before acceptance: `SHUTDOWN SAVE` the compose engine (restart policy `no` first),
`docker rename` the retired cutover back and `docker start` it, restore `.env` from
`.env.bak-ra9i`, recreate the three consumers with `--no-deps`. Both engines use the same
volume, so no data moves in either direction.

Retirement after acceptance: `docker rm contraclaim-falkordb-cutover-retired`;
`docker image rm contraclaim/falkordb-rollback:20260920T211831Z-A2 contraclaim/falkordb-rollback:20260920T200635Z`;
shred `.env.bak-ra9g-a2` and `.env.bak-ra9i`. The four rescue archives are not under the
backup retention root; keep or delete them as an explicit owner act.

## 5. Final graph parity (R-A9H)

Live, and reproduced exactly by the disposable restore of the new canonical archive
(`falkordb-data-20260921-093422.tar.gz`, sha256 `2b65e23e527d02b41540b24e92f6dc14f5f27f6e16825a655dca7c4230569c92`,
restored into a run-owned volume on `--network none` with a throwaway credential, torn down, 0
residue): `contraclaim`, **156 nodes, 220 relationships**, labels `Clause 3, Contract 1,
ContractDocument 1, Letter 151`, types `CITES 215, HAS_CLAUSE 3, HAS_DOCUMENT 1, HAS_SUBCLAUSE 1`.
(`alpine:3` was pulled once from Docker Hub for the extraction helper.)

## 6. Original FalkorDB rollback container — disposition

**Preserved.** `de249ee3df20…`, `Exited (0)`, `restart: no`. Retirement trigger: the §4
preconditions 1–4, earliest **2026-09-22** (after the 2026-09-21T20:00Z nightly and its
restore proof, and the 24 h observation), executed as §4 step 5. The rollback images and the
rescue archives are also retained; the retention policy does not permit removing them yet.

## 7. Secret cleanup and the old credential

| Item | Result |
|---|---|
| `.env.bak-ra9g-a2` | **Retained, deliberately.** It is the documented rollback source for the pre-rotation credential (Falkor doc §5.2), and the preserved original still holds that credential in its own `REDIS_ARGS`. Removing it before the original is retired would break a stop condition. Retire it with the original (§4). Mode 600, `ubuntu`, not in any evidence tree |
| Old credential ≠ new | yes |
| Old credential on the live engine | `WRONGPASS` |
| New credential on the live engine | `PONG` |
| Old credential in active config (`.env`, both compose files) | 0 |
| Old credential in release evidence / backup manifests | 0 / 0 |
| Old credential elsewhere on the host | `.env.bak-ra9g-a2`, `.env.pre-internal-credential-rotation-20260726T114259Z` (the 2026-07-26 rotation's own backup), `/var/log/auth.log` and two uid-1000 journal files (written by R-A9H's own probes, plus 2026-09-06 lines — see §9); env of the out-of-band `contraclaim-arbitration-audit-new` and `-test` containers (cannot reach the graph) |
| New credential in evidence / diagnostic scripts | 0 / 0 |
| Temporary render and scratch files | 67 cutover scratch items under `/tmp` (`ra9g-*`, `ra9e/`, `*_r9e.sh`) scanned by value — **CLEAN** — then removed under owner authorization. Two R-A9E scripts outside those patterns remain (`/tmp/s20_segmentA_restore.sh`, `/tmp/s32_tls_and_gate3.sh`) |

## 8. Password-rotation durability

The live engine's running `requirepass` equals the `.env` value; its startup `REDIS_ARGS`
carry the `.env` value and not the old one; restart policy `unless-stopped`; the compose
render resolves `requirepass` to the `.env` value; `backend`, `contract-worker` and
`document-worker` each carry the new credential and not the old. A restart therefore
re-applies the current credential. **No restart was performed** to demonstrate it — that is a
graph interruption and was not authorized; durability is proven by configuration. §4 step 7
repeats the proof on the compose engine.

## 9. Old-credential exposure (§13) — record and severity

**What happened.** During R-A9G the pre-rotation FalkorDB password was printed into the
Claude session transcript, because output was redacted by key name rather than by value.

**What R-A9H found — and caused.** Both the old **and the current** FalkorDB credentials are
in `/var/log/auth.log` (`syslog:adm 0640`) and in uid-1000 journal files. Attributed line by
line (secret redacted, `22-credential-log-attribution.txt`): **every** auth.log line holding
either credential, and every 2026-09-21 journal line, was written by **R-A9H's own
verification probes**, which ran `sudo grep -F -- "<credential>" /var/log` to check exposure —
and `sudo` records its argv. The first such line (00:29Z) is the probe that "found" it. The
only older hits are six journal lines from **2026-09-06** (before R-A9G) carrying the **old**
credential on `sudo docker compose …` commands. R-A9G itself left no credential in the host
logs. Neither credential is in any release evidence file, backup manifest, tracked file or
diagnostic script. The attribution itself was done inside a root shell reading the values
from the env files (`sudo bash -s` with a heredoc), so it added no argv line.

**Severity: LOW.** Exposure is host-local, to root, the `adm` group and uid 1000 — and uid 1000
is in the `docker` group, which is root-equivalent, so the logs grant nobody a capability they
lack. The old credential is dead (`WRONGPASS`). The transcript exposure is outside the host and
is limited to the old credential, which is dead. It is not zero: the current credential is on
disk in plaintext in two log stores, put there by this phase, until they rotate. The log lines
were **not** edited: altering the system audit log is not an authorized R-A9H act.

**Remediation.** Re-rotate `FALKORDB_PASSWORD` during the normalization window (§4 step 6), at
no extra outage — this phase's own leak makes that recommendation stronger. Guidance changed:
the backup flush reads credentials inside the container through `REDISCLI_AUTH`; the Falkor doc
and CLAUDE.md now say never to place a credential on a host argv — **including when searching
for it** — and to redact evidence **by value** (`scripts/evidence_secret_scan.py`).

## 10. document-worker

| Question | Answer |
|---|---|
| Required? | **Yes.** It is the only extraction owner: `backend` runs `START_DOCUMENT_EXTRACTION_WORKERS=false`, `contract-worker` likewise; `document-worker` runs `true`, pipeline `legacy_v0`. `post_deploy_verify.sh` fails without it |
| Running? | Yes — `8d36d54c0837`, certified image `52fd95af…`, `unless-stopped`, 0 restarts, logs clean ("Document extraction worker restricted to pipeline versions: ['legacy_v0']") |
| Health | no container healthcheck (none defined in compose); liveness is its process and the `post_deploy_verify.sh` ownership check |
| Queue | `document_processing_jobs`: 148 completed, 4 dead-lettered — all four from 2026-07-09, unrelated. Last completed job 2026-08-24: the extraction path has **not** been exercised live since the cutover (that proof belongs to the Gate-3 / P0-002 backlog, §17) |
| Why absent before the cutover | The previously deployed source (`main` @ `b2d5025`) had **no** `document-worker` service; it arrived on the release branch in `2b1eef7`. The deployment guide's every build/up command lists `backend contract-worker client` and never `document-worker`, and the cutover's explicit start list followed it. Corrected in the guide and the checklist |
| `document-worker-canary` | **Not expected in production.** `replicas: ${DOCUMENT_WORKER_CANARY_REPLICAS:-0}`; runs only inside an authorised unified-extraction canary |

**Expected production worker inventory:** `contract-worker` ×1 (contract queue + the single
scheduler), `document-worker` ×1 (extraction, `legacy_v0`), `document-worker-canary` ×0.

**`ALLOWED_DOCUMENT_MIMES` — no risk; no change.** The variable is set in no container's
environment and not in `.env`. All three containers resolve the same code default from
`core/config.py`: `application/pdf, application/zip, image/jpeg, image/png, text/plain`. The
worker's effective policy is identical to the backend's, so the R-A9G `WARN` is config-check
visibility debt, not an extraction-path risk.

## 11. ClamAV final topology

`7f898d012f74`, `clamav/clamav:1.4.6@sha256:71fbb76b…` (image `6dc7ff3fabde…`, unchanged),
two-file `config_files` label, volume `contraclaim_clamav_db → /var/lib/clamav` (same volume),
`egress-net` + `service-net`, 3 GiB limit, healthy, 0 restarts. `clamd` + `freshclam
--checks=12` running; last FreshClam 2026-09-21 03:39: daily 28129 up to date (the loaded
build, 2026-09-20 06:26). EICAR: `Eicar-Test-Signature FOUND` via `clamdscan`, and
`(False, 'Eicar-Test-Signature')` through the backend's own `AntivirusService`
(`fail_open=False`); a clean file returns `(True, None)`. Nothing uploaded or persisted.

**Compose labels — metadata only (A).** `backend`, `client`, `gateway`, `contract-worker` and
`document-worker` still carry the three-file `config_files` label because they were created
before the retirement. Compose decides recreation by `config-hash`, and every one of them
matches the two-file render (the override defines only `clamav`), so a two-file `up` recreates
none of them. They heal on their next ordinary recreate; nothing was recreated for looks. The
override file itself is still on disk, untracked — harmless now that no command uses it.

## 12. R-A9G procedure deviation — ClamAV sequencing

Recorded as it happened: the render-equivalence check that `PRODUCTION_CUTOVER_CHECKLIST.md`
§13a requires **before the deploy** was performed **later** — immediately before the override
retirement, after the application was deployed. Result: **0 differing keys** (R-A9G evidence
`37-clamav-retirement.txt`). The order is not rewritten here.

**Disposition:** process-order deviation; no observed production impact (`clamav` was not
recreated during the deploy, and its config-hash never changed); the evidence remains valid
for the retirement, which is the only decision it gates. **Follow-up: none required** — the
override is gone, so the check can never be needed again; the checklist carries the note.

## 13. Backup retention — defect D2, fixed

`find "$BACKUP_ROOT" … -delete 2>/dev/null` failed on exactly two files in
`/var/backups/contractdms/tag-migration-20260710-005605/` (`root:root 0755`; the backup runs
as `ubuntu`, so unlink is denied in that directory). Local only — off-site retention is an S3
lifecycle rule. Growth was not driven by it (176 KB); the backup root is 11 G, dominated by
the 355 MB uploads archive per run.

Fix, nothing deleted: the directory was moved to `/var/backups/contraclaim-retained/` (root
0750, with a README); sha256 of both files identical before and after; expired files under the
retention root 2 → **0**; the next run printed no warning. The script now names the paths it
cannot remove. Latent: `restore-drills/` and `release-updates/` are also `root:root` but hold
no files. Owner decision outstanding: keep or delete the relocated tag-migration pre-image.

## 14. Canonical production inventory — the new baseline

Captured 2026-09-21T04:16Z (`17-production-inventory.txt`). Networks and volumes without the
`contraclaim_` prefix. "Local build" images have no registry digest; their image id is the
identity.

| Service | Container | Image (id / digest) | Restart | Networks | Volumes | Health | Owner / process | Backup coverage |
|---|---|---|---|---|---|---|---|---|
| backend | `contraclaim-backend-1` `5eec82158b52` | `52fd95af…a23fb` (certified, local) | unless-stopped | data, egress, service | backend_uploads, backend_logs | healthy | web/API only; no workers, no scheduler | uploads archive (VALID) |
| contract-worker | `contraclaim-contract-worker-1` `5660b1305fc5` | `52fd95af…` | unless-stopped | data, egress, service | backend_uploads, backend_logs | — | contract queue + **sole scheduler** | — (stateless) |
| document-worker | `contraclaim-document-worker-1` `8d36d54c0837` | `52fd95af…` | unless-stopped | data, egress, service | backend_uploads, backend_logs | — | **sole extraction owner**, `legacy_v0` | — (stateless) |
| document-worker-canary | none | — | — | — | — | — | 0 replicas by design | — |
| client | `contraclaim-client-1` `39aeb7c40ffb` | `c7ccd59e…4e00f8` (certified, local) | unless-stopped | edge, service | — | healthy | SPA | — |
| gateway | `contraclaim-gateway-1` `572347116e90` | `httpd@sha256:393435ee…` (`00fe3afe…`) | unless-stopped | edge, service | — | healthy | internal HTTP gateway behind host nginx | — |
| mongo1/2/3 | `contraclaim-mongo{1,2,3}-1` | `mongo@sha256:ffa440e8…` (`7281281f…`) | unless-stopped | data | mongo{1,2,3}_data + anon configdb | healthy | `rs0` PRIMARY + 2 SECONDARY | mongodump archive (freshness only — no content contract) |
| qdrant | `contraclaim-qdrant-1` `c9f9f13d102a` | `qdrant@sha256:05fecce7…` (`449e3214…`) | unless-stopped | data | qdrant_data, qdrant_snapshots | healthy | `contracts`, 2,092 points | qdrant-data + qdrant-snapshots (VALID) |
| redis | `contraclaim-redis-1` `e492fb4a88c7` | `redis@sha256:6ab0b6e7…` (`487efc06…`) | unless-stopped | service | redis_data | healthy | sessions / queue / rate limits | redis-data (VALID, flush proven) |
| falkordb (live) | `contraclaim-falkordb-cutover` `bd67a8ba439c` — **out of band** | `falkordb@sha256:af5f2aa0…` (`13ee9b3b…`) | unless-stopped | data (alias `falkordb`) | falkordb_data:/data | **no healthcheck** | graph `contraclaim` | falkordb-data (VALID, flush proven) |
| falkordb (compose) | `contraclaim-falkordb-1` `de249ee3df20` | same image | **no** | data | falkordb_data | exited (0) | **preserved rollback — must stay stopped** | rollback image + rescue archives |
| clamav | `contraclaim-clamav-1` `7f898d012f74` | `clamav@sha256:71fbb76b…` (`6dc7ff3f…`) | unless-stopped | egress, service | clamav_db | healthy | clamd + freshclam | — (signatures re-downloadable) |
| mongo-init | `contraclaim-mongo-init-1` | `mongo:8.0` | no | data | anon | exited (0) | one-shot `rs0` initiator | — |

Also on the host and **not** release services: `contraclaim-arbitration-audit-new` and
`-test` (running since 2026-07-24, out of band, on `data-net` + `service-net`, carrying the
dead Falkor credential), `ra9bs-e2e` (running since 2026-09-15), nine anonymous containers
`Exited (1)` since 2026-07-24, and legacy `redis` / `falkordb` / `qdrant` exited since 2025-12.
Owner declined their cleanup in R-A9H; scheduled in §18.

## 15. PR #20 — recommendation and merge plan

**Recommendation: B — leave PR #20 open** through the Falkor normalization and its acceptance
(owner decision 2026-09-21). `release/contraclaim-rc1` is what production tracks and is the
live rollback reference; nothing in the repository's policy requires `main` to mirror
production (CLAUDE.md: "Deploy = get the commit onto the branch the server tracks"), and the
PR title itself says "CI evidence only, DO NOT MERGE".

**Merge plan, for a later owner authorization:**

1. Preconditions: Falkor normalization accepted; the release branch's CI green at its tip;
   production re-read to confirm it still tracks `release/contraclaim-rc1` at that tip.
2. `contraclaim` remote (PR #20's repository): `contraclaim/main` is `b2d5025`, an **ancestor**
   of the release tip — a fast-forward. `git push contraclaim release/contraclaim-rc1:main`
   (no merge commit, no force). PR #20 then shows merged.
3. `origin` remote: `origin/main` (`50a0b47`) is **not** an ancestor — it carries 72 commits the
   release does not. This is the known diverged-mains condition. Do **not** force-push. Reconcile
   separately with a reviewed merge commit, or keep `origin/main` as its own line and record
   that decision.
4. After the merge, decide whether production should track `main`; if so, move the server's
   branch in its own step and update the deployment guide §5.

## 16. Third-party image exception

Recorded unchanged in `THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md` and the owner decision record
§8: MongoDB (273 fixable C/H), FalkorDB (148), Qdrant (84), httpd (51). **Expiry 2026-10-15.
Mandatory review trigger 2026-10-06. Remediation window 2026-10-06 → 2026-10-15, httpd first.**
Not automatically renewable. The digests running in production equal the excepted digests
(§14). No upgrade in R-A9H.

**Remediation ticket (post-release):** *Remediate the four excepted third-party images before
2026-10-15.* On 2026-10-06 re-scan the exact deployed digests (Trivy, C+H, without
`--ignore-unfixed`); any new exploitable CRITICAL or public-path risk accelerates the work. Order:
(1) httpd `2.4` → `2.4.68` (no migration expected) via a disposable drill, then gateway recreate;
(2) MongoDB `8.0.26` → `8.0.30` rolling across `rs0`, secondaries first; (3) FalkorDB
`v4.0.8` → `v4.20.4` — one-way persistence, so take and restore-prove a pre-upgrade archive,
do it **after** the Falkor normalization, and drill parity on a copy first; (4) Qdrant — no safe
patch; `1.12 → 1.19` is a one-minor-at-a-time storage chain plus a `qdrant-client` bump, which
needs its own plan or an explicit, re-dated owner exception. Each image: digest pin, drill,
backup, restore proof, `post_deploy_verify.sh`, observation.

## 17. G32 and the accepted Gate-3 debt

**G32: NOT RUN · NOT CERTIFIED · POST-RELEASE · SEPARATE OWNER AUTHORIZATION REQUIRED.** Not
started. Follow-up item: owner authorization for the G32 production migration under
`G32-PRODUCTION-MIGRATION-RUNBOOK.md` with its four approvals, scheduled after the Falkor
normalization (both touch the graph; do not interleave them).

**Post-release validation backlog** (owner decision record A1–A7, follow-up **2026-10-15**),
ordered. Nothing here runs live models without separate approval.

| # | Item | Why this position | Model spend |
|---|---|---|---|
| 1 | Gate 3 **b7** arbitration drafting, deployed-browser E2E | owner-prioritised; deterministic, zero model cost | none |
| 2 | Gate 3 **b3–b6, b9** deployed-browser E2E gaps | same harness as b7 | none expected |
| 3 | First live document-extraction proof after cutover (upload → `document-worker` → vectors → graph) | the path has not run live since 2026-08-24 | none (legacy_v0) |
| 4 | Gate 3 **b8** degraded / empty / loading states | UI debt; structural guards stay mandatory | none |
| 5 | **P0-002** combined live AI / vector / graph integration proof | needs live models | **requires approval** |
| 6 | A7 production-scale recovery timing (RPO 24 h / RTO 8 h) | needs a restore of production-sized data | none |
| 7 | A6 independent backup failure domain (backups share the `contraclaim` S3 bucket) and verification of the S3 lifecycle rule that off-site retention depends on | architecture; AWS access | none |
| 8 | A4 Python dependency advisories | coordinated framework upgrade; re-certification | none |

## 18. Remaining open operational debt

| ID | Debt | Next action |
|---|---|---|
| R9H-1 | FalkorDB compose normalization (§4); the live engine has no healthcheck and unbounded logs until then | next phase, from 2026-09-22 |
| R9H-2 | Original Falkor container, rollback images, `.env.bak-ra9g-a2` retained | retire with R9H-1 |
| R9H-3 | Current Falkor credential in `/var/log/auth.log` + journal, written by R-A9H's own verification probes (LOW) | re-rotate in the R9H-1 window |
| R9H-4 | Out-of-band containers: 2 arbitration-audit (dead credential), `ra9bs-e2e`, 9 anonymous exited, 3 legacy exited | owner-authorized cleanup |
| R9H-5 | Docker disk: 58.9 GB reclaimable images, 11.3 GB build cache; disk 65 % | prune build cache first; never the rollback or `pre-ra9g` tags |
| R9H-6 | Relocated tag-migration pre-image; empty root-owned `restore-drills/`, `release-updates/` | owner keep/delete |
| R9H-7 | `backup_status` still checks mongo, uploads and the manifest for freshness only | existing debt |
| R9H-8 | Two R-A9E scripts left in `/tmp` | remove with R9H-4 |
| R9H-9 | Stale three-file compose labels on five containers | none — heals on next recreate |
| R9H-10 | `document-worker` has no container healthcheck | consider with the next compose change |
| R9H-11 | The manifest's `persistence_flush` block and exit 3 are read by nothing automatic: `backup_status.py` / `GET /health/operations` (`services/operations_health.py`, in the image) do not consult them, so a failed flush shows only in the cron log and the manifest. Wiring it in is a backend-image change, out of scope here | next release |

## 19. Post-production observation

**`POST_PRODUCTION_OBSERVATION=CLEAN`.** 2026-09-21T04:08:29Z → 04:38:58Z (30 m 29 s), 7 samples
every 5 minutes, read-only, taken after the topology work of this phase (evidence
`20-post-production-observation.txt`). Every sample: live / ready / edge / web **200**,
unhealthy **0**, restart total **0** (12 containers incl. the Falkor engine), `rs0`
PRIMARY + 2 SECONDARY, Falkor `PONG` 156 / 220 with `falkordb` → `172.18.0.5` (the cutover
engine), Qdrant green / 2,092, Redis `PONG`, ClamAV healthy, `document-worker` running / 0
restarts, `backup_status` ok, 0 error-level lines in backend and both workers. Docker
`die` / `oom` / `restart` events during the window: **0**.

The second backup run and the `e6054a6` deploy happened after the window closed; neither
touched a container (image ids, restart counts and start times recorded unchanged in
`10c-deploy-review-fix.txt`).

## 20. Evidence

`/var/backups/contraclaim-stg-evidence/R-A9H-20260921T040058Z/` (outside every Docker volume),
sealed with `SHA256SUMS`; secret scan by value against `.env` and `.env.bak-ra9g-a2` — result
in `21-evidence-secret-scan.txt` (must read CLEAN; it is run over the finished directory
before sealing). Contents: initial capture and probes (00–03), deploy guard abort and the two
deploys (10a, 10b, 10c), retention relocation (11), canonical backups (12, 12b) and their
verification (13, 13b), disposable restores (14, 14b), credential state (15), ClamAV (16),
inventory (17), documentation diff (18), final checkout (19), observation (20), credential log attribution (22). The digest of
`SHA256SUMS` itself is recorded in the closing report and the handoff memory, not here — this
file is inside the sealed set.

## 21. Next operational milestone

**R-A9I — FalkorDB compose normalization**, earliest 2026-09-22: confirm the 2026-09-21T20:00Z
nightly (`persistence_flush.falkordb = ok`, `falkordb-data` VALID), prove a restore from it, take
owner acceptance and a short window, then execute §4 with the credential re-rotation. After it:
retire the rollback artefacts, then the out-of-band container cleanup, then the 2026-10-06
third-party re-scan.
