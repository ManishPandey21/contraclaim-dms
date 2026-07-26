# ContractDMS Docker Stack

This directory contains the self-contained Docker environment for the ContractDMS platform. It bundles the backend API, frontend client, LangGraph orchestration layer, document/OCR pipeline, and supporting data services so that the entire solution can be run with a single docker compose up command.

## Structure

- ackend/ � FastAPI backend copied from the main repository
- client/ � React front-end copy
- services/ � Custom microservices (Graphiti, Docling, LangGraph)
- config/ � Reverse proxy configuration and secrets placeholder
- scripts/ � Helper automation for deployment and backups
- docker-compose.yml � Primary compose file
- docker-compose.prod.yml � Production overrides
- .env.example � Environment template (copy to .env to configure)

## Getting Started

`ash
cd project
cp .env.example .env
# populate secrets, DB URLs, etc.
echo "your-qdrant-api-key" > config/secrets/qdrant_api_key
./scripts/deploy.sh
`

Once the containers start:
- Frontend: http://localhost/
- Backend API: http://localhost/api/
- LangGraph orchestrator: http://localhost/langgraph/
- Qdrant dashboard/API: http://localhost:6333

## Development Notes
- Python services use Python 3.12. Create project virtual environments explicitly with `py -3.12 -m venv backend/.venv` on Windows or `python3.12 -m venv backend/.venv` on Ubuntu; do not change the operating system's default `python3` interpreter.
- The backend build uses ackend/.env. Adjust for Docker context as required.
- To rebuild after code changes, run docker compose build backend client.
- WebSocket updates are available at ws://localhost/ws/<job_id> once a workflow is running.
- Marker CLI is required for Marker-based contract ingestion; install it separately and set `MARKER_CMD` (or set `MARKER_ENABLED=false` to disable). See [Marker CLI setup](docs/history/marker-cli-setup.md).

## Backups
`
./scripts/backup.sh            # saves snapshots under project/backups/
./scripts/restore.sh backups/<timestamp>
`

## Production Deployment
`
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml up -d
`

Customize resource limits, security headers, and TLS termination in config/httpd.conf for production use.
