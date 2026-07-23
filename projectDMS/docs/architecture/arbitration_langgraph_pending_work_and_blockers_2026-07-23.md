# Arbitration Pleadings LangGraph — Pending Work & Blocker Audit

**Audit date:** 2026-07-23
**Auditor method:** Graphify graph for orientation, then direct source verification of every material claim
**Source requirement:** `docs/architecture/arbitration_pleadings_langgraph_workflow_audit_and_implementation_plan_2026-07-21.md`
**Progress tracker cross-checked:** `ARBITRATION_LANGGRAPH_IMPLEMENTATION_PROGRESS.md` (last updated 2026-07-22)
**Repository:** `C:\SaaS\projectDMS`
**Runtime status (verified):** `ARBITRATION_ENGINE_DEFAULT=arbitration_v2`, `ROLLOUT_MODE=off`, `PRIMARY_PERCENT=0`, `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false` — fail-safe, LangGraph is **not** primary.

---

## 0. Verification Honesty Note (what was and was not exercised)

This audit **verified code presence and wiring against current source**. It did **not** independently re-run the automated suite, because the local environment cannot:

- Repo `.venv` (`.venv/Scripts/python.exe`, Python 3.10.3) has **neither** `fastapi` **nor** `langgraph` installed.
- The system Python has `fastapi 0.115.11` but **no** `langgraph` / `langgraph.checkpoint.mongodb`.
- The full arbitration workflow suite therefore requires the **production backend image** (as the progress tracker itself states). This is an environment limitation, **not** a code failure.

Every "implemented" status below is backed by a file/line citation. Every "passed test" figure is **as reported by the progress tracker**, not re-executed here, and is labelled as such. All acceptance/blocker items are stated as **open until proven in a production-like environment**.

---

## 1. Executive Summary

The arbitration LangGraph migration is **code-complete through Phase 6**. All six phases have committed implementations, a fail-safe rollout configuration, and (per the tracker) broad local unit + partial production-image coverage. The subsystem correctly **remains on `arbitration_v2`** with production acceptance `false`.

**The remaining work is overwhelmingly acceptance/verification, not new code.** Two genuine external blockers remain, plus a set of production-like acceptance gates and a small number of partial code items.

Since the tracker's 2026-07-22 snapshot, the code has **advanced further** (verified today):

| Advance since tracker | Evidence |
|---|---|
| OpenAI PDF file-input fix **applied** | `services/openai_service.py:105` now uses `{"type":"file","file":{"file_id":file_id}}` (the correct format) |
| Historical paragraph backfill **tooling added** | `services/arbitration_drafting/historical_paragraph_review.py` (read-only audit + safe single-candidate resolution) |
| Server-backed acceptance governance **added** | `services/arbitration_drafting/acceptance.py` (HMAC-signed receipts over the 14 global criteria) + migration `v20260722_0004` |
| Effect lease-recovery **added** | migration `v20260723_0001_arbitration_effect_recovery.py` |

Net: two release blockers (one now **code-fixed, live re-verify pending**; one **still open**) and a defined acceptance backlog stand between the current state and a bounded primary rollout.

---

## 2. Verified Implementation Status by Phase

Ratings: **DONE** (code + local tests present), **DONE (accept. pending)** (code present; production-like acceptance open), **OPEN** (material code missing).

| Phase | Scope | Code status | Evidence (file:line / artifact) |
|---|---|---|---|
| **0 — Containment** | Close approval/provenance/export/new-matter/section-regen bypasses (P0-01…P0-08) | **DONE** | `case_workspace.py`, `context.py`, `service.py`, `validator.py`; migration `v20260721_0001_arbitration_phase0_containment.py` |
| **1 — Engine abstraction + snapshots + effect ledger** | `ArbitrationWorkflowEngine`, v2 adapter, immutable snapshots, effect/approval/event/plan repos, CAS, atomic version allocation | **DONE** | `engines/base.py`, `engines/v2.py`, `engines/policy.py`, `workflow_repository.py`, `workflow_domain.py`; migration `v20260721_0002` |
| **2 — Graph skeleton + durable human gates** | Official `StateGraph` + Mongo saver, minimal typed state, 8 interrupts, 202/state/resume/cancel APIs, redacted ops checkpoints | **DONE (accept. pending)** | `langgraph_engine.py:243-339` (all nodes/edges), `workflow_service.py`; checkpoint TTL migration `v20260722_0001` |
| **3 — Evidence fan-out + deterministic merge + type routing** | 10 typed read-only analysis branches, deterministic artifact-set hash, opponent-pleading immutable parse, drift invalidation | **DONE (accept. pending)** | `langgraph_engine.py:246-251` (parallel analyze nodes), `workflow_domain.py`, `paragraph_positions.py` |
| **4 — Plan + drafting + validation + bounded remediation** | Versioned plan + counsel approval, 8 validation branches, capped duplicate-citation remediation, parent lineage | **DONE (accept. pending)** | `workflow_validation.py`, `workflow_service.py`, `generator.py`; graph `phase4-v1`, state schema `2` (`core/config.py:230-231`) |
| **5 — Shadow/canary + operational hardening** | Fail-safe shadow/canary, durable Redis filing-export queue (leases/heartbeats/dead-letter/effect keys), health/metrics | **DONE (accept. pending)** | `filing_export_queue.py`, `workflow_hardening.py`, `shadow_candidate.py`; migrations `v20260722_0003`, `v20260723_0001` |
| **6 — Controlled primary rollout** | Cutover controls + signed acceptance receipt; activation withheld | **DONE — activation correctly withheld** | `acceptance.py`, `approval_policy.py`; config gates `core/config.py:222-262`; migration `v20260722_0004` |

