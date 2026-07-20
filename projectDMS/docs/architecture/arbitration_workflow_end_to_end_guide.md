# AI-Assisted Arbitration Pleadings — End-to-End Workflow Guide & Audit

Date: 2026-07-11 · Covers: Statement of Claim (SoC), Statement of Defence (SoD), Counterclaim, Rejoinder / Reply to Counterclaim

**Audit basis (honesty statement).** Every claim in this guide is grounded in a direct read
of the cited source files. Functional claims are verified at the level of the project's
deterministic test suites (52 drafting-workflow tests + 7 HTTP tenant-isolation tests +
2 frontend workspace tests, re-run for this audit — see the Verification Appendix), a
frontend typecheck, and code inspection. **No live end-to-end run against real MongoDB /
Qdrant / an LLM key was executed for this audit**; statements about live behavior of those
integrations are marked *partially verified* where they occur.

---

## 1. Architecture at a Glance

The workflow is a **matrix-driven preparation pipeline** in front of a **source-grounded
draft generator**. Its central rule (from the SoC/SoD/Rejoinder preparation guide): *no
pleading is drafted from memory — every material point must trace to a clause, fact, date,
notice, evidence item, impact, calculation, and human approval.*

```
Project records (documents, letters, claims, variations, IPCs, BGs, chronology, clauses)
        │
        ▼  (AI agents + manual entry, per-row human review)
Arbitration CASE workspace ──► 12 matrix collections (document index, chronology, clause,
        │                       issue, claim, defence, counterclaim, rejoinder, quantum,
        │                       notice, jurisdiction, expert alignment)
        ▼  (readiness gate: blockers must clear)
Arbitration DRAFT ──► source ledger (verified rows only) ──► deterministic generator
        │                                                        │
        ▼  validator (citations/amounts/dates/new-matter)        ▼
Versioned draft ──► human approval (locks) ──► DOCX/PDF export / filing bundle (ZIP with
                                               real exhibit files in guide §19 volumes)
```

**Backend** (`backend/rbac_backend/`):

| Component | File | Role |
|---|---|---|
| API router | `routers/arbitration_drafting.py` | All `/api/arbitration/*` endpoints (cases, matrices, agents, drafts, exports) |
| Case workspace service | `services/arbitration_drafting/case_workspace.py` | Cases, matrix CRUD + review workflow, readiness gate, citation audit, filing bundle |
| Drafting service | `services/arbitration_drafting/service.py` | Draft lifecycle: create → generate → version → approve/lock → export |
| Context builder | `services/arbitration_drafting/context.py` | Builds the **source ledger** from matrices + registers (verified-only) |
| Generator | `services/arbitration_drafting/generator.py` | Deterministic source-grounded section writer (`arbitration_pleadings.v2`) |
| Validator | `services/arbitration_drafting/validator.py` | Blocks uncited sources, unsupported amounts/dates, rejoinder new matter; duplication & global-claim lint |
| Agents | `services/arbitration_drafting/agents/` | Deterministic (default) + LLM mode (`ARBITRATION_AGENT_MODE=llm`) matrix agents |
| Exporter | `services/arbitration_drafting/exporter.py` | DOCX (python-docx w/ minimal fallback) and PDF (reportlab) |
| Models | `models/arbitration_drafting.py` | Pydantic models, enums, status vocabularies |
| Migrations | `migrations/v20260705_0001..0003` | Production indexes for all arbitration collections |

**Frontend** (`client/src/`):

| Page | Route(s) | Role |
|---|---|---|
| `pages/ArbitrationCaseWorkspacePage.tsx` | `/arbitration/cases[/new/:caseId/matrices/readiness/filing-bundle]` | Case dashboard, matrix editors, agent runners, review actions, readiness panel, filing bundle |
| `pages/ArbitrationDraftingPage.tsx` | `/arbitration/{drafts,claim,defence,rejoinder,counterclaim}[/:draftId]` | Draft register, per-type creation forms, draft viewer, generation, import, export |
| `services/arbitration-cases-api.ts`, `services/arbitration-drafting-api.ts` | — | Typed API clients |

