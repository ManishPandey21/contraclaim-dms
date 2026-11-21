# KD-1 (High): key dates of a second contract calculated from the main contract

Registered 2026-11-17 on the owner's decision (High priority). Found in passing during
the 4B-23 review. **No stored production date is changed by anything in this document
or its commit; a correction of stored dates needs the owner's approval.**

## The defect (verified in code)

A key date is `LOA + week x 7` (or `(week - 1) x 7` under the FIDIC-style basis). The LOA
date and the week basis come from the Contract Master. Before the fix,
`KeyDateService._start_and_basis` always read the Contract Master row with
`contract_id: "primary"`, whatever contract the milestone belonged to. So on a project
with several contracts, a milestone of any other contract was calculated from the main
contract's LOA date and week basis. Paths affected:

| Path | Before the fix |
|---|---|
| Create a milestone (`create_milestone`) | Its calculated, original and current dates came from the main contract. |
| Change its week or start (`update`) | Recalculated from the main contract. |
| "Recalculate project" (`recalculate_project`, `POST /api/key-dates/recalculate`) | Recomputed **every** milestone of the project from the main contract, and re-derived the original and current baseline of each one with no approved revision. Run on a multi-contract project, it would have rewritten correct second-contract baselines with wrong ones. |
| Key-date CSV import | Not affected: the key-date CSV has no contract column, so every imported milestone belongs to the main contract. |

Milestones with an approved EOT revision kept their in-force date in every path; only
their `calculated_key_date` was affected.

## The fix (code only; commit in the ticket register)

- `_start_and_basis(..., contract_id)` reads the milestone's own contract's Contract
  Master entry. The main contract keeps its fallbacks (project start date, then an
  explicit start date given for the calculation).
- A contract other than the main one **never borrows** the main contract's or the
  project's date: it uses its own LOA, or an explicit start date given for that
  calculation, or it is refused ("The Contract Master entry for contract X has no
  contract start date (LOA)").
- "Recalculate project" works contract by contract. A contract with no LOA of its own
  is **skipped and reported** (`skipped_by_contract`); its milestones are not touched.
  The audit event records each contract's basis and the skips.

Proof: `T/test_key_dates.py` (3 new tests, each failing on the old code: own LOA and
basis on create and update; refusal or explicit date, never the main contract's;
recalculation per contract with a skip). All 22 key-date tests pass.

## Impact analysis (read-only): reading P-16

`scripts/claim_arbitration_readonly_readings.py` reading **P-16** (counts only, no ids or
names; run with the other readings, section F). For each milestone of a contract other
than `"primary"` it compares the stored `calculated_key_date` with the date from its own
contract and with the date from the main contract:

- `matches_main_contract_not_own`: calculated wrongly (the defect);
- `calculated_from_main_contract_with_no_approved_revision`: of those, the ones whose
  baseline (original and current dates) also came from that calculation;
- `own_contract_has_no_loa`: cannot be corrected automatically; a person must record
  the contract's LOA first;
- `matches_own_contract`, `matches_neither`, `projects_with_several_contracts`,
  `projects_affected`.

Proof: `I/test_claim_arbitration_readings_mongo.py` seeds each case and asserts each
count, and that the database is unchanged; `T/test_claim_arbitration_readonly_readings.py`
pins the script's restated formula to the service's.

## Safe correction plan (each step gated)

1. **Read P-16 in production** (authorised read-only access, AQ-11). If
   `matches_main_contract_not_own` is 0, there is nothing to correct; record it and stop.
2. **Owner decision** on the affected projects, with the P-16 counts: whether stored dates
   are corrected at all. A wrongly calculated date may already have been relied on in
   correspondence or a notice; correcting it is a contractual decision, not a data fix.
3. **If approved, per project:** a person records any missing contract LOA in the
   Contract Master, then runs "Recalculate project" for that project. It re-derives only
   milestones with no approved revision, contract by contract, skips any contract still
   without an LOA, and writes an audit event with each basis. Approved EOT revisions
   are never overwritten.
4. **Before and after:** take P-16 again after each project; `matches_main_contract_not_own`
   for that project must fall to the number of milestones with an approved revision
   (their in-force dates are deliberately kept).
5. **Rollback:** the audit event holds each contract's basis; the previous dates are in
   the milestone's audit history. A dry-run mode for the recalculation (counts only) is
   recommended before step 3 and is not built yet.

Nothing in steps 3 to 5 runs without the owner's approval and the production reading.
