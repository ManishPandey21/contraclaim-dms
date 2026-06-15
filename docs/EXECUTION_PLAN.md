# ContraclaimDMS — 30-Day Execution Plan & 90-Day Roadmap

> Derived from the end-to-end repository audit. Every task is grounded in the
> actual codebase (files/modules named inline). Status reflects work already
> merged: `Hotfix #1` (retrieval-engine authorization consolidated onto
> `PolicyService`, `_ensure_scope` removed, isolation tests added) and
> `Hotfix #2` (84 dead files removed, `forbid-dead-code-paths` pre-commit guard).

## 0. Assumptions & operating model

- **Team:** 1–2 engineers + product owner. Estimates are in **dev-days**; a solo
  dev should expect the 30-day plan to land in ~6–7 weeks.
- **Branch discipline:** one PR per task ID; CI (gitleaks, pre-commit, pytest,
  Trivy) must be green before merge to `main`.
- **Definition of Done:** code + tests + CI green + docs updated + verified on staging.
- **Already complete (do not re-do):** `Hotfix #1` and `Hotfix #2` — roughly 60%
  of Week 1.

### Grounding facts (measured)

| Metric | Value |
|---|---|
| Total routers | 42 |
| Routers using `PolicyService` | 32 |
| Routers using `require_permission` | 34 |
| Routers still using legacy `has_permission` (direct `db.roles` query) | **10** |
| Routers using `authorize_scope` / `build_scope_query` | 24 / 6 |
| Payment scaffolding | `PaymentGatewayInterface` ABC + Stripe/Razorpay factory exist; only `NoOpPaymentGateway` implemented |
| `console.log` in `client/src` | 20 |
| Root markdown docs to consolidate | 35 |

---

## 30-DAY PLAN

### Week 1 — Stop the bleeding (Authorization consolidation + cleanup)

| ID | Task | Files / modules | Acceptance criteria | Effort | Risk / rollback |
|----|------|-----------------|---------------------|--------|-----------------|
| W1.0 | ✅ DONE: retrieval auth + archive/duplicate removal + dead-code guard | `routers/retrieval_engine.py`, `.pre-commit-config.yaml` | Merged (`34459fa`) | — | — |
| W1.1 | Migrate the **10 routers still using legacy `has_permission`** to `require_permission` + `PolicyService.authorize` | the 10 routers; `core/security.py` | 0 routers reference `has_permission`; behavior-parity tests pass | 3 d | Permission-name mismatch → run RBAC matrix (W2.2) before merge |
| W1.2 | Define **one canonical authz pattern** and document it. Keep `build_scope_query` (legitimate list-filtering) and `authorize_scope`, but route every **mutation** through `PolicyService.authorize`/`authorize_document` | new `docs/AUTHZ.md`; spot-fix routers | `docs/AUTHZ.md` published; mutations audited | 2 d | Low |
| W1.3 | **Deprecate** `has_permission`/`require_roles` (`warnings.warn` + docstring); add CI grep guard forbidding new `has_permission(` usage and any `import .*archive` | `core/security.py`, `.pre-commit-config.yaml` | CI fails on new legacy usage / archive import | 0.5 d | Low |
| W1.4 | Backfill audit emission on migrated routers | migrated routers, `services/audit_event_service.py` | every authz decision emits a `policy.authorize` audit event | 1 d | Low |

**Exit:** one authorization mechanism for mutations; legacy helpers deprecated and CI-guarded; `docs/AUTHZ.md` published.

### Week 2 — Prove isolation (business-critical)

| ID | Task | Files / modules | Acceptance criteria | Effort | Risk |
|----|------|-----------------|---------------------|--------|------|
| W2.1 | **Multi-tenant isolation suite** over *every* list/read/download/RAG/mutation endpoint: Org-A token vs Org-B resource ⇒ 403/empty | new `tests/test_tenant_isolation.py`; `httpx` + CI `mongo` service + `initial_data/` seeds | Every endpoint covered; Org-A can never see Org-B; suite gates CI | 4 d | Will surface real leaks — that's the point |
| W2.2 | **RBAC matrix suite** — role × action × scope (extend `test_rbac_policy_hardening.py`) | new `tests/test_rbac_matrix.py` | Every combination asserted | 2 d | Med |
| W2.3 | **Fix every gap** found by W2.1/W2.2 | wherever found | All isolation/matrix tests green | 2 d | Variable — buffer here |
| W2.4 | **JWT `type` claim**: add `"type":"access"` in `create_access_token`; `get_current_user` rejects non-`access`; step-up/refresh can't be used as access | `core/security.py`, `routers/auth.py`, `services/step_up_service.py` | Token without `type=access` → 401; tests added | 1 d | Low (additive, back-compat window) |
| W2.5 | **Logout/session alignment**: today `get_current_user` checks Redis `user_jwt_min_iat` but **not** session-active, so a token still works after logout until expiry. Reject invalidated sessions (check `is_session_active`, and/or set `min_iat` on logout & password change) | `core/security.py`, `services/authentication_service.py`, `routers/auth.py` | Token used after logout → 401 | 1.5 d | Med — adds a Redis read/request; cache it |

