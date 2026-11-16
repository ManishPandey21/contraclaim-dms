# Phase 4b — Claim Bench and Arbitration Hardening, Correctness and Usability: implementation plan

Date: 2026-11-16 · Revised: 2026-11-16 after owner review (section 13) · Status: **Approved in principle with amendments; implementation authorised from Wave 0 (section 13).** · Branch: `claude/zen-ptolemy-695liz`, plan first written at `3d704e1` against code at `f17e4b0`

Parent spec: `docs/superpowers/specs/2026-11-15-claim-arbitration-skills-audit-and-plan.md` (Moves 0–8, decisions D-01 to D-04, corrections A to C). Phase status notes: `docs/superpowers/plans/2026-11-15-claim-arbitration-phase0.md` to `2026-11-19-claim-arbitration-phase4.md`. Operator notes: `docs/CLAIM_BENCH.md`.

Paths below are relative to `projectDMS/backend/rbac_backend/` (backend) or `projectDMS/client/src/` (frontend) unless they start with `projectDMS/` or `scripts/`. Line numbers are those at `f17e4b0`.

## Standing execution rules for this phase

- `CLAIM_BENCH_ROLLOUT_MODE` stays `off` in every deployed environment until the exit criteria in section 9 are met. Staging may use `canary` for the internal test tenant only.
- All four claim and arbitration skill packs stay unpublished. Phase 4b builds the evaluation machinery; it does not publish.
- No merge, deploy or production change is part of this plan. Production readings in section 8.1 are read-only.
- Phase 5 (Hearing Room) does not start until section 9 is met.
- Passing unit tests is not evidence of end-to-end readiness. Each ticket names the integration, route, browser or concurrency test that proves it.
- No arbitration engine flag, rollout mode, prompt-version constant or acceptance receipt is changed. Every arbitration change in this phase adds a blocker, adds information, or fixes a check that is broken; none relaxes a gate.

---

## 0. Summary

The Phase 0–4 implementation is structurally sound but cannot be switched on. Four blockers stop it: a publication bypass (B1), a pleading check that can never pass (B2), reviewer roles nobody can hold (B3), and approval and export buttons that offer actions the server refuses (B4). Beyond those, twenty correctness defects were confirmed against the source, several of them in contractual arithmetic (already-granted EOT, neutral delay, amounts) and in four-eyes controls (notice verification, determinations).

Phase 4b fixes these in 48 tickets across nine workstreams. It also builds the missing journey: claim start → heads → notice clock → delay → quantum → determination or submission → escalation → pleading. The order is: first measure production read-only, then fix security, then fix the arithmetic, then the user experience, then end-to-end proof.

**Production is not assumed empty.** Production runs `release/contraclaim-rc1` at `34d664a` (read 2026-11-15, `docs/POST_CUTOVER_OPERATIONAL_ASSESSMENT_2026-11-15.md` §6). That release has 19 migrations, the claims register (eight-status enum, free `amount_claimed`, `sla_rules` windows) and arbitration drafting on `arbitration_v2`. It does not have the skill registry, the Bench or any Phase 0–4 change. Nothing about its claim, arbitration, role or user data has been read. Ticket 4B-01 reads it before any migration, backfill or legacy-route decision is final.

---

## 1. How the findings were verified

| Method | Scope | Result |
|---|---|---|
| Test run, Python 3.12 against `rbac_backend/requirements.txt` with CI env values | 13 claim, skill and gate test files | 354 passed |
| Test run, vitest | `components/claim-bench`, `lib/__tests__`, `ClaimBenchPage`, `ArbitrationDraftingPage` tests | 108 passed; no type errors in changed files |
| Independent code review, one reviewer per phase | Phases 0–4 against the parent spec | Section 2 findings, first pass |
| Second independent verification, four reviewers by area | Every first-pass finding re-traced to root cause; pure functions executed with crafted inputs | Confirmed, corrected or refuted per finding (section 2.4) |
| Direct source read by the plan author | B1, B2, B3, C-01, C-02, C-08, C-12, C-15, C-17 | Confirmed; quoted lines below |

No live backend, browser run or Playwright run was available. Findings about runtime behaviour across services are stated as traced, not observed.

---

## 2. Verified root-cause analysis

Each finding has an ID used throughout this plan. **V** = confirmed by reading and, where stated, executing the code. **P** = plausible, traced but not executed end to end.

### 2.1 Blockers

**B1 — Skill publication can bypass every required approval (V, security).**

- Root cause.
  - `routers/skills.py:140-170`: `POST /skills/import` builds the `SkillManifest` from a client form field.
  - `services/skills/registry.py:253-300`: `create_draft` refuses only non-draft versions (`:265`). It `$set`s the whole record, manifest included, over any draft (`:294-299`), and forces `owner_scope=SYSTEM_SCOPE` without checking the key against the bundled draft keys (`:276`).
  - `registry.py:413-417`: `_require_approvals` returns early when `required_approvals` is empty. `tests/test_skill_publication_gate.py:189` asserts that early return as intended behaviour.
  - `services/skills/registry.py:144-147`: `resolve` picks the highest published system semver.
- Exploit. A holder of `drafting.skill.publish` imports `indian-infra-claims` as `1.0.1` with `required_approvals: []`, types an evaluation report, and publishes. The new version becomes the live claims pack for every tenant with no legal sign-off.
- Aggravating facts.
  - `services/skills/loader.py:143`: `content_hash = bundle_hash(skill_md, references)` excludes the manifest. A re-import with identical text keeps existing approvals valid while weakening the requirements.
  - `models/skill.py:75-91`: the evaluation report is client-typed with a client-set `threshold`.
  - `services/skills/approvals.py:40-49`: `eval_report_hash` covers suite, score, threshold and case count only. It excludes `recorded_by`, so "evaluator ≠ publisher" (`registry.py:426`) compares against a name the publisher chooses.
  - The UI asks for the evaluator's user id as free text (`components/skills/SkillPublishPanel.tsx:154-183`).
  - No server-side evaluation runner exists. The only report producer is the seed stub (`services/skills/system_skills.py:269-276`).

**B1b — Publish and revoke race; independence enforced only at record time (V).**

- `registry.py:329-345`: approvals are checked, then `update_one` filters on status and content hash only. A revoke landing between the two is missed. Its follow-up (`routers/skills.py:262-268`) sees `draft` and does not deprecate.
- Revoke deprecates a published version whenever the content hash matches, including for a stale or unrelied-on approval.
- Legal/technical independence is checked when an approval is recorded (`approvals.py:168-184`), never at publish. The approvals index is non-unique (`migrations/v20261115_0001_seed_claim_arbitration_skill_drafts.py`). One user holding both technical roles can give both EOT technical approvals.
- No router-level test covers revoke → deprecate.

**B2 — Every Statement of Claim pleading an approved claim head is blocked; a case with none passes (V).**

- Root cause.
  - `services/arbitration_drafting/citation_states.py:99-113`: `pleaded_head_gaps` reads `metadata.clause_ids`, `facts`, `causation`, `relief`, `notice_ids`, `amount_or_days` and `evidence_ids`.
  - `services/arbitration_drafting/context.py:1393-1410`: the claim-matrix ledger builder copies only `evidence_ids`, `notice_ids`, `calculation_id`, `amount_or_days` and permission fields into metadata.
  - Escalation does write all fields to the row (`services/claim_escalation.py:168-186`). `create_matrix_row` keeps them (`case_workspace.py:423-458`, models `extra="allow"`), but they never reach the validator.
- Further causes found in verification.
  - `claim_escalation.py:181` hard-codes `notice_ids: []`. The manual claim-matrix form (`pages/ArbitrationCaseWorkspacePage.tsx:174-181`) has no clause, notice or evidence field. No screen can satisfy "a notice record".
  - Zero approved claim rows yield zero gaps, so the check passes trivially. Unapproved rows are dropped silently (`context.py:1364`). Rows selected through `approved_matrix_revision` (`context.py:534-539`) are never checked as heads.
  - The legal-review gate (`workflow_service.py:767-787`) reads `run.validation_blockers`, which `workflow_validation.py:154-260` classifies by keyword. Head-gap wording ("the contractual provision…", "the causal link…") matches no keyword, so it can pass legal review and fail only at final approval.
  - `tests/test_claim_escalation.py:315-347` builds `metadata` by hand, so the defect is invisible to the suite.

**B3 — No one can hold the reviewer roles; reviewers cannot use the review screen (V).**

- Root cause.
  - `chief_contract_expert`, `delay_planning_expert`, `arbitration_case_preparation_expert` and `senior_legal_approver` exist only as string constants (`services/skills/system_skills.py:50-53`, `services/arbitration_drafting/approval_policy.py:87-103`). None is seeded in `initial_data/default_roles.py` or any migration.
  - Custom roles get an ObjectId `_id` (`services/role_service.py:525`), and users store that id (`routers/users.py:899-939`). `actor_role_names` compares names (`core/permissions.py:464-486`), so a custom role named `chief_contract_expert` never matches. Only a role document whose string `_id` equals the name works, which is the seeded-system-role pattern.
  - Role names are globally unique (`role_service.py:458`): one tenant creating the name squats it for all.
  - `pages/UsersPage.tsx:344,408,571-578` assigns exactly one role from a fixed list. Editing a user replaces all roles and erases any reviewer role.
  - `_enforce_role_assignment_scope` (`routers/users.py:958-1013`) lets an org admin assign any org-scoped role. Seeding reviewer roles at org scope would let a tenant admin make themselves a platform legal approver.
  - `pages/SkillAdminPage.tsx:40` loads only `GET /skills/admin`, which needs `drafting.skill.publish` (`routers/skills.py:91-101`), although the approval routes need only `drafting.skill.read`. A pure reviewer sees an error page.
- Wider fact: `ARBITRATION_REVIEWER_ROLE_MATRIX` is not passed through any compose file, and `legal_reviewer`, `contract_reviewer`, `delay_reviewer`, `quantum_reviewer` and `export_authorizer` are not seeded either. Unless production holds hand-made role documents (reading P-05), today's arbitration reviewer gates are also unreachable there. This is pre-existing and recorded as AQ-3.

**B4 — Approve and Export are offered when the server will refuse them (V).**

- Root cause.
  - `pages/ArbitrationDraftingPage.tsx:564`: `disabled={approving || !draft || !markdown}`.
  - `components/arbitration/DraftCitationPanel.tsx:83`: shows the internal key "S3", with the label and reason only in a `title` tooltip. Its data is not passed to the page.
- The server refuses correctly. Approve checks blockers, validation and the governed chain (`services/arbitration_drafting/service.py:845-879`); filing export checks approved, locked, hash, validation and chain (`:943-976`).
- `GET /arbitration/drafts/{id}/citations` (`routers/arbitration_drafting.py:743`) already returns `can_be_approved`, `cannot_be_approved_reason`, `approval_blockers` and `pleaded_head_gaps`. The UI does not use them.
- **4B-39 (2026-11-16):** the citations response carries `action_gates.approve` and `action_gates.filing_export` (`services/arbitration_drafting/action_gates.py`): allowed, structured reasons with a page anchor each, and `checked_on_action` naming what is checked only on the action (case readiness, plan and legal review approvals; the filing citation audit). Every reason is one the action refuses on, including the caller's permission and author-approver separation. Approve and filing export now run the same gate first and refuse with its reasons (409 `draft_action_refused`), so the screen and the API decide from one function. The page disables Approve and the filing exports from the gate (also while it is unknown), lists the reasons with links, and the citation panel shows each citation's or exhibit's label, and why an unresolved exhibit is not found. Order change: a gate reason is now reported before the gate-role and case-readiness refusals. Proof: `test_arbitration_ui_api_parity.py` (2, CI real-Mongo step) builds a draft for every gate code, reads the gate as the page does, and calls the action: refused with that code (403 for permission); `ArbitrationDraftingPage.test.tsx` (+3), citation panel (+2). The approval-gate stepper is 4B-40.

### 2.2 Correctness defects

