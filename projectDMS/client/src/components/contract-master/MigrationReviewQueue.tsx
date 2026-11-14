/**
 * Migration review queue — T30.
 *
 * A flat, filterable table rather than permanent grouping (HITL decision UI-04):
 * migration volume is unknown until the rehearsal measures it, and forcing a
 * grouping model before then would bake in a guess.
 *
 * The thing this surface exists to prevent is an operator promoting something
 * nobody decided. So it shows **why each candidate is blocked, per axis, in the
 * server's own words** — a bare "needs review" chip tells an operator that
 * something is wrong but not which decision is theirs to make, which is how a
 * queue becomes a list of items people click through.
 *
 * A suggestion is marked as a suggestion. Three heuristics agreeing is still one
 * heuristic counted three times, and rendering that as a classification would
 * make a filename authoritative by presentation.
 *
 * There is no promote-all, no approve-all, and no auto-resolve. Their absence is
 * the feature, and it is asserted by test.
 */

import React, { useMemo, useState } from "react";

import type { ReconciliationCandidate } from "@/services/contract-master-v1-api";

export type BlockerFilter = "all" | "scope" | "type" | "promotable";

export interface MigrationReviewQueueProps {
  candidates: ReconciliationCandidate[];
  onClaim?: (candidateId: string) => void;
  onAdjudicate?: (candidateId: string) => void;
  onPromote?: (candidateId: string) => void;
}

export function MigrationReviewQueue({
  candidates,
  onClaim,
  onAdjudicate,
  onPromote,
}: MigrationReviewQueueProps) {
  const [filter, setFilter] = useState<BlockerFilter>("all");

  const visible = useMemo(() => {
    if (filter === "all") return candidates;
    if (filter === "promotable") {
      return candidates.filter((c) => c.promotion_blocked_reasons.length === 0);
    }
    return candidates.filter((c) =>
      c.promotion_blocked_reasons.some((reason) => reason.startsWith(filter)),
    );
  }, [candidates, filter]);

  return (
    <div data-testid="migration-queue">
      <div className="mb-3 flex items-center gap-2 text-sm">
        <label htmlFor="migration-filter">Filter</label>
        <select
          id="migration-filter"
          className="rounded border border-slate-300 px-2 py-1"
          value={filter}
          onChange={(event) => setFilter(event.target.value as BlockerFilter)}
        >
          <option value="all">All candidates</option>
          <option value="scope">Blocked on scope</option>
          <option value="type">Blocked on type</option>
          <option value="promotable">Promotable</option>
        </select>
        <span className="text-xs text-slate-600">
          {visible.length} of {candidates.length}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="text-left text-slate-600">
            <tr>
              <th className="py-2">Document</th>
              <th>Scope</th>
              <th>Type</th>
              <th>Blocking reasons</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((candidate) => (
              <tr
                key={candidate.candidate_id}
                className="border-t border-slate-100 align-top"
                data-testid="migration-row"
              >
                <td className="py-2">
                  <code className="text-xs">{candidate.canonical_document_id}</code>
                </td>
                <td data-testid="scope-state">{candidate.scope_state}</td>
                <td data-testid="type-state">
                  {candidate.type_state}
                  {candidate.type_state === "TYPE_SUGGESTED" ? (
                    <span className="ml-1 text-xs text-amber-800" data-testid="suggestion-marker">
                      (suggestion, not confirmed)
                    </span>
                  ) : null}
                </td>
                <td>
                  {candidate.promotion_blocked_reasons.length ? (
                    <ul className="list-disc pl-4 text-xs text-rose-800">
                      {candidate.promotion_blocked_reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  ) : (
                    <span className="text-xs text-green-800">Both axes resolved.</span>
                  )}
                  {candidate.scope_hint ? (
                    <p className="mt-1 text-xs text-slate-600" data-testid="scope-hint">
                      Hint: {candidate.scope_hint}
                    </p>
                  ) : null}
                </td>
                <td>
                  <div className="flex gap-2">
                    <button
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                      onClick={() => onClaim?.(candidate.candidate_id)}
                    >
                      Claim
                    </button>
                    <button
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                      onClick={() => onAdjudicate?.(candidate.candidate_id)}
                    >
                      Adjudicate
                    </button>
                    <button
                      className="rounded bg-slate-900 px-2 py-1 text-xs text-white disabled:bg-slate-300"
                      disabled={candidate.promotion_blocked_reasons.length > 0}
                      onClick={() => onPromote?.(candidate.candidate_id)}
                      data-testid="promote-candidate"
                    >
                      Promote
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default MigrationReviewQueue;
