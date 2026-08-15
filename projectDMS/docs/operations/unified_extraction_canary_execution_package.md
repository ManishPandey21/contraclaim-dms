# Unified extraction canary — production execution package (Task 7.6 Steps 1–5)

> ## STATUS: PREPARED — NOT AUTHORISED, NOT EXECUTED
>
> This package contains no executed production commands. It is written to be run
> **sequentially by an authorised operator inside an approved production change
> window**, without interpreting intent.
>
> - Prepared against commit `1da6276` (Task 7.6 Step 6 = PASS).
> - Companion document: [`unified_extraction_canary_and_rollback.md`](unified_extraction_canary_and_rollback.md)
>   (design rationale, gap register). **This file is the executable one.**
> - Every value in the evidence tables must be read **from the running production
>   system during the window**. A value copied from this repository, from a
>   default, or from a previous window is not evidence.
>
> **Two blockers stand between this package and authorisation. Both are listed in
> §11 and neither can be closed from a development machine.**

---

## Conventions used throughout

| Marker | Meaning |
|---|---|
| **[RO]** | Read-only. Cannot change production state. Safe to repeat. |
| **[MUT]** | Mutating. Changes production. Requires the window and the preceding gate to have passed. |
| **⛔ ABORT** | Stop immediately, do not proceed, execute §9 rollback if past Stage C, record NO-GO. |

Shell prefix assumed for every command block:

```bash
ssh contraclaim
cd /opt/contraclaim-dms/projectDMS
```

Compose alias used below — **the file set must be confirmed by C1.2 before use**:

```bash
DC="docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml"
```

> The base `docker-compose.yml` is deliberately excluded. Including it makes `up`
> fail on the missing dev-only `client/.env.development`; `config` and `ps` still
> succeed with the wrong set, so the mistake surfaces only at `up`.

---

# 1. Step-by-step execution matrix

Derived line by line from Task 7.6 Steps 1–5 in
`docs/superpowers/plans/2026-08-14-unified-page-extraction-phases-0-7.md`.
No acceptance criterion has been dropped or paraphrased away.

## Step 1 — Prove pre-canary safety  **[all RO except the backup itself]**

Specification text: *"Back up production `.env`, uploads, and Mongo using the
existing production runbook; record deployed commit, replica-set health, current
job counts by status/pipeline version, worker restart counts, readiness, and the
latest verified backup age. Confirm Task 0.4's RAR result: if it is not positive,
production keeps `RAR_UPLOAD_ENABLED=false` throughout the canary."*

