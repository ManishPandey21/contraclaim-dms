# Key Dates — Contractor EOT Submissions vs Employer EOT Grants

**Status:** Plan only. No code changed in producing this document.
**Date:** 2026-08-13
**Predecessor:** [KEY_DATE_EOT_REVISION_IMPLEMENTATION_PLAN.md](KEY_DATE_EOT_REVISION_IMPLEMENTATION_PLAN.md) (shipped 2026-08-11)
**Source requirement:** `contraclaim_key_dates_eot_revision_workflow_prompt.md` + the 2026-08-13 request
to record Contractor EOT submissions and Employer EOT grants as independent, many-to-many events.

---

## 1. Headline finding

**The requested workflow is ~80% already implemented.** A project/contract-level baseline +
successive-EOT workflow shipped on 2026-08-11 and is live in the codebase. This plan is a **delta**
on top of it, not a greenfield design.

| Capability | Where it lives today |
|---|---|
| `KeyDateBaseline`, `EOTSubmission(+Item)`, `EOTDetermination(+Item)` models | [key_date.py:267-436](../backend/rbac_backend/models/key_date.py) |
| Lock-submission vs freeze-determination as two distinct controls | [key_date_revision_service.py:407,660](../backend/rbac_backend/services/key_date_revision_service.py) |
| Atomic unbounded `EOT-N` allocation (`$inc next_revision_number`) | [key_date_revision_service.py:283-292](../backend/rbac_backend/services/key_date_revision_service.py) |
| `contractual_date_at_submission` snapshot per item | [key_date_revision_service.py:263](../backend/rbac_backend/services/key_date_revision_service.py) |
| Many-to-many determination → submissions (`eot_submission_ids: List[str]`) | [key_date.py:366,405](../backend/rbac_backend/models/key_date.py) |
| Milestone-level partial grants + carry-forward of undetermined milestones | [key_date_revision_service.py:690-707](../backend/rbac_backend/services/key_date_revision_service.py) |
| 20 routes: freeze, create/update/lock, determine/freeze, CSV preview+import, per-revision & dynamic history export | [key_dates.py:370-800](../backend/rbac_backend/routers/key_dates.py) |
| Permissions `dms.keydate.baseline.freeze` / `.eot.lock_submission` / `.eot.determine` / `.eot.freeze_determination` | [permissions.py:243-255](../backend/rbac_backend/core/permissions.py) |
| Frozen-baseline immutability guard on every legacy write path | [key_date_service.py:184-204](../backend/rbac_backend/services/key_date_service.py) |
| Indexes incl. unique `(project_id, contract_id, revision_number)` | [v20260811_0001](../backend/rbac_backend/migrations/v20260811_0001_key_date_eot_revision_workflow.py) |
| UI: submission + determination dialogs, CSV import, revision history | [KeyDateRevisionWorkflow.tsx](../client/src/components/key-dates/KeyDateRevisionWorkflow.tsx) (471 lines) |
| Tests incl. "EOT-2 while EOT-1 pending → later partial grant" | [test_key_date_revision_workflow.py:160](../backend/rbac_backend/tests/test_key_date_revision_workflow.py) |

### 1.1 Requirement conformance

| Requirement (2026-08-13 request) | Status |
|---|---|
| Multiple successive EOT-1…EOT-N, no hard limit | **Met** |
| Every submission recorded separately with proposed dates, submission date, details | **Met** |
| Employer grant is a separate event from submission | **Met** |
| Employer not required to grant against every application | **Met** |
| One Employer decision covering one *or more* submissions | **Met** |
| Multiple Employer grants over project life | **Met** |
| Chronology preserved, nothing overwritten | **Met** |
| Contractor-requested vs Employer-granted dates distinguished at every stage | **Met** |
| **Link to the uploaded EOT application/document** | **NOT MET — G1** |
| **Retain submissions marked … superseded … or not separately determined** | **NOT MET — G4** |
| Employer grant issued with no corresponding submission | **NOT MET — G2** |
| Each submission independently traceable inside a *combined* determination | **NOT MET — G3** |

---

## 2. Defects and gaps found

Each is evidenced against current code. G5 and G6 are live correctness defects, not missing features.

### G1 — No document/letter link on submissions or determinations
`EOTSubmission` carries `eot_reference` and `contractor_letter_reference` as **free text only**
([key_date.py:342-345](../backend/rbac_backend/models/key_date.py)). Neither entity has
`linked_document_ids` / `linked_letter_ids`, even though `KeyDateMilestone` already has both
([key_date.py:88-89](../backend/rbac_backend/models/key_date.py)). The uploaded EOT application PDF
is therefore unreachable from the EOT record.

### G2 — A determination cannot exist without a submission
`eot_submission_ids: List[str] = Field(..., min_length=1)`
([key_date.py:366](../backend/rbac_backend/models/key_date.py)), reinforced by
`raise KeyDateError("Select at least one EOT submission for determination")`
([key_date_revision_service.py:509](../backend/rbac_backend/services/key_date_revision_service.py)).
An Engineer's own-initiative extension (FIDIC 8.4/8.5) or a global determination cannot be recorded
without fabricating a Contractor submission, which corrupts the submission history.

### G3 — Combined determinations silently drop earlier claims
```python
for submission in sorted(submissions, key=lambda row: int(row.get("revision_number") or 0)):
    for item in (full or {}).get("items", []):
        covered_by_ref[_clean_ref(item.get("milestone_ref")).casefold()] = (submission, item)
```
[key_date_revision_service.py:450-455](../backend/rbac_backend/services/key_date_revision_service.py) —
the dict is keyed on milestone ref alone and iterated in ascending revision order, so **the last
submission wins**. For a determination covering EOT-1 and EOT-2 that both claim KD-01, only EOT-2's
`submitted_date` and `claimed_extension_days` are snapshotted onto the determination item. EOT-1's
claim vanishes from the determination record, and nothing states which submission the granted date
answers.

Note the constraint this must respect: `key_date_eot_determination_items` has a **unique**
`(determination_id, key_date_id)` index
([v20260811_0001:76](../backend/rbac_backend/migrations/v20260811_0001_key_date_eot_revision_workflow.py)),
so one determination physically cannot hold two rows for the same milestone. Attribution must
therefore live *inside* the single item, not as extra rows.

### G4 — No per-submission determination outcome
`workflow_summary` only produces aggregate counters (`pending_determinations`,
`open_eot_submissions`)
([key_date_revision_service.py:769-782](../backend/rbac_backend/services/key_date_revision_service.py)).
There is no per-submission answer to "was EOT-1 accepted, partially accepted, rejected, superseded,
or never separately determined?". `EOTSubmissionStatus.SUPERSEDED` exists in the enum
([key_date.py:51](../backend/rbac_backend/models/key_date.py)) but **no code path ever sets it**.

### G5 — Out-of-order determination freezes regress the contractual date *(live defect)*
`freeze_determination` writes the grant date straight onto the milestone with no comparison against
what is already in force:
```python
await self.db.key_date_milestones.update_one(
    {"_id": str(item.get("key_date_id"))},
    {"$set": {"current_approved_key_date": grant_date, ...}},
)
```
[key_date_revision_service.py:697-707](../backend/rbac_backend/services/key_date_revision_service.py)

