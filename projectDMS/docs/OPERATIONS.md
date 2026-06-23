# ContraclaimDMS — Operations Runbook

Operability reference for production/pilot deployments. Pairs with
[ARCHITECTURE.md](ARCHITECTURE.md) (system design) and [AUTHZ.md](AUTHZ.md)
(authorization model).

Stack (see `docker-compose.prod.yml`): Apache `gateway` → `client` (Vite) +
`backend` (FastAPI) + `contract-worker`; data plane: MongoDB (replica set),
Qdrant (vectors), FalkorDB (graph), Redis (sessions/queue), ClamAV.

---

## 1. Deployment

### 1.1 Preflight (before every deploy)

```bash
scripts/pre_deploy_readiness.sh        # disk/mem, env completeness, port exposure
```

The backend additionally **fails fast on startup** via
`settings.validate_runtime_configuration()` if production config is unsafe
(placeholder `SECRET_KEY`, `ALLOW_DEV_HEADERS=true`, fail-open RBAC, insecure
cookies, localhost DB, missing replica set, dev CORS origins, missing metrics
token, etc.). To check config without starting the app:

```bash
scripts/preflight.py                   # validates settings + pings data stores
```

### 1.2 Release

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

### 1.3 Post-deploy verification

```bash
scripts/post_deploy_verify.sh          # container health + smoke
SMOKE_BASE_URL=https://<host> scripts/smoke_health.py   # /health/live, /health/ready
```

Health endpoints: `GET /health/live` (process up), `GET /health/ready`
(dependencies reachable). Metrics are token-gated via `METRICS_TOKEN`.

---

## 2. Backups

### 2.1 What is backed up

| Component | How | Script |
|---|---|---|
| MongoDB (all collections) | `mongodump --gzip --archive` | `scripts/mongo_backup.sh` |
| Qdrant / FalkorDB / Redis / uploads | `BGSAVE` then volume tarball | `scripts/production_backup.sh` |
| Offsite copy | `aws s3 sync` to a backups bucket | `scripts/backup_offsite_s3.sh` |

### 2.2 Schedule (host cron)

```cron
# Nightly local + offsite backup at 01:30; logs to syslog
30 1 * * *  cd /opt/contraclaim && BACKUP_S3_BUCKET=my-backups \
  ./scripts/backup_offsite_s3.sh >> /var/log/contraclaim-backup.log 2>&1
```

### 2.3 Retention

- **Local:** `production_backup.sh` prunes files older than `RETENTION_DAYS` (default 14).
- **Offsite (S3):** enforce with an S3 **lifecycle rule** on the backups bucket
  (e.g. expire after 90 days, transition to Glacier after 30). Do not rely on
  client-side pruning for the offsite copy.

### 2.4 Verify a backup exists

```bash
aws s3 ls s3://$BACKUP_S3_BUCKET/contraclaim/backups/ --recursive | tail
```

---

## 3. Restore runbook (TESTED PROCEDURE)

> Run a restore **drill** quarterly into a throwaway environment and record the
> time taken. Untested backups are not backups.

### 3.1 Fetch the backup set

```bash
aws s3 sync s3://$BACKUP_S3_BUCKET/contraclaim/backups/<host>/ /var/backups/contractdms/
```

### 3.2 MongoDB (logical restore)

```bash
# Point DATABASE_URL at the target cluster.
scripts/mongo_restore.sh /var/backups/contractdms/mongo/<db>-<stamp>.archive.gz
```

`mongorestore` is namespace-scoped (`--nsInclude=<db>.*`). For a clean restore,
drop the target DB first or restore into an empty cluster.

### 3.3 Qdrant / FalkorDB / Redis / uploads (volume restore)

Stop the affected services first, then restore each volume:

```bash
docker compose -f docker-compose.prod.yml stop qdrant falkordb redis backend contract-worker

scripts/production_restore_volumes.sh --apply <project>_qdrant_data     /var/backups/contractdms/volumes/qdrant-data-<stamp>.tar.gz
scripts/production_restore_volumes.sh --apply <project>_falkordb_data   /var/backups/contractdms/volumes/falkordb-data-<stamp>.tar.gz
scripts/production_restore_volumes.sh --apply <project>_redis_data      /var/backups/contractdms/volumes/redis-data-<stamp>.tar.gz
scripts/production_restore_volumes.sh --apply <project>_backend_uploads /var/backups/contractdms/volumes/backend-uploads-<stamp>.tar.gz

docker compose -f docker-compose.prod.yml up -d
```

(`<project>` is the compose project name; `docker volume ls` shows the prefix.)

### 3.4 Post-restore verification

```bash
scripts/post_deploy_verify.sh
scripts/smoke_health.py
```

Spot-check: log in, open a document, run one RAG query, confirm vector search
returns results (validates Qdrant restore) and references resolve (validates
FalkorDB restore).

### 3.5 Restore ordering & RPO/RTO

- **Order:** Mongo (source of truth) → volumes → start services.
- **RPO:** = backup interval (nightly ⇒ up to 24h). Tighten with more frequent
  Mongo dumps if needed.
- **Vector/graph drift:** if Qdrant/Falkor are stale relative to Mongo, run the
  reconciler (`scripts/reconcile_vectors.py`, `/api/storage-sync/reconcile`) to
  rebuild vectors from documents rather than restoring the volume.

