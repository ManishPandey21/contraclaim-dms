# Unified extraction canary — production execution package (Task 7.6 Steps 1–5)

> # ⛔ RELEASE FREEZE STOPPED — DO NOT REQUEST AUTHORISATION
>
> An adversarial review of the Phase 6 quality gate, **independently confirmed by
> direct probe**, found defects that invalidate the premise this canary would be
> authorised on. The deterministic gate does not do the thing being certified.
>
> | | Confirmed defect | Verified by |
> |---|---|---|
> | **G18** | ~~Unrecognised corruption reports `pass`~~ — **FIXED** at `5ed1f0b` | 17 regression tests |
> | **G20** | ~~Repairs computed then discarded~~ — **FIXED** at `a0c47a8` | 7 tests via the production path |
> | **G21** | Gate verdicts are **never persisted** — `record_pages` runs at `engine.py:142`, before the gate. The canary's own abort criteria are unmeasurable. | call-site trace |
> | **G22** | Hard gate (a) is **9 `not_checkable` + 3 `pass`**, and `subtotal_identity` runs in **0 of 12** cases — the tests build single-row tables. | executed all 12 cases |
> | **G23** | Escalated content is **embedded into Qdrant before** the review flag is written (`document_processor.py:683` → `:694`, then `document_service.py:1556`). | ordering trace |
>
> Together: corrupted values can pass (G18), a correct repair never reaches the
> index (G20), none of it is observable (G21), the gate that certifies it was
> never exercised on multi-row tables (G22), and flagged text is already
> retrievable before anyone is told (G23).
>
> **A previous revision of this document claimed G18 "fails closed". That claim
> was wrong, was mine, and is retracted** — it was tested only against a
> single-row table. §11.0 and the G18 row now carry the correction.
>
> No production command has been run. The candidate is **not** a release
> candidate until these are fixed and re-verified. Everything below remains
> structurally valid as a *procedure*, but must not be executed.
>
> ---
>
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

### Table D — resources

> **Resolved: there are no recorded acceptance thresholds.** Task 7.6 Step 3
> requires metrics to "remain within the recorded acceptance bounds". The
> repository was searched exhaustively (Task 0.5 spec, the Phase 0 measurements
> document, git history, all architecture/findings docs). **No acceptance
> threshold exists anywhere.** What exists is an *idle* baseline, recorded for a
> different purpose. Classification below; sources are exact.

| Metric | Value | Classification | Source |
|---|---|---|---|
| backend CPU | 0.47% | **MEASURED BASELINE (idle)** | `phase0_extraction_measurements_2026-08-14.md` §4 |
| backend memory | 505.4 MiB | **MEASURED BASELINE (idle)** | ibid §4 |
| contract-worker CPU | 0.14% | **MEASURED BASELINE (idle)** | ibid §4 |
| contract-worker memory | 208.1 MiB | **MEASURED BASELINE (idle)** | ibid §4 |
| Host capacity | 8 cores, 23 GB RAM, 16.9 GB available | **MEASURED BASELINE** | ibid §4 |
| clamav memory | 950.2 MiB / 2 GiB (46%) | **OBSERVATIONAL VALUE ONLY** — flagged as the tightest resource in the stack | ibid §4 |
| **document-worker CPU/memory** | — | **MISSING** — the service does not appear in the baseline at all | ibid §4 |
| OCR wall time | — | **MISSING** — "recent document_pipeline OCR timings: 0" | ibid §4 |
| OCR pages/document | — | **MISSING** — page counts not persisted before Phase 3 | ibid §3 caveat |
| Queue age | — | **MISSING** — never measured | — |
| Request latency p95 | — | **MISSING** — never measured | — |
| Error rate | — | **MISSING** — never measured | — |
| Cost bound | — | **MISSING** — never measured | — |
| **Any acceptance threshold** | — | **MISSING** | The plan never converts the baseline into a threshold |

**Why the idle baseline cannot be promoted to a threshold.** The Phase 0 document
states it directly: a `docker logs` search over 720 hours returned **zero**
`document_pipeline` lines, so *"there is no production load baseline to compare
against, and none can be harvested from history."* An idle-state figure is not an
upper bound for a working extraction pipeline; treating 0.47% CPU as a limit
would fail the canary the moment it did any work. This package therefore does
**not** convert it, and no number here is invented.

**Minimum decision the authoriser must make before the window** (one of):

### G15 authoriser decision memo

Three terms are kept strictly separate throughout, because conflating them is
how a fabricated threshold gets born:

| Term | Definition | Status here |
|---|---|---|
| **Measured baseline** | A number observed from the real system at a known time and state | Exists, **idle only** (backend 0.47% CPU / 505.4 MiB; contract-worker 0.14% / 208.1 MiB) |
| **Canary observation** | A number recorded during the window, for comparison and record | Will exist after Stage C |
| **Acceptance threshold** | A pre-agreed limit whose breach means abort | **Does not exist and never has** |

