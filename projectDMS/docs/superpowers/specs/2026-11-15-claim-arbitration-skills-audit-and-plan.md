# Claim → Arbitration: audit and ten-move plan, with skills doing the heavy lifting

Date: 2026-11-15 · Revised: 2026-11-15 after owner decisions D-01 to D-04 and corrections A to C (see Part D) · Status: plan only, nothing implemented · Scope: `projectDMS/backend/rbac_backend`, `projectDMS/client`

**The one-line finding.** The product has a governed skill engine that only letters use, a claims register that is a flat form, three EOT systems that do not know about each other, and an arbitration module whose prompts are hard-coded strings. The four skill packs supplied with this request (`indian-infra-claims`, `indian-infra-eot-claims`, `indian-infra-arbitration-pleadings`, `indian-infra-arbitration-hearing`) describe exactly the method the claim and arbitration screens are missing. Two of them are already named in the code as *known but not installed*. The plan installs them and rebuilds the claim-to-hearing journey around them.

How this was checked: code read of the claims, key-date, hindrance, skills and arbitration modules; the SPA run locally (Vite) with the API mocked in the browser, the same way the repo's own mocked Playwright suites work. No backend was running, so screenshots prove layout and interaction design, not server behaviour.

---

## Part A — Audit

### A1. What exists today

| Area | State | Evidence |
|---|---|---|
| Skill engine | Versioned packs, manifests, org/project bindings, publish gated on an eval report, deterministic chain routing, strict LLM call with deterministic fallback and `degraded` marking, input-hash step caching | `services/skills/{registry,executor,router,compiler,loader}.py`, `models/skill.py` |
| Skills installed | 3: letter-analysis, contract-letters, variations | `services/skills/system_skills.py:44-118` |
| Skills consumed by | Letter Bench only | `services/letter_drafting/bench.py:74` is the single importer |
| Claims skill, EOT skill | Declared `KNOWN_UNINSTALLED_SKILLS`; shown in the Letter Bench chain as "Not installed" | `system_skills.py:38-41` |
| Claims | CRUD register, free status changes, separate approval record, free-text AI assessment, ZIP evidence bundle | `models/claim.py`, `routers/claims.py`, `services/claim_assessment_service.py` |
| EOT | Three unlinked systems: `claim.eot_days_*`, hindrance register (`claimed_days`, `assessed_days`, `critical_path_impact`), key-date `EOTSubmission`/`EOTDetermination` | `models/claim.py`, `models/evidence_registers.py:197-290`, `models/key_date.py:325-493` |
| Arbitration | Case workspace, 12 matrices, 4 pleading types, LangGraph workflow with 8 human gates and 8 validators, DOCX/PDF/ZIP filing bundle | `services/arbitration_drafting/*`, `langgraph_engine.py:302-408` |
| Arbitration prompts | Hard-coded Python strings, OpenAI only (`gpt-4o-mini` agents, `gpt-4o` drafting); no use of the skill engine | `agents/llm.py:70-125`, `llm_generator.py:131-175` |
| Hearing phase | Nothing. The case lifecycle ends at `filed` | keyword search across services and pages |

### A2. Gaps against the skills' own mandatory workflows

**Claims skill — "Preparing a claim" (5 steps)**

| Skill step | Today |
|---|---|
| 1. Event + exact sub-clause | Clauses are free strings; assessment citations are never written back to the claim |
| 2. Notice compliance (awareness date, contractual window) | No awareness date. Window is a fixed per-type default (`sla_service.py:31-39`), not read from the contract. It is only computed while `notice_date` is empty, so a notice that *was* served late is never flagged |
| 3. Evidence chronology | None per claim. Linked documents have roles but no sequence |
| 4. Separate time and cost | One amount and one EOT-days pair per claim; no heads, no concurrency |
| 5. Structured submission | Absent. The "evidence bundle" is a ZIP of raw records |

**Claims skill — "Analysing / determining"**: no per-head result (Full / Partial / None / Insufficient), no request-for-information loop, no strategy note. The AI assessment returns one free-text answer and ignores dates, amounts, EOT days, linked letters and hindrances (`claim_assessment_service.py:60`).

**EOT skill — 6-step sequence**: baseline, event catalogue (Employer / Contractor risk), critical path, concurrency matrix, net EOT table, cost consequences. The hindrance register holds most of the raw inputs, but nothing links a hindrance to a claim (`HindranceDetailPage.tsx:359` says so explicitly), and there is no concurrency matrix or net-EOT table anywhere. There is no CPM network in the product, so true Time Impact Analysis is not possible today; As-Planned vs As-Built and Windows on programme milestones are.

**Pleadings skill**: every claim must identify provision, event, causal link and quantified relief. The validators check citations, structure, duplication and new matter, but not this four-element test per head. Counterclaim reuses the SoC section set and its agent is review-only. Opponent pleadings are pasted text.

**Hearing skill**: all eight deliverables (strategy note, opening, visual timeline, hearing matrix, slide deck, examination plans, closing, aide-mémoire) are absent.