| ID | Purpose | Command | RO/MUT | Expected | PASS | FAIL / ABORT | Evidence | Rollback | Depends on |
|---|---|---|---|---|---|---|---|---|---|
| **C1.1** | Deployed branch + commit — the runbook default is not trustworthy | `git branch --show-current; git rev-parse HEAD` | RO | A branch name and 40-char SHA | SHA == the authorised commit | Any other SHA ⛔ | Both strings | n/a | — |
| **C1.2** | Actual compose file set, from the container's own label | `docker inspect $($DC ps -q backend \| head -1) --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'` | RO | `docker-compose.prod.yml,docker-compose.mongo-replicaset.yml` | Exactly that set | Base compose present, or set differs ⛔ | Full label string | n/a | C1.1 |
| **C1.3** | Working tree is clean — running code must match the SHA | `git status --porcelain` | RO | Empty | Empty | Non-empty ⛔ | Output | n/a | C1.1 |
| **C1.4** | Replica-set health | `$DC exec -T backend python -c 'import os;from pymongo import MongoClient;print(MongoClient(os.environ["DATABASE_URL"]).admin.command("replSetGetStatus")["ok"])'` | RO | `1` | `1` | Anything else ⛔ | Output + member states | n/a | C1.2 |
| **C1.5** | Redis health | `$DC exec -T redis sh -lc 'redis-cli -a "$REDIS_PASSWORD" ping'` | RO | `PONG` | `PONG` | Otherwise ⛔ | Output | n/a | C1.2 |
| **C1.6** | Baseline job counts by pipeline version and status | `$DC exec -T backend python -m scripts.unified_extraction_canary_status` | RO | JSON report | `unified_v1` counts all **0**; `unrecorded`/`legacy_v0` = existing work | Any pre-existing `unified_v1` job ⛔ (means routing already active) | Whole JSON | n/a | C1.2 |
| **C1.7** | Worker restart counts — baseline for "no restart loop" later | `$DC ps --format '{{.Name}} {{.Status}}'; docker inspect $($DC ps -q document-worker) --format '{{.RestartCount}}'` | RO | Stable counts | Recorded | — | Per-service count | n/a | C1.2 |
| **C1.8** | Readiness | `$DC exec -T backend curl -fsS localhost:8000/health/ready` | RO | Ready payload | HTTP 200 | Non-200 ⛔ | Body | n/a | C1.2 |
| **C1.9** | Filesystem capacity | §2 | RO | §2 tables | §2 formula satisfied | Shortfall ⛔ **NO-GO** | §2 evidence block | n/a | — |
| **C1.10** | Migration/index preflight | §3 | RO | §3 outputs | §3 criteria | Any §3 failure ⛔ | §3 evidence block | n/a | C1.4 |
| **C1.11** | Archive/RAR configuration | §4 | RO | §4 outputs | ZIP admitted; RAR disabled | RAR enabled without Task 0.4 proof ⛔ | §4 evidence block | n/a | C1.2 |
| **C1.12** | **Backup** `.env`, uploads volume, Mongo | Per `docs/CONTRACLAIM_DOCKER_DEPLOYMENT_UPDATE_GUIDE.md` | **MUT** (writes backup only) | Backup artefacts | Backup completes **and** is verified restorable-listable; age recorded | Backup fails/unverifiable ⛔ **NO-GO** | Path, bytes, sha256, age | n/a | C1.9 (space must exist first) |
| **C1.13** | Post-backup free space re-check | `df -h /` and backup target | RO | Free space after backup | Still ≥ §2 required headroom | Below ⛔ | Figures | n/a | C1.12 |
| **C1.14** | Confirm Task 0.4 RAR result | Read `docs/architecture/phase0_extraction_measurements_2026-08-14.md` | RO | Recorded RAR finding | Result is **negative** → `RAR_UPLOAD_ENABLED=false` holds | Anyone proposing to enable RAR without positive EICAR-in-RAR proof ⛔ | Quoted finding | n/a | — |

**Step 1 gate:** every row PASS. Any ⛔ → **NO-GO, production unchanged, no deployment.**

## Step 2 — Deploy code with unified routing globally off

Specification text: *"Rebuild only affected backend/document-worker services.
Start with `UNIFIED_EXTRACTION_ENABLED=false` and an empty canary allowlist.
Verify readiness, route/auth controls, one document-worker owner, no duplicate
background document loop on the web tier, stable restart counts, and no change in
normal job throughput."*

| ID | Purpose | Command | RO/MUT | PASS | FAIL / ABORT | Evidence | Depends on |
|---|---|---|---|---|---|---|---|
| **C2.1** | Confirm `.env` has routing off before deploying | `grep -E 'UNIFIED_EXTRACTION_(ENABLED\|CANARY_ORG_IDS)\|DOCUMENT_WORKER_' .env` | RO | `ENABLED=false`, allowlist empty, canary replicas 0 | Otherwise ⛔ | Lines | Step 1 |
| **C2.2** | Fetch the authorised commit | `git fetch --all && git pull --ff-only origin <branch>` | **MUT** | HEAD == authorised SHA | Non-fast-forward ⛔ | SHA before/after | C2.1 |
| **C2.3** | Rebuild **only** affected services | `$DC build backend document-worker document-worker-canary` | **MUT** | Build succeeds | Failure ⛔ (nothing deployed yet) | Image IDs + digests | C2.2 |
| **C2.4** | Recreate only those services | `$DC up -d --no-deps backend document-worker document-worker-canary` | **MUT** | Containers healthy | Crash/restart loop ⛔ → §9 | `ps` output | C2.3 |
| **C2.5** | Readiness + route/auth controls | `scripts/post_deploy_verify.sh` | RO | 0 failures | Any FAIL ⛔ → §9 | Full output | C2.4 |
| **C2.6** | Exactly one document-worker owner | Covered by C2.5 (`START_DOCUMENT_EXTRACTION_WORKERS`) | RO | Only `document-worker` (+ canary at 0 replicas) | Web tier extracting ⛔ | Verify output | C2.5 |
| **C2.7** | No duplicate background document loop on the web tier | `$DC logs --since=10m backend \| grep -c 'document_pipeline.*claim'` | RO | `0` | Non-zero ⛔ | Count | C2.5 |
| **C2.8** | Exactly one scheduler owner | Covered by C2.5 (`RUN_SCHEDULER`) | RO | Exactly 1 | ≠1 ⛔ | Verify output | C2.5 |
| **C2.9** | Default worker restricted to `legacy_v0` | Covered by C2.5 | RO | `[legacy_v0]` | Empty/unrestricted ⛔ | Verify output | C2.5 |
| **C2.10** | Canary worker still at 0 replicas | Covered by C2.5 | RO | `replicas=0` | >0 ⛔ | Verify output | C2.5 |
| **C2.11** | Stable restart counts vs C1.7 | `docker inspect ... --format '{{.RestartCount}}'` | RO | Unchanged | Increasing ⛔ → §9 | Before/after | C2.4 |
| **C2.12** | No `unified_v1` jobs exist or are processed | `$DC exec -T backend python -m scripts.unified_extraction_canary_status` | RO | `unified_v1` all 0 | Any >0 ⛔ | JSON | C2.4 |
| **C2.13** | Normal (legacy) throughput unchanged | Compare queue depth/completion rate against C1.6 over ≥30 min | RO | Within baseline bounds | Material regression ⛔ → §9 | Both samples | C2.12 |
| **C2.14** | Legacy processing still works end to end | Upload one ordinary demo document; confirm `pipeline_version=legacy_v0` and `completed` | **MUT** (demo data) | Completes on legacy | Fails ⛔ → §9 | Job doc | C2.13 |

**Step 2 gate:** all PASS. Failure → §9 rollback, record NO-GO. Note the code is
deployed at this point but routing is globally off, so Stage B is a safe resting
state — this is the state production returns to on rollback.

## Step 3 — Enable one safe canary scope

Specification text and its five explicit assertions are reproduced verbatim in
§6 Stage C and §7. Every one is a hard acceptance requirement.

## Step 4 — Execute and validate rollback

Specification text is reproduced verbatim in §9, which is the executable form.

## Step 5 — Record the go/no-go evidence

Specification text: *"The operations document records exact commands/output,
deployed commit, canary document IDs (demo data only), job/page/intervention
counts, zero-call clean result, resource metrics, rollback result, and a go/no-go
decision. Global enablement is a separate authorised change and is blocked unless
both canary and rollback validations pass."*

| ID | Requirement | Where recorded | PASS criterion |
|---|---|---|---|
| C5.1 | Exact commands **and their output** | §5 evidence tables + attached log | No row left `<unfilled>` |
| C5.2 | Deployed commit | §5 table A | Matches authorised SHA |
| C5.3 | Canary document IDs — **demo data only** | §7 table | All belong to the approved demo org |
| C5.4 | Job / page / intervention counts | §5 table C | Before and after both present |
| C5.5 | **Zero-call clean result** | §7 fixture 1 | `page_extraction_interventions` count unchanged by the clean document |
| C5.6 | Resource metrics | §5 table D | CPU/mem/queue age/latency/error rate within Phase 0.5 bounds |
| C5.7 | Rollback result | §9 | All §9 rows PASS |
| C5.8 | Explicit go/no-go decision | §10 | Signed, dated, named |
| C5.9 | Global enablement remains separate | §10 footer | Recorded as **blocked** regardless of outcome |

---

# 2. Production capacity requirement (closes G11)

**Not measured yet — deliberately.** Step 1 activity belongs inside the window.
These are the exact read-only commands to run, and the formula that turns their
output into a decision. No percentage is invented here; every term is measured.

## 2.1 Measurement commands **[all RO]**

```bash
# M1 filesystem free/used, all relevant mounts
df -h / /var/lib/docker /opt/contraclaim-dms

