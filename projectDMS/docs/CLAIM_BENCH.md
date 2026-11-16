# Claim Bench and Notice Clock

Phase 1 of `docs/superpowers/specs/2026-11-15-claim-arbitration-skills-audit-and-plan.md` (Moves 1 and 2).

## What it is

**Claim Bench.** A claim is worked as a board of heads. Each head is tested on five elements: event, notice, contractual basis, causation, quantum. Three roles are kept apart:

| Who | Does what |
|---|---|
| Records | Decide facts: whether notice was served and when, which cited clauses exist in the project's clause index, what days and amount are recorded. |
| Claims skill | Proposes a state and a reason for each element, when a published version exists. Its proposal is held to the recorded facts. |
| A person | Makes every call. The board locks only when a person has decided all five elements and the classification of every head. |

**Notice Clock.** The notice period for a claim type is taken from the project's own contract, with the clause wording it is read from, and is verified by a person. Records that indicate a claim nobody has raised (a critical-path hindrance on the Employer, a letter read as raising a claim or delay) are listed with the clock each is already on.

## While the claims skill is unpublished

The claims skill is installed as a draft and needs technical and legal approval before it can be published (`/skills`). Until then:

- "Test this head" checks the head against the records only. No model is called.
- The step is stored as degraded with `Skill not used: No published version`, and the Bench shows "Not in use · Awaiting legal approval".
- No element is ever proposed as "supported". Only a person can record that.

Nothing else about the Bench depends on the skill.

**The approvals a skill needs are set by the server, not by its import (Phase 4b, 4B-04).** An import can add reviews to a manifest but never remove one. The claims, EOT, pleadings and hearing packs always need the technical and legal reviews they are bundled with. Every other skill version, including the next version of a published letter pack and any new key imported, needs at least the independent legal sign-off (`senior_legal_approver`). An approval is bound to the text (`content_hash`), the manifest (`policy_hash`) and the evaluation result including who ran it. A change to any of them makes the approval stale. A draft that has approvals cannot be re-imported under the same version number. An evaluation report may not set its own threshold below 0.85 (`SKILL_EVAL_MIN_THRESHOLD` may raise it, never lower it).

## Rules worth knowing

- **Which party.** A head is tested from the position of the party the project acts for (`Project.contractual_capacity`). If that is not set, the test is refused and the Bench asks. Set it on the Bench, or with `PATCH /api/projects/{id}/contractual-capacity` (`projects:update`).
- **Notice period sources**, in order of preference: a requirement recorded from the contract (`contract_verified` once a person has verified it, `contract_unverified` before), an organisation rule (`sla_rules`), an assumed typical figure. Only `contract_verified` is reported as the contract's period. Everything else is worded as a figure to check.
- **A requirement must quote its clause.** The quoted wording has to state the number of days recorded. A new requirement supersedes the current one, which is kept as history.
- **Signals write nothing.** Listing signals reads the hindrance register and the Letter Bench boards. A potential claim is created only when a person raises it, once per source.
- **Amount follows the heads; days do not.** The register's amount is the sum of head amounts. Days are never summed, because heads can overlap in time.
- **Edits after a test.** Changing a head, or the claim's dates or type, marks the affected tests stale and reopens a locked board. People's decisions are kept.
- **Frozen evidence** makes the board read-only.

## Flags

| Variable | Values | Default |
|---|---|---|
| `CLAIM_BENCH_ROLLOUT_MODE` | `off`, `canary`, `primary` | `off` |
| `CLAIM_BENCH_CANARY_TENANT_IDS` | comma-separated organisation ids | empty |

The server decides. With the Bench off, `/claims/{id}` shows the claim record page as before, and the board routes and the two signal routes (`/claims/bench/signals`, `/claims/bench/potential`) answer 404. The notice-requirement routes and the register's notice column work in every mode.

Any other value, including `shadow` (named in the original plan but never built), stops the backend at startup with "CLAIM_BENCH_ROLLOUT_MODE must be off, canary, or primary". Before this check an unknown value silently meant `off`.

**Rollback:** set `CLAIM_BENCH_ROLLOUT_MODE=off` and restart the backend. Boards already created stay stored on their claims and reappear when the flag is turned back on.

## Migration

`20261116_0001 claim_bench_indexes` adds indexes only: idempotent skill steps, one notice period in force per project and claim type, one claim per origin record. No claim is changed. A claim gets its board the first time someone opens the Bench on it, with one head carried over from its register fields.

## Permissions

| Action | Permission |
|---|---|
| Read a board, signals, notice requirement | `dms.claim.view` |
| Read notice-period candidates (contract wording) | `dms.claim.view` and `dms.contract.clause.read` |
| Open a board, edit heads, decide, lock | `dms.claim.edit` |
| Test a head | `dms.claim.assess` |
| Record a notice requirement | `dms.claim.edit` |
| Verify a notice requirement | `dms.claim.manage` |
| Raise a potential claim | `dms.claim.create`, plus read access to the source (`dms.hindrance.view` or `drafting.request.view`) |