**Claim ↔ arbitration**: no foreign key either way. A case scoops up "the latest 50 claims in the project" (`agents/deterministic.py:305`) and maps them heuristically into matrix rows. Arbitration has a richer `ArbitrationClaimHead` than the Claim model, and the two never sync.

### A3. Frontend audit (screenshots in `2026-11-15-claim-arbitration-shots/`)

**Claim Register** — `01-claims-register.png`

- The create/edit dialog cannot capture event date, notice date, EOT days, clauses, currency or responsible party (`ClaimsRegisterPage.tsx:434-517`). The time-bar tracker therefore never fires for a claim created in the UI.
- Seven icon-only actions per row, no labels. Status is changed by an inline dropdown with no transition rules.
- No totals, no ageing, no time-bar column, no "what needs me today". Amounts show `184,500,000` rather than Indian grouping (`18,45,00,000` / ₹18.45 Cr); dates show `11/20/2026` (US order) in an Indian product.
- Badge contrast: "Disputed" is dark red text on red, "Draft" grey on grey — both hard to read.

**Claim Details** — `02-claim-detail.png`

- The fixture is a claim with event on 2 March and notice on 11 April: 40 days against a 28-day window. The page shows both dates and says nothing. This is the single most valuable fact on the screen and it is invisible.
- Linked letters render as `Letter l1`, `Letter l2` (raw ids). Responsible party is a raw id. Clauses are a grey footnote.
- No edit on the page. No chronology, heads, quantum, delay, determination or history. The link-a-document search form takes more space than the claim itself.
- Approval dialog asks the user to type a reviewer's user id (`ClaimApprovalDialog.tsx:120-125`).

**Arbitration case workspace** — `04-arb-case-overview.png`, `05-arb-case-matrices.png`

- The page exposes engine vocabulary to lawyers: "Durable Pleading Workflow", "Start governed workflow", "Queue Orchestrator Job", "Deterministic" mode.
- Users must type identifiers: "Comma-separated scoped document IDs", opponent version ids, "Source id".
- Twelve agent buttons plus an orchestrator, with no indication of order or of what has already run.
- Twelve matrix tabs wrap onto two rows; "Add Row" is a column of blank text inputs (date typed as text); rows render as a generic "Content / Status / Action" table.
- Eight human gates are the heart of the workflow and appear only as a status card and a blocker list. There is no "you are here".
- One 1,731-line component with 29 `useState` hooks holds the register, the new-case form, the dashboard, the matrix editor, readiness and export.

**Pleading drafting** — `08-arb-new-soc.png`, `09-arb-draft.png`

- Starting a Statement of Claim is a blank form with three empty text areas. Case workspace defaults to "None — ungated draft", which bypasses the readiness gate by default.
- The draft is shown as raw Markdown in a monospace block (`# STATEMENT OF CLAIM`). Exhibit references are plain text, not links. No paragraph-level provenance, no diff between versions.
- "Evidence Status: Sources: 0" beside a draft that cites two exhibits.

**Navigation**: eight sidebar entries for one journey (Claims Register, SLA Tracker, Chronology Builder, Arbitration, Case Workspaces, Saved Drafts, four Draft-X links). Sidebar also has a typo: "Regisration".

### A4. Technical debt that the plan must respect

- `SkillExecutor` imports `OUTPUT_SCHEMAS` from `models/letter_bench.py` and `PromptRegistry` from `letter_drafting`; `SkillStep` lives in the letter-bench model. The engine is generic in design and letter-bound in wiring.
- Default model is hard-wired (`DEFAULT_SKILL_MODEL = "gpt-4o"`, `executor.py:35`).
- The LangGraph arbitration engine is deployed but switched off in production (`ARBITRATION_ENGINE_DEFAULT=arbitration_v2`); open blockers B1–B6, chiefly no legally reviewed golden matters. Zero production cases.
- `POST /claims/{id}/status` has no transition guard and no `@handle_exceptions` (`routers/claims.py:194`).
- Publishing a skill does not require a legal signature. `SkillEvalReport.signed_off_by` is optional and `SkillRegistry.publish()` checks only guardrail findings, score and case count (`registry.py:292-318`). The evaluator, the signatory and the publisher are not distinguished.
- No project or organisation field records which party the workspace acts for. The Letter Bench carries `role` per board; claims and arbitration carry none (arbitration has `party_perspective` per case only).
- The supplied skill packs are thin on references (7–20 lines each) and cite authorities by name only (McDermott, Unibros, Batliboi). They are method, not law reports; a lawyer must confirm them before the packs are published.

---

## Part B — The ten moves

Design rule for all ten: **the skill decides the method, the code does the arithmetic, the human makes the call.** Skills never compute dates, days or rupees; they classify, test, explain and draft. Every number shown to a user is produced by deterministic code from recorded inputs. Where the contract or the delay method decides how a number is treated (a notice window, a concurrent period), code computes the facts and a named person records the treatment; nothing is assumed silently.