| | **D-1 — observational** *(recommended)* | **D-2 — declared thresholds** | **D-3 — generate a baseline first** |
|---|---|---|---|
| **What is measured** | CPU, memory, queue age, latency, error rate, restart counts — recorded before and during, plus the two hard zero-cost gates | Same metrics, compared against limits written down in advance | Legacy-pipeline load run over the fixture corpus on comparable hardware, then the same metrics |
| **When the threshold is established** | Never — no numeric threshold is set | Before the window, by judgement | After the pre-window baseline run, derived from data |
| **Who accepts it** | Authoriser accepts *the absence* of numeric limits | Authoriser owns the numbers and that they are judgement, not measurement | Authoriser accepts thresholds derived from a real load run |
| **Abort behaviour** | Abort on *service-affecting symptoms only*: readiness failure, restart loop, queue not draining, request-tier error-rate rise, or either zero-cost gate breached | Abort on any threshold breach, plus the symptom list | Same as D-2, with better-founded numbers |
| **Advantages** | Honest about what is known; cannot produce a false abort from an invented limit; still catches every failure that actually harms the service; no extra window | Crisp, unambiguous pass/fail; easy to audit | Highest confidence; the only option producing a defensible numeric limit |
| **Risks** | A slow degradation inside "no symptom" territory could pass unflagged; relies on operator judgement during the window | Numbers are guesses. A too-tight limit aborts a healthy canary; a too-loose one certifies nothing. Both are recorded as if measured, which is the worst failure mode | Costs an additional change window and its own authorisation; delays the canary; the baseline still will not include `document-worker`, which has never run under load |

**Recommendation: D-1.**

The canary's purpose is to prove *tenant and version isolation*, and every
isolation assertion is already a hard binary gate (Gates B and C) that owes
nothing to resource numbers. The only resource figures with a genuine threshold —
model calls and cost on the clean fixture — are hard-gated at **zero**, taken
from Task 7.6's own words. For everything else there is one idle sample, taken
when the pipeline had been dormant for 30 days, from a stack in which
`document-worker` does not even appear. Promoting that into a limit would invent
a measurement, and D-2 would record the invention as though it were evidence.
D-3 is defensible but buys precision the isolation proof does not need, at the
cost of a second authorised window.

**The decision is the authoriser's.** If they prefer a numeric gate, D-2 is
acceptable provided the sign-off records the numbers as *judgement, not
measurement*.

Until an option is chosen, Gate C row C13 is **undecidable** and is marked as such.

| Metric | Baseline (idle, above) | During canary | Threshold | PASS? |
|---|---|---|---|---|
| backend CPU % | 0.47% | `<unfilled>` | per D-1/D-2/D-3 | `<unfilled>` |
| backend memory | 505.4 MiB | `<unfilled>` | per D-1/D-2/D-3 | `<unfilled>` |
| document-worker CPU % | **no baseline** | `<unfilled>` | per D-1/D-2/D-3 | `<unfilled>` |
| document-worker memory | **no baseline** | `<unfilled>` | per D-1/D-2/D-3 | `<unfilled>` |
| clamav memory | 950.2 MiB / 2 GiB | `<unfilled>` | watch: already 46% idle | `<unfilled>` |
| Queue age (max) | **no baseline** | `<unfilled>` | per D-1/D-2/D-3 | `<unfilled>` |
| Request latency p95 | **no baseline** | `<unfilled>` | per D-1/D-2/D-3 | `<unfilled>` |
| Error rate | **no baseline** | `<unfilled>` | per D-1/D-2/D-3 | `<unfilled>` |
| **Model calls (F1 clean)** | 0 | `<unfilled>` | **0 — hard gate, from the plan text itself** | `<unfilled>` |
| **Cost (F1 clean)** | 0 | `<unfilled>` | **0 — hard gate, from the plan text itself** | `<unfilled>` |

The last two rows are the only resource figures with a genuine threshold, and it
comes from Task 7.6's own words: *"zero intervention/model rows"*.

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
| Gate verdicts | page `quality_verdict` + `quality_checks` + `needs_review`; verdicts `pass` / `not_checkable` / `fail` / conflicting. **Record the full per-page verdict distribution, not just failures** — see the NOT_CHECKABLE note below. |
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

### Required: per-page verdict distribution

```bash
$DC exec -T backend python -c '
import os, json, collections
from pymongo import MongoClient
db = MongoClient(os.environ["DATABASE_URL"]).get_default_database()
ORG = os.environ["CANARY_ORG"]
dist = collections.Counter()
review = 0
for p in db.document_ocr_pages.find({"organization_id": ORG},
                                    {"quality_verdict":1,"needs_review":1}):
    dist[p.get("quality_verdict") or "unset"] += 1
    review += 1 if p.get("needs_review") else 0
print(json.dumps({"verdicts": dict(dist), "pages_needing_review": review}, indent=2))'
```

**Why this is required evidence, not a nicety.** `NOT_CHECKABLE` is accepted and
**never escalates** — by design, and correctly so: *"nothing was verified" is not
"something is wrong"*, and treating it as such is what produced 12 paid calls on
a correct document (`document_processor.py:122-136`). The verdict is persisted
per page, so an unverifiable value is *recorded* as unverified rather than
hidden — but it is **not** flagged for review.

Consequence to watch during the canary: a corrupted amount in a form the repair
pattern does not recognise (G18) parses as unverifiable and lands in
`NOT_CHECKABLE`, not `FAIL`. It is therefore visible only in this distribution.

