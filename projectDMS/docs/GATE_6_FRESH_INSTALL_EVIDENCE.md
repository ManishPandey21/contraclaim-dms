# Gate 6 bullet 2 — fresh install against real MongoDB

**Bullet, verbatim:** "Fresh install path is tested against real MongoDB."

**Catalogue coverage: 30 of 30 migrations**, as of the run of 2026-11-17
recorded in "The 30-migration catalogue" below; the earlier runs for 28, 27, 26,
25, 24, 23 and 22 are kept after it. The mapping that follows is the original
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

## The 30-migration catalogue: run of 2026-11-17

**Disposition: migration path measured for 30 of 30; acceptance as gate
evidence is the release owner's to confirm**, on the same footing as the runs
below. Two migrations were added (plan 4B-06 and 4B-07, D-06):

- `20261121_0001 reviewer_role_identities` (M-02) seeds nine reviewer role
  documents with no permissions. It is skip-existing. On an empty database it
  inserts nine. On a database that already holds a role document with one of
  the keys or names it reports a warning for that role and leaves the document
  unchanged.
- `20261121_0002 reviewer_role_assignment_indexes` (M-03) adds three indexes to
  `reviewer_role_assignments`, one of them a partial unique index: one live
  assignment per person, role and scope.

**What this run does not show.** The database is empty, so the seed's conflict
path and the unique index's refusal are not exercised here. They are proven on
a real replica set by `tests/integration/test_reviewer_assignments_mongo.py`
(a pre-existing role document is reported and untouched; five concurrent grants
leave one live assignment). The fresh-install script was not extended with
probes for these two migrations. **Production:** M-02 has not been run against
any copy of production data. Reading P-05 is not read, so whether production
holds conflicting role documents is unknown.

**Backend image.** Built as for the 28-migration run, from the commit in the
block, with `GATE6_EXPECTED_MIGRATIONS=30`. No source file differs from the
commit.

### Evidence block

```text
GATE 6 BULLET 2 - FRESH INSTALL RUN  gate6-20261117t061637z-22234
Result            PASS - 30/30 migrations applied to an empty MongoDB
Date (UTC)        2026-11-17T06:17:14Z
Host              Linux 6.18.44-fc-v553 x86_64; docker 29.9.2
Commit            8726558837bfaea841957739a7feba98eefdaba8 (claude/zen-ptolemy-695liz); uncommitted backend changes: 0
Backend image     contraclaim-gate6-backend:8726558837bf-sandboxca sha256:cfb80a7bdc3f8e7380174cecde4dda30acf8b87e4172927638327526f55cbe47
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
  configured_host=gate6-20261117t061637z-22234-mongo
  database=gate6_fresh_install
  server_me=gate6-20261117t061637z-22234-mongo:27017
  server_set=gate6rs

Commands (each in a new backend container: docker run --rm --network <private> -e DATABASE_URL=mongodb://<disposable>:27017/gate6_fresh_install?replicaSet=gate6rs -e MONGODB_DATABASE=gate6_fresh_install -e MONGODB_REPLICA_SET=gate6rs -e ENVIRONMENT=development contraclaim-gate6-backend:8726558837bf-sandboxca)
  1. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=30 applied=0 pending=30 applied_not_in_catalogue=0
  2. python -m rbac_backend.scripts.migrate_database --fail-on-warning
       exit 0   total=30 applied=0 dry_run=30 skipped=0 warnings=0 notices=14; database still empty afterwards
  3. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
       exit 0   total=30 applied=30 dry_run=0 skipped=0 warnings=0 notices=14
  4. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning   (second apply)
       exit 0   total=30 applied=0 dry_run=0 skipped=30 warnings=0 notices=0
       collections=161 indexes=560 documents=50, identical before and after
  5. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=30 applied=30 pending=0 applied_not_in_catalogue=0

Skill and claim migrations 20261114_0001 to 20261120_0001, read with mongosh: 72 passed, 0 failed
  PASS  collection skills exists
  PASS  collection skill_versions exists
  PASS  collection skill_bindings exists
  PASS  collection skill_approvals exists
  PASS  collection letter_position_boards exists
  PASS  collection letter_skill_steps exists
  PASS  collection skill_steps exists
  PASS  collection claim_notice_requirements exists
  PASS  collection claim_head_links exists
  PASS  collection claim_determinations exists
  PASS  collection claim_privileged exists
  PASS  collection claim_submissions exists
  PASS  index skills.skill_owner_key_unique  -  key={"owner_scope":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_unique  -  key={"skill_key":1,"version":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_status  -  key={"skill_key":1,"status":1} unique=false partialFilterExpression=null
  PASS  index skill_bindings.skill_binding_scope_unique  -  key={"organization_id":1,"project_id":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_approvals.skill_approvals_by_version  -  key={"skill_key":1,"version":1,"decided_at":1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_letter_unique  -  key={"letter_id":1} unique=true partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_scope_time  -  key={"organization_id":1,"project_id":1,"updated_at":-1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_document_unique  -  key={"organization_id":1,"incoming_document_id":1} unique=true partialFilterExpression={"incoming_document_id":{"$type":"string"}}
  PASS  index letter_position_boards.bench_board_trigger_unique  -  key={"organization_id":1,"trigger.type":1,"trigger.id":1} unique=true partialFilterExpression={"trigger.id":{"$type":"string"}}
  PASS  index letter_skill_steps.bench_step_letter_time  -  key={"letter_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_idempotency_unique  -  key={"letter_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_idempotency_unique  -  key={"subject_type":1,"subject_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_parent_time  -  key={"parent_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_notice_requirements.notice_requirement_current_unique  -  key={"organization_id":1,"project_id":1,"claim_type":1} unique=true partialFilterExpression={"status":"current"}
  PASS  index claim_notice_requirements.notice_requirement_history  -  key={"organization_id":1,"project_id":1,"claim_type":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claims.claim_origin_unique  -  key={"organization_id":1,"origin.type":1,"origin.id":1} unique=true partialFilterExpression={"origin.id":{"$type":"string"}}
  PASS  index claim_head_links.claim_head_link_active_unique  -  key={"claim_id":1,"head_id":1,"target_type":1,"target_id":1} unique=true partialFilterExpression={"status":"active"}
  PASS  index claim_head_links.claim_head_link_claim_time  -  key={"claim_id":1,"status":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_head_links.claim_head_link_target  -  key={"organization_id":1,"target_type":1,"target_id":1,"status":1} unique=false partialFilterExpression=null
  PASS  index claim_determinations.claim_determination_revision_unique  -  key={"claim_id":1,"origin":1,"revision":1} unique=true partialFilterExpression=null
  PASS  index claim_determinations.claim_determination_scope_status  -  key={"organization_id":1,"project_id":1,"status":1} unique=false partialFilterExpression=null
  PASS  index claim_privileged.claim_privileged_claim_kind_time  -  key={"claim_id":1,"kind":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claim_privileged.claim_privileged_one_strategy_note  -  key={"claim_id":1} unique=true partialFilterExpression={"kind":"strategy_note"}
  PASS  index claim_submissions.claim_submission_claim_unique  -  key={"claim_id":1} unique=true partialFilterExpression=null
  PASS  index claims.claim_arbitration_case  -  key={"organization_id":1,"arbitration_case_id":1} unique=false partialFilterExpression={"arbitration_case_id":{"$type":"string"}}
  PASS  index arbitration_cases.arbitration_case_claim_ids  -  key={"organization_id":1,"claim_ids":1} unique=false partialFilterExpression=null
  PASS  index arbitration_chronology_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_claim_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_clause_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_counterclaim_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_defence_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_document_index.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_expert_alignment.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_issue_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_jurisdiction_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_notice_compliance.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_quantum_annexures.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_rejoinder_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  partial indexes expected  -  19 declared
  PASS  skill_versions holds exactly the three published skills and the four draft packs  -  7 rows
  PASS  skill indian-infra-arbitration-hearing stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:arbitration_case_preparation_expert"]
  PASS  skill indian-infra-arbitration-pleadings stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-contract-letters seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-eot-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert","technical:delay_planning_expert"]
  PASS  skill indian-infra-letter-analysis seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-variations seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skills holds exactly the three published skills (a draft gets no latest-published pointer)  -  3 rows
  PASS  skill indian-infra-contract-letters active, latest published 1.0.0
  PASS  skill indian-infra-letter-analysis active, latest published 1.0.0
  PASS  skill indian-infra-variations active, latest published 1.0.0
  PASS  no skill approval exists on a fresh install  -  0 rows
  PASS  ledger records 20261114_0001
  PASS  ledger records 20261114_0002
  PASS  ledger records 20261114_0003
  PASS  ledger records 20261115_0001
  PASS  ledger records 20261116_0001
  PASS  ledger records 20261117_0001
Seeded skills (key, version, content hash) - equal to the files bundled in the image: YES
  indian-infra-arbitration-hearing 1.0.0 c4f100f0ed20d848554aed28c414c0fd84aecb4de76e9f76880e1e8199aafeed
  indian-infra-arbitration-pleadings 1.0.0 8af9e4d95a0a24da65d9e27b67e468518f068478fbf55c1cff52eebcee47a36c
  indian-infra-claims 1.0.0 5f883bf0c2b1f5f6b7c38da6db506d86797e798fb8fcd2af44b54c52a149e1c6
  indian-infra-contract-letters 1.0.0 818a6ad8f429feedbac0732bce835af5948992af029e2ddc6a507f9847151754
  indian-infra-eot-claims 1.0.0 e37c1e84b7ef8250c81f4737247e313968f1edb3448b5d5b2057deb0b610c6b0
  indian-infra-letter-analysis 1.0.0 4fb6f430b50aa8e166ebe044f500597a794aa36f26e9f6020947ae841fe9be53
  indian-infra-variations 1.0.0 49bb0d50e9a05301004d567904ad00b4fd5a4fefe50cf30df20b41823e1f62ad
Unique indexes probed with real inserts (probe rows removed afterwards): 36 passed, 0 failed
  PASS  two boards without a document are both accepted
  PASS  two boards with a null document are both accepted
  PASS  first board for a document is accepted
  PASS  second board for the same document is refused  -  E11000 bench_board_document_unique
  PASS  first board for a trigger record is accepted
  PASS  second board for the same trigger record is refused  -  E11000 bench_board_trigger_unique
  PASS  second board for the same letter is refused  -  E11000 bench_board_letter_unique
  PASS  a second 1.0.0 of a seeded skill is refused  -  E11000 skill_version_unique
  PASS  a second 1.0.0 of a draft pack is refused  -  E11000 skill_version_unique
  PASS  first current notice period is accepted
  PASS  a second current notice period for the same project and claim type is refused  -  E11000 notice_requirement_current_unique
  PASS  superseded notice periods are kept as history
  PASS  another claim type on the project is accepted
  PASS  two claims with no origin are both accepted
  PASS  first claim raised from a hindrance is accepted
  PASS  a second claim raised from the same hindrance is refused  -  E11000 claim_origin_unique
  PASS  a claim raised from a letter with the same id is a different origin
  PASS  first skill step is accepted
  PASS  the same skill step replayed is refused  -  E11000 skill_step_idempotency_unique
  PASS  first link from a head to a hindrance is accepted
  PASS  a second active link from the same head to the same hindrance is refused  -  E11000 claim_head_link_active_unique
  PASS  removed links to it are kept as history
  PASS  the same hindrance can be linked to another head
  PASS  first determination revision is accepted
  PASS  the same revision of an authored determination is refused  -  E11000 claim_determination_revision_unique
  PASS  a received determination may carry the same revision number
  PASS  a strategy note is accepted
  PASS  a second strategy note for the claim is refused  -  E11000 claim_privileged_one_strategy_note
  PASS  simulations of the same claim are many
  PASS  first submission for a claim is accepted
  PASS  a second submission for the claim is refused  -  E11000 claim_submission_claim_unique
  PASS  first escalated row is accepted
  PASS  the same origin in the same case is refused  -  E11000 matrix_case_origin_unique
  PASS  rows added by hand are unconstrained
  PASS  the same origin in another case is accepted
  PASS  probe rows removed  -  0 left

Step logs         /tmp/claude-0/-home-user-contraclaim-dms/ad14b91e-b109-5ad0-a648-ab64e72d2276/scratchpad/gate6/out
```