Failure scenario: EOT-2 is determined first and grants KD-01 to 01-Sep-2026. EOT-1 is determined
later (as the source spec §11 explicitly requires to be supported) and grants KD-01 to 25-May-2026.
Freezing EOT-1 **pulls the contractual date backward to 25-May**, silently shortening the
Contractor's time and invalidating every overdue/alert calculation downstream. The same
order-dependence affects the summary label, which picks `effective[-1]` after sorting by `frozen_at`
([key_date_revision_service.py:749-753](../backend/rbac_backend/services/key_date_revision_service.py)).

### G6 — Register status can reflect an *unfrozen* determination *(live defect)*
```python
linked.sort(key=lambda row: row.get("created_at") or datetime.min)
latest_status = linked[-1].get("status") if linked else (...)
```
[key_date_service.py:335-339](../backend/rbac_backend/services/key_date_service.py) — `linked` is
every determination covering the submission, with **no `frozen_at` filter**. A draft determination
sitting at `under_review` or even `granted` but not yet frozen surfaces as the milestone's
`latest_eot_status` on the register. This contradicts the core rule that a pending/unfrozen
determination must not present as contractual status.

### G7 — Dead duplicate guard
`KeyDateRevisionService.assert_baseline_editable`
([key_date_revision_service.py:113](../backend/rbac_backend/services/key_date_revision_service.py))
is **never called anywhere**. The real enforcement is
`KeyDateService._assert_original_baseline_editable`
([key_date_service.py:184](../backend/rbac_backend/services/key_date_service.py)). Two methods with
near-identical names, one live and one inert, is a trap: a future write path may call the dead one
and ship with no guard at all.

---

## 3. Confirmed decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Allow standalone Employer determinations**, gated by an explicit `origin` discriminator and a mandatory reason | An unlinked grant becomes a deliberate, auditable act instead of a data-entry slip |
| D2 | **Link existing DMS document/letter IDs** (`linked_document_ids` + `linked_letter_ids`); no new upload path | Reuses the `KeyDateMilestone` pattern; avoids a second upload entry point that would need its own `duplicate_detection.precheck_upload` |
| D3 | **Per-item `source_submission_id` + full `covered_claims` array** | Only shape compatible with the unique `(determination_id, key_date_id)` index; keeps every covered claim traceable |
| D4 | **Derive the per-submission outcome at read time** | No denormalised field to drift — the failure mode this repo has been bitten by before |
| D5 | **Recompute effective dates from *all* frozen determinations on every freeze** | Order-independent, deterministic, self-healing; fixes G5 at the class level rather than the instance |
| D6 | `source_submission_id` is **user-named, defaulting to the earliest covered submission that claimed that milestone and is not yet determined** | Matches how an Employer answers the oldest outstanding claim; keeps CSV import unblocked |
| D7 | **Submission supersession is an explicit act only**, never inferred, with mandatory reason + audit | A later EOT for the same milestone is usually an *additional* claim, not a replacement (source spec §11) |
| D8 | **Plan legacy deprecation, do not remove yet** | Keeps blast radius small; legacy removal gets its own gated workstream |

---

## 4. Data model changes

All additions are optional fields — existing documents remain valid without backfill for reads.

### 4.1 `EOTSubmission`
```
linked_document_ids: List[str] = []          # D2
linked_letter_ids:   List[str] = []          # D2
superseded_by_submission_id: Optional[str]   # D7, explicit only
superseded_reason:          Optional[str]    # D7, mandatory when superseding
superseded_at:              Optional[datetime]
superseded_by:              Optional[str]    # actor
```

### 4.2 `EOTDetermination`
```
linked_document_ids: List[str] = []
linked_letter_ids:   List[str] = []
origin: EOTDeterminationOrigin = CONTRACTOR_SUBMISSION   # D1
```
`eot_submission_ids` drops `min_length=1`. A model validator enforces:

- `origin == contractor_submission` → at least one submission id (current behaviour preserved)
- `origin == employer_initiated` → `eot_submission_ids` must be empty **and** `remarks` non-blank

New enum:
```python
class EOTDeterminationOrigin(str, Enum):
    CONTRACTOR_SUBMISSION = "contractor_submission"
    EMPLOYER_INITIATED = "employer_initiated"
```

### 4.3 `EOTDeterminationItem`
```
source_submission_id: Optional[str]      # D3/D6 — null for employer_initiated
covered_claims: List[CoveredClaim] = []  # D3
```
```python
class CoveredClaim(BaseModel):
    eot_submission_id: str
    revision_label: str          # "EOT-1"
    submitted_date: Optional[datetime]
    claimed_extension_days: Optional[int]
```
`submitted_date` / `claimed_extension_days` stay on the item as the *primary* claim (the one from
`source_submission_id`) so existing exports and the frozen-determination validation keep working
unchanged.

### 4.4 Derived, not persisted — `SubmissionOutcome`
Computed per submission on read (D4), in this precedence order:

| Outcome | Condition |
|---|---|
| `withdrawn` / `draft` | mirrors `EOTSubmissionStatus` |
| `superseded` | `superseded_by_submission_id` is set (explicit, D7) |
| `accepted` | a frozen determination covers it and **every** claimed milestone got `granted` |
| `partially_accepted` | frozen coverage with a mix of `granted` / `partially_granted` / `rejected` / `no_change` |
| `rejected` | frozen coverage and no milestone received an effective grant |
| `not_separately_determined` | locked, no frozen determination covers it, **but** a later submission overlapping its milestone set has been determined |
| `pending` | locked/submitted, no frozen coverage, nothing later determined |

`not_separately_determined` is the case the request calls out explicitly: EOT-1 and EOT-2 are
outstanding, the Employer determines only after EOT-3, and EOT-1/EOT-2 were never answered in their
own right. Deriving it (rather than storing it) means it self-corrects if EOT-1 is determined later.

---

## 5. Effective contractual date — recomputation (D5, fixes G5)

Replace the blind write in `freeze_determination` with a pure recomputation over the full frozen
set. New method `KeyDateRevisionService.recompute_effective_dates(org, project, contract)`:

1. Seed every milestone's effective date from the **frozen baseline snapshot**
   (`key_date_baselines.items[].original_contractual_date`) — never from the mutable milestone row.
2. Load all determinations where `frozen_at is not None` and `status in FINAL_DETERMINATION_STATUSES`.
3. **Exclude** any determination whose `_id` appears in another frozen determination's
   `supersedes_determination_ids`.
4. Sort ascending by `(determination_date, frozen_at, _id)`. `determination_date` is the
   *contractual* event; `frozen_at` and `_id` are deterministic tie-breakers only.
5. Apply in order: items with `determination_result in {granted, partially_granted}` **and** a grant
   date set the milestone's effective date. `rejected` / `no_change` / `pending` write nothing —
   the previous effective date carries forward (source spec §14, §30).
6. Persist `current_approved_key_date`, `effective_determination_id`, `effective_determination_date`
   per milestone; write only where the value actually changed.

Properties this buys:

- **Order-independent.** Freezing EOT-1 after EOT-2 re-sorts by determination date, so EOT-2's later
  determination still governs. G5 cannot recur.
- **Idempotent.** Re-running converges to the same state — safe to expose as a repair endpoint and
  safe to call from a migration.
- **Fail-visible.** A milestone with no baseline snapshot row raises rather than silently defaulting,
  consistent with the house rule against silent success.

Callers: `freeze_determination` (after the freeze transitions), the new admin recompute endpoint,
and the migration backfill.

`workflow_summary.current_contractual_baseline` derives its label from the same ordered set, so the
header can no longer disagree with the register.

