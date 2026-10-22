# CLAUDE.md

Agent orientation for the ContraClaim DMS repo (working dir `C:\SaaS\projectDMS`; **git root is `C:\SaaS`**).

## Project Overview

ContraClaim DMS ("ContractDMS") is contract correspondence and document management with AI-assisted drafting for EPC / infrastructure / claims teams. The backend is Python 3.12 + FastAPI (`backend/rbac_backend`), with MongoDB as system of record, Qdrant for vectors, FalkorDB for letter/clause references, Redis for sessions/queue/rate limits, and S3 for file objects. The frontend is a React 18 + TypeScript + Vite SPA (`client/`), tested with Vitest and Playwright. It ships as a Docker Compose stack (Apache gateway, backend, workers, data services); production runs at `contraclaim.com`.

## Architecture

- Entry point `backend/rbac_backend/main.py`: builds the FastAPI app and mounts 64 routers under `/api`, plus `ws` and `health` at the root.
- Lifespan (`main.py`) starts background job services, the contract-ingest queue, and the scheduler; `core/config.py` is the startup config gate.
- AuthN: HttpOnly cookie (`cc_access_token`) + `X-CSRF-Token` on unsafe methods; `core/security.py::get_current_user` validates the JWT, the Redis `min_iat` floor, and session liveness.
- AuthZ: one model — `PolicyService.authorize(...)` for scoped/mutating routes, `require_permission(...)` for permission-only gates, `build_scope_query(...)` for list filtering. Deny by default.
- Request path: browser → gateway (Apache: CSP/HSTS headers, `/api`→backend, `/`→client) → router → service → MongoDB / Qdrant / FalkorDB / Redis / S3.
- Ingestion: `upload → MIME sniff + ClamAV → sha256 dedup → S3 + file_object → OCR/metadata → chunk → embed → Qdrant (+ Mongo mirror) → graph refs`.
- Retrieval and drafting: `retrieval/` does clause-first, tenant-filtered vector search (HyDE / RAG-Fusion RRF, critique loop, enforced citations); `agents/` + LangGraph drive drafting.
- Workers `contract-worker` (ingest queue) and `document-worker` (`+canary`) run the same backend image.
- Side services: `services/graphiti`, `services/docling`, `services/langgraph`.
- Frontend: `client/src/main.tsx` → `App.tsx` → `routes.tsx`; pages in `client/src/pages`, HTTP calls in `client/src/services`.

## Key Directories

| Path | Purpose |
|---|---|
| `backend/rbac_backend/routers/` | FastAPI routers (66 `.py` files), all mounted under `/api` |
| `backend/rbac_backend/services/` | Domain services (152 `.py` files): authz, billing, retrieval, letters, documents |
| `backend/rbac_backend/core/` | `config.py`, `security.py`, `permissions.py`, scope/tenant/policy |
| `backend/rbac_backend/{models,schemas}/` | Mongo document models; request/response schemas |
| `backend/rbac_backend/{retrieval,agents,ingestion}/` | RAG, LangGraph agents, OCR/extraction pipeline |
| `backend/rbac_backend/migrations/` | Migrations run via `rbac_backend.scripts.migrate_database` |
| `backend/rbac_backend/tests/` | 298 `test_*.py` files; `pytest.ini` sets this as the pytest root |
| `client/src/` | React SPA: `pages/`, `components/`, `services/`, `hooks/` |
| `client/e2e/` | Playwright specs |
| `services/` | Side microservices: `graphiti/`, `docling/`, `langgraph/` |
| `docs/` | Canonical docs: `ARCHITECTURE.md`, `AUTHZ.md`, `OPERATIONS.md`, `adr/` |
| `scripts/` | Ops/CI helpers, incl. `rbac_phase0_route_inventory.py`, `pre_deploy_readiness.sh` |
| `config/` | Gateway configuration and secret placeholders |

## Build Commands

Documented only — **none executed** in this session (they write build output or need network/images).

```bash
# CI pins Python 3.12 / Node 20 (.github/workflows/ci.yml)
cd client && npm run build      # vite build + SSR prerender + public prerender
cd client && npm run lint       # eslint . --max-warnings=0
python -m compileall -q backend/rbac_backend    # CI compile check, from git root

docker build -t projectdms-backend ./backend    # likewise ./client, ./services/{langgraph,graphiti,docling}
```

## Test Commands

Documented only — **none executed**: they need live Mongo/Redis/Qdrant/FalkorDB, write caches, or download browsers.

```bash
# Backend — only this interpreter has the full dependency set.
# Run from C:/SaaS/projectDMS (tests import backend.rbac_backend...).
backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q

cd client && npm run test          # vitest (CI uses: npm test -- --run)
cd client && npm run test:e2e      # playwright (CI installs chromium first)
cd client && npx tsc -b            # not a pass/fail gate — see Risks

# Route/authz gate: regenerate and diff after changing any route or its authorization
backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format json
```

`pytest.ini` markers: `asyncio`, `integration`, `external_service`, `live_external_service`, `manual`.

