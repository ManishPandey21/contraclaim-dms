# Claim → Arbitration Phase 1: status

**Spec:** `docs/superpowers/specs/2026-11-15-claim-arbitration-skills-audit-and-plan.md` (Moves 1 and 2).
**Operator notes:** `docs/CLAIM_BENCH.md`.

## Done

| Area | What | Where |
|---|---|---|
| Skill engine | Generic `skill_steps` store and step runner for non-letter subjects; a feature registers its own prompt template | `services/skills/steps.py`, `services/letter_drafting/prompts.py` |
| Claim Bench | Heads, five elements, proposals vs decisions, gate, lock, compare-and-set edits, rollout flag | `services/claim_bench.py`, `routers/claim_bench.py`, `models/claim.py` |
| Record checks | Deterministic proposal when no skill is published; the floor a skill's proposal is held to | `record_checks`, `apply_fact_guards` |
| Notice Clock | Candidates from the clause index, recorded requirement with quote, verification, supersession | `services/claim_notice_requirements.py`, `models/claim_notice.py` |
| Assessment | Uses the recorded requirement; four sources; only a verified one is the contract's | `services/claims_notice.py` |
| Signals | Hindrance and Letter Bench signals with clocks; potential claims raised once per source | `services/claim_signals.py` |
| Letters | A notice letter can be started on the Letter Bench from a claim | `services/letter_drafting/bench.py` |
| Project | Narrow route to state which party the project acts for | `routers/projects.py` |
| Screens | Claim Bench page, notice clock card and period dialog, signals strip on the register, `potential` status | `client/src/pages/ClaimBenchPage.tsx`, `client/src/components/claim-bench/` |
| Migration | `20261116_0001` indexes | `migrations/` |

## Departures from the spec, and why

- **Signals propose; a person raises.** The spec said triggers "create a potential claim". Creating claims from inside the hindrance register and the Letter Bench would mean writing to two certified modules on every save. Signals are instead computed at read time, each with its notice clock already running, and a claim is created when a person accepts one. The clock is visible either way.
- **The board is stored on the claim**, not in a separate collection, with compare-and-set on `bench.board_version`. No data migration: a board is created on first open.
- **Clause references are clause numbers**, checked against the clause index at test time, not stored clause ids. Clause records are re-created on reprocessing, so an id would go stale; the number is what the contract and the user both use.
- **Instrument precedence is shown, not computed.** Candidates from documents that modify the general conditions are listed first and marked. Which one governs is the person's decision, recorded with the quote.
- **Verification may be by the same person who recorded the period.** It needs `dms.claim.manage`. Whether a second person should be required is still open.
- **Days are not rolled up** from heads to the claim; amounts are.

## Not done

- **Project form field** for the party. It is set from the Bench banner or the API.
- **Variation trigger** for a potential claim (an instruction without an agreed rate). Hindrance and letter signals only.
- **Route-level tests** for the new routes. The services, the pure logic and the page are tested; the routes rely on the same `_load_authorized` path as the claim register. This is the same gap noted in Phase 0.
- **Playwright e2e** for the Bench.
- **Head-level awareness dates.** The notice clock is per claim.
