# Same-host staging rehearsal — maintenance window runbook

R-A8A's verdict is **B**: the production host has the hardware for the staging
stack, but not for both stacks at once. The rehearsal therefore runs inside a
maintenance window with the production application stack **stopped**, on the same
Docker daemon.

That decision buys a hardware fit and pays for it in blast radius. Everything in
this document exists because one daemon now holds both stacks, and Docker will do
exactly what it is asked.

> **Nothing in this file has been executed.** It is the plan R-A8C follows. The
> commands are written out so they are reviewed before they are typed, not
> composed under time pressure with production down.

---

## 0. The one thing that must be done first

**FalkorDB's persistence is not on its volume.** The deployed
`docker-compose.prod.yml` starts FalkorDB without `--dir /data`, so Redis
defaults `dir` to the image's working directory, `/FalkorDB`. Every RDB and AOF
write lands in the **container's writable layer**; the mounted volume
`contraclaim_falkordb_data` is empty, and the nightly backup — which archives
that volume — produces a valid, verified, 89-byte archive of one empty
directory. Every run reports success.

Two consequences, both load-bearing for this window:

1. **`docker compose stop` is safe; `docker compose down` is not.** `stop` leaves
   the container in place, writable layer included. `down` (with or without
   `-v`) removes containers, and removing `contraclaim-falkordb-1` destroys the
   only on-host copy of the `contraclaim` graph.
2. **The graph must be captured out of the writable layer before anything else
   in this window happens**, and again after the application tier is stopped.
   `scripts/production_backup.sh` alone does not capture it.

The release branch fixes the cause — `--dir /data` in
`docker-compose.prod.yml`, plus `scripts/backup_volume.sh`, which fails the
backup when the archive holds none of its required entries. Neither is deployed
yet. Until the cutover recreates FalkorDB **with the volume pre-seeded from the
rescue archive**, the rescue capture below is the backup.

### Rescue capture (read-only against the container)

```bash
STAMP=$(date '+%Y%m%d-%H%M%S')
TMP=$(mktemp -d /tmp/falkor-rescue.XXXXXX)
docker exec contraclaim-falkordb-1 sh -c \
  'PW=$(printf "%s" "$REDIS_ARGS" | sed -n "s/.*--requirepass \([^ ]*\).*/\1/p"); \
   redis-cli --no-auth-warning -a "$PW" BGSAVE'
sleep 5
docker cp contraclaim-falkordb-1:/FalkorDB/dump.rdb        "$TMP/dump.rdb"
docker cp contraclaim-falkordb-1:/FalkorDB/appendonlydir   "$TMP/appendonlydir"
ARCH=/var/backups/contractdms/volumes/falkordb-persistence-${STAMP}.tar.gz
tar -czf "$ARCH" -C "$TMP" .
rm -rf "$TMP"

# A file is not a backup. Prove the entries are there.
bash scripts/backup_volume.sh --verify "$ARCH" '*dump.rdb' '*appendonlydir*'
sha256sum "$ARCH" | tee -a /var/backups/contractdms/manifests/checksums-${STAMP}.sha256
```

The archive is shaped to restore straight into a volume mounted at `/data`, so
`scripts/production_restore_volumes.sh` consumes it unchanged.

---

## 1. Preconditions

| | Check |
|---|---|
| Production healthy | replica set `rs0` 3/3 with a PRIMARY, every container `healthy`, `/health/ready` 200 |
| Fresh backup verified | Mongo archive, four volume archives **and** the FalkorDB rescue archive, each opened and checked for required entries, each checksummed |
| Before-state frozen | §2 captured to a file the after-state is diffed against |
| Swap | present (§7) |
| Release source on the host | §8 |
| Staging credentials | present (§9) |
| Backup cron disabled | `/etc/cron.d/contraclaim-backup` fires at 01:30 daily and shells into the stopped stack. Either schedule the window clear of 01:30–02:00 IST or comment the entry for its duration |
| `certbot.timer` | a renewal attempt during the window fails while nginx is down. Stop the timer with nginx and start it again with nginx |
| **Time budget** | **§1a. The only precondition that expires. It is checked last, immediately before the stop, and again at the stop** |

