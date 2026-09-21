# Production FalkorDB persistence cutover — `/FalkorDB` → `/data`

**Status (R-A9H, 2026-09-21): EXECUTED in R-A9G Attempt 2 (2026-09-20, Variant A). The graph
now persists on `/data` in `contraclaim_falkordb_data`; parity 156 / 220 / 4 labels held.**

> **CURRENT TEMPORARY TOPOLOGY — READ BEFORE ANY COMPOSE COMMAND.** The live engine is the
> out-of-band container `contraclaim-falkordb-cutover` (holds the `falkordb` alias on
> `contraclaim_data-net`). The compose `falkordb` service is the **preserved original**
> `contraclaim-falkordb-1` (`de249ee3df20…`), stopped, `restart: no`, still configured with
> the `falkordb` alias, `dir=/FalkorDB` and the **pre-rotation** password. `backend`,
> `contract-worker`, `document-worker` and `document-worker-canary` all declare
> `depends_on: falkordb: service_healthy`, so **any** `docker compose up` that names one of
> them without `--no-deps` — and every blanket `docker compose up -d` — starts that original:
> two containers answer to `falkordb`, and one of them serves stale data on a dead credential.
>
> * Never run a blanket `docker compose up -d` or `docker compose start`.
> * Always pass `--no-deps` when recreating an application service.
> * A host reboot is safe: Docker restart policies do not evaluate compose `depends_on`, the
>   original is `restart: no`, the cutover engine is `unless-stopped`.
>
> This warning is retired by the normalization in §5.4, which is **deferred** until its
> retention conditions hold (`docs/R_A9H_POST_PRODUCTION_CLOSURE.md` §4).

~~Status: PROCEDURE ONLY. NOT EXECUTED. Requires its own owner authorisation and
a maintenance window.~~ Nothing in R-A8R touched production; every state below
was read from the running deployment on 2026-09-08 and is re-derivable with the
commands quoted.

This is a **production cutover blocker**, in the precise sense that deploying
`release/contraclaim-rc1` without it leaves the nightly backup failing loudly
every night. That is correct, intended, fail-visible behaviour — and it is a
thing to schedule rather than discover.

---

## 1. The current state, measured

| Fact | Value | How it was read |
|---|---|---|
| Container | `contraclaim-falkordb-1`, id `de249ee3df20…` | `docker ps -a --filter name=falkor` |
| Image | `falkordb/falkordb:v4.0.8` | same |
| Restart policy | `unless-stopped` | `docker inspect` |
| Persistence directory | **`/FalkorDB`** | `redis-cli CONFIG GET dir` inside the container |
| `appendonly` | `yes`, `appenddirname` `appendonlydir`, `dbfilename` `dump.rdb` | `CONFIG GET` |
| On-disk state | `/FalkorDB/dump.rdb` 306,212 B; `/FalkorDB/appendonlydir/` 3 members (base RDB, incr AOF, manifest) | `docker exec … ls -la` |
| Mounted volume | `contraclaim_falkordb_data` → `/data` | `docker inspect … .Mounts` |
| Volume contents | **empty** — `.` and `..` only | `ls -la /var/lib/docker/volumes/contraclaim_falkordb_data/_data` |
| Graph | `contraclaim`: **156 nodes, 220 relationships**, labels `Contract`, `ContractDocument`, `Clause`, `Letter` | `GRAPH.LIST`, `GRAPH.QUERY` |
| Deployed compose `REDIS_ARGS` | **no `--dir`** | `/opt/contraclaim-dms/projectDMS/docker-compose.prod.yml` |
| Release compose `REDIS_ARGS` | **`--dir /data`** | `docker-compose.prod.yml:392` |

**Why the volume is empty.** The image's `WORKDIR` is `/FalkorDB` and Redis
defaults `dir` to the working directory. Without `--dir` the RDB and the AOF are
written into the **container's writable layer**, not into the mount. The graph's
only on-host copy therefore lives inside a container object, and
`docker rm` on that object destroys it.

**Why this becomes visible on deploy.** `scripts/production_backup.sh` archives
the `falkordb-data` volume; the volume holds nothing, so the archive is 89 bytes.
Until R-A8Q that archive was reported `falkordb-data: ok` on freshness alone.
`services/backup_archive_validation.py` now opens it, finds no `dump.rdb` and no
non-empty `appendonlydir/` member at the archive root, and returns
`INVALID_CONTENT`. `production_backup.sh` runs under `set -e`, so the nightly run
stops there.