| # | Move | Skill that carries it | Replaces |
|---|---|---|---|
| 1 | Claim Bench: a claim is a board of heads, each tested on five elements | claims (prepare) | Flat claim detail page |
| 2 | Notice Clock: time-bar radar that opens claims before they exist | claims + letter-analysis + contract-letters | `sla_service` fixed windows |
| 3 | Delay Lab: concurrency matrix and net EOT on a swim-lane timeline | eot-claims | Three unlinked EOT systems |
| 4 | Quantum Builder with proof locks | claims (quantum principles) + eot-claims step 6 | Single `amount_claimed` field |
| 5 | Two Chairs: role-aware evaluation, plus a labelled internal simulation of the other side | claims (determine), role overlay | Free-text "AI assessment" |
| 6 | Submission Pack with a privilege wall | claims (structure) + contract-letters | Raw ZIP evidence bundle |
| 7 | Escalate to Arbitration: one action, explicit lineage | pleadings (integration section) | "Latest 50 claims in the project" |
| 8 | Pleadings on skills: Traverse Board and four-element validator | arbitration-pleadings | Hard-coded prompt strings, raw-id forms |
| 9 | Hearing Room: the eight deliverables as a kit | arbitration-hearing | Nothing (lifecycle ends at `filed`) |
| 10 | Dispute Spine and exposure band | all four, read-only | Eight sidebar entries |

### Move 0 (foundation) — make the skill engine serve more than letters

Not counted in the ten; everything depends on it.

Backend
- Move `OUTPUT_SCHEMAS` to `services/skills/schemas.py` as a registry with `register_schema(key, model)`; `models/letter_bench.py` registers its three. Move `SkillStep` to `models/skill.py` with a `subject_type` (`letter` | `claim` | `claim_head` | `arbitration_case` | `arbitration_draft` | `hearing`) and `subject_id`. New collection `skill_steps` for non-letter subjects; letter steps stay where they are (no data migration).
- `SkillExecutor` takes a prompt provider interface instead of the letter `PromptRegistry`; model name comes from settings (`SKILL_MODEL`, default unchanged) rather than a module constant.
- Install the four packs under `initial_data/skills/<key>/` with manifests in `system_skills.py`. Remove claims and EOT from `KNOWN_UNINSTALLED_SKILLS`. Kinds: claims → `evaluation`, eot → `evaluation`, pleadings → `drafting`, hearing → `drafting`.
- Manifests declare chains: claims → eot (keywords: delay, critical path) → contract-letters; claims → pleadings (`issue_types: ["dispute"]`); pleadings → hearing.
- New output schemas (all Pydantic, all owned by us): `claim_head_test.v1`, `notice_compliance.v1`, `delay_event_classification.v1`, `concurrency_finding.v1`, `quantum_head_review.v1`, `claim_determination.v1`, `submission_section.v1`, `strategy_note.v1`, `pleading_head.v1`, `traverse_response.v1`, `hearing_deliverable.v1`, `examination_plan.v1`.
- New validators in `skill_validators.py`: `four_elements_per_head`, `notice_pleaded_expressly`, `quantum_has_record`, `every_point_has_exhibit`, `no_invented_amounts` (every number in output must exist in inputs).
- Migration `v2026MMDD_0001_seed_claim_arbitration_skills.py` seeds the four as **draft**, not published. Emit documentary notes as `notices=`.
- **Strengthened publication gate (D-03).** New collection `skill_approvals`, one record per review: `skill_key`, `version`, `content_hash`, `eval_suite`, `eval_report_hash` (hash of suite, score, threshold, case count), `kind` (`technical` | `legal`), `reviewer_role`, `reviewer_user_id`, `decided_at`, `decision`, `revoked_at`, `revoked_by`. `SkillManifest` gains `required_approvals` (list of `{kind, reviewer_role}`).
  - `SkillRegistry.publish()` refuses unless every required approval exists, is not revoked, and matches the exact version, content hash and eval-report hash being published. A re-import, a changed pack or a re-run evaluation makes earlier approvals stale by construction.
  - Evaluator (`eval_report.recorded_by`, new, required), each approver and the publisher must be three different users for the legal approval; the publisher may not be the legal signatory. A Super Admin may publish an approved version but holds no implicit approval: approval rights come only from an explicit role assignment.
  - Legal sign-off uses the existing `senior_legal_approver` role (`arbitration_drafting/approval_policy.py:90-98`); it is lifted from the arbitration policy into `core/permissions.py` so skills and arbitration share one definition. New specialist roles for technical review: `chief_contract_expert`, `delay_planning_expert`, `arbitration_case_preparation_expert`. Adding roles is a behaviour change and goes through the single role-normalisation source.
  - Revoking an approval on a published version deprecates it and blocks new steps with that version; existing outputs keep their recorded version and hash.
  - The three already-published letter packs carry the "seed-structural" report with no signatory. They keep working; the gate applies to them at their next version. This is recorded as a notice, not hidden.
  - A published skill never substitutes for case-level approval. Pleadings and hearing material keep every existing gate.

  | Skill pack | Technical reviewer | Legal sign-off |
  |---|---|---|
  | indian-infra-claims | Chief Contract Expert | Senior Legal Approver |
  | indian-infra-eot-claims | Delay/Planning Expert and Chief Contract Expert | Senior Legal Approver |
  | indian-infra-arbitration-pleadings | Contract/Claims Expert | Lead Arbitration Counsel (holding `senior_legal_approver`) |
  | indian-infra-arbitration-hearing | Arbitration Case Preparation Expert | Lead Arbitration Counsel (holding `senior_legal_approver`) |