- **F1 (clean fixture) must be all `pass`.** Any `not_checkable` on F1 is a Gate C
  failure under C6, not a curiosity.
- **F1 fixture design constraint — verified by direct probe.** An amount cell
  carrying a currency prefix (`Rs 134,460`) is `NOT_CHECKABLE` even when the
  arithmetic is entirely correct, because the cell does not parse as a number.
  Select F1 so its amount columns are bare numerics; otherwise C6 fails for a
  benign formatting reason and the window aborts on a false signal. Confirm the
  chosen F1 produces all-`pass` **before** the window, not during it.
- A high `not_checkable` share on F2–F4 is not an abort trigger by itself, but it
  **must be recorded** and it bounds what the canary may claim: those pages were
  extracted, not verified.

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

# 10. GO / NO-GO — three separate decisions

These are **three distinct acceptance decisions taken at three different times**.
They must not be collapsed into one. Passing Gate A does not authorise the
canary; passing Gate B does not declare Task 7.6 accepted.

## Gate A — authorise **starting** the production window

*What must be known before connecting to or changing production at all.*
Evaluated in Stage A, before any deployment.

| # | Condition | Verdict if violated |
|---|---|---|
| A1 | Insufficient production disk/headroom (§2 formula unsatisfied) | **NO-GO** — no deployment |
| A2 | Backup unavailable or unverifiable | **NO-GO** |
| A3 | Wrong commit or branch on the production checkout | **NO-GO** |
| A4 | Working tree dirty on the production checkout | **NO-GO** |
| A5 | Unexpected compose file set / service topology | **NO-GO** |
| A6 | Migration **preflight** failure — blocking duplicate rows, dry-run error/warning (§3 P1–P5) | **NO-GO** |
| A7 | Mongo replica-set, Redis, or readiness check failing | **NO-GO** |
| A8 | RAR enabled without positive EICAR-in-RAR proof | **NO-GO** |
| A9 | More than one scheduler owner, or an unrestricted extraction worker, already present | **NO-GO** |
| A10 | Resource-threshold decision (D-1/D-2/D-3, Table D) not made by the authoriser | **NO-GO** — row 18 is otherwise undecidable |

**Gate A failure leaves production entirely unchanged.**

## Gate B — authorise **enabling** the one-org canary

*What must pass after deployment and migration, before any `unified_v1` job is
permitted to exist.* Evaluated at the end of Stage B.

| # | Condition | Verdict if violated |
|---|---|---|
| B1 | Migration `20260814_0001` not applied, or applied without the expected indexes | **NO CANARY** |
| B2 | Unique `(document_id, extraction_run_id, page_number)` index absent | **NO CANARY** — two workers could write contradictory page evidence |
| B3 | Unique `document_id` index on `document_extraction_heads` absent | **NO CANARY** |
| B4 | `post_deploy_verify.sh` **live run** reports any FAIL (closes G12) | **NO CANARY** |
| B5 | Default worker not restricted to `legacy_v0`, or unrestricted | **NO CANARY** |
| B6 | Canary worker not pinned to `unified_v1`, or not at 0 replicas pre-enable | **NO CANARY** |
| B7 | More than one scheduler owner | **NO CANARY** |
| B8 | Web tier acting as an extraction worker | **NO CANARY** |
| B9 | Any `unified_v1` job already exists | **NO CANARY** |
| B10 | Legacy processing regressed (C2.13/C2.14) | **NO CANARY** → §9 |
| B11 | Restart loop on any worker | **NO CANARY** → §9 |
| B12 | Archive config unverified where archive fixtures are in scope (§4) | **NO CANARY** for archive fixtures |

**Gate B failure returns production to the Stage B resting state** (code deployed,
routing globally off) or rolls back per §9. It does **not** require undoing the
migration — index creation is additive and the legacy path does not use them.

## Gate C — final Task 7.6 **GO**

*What must pass before Phase 7 operational acceptance can be declared.*
Evaluated after Stage C and the rollback drill.

| # | Condition | Verdict if violated |
|---|---|---|
| C1 | Non-canary tenant received `unified_v1` | **NO-GO** + ABORT → §9 |
| C2 | Cross-version execution detected (§8 signals 2/3/4) | **NO-GO** + ABORT → §9 |
| C3 | Page evidence or intervention rows outside the canary org | **NO-GO** + ABORT → §9 |
| C4 | Duplicate page evidence or duplicate heads | **NO-GO** + ABORT → §9 |
| C5 | F1 clean fixture produced **any** intervention/model row | **NO-GO** — the zero-cost claim is false |
| C6 | F1 did not reach `completed` with PASS verdicts and a published head | **NO-GO** |
| C7 | Mixed fixture did not checkpoint/reclaim to its expected state | **NO-GO** |
| C8 | **Rollback drill (§9) not executed, or any of its 7 rows failed** (closes G13) | **NO-GO** |
| C9 | Evidence/run identity changed during rollback | **NO-GO** + incident |
| C10 | Any page evidence deleted or any `pipeline_version` silently rewritten | **NO-GO** + incident |
| C11 | Unexplained processing failures | **NO-GO** + ABORT → §9 |
| C12 | Any Step 5 evidence field left `<unfilled>` | **NO-GO** |
| C13 | Resource metrics breach the threshold chosen under D-2/D-3; **or**, under D-1, a service-affecting symptom occurred (readiness failure, restart loop, queue not draining, request-tier error-rate rise) | **NO-GO** + ABORT → §9 |
| C14 | Sign-off text claims validation of the LLM/Vision fallback ladder | **NO-GO** — factually false while G5/G6 are open |

