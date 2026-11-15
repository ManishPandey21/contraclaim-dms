# Claim → Arbitration: audit and ten-move plan, with skills doing the heavy lifting

Date: 2026-11-15 · Status: plan only, nothing implemented · Scope: `projectDMS/backend/rbac_backend`, `projectDMS/client`

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
- The supplied skill packs are thin on references (7–20 lines each) and cite authorities by name only (McDermott, Unibros, Batliboi). They are method, not law reports; a lawyer must confirm them before the packs are published.

---

## Part B — The ten moves

Design rule for all ten: **the skill decides the method, the code does the arithmetic, the human makes the call.** Skills never compute dates, days or rupees; they classify, test, explain and draft. Every number shown to a user is produced by deterministic code from recorded inputs.

| # | Move | Skill that carries it | Replaces |
|---|---|---|---|
| 1 | Claim Bench: a claim is a board of heads, each tested on five elements | claims (prepare) | Flat claim detail page |
| 2 | Notice Clock: time-bar radar that opens claims before they exist | claims + letter-analysis + contract-letters | `sla_service` fixed windows |
| 3 | Delay Lab: concurrency matrix and net EOT on a swim-lane timeline | eot-claims | Three unlinked EOT systems |
| 4 | Quantum Builder with proof locks | claims (quantum principles) + eot-claims step 6 | Single `amount_claimed` field |
| 5 | Two Chairs: run your own claim as the other side | claims (determine), role overlay | Free-text "AI assessment" |
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
- Migration `v2026MMDD_0001_seed_claim_arbitration_skills.py` seeds the four as **draft**, not published. Publishing goes through the existing eval-report gate after legal sign-off. Emit documentary notes as `notices=`.
- Permissions: reuse `drafting.skill.*` for administration; add `dms.claim.skill.run` and `dms.arbitration.skill.run`.

Frontend
- Lift `SkillRail`, `SkillStepDrawer`, `SkillPalette` from `components/letter-bench/` to `components/skills/`, parameterised by subject. Move the admin page route from `/letters/skills` to `/skills` (redirect the old path).

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
- `services/claims/notice_clock.py` (deterministic): window = clause-index lookup for the project's claims clause (FIDIC 20.1/20.2 → 28 days; contract data may shorten it) with the existing `sla_rules` override; falls back to today's per-type default and says so. Computes `due`, `days_left`, and — the missing case — `served_late_by` when a notice exists.
- Triggers that create a **potential claim** (new status before `draft`): a Letter Bench analysis chain that routes to the claims or EOT skill; a hindrance saved with `critical_path_impact` and employer responsibility; a variation instruction without an agreed rate.
- Skill step `notice_compliance.v1`: given event, awareness evidence, notice letter and clause text, states whether notice was compliant and, if late, what the record shows about prejudice to the Employer. The skill's principle — never rely on time-bar alone — becomes a visible field in both chairs.
- One action "Draft notice" opens a Letter Bench pre-loaded with the head, routed to the contract-letters skill.

Frontend
- Register gains a first column: a day-count pill (green / amber / red / "served 12 days late"). A strip above the table: "3 notices due this week · 1 already late".
- Bench header shows the clock with the clause it was read from, linked to the clause.

### Move 3 — Delay Lab

Backend
- `ClaimDelayEvent` links a claim head to an existing hindrance (`hindrance_id`) or stands alone: dates, `risk_owner` (employer | contractor | neutral), `critical` (yes | no | unproven), `notice_ref`, `source_ids`. The hindrance register gains `claim` as a link target; key-date `EOTSubmission` gains optional `claim_id`. No data is moved; the three systems are joined, not merged.
- `services/claims/delay_engine.py` (deterministic, pure functions, heavily unit-tested): overlap detection between employer and contractor critical events → concurrency periods; net EOT = critical employer days − previously granted − overlap adjustments; revised completion date; LD-exposed days. Output is the skill's table: Event | Type | Critical delay | Concurrent? | Net EOT.
- Skill steps: `delay_event_classification.v1` (proposes risk owner and whether the record proves criticality, with reasons) and `concurrency_finding.v1` (narrative per overlap: EOT yes, prolongation cost no unless separated). Method is stated on every output; with no CPM data the only offered methods are As-Planned vs As-Built and Windows on milestones. TIA is listed as unavailable with the reason.

Frontend
- A swim-lane timeline: Employer lane, Contractor lane, Neutral lane, milestones on top. Overlaps are hatched. Dragging is not allowed; dates come from records. Below it, the net-EOT table, each cell traceable to the events that produced it.
- Use the `dataviz` conventions already in the product for colour; risk owner is encoded by lane position and label, not colour alone.

