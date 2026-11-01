# Gate 6 bullet 3 — existing database upgrade path: evidence mapping

**Bullet:** "Existing database upgrade path is tested against a staging copy."

**Status: SATISFIED** by the R-A8W Stage B staging execution on 2026-09-13
(run `R-A8W-STAGEB-20260913T154822Z`, evidence directory
`/var/backups/contraclaim-stg-evidence/R-A8W-STAGEB-20260913T154822Z`, files
`24-29-gate6b3-production-copy-upgrade.txt`, `26-restore-raw-output.txt`,
`28-migration-raw-*.txt` and `29-migration-raw-list-after-start.txt`).

This document is deliberately separate from
[GATE_6_FRESH_INSTALL_EVIDENCE.md](GATE_6_FRESH_INSTALL_EVIDENCE.md). A fresh
install and a restored production copy are different surfaces: a migration that
only works on an empty database is exactly what this bullet exists to catch, so
neither document may stand in for the other.

The procedure followed is [GATE_6_UPGRADE_PATH_PROCEDURE.md](GATE_6_UPGRADE_PATH_PROCEDURE.md).
Every step ran in order, and none was allowed to compensate for an earlier one.

## Deployment under test

| | |
|---|---|
| Release | `release/contraclaim-rc1` @ `37e79ba9de96e019a2a2d33e8907be6a5d70ca48`, tree `4419a827c964…`, dirty 0 |
| Image | backend `sha256:d9eedb2cbee3…` (backend, contract-worker and document-worker) |
| Target | replica set `rsstg` (read back from `rs.status().set`), database `contraclaim_staging`, compose project `contraclaim-stg` only |
| Production | stopped for the window with `docker compose stop`; never a restore or migration target |

## Requirement-by-requirement

| # | Literal element of the bullet or procedure | Measurement | Result |
|---|---|---|---|
| 1 | "a staging **copy**" of an existing database | Archive `contraclaim-20260913-013001.archive.gz`, the nightly production backup; sha256 `a08d252b…c88be7` equal to its manifest; `gzip -t` clean; mounted read-only | SATISFIED |
| 2 | Restore only into staging, never over live data | Writers stopped (0 running); `rs.status().set == rsstg`; `dropDatabase` on `contraclaim_staging`, then `listCollections` = 0 before restore | SATISFIED |
| 3 | Restore with the reviewed guards armed | `mongo_restore.sh` in compose mode, `RESTORE_SOURCE_DB=contraclaim` → `MONGO_DB=contraclaim_staging`, `replicaSet=rsstg`, no `ALLOW_PRODUCTION_RESTORE`; production denylist echoed; exit 0, `STATUS=OK`, `MATCH=YES`, `FAILED_DOCS=0`, `MISSING_COLLECTIONS=<none>`, expected collections from the archive prelude | SATISFIED |
| 4 | Restore parity from an independent measurement | `RESTORE_EXPECTED_DOCS=47722` from the R-A8W Stage A disposable drill of the same archive; read-back 162 collections / 47,722 documents; no database named `contraclaim` on `rsstg` | SATISFIED |
| 5 | Production-shaped state measured **before** the application starts | permissions 263 total, 263 distinct, 0 duplicate groups, `_id_` index only; ledger 18 | SATISFIED |
| 6 | The upgrade path is **tested**: plan, dry run, apply, idempotence | `--list` 16 applied / 2 `applied_not_in_catalogue` / 3 pending; `--fail-on-warning` dry run exit 0 (3 `dry_run`, 0 warnings); `--apply` exit 0, exactly 3 applied (`20260814_0001`, `20260820_0001`, `20260906_0001`); second `--apply` 0 applied, 19 skipped | SATISFIED |
| 7 | `20260906_0001` is safe on production data | applied with no warnings and no notices; permissions still 263 == 263 distinct; `uq_permissions_name` present and unique; a duplicate permission insert refused with code 11000 | SATISFIED |
| 8 | The upgraded copy runs the application | backend, contract-worker and document-worker started on the restored database; `/health/live` 200, `/health/ready` 200 `ready` | SATISFIED |
| 9 | Startup seeding on top of the upgrade is accounted for | after start permissions 265 == 263 + DELTA, where DELTA = 2 release-only names (`dms.contract.applicability.manage`, `dms.contract.catalogue.browse`), measured from the database and `DEFAULT_PERMISSIONS` before start; 0 duplicate groups; `--list` 0 pending | SATISFIED |

## What this evidence does not claim

* It does not certify a **production** migration. G32 production state remains
  NOT CERTIFIED and no production migration was run or authorised.
* The two `applied_not_in_catalogue` ledger rows (`20260730_0001`,
  `20260806_0001`) exist in production and have no code in this release's
  catalogue. The runner reports them and does not fail on them; their
  disposition belongs to the cutover.
* Staging-scale evidence only: one production archive (47,722 documents), one
  host. It says nothing about restore or migration timing at a larger scale.

The same window's Gate 9 bullet 4 smoke ran after this upgrade on the same
database and is recorded against that bullet, not here.
