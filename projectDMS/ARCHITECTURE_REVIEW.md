# Contraclaim DMS — Architecture Conformance & Production-Readiness Review

**Reviewer:** Engineering review (read-only — no code changed)
**Date:** 2026-06-22
**Scope:** `backend/rbac_backend` (FastAPI app), sidecar `services/` (docling, graphiti, langgraph), `client/`, infra (`backend/Dockerfile`, `scripts/docker-compose*.yml`, `.github/workflows/ci.yml`)
**Inputs:** Two "Contraclaim DMS" architecture diagrams (Recommended + Enhanced) vs. the actual repository.

---

## 1. Executive Summary

This is a **mature, security-conscious codebase** — substantially more production-ready than a typical AI/RAG app at this stage. The RBAC + tenant-isolation model, config hardening, durable ingest queue, CI pipeline (secret scan, dependency audit, image scan), and ~360 tests across 62 files are real strengths.

The gap is **not** quality; it is **selective architecture drift** from the diagrams. Several boxes in the "Shared Intelligence & Output Controls" and "Intent Router" layers exist in the diagram but are **not implemented as discrete components** (intent router, structured-query/Text2Mongo, output toxicity/hallucination guardrails, PII detection/masking, cross-encoder reranker, LLM gateway). Some are reasonable omissions; a few are genuine production risks.

The single most important **bug-class finding**: **database indexes are never created at application startup** in normal operation (`connect()` is only invoked by a seed script), which silently disables performance indexes *and* several **uniqueness/idempotency guarantees and TTL cleanups** in production.

**Overall readiness: ~75%.** Close the High-severity items below before scaling to production load.

---

## 2. What's Actually Built (ground truth)

| Layer | Diagram component | Implemented? | Where |
|---|---|---|---|
| 1. Interface | React/Next frontend, FastAPI REST/WS | ✅ Yes | `client/`, `main.py`, `routers/ws.py` |
| 2. Security guardrails | JWT auth | ✅ Yes | `core/security.py` (HS256, typed tokens, JWT min-iat invalidation, session check) |
| | RBAC + tenant isolation | ✅ Strong | `authorize_scope`, `build_scope_query`, `PolicyService`, 16+ isolation tests |
| | Project/contract filters | ✅ Yes | `build_scope_query` org/project scoping |
| | Input validation | ⚠️ Partial | Pydantic models + upload MIME/size limits; no central prompt-injection classifier |
| | Rate limit | ✅ Yes (config) | login IP/email + per-user limits in `config.py` |
| | Prompt-injection check | ⚠️ Mitigation only | Prompts frame evidence as "untrusted"; **no dedicated detection node** |
| | Audit logging | ✅ Yes | `audit_logger`, `audit_event_service`, `document_audit_service` |
| 3. Intent Router | Route query → search / draft / structured | ❌ **Not implemented** | No router; clients call distinct typed endpoints |
| 4A. Doc Search & Q&A | Dense vector (Qdrant) | ✅ Yes | `retrieval/vector_client.py`, `retrieval/service.py` |
| | BM25 keyword | ⚠️ "BM25-lite" | `_lexical_score` (Mongo failsafe only, not co-ranked) |
| | Hybrid merge (BM25+dense) | ⚠️ Partial | Vector-primary; Mongo is *failover*, not a fused hybrid |
| | Cross-encoder reranker | ❌ Heuristic only | `_rerank_contract_results` (keyword/clause weighting, no model) |
| | HyDE / RAG-Fusion (RRF) | ✅ Yes (bonus) | `_generate_hypothetical`, `_fuse_results` (RRF k=60) |
| | LLM answer + citations | ✅ Strong | `_enforce_citations`, iterative critique-refine loop |
| 4B. LangGraph drafting | Node workflow w/ self-check | ✅ Yes (hand-rolled) | `ai_workflows/langgraph/letter_pipeline.py` |
| | Critic / reviewer node | ✅ Yes | `review_draft` (clause-grounding + LLM review, blocking findings) |
| 4C. Structured register queries | Text2Mongo/Text2SQL, read-only, human approval | ❌ **Not implemented** | Registers served by typed REST (`dashboard`, `reports`, `variations`, `bank_guarantees`…) |
| 5. Shared Intelligence | LLM layer via gateway | ⚠️ Direct SDK | `openai_service`, `retrieval/generator.py`; no gateway/abstraction for multi-provider |
| | Redis cache | ✅ Yes | `cache_service`, `runtime_state` |
| | Output guardrails (hallucination/toxicity) | ⚠️ Partial / ❌ | Citation grounding + reviewer = partial hallucination control; **no toxicity check** |
| | PII redaction (detect & mask) | ❌ **Not implemented** | Only observability query length-redaction (`_redact_query`) |
| | Response schema validation | ✅ Yes | Pydantic response models throughout |
| | Traceable source references | ✅ Strong | `Citation`, `DraftSource`, clause/page provenance |
| 6. Data stores | MongoDB | ✅ Yes | motor/beanie |
| | Qdrant | ✅ Yes | `vector_client` w/ Mongo failover |
| | Object storage / local FS | ✅ Yes | `s3_service`, `file_service`, immutable `file_objects` |
| | Redis | ✅ Yes | runtime state, queue, rate limits |
| | Knowledge graph | ✅ FalkorDB (not Neo4j) | `graph/graph_adapter.py`, `falkor_graph_service` |
| | PostgreSQL (optional) | ❌ Not used | LangGraph checkpoints not persisted to PG |
| Enhanced extras | Observability/OTEL | ✅ Opt-in | `observability/tracing.py`, request middleware |
| | Eval pipeline (RAGAS) | ❌ Not present | `test_retrieval_quality.py` is unit-level only |
| | Critic/multi-LLM debate | ⚠️ Single reviewer | One reviewer model, no debate |
| | Kafka / user-feedback loop | ❌ Not present | Redis queue instead of Kafka |

