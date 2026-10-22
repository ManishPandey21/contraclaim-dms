# Production-session prompt — EXTEND unified_v1 canary (for owner approval)

Paste this into a separate production session only after the owner has approved
EXTEND. This cleanup session did not change the canary.

---

PRODUCTION SESSION — EXTEND THE unified_v1 CANARY (SAME ORG, VOLUME-BASED EXIT)

Owner approval: EXTEND the existing canary. Scope is unchanged: organisation
`c06c95b2-c6f3-4e79-998f-af4843524f7b` (GULERMAK-SAM JV) only, and `unified` global stays `false`.
Do NOT add a second organisation. Do NOT change any `.env` value, image, compose file or
allowlist. This is an observation extension, not a reconfiguration.

## Background

The first 24-hour window (2026-10-13 15:36Z → 2026-10-14 15:36Z) recorded 0 canary
uploads and 0 `unified_v1` jobs. Its result was **INSUFFICIENT VOLUME**, which is not
a stability PASS. Evidence for that window:
`/var/backups/contraclaim-stg-evidence/REL-b98c1f48-CANARY-20261013/`. Do not modify it.

## 0. Re-establish (read only)

- Server `git branch --show-current` and `git rev-parse HEAD`. Expect release `b98c1f48`.
  Release has since advanced with client-only and test-only merges (#44, #67) that are not deployed.
  Report the actual SHA.
- Running images: backend, contract-worker, document-worker and canary worker on `8042765f`; client on `74d08b27`.
- Routing proof, in-container `resolve_pipeline_version`: the canary org resolves to `unified_v1`,
  and every other org and no-org resolves to `legacy_v0`.
- Queue: active jobs, dead letters (baseline 4), review queue.
- Canary worker: restarts, OOM kills, memory.

If anything differs from these expectations, STOP and report. Do not repair anything.

## 1. Restart the read-only observer for a new window

Reuse `/opt/contraclaim-dms/r111_observer.sh` (with `r111_obs.py` and `r111_obs_post.py`). Write to a
NEW evidence directory, `REL-<sha8>-CANARY-EXT-<YYYYMMDD>`. Never write into the first window's directory.

## 2. Exit criteria — volume, not just time

The window closes when BOTH of these hold:

1. At least 7 days have elapsed, or the owner closes it earlier.
2. At least 4 fresh `unified_v1` jobs have completed from the canary org. Where reasonably available,
   they should include:
   - a native-text PDF
   - a scanned PDF
   - a mixed native/scanned PDF
   - a table-heavy PDF

Only real or owner-approved safe documents count. Do not synthesise uploads into production
without explicit owner approval per document. Prior production evidence (the GLM re-upload and
document 4299) is historical context and does not count toward the fresh-job minimum.

If any job enters `human_review_required`, an organisation-admin or project-admin of the canary
org performs the review, NOT Super Admin. This closes the open production RBAC gap.

## 3. Per job, record

status, pipeline_version, pages (OCR / native), processing time, retries, canonical / vector /
graph / metadata outcome, quality-gate verdict, the reviewer's role (if reviewed),
and any error in backend, document-worker or canary-worker logs.

## 4. Rollback triggers (report and STOP; the owner decides)

- A job from any non-canary org routed to `unified_v1`, or a canary-org job claimed by the legacy worker.
- A canary job marked completed while canonical, vector or graph output is missing (the silent-success pattern).
- A canary-worker OOM or crash loop.
- Any cross-tenant data appearing in review or retrieval.

## 5. Closing report

WINDOW START / END, CANARY JOBS by status, document types covered, retries, leaks, worker
cross-claims, canonical / vector / graph failures, OOM / restarts, reviewer roles, and
RESULT = PASS / EXTEND / ROLLBACK / INSUFFICIENT VOLUME, with evidence paths.

PRODUCTION CONFIG CHANGED = NO (expected)