- **Working perspective (D-01).** `Project.contractual_capacity` (`contractor` | `engineer` | `employer`), with an organisation default; unset means the user is asked once per project, never assumed. Every claim board, skill step and arbitration seed records the perspective it was made under.
- Permissions: reuse `drafting.skill.*` for administration; add `dms.claim.skill.run` and `dms.arbitration.skill.run`.

Frontend
- Lift `SkillRail`, `SkillStepDrawer`, `SkillPalette` from `components/letter-bench/` to `components/skills/`, parameterised by subject. Move the admin page route from `/letters/skills` to `/skills` (redirect the old path).
- Skill admin shows, per version, the approvals required, who gave each, what they were bound to, and why Publish is disabled when one is missing, stale or revoked.

Tests: extend `test_skill_loader_compiler.py` and `test_skill_registry_router_executor.py` for the four packs; one test per schema that the deterministic fallback validates; regenerate the route inventory.

### Move 1 — Claim Bench

A claim stops being a row with an amount and becomes a board. Each **head of claim** is a card with five lights: Event · Notice · Basis · Causation · Quantum. The claims skill's preparation workflow tests each light and proposes a state; the user confirms or overrides, exactly as positions are confirmed on the Letter Bench.

Backend
- `ClaimHead` (embedded list on the claim, `head_id` stable): `kind` (time | cost | time_and_cost), `title`, `event_summary`, `awareness_date`, `clause_refs` (ids into the clause index, not strings), `delay_event_ids`, `amount_claimed`, `eot_days_claimed`, `elements` (five `{state: supported|weak|missing|not_applicable, proposed_state, reason, source_ids, decided_by}`), `classification` (excusable | compensable | contractor_risk | undetermined).
- `ClaimBoard` state on the claim: `board_version` (optimistic concurrency, 409 on stale, same contract as the Letter Bench), `stage` (heads → tested → locked → composed), `chain`.
- Service `services/claims/bench.py`, modelled on `letter_drafting/bench.py`: `open`, `add_head`, `run_head_test` (skill step, schema `claim_head_test.v1`, fallback marks every element `missing` with reason "not assessed" and `degraded=True`), `decide_element`, `lock`.
- Routes under `/api/claims/{id}/bench/*`, all through `PolicyService.authorize`. Fix `POST /claims/{id}/status`: add a transition table and `@handle_exceptions`.
- Migration backfills one head per existing claim from its amount and EOT days; existing fields stay as computed roll-ups so the register and arbitration readers keep working.

Frontend
- `/claims/:id` becomes the Bench. Left: heads as cards with five lights. Centre: the selected head's elements, each with the skill's proposed state, its reason, and the sources it relied on. Right: skill rail showing which skill version ran and whether a step is stale or degraded.
- Create dialog replaced by a three-question start: what happened, when did you become aware, which contract. Everything else is captured on the board.
- Replace the reviewer-id text field with a user picker; letters show number, date and subject.

### Move 2 — Notice Clock

Claims are lost before they are opened. The clock runs from **awareness**, the window comes from **this contract's clause**, and the system proposes the claim.

