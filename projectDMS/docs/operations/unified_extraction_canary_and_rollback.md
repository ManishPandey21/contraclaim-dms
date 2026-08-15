# Unified extraction — canary and rollback runbook

> ## STATUS: IMPLEMENTATION VERIFIED — NOT PRODUCTION-ACCEPTED
>
> These are four different things and this document keeps them apart:
>
> | | State | Meaning |
> |---|---|---|
> | **Implementation** | **VERIFIED** | The canary routing, claim boundary, legacy control path and compose topology exist and are proven by automated test on this branch. |
> | **Production operational acceptance** | **NOT RUN** | No deployment, canary window, or production command has been executed. Nothing here has met production. |
> | **Rollback mechanism** | **IMPLEMENTED + TESTED LOCALLY** | Emptying the allowlist provably reroutes new work while in-flight jobs keep their persisted version and evidence identities. Proven in tests, not in production. |
> | **Rollback drill** | **NOT EXECUTED** | No process has been stopped, scaled, or recreated. See G13. |
>
> Controls existing is not acceptance, and a passing test suite is not a
> production window.
>
> - Phase 7 **implementation** (7.1–7.5): complete.
> - Task 7.6 **Step 6** (controls/runbook): **PASS — implementation-frozen** (§8).
> - Task 7.6 **Steps 1–5** (operational acceptance): **PENDING AUTHORISATION / BLOCKED**.
> - Phase 7 **operational acceptance**: **NOT COMPLETE**.
>
> Full matrix in §8. Section 6 is the evidence template and is deliberately empty.
> Open gaps: G1a, G2–G13 in §7. **G11 (unmeasured production disk capacity)
> blocks Step 1 sign-off.**

---

## 0. Routing history — resolved

An earlier revision of this runbook recorded that `pipeline_version` was written
but never read, so the canary was a label with no effect. **That is now fixed.**
Both boundaries exist:

- **Routing boundary.** `DocumentProcessor.process_document` branches on the
  job's persisted `pipeline_version`. `legacy_v0` runs `_extract_legacy()` — a
  deliberately minimal restoration of the pre-Phase-3 path: one document-level
  `is_pdf_textual` decision, no page store, no quality gate, no fallback.
  Anything not explicitly `unified_v1` takes it, so an unknown value never opts
  a tenant into newer code.
- **Claim boundary.** `_claim_next_processing_job(pipeline_versions=…)` filters
  on the persisted version. A worker restricted to `unified_v1` **physically
  cannot claim** another tenant's legacy job, and a legacy-restricted worker
  also matches jobs with no recorded version.

Both paths converge on one shared `_extract_and_persist()`, so there is a single
implementation of content extraction, metadata parsing and persistence — the
legacy path differs only in how the text was obtained.

`_extract_legacy()` is **temporary**. Delete it, `legacy_v0`, and the claim
restriction once the unified pipeline is globally accepted.

**Still outstanding from Step 6's Files list:**

- `scripts/post_deploy_verify.sh` was **not** modified; it does not check the
  worker flag matrix, the single-scheduler-owner invariant, or canary
  contamination.
- The repository-root `.env.example` was **not** updated. Only
  `backend/rbac_backend/.env.example` carries the new variables.

**Scope:** enabling the unified page-extraction pipeline (Phases 1–7) for one
organisation at a time, and proving rollback before any global enablement.

---

## 1. What the controls actually do

| Control | Default | Effect |
|---|---|---|
| `UNIFIED_EXTRACTION_ENABLED` | `false` | Global switch. When true every organisation is unified. |
| `UNIFIED_EXTRACTION_CANARY_ORG_IDS` | *(empty)* | Comma-separated exact organisation ids opted in while the global flag is off. |
| `START_DOCUMENT_EXTRACTION_WORKERS` | `false` | Runs the durable extraction loop. True only on `document-worker`. |
| `EXTRACTION_FALLBACK_ENABLED` | `false` | The LLM/Vision ladder. The only runtime cost in the pipeline. |
| `EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT` | `5` | Hard per-document ceiling on model calls. |
| `RAR_UPLOAD_ENABLED` | `false` | Blocked until EICAR-in-RAR detection is proven on the deployed clamd. |

| `DOCUMENT_WORKER_PIPELINE_VERSIONS` | *(empty)* | Restricts which versions a worker may claim. Empty = all. |
| `DOCUMENT_WORKER_CANARY_REPLICAS` | `0` | Replicas of `document-worker-canary`, which is pinned to `unified_v1`. |

