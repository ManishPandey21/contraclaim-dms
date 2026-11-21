# Claim → Arbitration Phase 4: status

**Spec:** `docs/superpowers/specs/2026-11-15-claim-arbitration-skills-audit-and-plan.md` (Moves 7 and 8, correction C).
**Operator notes:** `docs/CLAIM_BENCH.md`, section "Escalation to arbitration and draft evidence controls".

This phase is the first to touch the arbitration module. It changes no engine flag, no rollout mode and no acceptance or approval gate. Every change either adds a blocker or adds information; none removes or relaxes a check.

## What the audit got wrong, and what that changed

The audit described "ungated drafting". Reading the module for this phase showed that most of correction C was already in place: a draft with no case could not be approved or exported for filing (watermarked preview only), citations to sources outside the ledger blocked approval, unverified ledger rows blocked approval, and filing export required a clean citation audit plus the full workflow receipt chain. Phase 4 therefore adds what was actually missing rather than rebuilding what was there.

## Done

| Area | What | Where |
|---|---|---|
| Citation states (C) | verified / unverified / unresolved by one function, used by the validator and the screen; counts stored on each new version; a cited unverified source named in the blockers | `services/arbitration_drafting/citation_states.py`, `validator.py`, `service.py` |
| Pleaded heads (C, Move 8 "four elements") | A Statement of Claim is blocked while a head lacks provision, event, causal link, quantified relief, a notice record, or records for its figure | same |
| Scratch drafts (C) | Case-less drafts must be requested explicitly; flag on the draft; cleared on attaching a case; migration marks existing ones | `routers/arbitration_drafting.py`, `models/arbitration_drafting.py`, migration `20261119_0001` |
| Citations route | `GET /arbitration/drafts/{id}/citations` | `routers/arbitration_drafting.py` |
| Escalation (Move 7) | Preview and run; deterministic seeding of six matrices through the case workspace's own row path; origin keys; idempotent; freeze; case and claim linked | `services/claim_escalation.py`, `routers/claim_escalation.py` |
| Case scope (Move 7) | A case with `claim_ids` reads only those claims | `agents/deterministic.py::_find_source_rows` |
| Pleadings skill (Move 8) | Published skill block attached to the drafting prompt and recorded on the version; nothing changes while unpublished | `llm_generator.py`, `service.py::_pleading_skill` |
| Screens | Escalate dialog on the Bench; citation panel and explicit scratch choice on the drafting page | `client/src/components/claim-bench/EscalateDialog.tsx`, `client/src/components/arbitration/DraftCitationPanel.tsx` |

## Departures from the spec, and why

- **The four-elements check is in the draft validator, not a new LangGraph node.** The engine's graph, its node list and its prompt versions are pinned by tests and tied to the acceptance receipt chain. A validator rule blocks approval on both engines without altering the graph. A fan-out node can follow once the engine is accepted for production.
- **The skill is attached to the existing prompt; the prompt constants are not replaced.** The spec has the `*_INSTRUCTION` constants and the counsel prompt replaced by compiled skill blocks and the prompt-version constants bumped. With the skill unpublished that would change production prompts today for no benefit and would need the pinned versions re-accepted. Attaching keeps today's prompt byte-identical, and the recorded prompt version gains a suffix naming the skill text only when a skill is in use.
- **Only the pleading-prose generator takes the skill.** The matrix agents' instructions (`agents/llm.py`) are untouched.
- **Mandatory provenance is enforced per pleaded head, not for the arbitration clause.** The jurisdiction readiness checks already require the arbitration agreement before a case is ready; a second check in the draft validator would duplicate it.
- **Escalated heads arrive with no notice records.** The claim holds a notice date, not a notice document reference. The row carries the dates; a person links the notice in the case. Until then the pleaded-head check reports it.
- **Locked cost lines are not seeded.** The spec seeds quantum lines; a line whose proof is not linked is left behind and listed, consistent with the submission.
- **Citation counts are stored on new versions only.** Older versions are classified when read.

## Not done

- **Dispute ladder** (determination → DAB or conciliation → Section 21 notice → limitation). The three-year placeholder in `agents/deterministic.py` is unchanged.
- **Multi-select escalation from the register.** The API takes several claims; the screen escalates one, from the Bench.
- **Traverse Board, gate stepper, version diff, exhibit chips inside the pleading text, pickers for raw ids, renamed engine vocabulary, and the split of the two arbitration pages.** None of the Move 8 frontend beyond the citation panel and the scratch choice.
- **Counterclaim section set and agent; opponent pleading upload.**
- **Un-escalating a claim.** There is no route to detach a claim from a case or to unfreeze it.
- **Document-link freeze.** Escalation sets the claim's own freeze fields; it does not call the document-relationship freeze for each link.
- **Playwright e2e.**

## For the owner

- **The new blockers apply to drafts in flight.** A Statement of Claim whose matrix heads lack a notice record or a causal link will be blocked the next time a version is made. That is the intended effect of correction C, but it is a change in behaviour for anyone drafting today.
- **Creating a draft without a case now needs `scratch: true`.** Any integration that posts to `/arbitration/drafts` without a case will get a 422.