**Graph fidelity check (verified):** every node in the requirement's proposed target graph (§8.2/§8.3) is present in `langgraph_engine.py` — `validate_intake`, `capture_input_snapshot`, `document_selection_gate`, five parallel `analyze_*` nodes, `merge_evidence_and_matrices`, `material_question_gate`, `matrix_review_gate`, `readiness_approval_gate`, `build_pleading_plan`, `plan_approval_gate`, draft/validation fan-out + `merge_validation_artifacts`, `legal_review_gate`, `draft_approval_gate`, `export_authorization_gate`, `complete`/`cancelled`. Routing uses `continue_or_cancel` conditional edges throughout.

**Migrations present (9, verified):** `v20260705_0001/0002/0003`, `v20260721_0001/0002`, `v20260722_0001/0002/0003/0004`, `v20260723_0001`.

**Test surface (verified to exist; results as reported by tracker, not re-run):** `test_arbitration_drafting.py` (**124** test functions), plus `test_arbitration_checkpoint_ttl_integration.py`, `test_arbitration_http_isolation.py`, `test_filing_export_queue.py`, `test_filing_export_queue_integration.py`. Tracker reports the arbitration suite at 82–124 passing across runs and a 99-test production-image acceptance run.

---

## 3. Blocker Register (current, verified)

| # | Blocker | Type | Verified current status | Owner action to clear |
|---|---|---|---|---|
| **B1** | Live OpenAI uploaded-PDF extraction returned no content (tracker 2026-07-22) | External integration | **Code fix applied** — `openai_service.py:105` uses `{"type":"file","file":{"file_id":file_id}}`; enhanced error logging present. **Live round-trip re-verification pending.** | Re-run live uploaded-PDF extraction against the production OpenAI path; capture a passing round-trip artifact. |
| **B2** | FalkorDB vector round-trip fails — deployed image exposes `GRAPH.*` but not the `FT.*` (RediSearch) commands `FalkorDBVectorService` expected | Architecture mismatch | **RESOLVED (2026-07-23).** Chose Approach A: the broken path was removed and the system standardizes on Qdrant. `services/falkordb_vector_service.py` (write-only, never read, `FT.*`-dependent) was deleted; its only caller `services/data_sync.py` now writes Qdrant-only; vector-only config (`FALKORDB_INDEX_NAME`, `FALKORDB_VECTOR_DIM`, `falkordb_index_name`, `falkordb_vector_dim`) and the two dead integration tests were removed. FalkorDB's `GRAPH.*` knowledge-graph use is untouched. Verified: no dangling refs in `backend/`, all changed files compile, config suite 15/15, integration suite collects 4 tests. | Done — no further action. The `FT.*` mismatch cannot recur because nothing calls RediSearch anymore. |
| **B3** | Kill/restart-at-**every** graph node & human gate not proven against production MongoDB | Recovery acceptance | **Open** — only the **document-selection** interrupt has a production restart/resume proof. | Run interrupt→backend-restart→resume at every gate and Phase-4 node against production Mongo. |
| **B4** | Authenticated **browser** + **legal-reviewer** end-to-end flows unexercised | HITL acceptance | **Open** — signed-JWT API isolation passed, but is not a substitute for a real signed-in browser + separated legal-review exercise. | Execute full browser workflow with two real tenant accounts and separated reviewer/legal roles. |
| **B5** | S3/object-storage outage + live-model rate-limit + sustained cross-service load untested | Capacity/resilience | **Open.** | Run S3 failure/recovery, model rate-limit, and sustained end-to-end concurrency drills. |
| **B6** | No production samples for all four pleading types (SoC, SoD, Counterclaim, Rejoinder) | Coverage | **Open** — production contains zero arbitration cases/runs/drafts, so no acceptance window or sample coverage exists. | Produce reviewed golden runs for each of the four pleadings in a production-like scope. |
| **B7** | Historical defence/rejoinder rows unmigrated where paragraph-response mapping is ambiguous | Data quality | **Tooling exists** (`historical_paragraph_review.py`, read-only + single-candidate resolution). **Production backfill run pending.** | Run the conservative audit on production data; resolve unambiguous rows; escalate ambiguous rows to manual review. |
| **B8** | Stakeholder / legal / security **sign-off** and signed acceptance receipt absent | Governance | **Open** — receipt mechanism is built (`acceptance.py`, HMAC over 14 criteria) but **not issued**, by design. | Obtain sign-off only after B1–B7 clear; then issue the signed receipt and set `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED` deliberately. |

