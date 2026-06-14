# Production Readiness Phase Plan

This plan tracks the repository changes and the server-side work required before
promoting the Docker Compose deployment to production.

## Phase 0 - Repository Deployability

Status: implemented in repo.

- Replace the corrupted legacy metadata module with a maintained compatibility wrapper.
- Require backend compile checks before deployment.
- Make base Compose render even when optional local `backend/.env` is absent.
- Replace the production Compose overlay with a self-contained production file.
- Keep only the gateway port public in production Compose.
- Fix Apache gateway config and preserve `/api/*` paths when proxying to FastAPI.
- Install `curl` in Graphiti and LangGraph images so container health checks work.

Required gate:

```bash
python -m compileall -q backend/rbac_backend
docker compose --env-file .env -f docker-compose.prod.yml config
docker run --rm -v "$PWD/config/httpd.conf:/usr/local/apache2/conf/httpd.conf:ro" -v "$PWD/config/health.txt:/usr/local/apache2/htdocs/health.txt:ro" httpd:2.4 httpd -t
```

## Phase 1 - Secrets And Environment

Status: partially implemented in repo; server values must be created and rotated.

- Use root `.env` as the production environment source.
- Set `ENVIRONMENT=production`, `ENABLE_API_DOCS=false`, and `ALLOW_DEV_HEADERS=false`.
- Use a production MongoDB replica-set URI in `DATABASE_URL`.
- Set strong values for `SECRET_KEY`, `REDIS_PASSWORD`, `FALKORDB_PASSWORD`, `QDRANT_API_KEY`, `LANGGRAPH_API_TOKEN`, `METRICS_TOKEN`, SMTP credentials, AWS/S3 credentials, and SMTP settings encryption key.
- Rotate any credentials that were ever committed, copied into chat, or used in local files.
- Restrict `.env`, `backend/.env`, and `config/secrets/*` permissions to the deploy user.

Required gate:

```bash
chmod 600 .env config/secrets/qdrant_api_key
./scripts/pre_deploy_readiness.sh
```

## Phase 2 - Data Durability

Status: partially implemented in repo; server backup drills are required.

- Use the existing self-managed MongoDB replica set; do not run standalone MongoDB in production Compose.
- Mount backend uploads on the production `backend_uploads` Docker volume.
- Confirm whether S3 is primary or local is primary for each organization/project.
- Run `scripts/production_backup.sh` on a schedule.
- Copy backups off-host.
- Test restore for MongoDB and every application data volume before launch.

Required gate:

```bash
BACKUP_ROOT=/var/backups/contractdms ./scripts/production_backup.sh
./scripts/mongo_restore.sh /path/to/test/archive.gz
./scripts/production_restore_volumes.sh --apply contract-ai_backend_uploads /path/to/backend-uploads.tar.gz
```

Run destructive restore drills only against staging or an isolated restore host.

## Phase 3 - Private Networking And TLS

Status: production Compose private networks implemented; server firewall/TLS still required.

- Keep MongoDB, Redis, FalkorDB, Qdrant, Graphiti, LangGraph, backend, and client off public interfaces.
- Allow public traffic only to the gateway or an upstream load balancer.
- Terminate TLS at a reverse proxy, load balancer, Caddy, Nginx, Traefik, or a hardened Apache TLS config.
- Set `PUBLIC_BASE_URL`, `PUBLIC_API_URL`, `APP_URL`, and `CORS_ORIGINS` to HTTPS origins.

Required gate:

```bash
sudo ufw status verbose
sudo ss -lntp
curl -fsS https://YOUR_DOMAIN/health
curl -fsS https://YOUR_DOMAIN/api/health/ready
```

## Phase 4 - Observability And Incident Response

Status: in-app metrics and request IDs implemented; dashboards are still required.

- Scrape `/metrics` with `X-Metrics-Token`.
- Forward Docker logs to centralized logging.
- Create alerts for readiness failures, 5xx rate, slow request rate, Redis failures, Mongo replica health, upload failures, queue backlog, disk usage, and backup failures.
- Document on-call ownership and rollback authority.

Required gate:

```bash
curl -fsS -H "X-Metrics-Token: $METRICS_TOKEN" http://localhost:8000/metrics
docker compose --env-file .env -f docker-compose.prod.yml logs --since=15m backend
```

## Phase 5 - Release And Rollback

Status: repo has scripts/runbooks; zero-downtime still requires deployment architecture.

- Build immutable images tagged by commit SHA.
- Deploy to staging first.
- Run health smoke checks and one authenticated document upload smoke.
- Keep previous image tags available.
- For Docker Compose on a single host, use a maintenance window unless an external load balancer and blue/green Compose projects are configured.

Required gate:

```bash
./scripts/post_deploy_verify.sh
SMOKE_BASE_URL=https://YOUR_DOMAIN python scripts/smoke_health.py
```

## Phase 6 - Zero-Downtime Upgrade Path

Status: not complete.

Docker Compose can be made near-zero-downtime only with additional infrastructure:

- Run two Compose project names, for example `contract-ai-blue` and `contract-ai-green`.
- Put a host-level reverse proxy or load balancer in front of both stacks.
- Share external MongoDB, Redis, object storage, and vector/graph persistence safely.
- Deploy the idle color, wait for `/health/ready`, switch traffic, then drain the old color.
- Keep schema and data changes backward compatible across both versions.

Until that exists, production deploys should be treated as controlled maintenance-window deploys.
