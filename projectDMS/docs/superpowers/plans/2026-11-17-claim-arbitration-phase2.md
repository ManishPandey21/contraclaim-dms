# Claim → Arbitration Phase 2: status

**Spec:** `docs/superpowers/specs/2026-11-15-claim-arbitration-skills-audit-and-plan.md` (Moves 3 and 4, decision D-02, correction A).
**Operator notes:** `docs/CLAIM_BENCH.md`, section "Delay Lab and Quantum Builder".

## Done

| Area | What | Where |
|---|---|---|
| Delay engine | Pure date arithmetic: counted events, overlaps, range vs settled figure, compensable days | `services/claim_delay_engine.py` |
| Links (D-02) | Many-to-many, revision-pinned links from a head to hindrances, EOT submissions and determinations; registers untouched; history kept | `services/claim_delay_lab.py`, `models/claim_delay.py`, collection `claim_head_links` |
| Concurrency (A) | Overlaps computed, never auto-deducted; treatment with basis decided per overlap; range until decided; blocks the lock | same |
| Already granted | Read from linked determination items, with the reason when not counted | same |
| Quantum | Lines with proof locks by category; prolongation tied to compensable periods; records must be claim documents | `services/claim_quantum.py` |
| Lock gate | A started delay assessment must be finished before the board locks | `services/claim_bench.py` |
| Screens | Delay tab with swim-lane timeline, event and overlap decisions; Quantum tab with locks and totals | `client/src/components/claim-bench/` |
| Migration | `20261117_0001` indexes | `migrations/` |

## Departures from the spec, and why

- **A link stores the values it relies on, not only a revision id.** The hindrance register has no revision numbers, so "pinned to a revision" is implemented as a hash plus a snapshot of the six fields the assessment uses. The register stays authoritative; the snapshot is what makes "the record has changed since" detectable and keeps the assessment stable until someone chooses the latest.
- **Decisions live on the head, links in their own collection.** The spec described a versioned `EotAssessment` object. Decisions are stored under the head on the board (same compare-and-set as every other edit) and the assessment is computed from them on read. There is no separate assessment version history beyond the board's own.
- **`days_recommended` is not a stored field.** The extension shown is computed from the decisions; a typed "recommended" figure would be a second number that could disagree with them.
- **LD exposure is shown as "Contractor delay alone"**, the count of Contractor critical days with no Employer delay. It is not valued.
- **Neutral events** are counted only when a person records that the contract gives time for them, with a basis. The spec did not address them.

## Not done

- **Skill steps.** `delay_event_classification.v1` exists as a schema; no step calls the EOT skill for events, overlaps or quantum lines. Both skills are unpublished, so a step today could only return the record fallback, which is what the register proposal already is. Built with Phase 3, where the claims skill's determination workflow is wired.
- **The contract's concurrency clause** is not looked up and quoted on an overlap.
- **Programme milestones, key dates, letters and documents as link targets.** Hindrances, EOT submissions and EOT determinations only.
- **Revision check for submissions** is by status and dates; items of a submission are not snapshotted.
- **Route-level tests and Playwright e2e**, as in earlier phases.