### Gate mapping for the key gaps

```text
G11 capacity          -> measured during Stage A      -> failure blocks ANY deployment      (Gate A)
G3/G4 migration+index -> applied/verified in window   -> failure blocks CANARY ENABLE       (Gate B)
G12 live verify run   -> executed in Stage B          -> must pass before CANARY ENABLE     (Gate B)
G13 rollback drill    -> executed after Stage C       -> required before FINAL GO           (Gate C)
G5/G6 model/Vision    -> ACCEPTED LIMITATION          -> deterministic-only canary; may NOT
                                                         be claimed as ladder validation    (Gate C, C14)
G14 worker identity   -> ACCEPTED LIMITATION          -> behavioural evidence is authoritative
```

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

# 11. Final pre-authorisation gap matrix (G1–G14)

Each gap is classified as exactly one of `CLOSED`, `ACCEPTED LIMITATION`,
`PRODUCTION-WINDOW EVIDENCE REQUIRED`, `AUTHORISER DECISION REQUIRED`, `BLOCKER`.

| Gap | Status | Code blocker | Blocks window start (Gate A) | Blocks canary enable (Gate B) | Blocks final GO (Gate C) | Acceptance / owner |
|---|---|---|---|---|---|---|
| **G1** canary routing non-functional | `CLOSED` | No | No | No | No | Routing + claim boundary implemented and tested (`a1dc25a`, `1da6276`) |
| **G1a** temporary legacy path | `ACCEPTED LIMITATION` | No | No | No | No | Equivalence-tested vs `ffa844b`. Delete `_extract_legacy`, `legacy_v0` and the claim restriction after global acceptance. Owner: engineering, post-Phase-7 |
| **G2** real Mongo E2E never run | `PRODUCTION-WINDOW EVIDENCE REQUIRED` | No | No | No | **Yes** | F1–F4 complete against production Mongo with §8 clean. First real-infrastructure run; elevated risk accepted in writing before Stage C. Owner: authoriser + operator |
| **G3** migration not applied | `PRODUCTION-WINDOW EVIDENCE REQUIRED` | No | Preflight only (A6) | **Yes** (B1) | **Yes** | §3 P1–P5 pass, then `--apply`, then index verification. Owner: operator |
| **G4** unique page-evidence index | `PRODUCTION-WINDOW EVIDENCE REQUIRED` | No | No | **Yes** (B2) | **Yes** | Hard prerequisite for two concurrent workers. Owner: operator |
| **G5** no real reconstruction/model adapters | `ACCEPTED LIMITATION` | No | No | No | No — **but bounds the claim (C14)** | `EXTRACTION_FALLBACK_ENABLED=false`; ladder inert. **Not closed by this canary and cannot be.** Sign-off must state the limitation. Owner: authoriser |
| **G6** scanned-raster/model escalation | `ACCEPTED LIMITATION` | No | No | No | No — **but bounds the claim (C14)** | Unit-tested only; F2 exercises OCR, not raster escalation into the ladder. **Not closed by this canary.** Owner: authoriser |
| **G7** production archive MIME config | `PRODUCTION-WINDOW EVIDENCE REQUIRED` | No | No | Only if archive fixtures in scope (B12) | No | §4 A1–A3. If `application/zip` absent: fix under authorisation or drop archive fixtures and record why. Owner: operator |
| **G8** RAR disabled/unverified | `ACCEPTED LIMITATION` | No | Only if found enabled (A8) | No | No | Stays `false` throughout, verified by §4. Enabling requires EICAR-in-RAR evidence in a separate reviewed change. Owner: authoriser |
| **G9** `contract_ocr_pages` / jobs unindexed | `ACCEPTED LIMITATION` | No | No | No | No | Pre-existing, out of Phase 7 scope. Monitor queue age. Owner: engineering |
| **G10** intake MIME sniff without filename | `ACCEPTED LIMITATION` | No | No | No | No | Durable job carries the validated MIME. No action. Owner: engineering |
| **G11** production disk capacity | `PRODUCTION-WINDOW EVIDENCE REQUIRED` + `AUTHORISER DECISION REQUIRED` | No | **Yes** (A1) | **Yes** | **Yes** | §2 M1–M8 measured, formula evaluated, threshold accepted by authoriser. Owner: operator measures, authoriser accepts |
| **G12** verify script never run live | `PRODUCTION-WINDOW EVIDENCE REQUIRED` | No | No | **Yes** (B4) | **Yes** | C2.5 live run, output pasted into §5. Owner: operator |
| **G13** rollback drill never executed | `PRODUCTION-WINDOW EVIDENCE REQUIRED` | No | No | No | **Yes** (C8) | §9 executed in full, all 7 rows PASS. Owner: operator |
| **G14** no worker identity on jobs | `ACCEPTED LIMITATION` | **No** — adjudicated §11.1 | No | No | No | Task 7.6 requires pipeline-execution proof, not container identity. Claim path unmodified. Deferred to a later observability task. Owner: engineering |
| **G15** resource acceptance thresholds do not exist | `AUTHORISER DECISION REQUIRED` | No | **Yes** (A10) | No | **Yes** (C13) | Only an *idle* baseline exists and the plan never converts it to a threshold. Authoriser must choose D-1, D-2 or D-3 (Table D). Owner: authoriser |
| **G24** `EXTRACTION_FALLBACK_ENABLED` has no production reader | **`BLOCKER`** | **YES** | **YES** | **YES** | **YES** | Readers are the config definition, `.env.example`, a test asserting the default, the status script and compose. **Zero application code reads it.** Worse than decorative: the committed specification (plan line 8829) *requires* `if settings.EXTRACTION_FALLBACK_ENABLED and source_kind in {PDF, IMAGE}: ladder = ExtractionFallbackLadder(...)`, and that wiring was never implemented — nothing constructs a ladder in production. So **G5's stated premise is wrong**: the ladder is inert because the code does not exist, not because the flag is false, and the rollback lever documented in the runbook does not exist. Owner: engineering |
| **G25** reading-order check false-escalates ordinary correspondence | **`BLOCKER`** | **YES** | **YES** | **YES** | **YES** | **5 of 7** realistic contractual sentences FAIL, e.g. *"Kindly arrange to release the payment to Gulermak Sam India Kanpur Metro JV"*. `reading_order.py:33` `_HEADER_RUN_MIN = 3` flags any long line ending in ≥3 capitalised non-function words — which is what a company name, drawing title or JV name looks like. `reading_order` FAIL propagates directly to page FAIL (`gate.py:208-210`) → escalation. On a correspondence-heavy DMS this plausibly escalates the majority of pages, and with no ladder each becomes human review. Owner: engineering |
| **G26** genuinely ambiguous date columns escalate | `ACCEPTED LIMITATION` | No | No | No | No | **The uncertainty is represented correctly** — a column whose values all read validly both ways returns INDETERMINATE with an accurate detail, exactly as the evidence document's §3.4 requires ("mark the value uncertain and never promote it"). The issue is proportionality: INDETERMINATE escalates, and no amount of reprocessing can resolve a convention the document never states. A human can resolve it from context, so routing to review is defensible; the volume on date registers is the risk. Not a correctness defect. Owner: engineering, post-canary |
| **G18** unrecognised corruption reports PASS — **fails OPEN** | **`CLOSED`** — fixed at `5ed1f0b` | No | No | No | No | Root cause: NOT_CHECKABLE conflated "the check did not apply" with "the check applied and the number was unreadable"; `_aggregate`'s `any(...)` then let a clean peer row speak for the page. `is_unreadable_amount` now discriminates on whether the cell contains digits — damaged numbers FAIL, structural noise (repeated `Amount` header, empty cell) stays NOT_CHECKABLE. `check_subtotal` no longer lets the corruption disable its own corroboration. 17 regression tests (14 failed before, all pass after); the 12 false-positive cases still produce **zero** FAILs. |
| **G20** repairs computed then discarded | **`CLOSED`** — fixed at `a0c47a8` | No | No | No | No | Root cause: `verdict.repairs` had no consumer, and `combined_text` was frozen at `engine.py:156` before the gate ran. Repairs are now applied to page text **and table cells**, the gate is re-run to re-verify, `raw_text` preserves the original for audit, `applied_repairs` carries provenance, and `combined_text` is rebuilt — but only when a repair actually landed, so the no-repair path is unchanged. 7 tests through `_apply_quality_gate`, the production path. |
| **G18 (history)** retracted safety claim | `CLOSED` | No | No | No | No | Recorded for audit: a previous revision of this document asserted G18 "verified to fail CLOSED". **That claim was wrong and is retracted** — it was tested only against a single-row table. With a clean peer row an unparseable amount yielded `NOT_CHECKABLE` and `_aggregate`'s `any(...)` promoted the page to `pass`. Confirmed by probe for all six unreadable forms and for the measured corruption `1 ,900,000`. Fixed at `5ed1f0b`. |
| **G20** recognised repairs are computed and discarded | **`BLOCKER`** | **YES** | **YES** | **YES** | **YES** | `verdict.repairs` is never read outside the quality package (`grep '\.repairs'` → only `quality/gate.py`, `quality/models.py`). `extraction.combined_text` is frozen at `engine.py:156`, *before* the gate runs at `document_processor.py:375`, and becomes `raw_ocr_text` at `:380`. So hard gate (b) — "all 9 detected with correct repairs" — is true at unit level and **vacuous in the pipeline**: the indexed text still carries `1 ,900,000`, which parses downstream as `1`. This is the ₹2.5-crore failure mode the evidence document was written to prevent, still live. Also violates the plan's own invariant (plan line 34: must not suppress "repairs"). Owner: engineering |
| **G22** hard gate (a) is far weaker than it reads | **`BLOCKER`** | **YES** | **YES** | **YES** | **YES** | Executing all 12 false-positive cases gives **9 `not_checkable`, 3 `pass`** — nine are satisfied by the gate *declining to check*, not by checking correctly. And `subtotal_identity` runs in **0 of 12**: `test_quality_gate.py:70` builds `[[headers, row]]`, a single-row table, so `_split_total_row` bails at `gate.py:166-167`. Case 6 — the only case carrying a `stated_total`, and the one meant to prove the monetary tolerance — never uses it and passes on the tautology `1 × 18,450,139 = 18,450,139`. The column-role/date paths that need ≥2 rows are likewise never exercised. Owner: engineering |
| **G23** escalated content is indexed before it is flagged | **`BLOCKER`** | **YES** | **YES** | **YES** | **YES** | `process_document` runs `_save_results` → `save_document_data(...)` with embeddings (`document_processor.py:561-568,683`) and returns `success=True` (`:694`); only afterwards does `document_service.py:1556` call `_checkpoint_extraction_attempt`, which sets `human_review_required`. So a FAIL page's text is already in Qdrant and answerable by retrieval and drafting **before any human sees the flag**. "Fail closed" is a status label on `documents`, not a containment boundary — the house silent-success pattern `CLAUDE.md` warns about. Owner: engineering |
| **G21** gate verdicts are never persisted — canary is unobservable | **`BLOCKER`** | **YES** | **YES** | **YES** | **YES** | `record_pages` is called only inside `engine.extract()` (`engine.py:142`), *before* the gate mutates the pages (`document_processor.py:136-138`). No second write exists. `quality_verdict` is therefore always `None` in `document_ocr_pages`. **The per-page verdict-distribution query added to §7 of this package would bucket 100% of pages as `unset`**, and Gate C6 cannot be evaluated. Owner: engineering |
| **G19** "12 and 9 are the complete pattern set" is a generalisation from one document | `ACCEPTED LIMITATION` | No | No | No | **Yes — for global rollout** | The counts are measured facts about **one** 9-page claim PDF, not a proven taxonomy of the corpus. The canary tests one approved organisation's demo fixtures, so the narrow basis is proportionate. Before global rollout the gate should be run over a broader corpus and the pattern set re-derived. Owner: engineering + authoriser |
| **G17** spec cited an untracked source document | `CLOSED — SOURCE VERSIONED` | No | No | No | No | Committed unedited at **`26b7343`** after a full 453-line review (§11.0). Arithmetic independently re-derived and confirmed; fixtures match entry for entry; nothing contradicted or obsolete; no secrets; the real party data it contains was already committed in seven other files, so no new exposure. Content deliberately **not** edited to agree with current code. Inherited limitations recorded as G18/G19. |
| **G16** governing specification not version-controlled | `CLOSED` | No | No | No | No | Committed as `4ef134d` after review: purely additive at task level (3.4, 4.3, 7.5, 7.6 added, none removed), deletions are in-place refinements matching what was built, no conflict markers/secrets/unrelated content, every named file exists. Included in the release candidate. |

