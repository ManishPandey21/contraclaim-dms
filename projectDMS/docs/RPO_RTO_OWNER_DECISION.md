# Gate 8 bullet 7 — RPO and RTO: the measurements, and the decision taken from them

**Status: OWNER DECISION MADE, 2026-09-08 — Candidate A (Conservative).
RPO 24 h · RTO 8 h · restore drill quarterly in staging. Gate 8 is 8/8.**

**The bullet is closed as OBJECTIVE RECORDED, not OBJECTIVE FULLY DEMONSTRATED.**
The decision is written into [OPERATIONS.md](OPERATIONS.md) §5 with the date and
the release owner's approval. Neither figure has been validated end to end: the
owner explicitly accepted the staging-scale restore measurements in §1 as the
evidence basis for this release, subject to later production-scale validation,
and carried §5's items 1 and 3 — S3 failure-domain separation and detection
time — as production-cutover debt. §5 item 2 is satisfied by that explicit
acceptance rather than by a production-scale drill. No production S3
architecture change is authorised in this phase.

Everything below is the analysis the decision was made from, and is preserved
as it was written on 2026-09-07. Its present tense — "nothing here ticks the
bullet", "Gate 8 remains 7 of 8" — describes the state before the decision
above, not the state now.

Gate 8 bullet 7 reads "RPO and RTO are recorded". R-A8M measured the numbers
that a target has to be defensible against; it did not, and could not, set the
target. A recovery objective is a statement about how much data this business
accepts losing and how long it accepts being down. That is the release owner's
to make, and this phase deliberately does not make it.

This note exists so the decision can be made from measurements rather than from
intuition. **Nothing here ticks the bullet.** Ticking it requires the owner to
choose a row in §4 and say so.

---

## 1. What is measured, and where it was measured

Every figure below is from the R-A8M staging execution on the production host
(2026-09-07 IST), on the same hardware production runs on, against a staging
stack of the same shape. Figures marked *production-configured* come from
production's own configuration rather than from a run.

| # | Measurement | Value | Source |
|---|---|---|---|
| 1 | Backup cadence | **daily, 01:30 host time**, `flock`-guarded against overlap | `config/contraclaim-backup.cron` (production-configured) |
| 2 | Freshness alarm threshold | **26 h** | `BACKUP_MAX_AGE_HOURS`, `scripts/backup_status.py`, `/health/operations` (production-configured) |
| 3 | Backup duration — Mongo + 5 volumes | **5 s**, all checksums verified | R-A8M Gate 8 |
| 4 | Off-site leg — `aws s3 sync` to the backup bucket | **9 s**, objects confirmed present | R-A8M Gate 8 |
| 5 | Volume restore — 4 volumes | **3 s**, 4 of 4 | R-A8M Gate 8 |
| 6 | Mongo logical restore | **6 s** for 222 documents | R-A8M Gate 8 |
| 7 | Application image rollback, each direction | **35 s**, health 200 `ready` at both ends | R-A8M Gate 8 |
| 8 | Planned full stop → full restart of the production stack | **59 min 23 s** | R-A8M outage window |
| 9 | Post-recovery verification | `post_deploy_verify.sh` 0 failures, 0 warnings, exit 0 | R-A8M §19 |

**The 59 minutes in row 8 is not an RTO.** It is a *planned maintenance window*
that included a staging bring-up, three gate executions, evidence capture and a
teardown between the stop and the start. It is an upper bound on nothing except
itself, and it is quoted here only so nobody mistakes it for a recovery figure
later.

## 2. What is NOT measured, and therefore not defensible yet

A target that ignores these would be a number, not a commitment.

1. **Detection time.** Every figure above starts at "an operator has decided to
   restore". Nothing measures how long a data-loss event takes to be noticed.
   `/health/operations` and the 26 h freshness alarm cover *backup staleness*,
   not corruption or deletion of live data.
2. **Decision and authorisation time.** `mongo_restore.sh` refuses a production
   target unless `ALLOW_PRODUCTION_RESTORE=1` is given deliberately, and the
   runbook requires the application to be stopped first. That is correct, and it
   is human time nobody has timed.
