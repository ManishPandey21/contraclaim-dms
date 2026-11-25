# Phase 4b readings P-01 to P-16: operator procedure and evidence template

Date: 2026-11-17 · Owner decision AQ-11 (2026-11-17): the readings remain mandatory.
An unavailable reading is recorded as **not read**, with its reason. It is never
treated as zero, and an unreadable database is never treated as an empty one.

Plan: `docs/superpowers/specs/2026-11-16-claim-bench-phase-4b-hardening.md` §8.1.
Results are recorded in `docs/POST_CUTOVER_OPERATIONAL_ASSESSMENT_2026-11-15.md` §6.

## 1. Who runs this, and what it touches

- **Operator:** a person with authorised shell access to the host, named by the
  release owner. The implementation sessions have no production or staging access
  and do not run it.
- **Read-only.** The script uses `docker ps`, `inspect`, `logs` and `exec` of
  commands that read. Its database reader allows `find`, `count_documents`,
  `estimated_document_count`, `distinct` and `aggregate` only, and refuses `$out` and
  `$merge`. It starts, stops and changes nothing, and writes one report directory on
  the host.
- **No secret is typed, pasted or printed.** The operator never puts a credential
  on a command line and never pastes one into a chat, ticket or this repository.
  The script reads what it needs from the host's own env file.
- **Counts only.** The readings print counts, type names and configuration flags.
  They print no name, email address, document title or record id.

## 2. Before you start

1. Confirm the environment you are on (production or staging) and write it down.
   Run the procedure once per environment; never merge the two reports.
2. Read the checkout's branch and commit. They are reading P-01, and they decide
   whether the script is present:

   ```bash
   cd /opt/contraclaim-dms/projectDMS && git branch --show-current && git log -1 --format='%h %cs %s'
   ```

3. The reader `scripts/claim_arbitration_readonly_readings.py` is on
   `claude/zen-ptolemy-695liz`, not on the deployed release. Do **not** change the
   deployed checkout to get it. Copy the two scripts to a directory outside the
   checkout instead:

   ```bash
   mkdir -p /tmp/phase4b-readings && cd /tmp/phase4b-readings
   ```

   Place `post_cutover_readonly_checks.sh` and
   `claim_arbitration_readonly_readings.py` there, taken from the branch at the
   commit named in the evidence template (section 5). Record that commit.

## 3. Run

Point the script at the deployed checkout and at a report directory you own:

```bash
PROJECT_DIR=/opt/contraclaim-dms/projectDMS OUT_DIR=/tmp/phase4b-readings bash /tmp/phase4b-readings/post_cutover_readonly_checks.sh
```

If the script looks for the reader under `PROJECT_DIR/scripts` and does not find
it, section F reports each reading as not read. In that case run the reader alone,
piped into the running backend container so it uses that container's own database
settings (replace `<backend>` with the container name from `docker ps`):

```bash
docker exec -i <backend> python - < /tmp/phase4b-readings/claim_arbitration_readonly_readings.py > /tmp/phase4b-readings/readings.jsonl
```

The first output line (`"reading": "DB"`) names the database and says whether it
looks like the application's. If it does not, every reading is `not_read`: stop and
report that, do not retry against another database by hand.

## 4. Sanitise before anything leaves the host

Scan the report directory **by value** against the host's env files. The scanner
prints counts, never a secret:

```bash
python3 /opt/contraclaim-dms/projectDMS/scripts/evidence_secret_scan.py /tmp/phase4b-readings /opt/contraclaim-dms/projectDMS/.env
```

- **CLEAN:** the report may be attached to the assessment document.
- **Any finding:** do not copy the report anywhere. Tell the release owner which
  file was flagged (the file name only) and delete the report directory.

Then remove the copied scripts and, once the report is filed, the report directory.

## 5. Evidence template

Copy this block into `docs/POST_CUTOVER_OPERATIONAL_ASSESSMENT_2026-11-15.md` §6,
one block per environment. Leave a reading as "Not read" rather than guessing.

