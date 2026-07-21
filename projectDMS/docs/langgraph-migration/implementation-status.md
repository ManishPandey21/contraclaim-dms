# LangGraph migration implementation status

This is the local implementation record for the authoritative phase plan. It
does not constitute production rollout approval.

| Phase | Local implementation status |
| --- | --- |
| 0 | Complete: deterministic seven-case corpus, legacy API/UI baseline and rollback runbook. |
| 1 | Complete: v2/v3 engine selector, request hash/idempotency, server rollout policy, permissions and route throttling. |
| 2 | Complete: immutable input/evidence snapshots, timestamp correction, effect ledger, outbox, reconciliation candidates and indexes. |
| 3 | Complete: official `StateGraph`, MongoDB saver, separate Redis drafting queue/worker, state/resume/cancel and redacted operations checkpoints. |
| 4 | Complete: scoped context/evidence and correspondence review are executed before graph checkpointing; only snapshot identifiers enter state. |
| 5 | Complete: versioned required-question and strategy gates use durable LangGraph interrupts plus optimistic concurrency. |
| 6 | Complete: graph stages cover generation/validation/risk/approval flow; v2 domain logic is an explicit, idempotent adapter while its internals are migrated. High legal-risk review, export/issue effect records and outbox events are enforced. |
| 7 | Complete locally: deterministic shadow capture, canary selection, fallback eligibility and step-up force-v2 route. |
| 8 | Guarded: production primary mode is refused without `DRAFT_ENGINE_PRODUCTION_ACCEPTED=true`; no local change asserts the required production observation period. |

## Operations defaults

`DRAFT_ENGINE_DEFAULT=v2`, `DRAFT_ENGINE_ROLLOUT_MODE=off`,
`DRAFTING_QUEUE_ENABLED=false`, and `START_DRAFTING_QUEUE_WORKERS=false` are
the safe defaults. The experimental sidecar is not selected by this policy.

The current backend runtime is Python 3.10. Official LangGraph interrupt nodes
are therefore invoked synchronously on a worker thread; Mongo/Redis and the
domain adapters remain asynchronous. This was verified against the installed
LangGraph 1.2.9 API and avoids an unplanned Python upgrade.