3. **Restore at production scale.** Row 6 is 222 documents. Production's Mongo
   has never been restore-drilled, and the relationship between 222 documents
   and production's collection sizes is not linear in any way this evidence
   establishes. **Qdrant is the specific unknown**: production carries 2,092
   points in `contracts` and the drill restored 3.
4. **Off-site retrieval.** Row 4 is the *upload* leg. Pulling a backup set back
   out of S3 to a host that has lost its local `/var/backups` has never been
   timed, and that is the path a real disaster uses.
5. **Blast radius of the shared bucket.** Production documents and production
   backups still share one S3 bucket (`docs/S3_STORAGE_POSTURE_DEBT.md`). Any
   RPO that treats the off-site copy as an independent failure domain is
   currently false, and this is a **precondition**, not a caveat — see §5.
6. **Production migration state.** Migration `20260906_0001` has been applied to
   staging only. A restore of a production archive into a production database
   is therefore a path nothing has exercised end to end.

## 3. What the current configuration already implies

Independently of any target the owner chooses, today's configuration *already*
commits to the following, and these are consequences rather than choices:

* **Worst-case data loss is bounded by the backup interval**, so with a daily
  01:30 backup the current de-facto RPO is **up to 24 hours**, and up to
  **26 hours** before anything alarms.
* There is **no continuous archiving, no oplog tailing and no point-in-time
  recovery.** Any RPO shorter than the backup interval requires new capability,
  not a new number.
* Recovery is **manual throughout** — no automated failover exists — so any RTO
  must include human response time, which §2 says is unmeasured.

## 4. Candidate policies for the owner to choose between

Each row is internally consistent: the target, what it costs, and what it needs
before it can be claimed.

| | **A — Conservative** | **B — Moderate** | **C — Aggressive** |
|---|---|---|---|
| **RPO** | 24 h | 6 h | ≤ 15 min |
| **RTO** | 8 h | 4 h | 1 h |
| Change needed | **none** — matches the daily cron already running | backups every 6 h; off-site retrieval timed | continuous oplog archiving / PITR; a warm standby |
| Restore-drill cadence | quarterly, staging | monthly, staging | monthly, at production scale |
| Evidence still owed | production-scale restore timing; off-site retrieval timing; detection time | all of A, plus a 6-hourly backup running and alarmed | all of B, plus PITR built, drilled and monitored |
| Honest today? | **Yes** — this is what the deployment already does | No — needs work first | No — needs new architecture |

**Recommendation: A.** It is the only row this deployment can claim without
building something first, and a recorded objective the system does not meet is
worse than an unrecorded one. A is also compatible with tightening later: B and
C are strictly additive.

**A is a recommendation, not a decision, and this note does not tick the
bullet.**

## 5. Preconditions before ANY row above may be recorded as met

1. **The shared S3 bucket must be separated** (`docs/S3_STORAGE_POSTURE_DEBT.md`).
   Until then the off-site copy is not an independent failure domain and no RPO
   may be stated as if it were. Carried as production-cutover debt; R-A8N did
   not touch production storage.
2. **A production-scale restore drill**, or an explicit owner acceptance that
   the target is based on staging-scale timings.
3. **Detection time must be estimated or measured**, because RTO starts at the
   event and not at the operator's first command.

## 6. How to record the decision

When the owner chooses:

1. Write the chosen RPO and RTO into `docs/OPERATIONS.md` next to the backup and
   restore procedures, with the date and who decided.
2. Tick Gate 8 bullet 7 in `docs/PRODUCTION_READINESS_RELEASE_GATE.md`, with the
   evidence line naming this note and the decision.
3. Re-run `scripts/production_readiness_score.py`; Gate 8 becomes 8/8.

Steps 1, 2 and 3 were carried out on 2026-09-08 (release programme R-A8O):
the decision is in [OPERATIONS.md](OPERATIONS.md) §5, Gate 8 bullet 7 is ticked in
[PRODUCTION_READINESS_RELEASE_GATE.md](PRODUCTION_READINESS_RELEASE_GATE.md) with an
evidence line that says RECORDED and not DEMONSTRATED, and
`scripts/production_readiness_score.py` re-derives **Gate 8 at 8 of 8 — 10.00/10**,
moving the total from 63/100 to **65/100**.