Backend
- `services/claims/notice_clock.py` (deterministic). The window is a `NoticeRequirement` record per project and claim type: `period_days`, `trigger` (awareness | event | other), `clause_id`, `instrument_id` and revision, `source` and `verification`.
  - `source` is resolved through the contract instruments in priority order — amendments, then special / particular conditions, then general conditions (`models/contract_document.py:81-89`) — using only instruments whose projection is current. A later instrument overrides an earlier one.
  - `verification` is one of `verified` (a person with `dms.contract.clause.read` and claim-manage rights confirmed the extracted period against the quoted clause text), `extracted_unverified`, or `assumed_default`.
  - An `assumed_default` (today's per-type figure, e.g. 28 days) is displayed as "Assumed 28 days — contract provision not found", in a distinct style, and is never written into a notice-compliance finding, a submission, a determination or an arbitration matrix row as a contractual requirement. Those outputs state "notice period not established from the contract" until the requirement is at least extracted.
  - Computes `due`, `days_left`, and — the missing case — `served_late_by` when a notice exists, each carrying the verification state of the window it used.
- Triggers that create a **potential claim** (new status before `draft`): a Letter Bench analysis chain that routes to the claims or EOT skill; a hindrance saved with `critical_path_impact` and employer responsibility; a variation instruction without an agreed rate.
- Skill step `notice_compliance.v1`: given event, awareness evidence, notice letter and clause text, states whether notice was compliant and, if late, what the record shows about prejudice to the Employer. The skill's principle — never rely on time-bar alone — becomes a visible field in both chairs.
- One action "Draft notice" opens a Letter Bench pre-loaded with the head, routed to the contract-letters skill.

Frontend
- Register gains a first column: a day-count pill (green / amber / red / "served 12 days late"). A strip above the table: "3 notices due this week · 1 already late".
- Bench header shows the clock with the clause it was read from, linked to the clause.

### Move 3 — Delay Lab

The claim head owns the EOT **assessment** (D-02). The hindrance register and the key-date submissions and determinations remain the authoritative records of their own facts. The Delay Lab stores links and analysis; it never copies or edits those records, so it cannot become a fourth register.

```
Claim (contract and project scoped)
└─ Claim Head — EOT entitlement        assessment, causation, days claimed, days recommended
     ├─ hindrance events               → hindrance register (authoritative)
     ├─ key-date EOT submissions       → key dates (authoritative)
     ├─ EOT determinations             → key dates (authoritative)
     ├─ notice letters                 → letters / documents
     ├─ baseline and updated programmes→ programme milestones, documents
     └─ supporting evidence            → documents
```

Backend
- New collection `claim_head_links`: `claim_id`, `head_id`, `target_type` (hindrance | eot_submission | eot_determination | key_date | programme_milestone | letter | document), `target_id`, `target_revision` (the revision or content hash linked, immutable), `role`, `created_by`, `created_at`, `superseded_by`. Many-to-many in both directions: one hindrance may serve several heads and milestones; one submission may cover several hindrances and heads. Links are never updated in place; a change writes a new link and supersedes the old, so history is kept.
- When a linked record gains a newer revision, the head shows "linked revision is not the latest" and dependent analysis steps go stale. Nothing re-points automatically.
- `EotAssessment` on the head, versioned: `method` (as_planned_vs_as_built | windows; TIA and collapsed as-built listed as unavailable without a CPM network), `baseline_ref`, `events[]`, `overlaps[]`, `days_claimed`, `days_recommended`, `days_approved` (read from linked determinations, never typed), `previously_granted`, `status`, and the full source list for every figure.
- `services/claims/delay_engine.py` (deterministic, pure functions) computes **facts only**: per-event critical delay as recorded, every overlap between employer-risk and contractor-risk events with its dates and length, previously granted days, and days at LD exposure under each treatment.
- **Overlaps are not deducted automatically (correction A).** Each overlap is a `ConcurrencyFinding` that must be decided: `treatment` (eot_granted_no_cost | eot_granted_with_cost | apportioned | no_eot | not_true_concurrency), `days_to_eot`, `basis` (the contract clause, the method and the reasoning), `decided_by`, `decided_at`. The contract's own concurrency provision, where the clause index has one, is quoted on the finding. Until every overlap on a head is decided, the net-EOT figure is shown as a range (all overlaps counted to the Contractor ↔ none counted) and the head cannot be locked.
- Skill steps: `delay_event_classification.v1` proposes risk owner and whether the record proves criticality, with reasons; `concurrency_finding.v1` proposes a treatment per overlap with its reasoning and the opposing argument. Both are proposals; the delay/planning reviewer decides.
- The net-EOT table (Event | Type | Critical delay | Concurrent? | Treatment | Net EOT) is assembled from decided findings only.

Frontend
- A swim-lane timeline: Employer lane, Contractor lane, Neutral lane, milestones on top. Overlaps are hatched and numbered; each opens its finding. Dates are read from the linked records and cannot be dragged.
- Each overlap card shows the computed facts, the contract provision if one exists, the proposed treatment, and the decision control. Undecided overlaps are counted in the head's blockers.
- Linked records open in their own register; the Lab shows which revision is linked.
- Risk owner is encoded by lane position and label, not colour alone.

### Move 4 — Quantum Builder with proof locks

Backend
- `QuantumLine` per head: `category` (direct | prolongation | disruption | overhead_formula | loss_of_profit | interest), `basis`, `period`, `amount`, `record_ids`, `formula` (none | hudson | emden | eichleay), `lock_state`.
- Locks enforced in code, taken straight from the quantum reference: prolongation only for periods the Delay Lab's decided findings treat as compensable (non-concurrent employer delay, or an overlap decided `eot_granted_with_cost` with separated costs); a formula line stays locked until records of actual loss are linked; loss of profit needs evidence of work declined. A locked line cannot enter a submission or a pleading.
- Arithmetic is code. Skill step `quantum_head_review.v1` only reviews: is each line supported, what record is missing, is it a global claim.
- Roll-up writes `amount_claimed`; arbitration's quantum annexures are generated from these lines in Move 7.

Frontend
- A build-up table with a padlock on each locked line and the exact record that would unlock it. Totals shown three ways: claimed, supported, locked.

### Move 5 — Two Chairs

Three perspectives are supported as equals (D-01):

| Perspective | Work on the board |
|---|---|
| Contractor | Prepare, substantiate, quantify and submit claims |
| Engineer | Evaluate claims, assess entitlement, determine admissible time and cost, prepare recommendations or determinations within delegated authority |
| Employer | Review exposure, the Engineer's recommendations, counterclaims, disputes and settlement options |

The working perspective comes from `Project.contractual_capacity` (Move 0), not from a global assumption. Release 1 leads with Contractor-side preparation; the Engineer and Employer evaluation workflow ships in the same phase and is not a reduced mode.

Backend
- Two separate record types, never one record with a flag:
  - `ClaimDetermination` — an **official** finding. Can be created only when the project's capacity is `engineer` or `employer`, by a user holding determination authority; passes the approval workflow; immutable once approved; a change is a new revision. Feeds a FIDIC 3.7-style letter through the contract-letters skill.
  - `ClaimSimulation` — an **internal analysis** of how another party is likely to respond. Stored in the privileged collection with the strategy note (Move 6), permission `dms.claim.strategy.read`, excluded from every export, from the arbitration source ledger and from matrix seeding. Every API response and screen carries the label "Internal analysis — not a determination".
- Both hold per-head `result` (full | partial | none | insufficient_information), `days`, `amount`, `findings`, `residual_risks`, `information_required[]` with deadline and contractual consequence. Schema `claim_determination.v1`; fallback is `insufficient_information` and degraded.
- **Switching perspective changes the lens, not the record.** Contractual facts (events, dates, notices, clauses, linked records, quantum lines) are perspective-independent and are not written by a perspective switch. A simulation cannot write to any fact, to another party's finding, or to an approved determination; this is enforced in the service and covered by tests that attempt each overwrite.
- A determination received from the real Engineer is recorded as a linked document plus a transcribed `ClaimDetermination` with `origin: received`, distinct from one authored in the workspace.
- Supersedes `claim_assessment_service` for new claims; the old endpoint stays and returns history.

Frontend
- The Bench header names the perspective the project works in. A contractor workspace offers "Simulate the Engineer's response"; the result opens in a visibly different frame with the internal-analysis label on every panel and on any printout.
- An engineer or employer workspace opens on evaluation: heads to determine, information to request, recommendation to issue. "Request information" drafts the letter.

### Move 6 — Submission Pack with a privilege wall

Backend
- `services/claims/composer.py`: composes the skill's six sections (executive summary, contractual basis with quoted wording, chronology and evidence schedule, delay analysis, quantum build-up, relief and reservation) from the **locked** board, one skill step per section (`submission_section.v1`), re-generating only stale sections. Validators: `exact_sub_clause_cited`, `reservation_of_rights`, `no_invented_amounts`.
- Evidence schedule is deterministic: linked documents ordered by date and numbered.
- The strategy note (`strategy_note.v1`: strengths, weaknesses, residual risks) is stored in a separate collection with its own permission `dms.claim.strategy.read`, never included in any export, and excluded from the arbitration source ledger.
- Export DOCX/PDF through the existing exporter; the ZIP bundle remains as the annexure set.

Frontend
- A reading view of the composed submission with section-level "regenerate", paragraph provenance on hover, and a separate, visibly marked "Internal — not for issue" tab for the strategy note.

### Move 7 — Escalate to Arbitration

Backend
- `POST /api/claims/escalate` with `claim_ids[]` creates or extends a case. Case gains `claim_ids`; claim gains `arbitration_case_id` and status `disputed`. Evidence freeze is applied (the field exists).
- Seeding is deterministic and explicit: heads → `claim-matrix` rows; notice results → `notice-compliance`; quantum lines → `quantum-annexures`; delay events and linked letters → `chronology-matrix`; linked documents → `document-index`. Rows carry `origin: {claim_id, head_id}` and start `needs_review`, as the existing rule requires. Simulations, strategy notes and assumed-default notice periods are never seeded; undecided concurrency findings are seeded as open issues, not as figures. `_find_source_rows` prefers `case.claim_ids` and falls back to project scope only for cases with none.
- Dispute ladder on the case (deterministic tracker): determination → DAB / conciliation if the contract requires it → Section 21 notice date → limitation date, replacing the default three-year placeholder in `agents/deterministic.py:34` with dates the user confirms.

Frontend
- "Escalate" on the register (multi-select) and on the Bench. A confirmation sheet shows exactly what will be carried over and what is still weak.

### Move 8 — Pleadings on skills

Backend
- Replace the `*_INSTRUCTION` constants (`agents/llm.py:70-125`) and the counsel prompt (`llm_generator.py:131-175`) with compiled skill blocks from `indian-infra-arbitration-pleadings`, attached through `attach_skill_block`. Keep `strict=True` and the deterministic fallbacks. Bump the prompt-version constants (tests pin them). Record `skill_key`, version and content hash on the workflow state next to the existing snapshot hashes.
- New validator node `four_elements` in the LangGraph fan-out: each pleaded head must carry provision, event, causal link and quantified relief; notice compliance must be pleaded expressly.
- Counterclaim gets its own section set and a real agent (LD, defects, over-payment, indemnity, costs).
- Opponent pleading intake accepts an uploaded document (existing extraction pipeline) and splits it into numbered paragraphs.
- **Evidence and approval controls (correction C).**
  - Every citation in a draft version is classified deterministically as `verified` (resolves to an approved document-index row with a source in the ledger), `unverified` (resolves to a row not yet approved) or `unresolved` (no matching exhibit). Counts are stored on the version. The "Sources: 0" figure beside a draft that cites exhibits is replaced by these three counts from the same computation the validators use.
  - Legal approval, draft approval and filing export are refused while any citation is `unverified` or `unresolved`, or while a mandatory provenance item (arbitration clause source, notice records for each pleaded head, quantum support for each amount) is missing. The refusal lists each item.
  - A draft with no case workspace is a **scratch draft**: creation requires choosing it explicitly (the default becomes "select a case"), it is watermarked, and it can be neither approved nor exported for filing. Attaching it to a case subjects it to every gate from that point. Existing ungated drafts are reported by the migration as notices and become scratch drafts; none is deleted.
- The engine stays behind its existing rollout flags. This move does not change which engine production runs, and no step in this plan bypasses the arbitration approval gates, the acceptance receipt chain or the production rollout controls.

Frontend
- **Traverse Board** for defence and rejoinder: opponent paragraph on the left, our response on the right (admit / deny / put to proof), denial reason mandatory, positive case and exhibit attached. This uses the existing paragraph-response model.
- The eight gates become a horizontal stepper with the current gate, who must act, and what blocks it.
- Every raw-id input becomes a picker. Engine vocabulary is renamed: "Prepare the case file", "Review the matrices", "Approve for drafting".
- The draft renders as a formatted pleading with numbered paragraphs and clickable exhibit chips; version diff. Exhibit chips show their state (verified / unverified / unresolved); Approve and Export are disabled with the blocking list shown.
- Split the two monolith pages into route-level components (`CaseRegister`, `CaseDashboard`, `MatrixEditor`, `Readiness`, `FilingBundle`, `PleadingEditor`, `TraverseBoard`).

### Move 9 — Hearing Room

Backend
- Case status gains `hearing_preparation`, `hearing`, `award` after `filed`.
- `services/arbitration_hearing/`: one generator per deliverable, each a skill step (`hearing_deliverable.v1`, `examination_plan.v1`) over approved matrices and the filed pleadings only. Validator `every_point_has_exhibit`. Internal documents (strategy note, examination plans, aide-mémoire) are stored apart from tribunal-facing ones (opening outline, timeline, matrix, deck, closing skeleton), with separate permissions.
- Witness register (`name`, `role`, `statement_document_id`, `topics`). Examination plans are per witness.
- **Deck (D-04): PDF only in Release 1.** The deck is a structured, editable object in ContraClaim (`HearingDeck` → ordered `slides[]`, each with a layout type, title, body blocks, exhibit references and source step), following the skill's eleven-section order, one idea per slide. Users edit slides before export; an edited slide is marked so regeneration does not overwrite it silently.
  - Export is a presentation-style PDF through the existing reportlab exporter with a new landscape slide template: one slide per page, large type, rendered timeline pages from the chronology matrix, one or two claim-summary slides per head, exhibit references on every evidential point, page numbers, and a footer with case reference, deck version and content hash.
  - Tribunal-facing and internal material are separate documents with separate exports; internal pages carry an "Internal — not for the tribunal" band and cannot be included in a tribunal-facing export.
  - No new dependency. PPTX export is deferred to a later release, after demand, layout fidelity, dependency security and export regression are checked. Existing DOCX/PDF pleading exports are untouched; their tests are the regression guard.
- "Tribunal questions": a fixed list from the advocacy reference (why was notice late, where is the critical-path proof) plus skill-proposed ones, each answered with exhibits or flagged as a gap.

Frontend
- `/arbitration/cases/:id/hearing`: a kit of eight cards with readiness per card, a timeline view colour-and-lane coded by risk owner, a slide editor with live preview, a rehearsal view for the opening with timing marks.

### Move 10 — Dispute Spine and exposure band

Backend
- `GET /api/disputes/spine?claim_id|case_id` assembles one read model: event → notice → claim → determination → dispute ladder → pleadings → hearing → award, with the date, state and owner of each stage.
- Exposure band (deterministic): per head, claimed vs supported vs the opposing chair's likely result gives best / likely / worst totals in days and rupees. No probabilities are invented; "likely" is the confirmed result of an official determination where one exists, otherwise of an internal simulation, and is labelled with which. The band is internal analysis and is not exported.

Frontend
- A single "Disputes" entry replaces eight sidebar links. The spine is a persistent header on every claim and case screen. Portfolio view: all claims by stage, with exposure bands and the Notice Clock strip.
- House-keeping in the same pass: Indian number grouping with ₹ lakh/crore, `DD-MM-YYYY` dates, accessible badge contrast, labelled row actions, the "Regisration" typo.

---

## Part C — Delivery

### Sequence

| Phase | Contents | Ships to users as |
|---|---|---|
| 0 | Move 0 including the strengthened publication gate and project perspective; claim model fields; status transition guard; register and detail quick fixes (dates, grouping, contrast, pickers, late-notice flag computed deterministically) | Visible fixes, no AI change |
| 1 | Moves 1, 2 | Claim Bench behind `CLAIM_BENCH_ROLLOUT_MODE` (off / shadow / primary, same pattern as the Letter Bench) |
| 2 | Moves 3, 4 | Delay Lab and Quantum Builder tabs on the Bench |
| 3 | Moves 5, 6 | Two Chairs, Submission Pack; old assessment retired from the UI |
| 4 | Moves 7, 8 | Escalation; pleadings on skills behind the existing arbitration rollout flags. The evidence and approval controls (correction C) ship first within this phase and do not depend on skills |
| 5 | Move 9 | Hearing Room with PDF deck; PPTX deferred |
| 6 | Move 10 | Navigation change, portfolio view |

Each phase is independently releasable. Phases 0–3 do not touch the arbitration engine.

### Gates that apply to every phase (from `CLAUDE.md`)

- Authorization through `PolicyService.authorize` / `require_permission` / `build_scope_query`; permission names in `dms.*` form; routers re-raise `(BaseDomainError, HTTPException)` together.
- Regenerate and diff the route inventory after any route change.
- Every new router gets one test that resolves its real dependencies.
- Rate limiters pass `scope=`. LLM drafting calls use `strict=True` with a deterministic fallback and degraded marking.
- Migrations dry-run with `--fail-on-warning`; documentary notes as `notices=`.
- Mocked Playwright suites for each new screen, added to `MOCKED_SUITES`; one staging suite per phase.

### Risks and what the plan does about them

| Risk | Handling |
|---|---|
| Skill text is method, not verified law; authorities are cited by name only | Seed as draft; publication requires technical review and independent legal approval bound to version, content hash and evaluation (Move 0); skill text never enters the source ledger (already enforced by design) |
| No golden set for claims or pleadings (existing blocker B1) | Build 10–15 anonymised matters per skill during Phase 1; this also unblocks B1 |
| Model invents days or amounts | Skills never compute; `no_invented_amounts` validator; all numbers from deterministic engines |
| No CPM data | Delay Lab states its method and its limits; TIA shown as unavailable rather than faked |
| Concurrent delay reduced to arithmetic | Overlaps are computed, never auto-deducted; each needs a recorded treatment with its contractual basis; undecided overlaps show a range and block locking |
| An assumed notice period mistaken for the contract's | Three verification states; an assumed default is never written into any finding or outgoing document |
| A simulation mistaken for a determination | Separate record types and collections; simulations are privileged, labelled, unexportable and cannot write facts or findings |
| Privileged strategy material leaking into exports or pleadings | Separate collection, separate permission, excluded from exporters and the source ledger, covered by tests |
| Three EOT systems drifting further, or a fourth appearing | Many-to-many revision-pinned links in Move 3; the claim head owns only the assessment, key-date and hindrance records stay authoritative and are never copied |
| Arbitration engine is off in production | Move 8 keeps both engines working and changes no rollout flag |
| Single LLM provider, hard-wired model | Model name moves to settings in Move 0; provider abstraction is out of scope |

### Not in this plan

PowerPoint export of the hearing deck; provider abstraction for the LLM; a CPM scheduling engine; post-award work under Sections 34 and 37; e-filing integrations; changes to the letter, variation or contract-master modules beyond the links named above.

---

## Part D — Decision record (owner, 2026-11-15)

| No. | Decision | Status | Where it lands |
|---|---|---|---|
| D-01 | Contractor-first for contractor workspaces; perspective derived from project/organisation configuration; Engineer and Employer workflows equally supported; switching perspective never changes facts or another party's approved findings; contractor-side simulations are internal analysis, not determinations | Approved with conditions | Move 0 (perspective), Move 5 |
| D-02 | Claim head owns the EOT assessment; hindrances, key-date submissions, determinations, notices, programmes and evidence are linked many-to-many by immutable, revision-pinned references; existing registers stay authoritative | Approved with conditions | Move 3 |
| D-03 | Independent Senior Legal Approver signs before any of the four packs is published; specialist technical co-review per pack; evaluator, signatory and publisher are separate; approval bound to version, content hash, suite, results and time; missing, revoked or stale approval blocks publication; Super Admin cannot substitute for legal approval; case-level gates still apply | Approved | Move 0 |
| D-04 | Presentation-style PDF hearing deck in Release 1, editable in-app before export; PPTX deferred | Approved | Move 9 |

Corrections accepted in this revision

| Ref | Correction | Change made |
|---|---|---|
| A | Subtracting overlaps from employer-caused critical delay is too simplistic | Delay engine computes overlaps as facts only; each overlap needs a decided treatment with contractual basis and method; net EOT is a range until decided (Move 3) |
| B | A default 28-day period must never be presented as a verified contractual requirement | Notice requirement resolved from executed instruments including amendments and special conditions, with three verification states; assumed defaults never enter findings or outgoing documents (Move 2) |
| C | Ungated drafting and a draft citing exhibits with zero linked sources | Citation states (verified / unverified / unresolved) computed and shown; approval and filing blocked on missing provenance; case-less drafts become non-approvable scratch drafts (Move 8) |

Standing constraint: the phased rollout is retained, and no implementation step may bypass the existing arbitration approval gates or production rollout controls.

Still open, to settle when Phase 0 is specified in detail
- Which named users hold `senior_legal_approver` and the three specialist roles, per organisation.
- Whether a technical reviewer and the evaluator may be the same person (the plan currently requires only that the legal signatory and the publisher differ from the evaluator and from each other).
- Who may mark a notice requirement `verified`: this revision assumes a user with claim-manage rights; a contracts-lead role may be preferable.