**Exit:** isolation suite green **and gating CI** (the single most important audit deliverable); JWT `type` enforced; logout is immediate.

### Week 3 — Make it sellable (billing + UX)

| ID | Task | Files / modules | Acceptance criteria | Effort | Risk |
|----|------|-----------------|---------------------|--------|------|
| W3.1 | Implement **concrete payment adapter** behind existing `PaymentGatewayInterface` (factory already branches `stripe`/`razorpay`; only `NoOpPaymentGateway` built) | `services/payment_gateway.py` | Provider implements create_customer/subscription/invoice/charge; unit tests with mocked SDK | 4 d | Provider choice is a decision |
| W3.2 | **Webhook endpoint** `/api/billing/webhooks/{provider}` with signature verification + **idempotent** handling → updates `subscriptions`/`entitlements`/`billing_records` | new `routers/billing_webhooks.py`, `services/subscription_lifecycle_service.py` | Signed webhook updates entitlements; replay is a no-op | 3 d | High — verify signatures, never trust body alone |
| W3.3 | Wire `MonetizationService.create_subscription/convert_trial/add_addon` to the gateway; reconcile entitlements on success/failure | `services/monetization_service.py`, `services/entitlement_service.py` | Paid subscription flips entitlement to active; dunning on failure | 2 d | Med |
| W3.4 | Frontend **checkout + subscription management** (`SubscriptionManagementPage.tsx` exists) + `PlanSettingsPage` | `client/src/pages/SubscriptionManagementPage.tsx` | Subscribe with **test card** end-to-end on staging | 3 d | Med |
| W3.5 | Collapse remaining **page overlap**: `DocumentsPage` vs `EnhancedDocumentsPage`, `UploadPage` vs `ContractsUploadPage` | `client/src/routes.tsx` | One documents page + one upload entry; routes updated | 1.5 d | Low |
| W3.6 | Replace **20 `console.log`** with `lib/error-logger` + lint rule | `client/src` | 0 `console.log` in `client/src` | 0.5 d | Low |
| W3.7 | **Onboarding wizard + demo seed** (build on `initial_data/default_*`) | new `initial_data/demo_seed.py`; first-run UI | One command seeds demo org/project/docs; guided first-run | 2 d | Low |

**Exit:** test-card subscription completes end-to-end on staging; webhooks idempotent + signature-verified; demo data is one command.

**Decision — payment provider:** India-first EPC/infra ICP (AWS `ap-south-1`, INR) → **Razorpay** is the pragmatic default (UPI/netbanking/cards, GST invoicing); **Stripe** if early pilots are global/multi-currency. The interface supports either; pick one for v1, keep the other behind the factory.

### Week 4 — Make it operable

| ID | Task | Files / modules | Acceptance criteria | Effort | Risk |
|----|------|-----------------|---------------------|--------|------|
| W4.1 | **Scheduled backups**: `mongodump` (replica set) + **Qdrant snapshot** (snapshot path already in `docker-compose.prod.yml`) + FalkorDB RDB/AOF → S3 (AWS creds set); retention | new `scripts/backup.sh`, cron sidecar in `docker-compose.prod.yml` | Nightly backup runs; artifacts in S3; alert on failure | 2 d | Med |
| W4.2 | **Restore runbook** — *tested* restore of Mongo + Qdrant + Falkor to a clean env | `docs/OPERATIONS.md` | A timed, documented, **executed** restore drill passes | 1.5 d | High value — untested backups don't count |
| W4.3 | **TLS**: terminate in the `gateway` (httpd) on :443 with ACM/Let's Encrypt **or** document external LB termination; HSTS already in `config/httpd.conf` | `config/httpd.conf`, `docker-compose.prod.yml`, `docs/OPERATIONS.md` | TLS A-grade (SSL Labs) on pilot domain | 1.5 d | Med |
| W4.4 | **Docs consolidation**: 35 root `.md` → `docs/OPERATIONS.md` + `docs/ARCHITECTURE.md`; rest to `docs/history/` | root `*.md`, `docs/` | ≤3 top-level docs; history archived | 1 d | Low |
| W4.5 | **First design-partner pilot deploy**: provision, run `settings.validate_runtime_configuration()`, smoke tests, observability dashboards | `docker-compose.prod.yml`, `routers/health.py`, `observability/` | Pilot live, healthchecks green, backups running | 2 d | Med |