**Nothing above is closed by a unit test.** G2, G3, G4, G7, G12 and G13 require
production execution. G5 and G6 are **not** closed merely because a deterministic
canary can proceed — they bound what the canary may be said to prove.

---

# 11-bis. Gap register — narrative window impact

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
| **G14** no worker identity on jobs | **ADJUDICATED → ACCEPTED LIMITATION.** See §11.1. Task 7.6 requires proof that the correct *pipeline version executed*, not proof of *container identity*. The claim path is left unmodified. | No | No | No | Behavioural evidence (§8) is authoritative; container logs corroborate. Persistent worker-instance attribution deferred to a later observability task. |

**No gap in this table is closed by a unit test.** G3, G4, G11, G12 and G13 all
require production execution inside the authorised window.

## 11.0 G17 investigation — the cited source document

Full read of `docs/architecture/mixed_pdf_ingestion_and_summary_plan_2026-08-13.md`
(453 lines), not just the cited snippets.

**Purpose.** Evidence for the companion architecture document, proving mixed-page
handling against one real 9-page contractor claim PDF, plus a two-tier summary
proposal. **Provenance:** measured with `backend/.venv` using `pdfplumber`/`pypdf`,
replicating two production decision points verbatim.

### Classification of every material statement Task 7.6's lineage relies on

