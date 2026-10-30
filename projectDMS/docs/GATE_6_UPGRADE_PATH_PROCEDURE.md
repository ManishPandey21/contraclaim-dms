# Gate 6 bullet 3 — production-copy upgrade path procedure

**Bullet:** "Existing database upgrade path is tested against a staging copy."

This is the order of operations for restoring a production Mongo archive into the
staging replica set `rsstg` and upgrading it with the release migrations. It
exists because the R-A8W independent review (the first review of this path) found
that the individual tools are safe only when used in a particular way, and that
several green signals along the path prove less than they appear to.

It is run **inside a maintenance window on the same host**, against the
`contraclaim-stg` project only. Nothing here is ever pointed at `rs0` or at the
database `contraclaim`.

## Findings this procedure answers

| Review finding | What goes wrong otherwise | Procedure step |
|---|---|---|
| HIGH-1 — URI shapes that skipped the production refusal (`;` separator, loopback/IP, trailing dot, qualified name, SRV) | a restore reaches a production member | fixed in `scripts/mongo_restore.sh` (R-A8W); **and** step 3 always names `replicaSet=rsstg` |
| HIGH-2 — nothing stops a restore into a non-empty database | fresh-install collections and permission rows survive under the production copy and contaminate parity | steps 1–2: stop writers, drop, prove empty |
| HIGH-3 — startup seeds missing permissions (`main.py` → `ensure_permission_catalog_and_superadmin`, create-if-absent) | the "263 permissions" assertion fails, or passes for the wrong reason, once the backend has started | step 5 asserts **before** start; step 7 asserts `263 + |release ∖ production|` after |
| MEDIUM-4 — restore parity is computed from the restore's own output | a partial restore, or one over leftover data, reports `STATUS=OK` | step 3 sets `RESTORE_EXPECTED_DOCS` from an independent measurement; step 4 reads the database back |
| MEDIUM-5 — compose-mode defaults name the production file pair and `.env` | the restore execs into the wrong project | step 3 sets `COMPOSE_FILES` with `-p contraclaim-stg` and `ENV_FILE=.env.staging` |
| MEDIUM-6 — `/health/ready` checks no migration state | a green ready probe is read as "migrated" | step 5 `--list` and `getIndexes()` are separate evidence |
| LOW-7 — the migration CLI never prints its database | a wrong-target run looks like "19 pending" | step 5 runs inside the staging backend container and checks the expected 16 / 2 / 3 first |

## Inputs, measured before the window

* The archive: a nightly `production_backup.sh` archive, its sha256 matching the
  manifest recorded when it was written, `gzip -t` clean. Read-only.
* `EXPECTED_DOCS` / expected collection count and the per-collection counts for
  **that archive**, read back from a disposable drill database (the standing
  owner authorization for run-owned drills covers this). Not from the restore's
  own report.
* `PROD_PERMISSION_NAMES` from the same drill, and
  `DELTA = |release DEFAULT_PERMISSIONS names ∖ PROD_PERMISSION_NAMES|`.

## Steps

```bash
STG="docker compose -p contraclaim-stg -f docker-compose.prod.yml \
     -f docker-compose.mongo-replicaset.yml -f docker-compose.staging.yml --env-file .env.staging"
```

1. **Stop every writer.** `$STG stop backend contract-worker document-worker`.
   Nothing may be connected to `contraclaim_staging` while it is replaced.
2. **Drop and prove empty.** On `rsstg`, confirm `rs.status().set == "rsstg"`,
   then `db.getSiblingDB('contraclaim_staging').dropDatabase()`, then
   `listCollections` on it must return **0**. Record all three.
3. **Restore with every guard armed.**

   ```bash
   RESTORE_EXEC_CONTEXT=compose MONGO_EXEC_SERVICE=mongo1 \
   COMPOSE_FILES="-p contraclaim-stg -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml -f docker-compose.staging.yml" \
   ENV_FILE=.env.staging \
   MONGO_URI="mongodb://<stg-user>:<pw>@mongo1:27017,mongo2:27017,mongo3:27017/?authSource=admin&replicaSet=rsstg" \
   RESTORE_SOURCE_DB=contraclaim MONGO_DB=contraclaim_staging \
   RESTORE_EXPECTED_DOCS="$EXPECTED_DOCS" \
   bash scripts/mongo_restore.sh <archive>
   ```

   No path database in the URI. No `ALLOW_PRODUCTION_RESTORE`. Required:
   exit 0, `STATUS=OK`, `MATCH=YES`, `FAILED_DOCS=0`, `MISSING_COLLECTIONS=<none>`,
   `EXPECTED_COLLECTION_SOURCE=archive prelude`, and every production denylist
   line echoed.
4. **Read the database back.** Collection count and per-collection document
   counts of `contraclaim_staging` equal the drill's figures for the same
   archive; `listDatabases` on `rsstg` shows **no** database named `contraclaim`.
5. **Migrate, with the application still stopped**, inside a one-off staging
   backend container so the database is the staging one:
   `--list` (expect 16 `applied`, 2 `applied_not_in_catalogue`, 3 `pending`) →
   `--fail-on-warning` dry run (exit 0) → apply (exactly the 3 pending) →
   re-apply (0 applied). Then, **before any start**: `permissions` total =
   distinct = the production count, 0 duplicate name groups, `uq_permissions_name`
   present and `unique`, and a duplicate insert refused with `11000`.
6. **Start the application**: `$STG up -d --no-deps backend contract-worker document-worker`,
   then `/health/ready` 200 on the restored database.
7. **After start**: `permissions` total = production count + `DELTA`, still 0
   duplicate groups; `--list` shows 0 pending.
8. **Gate 9 bullet 4**: `scripts/post_deploy_verify.sh` against the staging
   project, 0 failures.

Any step that does not produce its required result stops the procedure; nothing
later compensates for it.