**Do not soften the check.** The archive genuinely holds no graph. The correct
response is to move the data, not to lower the bar back to a size test — which is
the exact defect R-A8P F4 recorded, where a 14.7 MB archive was verified,
checksummed, kept, and unrestorable.

---

## 2. What "the backup is valid" means, and who agrees

One definition, one seam: `backend/rbac_backend/services/backup_archive_validation.py`.

| Caller | How it reaches the seam |
|---|---|
| `scripts/production_backup.sh` | calls `scripts/backup_volume.sh` per volume |
| `scripts/backup_volume.sh` | `scripts/validate_backup_archive.py --profile redis-persistence` |
| `scripts/backup_status.py` | `services/operations_health.py` → `validate_archive` |
| `scripts/post_deploy_verify.sh` | `backup_status.py` |
| `scripts/pre_deploy_readiness.sh` | `backup_status.py` |
| `GET /health/operations` | `operations_health` |
| `scripts/falkordb_rescue_archive.sh` | validates its own output before exiting 0 |
| `scripts/falkordb_rescue_restore_drill.sh` | the rescue helper, plus archive-root inspection |
| `docs/SAME_HOST_STAGING_MAINTENANCE_WINDOW.md` | the same `validate_backup_archive.py` invocation |

The `redis-persistence` profile accepts, **at the archive root**, a usable RDB
(non-empty, `REDIS` magic) **or** a usable multi-part AOF — either alone is a
complete recovery artefact. Refused, each with its own test:

* an 89-byte archive of an empty directory → `INVALID_CONTENT`;
* entries under a `FalkorDB/` prefix → `INVALID_ROOT`, with the re-pack command;
* module binaries and no persistence → `INVALID_CONTENT`;
* an empty `appendonlydir/` with no `dump.rdb` → `INVALID_CONTENT`;
* a zero-byte or magic-less `dump.rdb` → `INVALID_CONTENT`.

Accepted: RDB-only, AOF-only, RDB+AOF. `backend/rbac_backend/tests/test_backup_archive_validation.py`
and `test_backup_health_content_validation.py` carry all of them — **66 tests
pass** across the archive, health and runbook modules as of this phase.

`ok` is gone from the per-artifact vocabulary. Only `VALID` and `UNVERIFIED` (no
contract declared) are healthy; `UNEVALUATED` — contract declared, not applied —
is **not**, because marking success on a skipped step is the defect the module
exists to close.

---

## 3. The rollback shape decides the procedure, so read this first

The cutover's rollback must not depend on reconstructing the old writable layer.
That constraint rules out the obvious sequence.

`docker compose up -d falkordb` against the release compose **recreates** the
service container and **removes the old one**. The old container's writable layer
— which is where the entire production graph lives today — goes with it. A
rollback after that point has nothing to start; it can only restore, which is
reconstruction.

So there are two variants, and they are not equivalent.

### Variant A — out-of-band new container (satisfies the constraint)

The release container is created directly with `docker run`, under its own name
and with a `falkordb` network alias, so **compose never touches the original
container object**. The original stays on the host, stopped, holding its layer,
for the whole acceptance period. Rollback is `docker stop` the new one and
`docker start` the original — no restore, no reconstruction.

Cost: the new container is not compose-managed until after acceptance, so its
healthcheck, restart policy and logging options must be supplied on the command
line and then re-derived when it is adopted back into compose.

### Variant B — compose-managed (simpler, weaker rollback)

`docker compose stop falkordb`, seed the volume, `docker compose up -d falkordb`.
The original container object is removed at that step. Rollback then means
starting a container from `docker commit`'s snapshot of the original layer, or
restoring the rescue archive — both reconstruction.

**Variant B does not meet the stated rollback requirement.** It is written down
because it is materially simpler and the owner may accept image-based rollback;
it must not be chosen by default.

**Recommendation: Variant A**, with the commit snapshot and the validated rescue
archive kept as independent nets regardless of variant.

### OWNER DECISION, 2026-09-20 (release programme R-A9F): **VARIANT A**