| Statement | Classification | Independently checked here? |
|---|---|---|
| The ~20 arithmetic identities (§3.2) | **MEASURED FACT** | **Yes — every one re-derived and confirmed**, including the 1-unit rounding discrepancies the document itself flags |
| The 9 split-digit corruptions and their true values (§3.1) | **MEASURED FACT** | Partially — 4 of 9 confirmed by independent arithmetic (`2350×30`, `162×830`, `286×1548`, `24×2040`); the rest corroborated by column sums |
| The 12 false-positive patterns (§3.3) | **MEASURED FACT** | Yes — e.g. `3 × 32.61 × 3,200 = 313,056` confirms the `Nos` multiplier case |
| "Native text layer ⇒ high confidence is false" (§3.1) | **DERIVED RULE** | Follows from the measured corruptions |
| Gate runs unconditionally; only escalation is conditional | **DESIGN DECISION** | Implemented; 48 Phase 6 tests |
| 1% rounding tolerance (§3.3) | **DESIGN DECISION** | Anchored to a measured case (58 displayed vs 57.5 true = 0.86%), but the 1% value itself is a choice |
| Dual-confirmation repair rule (§3.2) | **DESIGN DECISION** — and §8 open question 1 explicitly defers it as "a product/legal call, not an engineering one" | Not settled by the document |
| Pages 1–2 are the covering letter | **ASSUMPTION** — the document flags this itself in §9 as inference from position and page size | Not checkable without OCR |
| Date disambiguation by chronological monotonicity | **ASSUMPTION**, flagged in §8 open question 2 | Not settled |
| `is_pdf_textual(max_pages=5)` silently loses pages 1–2 | **MEASURED FACT** | Not re-checkable from the repo (see limitation below) |
| Per-page char/image/table counts, orientation (§1) | **MEASURED FACT** | **Not reproducible from the repo** |

