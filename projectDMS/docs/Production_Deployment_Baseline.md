# Production Deployment Baseline

This document captures the minimum deployment baseline for running the SaaS stack outside local development.

## Required Runtime Mode

Set:

```env
ENVIRONMENT=production
ENABLE_API_DOCS=false
ALLOW_DEV_HEADERS=false
```

Production startup fails if:

- `ALLOW_DEV_HEADERS=true`
- `DATABASE_URL` points to `localhost` or `127.0.0.1`
- `CORS_ORIGINS` contains development origins
- `SECRET_KEY` is shorter than 32 characters
- `LANGGRAPH_ENABLED=true` and `LANGGRAPH_API_TOKEN` is missing
- critical settings still use placeholders

## Health Endpoints

- `/health/live`: process liveness only.
- `/health/ready`: readiness check for MongoDB, Redis-backed contract queue, local storage config, and runtime configuration.
- `/health`: compatibility endpoint for older probes.

Use `/health/ready` for container readiness and load-balancer target health.

## Secrets

Do not copy `.env` files into images or source control.

Use a secret manager or orchestrator-native secrets for:

- `DATABASE_URL`
- `SECRET_KEY`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_BUCKET_NAME`
- `OPENAI_API_KEY`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `QDRANT_API_KEY`
- `LANGGRAPH_API_TOKEN`

All previously exposed credentials must be rotated before production use.

## Network Exposure

Only expose the public gateway or load balancer.

Do not expose these services publicly:

- MongoDB
- Redis
- Qdrant
- FalkorDB

They should be reachable only on private service networks.

## Deployment Flow

1. Build immutable backend and frontend images tagged by commit SHA.
2. Run tests and scans in CI.
3. Push images to registry.
4. Deploy to staging.
5. Run smoke tests against `/health/ready` and critical API routes.
6. Deploy to production using rolling or blue/green deployment.
7. Roll back to the previous image tag if readiness or smoke tests fail.

See `docs/Release_Runbook.md` for the release and rollback checklist.

## Minimum Smoke Tests

- `GET /health/live` returns 200.
- `GET /health/ready` returns 200.
- Login succeeds for a known test user in staging.
- Document list endpoint returns authorized data.
- Small document upload succeeds.
- Contract upload session can be created.

Health-only smoke check:

```bash
SMOKE_BASE_URL=https://staging.example.com python scripts/smoke_health.py
```

## Logging

The backend adds an `X-Request-ID` header to responses and logs request method, path, status code, latency, and request ID.

Forward container logs to centralized logging in production.
