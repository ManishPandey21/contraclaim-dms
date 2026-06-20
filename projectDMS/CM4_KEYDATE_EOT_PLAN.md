# CM-4 — Key Date ⇄ EOT ⇄ Contract Master: Implementation Plan

**Inputs studied:** `Key Dates Status_ as on 02.01.2023.xlsx` (5 metro contracts: KNPCC-05/06, AGCC-02, AGE-1&2, KNPAGT-03) and `Keydates-Milestones KNPCC-05.xlsx` (KNPCC-05 with revised-date + LD block). Both are weeks-from-LOA milestone registers.

> Extends `CONTRACT_MASTER_ENHANCEMENT_PLAN.md` (CM-1..CM-3 are done). CM-4 connects the **Key Date** module to the **Contract Master** (LOA/start source) and to **Bank Guarantee** (auto required-up-to on EOT), and aligns the register to the real spreadsheet layout — including **one column per EOT**.
>
> **Decisions (2026-06-20):** (1) Week basis default = `loa_plus_weeks` (LOA + weeks×7); LOA is the contract start date unless the LOA states a different commencement date (overridable). (2) Contract completion date stays **manually editable** (CM-3 revise-completion) — CM-4c is an *optional, off-by-default* convenience, not an auto-cascade.
>
> **Status: CM-4a DONE** ✅ — `week_basis` on Contract Master, corrected `calculate_key_date`, Key Date reads LOA+basis from the master, `POST /key-dates/recalculate` (opt-in), Contract Master form basis selector + register "Recalculate" button, tests green. CM-4b / CM-4c pending.

---

## 1. What the real sheets establish

| Sheet column | Meaning | Current support |
|---|---|---|
| `LOA` | Letter of Acceptance date = contract start | `contract_master.contract_start_date` (CM-1) |
| `Weeks from LoA` | contractual week number | `milestone.contractual_week_number` ✅ |
| `Time to Achieve (Days)` | `weeks × 7` | derived |
| `Contractual Date` | **`LOA + weeks × 7`** | ⚠️ my formula uses `start + (week-1)×7` — **off by one week** |
| `Revised Date` | latest EOT-revised date | `current_approved_key_date` ✅ (but only the *latest* is shown) |
| `Status`, `Date of Achievement` | | `status` (derived) + `actual_achievement_date` ✅ |
| `Delay (days/weeks)`, `Amount of LD` | LD computation | not yet (optional, §6) |

**Two corrections CM-4 must make:** (a) the week→date formula, and (b) surface **every** EOT revision as its own column, not just the latest.

---

## 2. Part A — Align the weeks→date formula (correctness)

**Problem:** `services/key_date_service.calculate_key_date` = `start + (week-1)×7`. The sheets (and KMRC/UPMRC practice) use `LOA + week×7`.

**Plan:**
- Add a `week_basis` setting on the **contract master** (`"loa_plus_weeks"` default = matches the sheets; `"loa_plus_weeks_minus_1"` = FIDIC-style fallback). Store under contract_master so it's configurable per contract.
- `calculate_key_date(start, week, basis)` → `start + (week × 7)` or `start + ((week-1) × 7)`.
- `KeyDateService` reads `contract_start_date` **and** `week_basis` from the contract master (CM-3 already wired the start-date read path) — milestone creation no longer re-types the LOA.
- **Migration safety:** existing milestones keep their stored `calculated_key_date`/`original_planned_key_date` (immutable); only *new* calculations use the basis. Add a `POST /key-dates/recalculate?project_id=` (admin) to opt-in re-derive originals where desired.
- Tests: `calculate_key_date` both bases; KNPCC-05 fixture (KD-01 4wk → contractual 2021-04-05 from LOA 2021-03-08).

---

## 3. Part B — One column per EOT (multi-EOT columnar register)  *(the user's explicit ask)*

The data already exists: `key_date_extension_history` holds one immutable record per revision (`revision_number`, `approved_revised_key_date`, `eot_letter_reference`, `approval_letter_reference`, `approval_date`, `status`). CM-4 surfaces it as columns.

**Backend:**
- Extend the milestone list/get response with a compact `revisions` array (already persisted): `[{revision, approved_revised_key_date, eot_letter_reference, approval_letter_reference, approval_date, status}]`. Add it in `KeyDateService.decorate` by joining `key_date_extension_history` for the milestone (or a batched `revisions_by_milestone` for the list endpoint to avoid N+1).
- No new collection; read-only projection.