## The 28-migration catalogue: third run of 2026-11-16

**Disposition: migration path measured for 28 of 28; acceptance as gate
evidence is the release owner's to confirm**, on the same footing as the runs
below. The owner's decision of 2026-11-14 accepting a disposable, empty
`mongo:8.0` for this bullet has not been restated for this run. One migration
was added: `20261120_0001 arbitration_matrix_origin_unique` (plan 4B-36, M-05).
It adds a partial unique index on (`case_id`, `origin_key`), where `origin_key`
is a string, to each of the 12 arbitration matrix collections, so an escalated
claim's row is carried into a case once. On an empty database it creates the
12 indexes. On a database that already holds duplicate (case, origin) rows it
reports a warning per collection and does not create that collection's index;
it deletes and merges nothing. That path is covered by
`tests/integration/test_claim_escalation_safety_mongo.py`, not by this run,
which has no rows.

**Backend image.** Built by `docker build` from `backend/Dockerfile` at the
commit in the block, host network, with the proxy CA mounted as a build secret.
One difference from the earlier runs: the sandbox proxy now refuses plain-HTTP
apt fetches, so the `apt-get` instruction also used the secret, with the Debian
sources switched to HTTPS and an apt CA setting for that instruction only; the
same instruction restored both, and the built image was checked to hold
neither (and no CA file). No source file differs from the commit.

