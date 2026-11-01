# Gate 6 bullet 2 — fresh install against real MongoDB

**Bullet, verbatim:** "Fresh install path is tested against real MongoDB."

**Disposition: SATISFIED by the R-A8Q Stage B staging run, 2026-09-08.** No new
staging window was needed; this file exists because the run's own receipt lives
under `.claude/`, which is gitignored and does not survive a clone, and a gate
that cites evidence nobody can read after cloning is citing nothing.

## Why this file, and what it is not

R-A8S is an offline phase. It did not re-run anything. What it did was read the
bullet literally, decompose it, and check each element against evidence that
already existed — the reviewer's job, not the operator's. Where an element is
met, the measurement that meets it is quoted. Where it is not, it is left
unticked and named.

This file is **not** a claim about Gate 6 bullet 3 ("existing database upgrade
path is tested against a staging copy"). That bullet asks for a *production copy*
restored into staging and then migrated, which R-A8Q did not do — the staging
database was created empty. Bullet 3 is still open and is R-A8T's work.

## The mapping

| # | Literal requirement | R-A8Q evidence | Where | Pass? |
|---|---|---|---|---|
| 1 | **Fresh** — the database starts with no prior schema, data or migration ledger | Staging brought up as its own compose project `contraclaim-stg`: **9 named volumes, all `contraclaim-stg_*`, 0 external**, so the Mongo data directory was new. Corroborated by the migration runner itself: the first apply reported **19 applied**, which a database carrying a ledger could not report | R-A8Q receipt §13 (render and guard), §16 | **YES** |
| 2 | **Install path** — the project's own documented mechanism, not hand-run DDL | `python -m rbac_backend.scripts.migrate_database`, the entry point `CLAUDE.md` and `docs/OPERATIONS.md` name, run inside the backend container | R-A8Q receipt §16 | **YES** |
| 3 | **Tested** — executed and observed, not asserted | `--list` exit 0, 19 migrations, `20260906_0001 permission_name_unique` present. Dry run `--fail-on-warning` **exit 0, `warnings: []`**. Apply **exit 0, 19 applied**. Second apply **exit 0, 0 newly applied, 19 skipped** — idempotent. `--list` after: **19/19 applied** | R-A8Q receipt §16 | **YES** |
| 4 | **Real MongoDB** — a real server, not a fake or an in-memory double | Target proved *before* anything ran: `MONGODB_DATABASE = contraclaim_staging`, configured `replicaSet = rsstg`, and `rs.status().set` read back as `rsstg` — a live replica set, and the read-back is what distinguishes a real target from a configured one | R-A8Q receipt §16 (opening paragraph) | **YES** |
| 5 | **Seeds** — a fresh install includes the permission catalogue, or "install" means only the indexes | `uq_permissions_name` present, `unique: true`, key `{name:1}`. **198 total == 198 distinct, 0 duplicates.** A backend restart re-seeded and left it at 198/0 — idempotent. The index then refused a real re-seed attempt with `E11000 duplicate key` on all 198, which is migration `20260906_0001`'s purpose observed rather than asserted | R-A8Q receipt §16 (permission catalogue) | **YES** |
| 6 | **The application works on it** — an install that migrates but cannot serve is not an install | The same stack served Gate 2 (**45 collected, 45 executed, 0 skips**) and Gate 3 bullet 1 (**4/4 over TLS, unmocked**) against that database, and the app's own initializer seeded an account on it | R-A8Q receipt §17, §19; `docs/PRODUCTION_READINESS_RELEASE_GATE.md` Gate 2 and Gate 3 bullet 1 | **YES** |

**Six of six.** No element of the bullet is unevidenced, and no element is
satisfied by inference from another.

## What was deliberately not counted

- **Bullet 3.** Nothing here touches the upgrade path. A fresh install and a
  restored production copy are different failure surfaces; conflating them is how
  a migration that only works on an empty database reaches production.
- **A local run.** Gate 2's rule — a developer-machine run is a precondition and
  never gate evidence — is applied here too. `test_migration_runner.py` and
  `test_migration_warning_classification.py` are preconditions; the staging
  execution is the evidence.
- **The dry run alone.** `--fail-on-warning` exiting 0 says the plan is clean,
  not that it applied. The apply, the second apply and the post-apply `--list`
  are what close the bullet.

## What would reopen it

A new migration landing in `backend/rbac_backend/migrations/` after 2026-09-08
is not covered by this evidence: the run measured 19 migrations, and a twentieth
has never been applied to an empty database.
`backend/rbac_backend/tests/test_gate6_fresh_install_evidence.py` fails when the
migration count moves away from the number recorded here, which forces the tick
to be re-earned rather than inherited.