---

## 1a. The time gate — the last thing checked before the stop

Every other precondition in §1 answers a question about state. None of them
answers *"is there enough window left to finish this and still hold the
recovery reserve?"*, and that is the question R-A8P got wrong.

R-A8P's window opened at `08:05:07Z` for four hours, reserving the final 90
minutes for teardown, restart, recovery verification and observation — so the
reserve began at `10:35:07Z`. Its PRE-OUTAGE GO/NO-GO was **24/24 GREEN at
`08:15Z`**. The session then sat idle for about two and a half hours, and the
production stop executed at **`10:42:04Z`** — seven minutes *inside* the
reserve, with staging not yet started. Nothing refused it, because a GO computed
from twenty-four state checks stays "true" for as long as the state holds, and
the clock is not one of the things it was checking.

### The contract

```
NOW + EXPECTED_EXECUTION_BUDGET + RECOVERY_RESERVE  <=  WINDOW_END
NOW < HARD_RECOVERY_START            (= WINDOW_END - RECOVERY_RESERVE)
```

with

```
LATEST_SAFE_STOP = HARD_RECOVERY_START - EXPECTED_EXECUTION_BUDGET
```

`scripts/check_maintenance_time_budget.py` is the only thing that decides it.
Exit 0 is GO, exit 2 is NO-GO, and exit 2 is also what a malformed window end, a
non-positive reserve or an absent execution budget produce — a gate that cannot
evaluate its inputs refuses.

### The sequence, and the reason it is an order rather than a list

```
PREP  →  broad GO/NO-GO  →  FINAL CLOCK RECHECK  →  TIME-BUDGET GATE #1
      →  record OUTAGE_START  →  TIME-BUDGET GATE #2  →  audited stop
```

**Nothing goes between the last time gate and the stop** except recording
`OUTAGE_START` and issuing the stop itself. No re-reads, no "one more check", no
waiting on anything. If the operator or the session pauses materially after gate
#1 — a break, a question, a slow command — **gate #1 is re-run from the top**.
"It was green earlier" is not evidence and is not accepted as one.

```bash
WINDOW_END=2026-09-08T12:05:07+00:00      # from the owner, not invented
RESERVE=90                                 # minutes, production recovery
BUDGET=120                                 # minutes, see "Execution budget" below
GATE=/var/backups/contraclaim-stg-evidence/<run-id>/time-gate.json

# GATE #1 — after every other GO item passes, and not before
python3 scripts/check_maintenance_time_budget.py \
  --window-end "$WINDOW_END" \
  --recovery-reserve-minutes "$RESERVE" \
  --execution-budget-minutes "$BUDGET" \
  --max-authorization-age-seconds 300 \
  --emit-authorization "$GATE"            # exit 0 or the window is over

date -u '+OUTAGE_START %Y-%m-%dT%H:%M:%SZ' | tee -a "$EVIDENCE/outage.txt"

# GATE #2 — immediately before the stop. Re-reads the clock, recomputes the
# budget, and refuses an authorization older than its declared lifetime.
python3 scripts/check_maintenance_time_budget.py --confirm "$GATE"

$PROD stop gateway client backend contract-worker    # only if gate #2 exited 0
```

The authorization document holds clock data only — window end, reserve, budget,
issue time, expiry, verdict — so it is safe to seal into the evidence directory,
and it is the record of *when* the stop was authorized rather than *that* it
was.

### Execution budget

`EXPECTED_EXECUTION_BUDGET` is the time the work between the stop and the
reserve needs: production stop, staging bring-up, migration recertification,
Gate 2, the stale Gate 7 and Gate 8 bullets, Gate 3 bullet 1, and the evidence
freeze. It **excludes** the recovery reserve, which pays for teardown, restart,
recovery verification and observation.

