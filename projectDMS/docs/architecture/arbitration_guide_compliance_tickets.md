# Arbitration Pleadings — Guide-Compliance Tickets

Date: 2026-07-05

Derived from a strict validation of the arbitration pleadings workflow against
`SoC_SoD_Rejoinder_Preparation_Guide.md` (treated as source of truth). Each ticket
cites the evidence and the files to touch. Suggested sequencing is at the end.

## EPIC: ARB-100 — Guide-compliance hardening for arbitration pleadings workflow

---

### ARB-101 — Replace deterministic agents with real LLM prompt/parse (or formally ratify the baseline)

**Priority:** P0 · **Size:** L · **Deviation type:** core intent · **Status: DONE (2026-07-05)**

> **Implementation notes.** LLM path chosen. `agents/llm.py` adds `LLMArbitrationAgent`
> (subclass of the deterministic agent, same persistence/idempotency/scoping machinery)
> with LLM-driven handlers for clause-interpretation, claim-identification, issue-framing,
> defence-analysis (real implementation replacing the stub), and document-understanding.
> Chronology, document-indexing, quantum, and notice-compliance deliberately stay
> deterministic (amounts/events must trace to registers). `agents/__init__.py` dispatches
> by `options.agent_mode` > `ARBITRATION_AGENT_MODE` env > `deterministic` default, with
> deterministic fallback + warning when no LLM client is configured. Guardrails: prompts
> contain only scoped source rows keyed by id; rows citing unknown ids are rejected;
> LLM rows are always `needs_review` (`auto_approve` ignored); amounts/excerpts/metadata
> are copied from source records, never model output. Covered by 7 new tests in
> `test_arbitration_drafting.py` (30 total passing).

**Problem.** "Agents" are deterministic regex/heuristic extractors
(`backend/rbac_backend/services/arbitration_drafting/agents/deterministic.py:13-14`,
model tag `deterministic-matrix-agent`); the generator is `deterministic-source-grounded`
(`backend/rbac_backend/services/arbitration_drafting/generator.py:113-119`). The
guide/spec imply AI-assisted drafting and analysis.

**Tasks**
- [x] Decision gate: LLM path chosen (owner request, 2026-07-05); deterministic remains the default until `ARBITRATION_AGENT_MODE=llm` (or `options.agent_mode`) is set.
- [x] `LLMArbitrationAgent` behind the existing `run_arbitration_agent` contract (`agents/llm.py`, dispatcher in `agents/__init__.py`) — matrix rows, `source_ids`, warnings/errors shape unchanged.
- [x] Source grounding preserved: prompts receive only scoped source rows keyed by id; parser rejects rows citing unknown source ids; drafting-side validator (`validator.py:43-48`) unchanged and still blocks hallucinated `[S#]`/amounts/dates.
- [x] Real `prompt_version`/`model` values recorded on `ArbitrationAgentRun` (queued records too, via `agent_run_metadata`).
- [x] Feature flag deterministic vs. LLM per run; deterministic offline/test fallback with explicit warning.

**Acceptance criteria** *(all verified by tests, 30 passing)*
- Generation still passes all existing tests with deterministic flag on. ✓
- With LLM flag on, a mocked-LLM test produces matrix rows and hallucinated source citations are rejected. ✓ (`test_llm_agent_creates_needs_review_rows_from_mocked_output`, `test_llm_agent_rejects_rows_citing_unknown_sources`)
- No pathway lets an LLM assertion bypass `approval_blockers`: LLM matrix rows are forced to `needs_review` even with `auto_approve`, and the generation validator is untouched. ✓

---

### ARB-102 — Jurisdiction, limitation & pre-arbitration as first-class records

**Priority:** P0 · **Size:** M · **Guide ref:** §2, §5.2 · **Status: DONE (2026-07-05)**

