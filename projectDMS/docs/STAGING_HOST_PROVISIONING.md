# Staging host — provisioning specification and Gate execution plan

**Status: the host does not exist.** Release programme phase R-A6 stopped staging
provisioning on the development machine at the resource floor, and phase R-A7
searched the repository and the local SSH configuration and found no staging host
of any kind. This document is the specification the host must meet and the exact
order the next phase runs against it. It is provider-neutral on purpose: nothing
here assumes AWS, Hetzner, OVH, a VM on a workstation, or bare metal.

Everything below is derived from `docker-compose.prod.yml`,
`docker-compose.mongo-replicaset.yml` and `docker-compose.staging.yml` on
`release/contraclaim-rc1`. Re-derive it rather than trusting this file if those
have moved.

---

## 1. Why a separate host

The development machine has 7.89 GB of RAM, of which the Docker Desktop VM gets
3.77 GB. `docker-compose.prod.yml` declares a 2 GB memory limit for `clamav` and
`--maxmemory 2gb` for `falkordb`: **4 GB of source-declared ceiling for two
services**, before any of the three mongod, three Python, or four remaining
containers. Free disk was 15.94 GB against roughly 17.7 GB of images, build
cache, volumes and staging backups, and the backend and client images do not
exist locally and must be built.

That is not a tuning problem. Gate 2, and Gates 3, 7 and 8 behind it, need a host
that can run the stack and the test suite at the same time.

## 2. Host specification

### Minimum accepted

| Resource | Specification | Why |
|---|---|---|
| OS | Linux x86-64, Ubuntu 22.04 or 24.04 LTS | `deploy_ubuntu.sh`, `backup.sh`, `restore.sh`, `post_deploy_verify.sh` and the replica-set initiator are all bash; the 64000 `nofile` ulimit applies natively rather than through a VM |
| CPU | 8 vCPU | 3 mongod + 3 Python containers + clamd + Qdrant + FalkorDB; `freshclam` and OCR are both sustained CPU |
| RAM | 24 GB, with **at least 16 GB available to the container runtime** | The stack is ≈13 GB steady state; 16 GB absorbs mongod and OCR peaks and leaves 8 GB for the OS and the test runner |
| Disk | **40 GB free SSD** at provisioning time, dedicated | ≈17.7 GB of images, build cache and volumes, plus backup archives and a second full data copy for the restore drill |
| Docker | Engine 24+ with the Compose v2 plugin | The compose files use `deploy.resources`, profiles, and `condition: service_healthy` |
| Isolation | Not the production host, not the development machine | Staging credentials, staging data, staging blast radius |

If Docker itself runs inside a VM on this host, **16 GB must reach the Docker
workload**, not merely be installed on the outer machine.

### Recommended

12 vCPU, 32 GB RAM (20 GB to the container runtime), 60 GB free SSD. This fits
Playwright E2E (Gate 3) and a full restore drill (Gate 8) in the same
provisioning window instead of a second one.

### Network

| Direction | Requirement |
|---|---|
| Outbound HTTPS | `api.openai.com` (Gate 2 bullet 2), the S3 endpoint for the staging region, the container registries, `database.clamav.net` for the first ClamAV signature download |
| Inbound | **None from the public internet.** The gateway binds `127.0.0.1:18080`; reach it over an SSH tunnel. The bundled gateway speaks plain HTTP while the backend sets `AUTH_COOKIE_SECURE=true`, so it must never be a public entry point |
| SSH | Key-only, password authentication disabled, root login disabled |
| Firewall | Default deny inbound except SSH. No data-tier port is published by the compose set, and `pre_deploy_readiness.sh` fails a configuration that publishes one |

### Filesystem

| Path | Purpose |
|---|---|
| `/opt/contraclaim-stg` | Checkout of `release/contraclaim-rc1` |
| `/var/backups/contraclaim-stg` | `BACKUP_ROOT`. Bind-mounted read-only into `backend` and `contract-worker`; written by `scripts/backup.sh` and read by the Gate-8 restore drill. Must be on the same 40 GB budget |

DNS is optional. `PUBLIC_BASE_URL` and `CORS_ORIGINS` must be real staging
hostnames because the backend's startup validation rejects localhost and dev
origins under `ENVIRONMENT=production`, but they need not resolve publicly — a
hosts-file entry on the operator's machine is sufficient for a tunnelled run.

## 3. Service set

Derived from the compose files, not from a list anybody typed. Thirteen
steady-state containers plus one run-once:

`backend`, `client`, `contract-worker`, `document-worker`, `qdrant`, `falkordb`,
`redis`, `clamav`, `gateway`, `mongo1`, `mongo2`, `mongo3`, plus the run-once
`mongo-init`.

**Excluded, and each for a reason in source:**

| Service | Why it is not deployed |
|---|---|
| `graphiti` | Behind the `graph-experimental` profile, and `GRAPHITI_ENABLED` is `"false"` |
| `document-worker-canary` | `deploy.replicas` defaults to `0`. Staging does not invent a canary; the rollout policy is production's |
| LangGraph, Docling | In no production compose file at all. CI building an image is not a reason to deploy it |

