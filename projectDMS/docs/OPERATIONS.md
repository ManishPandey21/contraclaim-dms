# Contraclaim DMS Operations Runbook

This runbook covers production and managed-pilot operations for Contraclaim DMS.
Production promotion is controlled by [PRODUCTION_READINESS_RELEASE_GATE.md](PRODUCTION_READINESS_RELEASE_GATE.md).

## 1. Deployment Gates

Run before every production deploy:

```bash
scripts/pre_deploy_readiness.sh
python scripts/preflight.py
```

Use `REQUIRE_FRESH_BACKUP=true scripts/pre_deploy_readiness.sh` when the release
requires a fresh local backup before deploy.

Run the database migration dry-run before every staging or production deploy:

```bash
cd backend
python -m rbac_backend.scripts.migrate_database --list
python -m rbac_backend.scripts.migrate_database --fail-on-warning
```

`--fail-on-warning` distinguishes two things a migration can say, by provenance
rather than by severity. A **warning** is a finding about the database in front
of the migration — it names a row or a change this deployment carries, and it is
absent when the data is clean; it exits 2. A **notice** is a constant sentence
the migration writes about its own design, identical on every database; it is
printed as `NOTICE …` on stderr and does not fail the gate. Both are always
reported and neither is suppressed. Until R-A8N the runner failed on both, so
this documented gate exited 2 on every healthy tree — `20260721_0001` carries
two documentary notes — which is what R-A8M measured on staging (F-A8M-1).

Apply pending migrations only after backup freshness is confirmed and the dry-run
output has been reviewed:

```bash
cd backend
python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
```

`scripts/pre_deploy_readiness.sh` can enforce the dry-run gate:

```bash
RUN_MIGRATION_DRY_RUN=true REQUIRE_MIGRATION_DRY_RUN=true scripts/pre_deploy_readiness.sh
```

The backend also validates production runtime config at startup. It rejects
unsafe settings such as placeholder secrets, development CORS origins, insecure
cookies, fail-open RBAC, missing metrics token, missing Redis URLs, standalone
MongoDB unless explicitly accepted, raw-query observability in production, and
missing production backup configuration.

## 2. Health And Monitoring

Endpoints:

- `/health/live`: process liveness.
- `/health/ready`: MongoDB, Redis, config, and writable upload-storage readiness.
- `/health/observability`: token-gated operational snapshot.
- `/health/operations`: token-gated backup report - freshness **and**, for every
  archive with a declared content contract, whether it is a usable recovery
  artefact. Each artifact carries an explicit state: `VALID`, `UNVERIFIED`
  (fresh, no contract declared), `UNEVALUATED` (contract declared, not
  applied), `STALE`, `EMPTY`, `MISSING`, `UNREADABLE`, `INVALID_CONTENT` or
  `INVALID_ROOT`. Only `VALID` and `UNVERIFIED` are healthy, and only `VALID`
  claims the archive restores. The contract lives in
  `services/backup_archive_validation.py` and is shared with
  `scripts/backup_volume.sh`, so the nightly backup and the health endpoint
  cannot drift apart.
- `/metrics`: Prometheus text format, token-gated when `METRICS_TOKEN` is set.

Important environment variables:

- `METRICS_ENABLED=true`
- `METRICS_TOKEN=<internal-scrape-token>`
- `SLOW_REQUEST_THRESHOLD_MS=2000`
- `BACKUP_ROOT=/var/backups/contractdms`
- `BACKUP_MAX_AGE_HOURS=26`
- `BACKUP_REQUIRED_IN_PRODUCTION=true`
- `BACKUP_S3_BUCKET=<offsite-backups-bucket>`
- `BACKUP_S3_PREFIX=contraclaim/backups`

Prometheus metrics include HTTP request counts, latency histograms, server error
counts, domain audit event counts, and backup health gauges:

- `contractdms_backup_health`
- `contractdms_backup_latest_age_seconds`
- `contractdms_backup_missing_artifacts`
- `contractdms_backup_unhealthy_artifacts`

## 3. Alerting Baseline

Alert on these signals before production promotion:

- `/health/ready` returns non-200 for more than two checks.
- `/health/operations` returns non-200 or `contractdms_backup_health == 0`.
- `contractdms_backup_latest_age_seconds > BACKUP_MAX_AGE_HOURS * 3600`.
- `contractdms_server_errors_total` increases quickly over a short window.
- Request latency p95 exceeds the release SLO for key workflows.
- Backend logs contain repeated `traceback`, `critical`, `unhandled`, or
  `exception` signatures after deploy.
- Disk free space at `BACKUP_ROOT` falls below the backup retention requirement.

## 4. Backups

What is backed up:

| Component | Method | Script |
| --- | --- | --- |
| MongoDB | `mongodump --gzip --archive` | `scripts/mongo_backup.sh` |
| Uploads | Docker volume tarball | `scripts/production_backup.sh` |
| Qdrant | Docker volume tarball | `scripts/production_backup.sh` |
| FalkorDB | BGSAVE plus Docker volume tarball | `scripts/production_backup.sh` |
| Redis | BGSAVE plus Docker volume tarball | `scripts/production_backup.sh` |
| Offsite copy | AWS S3 sync | `scripts/backup_offsite_s3.sh` |

Run local backup:

```bash
BACKUP_ROOT=/var/backups/contractdms scripts/production_backup.sh
```

Run local plus offsite backup:

```bash
BACKUP_ROOT=/var/backups/contractdms \
BACKUP_S3_BUCKET=<offsite-backups-bucket> \
scripts/backup_offsite_s3.sh
```

The backup job writes:

- `manifests/backup-<stamp>.json`
- `manifests/latest.json`
- `manifests/checksums-<stamp>.sha256`
- `mongo/<db>-<stamp>.archive.gz`
- `volumes/<label>-<stamp>.tar.gz`

Check the backups - freshness, and the content of every archive that declares
a contract:

```bash
python scripts/backup_status.py --root /var/backups/contractdms --max-age-hours 26
python scripts/backup_status.py --json
```

Cron example:

```cron
30 1 * * * cd /opt/contraclaim && BACKUP_S3_BUCKET=<bucket> ./scripts/backup_offsite_s3.sh >> /var/log/contraclaim-backup.log 2>&1
45 1 * * * cd /opt/contraclaim && python ./scripts/backup_status.py --root /var/backups/contractdms --max-age-hours 26 >> /var/log/contraclaim-backup-status.log 2>&1
```

Use an S3 lifecycle rule for offsite retention, for example transition to cold
storage after 30 days and expire after 90 days.

## 5. Restore Drill

Run a restore drill quarterly in an isolated environment. Record the date,
source backup stamp, target environment, elapsed restore time, verification
results, RPO, and RTO.

Restore order:

1. Stop application services in the target environment.
2. Restore MongoDB.
3. Restore uploads, Qdrant, FalkorDB, and Redis volumes.
4. Start services.
5. Run health, search, document, and graph smoke checks.

Mongo restore:

```bash
MONGO_URI=<target-uri> MONGO_DB=<target-database> \
scripts/mongo_restore.sh /var/backups/contractdms/mongo/contraclaim-<stamp>.archive.gz
```

`MONGO_DB` has no default. It used to default to `contraclaim`, so a forgotten
variable aimed the restore at production; the script now refuses to run without an
explicit target.

Two further behaviours matter when the target is a replica set addressed by
docker-internal names.

- **Execution context.** The production and staging URIs name the replica set as
  `mongo1`, `mongo2`, `mongo3`, which do not resolve on the host - R-A8I's drill
  failed with `dial tcp: lookup mongo1 ... server misbehaving` and completed only
  when the same script was re-run by hand inside the container.
  `RESTORE_EXEC_CONTEXT` defaults to `auto`: the script resolves each host in the
  URI and, if one does not resolve, runs `mongorestore` inside the replica-set
  container (`MONGO_EXEC_SERVICE`, default `mongo1`) with the archive streamed on
  stdin. Force it with `RESTORE_EXEC_CONTEXT=host` or `=compose`. The context it
  chose is printed before the restore starts - read that line; it is the answer to
  "which topology did this actually use".
