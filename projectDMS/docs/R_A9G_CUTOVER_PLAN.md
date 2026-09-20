# R-A9G production cutover plan — scope, window, ordering

**Status: PLAN ONLY. NOT AUTHORISED. NOT SCHEDULED. NOT EXECUTED.**
Written 2026-09-20 in release programme R-A9F, which made no production change of any kind.

This file is the *shape* of the window: what is in scope, how long it needs, and in what order
the existing documents are executed. It does not restate their steps. Where it and another
document disagree, **`PRODUCTION_CUTOVER_CHECKLIST.md` is the canonical ordering** and this file
is wrong.

## Preconditions — none of which R-A9F can satisfy

R-A9G may not start until every row is recorded in writing.

| # | Precondition | Where |
|---|---|---|
| 1 | The owner accepts high-risk items A1–A9 | `docs/R_A9F_OPEN_GATE_MATRIX.md`, "Consolidated high-risk acceptance list" |
| 2 | Gate 9 b2 ticked (high-severity risks fixed or explicitly accepted) | release gate |
| 3 | Gate 9 b1 decided — close P0-002/P0-008 or convert the register rows to accepted debt | release gate + Critical Blocker Register |
| 4 | Gate 9 b5 ticked by the owner (legitimately tickable now; R-A9F left it) | release gate |
| 5 | Gate 9 b6 — the release owner signs off | release gate |
| 6 | The 3 production roles / 4 users carried from R-A9B reviewed | R-A9B receipt §16 |
| 7 | The maintenance window granted, with the hard recovery start agreed and recorded | this file |

## Window

| Item | Value |
|---|---|
| **Recommended window** | **8 hours** |
| Minimum execution budget | **120 minutes** |
| Minimum recovery reserve | **90 minutes** |
| `LATEST_SAFE_STOP` | `WINDOW_END − 210 min` — for an 8-hour window, `WINDOW_START + 270 min` |
| Gate | `scripts/check_maintenance_time_budget.py --recovery-reserve-minutes 90 --execution-budget-minutes 120`, run **before** the stop and **again immediately before** the stop command |
| Expected outage | materially longer than R-A9E's 29 m 54 s or R-A9C's 8 m 28 s: those windows only stopped and restarted production, while this one deploys, migrates, aligns roles, moves Falkor persistence and rotates a credential |

**Do not schedule this automatically.** The window is an owner grant.

Why 8 hours rather than the 5–6 the checklist carried: that figure was sized for a deploy plus
migrations. R-A9G additionally carries the explicit role alignment, the FalkorDB Variant-A
persistence cutover with an out-of-band engine, a `FALKORDB_PASSWORD` rotation, the ClamAV
override retirement, backup verification, restore verification and a ≥ 40-minute observation.

## Ordering

Each phase names the document that owns it. Nothing here supersedes those documents.

| # | Phase | Owned by | Production state |
|---|---|---|---|
| 1 | Release freeze and provenance re-derivation | checklist §1; `docs/R_A9F_PRODUCTION_MANIFEST.md` | online |
| 2 | Time budget agreed and gate run | checklist §2 | online |
| 3 | Final CI and security re-check on the deploying HEAD; image freshness gate; evidence secret scan with a non-zero hunted count | checklist §3 | online |
| 4 | Pre-outage revalidation with production still online; **ClamAV render-equivalence proof** | checklist §4, **§13a first bullet** | online |
| 5 | Fresh backups — the pre-deploy backup contract, all artefacts semantically `VALID` | checklist §5 | online |
| 6 | FalkorDB rescue archive, fresh in this window | checklist §6; Falkor doc §4.2–4.3 | online |
| 7 | Rescue restore proof, disposable; `docker commit` snapshot of the original layer | checklist §7; Falkor doc §4.4–4.5 | online |
| 8 | S3 standing decision confirmed or revised, **before** the stop | checklist §8 | online |
| 9 | Time gates (both), then the audited production stop | checklist §9 | **outage begins** |
| 10 | FalkorDB `/FalkorDB` → `/data`, **Variant A** | checklist §10; Falkor doc §4.6–4.13 | stopped |
| 11 | Graph parity — nodes, edges, labels | checklist §11; Falkor doc §4.14–4.15 | stopped |
| 12 | Release image deployment | checklist §13 | stopped |
| 13 | Production migrations — inspect, dry-run, apply, re-apply | checklist §14; manifest §7 | app tier stopped |
| 14 | **Role-contract alignment** — inspect, dry-run, apply, re-apply (NOOP) | checklist §14; `docs/R_A9F_ROLE_ALIGNMENT_CUTOVER_DIFF.md` | app tier stopped |
| 15 | Application startup; permissions and indexes; auth refresh smoke; gate-style health | checklist §16–§19 | **outage ends** |
| 16 | `FALKORDB_PASSWORD` rotation — **only after parity**, never before | checklist §12; Falkor doc §4.19–4.21 | online |
| 17 | Backup verification after the cutover — the post-deploy backup contract | checklist §20; Falkor doc §4.16–4.17 | online |
| 18 | Restore verification — one disposable restore from the **new** production archive | checklist §20; Falkor doc §4.18 | online |
| 19 | `post_deploy_verify.sh`, 0 failures, mandatory edge check | checklist §21 | online |
| 20 | **ClamAV temporary-override retirement**, one step, then re-verify | checklist §13a final bullets | online |
| 21 | Observation ≥ 40 minutes, sampled every 5; evidence frozen, checksummed, secret-scanned | checklist §23 | online |
| 22 | Retain the FalkorDB rollback artefacts until the retention rule is satisfied and the owner records acceptance | Falkor doc §5.4 | online |