**MongoDB collections** (Mongo is the source of truth; Qdrant/FalkorDB are derived):
`arbitration_cases`, `arbitration_drafts`, `arbitration_draft_versions`,
`arbitration_selected_references`, `arbitration_claim_heads`,
`arbitration_paragraph_responses`, `arbitration_generation_runs`,
`arbitration_agent_runs`, `arbitration_readiness_checks`, `arbitration_bundle_exports`,
plus 12 matrix collections mapped in `services/arbitration_drafting/matrix_registry.py`.

---

## 2. Permissions (RBAC)

Every route enforces a `dms.arbitration.*` permission through `PolicyService` plus a
**tenant-scoped case lookup** (cross-tenant case ids return 404 without leaking existence;
`routers/arbitration_drafting.py` `_load_case_and_authorize` passes
`build_scope_query(current_user)` into `case_workspace.get_case`). Proven end-to-end by
`tests/test_arbitration_http_isolation.py` (7 tests: scoped lists, 404 cross-tenant
reads/writes, 403 cross-tenant drafts, 403 foreign-org case create, superadmin retained).

| Permission | Gates |
|---|---|
| `dms.arbitration.view` | Read cases, matrices, drafts, versions, readiness, audits |
| `dms.arbitration.create` | Create cases and drafts |
| `dms.arbitration.edit` | Update cases/drafts, matrix CRUD, imports, prepare-from-case, assign/comment reviews |
| `dms.arbitration.generate` | Run agents, generate drafts, regenerate sections |
| `dms.arbitration.approve` | Approve/reject matrix rows, approve readiness, approve/return drafts |
| `dms.arbitration.export` | DOCX/PDF export, filing bundle, queued exports |
| `dms.arbitration.audit` | Draft audit-event trail |
| `dms.arbitration.admin` | Delete drafts |

---

## 3. End-to-End Workflow

### Stage 0 — Prerequisites (outside this module)

Project records must already exist in the DMS: uploaded documents/letters (OCR'd, with
`filepath_local`/`filepath_s3`), contract clauses (`contract_clauses`/`document_vectors`),
registers (`claims`, `variations`, `ipc_bills`, `bank_guarantees`), and optionally verified
chronology events from the Chronology Builder (`matter_chronology_events`). The pleading
workflow **selects and links** these; it does not ingest files itself.

### Stage 1 — Create the Case Workspace

*Page:* `/arbitration/cases/new`. *Endpoint:* `POST /api/arbitration/cases`.

**Inputs:** project (org auto-derived), title, case reference, party perspective
(claimant/respondent/both/neutral), tribunal details, institutional rules, seat, venue,
language, governing law, arbitration clause text, case summary.
**Output:** case in status `matrix_preparation`.
**Guardrail:** creating a case against a foreign organization is rejected 403 by policy.

### Stage 2 — Populate the Matrices (agents + manual)

*Page:* `/arbitration/cases/:caseId/matrices` (12 tabs). Rows can be added manually
(per-matrix form) or generated by agents (dashboard "Run/Queue" buttons; queued runs go
through the background-job service with persisted `arbitration_agent_runs`).

**Agent catalog** (`agents/deterministic.py`; dispatch in `agents/__init__.py`):