## Delay Lab and Quantum Builder (Phase 2)

Each head has three tabs: Elements, Delay, Quantum.

**Delay.** A head links to hindrances and to key-date EOT submissions and determinations. Linking writes nothing to those registers. A link is pinned to the record as it was; if the record later changes the Lab says so and keeps using the linked version until someone chooses "Use latest".

- Only an event a person has decided is **critical**, with a decided risk owner, is counted. What the hindrance register records is shown as a proposal.
- Days are calendar days, counted inclusively. An event with no end date runs to the "analysis runs to" date.
- Where critical Employer delay and critical Contractor delay fall on the same days, that is an **overlap**. It is never deducted or added automatically. Each overlap needs a treatment (time no cost, time and cost, apportioned, no time, not true concurrency) and a stated basis. Until then the extension is shown as a range and the board cannot be locked.
- A finding belongs to the overlap it was made for. If the events change so that the overlap no longer exists, the finding is dropped, not carried over.
- **Already granted** days are read from linked determinations (the item for the milestone named when linking). They cannot be typed. A superseded, pending or rejected item is not counted, and the Lab says why.
- Neutral events give time only if someone records that the contract does, with its basis.
- Methods offered: as-planned vs as-built, and windows. Time impact analysis and collapsed as-built are listed as unavailable: the product holds milestones, not a programme network.

**Quantum.** A head's amount becomes the total of its lines once it has any. Each line is locked until its proof is linked:

| Kind of cost | Unlocks when |
|---|---|
| Direct, disruption | At least one supporting record |
| Prolongation | A period, every day of it inside a period decided as compensable, and a supporting record |
| Overheads by formula | A formula chosen, a period inside compensable periods, and a record of actual loss |
| Loss of profit | A record of work available and declined |
| Interest | A stated basis |

Compensable periods come from the delay decisions: Employer critical delay with no Contractor delay, plus any overlap decided as "time and cost" or "not true concurrency". A record must be a document already linked to the claim. A locked line stays on the board with what would unlock it, and is left out of the supported total.

Migration `20261117_0001` indexes `claim_head_links`. Linking needs `dms.claim.edit` and read access to the record (`dms.hindrance.view` or `dms.keydate.view`).

## Two Chairs and the Submission Pack (Phase 3)

Behind the same flag as the Bench. When the Bench is on, the claim page gains a row of views: Board, Determination(s), Submission, Internal analysis.

**Determinations.** An official finding on the claim, head by head: made out in full, in part, not at all, or cannot yet be determined, with reasons and, where it cannot be determined, the information required.