The only measurement this repository has is R-A8M: **59 minutes 23 seconds** of
outage for a strict *superset* of that work — it also covered every Gate 3,
Gate 7 and Gate 8 bullet, plus the teardown and the production restart that now
live inside the reserve. One measurement, one host, no variance data, so the
planning value is that measurement doubled and rounded up to the next quarter
hour: **120 minutes**. Do not plan with 59; it is a floor that has been observed
once, not a budget.

A four-hour window therefore permits a stop no later than `WINDOW_START + 30m`,
which leaves nothing for the pre-outage revalidation. **Book five to six hours**
unless the budget itself has been re-derived from a newer measurement.

---

## 2. Before-state to freeze

Capture all of this to a file **before** stopping anything. The after-state in
§5 is a diff against it, not a fresh judgement.

```bash
{
  echo "### containers";      docker ps -a --format '{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Image}}'
  echo "### compose labels";  for c in $(docker ps -aq); do
                                docker inspect -f '{{.Name}} proj={{index .Config.Labels "com.docker.compose.project"}} svc={{index .Config.Labels "com.docker.compose.service"}}' "$c"
                              done
  echo "### volumes";         docker volume ls
  echo "### networks";        docker network ls
  echo "### ports";           docker ps --format '{{.Names}}\t{{.Ports}}'; sudo ss -tlnp
  echo "### replica set";     docker exec contraclaim-mongo1-1 mongosh --quiet --eval \
                                'const s=rs.status(); print(s.set); s.members.forEach(m=>print(m.name,m.stateStr,m.health))'
  echo "### falkor graphs";   docker exec contraclaim-falkordb-1 sh -c \
                                'PW=$(printf "%s" "$REDIS_ARGS" | sed -n "s/.*--requirepass \([^ ]*\).*/\1/p"); redis-cli --no-auth-warning -a "$PW" GRAPH.LIST'
  echo "### qdrant";          docker exec contraclaim-backend-1 python -c 'import os,urllib.request,json;u=os.environ["QDRANT_URL"];r=urllib.request.Request(u+"/collections",headers={"api-key":os.environ["QDRANT_API_KEY"]});print([c["name"] for c in json.load(urllib.request.urlopen(r))["result"]["collections"]])'
  echo "### redis keyspace";  docker exec contraclaim-redis-1 sh -c 'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" INFO keyspace'
  echo "### readiness";       docker exec contraclaim-backend-1 python -c 'import json,urllib.request;print(json.dumps(json.load(urllib.request.urlopen("http://127.0.0.1:8000/health/ready")),indent=1))'
} > /var/backups/contractdms/manifests/before-state-$(date '+%Y%m%d-%H%M%S').txt
```

Two facts this capture records that nothing else does:

* **`contraclaim-clamav-1` carries a stale `project.working_dir` label**
  (`/opt/contraclaim-dms`, where no compose file exists any more). Compose selects
  containers by the **project label**, so it is still stopped and started by the
  commands below — but verify it by name rather than assuming.
* **Two unmanaged sidecars** are running: `contraclaim-arbitration-audit-new`
  (no compose labels at all) and `contraclaim-arbitration-audit-test`
  (`project=contraclaim`, `service=backend`, no working dir). Both are
  `tail -f /dev/null` shells from 2026-07-24 with `restart: no`, attached to
  `contraclaim_data-net` and `contraclaim_service-net`. They perform no business
  function, and compose `stop` will not reliably reach the first one.

---

## 3. Stop sequence

```bash
cd /opt/contraclaim-dms/projectDMS
PROD='docker compose -p contraclaim -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml --env-file .env'
```

> **NEVER `docker compose down`, with or without `-v`, against project
> `contraclaim`.** `-v` deletes the data volumes; `down` on its own deletes the
> containers, and that destroys the FalkorDB graph (§0). `stop` is the only verb
> used here.

