# LangGraph and Graphiti Integration Audit

This document summarizes how LangGraph and Graphiti are integrated in the ContraClaim DMS repo, which configuration values are required, and the issues found during the repo audit.

## How It Is Integrated

The app currently has three overlapping graph/orchestration layers:

| Layer | Location | Purpose | Current usage |
| --- | --- | --- | --- |
| Backend LangGraph-inspired letter pipeline | `backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py` | Letter drafting, background generation, strategy plan generation, graph thread context, retrieval-backed drafting | Actively used by backend API and frontend |
| Standalone LangGraph microservice | `services/langgraph/orchestrator.py` | Real `langgraph` package-based workflow orchestration service | Present in Docker Compose, but not used by the main frontend letter workflow |
| Graphiti microservice | `services/graphiti/app.py` | FalkorDB-backed knowledge graph API | Present in Docker Compose and gateway, but backend adapter contract is not aligned |

## Active Backend LangGraph Flow

Frontend calls backend endpoints:

| Endpoint | Backend location | Purpose |
| --- | --- | --- |
| `POST /api/ai-assistant/langgraph/draft` | `backend/rbac_backend/routers/ai_assistant.py` | Run the LangGraph-style draft workflow |
| `POST /api/ai-assistant/langgraph/background` | `backend/rbac_backend/routers/ai_assistant.py` | Run background/context generation only |
| `POST /api/ai-assistant/langgraph/strategy-plan` | `backend/rbac_backend/routers/ai_assistant.py` | Generate structured strategy plan |
| `GET /api/ai-assistant/langgraph/runs/{letter_id}` | `backend/rbac_backend/routers/ai_assistant.py` | Fetch latest stored LangGraph run |
| `GET /api/ai-assistant/langgraph/config` | `backend/rbac_backend/routers/ai_assistant.py` | Read LangGraph LLM config |
| `PUT /api/ai-assistant/langgraph/config` | `backend/rbac_backend/routers/ai_assistant.py` | Update LangGraph LLM config |

The active backend flow is:

1. Frontend hook calls a backend LangGraph endpoint.
2. `AIAssistantController` validates auth, rate limit, and scope.
3. `AIService.generate_draft_with_langgraph()` creates `LetterDraftGraph`.
4. `LetterDraftGraph.run()` executes deterministic workflow nodes.
5. The pipeline uses:
   - MongoDB for letters, documents, and saved run state.
   - Qdrant through `RetrievalService` for semantic context.
   - FalkorDB through `FalkorGraphService` for graph thread context.
   - LLM config through `LLMConfigService`.
6. Result is persisted through `LetterService.record_langgraph_result()`.

## Standalone LangGraph Service

The standalone service exists here:

| File | Purpose |
| --- | --- |
| `services/langgraph/orchestrator.py` | FastAPI service using the real `langgraph` package |
| `services/langgraph/Dockerfile` | Docker image for the service |
| `services/langgraph/requirements.txt` | Service dependencies |

Docker Compose exposes it as:

| Compose service | Internal URL | Gateway URL |
| --- | --- | --- |
| `langgraph` | `http://langgraph:9000` | `/langgraph/` |

The service expects:

| Key | Example |
| --- | --- |
| `LANGGRAPH_REDIS_URL` | `redis://redis:6379/0` |
| `LANGGRAPH_GRAPHITI_URL` | `http://graphiti:8080` |
| `LANGGRAPH_QDRANT_URL` | `http://qdrant:6333` |
| `LANGGRAPH_BACKEND_URL` | `http://backend:8000` |
| `LANGGRAPH_API_TOKEN` | `<strong-service-token>` |
| `LANGGRAPH_LOG_DIR` | `/app/logs` |
| `LANGGRAPH_INGEST_QUEUE` | `workflow:ingest` |
| `LANGGRAPH_WS_KEEPALIVE` | `15` |

The backend internal document processing endpoint is protected by `LANGGRAPH_API_TOKEN`:

| Endpoint | Location |
| --- | --- |
| `POST /api/internal/documents/{id}/process` | `backend/rbac_backend/routers/documents.py` |

## Graphiti Service

The Graphiti service exists here:

| File | Purpose |
| --- | --- |
| `services/graphiti/app.py` | FastAPI service backed by FalkorDB/RedisGraph |
| `services/graphiti/Dockerfile` | Docker image for the service |
| `services/graphiti/requirements.txt` | Service dependencies |

Docker Compose exposes it as:

| Compose service | Internal URL | Gateway URL |
| --- | --- | --- |
| `graphiti` | `http://graphiti:8080` | `/graphiti/` |