---

## 4. Pending-Work Register (phase-wise)

### Phase 0 — Containment
- **P0-A (acceptance):** Authenticated two-tenant browser exercise of reviewer-role/step-up flows and real filing-export artifacts. *(Code done; only the perimeter `401` probes and API-fixture isolation proven.)*
- **P0-B (data):** Confirm every migrated historical approval/readiness row either carries evidence of approval or was returned to review (spot-audit on production).

### Phase 2 — Durable graph + gates
- **P2-A:** Kill/restart-at-every-node/gate on production Mongo (**B3**).
- **P2-B:** TTL-expiry coverage at **every** checkpoint (only the exercised path is proven).

### Phase 3 — Fan-out + merge + routing
- **P3-A:** Production bounded-concurrency/load test of the 10-way fan-out against Mongo/Qdrant/S3/model limits (**B5**).
- **P3-B:** Production data-quality review + conservative source-version backfill for unversioned/absent source references (related to **B7**).

### Phase 4 — Plan/draft/validate/remediate
- **P4-A:** Production-image real-Mongo checkpoint compatibility + restart at the new plan/draft/validation/remediation nodes (**B3**).
- **P4-B:** Authenticated legal-review/revision browser flow (**B4**).

### Phase 5 — Shadow/canary + hardening
- **P5-A:** Clear **B1** (OpenAI live PDF re-verify). ~~**B2** (FalkorDB/RediSearch)~~ **resolved 2026-07-23** — FalkorDB vector path removed, standardized on Qdrant.
- **P5-B:** S3/model outage + sustained-load drills (**B5**).
- **P5-C:** Complete every-node Mongo restart (shared with **B3**) and the security/privacy review for snapshots/checkpoints/step-up.
- **P5-D:** Establish the shadow parity metric window (8 redacted parity dimensions exist; a real comparison window across all four pleadings is pending).

### Phase 6 — Primary rollout
- **P6-A:** Golden SoC/SoD/Counterclaim/Rejoinder samples pass legal review + grounding assertions (**B6**).
- **P6-B:** Operator recovery proof + legal/security sign-off (**B8**), then issue the signed acceptance receipt.
- **P6-C:** Only then enable a **bounded** primary scope; keep immutable `v2` fallback and monitoring; defer `v2` deprecation until parity/recovery windows are met.

### Cross-cutting UI (partial)
- **U-1:** Rich page-level document **preview/selector** and visual **version diff** remain partial (immutable opponent/document IDs are exposed; snippets + version history exist in current views).

---

## 5. Phase-wise Plan to Primary Rollout

Ordered by dependency. Code is largely done; each step is an **acceptance gate**.

**Step 1 — Clear external integration blockers (Phase 5 core).**
- Re-verify OpenAI live PDF round-trip (B1); capture passing artifact.
- ~~Resolve FalkorDB/RediSearch (B2)~~ **Done 2026-07-23** — removed the broken `FalkorDBVectorService`; Qdrant is the sole vector store.
- Exit criterion: OpenAI round-trip green. (B2 closed: no RediSearch dependency remains.)

**Step 2 — Prove durable recovery at every node (Phase 2/4).**
- Interrupt→restart→resume at every gate and Phase-4 node on production Mongo (B3); every-node TTL-expiry.
- Exit criterion: zero duplicate authoritative effects; stale state-version resumes return `409`.

**Step 3 — Prove capacity + resilience (Phase 3/5).**
- Bounded-concurrency/load on the fan-out; S3/model outage + rate-limit drills (B5).
- Exit criterion: agreed latency/error thresholds hold; retries stay bounded and transient-only.