| # | Step | Command |
|---|---|---|
| 1 | Health + before-state | §2 |
| 2 | Hot backup | `RETENTION_DAYS=100000 STAMP=$STAMP bash scripts/production_backup.sh` |
| 3 | Suspend the backup cron | comment `/etc/cron.d/contraclaim-backup` |
| **3a** | **TIME-BUDGET GATE #1** | **§1a — after every other GO item, and nothing else between this and step 5 but 3b and 4** |
| **3b** | **Record `OUTAGE_START`** | `date -u '+OUTAGE_START %Y-%m-%dT%H:%M:%SZ' \| tee -a "$EVIDENCE/outage.txt"` |
| 4 | Stop ingress | `sudo systemctl stop certbot.timer && sudo systemctl stop nginx` |
| **4a** | **TIME-BUDGET GATE #2** | `python3 scripts/check_maintenance_time_budget.py --confirm "$GATE"` — **RED here means abort before step 5, no exception** |
| 5 | Stop the application tier | `$PROD stop gateway client backend contract-worker` |
| 6 | Stop the unmanaged sidecars **by name** | `docker stop contraclaim-arbitration-audit-new contraclaim-arbitration-audit-test` |
| 7 | Quiesced backup — the authoritative one | repeat step 2 with a new `$STAMP`, then the §0 rescue capture |
| 8 | Verify every archive before going further | `scripts/backup_volume.sh --verify` per archive; `python3 scripts/backup_status.py --root /var/backups/contractdms --max-age-hours 26` |
| 9 | Stop the data tier | `$PROD stop clamav qdrant falkordb redis mongo3 mongo2 mongo1` |

Step 7 is deliberately after step 5: a backup taken while the application is
writing is a fuzzy point in time. The hot backup at step 2 is the one that
survives a failure *during* the stop.

Steps 3a, 3b and 4a are the R-A8P remediation and they are an order, not a
checklist. Step 4a re-reads the clock and recomputes the budget; it refuses an
authorization older than five minutes, so a pause anywhere after step 3a turns
into a refusal rather than into an overrun. **If step 4a is RED, ingress has
been stopped and nothing else has: start nginx and `certbot.timer` again,
restore the backup cron, and reschedule.** That is a sub-minute reversal, which
is the whole reason gate #2 sits before step 5 rather than after it.

### Proofs required before staging is created

```bash
# 1. Nothing of production is running.
docker ps --format '{{.Names}}' | grep -E '^contraclaim' && echo "STOP: still running" || echo "OK: production stopped"

# 2. Every production volume still exists. Expect 9 project volumes plus the two
#    loose pre-compose volumes (falkordb_data, qdrant_data).
docker volume ls --format '{{.Name}}' | grep -E '^(contraclaim_|falkordb_data$|qdrant_data$)' | sort

# 3. Every production network still exists.
docker network ls --format '{{.Name}}' | grep '^contraclaim_' | sort

# 4. Diff both against the frozen before-state. Any missing line is an abort.
```

---

## 4. Restart / rollback sequence

This is both the end-of-window restart and the rollback from any failure after
§3. Write it down before staging exists, because that is when it is needed.

> **Use `start`, never `up -d`.** `up` recreates a container whose configuration
> hash no longer matches, and recreating `contraclaim-falkordb-1` destroys the
> graph in its writable layer (§0). `start` restarts the same containers.

