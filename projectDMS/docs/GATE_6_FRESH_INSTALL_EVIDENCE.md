# Gate 6 bullet 2 — fresh install against real MongoDB

**Bullet, verbatim:** "Fresh install path is tested against real MongoDB."

**Catalogue coverage: 22 of 22 migrations**, as of the run of 2026-11-14 recorded
in "The 22-migration catalogue" below. The mapping that follows is the original
19-migration staging run and is kept as measured.

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

A new migration landing in `backend/rbac_backend/migrations/` after 2026-11-14
is not covered by this evidence: the 2026-09-08 staging run measured 19
migrations and the 2026-11-14 run below measured 22, and a twenty-third has
never been applied to an empty database.
`backend/rbac_backend/tests/test_gate6_fresh_install_evidence.py` fails when the
migration count moves away from the number recorded here, which forces the tick
to be re-earned rather than inherited.

## The 22-migration catalogue: run of 2026-11-14

**Disposition: migration path re-earned for 22 of 22.** Merging the Letter Bench
into the release line added three migrations after the 2026-09-08 run
(`20261114_0001 skill_registry`, `20261114_0002 seed_system_skills`,
`20261114_0003 letter_bench_indexes`). They were applied, with the other 19, to
an empty MongoDB by `scripts/gate6_fresh_install.sh`. The output below is that
script's evidence block, unedited apart from the step-log path.

### Where it ran, and what that means

- **Not on the staging host.** It ran in an ephemeral cloud development
  container, executed by the coding agent working on pull request #109, on the
  release owner's instruction of 2026-11-14 that a disposable, empty
  `mongo:8.0` is acceptable evidence for this bullet and an in-memory fake is
  not.
- **That is a departure from "a local run is never gate evidence"** in the
  section above, taken by owner decision and recorded here rather than left
  implicit. What separates this from a developer-machine test run: the server
  is a real `mongod` 8.0.38 in a one-member replica set, proved empty first;
  the commands are the documented install path, run inside the backend image;
  and the result is read back from the server with `mongosh`, not asserted by
  the code under test.
- **The same script runs unchanged on staging.** If a staging execution is
  wanted as well, run it there and add its block below this one.
- **Backend image.** Built from `backend/Dockerfile` at the commit named in the
  block, with one build-time difference: the container's outbound TLS is
  intercepted by a proxy, so its CA bundle was bind-mounted for the
  `pip install` instruction only. A bind mount leaves no file in the image, and
  no source file differs from the commit. That is why the block says the image
  was supplied rather than built by the run.

### What this run did not re-measure

The script exercises the migration path only and does not start the
application. Rows 5 and 6 of the mapping (the permission catalogue seeded at
backend start, and the application serving requests on the installed database)
keep their 2026-09-08 measurement on the 19-migration catalogue. The three new
migrations touch neither the `permissions` collection nor any collection the
Gate 2 and Gate 3 suites read. The two notices in the dry run and the apply are
the documentary notes `20260721_0001` has always carried; there were no
warnings.

### Evidence block