Graphiti exposes these endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Basic health check |
| `GET /health/ready` | FalkorDB readiness check |
| `POST /documents` | Upsert document into graph |
| `POST /episodes` | Add episode |
| `GET /search` | Search documents |
| `POST /relationships` | Create relationship |
| `POST /temporal` | Temporal graph query |
| `POST /graph/query` | Raw Cypher query |

Graphiti expects:

| Key | Example |
| --- | --- |
| `GRAPHITI_DB_URL` | `redis://falkordb:6379` |
| `GRAPHITI_OPENAI_API_KEY` | `<openai-api-key>` |
| `GRAPHITI_LOG_LEVEL` | `INFO` |
| `GRAPHITI_GRAPH_NAME` | `contraclaim` |

## Required Configuration

### Root `.env` for Docker Compose

The root `.env` is used by `docker-compose.yml` interpolation.

```env
QDRANT_API_KEY=<qdrant-api-key>
OPENAI_API_KEY=<openai-api-key>
LANGGRAPH_API_TOKEN=<strong-service-token>
REDIS_PASSWORD=
```

For local Docker, keep `REDIS_PASSWORD` blank unless Redis is started with password enforcement.

### Backend Docker Env

For Docker Compose, update `backend/.env`.

```env
LANGGRAPH_ENABLED=true
LANGGRAPH_API_TOKEN=<same-strong-service-token>
LANGGRAPH_MODEL=gpt-4o
LANGGRAPH_DRAFTER_MODEL=gpt-4o
LANGGRAPH_REVIEWER_MODEL=gpt-4o-mini
LANGGRAPH_PLAN_MODEL=grok-4-1-fast

FALKORDB_ENABLED=true
FALKORDB_URL=redis://falkordb:6379
FALKORDB_HOST=falkordb
FALKORDB_PORT=6379
FALKORDB_GRAPH_NAME=contraclaim
FALKORDB_PASSWORD=

QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=<same-qdrant-api-key>
QDRANT_COLLECTION=contracts
QDRANT_VECTOR_SIZE=1536
QDRANT_DISTANCE=Cosine
QDRANT_TIMEOUT=15

GRAPHITI_API_URL=http://graphiti:8080
GRAPHITI_BASE_URL=http://graphiti:8080
GRAPHITI_API_KEY=<graphiti-api-key-or-empty-if-not-enforced>
GRAPHITI_WORKSPACE=ContraClaim
```

### Backend Running on Host, Services Running in Docker

If the backend runs on Windows/host and Redis, FalkorDB, Qdrant, Graphiti run in Docker with ports exposed:

```env
FALKORDB_URL=redis://localhost:6380
FALKORDB_HOST=localhost
FALKORDB_PORT=6380
QDRANT_URL=http://localhost:6333
GRAPHITI_API_URL=http://localhost:8080
GRAPHITI_BASE_URL=http://localhost:8080
APP_REDIS_URL=redis://localhost:6379/1
RUNTIME_STATE_REDIS_URL=redis://localhost:6379/1
CONTRACT_QUEUE_REDIS_URL=redis://localhost:6379/0
```

### Frontend Env

If LangGraph UI features should be visible:

```env
VITE_API_BASE_URL=http://localhost:8000/api
VITE_LANGGRAPH_ENABLED=true
```

For production:

```env
VITE_API_BASE_URL=https://app.contraclaim.com/api
VITE_LANGGRAPH_ENABLED=true
```

## Issues Found

### 1. `LANGGRAPH_API_TOKEN` Is Used But Missing From Backend Settings

`backend/rbac_backend/core/config.py` validates `settings.LANGGRAPH_API_TOKEN`, and `backend/rbac_backend/routers/documents.py` uses it for internal service auth.

Current issue:

- `LANGGRAPH_API_TOKEN` is not declared as a `Settings` field.
- Production validation can fail even if the env file contains the key.
- The standalone LangGraph service may fail to call `/api/internal/documents/{id}/process`.

Recommended fix:

Add this field to `Settings` in `backend/rbac_backend/core/config.py`:

```python
LANGGRAPH_API_TOKEN: Optional[str] = Field(default=None, validation_alias="LANGGRAPH_API_TOKEN")
```

### 2. Graphiti Env Key Mismatch

The backend env uses:

```env
GRAPHITI_API_URL=
```

But `backend/rbac_backend/graph/graph_adapter.py` reads:

```python
GRAPHITI_BASE_URL
```

Current issue:

- `GRAPHITI_API_URL` is documented/present.
- `GRAPHITI_BASE_URL` is read by code.
- Neither key is declared in `core/config.py`.
- `GraphAdapter` remains disabled.

Recommended fix:

Add both fields to backend settings and prefer one canonical key:

```python
GRAPHITI_API_URL: Optional[str] = Field(default=None, validation_alias="GRAPHITI_API_URL")
GRAPHITI_BASE_URL: Optional[str] = Field(default=None, validation_alias="GRAPHITI_BASE_URL")
GRAPHITI_API_KEY: Optional[str] = Field(default=None, validation_alias="GRAPHITI_API_KEY")
GRAPHITI_WORKSPACE: Optional[str] = Field(default=None, validation_alias="GRAPHITI_WORKSPACE")
```

Then update `GraphConfig.from_settings()` to use `GRAPHITI_BASE_URL` or `GRAPHITI_API_URL`.

### 3. Backend GraphAdapter and Graphiti Service Endpoint Contracts Do Not Match

Backend `GraphAdapter` calls:

| Backend expected endpoint | Graphiti service currently has |
| --- | --- |
| `/graph/nodes` | Missing |
| `/graph/edges` | Missing |
| `/graph/neighbors` | Missing |
| `/graph/query` with `{pattern, filters}` payload | `/graph/query` expects raw Cypher `{query, parameters}` |

Current issue:

- Enabling `GraphAdapter` will likely fail with 404 or request shape errors.

Recommended fix options:

1. Add `/graph/nodes`, `/graph/edges`, and `/graph/neighbors` to `services/graphiti/app.py`.
2. Or change `GraphAdapter` to call the existing Graphiti endpoints:
   - `/documents`
   - `/relationships`
   - `/graph/query`

### 4. Graph Names Are Inconsistent

Backend uses:

```env
FALKORDB_GRAPH_NAME=contraclaim
```

Graphiti Dockerfile defaults:

```dockerfile
GRAPHITI_GRAPH_NAME=contract_graph
```

Current issue:

- Backend direct Falkor writes and Graphiti service writes can land in different FalkorDB graphs.

Recommended fix:

Use one graph name everywhere, preferably:

```env
FALKORDB_GRAPH_NAME=contraclaim
GRAPHITI_GRAPH_NAME=contraclaim
```

Add this to the `graphiti` service environment in `docker-compose.yml`:

```yaml
GRAPHITI_GRAPH_NAME: "contraclaim"
```

### 5. `services/graphiti` and `services/langgraph` Use Wrong `BaseSettings` Import

Both service files import:

```python
from pydantic import BaseSettings
```

But requirements use Pydantic v2:

```txt
pydantic>=2.7.1
```

Current issue:

- In Pydantic v2, `BaseSettings` moved to `pydantic-settings`.
- Services may fail on startup.

Recommended fix:

In both service requirements, add:

```txt
pydantic-settings>=2.2.1
```

In both Python files, change imports to:

```python
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
```

Affected files:

- `services/graphiti/app.py`
- `services/langgraph/orchestrator.py`
- `services/graphiti/requirements.txt`
- `services/langgraph/requirements.txt`

### 6. Standalone LangGraph Service Has Malformed Cypher Queries

In `services/langgraph/orchestrator.py`, the queries are malformed:

```python
"MATCH (d:Document) WHERE d.id IN  RETURN d"
```

```python
"MATCH (d:Document)-[:HAS_ISSUE]->(i:Issue {type: }) WHERE d.id IN  RETURN d, i"
```

Current issue:

- Missing `$ids` and `$rule`.
- `/workflows` will fail when it reaches Graphiti calls.

Recommended fix:

Use parameterized Cypher:

```python
"MATCH (d:Document) WHERE d.id IN $ids RETURN d"
```

```python
"MATCH (d:Document)-[:HAS_ISSUE]->(i:Issue {type: $rule}) WHERE d.id IN $ids RETURN d, i"
```

### 7. Standalone LangGraph Calls Missing Graphiti Endpoint

`services/langgraph/orchestrator.py` calls:

```python
GET /vector/collections
```

Graphiti service does not expose this endpoint.

Current issue:

- `/agents/collections` in LangGraph service fails.

Recommended fix:

Either:

- Add `/vector/collections` to `services/graphiti/app.py`, or
- Change the LangGraph service to call Qdrant directly for collection listing.

### 8. Redis Password Wiring Is Inconsistent

Current Compose Redis service:

```yaml
redis:
  command: ["redis-server", "--save", "60", "1", "--appendonly", "yes"]
  environment:
    REDIS_PASSWORD:
```

Current LangGraph service URL:

```yaml
LANGGRAPH_REDIS_URL: "redis://:${REDIS_PASSWORD}@redis:6379/0"
```

Current issue:

- Setting `REDIS_PASSWORD` alone does not enable Redis auth.
- If `REDIS_PASSWORD` is non-empty and Redis is not started with `--requirepass`, clients may attempt AUTH against passwordless Redis and fail.

Recommended local Docker setup:

```env
REDIS_PASSWORD=
APP_REDIS_URL=redis://redis:6379/1
RUNTIME_STATE_REDIS_URL=redis://redis:6379/1
CONTRACT_QUEUE_REDIS_URL=redis://redis:6379/0
LANGGRAPH_REDIS_URL=redis://redis:6379/0
```

Recommended production setup:

```yaml
redis:
  command: ["sh", "-c", "redis-server --save 60 1 --appendonly yes --requirepass \"$REDIS_PASSWORD\""]
  environment:
    REDIS_PASSWORD: "${REDIS_PASSWORD}"
```

Then use:

```env
APP_REDIS_URL=redis://:<REDIS_PASSWORD>@redis:6379/1
RUNTIME_STATE_REDIS_URL=redis://:<REDIS_PASSWORD>@redis:6379/1
CONTRACT_QUEUE_REDIS_URL=redis://:<REDIS_PASSWORD>@redis:6379/0
LANGGRAPH_REDIS_URL=redis://:<REDIS_PASSWORD>@redis:6379/0
```

### 9. Frontend LangGraph Flag Is Incomplete

Frontend reads:

```ts
VITE_LANGGRAPH_ENABLED
```

Current issue:

- `VITE_LANGGRAPH_ENABLED` is used in `client/src/config/features.ts`.
- It is missing from `client/.env.example`.
- Some pages call LangGraph hooks directly, so the flag does not fully disable all LangGraph behavior.

Recommended fix:

Add to `client/.env.example`:

```env
VITE_LANGGRAPH_ENABLED=false
```

Add to development/production env when enabled:

```env
VITE_LANGGRAPH_ENABLED=true
```

Then audit pages that call `useLanggraphDraft()` or `useLanggraphStrategyPlan()` and gate actions/UI consistently.

## Recommended Fix Order

1. Add missing backend settings fields:
   - `LANGGRAPH_API_TOKEN`
   - `GRAPHITI_BASE_URL`
   - `GRAPHITI_API_URL`
   - `GRAPHITI_API_KEY`
   - `GRAPHITI_WORKSPACE`
2. Decide whether Graphiti should be used through the backend adapter, the separate service, or both.
3. Align Graphiti service endpoints with backend `GraphAdapter`, or update `GraphAdapter` to use existing Graphiti endpoints.
4. Align Falkor graph names:
   - `FALKORDB_GRAPH_NAME=contraclaim`
   - `GRAPHITI_GRAPH_NAME=contraclaim`
5. Fix `BaseSettings` imports and add `pydantic-settings` in both service requirements.
6. Fix malformed Cypher in `services/langgraph/orchestrator.py`.
7. Decide Redis auth mode:
   - Local: password blank.
   - Production: `--requirepass` and passworded URLs.
8. Add and consistently honor `VITE_LANGGRAPH_ENABLED` in frontend env and UI code.

## Practical Local Docker Configuration

Use this when all services run under Docker Compose:

```env
# Root .env
QDRANT_API_KEY=<qdrant-api-key>
OPENAI_API_KEY=<openai-api-key>
LANGGRAPH_API_TOKEN=<strong-service-token>
REDIS_PASSWORD=
```

```env
# backend/.env
LANGGRAPH_ENABLED=true
LANGGRAPH_API_TOKEN=<same-strong-service-token>

FALKORDB_ENABLED=true
FALKORDB_URL=redis://falkordb:6379
FALKORDB_HOST=falkordb
FALKORDB_PORT=6379
FALKORDB_GRAPH_NAME=contraclaim
FALKORDB_PASSWORD=

QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=<same-qdrant-api-key>

APP_REDIS_URL=redis://redis:6379/1
RUNTIME_STATE_REDIS_URL=redis://redis:6379/1
CONTRACT_QUEUE_REDIS_URL=redis://redis:6379/0

GRAPHITI_API_URL=http://graphiti:8080
GRAPHITI_BASE_URL=http://graphiti:8080
```

## Practical Host Backend Configuration

Use this when backend runs on the host and graph/vector/cache services run in Docker:

```env
LANGGRAPH_ENABLED=true
LANGGRAPH_API_TOKEN=<strong-service-token>

FALKORDB_ENABLED=true
FALKORDB_URL=redis://localhost:6380
FALKORDB_HOST=localhost
FALKORDB_PORT=6380
FALKORDB_GRAPH_NAME=contraclaim
FALKORDB_PASSWORD=

QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=<qdrant-api-key>

APP_REDIS_URL=redis://localhost:6379/1
RUNTIME_STATE_REDIS_URL=redis://localhost:6379/1
CONTRACT_QUEUE_REDIS_URL=redis://localhost:6379/0

GRAPHITI_API_URL=http://localhost:8080
GRAPHITI_BASE_URL=http://localhost:8080
```
