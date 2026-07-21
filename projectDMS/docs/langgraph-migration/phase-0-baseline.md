# LangGraph migration — Phase 0 baseline

This baseline was captured against the local repository on 2026-07-21 before
introducing the official LangGraph runtime. It establishes the rollback-safe
behaviour that must remain available while the rollout policy is `off`.

## Verified runtime anchors

- The active browser flow is `LetterDraftPage` → `useLetterDrafting` →
  `POST /api/letters/{letter_id}/drafting/runs`.
- `DraftRunService.create_run` is the synchronous v2 engine. It already builds
  context, source evidence, an incoming-letter analysis, probing questions,
  planning sheet, reply matrix, draft artifact, validation report and legal
  risk report.
- The existing `services/langgraph` sidecar and the hand-written
  `ai_workflows/langgraph/letter_pipeline.py` are not the production drafting
  path.
- The current `ContractIngestQueue` is durable Redis infrastructure. The
  in-memory `BackgroundJobProcessor` is not acceptable for drafting work.

## Regression corpus and measurements

`backend/rbac_backend/tests/fixtures/letter_drafting_langgraph_phase0_cases.json`
contains deterministic, non-customer cases for delay/EOT, variation, payment,
quality, claim, dispute and progress. For each phase candidate, capture the
following before comparison and retain only redacted hashes/metrics in CI:

| Signal | Baseline requirement |
| --- | --- |
| Evidence | Source IDs/hashes, permitted use, and source-ledger count |
| Workflow | Question IDs/categories, planning sheet, reply matrix, next action |
| Output | Draft hash, validation findings, legal-risk severity/count |
| Operational | latency, model/token cost, retry/fallback reason, queue duration |
| Safety | request idempotency, concurrent submit behaviour, tenant isolation |

The legacy service currently records probing questions but continues into
planning and generation in the same request. Phase 5 replaces that observed
behaviour with an interrupt/resume gate; the final migration test must prove a
draft cannot be generated until all required answers are accepted.

## Acceptance evidence retained locally

- Backend legacy drafting tests: `66 passed` on 2026-07-21.
- The frontend test `LetterDraftPage.basic-render.test.tsx` pins the disabled
  `LANGGRAPH_ENABLED` rendering path.
- The feature/rollout default remains disabled until Phase 7 criteria are met.

## Rollback owner checklist

1. Set `DRAFT_ENGINE_ROLLOUT_MODE=off` and retain `DRAFT_ENGINE_DEFAULT=v2`.
2. Stop only the dedicated drafting worker; do not stop contract-ingest workers.
3. Requeue no jobs automatically. Inspect the Mongo effect ledger and resume or
   cancel each affected run through the audited endpoint.
4. If a v3 run is fallback-eligible and has no external side effect, start a
   new v2 run linked by `fallback_of_run_id`; preserve its immutable snapshots.
5. Verify API 200 legacy creation, tenant authorization, final approval and
   export/issue tests before declaring rollback complete.

No production cutover is authorized by this local baseline. Phase 8 requires
the separately collected production acceptance evidence defined in the plan.