**Step 4 — Authenticated HITL acceptance (Phase 0/4).**
- Full browser workflow with two real tenants and separated reviewer/legal roles (B4); finish page-preview/version-diff UI (U-1).
- Exit criterion: role gates, author/approver separation, drift-invalidation, and governed export all pass in-browser.

**Step 5 — Data-quality closure (Phase 3).**
- Run `historical_paragraph_review.py` on production; resolve unambiguous rows; queue ambiguous rows for manual review (B7).
- Exit criterion: no silent upgrades; ambiguous rows are `needs_review`/`missing`, never guessed.

**Step 6 — Golden legal samples (Phase 6).**
- Reviewed SoC, SoD, Counterclaim, Rejoinder runs + missing-evidence/contradictory-expert variants (B6), evaluated on source IDs/gates/citations, not prose.
- Exit criterion: all four pass legal review + grounding assertions.

**Step 7 — Sign-off + bounded activation (Phase 6).**
- Legal/security/operator sign-off (B8); issue HMAC acceptance receipt; set `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=true` deliberately.
- Enable bounded primary scope (allowlist/small percent); retain `v2` fallback + monitoring.
- Exit criterion: all 14 §14.2 global criteria true; no open P0/P1; operators can recover a paused/failed run.

---

## 6. Global Acceptance Criteria Status (requirement §14.2, verified)

| # | Criterion | Status | Note |
|---|---|---|---|
| 1 | Phase-0 bypasses closed + negative authorization tests | **Met (code) / browser pending** | P0-01…08 done; browser acceptance = B4 |
| 2 | Every authoritative run has immutable input/evidence/opponent snapshots + hashes | **Met (code)** | `workflow_repository.py`, `workflow_domain.py` |
| 3 | No raw evidence/draft in checkpoints or ops responses | **Met (code)** | Checkpoint state = IDs/hashes; fail-closed on raw/oversized |
| 4 | Crash/restart at every node & gate resumes without duplicate effects | **Partial** | Only document-selection proven — **B3** |
| 5 | All four pleading routes enforce required predecessors/matrices/gates | **Met (code)** | `workflow_domain.py` routing; SoD/Rejoinder require chronology; Counterclaim requires issue framing |
| 6 | New matter blocked without permission-obtained receipt | **Met (code)** | P0-07 corrected semantics + dedicated endpoint |
| 7 | Every export references one approved immutable version + readiness + passing citation/exhibit audit | **Met (code)** | P0-06 governed export |
| 8 | No client/edit/generate path can self-approve | **Met (code)** | P0-01/02 + `approval_policy.py` separation |
| 9 | Parallel merge deterministic + auditable source revisions | **Met (code) / prod-load pending** | Artifact-set hash stable serial/parallel — load = **B5** |
| 10 | Retry bounded, transient-only, observable, non-duplicating | **Met (code)** | Retry budget + effect keys (`core/config.py`, effect ledger) |
| 11 | `v2` fallback reuses original snapshot, records reason, cannot overwrite candidate | **Met (code)** | Force-v2 binds immutable snapshot |
| 12 | Golden SoC/SoD/Counterclaim/Rejoinder pass legal review + grounding | **Open** | **B6** |
| 13 | Real Mongo/Redis/Qdrant/S3/live-model + load + backup/restore + browser tests pass | **Partial** | Redis/Qdrant/Mongo-TTL/restore passed; B2 (FalkorDB) resolved by removal; S3/model/browser open — **B1/B4/B5** |
| 14 | Runbooks cover paused/failed/drift/stuck-lease/fallback/export recovery | **Met (doc)** | `docs/architecture/arbitration_langgraph_operations_runbook.md` |

**Met (code): 10/14. Partial/Open: 4/14** — all four gated on production-like acceptance, not new code.

---

## 7. Bottom Line

- **Code:** complete through Phase 6; graph faithfully matches the proposed architecture; config is fail-safe.
- **Not primary — correctly.** `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false` is the deliberate gate.
- **To reach bounded primary rollout, clear in order:** ~~B2~~ **(resolved 2026-07-23 — FalkorDB vector path removed, Qdrant standardized)** → B1 (fix applied → re-verify) → every-node restart (B3) → capacity/resilience (B5) → authenticated browser + legal HITL (B4) → historical backfill run (B7) → golden four-pleading samples (B6) → sign-off + signed receipt (B8).
- **No P0/P1 code item is currently open.** The critical path is verification, integration, and governance — not implementation.
