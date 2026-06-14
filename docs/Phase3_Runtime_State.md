# Phase 3 Runtime State Implementation

## Implemented

The backend now supports shared Redis-backed runtime state for:

- authentication sessions
- failed login attempt counters
- account lockouts
- rate limiting
- shared application cache

The implementation uses `APP_REDIS_URL` or `RUNTIME_STATE_REDIS_URL` when configured. If neither is configured, local development keeps the existing in-memory fallback behavior.

## Required Production Environment

Production must set one of:

```env
APP_REDIS_URL=redis://redis:6379/1
RUNTIME_STATE_REDIS_URL=redis://redis:6379/1
```

Production startup fails if runtime Redis is not configured.

Contract ingestion queue workers should use:

```env
CONTRACT_QUEUE_REDIS_URL=redis://redis:6379/0
```

## Process Separation

The API process now has startup toggles:

```env
START_BACKGROUND_SERVICES=true
START_CONTRACT_QUEUE_WORKERS=false
```

The worker process can be started with:

```bash
python -m rbac_backend.worker
```

Recommended contract worker configuration:

```env
START_BACKGROUND_SERVICES=false
START_CONTRACT_QUEUE_WORKERS=true
```

`docker-compose.yml` now includes a `contract-worker` service and keeps contract queue consumers out of the API container by default.

## Remaining Work

General document background jobs still use function references and an in-process queue. They are not fully durable across restarts yet.

To make them fully durable:

1. Replace function-reference jobs with named job types and JSON payloads.
2. Persist jobs in Redis/Mongo before enqueue.
3. Run document processing in a dedicated worker process.
4. Add retries, timeout, dead-letter queue, and queue metrics.
5. Make every job handler idempotent.

This should be handled before scaling document OCR/background processing beyond one API replica.