- **Made here** only in a project that acts as Engineer or Employer, by a person holding `dms.claim.determine`. A second person with that permission approves it; nobody who made a finding can approve or return it. Approved, it does not change. "Start a new revision" creates a new record that supersedes it.
- **Received** from the other side: any workspace records one, tied to the document it arrived in (which must already be linked to the claim). Once recorded it does not change.
- Days and amounts are typed by the person. The figures on the record (days claimed, the delay assessment's figure or range, amount claimed, amount with and without proof) are shown beside each head and never copied in.
- "Propose findings" puts a proposal beside each head. With no published claims skill it is the record's: it lists what the board leaves open and never proposes an entitlement. A published skill's proposal cannot be "made out in full" over an element the board does not hold as supported, and never carries a figure.
- A determination reads the board. It writes nothing to the claim, its heads, its links or another determination.

**Internal analysis** (privileged). Needs `dms.claim.strategy.read` to see at all, and claim edit rights as well to change.

- **Simulation**: how another party is likely to respond. Every response and screen carries "Internal analysis — not a determination". With no published skill it predicts nothing and lists what the board shows to be open to challenge.
- **Strategy note**: strengths, weaknesses, residual risks, free notes. One per claim. "Draft from the board" replaces the three lists and keeps the notes.
- Both live in the `claim_privileged` collection and nowhere else. One module (`services/claim_privileged.py`) names that collection; a test fails if another starts to. They are not on the claim record, not in the shared skill-step store, not in the evidence bundle, not in the submission or its exports, and nothing in the arbitration module reads them.

**Submission.** A Contractor's workspace only, and only from a **locked** board.

- Six sections: executive summary, contractual basis, chronology and evidence, delay analysis, quantum, relief and reservation of rights. Plus an evidence schedule: the claim's linked documents in date order, numbered E-1 onwards.
- Each section is composed from the record by code. With a published skill, the skill may re-express a section; its text is used only if every number in it is on the record, every clause it names is one the heads cite, and the reservation of rights survives. Otherwise the record composition is used and the reason shown.
- What never goes in: a notice period not verified against the contract (the submission then gives only the date notice was served), a cost line whose proof is not linked, clause wording that is not in the clause index, and anything privileged.
- "For you, not in the document" notes say what was left out and why. They are not exported.
- A section goes stale when the record it was composed from changes. Re-composing redoes only stale sections. Export is refused while any section is stale or the board is unlocked.
- Export is Word or PDF. Until the claim is approved through its existing approval workflow, the export is headed "DRAFT. Not approved for issue." Each export is written to the audit trail.

**The old AI assessment** button is hidden on the register and the record page where the Bench is on. The endpoint and its history are unchanged; where the Bench is off nothing changes.

**Permissions (revised by Phase 4b, 4B-10).** `dms.claim.determine` and `dms.claim.strategy.read` are granted only deliberately, by name, on a role an administrator assigns:

- They are in **no** default role. The earlier release definitions gave them to `orgadmin` and `projectadmin`, and role-contract alignment would have pushed them to existing role documents. That is withdrawn, and the seed catalogue now fails validation if any default role carries them.
- **Nothing stands in for them.** Super Admin status, the `*` wildcard and `dms.admin` (which `orgadmin`, `projectadmin` and `contractmgr_org` hold) each used to pass these checks. PolicyService now checks them against the user's own role documents, without the permission cache, so a revocation takes effect on the next request. A refusal is audited with the reason `explicit_grant_required`.
- Until an administrator grants them, nobody can author or approve a determination or open Internal analysis. Ordinary claim work for admins is unchanged.

Migration `20261118_0001` adds indexes only.

## Escalation to arbitration and draft evidence controls (Phase 4)

**Escalate to arbitration** (Bench header; behind the Bench flag). Needs `dms.claim.manage` on the claim and `dms.arbitration.create` (new case) or `dms.arbitration.edit` (existing case).

- A confirmation sheet first shows what would be carried, what is weak on the record, and what is left behind and why. Nothing changes until "Escalate".
- Carried: heads → claim matrix; notice dates → notice compliance; cost lines with proof → quantum annexures; key dates and delay events → chronology; linked documents → document index; overlaps with no decided treatment → issue matrix as open issues with no figure.
- Never carried: a notice period not verified against the contract (only the dates go), cost lines without proof, simulations and the strategy note.
- Every row is created through the case workspace's own row path, so it starts as **needs review** and withdraws any readiness approval already given on the case, exactly as a row added by hand does. Escalation approves nothing.
- Rows carry their origin (claim, head). Escalating the same claim again adds nothing twice.
- The claim records its case, its evidence is frozen (the board becomes read-only), and its status becomes `disputed` where the claim lifecycle allows that move.
- A case built this way names its claims. Its claim agent reads those claims only; a case with none named keeps reading the project's register as before.

**Evidence controls on a pleading draft** (arbitration module; these apply whatever the Bench flag says).

- Each citation in a draft version is **verified** (in the ledger and admissible), **unverified** (in the ledger but user-supplied or not yet verified) or **unresolved** (not in the ledger). The draft page shows the three counts and each citation's state in place of the bare "Sources" figure. One function computes them for the screen and for the approval gate.
- A cited source that is unverified is now named in the approval blockers. (Unresolved citations and unverified ledger rows already blocked approval.)
- In a Statement of Claim, each pleaded head must carry the provision relied on, the event, the causal link, quantified relief and a notice record, and records supporting any figure pleaded. Each missing item is a named approval blocker.
- (Phase 4b, 4B-33) These elements now reach the check: the source ledger carries each approved head's provision, event, causal link and relief. Before, every approved head was reported as missing them. A Statement of Claim is also blocked when the case has no approved claim head at all, or when heads are still awaiting approval. The legal-review step of the governed workflow blocks on these too; it used to let them through.
- (Phase 4b) An arbitration case created in the case workspace can now be opened and listed. On a real MongoDB it could not: the case queries did not match a stored `deleted_at: null`.
- A draft with no case is a **scratch draft** and must be asked for by name: the API refuses to create a case-less draft without `scratch: true`, and the form no longer defaults to "none". A scratch draft could already be neither approved nor exported for filing; that is unchanged. Attaching it to a case clears the flag and puts it under every case gate. Migration `20261119_0001` marks existing case-less drafts as scratch; none is deleted or changes status.

**Pleadings skill.** When a version of `indian-infra-arbitration-pleadings` is **published** (technical review plus independent legal approval) and the project acts as Contractor or Employer, its text is placed in front of the drafting prompt and its key, version and hash are recorded on the draft version. The task prompt, its hard rules, the reconciliation of citations and the validator are unchanged. While the skill is unpublished, which is the case today, the prompt is byte-for-byte what it was. No engine flag, rollout mode or approval gate is touched.

## Not in this phase

- The project form has no field for the party; it is set from the Bench.
- Skill proposals for delay events, overlap treatment and quantum lines. The delay proposals shown come from the hindrance register.
- The contract's own concurrency clause is not looked up and quoted on an overlap; the person states the basis.
- Notice periods are proposed from single clauses. Precedence between a general condition and a document that modifies it is shown (modifying documents are listed first and marked) and decided by the person, not computed.