### Move 4 — Quantum Builder with proof locks

Backend
- `QuantumLine` per head: `category` (direct | prolongation | disruption | overhead_formula | loss_of_profit | interest), `basis`, `period`, `amount`, `record_ids`, `formula` (none | hudson | emden | eichleay), `lock_state`.
- Locks enforced in code, taken straight from the quantum reference: prolongation only for periods the Delay Lab marks non-concurrent employer delay; a formula line stays locked until records of actual loss are linked; loss of profit needs evidence of work declined. A locked line cannot enter a submission or a pleading.
- Arithmetic is code. Skill step `quantum_head_review.v1` only reviews: is each line supported, what record is missing, is it a global claim.
- Roll-up writes `amount_claimed`; arbitration's quantum annexures are generated from these lines in Move 7.

Frontend
- A build-up table with a padlock on each locked line and the exact record that would unlock it. Totals shown three ways: claimed, supported, locked.

### Move 5 — Two Chairs

The same board, viewed from the other side. Switching chair re-runs the claims skill's determination workflow with the engineer or employer role (the manifest already supports `roles_supported`).

Backend
- `ClaimDetermination` per head: `result` (full | partial | none | insufficient_information), `days`, `amount`, `findings`, `residual_risks`, `information_required[]` with deadline and contractual consequence. Schema `claim_determination.v1`; fallback is `insufficient_information` and degraded.
- For a contractor, this is a red-team run stored as `internal`; for an engineer or employer it is the draft determination and feeds a FIDIC 3.7-style letter through the contract-letters skill. One model, two uses.
- Supersedes `claim_assessment_service` for new claims; the old endpoint stays and returns history.

Frontend
- A chair toggle in the Bench header. In the opposing chair each head shows the likely answer and the three weakest points. "Request information" drafts the letter.

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
- Seeding is deterministic and explicit: heads → `claim-matrix` rows; notice results → `notice-compliance`; quantum lines → `quantum-annexures`; delay events and linked letters → `chronology-matrix`; linked documents → `document-index`. Rows carry `origin: {claim_id, head_id}` and start `needs_review`, as the existing rule requires. `_find_source_rows` prefers `case.claim_ids` and falls back to project scope only for cases with none.
- Dispute ladder on the case (deterministic tracker): determination → DAB / conciliation if the contract requires it → Section 21 notice date → limitation date, replacing the default three-year placeholder in `agents/deterministic.py:34` with dates the user confirms.

Frontend
- "Escalate" on the register (multi-select) and on the Bench. A confirmation sheet shows exactly what will be carried over and what is still weak.

### Move 8 — Pleadings on skills

Backend
- Replace the `*_INSTRUCTION` constants (`agents/llm.py:70-125`) and the counsel prompt (`llm_generator.py:131-175`) with compiled skill blocks from `indian-infra-arbitration-pleadings`, attached through `attach_skill_block`. Keep `strict=True` and the deterministic fallbacks. Bump the prompt-version constants (tests pin them). Record `skill_key`, version and content hash on the workflow state next to the existing snapshot hashes.
- New validator node `four_elements` in the LangGraph fan-out: each pleaded head must carry provision, event, causal link and quantified relief; notice compliance must be pleaded expressly.
- Counterclaim gets its own section set and a real agent (LD, defects, over-payment, indemnity, costs).
- Opponent pleading intake accepts an uploaded document (existing extraction pipeline) and splits it into numbered paragraphs.
- The engine stays behind its existing rollout flags. This move does not change which engine production runs.

Frontend
- **Traverse Board** for defence and rejoinder: opponent paragraph on the left, our response on the right (admit / deny / put to proof), denial reason mandatory, positive case and exhibit attached. This uses the existing paragraph-response model.
- The eight gates become a horizontal stepper with the current gate, who must act, and what blocks it.
- Every raw-id input becomes a picker. Engine vocabulary is renamed: "Prepare the case file", "Review the matrices", "Approve for drafting".
- The draft renders as a formatted pleading with numbered paragraphs and clickable exhibit chips; version diff.
- Split the two monolith pages into route-level components (`CaseRegister`, `CaseDashboard`, `MatrixEditor`, `Readiness`, `FilingBundle`, `PleadingEditor`, `TraverseBoard`).

### Move 9 — Hearing Room

