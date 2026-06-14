# AI-Powered Contract Management Stack

This project bundles the supporting services required for semantic contract management and AI-assisted workflows. The stack includes Qdrant for vector search, FalkorDB-backed Graphiti knowledge graphs, Docling OCR, LangGraph orchestration, Redis caching, and an nginx gateway.

## Prerequisites
- Docker Engine 24+ and Docker Compose v2
- ash shell for the helper scripts
- Optional: OpenAI account for AI drafting capabilities

## Quick Start
1. Clone or copy this directory.
2. Create your environment file:
   `ash
   cd project
   cp .env.example .env
   # edit .env with secure values
   mkdir -p config/secrets
   echo "your-qdrant-api-key" > config/secrets/qdrant_api_key
   `
3. Run the deployment script:
   `ash
   ./scripts/deploy.sh
   `
4. Verify services:
   `ash
   docker compose ps
   curl http://localhost/health
   `

## Initial Database Setup
- **Qdrant**: Create collections after the first start (example shown in config/collections.http if required). Use the API key stored in config/secrets/qdrant_api_key.
- **FalkorDB**: Load your schema or seed data using GRAPH.QUERY calls via redis-cli or integration scripts.

## Backup & Restore
- Backup volumes: ./scripts/backup.sh [backup-dir]
- Restore from backup: ./scripts/restore.sh backups/<timestamp>

Backups are stored as .tar.gz archives of each Docker volume.

## Production Deployment
Use the production overlay file:
`ash
docker compose --env-file ./.env -f docker-compose.yml -f docker-compose.prod.yml up -d
`
This enables replica scaling and resource limits for Graphiti and LangGraph.

## Troubleshooting
- Validate configuration with docker compose config.
- Inspect logs via docker compose logs <service> or view rotated files in the mounted log volumes.
- Ensure ports 80, 6333, 6379, 6380, 8080, 8081, and 9000 are free.

## Directory Overview
- services/ contains Docker build contexts for Python services.
- config/nginx/nginx.conf controls API routing.
- data/ reserved for optional bind mounts or offline ingestion.
- scripts/ holds helper automation for deploy/backup/restore.
- docker-compose*.yml orchestrate the stack for dev and production.

## Security Notes
- Store secrets outside the repository. The Compose file reads the Qdrant API key from config/secrets/qdrant_api_key.
- Set strong passwords for Redis and FalkorDB in .env.
- Configure HTTPS by replacing nginx with a TLS-enabled proxy (e.g., using Let's Encrypt certbot sidecar).