Phases 10 and 12–14 are the irreversible core. Rollback criteria are checklist §22; the graph
rollback procedure is Falkor doc §5.

## The Falkor Variant-A sequence, mapped

The owner's decision is **Variant A** (recorded in `PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md`
§3 and in the checklist's standing-decisions table). The required sequence maps onto that
document as follows — it is not a second procedure.

| # | Required step | Document section |
|---|---|---|
| 1 | Fresh rescue archive from `/FalkorDB` | §4.2 |
| 2 | Semantic validation of that archive | §4.3 |
| 3 | Disposable restore proof | §4.4 |
| 4 | **Preserve the original container object** — stopped, never removed | §4.7 |
| 5 | Create and seed the release `/data` volume at the correct root | §4.8–4.9 |
| 6 | Start the NEW release Falkor engine **out of band** (`docker run`, `falkordb` network alias, compose never addresses the service) | §4.11 |
| 7 | Authenticated `PING` | §4.12 |
| 8 | `GRAPH.LIST` | §4.13 |
| 9 | Graph parity — nodes, edges, labels | §4.14 |
| 10 | Application graph round-trip | §4.15 |
| 11 | New semantic backup | §4.16–4.17 |
| 12 | Disposable restore of the NEW backup | §4.18 |
| 13 | Switch the application dependency to the new engine | §4.20 |
| 14 | Verify the application | §4.21 |
| 15 | Rotate `FALKORDB_PASSWORD` — **only after parity** | §4.19 |
| 16 | Observation | §4.22 |
| 17 | Keep the original container until the rollback-retention rule is satisfied | §5.4 |

Parity target, measured read-only on 2026-09-20: graph `contraclaim`, **156 nodes, 220 edges,
7 labels**. R-A9E reproduced exactly those numbers from a rescue archive in a disposable
`/data` engine, which proves the procedure — it does **not** substitute for taking a fresh
archive inside the window.

## Explicitly out of scope for R-A9G

| Item | State |
|---|---|
| **G32 production state migration** | **NOT RUN.** Deploying this release is G32's own Stage 1 — the readers already ignore the shared Letter node's properties — so the release is safe without it. G32 remains a **post-release certification** item and needs **separate owner authorisation** and its own four approvals (`G32-PRODUCTION-MIGRATION-RUNBOOK.md`; checklist §15). Do not add it to this cutover. |
| **Third-party image upgrades** | Not performed. Carried under the dated R-A9E exception to 2026-10-15; remediation window 2026-10-06 → 2026-10-15, httpd first. |
| **Merging PR #20 or PR #21** | Not part of the cutover. Deploy = get the commit onto the branch the server tracks. |
| **`graphiti`** | Stays behind the `graph-experimental` profile; does not start. |
| **Generalising the F-A9D-1 removal** | `contractmgr_org`, `settings_manager`, `limited_user`, `projectuser` and `orguser` are never touched. |

## Two facts that must be re-derived inside the window, not assumed

1. **The branch production tracks.** As of 2026-09-20 production is on `main` @ `b2d5025`. The
   runbook has said `main` while production sat on a `codex/*` branch for long stretches. Read
   the server's own `git branch --show-current` every time.
2. **The compose file set.** The running `clamav` container carries a **three**-file
   `com.docker.compose.project.config_files` label. Read the label rather than assuming, and
   keep the ClamAV override in every production compose command until §13a retires it.