> **Implementation notes.** New `jurisdiction-matrix` slug → `arbitration_jurisdiction_matrix`
> collection with three row types: `limitation` (cause-of-action/rejection/final-bill/
> acknowledgement dates, deterministic expiry computation, status within_limitation /
> at_risk / time_barred / needs_review), `pre_arbitration_step` (seeded checklist:
> dispute_notice, engineer_decision, amicable_settlement, conciliation, cooling_period;
> required + compliance_status per step), and `arbitration_clause_scope`. The jurisdiction
> agent populates all three (per-claim limitation rows, options can supply dates and
> `limitation_period_years`, default 3), is idempotent, and is in the orchestrator default
> sequence. Readiness now includes `limitation_analysis` (time-barred → BLOCKED),
> `pre_arbitration_compliance` (required+incomplete step → BLOCKED, i.e. premature), and
> `arbitration_clause_scope` (out_of_scope → BLOCKED); missing records → NEEDS_LEGAL_REVIEW,
> so `approve-readiness` and draft generation are gated. Indexes in migration
> `v20260705_0002`; matrix editor tab "Jurisdiction & Limitation" added to the workspace UI.
> Note: existing cases will now show the two jurisdiction blockers until rows are added —
> intended behavior per the guide (§2/§5.2).

**Problem.** Jurisdiction agent only copies the arbitration clause and defers everything
to "legal review" (`agents/deterministic.py:391-396`). No limitation analysis,
pre-arbitration compliance, pleading timetable, or amendment-rule records exist.

**Tasks**
- [x] Add `arbitration_jurisdiction_matrix` capturing: cause-of-action date, rejection/final-bill date, acknowledgements, limitation computation + result, pre-arb steps (engineer decision/dispute notice/amicable settlement/conciliation/cooling), scope-of-clause check.
- [x] Register slug in `matrix_registry.py`.
- [x] Jurisdiction agent populates/flags these (`agents/deterministic.py` `_jurisdiction`, `_limitation_analysis`, `_pre_arbitration_steps`).
- [x] Readiness checks `limitation_analysis`, `pre_arbitration_compliance` (and `arbitration_clause_scope`) in `case_workspace.py` `_jurisdiction_readiness` using `NEEDS_LEGAL_REVIEW`/`BLOCKED`.
- [x] Frontend matrix editor entry in `MATRIX_FIELDS` + `MatrixSlug`/`MATRIX_DEFINITIONS`; migration `v20260705_0002` for indexes.
- [ ] Deferred: pleading timetable and amendment-rule records (not covered by acceptance criteria; fold into ARB-108 backlog if needed).

**Acceptance criteria** *(all verified by tests, 35 passing)*
- A case past limitation raises a blocking readiness check. ✓ (`test_readiness_blocks_time_barred_premature_and_out_of_scope_case`)
- Missing pre-arb step blocks `approve-readiness`. ✓ (409 raised in same test; missing records covered by `test_readiness_flags_missing_limitation_and_prearb_records`)
- Unit test covering time-bar and premature-claim flags. ✓ (`test_jurisdiction_agent_computes_limitation_and_seeds_prearb_steps`, `test_jurisdiction_agent_marks_recent_cause_of_action_within_limitation`, `test_readiness_passes_when_limitation_and_prearb_resolved`)

---

### ARB-103 — Expert alignment (§12): ingestion + consistency agent

**Priority:** P1 · **Size:** M · **Guide ref:** §12, §12.1 · **Status: DONE (2026-07-05)**

> **Implementation notes.** New `expert-alignment` slug → `arbitration_expert_alignment`
> collection. The `delay-expert` agent (stub replaced) seeds per-claim checklist rows:
> `delay` rows start `concurrency_addressed=false` with a `concurrency_not_addressed`
> risk flag (guide acceptance), `quantum` rows compare the pleaded amount against
> quantum annexures (match by calculation id, then amount) recording `calculation_match`,
> `verified_amount`, and contradictions (`amount_mismatch` / `quantum_annexure_missing`).
> Expert-looking documents in the document index are linked as `expert_report_source_id`.
> Context builder ingests approved alignment rows as `expert_report` sources
> (`allowed_use=expert`, matrix group `experts`) and `_expert_consistency_warnings`
> emits pleaded-vs-verified amount mismatches, contradiction, and unaddressed-concurrency
> warnings into `context_warnings`, which the drafting service merges into
> `validation_warnings`. Readiness check `expert_alignment` flags delay/quantum claims
> without an approved aligned expert record (fires only when such claims exist).
> Migration `v20260705_0003`; "Expert Alignment" matrix tab in the workspace UI.
> Deferred: technical/contract expert row types exist via the generic matrix editor but
> have no automated seeding yet.