# M2 Docker filesystem/image usage (images, containers, volumes, build cache)
docker system df -v

# M3 MongoDB data size, storage size, index size
$DC exec -T backend python -c '
import os, json
from pymongo import MongoClient
db = MongoClient(os.environ["DATABASE_URL"]).get_default_database()
s = db.command("dbStats")
print(json.dumps({k: s[k] for k in
  ("dataSize","storageSize","indexSize","fsUsedSize","fsTotalSize")}, indent=2))'

# M4 journal / oplog headroom
$DC exec -T backend python -c '
import os, json
from pymongo import MongoClient
c = MongoClient(os.environ["DATABASE_URL"])
o = c.local.command("collStats", "oplog.rs")
print(json.dumps({"oplog_size": o["maxSize"], "oplog_used": o["size"]}, indent=2))'
du -sh /var/lib/docker/volumes/*mongo*/_data/journal 2>/dev/null

# M5 extraction/OCR/raster temp location and current usage
$DC exec -T document-worker sh -lc 'echo "PROCESS_DIR=$PROCESS_DIR"; df -h /tmp /app/uploads; du -sh /tmp 2>/dev/null'

# M6 production backup size (latest verified)
ls -lh "${BACKUP_ROOT:-/var/backups/contractdms}" | tail -5
du -sh "${BACKUP_ROOT:-/var/backups/contractdms}"

# M7 free space AFTER the required backup is created — run post-C1.12
df -h / "${BACKUP_ROOT:-/var/backups/contractdms}"

# M8 rollback image/layer headroom: size of images that must coexist
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}} {{.ID}}' | grep -Ei 'backend|document-worker|client'
```

## 2.2 Required-headroom formula

Fill from measured values only. Every term has a named source.

| Term | Symbol | Source | Definition |
|---|---|---|---|
| Backup requirement | `B` | M6 | Size of one full backup set (Mongo dump + uploads + `.env`) |
| Deployment/image requirement | `D` | M8 | Sum of the **new** backend + document-worker image sizes; they land alongside the old ones |
| Extraction temp requirement | `E` | M5 | Peak temp usage per document × max concurrent documents. Per-document peak = largest canary fixture size × raster multiplier observed in Phase 0.5. If no multiplier is recorded, measure one on the largest fixture before Stage C and use that. |
| Database growth / journal | `G` | M3 + M4 | Projected page-evidence growth for the canary set + free journal/oplog space required to stay healthy |
| Rollback reserve | `R` | M8 | Size of the previous backend + document-worker images, which must remain resident so rollback never requires deleting the running image first |
| Safety margin | `S` | M1 + M3 | Derived, not invented: `S = max(observed 7-day growth in fsUsedSize, 0.5 × (B + D + E + G + R))`. The growth term uses real history; the fallback term ensures the margin scales with the operation's own footprint. |

```text
required_headroom = B + D + E + G + R + S
```

**Decision rule.**

```text
IF free_space_after_backup (M7)  >=  required_headroom   -> capacity PASS
ELSE                                                     -> NO-GO, Step 1 fails,
                                                            no deployment occurs
```

Record every symbol with its measured value in §5 table E. A term left blank is a
NO-GO — the formula cannot be evaluated on assumptions.

> **Explicitly out of scope for this package:** the development workstation this
> was prepared on reported 238G/238G with ~152M free. It is a Windows machine on
> Docker context `desktop-linux`, not the deployment host. It has no bearing on
> production capacity, which remains unmeasured until M1–M8 run in the window.

---

# 3. Migration / index preflight (`20260814_0001`)

**Do not run the migration.** `migrate_database` defaults to **dry-run**;
`--apply` is required to mutate. Every command below is therefore RO unless
marked otherwise.

```bash
# P1 current migration state - has 20260814_0001 ever run?
$DC exec -T backend python -c '
import os, json
from pymongo import MongoClient
db = MongoClient(os.environ["DATABASE_URL"]).get_default_database()
rows = list(db["schema_migrations"].find({}, {"_id":0}))
print(json.dumps(rows, indent=2, default=str))'

# P2 migration plan without executing upgrade code
$DC exec -T backend python -m rbac_backend.scripts.migrate_database --list

# P3 dry run (NO --apply => does not mutate), warnings are failures
$DC exec -T backend python -m rbac_backend.scripts.migrate_database --fail-on-warning

# P4 indexes BEFORE
$DC exec -T backend python -c '
import os, json
from pymongo import MongoClient
db = MongoClient(os.environ["DATABASE_URL"]).get_default_database()
for c in ("document_ocr_pages","document_ocr_batches","document_extraction_heads"):
    print(c, json.dumps(list(db[c].list_indexes()), indent=2, default=str))'

# P5 duplicates that would BLOCK the unique index creation
$DC exec -T backend python -c '
import os, json
from pymongo import MongoClient
db = MongoClient(os.environ["DATABASE_URL"]).get_default_database()
dupes = list(db["document_ocr_pages"].aggregate([
  {"$group": {"_id": {"d":"$document_id","r":"$extraction_run_id","p":"$page_number"},
              "n": {"$sum": 1}}},
  {"$match": {"n": {"$gt": 1}}},
  {"$limit": 20}]))
print("BLOCKING DUPLICATES:", len(dupes)); print(json.dumps(dupes, indent=2, default=str))

heads = list(db["document_extraction_heads"].aggregate([
  {"$group": {"_id": "$document_id", "n": {"$sum": 1}}},
  {"$match": {"n": {"$gt": 1}}}, {"$limit": 20}]))