**Nothing in the document was found OBSOLETE or contradicted by the implementation.**

### The one real limitation

§9 states: *"Probe scripts are in the session scratchpad, not the repo."* The
source PDF is likewise absent. So §1/§2 per-page measurements **cannot be
re-derived from the repository** — committing the document versions its
conclusions, not the means to reproduce them.

**This does not weaken the acceptance argument, because the gates do not depend
on that PDF at runtime.** `golden_page_routing.json` names fixture
`build_mixed_pdf`, a **synthetic** 9-page PDF built with `pikepdf`
(`tests/fixtures/pdf_builders.py:81`) that mirrors the measured structure — two
text-free pages, seven text pages, one landscape. The acceptance argument is
fully reproducible from committed artefacts alone.

### Does Task 7.6 depend on this document?

**No.** Task 7.6's committed text cites it **zero** times. The two citations in
the committed specification belong to **Task 0.2** (golden page-routing fixture)
and **Task 6.5** (Phase 6 hard gates). The canary executes the deterministic path
that includes those gates; it does not re-derive them.

### Confidentiality

The document names five real commercial parties and a real ₹22,140,168 claim.
That data is **already committed** in seven or more files — including
`tests/fixtures/pdf_builders.py`, `test_quality_numeric_checks.py`, and the plan
committed at `4ef134d`. Committing the source therefore adds **no new exposure**.

### Circularity assessment

| Assertion | Type |
|---|---|
| `len(cases) == …["false_positive_patterns_that_must_not_fail"] == 12` | **Circular** — fixture self-consistency. Guards against truncation; proves nothing empirical. |
| `detect_split_digits(corrupted) == expected` | **Implementation-conformance**, with expected values **arithmetically corroborated** independently |
| Gate does not FAIL / does not escalate on the 12 cases | **Genuine behavioural validation** — the inputs are arithmetically correct, so a FAIL would be a real defect |
| "12 and 9 are the complete set of patterns" | **UNVERIFIED CLAIM** — a generalisation from one document. See G18. |

## 11.1 G14 adjudication — worker identity

**Question.** Does Task 7.6 require (A) proof of the worker/container identity
that claimed the job, or (B) proof that the correct pipeline version actually
executed?

**Answer: (B).** Every acceptance assertion in Task 7.6 is stated in terms of
job/page/evidence state. Quoting the specification:

- *"only those jobs have `pipeline_version=unified_v1`"* — job state.
- *"clean fixture ends `completed`, has PASS page verdicts, a published
  extraction head, and zero intervention/model rows"* — page/head/ledger state.
- *"mixed fixture checkpoints/reclaims remaining pages and reaches the expected
  completed/review state"* — job/page state.
- *"no non-canary organisation acquires unified page rows or intervention
  rows"* — page/ledger state.

And the Interfaces clause: *"The worker claim predicate and processor dispatch
honour the persisted job version, so a canary worker cannot accidentally
claim/upgrade every tenant."* The property named is **claim-predicate
correctness**, evidenced by what did or did not get processed — not by a
recorded container name.

**The word "worker" appears in Task 7.6 only as the subject of a behaviour, never
as a value to be persisted or asserted.** No sub-step asks for worker identity.

**Evidence chain actually available, and why it is sufficient:**

```text
unified_v1 executed  =>  document_ocr_pages rows exist  AND  head published
legacy_v0  executed  =>  no page rows            AND  no head
```

This is a **stronger** guarantee than `claimed_by` would provide. A persisted
worker name records *who picked the job up*; the page-evidence signature records
*which code actually ran*. A mislabelled or spoofed worker name could not change
the second. Combined with:

- the disjoint claim predicates (proven in `test_pipeline_routing_boundary.py`),
- `post_deploy_verify.sh` asserting disjointness against the live containers,
- per-container log correlation (§8 supplementary),

Task 7.6's assertions are fully satisfiable without schema change.

**Decision: classify G14 as an observability enhancement / accepted limitation.**

- `_claim_next_processing_job()` is **not modified**. It is concurrency-sensitive,
  currently verified, and adding a Mongo write immediately before a production
  canary would introduce risk that closes no acceptance criterion.
- Persisted execution artifacts (page rows, heads, ledger) are the **authoritative**
  evidence of pipeline execution.
- Container logs are retained as **corroboration only**.
- Persistent worker-instance attribution is **deferred to a later observability
  task**, outside Phase 7.

This is not a code-level blocker.

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

---

# 13. Production change request (for authorisation)

## Release