**Problem.** `delay-expert` / `review-consistency` are review-only stubs
(`agents/deterministic.py:96-98, 398-399`); no expert-report source type, matrix, or
alignment checklist.

**Tasks**
- [x] Expert-report source type (`ArbitrationSourceType.EXPERT_REPORT`, `ArbitrationSourceUse.EXPERT`) and ingestion into context (`context.py` `_expert_alignment_sources`).
- [x] `arbitration_expert_alignment` records (expert type, methodology, concurrency addressed, calc-match, contradictions, risk flags, report link).
- [x] Delay/quantum expert alignment checklist agent (`agents/deterministic.py` `_delay_expert_alignment`, replaces stub; in orchestrator sequence).
- [x] Readiness check `expert_alignment`: delay/quantum claims flagged when no aligned expert record (`case_workspace.py` `_expert_readiness`).
- [ ] Deferred: automated seeding for technical/contract expert types; `review-consistency` remains a stub (draft-level consistency is covered by the validator).

**Acceptance criteria** *(verified by tests, 45 passing)*
- Pleaded amount not matching an expert quantum record produces a warning surfaced in `validation_warnings`. ✓ (`test_context_warns_on_expert_amount_mismatch_and_unaddressed_concurrency` — context warnings merge into `validation_warnings` in `service.generate`)
- Concurrency-not-addressed flag appears for EOT claims. ✓ (`test_delay_expert_agent_flags_concurrency_and_amount_mismatch`, readiness in `test_expert_alignment_readiness_blocks_then_ready`)

---

### ARB-104 — Filing bundle: embed actual exhibit files + volume structure + pin-cite audit

**Priority:** P1 · **Size:** M · **Guide ref:** §19.1–19.3, §9

**Problem.** Bundle is summary + metadata JSON + draft markdown only; it does not embed
source document files or the Volume 1–8 structure (`case_workspace.py:835-864`). Citation
audit only matches `C/R/J/…-\d+` and `[S#:]` keys, not page/para pin-cites
(`case_workspace.py:708-802`).

**Tasks**
- [ ] Resolve `source_file_link`/`source_id` for each exhibit and stream the actual file into the ZIP under guide volumes (Volume 1 pleading, 2 contract, 3 correspondence, …).
- [ ] Add exhibit→file mapping and handle missing files as blocking citation-audit issues.
- [ ] Extend citation audit to detect page/paragraph pin-cites (e.g. `C-12, p.3 ¶4`) and verify against `page_numbers`.
- [ ] Keep existing JSON manifest + summary for machine consumption.

**Acceptance criteria**
- ZIP contains at least one real exhibit binary under a volume folder.
- Citation audit flags a pin-cite whose page is absent from the exhibit's `page_numbers`.
- Existing `test_filing_bundle_zip_contains_manifest_matrices_and_drafts` still passes.

---

### ARB-105 — Missing pleading sections (SoC index/interest/costs/verification; SoD objections/interest reply)

**Priority:** P1 · **Size:** M · **Guide ref:** §13 (Steps 2, 11, 12, 14), §14 (Steps 2, 8)

**Problem.** SoC generator lacks Index, Interest, Costs, Verification sections; SoD
`preliminary_objections` body is a hard-coded `[Evidence required]` placeholder
(`generator.py:165-224`, `generator.py:210`).

**Tasks**
- [ ] Add `index`, `interest`, `costs`, `verification` to SoC section set + `SECTION_KEYS_BY_DRAFT_TYPE` (`generator.py:9-56`).
- [ ] Drive SoD `preliminary_objections` from an objections matrix/list instead of the static placeholder (`generator.py:210`).
- [ ] Add SoD/Rejoinder "reply to interest and costs" section.
- [ ] Interest section wired to `interest_rate` (`models/arbitration_drafting.py:480`) and an interest quantum annexure (see ARB-106).

**Acceptance criteria**
- Generated SoC contains distinct Index, Interest, Costs, and Verification headings.
- Preliminary objections render from matrix rows (test with ≥1 objection row).

---

### ARB-106 — Quantum completeness: delay↔cost link + interest computation + claim-summary rollup

**Priority:** P2 · **Size:** M · **Guide ref:** §11.2, §11.3

