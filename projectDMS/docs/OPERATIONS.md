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
- `/health/operations`: token-gated backup freshness report.
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

Check backup freshness:

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

## 6. Post-Deploy Verification

Run:

```bash
scripts/post_deploy_verify.sh
```

This checks container status, `/health/live`, `/health/ready`,
`/health/observability`, `/metrics`, backup freshness, MongoDB, Redis, and
recent backend exception signatures. Set `REQUIRE_FRESH_BACKUP=true` to make
backup freshness a hard post-deploy gate.

## 7. Scheduler Ownership

Only one process should run scheduled jobs in production. In
`docker-compose.prod.yml`, `contract-worker` has `RUN_SCHEDULER=true` and the
web `backend` has `RUN_SCHEDULER=false`. The scheduler also uses Mongo leader
locks as a safety net.

Verify after deploy:

- Worker logs show scheduler startup.
- `scheduler_locks` contains short-lived locks while jobs run.
- No duplicate daily or weekly notifications are emitted.

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