## Read-Only Checks Run

No build, test, install, or network command was run. Executed commands and results:

- `git rev-parse --show-toplevel` → `C:/SaaS` (confirming the git root is the parent dir, not `projectDMS`).
- `git rev-parse --abbrev-ref HEAD` → `integrate/key-date-eot`.
- `git log -1 --format='%H %ad %s' --date=short` → `a179ca9edd4d98266dba530c3e87df1a67135d5c 2026-09-06`.
- `git remote -v` → `origin` = `ManishPandey21/projectDMS`, `contraclaim` = `ManishPandey21/contraclaim-dms`.
- `node --version` → `v24.21.0`; `npm --version` → `11.6.0`.
- `backend\.venv\Scripts\python.exe --version` → `Python 3.10.3`.
- `Test-Path`: `backend/.venv/Scripts/python.exe`, `client/node_modules`, and `client/node_modules/.bin/{vitest,playwright,eslint}.cmd` all exist.
- Read `scripts/rbac_phase0_route_inventory.py` argparse: `--format` accepts `summary|json|markdown`; there is no `--output` (redirect yourself).

## Conventions

- Pre-commit gate (`.pre-commit-config.yaml`): ruff `--fix` and ruff-format, plus mypy, scoped to `backend/rbac_backend/(ingestion|retrieval|agents|observability)/`, `routers/retrieval_engine.py`, `tests/test_retrieval_engine_basics.py`.
- Local pre-commit guards: `forbid-print` (same scope), `forbid-dead-code-paths`, and `forbid-legacy-authz` — the latter blocks `Depends(has_permission(` and `archive` imports in `.py`/`.ts`/`.tsx`.
- Permission names use the `dms.*` convention (`dms.contract.clause.read`), not `contract:read`.
- Routers re-raise domain errors as a group: `except (BaseDomainError, HTTPException)`; letting siblings fall to `except Exception` converts 403s into 500s.
- Every new rate limiter must pass `scope=`.
- Backend async tests each get their own `asyncio.run` loop (`backend/conftest.py`, no pytest-asyncio); clear module-level Motor globals between tests.
- Client lint is zero-warning (`--max-warnings=0`).

## Agent Instructions

- Read `docs/ARCHITECTURE.md`, `docs/AUTHZ.md`, `docs/OPERATIONS.md` before changing any design; they are canonical. `CLAUDE.md` holds deeper operational notes.
- Authorize only through `PolicyService.authorize` / `require_permission` / `build_scope_query`; never query `db.roles` directly or hand-roll a scope check.
- Run backend tests only with `backend/.venv/Scripts/python.exe`, from `C:/SaaS/projectDMS`.
- Regenerate and diff the route inventory after touching any route or its authorization.
- Dry-run migrations before any deploy: `migrate_database --list`, then `--fail-on-warning`.
- Stage explicit paths only: never `git add -A` (large uncommitted trees accumulate here), and diff files you did not edit before staging.
- Add no new module-local `ROLE_ALIASES` table, and never seed a `superuser` role document to make a check pass.
- New LLM call sites that emit drafting content pass `strict=True` and supply a deterministic fallback plus degraded marking.
- Keep research and inspection read-only: write generated artifacts outside the repo tree.

## Risks / Unknowns / Blocked Actions

- ⚠️ Unverified: no build or test command was executed; their pass/fail state is unknown. They require live data services, write build/cache output, or fetch browsers.
- ⚠️ Interpreter drift: `.python-version` and CI pin **Python 3.12**, but `backend/.venv/Scripts/python.exe` reports **3.10.3**. Recreate with `py -3.12 -m venv backend/.venv` per `README.md` before trusting backend results. ❓ Unknown whether this venv still carries the full dependency set.
- ⚠️ `cd client && npx tsc -b` is reported to emit many pre-existing errors, so it is not a pass/fail gate; check that your own files are absent from its output. Not re-measured this session.
- ⚠️ `make stop` kills port 3000, but the Vite dev server listens on **5173** (`client/vite.config.ts`); the `make` help text is stale.
- ⚠️ `docs/ARCHITECTURE.md` says "~40 routers"; `main.py` mounts 64 under `/api`. Trust the code.
- ⚠️ The git root being `C:\SaaS` means git commands span a wider tree than this project; treat `git status` / `add` / `stash` as potentially mutating.
- Blocked, needs approval / network / credentials: `npm ci`, `npx playwright install`, `docker build`, `docker compose up`, `scripts/pre_deploy_readiness.sh`, in-container migration dry-run, and production SSH (`contraclaim`).
- ❓ Unknown: whether the live stack still runs `docker-compose.prod.yml` + `docker-compose.mongo-replicaset.yml`; verifying requires SSH to production.

## Next Steps

- Recreate `backend/.venv` on Python 3.12, then run the backend suite to re-measure the real pass/fail baseline.
- Re-measure `npx tsc -b` and either fix the errors or record the baseline count.
- Before the next deploy, read the running container's `com.docker.compose.project.config_files` label to confirm the compose file set.