- **Production is refused by default.** A `MONGO_DB` of `contraclaim`, or a URI
  naming `replicaSet=rs0`, exits non-zero before connecting unless
  `ALLOW_PRODUCTION_RESTORE=1` is also set. Step 1 above is not optional: R-A8I
  recorded a restore racing a running application producing 396 permission rows for
  198 distinct names.
- **The exit code certifies parity, not completion.** R-A8M's idempotency run
  reported `0 document(s) restored successfully. 222 document(s) failed to
  restore.`, `mongorestore` exited 0 — duplicate-key failures are not fatal to it —
  and the script printed `MongoDB restore completed`. It no longer does. After the
  restore the script prints a `--- RESTORE VERIFICATION ---` block
  (`EXPECTED_DOCS`, `RESTORED_DOCS`, `FAILED_DOCS`, `EXPECTED_COLLECTIONS`,
  `RESTORED_COLLECTIONS`, `MISSING_COLLECTIONS`, `MATCH`, `STATUS`) and exits
  non-zero unless `STATUS` is `OK` or `OK_EMPTY`. **Read `STATUS`, not the prose.**

  | Situation | Outcome |
  |---|---|
  | every offered document restored | `STATUS=OK`, exit 0 |
  | any failed document, or a shortfall against the expectation | `STATUS=FAILED`, exit 5 |
  | a collection the archive carried and the restore skipped | `STATUS=FAILED`, exit 5 |
  | nothing restored and nothing offered | `STATUS=FAILED`, exit 5 — a genuinely empty archive is indistinguishable from a wrong `--nsInclude`, a wrong database or a wrong endpoint, so declare it with `RESTORE_ALLOW_EMPTY=1` (`STATUS=OK_EMPTY`) |
  | `mongorestore` printed no summary this script can read | `STATUS=UNVERIFIED`, exit 4 |
  | `RESTORE_VERIFY=0` | restores, certifies nothing, and says so |

  `RESTORE_EXPECTED_DOCS=<n>` replaces the archive's own accounting with a count
  you state, which is the stronger check when you know it: the archive's own
  accounting cannot see documents it never read.

### Off-site backup posture

Production documents and production backups currently share one S3 bucket, and the
staging backup principal deliberately has no `DeleteObject`. Both are recorded,
with what closing the first involves and how to clean up after the second, in
[S3_STORAGE_POSTURE_DEBT.md](S3_STORAGE_POSTURE_DEBT.md). Read it before quoting
an RPO that treats the off-site copy as a separate failure domain.

Volume restore:

```bash
docker compose -f docker-compose.prod.yml stop backend contract-worker qdrant falkordb redis

scripts/production_restore_volumes.sh --apply <project>_backend_uploads /var/backups/contractdms/volumes/backend-uploads-<stamp>.tar.gz
scripts/production_restore_volumes.sh --apply <project>_qdrant_data /var/backups/contractdms/volumes/qdrant-data-<stamp>.tar.gz
scripts/production_restore_volumes.sh --apply <project>_falkordb_data /var/backups/contractdms/volumes/falkordb-data-<stamp>.tar.gz
scripts/production_restore_volumes.sh --apply <project>_redis_data /var/backups/contractdms/volumes/redis-data-<stamp>.tar.gz

docker compose -f docker-compose.prod.yml up -d
```

Post-restore verification:

```bash
scripts/post_deploy_verify.sh
SMOKE_BASE_URL=https://<host> SMOKE_CHECK_OPERATIONS=true METRICS_TOKEN=<token> python scripts/smoke_health.py
```

Manual checks:

- Log in as an admin.
- Open a known document and download it.
- Run one contract search and one Contract Q&A query.
- Confirm vector-backed search returns results.
- Confirm reference links resolve through FalkorDB-derived graph views.
- Confirm `/health/operations` reports `status=ok`.

If Qdrant or FalkorDB are stale but Mongo and uploads are correct, prefer a
reconciliation/rebuild over treating the derived stores as authoritative.

### Recovery objective — RPO and RTO

**Status: OBJECTIVE RECORDED. Not OBJECTIVE FULLY DEMONSTRATED.**