```bash
cd /opt/contraclaim-dms/projectDMS
PROD='docker compose -p contraclaim -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml --env-file .env'

# 1. Data tier, replica set first.
$PROD start mongo1 mongo2 mongo3

# 2. Wait for a PRIMARY. rs0 elects on its own; it does not need re-initiating.
#    NEVER run rs.initiate() here - the config is already in the data volumes.
until docker exec contraclaim-mongo1-1 mongosh --quiet --eval \
      'quit(rs.status().members.some(m => m.stateStr === "PRIMARY") ? 0 : 1)'; do sleep 5; done
docker exec contraclaim-mongo1-1 mongosh --quiet --eval \
  'const s=rs.status(); print(s.set); s.members.forEach(m=>print(m.name,m.stateStr,m.health))'

# 3. Remaining data services.
$PROD start redis qdrant falkordb clamav

# 4. Application tier, then the edge.
$PROD start backend contract-worker
$PROD start client gateway

# 5. Restore the before-state exactly: the two sidecars have restart: no and
#    will not come back on their own. Restart them unless the owner has
#    authorised their removal separately.
docker start contraclaim-arbitration-audit-new contraclaim-arbitration-audit-test

# 6. Ingress and schedulers.
sudo systemctl start nginx && sudo systemctl start certbot.timer
# uncomment /etc/cron.d/contraclaim-backup
```

### Post-restart verification

```bash
bash scripts/post_deploy_verify.sh          # the copy on the server, not the release copy
```

The **release** branch's `post_deploy_verify.sh` fails without a running
`document-worker`, and production has no such service (§6). Until the cutover
deploys it, run the server's own copy.

Then re-take every §2 capture and diff:

| Check | Expected |
|---|---|
| `/health/live`, `/health/ready` | 200, `status: ready`, `checks.*` all `ok` |
| Replica set | `rs0`, 3 members, exactly one PRIMARY, `health=1` |
| Falkor | `GRAPH.LIST` → `contraclaim`, and the node/relationship counts from the before-state |
| Qdrant | collection `contracts`, status `green`, point count unchanged |
| Redis | `db0` and `db1` key counts unchanged |
| Volumes / networks | identical name sets to the before-state |
| Public edge | `https://contraclaim.com` 200 through host nginx |

---

## 5. Staging teardown

Staging is disposed of with `down -v`, which is the one destructive command in
this window. It is gated.

### Enumerate ownership first

```bash
docker ps  -a --filter label=com.docker.compose.project=contraclaim-stg --format '{{.Names}}'
docker volume  ls --filter label=com.docker.compose.project=contraclaim-stg --format '{{.Name}}'
docker network ls --filter label=com.docker.compose.project=contraclaim-stg --format '{{.Name}}'
```

Every line must begin `contraclaim-stg`. A single `contraclaim_` name is an
abort.

### Then run it through the guard

```bash
cd /opt/contraclaim-stg/projectDMS
STG='docker compose -p contraclaim-stg
     -f docker-compose.prod.yml
     -f docker-compose.mongo-replicaset.yml
     -f docker-compose.staging.yml
     --env-file .env.staging'

$STG config --format json > /tmp/staging-rendered.json

python3 scripts/staging_teardown_guard.py \
  --project contraclaim-stg \
  --rendered-config /tmp/staging-rendered.json \
  -- $STG down -v --remove-orphans
```

`scripts/staging_teardown_guard.py` refuses, and does not run the command,
unless **all** of the following hold:

* the project is exactly `contraclaim-stg` — an allowlist of one, so
  `contraclaim`, an empty `COMPOSE_PROJECT_NAME`, and any unrecognised name are
  all refused;
* the rendered configuration names that same project;
* the command line after `--` selects that same project;
* every declared volume and network resolves to a name under
  `contraclaim-stg_`, whether that name comes from the project prefix or from a
  pinned `name:`;
* no volume or network is `external` — an external resource is attached by its
  literal name and survives `down -v`, so a disposable stack must own everything
  it names.

It decides over the *rendered* configuration rather than over the command line,
because `external:` and a pinned `name:` are invisible in the command and are how
a correctly-named project reaches a production volume. It reports resource names
only; `docker compose config` resolves environment values, so echoing the
document would print secrets.

`--remove-orphans` is scoped to `-p contraclaim-stg` and cannot reach a container
carrying the production project label.

### After teardown