| ID | Finding | Root cause | Evidence of failure |
|---|---|---|---|
| C-01 | Already-granted EOT ignores the key-date register's supersession and freeze rules (V, executed) | `services/claim_delay_lab.py:273-294` rejects status `superseded`, which in the register is the *superseding, operative* determination (`services/key_date_revision_service.py:43, 884-885`). It counts drafts (`frozen_at` null), `pending` and `under_review`, and sums every linked determination. The register's rule is `_replay_determinations` (`key_date_revision_service.py:1166-1187`). The snapshot (`claim_delay_lab.py:524-533`) omits `frozen_at` and supersession, so neither change marks a link stale. | D1 granted 10 days; D2 status `superseded`, supersedes D1, grants 15: Lab says 10, register says 15. An `under_review` determination granting 5 days is counted. |
| C-02 | An Employer event can reduce the extension; neutral × Contractor concurrency is never surfaced (V, executed) | `services/claim_delay_engine.py:144` sets `neutral_only_days = len(neutral - employer)`, used in `net_eot` (`:195`). Neutral events never enter `Overlap`. | C1 and N1 critical 1–10 Jan, neutral included: 10 days. Add E1 1–10 Jan with the overlap decided `no_eot`: 0 days. |
| C-03 | Concurrency findings are keyed by dates only; adjacent events merge (V, executed) | `claim_delay_engine.py:155` builds `overlap_id` from the dates. `ConcurrencyFinding` (`models/claim_delay.py:89-96`) stores no event ids; `_drop_orphan_findings` (`claim_delay_lab.py:683-697`) compares ids only. | {E1,C1} and {E2,C2} on the same dates share one finding. Ca 1–5 and Cb 6–10 against E1 1–10 give one overlap and one treatment. |
| C-04 | `not_true_concurrency` is compensable without any cost basis (V) | `claim_delay_engine.py:29` puts it in `COMPENSABLE_TREATMENTS`. "Separated costs" (parent spec Move 4) is not enforced anywhere. | `compensable_days` with `not_true_concurrency` returns 10. |
| C-05 | A key-date record from another contract can be linked to a claim head (V) | `claim_delay_lab.py:573-577` checks organisation and project only. Claims have no `contract_id` (`models/claim.py`); key-date records do (`models/key_date.py`). Same gap noted at `services/arbitration_drafting/context.py:1840`. | A multi-contract project links contract B's determination to a contract A claim. |
| C-06 | Quantum: formula amounts typed; money is float; proof locks not re-checked; misleading blocker (V) | `services/claim_quantum.py:45` stores a typed `amount: float` for Hudson, Emden and Eichleay lines; sums are floats (`:117-128`). `line_lock_reasons` (`:75-106`) never re-checks that records are still linked to the claim. `claim_delay_engine.py:118-123` returns None both for "no end date" and "end before start", and `claim_delay_lab.py:316-320` gives the wrong instruction for the second. | Unlink a proof document: the line stays unlocked. An event ending before it starts says "set the date the analysis runs to". |
| C-07 | Two sources for the claimed amount (V) | `routers/claims.py:117` `BENCH_FACT_FIELDS` omits `amount_claimed`; `PUT` (`:277-289`) accepts it on a claim with a board, even a frozen one (no `evidence_frozen_at` check in `routers/claims.py`). The register sends it on every save (`pages/ClaimsRegisterPage.tsx:261`). `services/claim_bench.py:572-576` sums only `if amounts`. `services/claim_quantum.py:183-186` `_roll_up` does nothing with zero lines. `claim_bench.py:815-819` `patch_head` accepts a typed amount over a build-up. | Clear every head amount: the register keeps the old total. Remove the last quantum line: the head keeps its amount. **4B-17 (2026-11-16):** once a claim has a board, `PUT /claims` refuses a changed or cleared `amount_claimed` or `eot_days_claimed` (409 `claim_amount_from_board`) and drops unchanged values (the register sends every field on save); a frozen claim refuses every edit (4B-12). The roll-up writes `null` when no head has an amount. Removing a head's last quantum line clears its amount. A typed or cleared head amount on a head with lines is 409 `head_amount_from_lines`. The register shows the two fields read-only and does not send them. Paise and formulas stay with C-06. Compatibility: a board whose heads all have no amount clears the claim's amount on its next save; not read against production (the Bench is off there). Proof: `test_claim_amount_source_mongo.py` (5, CI real-Mongo step), `ClaimsRegisterPage.board-amount.test.tsx` (2). |
| C-08 | A typed quote can become a contract-verified notice period; the recorder can verify their own entry (V) | `services/claim_notice_requirements.py:168-176` checks only that the quote contains the day number. `:183-189` copies `clause_no`, `quote`, `clause_uid`, `document_id` and `origin` from the client. `models/claim_notice.py:63` makes `claim_type` free text. `set` needs `CLAIM_EDIT` only (`routers/claim_bench.py:273-276`); `verify` needs `CLAIM_MANAGE` only (`:294-301`) with no recorder ≠ verifier check (`claim_notice_requirements.py:212-227`). No audit event. | Record "clause 99.9: notice within 60 days" with `origin=clause_index`, verify it yourself: every claim of that type reads "contract, verified". **4B-14 (2026-11-16):** `set` resolves the clause in the project's clause index (by `clause_uid`, then normalised number); a resolved clause must contain the normalised quote (422 `notice_quote_not_in_clause`) and supplies the number, title, document and uid; otherwise the record is `entered_from_contract` with none of the client's document or uid claims. `claim_type` must be a `ClaimType` (422). `set` and `verify` need `dms.contract.clause.read`; the recorder cannot verify (403 `notice_self_verification`); verification is compare-and-set; both write `claim.notice_requirement.set` / `.verified` audit events. Legacy records keep `origin=entered` and their verification; reading P-14 counts self-verified ones before any is re-flagged. Proof: `test_notice_verification_mongo.py` (5, CI real-Mongo step). |
| C-09 | Determination four-eyes is form only (V) | `services/claim_determinations.py:585-612`: approve and send-back never check `user_id == reviewer_id`; `:578` checks only "not the author", not existence or authority. `_carried` (`:453-461`) keeps `result` and `findings` and clears only `decided_by`; `submit_blockers` (`:255-265`) checks `result is None` only. Approve, send-back and `set_finding` never re-check project capacity (`:333`). `revise` (`:463-472`) accepts superseded revisions. `components/claim-bench/DeterminationsPanel.tsx:251` defaults `issued_by` to an invalid party. | A authors v1, B approves; B opens v2 unchanged and submits; A approves v2, which is A's own findings re-issued. |
| C-10 | Escalation is not concurrency-safe or reversible (V) | `services/claim_escalation.py:433-437` uses `find_one` then insert, with no unique index (`migrations/v20261119_0001_claim_escalation_and_scratch_drafts.py`). The claim update (`:439-449`) is unconditional and bypasses `_require_status_transition` (`routers/claims.py:148-162`) and per-claim audit (only a case event, `:455`). A filed or archived case is not refused. The preview runs without the target (`components/claim-bench/EscalateDialog.tsx:61`), so a claim already in case X is blocked when X is chosen. There is no un-escalate route. The freeze is a direct `$set`, not `DocumentRelationshipService.freeze` (`services/document_relationship_service.py:565`), which then refuses with "already frozen". | Two concurrent runs into one case insert duplicate rows; two "new case" runs create an orphan case. **4B-36 (2026-11-16):** an escalation run (1) refuses a filed or archived case (preview blocker and 409) and runs every freeze check before writing (`DocumentRelationshipService.freeze_preflight`, split out of `freeze` unchanged), because nothing unfreezes a claim; (2) for a new case takes an idempotency key as a unique `_id` in `claim_escalation_keys` (422 without one): a repeat waits for and extends the first run's case; (3) reserves each claim with one update conditional on `{arbitration_case_id: null, status}` that moves the status through the lifecycle and records `escalation` (prior status, freeze state, case, key, seeded rows); a claim already in this case makes the run a no-op, one taken meanwhile releases this run's reservations, soft-deletes a case it created and answers 409 `escalation_claim_taken`; (4) freezes through `DocumentRelationshipService.freeze`; (5) seeds rows behind migration `20261120_0001` (M-05: partial unique (`case_id`, `origin_key`) on the 12 matrix collections; duplicates found by the dry run are warnings and that collection keeps no new index), reading a duplicate key as already carried; (6) audits `claim.escalated` per claim. The preview is computed for the chosen case. Departure: the idempotency key lives in `claim_escalation_keys` (unique `_id`, no index migration) and is copied to `claims.escalation.idempotency_key`; M-04 is not needed for it. Not here: weak-head acknowledgement (UI, 4B-40) and undo (4B-37). Proof: `test_claim_escalation_safety_mongo.py` (7, CI real-Mongo step), `EscalateDialog.test.tsx` (+2). **4B-37 (2026-11-16):** `POST /claims/escalation/undo` {claim, case, reason} needs `dms.claim.manage` on the claim, `dms.arbitration.edit` on the case and a reason (422), and is refused (409 `escalation_undo_after_pleading`) once any draft carrying the case's id has a version; scratch drafts never count. It restores the claim's prior status only if the status is still the one escalation set (409 `escalation_claim_moved`); lifts only the freeze the escalation made, by its stamp (`DocumentRelationshipService.lift_freeze`, one transaction, audited `document_relationships.unfrozen`), giving each link back the version recorded before the freeze, while an earlier freeze is kept; archives the seeded rows (`deleted_at`, reason) and moves their `origin_key` to `archived_origin_key` so the claim can be escalated again; removes the claim from the case, withdraws readiness approvals, and soft-deletes a case the escalation created that has no other claims, rows or drafts; keeps the record in `escalation_history`; audits `claim.escalation_undone` and `arbitration_case.claim_escalation_undone`. Legacy `linked_document_ids` turned into links by the freeze stay links. The "Undo escalation" control is UI work (4B-40). Proof: `test_claim_escalation_undo_mongo.py` (6, CI real-Mongo step). |
| C-11 | Citation states accept unknown statuses as verified and miss exhibit references (V) | `citation_states.py:27`: only `[S<n>: …]` is detected; `workflow_validation.py:52` also accepts `[S1]` and `[SRC-n]`. Exhibit references ("Ex. C-12", "Exhibit R/3", "CW-1/4", "Annexure A-7") are not detected. `UNVERIFIED_SOURCE_STATUSES` (`:28`) is a deny-list, so `rejected`, `stale` and `ai_suggested` count as verified, and it contradicts `context.py:30-45` on `selected`. Ledger builders default missing status to `approved` (`context.py:908, 966, 1074, 1271, 1330, 1388, 1449, 1512`). Exhibit numbering is max+1 with no unique index (`case_workspace.py:2511-2523`). | A draft citing a missing exhibit as "Ex. C-12" shows "cites no sources" and is not blocked on it. |
| C-12 | Drafting a notice from a claim raises a signal for a duplicate claim (V, traced) | `services/letter_drafting/bench.py:379-383, 524-530, 716, 1946-1954` gives a claim-triggered board an issue type of "claim" or "delay". `services/skills/router.py:77-86` adds the claims or EOT link even when unavailable. `services/claim_signals.py:181-188` lists any board with that chain and has no trigger filter. Also: `raise_potential` (`:210`) does not re-check hindrance eligibility; signal routes (`routers/claim_bench.py:142-192`) skip `ensure_enabled`; an organisation-less board makes the duplicate lookup use `""` while create stores the user's organisation, so a second accept raises a 500. **4B-13 (2026-11-16):** a board whose trigger is a claim is never listed and raising it answers 422 `signal_not_eligible`; raising a hindrance re-reads critical path, responsibility and archive (422); the organisation is the source's, else the navbar selection, else the user's, and one value is used to find and to create (none at all → 422 `signal_organisation_required`). `ensure_enabled` on both routes was already in place (4B-03). Observed, not changed: Letter Bench signals are read with `drafting.request.view`, which a tenant role reaches only through a drafting-expert allocation, so an org or project admin sees hindrance signals only. Proof: `test_claim_signals_mongo.py` (7, CI real-Mongo step), `test_claim_notice_clock.py` (+2). | Start a notice letter from claim CLM-7: the register shows "a letter may be a claim not yet raised"; accepting creates a second claim. |
| C-13 | Claim creation and status writes are unguarded (V) | `models/claim.py:202` `ClaimCreate` inherits `status` and `origin`; `routers/claims.py:205-238` persists both. The origin unique index (`migrations/v20261116_0001_claim_bench_indexes.py:57-63`) can be squatted to hide a real signal. The status check reads, then `ClaimService.update` (`services/claim_service.py:83-84`) writes on `{_id}` only. `claim_service.py:80` drops None, so no field can be cleared; `lib/format-in.ts:59-61` turns a cleared date into `undefined`. A string in `sla_rules` makes `services/sla_service.py:92,107-108` raise `timedelta(days="28")`. | A claim created at `agreed`; two concurrent transitions both succeed; a wrong notice date cannot be removed; `/sla` returns 500 for an organisation with a string window. |
| C-14 | Working perspective can be switched freely, by two paths, without audit (V) | `routers/projects.py:308-332` needs `projects:update`, writes no audit and has no lock. `PUT /projects/{id}` (`:335-379`) also accepts `contractual_capacity` (`models/project.py:30`). Determinations read it live (`claim_determinations.py:317, 399-418`). The Bench banner (`pages/ClaimBenchPage.tsx:185-211`) and "Open the board" (`:230`) are shown to users who cannot act. | Flip a project to Engineer, author a determination, flip back to Contractor. |
| C-15 | Super Admin reads privileged analysis and approves determinations (V) | `services/policy_service.py:147-148` returns `_allow("superadmin")` before any check, as do `:166`, `:210` and `services/permission_service.py:805`. No exclusion mechanism exists. | Super Admin opens Internal analysis for any organisation in the active selection. |
| C-16 | Submission depends on the viewer; validators are bypassable; Word output can be invalid (V, executed) | `routers/claim_findings.py:373-430` composes, views and exports from the per-user `_claim_documents` (`routers/claim_bench.py:683-691`). Stored chronology text carries the composer's document titles (`services/claim_submission.py:377`); a narrower viewer gets a different `input_hash` (`:250, 723`), sees sections as stale and cannot export (`:729-733`). `numbers_in` and `validate_section_text` (`:159-183`) accept amounts in words, lakh/crore scaling, borrowed digits and cross-head swaps. The reservation check is a substring (`"reserv"`). `_CLAUSE` (`:157`) misses "Cl." and "Article" and matches "Clause 2026". The DOCX writer (`:534-581`) does not strip XML-invalid control characters and has no `w:tblGrid`. `python-docx` is in neither requirements file, so `services/arbitration_drafting/exporter.py:106-110` always falls back to a minimal writer in production with the same control-character defect. | "INR 10 lakh and 45 crore" passes when 10 and 45 appear in a date and a day count. A title containing `\x0b` produces a file Word will not open. |
| C-17 | A board can be locked with stale tests; staling can fail silently (V) | `services/claim_bench.py:272-294` `compute_gate` counts `stale` but never blocks on it. `routers/claims.py:137-141` `mark_bench_tests_stale` returns None on a compare-and-set miss and the caller ignores it (`:289`). | Change the event date while another user edits the board: the board stays locked on tests that no longer match the facts. |
| C-18 | Rollout configuration fails open to "off" silently; tenant cache not keyed (V) | `services/claim_bench.py:56, 209-227` map any unknown value, including the spec's `shadow`, to off. `core/config.py:697-760` validates other rollout modes but not this one. `hooks/useClaimBench.ts:41-52` caches availability for 60 s without organisation or project in the key. | A typo in the mode silently disables the Bench. |
| C-19 | Explicit-grant permissions are pushed to org and project admins (V) | `initial_data/default_roles.py:400-405` adds `dms.claim.determine` and `dms.claim.strategy.read` to `orgadmin` and `projectadmin`, contradicting its own comment (`:398-401`). `services/role_contract_alignment.py:125-131, 203-231` treats these as class-A additions, so `scripts/align_role_contract.py --apply` grants them to existing roles. `docs/CLAIM_BENCH.md` says no existing role gains them. | After alignment, every org admin can author determinations and read privileged strategy. |
| C-20 | Attaching a scratch draft to a case is not validated (V) | `services/arbitration_drafting/service.py:232-242` sets `is_scratch = not case_id` without loading the case or checking organisation and project. The router checks `ARBITRATION_EDIT` on the draft only (`routers/arbitration_drafting.py:690-698`). | A draft attached to a nonexistent or foreign case loses its scratch mark. **4B-38 (2026-11-16):** creating a draft in a case or attaching one loads the case (missing or deleted → 404; another organisation's is not visible → 404) and requires it to be in the draft's organisation and project (409 `draft_case_scope_mismatch`); the route also requires `dms.arbitration.edit` on the case. Nothing is written on a refusal, so the scratch mark is kept. Proof: `test_draft_case_attach_mongo.py` (3, CI real-Mongo step). |
| C-21 | Reviewer and approver pickers are not scoped to the project or to authority (V) | `components/claims/ClaimApprovalDialog.tsx:96-115` calls `/users` without organisation; `services/authorization_service.py:380-440` returns all the caller's organisations; there is no authority filter. The determination approver list loads only the first 50 users. | People from other projects, or without authority, are offered and then refused. |
| C-22 | Journey gaps the spec required and Phases 0–4 left out (V) | No three-question start (`pages/ClaimsRegisterPage.tsx:546-670` is a 14-field form). No net-EOT table (`components/claim-bench/DelayTab.tsx`). Letters shown as raw ids (`pages/ClaimDetailPage.tsx:261-268`). The register strip counts only filtered, loaded rows. Timeline bars are unlabelled except by tooltip (`DelayTab.tsx:73-78`). No determination or RFI letter content, deadlines UI, section editing, gate stepper, pickers for raw ids, or plain-language vocabulary on arbitration pages. | — **4B-40 (2026-11-16), arbitration and escalation part:** approval-gate stepper for the eight gates on the case workspace (current gate and who acts, from the workflow state; no node name, engine or hash shown); document picker and version pickers instead of the id fields; plain status words on both arbitration pages and the project name instead of its id; multi-select escalation from the register (one project); per-claim weak acknowledgement, enforced by the server (422 `escalation_weak_unacknowledged` for an unlocked, weak claim not listed in `acknowledged_weak`); "Undo escalation" on the Bench for claim managers. Proof: `e2e/arbitration-pleading-gates.spec.ts` (mocked: the stepper names the gate and who acts; no ObjectId, UUID, hash or node name visible though the mock sends them), RTL for the stepper, pickers, dialog, undo and register, and `test_claim_escalation_safety_mongo.py` (+1). The other C-22 items stay with 4B-20, 4B-25, 4B-31, 4B-32 and 4B-34. |
| C-25 | **Found while building 4B-33, reproduced on production's code `34d664a`:** an arbitration case created through `create_case` cannot be found on a real MongoDB (V, executed) | `ArbitrationCase.deleted_at` defaults to None and is stored as `null`. The case queries in `services/arbitration_drafting/case_workspace.py` (`get_case`, `list_cases`, `update_case`, readiness approval, case-scoped source lookup) asked for `{"deleted_at": {"$exists": False}}`, which a stored null does not satisfy. The in-memory test double ignores `$exists`, so no unit test saw it. Fixed with 4B-33: those five queries use `{"deleted_at": None}` (missing or null). Matrix rows and drafts declare no `deleted_at` and are unaffected. Evidence class: repository-inferred, reproduced locally, not a production reading. It may explain the absence of production arbitration cases, which only a reading can confirm | `create_case` then `get_case` gave 404 and `list_cases` gave `[]`, on 34d664a and on this branch, MongoDB 8.0.4 |
| C-26 | **Found while verifying C-25 (owner decision 4), reproduced on 34d664a and 6d65bc3:** the arbitration case list and drafts list refuse every tenant user with 403 "No active subscription" when the request names no organisation (V, executed) | `routers/arbitration_drafting.py` passed the query's `organization_id` (None) to `PolicyService.authorize`, and the entitlement check finds no subscription without one. The arbitration pages list with no parameters and the escalation dialog sends only `project_id`. Super Admin skips the check, so it was unaffected. Fixed: both routes fall back to `current_user.organization_id`, as the other register routes do; the scope filter is unchanged. Evidence class: repository-inferred, reproduced locally through the production app; production impact depends on its subscription rows (not read) | `test_arbitration_case_lookup_mongo.py`: 6 tests; without the C-26 change 4 fail, without the C-25 change 5 fail |
| C-27 | **Found while building 4B-12, same pattern as C-26, same code on 34d664a:** `GET /api/sla/upcoming` and `/api/sla/breached` refuse every tenant user with 403 "No active subscription" when the request names no organisation, and the organisation's own notice windows are never looked up (V, executed) | `routers/sla.py` passed the query's `organization_id` (None) to `authorize` and to `list_sla`. The SLA tracker page and the claims register call them with no organisation. Fixed in 4B-12: both fall back to `current_user.organization_id`; row scope unchanged. Evidence class: repository-inferred, reproduced locally through the production app; production impact depends on its subscription rows (not read). Added to the hotfix proposal on the owner's instruction (2026-11-16) | `test_claim_integrity_mongo.py`: without the change, the SLA test fails |
| C-23 | Legacy claims may not fit the Bench (P) | `services/claim_bench.py:243-261` `initial_head` drops float days (`isinstance int`) and string amounts. An off-enum legacy status would fail `ClaimRead` validation. | Depends on production data (reading P-03). |

### 2.3 Lesser defects carried into tickets

- Overlap and event selects cannot be cleared (`DelayTab.tsx`); overlap cards list counts, not the hindrances involved.
- "Start a new revision" shows on superseded determinations; approver and comment state is shared across determination cards.
- Escalation into a `filed` or `archived` case; the escalation dialog's case list is not filtered by status.
- The Bench availability check and the "Open" button are shown to view-only users.

### 2.4 Corrections to the earlier audit

| Earlier statement | Verified position |
|---|---|
| "`/unlock` needs only `CLAIM_EDIT`" was listed as a defect | It is the same permission as lock (`routers/claim_bench.py:447, 462`), a design choice. Decision D-22 below changes both. |
| "Escalation leaves no audit" | A case-level event `claim.escalated_to_arbitration` is written (`claim_escalation.py:455`); per-claim status audit is what is missing. |
| "`approval_policy.py` was changed in Phase 4" | It was refactored in Phase 0 to call `actor_role_names`; the logic is equivalent. No engine flag, rollout mode or gate table changed in Phases 0–4. |
| "Nobody holds the new permissions" (Phase 3 note) | Role-contract alignment grants them to org and project admins (C-19). |
| "Arbitration gates work today" (implicit) | Their reviewer roles are not seeded and the override matrix is unset in compose (B3). Production state unknown until P-05. |

---

## 3. Final decisions (ADR form)

Each decision: context, decision, consequences. "Amended" marks a change from the version approved on 2026-11-16 to meet the owner's Phase 4b requirements.

**D-05 — Approval requirements for system packs are server-controlled and only grow (amended).**

- Context: B1, B1b.
- Decision:
  - For any key in the bundled system catalogue, the server takes `required_approvals` from the bundled manifest (`services/skills/system_skills.py` `_MANIFESTS`) as a floor. An import may add requirements, never remove or weaken them.
  - The manifest's policy fields (`required_approvals`, `kind`, chains) are hashed into a `policy_hash` stored on the version. Every approval binds `content_hash`, `policy_hash` and `eval_run_id`.
  - Re-importing over a draft that has a live approval is refused.
  - Evaluation runs are created server-side (D-21), with `recorded_by` set from the session. The minimum threshold is server configuration, not client input.
  - Publish re-verifies approvals atomically (D-05a).
- Consequences: one more collection (`skill_eval_runs`), and import of a system key needs the bundle's policy. Tenant-owned packs keep today's behaviour unless they declare requirements.

**D-05a — Publish is atomic with respect to approvals (new, from B1b).**

- Decision (amended after owner review B.1: a standalone digest comparison is not enough):
  - **The version document is the single serialisation point.** It carries an integer `approval_revision`. Recording an approval, revoking one, publishing and deprecating each run in one MongoDB multi-document transaction (the pattern already used in `services/key_date_revision_service.py:112` and `services/letter_drafting/repository.py:598`). Each of these transactions writes the version document, incrementing `approval_revision` or changing `status`.
  - Inside the publish transaction the approvals are read, the gate is evaluated, and the version is updated with the filter `{status: draft, content_hash, policy_hash, approval_revision: <value read>}`. A concurrent record or revoke also writes the version document, so MongoDB's write-conflict detection aborts one of the two transactions. Neither can commit on a stale view of the other. The aborted side retries once, then returns 409 "approvals changed, reload".
  - Revoke writes the approval's `revoked_at` and the version's `approval_revision`, and, if the version is published and the revoked approval was relied on, its `status: deprecated`, all in the same transaction. A crash between writes therefore cannot leave a revoked approval on a published version.
  - `approvals_digest`, a hash of the live approval ids, is stored on the version for display and audit only. It is not the consistency mechanism.
  - Deployment precondition: these paths need a replica set. Production and CI both run one (`docker-compose.mongo-replicaset.yml`; CI `rs0` on port 27018). On a standalone server the service refuses to publish rather than falling back to a non-transactional path.
  - Proof: a real-replica-set concurrency suite that interleaves record, revoke and publish (including fault injection that aborts after the first write), and after every run checks the invariant "no published version has a revoked, stale or missing required approval".
  - Independence (distinct users across live approvals; legal ≠ evaluator ≠ publisher; two different people for the two EOT technical approvals) is checked at publish as well as at record.
  - ~~A partial unique index ensures one live approval per (skill, version, kind, reviewer_role).~~ **As built (4B-05):** no index. The duplicate and independence checks run inside the record transaction, which writes the version document. Two records on one version therefore conflict, and the retried one sees the first. This gives the guarantee without a migration, so the Gate 6 count is unchanged. Proven on a replica set by a race between two reviews by one person.
  - Revoke deprecates a published version only if the revoked approval was one the publication relied on.

**D-06 — Reviewer roles are seeded role identities held through scoped, audited assignments (amended).**

- Context: B3, plus the requirement for scoped, explicit, auditable assignments within the existing RBAC model.
- Decision:
  - Seed `chief_contract_expert`, `delay_planning_expert`, `arbitration_case_preparation_expert` and `senior_legal_approver` as role documents with string `_id`, `scope="system"` and no permissions. They are identities, not permission bundles. Seeding is skip-existing; a conflicting document is reported, not overwritten.
  - A user holds a reviewer role only through a `reviewer_role_assignments` record:
    - Fields: `user_id`, `role_key`, `scope_type` (`platform` | `organization` | `project`), `organization_id`, `project_id`, `granted_by`, `reason`, `granted_at`, `expires_at`, `revoked_at`, `revoked_by`, `revoke_reason`.
    - The record follows the `expert_allocations` pattern: create, revoke, audit with before and after (`services/allocation_service.py:36-99`).
  - `core/permissions.py` gains `reviewer_roles_for(user, scope)`, the single resolver. Its rules (amended after owner review B.2):
    - **Platform authority comes only from an active, unexpired, unrevoked `platform`-scope assignment.** Approving a system skill pack calls the resolver with platform scope and accepts nothing else.
    - Organisation and project assignments answer only for a resource in that organisation or project. They never confer platform authority, and an organisation assignment does not reach another organisation.
    - **Legacy role names** in `users.roles` (hand-made role documents, if reading P-05 finds any) are honoured only for arbitration case gates, only within the user's own organisation, and are reported in the reconciliation output of 4B-06. They are never read for platform authority. `actor_role_names` keeps its current behaviour for arbitration gates only, so existing case work is not broken, and it is not called by the skill approval path.
    - Super Admin holds no reviewer role by status. A Super Admin may grant platform scope to someone else, never to themselves (`granted_by ≠ user_id`).
  - Organisation and project scope (for arbitration case approvals) may be granted by that organisation's admin, for that scope only. Least privilege: an assignment carries a role identity and no permissions; expiry is required for organisation and project scope (default 12 months) and optional for platform scope.
  - Reconciliation of legacy roles (4B-06): a read-only report lists every user holding a reviewer role key through `users.roles`. An org admin, or a Super Admin for platform scope, converts each into an explicit assignment. Nothing is converted automatically.
  - `pages/UsersPage.tsx` stops replacing a user's role list on edit.
- Consequences: one new collection, one migration, and one admin screen. Arbitration gates read the same resolver behind no flag, because this enables gates that were unreachable and relaxes none (AQ-3 covers the other arbitration roles).

**D-07 — The evaluator may be a technical reviewer; legal sign-off is independent.**

- Decision: the legal signatory differs from the evaluator and the publisher. The two EOT technical approvals come from two different people. The publisher may be a technical reviewer.

**D-08 — A contract notice period is verified by a second, authorised person against the indexed clause (amended).**

- Decision:
  - On `set`, the server resolves the clause in `contract_clauses` by `clause_uid`, then by normalised `clause_no` (`services/letter_drafting/clause_checker.py:40, 98-126`). The normalised quote must be a substring of the clause text, and the period must appear in it.
  - `origin=clause_index` is set by the server only when the clause resolves; otherwise the record is `entered_from_contract` and shown as such.
  - `claim_type` is validated against `ClaimType`.
  - `verify` needs `dms.claim.manage` and `dms.contract.clause.read`, and the verifier cannot be the recorder.
  - Both actions emit audit events.

**D-09 — Rollout is off, then canary, then primary; legacy retirement waits for evidence (amended).**

- Decision:
  - Modes are `off | canary | primary`. Any other value fails the startup configuration gate.
  - The legacy claim record page and the old AI assessment are retired only when all of these hold: reading P-11 shows their use; the Bench has run primary for 30 days without rollback; and the owner explicitly approves retirement in writing (AQ-8). Until then both remain, and the record page stays reachable from the Bench.
- Consequence: two claim views coexist during canary. The Bench header links to the record view.

**D-10 — No blanket backfill; read first, then create boards lazily with safe coercion (amended).**

- Decision:
  - No migration writes heads into existing claims in Phase 4b.
  - 4B-01 reads the legacy claim inventory (P-03).
  - `initial_head` coerces legacy figures: integral floats to int days; numeric strings to amounts only when unambiguous, otherwise left empty and flagged "figure needs re-entry".
  - A claim whose status is outside the enum opens read-only with an explanation.
  - A backfill is reconsidered only if P-03 shows active legacy claims.
- New claims get their first head at creation (D-24).

**D-11 — Perspective has one write path, is audited, and locks once used (amended).**

- Decision:
  - `contractual_capacity` is written only through the dedicated route; `PUT /projects/{id}` ignores it.
  - Every change emits an audit event with before, after and reason.
  - Once a determination, simulation or submission exists on the project, only an org admin may change it, with a reason. Existing determinations keep the capacity recorded on them, and determination writes re-check the capacity at the moment of writing.
  - An organisation default exists; the Bench asks only users who can set it.

**D-12 — Super Admin is excluded from privileged legal material; explicit-grant means explicit.**

- Decision:
  - `PolicyService` checks a `SUPERADMIN_EXCLUDED_PERMISSIONS` set before the Super Admin shortcut: `dms.claim.strategy.read`, `dms.claim.determine`, and the determination approve action.
  - A Super Admin who needs access is granted it like anyone else, by explicit role and audit.
  - `dms.claim.determine` and `dms.claim.strategy.read` are removed from the `orgadmin` and `projectadmin` defaults (C-19). Neither is in production yet, so no existing grant is withdrawn.

**D-13 — Determination approval is four-eyes in substance.**

- Decision:
  - Only the named reviewer may approve or send back.
  - The reviewer must exist, hold `dms.claim.determine` on the project, and be a project member.
  - A new revision clears every finding's `result`, so each must be decided again.
  - Capacity is re-checked on every write.
  - Only the latest revision can be revised.
  - No legal role is required for a determination.

**D-14 — One authoritative amount; formulas computed in code; money in integer paise.**

- Decision:
  - Once a board exists, `amount_claimed` and EOT days on the claim are derived and read-only. `PUT /claims` refuses them, and refuses any change to a frozen claim.
  - A head amount comes from its quantum lines when any exist; typed entry is refused.
  - Clearing every amount clears the roll-up.
  - Hudson, Emden and Eichleay are computed from typed inputs, each input carrying its record.
  - Money is stored and summed as integer paise, and displayed with Indian grouping.

**D-15 — Already-granted EOT follows the register's own rule.**

- Decision:
  - Extract the operative-determination rule from `KeyDateRevisionService._replay_determinations` into a pure function shared by both modules.
  - A linked determination counts only if it governs its milestone.
  - The snapshot carries `frozen_at`, `supersedes_determination_ids` and a superseded-by flag, so any change marks the link stale.

**D-16 — Neutral, Employer and Contractor concurrency are all decided facts; nothing is deducted automatically (amended).**

- Decision:
  - Overlaps are computed for Employer × Contractor and Neutral × Contractor.
  - A finding is identified by its dates and its event sets; a change to either orphans it.
  - Adjacent events of one owner produce separate overlaps unless a person merges them.
  - Neutral days count only where a person records the contract's entitlement, and never vanish because an Employer event covers them.
  - `not_true_concurrency` counts for time. It never creates a monetary entitlement automatically (AQ-4). Cost for any overlap, whether `not_true_concurrency` or `eot_granted_with_cost`, counts only when all of these are recorded on the finding:
    - the contractual basis;
    - the causation reasoning;
    - linked cost evidence (documents linked to the claim);
    - an authorised review: a person holding a `delay_planning_expert` assignment for the project, or the claim's determination authority, who is not the person who recorded the finding.
  - Until then, the overlap's days are time-only in the quantum lock rules.

**D-17 — Citations: one parser; verified is an allow-list; exhibits are first-class.**

- Decision:
  - One regex module serves the validator and the workflow.
  - Exhibit references (`C-12`, `R/3`, `CW-1/4`, `Annexure A-7`) are detected and resolved against case exhibits.
  - "Verified" means an approved row with a source in the ledger, from an explicit allow-list of statuses. Missing status defaults to unverified, not `approved`.
  - Exhibit numbers are unique per case.

**D-18 — Escalation is idempotent, guarded and reversible until the first pleading version (amended).**

- Decision:
  - A partial unique index covers (case, matrix, `origin_key`); a duplicate-key error resolves to the existing row.
  - The claim update is conditional on `{arbitration_case_id: null, status}`. A new-case run carries an idempotency key.
  - The status moves through the guarded transition with a per-claim audit.
  - Filed or archived cases are refused. The preview is computed for the chosen target.
  - The freeze goes through `DocumentRelationshipService.freeze`.
  - Un-escalation is allowed until the first pleading version exists in the arbitration case (AQ-6). A version counts if its draft carries this case's id. Case-less scratch drafts never count, even if they cite the same claims:
    - It soft-archives the rows the escalation seeded, with a reason.
    - It restores the claim's prior status (recorded at escalation) and lifts the freeze.
    - It needs `dms.arbitration.edit` on the case and `dms.claim.manage` on the claim, and is audited.
    - Everything escalation changed is recorded at escalation (prior status, freeze state, seeded row ids, case created or extended), so un-escalation reverses exactly that. The audit trail of both actions is kept.
  - An unlocked board may be escalated only after the user acknowledges each weak head.

**D-19 — One Word writer path, safe in both modules (amended).**

- Decision:
  - A shared XML sanitiser for both minimal writers ships first.
  - Adding `python-docx` to `rbac_backend/requirements.txt` is approved conditionally (AQ-7). Conditions:
    - the sanitiser has landed;
    - `pip-audit` is clean;
    - a regression comparison exists of current and new exports, by structure and text, for the existing arbitration export fixtures.
  - To prevent unreviewed changes to production output, the writer is chosen by `DOCX_WRITER` (`minimal` | `python_docx`, default `minimal`). It moves to `python_docx` only after the owner reviews the regression comparison.

**D-20 — Phase 4b precedes Phase 5.** This document is the Phase 4b plan.

**D-21 — Packs stay unpublished; evaluation is server-side against golden sets.** Requirements are in section 7. Datasets live in a restricted, encrypted evaluation store outside the repository; only hashes and synthetic fixtures are in GitHub. Every run has a configured model budget, and exceeding it needs explicit approval (AQ-10).

**D-22 — Lock and unlock (new).**

- Decision:
  - Lock needs `dms.claim.edit`.
  - Unlock needs `dms.claim.manage` and a reason, and writes an append-only audit event (AQ-9). The reason is stored on the event; no route edits or deletes it.
  - Stale tests block the lock.
  - A failed stale-marking is retried, and otherwise returns 409.

**D-23 — Claims carry a contract identity (new; resolves C-05; AQ-1 decided).**

- Decision:
  - Every new claim is associated with a contract where one is identifiable. A valid contract is one in the claim's project that the user can read and whose instrument is not withdrawn.
  - If exactly one valid contract exists, it is selected automatically and shown. If several exist, the user must choose. If none exists, the claim is created without one and shows "No contract identified".
  - Legacy claims may keep `contract_id: null` until someone resolves it. A claim with no contract can link only records that themselves have no contract.
  - No cross-contract linking: key-date submissions and determinations, hindrances, documents and letters that carry a different `contract_id` are refused (422) for links, quantum records, submission evidence and escalation.

**D-24 — Three-question claim start (new; parent spec Move 1).**

- Decision:
  - A new claim starts from three questions: what happened, when you became aware, and which contract.
  - That creates the claim (status `draft`) and its first head. Everything else is captured on the board.
  - The full form remains as "More details".
  - `POST /claims` no longer accepts `status` beyond `draft` or `notified`, nor `origin`; potential claims are created only by the signal path.

**D-25 — Restricted evidence blocks export without leaking it (new; AQ-5 decided).**

- Decision:
  - Composing and staleness use the claim's full linked evidence set, not the viewer's, so every viewer sees the same state.
  - A viewer without access to an item sees "Restricted item" in its place: no title, date, type, author or count by type. Only the total number of restricted items is shown.
  - Export is refused while any required item is inaccessible to the exporter. The message says how many items are restricted and who can export (the claim owner, or a user with access to all evidence). It names no item.
  - The refusal is a structured error (`code: evidence_restricted`) and is audited.

**D-26 — Existing callers of `POST /arbitration/drafts` keep working while the scratch requirement is introduced (new; owner review B.3).**

- Decision:
  - Reading P-12 lists callers in the last 90 days that create drafts without a case (API access logs, grouped by client and user agent; counts only).
  - `ARBITRATION_CASELESS_DRAFT_MODE` is `implicit_scratch` (default) or `strict`.
    - In `implicit_scratch`, a case-less request without `scratch` is created as a scratch draft, which can be neither approved nor exported for filing, so no gate is bypassed. The response carries a `Deprecation` header and a `warnings[]` entry, and the call is logged with its caller.
    - `strict` returns 422 with `code: scratch_required` and the instruction to send `scratch: true` or a `case_workspace_id`.
  - The switch to `strict` needs the owner's approval, after P-12 and the deprecation logs show no remaining legacy callers. The UI always sends the explicit flag.
  - **4B-45 (2026-11-16):** built as decided. Phase 4 had already made the route behave as `strict` (422 without `scratch`); the default is now `implicit_scratch`. The setting is checked by the startup configuration gate (any other value refuses to start). In `implicit_scratch` the response carries `Deprecation: true`, a `Warning` header and a `warnings[]` entry, and the call is logged (`arbitration.caseless_draft.implicit_scratch`, with draft, user, organisation, project and user agent) for reading P-12. `strict` answers 422 `scratch_required` and writes nothing. The client payload type makes `scratch` required, so every UI call sends it (the case workspace sends `false`). The instruction names `case_id`, the field the API takes. Proof: `test_caseless_draft_compat_mongo.py` (2, CI real-Mongo step: an implicit scratch draft cannot be approved or filing-exported; strict refuses), `test_claim_bench_rollout_guard.py` (+8 config cases), `test_claim_escalation.py` (strict). Mode stays `implicit_scratch` until the owner approves the switch.

**D-27 — Backend enforcement of the privilege wall and provenance, independent of the UI (new; owner review B.6).**

- Decision:
  - Every route that returns or exports claim, determination, submission, escalation or pleading content enforces, in the service layer:
    - the privilege wall: `claim_privileged` is read only by `services/claim_privileged.py`, behind `dms.claim.strategy.read`, never for Super Admin by status, and never in exports, the source ledger, escalation or step stores;
    - provenance: every figure, citation and evidence item in an output names a source the caller may access; anything else is refused or replaced per D-25.
  - Hidden buttons are a convenience, never a control. Tests call the API directly with a token whose UI would hide the action.

**D-28 — Specialist defence skill `india-metro-arbitration-defence` (new; owner review B.4).**

- Decision:
  - The supplied bundle `india-metro-arbitration-defence_R2.zip` is evaluated as a separate specialist skill. It is installed, if at all, as an unpublished draft under its own key, with its own approvals (Arbitration Case Preparation Expert technical review; platform `senior_legal_approver` legal sign-off) and its own golden set.
  - It is not on the Phase 4b critical path, and nothing in Phase 4b depends on it.
  - Its integration dependencies are recorded, not built, in Phase 4b. They include:
    - the defence and counterclaim section set, and opponent pleading intake (both Phase 4b non-goals);
    - the pleading-side chains;
    - output schemas;
    - the Traverse Board.
  - **Input status: supplied by the owner on 2026-11-16 and inspected without execution.** Facts recorded:
    - **Provenance.** `india-metro-arbitration-defence_R2.zip`, 60,487 bytes, SHA-256 `47c08cffba9be8d470d8b4f23943414d388e969f5b937b510eb62ef1c639af8e`.
    - **Contents.**
      - 25 entries, 117,288 bytes unpacked, with no absolute paths, parent references or links.
      - The same skill appears twice, under `india-metro-arbitration-defence/` and `india-metro-arbitration-defence_R2/india-metro-arbitration-defence/`. The two copies are byte-identical.
      - Each copy holds `SKILL.md` (SHA-256 `fcb2c0ef…e48e`), seven reference files (`01-intake` to `07-qc-checklist`) and `scripts/claim_matrix.py` (SHA-256 `33b7b62f…e44a`).
    - **The registry would refuse it as supplied** (`services/skills/loader.py`). It contains two `SKILL.md` files, and `.py` is not an allowed suffix (`ALLOWED_SUFFIXES = {".md", ".txt"}`). The refusal is correct and must not be relaxed.
    - **The script.**
      - `claim_matrix.py` uses the standard library only. It builds a claim–defence–evidence matrix and a paragraph-by-paragraph traverse skeleton from a CSV, and flags unanswered SoC paragraphs.
      - It was read, not run. Bundled code is never executed by the product.
      - If its function is wanted, it is re-implemented as reviewed, deterministic service code ("the code does the arithmetic"), with its own tests.
    - **Integration dependencies found in the text:**
      - The skill asks for web search to confirm current law. The skill engine has no web access, so those points must surface as `[LAW TBC]` placeholders and be validated as such.
      - It is respondent-oriented, for either an Employer or a contractor resisting a counterclaim, so it needs the D-01 perspective mapping.
      - It needs a Statement of Defence and counterclaim section set, opponent SoC intake split into paragraphs, and the Traverse Board. All three are Phase 4b non-goals.
      - Its placeholder vocabulary (`[CLAUSE TBC]`, `[AUTHORITY TBC]`) must be recognised by the citation validator as unresolved, never as verified.
      - It cites authorities by name, and these need legal verification before any publication.
    - **Next step (4B-48):** repackage as a single root with `.md` references only, with the script described in a reference file. Install as an unpublished draft under its own key, and record the dependency list above as tickets for the phase that builds defence pleadings.

---

## 4. Proposed changes

### 4.1 Database

| Collection or field | Change | Migration |
|---|---|---|
| `skill_versions` | Add `policy_hash` and `approvals_digest` | M-01, computes both for existing drafts |
| `skill_eval_runs` (new) | `skill_key`, `version`, `content_hash`, `policy_hash`, `suite_id`, `suite_hash`, `case_results[]`, `score`, `threshold`, `recorded_by`, `started_at`, `finished_at`, `status` | M-01, indexes |
| `skill_approvals` | Add `policy_hash` and `eval_run_id`; partial unique index on live approvals | M-01; dry-run reports duplicates as warnings |
| `roles` | Seed four reviewer identities (string `_id`, `scope="system"`, no permissions) | M-02, skip-existing |
| `reviewer_role_assignments` (new) | As D-06 | M-03, indexes |
| `claims` | `contract_id` (optional), `escalation.prior_status`, `escalation.idempotency_key` | M-04, indexes only |
| `notice_requirements` | `origin` gains `entered_from_contract`; `verified_by ≠ created_by` enforced in code | none |
| Matrix collections | Partial unique index on (`case_id`, `origin_key`) where `origin_key` exists; unique (`case_id`, `exhibit_no`) | M-05 = `20261120_0001_arbitration_matrix_origin_unique` (4B-36); dry-run lists duplicates as warnings and does not create the index until they are resolved. The exhibit index already existed in `ensure_indexes` (4B-35) |
| `claim_head_links` | Snapshot fields `frozen_at`, `supersedes_determination_ids`, `superseded_by` | none; old links re-snapshot on read and show "linked record changed" |
| `quantum_lines` (embedded) | `amount_paise` (int); `formula_inputs`; `amount` kept as a derived read field | none; computed on read, written on next save |
| `claim_submissions` | `evidence_snapshot[]` (document id, title, date, revision hash) | none |
| `arbitration_drafts` | No schema change | — |

Each new migration needs a recorded fresh-install run (Gate 6) and an update to `tests/test_gate6_fresh_install_evidence.py` (`MIGRATIONS_PROVEN_ON_A_FRESH_DATABASE`, currently 27). Each is run first with `--fail-on-warning`, and documentary text goes in `notices=`.

### 4.2 Backend and API

| Area | Change | Finding |
|---|---|---|
| `routers/skills.py`, `services/skills/registry.py`, `approvals.py` | Policy floor; `policy_hash`; refuse import over approved drafts; atomic publish; independence at publish; scoped deprecation on revoke; audit events | B1, B1b |
| `services/skills/evaluation.py` (new), `routers/skills.py` | `POST /skills/{key}/versions/{v}/eval-runs` (runs the golden suite server-side); `GET …/eval-runs`; approvals and publish take `eval_run_id` | B1, D-21 |
| `routers/skills.py` | `GET /skills/review-queue` (`drafting.skill.read` plus a named reviewer); version read for reviewers | B3 |
| `core/permissions.py`, `services/reviewer_assignments.py` (new), `routers/reviewer_assignments.py` (new) | `reviewer_roles_for`; create, revoke and list assignments; audit | B3, D-06 |
| `services/policy_service.py` | `SUPERADMIN_EXCLUDED_PERMISSIONS` checked before the shortcut | C-15 |
| `initial_data/default_roles.py` | Remove explicit-grant claim permissions from admin defaults | C-19 |
| `routers/claims.py`, `services/claim_service.py`, `models/claim.py` | Create: no `origin`, status `draft`/`notified` only. Compare-and-set on status. Frozen refuses writes. Explicit null unsets clearable dates. Derived amount and days refused when a board exists. | C-07, C-13 |
| `services/sla_service.py` | Coerce or validate window values; never raise on a string | C-13 |
| `services/claim_bench.py`, `routers/claim_bench.py` | Stale tests block the lock; compare-and-set retry; unlock with manage and reason; `ensure_enabled` on signal routes; `initial_head` coercion; three-question create endpoint `POST /claims/start` | C-17, C-12, C-23, D-24 |
| `services/claim_signals.py` | Exclude claim-triggered boards; re-check eligibility on raise; resolve the organisation | C-12 |
| `services/claim_notice_requirements.py`, `routers/claim_bench.py` | Server clause resolution, quote match, enum type, clause-read permission, second-person verify, audit | C-08 |
| `routers/projects.py` | Single write path, audit, lock rule, organisation default | C-14 |
| `services/key_date_revision_service.py`, `services/claim_delay_lab.py` | Shared operative-determination rule; snapshot fields; contract check | C-01, C-05 |
| `services/claim_delay_engine.py`, `models/claim_delay.py` | Neutral overlaps; finding identity by event sets; split adjacent runs; separate end-before-start; cost basis for `not_true_concurrency`; net-EOT table read model | C-02, C-03, C-04, C-06 |
| `services/claim_quantum.py` | Paise; formula computation; lock re-check against the unfiltered claim document set | C-06, D-14 |
| `services/claim_determinations.py`, `routers/claim_findings.py` | Reviewer-only decision; reviewer validation; carried findings cleared; capacity re-check; latest-only revise | C-09 |
| `services/claim_submission.py` | Compose from the unfiltered linked set with an evidence snapshot; viewer-side redaction; per-head figure validation with unit normalisation; reservation by required wording; wider clause regex; XML sanitiser and `tblGrid` | C-16 |
| `services/letter_drafting/bench.py` | Determination and RFI starts carry findings, information items and deadlines into the board as context | C-22 |
| `services/arbitration_drafting/context.py`, `citation_states.py`, `validator.py`, `workflow_validation.py` | Copy head fields into metadata (with a fallback to row fields); block a Statement of Claim with zero approved heads or with unapproved heads; explicit blocker mapping for legal review; one citation parser; exhibits; allow-list | B2, C-11 |
| `services/claim_escalation.py`, `routers/claim_escalation.py` | Unique index use; conditional claim update; idempotency key; guarded status and audit; target-aware preview; refuse filed or archived; document freeze; carry notice-role documents into `notice_ids`; `POST /claims/escalation/undo` | C-10, B2, D-18 |
| `services/arbitration_drafting/service.py`, `routers/arbitration_drafting.py` | Validate the case on attach | C-20 |
| `services/arbitration_drafting/exporter.py` | Shared sanitiser; `python-docx` behind AQ-7 | C-16 |
| All new or changed routes | `PolicyService.authorize`; `@handle_exceptions`; re-raise `(BaseDomainError, HTTPException)`; limiters with `scope=`; a structured error body `{code, user_message, detail}` for every refusal the UI explains | all |

Route inventories are regenerated after each ticket that adds or changes a route (`scripts/rbac_phase0_route_inventory.py`, `scripts/gate3_route_inventory.py`).

### 4.3 Frontend — the journey

One claim, one header, eight steps. The header shows each step's state (not started, in progress, blocked, done), who must act, and the next action.

```text
Start → Heads → Notice → Delay → Quantum → Determination | Submission → Escalation → Pleading
```

| Step | Screen change | Finding |
|---|---|---|
| Start | Three-question dialog (what happened, when aware, which contract); "More details" opens the full form | D-24 |
| Heads | Board as today. Lock and unlock with reason. Stale tests shown as a blocker with "Re-test". "Open" shown only to editors. | C-17, C-14 |
| Notice | Clock card. Period dialog resolves the clause and shows the quote beside the clause text. Verification shows who recorded and who may verify. Notice pill is the register's first column; the strip uses server counts. | C-08, C-22 |
| Delay | Labelled timeline bars with keyboard focus; milestone lane; overlap cards name their events; neutral overlaps; clearable selects; **net-EOT table** (Event, Owner, Critical days, Concurrent with, Treatment, Net EOT, Basis) | C-02, C-03, C-22 |
| Quantum | Formula lines with inputs and records; totals in ₹ lakh/crore; a lock shows the record that would unlock it | C-06, D-14 |
| Determination | Approver picker scoped to authority holders on the project. "Issue determination letter" and "Request information" open the Letter Bench pre-filled with findings, items and deadlines. Deadlines editable. | C-09, C-21, C-22 |
| Submission | Reading view with paragraph provenance (document, date, revision); restricted documents shown as "Restricted document" to viewers without access; section editing with an "edited" mark that recomposition does not overwrite silently | C-16, C-22 |
| Escalation | Multi-select from the register; preview against the chosen case; weak-head acknowledgement; "Undo escalation" while allowed | C-10, D-18 |
| Pleading | Approval-gate stepper for the eight gates, derived from workflow state; Approve and Export disabled with a plain reason and a link to each blocker; exhibit chips with number, title and state; scratch-draft banner; pickers instead of id fields | B4, C-11, C-22 |

Cross-cutting:

- **Shared pickers.** `components/pickers/UserPicker.tsx` (project- and permission-scoped), `DocumentPicker.tsx` (wrapping `components/documents/LinkedDocumentsPicker.tsx`) and `LetterPicker.tsx`.
- **Plain language.** One map, `lib/plain-language.ts`, for statuses, engine terms and error codes. Examples: "Prepare the case file", "Review the matrices", "Approve for drafting".
- **Errors.** API refusals render `user_message`. No database id, ObjectId, internal key or stack text is shown. A mocked Playwright assertion fails if visible text matches a 24-hex id or an `S<n>` ledger key outside a chip's secondary label.
- **Data hooks.** Queries are keyed by organisation and project.
- **Accessibility.** Risk owner by label and position, not colour alone. Every action is labelled. Contrast meets WCAG AA.

---

## 5. Implementation tickets (dependency order)

Each ticket is independently testable and lands in its own commit. "Proof" names the test that must pass; "Gate" names extra evidence. Size: S ≤ 1 day, M 2–3 days, L 4–5 days.

### Wave 0 — measure and guard

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-01 | Read-only claim, arbitration, role and skill inventory: section F of `scripts/post_cutover_readonly_checks.sh` covering readings **P-01 to P-14** (P-13 added for C-25 by owner decision 4; P-14 for C-08 with 4B-14). Each result is recorded in the post-cutover assessment §6 with its evidence class (section 8.1). A reading not taken is recorded as not taken, never as zero. | — | Script is read-only by construction (reviewed command list; tests assert no write verb); readings recorded with their class; production readings need authorised access (AQ-11) | M |
| 4B-02 | Route tests that resolve real dependencies for `routers/claim_bench.py`, `claim_findings.py`, `claim_escalation.py` (none exist today) | — | New `tests/test_claim_routes_real_deps.py` passes with no factory overrides | M |
| 4B-03 | Rollout guard: validate `CLAIM_BENCH_ROLLOUT_MODE` in the config gate; `ensure_enabled` on signal routes; frontend query keys include organisation and project | — | Config test (`shadow` raises); route test (signal routes 404 when off); hook test (refetch on organisation change) | S |

### Wave 1 — publication security (B1, B1b)

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-04 | Policy floor and `policy_hash`; refuse weaker system manifests; refuse re-import over approved drafts; migration M-01 (fields, indexes) | 4B-02 | Re-import of a system key with `[]` → 422; new version without the floor → 422; re-import over an approved draft → 409; manifest change invalidates approvals | M |
| 4B-05 | Transactional record, revoke, publish and deprecate serialised on the version document (D-05a); independence at publish; partial unique live-approval index; deprecate on revoke only if relied on; skills audit events | 4B-04 | Real-replica-set suite: interleaved record, revoke and publish with fault injection, invariant checked after each run; one user cannot satisfy both EOT technical approvals; router-level revoke → deprecate test | L |

### Wave 2 — reviewer roles and privilege (B3, C-15, C-19)

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-06 | Seed reviewer identities (M-02, skip-existing, conflicts reported as warnings) | 4B-01 (P-05 read) | Migration dry-run on a copy of P-05 data; seeded documents have no permissions | S |
| 4B-07 | `reviewer_role_assignments`: model, service, routes, resolver (D-06 rules), audit, M-03; Super Admin-only platform grants; no self-grant; org admin limited to own scope; expiry | 4B-06 | RBAC matrix test (section 6.4); resolver tests: a legacy role name, an organisation assignment or a custom role with the same name never yields platform authority; expiry and revoke take effect on the next request | L |
| 4B-08 | Users screen preserves the role list; reviewer assignment admin screen | 4B-07 | RTL: editing a user keeps extra roles; mocked E2E: Super Admin grants platform scope; org admin cannot | M |
| 4B-09 | Reviewer queue endpoint and read-only reviewer view on `/skills` | 4B-07 | Pure reviewer opens `/skills`, sees the queue and records an approval; publisher-only actions absent | M |
| 4B-10 | Super Admin exclusion; remove explicit-grant claim permissions from admin defaults; align `docs/CLAIM_BENCH.md` | 4B-02 | Super Admin → 403 on strategy read, determine and approve; role-contract alignment inspect shows no class-A addition of these permissions | S |

### Wave 3 — shared UI foundations

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-11 | Shared `UserPicker`, `DocumentPicker`, `LetterPicker`; `lib/plain-language.ts`; structured error rendering | — | Component tests; picker scoped by project and permission (API contract test) | M |

### Wave 4 — claim core integrity

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-12 | Claim create and status hardening: no client `origin`; status limited; compare-and-set transitions; frozen claims refuse PUT; explicit-null unset; `sla_rules` coercion | 4B-02 | Concurrency: two transitions from `submitted` → exactly one 200 and one 409; create at `agreed` → 422; clearing a date persists null; `/sla` with "28" → 200 | M |
| 4B-13 | Signals: exclude claim-triggered boards; re-check eligibility; organisation resolution | 4B-12 | Integration: notice letter from a claim yields no signal and raise → 422; non-critical hindrance → 422; double accept of an org-less board returns one claim | S |
| 4B-14 | Notice verification (D-08) with audit | 4B-12 | Quote not in clause → 422; unknown clause → stored as `entered_from_contract`; self-verify → 403; no clause-read → 403 on set and verify; audit rows written | M |
| 4B-15 | Perspective (D-11): single path, audit, lock, organisation default, UI gating | 4B-10 | Change after a determination → 409 for a non-org-admin; PUT cannot set it; audit row; view-only user sees no banner actions | M |
| 4B-16 | Lock and stale (D-22); compare-and-set retry for stale marking | 4B-12 | Lock with a stale head → 409; concurrent board write during a fact change still stales and unlocks; unlock without manage → 403 | S |
| 4B-17 | Single amount source (D-14, register side): derived and read-only once a board exists; roll-up clears; typed head amount refused with lines | 4B-12 | Five tests listed for C-07 in section 6.1 | M |
| 4B-18 | Claim contract identity (D-23, M-04) | 4B-01 (P-03), AQ-1 | Create asks for the contract when the project has several; legacy claims unaffected | S |
| 4B-19 | Lazy board coercion for legacy claims (D-10) | 4B-01 (P-03) | `initial_head` with string amount, float days, no figures, off-enum status | S |
| 4B-20 | Three-question start (D-24): `POST /claims/start` creates the claim and first head | 4B-11, 4B-12, 4B-18 | Integration and RTL; mocked E2E step 1 | M |

### Wave 5 — delay and quantum

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-21 | Already-granted from the register rule (D-15), snapshot fields, staleness | 4B-02 | Supersession, draft, under-review and freeze cases in section 6.1; same function used by the register test suite | M |
| 4B-22 | Concurrency engine (D-16): neutral overlaps, identity by events, split runs, end-before-start, cost basis | — | Property tests (section 6.1): adding an Employer event never lowers the extension; no automatic deduction under any treatment set | L |
| 4B-23 | Contract scope on links | 4B-18 | Cross-contract link → 422 | S |
| 4B-24 | Quantum in paise; formula computation; lock re-check against the unfiltered document set | 4B-17 | Formula fixtures checked by hand against published worked examples; unlinking a record locks the line; totals exact at ₹10,000 crore | M |
| 4B-25 | Net-EOT table read model and UI; timeline labels, milestone lane, clearable selects | 4B-21, 4B-22 | Component tests; table totals equal the engine's settled figure | M |

### Wave 6 — determinations and submission

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-26 | Determination four-eyes (D-13); latest-only revise; received-form default | 4B-07, 4B-10, 4B-11, 4B-15 | Non-reviewer approve → 403; unauthorised reviewer → 422; carried revision cannot be submitted undecided; capacity flip then approve → 409; revise superseded → 409 | M |
| 4B-27 | Submission evidence snapshot and viewer redaction; stable hashes | 4B-24 | Two users with different document access see the same staleness state; the narrower one sees "Restricted document" and cannot export (AQ-5) | M |
| 4B-28 | Submission validators: per-head figures, unit and word normalisation, reservation wording, clause regex | — | Bypass corpus (section 6.6) all rejected; true-positive corpus all accepted | M |
| 4B-29 | Word output: shared sanitiser and `tblGrid` for both writers | — | Fuzz test: every control character yields a package whose parts all parse as well-formed XML, with `[Content_Types].xml`, relationships and `w:tblGrid` present (no new test dependency; `python-docx` is not in CI until 4B-30) | S |
| 4B-30 | `python-docx` in `rbac_backend/requirements.txt` | AQ-7, 4B-29 | `pip-audit` clean; arbitration exporter regression tests updated and reviewed | S |
| 4B-31 | Determination and RFI letters pre-filled; information deadlines in the UI | 4B-26 | Integration: the Letter Bench board carries findings and items; mocked E2E step 6 | M |
| 4B-32 | Submission section editing with an "edited" mark and provenance view | 4B-27 | Recompose does not overwrite an edited section without confirmation | M |

### Wave 7 — escalation and pleadings (B2, B4)

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-33 | B2 ledger metadata; zero-head and unapproved-head blockers; explicit legal-review mapping | 4B-02 | End to end through `create_matrix_row` → approve → ledger → validator for escalated and manual rows; zero claim rows → blocked; legal review blocks head gaps | M |
| 4B-34 | Claim-matrix row pickers for clause, notice and evidence; escalation carries notice-role documents into `notice_ids` | 4B-11, 4B-33 | Escalated claim with a linked notice reaches a Statement of Claim with no head gaps | M |
| 4B-35 | Citations (D-17): one parser, exhibit detection, allow-list, status defaults, unique exhibit numbers (M-05 part) | 4B-33 | Corpus tests for formats; unknown status → unverified; concurrent exhibit creation yields distinct numbers | M |
| 4B-36 | Escalation safety (D-18 except undo): unique index (M-05), conditional update, idempotency key, guarded status, audit, target preview, filed or archived refused, document freeze | 4B-12 | `asyncio.gather` of five runs: one case, no duplicate rows; filed case → 409; re-run is a no-op | M |
| 4B-37 | Un-escalation | 4B-36 | Undo before any pleading version restores status and freeze and archives rows; after a version → 409; audit rows | M |
| 4B-38 | Validate the case on attaching a draft | 4B-02 | Nonexistent or foreign case → 404 or 409; scratch mark kept | S |
| 4B-39 | B4: Approve and Export disabled from `can_be_approved` with structured reasons; exhibit chips; backend parity test | 4B-33, 4B-35 | For every disabled state in the UI, the matching API call is refused (parity test); RTL tests | M |
| 4B-40 | Approval-gate stepper; pickers for raw-id inputs; plain vocabulary on arbitration pages; multi-select escalation from the register | 4B-11, 4B-36, 4B-39 | Mocked E2E: no raw id visible; the stepper names the current gate and who acts | L |

### Wave 8 — skill evaluation and proof

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-41 | Server-side evaluation runner and `skill_eval_runs`; approvals and publish by `eval_run_id`; threshold from configuration | 4B-05 | Run record carries the session user; changing `recorded_by` is impossible; threshold 0 refused | L |
| 4B-42 | Golden sets for the claims and EOT packs (section 7); pleadings set specified, built in Phase 5 preparation | 4B-41 | Suites load, hash and run; baseline report recorded; no publication | L |
| 4B-43 | Playwright mocked suites `claim-journey.spec.ts`, `arbitration-pleading-gates.spec.ts`, `skills-review.spec.ts`, added to `MOCKED_SUITES` (`projectDMS/client/playwright.config.ts:25`) | Waves 3–7 | Suites green in CI | M |
| 4B-44 | Staging suite against the deployed stack (real backend, MongoDB replica set, canary internal tenant); runbook `docs/CLAIM_BENCH_STAGING_CHECKS.md`; Gate 6 fresh-install run for M-01 to M-05; route inventories regenerated | all | Two consecutive green staging runs; Gate 6 record; inventories diffed and committed | M |
| 4B-45 | Backward compatibility for `POST /arbitration/drafts` (D-26): `ARBITRATION_CASELESS_DRAFT_MODE`, deprecation header and warning, caller logging, structured 422 in strict mode | 4B-02 | Route tests in both modes; an implicit scratch draft can be neither approved nor filing-exported; the UI always sends the flag | S |
| 4B-46 | Backend privilege-wall and provenance enforcement suite (D-27): direct API calls for every content and export route with tokens whose UI would hide the action; source-tree scan extended to exporters, ledger, escalation and step stores | 4B-10, 4B-27 | Every call refused or redacted per D-25 and D-27; scan green | M |
| 4B-47 | Real-MongoDB integration suites for the claim, skill and escalation modules: environment variables `CLAIM_BENCH_MONGODB_URI`, `SKILL_PUBLICATION_MONGODB_URI` and `CLAIM_ESCALATION_MONGODB_URI`, wired into the CI real-Mongo step and the coverage rule (`tests/test_ci_real_mongo_coverage.py`) | 4B-02 | Suites run, not skip, in CI against `rs0` | M |
| 4B-48 | Evaluate `india-metro-arbitration-defence_R2.zip` (D-28): provenance and integrity record; untrusted-bundle handling (size limits, guardrail scan); install as an unpublished draft under its own key with its own required approvals; integration-dependency record; golden-set specification. Bundle supplied and inspected 2026-11-16 (D-28); as supplied it is refused by the loader and must be repackaged. Not on the critical path. | 4B-04 | Draft present, unresolvable for drafting, approvals required; dependency record committed | M |

### Added by owner decisions, 2026-11-16

| ID | Ticket | Depends | Proof | Size |
|---|---|---|---|---|
| 4B-49 | **High priority (AQ-12).** RBAC audit of `dms.document.review_extraction`: every path by which it is effectively held (named grant, `dms.admin` widening, `*` wildcard, Super Admin and Super User shortcuts, role aliases, organisation and project role assignments, service accounts and workers that call the extraction-review routes). Deliverables: an inheritance matrix produced by code from the seed catalogue and `PolicyService`, a read-only reading of which users hold it only by inheritance (counts by role, no names), and a remediation proposal applying explicit-grant-only. No access is withdrawn until the affected users and services are identified and the owner approves a migration path (for example, grant by name first, then close the inheritance) | 4B-10 | Matrix test pinned to the current behaviour; proposal reviewed; production reading recorded (AQ-11) | M |

**As built, 4B-49 (2026-11-16).** Audit only; no access withdrawn. Proposal: `docs/RBAC_REVIEW_EXTRACTION_REMEDIATION.md`.

- Inheritance matrix by code. `review_extraction_grant_paths` (in the readings script) classifies how a user's roles hold `dms.document.review_extraction`. The paths are: named; named by catalogue id; `dms.admin` widening; `*`; the Super Admin shortcut. `tests/test_review_extraction_inheritance_matrix.py` pins the seed-catalogue matrix to today's behaviour. `tests/integration/test_review_extraction_inheritance_mongo.py` checks it against the real `PolicyService.has_permission` and `has_explicit_grant` for 19 personas, and against the live review route's `can_review`. The personas cover every seed role, legacy and system aliases, Super User, custom `*`, `dms.admin` and named roles, a deactivated role, user-level permissions, a service account, and the catalogue-id spelling.
- Seed result: Super Admin and Contract Manager - Organization hold it only by inheritance; Organization Admin and Project Admin hold it by name. Super User, memberships, user-level permissions, aliases and service accounts add no path. No worker calls the routes.
- Reading P-15 counts users by name, by name only through a catalogue id, and only by inheritance (by path, seed role, account type and disabled). It names no one and counts custom roles without naming them. Not read on production (AQ-11).
- Defect fixed alongside (4B-10): the Permissions page saves roles by catalogue id. `has_explicit_grant` now accepts that id, or a catalogue label, for the same permission, so a grant made through the product works for `dms.claim.determine` and `dms.claim.strategy.read`. `*`, `dms.admin` and Super Admin are still refused. Not fixed, and recorded for a separate task: the effective-permissions endpoint sends the client the id instead of the name for such grants.

**As built, 4B-11 and 4B-34 (2026-11-16).**

- 4B-11. `GET /api/pickers/users?project_id=&permission=` (`routers/pickers.py`, `services/picker_service.py`) lists active members of the project who hold the permission. Membership uses `ScopeService.is_client_scope_allowed`; authority uses `PolicyService.has_permission`, so `dms.claim.determine` counts only when held by name. Super Admin, Super User and service accounts are never offered. The project's organisation is read from the project. The caller needs the domain's view permission on that project. Only an allow-list of seven permissions can be asked about, and the response carries an id and a name only. Client: `components/pickers/UserPicker.tsx`, `DocumentPicker.tsx` and `LetterPicker.tsx`; `lib/plain-language.ts` (statuses, engine terms, error codes, `errorMessage` rendering `user_message` with ids scrubbed); `lib/picker-labels.ts`. `tests/test_plain_language_coverage.py` fails on any server status or structured error code the map lacks. Adopting `UserPicker` in the determination approver and the claim approval dialog is 4B-26 (C-21's second half).
- 4B-34. Escalation now puts the claim's documents linked with the role `notice` into each head's `notice_ids`, and into the notice-compliance row's `notice_document_ids`. Only documents the caller may see are used, through the same relationship listing as the rest of the claim's documents. The preview warns when none is linked. The case workspace's claim-matrix form gains `ClaimHeadLinksPicker`: clauses from the case's clause matrix (or added by number), a `LetterPicker` for the notice and a `DocumentPicker` for evidence. A head entered by hand can therefore be complete. Proof: an escalated claim with a linked notice and a supported causation reaches a Statement of Claim with no head gaps (real MongoDB); the same is shown through the real relationship and escalation routes.

**As built, 4B-35 (2026-11-16).**

- One parser, `services/arbitration_drafting/citation_refs.py`, serves the validator, the citation view and the governed workflow gate. It reads `[S3]`, `[S3: …]`, `[SRC-4]` and `[SRC-4: …]`. It also reads exhibit references and normalises them to the workspace's exhibit ids: "Ex. C-12", "Exh. C 12", "Exhibit R/3", "CW-1/4", "Annexure A-7", "Annex. A/7", and bare upper-case "C-12" and "R-3". A label inside `[S3: Ex. C-12]` is not counted twice. Letter numbers, road names, dates and clause numbers are not read as exhibits (corpus test).
- Verified is the allow-list `CITATION_VERIFIED_STATUSES` (approved, edited_verified, ready, supported, user_verified, valid, verified). A missing, unknown, `rejected`, `stale`, `ai_suggested`, `authority_denied` or `selected` status is unverified. `selected` was already unverified at both gates; it stays in the ledger-membership set in `context.py`, which is a different question.
- Ledger builders no longer default to `approved`. `ledger_verification_status` reports, in order: a negative status (stale, authority denied, revoked, withdrawn); otherwise an allow-listed approval; otherwise the first field present; otherwise `unverified`. Two test fixtures that relied on a status-less row counting as verified were given an explicit `approved`.
- Exhibit citations resolve against the ledger rows' `exhibit_id`. An unresolved exhibit blocks approval by name ("Ex. C-99"), and an exhibit counts as a same-sentence citation. The sentence splitter no longer splits after "Ex.", "Exh.", "Ann." or "Annex.".
- Exhibit numbers: the unique `(case_id, exhibit_id)` index already existed in `core.database.ensure_indexes`. Concurrent creation therefore could not duplicate a number, but the losing request failed with a 500. An assigned number that loses the race is now re-assigned (up to 8 attempts); a number the user chose that is taken answers 409. No new migration. Whether production's index exists, and whether it holds duplicates, is not read (AQ-11).

**As built, 4B-12 (2026-11-16).**

- Create (`POST /claims`): a client-supplied `origin` is refused with 422 (`claim_origin_server_owned`), so the origin unique index can no longer be squatted to hide a real signal. Only the signal path sets an origin. A status other than `draft` or `notified` is refused with 422 (`claim_create_status`).
- Status moves (`POST /claims/{id}/status`, and a status change through `PUT`) are compare-and-set on the status the move was checked against. Of two concurrent moves from `submitted`, one returns 200 and the other 409 (`claim_changed_concurrently`). A move to the same status is a no-op.
- A claim whose evidence is frozen (escalated) refuses `PUT` with 409 (`claim_evidence_frozen`). Status moves remain possible, so a disputed claim can still be closed.
- An explicit null clears a clearable field (dates, description, reference, agreed figures, responsible party). A null for any other field is ignored, as before. The register form now sends null for a field emptied while editing.
- `sla_rules` notice windows are coerced to whole days from 1 to 365 (`window_days`); anything else is ignored with a warning, and the default applies.
- C-27 fixed alongside (see the defect table).

**As built, 4B-21 (2026-11-16).**

- `services/key_date_determination_rule.py` holds the register's rule as pure functions (`operative_determinations`, `governing_by_milestone`, `superseded_by`). `KeyDateRevisionService._replay_determinations` now calls them, and its status constants are imported from there, so the register and the Delay Lab cannot drift. The register's own tests pass unchanged.
- A linked determination's days count only if it is frozen, its status is final, nothing supersedes it, and it governs the head's milestone in the register. Status `superseded` is no longer a refusal: in the register it marks the superseding, operative determination. Drafts, `pending` and `under_review` are not counted. A later determination on the same milestone governs, and the two are not summed. Each refusal names its reason.
- The determination snapshot carries `frozen_at`, `supersedes_determination_ids`, `superseded_by` and `governs`, so freezing, superseding or a change of governing determination marks the link stale.
- Compatibility: a determination link recorded before 4B-21 has none of these fields. It is not counted and shows "Re-link this determination" until refreshed. Whether production holds such links (P-03 / P-07) is not read; the Claim Bench is off there.

**As built, 4B-29 (2026-11-16).**

- `services/docx_xml.py` is the one source of Word text and tables for the claim-submission writer and the arbitration exporter's fallback writer. Characters XML 1.0 forbids are removed, not escaped; markup is escaped; tables carry a `w:tblGrid` and rows are padded to it. The python-docx path removes the same characters before python-docx sees them.
- Production-visible change, for review under AQ-7: the arbitration fallback writer, which production uses because python-docx is not installed, now writes real tables where it used to write tab-joined lines. 4B-30 (python-docx) is not started.
- Not changed: the letter-drafting Word writer (`services/letter_drafting/service.py`) builds its own XML with the same escaping gap. It is outside 4B-29's two writers and is live in production; noted for a follow-up.

**As built, 4B-28 (2026-11-16).**

- `services/submission_figures.py`; `validate_section_text` uses it. Figures are typed: money (INR, Rs or ₹, or a lakh/crore/million scale, applied), days, or plain numbers. Amounts and days written in words are read. A money or day figure must match a record figure of the same value and type, so digits borrowed from a date or another figure no longer pass. Where a paragraph names one head, its money and day figures must be that head's own (from record paragraphs naming that head), which closes cross-head swaps. Clause references are read in "Clause", "Sub-Clause", "Cl.", "Article" and "Art." forms, and a four-digit year is not a clause. The reservation must appear in a sentence without a negation ("not", "no", "without", "waive").
- Corpus: 13 bypasses rejected, 10 faithful rewrites accepted (`tests/test_submission_validator_corpus.py`). Limitation: a currency written with no space before its digits ("INR1,20") is read as a plain number.

**As built, 4B-22 (2026-11-16).**

- Two kinds of overlap (`services/claim_delay_engine.py`). Employer × Contractor is computed as before. Neutral × Contractor is computed once the head records a neutral entitlement (`include_neutral` with its basis). A day on which all three owners are critical belongs to both, and each is decided on its own.
- Identity by events (C-03): an overlap id is its kind, dates and a digest of its event ids, and the finding stores `event_ids`. Two pairs of events on the same dates are two findings. A change to the dates or events orphans the finding, which the existing orphan sweep removes. Adjacent events of one owner give separate overlaps; a person-initiated merge is not built.
- Time is counted as a set of days (C-02): Employer days with no Contractor delay; neutral days with no Contractor delay where the entitlement is recorded; and the days of every overlap decided as carrying time. Neutral days no longer vanish because an Employer event covers them. An apportioned overlap adds its days, capped by what is not already counted, so no day is counted twice. The plan's example: C1, N1 and E1 on 1–10 January, with the E × C overlap decided `no_eot` and the N × C entitlement recorded, gives 10 days.
- No automatic money (C-04, AQ-4): `not_true_concurrency` and `eot_granted_with_cost` carry time at once. They carry cost only when the finding's `cost_basis` (contractual basis, causation, cost evidence linked to the claim) has been recorded and reviewed by someone else holding `dms.claim.determine` by name. Routes: `PUT …/overlaps/{id}/cost-basis` and `POST …/cost-basis/review`. Re-deciding the treatment withdraws the cost basis. Neutral delay never carries cost. The quantum lock rules follow. The `delay_planning_expert` route to reviewing waits on 4B-07.
- End before start (C-06, engine part): reported as `reversed_event_ids`, with its own message ("correct the dates on the register"), no longer "set the date the analysis runs to".
- UI: the Delay tab labels each overlap's kind and shows a cost panel (`OverlapCostBasis`) for an Employer × Contractor overlap decided as carrying cost. The net-EOT table is 4B-25.
- Compatibility: findings stored before 4B-22 are keyed by dates only. They no longer match any overlap and are removed by the orphan sweep on the next delay edit, so every such overlap must be re-decided. Whether production holds any (P-03/P-07) is not read; the Bench is off there.
- Proof: engine tests including C-02, C-03 and C-04 exactly, and two seeded property tests (40 seeds × 25 cases each). (1) Under any single treatment policy, adding an Employer event never lowers the low or high end. (2) For any decisions, low ≥ the undisputed Employer days, high ≤ every day an entitled event covers, and cost days ⊆ time days. The second property found a double count in `high`, now fixed.

**C-25 (owner decision 4).** High priority. The read-only assessment is reading P-13 in 4B-01. The fix is verified on a real MongoDB for create, list, get, update, soft-delete, tenant isolation and case-driven drafting. If P-13 shows production is affected, a minimal, independently tested hotfix proposal against the production release branch is ready (`docs/C25_HOTFIX_PROPOSAL.md`); it is not deployed without separate approval. The proposal also carries C-26 (found while verifying C-25) and C-27 (added on the owner's instruction), and its tests exercise every operation listed above.

Critical path: 4B-02 → 4B-47 → 4B-04 → 4B-05 → 4B-07 → 4B-26 → 4B-33 → 4B-36 → 4B-39 → 4B-43 → 4B-44. Waves 4 and 5 run beside Waves 1 and 2 once 4B-02 lands. 4B-48 is off the critical path.

**Production-reading dependencies.** 4B-01 writes the read-only script, which is code and needs no access. Taking the readings needs authorised read-only access (AQ-11). Until the readings exist, the tickets below may be built and tested, but their reading-dependent step is not final and they are not released:

- 4B-06 (seed conflicts, P-05);
- 4B-18 (contract coverage, P-03);
- 4B-19 (legacy coercion, P-03);
- 4B-45 (switch to `strict`, P-12);
- 4B-49 (closing the extraction-review inheritance, P-15);
- M-01 to M-05 release ordering (P-02);
- D-09 retirement (P-11).

**Execution order as authorised (owner review C).**

1. Wave 0: 4B-02, 4B-03, 4B-01 (script only), 4B-47.
2. Independent security groundwork: 4B-04, 4B-10, then 4B-05.
3. Remaining waves in table order.

---

## 6. Test strategy and traceability

### 6.1 Traceability: every finding to tickets and tests

| Finding | Tickets | Tests that must exist and pass |
|---|---|---|
| B1 | 4B-04, 4B-41 | `test_skill_publication_gate.py`: system re-import with `[]` refused; new version below floor refused; manifest change invalidates approvals; eval run `recorded_by` from session; threshold floor. The existing assertion at `:189` is replaced. |
| B1b | 4B-05 | `test_skill_publication_concurrency.py` (new): revoke versus publish race; duplicate concurrent approvals; router revoke → deprecate; deprecate only when relied on |
| B2 | 4B-33, 4B-34 | `test_pleaded_heads_end_to_end.py` (new): escalated row and manual row through the real ledger; zero heads blocked; unapproved heads blocked; legal-review gate blocks |
| B3 | 4B-06 to 4B-09 | `test_reviewer_assignments.py` (new): RBAC matrix 6.4; `UsersPage` RTL; `skills-review.spec.ts` |
| B4 | 4B-39, 4B-40 | Parity test `test_arbitration_ui_api_parity.py` (new) plus RTL; `arbitration-pleading-gates.spec.ts` |
| C-01 | 4B-21 | `test_claim_delay_quantum.py`: D1 + superseding D2 → 15; draft not counted; under review not counted; freeze marks stale |
| C-02 | 4B-22 | Engine: C1 + N1 + E1 (decided `no_eot`) → 10 with neutral entitlement recorded; property test: adding an Employer event never lowers the low end |
| C-03 | 4B-22 | Engine: event swap orphans the finding; adjacent events give two overlaps |
| C-04 | 4B-22 | Engine: `not_true_concurrency` without cost basis → 0 compensable days |
| C-05 | 4B-18, 4B-23 | Link from another contract → 422 |
| C-06 | 4B-22, 4B-24 | Formula fixtures; unlink locks the line; end-before-start message |
| C-07 | 4B-17 | PUT amount on a board claim → 409; PUT on frozen → 409; clearing heads nulls the roll-up; removing the last line clears the head; typed head amount with lines → 409 |
| C-08 | 4B-14 | Six tests listed at 4B-14 |
| C-09 | 4B-26 | Five tests listed at 4B-26 |
| C-10 | 4B-36, 4B-37 | Concurrency, idempotency, filed case, target preview, freeze service, undo before and after a version |
| C-11 | 4B-35 | Format corpus; unknown status; allow-list; exhibit numbering race |
| C-12 | 4B-13 | Three tests listed at 4B-13 |
| C-13 | 4B-12 | Four tests listed at 4B-12 |
| C-14 | 4B-15 | Four tests listed at 4B-15 |
| C-15 | 4B-10 | Super Admin 403 on three actions |
| C-16 | 4B-27 to 4B-30 | Redaction and stable hashes; bypass corpus; DOCX fuzz; `pip-audit` |
| C-17 | 4B-16 | Lock with stale → 409; concurrent stale marking |
| C-18 | 4B-03 | Config, route and hook tests |
| C-19 | 4B-10 | Role-contract alignment inspect output |
| C-20 | 4B-38 | Attach validation |
| C-21 | 4B-11, 4B-26 | Picker API returns only project members holding the permission |
| C-22 | 4B-20, 4B-25, 4B-31, 4B-32, 4B-34, 4B-40 | `claim-journey.spec.ts` steps 1–8 |
| C-23 | 4B-19 | `initial_head` coercion tests |
| D-25 | 4B-27, 4B-46 | Export refused with `evidence_restricted`; no title, date or type of a restricted item in any response (asserted on the raw JSON) |
| D-26 | 4B-45 | Both modes; implicit scratch drafts cannot be approved or exported |
| D-27 | 4B-46 | Direct API calls with hidden-action tokens are refused or redacted |
| D-28 | 4B-48 | Draft unresolvable; approvals required |

### 6.2 Backend integration tests

Each test runs against the real routers with real dependency factories (4B-02 pattern), a MongoDB test database with `subscriptions` and `superuser` fixtures as required by `projectDMS/CLAUDE.md`, and no service mocks for the module under test. Async tests use the per-test `asyncio.run` loop; fixtures are context managers.

### 6.3 Frontend component tests

vitest plus RTL for every changed component: disabled states carry a visible reason; no raw id in rendered text; pickers call scoped endpoints; plain-language map coverage, with a test failing on an unmapped status or error code.

### 6.4 RBAC matrix

| Actor | Platform reviewer grant | Org reviewer grant | Record pack approval | Publish | Read strategy | Determine | Approve determination | Verify notice period | Change perspective (after use) |
|---|---|---|---|---|---|---|---|---|---|
| Super Admin, no assignment | Yes (others only) | Yes | No | Yes, if not evaluator or signatory | **No** | **No** | **No** | No | No |
| Org admin | No | Own organisation | No | No | Only if granted | Only if granted | Only if granted | If manage + clause read, not recorder | Yes, with reason |
| Platform `senior_legal_approver` | No | No | Legal | No, for versions they signed | No | No | No | No | No |
| Project member with `dms.claim.determine` | No | No | No | No | No | Yes (Engineer or Employer project) | Yes, if named reviewer and not author | No | No |
| Claim editor | No | No | No | No | No | No | No | No (records only) | No |
| Viewer | No | No | No | No | No | No | No | No | No |

Every cell is one parametrised test. Cross-tenant variants (actor from organisation A, resource in B) must return 403 or 404, never 200 or 500.

### 6.5 Concurrency tests

Using `asyncio.gather`, against a MongoDB replica set where the code relies on unique indexes:

- publish versus revoke;
- two approvals by one user;
- two status transitions;
- five escalation runs into one case;
- two new-case escalations of one claim;
- a stale-marking versus board write;
- two exhibit creations;
- two signal accepts.

### 6.6 Security tests

- **Publication bypass corpus:** the import variants from B1.
- **Validator bypass corpus:** amounts in words; lakh/crore; borrowed digits; cross-head swaps; negated reservation; "Cl." and "Article".
- **XML:** control characters, `&`, `<`, `>` and RTL marks in every exported text field.
- **Privilege wall:** a source-tree scan that `claim_privileged` is read by one module only (existing test, extended to exporters, ledger and escalation).
- **Tenant isolation:** cross-organisation tests on every new route.
- **UI-independent enforcement (D-27):** every refusal the UI implies is also tested by a direct API call.
- **Resolver authority (D-06):** legacy role names, organisation assignments, expired assignments, revoked assignments and same-name custom roles each fail to confer platform authority.
- **Audit completeness:** each privileged action writes exactly one audit row with actor, before and after.

### 6.7 Playwright end-to-end

- Mocked suites (4B-43) run in CI.
- The staging suite (4B-44) runs against the deployed stack with the internal canary tenant and covers the full journey, steps 1 to 8: a contractor project from claim start to an escalated case, and an Engineer project to an approved determination with a pre-filled letter.
- Each run records its build identifiers and is attached as evidence.
- A mocked pass does not satisfy a staging criterion.

---

## 7. Skill-pack golden-set evaluation requirements

Applies to `indian-infra-claims` and `indian-infra-eot-claims` in Phase 4b. The pleadings and hearing sets are specified here and built before Phase 5 publication is considered.

| Requirement | Value |
|---|---|
| Matters per pack | Claims 15; EOT 15; pleadings 12; hearing 10 |
| Composition, claims | At least 4 Engineer or Employer perspective; 3 late notices (one with prejudice evidence, one without); 2 global claims; 2 with missing records; 2 variation-derived; 2 straightforward |
| Composition, EOT | At least 4 with true concurrency; 2 with neutral events; 2 with superseded determinations; 2 with no critical-path proof; 2 windows-method matters |
| Source | Anonymised real matters with owner consent, or matters written by the reviewing experts. Real-matter material is stored outside the repository in the restricted evaluation store (AQ-10); the repository holds only hashes and synthetic fixtures. |
| Labels | Two independent experts per matter (the pack's technical reviewer role and a second expert), with disagreements adjudicated by the legal approver. Labels are element states, classification, overlap treatment proposals and the expected "information required" list. |
| Hard gates (any failure fails the run) | Zero invented amounts or days; zero assumed notice periods presented as contractual; zero citations to clauses absent from the matter; zero privileged content in non-privileged outputs; schema validity 100 % |
| Scored gates | Element-state agreement with adjudicated labels ≥ 0.85 per pack and ≥ 0.75 per element type; classification agreement ≥ 0.85; degraded-step rate ≤ 5 % |
| Run binding | A run records suite hash, pack content hash, policy hash, model, configuration and the session user. Approvals bind the run id. Any pack, suite or model change requires a new run. |
| Reporting | A per-matter diff view for reviewers. Runs are retained. Cost per run recorded. |
| Publication | Not in Phase 4b. A passing run is necessary, not sufficient: D-05 approvals are still required. |

---

## 8. Migration, compatibility, canary, monitoring and rollback

### 8.1 Production and staging readings before any migration (4B-01, read-only)

| Reading | Question | Decides |
|---|---|---|
| P-01 | Deployed commit and image labels | Baseline |
| P-02 | Migration ledger contents | Order of M-01 to M-05 relative to the unapplied `20261114_*` and Phase 0–4 migrations |
| P-03 | Claims by status, type, `amount_claimed` type, EOT-days type; off-enum statuses; claims touched in the last 90 days | D-10 backfill; 4B-19 coercion rules |
| P-04 | Value types in `sla_rules` and notice windows | 4B-12 coercion |
| P-05 | Role documents whose `_id` or name is any reviewer role; count of users holding each (counts only, no names in evidence) | M-02 conflicts; AQ-3 |
| P-06 | Users holding more than one role | Exposure to the `UsersPage` overwrite |
| P-07 | Arbitration cases by status; drafts with no case; drafts in approval; claim-matrix rows by origin | Scratch migration effect; B2 effect on drafts in flight |
| P-08 | `skill_*` collections present | Expect absent at `34d664a` |
| P-09 | Projects with `contractual_capacity` | Expect absent |
| P-10 | Whether `ARBITRATION_REVIEWER_ROLE_MATRIX` is set in the running backend (presence, and value, which is not secret) | AQ-3 |
| P-11 | Use of the claims register, claim detail and AI assessment in the last 90 days (audit and access logs) | D-09 retirement |
| P-12 | Callers of `POST /arbitration/drafts` without a case in the last 90 days (access logs; counts by client) | D-26 switch to strict |
| P-13 | C-25 (owner decision 4, 2026-11-16): arbitration cases with `deleted_at` missing, null and set; how many the pre-fix query (`$exists: false`) could not find; their statuses and number of organisations; drafts and matrix rows referencing them (counts only) | Whether production is affected, and so whether the C-25 hotfix proposal is taken forward |
| P-14 | C-08 / D-08 (4B-14, 2026-11-16): notice requirements by status; current ones by origin and verification; current ones verified by their own recorder; current ones with a claim type outside `ClaimType`; organisations holding any (counts only) | Whether legacy notice periods need re-verification after 4B-14; until read, none is re-flagged |

The script uses only reads, never prints a credential, and its evidence is scanned by value with `scripts/evidence_secret_scan.py`.

**Evidence classes (owner review B.5).** Every statement in this plan or its evidence about production carries one of these classes:

| Class | Meaning |
|---|---|
| Production — verified | Read from the running production system by authorised read-only access, with date, operator and command |
| Staging — verified | Read from staging the same way; says nothing about production |
| Repository — inferred | Deduced from code or configuration in a named commit, such as "`34d664a` has 19 migrations"; not a reading |
| Not read | No reading exists |

As of this revision, the only "Production — verified" fact is the deployed checkout (branch `release/contraclaim-rc1`, commit `34d664a`), read on 2026-11-15. The running image revision was not read. Everything else about production in this plan is "Repository — inferred" or "Not read".

**An unreadable database is not an empty one (owner review B.7).** If production cannot be read, the readings are recorded as "Not read" with the blocker. No migration, backfill, seed or compatibility switch may be justified by the absence of data that was never read. Any reading-dependent decision stays open.

### 8.2 Compatibility

- Legacy claim fields stay readable and writable through the register while the Bench is off. With the Bench on, derived fields are read-only only on claims that have a board.
- `POST /claims` rejecting `origin` and late statuses is a contract change. The client never sends `origin`; P-11 and API logs confirm no other caller before release.
- Arbitration: new blockers apply to drafts in flight (P-07 measures how many). No engine, prompt or gate configuration changes. The `python-docx` change is gated (AQ-7).
- Phase 0–4 migrations (`20261115_0001` to `20261119_0001`) are not yet in production. Phase 4b migrations are appended after them, and the whole sequence is dry-run against a restored copy of production data before release.

### 8.3 Canary

1. Staging with `CLAIM_BENCH_ROLLOUT_MODE=canary` and the internal test tenant only; 4B-44 green twice.
2. Production remains `off` until section 9 is met and the owner approves. Then `canary` for one internal tenant for at least 14 days, with real internal matters.
3. `primary` only after the canary criteria in 8.4 hold for 14 consecutive days and the owner signs off.

### 8.4 Monitoring

| Signal | Threshold for rollback or hold |
|---|---|
| 5xx on `/api/claims*` and `/api/arbitration/drafts*` | Any sustained rise over baseline; any 5xx from a new route |
| 409 rate on board writes | > 5 % of writes over a day: investigate UX |
| Degraded skill steps | Expected 100 % while packs are unpublished; anything else is an alarm |
| Duplicate-key resolutions on escalation | Counted; any duplicate row is a Sev 2 |
| Approval and publication audit events | Each publish has its approvals and run id; a mismatch is a Sev 1 |
| Super Admin access attempts to privileged routes | Logged; any 200 is a Sev 1 |
| Frontend error toasts with an unmapped code | Any: fix the mapping |

### 8.5 Rollback

| Change | Rollback |
|---|---|
| Bench exposure | Set the mode to `off`. Bench routes return 404, the legacy register keeps working, and board data is retained. |
| Index migrations (M-01, M-03, M-04, M-05) | Drop the indexes. No data loss. |
| Role identities (M-02) | Remove only if no assignment references them; otherwise revoke the assignments first. |
| Reviewer assignments | Revoke; audit is retained. |
| `python-docx` | Revert the requirement; the minimal writer with sanitiser remains. |
| Escalation | Un-escalate while allowed; afterwards, case-level remedies only. |

Every migration is additive or index-only. None deletes or rewrites user data.

---

## 9. Exit criteria for starting Phase 5

All must hold, each with recorded evidence:

1. Every ticket 4B-01 to 4B-47 is merged to the working branch with its proof tests. 4B-48 is excluded: it is off the critical path.
2. Every finding in section 6.1 maps to at least one passing test that exercises real dependencies.
3. The RBAC matrix (6.4) and the concurrency (6.5) and security (6.6) suites are green.
4. Mocked Playwright suites are green in CI. The staging suite has passed twice consecutively on the deployed stack, with a Statement of Claim drafted from an escalated claim passing every existing gate.
5. Gate 6 fresh-install runs are recorded for the final migration count, and route inventories are regenerated and diffed.
6. Readings P-01 to P-12 are recorded for staging and production, and decisions D-09, D-10 and AQ-3 are finalised against them.
7. Claims and EOT golden-set baseline runs are recorded (section 7). No pack is published.
8. AQ-1 to AQ-10 are decided (section 13), and AQ-11 has been resolved by authorised read-only access.
9. The owner has accepted Phase 4b in writing in this document's decision record.
10. The complete claim → escalation → pleading → approval journey has passed with real dependencies: a real backend, a MongoDB replica set and real authorisation. Mocked suites do not satisfy this criterion.

---

## 10. Residual risks and non-goals

### Residual risks

| Risk | Mitigation and remaining exposure |
|---|---|
| Production data differs from what the code expects (legacy types, hand-made roles) | 4B-01 readings before any migration; skip-existing seeding. Exposure remains until readings are taken. |
| Reviewer resolver changes which arbitration users pass gates | Enabling only; hand-made `users.roles` still honoured. AQ-3 decides seeding the other gate roles. |
| Legal reading of concurrency and cost (`not_true_concurrency`, apportionment) | Treatments are recorded decisions with a basis; AQ-4 confirms the default. |
| Golden-set labels are expert opinion | Two experts plus adjudication; packs remain unpublished. |
| Exhibit detection misses an unusual format | The format corpus grows with each matter; unresolved formats are reported. |
| Migration ordering with unapplied `20261114_*` migrations in production | Full-sequence dry-run on a restored copy (8.2). |
| Bench and legacy views disagree during canary | Derived fields are read-only on board claims; the legacy view shows "managed on the Bench". |
| UI copy is legally imprecise | Plain-language map reviewed by the legal approver before canary. |

### Non-goals for Phase 4b

- Publishing any skill pack, or changing the arbitration engine, rollout mode, prompt-version constants or acceptance receipts.
- Hearing Room (Phase 5) and Dispute Spine (Phase 6).
- Traverse Board, counterclaim section set and agent, opponent pleading upload, dispute ladder, and the full split of the two arbitration pages. Only the components touched by B4 and 4B-40 are extracted.
- Head-level awareness dates (the notice clock stays per claim).
- A CPM engine or true Time Impact Analysis.
- Provider abstraction for the LLM.
- Removing the legacy claim record page or AI assessment (D-09).
- Any deploy, merge or production write.

---

## 11. Architectural questions (decided 2026-11-16, see section 13; AQ-11 open)

| ID | Question | Recommendation |
|---|---|---|
| AQ-1 | Claims have no contract. Should `contract_id` be required for new claims, or only when the project has several contracts? | Required when the project has more than one contract; optional otherwise; legacy claims unchanged |
| AQ-2 | Reviewer assignments: a new collection, or an extension of `expert_allocations`? And `senior_legal_approver` serves both platform pack sign-off and organisation-level case approval. | New collection with the same audit pattern. One role key held at different scopes, checked by scope: pack approval needs platform scope; case approval needs organisation or project scope. |
| AQ-3 | `legal_reviewer`, `contract_reviewer`, `delay_reviewer`, `quantum_reviewer` and `export_authorizer` (arbitration gates) are also unseeded. Seed them in M-02? | Yes, as identities with no assignments, after P-05 and P-10 are read. It enables existing gates and relaxes none. |
| AQ-4 | Does `not_true_concurrency` carry cost only with a recorded cost basis? | Yes (D-16), confirmed by the legal approver |
| AQ-5 | A user lacking access to some submission evidence: refuse export, or export with those items redacted? | Refuse, listing the restricted items; a full-access user exports |
| AQ-6 | "Before the first pleading version": any draft version in the case, or only a Statement of Claim? | Any draft version in the case |
| AQ-7 | Adding `python-docx` changes production pleading Word output. Accept within Phase 4b? | Yes, after the sanitiser lands and the exporter regression tests are reviewed |
| AQ-8 | Legacy claim page and AI assessment retirement date | Decide after P-11 and 30 days primary (D-09) |
| AQ-9 | Unlock at `dms.claim.manage`: too strict for small teams? | Keep manage plus reason; revisit after canary feedback |
| AQ-10 | Where golden-set material lives, and the model budget for evaluation runs | A restricted evaluation store outside the repository; a budget set per run before 4B-42 |
| AQ-11 | Authorised read-only access for readings P-01 to P-14 on production (and staging), and the operator who takes them | **Owner decision 2 (2026-11-16):** the readings remain mandatory. Development and local testing continue; migration compatibility, role seeding, legacy claims and deployment decisions are not finalised until the evidence exists. Still open: no production access from this environment |
| AQ-12 | Found in 4B-10: `dms.document.review_extraction` is also meant to be granted only by name, but any `dms.admin` holder passes it, because PolicyService widens `dms.*` checks to `dms.admin`. That includes `contractmgr_org`, which the seed comment says must not have it. Unlike the two claim permissions, this one is live in production, so closing the gap would withdraw access people may use today. | Open. Recommendation: add it to `SUPERADMIN_EXCLUDED_PERMISSIONS` after a production reading of which users hold it only through `dms.admin`. 4B-10 deliberately left it unchanged. **Owner decision 3 (2026-11-16):** a separate high-priority audit and remediation ticket, 4B-49; explicit-grant-only is the target; existing production workflows are preserved until the affected users and services are identified and a migration path is approved. **4B-49 (2026-11-16):** matrix, reading P-15 and a five-step migration path delivered; awaiting P-15 and the owner's decisions on Contract Manager - Organization and Super Admin |

---

## 12. Final acceptance checklist

Blockers

- [ ] B1: system-pack approval floor enforced server-side; manifest policy hashed; evaluator identity from session; approvals bound to version, content, policy and run
- [ ] B1b: publish atomic against approvals; independence at publish; revoke deprecates only relied-on approvals
- [ ] B2: escalation → matrix → ledger → validator carries every head element; zero or unapproved heads block a Statement of Claim; legal review blocks head gaps
- [ ] B3: four reviewer identities seeded; scoped, audited, expiring assignments; Super Admin-only platform grants; reviewers use a read-only review view
- [ ] B4: Approve and Export disabled with plain reasons; every disabled UI action refused by the API (parity test)

Correctness

- [ ] Already-granted EOT from the register rule (C-01)
- [ ] Neutral, Employer and Contractor overlaps decided, none auto-deducted (C-02, C-03, C-04)
- [ ] One amount source; formulas in code; paise (C-06, C-07)
- [ ] Notice period verified by a second authorised person against the clause index (C-08)
- [ ] Determination four-eyes; revisions re-decided (C-09)
- [ ] Escalation idempotent and reversible until the first pleading version (C-10)
- [ ] Citations allow-listed; exhibits detected (C-11)
- [ ] No duplicate claims from signals (C-12)
- [ ] Status, perspective and tenant guards (C-13, C-14, C-15, C-19, C-20)
- [ ] Submission evidence snapshot stable and access-controlled; validators hardened; Word output well-formed (C-16)
- [ ] Stale tests block the lock (C-17); rollout configuration validated (C-18)

Journey

- [ ] Three-question start → heads → notice → delay (net-EOT table) → quantum → determination (pre-filled letters) or submission (provenance) → escalation (multi-select, undo) → pleading (gate stepper)
- [ ] Shared pickers; plain-language statuses; no database id or engine term shown; structured error messages

Proof

- [ ] Real-dependency route tests; RBAC matrix; concurrency; security corpora
- [ ] Mocked Playwright suites in CI; staging suite green twice on the deployed stack
- [ ] Gate 6 runs; route inventories; readings P-01 to P-11 recorded
- [ ] Golden-set baselines recorded; no pack published
- [ ] Real-MongoDB suites run in CI (4B-47); the complete claim → escalation → pleading → approval journey passes with real dependencies
- [ ] Backward compatibility for `POST /arbitration/drafts` (4B-45); backend privilege-wall and provenance suite (4B-46)
- [ ] Every production statement carries its evidence class; unread readings recorded as not read
- [ ] Open questions answered or deferred by the owner; Phase 4b accepted in writing

## Decision record

| Date | Decision | By |
|---|---|---|
| 2026-11-16 | Audit findings accepted; D-05 to D-21 approved subject to the Phase 4b requirements; plan requested | Owner |
| 2026-11-16 | Plan approved in principle subject to amendments B.1 to B.7; AQ-1 to AQ-10 decided; implementation authorised from Wave 0 and independent security groundwork; PR #114 stays draft | Owner |
| — | Phase 4b exit criteria met and accepted; Phase 5 may start | Pending |

## 13. Owner review, 2026-11-16

### Decisions on the architectural questions

| ID | Decision | Lands in |
|---|---|---|
| AQ-1 | Associate every new claim with a contract where identifiable; auto-select only when exactly one valid contract exists; explicit selection when several exist; legacy null until resolved; no cross-contract evidence or key-date linking | D-23, 4B-18, 4B-23 |
| AQ-2 | Dedicated `reviewer_role_assignments` with platform, organisation and project scopes, expiry, revocation, audit and least privilege. Platform legal approval needs an explicit platform assignment; organisation and project assignments never confer it; legacy roles reconciled without a bypass. | D-06, 4B-06, 4B-07 |
| AQ-3 | Seed the unseeded arbitration reviewer identities, subject to P-05 and P-10; identities only, no permissions or assignments | D-06, 4B-06 |
| AQ-4 | No automatic money from `not_true_concurrency`; recorded contractual basis, causation, cost evidence and authorised review required | D-16, 4B-22, 4B-24 |
| AQ-5 | Refuse export when required evidence is inaccessible; explain without leaking content or metadata | D-25, 4B-27, 4B-46 |
| AQ-6 | Block un-escalation after the first pleading version in the case; case-less scratch drafts do not count; full audit and reversible state before the cut-off | D-18, 4B-37 |
| AQ-7 | `python-docx` conditionally, after sanitisation, dependency scan and regression testing; no unreviewed change to production export behaviour | D-19, 4B-29, 4B-30 |
| AQ-8 | No retirement of the legacy claim page or AI assessment until production use is verified, primary has been stable for 30 days, and the owner approves explicitly | D-09 |
| AQ-9 | Unlock keeps `dms.claim.manage`, a mandatory reason and an immutable audit trail | D-22, 4B-16 |
| AQ-10 | Restricted, encrypted evaluation store outside the repository; hashes and synthetic fixtures in GitHub; configured per-run model budget, explicit approval to exceed | D-21, section 7, 4B-41, 4B-42 |

### Mandatory amendments and where they are met

| Ref | Amendment | Change in this plan |
|---|---|---|
| B.1 | Prove publication and revocation atomicity across approval records and version state; a standalone digest comparison must not leave a gap | D-05a rewritten: transactions serialised on the version document; fault-injection concurrency proof; 4B-05 |
| B.2 | The resolver cannot treat legacy role names or organisation assignments as platform legal authority | D-06 resolver rules; 4B-07 resolver tests; section 6.6 |
| B.3 | Backward-compatibility ticket for `/arbitration/drafts` callers | D-26; ticket 4B-45; reading P-12 |
| B.4 | Evaluate `india-metro-arbitration-defence_R2.zip` as a separate, unpublished specialist skill; record dependencies; not on the critical path | D-28 (bundle supplied and inspected; findings recorded); ticket 4B-48 |
| B.5 | P-01 in 4B-01; distinguish verified production evidence from staging or repository assumptions | 4B-01 covers P-01 to P-12; evidence classes in section 8.1 |
| B.6 | Privilege wall and provenance enforced at the backend regardless of UI | D-27; ticket 4B-46; section 6.6 |
| B.7 | An inaccessible production database is not evidence of an empty or safe-to-migrate one | Section 8.1 rule; reading-dependent tickets listed in section 5 |

### Authorisation and limits

- **Authorised:** implementation on `claude/zen-ptolemy-695liz` in the approved dependency order, starting with Wave 0 and the independent security groundwork.
- **Readings:** production readings only through authorised read-only access. Where access is unavailable, the blocker is recorded, and only work that does not depend on the readings continues.
- **Not authorised without separate authorisation:** any production migration, backfill, deployment, rollout activation, skill publication, merge or history rewrite. PR #114 stays draft.
- **Verification required:** real-MongoDB integration, router authorisation, concurrency, security and frontend component tests, plus mocked and staging Playwright suites. The complete claim → escalation → pleading → approval journey must pass with real dependencies before Phase 5.