| Agent | Output | Grounding behavior |
|---|---|---|
| `orchestrator` | Runs the standard sequence: document-indexing → chronology-adapter → clause-interpretation → jurisdiction → claim-identification → quantum → delay-expert → notice-compliance → issue-framing | Each step idempotent (unique keys per row) |
| `document-indexing` | Document-index rows with exhibit ids (`C-1…`, prefix from party perspective or option) | Metadata copied from the source document; risk flags for missing date/letter-no/file link |
| `chronology-builder-adapter` | Chronology-matrix rows | **Verified chronology events only** (unverified only in review mode) |
| `clause-interpretation` | Clause-matrix rows | Clause excerpts copied from `contract_clauses`/`document_vectors`, never invented |
| `jurisdiction` | Jurisdiction-matrix rows: clause-scope check, per-claim **limitation computation** (base date + period → `within_limitation`/`at_risk`/`time_barred`), pre-arbitration step checklist | Dates from claim records or run options; missing dates → `needs_review`, never guessed |
| `claim-identification` | Claim-matrix rows from the claims register | Amounts/currency/evidence ids copied from the register |
| `quantum` | Quantum annexures per claims/variations/IPCs incl. cost-head + delay-event linkage; **computed interest annexures** (`interest_rate` × period, simple interest); `Q-CLAIM-SUMMARY` rollup (principal/interest/total) | *No amount without a traceable register source*; rollup recomputed in place on re-run |
| `delay-expert` | Expert-alignment rows: delay rows flag `concurrency_not_addressed` for EOT claims; quantum rows compare pleaded vs annexure amounts (`calculation_match`, contradictions) | Contradictions become agent warnings + context warnings |
| `notice-compliance` | Notice-compliance rows from notice-like document-index rows | Compliance status starts `needs_review` |
| `issue-framing` | Issue-matrix rows, one per claim, with §16 dispute-category templates (eot_delay/prolongation/variation/payment/ld) | Evidence/clause links copied from the claim row |
| `review-consistency` | §5.2 red-flag review: annotates claim rows with `red_flags` (no notice, no cost records, no critical-path impact, no written instruction, final-bill waiver, out-of-scope, no contemporaneous chronology) | Guidance only — never changes approval statuses |
| `defence-analysis`, `counterclaim-setoff`, `rejoinder-reply` | Deterministic mode: review-only stubs (defence-analysis has a **real LLM-mode implementation**) | LLM defence rows enforce *no blanket denial without a reason* |

**LLM mode** (`options.agent_mode: "llm"` per run, or `ARBITRATION_AGENT_MODE=llm`;
model via `ARBITRATION_AGENT_MODEL`, default `gpt-4o-mini`): `agents/llm.py` runs
LLM-driven clause interpretation, claim identification, issue framing, defence analysis,
and document understanding with three hard guardrails enforced *in code*:
prompts contain **only scoped source rows keyed by id**; parsed rows citing unknown ids
are **rejected**; LLM rows are **always persisted `needs_review`** (`auto_approve` is
ignored). Amounts, clause excerpts, and document metadata are copied from source records,
never taken from model output. Falls back to deterministic with an explicit warning when
no API key is configured. *(LLM-path behavior verified with mocked model output; not
exercised against a live model in this audit.)*

### Stage 3 — Human Review of Matrix Rows

*Endpoint:* `POST /cases/{id}/{matrix}/{row}/review` with actions assign / comment /
request_changes / approve / reject. Multi-role approval: each matrix has default required
reviewer roles (e.g. claim-matrix → legal + quantum; jurisdiction → legal); a row is only
"ready" when **every required role has approved**. Assignments sync to the task board
(`TaskSyncService`); every action lands in the row's `approval_log`. Approve/reject
require `dms.arbitration.approve`. Pending/rejected reviews are a readiness blocker.

### Stage 4 — Readiness Gate

*Page:* `/arbitration/cases/:caseId/readiness`. *Endpoints:* `GET .../readiness`,
`POST .../approve-readiness`.

`_compute_readiness_checks` (`case_workspace.py`) evaluates, per pleading type:

| Check | Blocks when |
|---|---|
| `document_index_verified` | No approved exhibit-backed document with a source link |
| `clause_support` | No approved clause row |
| `issue_framing` | No approved issue rows |
| `matrix_human_review` | Any row pending/rejected/partially approved |
| `claim/defence/counterclaim/rejoinder_matrix` | The pleading-specific matrix has no approved rows |
| `quantum_support` | A claim pleads an amount/days without an approved quantum annexure |
| `notice_compliance_risk` | Missing/late/non-compliant notices (legal review) |
| `limitation_analysis` | **Time-barred → hard block**; missing/unresolved → legal review |
| `pre_arbitration_compliance` | Required step incomplete (premature) → hard block; unrecorded → legal review |
| `arbitration_clause_scope` | Any claim marked out-of-scope → hard block |
| `expert_alignment` | Delay/quantum claims without an approved aligned expert record (concurrency addressed / calculation match) |
| `rejoinder_new_matter` | Rejoinder row introduces new matter without a tribunal-permission flag |

`approve-readiness` refuses (409) while blockers exist and otherwise moves the case to
`ready_for_drafting`. **The same gate re-runs on every draft generation and approval**
for drafts linked to a case (`assert_case_ready_for_draft`).

### Stage 5 — Create the Draft

Two paths:

1. **From the case workspace** (recommended): create draft → `POST
   /drafts/{id}/prepare-from-case` copies approved document-index rows and clause rows in
   as selected references, claim-matrix rows as claim heads, and inherits
   tribunal/clause/governing-law from the case.
2. **Standalone** (`/arbitration/claim|defence|rejoinder|counterclaim` forms): case
   details, dispute type, manual facts, arbitration clause, relief, amount/interest, one
   optional manual evidence note. ⚠️ Standalone drafts have **no case → no readiness
   gate** (see Gaps).

### Stage 6 — Source Ledger Construction (retrieval & linking)

On every generate/refresh, `ArbitrationContextBuilder.build` assembles the ledger from:
draft-selected references → case matrices (document index, chronology w/ event hydration
from `matter_chronology_events`, clause, issue, claim/defence/counterclaim/rejoinder,
quantum, notice, **jurisdiction**, **expert alignment**) → project registers (claims,
variations, IPCs, bank guarantees) → contract search (Qdrant-backed clause retrieval) →
verified evidence-graph links.

Rules enforced in code:
- **Verified/approved rows only** by default; AI-suggested or needs-review sources appear
  only in explicit review mode and inject a warning.
- Every row gets a stable `source_key` (S1…), a **`source_hash`**, a `permitted_uses`
  tag (fact/clause/chronology/notice/quantum/expert/annexure/background), quality flags
  (missing citation/snippet/exhibit/file-link, user-supplied, unverified-AI), and an
  evidence-strength grade.
- Expert-consistency warnings fire when a pleaded amount mismatches the expert-verified
  amount or a delay claim's concurrency is unaddressed.
- `missing_evidence` is computed (claim heads without support, unknown source ids,
  exhibitless documents) and travels with the version.

### Stage 7 — Generation

*Endpoints:* `POST /drafts/{id}/generate`, `POST /drafts/{id}/sections/{key}/regenerate`,
`POST /drafts/{id}/paragraph-responses/import-soc|import-defence` (paragraph-wise import,
numbering preserved).

The generator (`generator.py`, deterministic `arbitration_pleadings.v2`) writes
type-specific sections:

- **SoC / Counterclaim:** caption, **index** (pleading + exhibit document index),
  introduction, parties, jurisdiction, factual background, claim-wise legal claims,
  quantum, **interest** (wired to `interest_rate` + computed interest annexures),
  **costs**, relief, **verification / statement of truth**, annexures.
- **SoD:** overview, **preliminary objections rendered from jurisdiction-matrix rows**,
  paragraph-by-paragraph response (imported SoC paragraphs; denials without evidence are
  forced to `[Evidence required]`), respondent facts, legal defences (defence matrix),
  quantum challenge, **reply to interest and costs**, counterclaim, relief, annexures.