```bash
docker volume  ls --format '{{.Name}}' | grep contraclaim-stg && echo "STOP: residue" || echo "OK: no staging volumes"
docker network ls --format '{{.Name}}' | grep contraclaim-stg && echo "STOP: residue" || echo "OK: no staging networks"
# and re-run the §3 proofs: every production volume and network still present
sudo rm -rf /var/backups/contraclaim-stg   # staging backup root, never /var/backups/contractdms
```

---

## 6. Deployed-source drift the cutover must reconcile

Read from the server, not from a marker file.

| Item | Server today | Release branch | Why | Intentional | Cutover action |
|---|---|---|---|---|---|
| Checkout HEAD | `main` @ `b2d5025` (2026-08-13), reached by `pull --ff-only` | `release/contraclaim-rc1` @ `8fc5a4c` | production tracks `main`; the release branch has never been pushed to a remote the host can reach | yes | fetch the release branch (§8) into `/opt/contraclaim-stg` — **not** into `/opt/contraclaim-dms` during the rehearsal |
| `.deployed-git-commit` | 41 bytes: `aad618c349e003cfca7e1d3ad11e7397d397c01f` **+ a literal `n`** | n/a | written by an out-of-band deploy path (no script in `scripts/` writes it) using an `echo -n "…\n"` idiom that emitted the `n` as text | **no — this is a defect** | do not trust it. It is both malformed *and* stale: `aad618c` is a real commit and an ancestor of HEAD, with **10 commits** merged past it across three fast-forwards on 12–13 Aug that never rewrote the marker |
| `document-worker` | absent from the server's `docker-compose.prod.yml` and not running | present, plus `document-worker-canary` at 0 replicas | added on the release branch by `2b1eef7`; never deployed | yes | the cutover introduces the service. Until then the release copy of `post_deploy_verify.sh` cannot pass on production |
| Built images | `backend` 2026-08-13 13:23, `client` 2026-08-13 13:43, `contract-worker` 2026-08-11 23:43 (IST) | one tree | each service was rebuilt at a different point in the Aug 11–13 sequence, so the three running images correspond to **three different commits** | no | rebuild all application images from one commit at cutover; record that commit |
| `contraclaim-clamav-1` | `project.working_dir=/opt/contraclaim-dms` (no compose file there now) | n/a | created by a compose invocation from the parent directory | no | harmless — compose selects by project label — but verify clamav by name in §3 and §4 |
| Unmanaged sidecars | 2 running, 9 exited scratch containers, plus loose `falkordb_data` / `qdrant_data` volumes and 8 anonymous volumes | n/a | manual `docker run` debugging from 2026-07-24 onward | no | stop by name for the window; removal is a separate authorised cleanup, not part of this one |

`.deployed-git-commit` is not authoritative for any decision in this runbook.
The authoritative facts are the checkout's `git rev-parse HEAD`, the image build
timestamps, and the container labels.

---

## 7. Swap

The host has **0 B of swap** and 22.91 GiB of RAM. With production stopped,
roughly 20.5 GiB is available to staging: ~2.4 GiB is OS plus Docker daemon, and
the ~4.7 GiB the production containers hold today is returned.

The staging steady state is ≈13 GB. The remaining ≈7.5 GiB has to absorb, in the
same window: the backend image build (build-essential plus 123 pip packages), the
client node build, the Gate-2 pytest runner, Gate-3's Playwright browser, and
Gate-8's restore drill — which stands up a **second** isolated data tier.
`docs/STAGING_HOST_PROVISIONING.md` budgets 8 GB for the OS and test runner
alone. `/tmp` and `/dev/shm` are 12 GB **tmpfs**, so anything a build or a test
writes there is charged to the same RAM.

**Verdict: SWAP REQUIRED BEFORE R-A8C.**

Recommended: an **8 GiB** swapfile on `/` (133 GB free), `vm.swappiness=10` so
resident mongod pages are not evicted preferentially, created as its own
authorised step at the **start** of the window — before production is stopped, so
it also covers a peak during the stop — and removable in one command afterwards.

