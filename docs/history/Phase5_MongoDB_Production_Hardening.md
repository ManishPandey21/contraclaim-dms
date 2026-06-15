# Phase 5 - MongoDB Production Hardening

Date: 03 May 2026

Scope: self-managed MongoDB production hardening without moving to MongoDB Atlas.

## Implemented

- Added production MongoDB connection controls in `backend/rbac_backend/core/config.py`.
- Added MongoDB client pooling, timeouts, app name, retry writes, database-name selection, and production replica-set checks in `backend/rbac_backend/core/database.py`.
- Changed index creation to run asynchronously with retry so startup is not blocked by slow index builds.
- Added compound indexes for high-volume tenant-scoped document, file object, contract, contract upload, audit, letter, and bulk upload query patterns.
- Added optional three-node self-managed replica-set Compose overlay in `docker-compose.mongo-replicaset.yml`.
- Added MongoDB backup and restore scripts:
  - `scripts/mongo_backup.sh`
  - `scripts/mongo_restore.sh`
- Added replica-set initialization script:
  - `scripts/mongo/init-replica-set.sh`
- Added MongoDB production environment examples in `.env.example` and `backend/rbac_backend/.env.example`.

## Required Production Settings

Use a replica-set connection string for production:

```env
DATABASE_URL=mongodb://mongo1:27017,mongo2:27017,mongo3:27017/contraclaim?replicaSet=rs0&retryWrites=true
MONGODB_DATABASE=contraclaim
MONGODB_REPLICA_SET=rs0
MONGODB_MAX_POOL_SIZE=100
MONGODB_MIN_POOL_SIZE=5
MONGODB_SERVER_SELECTION_TIMEOUT_MS=5000
MONGODB_CONNECT_TIMEOUT_MS=5000
MONGODB_SOCKET_TIMEOUT_MS=20000
MONGODB_ALLOW_STANDALONE_PRODUCTION=false
```

Production startup now rejects MongoDB localhost URLs and rejects standalone MongoDB unless `MONGODB_ALLOW_STANDALONE_PRODUCTION=true` is explicitly set. That override is only for emergency maintenance, never normal production.

## Self-Managed Replica Set

For a local or VPS self-managed replica set:

```bash
docker compose -f docker-compose.mongo-replicaset.yml up -d
```

Use this API/worker MongoDB URL:

```env
DATABASE_URL=mongodb://mongo1:27017,mongo2:27017,mongo3:27017/contraclaim?replicaSet=rs0&retryWrites=true
MONGODB_REPLICA_SET=rs0
```

Production hardening still requires host-level controls:

- Keep MongoDB ports private to the Docker/internal network or VPN.
- Enable authentication before exposing this outside a trusted private network.
- Use TLS for cross-host replica-set traffic.
- Store key files and database users in a secret manager or root-only host files.
- Place MongoDB volumes on monitored disks with alerts for free space, I/O wait, and filesystem errors.

## Backup

Run a logical backup:

```bash
MONGO_URI="$DATABASE_URL" MONGO_DB=contraclaim BACKUP_DIR=/backups/mongo RETENTION_DAYS=14 ./scripts/mongo_backup.sh
```

Schedule it through cron/systemd timer on the database host or backup runner:

```cron
15 2 * * * cd /opt/projectDMS && MONGO_URI='mongodb://...' MONGO_DB=contraclaim BACKUP_DIR=/backups/mongo RETENTION_DAYS=14 ./scripts/mongo_backup.sh
```

Minimum policy:

- Daily full logical backup.
- Retain at least 14 days locally.
- Copy backups to an off-host encrypted location.
- Test restore before production launch and at least monthly.

## Restore Drill

Restore into a non-production MongoDB instance first:

```bash
MONGO_URI="mongodb://restore-mongo:27017/contraclaim?replicaSet=rs0" MONGO_DB=contraclaim ./scripts/mongo_restore.sh /backups/mongo/contraclaim-YYYYMMDD-HHMMSS.archive.gz
```

Verification checklist:

- API `/health/ready` succeeds against restored MongoDB.
- Document list, document download metadata, contract list, and contract ingestion job screens load.
- Counts for `documents`, `file_objects`, `document_versions`, `contracts`, `contract_versions`, and `document_audit_events` match the backup source.
- A sample restored file object resolves to the expected local/S3 storage key.

## Indexes

Phase 5 adds tenant-scoped compound indexes for:

- Documents by organization, project, lifecycle, status, upload type, and created/updated time.
- File objects by organization, project, hash, provider, bucket, storage key, and created time.
- Contracts and contract versions by organization, project, lifecycle, version, and file object.
- Audit events by resource, tenant, actor, and event time.
- Contract upload sessions by TTL expiry and user/project.
- Contract ingest jobs by tenant, upload, status, and update time.
- Bulk upload jobs by job ID, status, and creation time.

Before launch, run `explain("executionStats")` for the highest-volume document list/search, contract list, upload-session, and audit queries and confirm they use `IXSCAN` rather than collection scans.

## Slow Query Monitoring

Enable MongoDB profiling in production with a conservative threshold:

```javascript
db.setProfilingLevel(1, { slowms: 100 })
```

Inspect recent slow queries:

```javascript
db.system.profile.find().sort({ ts: -1 }).limit(20).pretty()
```

Operational alerts should cover:

- Primary unavailable or frequent elections.
- Replication lag above the accepted threshold.
- Disk utilization above 80%.
- Slow query volume spikes.
- Connections near configured limits.
- Backup job failure or missing backup for more than 24 hours.

## Retention Policy

Recommended starting policy:

- `contract_upload_sessions`: TTL by `expiresAt`; implemented.
- Runtime sessions, lockouts, rate-limit buckets, and cache: Redis TTL; implemented in Phase 3 when Redis is configured.
- `bulk_upload_jobs`: retain 30-90 days, then archive/delete after reporting requirements are defined.
- `contract_ingest_jobs`: retain 90 days for troubleshooting, then archive summary records.
- `document_audit_events`: retain long term; do not TTL-delete until legal/compliance retention is approved.
- `file_objects`, `document_versions`, and `contract_versions`: immutable records; archive cold files but keep metadata unless a tenant retention/deletion policy requires removal.

## Remaining Work

- Enable MongoDB auth, TLS, and key-file authentication for a real multi-host deployment.
- Add a scheduled backup runner and off-host encrypted backup copy.
- Run and document a restore drill.
- Add dashboards and alerts for MongoDB health, disk, replication lag, connections, and slow queries.
- Replace remaining unbounded request-path `to_list(length=None)` calls with pagination or streaming.
- Add idempotent migration/backfill runner for future schema/index/data migrations.