**RE-CONFIRMED by the owner on 2026-09-20 in release programme R-A9G-0**
(`docs/R_A9G_OWNER_DECISION_RECORD.md` section 9), which restated the seventeen required
steps and recorded that **no Variant-A-specific owner choice remains open before R-A9G**.

The owner has chosen **Variant A**. Variant B is **withdrawn** for this cutover and must
not be substituted for convenience during the window. The decision is recorded in
`PRODUCTION_CUTOVER_CHECKLIST.md`'s standing-decisions table, which previously carried this
row as OPEN, and in `docs/R_A9F_OPEN_GATE_MATRIX.md`.

What that binds the R-A9G window to:

* The original production FalkorDB container object (`contraclaim-falkordb-1`, id
  `de249ee3df2009c0ac12655cad95a9edc6d995d152d4632131d5fe74e5a712fc` as of 2026-09-20,
  `dir=/FalkorDB`) is **preserved, stopped, not removed**, for the whole acceptance period.
  Rollback is `docker stop` the new engine and `docker start` the original - never a restore.
* The release engine is created out of band with `docker run`, under its own name and with a
  `falkordb` network alias, so **no compose command may address the `falkordb` service**
  until the owner records acceptance and it is adopted back into compose (section 5.4).
* `FALKORDB_PASSWORD` is rotated **only after** graph parity is proven (section 4.19), never
  before.
* The rescue archive and the commit snapshot are kept as independent nets regardless.

R-A9E already exercised the first three steps of the sequence against live production on
2026-09-20. It was not purely read-only, and the distinction matters: the rescue script issues a
`BGSAVE` so the snapshot on disk is current, which writes inside production's own persistence
directory. Nothing else about production was touched - the container object was the same object,
still running, 0 restarts, before and after. What ran: a hot rescue archive taken from `/FalkorDB`
(`falkordb-persistence-20260920T110319Z-RA9E-hot.tar.gz`, sha256 `ebecc4cea8a273a5...`),
semantically validated under the `redis-persistence` contract, and restored into a
**disposable** run-owned volume and engine that came up with `dir=/data` and reproduced the
production graph exactly - **156 nodes, 220 edges, 4 labels (`Contract`, `ContractDocument`, `Clause`, `Letter`)**, matching the live inventory -
*(R-A9H correction, 2026-09-21: first written as "7 labels", which counted the output lines of
`CALL db.labels()` - a `label` header, the four labels and two statistics lines - not labels.)*
after which the original container was re-checked and found to be the same object, running,
0 restarts. That proves the procedure; it does **not** substitute for step 4.2, which must
take a **fresh** archive inside the window.

---

## 4. Cutover sequence — Variant A

Every step is quoted with its own verification. A step that cannot be verified is
a step that has not been done.

`COMPOSE="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml -f docker-compose.clamav-r-a8z.yml"`

> **R-A9H: the ClamAV override was RETIRED on 2026-09-20T21:40Z (R-A9G §13a).** From then on
> `COMPOSE="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml"`. The note below
> describes the pre-retirement state and is kept as written.

> **The ClamAV override is part of that variable until it is retired.** Production's running
> `clamav` container carries a three-file `com.docker.compose.project.config_files` label, so a
> compose command built from only the first two files renders a *different* ClamAV service and
> will recreate it on `up`. `config` and `ps` still succeed with the wrong file set, so the
> mistake only surfaces at `up`. The override is retired in one deliberate step **after** the
> release stack is running and verified - see `PRODUCTION_CUTOVER_CHECKLIST.md` section 13a.
throughout — **not** the base `docker-compose.yml`, which makes `up` fail on a
missing `client/.env.development`.

### 4.1 Production health GO

```bash
docker compose $COMPOSE ps
curl -fsS http://<backend-ip>:8000/health/ready
scripts/post_deploy_verify.sh
```

Required: every container up, `rs0` 3/3 with exactly one PRIMARY, `/health/ready`
ready, `post_deploy_verify.sh` 0 failures. **A cutover does not start on a
degraded stack**; a rollback into an already-broken state has nothing to roll
back to.

Record the graph inventory now — it is the parity baseline for step 4.14:

```bash
docker exec contraclaim-falkordb-1 sh -lc '
  redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" GRAPH.LIST
  redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" GRAPH.QUERY contraclaim "MATCH (n) RETURN count(n)"
  redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" GRAPH.QUERY contraclaim "MATCH ()-[r]->() RETURN count(r)"
  redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" GRAPH.QUERY contraclaim "CALL db.labels()"'
```

Expected today: `contraclaim`; 156; 220; `Contract`, `ContractDocument`, `Clause`,
`Letter`.

### 4.2 Fresh canonical rescue archive

```bash
bash scripts/falkordb_rescue_archive.sh contraclaim-falkordb-1 \
  /var/backups/contraclaim/falkordb-rescue-$(date -u +%Y%m%dT%H%M%SZ).tar.gz
```

The helper reads `CONFIG GET dir` from the container, BGSAVEs (**a BGSAVE timeout
is fatal** — a stale snapshot passes every downstream check), copies `dump.rdb`
and `appendonlydir/` to the **root** of a staging directory, writes
`tar -C "$staging" .`, and validates before exiting 0.

Do not hand-type this. The hand-typed version is what produced R-A8O's
unrestorable `FalkorDB/`-rooted archive.

### 4.3 Semantic archive validation

```bash
python scripts/validate_backup_archive.py <archive> --profile redis-persistence
tar -tzf <archive> | head -20
sha256sum <archive> | tee <archive>.sha256
```

Required: exit 0, status `VALID`, entries `./dump.rdb` and `./appendonlydir/…` at
the root. **Size is not the signal.**

### 4.4 Disposable rescue restore proof

```bash
bash scripts/falkordb_rescue_restore_drill.sh
```

Required: **8/8 PASS**, including the two negative controls (the `FalkorDB/`
shape and the 89-byte archive). This exercises rescue → restore into a clean
volume → fresh container on `--dir /data` → graph inventory identical, against
disposable resources prefixed `ra8qrescue_`. It touches no live container, volume
or graph. It ran 8/8 on this host in R-A8Q; re-run it, do not cite that run.

### 4.5 Snapshot the original writable layer

```bash
docker commit contraclaim-falkordb-1 contraclaim/falkordb-rollback:$(date -u +%Y%m%dT%H%M%SZ)
docker image ls contraclaim/falkordb-rollback
```

Independent of the container object and of the archive. This is the net that
survives an accidental `docker rm`.

### 4.6 Stop ingress and the application tier

```bash
systemctl stop nginx
crontab -l   # comment the backup line for the window, and note that you did
docker compose $COMPOSE stop gateway client backend contract-worker
```

Ingress first, so no request is accepted after the graph stops being writable.
The two `restart: no` sidecars are stopped by name.

### 4.7 Preserve the original FalkorDB container

```bash
docker compose $COMPOSE stop falkordb           # STOP. Never `down`, never `rm`.
docker ps -a --filter name=contraclaim-falkordb-1 --format '{{.ID}} {{.Status}}'
```

Required: the container still **exists**, `Exited (0)`. Its id must still be
`de249ee3df20…`. If it is gone, the rollback in §5 is unavailable and the cutover
must stop.

> **R-A9G correction — how FalkorDB is actually stopped.** On `falkordb/falkordb:v4.0.8`,
> `docker stop` (and therefore `docker compose stop`) is **not** a reliable graceful stop: the
> image's PID 1 is a shell wrapper that does not forward `SIGTERM` to `redis-server`, so the
> engine is killed at the stop timeout without its final save. The canonical graceful stop,
> as executed in R-A9G Attempt 2 (evidence `21-falkor-shutdown-save.txt`), is:
>
> ```bash
> docker update --restart=no <container>        # FIRST, or the policy restarts it
> docker exec <container> sh -c 'REDISCLI_AUTH="$FALKORDB_PASSWORD" redis-cli SHUTDOWN SAVE'
> docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' <container>   # REQUIRED: exited 0
> ```
>
> The password is expanded **inside** the container. Never pass it on a host command line:
> `sudo` records argv in `/var/log/auth.log` and the journal keeps it (R-A9H receipt §9). Use
> `REDISCLI_AUTH` rather than `-a` even inside the container: a container process is a host
> process, and `-a` puts the password in its world-readable `/proc/<pid>/cmdline`. Anything
> other than `Exited (0)` is a failed stop and the persistence on disk is not trusted.