- **Rejoinder:** scope, response to preliminary objections, paragraph-wise replies
  (imported SoD), clarified facts, reply to legal defences (rejoinder matrix, new-matter
  rows visibly flagged), quantum reply, reply to interest/costs, reply to counterclaim,
  reaffirmed relief, annexures.

Every material line carries a `[S#: citation]` source label or an explicit
`[Evidence required]` marker. Each run is recorded in `arbitration_generation_runs` with a
**stable input hash** — identical inputs reuse the latest version instead of re-generating.

### Stage 8 — Validation (anti-hallucination gate)

`validator.py` produces warnings + **approval blockers** stored on the version:

- cites a source key **not in the ledger** → blocker;
- monetary amount or date **not present in the selected evidence/draft inputs** → blocker;
- rejoinder that reads like a new claim (expanded phrase set) or a rejoinder-matrix row
  flagged `new_matter` without tribunal permission → blocker;
- SoD/Rejoinder without imported paragraphs, denials without support → warnings;
- §18 lint: duplicate claim heads, duplicate cost heads, global-claim risk (amount with no
  event-to-cost links) → warnings.
`validation_status` = blocked / needs_review / passed on every version.

### Stage 9 — Review, Editing, Versioning, Approval

- **Versions:** every generation and every manual save (`POST /drafts/{id}/versions`,
  full markdown) creates an immutable numbered version carrying its own source ledger,
  missing-evidence list, warnings, and validation report. `GET /versions[/n]` reads back.
- **Approval:** `POST /drafts/{id}/approve` (approve permission) **refuses while
  approval blockers exist or the case readiness gate is blocked**, then locks the draft
  (`is_locked`; all mutating endpoints 409 afterwards). `return-for-revision` unlocks
  with a reason. All lifecycle actions emit audit events (`GET /drafts/{id}/audit`) and
  sync to the task board.
- **UI:** the draft page shows the rendered markdown, per-section regeneration, the
  source ledger with quality flags, missing-evidence alerts, and a "Legal Safety" panel
  listing blockers (red) and warnings (amber) — plus (since 2026-07-11) Approve /
  Return-for-Revision buttons with blocker-aware error surfacing, a Version History
  card with per-version viewing, an Edit mode that saves manual versions, and an
  Evidence Search card that links/removes draft references.

### Stage 10 — Export, Bundles, Downloads

| Artifact | Where | Contents |
|---|---|---|
| Pleading DOCX/PDF | Draft page buttons → `GET /drafts/{id}/export/docx|pdf` | Latest version rendered (headings preserved); marks draft `exported` |
| Filing bundle ZIP | Case workspace filing-bundle tab → `GET /cases/{id}/filing-bundle/zip` (or queued `POST .../exports` with polling + download; 12 MB inline cap on queued path) | Guide §19 volumes: `volume-1-pleadings/` (draft markdown), volumes 2–8 with **real exhibit binaries** (resolved `filepath_local` → S3, placed by document type), `volume-6/quantum-annexures.json`, `volume-7/expert-alignment.json`, `exhibits/exhibit-files.json` mapping (with per-exhibit errors), `manifest.json`, matrices JSON, readiness, citation audit, summary markdown |
| Bundle DOCX/PDF | Same tab | Bundle summary document |
| Exhibit list / citation audit | `GET /cases/{id}/exhibit-list`, `/citation-audit` | Exhibit registry; audit of every draft citation |

**Citation audit** (blocking issues fail `ok`): draft cites an exhibit absent from the
registry; cites a source key absent from the ledger; document rows without source link or
exhibit id; **exhibit files that cannot be resolved to bytes**; **pin-cites**
(`C-12, p.3 ¶4`) whose page is outside the exhibit's recorded pages.

---

## 4. Pleading-Specific Flows