**The property that makes this safe**, and which is now implemented and tested:
`pipeline_version` is decided **once, at job creation**, persisted on the job,
and honoured by both the claim predicate and the processor. Therefore:

- emptying the allowlist cannot reclassify work already queued;
- a worker restricted to one version cannot claim the other's jobs;
- a job queued before this change has no `pipeline_version` and is legacy.

Everything fails closed onto the legacy path: unknown scope, missing
organisation, unrecognised version, or absent version all stay legacy.

---

## 2. Pre-canary safety (Step 1)

Do not start without all of these recorded:

1. Backup production `.env`, uploads volume, and Mongo per
   `docs/CONTRACLAIM_DOCKER_DEPLOYMENT_UPDATE_GUIDE.md`. Record the verified
   backup age.
2. Read the server's own branch — **do not trust the runbook's default**:
   ```bash
   ssh contraclaim "cd /opt/contraclaim-dms/projectDMS && git branch --show-current && git rev-parse --short HEAD"
   ```
3. Read the compose file set from the running container's own label, not from
   assumption:
   ```bash
   ssh contraclaim "docker inspect \$(docker ps -q --filter name=backend | head -1) --format '{{index .Config.Labels \"com.docker.compose.project.config_files\"}}'"
   ```
4. Capture the baseline:
   ```bash
   docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
     exec -T backend python -m scripts.unified_extraction_canary_status
   docker stats --no-stream
   ```
5. Confirm the RAR result from
   `docs/architecture/phase0_extraction_measurements_2026-08-14.md`. **It is
   currently negative — RAR was never functionally proven — so
   `RAR_UPLOAD_ENABLED` stays `false` throughout.**

6. **Filesystem capacity — hard precondition, not yet measured.**

   The unified pipeline writes per-page evidence rows, may rasterise pages, and
   the window requires a fresh backup and headroom to roll back. All of that
   consumes disk that the legacy path does not. Record every line below **from
   the production host** before starting:

   ```bash
   ssh contraclaim "df -h / /var/lib/docker /opt/contraclaim-dms; docker system df"
   ```

   | Item | What to record | Why it gates the window |
   |---|---|---|
   | Free space on the data filesystem | `df -h` avail + % used | A full disk during extraction corrupts nothing but stalls every tenant |
   | Docker image/layer headroom | `docker system df` images/build cache | Rebuilding backend + worker images needs room for the new layers *alongside* the old ones |
   | Mongo data + journal headroom | dbPath size, oplog size, journal dir | Index creation for `20260814_0001` needs working space; a full journal wedges the replica set |
   | Extraction/OCR/raster temp headroom | free space on the container temp dir and `process_dir` | OCR writes whole intermediate PDFs and page rasters per document |
   | Backup size and headroom | size of the latest verified backup + free space where it lands | Step 1 requires a fresh backup; it must fit |
   | Rollback headroom | space to keep the previous image *and* the current one | Rollback must not require deleting the running image first |

   **No threshold is defined yet, and one must not be invented here.** The plan
   specifies no figure, and this repository holds no measurement of production
   disk usage or backup size. Before the window, derive an explicit safe minimum
   from *current* production usage and the *actual* latest backup size, record
   both numbers and the derived threshold in §6, and have the change authoriser
   accept it. A window may not start against an unmeasured filesystem.

   > Known, unrelated to production: the development host this work was done on
   > reported 238G/238G with ~152M free. That is a **local Windows workstation**
   > (Docker context `desktop-linux`), not the deployment host — production is
   > remote at `contraclaim:/opt/contraclaim-dms/projectDMS`. It caused a pytest
   > cache-write failure, worked around with `-p no:cacheprovider`. It says
   > **nothing** about production capacity, which remains unmeasured (G11).

**Migration dry run — not yet executed.** `20260814_0001_document_extraction_indexes`
has never run against a real database:
```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
  exec -T backend python -m rbac_backend.scripts.migrate_database --fail-on-warning
```
The unique `(document_id, extraction_run_id, page_number)` index must exist before
two workers can safely write page evidence.

---

## 3. Deploy with routing globally off (Step 2)