### 4.8 Seed the volume at the correct root

```bash
docker run --rm \
  -v contraclaim_falkordb_data:/data \
  -v "$(dirname <archive>)":/in:ro \
  busybox sh -c 'ls -A /data'
```

Required: **empty**. If it is not, stop — something already wrote there and the
seed would merge two states.

```bash
docker run --rm \
  -v contraclaim_falkordb_data:/data \
  -v "$(dirname <archive>)":/in:ro \
  busybox sh -c 'tar -xzf /in/$(basename <archive>) -C /data && ls -la /data'
```

Deliberately the archive and not `docker cp` from the live container: the archive
is the artefact that was validated in 4.3 and restore-proved in 4.4. Seeding from
anything else means seeding from something nothing checked.

### 4.9 Verify the persistence at the volume root

```bash
docker run --rm -v contraclaim_falkordb_data:/data busybox sh -c '
  ls -la /data
  head -c 5 /data/dump.rdb
  ls -la /data/appendonlydir'
```

Required: `/data/dump.rdb` non-empty and beginning `REDIS`; `/data/appendonlydir/`
with its manifest, base and incr members. **Not** `/data/FalkorDB/…` — that is
the `INVALID_ROOT` shape, and the engine does not look there.

**A note on which file loads.** With `appendonly yes` the engine loads the **AOF**,
not the RDB. Both are seeded, and the parity check in 4.14 is what proves the
loaded state is the right one. Do not seed the RDB alone on the assumption that
it wins.

### 4.10 Deploy the release checkout

```bash
git -C /opt/contraclaim-dms/projectDMS branch --show-current   # read it, every time
git -C /opt/contraclaim-dms/projectDMS pull --ff-only origin <that branch>
grep -n -A 20 '^  falkordb:' docker-compose.prod.yml | grep -- '--dir /data'
```

Required: `--dir /data` present in the checked-out compose. `test_deployment_config.py`
fails when `--dir` and the mount diverge; confirm it here anyway, because the
file on the server is the one that runs.

### 4.11 Create and start the release container, out of band

```bash
docker run -d \
  --name contraclaim-falkordb-cutover \
  --network contraclaim_data-net --network-alias falkordb \
  --restart unless-stopped \
  -v contraclaim_falkordb_data:/data \
  -e FALKORDB_PASSWORD="$FALKORDB_PASSWORD" \
  -e REDIS_ARGS="--dir /data --save 900 1 --save 300 10 --appendonly yes --requirepass $FALKORDB_PASSWORD --maxmemory 2gb --maxmemory-policy volatile-lru" \
  falkordb/falkordb:v4.0.8
```

The alias is what makes the consumers resolve `falkordb` to this container. The
original is stopped, so its own alias is not in DNS.

Confirm the network name first — `docker network ls | grep data-net` — and confirm
only one container answers the alias:

```bash
docker network inspect contraclaim_data-net \
  --format '{{range .Containers}}{{.Name}} {{end}}'
```

### 4.12 Authenticate

```bash
docker exec contraclaim-falkordb-cutover sh -lc \
  'redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" PING'
docker exec contraclaim-falkordb-cutover sh -lc \
  'redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" CONFIG GET dir'
```

Required: `PONG`, and `dir` = **`/data`**.

### 4.13 `GRAPH.LIST`

```bash
docker exec contraclaim-falkordb-cutover sh -lc \
  'redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" GRAPH.LIST'
```

Required: `contraclaim`. **An empty `GRAPH.LIST` is a STOP**, not a retry: the
data did not load, and the original container still holds it. Go to §5.

### 4.14 Graph parity

Re-run the 4.1 inventory against the new container. Required, exactly:

| | Before | After |
|---|---|---|
| graphs | `contraclaim` | `contraclaim` |
| nodes | 156 | **156** |
| relationships | 220 | **220** |
| labels | `Contract`, `ContractDocument`, `Clause`, `Letter` | identical set |

Counts are the check. "It started" is not.

### 4.15 Application graph smoke

```bash
docker compose $COMPOSE start backend contract-worker client gateway
curl -fsS http://<backend-ip>:8000/health/ready       # falkordb dependency ok
systemctl start nginx
curl -fsS -o /dev/null -w '%{http_code}\n' https://web.contraclaim.com/health
```