print("BLOCKING HEAD DUPLICATES:", len(heads))'
```

| ID | Establishes | PASS | FAIL / ABORT |
|---|---|---|---|
| P1 | Whether `20260814_0001` previously ran | Either absent (will apply) or recorded applied with matching name | Recorded applied but indexes missing (P4) → inconsistent ledger ⛔ |
| P2 | The plan | `20260814_0001` listed | Absent ⛔ |
| P3 | Safe inspection | Exit 0, no warnings | Non-zero or any warning ⛔ |
| P4 | Indexes before | Recorded | — |
| P5 | Blocking rows | **0 duplicates and 0 head duplicates** | Any >0 ⛔ — unique index creation would fail |

**Expected indexes AFTER `--apply` (verify in-window, C2.5 also checks these):**

| Collection | Key | Unique |
|---|---|---|
| `document_ocr_pages` | `(document_id, extraction_run_id, page_number)` | **yes** |
| `document_ocr_pages` | `(document_id, extraction_run_id, status)` | no |
| `document_ocr_pages` | `(organization_id, project_id)` | no |
| `document_ocr_batches` | `(document_id, extraction_run_id)` | no |
| `document_extraction_heads` | `document_id` | **yes** |

Exact unique verification:

```bash
$DC exec -T backend python -c '
import os
from pymongo import MongoClient
db = MongoClient(os.environ["DATABASE_URL"]).get_default_database()
want = (("document_id",1),("extraction_run_id",1),("page_number",1))
hit = [i for i in db["document_ocr_pages"].list_indexes() if tuple(i["key"].items())==want]
assert hit and hit[0].get("unique"), "UNIQUE PAGE INDEX MISSING"
print("unique page index OK:", hit[0]["name"])'
```

**If the migration fails when applied [MUT recovery]:**

1. Do **not** retry blindly. Capture the exact error.
2. `create_index` is idempotent and additive — a partial failure leaves existing
   indexes intact and creates no data change. There is no data to un-migrate.
3. If failure was a duplicate-key error, the blocking rows are real data: stop,
   record NO-GO, and resolve duplicates as a separate reviewed change.
4. If the ledger recorded the version but indexes are absent, correct the ledger
   only under explicit authorisation; do not silently re-run.
5. Production remains on `legacy_v0` throughout, which needs none of these
   indexes — so a failed migration is a NO-GO, not an incident.

---

# 4. Archive configuration verification **[all RO]**

```bash
# A1 effective container environment (NOT repository defaults)
docker inspect $($DC ps -q document-worker) \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E 'ALLOWED_DOCUMENT_MIMES|ALLOWED_ENCLOSURE_MIMES|RAR_UPLOAD_ENABLED'

# A2 same for the request tier that validates uploads
docker inspect $($DC ps -q backend) \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E 'ALLOWED_DOCUMENT_MIMES|ALLOWED_ENCLOSURE_MIMES|RAR_UPLOAD_ENABLED'

# A3 actual policy the RUNNING application serves
$DC exec -T backend curl -fsS localhost:8000/api/upload-policy
```

| ID | Check | PASS | FAIL / ABORT |
|---|---|---|---|
| A1/A2 | `application/zip` present in effective `ALLOWED_DOCUMENT_MIMES` **and** `ALLOWED_ENCLOSURE_MIMES` | Present in both tiers | Absent → archive upload returns 415; record and either fix under authorisation or drop archive fixtures from the canary (G7) |
| A1/A2 | `RAR_UPLOAD_ENABLED` | `false` | `true` ⛔ unless a positive EICAR-in-RAR ClamAV result exists in `docs/architecture/phase0_extraction_measurements_2026-08-14.md`. It currently does not. |
| A3 | Served policy matches effective env | Match | Divergence ⛔ — the app is not honouring its own configuration |
| — | Env var vs code default | An explicit env value **overrides the code default entirely** | If set, `application/zip` must be listed explicitly or it is excluded |

**RAR stays disabled. This package does not enable it and no step below turns it on.**

---

# 5. Evidence capture template

Fill **before** and **after**. `<unfilled>` anywhere = Step 5 incomplete = NO-GO.

### Table A — window identity

| Field | Before | After | Source |
|---|---|---|---|
| Timestamp (UTC) | `<unfilled>` | `<unfilled>` | `date -u` |
| Operator (name) | `<unfilled>` | `<unfilled>` | — |
| Change authoriser | `<unfilled>` | `<unfilled>` | — |
| Production hostname | `<unfilled>` | `<unfilled>` | `hostname -f` |
| Branch | `<unfilled>` | `<unfilled>` | C1.1 |
| Commit SHA | `<unfilled>` | `<unfilled>` | C1.1 |
| Compose file set | `<unfilled>` | `<unfilled>` | C1.2 (container label) |

### Table B — topology

| Field | Before | After | Source |
|---|---|---|---|
| backend image ID / digest | `<unfilled>` | `<unfilled>` | `docker inspect` |
| document-worker image ID / digest | `<unfilled>` | `<unfilled>` | `docker inspect` |
| document-worker-canary image ID / digest | `<unfilled>` | `<unfilled>` | `docker inspect` |
| document-worker replicas | `<unfilled>` | `<unfilled>` | `$DC ps` |
| document-worker-canary replicas | `<unfilled>` | `<unfilled>` | `$DC ps` |
| Scheduler owner (must be exactly 1) | `<unfilled>` | `<unfilled>` | `RUN_SCHEDULER` per container |
| document-worker `DOCUMENT_WORKER_PIPELINE_VERSIONS` | `<unfilled>` | `<unfilled>` | container env |
| canary `DOCUMENT_WORKER_PIPELINE_VERSIONS` | `<unfilled>` | `<unfilled>` | container env |
| `UNIFIED_EXTRACTION_ENABLED` | `<unfilled>` | `<unfilled>` | container env |
| `UNIFIED_EXTRACTION_CANARY_ORG_IDS` (exact) | `<unfilled>` | `<unfilled>` | container env |
| Restart counts per service | `<unfilled>` | `<unfilled>` | C1.7 |

### Table C — data state

| Field | Before | After | Source |
|---|---|---|---|
| Migration version(s) applied | `<unfilled>` | `<unfilled>` | P1 |
| Unique page index present | `<unfilled>` | `<unfilled>` | §3 |
| Unique head index present | `<unfilled>` | `<unfilled>` | §3 |
| Jobs by `pipeline_version` × status | `<unfilled>` | `<unfilled>` | C1.6 |
| `document_ocr_pages` count | `<unfilled>` | `<unfilled>` | C1.6 |
| `document_extraction_heads` count | `<unfilled>` | `<unfilled>` | C1.6 |
| `page_extraction_interventions` count | `<unfilled>` | `<unfilled>` | C1.6 |
| Mongo health (`replSetGetStatus.ok`) | `<unfilled>` | `<unfilled>` | C1.4 |
| Redis health | `<unfilled>` | `<unfilled>` | C1.5 |
| Worker health / readiness | `<unfilled>` | `<unfilled>` | C1.8 |

### Table D — resources (Phase 0.5 bounds)

| Metric | Baseline | During canary | Bound | PASS? |
|---|---|---|---|---|
| CPU % (worker) | `<unfilled>` | `<unfilled>` | Phase 0.5 | `<unfilled>` |
| Memory (worker) | `<unfilled>` | `<unfilled>` | Phase 0.5 | `<unfilled>` |
| Queue age (max) | `<unfilled>` | `<unfilled>` | Phase 0.5 | `<unfilled>` |
| Request latency p95 | `<unfilled>` | `<unfilled>` | Phase 0.5 | `<unfilled>` |
| Error rate | `<unfilled>` | `<unfilled>` | Phase 0.5 | `<unfilled>` |
| Model calls | `<unfilled>` | `<unfilled>` | **0 for clean fixture** | `<unfilled>` |
| Cost | `<unfilled>` | `<unfilled>` | **0 for clean fixture** | `<unfilled>` |

### Table E — capacity (§2)

| Symbol | Meaning | Measured | Source |
|---|---|---|---|
| `B` | Backup requirement | `<unfilled>` | M6 |
| `D` | New image requirement | `<unfilled>` | M8 |
| `E` | Extraction temp requirement | `<unfilled>` | M5 |
| `G` | DB growth + journal | `<unfilled>` | M3/M4 |
| `R` | Rollback reserve | `<unfilled>` | M8 |
| `S` | Derived safety margin | `<unfilled>` | formula §2.2 |
| **`required_headroom`** | Sum | `<unfilled>` | — |
| **`free_after_backup`** | Actual | `<unfilled>` | M7 |
| **Verdict** | `free ≥ required` | `<unfilled>` | — |

---

# 6. The canary sequence

## Stage A — pre-change gate  **[RO]**

Run every Step 1 check (C1.1–C1.14), §2, §3, §4.

```text
IF any critical check FAILS:
    STOP
    -> no deployment
    -> record NO-GO in §10
    -> production remains unchanged