---

## 6. API changes

Follows existing `/api/key-dates/...` conventions.

| Method | Path | Change |
|---|---|---|
| `POST` | `/key-dates/eot-submissions` | accepts `linked_document_ids`, `linked_letter_ids` |
| `PUT` | `/key-dates/eot-submissions/{id}` | same (blocked once locked, as today) |
| `POST` | `/key-dates/eot-submissions/{id}/supersede` | **new** — body `{superseded_by_submission_id, reason}`; D7 |
| `POST` | `/key-dates/eot-determinations` | accepts `origin`, `linked_document_ids`, `linked_letter_ids`; `eot_submission_ids` may be empty when `origin=employer_initiated`; items accept `source_submission_id` |
| `PUT` | `/key-dates/eot-determinations/{id}` | same |
| `POST` | `/key-dates/recompute-contractual` | **new** — idempotent repair, `dms.keydate.manage` |
| `GET` | `/key-dates/workflow` \| `/revisions` | response gains per-submission `determination_outcome`, `determining_determination_ids`, `linked_document_ids`; summary gains `not_separately_determined_count`, `employer_initiated_count` |

Validation on the supersede route: target and superseding submission must share project + contract,
the superseding submission must have a **strictly higher** `revision_number`, the target must be
`locked`, and `reason` must be non-blank. Superseding is not permitted once a frozen determination
already covers the target — that would rewrite settled contractual history (§40).

**Required after merge** (CLAUDE.md rule — `test_route_authz_gate_evidence.py` /
`test_route_control_manifest.py` fail otherwise):

```bash
backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json
```

---

## 7. RBAC

Reuse existing permissions; add one.

| Action | Permission |
|---|---|
| Create/edit submission, attach documents | `dms.keydate.eot_submit` (existing) |
| Lock submission | `dms.keydate.eot.lock_submission` (existing) |
| Create/edit determination, incl. employer-initiated | `dms.keydate.eot.determine` (existing) |
| Freeze determination | `dms.keydate.eot.freeze_determination` (existing) |
| **Supersede a locked submission** | `dms.keydate.eot.supersede` (**new**, elevated — §40 correction control) |
| Recompute contractual dates | `dms.keydate.manage` (existing) |

Adding a permission requires updating `core/permissions.py`, `initial_data/default_permissions.py`
and `contracts/permission_contract.json`. Every route continues to go through
`_authorize_workflow_scope` / `PolicyService.authorize`; no new tenant-scoping mechanism (§39).
Document links are validated server-side to belong to the same organisation **and** project as the
EOT — an unvalidated `linked_document_ids` is a cross-tenant read primitive.

---

## 8. UI changes

[KeyDateRevisionWorkflow.tsx](../client/src/components/key-dates/KeyDateRevisionWorkflow.tsx) and
[key-dates-api.ts](../client/src/services/key-dates-api.ts):

- **Submission dialog** — document/letter picker (reuse the existing register link control), shown
  read-only once locked.
- **Determination dialog** — `origin` toggle: *Against Contractor submission(s)* vs *Employer
  initiated*. Choosing the latter hides the submission checkboxes and makes the reason field
  mandatory. Per-milestone row gains a **Source submission** select, pre-filled per D6, listing only
  the covered submissions that claimed that milestone.
- **Revision history table** — new *Outcome* column rendering the derived
  `determination_outcome` badge, with `not_separately_determined` visually distinct from `pending`.
  Row action **Supersede** behind the new permission.
- **Header** — `Open EOT Submissions: n · Oldest Pending: EOT-1 · Current Contractual Baseline:
  Original`, per source spec §31. Never label the latest submission as the baseline.
- **Register** ([KeyDateRegisterPage.tsx](../client/src/pages/KeyDateRegisterPage.tsx)) — no column
  changes; the G6 fix changes only which status value arrives.
- **Detail lineage** ([KeyDateDetailPage.tsx](../client/src/pages/KeyDateDetailPage.tsx)) — show each
  submission's outcome and, for a granted item, which submission the grant answered.

---

## 9. CSV changes

- **Submission import** — unchanged headers. Document links are **not** importable via CSV; a
  document id in a spreadsheet is unverifiable and a cross-tenant risk. UI/API only.
- **Determination import** — one new optional column `source_submission_ref` accepting a revision
  label (`EOT-1`). Blank falls back to the D6 default. An unknown or uncovered label is a row error.
- **Protected-column blocklist** extended (currently
  [key_date_revision_service.py:823-829](../backend/rbac_backend/services/key_date_revision_service.py))
  with `origin`, `determination_id`, `source_submission_id`, `superseded_by_submission_id`,
  `linked_document_ids`. Protected values continue to be read from the database, never from the file.
- **Preview** gains the `Source` column so the user sees attribution before importing.

---

## 10. Migration

`v20260813_0001_key_date_eot_attribution.py`. No new indexes — the 2026-08-11 migration already
creates every index this design needs, including the unique constraints that make duplicate revision
numbers and duplicate determination references impossible.

1. Set `origin = "contractor_submission"` on all existing `key_date_eot_determinations`.
2. Backfill `source_submission_id` on existing determination items using the **same latest-covered
   rule** that produced them, so no historical record changes meaning.
3. Backfill `covered_claims` from the covered submissions' items — this *recovers* the EOT-1 claims
   that G3 dropped, where the source submission still exists.
4. Initialise `linked_document_ids` / `linked_letter_ids` to `[]`.
5. Run `recompute_effective_dates` for every project/contract holding at least one frozen
   determination. Report per-project date changes in the migration summary — a change here means G5
   had already corrupted that project's contractual dates, and must be reviewed by the contract
   team, not silently applied.

Step 5 is the one with contractual consequence. Dry run first, always:

```bash
cd backend && python -m rbac_backend.scripts.migrate_database --list && python -m rbac_backend.scripts.migrate_database --fail-on-warning
```

On production this runs **inside the backend container** — the host interpreter lacks `motor`/`dotenv`
and will fail with an environment artefact that looks like a gate failure.

---

## 11. Concurrency and integrity

Existing protections that carry over: atomic `$inc` revision allocation, unique
`(project_id, contract_id, revision_number)`, unique `(determination_id, key_date_id)`, unique
determination reference per contract, and conditional `find_one_and_update` guards on lock/freeze
(`{"locked_at": None}` / `{"frozen_at": None}`) with race-tolerant re-reads.

New surface to protect:

- **Supersede** — conditional update on `{"superseded_by_submission_id": None, "status": "locked"}`;
  a lost race returns the winner's state rather than erroring.
- **Recompute** — must tolerate concurrent execution. It is idempotent and converges, but two
  interleaved runs could briefly write different intermediate values, so it takes a per
  project/contract advisory guard and writes only changed fields.
- **Cycle safety** — supersession links form a DAG; the supersede route rejects a link whose target
  is already (transitively) superseded by the source.

---

## 12. Test matrix

Backend — extend [test_key_date_revision_workflow.py](../backend/rbac_backend/tests/test_key_date_revision_workflow.py):

