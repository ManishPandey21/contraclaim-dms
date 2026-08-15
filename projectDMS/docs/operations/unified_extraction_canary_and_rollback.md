# Unified extraction — canary and rollback runbook

**Status:** controls implemented and committed. **The canary has not been run.**
No production deployment, canary window, or rollback drill described here has been
executed. Section 6 is the evidence template and is deliberately empty.

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

**The property that makes this safe:** `pipeline_version` is decided **once, at job
creation**, and persisted on the job. Every reader honours the stored value rather
than re-deriving it. Consequences:

- emptying the allowlist cannot reclassify work already queued;
- a canary worker cannot sweep up other tenants' jobs;
- a job queued before this change has no `pipeline_version` and is treated as legacy.

Everything fails closed onto the legacy path: unknown scope, missing organisation,
or absent version all stay legacy.

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

Add **only** the approved demo organisation id, then recreate the worker:

```bash
UNIFIED_EXTRACTION_CANARY_ORG_IDS=<demo-org-id>
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps document-worker
```

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

Rollback is **not** a redeploy and **never** deletes evidence.

```bash
UNIFIED_EXTRACTION_CANARY_ORG_IDS=
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps document-worker
```

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

## 7. Known gaps carried into this window

Honest list; none are blockers for a canary but all affect interpretation.

- The **Mongo-backed end-to-end acceptance test** is unimplemented. It fails
  loudly under `RUN_UNIFIED_EXTRACTION_E2E=1` rather than skipping green, so the
  canary is currently the *first* real-infrastructure exercise of this pipeline.
- The **fallback ladder has no production model adapters**. With
  `EXTRACTION_FALLBACK_ENABLED=false` it is inert, so the canary measures the
  deterministic path only. Cost figures in the ledger are untested against a real
  provider.
- **No fixture carries a real page raster**, so the `SCANNED_IMAGE`-with-empty-text
  escalation path has never run end to end.
- `contract_ocr_pages` and `document_processing_jobs` remain **unindexed**.
- Production `.env` must add `application/zip` to `ALLOWED_DOCUMENT_MIMES` and
  `ALLOWED_ENCLOSURE_MIMES` before archive upload works; the code default alone
  does not apply where an explicit env value is set.
- Intake still sniffs `filetype` without filename context; the durable job carries
  the validated MIME, but the original sniff is unchanged.