```

Nothing in Stage A modifies production except the backup (C1.12), which only
adds files.

## Stage B — deploy with routing OFF  **[MUT]**

Execute C2.1 → C2.14 in order. Required end state:

- services ready (C2.5);
- exactly one extraction owner, canary at 0 replicas (C2.6, C2.10);
- default worker claims **only** `legacy_v0` (C2.9);
- exactly one scheduler owner (C2.8);
- **zero** `unified_v1` jobs created or processed (C2.12);
- legacy processing demonstrably still works (C2.14);
- restart counts stable (C2.11).

Stage B is a **safe resting state**. If anything later fails, this is where
production returns.

## Stage C — enable one-organisation canary  **[MUT]**

Only after Stage B fully passes.

```bash
# S3.1 select the ONE approved low-risk demo organisation
#      (id recorded in §10 by the change authoriser, not chosen here)
ORG="<approved-demo-org-id>"

# S3.2 set the exact allowlist and canary replicas in .env
#      Leave UNIFIED_EXTRACTION_ENABLED=false. Leave the default worker on legacy_v0.
#   UNIFIED_EXTRACTION_CANARY_ORG_IDS=$ORG
#   DOCUMENT_WORKER_CANARY_REPLICAS=1
#   DOCUMENT_WORKER_PIPELINE_VERSIONS=legacy_v0

# S3.3 recreate only the two workers
$DC up -d --no-deps document-worker document-worker-canary

# S3.4 confirm topology from the running containers
scripts/post_deploy_verify.sh
```

**Hard acceptance requirement — prove from actual Mongo job data:**

```bash
$DC exec -T backend python -c '
import os, json
from pymongo import MongoClient
db = MongoClient(os.environ["DATABASE_URL"]).get_default_database()
ORG = os.environ["CANARY_ORG"]
print(json.dumps({
  "canary_org_unified": db.document_processing_jobs.count_documents(
      {"organization_id": ORG, "pipeline_version": "unified_v1"}),
  "canary_org_legacy": db.document_processing_jobs.count_documents(
      {"organization_id": ORG, "pipeline_version": "legacy_v0"}),
  "NON_canary_unified_MUST_BE_0": db.document_processing_jobs.count_documents(
      {"organization_id": {"$ne": ORG}, "pipeline_version": "unified_v1"}),
}, indent=2))'
```

```text
canary organisation  -> unified_v1
all other orgs       -> legacy_v0
NON_canary_unified_MUST_BE_0 == 0
```

Any non-zero third value = **immediate ABORT → §9**.

---

# 7. Canary test document set

All fixtures must belong to the approved demo organisation. **Demo data only —
no real tenant document may be used.**

| # | Fixture | Purpose | Expected `pipeline_version` | Expected claim worker | Expected terminal state |
|---|---|---|---|---|---|
| **F1** | Clean text-native PDF | Zero-cost happy path | `unified_v1` | `document-worker-canary` | `completed` |
| **F2** | Scanned / OCR-required PDF | OCR path under page-wise extraction | `unified_v1` | canary | `completed` |
| **F3** | Mixed-content PDF (text + scanned pages) | Checkpoint / reclaim behaviour | `unified_v1` | canary | `completed` or `partially_processed` → expected review state |
| **F4** | Multi-page PDF (≥ 10 pages) | Page persistence + head publication | `unified_v1` | canary | `completed` |
| **F5** | Non-canary org document (control) | Proves isolation | `legacy_v0` | `document-worker` | `completed` |
| **F6** | Deliberately paused mid-run canary job (Step 4 input) | Rollback survival evidence | `unified_v1` | canary | in-flight at rollback |

### Evidence required per fixture

| Field | Query source |
|---|---|
| Job `pipeline_version` | `document_processing_jobs.pipeline_version` |
| Claiming worker | **Not recorded in Mongo (G14).** Derive from per-container logs (§8 supplementary) and corroborate with page-evidence presence |
| Extraction status | `document_processing_jobs.status` |
| Pages recorded | `document_ocr_pages` count for `(document_id, extraction_run_id)` |
| Extraction run id | **Derived, not stored: `extraction_run_id == str(job._id)`** (`document_service.py:1546`). Stable across retries by construction, so a resumed attempt upserts into the same run instead of orphaning earlier pages. Verify page rows reference this exact value. |
| Page evidence rows | `document_ocr_pages` documents |
| Head / current run identity | `document_extraction_heads.{extraction_run_id, expected_page_numbers, published_at}` |
| Attempts | `document_processing_jobs.attempts` |
| Gate verdicts | page `status` + `needs_review`; verdicts `pass` / `not_checkable` / `fail` / conflicting |
| Interventions | `page_extraction_interventions` rows for the document |
| Processing state | `queued`/`processing`/`completed`/`stored_only`/`partially_processed`/`human_review_required`/`failed` |
| Downstream persisted document | `documents` row: `file_path`, extracted text, metadata |
| Error / retry state | job `error`, `attempts`, `max_attempts` |

### F1 acceptance (the headline number)

```text
F1 clean document MUST produce:
  - status = completed
  - all page verdicts PASS (no needs_review)
  - exactly one published head, expected_page_numbers complete
  - page_extraction_interventions count UNCHANGED  (zero model calls, zero cost)