## 4. Isolation namespace

`docker-compose.staging.yml` pins `name: contraclaim-stg`, and Docker prefixes
every network and volume with the project name. Nothing has to be renamed by
hand, and nothing is `external`.

| Class | Names |
|---|---|
| Networks | `contraclaim-stg_data-net`, `contraclaim-stg_service-net`, `contraclaim-stg_edge-net`, `contraclaim-stg_egress-net` |
| Volumes | `contraclaim-stg_mongo1_data`, `_mongo2_data`, `_mongo3_data`, `_qdrant_data`, `_qdrant_snapshots`, `_falkordb_data`, `_redis_data`, `_backend_uploads`, `_backend_logs` |
| Host ports | `127.0.0.1:18080` → gateway. Nothing else |
| Mongo replica set | `rsstg` |
| Database | `contraclaim_staging` |
| Falkor application graph | `contraclaim_staging` |
| Qdrant application collections | `stg_document_vectors` |
| Gate-test graphs | Run-owned only, minted by `authority_band_graph.disposable_graph_name()`; the delete gate is `owned_by_this_run()` |
| Gate-test collections | Run-owned `g28_<uuid>`, deleted by the test |
| Gate-test Redis keys | `stg_gate2_<run token>*`, deleted and then asserted gone |

Disposal is one command: `docker compose -p contraclaim-stg down -v`.

## 5. Mongo cache policy

Each mongod sizes its WiredTiger internal cache at half of visible RAM minus
1 GB and every member sees the whole host. Unbounded on a 16 GB container budget
that is roughly 7.5 GB of intent per member, 22.5 GB between them.

`docker-compose.mongo-replicaset.yml` now carries the mechanism with an **off**
default: `MONGO_WIREDTIGER_CACHE_GB` unset reproduces today's production argument
list exactly (`mongod --replSet rs0 --bind_ip_all`), and set appends
`--wiredTigerCacheSizeGB`. Production can adopt the bound later by setting one
variable, which is a decision rather than a side effect.

Staging sets **1 GB per member** with a 2 GB hard container limit behind it:
3 GB of cache, ≈4.5 GB with process overhead, inside a stack budgeted at ≈13 GB
alongside ClamAV's 2 GB limit and FalkorDB's 2 GB maxmemory.

## 6. Credentials

Two decisions no amount of hardware substitutes for. Both are **start-required**:
compose will not render without them, so the stack does not boot.

1. **A staging-issued OpenAI API key.** Gate 2 bullet 2 is a real round trip
   against `api.openai.com`. A developer or production key invalidates the bullet
   whether or not the test passes. The staging run must receive it explicitly;
   the repository's `.env` must never be sourced into it.
2. **A staging-only AWS identity and two staging-only buckets**
   (`AWS_BUCKET_NAME` for documents, `BACKUP_S3_BUCKET` for off-site backup).

Minimum bucket policy for the staging IAM identity, scoped to those two buckets
and nothing else:

| Bucket | Actions |
|---|---|
| `AWS_BUCKET_NAME` | `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:ListBucket`, `s3:AbortMultipartUpload` |
| `BACKUP_S3_BUCKET` | `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` |

No `s3:*`, no wildcard resource, and no production bucket in either.

Everything else — `SECRET_KEY`, `REDIS_PASSWORD`, `FALKORDB_PASSWORD`,
`QDRANT_API_KEY`, `METRICS_TOKEN`, `SMTP_SETTINGS_ENCRYPTION_KEY` — is minted on
the staging host and exists nowhere else. SMTP credentials are start-required but
no Gate 2 bullet exercises them, so a syntactically valid placeholder is
sufficient and a real production mail credential is not acceptable.

The full variable list, by name and category only, is `.env.staging.example`.

## 7. R-A8 execution order

Run in this order. Each step's failure is a stop, not a warning.

```bash
# 0. Everything runs from the staging checkout with the staging env file.
cd /opt/contraclaim-stg/projectDMS
export COMPOSE="docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  -f docker-compose.staging.yml \
  --env-file .env.staging"
```