**Key architectural facts worth flagging to stakeholders:**

- The "LangGraph" workflow is a **hand-rolled deterministic node engine**, not the `langgraph` library (which *is* pinned in requirements but unused for the letter pipeline). This is fine — it's testable and dependency-light — but it means **no durable checkpointing**; a process restart mid-draft loses in-flight state.
- The **graphiti** and **langgraph** sidecar microservices in `services/` appear **vestigial** in the default configuration: `GRAPH_PROVIDER=direct_falkor` and `GRAPHITI_ENABLED=false` mean the app talks to FalkorDB directly, bypassing the graphiti service. Confirm whether these services are still deployed/needed.
- The "Structured Register Queries / Text2Mongo" box is intentionally replaced by **typed REST endpoints** — arguably *safer* than NL→query, but it is a real divergence from the diagram and should be reconciled (either update the diagram, or build the NL layer if conversational register queries are a product goal).

---

## 3. Production-Readiness Findings (by severity)

### 🔴 High

**H1 — Indexes never created at startup (correctness + performance + data-integrity).**
`core/database.connect()` schedules `ensure_indexes_with_retry`, but `connect()` is only called by `initial_data/demo_seed.py`. `main.py`'s `startup_event` never calls it; `get_db()/get_database()` lazily create the client/database **without** triggering index creation. Consequences in a fresh prod deploy:
- All ~200 performance indexes (org/project compound indexes) are absent → full collection scans at scale.
- **Unique indexes are not enforced**: e.g. `billing_webhook_events.event_id` (webhook idempotency), `document_share_tokens.token_hash`, `contract_master` uniqueness, `letter_draft_runs.run_id`, `issued_letters` — duplicates can slip in.
- **TTL indexes don't exist**: `contract_upload_sessions.expiresAt`, `document_share_tokens.expires_at` never auto-expire → unbounded growth and stale share tokens remaining valid.

*Fix:* call `await connect()` in `startup_event` (and `disconnect()` on shutdown), or move index creation into the app lifespan. Verify uniqueness assumptions in code don't already rely on these indexes for idempotency.

**H2 — In-process scheduler + single-process assumptions don't scale horizontally.**
`APScheduler` runs inside the FastAPI process (`main.py`). The Dockerfile launches **one uvicorn worker**, and `docker-compose.prod.yml` doesn't define the backend service (so replica policy is unknown). If the backend is ever run with >1 worker/replica, **every scheduled job (digests, SLA scan, BG expiry, key-date, reference reaper) fires N times** — duplicate emails/notifications. There is no leader election or distributed lock.

*Fix:* move cron jobs to a single dedicated worker/`worker.py` process (already present), or guard each job with a Redis lock (`SET NX PX`). Decide and document the worker topology.

**H3 — No PII detection/masking despite handling contracts, claims & correspondence.**
The diagram calls for "PII Redaction — Detect & Mask." Only observability *query* length-redaction exists. Contract/claim documents and LLM drafts (which embed counterparty names, financials, signatures) flow to OpenAI and into `rag_runs`/draft storage with no masking. For a legal-DMS this is a compliance exposure (GDPR/data-processing), especially since `OBSERVABILITY_STORE_RAW_QUERIES` can be toggled.