```bash
sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
sudo sysctl -w vm.swappiness=10
swapon --show; free -h
# permanent, only if the owner wants it kept:
#   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
#   echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/60-swappiness.conf
# removal after the window:
#   sudo swapoff /swapfile && sudo rm /swapfile
```

Honest limit: swap does not prevent an OOM kill when a single process's resident
set exceeds RAM. What it buys is that a *transient* peak degrades throughput
instead of killing a container — which is the correct trade for a rehearsal whose
whole purpose is to run peaks back to back. No OOM event appears in 60 days of
this host's journal, so this is prevention, not a response to an incident.

---

## 8. Getting the release onto the host

`release/contraclaim-rc1` exists only in the local worktree. The host's single
remote is `https://github.com/ManishPandey21/contraclaim-dms.git`, whose heads
are `main`, `integrate/key-date-eot`, `feat/letter-deletion-cascade` and four
`codex/*` branches. The branch is not reachable from the server.

| Option | Traceability | Reproducibility | Rollback | Remote mutation |
|---|---|---|---|---|
| **A. Push the branch to both remotes** | full — the certified SHA is a named ref anyone can fetch | `git fetch && git checkout 8fc5a4c` | `git push --delete <remote> release/contraclaim-rc1` | **additive only**: a new ref. No branch rewritten, `main` untouched |
| B. Signed/verified git bundle, scp'd to the host | SHAs preserved, but provenance lives in a file somebody has to keep | `git clone rc1.bundle` — needs the bundle to still exist | delete the checkout | none |
| C. Archive/rsync of the worktree | **none** — history is lost | not reproducible | — | none |

**Recommendation: A**, pushed to *both* `origin` and `contraclaim` per
`CLAUDE.md`. It is the only option that leaves a durable, fetchable record of
exactly what was certified, which the release gate needs regardless of how the
bytes travel; and its mutation is a new ref, which is as reversible as a remote
operation gets. C is rejected outright: a rehearsal against a tree with no
history certifies nothing.

If the owner declines a push, fall back to **B**:

```bash
git bundle create /tmp/rc1.bundle release/contraclaim-rc1
git bundle verify /tmp/rc1.bundle
sha256sum /tmp/rc1.bundle            # record on both ends
scp /tmp/rc1.bundle contraclaim:/tmp/
ssh contraclaim 'sha256sum /tmp/rc1.bundle && git clone -b release/contraclaim-rc1 /tmp/rc1.bundle /opt/contraclaim-stg'
```

Either way the release lands in **`/opt/contraclaim-stg`**. It must never be
fetched into `/opt/contraclaim-dms`, which is the live production checkout.

**This phase is not authorised to push, and did not.**

---

## 9. Staging credentials — the minimum, and what may be shared

Four values are start-required (`:?` in `docker-compose.prod.yml`): the stack
does not render, let alone boot, without them. No production value is acceptable
for any of them, and none was read.

| Credential | Minimum requirement |
|---|---|
| `OPENAI_API_KEY` | a **separately issued** staging/test key |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | an IAM principal used by nothing else |
| `AWS_BUCKET_NAME` | a new, empty, staging-only document bucket |
| `BACKUP_S3_BUCKET` | a new, empty, staging-only backup bucket, distinct from the production backup bucket |

**AWS — the same account is acceptable.** What has to be isolated is the
*principal* and the *resources*, not the account boundary: no gate asks for an
account boundary, and none of the failure modes (staging writing into production
objects, staging credentials granting production access) survives an IAM policy
scoped to exactly two new buckets. Use the two-bucket minimum policy in
`docs/STAGING_HOST_PROVISIONING.md` §6 — no `s3:*`, no wildcard resource, no
production bucket named anywhere. A separate account is stronger and is not
required.

