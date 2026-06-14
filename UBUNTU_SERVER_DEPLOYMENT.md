# Ubuntu Server Deployment Guide

This guide explains how to deploy the ContractDMS / ContraClaim stack on an Ubuntu server using Docker Compose.

The repository is designed to run these services:

- FastAPI backend on port `8000`
- React/Vite client served by the `client` container
- MongoDB
- Qdrant
- FalkorDB
- Redis
- Graphiti service
- LangGraph service
- Apache HTTP gateway on port `80`

## 1. Server Requirements

Recommended minimum server:

- Ubuntu 22.04 LTS or 24.04 LTS
- 4 CPU cores
- 8 GB RAM minimum, 16 GB preferred
- 80 GB disk minimum
- Public IP or private network access
- Ports open: `22`, `80`, optionally `443`

## 2. Connect to the Server

```bash
ssh ubuntu@YOUR_SERVER_IP
```

Update the system:

```bash
sudo apt update
sudo apt upgrade -y
```

Install basic tools:

```bash
sudo apt install -y ca-certificates curl gnupg git unzip ufw nano
```

## 3. Install Docker and Docker Compose

Remove old Docker packages if present:

```bash
sudo apt remove -y docker docker-engine docker.io containerd runc || true
```

Add Docker's official repository:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

Install Docker:

```bash
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Allow your user to run Docker:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

Verify:

```bash
docker --version
docker compose version
```

## 4. Configure Firewall

Allow SSH and HTTP:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw enable
sudo ufw status
```

If HTTPS is configured later:

```bash
sudo ufw allow 443/tcp
```

Avoid exposing database ports publicly unless there is a specific operational need.

## 5. Get the Code

Choose a deployment directory:

```bash
sudo mkdir -p /opt/contractdms
sudo chown "$USER":"$USER" /opt/contractdms
cd /opt/contractdms
```

Clone the repository:

```bash
git clone YOUR_REPOSITORY_URL .
```

Or copy the project folder to `/opt/contractdms` using `scp`, `rsync`, or your CI/CD pipeline.

## 6. Configure Environment Files

This project uses more than one environment file:

- Root `.env` for Docker Compose and shared services
- `backend/.env` for the FastAPI backend container
- `client/.env.production` for the Vite production build
- `config/secrets/qdrant_api_key` for the Qdrant Docker secret

Create the root `.env`:

```bash
cp .env.example .env
nano .env
```

Set strong production values. At minimum, review:

```env
COMPOSE_PROJECT_NAME=contract-ai
PUBLIC_BASE_URL=http://YOUR_DOMAIN_OR_SERVER_IP
OPENAI_API_KEY=replace-with-real-key
QDRANT_API_KEY=replace-with-strong-random-value
REDIS_PASSWORD=replace-with-strong-random-value
FALKORDB_PASSWORD=replace-with-strong-random-value
LANGGRAPH_API_TOKEN=replace-with-strong-random-value
MONGODB_URI=mongodb://mongo:27017/contractdms
```

Create backend environment:

```bash
cp backend/.env.example backend/.env
nano backend/.env
```

For Docker deployment, use container hostnames, not localhost:

```env
DATABASE_URL=mongodb://mongo:27017
SECRET_KEY=replace-with-long-random-secret
CORS_ORIGINS=http://YOUR_DOMAIN_OR_SERVER_IP
UPLOADS_DIR=uploads
OPENAI_API_KEY=replace-with-real-key
APP_URL=http://YOUR_DOMAIN_OR_SERVER_IP
```

If your backend expects `MONGODB_URI` instead of `DATABASE_URL`, keep both values:

```env
DATABASE_URL=mongodb://mongo:27017
MONGODB_URI=mongodb://mongo:27017/contractdms
```

Configure frontend production API URL:

```bash
nano client/.env.production
```

For gateway-based deployment, use:

```env
VITE_API_BASE_URL=http://YOUR_DOMAIN_OR_SERVER_IP/api
```

If HTTPS is configured:

```env
VITE_API_BASE_URL=https://YOUR_DOMAIN/api
```

Important: Vite reads `VITE_*` variables at build time. Update `client/.env.production` before building the Docker image.

Create Qdrant secret:

```bash
mkdir -p config/secrets
grep '^QDRANT_API_KEY=' .env | cut -d= -f2- > config/secrets/qdrant_api_key
chmod 600 config/secrets/qdrant_api_key
```

## 7. Validate Docker Compose Configuration

From the repository root:

```bash
docker compose --env-file .env config
```

If this command fails, fix the reported environment or YAML issue before continuing.

## 8. Build and Start Services

Development-style deployment:

```bash
docker compose --env-file .env up -d --build
```

Production deployment:

```bash
docker compose --env-file .env -f docker-compose.prod.yml up -d --build
```

Alternative using the included script:

```bash
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

Note: `scripts/deploy.sh` uses `docker-compose.yml`. If you want the production overlay, use the explicit production command above.

## 9. Verify Deployment

Check container status:

```bash
docker compose --env-file .env ps
```

Check gateway:

```bash
curl -i http://localhost/health
```

Check backend through gateway:

```bash
curl -i http://localhost/api/health
```

Check backend directly:

```bash
curl -i http://localhost:8000/health
```

Check service logs:

```bash
docker compose --env-file .env logs -f gateway
docker compose --env-file .env logs -f backend
docker compose --env-file .env logs -f client
docker compose --env-file .env logs -f langgraph
```

Open the application:

```text
http://YOUR_DOMAIN_OR_SERVER_IP/
```

## 10. Initial Data and Service Checks

Check MongoDB:

```bash
docker compose --env-file .env exec mongo mongosh --quiet --eval "db.adminCommand('ping')"
```

Check Redis:

```bash
docker compose --env-file .env exec redis redis-cli -a "$REDIS_PASSWORD" ping
```

Check FalkorDB:

```bash
docker compose --env-file .env exec falkordb redis-cli -h localhost ping
```

Check Qdrant:

```bash
curl -H "api-key: $(cat config/secrets/qdrant_api_key)" http://localhost:6333/collections
```

## 11. Update Existing Deployment

Pull latest code:

```bash
cd /opt/contractdms
git pull
```

If frontend env changed, confirm `client/.env.production` before rebuilding.

Rebuild and restart:

```bash
docker compose --env-file .env -f docker-compose.prod.yml up -d --build
```

Remove unused images after successful deployment:

```bash
docker image prune -f
```

## 12. Stop or Restart Services

Restart all services:

```bash
docker compose --env-file .env restart
```

Restart one service:

```bash
docker compose --env-file .env restart backend
```

Stop services without deleting volumes:

```bash
docker compose --env-file .env down
```

Stop services and delete volumes only if you intentionally want to remove data:

```bash
docker compose --env-file .env down -v
```

Use `down -v` carefully because it removes MongoDB, Qdrant, FalkorDB, and Redis data volumes.

## 13. Backups

Use the included production backup script:

```bash
chmod +x scripts/production_backup.sh
BACKUP_ROOT=/var/backups/contractdms ./scripts/production_backup.sh
```

Restore from backup:

```bash
chmod +x scripts/mongo_restore.sh scripts/production_restore_volumes.sh
./scripts/mongo_restore.sh /path/to/mongo/archive.gz
./scripts/production_restore_volumes.sh --apply contract-ai_backend_uploads /path/to/backend-uploads.tar.gz
```

Before restore, stop the stack:

```bash
docker compose --env-file .env down
```

Start it again after restore:

```bash
docker compose --env-file .env up -d
```

## 14. Optional HTTPS Setup

The current Compose stack exposes Apache HTTP on port `80`. For HTTPS, use one of these approaches:

- Put Nginx Proxy Manager, Caddy, Traefik, or cloud load balancer in front of this stack.
- Terminate TLS at your cloud provider load balancer.
- Replace or extend `config/httpd.conf` with TLS certificate configuration.

When HTTPS is enabled, update:

```env
PUBLIC_BASE_URL=https://YOUR_DOMAIN
APP_URL=https://YOUR_DOMAIN
VITE_API_BASE_URL=https://YOUR_DOMAIN/api
CORS_ORIGINS=https://YOUR_DOMAIN
```

Then rebuild the client:

```bash
docker compose --env-file .env -f docker-compose.prod.yml up -d --build client gateway backend
```

## 15. Common Troubleshooting

### Containers fail to start

```bash
docker compose --env-file .env ps
docker compose --env-file .env logs --tail=200 backend
docker compose --env-file .env logs --tail=200 gateway
```

### Port already in use

```bash
sudo ss -tulpn | grep ':80'
sudo ss -tulpn | grep ':8000'
```

Stop the conflicting service or change the mapped port in `docker-compose.yml`.

### Frontend cannot reach backend

Confirm `client/.env.production` contains the correct URL:

```env
VITE_API_BASE_URL=http://YOUR_DOMAIN_OR_SERVER_IP/api
```

Rebuild the client:

```bash
docker compose --env-file .env up -d --build client gateway
```

### Backend cannot reach MongoDB

Inside Docker, use `mongo` as the hostname:

```env
DATABASE_URL=mongodb://mongo:27017
MONGODB_URI=mongodb://mongo:27017/contractdms
```

Then restart backend:

```bash
docker compose --env-file .env restart backend
```

### Qdrant API key mismatch

Ensure root `.env` and `config/secrets/qdrant_api_key` contain the same value:

```bash
grep '^QDRANT_API_KEY=' .env
cat config/secrets/qdrant_api_key
```

If changed, restart Qdrant and dependent services:

```bash
docker compose --env-file .env restart qdrant backend graphiti langgraph
```

## 16. Deployment Checklist

- Docker and Docker Compose installed
- Firewall allows `22` and `80`
- Code is present in `/opt/contractdms`
- Root `.env` configured
- `backend/.env` configured with Docker hostnames
- `client/.env.production` configured before build
- `config/secrets/qdrant_api_key` created
- `docker compose config` passes
- Stack starts successfully
- `curl http://localhost/api/health` returns success
- Browser opens `http://YOUR_DOMAIN_OR_SERVER_IP/`
- Backup process tested

## 17. Updated Production Readiness Flow

After the Phase 0-7 production-readiness updates, use the stricter deployment checklist in:

```text
Production_Deployment_Readiness_Check_03052026.md
```

Before production deployment, run:

```bash
chmod +x scripts/pre_deploy_readiness.sh
./scripts/pre_deploy_readiness.sh
```

After deployment, run:

```bash
chmod +x scripts/post_deploy_verify.sh
./scripts/post_deploy_verify.sh
```

Important production requirements added by the latest phases:

- `ENVIRONMENT=production`
- `ENABLE_API_DOCS=false`
- `METRICS_TOKEN` when `METRICS_ENABLED=true`
- MongoDB replica-set connection string for production
- Runtime Redis URL for sessions, cache, lockouts, and rate limits
- No public exposure of MongoDB, Redis, Qdrant, or FalkorDB ports
- MongoDB backup before deployment and restore drill before launch

The older single-node MongoDB Compose service is acceptable only for local development or controlled staging. Production should use a self-managed replica set or an equivalent production MongoDB deployment.