```text
GATE 6 BULLET 2 - FRESH INSTALL RUN  gate6-20261114t222928z-9390
Result            PASS - 22/22 migrations applied to an empty MongoDB
Date (UTC)        2026-11-14T22:29:50Z
Host              Linux 6.18.44-fc-v470 x86_64; docker 29.9.2
Commit            6076693b901889aaff57d04d22fb9b830e45d4c9 (claude/elegant-darwin-fkv2u7); uncommitted backend changes: 0
Backend image     contraclaim-gate6-backend:6076693b9018-sandboxca sha256:278243676c3a41cdf806be62c8a79bc192d69a09d37b87fedfb7aa19cd557fad
                  supplied (GATE6_BACKEND_IMAGE)
MongoDB           mongo:8.0 sha256:d97fea17ccb8d004cd137be8dab54d70d0df11be69c9f422aefe4484c8414ab7
                  server version 8.0.38; replica set read back from rs.status().set: gate6rs
                  disposable container on a private network: no published port, no host bind mount

Empty before anything ran
  databases=["admin","config","local"]
  user_databases=0
  target_database=gate6_fresh_install
  target_collections=0
  ledger_rows=0
  EMPTY=YES
Target read back from inside the backend container
  configured_host=gate6-20261114t222928z-9390-mongo
  database=gate6_fresh_install
  server_me=gate6-20261114t222928z-9390-mongo:27017
  server_set=gate6rs

Commands (each in a new backend container: docker run --rm --network <private> -e DATABASE_URL=mongodb://<disposable>:27017/gate6_fresh_install?replicaSet=gate6rs -e MONGODB_DATABASE=gate6_fresh_install -e MONGODB_REPLICA_SET=gate6rs -e ENVIRONMENT=development contraclaim-gate6-backend:6076693b9018-sandboxca)
  1. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=22 applied=0 pending=22 applied_not_in_catalogue=0
  2. python -m rbac_backend.scripts.migrate_database --fail-on-warning
       exit 0   total=22 applied=0 dry_run=22 skipped=0 warnings=0 notices=2; database still empty afterwards
  3. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
       exit 0   total=22 applied=22 dry_run=0 skipped=0 warnings=0 notices=2
  4. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning   (second apply)
       exit 0   total=22 applied=0 dry_run=0 skipped=22 warnings=0 notices=0
       collections=152 indexes=519 documents=29, identical before and after
  5. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=22 applied=22 pending=0 applied_not_in_catalogue=0

Letter Bench migrations 20261114_0001 to _0003, read with mongosh: 28 passed, 0 failed
  PASS  collection skills exists
  PASS  collection skill_versions exists
  PASS  collection skill_bindings exists
  PASS  collection letter_position_boards exists
  PASS  collection letter_skill_steps exists
  PASS  index skills.skill_owner_key_unique  -  key={"owner_scope":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_unique  -  key={"skill_key":1,"version":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_status  -  key={"skill_key":1,"status":1} unique=false partialFilterExpression=null
  PASS  index skill_bindings.skill_binding_scope_unique  -  key={"organization_id":1,"project_id":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_letter_unique  -  key={"letter_id":1} unique=true partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_scope_time  -  key={"organization_id":1,"project_id":1,"updated_at":-1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_document_unique  -  key={"organization_id":1,"incoming_document_id":1} unique=true partialFilterExpression={"incoming_document_id":{"$type":"string"}}
  PASS  index letter_position_boards.bench_board_trigger_unique  -  key={"organization_id":1,"trigger.type":1,"trigger.id":1} unique=true partialFilterExpression={"trigger.id":{"$type":"string"}}
  PASS  index letter_skill_steps.bench_step_letter_time  -  key={"letter_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_idempotency_unique  -  key={"letter_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  partial unique indexes expected  -  2 declared
  PASS  skill_versions holds exactly the three system skills  -  3 rows
  PASS  skill indian-infra-contract-letters seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-letter-analysis seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-variations seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skills holds exactly the three system skills  -  3 rows
  PASS  skill indian-infra-contract-letters active, latest published 1.0.0
  PASS  skill indian-infra-letter-analysis active, latest published 1.0.0
  PASS  skill indian-infra-variations active, latest published 1.0.0
  PASS  ledger records 20261114_0001
  PASS  ledger records 20261114_0002
  PASS  ledger records 20261114_0003
Seeded skills (key, version, content hash) - equal to the files bundled in the image: YES
  indian-infra-contract-letters 1.0.0 818a6ad8f429feedbac0732bce835af5948992af029e2ddc6a507f9847151754
  indian-infra-letter-analysis 1.0.0 4fb6f430b50aa8e166ebe044f500597a794aa36f26e9f6020947ae841fe9be53
  indian-infra-variations 1.0.0 49bb0d50e9a05301004d567904ad00b4fd5a4fefe50cf30df20b41823e1f62ad
Unique indexes probed with real inserts (probe rows removed afterwards): 9 passed, 0 failed
  PASS  two boards without a document are both accepted
  PASS  two boards with a null document are both accepted
  PASS  first board for a document is accepted
  PASS  second board for the same document is refused  -  E11000 bench_board_document_unique
  PASS  first board for a trigger record is accepted
  PASS  second board for the same trigger record is refused  -  E11000 bench_board_trigger_unique
  PASS  second board for the same letter is refused  -  E11000 bench_board_letter_unique
  PASS  a second 1.0.0 of a seeded skill is refused  -  E11000 skill_version_unique
  PASS  probe rows removed  -  0 left

Step logs         not retained: the container that ran this was ephemeral; this block is the record
```

### Reproducing it

```bash
cd projectDMS && bash scripts/gate6_fresh_install.sh
```

The script starts its own MongoDB on a private docker network, reads no
`DATABASE_URL`, env file or compose project, removes what it started, and
changes no tracked file. It exits non-zero without an evidence block if any
step is not exactly as expected.