*Fix:* add a redaction pass (Presidio or regex+NER) on (a) text sent to external LLMs where feasible and (b) anything persisted to logs/traces. At minimum, document the data-processing posture and DPA with the LLM provider.

### 🟠 Medium

**M1 — No output safety guardrail (toxicity / jailbreak / leakage check).**
Hallucination is partially controlled (citation enforcement + reviewer node), but there is no toxicity/safety filter and no check that drafts don't leak cross-tenant content pulled in error. Add a lightweight moderation call or rule-set on final drafts/answers.

**M2 — Hybrid search is failover, not fusion; reranker is heuristic.**
The diagram's value prop is BM25⊕dense fused then cross-encoder reranked. Today Qdrant is primary and Mongo lexical is only a *fallback*; the two are never co-ranked, and reranking is keyword-weighted, not a cross-encoder. Retrieval quality is likely below the diagram's intent on clause-precision queries. Consider true hybrid (RRF over BM25+dense) + a cross-encoder (e.g. `bge-reranker`) behind a flag, measured by an eval set.

**M3 — Contract ingest queue: visibility-timeout & multi-replica recovery gaps.**
`requeue_orphaned_jobs()` deletes the entire processing list on startup and re-queues all of it. With multiple replicas booting, a job actively being processed by replica A can be re-queued by replica B → **duplicate ingestion**. Recovery is startup-only (no heartbeat/visibility timeout), so a crashed worker's in-flight job is stuck until a full restart. `process_contract_ingest_job` should be verified **idempotent** (retries re-run it). DLQ has no alerting/reprocessing path.

**M4 — `get_current_user` hits MongoDB on every authenticated request.**
Each request does `db.users.find_one({email})` plus up to two Redis reads (min-iat, session). Correct, but a hot path with no short-TTL cache; under load this is avoidable DB pressure. Consider a small per-request/identity cache with explicit invalidation on the existing `user_jwt_min_iat` signal.

**M5 — `RBAC_ENTITLEMENT_FAIL_OPEN` and `CLAMAV_FAIL_OPEN` default to True.**
Production config validation *forces* these false in production (good), but the **defaults are fail-open**. Any non-`ENVIRONMENT=production` deployment (staging that mirrors prod, a misconfigured env string) silently bypasses entitlement checks and antivirus. Prefer secure-by-default (False) with explicit opt-in for dev.