---

## 4. TLS

Two supported models:

1. **External termination (recommended for cloud):** terminate TLS at an ALB /
   Cloudflare / nginx in front of the `gateway`. The gateway already emits HSTS
   and the full security-header set (`config/httpd.conf`). Publish only :443 at
   the edge; forward to the gateway's :80 on a private network.
2. **In-gateway termination (single-VM pilot):** use
   `config/httpd-tls.conf.example` — copy it to `config/httpd.conf`, mount a cert
   at `conf/certs/{fullchain.pem,privkey.pem}`, and publish `443:443` + `80:80`
   in the compose `gateway` service. It redirects :80 → :443 (allowing ACME
   http-01) and serves the same CSP/HSTS headers.

Verify: `https://<host>` scores A on SSL Labs; `curl -sI https://<host>` shows
`strict-transport-security`.

---

## 5. Secrets management

- All secrets are injected as environment variables with `${VAR:?...}` guards in
  `docker-compose.prod.yml` — the stack refuses to start if any are missing.
- Source them from a secrets manager (AWS Secrets Manager / SSM / Vault) into the
  `.env` consumed by compose; do not commit `.env`. `.gitignore` already blocks it.
- Rotate `SECRET_KEY` by deploying the new value — existing sessions are
  invalidated via the JWT `min_iat` / session checks (see AUTHZ.md / Week 2).

---

## 6. Monitoring & observability

- **Health:** `/health/live`, `/health/ready` (used by compose healthchecks).
- **Metrics:** enabled by `METRICS_ENABLED`, protected by `METRICS_TOKEN`.
- **Request logs:** every request logs `request_id`, method, path, status,
  duration; requests over `SLOW_REQUEST_THRESHOLD_MS` log a `slow request` warning.
- **RAG/agent runs:** persisted to the `rag_runs` / observability collections for
  retrieval traceability.
- **Audit:** authorization decisions emit `policy.authorize` events; billing
  events land in `billing_records` + `billing_webhook_events`. Export an
  arbitration evidence pack via `GET /api/audit/export` (CSV, `dms.audit.view`).
- **Distributed tracing (OpenTelemetry, opt-in):** install the `opentelemetry-*`
  packages, set `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT=http://<collector>:4318`,
  and the backend instruments FastAPI + exports OTLP spans. When enabled, request
  logs include `trace_id=` for log↔trace correlation. Disabled by default (no-op),
  so it never affects a deployment that hasn't opted in.

### Alerting (wire your monitor to these signals)
- **5xx rate:** alert when `status_code >= 500` exceeds a threshold — derivable
  from the request middleware logs (`request completed ... status_code=5xx`) and
  the metrics surface (`METRICS_ENABLED` + `METRICS_TOKEN`).
- **Latency:** the middleware logs `slow request` for any request over
  `SLOW_REQUEST_THRESHOLD_MS`; alert on its rate.
- **Health:** alert if `/health/ready` fails (compose healthchecks already gate it).
- **Backups:** alert on a missing nightly object in the backups bucket (see §2).

---

## 6a. Scheduled (cron) jobs

The app runs six cron jobs via APScheduler: daily/weekly email digests, the SLA
deadline scan, the key-date notification scan, the BG-expiry scan and the
reference-sync reaper.

- **Single owner:** only the process with `RUN_SCHEDULER=true` runs them. In the
  prod compose this is the `contract-worker` (keep it at **1 replica**); the web
  `backend` tier sets `RUN_SCHEDULER=false` because it may be scaled to >1 replica.
- **Safety net:** every job is wrapped in a Mongo leader lock (`scheduler_locks`
  collection). Even if more than one process enables the scheduler, each job fires
  **exactly once** per schedule; a `locked_until` TTL auto-releases a crashed
  holder. `SCHEDULER_LOCK_TTL_SECONDS` (default 3600) bounds that window.
- **Verify:** check the owner's startup log for `Scheduler started: 6 leader-locked
  cron jobs`; `db.scheduler_locks` gains one short-lived doc per job while it runs.

---

## 7. Demo / pilot seed

```bash
python -m rbac_backend.initial_data.demo_seed
```

Creates a demo EPC tenant (org + project + sample correspondence + a trial
subscription with DMS/drafting enabled) for sales demos and pilot onboarding.

---

## 8. Pilot go-live checklist

- [ ] `pre_deploy_readiness.sh` passes; `.env` sourced from secrets manager
- [ ] `ENVIRONMENT=production`, `ALLOW_DEV_HEADERS=false`, `AUTH_COOKIE_SECURE=true`
- [ ] MongoDB replica set healthy (`MONGODB_REPLICA_SET` set)
- [ ] TLS A-grade; HSTS present
- [ ] Nightly offsite backup cron installed; **one restore drill executed**
- [ ] `METRICS_TOKEN` set; healthchecks green; ClamAV `CLAMAV_FAIL_OPEN=false`
- [ ] Multi-tenant isolation suite green in CI (`test_tenant_isolation.py`)
- [ ] Payment provider configured (`PAYMENT_PROVIDER`, `RAZORPAY_*`) if billing live
- [ ] Demo/pilot data seeded; pilot users provisioned with correct roles