Then exercise a real graph read through the application — open a contract with
known clause/letter references and confirm they render. `/health/ready` proves
the socket; only a read proves the data.

### 4.16 Production backup

```bash
bash scripts/production_backup.sh
```

Required: exit 0, and the `falkordb-data` step no longer stops the run. This is
the whole reason the cutover is sequenced with the deploy.

### 4.17 Semantic backup validation

```bash
python scripts/backup_status.py --root /var/backups/contraclaim --max-age-hours 26
python scripts/validate_backup_archive.py \
  /var/backups/contraclaim/<latest>/falkordb-data.tar.gz --profile redis-persistence
```

Required: `falkordb-data` **`VALID`**, not `UNVERIFIED` and not `UNEVALUATED`.

### 4.18 Disposable restore proof from the NEW production backup

```bash
bash scripts/production_restore_volumes.sh --apply <disposable-volume> \
  /var/backups/contraclaim/<latest>/falkordb-data.tar.gz
```

into a **disposable** volume, then start a throwaway container on it and diff the
inventory against 4.14. The nightly archive being `VALID` says it satisfies the
contract; only this says it restores. Never restore into
`contraclaim_falkordb_data`.

### 4.19 Rotate `FALKORDB_PASSWORD`

The value has appeared in operational evidence. Rotate it **after** the graph is
proven, so a rotation failure is not tangled with a data-move failure.

One variable, several derived consumers. `docker-compose.prod.yml` builds
`FALKORDB_URL` and `GRAPHITI_DB_URL` from `${FALKORDB_PASSWORD}`, and compose
bakes environment at **create** time — so a `restart` keeps the old value and
every consumer must be **recreated**.

```bash
# 1. new value in .env only
#    (FALKORDB_URL in .env is not the one the containers read; the compose
#     template is. Change FALKORDB_PASSWORD and nothing else.)
# 2. change it in the engine, in place, without a restart
docker exec contraclaim-falkordb-cutover sh -lc \
  'redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" CONFIG SET requirepass "<new>"'
# 3. recreate every consumer so the derived URL is rebuilt
docker compose $COMPOSE up -d --no-deps --force-recreate backend contract-worker graphiti
# 4. prove it
docker exec contraclaim-falkordb-cutover sh -lc \
  'redis-cli --no-auth-warning -a "<new>" PING'
curl -fsS http://<backend-ip>:8000/health/ready
```

`CONFIG SET requirepass` is not persisted by itself; the new container created in
§6 picks the value up from `REDIS_ARGS`. Between step 2 and §6, `CONFIG REWRITE`
is **not** appropriate — this engine's config comes from arguments, not a file.

### 4.20 Update consumers atomically

Step 4.19's recreate is the atomic point. Verify no consumer is left on the old
credential:

```bash
for s in backend contract-worker graphiti; do
  docker compose $COMPOSE exec -T "$s" sh -lc 'echo "$FALKORDB_URL" | sed "s#://.*@#://***@#"'
done
```

### 4.21 Re-verify graph and application

Repeat 4.13, 4.14 and 4.15. Then:

```bash
scripts/post_deploy_verify.sh
```

Required: 0 failures. In production mode the edge check is now mandatory
(`docs/OPERATIONS.md` §6.1), so this also proves the public edge answered.

### 4.22 Observation, and retention of the rollback

Observe for at least 40 minutes, sampled every 5: container count, restart
counts, edge 200, `/health/live`, `/health/ready`, `rs0` 3/3 one PRIMARY, Falkor
`PONG`, Qdrant green, 0 backend exception signatures, 0 `die`/`oom` events.

**Retain, until explicit owner acceptance:**

* `contraclaim-falkordb-1`, stopped, not removed;
* `contraclaim/falkordb-rollback:<ts>`;
* the rescue archive and its `.sha256`.

---

## 5. Rollback

### 5.1 The decision point

Roll back on any of:

* `GRAPH.LIST` empty, or missing `contraclaim` (§4.13);
* node/relationship/label parity mismatch (§4.14);
* the application cannot read graph-backed data (§4.15);
* `/health/ready` does not reach ready with FalkorDB ok;
* the new backup will not validate or will not restore (§4.17, §4.18).

The point of no return is **not** reached at §4.11. It is reached only when the
original container is removed — which §6 does, and nothing before it does.