| Step | SoC | SoD | Counterclaim | Rejoinder |
|---|---|---|---|---|
| Primary matrix | claim-matrix | defence-matrix (+ para-wise SoC import) | counterclaim-matrix | rejoinder-matrix (+ para-wise SoD import) |
| Import | — | **Import SoC text** → numbered paragraph responses (default `require_proof`) | — | **Import SoD text** → numbered replies |
| Special gates | quantum annexure per amount; limitation; expert alignment | denials need evidence or `[Evidence required]` | same as SoC (jurisdiction/limitation/notice per counterclaim fields) | **new-matter double gate** (readiness + validator); cannot become a second SoC |
| Generator sections | 14 incl. index/interest/costs/verification | 11 incl. matrix-driven preliminary objections + interest/costs reply | SoC set titled "Counterclaim" | 12 incl. reply-to-counterclaim |

---

## 5. Traceability Chain (why outputs can't silently hallucinate)

1. Agents copy identity/amount/date fields **from source records**, never from analysis.
2. LLM agent output citing unknown source ids is dropped; LLM rows always `needs_review`.
3. Only human-approved rows enter the source ledger (verified-only default).
4. The generator can only cite ledger `source_key`s; unsupported facts render as
   `[Evidence required]`.
5. The validator blocks approval on unknown citations, unsupported amounts/dates,
   and rejoinder new matter.
6. Draft approval additionally re-checks the case readiness gate.
7. The citation audit cross-checks every exhibit citation, pin-cite page, and exhibit
   file against the registry before filing.
8. Every step is audited (`audit_events`, `arbitration_agent_runs`,
   `arbitration_generation_runs`, per-row `approval_log`) with prompt/model versions.

---

## 6. Gaps, Defects & Recommendations

**P1 — functional gaps visible to end users** — ***all four resolved 2026-07-11***

1. ~~Draft approval/return has no UI.~~ **Resolved:** the draft page now has an
   **Approve** button (locks the draft; 409 blockers from the readiness/validator gates
   are surfaced in the error toast) and a **Return for Revision** button with a reason
   prompt when locked; a "Locked (approved)" badge disables generate/import/regenerate/edit.
2. ~~No version history UI.~~ **Resolved:** a **Version History** card lists every
   version with validation status and timestamp; clicking a version views it
   ("Viewing vN" badge, "Back to Latest").
3. ~~No in-UI draft editing.~~ **Resolved:** an **Edit** mode opens the displayed
   markdown in an editor and "Save as New Version" posts a manual version
   (`POST /drafts/{id}/versions`), which re-runs the validator.
4. ~~Evidence search endpoint unused by UI.~~ **Resolved:** an **Evidence Search** card
   queries `POST /drafts/{id}/evidence/search` and links results to the draft via the new
   `POST /drafts/{id}/references` endpoint; selected references are listed with per-row
   removal (`DELETE /drafts/{id}/references/{ref_id}`). Both endpoints require edit
   permission and reject mutations on locked drafts (409).

**P2 — grounding / process risks**

5. ~~Standalone drafts bypass the readiness gate.~~ **Resolved 2026-07-11:** the
   validator now emits a standing "not linked to an arbitration case — readiness gate
   not applied" warning on every ungated version (legal review required, fail-visible);
   the draft page shows a prominent amber **Ungated draft** banner linking to the case
   workspaces; and the creation forms carry a **"Case workspace (recommended)"**
   selector (filtered to the chosen project) so the gated path is the default
   affordance. Standalone drafts remain possible but are never silent.
6. ~~Deterministic generator = template + citations, not prose.~~ **Resolved 2026-07-15:**
   an `LLMDraftGenerator` (`llm_generator.py`) runs behind the *same* ledger/validator
   contract — the deterministic generator supplies the grounded section skeleton and the
   LLM only rewrites each section into prose, with in-code reconciliation rejecting any
   rewrite that invents/drops a citation token or drops an `[Evidence required]` marker,
   and the unchanged validator as the backstop. Selected via `draft_mode` on generate
   (`ARBITRATION_DRAFT_MODE` env fallback); deterministic fallback with a context warning
   when no client is configured. UI: a **Source-grounded / AI prose** selector on the
   draft page.