| Field | Value |
|---|---|
| **Commit to deploy** | the tip of `integrate/key-date-eot` at authorisation time — record it here: `<SHA_AT_AUTHORISATION>` |
| **Deployable code/config frozen at** | **`1da6276`**. No code, compose, or `.env.example` change has been made since. Verify: `git diff --stat 1da6276 <SHA> -- backend scripts docker-compose.prod.yml .env.example` must be **empty**. |
| Commits after the code freeze | `c4104b2`, `81b1fea` — **documentation only** (this package) |
| Branch holding the candidate | `integrate/key-date-eot` |
| Production baseline SHA | **UNKNOWN — must be read from the server** (`git rev-parse HEAD` on `contraclaim:/opt/contraclaim-dms/projectDMS`). The runbook default is not trustworthy; production has sat on `codex/*` branches for long stretches. |
| Task reference | Task 7.6 Steps 1–5 |
| **Governing specification** | `docs/superpowers/plans/2026-08-14-unified-page-extraction-phases-0-7.md`, committed at **`4ef134d`** and contained in the candidate. Read Task 7.6 from git, not from a working tree. |
| Approved canary organisation | `<ORG_ID>` — one only, demo data |
| Window duration | `<HH:MM>` to `<HH:MM>` UTC on `<DATE>` |
| Execution package | this document |

## Authorised mutations — exhaustive

1. Create a verified backup of production `.env`, uploads volume, and Mongo.
2. `git fetch` + `git pull --ff-only` to `<FROZEN_SHA>` on the branch production tracks.
3. Rebuild images for **`backend`, `document-worker`, `document-worker-canary` only**.
4. Recreate those three services (`up -d --no-deps`).
5. Apply migration `20260814_0001` (creates 5 indexes; additive, no data rewrite).
6. Edit `.env`: set `UNIFIED_EXTRACTION_CANARY_ORG_IDS=<ORG_ID>`,
   `DOCUMENT_WORKER_CANARY_REPLICAS=1`, keeping
   `DOCUMENT_WORKER_PIPELINE_VERSIONS=legacy_v0`.
7. Recreate `document-worker` and `document-worker-canary` to pick that up.
8. Upload and process **demo-organisation fixtures F1–F6 only**.
9. Execute the §9 rollback: clear the allowlist, set canary replicas to 0,
   recreate both workers.
10. Write evidence into this document.

**Nothing else.** Any action not on this list is outside the authorisation.

## Explicitly prohibited

- Setting `UNIFIED_EXTRACTION_ENABLED=true` — global enablement is a **separate**
  authorised change, blocked regardless of this window's outcome.
- Enabling `RAR_UPLOAD_ENABLED` under any circumstance.
- Adding any organisation to the allowlist beyond the single approved `<ORG_ID>`.
- Processing any non-demo or real-tenant document.
- Downgrading any hard gate in §10 to a warning in order to proceed.
- Rewriting `pipeline_version` on any existing job.
- Deleting page evidence, extraction heads, intervention rows, or running
  `docker prune`.
- Rebuilding or recreating services other than the three named above.

## Automatic abort authority

**The operator may immediately execute the §9 rollback on any hard-gate failure
without obtaining a second approval.** Rollback is pre-authorised. Stopping is
never the action that requires permission; continuing past a failed gate is.

## Acceptance limitation — must be reproduced verbatim in the sign-off

> This canary validates the deterministic unified extraction path and
> tenant/version isolation. It does not constitute validation of the production
> LLM/Vision reconstruction ladder while G5/G6 remain open/accepted limitations.

## 13.1 Authorisation text

> I authorise a production change window on **`<DATE>` from `<START>` to `<END>`
> UTC** to execute Task 7.6 Steps 1–5 exactly as written in
> `docs/operations/unified_extraction_canary_execution_package.md`, deploying
> commit **`<FROZEN_SHA>`**.
>
> **Authorised mutations:** the ten items enumerated in §13, and no others.
>
> **Prohibited:** global enablement (`UNIFIED_EXTRACTION_ENABLED=true`); RAR
> enablement; any organisation beyond **`<ORG_ID>`**; processing any non-demo
> document; downgrading any hard gate; rewriting `pipeline_version`; deleting
> any evidence.
>
> **Resource threshold decision (G15):** I select option **`<D-1 | D-2 | D-3>`**
> from §5 Table D. Where D-2 or D-3 is chosen, the thresholds are: `<values>`.
>
> **Capacity (G11):** I accept the disk threshold derived in-window by the §2
> formula and recorded in §5 Table E. If `free_after_backup < required_headroom`,
> the window ends with NO-GO and no deployment occurs.
>
> **Specification versioning (G16):** I `<have committed the reviewed plan / accept
> that Task 7.6's specification is currently uncommitted>`.
>
> **Accepted limitations:** G1a, G5, G6, G8, G9, G10, G14. I acknowledge that this
> canary validates the deterministic unified extraction path and tenant/version
> isolation, and **does not** constitute validation of the production LLM/Vision
> reconstruction ladder while G5/G6 remain open.
>
> **Abort authority:** the operator may execute the §9 rollback immediately on any
> hard-gate failure without seeking further approval.
>
> Global enablement remains a separate authorised change.
>
> Authoriser: `<name>`  ·  Role: `<role>`  ·  Date: `<date>`
> Operator: `<name>`  ·  Date: `<date>`