```

### ⚠ What a passing canary does **not** prove

`EXTRACTION_FALLBACK_ENABLED=false` in production, so the LLM/Vision fallback
ladder is **inert** during this window. Therefore:

- **The complete fallback ladder is NOT validated by this canary.** Only the
  deterministic path runs.
- Reconstruction/model adapters are absent in production (**G5**); ledger
  cost/token fields remain untested against a real provider.
- Real scanned-raster escalation is not exercised end to end (**G6**); F2 tests
  OCR, not raster escalation into the ladder.
- A deterministic PASS on F1–F4 must never be reported as "the extraction
  fallback works in production". It is not evidence of that.

Any go/no-go write-up that claims ladder validation from this window is
**factually wrong** and must be corrected before sign-off.

---

# 8. Contamination checks  **[RO — run after every canary operation]**

> **Method note — read before interpreting results.** `document_processing_jobs`
> records **no worker identity**: the claim writes only `status`, `stage`,
> `started_at`, `heartbeat_at`, `updated_at` and `attempts` (see G14). "Which
> container claimed this job" is therefore **not answerable from Mongo**.
>
> These queries instead detect which **code path executed**, which is the thing
> that actually matters and is strictly stronger evidence than container
> attribution:
>
> - the unified path writes `document_ocr_pages` rows and publishes a head;
> - the legacy path writes **neither**.
>
> So a `legacy_v0` job carrying page evidence ran unified code, and a terminal
> `unified_v1` job with no page evidence ran legacy code. Both are cross-version
> execution regardless of which container did it.

```bash
$DC exec -T backend python -c '
import os, json
from pymongo import MongoClient
db = MongoClient(os.environ["DATABASE_URL"]).get_default_database()
J = db.document_processing_jobs; P = db.document_ocr_pages; ORG = os.environ["CANARY_ORG"]
out = {}

def has_pages(doc_id):
    return P.count_documents({"document_id": doc_id}, limit=1) > 0

# 1 non-canary organisation holding unified work
out["non_canary_unified"] = J.count_documents(
    {"organization_id": {"$ne": ORG}, "pipeline_version": "unified_v1"})

# 2 legacy-versioned job that produced page evidence => unified code ran on it
out["legacy_job_ran_unified_code"] = sum(
    1 for j in J.find({"pipeline_version": {"$in": ["legacy_v0", None]}},
                      {"document_id": 1})
    if has_pages(j["document_id"]))

# 3 terminal unified job with NO page evidence => legacy code ran on it
out["unified_job_ran_legacy_code"] = sum(
    1 for j in J.find({"pipeline_version": "unified_v1",
                       "status": {"$in": ["completed","partially_processed",
                                          "human_review_required"]}},
                      {"document_id": 1})
    if not has_pages(j["document_id"]))

# 4 unknown/missing version that produced page evidence => wrong path taken
out["unknown_version_ran_unified_code"] = sum(
    1 for j in J.find({"pipeline_version": {"$nin": ["legacy_v0","unified_v1", None]}},
                      {"document_id": 1})
    if has_pages(j["document_id"]))

# 5 page evidence belonging to a NON-canary organisation
out["non_canary_page_evidence"] = P.count_documents({"organization_id": {"$ne": ORG}})

# 6 duplicate page evidence
out["duplicate_page_evidence"] = len(list(P.aggregate([
  {"$group": {"_id": {"d":"$document_id","r":"$extraction_run_id","p":"$page_number"},
              "n":{"$sum":1}}},
  {"$match": {"n": {"$gt": 1}}}, {"$limit": 50}])))

# 7 duplicate extraction-run heads per document
out["duplicate_heads"] = len(list(db.document_extraction_heads.aggregate([
  {"$group": {"_id":"$document_id","n":{"$sum":1}}},
  {"$match": {"n":{"$gt":1}}}, {"$limit": 50}])))

# 8 jobs stranded by worker restrictions (queued, nobody permitted to claim)
out["stranded_unified_queued"] = J.count_documents(
    {"pipeline_version": "unified_v1", "status": {"$in": ["queued","retrying"]}})

# 9 intervention rows outside the canary organisation
out["non_canary_interventions"] = db.page_extraction_interventions.count_documents(
    {"organization_id": {"$ne": ORG}})