| | |
|---|---|
| **RPO** | **24 hours** |
| **RTO** | **8 hours** |
| **Restore-drill cadence** | **Quarterly, in staging** |
| Decided | **2026-09-08** |
| Decided by | **the release owner**, approving Candidate A — Conservative from [RPO_RTO_OWNER_DECISION.md](RPO_RTO_OWNER_DECISION.md) §4 |

This records the objective the deployment is being held to. It does **not**
record that the objective has been demonstrated end to end, and no run has
established that a real recovery completes inside 8 hours or that no more than
24 hours of data is lost. Candidate A was chosen because it is the row this
deployment already satisfies by construction — the daily 01:30 backup already
bounds worst-case loss at 24 hours — rather than one that needs new capability
first.

Read the two lines apart:

* **OBJECTIVE RECORDED** — the target exists, is owner-approved, and is the
  number every recovery decision is measured against from 2026-09-08.
* **OBJECTIVE FULLY DEMONSTRATED** — not claimed. See the debt below.

The owner accepted, explicitly and on the record, that **staging-scale restore
measurements are the present evidence basis** for this release, subject to later
production-scale validation.

#### Debt carried with this decision

These stay open, and each is a production-cutover or operational-hardening item
rather than a caveat on the number:

1. **Production document storage and backup storage must be separated into
   independent S3 failure domains.** They share one bucket today
   ([S3_STORAGE_POSTURE_DEBT.md](S3_STORAGE_POSTURE_DEBT.md)), so the off-site
   copy is not yet an independent failure domain, and the 24 h RPO must not be
   quoted as if it were. **No production S3 architecture change is authorised in
   this phase.**
2. **Production-scale restore timing is unmeasured.** Every restore figure behind
   this decision was taken at staging scale — a 222-document Mongo restore
   against production's live corpus, and 3 Qdrant points against production's
   2,092.
3. **Recovery-event detection time is unmeasured.** Every measured figure starts
   at "an operator has decided to restore". RTO starts at the event, so detection
   time must be estimated or measured before the 8 h RTO can be claimed as
   demonstrated end to end.

Re-drill quarterly in staging, per the cadence above, and revise this block when
any of the three items closes.

## 6. Post-Deploy Verification

Run:

```bash
scripts/post_deploy_verify.sh
```

This checks container status, `/health/live`, `/health/ready`,
`/health/observability`, `/metrics`, backup freshness and archive validity,
MongoDB, Redis, and recent backend exception signatures. Set
`REQUIRE_FRESH_BACKUP=true` to make the backup check a hard post-deploy gate;
without it a backup that will not restore is a warning rather than a failure.

### 6.1 The edge check has two modes, and neither is the other's default

Production and staging do not have the same edge, and the verifier must not
pretend they do. `scripts/lib/edge_target.sh` resolves the mode from
`DEPLOY_VERIFY_MODE`, or failing that from `ENVIRONMENT`, and **refuses**
(exit 2, reported as a `FAIL`) when neither says. There is no default, because
both defaults are wrong: guessing staging hands production the staging
exemption, and guessing production makes every staging run demand DNS it is
designed not to have.

| | production | staging |
|---|---|---|
| edge URL | **`PUBLIC_BASE_URL`, required** | `STAGING_EDGE_BASE_URL`, else `PUBLIC_BASE_URL`, required |
| TLS | required | required |
| public DNS name | **required** | **not required** — loopback and RFC 1918 are the staging topology |
| may address a production host | n/a — the plan reports `production_hosts=not-applicable`, because a line naming a control it did not consult reads as "checked against these" | **no** — refused against `PRODUCTION_PUBLIC_HOSTS` |
| unset edge | **FAIL** | **FAIL** |

The rule that matters most is the last row. It used to read "skip": the check
was wrapped in `if [[ -n "$PUBLIC_BASE_URL" ]]`, so a production run with the
variable unset reported the same `10 PASS, 0 failures` as one that reached the
public edge and got 200. The single control over the surface every user arrives
through was the one control that could disappear without saying so.