```text
Environment:            production | staging
Evidence class:         Production — verified | Staging — verified
Date and time (UTC):
Operator (role, not credentials):
Access used:            (e.g. SSH to the host, read-only procedure)
Script commit:          (branch commit the two scripts were taken from)
Secret scan result:     CLEAN | findings (report not attached)
DB line:                (database name; looks like the application's: yes | no)

Reading | Status (read | not read) | Result (counts only) | Reason if not read
P-01 deployed commit and image labels        |  |  |
P-02 migration ledger                        |  |  |
P-03 claims by status and value types        |  |  |
P-04 sla_rules value types                   |  |  |
P-05 reviewer role documents and holders     |  |  |
P-06 users with more than one role           |  |  |
P-07 arbitration cases, drafts, matrix rows  |  |  |
P-08 skill_* collections                     |  |  |
P-09 projects with contractual_capacity      |  |  |
P-10 ARBITRATION_REVIEWER_ROLE_MATRIX set    |  |  |
P-11 use of claims register and assessment   |  |  |
P-12 case-less draft callers, 90 days        |  |  |
P-13 arbitration cases hidden by null deleted_at |  |  |
P-14 notice requirements by status and origin |  |  |
P-15 extraction-review holders by path       |  |  |
P-16 second-contract key dates from main LOA  |  |  |

Anything unexpected (free text, no names, no ids):
```

## 6. Readings unavailable as of 2026-11-17, and what waits on each

Every row below is **not read** on production and on staging. Reason, for all of
them except P-01: no authorised operator has yet run this procedure, and the
implementation sessions have no access to either environment (AQ-11). The reader
exists and is tested against a disposable database only, which is not a reading.

| Reading | Production | Staging | Tickets and decisions that wait on it |
|---|---|---|---|
| P-01 | Checkout read 2026-11-15 (`release/contraclaim-rc1`, `34d664a`). Running image labels **not read** | Not read | Baseline for every other reading; release ordering |
| P-02 | Not read | Not read | Release order of M-01 to M-05 against the unapplied `20261114_*` migrations |
| P-03 | Not read | Not read | 4B-18 (contract identity, M-04), 4B-19 (legacy claim coercion rules), D-10 |
| P-04 | Not read | Not read | 4B-12 `sla_rules` coercion: built to coerce defensively; confirmation of what production holds |
| P-05 | Not read | Not read | 4B-06 (seeding reviewer identities, M-02 conflicts), 4B-07 legacy-role reconciliation report, AQ-3 |
| P-06 | Not read | Not read | 4B-08: how many users were exposed to the Users screen replacing their role list |
| P-07 | Not read | Not read | Effect of the scratch-draft migration and of the B2 blockers on drafts in flight; 4B-45 |
| P-08 | Not read | Not read | M-01: `skill_*` collections expected absent at `34d664a`; unconfirmed |
| P-09 | Not read | Not read | 4B-15: whether any project already records a contractual capacity |
| P-10 | Not read | Not read | AQ-3: whether the arbitration reviewer matrix is overridden; 4B-06 |
| P-11 | Not read | Not read | D-09 and AQ-8: retirement of the legacy claim page and AI assessment |
| P-12 | Not read | Not read | 4B-45: switching `ARBITRATION_CASELESS_DRAFT_MODE` to strict |
| P-13 | Not read | Not read | C-25: whether the arbitration case-lookup hotfix proposal is taken forward |
| P-14 | Not read | Not read | 4B-14: whether legacy notice periods need re-verification |
| P-15 | Not read | Not read | 4B-49 steps 2 to 5: no existing extraction-review access is withdrawn before it (AQ-12) |
| P-16 | Not read | Not read | KD-1: whether any stored key date of a contract other than the main one was calculated from the main contract's LOA, and so whether a correction (recalculation, with approval) is needed at all |

**What continues without the readings:** code, tests and migrations that add
indexes or seed skip-existing identities, built and proven against representative
data. **What does not:** applying any migration to production or staging, choosing
seeding or backfill rules from assumed data, switching a compatibility mode, or
withdrawing any access.
