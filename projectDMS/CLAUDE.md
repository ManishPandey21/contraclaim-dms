# ContraClaim DMS — working notes for Claude Code

Contract correspondence & document management with AI-assisted drafting for EPC /
infrastructure / claims teams. FastAPI + MongoDB backend, React/Vite SPA, Qdrant
(vectors), FalkorDB (graph), Redis (sessions/queue/rate limits), S3 (files).

**Read before designing anything:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/AUTHZ.md](docs/AUTHZ.md) · [docs/OPERATIONS.md](docs/OPERATIONS.md).
Those are canonical; this file holds the things that are easy to get wrong.

---

## Layout & repo facts

| Path | What |
|---|---|
| `backend/rbac_backend/` | FastAPI app — `routers/` (all under `/api`), `services/`, `core/`, `models/`, `retrieval/`, `agents/`, `ingestion/`, `migrations/`, `tests/` |
| `backend/rbac_backend/core/` | `security.py` (auth + `build_scope_query`), `permissions.py` (canonical roles/permissions), `policy`/`tenant_context.py`, `effective_scope.py`, `config.py` (startup config gate) |
| `client/` | React + Vite SPA (`src/pages`, `src/components`, `src/services`, `e2e/` Playwright) |
| `services/` | Side microservices (Graphiti, Docling, LangGraph) |
| `docs/`, `scripts/`, `config/` | Runbooks/audits, ops + codegen scripts, gateway conf + secrets |

- **The git repository root is `C:\SaaS`, not `C:\SaaS\projectDMS`.** `projectDMS/.git`
  is a stale directory git ignores. Remotes: `origin` = `ManishPandey21/projectDMS`,
  `contraclaim` = `ManishPandey21/contraclaim-dms`. **Push feature branches to both.**
- Large uncommitted trees accumulate here. Never `git add -A` blind — see *Lessons*.

## Commands

Backend tests — **only** this interpreter has the full dependency set
(`langgraph.checkpoint.mongodb`, `fastapi.testclient`). Others produce phantom
collection errors and false "dependency parity" blockers:

```bash
backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q
```

Run from the repo dir `C:/SaaS/projectDMS` (some tests import `backend.rbac_backend...`).

```bash
cd client && npm run lint && npx tsc -b && npm run build
```

```bash
cd client && npm run test && npm run test:e2e
```

After adding or changing any route or its authorization, regenerate the route
contract or `test_route_authz_gate_evidence.py` / `test_route_control_manifest.py` fail:

```bash
backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json
```

Migrations (dry run first, always, before any deploy):

```bash
cd backend && python -m rbac_backend.scripts.migrate_database --list && python -m rbac_backend.scripts.migrate_database --fail-on-warning
```

Local dev: `make front` (client) / `make back` (uvicorn `rbac_backend.main:app` :8000).

## Conventions

- **One authorization model.** `PolicyService.authorize(...)` for scoped/mutating
  routes, `require_permission(...)` for permission-only gates, `build_scope_query(...)`
  for list filtering. Never query `db.roles` directly or hand-roll a scope check.
  Pre-commit `forbid-legacy-authz` blocks `Depends(has_permission(...))`.
- **Permission names use the `dms.*` convention** (`dms.contract.clause.read`, …), not
  `contract:read`.
- **Role normalization lives once** in `core/permissions.py` (`CANONICAL_ROLE_ALIASES`,
  `normalize_role_name`). Never add a module-local `ROLE_ALIASES` or private normalizer —
  `test_role_alias_single_source.py` fails on it. Adding an alias is a *behaviour change*
  (a previously unresolvable role becomes effective).
- **Routers must re-raise domain errors as a group:** `except (BaseDomainError, HTTPException)`.
  Re-raising one domain error and letting siblings fall to `except Exception` turns 403s
  into 500s; `test_domain_error_reraise_guard.py` walks every router's AST for this shape.
- **Every new rate limiter must pass `scope=`** — unscoped falls back to a legacy shared
  `user:{id}` bucket that cross-contaminates ~19 routers.
- No `print()` in `ingestion|retrieval|agents|observability`; ruff + ruff-format + mypy run
  in pre-commit; archived/duplicate file paths are blocked by `forbid-dead-code-paths`.
- New LLM call sites that produce *drafting content* pass `strict=True` to
  `LLMGenerator.generate` and supply their own deterministic fallback + degraded marking.
  Changing arbitration prompts requires bumping the prompt-version constants (tests pin them).

## Security / RBAC rules that cost hours to rediscover

- **`EffectiveScope = Entitlement ∩ Navbar Selection.`** For the global roles
  (Super Admin / Super User), `current_user.organization_id` is the *active selection*,
  never the home organisation — `get_current_user` clears it and only a validated
  `X-Org-Id` re-populates it. Any new auth path that builds `CurrentUser` must do the same
  or consolidated views silently collapse to one org.
- **The gate answers membership; `build_scope_query` answers row visibility.** A
  project-tier user doing a collection read with *no* project selected gets **200 bounded
  to their assignments**, not 403 and not an org-wide list. Never bound results in the gate.
- **A `401` from a scoped endpoint may be a masked 403.** The client turns 401 into a
  forced logout, 403 into a toast — so a scope refusal must keep its own status.