| # | Test | Guards |
|---|---|---|
| T1 | Employer-initiated determination with zero submissions freezes and moves the date; without a reason it is rejected | G2, D1 |
| T2 | Determination covering EOT-1 + EOT-2 on the same milestone stores both claims in `covered_claims` and names one `source_submission_id` | G3, D3 |
| T3 | **Freeze EOT-2 (grants 01-Sep) then freeze EOT-1 (grants 25-May) → KD-01 stays 01-Sep** | G5, D5 |
| T4 | `recompute_effective_dates` is idempotent — second run writes nothing | D5 |
| T5 | Superseded determination is excluded from the effective set | D5 |
| T6 | Outcome derivation: `pending` → `not_separately_determined` once a later submission is determined → `partially_accepted` once its own determination freezes | G4, D4 |
| T7 | Explicit supersede sets the fields + emits audit; supersede of a determined submission is rejected; cycle rejected | G7/D7 |
| T8 | Register `latest_eot_status` ignores unfrozen determinations | G6 |
| T9 | Document links to another org/project are rejected | §39 |
| T10 | Determination CSV: `source_submission_ref` honoured; unknown label errors; protected columns rejected | §26 |
| T11 | Complete-history export carries outcome + source columns dynamically | §21D |

Frontend — Vitest for the origin toggle, the source-submission select default (D6), and outcome
badge rendering; Playwright for the acceptance scenario in §13.

Run:
```bash
backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q
```
```bash
cd client && npm run lint && npm run build && npm run test
```
`npx tsc -b` reports 146 pre-existing errors and cannot be used as a pass/fail gate — check that the
touched files are absent from its output instead.

---

## 13. Acceptance scenario (browser, against real project data)

1. Freeze Original baseline (KD-01 01-Jan-2026, KD-02 15-Jan-2026).
2. Create EOT-1, attach the Contractor's EOT application document, lock. Leave undetermined.
3. Create EOT-2 while EOT-1 is pending, attach its document, lock.
4. Create EOT-3 while both are pending, lock.
5. Confirm header: `Open EOT Submissions: 3 · Oldest Pending: EOT-1 · Current Contractual Baseline: Original`, and all three key dates still equal the Original.
6. Employer issues **one** determination covering EOT-1 + EOT-3, partially granting KD-01. Freeze.
7. Confirm: KD-01 moves to the granted date; KD-02 carries forward; EOT-2 shows
   `not_separately_determined`; EOT-1/EOT-3 show `partially_accepted`; EOT-2's submitted dates are
   byte-identical to what was recorded in step 3.
8. Employer issues a **standalone** determination (no submission) extending KD-02, with reason. Freeze.
9. Determine EOT-2 last, granting a date **earlier** than the step-6 grant → confirm KD-01 does not
   regress (T3 in the browser).
10. Download Original, each submission, each determination, and the complete history; confirm every
    revision is independently reproducible and the history export carries dynamic per-EOT columns.

---

## 14. Sequencing

| Phase | Content | Ships independently |
|---|---|---|
| P1 | **G5 + G6 fixes** — `recompute_effective_dates`, register status filter, T3/T4/T5/T8 — **IMPLEMENTED, see §16** | Yes — these are correctness bugs and should not wait on the feature work |
| P2 | G1 document/letter links + server-side scope validation (T9) — **IMPLEMENTED, see §17** | Yes |
| P3 | G2 employer-initiated origin (T1) — **IMPLEMENTED, see §18** | Yes |
| P4 | G3 attribution + `covered_claims` + CSV `source_submission_ref` (T2, T10) — **IMPLEMENTED, see §19** | Yes |
| P5 | G4 derived outcomes + D7 supersede + new permission + UI (T6, T7, T11) — **IMPLEMENTED, see §20** | Depends on P3/P4 |
| P6 | Migration + G7 dead-code removal + legacy deprecation markers (D8) — **IMPLEMENTED, see §21** | Last |

P1 is deployable on its own and is the highest-value change in this plan.

---

## 15. Risks and open items

- **P1 changes contractual dates on existing data.** Any project where determinations were frozen
  out of order currently holds a wrong `current_approved_key_date`. The recompute corrects it, which
  will look like an unexplained date change to users. Migration step 5 must report every change for
  contract-team review before it is accepted — do not present it as a silent fix.
- **Legacy dual model persists** (D8). `EOTApplication` / `ExtensionHistory` and the per-milestone
  `POST /key-dates/{id}/eot` route remain live, and `freeze_baseline` still refuses while any legacy
  application is active
  ([key_date_revision_service.py:149-158](../backend/rbac_backend/services/key_date_revision_service.py)).
  Projects with legacy EOTs cannot freeze a baseline until they are resolved — a migration path for
  those is **not** in this plan and is the largest open item.
- **`current_revision` is never incremented by the new workflow.** It only moves on the legacy review
  path ([key_date_service.py:543](../backend/rbac_backend/services/key_date_service.py)), and it
  gates baseline-overwriting branches in `update` and `recalculate_project`. Those branches are
  currently unreachable post-freeze because `_assert_original_baseline_editable` fires first, so this
  is latent rather than live — but it is one removed guard away from being a data-loss bug and should
  be resolved with the legacy migration.