Staging's exemption is from **public DNS only**. It still needs an edge, and it
still needs TLS: `AUTH_COOKIE_SECURE=true` means no session cookie survives
plain HTTP, which is why R-A8Q had to stand up a TLS terminator before Gate 3
bullet 1 could be measured at all. Declare `PRODUCTION_PUBLIC_HOSTS` for any
staging run on the production host — without it the "staging pointed at
production" refusal cannot fire, and the plan line says `production_hosts=none-declared`
so the operator can see that rather than assume otherwise.

`.env.staging.example` ships `PRODUCTION_PUBLIC_HOSTS=REPLACE-WITH-PRODUCTION-HOSTNAMES`
and **not** the real hostnames, because
`test_staging_compose.py::test_the_staging_template_exists_and_carries_no_real_value`
refuses a tracked staging template that names the production host — a template
that ships one is a copy-paste away from pointing staging at production, which is
the failure this variable exists to prevent. The cost of a placeholder is an
operator who never edits it, whose refusal list then matches nothing while the
plan reports it as declared. A control that is present, green and inert is worse
than an absent one, so `edge_target.sh` **refuses** an unedited placeholder in
staging mode rather than treating it as a hostname. Production mode ignores the
list entirely.

Every rule above has a test in
`backend/rbac_backend/tests/test_deploy_edge_verification.py`, including the
negative controls: a production edge on loopback, a private range, a single
container label or an internal-only suffix is refused, and so is a staging edge
that names a production host.

## 7. Scheduler Ownership

Only one process should run scheduled jobs in production. In
`docker-compose.prod.yml`, `contract-worker` has `RUN_SCHEDULER=true` and the
web `backend` has `RUN_SCHEDULER=false`. The scheduler also uses Mongo leader
locks as a safety net.

Verify after deploy:

- Worker logs show scheduler startup.
- `scheduler_locks` contains short-lived locks while jobs run.
- No duplicate daily or weekly notifications are emitted.

## 7a. Contract Master Reprojection Ownership

A promoted or corrected contract instrument is written `projection_status=PENDING`
in the same transaction that makes its classification authoritative. Only the
reprojection runtime moves it to `CURRENT`, and until then Contract Master
evidence for that contract answers `409 projection_not_current`. The 2026-09-25
staging rehearsal failed exactly here: PENDING was written and no process owned
it. A deploy that ships the code without a running owner reproduces that
incident, so ownership is verified after every deploy, not assumed.

| Service | `START_CONTRACT_REPROJECTION_WORKERS` | Owns |
|---|---|---|
| `contract-worker` | `true` (exactly one service) | reprojection, contract ingest queue, scheduler |
| `backend` (web) | unset / `false` | must never reproject |
| `document-worker` | unset / `false` | page extraction (`START_DOCUMENT_EXTRACTION_WORKERS=true`) |

**Build every image with the release identity.** Each service builds its own
image from `./backend`, so rebuilding only `backend` leaves `contract-worker` on
the previous code while the checkout says otherwise. The build stamps the commit
into the image label `org.opencontainers.image.revision`, and `up` passes the
same value to the process as `RELEASE_SHA`:

```bash
export RELEASE_SHA=$(git rev-parse HEAD)
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml build backend contract-worker document-worker document-worker-canary
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml up -d --no-deps backend contract-worker document-worker
```

An image built without `RELEASE_SHA` is labelled `unknown`, which the check below
reports as a failure: it cannot prove what code is running.