- **Trust `gate_evidence` in `route_control_manifest.json`, not `enforcement`.** The latter
  is substring matching over a source blob and can claim a gate that doesn't exist.
  `unsafe` in that inventory means "mutating HTTP method", not "insecure".
- Tenant-bound accounts hit `403 "No active subscription is configured…"` in
  `policy_service` *before* any scope logic; superadmin bypasses it. Any RBAC test run needs
  a `subscriptions` row per test org (`status`/`billing_status` `active`, `plan_code`
  `dms_enterprise`, `project_id: null`) **and** a `superuser` document in `roles`.

## Deployment

Production is `contraclaim.com` (SSH alias `contraclaim`), checkout
`/opt/contraclaim-dms/projectDMS`, remote `contraclaim-dms`, Docker Compose
(`docker-compose.yml` + `docker-compose.prod.yml`: gateway/backend/client/contract-worker/
qdrant/falkordb/redis/graphiti/clamav).

- **The runbook's branch is wrong.** `docs/CONTRACLAIM_DOCKER_DEPLOYMENT_UPDATE_GUIDE.md`
  says `main`; production has been checked out on a `codex/*` branch that diverges from
  `main` by well over a hundred commits. **Always read the server's own
  `git branch --show-current` before deploying.**
- Deploy = push the branch to both remotes → on the server `git pull --ff-only origin <that
  branch>` → rebuild only affected services (`build backend contract-worker client`) →
  `up -d --no-deps`. Gates: `scripts/pre_deploy_readiness.sh`, `python scripts/preflight.py`,
  migration dry-run; after: `scripts/post_deploy_verify.sh`.
- Before trusting any code read of production, verify the checkout is byte-identical to the
  running container (tree-wide `sha256sum` compare). Patches scp'd in for testing must be
  reverted and fast-forwarded afterwards.
- Backend refuses to start on unsafe production config (placeholder secrets, dev CORS,
  insecure cookies, fail-open RBAC, missing metrics token/Redis/backup config). That gate is
  deliberate — don't relax it to make a deploy pass.

## Lessons / recurring failure modes

- **A green suite proves nothing about dependency-injected code.** Router tests override
  `get_*_controller` factories, so a broken factory ships past 1000+ passing tests.
  `git add -A` once swept an unreviewed orphaned-`return` factory into production and took
  `GET /api/organizations` down. Diff files you did not personally edit before staging.
- **Backend async tests each get their own `asyncio.run` loop** (`backend/conftest.py`, no
  pytest-asyncio). Module-level Motor globals must be cleared between tests or a test passes
  alone and fails in the suite with "Event loop is closed". Async *fixtures* are unsupported —
  use `@asynccontextmanager` inside the test.
- **Silent failures are the house pattern here.** Vector writes once returned 0 chunks while
  ingest marked documents `completed` (Qdrant 401 + ignored `result["ok"]`); logging config
  once dropped everything below WARNING because `dictConfig` had no root logger. Prefer
  fail-visible: surface the error, mark the record degraded, never mark success on a skipped
  step.
- One malformed row in `notifications` 500s the listing for every tenant; `documents`
  silently drops bad rows but still counts them in `total`. Seed fixtures with complete,
  model-valid shapes.

## Decisions not to reverse accidentally

- Qdrant is the **only** vector store. The write-only `FalkorDBVectorService` was deliberately
  deleted; FalkorDB keeps `GRAPH.*` use only (letter/clause references).
- Contract retrieval is **clause-first**: structured `contract_clauses` records + the
  `contract_clauses` Qdrant namespace. Legacy `document_vectors`/token chunks are fallback only.
- Arbitration LangGraph engine is code-complete but intentionally **not primary**:
  `ARBITRATION_ENGINE_DEFAULT=arbitration_v2`, `ROLLOUT_MODE=off`,
  `PRODUCTION_ACCEPTED=false`. Flipping to primary requires the acceptance receipt chain.
- FalkorDB Letter nodes are MERGEd on `normCode` alone, so a node can be shared across
  documents/orgs — deletion checks for another live owner and skips instead of deleting.
- Duplicate-upload detection: any new upload entry point must call
  `duplicate_detection.precheck_upload` before creating a record, and any new downstream
  artifact must check `duplicate_status == "pending"` before writing.
- All reference writes go through `ReferenceSyncService.sync_bidirectional` (it owns
  bucket-replace idempotency + the cross-source guard); UI comparisons of letter numbers use
  the normalized form.

## Known open debt

- Client consumes neither `selection_required` nor `context_forbidden` (no scope-selection
  prompt exists yet).
- `test_route_control_manifest` can fail on clean HEAD: the checked-in manifest claims an
  `authorize()` gate on `/api/notifications` that the live route lacks — notifications has no
  permission dependency at all.
- Deferred from the AI-harness work: Learning Update persistence, OTel workflow spans, v3
  domain-adapter double-run, claim-support verification via NLI/LLM-judge.
- Arbitration acceptance is unproven in production (zero cases/runs), plus outstanding
  crash/restart drills, legal HITL review, and version diff/restore UI.
- Release-gate evidence (restore drill, live-integration/E2E proof) is still uncaptured;
  `scripts/production_readiness_score.py` scores low for that reason alone.