**M6 — LLM calls lack a unified gateway (timeouts/retries/budget/provider-failover).**
Multiple call sites (`openai_service`, `retrieval/generator`, pipeline nodes with per-node `LLMGenerator`) call the model directly. No central place enforces timeouts, retry/backoff, cost budgeting, or provider failover (the diagram's "LLM Layer via Gateway"). Pipeline nodes swallow LLM exceptions into `warnings` and silently degrade — good for resilience, risky for silent-quality-loss without metrics/alerts.

**M7 — CI doesn't exercise queue/graph/vector integration paths.**
CI sets `CONTRACT_QUEUE_ENABLED=false`, `FALKORDB_ENABLED=false`; Qdrant isn't in CI services. The durable queue, Falkor graph sync, and vector retrieval are only unit-mocked. No coverage gate, no load test.

### 🟡 Low / Polish

- **L1** — `main.py` uses deprecated `@app.on_event` (Starlette lifespan is the supported API). Migrate to `lifespan=` for forward-compat (and it's the natural home for the H1 fix).
- **L2** — Frontend stack mismatch: diagram says "Next.js"; CORS/config (`localhost:5173`) indicates **Vite + React**. Reconcile the diagram.
- **L3** — `redis==7.0.1` pin looks suspect for redis-py (current line is 5.x). Verify it resolves to the intended client; an unexpected major could surface at runtime only.
- **L4** — Dev-header auth path (`ALLOW_DEV_HEADERS`) is well-guarded but remains in the primary auth function; keep it covered by tests asserting it's off by default (it is).
- **L5** — No durable LangGraph checkpoint (diagram's PostgreSQL checkpoint store unused). Acceptable given deterministic re-run, but note it: long drafts aren't resumable across restarts.
- **L6** — `ObservabilityService.run_id` derived from `datetime.timestamp()` can collide under concurrency; prefer a UUID.

---

## 4. Strengths Worth Preserving

- **Tenant isolation is genuinely defense-in-depth**: `authorize_scope` (imperative gate) + `build_scope_query` (deny-by-default filters with ObjectId/string expansion) + dedicated test suites (`test_tenant_isolation`, `test_search_authz`, `test_retrieval_engine_scope_isolation`).
- **Config hardening** (`validate_runtime_configuration`) refuses to boot prod with placeholder secrets, dev CORS, fail-open flags, missing replica set, missing Redis, or raw-query storage. This is excellent.
- **Auth token hygiene**: typed tokens (`type=access` vs `step_up`), Redis `min_iat` invalidation, session-active enforcement.
- **CSRF**: signed double-submit token + origin verification, scoped to cookie-auth unsafe methods.
- **CI/CD maturity**: gitleaks, pre-commit, `pip-audit`, `npm audit --audit-level=high`, Trivy image scans, compileall.
- **Citation/provenance discipline** in retrieval and drafting — exactly right for a legal/claims domain.

---

## 5. Phased Implementation Plan

Sequenced by risk-reduction-per-effort. No code is changed by this review; these are recommendations.

### Phase 0 — Hotfixes (days, before next prod deploy)

1. **H1**: Invoke `connect()`/`disconnect()` in app lifespan so indexes (incl. unique + TTL) are created. Add a startup health check that asserts critical unique indexes exist. *(highest ROI)*
2. **H2**: Decide worker topology. Move APScheduler jobs to the dedicated worker process **or** wrap each job in a Redis `SET NX` lock. Document it.
3. **M5**: Flip `RBAC_ENTITLEMENT_FAIL_OPEN` / `CLAMAV_FAIL_OPEN` defaults to secure (False); keep prod validation as backstop.
4. **L1**: Migrate `on_event` → `lifespan` (natural carrier for #1).

*Verification:* fresh-DB integration test asserting indexes/TTLs exist; multi-replica test (or documented single-worker constraint) proving each cron fires once.

### Phase 1 — AI safety & data protection (1–2 sprints)

5. **H3 + M1**: Add a guardrail module: PII detect/mask on external-LLM payloads and persisted logs; toxicity/leakage check on final drafts/answers. Wire as explicit nodes so they appear in the trace (matches diagram layer 5).
6. **M6**: Introduce a thin **LLM gateway** (single client wrapping timeout, retry/backoff, cost metering, provider failover) and route all call sites through it. Emit metrics for degraded/fallback generations so M6's silent-degradation becomes observable.
7. **M7**: Add CI jobs that run the queue + Qdrant + Falkor paths against ephemeral services; add a coverage threshold.

*Verification:* red-team prompt set (injection/toxicity) in CI; gateway unit tests for timeout/retry/budget.

### Phase 2 — Retrieval quality to match the diagram (1–2 sprints)

8. **M2**: Implement true hybrid retrieval (RRF over BM25 + dense) and add a real cross-encoder reranker behind a feature flag.
9. **Eval pipeline**: stand up a RAGAS-style offline eval (faithfulness, context precision/recall) over a labeled clause-QA set, gated in CI, to quantify #8 before/after.

*Verification:* eval scores improve vs. baseline; latency budget respected (p95).

### Phase 3 — Ingest robustness & scale (1 sprint)

10. **M3**: Add visibility-timeout/heartbeat recovery to the ingest queue (don't blindly drain the processing list on boot); make `process_contract_ingest_job` provably idempotent; add DLQ alerting + a reprocess endpoint.
11. **M4**: Add a short-TTL identity cache in `get_current_user`, invalidated by the existing `min_iat` signal.

### Phase 4 — Architecture reconciliation (ongoing)

12. Decide the fate of the **graphiti/langgraph sidecar services** (remove if vestigial, or wire them in). Add the backend + MongoDB + nginx to the prod compose (or document the real deployment manifest, e.g. k8s).
13. Decide on **Intent Router** and **Text2Mongo structured-query** boxes: build them if conversational/agentic UX is a goal, otherwise **update the diagrams** to reflect the typed-REST reality.
14. Optionally add the diagram's **PostgreSQL checkpoint store** for resumable long drafts, and a **user-feedback loop** feeding the eval set.

---

## 6. Decision Log / Open Questions for the Team

- **Worker topology**: how many backend replicas/uvicorn workers in prod? (Determines H2 urgency.)
- **graphiti/langgraph services**: still deployed, or can they be retired? (`GRAPH_PROVIDER=direct_falkor` suggests retire.)
- **NL→register queries**: is conversational querying of variation/BG/IPC registers a product requirement, or are typed endpoints the intended final design?
- **LLM data processing**: is there a DPA/zero-retention agreement with the model provider? (Drives how aggressive H3 redaction must be.)
- **Diagram of record**: should the diagrams be updated to match the safer typed-REST + FalkorDB + Vite reality, or is the code expected to converge to the diagrams?
