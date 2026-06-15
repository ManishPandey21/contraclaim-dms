# Production Deployment Readiness Check - 03 May 2026

Use this document before deploying the updated repository to an Ubuntu production server.
python -c "import secrets; print(secrets.token_urlsafe(48))"python -c "import secrets; print(secrets.token_urlsafe(48))"

## Prompt To Run A Production Readiness Review

Copy this prompt into Codex/ChatGPT when you want a fresh deployment readiness audit:

```text
You are a senior DevOps, FastAPI, MongoDB, Redis, Docker Compose, and production incident-response architect.

Audit this repository for production deployment readiness after the Phase 0-7 updates.

Context:
- Backend: FastAPI.
- Database: self-managed MongoDB; do not recommend moving to MongoDB Atlas.
- Runtime state and queues: Redis.
- Document storage: local/S3 file object architecture.
- Vector/graph services: Qdrant, FalkorDB, Graphiti, LangGraph.
- Deployment target: Ubuntu server with Docker Compose.
- Recent updates added health/readiness endpoints, Redis runtime state, upload streaming, MongoDB hardening, metrics, audit events, optional document optimistic concurrency, and Mongo backup/restore scripts.

Tasks:
1. Verify whether this repo can be deployed safely to production today.
2. Check Docker Compose, env files, secrets handling, health checks, metrics, logging, MongoDB replica-set readiness, Redis, file storage, backup/restore, upload performance, and rollback readiness.
3. Identify any blockers that must be fixed before deployment.
4. Identify warnings that can be accepted for a controlled staging deployment.
5. Give an overall deployment readiness score out of 10.
6. Provide a go/no-go decision.
7. Provide exact commands to run before deploy, during deploy, and after deploy on Ubuntu.
8. Confirm whether the deployment is zero-downtime compatible and what manual steps are still required.

Be strict. Do not assume any secret, backup, TLS, firewall, dashboard, or restore drill exists unless the repository or server evidence proves it.
```

## Deployment Go/No-Go Gates

Do not deploy to production until all required gates pass:

- No real secrets are committed or copied into images.
- All production secrets are rotated and stored outside Git.
- `ENVIRONMENT=production` is set for backend containers.
- `ENABLE_API_DOCS=false` in production unless there is a controlled internal need.
- `METRICS_TOKEN` is set if `METRICS_ENABLED=true`.
- `DATABASE_URL` points to a self-managed MongoDB replica set, not localhost.
- `MONGODB_REPLICA_SET` is set, or the connection string contains `replicaSet=...`.
- MongoDB auth/TLS/keyfile is enabled for any multi-host or externally reachable deployment.
- MongoDB backup script runs successfully and a restore drill has been tested.
- Internal data ports are not exposed publicly: MongoDB, Redis, Qdrant, FalkorDB.
- `/health/live`, `/health/ready`, `/health/observability`, and `/metrics` work.
- API, worker, and frontend containers start from the intended image/build.
- Rollback command and previous image/tag are known.

## Ubuntu Deployment Steps

### 1. Prepare Server

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y ca-certificates curl gnupg git unzip ufw jq
```

Install Docker and Docker Compose plugin:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
newgrp docker
```

### 2. Lock Down Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

Do not allow public inbound access to:

- `27017` MongoDB
- `6379` Redis
- `6380` FalkorDB
- `6333`/`6334` Qdrant
- `8000` backend, unless it is behind a private load balancer or temporary internal maintenance access

### 3. Deploy Code

```bash
sudo mkdir -p /opt/contractdms
sudo chown "$USER":"$USER" /opt/contractdms
cd /opt/contractdms
git clone YOUR_REPOSITORY_URL .
```

For an update:

```bash
cd /opt/contractdms
git fetch --all --prune
git checkout main
git pull --ff-only
```

### 4. Configure Environment

Create environment files:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp client/.env.production.example client/.env.production 2>/dev/null || touch client/.env.production
mkdir -p config/secrets
```

Required production values:

```env
ENVIRONMENT=production
ENABLE_API_DOCS=false
DATABASE_URL=mongodb://mongo1:27017,mongo2:27017,mongo3:27017/contraclaim?replicaSet=rs0&retryWrites=true
MONGODB_DATABASE=contraclaim
MONGODB_REPLICA_SET=rs0
MONGODB_ALLOW_STANDALONE_PRODUCTION=false
APP_REDIS_URL=redis://redis:6379/1
RUNTIME_STATE_REDIS_URL=redis://redis:6379/1
CONTRACT_QUEUE_REDIS_URL=redis://redis:6379/0
METRICS_ENABLED=true
METRICS_TOKEN=replace-with-long-random-token
SECRET_KEY=replace-with-32-plus-char-secret
LANGGRAPH_API_TOKEN=replace-with-long-random-token
QDRANT_API_KEY=replace-with-long-random-token
REDIS_PASSWORD=replace-with-long-random-token
```

Create Qdrant secret:

```bash
grep '^QDRANT_API_KEY=' .env | cut -d= -f2- > config/secrets/qdrant_api_key
chmod 600 config/secrets/qdrant_api_key
```

### 5. Pre-Deploy Validation

Run:

```bash
chmod +x scripts/pre_deploy_readiness.sh
./scripts/pre_deploy_readiness.sh
```

Fix every `FAIL` before production deployment.

### 6. Backup Before Deploy

If this is an existing production server:

```bash
MONGO_URI="$DATABASE_URL" MONGO_DB="${MONGODB_DATABASE:-contraclaim}" BACKUP_DIR=/backups/mongo ./scripts/mongo_backup.sh
./scripts/backup.sh /backups/volumes
```

### 7. Deploy

For the current Compose stack:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

For the self-managed MongoDB replica-set overlay:

```bash
docker compose --env-file .env -f docker-compose.mongo-replicaset.yml up -d
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Make sure backend/worker `DATABASE_URL` points to `mongo1,mongo2,mongo3` when using the replica-set overlay.

### 8. Post-Deploy Verification

Run:

```bash
chmod +x scripts/post_deploy_verify.sh
./scripts/post_deploy_verify.sh
```

Then manually verify:

- Login works.
- Document list loads.
- Upload one small incoming document.
- Upload one small contract.
- Download the uploaded document.
- Check document audit events.
- Check `/metrics` through an internal network path.

### 9. Rollback

Rollback triggers:

- `/health/ready` remains failing after retries.
- Elevated 5xx rate.
- Uploads fail.
- Contract queue does not drain.
- MongoDB replica-set failure or data corruption signs.

Rollback command:

```bash
git checkout PREVIOUS_KNOWN_GOOD_COMMIT
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml up -d --build
./scripts/post_deploy_verify.sh
```

If immutable images are used, redeploy the previous image tag instead of rebuilding locally.

## Required Scripts

- Run before deployment: `scripts/pre_deploy_readiness.sh`
- Run after deployment: `scripts/post_deploy_verify.sh`
- Run before production update: `scripts/mongo_backup.sh`
- Run for full service volume backup where applicable: `scripts/backup.sh`