| # | Step | Command | Pass condition |
|---|---|---|---|
| 1 | Host preflight | `nproc`, `free -g`, `df -h /`, `docker version`, `docker compose version`, `docker ps -a`, `docker volume ls` | 8 vCPU, ≥24 GB, ≥40 GB free, Engine 24+, Compose v2, no `contraclaim-stg_*` resource already present |
| 2 | Backup directory | `sudo install -d -m 0750 /var/backups/contraclaim-stg` | Exists, writable, on the sized filesystem |
| 3 | Render the configuration | `$COMPOSE config >/dev/null` | No unresolved variable. This is also Gate 7's compose criterion |
| 4 | Start the replica set | `$COMPOSE up -d mongo1 mongo2 mongo3 mongo-init` | — |
| 5 | Wait for PRIMARY | `$COMPOSE exec -T mongo1 mongosh --quiet --eval 'rs.status().myState'` until `1` | Set name is `rsstg` |
| 6 | List migrations | `$COMPOSE run --rm -T backend python -m rbac_backend.scripts.migrate_database --list` | Inventory printed |
| 7 | Dry run | `$COMPOSE run --rm -T backend python -m rbac_backend.scripts.migrate_database --fail-on-warning` | Zero warnings |
| 8 | Apply | `$COMPOSE run --rm -T backend python -m rbac_backend.scripts.migrate_database --apply` | — |
| 9 | Idempotency | repeat step 8 | Second run is a no-op |
| 10 | Data tier | `$COMPOSE up -d redis qdrant falkordb clamav` | All `healthy` |
| 11 | Application tier | `$COMPOSE up -d backend contract-worker document-worker client gateway` | All `healthy` |
| 12 | Permission catalogue | the seed/assert path in `initial_data/default_permissions.py` | Catalogue matches `core/permissions.py` |
| 13 | Liveness | `curl -fsS http://127.0.0.1:18080/health/live` | 200 |
| 14 | Readiness | `curl -fsS http://127.0.0.1:18080/health/ready` | 200, every dependency reported up |
| 15 | Staging backup | `scripts/backup.sh` | Archives carry real persistence files (`test_deployment_config.py` pins the required entries) |
| 16 | **Gate 2** | the staging suite, `CONTRACLAIM_STAGING_GATE=1` (see below) | 6/6 bullets with `evidence:` references |
| 17 | **Gate 3** | `cd client && npm run test:e2e` against the staging gateway | — |
| 18 | **Gate 7** | `scripts/pre_deploy_readiness.sh`, `preflight.py` **inside the backend container**, `/metrics` token-gated | The host interpreter has no `motor`/`dotenv`; running these on the host fails for environment reasons, not real ones |
| 19 | **Gate 8** | `scripts/restore.sh` into a second isolated project, then re-verify | RPO and RTO recorded |
| 20 | Score | `python scripts/production_readiness_score.py` | — |
| 21 | **Gate 9** | last, and only after 2, 3, 7 and 8 | Owner sign-off |

Steps 16 onward belong to R-A8. **None of them may run against production.**

### The Gate 2 invocation

```bash
env -i \
  PATH="$PATH" HOME="$HOME" \
  CONTRACLAIM_STAGING_GATE=1 \
  ENVIRONMENT=staging \
  RUN_EXTERNAL_INTEGRATION_TESTS=1 \
  FALKOR_TEST_HOST=<staging host> FALKOR_TEST_PORT=6379 \
  FALKOR_TEST_PASSWORD=<staging falkordb password> \
  QDRANT_TEST_URL=http://<staging host>:6333 \
  QDRANT_URL=http://<staging host>:6333 \
  QDRANT_API_KEY=<staging key> \
  REDIS_TEST_URL=redis://:<staging redis password>@<staging host>:6379/0 \
  DATABASE_URL=<staging replica-set URI> \
  OPENAI_API_KEY=<staging-issued key> \
  backend/.venv/Scripts/python.exe -m pytest \
    backend/rbac_backend/tests/integration -m 'integration or live_external_service' -q
```

`env -i` is the point: the run is **constructed**, not inherited. An untracked
`.env` and a checked-out `config/secrets/qdrant_api_key` both exist in the
worktree, and either one silently deciding what the gate measured is the failure
mode this whole mechanism exists to prevent.

`CONTRACLAIM_STAGING_GATE=1` then, before a single test is collected: requires
every endpoint and credential above, refuses any that names `localhost` or
`127.0.0.1`, refuses the checkout-local secret, and converts a skip inside
`test_external_services_integration.py`, `test_qdrant_containment_live.py`,
`test_graph_end_to_end_material_influence_falkor.py` or
`test_redis_queue_runtime_state_live.py` into a failure.

Two R-A8J corrections to that paragraph, both of which R-A8I proved by execution.

`FALKOR_TEST_PASSWORD` is now mandatory and appears above. The staging FalkorDB
requires AUTH and the harness had no seam to give it one, so all 21 tests in the
bullet-4 module skipped with `Authentication required` and the bullet could not be
earned however the run was configured. Its absence now stops the run in the
preflight, before collection, with the status table rather than nine connection
attempts. The value is never printed.

And the skip-to-failure conversion **did not fire** in R-A8I. It matched paths
ending in `backend/rbac_backend/tests/integration/<file>`, and inside the backend
container the path is `/app/rbac_backend/tests/integration/<file>` - no `backend/`
segment, no match, 21 skips reported as skips, pytest exit 0. Membership is decided
on the package-relative path now, so the conversion fires in the container as well
as in the checkout. A Gate 2 run whose required modules skip exits non-zero.

## 8. What this document does not do

It does not tick a Gate 2 bullet. No staging environment exists, no staging test
has run, and a specification is not evidence. Every bullet in Gate 2 remains
unchecked, and the readiness score is unchanged.