Deploy the commit with `UNIFIED_EXTRACTION_ENABLED=false` and an empty allowlist,
then rebuild only the affected services:

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps --build backend document-worker
```

Verify before going further:

- `/health/ready` returns healthy;
- exactly one service has `START_DOCUMENT_EXTRACTION_WORKERS=true`;
- `contract-worker` is still the only `RUN_SCHEDULER=true` owner;
- the web tier still runs cleanup, assignment alerts, and subscription
  lifecycle — `START_BACKGROUND_SERVICES` must remain `true` there;
- restart counts stable; job throughput unchanged;
- the status script reports zero `unified_v1` jobs.

---

## 4. Enable one canary organisation (Step 3)

Two workers with **disjoint claims**: the existing worker keeps every tenant on
legacy, and a separate canary worker takes only unified jobs.

```bash
# .env
UNIFIED_EXTRACTION_CANARY_ORG_IDS=<demo-org-id>
DOCUMENT_WORKER_PIPELINE_VERSIONS=legacy_v0   # existing worker: legacy only
DOCUMENT_WORKER_CANARY_REPLICAS=1             # canary worker: unified_v1 only

docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps document-worker document-worker-canary
```

The isolation is enforced by the claim predicate, not by convention: the canary
worker's query filters on `pipeline_version`, so it cannot claim a legacy job
even if one is queued ahead of it.

Enqueue one clean PDF and one mixed fixture in that organisation only. Assert:

- only those jobs carry `pipeline_version=unified_v1`;
- the clean document ends `completed`, publishes one extraction head, and adds
  **zero** intervention rows — this is the zero-cost claim, verified on real
  infrastructure rather than in a fixture;
- the mixed document checkpoints and reclaims its remaining pages and reaches
  `completed` or `human_review_required` — never `completed` with unextracted
  pages;
- no non-canary organisation acquires unified page or intervention rows;
- CPU, memory, queue age, latency, error rate, model calls, and cost stay within
  the Phase 0 baseline bounds.

---

## 5. Rollback drill (Step 4)

Rollback is **server-side**: no image redeploy, no evidence deletion.

```bash
# .env
UNIFIED_EXTRACTION_CANARY_ORG_IDS=          # new jobs are legacy_v0 again
DOCUMENT_WORKER_CANARY_REPLICAS=0           # stop claiming unified jobs
DOCUMENT_WORKER_PIPELINE_VERSIONS=          # existing worker: unrestricted

docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps document-worker document-worker-canary
```

Order matters. Clear the allowlist **first** so no further unified jobs are
created, then scale the canary worker down. Any unified job still queued is
picked up by the now-unrestricted default worker and processed on the unified
path — its persisted version is honoured, so it is never silently reprocessed as
legacy.

Then confirm:

- new demo jobs are created as `legacy_v0`;
- any in-flight `unified_v1` job keeps its `extraction_run_id`, remaining pages,
  attempt counts, and previously published head — decide explicitly whether to
  resume it or mark it review-required;
- readiness holds, legacy jobs complete, no restart loop;
- page evidence from before and during the canary is intact.

**Do not** `docker system prune`, drop `document_ocr_*`, or delete
`page_extraction_interventions`. The ledger is the audit answer to why a page
reads differently from its PDF.

---

## 6. Go/no-go evidence — NOT YET COLLECTED

Fill every row from actual output. An empty row is a no-go.

| Item | Value |
|---|---|
| Change window / authoriser | |
| Deployed commit (server-read) | |
| Compose file set (label-read) | |
| Verified backup age | |
| Migration dry-run result | |
| Baseline status JSON | |
| Canary org id | |
| Canary document ids (demo only) | |
| Clean doc: final state / head / interventions | |
| Mixed doc: final state / remaining pages | |
| Non-canary contamination check | |
| CPU / memory / latency vs baseline | |
| Model calls and cost | |
| Rollback result | |
| **Go / no-go** | |

Global enablement (`UNIFIED_EXTRACTION_ENABLED=true`) is a **separate authorised
change** and is blocked until both the canary and the rollback drill pass.

---

## 8. Task 7.6 acceptance matrix

| Step | Status | Required evidence | Currently held |
|---|---|---|---|
| **1.** Prove pre-canary safety | **PENDING AUTHORISATION** | Verified backup age; server-read branch + commit; label-read compose file set; baseline status JSON; `docker stats`; job counts by status/version; migration dry-run output; RAR decision | None. No production command run. RAR decision *is* held: negative → stays disabled. |
| **2.** Deploy with routing globally off | **PENDING AUTHORISATION** | Readiness output; one `START_DOCUMENT_EXTRACTION_WORKERS=true` owner; one `RUN_SCHEDULER=true` owner; web tier still runs the other three background tasks; stable restart counts; unchanged throughput; zero `unified_v1` jobs | None. Flag matrix is asserted in `test_document_worker_compose.py` against the compose file only — not against a running stack. |
| **3.** Enable one canary scope | **BLOCKED** | Only canary-org jobs are `unified_v1`; clean doc → `completed`, one head, **zero** interventions; mixed doc checkpoints/reclaims; no non-canary contamination; resource + cost within Phase 0 bounds | None — and **blocked on §0**: without a routing reader or a legacy path, "only canary-org jobs are unified" is unachievable. Every job already runs the unified path. |
| **4.** Execute and validate rollback | **BLOCKED** | New demo jobs are `legacy_v0`; in-flight run id/remaining pages/attempts/head survive; readiness; no restart loop; no evidence loss | None — **blocked on §0**. Clearing the allowlist changes no behaviour today, so it cannot demonstrate rollback. Rollback currently means redeploying the prior image. |
| **5.** Record go/no-go evidence | **BLOCKED** | §6 table fully populated from real output; explicit go/no-go | None. §6 is empty by design; an empty row is a no-go. Blocked by Steps 1–4. |
| **6.** Commit canary controls/runbook | **PASS (implementation-frozen)** | All six files in the task's Files list changed and committed, with the Interfaces contract satisfied | Complete — see below. Verified by test, not by inspection. This is an *implementation* pass only: it certifies the controls exist and behave, not that they have been exercised in production. |

### Step 6 detail

| Item | Status | Evidence |
|---|---|---|
| `core/config.py` — `UNIFIED_EXTRACTION_ENABLED`, `UNIFIED_EXTRACTION_CANARY_ORG_IDS` | PASS | commit `e038fa7` |
| `docker-compose.prod.yml` — canary env on `document-worker` | PASS | commit `e038fa7`; `test_document_worker_compose.py` 11 passed |
| `scripts/unified_extraction_canary_status.py` | PASS | commit `e038fa7`; read-only, syntax-checked |
| `docs/operations/...canary_and_rollback.md` | PASS | commit `e038fa7`, this file |
| Job records `pipeline_version` at creation | PASS | `test_unified_extraction_canary.py`, 15 passed |
| **Interfaces: claim predicate + dispatch honour persisted version** | **PASS** | `pipeline_routing.py`; `test_pipeline_routing_boundary.py` 29 passed; `document-worker-canary` pinned to `unified_v1`, `test_document_worker_compose.py` 20 passed |
| Minimal temporary `legacy_v0` extraction path | PASS | `DocumentProcessor._extract_legacy`; both paths converge on `_extract_and_persist` |
| **`legacy_v0` is genuinely the pre-Phase-3 control path** | **PASS** | `test_legacy_pipeline_equivalence.py` 31 passed. The pre-Phase-3 `process_document` body is vendored verbatim from commit `ffa844b` (the commit before `c3840f1` introduced page-wise extraction) and run against the same fakes; result fields and all collaborator calls compare equal for text-native, OCR-required and empty fixtures. |
| **`scripts/post_deploy_verify.sh` updated** | **PASS** | 15 required canary checks present and read-only; `test_post_deploy_verify_canary_checks.py` 21 passed |
| **Repository-root `.env.example` updated** | **PASS** | Unified extraction canary section added (global flag, allowlist, both worker claim restrictions, canary replicas, fallback, RAR) |
| **Compose fails closed on omission** | **PASS** | `DOCUMENT_WORKER_PIPELINE_VERSIONS` defaults to `legacy_v0`, not empty; `test_document_worker_compose.py::test_the_default_worker_fails_closed_when_the_variable_is_omitted` |

### Exact condition permitting "Phase 7 fully complete"

Phase 7 may be declared fully complete **only when all of the following hold**:

1. ~~Task 7.6 Step 6 reaches PASS~~ — **DONE.** The routing contract is
   genuinely implemented (legacy path + reader) and the legacy path is proven
   equivalent to pre-Phase-3 behaviour against the vendored `ffa844b` oracle;
2. ~~`scripts/post_deploy_verify.sh` and the root `.env.example` are updated~~ —
   **DONE**, subject to G12 (the script has never run against a live stack);
3. Steps 1–5 are executed inside an explicitly authorised production change
   window, and §6 is fully populated from real output;
4. The rollback drill (Step 4) passes without loss of page evidence;
5. Every unresolved gap in §7 is either closed or explicitly accepted in writing
   by the change authoriser;
6. The go/no-go in §6 records **go**.

Until then: **Phase 7 implementation = complete (7.1–7.5); Phase 7 operational
acceptance = NOT COMPLETE.** Global enablement remains a separate authorised
change.

---

## 7. Known gaps carried into this window

**These are unresolved acceptance gaps, not footnotes.** Each must be closed or
explicitly accepted in writing before Phase 7 can be declared fully complete.

| # | Gap | Effect on acceptance |
|---|---|---|
| ~~G1~~ | ~~Canary routing is non-functional~~ | **CLOSED.** Routing and claim boundaries implemented and tested (§0). |
| G1a | `_extract_legacy` is **temporary duplicate surface**. While it exists, two extraction behaviours are in production. | Delete it, `legacy_v0`, and the claim restriction once the unified pipeline is globally accepted. Tracked, not a blocker. |
| G2 | **Mongo-backed end-to-end pipeline never exercised.** The acceptance test fails loudly under `RUN_UNIFIED_EXTRACTION_E2E=1` rather than skipping green. | A canary would be the *first* real-infrastructure run of this pipeline. Elevated risk for Step 3. |
| G3 | **Migration `20260814_0001` not verified against the production database.** Dry run never executed. | Step 1 cannot be signed off. |
| G4 | **Unique `(document_id, extraction_run_id, page_number)` index must exist before concurrent worker writes.** Created only by G3's migration. | Without it, two workers can write contradictory evidence for the same page while `publish_run`'s count check still passes. Hard prerequisite for running more than one document worker. |
| G5 | **No production reconstruction/model adapters.** `EXTRACTION_FALLBACK_ENABLED=false`; the ladder is inert. | A canary **cannot validate the fallback ladder at all** — only the deterministic path. Ledger cost/token fields are untested against a real provider. |
| G6 | **Real scanned-page raster escalation never exercised end to end.** No fixture carries a page raster; `SCANNED_IMAGE`-with-empty-text is unit-tested only. | The most common real-world escalation trigger is unproven. |
| G7 | **Production archive MIME configuration unverified.** An explicit `ALLOWED_DOCUMENT_MIMES` / `ALLOWED_ENCLOSURE_MIMES` in production `.env` overrides the code default entirely, so `application/zip` must be added there. | Archive upload silently returns 415 until done. |
| G8 | **RAR unverified and disabled.** Phase 0 proved ZIP recursion in ClamAV; RAR was never functionally proven. | `RAR_UPLOAD_ENABLED` stays `false`. Enabling requires EICAR-in-RAR evidence in the same reviewed change. |
| G9 | `contract_ocr_pages` and `document_processing_jobs` remain **unindexed** (pre-existing). | Performance/consistency risk outside this phase's scope. |
| G10 | Intake still sniffs `filetype` without filename context. | The durable job carries the validated MIME; the original sniff is unchanged. |
| **G11** | **Production filesystem capacity never measured.** No figure for free space, Docker layer headroom, Mongo/journal headroom, OCR/raster temp space, backup size, or rollback headroom exists anywhere in this repository, and the plan defines no threshold. | **Step 1 cannot be signed off.** An explicit safe minimum must be derived from current production usage and the actual latest backup size, recorded in §6, and accepted by the change authoriser. See §2.6. |
| **G12** | **`post_deploy_verify.sh` is statically verified only.** Its 15 canary checks are asserted to exist, to be read-only, and to parse — never executed against a running stack. | The script itself is unproven in production. Run it during Step 2 and paste the output into §6; a check that misreads a live container would only surface there. |
| **G13** | **Rollback is implemented and unit-tested, never drilled.** Claim/routing rollback behaviour is proven in `test_pipeline_routing_boundary.py` (in-flight jobs keep their version, run/evidence identities survive an allowlist change). No process has been stopped, scaled, or recreated. | Step 4 remains BLOCKED on authorisation. "Rollback works" currently means *the code does the right thing*, not *the operation has been performed*. |