print(json.dumps(out, indent=2))'
```

| Signal | Tolerance | Meaning if violated |
|---|---|---|
| `non_canary_unified` | **0** | Allowlist leaked; another tenant was routed to unified |
| `legacy_job_ran_unified_code` | **0** | Cross-version execution: canary worker took legacy work |
| `unified_job_ran_legacy_code` | **0** | Cross-version execution: legacy worker took canary work |
| `unknown_version_ran_unified_code` | **0** | Fail-closed default broke; unknown version ran new code |
| `non_canary_page_evidence` | **0** | Another tenant acquired unified page rows |
| `duplicate_page_evidence` | **0** | Unique index absent or two workers wrote the same page |
| `duplicate_heads` | **0** | More than one visible run per document |
| `stranded_unified_queued` | 0 while canary runs; may be >0 mid-rollback — see §9 step 3 | Jobs nothing is permitted to claim |
| `non_canary_interventions` | **0** | Another tenant entered the fallback ladder |

**Supplementary worker attribution (log-based, weaker).** Because Mongo cannot
answer it, correlate per-container logs by document name and timestamp:

```bash
$DC logs --since=60m document-worker        | grep 'document_pipeline' > /tmp/w_default.log
$DC logs --since=60m document-worker-canary | grep 'document_pipeline' > /tmp/w_canary.log
# A canary fixture must appear ONLY in w_canary.log; F5 only in w_default.log.
grep -c 'Legacy extraction path' /tmp/w_canary.log   # expect 0
```

`Legacy extraction path` appearing in the **canary** log = the canary ran legacy
code = ABORT. This is corroborating evidence; the Mongo signals above are
authoritative.

**Any cross-tenant or cross-version contamination = immediate ABORT / NO-GO.**
Not a warning, not a note. Execute §9 and stop.

**Any cross-tenant or cross-version contamination = immediate ABORT / NO-GO.**
Not a warning, not a note. Execute §9 and stop.

---

# 9. Rollback drill (Step 4)

Order matters. It preserves the persisted-version invariant: the allowlist is
cleared *before* the canary worker stops, so no new `unified_v1` job is created
that nothing can claim.

> **Do not rewrite queued `pipeline_version` values as part of rollback.**
> The persisted decision is the safety property. Rewriting it would reclassify
> work in flight — exactly what this design exists to prevent.

| # | Action | Command | RO/MUT | PASS criterion |
|---|---|---|---|---|
| **1** | Clear canary allowlist → no new `unified_v1` jobs | set `UNIFIED_EXTRACTION_CANARY_ORG_IDS=` in `.env`, then `$DC up -d --no-deps backend document-worker` | MUT | New demo job created after this is `legacy_v0` |
| **2** | Observe/record already-persisted `unified_v1` jobs | contamination query §8 + list in-flight jobs with `extraction_run_id`, `attempts`, remaining pages | **RO** | Full inventory captured **before** anything stops |
| **3** | Apply documented policy to in-flight jobs | Decide per job: **(a)** let it finish on the canary before scaling down, **(b)** leave it queued and requeue after the window, or **(c)** mark review-required. Record the choice and reason per job. | MUT | Every in-flight job has an explicit recorded decision. No job silently abandoned. |
| **4** | Scale canary to 0 | set `DOCUMENT_WORKER_CANARY_REPLICAS=0`, `$DC up -d --no-deps document-worker-canary` | MUT | `$DC ps` shows 0 canary containers; graceful stop, no kill |
| **5** | Verify new jobs are `legacy_v0` | enqueue one demo document, read its `pipeline_version` | MUT (demo) | `legacy_v0`, claimed by `document-worker`, reaches `completed` |
| **6** | Verify identities intact | compare each recorded job's `attempts`, `remaining_page_numbers`, `processing_state`, and its head's `extraction_run_id`/`expected_page_numbers`/`published_at` against step 2. **`extraction_run_id` is `str(job._id)`**, so it cannot drift unless the job itself was recreated — verify page rows still carry that exact run id. | **RO** | **Byte-identical** to step 2 for every field; page rows still reference `str(job._id)` |
| **7** | Verify nothing deleted or reclassified | compare `document_ocr_pages`, `document_extraction_heads`, `page_extraction_interventions` counts against Table C *before* | **RO** | Counts **≥** pre-canary; no pre-canary or canary page evidence removed; no `pipeline_version` value changed on any existing job |

**Additional Step 4 requirements from the specification:**

| Requirement | Verification |
|---|---|
| Readiness after rollback | `scripts/post_deploy_verify.sh` → 0 failures |
| Legacy job completion | step 5 above |
| No restart loop | restart counts stable vs Table B |
| No loss of pre-canary **or** canary page evidence | step 7 above |

Rollback ends with production in the **Stage B resting state**: new code
deployed, routing globally off, canary at 0 replicas, all evidence intact.

> If a deeper rollback is ever required (code, not routing), it is a separate
> authorised change: `git checkout` the prior SHA, rebuild, `up -d --no-deps`.
> The `R` term in §2.2 exists so the prior images are still resident for it.

---

# 10. GO / NO-GO decision table

| # | Condition | Gate | Verdict if violated |
|---|---|---|---|
| 1 | Insufficient production disk/headroom (§2) | Stage A | **NO-GO** |
| 2 | Backup unavailable or unverifiable | Stage A | **NO-GO** |
| 3 | Migration/index preflight failure (§3, incl. blocking duplicates) | Stage A | **NO-GO** |
| 4 | Wrong commit or branch | Stage A | **NO-GO** |
| 5 | Unexpected compose topology (file set / services) | Stage A | **NO-GO** |
| 6 | Unrestricted extraction worker | Stage A/B/C | **NO-GO / ABORT** |
| 7 | More than one scheduler owner | Stage A/B/C | **NO-GO / ABORT** |
| 8 | Non-canary tenant receives `unified_v1` | Stage C | **ABORT → §9** |
| 9 | Canary worker claims legacy work | Stage C | **ABORT → §9** |
| 10 | Duplicate page evidence | Stage C | **ABORT → §9** |
| 11 | Evidence/run identity changes during rollback | §9 | **NO-GO** (and incident) |
| 12 | Unexplained processing failures | any | **ABORT → §9** |
| 13 | Required health check failure (Mongo/Redis/readiness) | any | **NO-GO / ABORT** |
| 14 | Working tree dirty on the production checkout | Stage A | **NO-GO** |
| 15 | RAR enabled without positive EICAR-in-RAR proof | Stage A | **NO-GO** |
| 16 | Any Step 5 evidence field left `<unfilled>` | Step 5 | **NO-GO** |
| 17 | Restart loop on any worker | Stage B/C | **ABORT → §9** |
| 18 | Resource metrics outside Phase 0.5 bounds | Stage C | **ABORT → §9** |
| 19 | F1 clean fixture produced any intervention row | Stage C | **ABORT → §9** (zero-cost claim is false) |

**No hard gate may be downgraded to a warning in order to continue the rollout.**
A gate that is inconvenient is still a gate. Changing any row above requires the
change authoriser's written acceptance recorded in this document, before the
window, not during it.

### Decision record

| Field | Value |
|---|---|
| Canary organisation id (approved) | `<unfilled>` |
| Stage A verdict | `<unfilled>` |
| Stage B verdict | `<unfilled>` |
| Stage C verdict | `<unfilled>` |
| Rollback drill verdict | `<unfilled>` |
| **Final GO / NO-GO** | `<unfilled>` |
| Decided by | `<unfilled>` |
| Date (UTC) | `<unfilled>` |

**Global enablement (`UNIFIED_EXTRACTION_ENABLED=true`) remains a separate
authorised change and is blocked regardless of this window's outcome.**

---

# 11. Gap register — window impact

| Gap | Current status | Blocks starting window? | Blocks enabling canary? | Blocks final GO? | Required evidence / acceptance |
|---|---|---|---|---|---|
| **G1a** temporary legacy path | Open by design. `_extract_legacy` is duplicate surface; equivalence-tested against `ffa844b`. | No | No | No | Delete `_extract_legacy`, `legacy_v0` and the claim restriction after global acceptance. Tracked, not a blocker. |
| **G2** Mongo-backed E2E never exercised | Open. This window is the **first** real-infrastructure run. | No | No | **Yes** | F1–F4 complete against production Mongo with §8 clean. Elevated risk accepted in writing before Stage C. |
| **G3** production migration unverified | Open. Never run against a real DB. | **Yes** | **Yes** | **Yes** | §3 P1–P5 pass, then `--apply` succeeds and indexes verified. |
| **G4** unique page index | Open, created only by G3. | **Yes** | **Yes** | **Yes** | Unique-index verification in §3 returns OK. **Hard prerequisite for running two workers** — without it concurrent reclaims can write contradictory page evidence while `publish_run`'s count check still passes. |
| **G5** no real reconstruction/model adapters | Open. `EXTRACTION_FALLBACK_ENABLED=false`; ladder inert. | No | No | No — **but** it bounds the claim | The canary **cannot** validate the ladder. Sign-off must state this explicitly (§7). Not closable in this window. |
| **G6** scanned-raster escalation not E2E | Open. Unit-tested only. | No | No | No — bounds the claim | Same as G5: record as unvalidated. |
| **G7** archive MIME config unverified | Open. Explicit env overrides the code default entirely. | No | Only if archive fixtures are used | No | §4 A1–A3. If `application/zip` is absent, either fix under authorisation or drop archive fixtures and record why. |
| **G8** RAR unverified/disabled | Open. ZIP recursion proven in Phase 0; RAR never functionally proven. | No | No | No | `RAR_UPLOAD_ENABLED=false` throughout, verified by §4. Enabling requires EICAR-in-RAR evidence in a separate reviewed change. |
| **G9** `contract_ocr_pages` / `document_processing_jobs` unindexed | Open, pre-existing. | No | No | No | Out of scope. Monitor queue-age metrics (Table D). |
| **G10** intake sniffs `filetype` without filename context | Open, unchanged. | No | No | No | Durable job carries the validated MIME. No action this window. |
| **G11** production disk capacity never measured | **Open — blocking.** No figure exists anywhere. | **Yes** | **Yes** | **Yes** | §2 M1–M8 measured, formula evaluated, `free_after_backup ≥ required_headroom`, authoriser accepts the derived threshold. |
| **G12** `post_deploy_verify.sh` never run live | Open. Statically verified only (parses, read-only, 15 checks present). | No | No | **Yes** | C2.5 executes it against the running stack; output pasted into §5. A check that misreads a live container surfaces only here. |
| **G13** rollback never drilled | Open. Implemented and unit-tested; no process ever stopped/scaled. | No | No | **Yes** | §9 executed in full with all seven rows PASS. |
| **G14** no worker identity on jobs | **Newly discovered.** `_claim_next_processing_job` records `status`, `stage`, `started_at`, `heartbeat_at`, `updated_at`, `attempts` — and no claiming-worker field. Step 3's "which worker claimed it" cannot be answered from Mongo. | No | No | No — **but** it weakens the evidence | §8 substitutes code-path detection (page-evidence presence), which is strictly stronger than container attribution, plus log correlation. **Recommended pre-window change:** add a `claimed_by` field to the claim update so attribution is first-class. Small, additive, and it would make Step 3's assertion directly queryable. Not done here: it changes the claim path, which is outside this preparation task's scope. |

**No gap in this table is closed by a unit test.** G3, G4, G11, G12 and G13 all
require production execution inside the authorised window.

---

# 12. Pre-window checklist (operator tear-off)

```text
[ ] Change window approved, start/end times recorded
[ ] Change authoriser named in §5 Table A
[ ] Approved canary organisation id recorded in §10 (demo data only)
[ ] Authorised commit SHA recorded and matches 1da6276 or later approved SHA
[ ] Phase 0.5 resource bounds to hand (Table D)
[ ] Task 0.4 RAR result read; confirmed negative; RAR stays false
[ ] Rollback authority confirmed: operator may execute §9 without further approval
[ ] G11 threshold formula understood; operator can evaluate it in-window
[ ] This document open and writable for evidence capture
```

Execution order: **§6 Stage A → §2 → §3 → §4 → §5 (before) → §6 Stage B →
§6 Stage C → §7 → §8 → §9 → §5 (after) → §10 decision.**