**Exit:** verified nightly backups + tested restore; TLS A-grade; ≤3 top-level docs; pilot environment live.

### 30-day effort summary

~**41 dev-days**. With 2 engineers ≈ 4 calendar weeks; **solo ≈ 6–7 weeks**. If the
pilot date is fixed, defer W3.7 (onboarding polish) and W4.4 (doc cleanup); protect
W2 (isolation) and W3.1–W3.3 (billing).

---

## 90-DAY ROADMAP (outcomes, not just tasks)

### Month 1 — Harden & go live
- **Scope:** the entire 30-day plan **+ first PAID pilot live on one project.**
- **Success metrics:** pilot contract signed; first payment collected; baseline
  captured for "drafting time reduced %"; zero P1 isolation incidents.

### Month 2 — Deepen the moat

| Epic | What | Grounding | Success metric |
|------|------|-----------|----------------|
| M2.1 Hybrid + reranked RAG | Replace naive substring scoring in `_search_mongo` with BM25/Atlas Search hybrid + cross-encoder rerank; token-budgeted context (replace `[:6000]` truncation) | `retrieval/service.py` | Retrieval precision@5 ↑, citation coverage ↑ |
| M2.2 Traceability UI | Surface `IterationTrace`/citations the engine already produces | `retrieval/models.py` | Click a claim → source clause |
| M2.3 Client audit/export reports | Arbitration-grade PDF/CSV from `document_audit_events`/`audit_events` | `services/export_service.py` | Pilot exports a defensible audit pack |
| M2.4 SSO option | OIDC/SAML for enterprise login | `routers/auth.py` | One SSO-capable login |
| M2.5 Second pilot | Onboard pilot #2 | — | Second pilot signed |

### Month 3 — Scale to revenue

| Epic | What | Grounding | Success metric |
|------|------|-----------|----------------|
| M3.1 Self-serve billing GA | Signup → trial → paid with no sales touch | `MonetizationService.start_trial/convert_trial` | First self-serve conversion |
| M3.2 Usage-metered AI add-on | Meter + invoice AI drafting from `usage_events` | `usage_events`, `quota_buckets` | Metered revenue line live |
| M3.3 Enterprise tier | Data-isolation attestation + single-tenant/on-prem option | `docker-compose.prod.yml`, isolation suite as evidence | One enterprise-ready profile |
| M3.4 Convert pilots → subscriptions | Move pilots onto paid plans | billing | ≥1 pilot converted; MRR > 0 |

---

## Critical path & dependencies

1. **W2.1 isolation suite gates the pilot** — do not deploy multi-tenant prod without it.
2. **W3.1–W3.3 billing gates "paid"** in Month 1.
3. **W4.1–W4.2 backups gate "production"** — a pilot without tested restore is a liability.
4. **M2.1 (hybrid RAG)** depends on a healthy prod vector backend (Qdrant, already in compose).

## Top risks

| Risk | Mitigation |
|------|-----------|
| Isolation suite finds real cross-tenant leaks | That's the goal — buffer W2.3; treat as P0 |
| Webhook spoofing / double-charge | Signature verify + idempotency keys (W3.2) |
| Untested backups | W4.2 is a *drill*, not a doc |
| Solo-dev timeline slip | Defer W3.7/W4.4; protect W2 and W3.1–3.3 |
| Provider lock-in | Keep `PaymentGatewayInterface`; don't leak SDK types into `MonetizationService` |

## Decisions required

1. **Payment provider for v1** — Razorpay (India-first, recommended) or Stripe (global)?
2. **First pilot customer/segment** — which EPC/PMC project? (drives demo seed + onboarding)
3. **On-prem in Month 3?** — yes changes the deployment architecture now (keep single-tenant-clean).