**What the script now also checks.** The 12 new indexes with their partial
filter (the script's own count of partial indexes moves from 7 to 19), and four
probes against the claim matrix: the first escalated row is accepted, the same
origin in the same case is refused by `matrix_case_origin_unique`, rows added by
hand are unconstrained, and the same origin in another case is accepted.

### Evidence block

```text
GATE 6 BULLET 2 - FRESH INSTALL RUN  gate6-20261116t171756z-10107
Result            PASS - 28/28 migrations applied to an empty MongoDB
Date (UTC)        2026-11-16T17:18:25Z
Host              Linux 6.18.44-fc-v470 x86_64; docker 29.9.2
Commit            c468402a0a2ebfc396d86140147a9e7c4d1fbf00 (HEAD); uncommitted backend changes: 0
Backend image     contraclaim-gate6-backend:c468402a0a2e-sandboxca sha256:1e63c56de596757279ecd2a7c9157cd586857ef9e97735e1d4092c1501ab695f
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
  configured_host=gate6-20261116t171756z-10107-mongo
  database=gate6_fresh_install
  server_me=gate6-20261116t171756z-10107-mongo:27017
  server_set=gate6rs

Commands (each in a new backend container: docker run --rm --network <private> -e DATABASE_URL=mongodb://<disposable>:27017/gate6_fresh_install?replicaSet=gate6rs -e MONGODB_DATABASE=gate6_fresh_install -e MONGODB_REPLICA_SET=gate6rs -e ENVIRONMENT=development contraclaim-gate6-backend:c468402a0a2e-sandboxca)
  1. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=28 applied=0 pending=28 applied_not_in_catalogue=0
  2. python -m rbac_backend.scripts.migrate_database --fail-on-warning
       exit 0   total=28 applied=0 dry_run=28 skipped=0 warnings=0 notices=11; database still empty afterwards
  3. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
       exit 0   total=28 applied=28 dry_run=0 skipped=0 warnings=0 notices=11
  4. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning   (second apply)
       exit 0   total=28 applied=0 dry_run=0 skipped=28 warnings=0 notices=0
       collections=159 indexes=555 documents=39, identical before and after
  5. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=28 applied=28 pending=0 applied_not_in_catalogue=0

Skill and claim migrations 20261114_0001 to 20261120_0001, read with mongosh: 72 passed, 0 failed
  PASS  collection skills exists
  PASS  collection skill_versions exists
  PASS  collection skill_bindings exists
  PASS  collection skill_approvals exists
  PASS  collection letter_position_boards exists
  PASS  collection letter_skill_steps exists
  PASS  collection skill_steps exists
  PASS  collection claim_notice_requirements exists
  PASS  collection claim_head_links exists
  PASS  collection claim_determinations exists
  PASS  collection claim_privileged exists
  PASS  collection claim_submissions exists
  PASS  index skills.skill_owner_key_unique  -  key={"owner_scope":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_unique  -  key={"skill_key":1,"version":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_status  -  key={"skill_key":1,"status":1} unique=false partialFilterExpression=null
  PASS  index skill_bindings.skill_binding_scope_unique  -  key={"organization_id":1,"project_id":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_approvals.skill_approvals_by_version  -  key={"skill_key":1,"version":1,"decided_at":1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_letter_unique  -  key={"letter_id":1} unique=true partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_scope_time  -  key={"organization_id":1,"project_id":1,"updated_at":-1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_document_unique  -  key={"organization_id":1,"incoming_document_id":1} unique=true partialFilterExpression={"incoming_document_id":{"$type":"string"}}
  PASS  index letter_position_boards.bench_board_trigger_unique  -  key={"organization_id":1,"trigger.type":1,"trigger.id":1} unique=true partialFilterExpression={"trigger.id":{"$type":"string"}}
  PASS  index letter_skill_steps.bench_step_letter_time  -  key={"letter_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_idempotency_unique  -  key={"letter_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_idempotency_unique  -  key={"subject_type":1,"subject_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_parent_time  -  key={"parent_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_notice_requirements.notice_requirement_current_unique  -  key={"organization_id":1,"project_id":1,"claim_type":1} unique=true partialFilterExpression={"status":"current"}
  PASS  index claim_notice_requirements.notice_requirement_history  -  key={"organization_id":1,"project_id":1,"claim_type":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claims.claim_origin_unique  -  key={"organization_id":1,"origin.type":1,"origin.id":1} unique=true partialFilterExpression={"origin.id":{"$type":"string"}}
  PASS  index claim_head_links.claim_head_link_active_unique  -  key={"claim_id":1,"head_id":1,"target_type":1,"target_id":1} unique=true partialFilterExpression={"status":"active"}
  PASS  index claim_head_links.claim_head_link_claim_time  -  key={"claim_id":1,"status":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_head_links.claim_head_link_target  -  key={"organization_id":1,"target_type":1,"target_id":1,"status":1} unique=false partialFilterExpression=null
  PASS  index claim_determinations.claim_determination_revision_unique  -  key={"claim_id":1,"origin":1,"revision":1} unique=true partialFilterExpression=null
  PASS  index claim_determinations.claim_determination_scope_status  -  key={"organization_id":1,"project_id":1,"status":1} unique=false partialFilterExpression=null
  PASS  index claim_privileged.claim_privileged_claim_kind_time  -  key={"claim_id":1,"kind":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claim_privileged.claim_privileged_one_strategy_note  -  key={"claim_id":1} unique=true partialFilterExpression={"kind":"strategy_note"}
  PASS  index claim_submissions.claim_submission_claim_unique  -  key={"claim_id":1} unique=true partialFilterExpression=null
  PASS  index claims.claim_arbitration_case  -  key={"organization_id":1,"arbitration_case_id":1} unique=false partialFilterExpression={"arbitration_case_id":{"$type":"string"}}
  PASS  index arbitration_cases.arbitration_case_claim_ids  -  key={"organization_id":1,"claim_ids":1} unique=false partialFilterExpression=null
  PASS  index arbitration_chronology_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_claim_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_clause_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_counterclaim_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_defence_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_document_index.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_expert_alignment.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_issue_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_jurisdiction_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_notice_compliance.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_quantum_annexures.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  index arbitration_rejoinder_matrix.matrix_case_origin_unique  -  key={"case_id":1,"origin_key":1} unique=true partialFilterExpression={"origin_key":{"$type":"string"}}
  PASS  partial indexes expected  -  19 declared
  PASS  skill_versions holds exactly the three published skills and the four draft packs  -  7 rows
  PASS  skill indian-infra-arbitration-hearing stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:arbitration_case_preparation_expert"]
  PASS  skill indian-infra-arbitration-pleadings stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-contract-letters seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-eot-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert","technical:delay_planning_expert"]
  PASS  skill indian-infra-letter-analysis seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-variations seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skills holds exactly the three published skills (a draft gets no latest-published pointer)  -  3 rows
  PASS  skill indian-infra-contract-letters active, latest published 1.0.0
  PASS  skill indian-infra-letter-analysis active, latest published 1.0.0
  PASS  skill indian-infra-variations active, latest published 1.0.0
  PASS  no skill approval exists on a fresh install  -  0 rows
  PASS  ledger records 20261114_0001
  PASS  ledger records 20261114_0002
  PASS  ledger records 20261114_0003
  PASS  ledger records 20261115_0001
  PASS  ledger records 20261116_0001
  PASS  ledger records 20261117_0001
Seeded skills (key, version, content hash) - equal to the files bundled in the image: YES
  indian-infra-arbitration-hearing 1.0.0 c4f100f0ed20d848554aed28c414c0fd84aecb4de76e9f76880e1e8199aafeed
  indian-infra-arbitration-pleadings 1.0.0 8af9e4d95a0a24da65d9e27b67e468518f068478fbf55c1cff52eebcee47a36c
  indian-infra-claims 1.0.0 5f883bf0c2b1f5f6b7c38da6db506d86797e798fb8fcd2af44b54c52a149e1c6
  indian-infra-contract-letters 1.0.0 818a6ad8f429feedbac0732bce835af5948992af029e2ddc6a507f9847151754
  indian-infra-eot-claims 1.0.0 e37c1e84b7ef8250c81f4737247e313968f1edb3448b5d5b2057deb0b610c6b0
  indian-infra-letter-analysis 1.0.0 4fb6f430b50aa8e166ebe044f500597a794aa36f26e9f6020947ae841fe9be53
  indian-infra-variations 1.0.0 49bb0d50e9a05301004d567904ad00b4fd5a4fefe50cf30df20b41823e1f62ad
Unique indexes probed with real inserts (probe rows removed afterwards): 36 passed, 0 failed
  PASS  two boards without a document are both accepted
  PASS  two boards with a null document are both accepted
  PASS  first board for a document is accepted
  PASS  second board for the same document is refused  -  E11000 bench_board_document_unique
  PASS  first board for a trigger record is accepted
  PASS  second board for the same trigger record is refused  -  E11000 bench_board_trigger_unique
  PASS  second board for the same letter is refused  -  E11000 bench_board_letter_unique
  PASS  a second 1.0.0 of a seeded skill is refused  -  E11000 skill_version_unique
  PASS  a second 1.0.0 of a draft pack is refused  -  E11000 skill_version_unique
  PASS  first current notice period is accepted
  PASS  a second current notice period for the same project and claim type is refused  -  E11000 notice_requirement_current_unique
  PASS  superseded notice periods are kept as history
  PASS  another claim type on the project is accepted
  PASS  two claims with no origin are both accepted
  PASS  first claim raised from a hindrance is accepted
  PASS  a second claim raised from the same hindrance is refused  -  E11000 claim_origin_unique
  PASS  a claim raised from a letter with the same id is a different origin
  PASS  first skill step is accepted
  PASS  the same skill step replayed is refused  -  E11000 skill_step_idempotency_unique
  PASS  first link from a head to a hindrance is accepted
  PASS  a second active link from the same head to the same hindrance is refused  -  E11000 claim_head_link_active_unique
  PASS  removed links to it are kept as history
  PASS  the same hindrance can be linked to another head
  PASS  first determination revision is accepted
  PASS  the same revision of an authored determination is refused  -  E11000 claim_determination_revision_unique
  PASS  a received determination may carry the same revision number
  PASS  a strategy note is accepted
  PASS  a second strategy note for the claim is refused  -  E11000 claim_privileged_one_strategy_note
  PASS  simulations of the same claim are many
  PASS  first submission for a claim is accepted
  PASS  a second submission for the claim is refused  -  E11000 claim_submission_claim_unique
  PASS  first escalated row is accepted
  PASS  the same origin in the same case is refused  -  E11000 matrix_case_origin_unique
  PASS  rows added by hand are unconstrained
  PASS  the same origin in another case is accepted
  PASS  probe rows removed  -  0 left

Step logs         /tmp/claude-0/gate6/run
```

## The 27-migration catalogue: second run of 2026-11-16

**Disposition: migration path measured for 27 of 27; acceptance as gate
evidence is the release owner's to confirm**, on the same footing as the runs
below. One migration was added: `20261119_0001
claim_escalation_and_scratch_drafts`. It indexes the link between claims and
arbitration cases, and marks pleading drafts that have no case workspace as
scratch drafts. On an empty database it marks none. It deletes nothing and
changes no draft's status.

**This is the first migration in this series that writes to an arbitration
collection** (`arbitration_drafts`, one flag). A fresh install has no drafts, so
this run exercises the index half and the no-op of the update. What the update
does to existing drafts is covered by a unit test against sample rows, not by
this run; the dry run on a real database will report the number it would mark.

**Backend image.** Built by `docker build` from `backend/Dockerfile` at the
commit in the block, with the CA bundle mounted as a build secret for the
`pip install` instruction only, host network. No layering.

**What the script now also checks.** The two new indexes, including the partial
filter on `claims.arbitration_case_id`.

### Evidence block

```text
GATE 6 BULLET 2 - FRESH INSTALL RUN  gate6-20261116t073010z-22082
Result            PASS - 27/27 migrations applied to an empty MongoDB
Date (UTC)        2026-11-16T07:30:52Z
Host              Linux 6.18.44-fc-v470 x86_64; docker 29.9.2
Commit            c29608ada0b39abfe39507dede96c40535c9507f (claude/zen-ptolemy-695liz); uncommitted backend changes: 0
Backend image     contraclaim-gate6-backend:c29608ada0b3-sandboxca sha256:0456d9ad12776349581c9f68519433b1749cd5f49a273f8b6bccc78b06c251f0
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
  configured_host=gate6-20261116t073010z-22082-mongo
  database=gate6_fresh_install
  server_me=gate6-20261116t073010z-22082-mongo:27017
  server_set=gate6rs

Commands (each in a new backend container: docker run --rm --network <private> -e DATABASE_URL=mongodb://<disposable>:27017/gate6_fresh_install?replicaSet=gate6rs -e MONGODB_DATABASE=gate6_fresh_install -e MONGODB_REPLICA_SET=gate6rs -e ENVIRONMENT=development contraclaim-gate6-backend:c29608ada0b3-sandboxca)
  1. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=27 applied=0 pending=27 applied_not_in_catalogue=0
  2. python -m rbac_backend.scripts.migrate_database --fail-on-warning
       exit 0   total=27 applied=0 dry_run=27 skipped=0 warnings=0 notices=9; database still empty afterwards
  3. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
       exit 0   total=27 applied=27 dry_run=0 skipped=0 warnings=0 notices=9
  4. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning   (second apply)
       exit 0   total=27 applied=0 dry_run=0 skipped=27 warnings=0 notices=0
       collections=159 indexes=543 documents=38, identical before and after
  5. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=27 applied=27 pending=0 applied_not_in_catalogue=0

Skill and claim migrations 20261114_0001 to 20261119_0001, read with mongosh: 60 passed, 0 failed
  PASS  collection skills exists
  PASS  collection skill_versions exists
  PASS  collection skill_bindings exists
  PASS  collection skill_approvals exists
  PASS  collection letter_position_boards exists
  PASS  collection letter_skill_steps exists
  PASS  collection skill_steps exists
  PASS  collection claim_notice_requirements exists
  PASS  collection claim_head_links exists
  PASS  collection claim_determinations exists
  PASS  collection claim_privileged exists
  PASS  collection claim_submissions exists
  PASS  index skills.skill_owner_key_unique  -  key={"owner_scope":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_unique  -  key={"skill_key":1,"version":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_status  -  key={"skill_key":1,"status":1} unique=false partialFilterExpression=null
  PASS  index skill_bindings.skill_binding_scope_unique  -  key={"organization_id":1,"project_id":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_approvals.skill_approvals_by_version  -  key={"skill_key":1,"version":1,"decided_at":1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_letter_unique  -  key={"letter_id":1} unique=true partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_scope_time  -  key={"organization_id":1,"project_id":1,"updated_at":-1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_document_unique  -  key={"organization_id":1,"incoming_document_id":1} unique=true partialFilterExpression={"incoming_document_id":{"$type":"string"}}
  PASS  index letter_position_boards.bench_board_trigger_unique  -  key={"organization_id":1,"trigger.type":1,"trigger.id":1} unique=true partialFilterExpression={"trigger.id":{"$type":"string"}}
  PASS  index letter_skill_steps.bench_step_letter_time  -  key={"letter_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_idempotency_unique  -  key={"letter_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_idempotency_unique  -  key={"subject_type":1,"subject_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_parent_time  -  key={"parent_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_notice_requirements.notice_requirement_current_unique  -  key={"organization_id":1,"project_id":1,"claim_type":1} unique=true partialFilterExpression={"status":"current"}
  PASS  index claim_notice_requirements.notice_requirement_history  -  key={"organization_id":1,"project_id":1,"claim_type":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claims.claim_origin_unique  -  key={"organization_id":1,"origin.type":1,"origin.id":1} unique=true partialFilterExpression={"origin.id":{"$type":"string"}}
  PASS  index claim_head_links.claim_head_link_active_unique  -  key={"claim_id":1,"head_id":1,"target_type":1,"target_id":1} unique=true partialFilterExpression={"status":"active"}
  PASS  index claim_head_links.claim_head_link_claim_time  -  key={"claim_id":1,"status":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_head_links.claim_head_link_target  -  key={"organization_id":1,"target_type":1,"target_id":1,"status":1} unique=false partialFilterExpression=null
  PASS  index claim_determinations.claim_determination_revision_unique  -  key={"claim_id":1,"origin":1,"revision":1} unique=true partialFilterExpression=null
  PASS  index claim_determinations.claim_determination_scope_status  -  key={"organization_id":1,"project_id":1,"status":1} unique=false partialFilterExpression=null
  PASS  index claim_privileged.claim_privileged_claim_kind_time  -  key={"claim_id":1,"kind":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claim_privileged.claim_privileged_one_strategy_note  -  key={"claim_id":1} unique=true partialFilterExpression={"kind":"strategy_note"}
  PASS  index claim_submissions.claim_submission_claim_unique  -  key={"claim_id":1} unique=true partialFilterExpression=null
  PASS  index claims.claim_arbitration_case  -  key={"organization_id":1,"arbitration_case_id":1} unique=false partialFilterExpression={"arbitration_case_id":{"$type":"string"}}
  PASS  index arbitration_cases.arbitration_case_claim_ids  -  key={"organization_id":1,"claim_ids":1} unique=false partialFilterExpression=null
  PASS  partial indexes expected  -  7 declared
  PASS  skill_versions holds exactly the three published skills and the four draft packs  -  7 rows
  PASS  skill indian-infra-arbitration-hearing stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:arbitration_case_preparation_expert"]
  PASS  skill indian-infra-arbitration-pleadings stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-contract-letters seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-eot-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert","technical:delay_planning_expert"]
  PASS  skill indian-infra-letter-analysis seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-variations seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skills holds exactly the three published skills (a draft gets no latest-published pointer)  -  3 rows
  PASS  skill indian-infra-contract-letters active, latest published 1.0.0
  PASS  skill indian-infra-letter-analysis active, latest published 1.0.0
  PASS  skill indian-infra-variations active, latest published 1.0.0
  PASS  no skill approval exists on a fresh install  -  0 rows
  PASS  ledger records 20261114_0001
  PASS  ledger records 20261114_0002
  PASS  ledger records 20261114_0003
  PASS  ledger records 20261115_0001
  PASS  ledger records 20261116_0001
  PASS  ledger records 20261117_0001
Seeded skills (key, version, content hash) - equal to the files bundled in the image: YES
  indian-infra-arbitration-hearing 1.0.0 c4f100f0ed20d848554aed28c414c0fd84aecb4de76e9f76880e1e8199aafeed
  indian-infra-arbitration-pleadings 1.0.0 8af9e4d95a0a24da65d9e27b67e468518f068478fbf55c1cff52eebcee47a36c
  indian-infra-claims 1.0.0 5f883bf0c2b1f5f6b7c38da6db506d86797e798fb8fcd2af44b54c52a149e1c6
  indian-infra-contract-letters 1.0.0 818a6ad8f429feedbac0732bce835af5948992af029e2ddc6a507f9847151754
  indian-infra-eot-claims 1.0.0 e37c1e84b7ef8250c81f4737247e313968f1edb3448b5d5b2057deb0b610c6b0
  indian-infra-letter-analysis 1.0.0 4fb6f430b50aa8e166ebe044f500597a794aa36f26e9f6020947ae841fe9be53
  indian-infra-variations 1.0.0 49bb0d50e9a05301004d567904ad00b4fd5a4fefe50cf30df20b41823e1f62ad
Unique indexes probed with real inserts (probe rows removed afterwards): 32 passed, 0 failed
  PASS  two boards without a document are both accepted
  PASS  two boards with a null document are both accepted
  PASS  first board for a document is accepted
  PASS  second board for the same document is refused  -  E11000 bench_board_document_unique
  PASS  first board for a trigger record is accepted
  PASS  second board for the same trigger record is refused  -  E11000 bench_board_trigger_unique
  PASS  second board for the same letter is refused  -  E11000 bench_board_letter_unique
  PASS  a second 1.0.0 of a seeded skill is refused  -  E11000 skill_version_unique
  PASS  a second 1.0.0 of a draft pack is refused  -  E11000 skill_version_unique
  PASS  first current notice period is accepted
  PASS  a second current notice period for the same project and claim type is refused  -  E11000 notice_requirement_current_unique
  PASS  superseded notice periods are kept as history
  PASS  another claim type on the project is accepted
  PASS  two claims with no origin are both accepted
  PASS  first claim raised from a hindrance is accepted
  PASS  a second claim raised from the same hindrance is refused  -  E11000 claim_origin_unique
  PASS  a claim raised from a letter with the same id is a different origin
  PASS  first skill step is accepted
  PASS  the same skill step replayed is refused  -  E11000 skill_step_idempotency_unique
  PASS  first link from a head to a hindrance is accepted
  PASS  a second active link from the same head to the same hindrance is refused  -  E11000 claim_head_link_active_unique
  PASS  removed links to it are kept as history
  PASS  the same hindrance can be linked to another head
  PASS  first determination revision is accepted
  PASS  the same revision of an authored determination is refused  -  E11000 claim_determination_revision_unique
  PASS  a received determination may carry the same revision number
  PASS  a strategy note is accepted
  PASS  a second strategy note for the claim is refused  -  E11000 claim_privileged_one_strategy_note
  PASS  simulations of the same claim are many
  PASS  first submission for a claim is accepted
  PASS  a second submission for the claim is refused  -  E11000 claim_submission_claim_unique
  PASS  probe rows removed  -  0 left

Step logs         not retained: the container that ran this was ephemeral; this block is the record
```

## The 26-migration catalogue: run of 2026-11-16

**Disposition: migration path measured for 26 of 26; acceptance as gate
evidence is the release owner's to confirm**, on the same footing as the runs
below. One migration was added: `20261118_0001 claim_findings_indexes`, which
indexes claim determinations, the privileged claim store (simulations and
strategy notes) and claim submissions. It changes no claim, board or role: the
two permissions introduced with it are not granted to any existing role
document.

**Backend image.** Built by `docker build` from `backend/Dockerfile` at the
commit in the block, with the CA bundle mounted as a build secret for the
`pip install` instruction only, host network. No layering.

**What the script now also checks.** The three new collections and their five
indexes, and by real inserts: a determination revision is unique per claim and
origin while a received one may carry the same number; a claim holds one
strategy note and any number of simulations; a claim holds one submission.

### Evidence block

```text
GATE 6 BULLET 2 - FRESH INSTALL RUN  gate6-20261116t062241z-13608
Result            PASS - 26/26 migrations applied to an empty MongoDB
Date (UTC)        2026-11-16T06:23:02Z
Host              Linux 6.18.44-fc-v470 x86_64; docker 29.9.2
Commit            cde12fa36b33f5f30727ea142a3e3dc35478e0e7 (claude/zen-ptolemy-695liz); uncommitted backend changes: 0
Backend image     contraclaim-gate6-backend:cde12fa36b33-sandboxca sha256:5e0a919df6d2b7ab816c2f40904b84b8c7e3f76fe0b7790481e5ce28e0e3e2ba
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
  configured_host=gate6-20261116t062241z-13608-mongo
  database=gate6_fresh_install
  server_me=gate6-20261116t062241z-13608-mongo:27017
  server_set=gate6rs

Commands (each in a new backend container: docker run --rm --network <private> -e DATABASE_URL=mongodb://<disposable>:27017/gate6_fresh_install?replicaSet=gate6rs -e MONGODB_DATABASE=gate6_fresh_install -e MONGODB_REPLICA_SET=gate6rs -e ENVIRONMENT=development contraclaim-gate6-backend:cde12fa36b33-sandboxca)
  1. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=26 applied=0 pending=26 applied_not_in_catalogue=0
  2. python -m rbac_backend.scripts.migrate_database --fail-on-warning
       exit 0   total=26 applied=0 dry_run=26 skipped=0 warnings=0 notices=7; database still empty afterwards
  3. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
       exit 0   total=26 applied=26 dry_run=0 skipped=0 warnings=0 notices=7
  4. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning   (second apply)
       exit 0   total=26 applied=0 dry_run=0 skipped=26 warnings=0 notices=0
       collections=159 indexes=541 documents=37, identical before and after
  5. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=26 applied=26 pending=0 applied_not_in_catalogue=0

Skill and claim migrations 20261114_0001 to 20261117_0001, read with mongosh: 58 passed, 0 failed
  PASS  collection skills exists
  PASS  collection skill_versions exists
  PASS  collection skill_bindings exists
  PASS  collection skill_approvals exists
  PASS  collection letter_position_boards exists
  PASS  collection letter_skill_steps exists
  PASS  collection skill_steps exists
  PASS  collection claim_notice_requirements exists
  PASS  collection claim_head_links exists
  PASS  collection claim_determinations exists
  PASS  collection claim_privileged exists
  PASS  collection claim_submissions exists
  PASS  index skills.skill_owner_key_unique  -  key={"owner_scope":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_unique  -  key={"skill_key":1,"version":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_status  -  key={"skill_key":1,"status":1} unique=false partialFilterExpression=null
  PASS  index skill_bindings.skill_binding_scope_unique  -  key={"organization_id":1,"project_id":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_approvals.skill_approvals_by_version  -  key={"skill_key":1,"version":1,"decided_at":1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_letter_unique  -  key={"letter_id":1} unique=true partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_scope_time  -  key={"organization_id":1,"project_id":1,"updated_at":-1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_document_unique  -  key={"organization_id":1,"incoming_document_id":1} unique=true partialFilterExpression={"incoming_document_id":{"$type":"string"}}
  PASS  index letter_position_boards.bench_board_trigger_unique  -  key={"organization_id":1,"trigger.type":1,"trigger.id":1} unique=true partialFilterExpression={"trigger.id":{"$type":"string"}}
  PASS  index letter_skill_steps.bench_step_letter_time  -  key={"letter_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_idempotency_unique  -  key={"letter_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_idempotency_unique  -  key={"subject_type":1,"subject_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_parent_time  -  key={"parent_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_notice_requirements.notice_requirement_current_unique  -  key={"organization_id":1,"project_id":1,"claim_type":1} unique=true partialFilterExpression={"status":"current"}
  PASS  index claim_notice_requirements.notice_requirement_history  -  key={"organization_id":1,"project_id":1,"claim_type":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claims.claim_origin_unique  -  key={"organization_id":1,"origin.type":1,"origin.id":1} unique=true partialFilterExpression={"origin.id":{"$type":"string"}}
  PASS  index claim_head_links.claim_head_link_active_unique  -  key={"claim_id":1,"head_id":1,"target_type":1,"target_id":1} unique=true partialFilterExpression={"status":"active"}
  PASS  index claim_head_links.claim_head_link_claim_time  -  key={"claim_id":1,"status":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_head_links.claim_head_link_target  -  key={"organization_id":1,"target_type":1,"target_id":1,"status":1} unique=false partialFilterExpression=null
  PASS  index claim_determinations.claim_determination_revision_unique  -  key={"claim_id":1,"origin":1,"revision":1} unique=true partialFilterExpression=null
  PASS  index claim_determinations.claim_determination_scope_status  -  key={"organization_id":1,"project_id":1,"status":1} unique=false partialFilterExpression=null
  PASS  index claim_privileged.claim_privileged_claim_kind_time  -  key={"claim_id":1,"kind":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claim_privileged.claim_privileged_one_strategy_note  -  key={"claim_id":1} unique=true partialFilterExpression={"kind":"strategy_note"}
  PASS  index claim_submissions.claim_submission_claim_unique  -  key={"claim_id":1} unique=true partialFilterExpression=null
  PASS  partial unique indexes expected  -  6 declared
  PASS  skill_versions holds exactly the three published skills and the four draft packs  -  7 rows
  PASS  skill indian-infra-arbitration-hearing stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:arbitration_case_preparation_expert"]
  PASS  skill indian-infra-arbitration-pleadings stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-contract-letters seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-eot-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert","technical:delay_planning_expert"]
  PASS  skill indian-infra-letter-analysis seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-variations seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skills holds exactly the three published skills (a draft gets no latest-published pointer)  -  3 rows
  PASS  skill indian-infra-contract-letters active, latest published 1.0.0
  PASS  skill indian-infra-letter-analysis active, latest published 1.0.0
  PASS  skill indian-infra-variations active, latest published 1.0.0
  PASS  no skill approval exists on a fresh install  -  0 rows
  PASS  ledger records 20261114_0001
  PASS  ledger records 20261114_0002
  PASS  ledger records 20261114_0003
  PASS  ledger records 20261115_0001
  PASS  ledger records 20261116_0001
  PASS  ledger records 20261117_0001
Seeded skills (key, version, content hash) - equal to the files bundled in the image: YES
  indian-infra-arbitration-hearing 1.0.0 c4f100f0ed20d848554aed28c414c0fd84aecb4de76e9f76880e1e8199aafeed
  indian-infra-arbitration-pleadings 1.0.0 8af9e4d95a0a24da65d9e27b67e468518f068478fbf55c1cff52eebcee47a36c
  indian-infra-claims 1.0.0 5f883bf0c2b1f5f6b7c38da6db506d86797e798fb8fcd2af44b54c52a149e1c6
  indian-infra-contract-letters 1.0.0 818a6ad8f429feedbac0732bce835af5948992af029e2ddc6a507f9847151754
  indian-infra-eot-claims 1.0.0 e37c1e84b7ef8250c81f4737247e313968f1edb3448b5d5b2057deb0b610c6b0
  indian-infra-letter-analysis 1.0.0 4fb6f430b50aa8e166ebe044f500597a794aa36f26e9f6020947ae841fe9be53
  indian-infra-variations 1.0.0 49bb0d50e9a05301004d567904ad00b4fd5a4fefe50cf30df20b41823e1f62ad
Unique indexes probed with real inserts (probe rows removed afterwards): 32 passed, 0 failed
  PASS  two boards without a document are both accepted
  PASS  two boards with a null document are both accepted
  PASS  first board for a document is accepted
  PASS  second board for the same document is refused  -  E11000 bench_board_document_unique
  PASS  first board for a trigger record is accepted
  PASS  second board for the same trigger record is refused  -  E11000 bench_board_trigger_unique
  PASS  second board for the same letter is refused  -  E11000 bench_board_letter_unique
  PASS  a second 1.0.0 of a seeded skill is refused  -  E11000 skill_version_unique
  PASS  a second 1.0.0 of a draft pack is refused  -  E11000 skill_version_unique
  PASS  first current notice period is accepted
  PASS  a second current notice period for the same project and claim type is refused  -  E11000 notice_requirement_current_unique
  PASS  superseded notice periods are kept as history
  PASS  another claim type on the project is accepted
  PASS  two claims with no origin are both accepted
  PASS  first claim raised from a hindrance is accepted
  PASS  a second claim raised from the same hindrance is refused  -  E11000 claim_origin_unique
  PASS  a claim raised from a letter with the same id is a different origin
  PASS  first skill step is accepted
  PASS  the same skill step replayed is refused  -  E11000 skill_step_idempotency_unique
  PASS  first link from a head to a hindrance is accepted
  PASS  a second active link from the same head to the same hindrance is refused  -  E11000 claim_head_link_active_unique
  PASS  removed links to it are kept as history
  PASS  the same hindrance can be linked to another head
  PASS  first determination revision is accepted
  PASS  the same revision of an authored determination is refused  -  E11000 claim_determination_revision_unique
  PASS  a received determination may carry the same revision number
  PASS  a strategy note is accepted
  PASS  a second strategy note for the claim is refused  -  E11000 claim_privileged_one_strategy_note
  PASS  simulations of the same claim are many
  PASS  first submission for a claim is accepted
  PASS  a second submission for the claim is refused  -  E11000 claim_submission_claim_unique
  PASS  probe rows removed  -  0 left

Step logs         not retained: the container that ran this was ephemeral; this block is the record
```

## The 25-migration catalogue: third run of 2026-11-15

**Disposition: migration path measured for 25 of 25; acceptance as gate
evidence is the release owner's to confirm**, on the same footing as the two
runs below. One migration was added: `20261117_0001 claim_head_link_indexes`,
which indexes the links from a head of claim to the hindrances and key-date EOT
records it relies on. It changes no hindrance, key-date record or claim.

**Backend image.** Built by `docker build` from `backend/Dockerfile` at the
commit in the block, as in the 23-migration run: the CA bundle mounted as a
build secret for the `pip install` instruction only, host network. The registry
lookup that failed for the 24-migration run succeeded this time, so no layering
was needed.

**What the script now also checks.** The `claim_head_links` collection and its
three indexes, and by real inserts: a second active link from the same head to
the same hindrance is refused, removed links to it are kept, and the same
hindrance can be linked to another head.

### Evidence block

```text
GATE 6 BULLET 2 - FRESH INSTALL RUN  gate6-20261115t193902z-2641
Result            PASS - 25/25 migrations applied to an empty MongoDB
Date (UTC)        2026-11-15T19:39:42Z
Host              Linux 6.18.44-fc-v470 x86_64; docker 29.9.2
Commit            672b93bc5084f628bac51499024074fca346d52f (claude/zen-ptolemy-695liz); uncommitted backend changes: 0
Backend image     contraclaim-gate6-backend:672b93bc5084-sandboxca sha256:0e34dfcdfc38d3bb1b5a3ef89b6ef4115664201771dfa058ff61b6e96e88657c
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
  configured_host=gate6-20261115t193902z-2641-mongo
  database=gate6_fresh_install
  server_me=gate6-20261115t193902z-2641-mongo:27017
  server_set=gate6rs

Commands (each in a new backend container: docker run --rm --network <private> -e DATABASE_URL=mongodb://<disposable>:27017/gate6_fresh_install?replicaSet=gate6rs -e MONGODB_DATABASE=gate6_fresh_install -e MONGODB_REPLICA_SET=gate6rs -e ENVIRONMENT=development contraclaim-gate6-backend:672b93bc5084-sandboxca)
  1. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=25 applied=0 pending=25 applied_not_in_catalogue=0
  2. python -m rbac_backend.scripts.migrate_database --fail-on-warning
       exit 0   total=25 applied=0 dry_run=25 skipped=0 warnings=0 notices=6; database still empty afterwards
  3. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
       exit 0   total=25 applied=25 dry_run=0 skipped=0 warnings=0 notices=6
  4. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning   (second apply)
       exit 0   total=25 applied=0 dry_run=0 skipped=25 warnings=0 notices=0
       collections=156 indexes=533 documents=36, identical before and after
  5. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=25 applied=25 pending=0 applied_not_in_catalogue=0

Skill and claim migrations 20261114_0001 to 20261117_0001, read with mongosh: 50 passed, 0 failed
  PASS  collection skills exists
  PASS  collection skill_versions exists
  PASS  collection skill_bindings exists
  PASS  collection skill_approvals exists
  PASS  collection letter_position_boards exists
  PASS  collection letter_skill_steps exists
  PASS  collection skill_steps exists
  PASS  collection claim_notice_requirements exists
  PASS  collection claim_head_links exists
  PASS  index skills.skill_owner_key_unique  -  key={"owner_scope":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_unique  -  key={"skill_key":1,"version":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_status  -  key={"skill_key":1,"status":1} unique=false partialFilterExpression=null
  PASS  index skill_bindings.skill_binding_scope_unique  -  key={"organization_id":1,"project_id":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_approvals.skill_approvals_by_version  -  key={"skill_key":1,"version":1,"decided_at":1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_letter_unique  -  key={"letter_id":1} unique=true partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_scope_time  -  key={"organization_id":1,"project_id":1,"updated_at":-1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_document_unique  -  key={"organization_id":1,"incoming_document_id":1} unique=true partialFilterExpression={"incoming_document_id":{"$type":"string"}}
  PASS  index letter_position_boards.bench_board_trigger_unique  -  key={"organization_id":1,"trigger.type":1,"trigger.id":1} unique=true partialFilterExpression={"trigger.id":{"$type":"string"}}
  PASS  index letter_skill_steps.bench_step_letter_time  -  key={"letter_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_idempotency_unique  -  key={"letter_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_idempotency_unique  -  key={"subject_type":1,"subject_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_parent_time  -  key={"parent_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_notice_requirements.notice_requirement_current_unique  -  key={"organization_id":1,"project_id":1,"claim_type":1} unique=true partialFilterExpression={"status":"current"}
  PASS  index claim_notice_requirements.notice_requirement_history  -  key={"organization_id":1,"project_id":1,"claim_type":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claims.claim_origin_unique  -  key={"organization_id":1,"origin.type":1,"origin.id":1} unique=true partialFilterExpression={"origin.id":{"$type":"string"}}
  PASS  index claim_head_links.claim_head_link_active_unique  -  key={"claim_id":1,"head_id":1,"target_type":1,"target_id":1} unique=true partialFilterExpression={"status":"active"}
  PASS  index claim_head_links.claim_head_link_claim_time  -  key={"claim_id":1,"status":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_head_links.claim_head_link_target  -  key={"organization_id":1,"target_type":1,"target_id":1,"status":1} unique=false partialFilterExpression=null
  PASS  partial unique indexes expected  -  5 declared
  PASS  skill_versions holds exactly the three published skills and the four draft packs  -  7 rows
  PASS  skill indian-infra-arbitration-hearing stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:arbitration_case_preparation_expert"]
  PASS  skill indian-infra-arbitration-pleadings stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-contract-letters seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-eot-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert","technical:delay_planning_expert"]
  PASS  skill indian-infra-letter-analysis seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-variations seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skills holds exactly the three published skills (a draft gets no latest-published pointer)  -  3 rows
  PASS  skill indian-infra-contract-letters active, latest published 1.0.0
  PASS  skill indian-infra-letter-analysis active, latest published 1.0.0
  PASS  skill indian-infra-variations active, latest published 1.0.0
  PASS  no skill approval exists on a fresh install  -  0 rows
  PASS  ledger records 20261114_0001
  PASS  ledger records 20261114_0002
  PASS  ledger records 20261114_0003
  PASS  ledger records 20261115_0001
  PASS  ledger records 20261116_0001
  PASS  ledger records 20261117_0001
Seeded skills (key, version, content hash) - equal to the files bundled in the image: YES
  indian-infra-arbitration-hearing 1.0.0 c4f100f0ed20d848554aed28c414c0fd84aecb4de76e9f76880e1e8199aafeed
  indian-infra-arbitration-pleadings 1.0.0 8af9e4d95a0a24da65d9e27b67e468518f068478fbf55c1cff52eebcee47a36c
  indian-infra-claims 1.0.0 5f883bf0c2b1f5f6b7c38da6db506d86797e798fb8fcd2af44b54c52a149e1c6
  indian-infra-contract-letters 1.0.0 818a6ad8f429feedbac0732bce835af5948992af029e2ddc6a507f9847151754
  indian-infra-eot-claims 1.0.0 e37c1e84b7ef8250c81f4737247e313968f1edb3448b5d5b2057deb0b610c6b0
  indian-infra-letter-analysis 1.0.0 4fb6f430b50aa8e166ebe044f500597a794aa36f26e9f6020947ae841fe9be53
  indian-infra-variations 1.0.0 49bb0d50e9a05301004d567904ad00b4fd5a4fefe50cf30df20b41823e1f62ad
Unique indexes probed with real inserts (probe rows removed afterwards): 24 passed, 0 failed
  PASS  two boards without a document are both accepted
  PASS  two boards with a null document are both accepted
  PASS  first board for a document is accepted
  PASS  second board for the same document is refused  -  E11000 bench_board_document_unique
  PASS  first board for a trigger record is accepted
  PASS  second board for the same trigger record is refused  -  E11000 bench_board_trigger_unique
  PASS  second board for the same letter is refused  -  E11000 bench_board_letter_unique
  PASS  a second 1.0.0 of a seeded skill is refused  -  E11000 skill_version_unique
  PASS  a second 1.0.0 of a draft pack is refused  -  E11000 skill_version_unique
  PASS  first current notice period is accepted
  PASS  a second current notice period for the same project and claim type is refused  -  E11000 notice_requirement_current_unique
  PASS  superseded notice periods are kept as history
  PASS  another claim type on the project is accepted
  PASS  two claims with no origin are both accepted
  PASS  first claim raised from a hindrance is accepted
  PASS  a second claim raised from the same hindrance is refused  -  E11000 claim_origin_unique
  PASS  a claim raised from a letter with the same id is a different origin
  PASS  first skill step is accepted
  PASS  the same skill step replayed is refused  -  E11000 skill_step_idempotency_unique
  PASS  first link from a head to a hindrance is accepted
  PASS  a second active link from the same head to the same hindrance is refused  -  E11000 claim_head_link_active_unique
  PASS  removed links to it are kept as history
  PASS  the same hindrance can be linked to another head
  PASS  probe rows removed  -  0 left

Step logs         not retained: the container that ran this was ephemeral; this block is the record
```

## The 24-migration catalogue: second run of 2026-11-15

**Disposition: migration path measured for 24 of 24; acceptance as gate
evidence is the release owner's to confirm**, on the same footing as the
23-migration run below. One migration was added: `20261116_0001
claim_bench_indexes`, which indexes skill steps run for claims, the notice
period in force per project and claim type, and the record a potential claim
was raised from. It changes no claim. The output below is the script's evidence
block, unedited apart from the step-log path.

### How the backend image was produced, which differs from the earlier runs

`docker build` of `backend/Dockerfile` could not complete: Docker Hub answered
`429 Too Many Requests` to the lookup of `python:3.12-slim`, repeatedly, and
that base image was held only in the build cache, not as a tagged local image.
The image used here was therefore built in two layers on top of the previous
gate image:

- **Base:** `contraclaim-gate6-backend:38a4fbd997b8-sandboxca`, the image of
  the 23-migration run, itself built from `backend/Dockerfile`. Neither
  `backend/Dockerfile` nor `rbac_backend/requirements.txt` has changed between
  `38a4fbd` and the commit in the block (`git diff` over both is empty), and no
  backend file was deleted or renamed in between.
- **On top:** `COPY . .` of the current `backend/` (same `.dockerignore`),
  ownership, and the release label and variable.
- **Checked:** the SHA-256 of every tracked `.py`, `.md` and `.txt` file under
  `rbac_backend/` outside `tests/` (586 files) was read from inside the image
  and compared with the checkout. They are identical.

This is equivalent in content to the image `docker build` would have produced,
and it is not the same act. If that distinction matters for the tick, run the
script again where the registry is reachable; nothing else needs to change.

### What the script now also checks

The `skill_steps` and `claim_notice_requirements` collections and six new
indexes, with their uniqueness and partial filters, and by real inserts: a
second notice period in force for the same project and claim type is refused
while superseded ones and other claim types are accepted; a second claim raised
from the same hindrance is refused while claims with no origin, and a claim
from a letter with the same id, are accepted; a skill step replayed with the
same inputs is refused.

### Evidence block

```text
GATE 6 BULLET 2 - FRESH INSTALL RUN  gate6-20261115t185814z-12306
Result            PASS - 24/24 migrations applied to an empty MongoDB
Date (UTC)        2026-11-15T18:58:36Z
Host              Linux 6.18.44-fc-v470 x86_64; docker 29.9.2
Commit            a30570c57d8db6736bf97d09562ff25b25f26b1e (claude/zen-ptolemy-695liz); uncommitted backend changes: 0
Backend image     contraclaim-gate6-backend:a30570c57d8d-sandboxca sha256:9095423468484af015762d4e1dae3864c7d81797a8b51b7bdcaee9c9d9d9bde4
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
  configured_host=gate6-20261115t185814z-12306-mongo
  database=gate6_fresh_install
  server_me=gate6-20261115t185814z-12306-mongo:27017
  server_set=gate6rs

Commands (each in a new backend container: docker run --rm --network <private> -e DATABASE_URL=mongodb://<disposable>:27017/gate6_fresh_install?replicaSet=gate6rs -e MONGODB_DATABASE=gate6_fresh_install -e MONGODB_REPLICA_SET=gate6rs -e ENVIRONMENT=development contraclaim-gate6-backend:a30570c57d8d-sandboxca)
  1. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=24 applied=0 pending=24 applied_not_in_catalogue=0
  2. python -m rbac_backend.scripts.migrate_database --fail-on-warning
       exit 0   total=24 applied=0 dry_run=24 skipped=0 warnings=0 notices=5; database still empty afterwards
  3. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
       exit 0   total=24 applied=24 dry_run=0 skipped=0 warnings=0 notices=5
  4. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning   (second apply)
       exit 0   total=24 applied=0 dry_run=0 skipped=24 warnings=0 notices=0
       collections=155 indexes=529 documents=35, identical before and after
  5. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=24 applied=24 pending=0 applied_not_in_catalogue=0

Skill and claim migrations 20261114_0001 to _0003, 20261115_0001 and 20261116_0001, read with mongosh: 45 passed, 0 failed
  PASS  collection skills exists
  PASS  collection skill_versions exists
  PASS  collection skill_bindings exists
  PASS  collection skill_approvals exists
  PASS  collection letter_position_boards exists
  PASS  collection letter_skill_steps exists
  PASS  collection skill_steps exists
  PASS  collection claim_notice_requirements exists
  PASS  index skills.skill_owner_key_unique  -  key={"owner_scope":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_unique  -  key={"skill_key":1,"version":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_status  -  key={"skill_key":1,"status":1} unique=false partialFilterExpression=null
  PASS  index skill_bindings.skill_binding_scope_unique  -  key={"organization_id":1,"project_id":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_approvals.skill_approvals_by_version  -  key={"skill_key":1,"version":1,"decided_at":1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_letter_unique  -  key={"letter_id":1} unique=true partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_scope_time  -  key={"organization_id":1,"project_id":1,"updated_at":-1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_document_unique  -  key={"organization_id":1,"incoming_document_id":1} unique=true partialFilterExpression={"incoming_document_id":{"$type":"string"}}
  PASS  index letter_position_boards.bench_board_trigger_unique  -  key={"organization_id":1,"trigger.type":1,"trigger.id":1} unique=true partialFilterExpression={"trigger.id":{"$type":"string"}}
  PASS  index letter_skill_steps.bench_step_letter_time  -  key={"letter_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_idempotency_unique  -  key={"letter_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_idempotency_unique  -  key={"subject_type":1,"subject_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  index skill_steps.skill_step_parent_time  -  key={"parent_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index claim_notice_requirements.notice_requirement_current_unique  -  key={"organization_id":1,"project_id":1,"claim_type":1} unique=true partialFilterExpression={"status":"current"}
  PASS  index claim_notice_requirements.notice_requirement_history  -  key={"organization_id":1,"project_id":1,"claim_type":1,"created_at":-1} unique=false partialFilterExpression=null
  PASS  index claims.claim_origin_unique  -  key={"organization_id":1,"origin.type":1,"origin.id":1} unique=true partialFilterExpression={"origin.id":{"$type":"string"}}
  PASS  partial unique indexes expected  -  4 declared
  PASS  skill_versions holds exactly the three published skills and the four draft packs  -  7 rows
  PASS  skill indian-infra-arbitration-hearing stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:arbitration_case_preparation_expert"]
  PASS  skill indian-infra-arbitration-pleadings stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-contract-letters seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-eot-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert","technical:delay_planning_expert"]
  PASS  skill indian-infra-letter-analysis seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-variations seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skills holds exactly the three published skills (a draft gets no latest-published pointer)  -  3 rows
  PASS  skill indian-infra-contract-letters active, latest published 1.0.0
  PASS  skill indian-infra-letter-analysis active, latest published 1.0.0
  PASS  skill indian-infra-variations active, latest published 1.0.0
  PASS  no skill approval exists on a fresh install  -  0 rows
  PASS  ledger records 20261114_0001
  PASS  ledger records 20261114_0002
  PASS  ledger records 20261114_0003
  PASS  ledger records 20261115_0001
  PASS  ledger records 20261116_0001
Seeded skills (key, version, content hash) - equal to the files bundled in the image: YES
  indian-infra-arbitration-hearing 1.0.0 c4f100f0ed20d848554aed28c414c0fd84aecb4de76e9f76880e1e8199aafeed
  indian-infra-arbitration-pleadings 1.0.0 8af9e4d95a0a24da65d9e27b67e468518f068478fbf55c1cff52eebcee47a36c
  indian-infra-claims 1.0.0 5f883bf0c2b1f5f6b7c38da6db506d86797e798fb8fcd2af44b54c52a149e1c6
  indian-infra-contract-letters 1.0.0 818a6ad8f429feedbac0732bce835af5948992af029e2ddc6a507f9847151754
  indian-infra-eot-claims 1.0.0 e37c1e84b7ef8250c81f4737247e313968f1edb3448b5d5b2057deb0b610c6b0
  indian-infra-letter-analysis 1.0.0 4fb6f430b50aa8e166ebe044f500597a794aa36f26e9f6020947ae841fe9be53
  indian-infra-variations 1.0.0 49bb0d50e9a05301004d567904ad00b4fd5a4fefe50cf30df20b41823e1f62ad
Unique indexes probed with real inserts (probe rows removed afterwards): 20 passed, 0 failed
  PASS  two boards without a document are both accepted
  PASS  two boards with a null document are both accepted
  PASS  first board for a document is accepted
  PASS  second board for the same document is refused  -  E11000 bench_board_document_unique
  PASS  first board for a trigger record is accepted
  PASS  second board for the same trigger record is refused  -  E11000 bench_board_trigger_unique
  PASS  second board for the same letter is refused  -  E11000 bench_board_letter_unique
  PASS  a second 1.0.0 of a seeded skill is refused  -  E11000 skill_version_unique
  PASS  a second 1.0.0 of a draft pack is refused  -  E11000 skill_version_unique
  PASS  first current notice period is accepted
  PASS  a second current notice period for the same project and claim type is refused  -  E11000 notice_requirement_current_unique
  PASS  superseded notice periods are kept as history
  PASS  another claim type on the project is accepted
  PASS  two claims with no origin are both accepted
  PASS  first claim raised from a hindrance is accepted
  PASS  a second claim raised from the same hindrance is refused  -  E11000 claim_origin_unique
  PASS  a claim raised from a letter with the same id is a different origin
  PASS  first skill step is accepted
  PASS  the same skill step replayed is refused  -  E11000 skill_step_idempotency_unique
  PASS  probe rows removed  -  0 left

Step logs         not retained: the container that ran this was ephemeral; this block is the record
```

## The 23-migration catalogue: run of 2026-11-15

**Disposition: migration path measured for 23 of 23; acceptance as gate
evidence is the release owner's to confirm.** One migration was added after the
2026-11-14 run: `20261115_0001 seed_claim_arbitration_skill_drafts`, which
stores the claims, EOT, arbitration-pleadings and arbitration-hearing skill
packs as unpublished drafts and indexes `skill_approvals`. It was applied, with
the other 22, to an empty MongoDB by `scripts/gate6_fresh_install.sh`. The
output below is that script's evidence block, unedited apart from the step-log
path.

### What the run found before it passed

The first run of this script on the new catalogue **failed**, correctly. On an
empty database `20261114_0002 seed_system_skills` iterated the whole bundle,
which now includes the four draft packs, and gave each a `published_at` and a
`skills.latest_published_version` pointer before `20261115_0001` ran. The unit
tests had passed, because each exercised one migration against an in-memory
fake. The fix makes `20261114_0002` seed only packs bundled as published, and a
test now runs both seeds in order. The block below is the run after that fix.
Databases where `20261114_0002` had already been applied are unaffected: it had
only the three letter packs to seed at the time.

### Where it ran, and what that means

- **Same setting as the 2026-11-14 run**: an ephemeral cloud development
  container, executed by the coding agent, this time on the pull request that
  adds the migration. The server is a real `mongod` in a one-member replica
  set, proved empty first; the commands are the documented install path, run
  inside the backend image; the result is read back with `mongosh`.
- **The owner's decision of 2026-11-14** accepted a disposable, empty
  `mongo:8.0` as evidence for this bullet, for that run. It has not been
  restated for this one. This section and the number pinned in
  `test_gate6_fresh_install_evidence.py` were updated together so the tree is
  consistent; if the decision does not extend to this run, revert both and run
  the script on staging.
- **Backend image.** Built from `backend/Dockerfile` at the commit named in the
  block, with the same build-time difference as before: outbound TLS in the
  container is intercepted by a proxy, so its CA bundle was mounted for the
  `pip install` instruction only (a build secret; no file is left in the image)
  and the build used the host network. No source file differs from the commit.

### What changed in the script

`scripts/gate6_fresh_install.sh` now expects 23 migrations and checks, by
reading the server: the `skill_approvals` collection and its index; the four
draft packs at `1.0.0` with status `draft`, no publication date, no evaluation
report and a manifest naming a technical and a legal approver; exactly three
rows in `skills` (a draft gets no latest-published pointer); no approval rows;
and that a second `1.0.0` of a draft pack is refused by the unique index.

### What this run did not re-measure

As before, the script exercises the migration path only and does not start the
application.

### Evidence block

```text
GATE 6 BULLET 2 - FRESH INSTALL RUN  gate6-20261115t181625z-17480
Result            PASS - 23/23 migrations applied to an empty MongoDB
Date (UTC)        2026-11-15T18:16:46Z
Host              Linux 6.18.44-fc-v470 x86_64; docker 29.9.2
Commit            38a4fbd997b83d60351ab5530fa3cb6b5904b6eb (claude/zen-ptolemy-695liz); uncommitted backend changes: 0
Backend image     contraclaim-gate6-backend:38a4fbd997b8-sandboxca sha256:78d678e45e17cb946009bdedfe6059790cad32a78acd0a86c0502921fa3e516d
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
  configured_host=gate6-20261115t181625z-17480-mongo
  database=gate6_fresh_install
  server_me=gate6-20261115t181625z-17480-mongo:27017
  server_set=gate6rs

Commands (each in a new backend container: docker run --rm --network <private> -e DATABASE_URL=mongodb://<disposable>:27017/gate6_fresh_install?replicaSet=gate6rs -e MONGODB_DATABASE=gate6_fresh_install -e MONGODB_REPLICA_SET=gate6rs -e ENVIRONMENT=development contraclaim-gate6-backend:38a4fbd997b8-sandboxca)
  1. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=23 applied=0 pending=23 applied_not_in_catalogue=0
  2. python -m rbac_backend.scripts.migrate_database --fail-on-warning
       exit 0   total=23 applied=0 dry_run=23 skipped=0 warnings=0 notices=4; database still empty afterwards
  3. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
       exit 0   total=23 applied=23 dry_run=0 skipped=0 warnings=0 notices=4
  4. python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning   (second apply)
       exit 0   total=23 applied=0 dry_run=0 skipped=23 warnings=0 notices=0
       collections=153 indexes=521 documents=34, identical before and after
  5. python -m rbac_backend.scripts.migrate_database --list
       exit 0   total=23 applied=23 pending=0 applied_not_in_catalogue=0

Skill migrations 20261114_0001 to _0003 and 20261115_0001, read with mongosh: 36 passed, 0 failed
  PASS  collection skills exists
  PASS  collection skill_versions exists
  PASS  collection skill_bindings exists
  PASS  collection skill_approvals exists
  PASS  collection letter_position_boards exists
  PASS  collection letter_skill_steps exists
  PASS  index skills.skill_owner_key_unique  -  key={"owner_scope":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_unique  -  key={"skill_key":1,"version":1} unique=true partialFilterExpression=null
  PASS  index skill_versions.skill_version_status  -  key={"skill_key":1,"status":1} unique=false partialFilterExpression=null
  PASS  index skill_bindings.skill_binding_scope_unique  -  key={"organization_id":1,"project_id":1,"skill_key":1} unique=true partialFilterExpression=null
  PASS  index skill_approvals.skill_approvals_by_version  -  key={"skill_key":1,"version":1,"decided_at":1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_letter_unique  -  key={"letter_id":1} unique=true partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_scope_time  -  key={"organization_id":1,"project_id":1,"updated_at":-1} unique=false partialFilterExpression=null
  PASS  index letter_position_boards.bench_board_document_unique  -  key={"organization_id":1,"incoming_document_id":1} unique=true partialFilterExpression={"incoming_document_id":{"$type":"string"}}
  PASS  index letter_position_boards.bench_board_trigger_unique  -  key={"organization_id":1,"trigger.type":1,"trigger.id":1} unique=true partialFilterExpression={"trigger.id":{"$type":"string"}}
  PASS  index letter_skill_steps.bench_step_letter_time  -  key={"letter_id":1,"created_at":1} unique=false partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_idempotency_unique  -  key={"letter_id":1,"skill_key":1,"skill_hash":1,"input_hash":1} unique=true partialFilterExpression=null
  PASS  index letter_skill_steps.bench_step_id_unique  -  key={"step_id":1} unique=true partialFilterExpression=null
  PASS  partial unique indexes expected  -  2 declared
  PASS  skill_versions holds exactly the three published skills and the four draft packs  -  7 rows
  PASS  skill indian-infra-arbitration-hearing stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:arbitration_case_preparation_expert"]
  PASS  skill indian-infra-arbitration-pleadings stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert"]
  PASS  skill indian-infra-contract-letters seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-eot-claims stored at 1.0.0 as an unpublished draft that names a legal approver  -  status=draft published_at=null required_approvals=["legal:senior_legal_approver","technical:chief_contract_expert","technical:delay_planning_expert"]
  PASS  skill indian-infra-letter-analysis seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skill indian-infra-variations seeded at 1.0.0, published, system-owned  -  version=1.0.0 status=published owner_scope=system
  PASS  skills holds exactly the three published skills (a draft gets no latest-published pointer)  -  3 rows
  PASS  skill indian-infra-contract-letters active, latest published 1.0.0
  PASS  skill indian-infra-letter-analysis active, latest published 1.0.0
  PASS  skill indian-infra-variations active, latest published 1.0.0
  PASS  no skill approval exists on a fresh install  -  0 rows
  PASS  ledger records 20261114_0001
  PASS  ledger records 20261114_0002
  PASS  ledger records 20261114_0003
  PASS  ledger records 20261115_0001
Seeded skills (key, version, content hash) - equal to the files bundled in the image: YES
  indian-infra-arbitration-hearing 1.0.0 c4f100f0ed20d848554aed28c414c0fd84aecb4de76e9f76880e1e8199aafeed
  indian-infra-arbitration-pleadings 1.0.0 8af9e4d95a0a24da65d9e27b67e468518f068478fbf55c1cff52eebcee47a36c
  indian-infra-claims 1.0.0 5f883bf0c2b1f5f6b7c38da6db506d86797e798fb8fcd2af44b54c52a149e1c6
  indian-infra-contract-letters 1.0.0 818a6ad8f429feedbac0732bce835af5948992af029e2ddc6a507f9847151754
  indian-infra-eot-claims 1.0.0 e37c1e84b7ef8250c81f4737247e313968f1edb3448b5d5b2057deb0b610c6b0
  indian-infra-letter-analysis 1.0.0 4fb6f430b50aa8e166ebe044f500597a794aa36f26e9f6020947ae841fe9be53
  indian-infra-variations 1.0.0 49bb0d50e9a05301004d567904ad00b4fd5a4fefe50cf30df20b41823e1f62ad
Unique indexes probed with real inserts (probe rows removed afterwards): 10 passed, 0 failed
  PASS  two boards without a document are both accepted
  PASS  two boards with a null document are both accepted
  PASS  first board for a document is accepted
  PASS  second board for the same document is refused  -  E11000 bench_board_document_unique
  PASS  first board for a trigger record is accepted
  PASS  second board for the same trigger record is refused  -  E11000 bench_board_trigger_unique
  PASS  second board for the same letter is refused  -  E11000 bench_board_letter_unique
  PASS  a second 1.0.0 of a seeded skill is refused  -  E11000 skill_version_unique
  PASS  a second 1.0.0 of a draft pack is refused  -  E11000 skill_version_unique
  PASS  probe rows removed  -  0 left

Step logs         not retained: the container that ran this was ephemeral; this block is the record
```

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
