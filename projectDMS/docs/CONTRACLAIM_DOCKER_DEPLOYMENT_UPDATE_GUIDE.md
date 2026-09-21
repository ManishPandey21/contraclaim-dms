# ContraClaim Docker Deployment Update Guide

This guide explains how to update the ContraClaim Docker deployment on the Ubuntu production server.

Use it as a deployment checklist. Do not overwrite production configuration, environment files, uploaded documents, database volumes, backup directories, or runtime data.

> **CURRENT PRODUCTION FACTS (R-A9H, 2026-09-21) — read these first.**
>
> 1. **Branch.** Production tracks `release/contraclaim-rc1`, not `main`. Read
>    `git branch --show-current` on the server; §5 below says `main` and is wrong for now.
> 2. **Compose file set.** `docker-compose.prod.yml` + `docker-compose.mongo-replicaset.yml`.
>    The R-A8Z ClamAV override was **retired** on 2026-09-20; never add it.
> 3. **FalkorDB is in a temporary out-of-band topology** until normalization. Never run a
>    blanket `docker compose up -d`/`start`, and always pass `--no-deps` when recreating
>    `backend`, `contract-worker` or `document-worker` — each `depends_on` the compose
>    `falkordb` service, which is the preserved rollback container and must stay stopped.
>    See `PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` (status banner).
> 4. **Application services are four:** `backend` (web only), `contract-worker` (contract
>    queue + the only scheduler), `document-worker` (the only extraction owner, `legacy_v0`),
>    `client`. `document-worker-canary` runs **0** replicas outside an authorised canary.
> 5. **A certified release is retagged, not rebuilt** (§6).
>
> ~~TEMPORARY WARNING — PRODUCTION CLAMAV OVERRIDE (since R-A8Z, 2026-09-14).~~ *Retired
> 2026-09-20; kept below as history.*
> **UNTIL FULL CUTOVER, EVERY PRODUCTION COMPOSE COMMAND THAT CAN RECREATE CLAMAV
> MUST INCLUDE THE R-A8Z CLAMAV OVERRIDE:**
>
> ```bash
> docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml -f docker-compose.clamav-r-a8z.yml <command>
> ```
>
> The override is an untracked file in `/opt/contraclaim-dms/projectDMS`. Without
> it compose renders the old `clamav/clamav:1.4` service (no egress, no database
> volume) and recreates it, reinstating stale virus signatures. Details and the
> measured state: [CLAMAV_SIGNATURE_FRESHNESS.md](CLAMAV_SIGNATURE_FRESHNESS.md).

## 1. Connect to the Production Server

Connect using the configured SSH alias:

```bash
ssh contraclaim
```

Go to the production checkout:

```bash
cd /opt/contraclaim-dms/projectDMS
```

## 2. Confirm the Repository and GitHub Access

Use the GitHub CLI to confirm that the production server can access the correct repository:

```bash
gh auth status
gh repo view ManishPandey21/contraclaim-dms
```

Confirm that the local checkout points to the same repository and is on the expected branch:

```bash
git remote -v
git branch --show-current
git status --short
```

Expected state:

```text
Repository: ManishPandey21/contraclaim-dms
Branch: main
Git status: clean, or only known production-only untracked config/data files
```

If tracked files are modified, stop and review them before pulling. Do not discard production changes unless they are fully understood and backed up.

## 3. Review the Incoming Code

Fetch the latest commit metadata from GitHub:

```bash
git fetch origin main
```

Review incoming commits:

```bash
git log --oneline -n 10 HEAD..origin/main
git diff --stat HEAD..origin/main
```

If the incoming change includes database migrations, storage changes, queue changes, Docker volume changes, search/vector/graph changes, or unclear persistence changes, take and verify a backup before updating the checkout.

## 4. Verify or Take a Backup

Load the production environment values:

```bash
set -a
source .env
set +a
```

Check the latest backup status:

```bash
python3 scripts/backup_status.py \
  --root "${BACKUP_ROOT:-/var/backups/contractdms}" \
  --max-age-hours "${BACKUP_MAX_AGE_HOURS:-26}"
```

Confirm that the backup status is acceptable for the deployment. At minimum, verify the backup manifest and any data stores affected by the release, such as:

```text
MongoDB
Uploaded documents
Qdrant
FalkorDB
Redis
Backup manifest
```

If the backup is stale, missing, or incomplete, take a fresh backup using the project backup procedure, then rerun `backup_status.py` and confirm it passes before continuing.

## 5. Pull the Latest Code

Use a fast-forward pull so production does not create a merge commit:

```bash
git pull --ff-only origin main
```

Record the deployed commit hash:

```bash
git rev-parse HEAD
```

If `git pull --ff-only` fails, stop and inspect the cause. Do not use force reset or overwrite local files unless the impact is reviewed and approved.

## 6. Rebuild the Required Docker Images

> **A certified release is NOT rebuilt.** When the release programme has certified image ids
> (the production manifest records them), retag those ids onto the compose image names and
> start with `--no-build`; a rebuild yields a different, uncertified image:
>
> ```bash
> docker tag <certified-backend-id> contraclaim-backend:latest
> docker tag <certified-backend-id> contraclaim-contract-worker:latest
> docker tag <certified-backend-id> contraclaim-document-worker:latest
> docker tag <certified-client-id>  contraclaim-client:latest
> docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
>   up -d --no-deps --no-build backend contract-worker document-worker client
> ```
>
> The build path below is for ad-hoc fixes outside a certified release. Wherever it lists
> `backend contract-worker`, **add `document-worker`**: it runs the same backend image and
> owns document extraction. Leaving it out is how R-A9G reached `post_deploy_verify.sh` with
> no extraction owner.

Rebuild only the services affected by the release.

For a standard frontend and backend application update:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  build backend contract-worker client
```

For frontend-only changes:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  build client
```

For backend or worker-only changes:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  build backend contract-worker
```

If Docker Compose or infrastructure service definitions changed, review the diff first and include only the affected services.

## 7. Restart the Updated Containers

Restart the rebuilt application containers without recreating dependencies:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps backend contract-worker client
```

For frontend-only changes:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps client
```

For backend or worker-only changes:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps backend contract-worker
```

Avoid recreating database or data services unless the release explicitly requires it and a verified backup exists.

## 8. Confirm Container Health

Check the Docker Compose service table:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  ps
```

Check restart counts and OOM status:

```bash
docker inspect -f '{{.Name}} restarts={{.RestartCount}} oom={{.State.OOMKilled}} status={{.State.Status}}' \
  contraclaim-backend-1 \
  contraclaim-client-1 \
  contraclaim-contract-worker-1 \
  contraclaim-mongo1-1 \
  contraclaim-mongo2-1 \
  contraclaim-mongo3-1 \
  contraclaim-falkordb-1 \
  contraclaim-qdrant-1 \
  contraclaim-redis-1 \
  contraclaim-clamav-1
```

Expected state:

```text
Application containers are running
Healthchecked services are healthy
Restart counts are not increasing
OOMKilled is false
```

## 9. Review Logs

Review recent logs for the updated services:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  logs --tail=200 backend contract-worker client
```

If gateway or proxy configuration changed, include the gateway logs:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  logs --tail=200 gateway
```

Investigate any of these patterns before marking the deployment healthy:

```text
Traceback
Unhandled
Exception
CRITICAL
ERROR
connection refused
authentication failed
migration failed
```

## 10. Confirm Backend Readiness

Call the backend readiness endpoint from inside the Docker network:

```bash
backend_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' contraclaim-backend-1 | sed -n '1p')
curl -fsS "http://$backend_ip:8000/health/ready"
```

The readiness response should report required dependencies as available, including MongoDB, Redis, Qdrant, FalkorDB, storage, configuration, and ClamAV if enabled.

## 11. Confirm the Public Application

Check the public health endpoint:

```bash
curl -ksSI https://web.contraclaim.com/health
```

Check the public frontend:

```bash
curl -ksSI https://web.contraclaim.com/
```

Expected result:

```text
HTTP/2 200
```

## 12. Record the Deployment Result

Capture the final deployed commit:

```bash
git rev-parse HEAD
```

Capture the final service state:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  ps
```

Deployment notes should include:

```text
Repository: ManishPandey21/contraclaim-dms
Deployed commit: <commit hash>
Backup status: verified / not required, with reason
Rebuilt services: backend, contract-worker, client, etc.
Restarted services: backend, contract-worker, client, etc.
Service health: pass/fail
Public URL check: pass/fail
Errors encountered: none, or exact errors and resolution
```

## 13. Rollback Guidance

If the deployment fails and the issue cannot be fixed quickly:

1. Keep the failed commit hash and logs for investigation.
2. Confirm that the previous good commit is known.
3. Confirm that no migration or data change prevents rollback.
4. Checkout the previous good commit only after the data impact is reviewed.
5. Rebuild and restart the same services using the steps above.
6. Recheck logs, readiness, and the public application.

Do not restore a database backup unless the release changed data and rollback requires restoring data to a previous state.
