# Letter Bench: factual grounding — what is in this release and what comes next

Status on 2026-11-28, release branch `release/contraclaim-rc1-letterbench` (PR #116).
The Bench deploys with `LETTER_BENCH_ROLLOUT_MODE=off`. This document separates
the controls that make **activation** safe from the enhancements that come
after it, and is honest about what the current controls do not do.

## The root cause

The Bench writes each reply paragraph from the incoming assertion, the decided
position, the user's direction and the **numbers and titles** of the clauses the
sender cited. It is given no clause text, no project documents, no prior
correspondence, no site records and no programme. A model asked to justify a
position with nothing to cite will invent the justification. The 2026-11-28
real-model run showed exactly that ("the site records do not substantiate the
delay", "our evaluation indicates the design was compliant").

## In this release (minimum safe activation controls)

| Control | Where | What it does |
|---|---|---|
| Grounding rules in the reply prompt | `prompts.py` (compose template) | Separates the user's position from its reasons; forbids asserting reviews, findings, records, determinations, notices, dates or amounts not in the sources; requires the sender's statements to be attributed; gives two allowed alternatives: require substantiation, or a specific `[CONFIRM: what and where]` |
| Independent grounding check | `grounding.py`, run in the compose pipeline | Splits the composed letter into sentences and classifies each (verified fact, a party's assertion, Engineer's finding, contractual provision or interpretation, proposed argument, confirmation request, non-factual). A factual sentence is supported only by an independent source passage carrying the same specifics and most of the same words. The sender's own letter backs only attributed statements. A citation alone is not support. Disagreeing numbers are "contradicted". A clause or correspondence reference found in no source is unsupported. Each statement records its source, passage, document, page and any contradiction. No model score is used |
| Approval gate | `bench_approval_blockers`, `approve_stage` | A draft is saved and editable whatever the check finds, but cannot be approved while a material statement is unsupported or contradicted, a `[CONFIRM: ...]` placeholder remains, the reply was edited after it was composed, or the board is missing. Export and issue already require an approved draft |
| Reviewer confirmation with a recorded basis | `patch_paragraph` (`confirmation_basis`), Bench UI | A reviewer can confirm facts they wrote into a paragraph from their own knowledge; the basis, who and when are stored; any later edit clears it; the recomposed statement shows as `user_confirmed` with the basis |
| Reviewer view | `GroundingPanel`, `ReplyComposer` | Unsupported/contradicted statements first, with supporting passage, link to the source document and page, contradicting passage and basis; each flagged paragraph offers Re-run (regenerate that paragraph) |
| Skill administration needs explicit permission | `routers/skills.py` | `drafting.admin` alone no longer lets a role import, publish, deprecate or pin system skills |

### Measured performance of the check (synthetic data)

| Measure | Result |
|---|---|
| Recall on a held-out set of 15 unsupported sentences, written after tuning and scored once | 12/15 (80%) |
| False positives on 15 held-out legitimate sentences | 0/15 |
| Tuned set (25 unsupported / 23 legitimate) | 25/25, 0/23 — optimistic, it was tuned on this |
| Real-model drafts (39 replies to 32 synthetic letters): statements flagged | 3: 2 real (the reply said the sender cited no clause when it did), 1 own-side fact supported only loosely by evidence |
| Real-model drafts: invented external facts let through, by manual review of every passed sentence with event wording | none found; 1 party misattribution not detected |

**What these numbers do not show.** The check is lexical. It misses some
sentences written in forms it does not recognise ("did not mobilise ... until",
"were found to be ..."), it cannot tell which party a sentence names, and it
cannot judge whether a passage that shares words with a sentence actually means
the same thing. A small synthetic evaluation does not prove hallucinations are
eliminated. Until phase 2 lands, **every factual statement in a Bench reply
must be checked by the reviewer before approval**, and activation should start
in `canary` for one organisation.

## Phase 2 — before `primary` (bounded, about 2–3 weeks of work)

1. **Evidence retrieval into the Bench (the root-cause fix).** Before the
   paragraph steps run, build the governed source ledger the run pipeline
   already builds (`DraftContextBuilder`: authorised contract clauses with text,
   selected documents, prior correspondence on the thread, graph threads; all
   organisation/project scoped and publication-policy checked), select the
   passages relevant to each assertion, and pass them into the evaluation and
   compose prompts with `[S#]` tokens. The model can then cite, and the check
   matches citations to the same ledger. Needs: cross-organisation retrieval
   tests (a document of another organisation or project must never reach the
   prompt), a token budget per assertion, and prompt-version tracking.
2. **Semantic verification of supported statements.** For statements the
   lexical check marks supported or partly supported, run an entailment check
   (NLI model or an LLM judge with a fixed rubric) of the cited passage against
   the sentence; disagreement downgrades to `partially_supported` or
   `unsupported`. Its output is a verdict to review, never proof, and it is
   logged with the model and prompt version.
3. **Party and attribution check.** Know who wrote the incoming letter and the
   drafting side; flag a sentence that attributes a statement to a party who did
   not make it.
4. **Statement-level reviewer actions.** Confirm, replace or reject a single
   statement rather than a whole paragraph; search the project knowledge base
   from the statement; record confirmations in the audit trail as events.
5. **Evaluation at production scale.** At least 100 anonymised real letters
   (with consent), double-annotated, run against the configured production
   model with repeats; report grounding accuracy, unsupported-statement recall,
   false-positive rate, citation accuracy, drafting quality (rubric, two
   reviewers), latency and cost; regression-gate the check on the annotated set
   in CI.

## Phase 3 — enhancements

- Contradiction detection across documents (two sources that disagree are shown
  side by side, not only a sentence that disagrees with one source).
- Learning from reviewer confirmations (frequently confirmed facts proposed as
  project facts, with the confirming basis).
- Grounding for the earlier drafting wizard (v2), which shares the validator but
  not this check.

## Activation checklist (summary)

1. Deploy dark (mode `off`), verify, promote — release runbook.
2. Review the roles that hold `drafting.skill.publish` / `.bind` by name.
3. `canary` for one organisation; reviewers briefed that every factual statement
   must be checked; collect the grounding results of real use.
4. Phase 2 items 1–3 before `primary`.
