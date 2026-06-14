# FalkorDB Integration & Deployment Plan

This note captures how FalkorDB is wired into the codebase and how to operate it in production.

## Where it lives in code
- Config flags in `backend/rbac_backend/core/config.py`:
  - `FALKORDB_URL`, `FALKORDB_ENABLED`, `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_GRAPH_NAME`, `FALKORDB_PASSWORD`, `FALKORDB_CLEANUP_REFERENCES`.
- Service implementation: `backend/rbac_backend/services/falkor_graph_service.py`
  - Uses `redis.Redis` client and `GRAPH.QUERY` to talk to FalkorDB (RedisGraph).
  - Supports schema setup, upsert of `Letter` nodes with `CITES` / `REPLIES_TO` edges, neighbor and thread retrieval.
  - Safe guards: one Cypher per query; parameter serialization; optional cleanup of edges.
- Older iterations: `falkor_graph_service1/3/old.py` (not active).
- Health/diagnostic scripts: `backend/scripts/check_qdrant_falkor_*.py`, `debug_falkor.py`, `test_falkordb_connection.py`.

## How it is used
- Main consumer is `FalkorGraphService.upsert_letter_with_refs`:
  - Normalizes letter codes, ensures schema (indexes on `Letter.date`, `Letter.direction`).
  - MERGE `Letter` node, then MERGE `CITES` or `REPLIES_TO` edges to referenced letters.
  - Optional cleanup: deletes parser-sourced edges before re-inserting.
- Reads:
  - `get_letter`, `get_thread(depth)`, `get_neighbors`, plus debug helpers.
- Service is feature-flagged: when `FALKORDB_ENABLED` is false, calls no-op.

## Environment expectations
- Defaults (if unset): `redis://localhost:6380`, graph name `contraclaim`, cleanup enabled.
- Provide `FALKORDB_PASSWORD` if FalkorDB is secured; otherwise leave blank.
- Set `FALKORDB_ENABLED=true` to activate; set to false to bypass all graph writes.

## Deploying FalkorDB on Ubuntu (baremetal or alongside app)
1) Install Docker and run FalkorDB:
```bash
docker run -d --name falkordb \
  -p 6380:6379 \
  -v /var/lib/falkordb:/data \
  public.ecr.aws/redisgraph/redisgraph:2.10.14
```
2) (Optional) Set a password in `redis.conf` or via `requirepass` and pass it with `-e REDIS_PASSWORD=...`.
3) Persist data: keep `/var/lib/falkordb` volume.
4) Firewall: restrict 6380 to your VPC / localhost; proxy not required.

## App configuration (backend)
- In `.env` (or env vars):
```
FALKORDB_ENABLED=true
FALKORDB_URL=redis://localhost:6380
FALKORDB_HOST=localhost
FALKORDB_PORT=6380
FALKORDB_GRAPH_NAME=contraclaim
FALKORDB_PASSWORD=your-secret    # optional; omit if not set
FALKORDB_CLEANUP_REFERENCES=true # allows pruning old edges during upsert
```
- Restart the API after changes.

## Operational checks
- Connectivity: `python backend/scripts/check_falkor_env.py` then `backend/scripts/test_falkordb_connection.py`.
- Schema/index: first write auto-creates indexes; review logs for “schema setup completed”.
- Basic query: use `debug_falkor.py` or `FalkorGraphService.debug_count_letters`.

## Failure modes & mitigations
- Connection errors: verify host/port/password; check Docker container is running.
- Cypher errors: ensure single statement per call; avoid multi-statement queries.
- Performance: graph is letter-only; keep cleanup enabled to prevent edge bloat.
- Safety: set `FALKORDB_ENABLED=false` to hard-disable all graph writes if Falkor is down.

## When to use FalkorDB
- For letter threading/graph queries (CITES/REPLIES_TO relationships) when you need quick neighbor/thread traversal.
- Not required for contract search; it is orthogonal to Qdrant/Mongo vectors.

## Minimal rollout plan
1) Stand up FalkorDB container with persistence and (optionally) password.
2) Populate `.env` with `FALKORDB_*` vars and enable the flag.
3) Restart backend; watch logs for “FalkorDB schema setup completed”.
4) Run `test_falkordb_connection.py`; then exercise a letter ingestion path to create nodes/edges.
5) Monitor `journalctl -u contraclaim-api` for Falkor errors; keep `FALKORDB_ENABLED=false` as a quick kill-switch.
