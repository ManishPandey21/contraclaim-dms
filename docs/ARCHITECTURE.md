# ContraclaimDMS — Architecture

Contract correspondence & document management with AI-assisted drafting for
EPC / infrastructure / claims teams. This is the canonical system overview;
historical design notes are archived under `docs/history/`.

Companion docs: [OPERATIONS.md](OPERATIONS.md) · [AUTHZ.md](AUTHZ.md) ·
[EXECUTION_PLAN.md](EXECUTION_PLAN.md).

---

## 1. Components

```
                         ┌────────────── gateway (Apache) ──────────────┐
   browser ──TLS──▶      │  security headers, CSP/HSTS, reverse proxy    │
                         └───────┬───────────────────────────┬──────────┘
                                 │ /                          │ /api
                         ┌───────▼────────┐          ┌────────▼─────────┐
                         │ client (Vite/  │          │ backend (FastAPI)│
                         │ React)         │          │  routers/services│
                         └────────────────┘          └───┬───┬───┬───┬──┘
                                                         │   │   │   │
                              ┌──────────────────────────┘   │   │   └───────────┐
                              ▼              ┌────────────────┘   └────────┐      ▼
                        MongoDB (RS)      Qdrant (vectors)   FalkorDB (graph)   Redis
                     documents/letters/   chunk embeddings   letter/clause      sessions,
                     subscriptions/audit                     references         queue, locks

   contract-worker (FastAPI image, queue consumer)  ·  ClamAV (upload scanning)
```

- **gateway** — Apache `httpd`; terminates/forwards HTTP, sets CSP/HSTS/security
  headers, proxies `/api` → backend and `/` → client.
- **backend** — FastAPI app (`rbac_backend`), ~40 routers under `/api`.
- **contract-worker** — same image, runs the durable contract-ingestion queue.
- **client** — React + Vite SPA; cookie (HttpOnly) auth + CSRF.

## 2. Data stores

| Store | Role |
|---|---|
| **MongoDB** (replica set) | System of record: users, orgs, projects, documents, letters, subscriptions, billing, audit. Tenant-scoped by `organization_id`/`project_id` with compound indexes. |
| **Qdrant** | Vector store for chunk embeddings (RAG). Mongo `chunks`/`document_vectors` mirror for fallback. |
| **FalkorDB** | Knowledge graph: letter/clause references and chains. |
| **Redis** | Sessions, JWT invalidation floor, rate-limit buckets, contract-ingest queue. |
| **S3** | Immutable file objects (uploads) + offsite backups. |

## 3. Request & auth flow

1. Browser sends the HttpOnly cookie (`cc_access_token`) + `X-CSRF-Token` on
   unsafe methods.
2. `get_current_user` validates the JWT (rejecting non-`access` tokens), enforces
   the Redis `min_iat` floor and session liveness.
3. Endpoints authorize through **one model** (see AUTHZ.md): `PolicyService`
   (permission + entitlement + tenant scope + audit) for gated/scoped actions,
   `build_scope_query` for list filtering. Deny-by-default throughout.

## 4. Document & RAG pipeline

`upload → MIME sniff + ClamAV scan → sha256 dedup → store (S3 + file_object) →
OCR/metadata extraction → chunk → embed → Qdrant (+ Mongo mirror) → graph refs`.

Retrieval (`retrieval/service.py`): tenant-filtered vector search with HyDE /
RAG-Fusion (RRF), an iterative contract-QA critique loop, clause-aware reranking,
enforced citations, and prompt-injection guardrails (evidence is treated as
untrusted). Drafting uses a LangGraph pipeline (plan → draft → review).

## 5. Billing & entitlements

Plans/add-ons/subscriptions in Mongo. **Entitlements are derived** from the
active subscription's `status` + plan features + overrides (`EntitlementService`),
so flipping subscription state is the only reconciliation needed. Payments go
through a pluggable `PaymentGatewayInterface` (Razorpay adapter; Stripe stub);
`start_checkout` provisions a `pending` subscription, and the signature-verified,
**idempotent** webhook (`/api/billing/webhooks/{provider}`) promotes it to
`active`.

## 6. Multi-tenancy & isolation

Every tenant-scoped read/write/list is constrained to the caller's org/project
via `ScopeService.is_client_scope_allowed` / `build_scope_query`. The
`test_tenant_isolation.py` + `test_rbac_matrix.py` suites assert Org A can never
read/act on Org B.

## 7. CI/CD & deployment

CI (`.github/workflows/ci.yml`): gitleaks, pre-commit (ruff/mypy + custom
guards), pytest, frontend lint/test/build, pip-audit, npm audit, Trivy image
scans. Production via `docker-compose.prod.yml` (segmented internal networks,
required-secret guards, healthchecks). See OPERATIONS.md.