**Problem.** No delay-event→cost-head link table; interest is only a `calculation_type`
label with no computation; claim-summary totals not auto-rolled up
(`case_workspace.py:1257-1267`, `agents/deterministic.py:252-300`).

**Tasks**
- [ ] Add delay-event→cost-head linkage fields to quantum annexures (period, CP impact days, cost head, evidence).
- [ ] Implement interest computation (rate × period) producing an interest annexure.
- [ ] Auto-generate a claim-summary rollup (principal/interest/total) per §11.2.

**Acceptance criteria**
- Interest annexure amount computed and validated against `interest_rate`.
- Claim summary totals equal the sum of component annexures.

---

### ARB-107 — Tenant isolation: org-scoped case lookup + tests; live API & frontend E2E

**Priority:** P1 · **Size:** M · **Dimension:** RBAC + test coverage · **Status: DONE (2026-07-05)**

> **Implementation notes.** `get_case` now accepts a tenant `scope` filter and the
> router's `_load_case_and_authorize` passes `build_scope_query(current_user)`, so a
> cross-tenant case id 404s without leaking existence (policy `authorize_document`
> still runs as the second layer). All matrix/agent-run/exhibit/readiness routes go
> through the scoped case load. New `test_arbitration_http_isolation.py` (7 tests)
> follows the `test_http_isolation.py` pattern — real FastAPI app + real
> PolicyService/ScopeService, stubbed permission/entitlement gates, seeded two-tenant
> fake db: case list scoping, 404 on cross-tenant case get/patch/matrix/agent-runs/
> exhibit-list/readiness, 403 on cross-tenant draft reads, 403 on creating a case
> under a foreign organization, superadmin retained. Frontend:
> `ArbitrationCaseWorkspacePage.test.tsx` (vitest + testing-library) renders the
> matrices section, asserts all matrix tabs (incl. Jurisdiction & Limitation and
> Expert Alignment), and verifies tab interaction reloads rows for the selected matrix.

**Problem.** `get_case` looks up by `_id` only (`case_workspace.py:146-150`); isolation
relies solely on `policy.authorize_document`. No cross-tenant test, no live API/router
test, no frontend test in the arbitration suite.

**Tasks**
- [x] Org scoping on `get_case` (scope param applied at the router boundary; matrix queries covered via the scoped case load).
- [x] Cross-tenant tests (case, matrix rows, agent runs, exhibit list, readiness, drafts, case create) asserting 403/404 across orgs.
- [x] Router-level (TestClient) tests for the core endpoints (`test_arbitration_http_isolation.py`).
- [x] Frontend render/interaction test for `ArbitrationCaseWorkspacePage` (`client/src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx`).

**Acceptance criteria** *(verified by tests)*
- A user from org B cannot read/patch org A's case or matrix rows (test-proven). ✓ (`test_case_get_denies_cross_tenant`, `test_case_patch_denies_cross_tenant`, `test_matrix_rows_deny_cross_tenant`)
- CI runs at least one live-API test hitting `/api/arbitration/...`. ✓ (7 TestClient tests against the real app)

---

### ARB-108 (backlog) — Red-flag review, construction-issue templates, style/duplication lint

**Priority:** P2 · **Size:** S–M · **Guide ref:** §5.2, §16, §17, §18

**Tasks**
- [ ] Implement §5.2 red-flag checks as readiness/agent output (no notice, no CP impact, final-bill waiver, wrong party, claim-outside-clause).
- [ ] Add per-`ArbitrationDisputeType` issue templates (§16).
- [ ] Add global-claim (event→cost link required) and duplicate-head-across-claims lint (§18) in `validator.py`.
- [ ] Strengthen rejoinder new-matter detection beyond keyword match (`validator.py:93-95`).
- [ ] Reconcile notice matrix field names between UI (`requirement`/`risk_note`) and context builder (`contractual_requirement`/`risk`) (`context.py:568-608`, `ArbitrationCaseWorkspacePage.tsx:194-200`).

**Acceptance criteria**
- Duplicated cost head across two claim rows raises a warning.
- Notice fields entered in UI appear in the source ledger snippet.

---

## Suggested sequencing

1. ARB-101 decision gate
2. ARB-102, ARB-107 (P0/P1 foundations)
3. ARB-103, ARB-104, ARB-105
4. ARB-106
5. ARB-108