**Frontend — `KeyDateRegisterPage` columnar view:**
- Compute `maxRevisions = max over rows of revisions.length`.
- Render columns: `Ref | Description | Weeks | Original (contractual) | EOT-1 | EOT-2 | … | EOT-N | Current | Status | Achievement | Actions`, where each `EOT-k` cell shows that revision's approved date (and a tooltip with its EOT/approval letter refs). Empty when the milestone has fewer revisions — mirrors the spreadsheet exactly.
- Keep the existing per-milestone detail timeline (already built) for the full audit view; the columnar register is the at-a-glance view the sheets use.
- A "weeks view" toggle showing `Weeks from LoA` + `Days = weeks×7` columns to match the sheet 1:1.

**Tests:** helper that builds the dynamic EOT columns from a milestone's `revisions` (pure, unit-tested); the data is already covered by the existing extension-history tests.

---

## 4. Part C — Auto EOT → contract completion → BG cascade  *(the original CM-4)*

When an EOT is **approved** on a milestone that represents contract completion, the contract's `revised_completion_date` should move, which (via CM-2's cascade) recomputes every BG's required-up-to date.

**Plan:**
- Add `milestone_kind` to the milestone model: `"normal" | "completion"` (default normal). A milestone can be flagged the completion milestone (the sheets' KD-10/KD-11 "Completion of …").
- In `KeyDateService.review_eot` (the `approved` branch, after writing the extension history and updating `current_approved_key_date`): if `milestone_kind == "completion"`, resolve the contract master for `(org, project, contract_id)` and call `ContractMasterService.revise_completion(approved_revised_key_date)` → which already cascades `BankGuaranteeService.recompute_required_dates`. All audited.
- **Guarded — default OFF (per the 2026-06-20 decision):** behind a contract-master flag `auto_revise_completion_from_eot` that is **off by default**. The completion date is managed manually via the "Revise completion" action on the Contract Master page (CM-3); the EOT auto-cascade is opt-in only for projects that explicitly want it.
- Tests: approving an EOT on a completion milestone moves `contract_master.revised_completion_date` and flips a BG's `extension_required` (end-to-end across the three services, mirroring the existing CM-2 cascade test).

---

## 5. Phasing

| Phase | Scope | Acceptance |
|---|---|---|
| **CM-4a** ✅ | `week_basis` on contract master + `calculate_key_date(basis)` + KeyDate reads start+basis from master + recalculate endpoint + FE selector/button + tests | KD-01 (4 wk) = LOA + 28 d, matching the sheets |
| **CM-4b** | `revisions` projection on milestone responses + columnar EOT view (one column per EOT) + weeks toggle on `KeyDateRegisterPage` + helper test | Register shows Original + EOT-1..N + Current columns, like the spreadsheet |
| **CM-4c** | `milestone_kind` + auto EOT→`revise-completion` cascade (flagged) + test | Approving an EOT on the completion milestone moves the contract completion date and recomputes BG required-up-to |
| **CM-4d** *(optional)* | LD register (§6) | Delay + LD amount per milestone from achievement vs revised date |

Recommended order: **CM-4a → CM-4b → CM-4c** (correctness first, then the columnar UX the user asked for, then the automation).

---

## 6. Optional — Liquidated Damages (LD), seen in sheet 2

`Keydates-Milestones KNPCC-05.xlsx` computes, per milestone: `Delay = achievement − revised contractual date`, `Delay weeks`, and `Amount of LD = rate × total contract value` (here 0.01%/week), against a `Date upto which LD is applicable` and a summed `Total Contract Value` (Schedules A–D). This is a natural follow-on:
- Reuse `contract_master.current_contract_value` (Total Contract Value) + the milestone's revised date + achievement to compute delay/LD.
- A read-only LD column on the register and an LD summary card; configurable LD rate + cap per contract on the contract master.
- Kept **out of CM-4 core** because the user's ask was the EOT columns + CM-4; flagged here so it isn't lost.

---

## 7. Risks & decisions

- **Formula change is a correctness fix, not cosmetic.** Default to `loa_plus_weeks` (sheet behaviour); never silently rewrite already-stored original dates — only new calcs + an explicit recalculate action.
- **Dynamic EOT columns** are unbounded; cap the rendered columns (e.g. show up to N, with the detail timeline for the rest) to keep the table readable.
- **Completion auto-cascade** is guarded by a flag + an explicit completion-milestone designation, so it never surprises projects that revise completion manually.
- **Open decision (needs your call):** confirm the week basis is `LOA + weeks×7` for all contracts (the five sheets are consistent on this), and which milestone is the "completion" milestone to drive the BG cascade (looks like KD-10/KD-11 "Completion of …").