**What `scripts/post_deploy_verify.sh` asserts** (section "Contract Master
reprojection ownership"; read-only, it writes nothing):

1. `contract-worker` is running with `START_CONTRACT_REPROJECTION_WORKERS=true`.
2. `backend` and every `document-worker*` container do **not** have it set. A
   second owner would be safe (the claim lease) but is a topology error.
3. `document-worker` still owns extraction (`START_DOCUMENT_EXTRACTION_WORKERS=true`).
4. The `contract-worker` image revision equals the deployed checkout's commit
   (FAIL on mismatch); `backend` and `document-worker` are compared too (WARN).
5. The runtime recorded liveness in `contract_reprojection_runtime_heartbeats`
   within three poll intervals (`CONTRACT_REPROJECTION_POLL_SECONDS`, at least
   180 s), from a running `contract-worker` container, on the deployed
   `RELEASE_SHA` - and no running `backend` or `document-worker` container has a
   fresh row. Liveness is written throughout a pass, so a long pass over a
   backlog does not read as a dead owner; the pass start is recorded too, and a
   pass still running after 30 minutes (the claim lease) fails the check as
   stuck. A stopped runtime deletes its row, and rows of processes that
   died expire after 7 days (TTL); a row left by the container a deploy
   replaced names a host that no longer runs and is ignored. This proves the
   loop is running the new code, not only that the flag is set.

**Staging only: a deterministic PENDING generation.** Promote a fixture contract
on staging (the Contract Master runtime harness, or the migration adjudication
flow by hand), then run the verifier with its instrument id:

```bash
REPROJECTION_PROBE_INSTRUMENT_ID=<contract_documents._id> scripts/post_deploy_verify.sh
```

It waits up to `REPROJECTION_PROBE_TIMEOUT_SECONDS` (default 300) for the
instrument to leave PENDING, and passes only when it is `CURRENT` at its live
revision **and** its completed claim names a worker id that heartbeated from a
`contract-worker` container. The probe only reads; the promotion that creates the
PENDING generation is the operator's, so run it only against a fixture made for
the purpose, never a production instrument.

**When a generation does not reach CURRENT.** `GET
/api/contract-master/instruments/{id}/projection` shows the claim state:
`failures`, `next_attempt_at` and an operator-safe `last_error`. A FAILED
generation retries by itself with capped backoff (1, 2, 4 … 60 minutes); an
operator retry is `python -m rbac_backend.scripts.contract_reprojection_retry
--contract-document-id <instrument id>` inside the contract-worker container.

| `last_error` says | Meaning | Action |
|---|---|---|
| `embedding unavailable` / `vector publication failed` | a derived store is down | none; it retries |
| `the source of … is mutating` | an ingest (upload, OCR retry, reindex) is writing the text | none; the ingest re-opens the generation when it settles |
| `the source of … is failed` | the last ingest stopped part-way; its text is half-written | reindex the contract |
| `no persisted page text` | a PDF contract ingested before the page store existed | reindex the contract |

**Generic pipelines never repair a governed contract.** `POST
/api/v1/ingestion/jobs` answers `409` for a document a Contract Master instrument
names (that pipeline prunes every point it did not write, which deleted the
projection's vectors), general reprocess answers `409`, and storage repair
delegates: a CURRENT projection whose points are missing from the vector store is
handed back to reprojection (PENDING), a FAILED one is re-opened, and bulk resync
skips governed documents entirely.

**One writer of a contract's source.** A contract ingest holds a durable lease
on the document (`contract_source_leases`, 10 minutes, renewed every minute). A
second ingest of the same document is not started: the queue puts it back
without spending a retry, or leaves it to the run that holds the lease when it
is a redelivered copy of that run. An ingest that cannot renew for 7 minutes
stops itself (recorded as a failed, retried ingest) before its lease can lapse;
one whose lease was taken marks the source tainted. A lease that lapses while
`mutating` is a crashed ingest; a failed or tainted source is not projected
until another ingest settles it cleanly - the projection route then says
`the source of … is failed`, and the fix is a reindex.

## 8. Security Operations

- Keep `.env` out of git and source production values from a secrets manager.
- Rotate `SECRET_KEY` only with an explicit session invalidation plan.
- Keep `ALLOW_DEV_HEADERS=false` and `RBAC_ENTITLEMENT_FAIL_OPEN=false`.
- Keep `AUTH_COOKIE_SECURE=true`.
- Keep `ANTIVIRUS_ENABLED=true` and `CLAMAV_FAIL_OPEN=false` for production
  document upload.
- Keep `OBSERVABILITY_STORE_RAW_QUERIES=false` in production.

## 9. Evidence Capture Template

```text
Date:
Branch/commit:
Environment:
Backup stamp:
Command/check:
Result:
RPO observed:
RTO observed:
Evidence link or log path:
Owner:
Follow-up issue:
```
