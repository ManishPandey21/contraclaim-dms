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

The server decides. With the Bench off, `/claims/{id}` shows the claim record page as before and the board routes answer 404. The notice-requirement routes and the register's notice column work in every mode.

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

## Not in this phase

- The project form has no field for the party; it is set from the Bench.
- Determinations and the submission pack (Phase 3).
- Skill proposals for delay events, overlap treatment and quantum lines. The delay proposals shown come from the hindrance register; the EOT and claims skills are unpublished, and their review steps are built with Phase 3.
- The contract's own concurrency clause is not looked up and quoted on an overlap; the person states the basis.
- Notice periods are proposed from single clauses. Precedence between a general condition and a document that modifies it is shown (modifying documents are listed first and marked) and decided by the person, not computed.
