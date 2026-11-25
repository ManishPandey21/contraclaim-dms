# Claim → Arbitration Phase 3: status

**Spec:** `docs/superpowers/specs/2026-11-15-claim-arbitration-skills-audit-and-plan.md` (Moves 5 and 6, decision D-01).
**Operator notes:** `docs/CLAIM_BENCH.md`, section "Two Chairs and the Submission Pack".

## Done

| Area | What | Where |
|---|---|---|
| Determinations | Two origins (authored, received); authority and capacity checks; second-person approval; immutable once approved or recorded; revisions | `services/claim_determinations.py`, `models/claim_findings.py`, collection `claim_determinations` |
| Simulations (D-01) | Internal analysis of another party's likely response; labelled; privileged; writes only to its own collection | `services/claim_privileged.py`, collection `claim_privileged` |
| Strategy note | Strengths, weaknesses, residual risks, notes; drafted from the board, edited by a person, versioned | same |
| Privilege wall | One module names the collection; own permission; not on the claim, not in the step store, not in any export; a test scans the source tree | `tests/test_claim_findings.py` |
| Submission | Six sections composed from the locked board; three validators on skill text; per-section staleness; evidence schedule; Word and PDF export with draft mark | `services/claim_submission.py`, collection `claim_submissions` |
| Skill steps | `claim_determination.v1`, `submission_section.v1`, `strategy_note.v1`; record fallback, degraded marking | `models/claim_skill_outputs.py` |
| Permissions | `dms.claim.determine`, `dms.claim.strategy.read`, explicit grant only | `core/permissions.py`, `initial_data/default_roles.py` |
| Routes | 18 routes behind the Bench flag | `routers/claim_findings.py` |
| Screens | Workspace views on the Bench: determinations, submission, internal analysis | `client/src/components/claim-bench/` |
| Old assessment | Button hidden where the Bench is on | `ClaimDetailPage.tsx`, `ClaimsRegisterPage.tsx` |
| Migration | `20261118_0001` indexes | `migrations/` |

## Departures from the spec, and why

- **Approval of a determination is its own small workflow, not the generic `ApprovalService`.** The generic service binds one approval to one resource and assumes the drafter is the resource's creator. A determination needs "nobody who made any finding may decide", per revision, with the history on the record itself. The rule that matters (no self-approval, second person with authority) is enforced and tested.
- **Days and amounts are typed by the person, not taken from the skill or the engines.** The spec lists `days` and `amount` on a finding without saying who supplies them. A determining party may properly arrive at a figure different from the claimant's assessment, so the record's figures are shown beside the finding and never copied in.
- **A simulation's skill output is not stored as a skill step.** The shared step store is not privileged. The executor is called directly and the result is stored only in the privileged collection. The cost is that a simulation has no step record to inspect.
- **The submission is composed by code first; the skill re-expresses.** The spec has one skill step per section. With the skill unpublished that would produce six empty sections. Composing from the record gives a usable document today, and gives the validators something exact to hold skill text to.
- **The old assessment is hidden only where the Bench is on.** The Bench is off by default; removing the button everywhere would take a feature away from every current user.
- **Word export is written directly as a minimal document.** `python-docx` is not in `backend/requirements.txt`. The file has headings, paragraphs and one table, no styles. It has been checked structurally, not opened in Word.
- **No unique index stops two open authored determinations.** The service refuses a second; the index would need `$in` in a partial filter, which older MongoDB versions reject.

## Not done

- **The determination letter.** "Feeds a FIDIC 3.7-style letter through the contract-letters skill" is a link to the Letter Bench started from the claim. The findings are not passed into it.
- **Request for information** is likewise a link to the Letter Bench, not a letter pre-filled with the list.
- **Deadlines on information required** exist in the model and the API; the screen captures the item and the consequence, not a date.
- **Editing a submission section by hand.** Sections are composed and re-composed, not edited.
- **Printing a simulation.** The label is on every panel on screen; there is no print or export of privileged material at all.
- **Skill review steps for delay events, overlaps and quantum lines** (carried from Phase 2). Still not built: with both skills unpublished each would return only the record fallback.
- **Playwright e2e.** Screens were checked in a browser against a mocked API.

## For the owner

- **Who holds the two new permissions.** Nothing grants them in an existing database. Until someone does, determinations cannot be authored and Internal analysis cannot be opened.
- **Super Admin and privileged material.** The policy service admits a platform Super Admin to any permission, so a Super Admin acting inside an organisation can open its Internal analysis. If that should not be so, it needs a rule like the one for skill approvals (a named role, not a permission).
- **Whether an approved determination should also need the legal role**, as pleadings do.