Backend
- Case status gains `hearing_preparation`, `hearing`, `award` after `filed`.
- `services/arbitration_hearing/`: one generator per deliverable, each a skill step (`hearing_deliverable.v1`, `examination_plan.v1`) over approved matrices and the filed pleadings only. Validator `every_point_has_exhibit`. Internal documents (strategy note, examination plans, aide-mémoire) are stored apart from tribunal-facing ones (opening outline, timeline, matrix, deck, closing skeleton), with separate permissions.
- Witness register (`name`, `role`, `statement_document_id`, `topics`). Examination plans are per witness.
- Deck export to PPTX (python-pptx; new dependency, needs the pip-audit gate) following the skill's eleven-section order, one idea per slide. Visual timeline is rendered from the chronology matrix.
- "Tribunal questions": a fixed list from the advocacy reference (why was notice late, where is the critical-path proof) plus skill-proposed ones, each answered with exhibits or flagged as a gap.

Frontend
- `/arbitration/cases/:id/hearing`: a kit of eight cards with readiness per card, a timeline view colour-and-lane coded by risk owner, a rehearsal view for the opening with timing marks.

### Move 10 — Dispute Spine and exposure band

Backend
- `GET /api/disputes/spine?claim_id|case_id` assembles one read model: event → notice → claim → determination → dispute ladder → pleadings → hearing → award, with the date, state and owner of each stage.
- Exposure band (deterministic): per head, claimed vs supported vs the opposing chair's likely result gives best / likely / worst totals in days and rupees. No probabilities are invented; "likely" is the Two-Chairs result the user has confirmed.

Frontend
- A single "Disputes" entry replaces eight sidebar links. The spine is a persistent header on every claim and case screen. Portfolio view: all claims by stage, with exposure bands and the Notice Clock strip.
- House-keeping in the same pass: Indian number grouping with ₹ lakh/crore, `DD-MM-YYYY` dates, accessible badge contrast, labelled row actions, the "Regisration" typo.

---

## Part C — Delivery

### Sequence

| Phase | Contents | Ships to users as |
|---|---|---|
| 0 | Move 0; claim model fields; status transition guard; register and detail quick fixes (dates, grouping, contrast, pickers, late-notice flag computed deterministically) | Visible fixes, no AI change |
| 1 | Moves 1, 2 | Claim Bench behind `CLAIM_BENCH_ROLLOUT_MODE` (off / shadow / primary, same pattern as the Letter Bench) |
| 2 | Moves 3, 4 | Delay Lab and Quantum Builder tabs on the Bench |
| 3 | Moves 5, 6 | Two Chairs, Submission Pack; old assessment retired from the UI |
| 4 | Moves 7, 8 | Escalation; pleadings on skills behind the existing arbitration rollout flags |
| 5 | Move 9 | Hearing Room |
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
| Skill text is method, not verified law; authorities are cited by name only | Seed as draft; publish only with a signed eval report; a lawyer reviews each pack; skill text never enters the source ledger (already enforced by design) |
| No golden set for claims or pleadings (existing blocker B1) | Build 10–15 anonymised matters per skill during Phase 1; this also unblocks B1 |
| Model invents days or amounts | Skills never compute; `no_invented_amounts` validator; all numbers from deterministic engines |
| No CPM data | Delay Lab states its method and its limits; TIA shown as unavailable rather than faked |
| Privileged strategy material leaking into exports or pleadings | Separate collection, separate permission, excluded from exporters and the source ledger, covered by tests |
| Three EOT systems drifting further | Linked by foreign keys in Move 3; the claim is the parent, key-date submissions and hindrances stay authoritative for their own data |
| Arbitration engine is off in production | Move 8 keeps both engines working and changes no rollout flag |
| Single LLM provider, hard-wired model | Model name moves to settings in Move 0; provider abstraction is out of scope |

### Decisions needed before implementation

1. **Default chair.** Is the primary user the Contractor (preparing claims) or the Engineer / Employer (determining them)? The plan supports both; the default shapes the first screen. Recommendation: Contractor, since the existing register and letters flow are written from that side for claims.
2. **EOT parentage.** Confirm that a claim head is the parent and key-date EOT submissions link up to it (recommended), rather than the reverse.
3. **Legal reviewer.** Who signs the eval reports that publish the four packs?
4. **Deck format.** PPTX export (new dependency) or PDF only for the first release of the Hearing Room. Recommendation: PDF first.

### Not in this plan

Provider abstraction for the LLM; a CPM scheduling engine; post-award work under Sections 34 and 37; e-filing integrations; changes to the letter, variation or contract-master modules beyond the links named above.