- **Not verified in this pass:** whether production holds any project with frozen determinations
  (i.e. whether P1's migration has real work to do), and whether `key-dates-api.ts` needs type
  regeneration under `docs/SHARED_TYPES.md` conventions.
- Sections 4, 6–13 remain **plan only** — no code was written for P2–P6 and none of their behaviour has been tested.

---

## 16. P1 implementation record (2026-08-13)

Implemented test-first: all four tests were written and watched fail before any production code
changed.

### Changed

| File | Change |
|---|---|
| `services/key_date_revision_service.py` | New `recompute_effective_dates()`; `freeze_determination` no longer writes grant dates onto milestones, it freezes then recomputes |
| `services/key_date_service.py` | `_workflow_summary_for` filters linked determinations to `frozen_at` and orders by it |
| `tests/test_key_date_revision_workflow.py` | 4 new tests + two shared helpers |

### Tests

```
backend/rbac_backend/tests/test_key_date_revision_workflow.py .........  9 passed
```

| Test | RED (before) | GREEN (after) |
|---|---|---|
| `test_later_determination_of_an_earlier_eot_does_not_regress_the_contractual_date` | KD-01 became 2026-05-25 | stays 2026-09-01 |
| `test_recompute_effective_dates_is_idempotent` | `AttributeError` | returns `[]`, writes nothing |
| `test_superseded_determination_is_excluded_from_the_effective_set` | KD-01 stayed 2026-09-01 | reverts to baseline 2026-01-01 |
| `test_register_projection_ignores_an_unfrozen_determination` | `'granted'` | `'pending'` |

The supersession test was rewritten during RED: its first form passed against the unfixed code
because last-frozen-wins coincided with the expected answer. It now grants a *different* milestone in
the superseding determination, so exclusion is observable regardless of freeze order.

Full suite: **1867 passed, 5 failed, 9 skipped**. All 5 failures reproduce identically on clean HEAD
with these three files stashed — `test_permission_contract_generation`,
`test_route_authz_gate_evidence`, `test_route_control_manifest` (the known-open manifest drift
recorded in CLAUDE.md) and two `test_subscription_scope_index_integration` cases. **Pre-existing, not
caused by P1.**

### One addition beyond the stated P1 scope

`freeze_determination` now refuses to freeze when an item grants a milestone absent from the frozen
Original baseline snapshot. The recompute seeds from that snapshot, so without this check an
integrity gap would surface as a raised error *after* `frozen_at` was already set. Failing closed at
validation time is the correct place. It is a new refusal path and is called out here because it was
not in the P1 description.

### Deliberately not done in P1

- `POST /key-dates/recompute-contractual` (§6) — no route added, so **no route-manifest regeneration
  is required for P1**.
- The migration backfill (§10, P6). Consequence: an existing project whose determinations were frozen
  out of order keeps its wrong `current_approved_key_date` **until its next determination freeze**,
  which now recomputes and self-heals. Projects with no further determinations stay stale until the
  P6 migration runs.
- G7 dead-code removal, and every P2–P6 item.

### Not verified

No browser or production verification was performed. Whether any live project currently holds an
out-of-order frozen determination — i.e. whether real data is affected today — remains unchecked.

---

## 17. P2 implementation record (2026-08-13)

Implemented test-first. No route paths, permissions or gates changed, so **no route-manifest
regeneration is required**.

### Changed

| File | Change |
|---|---|
| `models/key_date.py` | `linked_document_ids` / `linked_letter_ids` on `EOTSubmissionCreate`, `EOTSubmissionUpdate`, `EOTSubmission`, `EOTDeterminationCreate`, `EOTDeterminationUpdate`, `EOTDetermination` |
| `services/key_date_revision_service.py` | New `_assert_links_in_scope()`; wired into create/update of both submissions and determinations; both entities persist the two arrays |
| `client/src/services/key-dates-api.ts` | DTO + payload fields; `normSubmission`/`normDetermination` default the arrays so pre-P2 records don't yield `undefined` |
| `tests/test_key_date_revision_workflow.py` | 4 new tests + `_seed_link_targets` helper |

### Scope validation

`_assert_links_in_scope` resolves every supplied id inside the EOT's own organisation **and**
project, against `documents` / `letters` (ObjectId `_id`, snake_case scope fields). Anything that
does not resolve there is refused: wrong organisation, wrong project, non-existent, deleted, or not
a parseable ObjectId. All four produce the same refusal, so the error cannot be used to probe
whether an id exists in another tenant.

This is new enforcement. The pre-existing `linked_document_ids` on `KeyDateMilestone`, the legacy
`EOTApplication` path, and `bank_guarantee_service` all persist link ids **without any validation** —
those remain unvalidated and are out of scope here.

### Tests

```
backend/rbac_backend/tests/test_key_date_revision_workflow.py .............  13 passed
```

| Test | RED (before) | GREEN (after) |
|---|---|---|
| `test_submission_document_and_letter_links_round_trip` | field absent | both arrays persist and return |
| `test_submission_rejects_a_document_from_another_project` | DID NOT RAISE | `KeyDateError` |
| `test_determination_rejects_a_letter_from_another_organisation` | DID NOT RAISE | `KeyDateError` |
| `test_submission_rejects_an_unparseable_document_id` | DID NOT RAISE | `KeyDateError` |

Regression: 44 passed across key-date, register-CSV and route-inventory suites. Client `npm run
build` succeeds; `tsc --noEmit` reports no errors in any touched file.

### Deliberately not done in P2

- **No UI.** The plan's phase table assigns UI work to P5; §8's document/letter picker is not built,
  so links are settable through the API but **not yet through the Key Dates page**. P2 delivers the
  data capability and the client contract only.
- CSV import still cannot carry document links, by design (§9).

### Open question this raised

Scope validation is strict: a document must carry the EOT's exact `project_id`. `models/document.py`
defines `project_id` with `default=""` on one model, so an organisation-level document with no
project would be **rejected**. Whether real contract documents are stored project-scoped or
org-scoped has not been checked against live data. If org-level documents are common, this needs
loosening to "same org, and project matches or is empty" before the picker ships in P5.

---

## 18. P3 implementation record (2026-08-13)

Implemented test-first. No route paths, permissions or gates changed — **no route-manifest
regeneration required**.

### Changed

| File | Change |
|---|---|
| `models/key_date.py` | New `EOTDeterminationOrigin` enum; `origin` on `EOTDeterminationCreate` + `EOTDetermination`; `eot_submission_ids` drops `min_length=1`; `employer_initiated_determinations` on the summary |
| `services/key_date_revision_service.py` | Origin rules in `create_determination`; `_determination_item_docs` resolves milestones from the project register when there is no submission; determination CSV drops the covered-submission requirement for employer-initiated; summary counts them and labels the baseline `Employer Determination` |
| `services/key_date_revision_export.py` | Complete-history export gains dynamic per-determination columns for employer-initiated grants |
| `client/src/services/key-dates-api.ts` | `EOTDeterminationOrigin` type, `origin` on the DTO + create payload, summary counter, `normDetermination` defaults `origin` for pre-P3 records |
| `tests/test_key_date_revision_workflow.py` | 8 new tests + `_employer_determination` helper |

### Origin rules (enforced in the service, not the schema)

| Origin | `eot_submission_ids` | `remarks` |
|---|---|---|
| `contractor_submission` (default) | at least one, each locked and in scope | optional |
| `employer_initiated` | must be **empty** | **required** — the reason the Employer acted of its own motion |

Enforcing in the service rather than via `Field(min_length=1)` keeps the refusal a 400 with a
contractual message, matching every other rule in this module, instead of a schema 422. `origin` is
deliberately **absent from `EOTDeterminationUpdate`** — it is a discriminator fixed at creation, not
an editable attribute.

### Tests

```
backend/rbac_backend/tests/test_key_date_revision_workflow.py ....................  21 passed
```

All eight went RED first (the first five on Pydantic `too_short`, confirming `min_length=1` was the
live constraint). Covered: a zero-submission grant moves the date and stores null claim fields; a
missing reason is refused; origin + submissions together is refused; contractor origin still requires
a submission; an out-of-project milestone is refused; determination CSV matches against the project
register; the summary counts and labels correctly.

Regression: 43 passed across key-date and route-inventory suites. Client builds; `tsc --noEmit`
reports no errors in any touched file.

### One gap found and fixed beyond the stated P3 scope

`history_table` built its determination columns keyed on `(submission_id, milestone_ref)`. An
employer-initiated determination has no submission id, so **its grant would have been silently
absent from the complete history export** while still having moved the Current Contractual Date —
a report showing a changed date with nothing explaining it. That is exactly the silent-success
pattern this codebase has been bitten by before, so it was fixed here rather than deferred: such
determinations now get their own `<reference> Granted` / `<reference> Status` column pair, keyed on
determination reference since they have no EOT-N label.

### Deliberately not done in P3

- **No UI.** The origin toggle described in §8 is not built; employer-initiated determinations are
  creatable through the API only. Still queued behind P5, as with P2's picker.
- The register projection (`_workflow_summary_for`) still keys on submissions, so an employer-initiated
  determination does not alter a milestone's `latest_eot_status`. This is correct — an outstanding
  Contractor submission remains outstanding — but means a milestone with no submissions shows no EOT
  status at all despite carrying an Employer grant. Its Current Contractual Date is correct.

---

## 19. P4 implementation record (2026-08-13)

Implemented test-first. No route paths, permissions or gates changed — **no route-manifest
regeneration required**.

### Changed

| File | Change |
|---|---|
| `models/key_date.py` | New `EOTDeterminationCoveredClaim`; `source_submission_id` on `EOTDeterminationItemInput`; `covered_claims` on `EOTDeterminationItem` |
| `services/key_date_revision_service.py` | `covered_by_ref` keeps every claim instead of overwriting; new `_determined_submission_ids()`; source selection + validation; CSV `source_submission_ref` → id resolution; template header; extended protected-column set |
| `services/key_date_revision_export.py` | `determination_table` gains `Against Submission` / `Other Claims Covered` |
| `client/src/services/key-dates-api.ts` | `EOTDeterminationCoveredClaimDTO`, `source_submission_id` + `covered_claims` on the item DTO and payload, normalizer default |
| `tests/test_key_date_revision_workflow.py` | 8 new tests + 2 helpers |

### The fix for G3

The defect was a dict keyed on milestone ref alone, iterated in ascending revision order, so the
last covered submission overwrote every earlier claim. It now accumulates a list per ref. Each
determination item stores:

- `covered_claims` — every covered submission that claimed that milestone, in revision order, with
  its own `submitted_date` and `claimed_extension_days`;
- `source_submission_id` — the one claim the granted date answers;
- `submitted_date` / `claimed_extension_days` — denormalised from the source claim, so existing
  exports and the pre-freeze validation keep working unchanged.

This shape was forced by the unique `(determination_id, key_date_id)` index: one determination
cannot hold two rows for one milestone, so attribution had to live inside the single row.

### Source selection (D6)

Explicit `source_submission_id` wins and is validated against the determination's own covered set —
naming a submission this determination does not cover is refused. Left blank, it defaults to the
**earliest covered claim on that milestone not already answered by a frozen determination**, falling
back to the earliest claim if all are answered. `_determined_submission_ids` excludes the
determination being edited, so re-importing a CSV into an unfrozen determination is stable.

### Tests

```
backend/rbac_backend/tests/test_key_date_revision_workflow.py ..............................  30 passed
```

All eight went RED first. Covered: both claims retained with the earliest undetermined as source; an
explicit source overrides it; an uncovered source is refused; the default skips an
already-determined EOT-1 in favour of EOT-2; CSV honours `source_submission_ref`; an unknown label is
a row error, not a silent default; `source_submission_id` as a CSV column is refused as protected.

Regression: 60 passed across key-date, register-CSV and route-inventory suites. Client builds;
`tsc --noEmit` reports no errors in touched files. The full backend suite at the P3 checkpoint was
**1879 passed / 5 failed**, the 5 being the same pre-existing failures proven against clean HEAD in
§16, with the pass count up exactly 12 (P2's 4 + P3's 8).

### One addition beyond the stated P4 scope

`determination_table` gained `Against Submission` and `Other Claims Covered`. Without them, a
downloaded combined determination showed a single submitted date with nothing saying which of EOT-1
or EOT-2 it belonged to — the same traceability loss G3 describes, just moved from the database into
the export. The requirement that each submission stay independently traceable is not met if the
document a user actually reads cannot express it.

### Deliberately not done in P4

- **No UI.** §8's per-milestone Source-submission select is not built. Determination attribution is
  settable through the API and CSV only. This is now the third phase queued behind P5's UI work.
- CSV preview exposes `source_submission_ref` in its row data, but no UI renders the Source column.

---

## 20. P5 implementation record (2026-08-13)

The largest phase: derived outcomes, explicit supersession, a new permission, the first new route,
and the whole UI backlog carried from P2–P4.

### Backend

| File | Change |
|---|---|
| `core/permissions.py`, `initial_data/default_permissions.py` | New `dms.keydate.eot.supersede`, registered at all four points (canonical list, enum, alias map, default catalogue) |
| `models/key_date.py` | `SubmissionOutcome` enum; supersede fields + derived `determination_outcome` / `determining_determination_ids` on `EOTSubmission`; `EOTSubmissionSupersedeRequest`; `not_separately_determined` on the summary |
| `services/key_date_revision_service.py` | `submission_outcomes()` (pure, static); `supersede_submission()`; summary wiring |
| `services/key_date_revision_export.py` | Optional per-submission `<label> Outcome` column |
| `routers/key_dates.py` | `POST /key-dates/eot-submissions/{id}/supersede`; history export passes outcomes |

### Outcome derivation

A submission is answered only by a **frozen** determination that covers it *and* rules on a milestone
it actually claimed. Precedence: explicit supersession → withdrawn/draft status → its own frozen
result (`accepted` / `partially_accepted` / `rejected`) → `not_separately_determined` → `pending`.

`not_separately_determined` fires when a **later** submission sharing at least one of this
submission's milestones has been answered while this one has not. That is the contractual position
the original request calls out: EOT-1 and EOT-2 outstanding, the Employer answers only EOT-3, and the
earlier claims were never determined in their own right. Deriving rather than storing it means it
self-corrects the moment EOT-1 is finally determined.

### Supersession (D7)

Explicit only, never inferred. Guarded on: mandatory reason; target must be `locked`; the superseding
submission must exist in the same project/contract with a **strictly higher** revision number; the
superseding submission must not itself be superseded (cycle guard); and the target must not already
be covered by a frozen determination — superseding a determined submission would rewrite settled
contractual history (§40). The write is a conditional update on
`{"superseded_by_submission_id": None}`, so a concurrent second supersede loses cleanly.

### UI — the P2–P4 backlog, now shipped

- **Extracted** `LettersLinker` out of `IPCBillRegisterPage.tsx` into
  `components/documents/LinkedDocumentsPicker.tsx` (with `disabled` and `title` props) rather than
  duplicating it. The IPC page now consumes the shared component; its local copy and its
  now-unused `documents-api` import are gone.
- **Submission dialog** — document picker ("Contractor's EOT application document"), read-only once
  locked. *(P2)*
- **Determination dialog** — Origin select (against submissions / employer-initiated), which hides
  the submission checkboxes and requires a reason; determination-letter picker; per-milestone
  **Against Claim** select defaulting to "Oldest outstanding", with the Submitted column following
  the selected claim. *(P2/P3/P4)*
- **Header** — `Employer Determination` action; stat tiles now show Open EOT Submissions, Oldest
  Pending and Not Separately Determined.
- **Revision history** — new Outcome column with `not_separately_determined` styled as a warning
  rather than another shade of "pending"; `Supersede` row action opening a dialog that only offers
  later, non-superseded revisions and requires a reason.

### Contract regeneration — and three pre-existing failures fixed

This is the first phase to add a route and a permission, so both generators were run per CLAUDE.md:

```bash
backend/.venv/Scripts/python.exe scripts/generate_permission_contract.py
backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json
```

Both artefacts turned out to be badly stale, and the regeneration was **not** cosmetic:

| Artefact | Diff | What it means |
|---|---|---|
| `contracts/permission_contract.json` | +21 / −1 lines; version `2026-08-06.1` → `2026-08-11.1` | The 2026-08-11 predecessor work added permissions and never regenerated the contract |
| `route_control_manifest.json` | +749 / −20 lines; **22 route entries added**, 2 removed | Only **one** of those 22 is P5's new supersede route |

The other 21 added entries are the *entire* 2026-08-11 EOT revision surface —
`/api/key-dates/baseline/freeze`, `/api/key-dates/eot-submissions/{id}/lock`,
`/api/key-dates/eot-determinations/{id}/freeze`, the CSV import/preview and export routes, and the
rest. **They existed in the application but were absent from the checked-in route control
manifest**, which is the artefact the authz gate-evidence tooling reads. Per CLAUDE.md,
`gate_evidence` in that manifest is the trusted record of what gates a route — and for these 21
routes there was no record at all. That is a control-visibility gap, not merely a stale file, and it
had been open since 2026-08-11.

Regenerating therefore **fixed three of the five long-standing failures** recorded in §16:
`test_permission_contract_generation`, `test_route_control_manifest`, and
`test_route_authz_gate_evidence` now pass. This was the required side effect of a mandated step, not
an attempt to fix unrelated tests — but it does mean these two artefacts carry substantial changes
beyond P5's own, and the 21 recovered route entries deserve a review of their recorded gates.

Full suite after P5: **1911 passed, 2 failed, 9 skipped.** The 2 remaining failures are the
`test_subscription_scope_index_integration` pair, proven pre-existing against clean HEAD in §16.

### Tests

```
backend  test_key_date_revision_workflow.py ......................................  38 passed
client   npm run test                                     349 passed | 3 skipped
```

All ten new backend tests went RED first. `npm run build`, `npx eslint` on every touched file, and
`tsc --noEmit` are all clean.

### Not verified

**No browser verification was performed.** The UI compiles, lints, type-checks and passes unit
tests, but no part of it has been exercised against a running stack — no dialog was opened, no
supersede was clicked, no outcome badge was seen rendering real data. The acceptance scenario in §13
remains entirely unrun. This is the largest outstanding risk in the whole workstream.

---

## 21. P6 implementation record (2026-08-13)

### Changed

| File | Change |
|---|---|
| `migrations/v20260813_0001_key_date_eot_attribution.py` | **New.** Backfills origin, per-item attribution, link arrays; re-derives contractual dates |
| `migrations/catalog.py` | Registers the migration after `20260811_0001` |
| `services/key_date_revision_service.py` | G7: dead `assert_baseline_editable` removed, replaced by a note naming the live guard |
| `routers/key_dates.py` | D8: the four legacy per-milestone EOT routes marked `deprecated=True` |
| `route_control_manifest.json` | Regenerated (route metadata changed) |
| `tests/test_key_date_eot_attribution_migration.py` | **New**, 5 tests |
| `tests/test_key_date_revision_workflow.py` | 2 new guard tests |

### Migration behaviour

1. `origin = "contractor_submission"` on every pre-P3 determination.
2. `linked_document_ids` / `linked_letter_ids` initialised on submissions and determinations;
   supersede fields defaulted to null.
3. `source_submission_id` + `covered_claims` backfilled on determination items. The source is set to
   the **latest** covered submission — deliberately reproducing the pre-P4 rule, because a migration
   must not silently restate what a historical record meant. `covered_claims` recovers the earlier
   claims that G3 had discarded.
4. `recompute_effective_dates` runs for every project/contract holding a frozen determination.

Step 4 is the one with contractual consequence. **Every changed date is emitted as a migration
warning**, e.g.

```
Contractual date changed for KD-01 in project proj-A: 2026-05-25 -> 2026-09-01. Review before accepting.
```

A project only produces warnings if G5 had already corrupted it. Because `migrate_database` is run
with `--fail-on-warning`, such a project **stops the deploy** and forces a human decision rather than
silently rewriting a contractual date. That is the intended behaviour, not an obstacle to work around.

Dry run writes nothing at all — it does not even evaluate dates, so it cannot report the repairs it
would make. Tested explicitly.

### Tests

```
test_key_date_eot_attribution_migration.py .....        5 passed
test_key_date_revision_workflow.py (45 total)          45 passed
route/manifest/permission-contract group                329 passed
```

Migration tests cover: dry run writes nothing; backfill lands on EOT-2 not EOT-1 (preserving
historical meaning); an out-of-order-frozen project is repaired to 2026-09-01 **and** reports the
change; a second run is a no-op with zero warnings; the version is registered and the catalogue stays
in ascending order. `migrate_database --list` shows `20260813_0001` as pending.

Full suite after P6: **1918 passed, 2 failed, 9 skipped** — exactly P5's 1911 plus P6's 7 new tests,
with the same two pre-existing `test_subscription_scope_index_integration` failures proven against
clean HEAD in §16.

### G7 and D8

`KeyDateRevisionService.assert_baseline_editable` is gone, with
`test_only_one_frozen_baseline_guard_exists` asserting it cannot come back and that
`KeyDateService._assert_original_baseline_editable` remains the single live guard.

The four legacy per-milestone routes (`POST .../eot`, `GET .../eots`, `POST .../eot/{id}/review`,
`GET .../history`) now carry `deprecated=True`, so they are flagged in OpenAPI while continuing to
work. `test_legacy_per_milestone_eot_routes_are_marked_deprecated` pins both halves: the legacy set is
flagged, and the replacement `/key-dates/eot-submissions*` surface is not.

### The legacy fold-in migration — defined, NOT implemented

D8 was "plan deprecation, do not remove yet". The removal path is therefore specified here and left
unwritten:

1. For each legacy `key_date_eot_applications` row, create an `EOTSubmission` in the project's
   contract scope carrying `eot_letter_reference`, `application_date` and
   `requested_revised_key_date` as a single-milestone item.
2. For applications with a review decision, create an `EOTDetermination` covering that submission,
   with `approved_revised_key_date` → `eot_granted_date` and the decision mapped to a
   `determination_result`, frozen at `reviewed_date`.
3. Reconcile `key_date_extension_history` against the generated determinations; discard nothing.
4. Re-run `recompute_effective_dates`, reporting date changes as warnings exactly as above.
5. Only then delete the legacy routes, `EOTApplication`, `ExtensionHistory`, and
   `KeyDateService._revisions_for`, and regenerate the manifest.

**Removal gate:** no legacy route may be deleted while any tenant holds a
`key_date_eot_applications` row with status `draft`, `submitted` or `under_review`, because
`freeze_baseline` already refuses to run in that state — deleting the routes would strand those
projects with no way to close the applications blocking their baseline freeze.

### Still open after P6

- **`current_revision` is never incremented by the new workflow** (§15). It moves only on the legacy
  review path and gates baseline-overwriting branches in `update` and `recalculate_project`. Those
  branches remain unreachable post-freeze only because `_assert_original_baseline_editable` fires
  first. Latent, not live — and it should be resolved by the fold-in migration above, not before.
- **Projects with active legacy EOT applications still cannot freeze a baseline.** Unchanged by P6.

---

## 22. Verification run (2026-08-13)

Run against a **local** stack: uvicorn on 127.0.0.1:8000, Vite on 5173, and the local
`mongodb://localhost:27017/contraclaim` development database. Production was not touched.

### Migration dry run — real MongoDB

```bash
cd backend && ../backend/.venv/Scripts/python.exe -m rbac_backend.scripts.migrate_database --fail-on-warning
```

`20260813_0001` reported `status: dry_run`, all four operations with `count: 0` / `scopes: 0`, and
`warnings: []`. The local database holds no EOT revision data, so the migration had nothing to
migrate — the code path executed, but its backfill and repair logic were **not** exercised against
real data here. That still awaits a database that has frozen determinations.

**Locally, `--fail-on-warning` returns exit code 2** — proven with `--target 20260811_0001`, which
excludes the new migration: exit 2 either way. The cause is `20260721_0001
arbitration_phase0_containment`, which emits two informational warnings by design.

That is a **local-only** artefact, and an earlier claim in this document that the gate "cannot be
used as a pass/fail signal" was wrong. On a database where the migrations are already applied they
are reported `skipped` and emit nothing, so the gate behaves correctly — confirmed on production
below.

### Production dry run — inside the backend container

Run read-only on `contraclaim` per CLAUDE.md, using the compose pair read from the running
container's own `com.docker.compose.project.config_files` label
(`docker-compose.prod.yml` + `docker-compose.mongo-replicaset.yml`):

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
  exec -T backend python -m rbac_backend.scripts.migrate_database --fail-on-warning
```

**`EXIT=0`. 15 migrations, all `skipped`, no warnings, nothing pending.**

This dry run did **not** exercise `20260813_0001`: production tracks `main` at `928c5ab`, and the
migration file is absent there. It establishes the baseline, not the new migration's behaviour.

Two findings from the same read-only session:

**1. The migration's blast radius in production is nil.**

| Collection | Production count |
|---|---|
| `key_date_milestones` | 33 |
| `key_date_baselines` | **0** |
| `key_date_eot_submissions` | **0** |
| `key_date_eot_determinations` | **0** |
| `key_date_eot_determination_items` | **0** |
| `key_date_eot_applications` (legacy) | **0** |

No project has ever frozen a baseline, so the whole 2026-08-11 revision workflow is unused in
production. Consequences, all of which downgrade risks flagged earlier in this document:

- `20260813_0001` has **nothing to backfill and no contractual date to repair**. The
  `--fail-on-warning` stop described in §21 cannot trigger on today's data.
- The G5 corruption this work fixes has **never occurred in production** — it needed a frozen
  out-of-order determination, and there are none.
- The D8 blocker ("projects with active legacy EOT applications cannot freeze a baseline") affects
  **no tenant**: the legacy collection is empty.

**2. The applied ledger holds 17 entries against a 15-migration catalogue in the container.**
Production has run two migrations that are not in the code currently deployed there — consistent
with the documented divergence between the two remotes' `main` branches, which carry the same work
under different SHAs. Not a fault, but it means the container's catalogue is not a complete record
of what has been applied.

### Section 13 acceptance scenario — real HTTP, real MongoDB

A throwaway `zz-eot-verify-org` / `zz-eot-verify-project` was created, the whole lifecycle driven
over HTTP against the running backend, then removed. **48 of 48 checks passed**, covering every
phase end-to-end through routers, `PolicyService`, real Motor queries and the real unique indexes —
none of which the fake-DB unit tests touch:

| Area | Verified |
|---|---|
| Baseline | freeze succeeds; a frozen baseline then refuses a new milestone (400) |
| P2 | document link round-trips; a cross-project document link is refused (400) |
| Successive EOTs | EOT-2 and EOT-3 created while EOT-1 pending; 3 open; baseline stays `Original`; both key dates stay at their original values |
| P4 | combined determination over EOT-1+EOT-3 retains **both** claims; source defaults to the oldest outstanding (EOT-1); primary claim is EOT-1's |
| Determination | KD-01 moves to the granted date; KD-02 carries forward after rejection |
| P5 | EOT-1/EOT-3 `partially_accepted`, EOT-2 `not_separately_determined`, summary counts it; EOT-2's submitted dates unchanged by EOT-1's determination |
| P3 | employer-initiated determination with zero submissions freezes and moves KD-02; one without a reason is refused (400) |
| **G5** | EOT-2 determined **last** with an **earlier** grant date → **KD-01 did not regress** |
| D7 | supersede by an earlier revision refused; supersede of a determined submission refused |
| Exports | baseline, submission, determination, and history in csv/xlsx/pdf all return content |
| Export content | `EOT-1 Outcome` column present; employer-initiated determination has its own column |

One assertion of mine was wrong and the code was right: the history export no longer says
`not_separately_determined` once EOT-2 has been determined. The corrected test now pins **both**
states — present while EOT-2 is unanswered, absent after it is answered — which demonstrates the
self-correcting property that motivated deriving the outcome instead of storing it.

### Browser walkthrough

Performed in an already-authenticated session (no credentials were entered at any point). The
seeded `zz-eot-verify` project was selected in the navbar and in the Register filter, and the page
rendered the full contractual state:

| Verified on screen | Result |
|---|---|
| Register `Current` column | KD-01 **15/04/2026**, KD-02 **20/02/2026** |
| **G5 in the browser** | KD-01 held the EOT-1+EOT-3 grant, *not* the later-frozen EOT-2 determination's earlier 10/02 |
| P3 | KD-02 carried the employer-initiated grant; a determination row labelled `↳ ZZCLIENT/2026/OWN` / "Employer determination" |
| P5 | Outcome column rendering "Partially accepted" / "Pending determination" badges |
| P5 | `Supersede` action on every locked revision; `Employer Determination` header button |
| Header tiles | Open EOT Submissions, Oldest Pending, Not Separately Determined |

A seeding gap surfaced first: the throwaway project was invisible to the navbar selector because the
seed wrote a minimal document. Real projects carry `projectCode`, `address`, `adminEmail` and
others; once those were added the project appeared. Not a product defect — an instance of the
house rule to seed model-valid shapes.

**Defect found in the browser — the header named a determination that governs nothing.**

The page read `Current Contractual Baseline: EOT-2` while every milestone's in-force date came from
the EOT-1+EOT-3 determination. `workflow_summary` still selected its label with
`effective.sort(key=frozen_at)` then `effective[-1]` — the **same order-dependence P1 removed from
the dates but left in the label**. The EOT-2 determination was frozen last, so it won the label
despite its grant having been superseded by contractual ordering and applying to nothing.

This also falsifies a claim made in §5 of this document: *"`workflow_summary.current_contractual_baseline`
derives its label from the same ordered set, so the header can no longer disagree with the register."*
That was untrue when written — the summary was never changed. Unit tests never caught it because none
asserted the label after an out-of-order freeze; only rendering the page did.

Fixed by extracting the replay into `KeyDateRevisionService._replay_determinations`, now shared by
`recompute_effective_dates` and `workflow_summary`. The label is chosen from the determinations that
actually govern a milestone's date, ordered by `determination_date`. Regression test:
`test_baseline_label_names_the_determination_actually_in_force`, written RED first and reproducing
the browser reading exactly. Confirmed over real HTTP: the acceptance run now asserts the label is
**not** `EOT-2` and **is** the latest governing determination — **50 of 50 checks pass**.

**Not completed in the browser.** Restarting the backend to load the fix cleared the in-memory
session store and logged the browser out; the session cannot be restored without entering a
password. So the fix itself was re-verified over HTTP rather than on screen, and these remain
unexercised by clicking: the determination dialog's origin toggle, the per-milestone Against Claim
select, the document picker, and the supersede dialog. `KeyDateRevisionWorkflow` has a 5-test
component suite covering those render paths, which is not the same as clicking them.

The throwaway `zz-eot-verify-org` / `zz-eot-verify-project` data has been **left in the local
database** so the walkthrough can be resumed after a login. Remove it with
`scratchpad/clean_zz.py`, which deletes only ids belonging to that project.

### Defect in the verification script itself

The throwaway-data cleanup contained
`db.key_date_eot_determination_items.delete_many({})` — an **unfiltered** delete that removes every
document in that collection, not only the test rows. It ran twice.

Impact assessment: before the run, `key_date_eot_determinations`, `key_date_eot_submissions` and
`key_date_milestones` all counted **0** in this database, so the items collection could only have
held orphan rows, and realistically was empty. But the count was never taken before deleting, and it
could not be taken afterwards because MongoDB had stopped. The script has been corrected to collect
child ids from the test project and delete only those. This affected the local development database
only — no production system was contacted at any point.