### 5.2 Procedure — before acceptance

```bash
# 1. stop the new engine and the consumers
systemctl stop nginx
docker compose $COMPOSE stop gateway client backend contract-worker
docker stop contraclaim-falkordb-cutover

# 2. PRESERVE the new volume. Do not delete it; it is the evidence for the
#    post-mortem, and deleting it destroys the only record of what was seeded.
docker volume ls | grep falkordb

# 3. restart the ORIGINAL container object
docker start contraclaim-falkordb-1
docker exec contraclaim-falkordb-1 sh -lc \
  'redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" CONFIG GET dir'   # /FalkorDB
```

If the credential was already rotated (§4.19), the original container still holds
the **old** `requirepass` in its own arguments, so restore the old value in `.env`
before recreating consumers — or re-apply the new one to the original with
`CONFIG SET requirepass`. Decide which before starting the rotation, not after.

```bash
# 4. verify the graph, against the SAME numbers as §4.1
docker exec contraclaim-falkordb-1 sh -lc \
  'redis-cli --no-auth-warning -a "$FALKORDB_PASSWORD" GRAPH.LIST'
#    plus node, relationship and label counts

# 5. revert the checkout if it was moved, and restart the application
git -C /opt/contraclaim-dms/projectDMS checkout <previous ref>
docker compose $COMPOSE up -d --no-deps --force-recreate backend contract-worker
docker compose $COMPOSE start client gateway
systemctl start nginx
scripts/post_deploy_verify.sh
```

`REQUIRE_FRESH_BACKUP` stays unset on the rollback path: with persistence back at
`/FalkorDB` the `falkordb-data` archive is 89 bytes again and correctly
`INVALID_CONTENT`. That is the pre-cutover state, and it must be reported as a
warning rather than block the recovery of a live service.

### 5.3 If the original container object is gone

Only then, and in this order:

1. start a container from `contraclaim/falkordb-rollback:<ts>` with the
   **original** `REDIS_ARGS` (no `--dir`), same volume, same network alias;
2. failing that, restore the §4.2 rescue archive into a clean volume and start
   the release configuration on it.

Both are reconstruction. Both are slower and less certain than 5.2, which is why
§4.7 exists.

### 5.4 When the original may be retired

All of the following, and not before:

* §4.14 parity held and §4.21 re-verified after the rotation;
* at least **one full nightly cycle** has run `production_backup.sh` to exit 0
  with `falkordb-data` `VALID`;
* one disposable restore from a **nightly** archive (not the cutover's own)
  reproduced the inventory;
* 24 h of clean observation — 0 restarts, 0 exception signatures;
* the owner records acceptance.

Then, and only then:

```bash
docker rm contraclaim-falkordb-1
docker image rm contraclaim/falkordb-rollback:<ts>
```

Adopt the engine back into compose in the same window:

```bash
docker stop contraclaim-falkordb-cutover && docker rm contraclaim-falkordb-cutover
docker compose $COMPOSE up -d --no-deps falkordb
```

> **R-A9H: use the §4.7 graceful stop, not `docker stop`,** for the cutover engine as well —
> `update --restart=no`, authenticated `SHUTDOWN SAVE`, require `Exited (0)` — before `docker rm`.
> The full normalization sequence, its verification and its rollback are specified in
> `docs/R_A9H_POST_PRODUCTION_CLOSURE.md` §4; that is the version to execute.

The volume is now the state, so this recreate is safe — which was the entire
point of the cutover.

---

## 6. What this does not do

* It does not change any AWS resource. Production documents and production
  backups still share one S3 bucket (`docs/S3_STORAGE_POSTURE_DEBT.md`).
* It does not apply migration `20260906_0001` or any G32 production migration.
  Those are separate authorisations in
  `docs/PRODUCTION_CUTOVER_CHECKLIST.md`.
* It does not rehearse `scripts/backfill_falkordb.py`. The graph is derived state
  and Mongo is canonical, so a rebuild is the last resort — but its runtime
  against a production-sized corpus is unmeasured and it is **not** a substitute
  for a verified backup.
* It proves nothing about RTO. The figures in
  `docs/RPO_RTO_OWNER_DECISION.md` §1 are staging-scale and the owner accepted
  them as such.