7. ~~Rejoinder agent review-only in deterministic mode.~~ **Resolved 2026-07-15:** the LLM
   agent's `rejoinder-reply` handler generates rejoinder-matrix rows from imported SoD
   paragraphs (grounded per-paragraph, no blanket denials), flagging `new_matter` +
   `tribunal_permission_required` so the readiness gate and validator block un-permissioned
   new matter. Runnable from the case dashboard's Rejoinder agent button. (Counterclaim
   generation from a respondent claim register remains manual — narrower, less clearly
   sourced — and is left as-is.)
8. ~~Registers ingest without per-row user selection.~~ **Resolved 2026-07-11:** drafts
   now carry `include_register_sources` (creation-form toggle; wholesale opt-out with a
   context warning) and `excluded_register_ids` (per-row **Exclude** buttons on
   register-origin ledger rows in the Evidence Status panel, via `PATCH /drafts/{id}`);
   exclusions are surfaced as context warnings and an "N register source(s) excluded"
   note.
9. ~~Interest/limitation parameters not exposed in the UI.~~ **Resolved 2026-07-11:**
   the case dashboard's Agent Runs card gained an **Agent options** panel
   (deterministic/LLM mode, interest rate % p.a., interest period days, limitation
   period years, cause-of-action date) applied to every run/queue call; empty fields are
   omitted from the payload.
10. **Amount/date validator is text-match based** — a supported amount reformatted
    (e.g. `1,000,000` vs `10,00,000`) can false-positive as unsupported; conversely
    amounts inside long snippets pass. Acceptable fail-closed bias, but worth normalizing.

**P3 — polish** — ***all resolved 2026-07-15***

11. ~~Exhibit prefix taxonomy free-text.~~ **Resolved:** the document-index exhibit-prefix
    field is now a guided selector (`C / R / J / CE / RE / QE / DE`).
12. ~~Queued bundle exports cap without UI steering.~~ **Resolved:** the filing-bundle
    Export panel now labels the 12 MB queued-export cap and steers large-exhibit cases to
    the streaming Direct-download ZIP.
13. ~~DOCX/PDF render headings/paragraphs only.~~ **Resolved:** the exporter parses
    markdown tables into real DOCX/PDF tables and inserts a generated Contents list of the
    section headings (minimal-docx fallback degrades tables to tab-joined rows).
14. ~~Pleading timetable / amendment-rule records unmodeled.~~ **Resolved:** the
    jurisdiction agent seeds `pleading_timetable` rows (per stage, with overdue/scheduled
    status) and an `amendment_rule` row; readiness flags overdue, unfiled pleading stages
    for legal review.

---

## 7. Verification Appendix

| Check | Result |
|---|---|
| `tests/test_arbitration_drafting.py` (52 tests: generator sections, validator blockers, context ledger, agents incl. LLM-mode mocks, jurisdiction/limitation, expert alignment, quantum interest/rollup, red flags, review workflow, filing bundle w/ real exhibit bytes, pin-cite audit) | ✅ re-run for this audit |
| `tests/test_arbitration_http_isolation.py` (7 tests, real app + real policy/scope services) | ✅ re-run for this audit |
| `ArbitrationCaseWorkspacePage.test.tsx` (2 vitest render/interaction tests) | ✅ passing (this session) |
| Frontend typecheck (`tsc --noEmit`) | ✅ clean (this session) |
| Live E2E with real MongoDB/Qdrant/S3/LLM | ❌ **not executed** — S3 exhibit download, Qdrant contract search, and live-LLM agent runs are code-inspected + unit-mocked only |

Change history for this workflow: `docs/architecture/arbitration_guide_compliance_tickets.md`
(ARB-101…ARB-108, all closed) against
`docs/architecture/soc_sod_rejoinder_agent_workflow_implementation_plan.md`.