**OpenAI — the same organization is acceptable, a separate key is not
optional.** Gate 2 bullet 2 is a real round trip; what invalidates it is *reusing
the production key*, which makes the evidence non-attributable and puts staging
traffic on production's quota and rate limits. A separately issued key in the
same organization gives independent attribution and independent revocation.
Where the organization supports projects, put the key in its own project with its
own spend limit.

Everything else — `SECRET_KEY`, `REDIS_PASSWORD`, `FALKORDB_PASSWORD`,
`QDRANT_API_KEY`, `METRICS_TOKEN`, `SMTP_SETTINGS_ENCRYPTION_KEY` — is minted on
the host at provisioning time and exists nowhere else. SMTP is start-required but
no Gate 2 bullet exercises it, so a syntactically valid placeholder is
sufficient; a production mail credential is not acceptable.

`.env.staging` is written on the host from `.env.staging.example` and is never
sourced from `/opt/contraclaim-dms/projectDMS/.env`.

---

## 10. Operating system

The host runs **Ubuntu 25.04 (Plucky Puffin)**, kernel 6.14.0-37. The
provisioning specification asks for 22.04 or 24.04 LTS.

Measured, not assumed:

* the host's own `ubuntu-distro-info --supported` lists `jammy`, `noble`,
  `resolute` — **`plucky` is not among them**;
* `archive.ubuntu.com/.../dists/plucky/Release` and
  `security.ubuntu.com/.../dists/plucky-security/Release` both still return 200,
  and `old-releases.ubuntu.com` returns 404, so the archive has not been moved;
* Docker Engine 28.2.2 with overlay2 and cgroup v2 systemd reports **no
  warnings**; thirteen containers have run for 3–8 weeks; no OOM event appears in
  60 days of the journal.

**Disposition: not a release blocker; accept as dated debt.** The runtime stack
is demonstrably operating correctly, and nothing observed is caused by the OS
version. The exposure is that a release the distribution has stopped supporting
stops receiving security updates, which is an operations risk on a public-facing
host rather than a defect in this release. An OS upgrade or host rebuild must
**not** be attempted inside this maintenance window — it is a second, larger
change with its own rollback plan, and combining them makes both unrecoverable.

Recommended follow-up, outside this release: rebuild onto 24.04 LTS or 26.04 LTS
(both currently supported), which also removes the same-host staging problem
entirely if the rebuild produces a second host.

---

## 11. Duration estimate

Ranges, because none of these has been timed on this host. The outage is the
**entire** rehearsal: staging and production cannot both run here, which is what
verdict B says.

| Phase | Estimate |
|---|---|
| Swapfile + preconditions | 5–15 min |
| Backup + verification (hot, quiesced, FalkorDB rescue) | 20–40 min |
| Production stop + proofs | 15–30 min |
| Release delivery, `/opt/contraclaim-stg` checkout, `.env.staging` | 20–40 min |
| Staging image builds (backend + client) | 25–60 min |
| Staging startup, steps 3–5 and 10–15 | 20–40 min |
| Migrations: list, dry run, apply, idempotency re-run | 5–15 min |
| **Gate 2** | 15–40 min |
| **Gate 3** Playwright E2E | 20–60 min |
| **Gate 7** readiness + preflight in-container | 10–20 min |
| **Gate 8** restore drill (second isolated data tier) | 30–75 min |
| Staging teardown + residue proofs | 5–15 min |
| Production restart | 10–25 min |
| Post-restart verification and before/after diff | 10–25 min |
| **Total outage** | **≈ 3 h 30 m – 7 h** |

Book **8 hours** with a declared abort point: if the clock reaches the booked end
minus 60 minutes, stop the rehearsal wherever it is, run §5 teardown and §4
restart, and reschedule. §4 alone is 20–50 minutes and is the rollback from any
failure.

An outage of this length for a rehearsal is itself an argument for a separate
staging host. Verdict B does not require one; it is what removes both the outage
and the shared-daemon blast radius.
