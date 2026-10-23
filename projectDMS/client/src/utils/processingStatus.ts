// Which document-processing statuses are final, so pollers know when to stop.
//
// Mirrors the backend, not a guess:
// - backend/rbac_backend/models/processing_state.py `TERMINAL_STATES`:
//   completed, stored_only, human_review_required, failed.
// - Durable job statuses written by services/document_service.py: completed,
//   dead_lettered, human_review_required, stored_only (queued, processing and
//   retrying are in flight).
// - `skipped`: written once, with the job dead-lettered, for a soft-deleted
//   document.
// - `not_queued`: the viewer's own placeholder when no job exists.
//
// Deliberately NOT terminal:
// - `metadata_extracted`: on the queued path the run writes it before the job
//   writes its verdict, so a poller reading the document would stop early.
// - `partially_processed` while the job is waiting to re-run the pages it
//   still owes (job stage `awaiting_page_resume`), or while a resolved human
//   review is being published (`review_resolved_publication_pending`). Any other
//   `partially_processed` is a settled verdict: text was extracted but
//   required processing did not finish, and nothing will run again on its own.

const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "dead_lettered",
  "human_review_required",
  "stored_only",
  "skipped",
  "not_queued",
]);

/** Job stage of a partially processed document that will be retried. */
export const PAGE_RESUME_STAGE = "awaiting_page_resume";

/**
 * Job stage after a human approved every review page (Release 1.1.1): the
 * deferred embeddings and graph publication are running, so `completed` or an
 * indexing verdict is still to come.
 */
export const REVIEW_PUBLICATION_PENDING_STAGE = "review_resolved_publication_pending";

const IN_FLIGHT_PARTIAL_STAGES: ReadonlySet<string> = new Set([
  PAGE_RESUME_STAGE,
  REVIEW_PUBLICATION_PENDING_STAGE,
]);

export const isTerminalProcessingStatus = (
  status: string | null | undefined,
  stage?: string | null
): boolean => {
  const normalized = (status || "").toLowerCase();
  if (!normalized) return false;
  if (TERMINAL_STATUSES.has(normalized)) return true;
  if (normalized === "partially_processed") {
    return !IN_FLIGHT_PARTIAL_STAGES.has((stage || "").toLowerCase());
  }
  return false;
};
