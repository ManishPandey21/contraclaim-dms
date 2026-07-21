# LangGraph v3 drafting rollback runbook

Use this runbook only for the dedicated letter-drafting LangGraph worker. It
does not authorize changes to contract ingestion, document queues, Redis data,
Mongo snapshots, or issued documents.

1. Set `DRAFT_ENGINE_ROLLOUT_MODE=off`; leave `DRAFT_ENGINE_DEFAULT=v2`.
2. Restart the web process to load the policy, then stop the dedicated drafting
   worker cleanly. Existing legacy v2 requests remain synchronous and available.
3. Query affected `letter_draft_runs` and their effect-ledger records. Do not
   replay a run that has an external side effect, an approved artifact, or an
   issued document.
4. For an eligible paused or failed run, use its recorded snapshots and
   `fallback_reason` to create a new v2 run. Keep the original run immutable
   and link the fallback to it.
5. Verify `/runs` returns its legacy 200 response, source-ledger authorization,
   stage approval, export, and issue behavior. Record the operator, reason,
   affected run IDs and validation results in the draft audit events.

Escalate any cross-tenant data exposure, duplicated external effect, or issued
document discrepancy before any retry.
