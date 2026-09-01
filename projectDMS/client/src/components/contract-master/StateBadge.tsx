/**
 * The five frozen viewer states, and the styling rule that goes with them.
 *
 * Only `APPLICABLE - EVIDENCE READY` gets success styling. That is not a visual
 * preference — the previous UI gave a green badge to ingestion "completed",
 * which answered "did processing finish" while being read as "may this be used
 * as evidence". Two different questions, one badge, and the wrong answer looked
 * reassuring (DEBT-12).
 *
 * Every other state names the gate that is closed, so a user can tell *why*
 * something is not usable rather than just that it is not.
 *
 * "Active" is deliberately absent from this vocabulary. It would fuse
 * applicability, projection currency and content authority into one word that
 * cannot be true or false.
 */

import React from "react";

import type { ContractViewerState } from "@/services/contract-master-v1-api";

const TONE: Record<ContractViewerState, string> = {
  "APPLICABLE - EVIDENCE READY": "border-green-300 bg-green-100 text-green-900",
  "CATALOGUED / UNASSIGNED": "border-slate-300 bg-slate-100 text-slate-800",
  "APPLICABLE - PROJECTION PENDING": "border-amber-300 bg-amber-100 text-amber-900",
  "APPLICABLE - CONTENT BLOCKED": "border-rose-300 bg-rose-100 text-rose-900",
  "SUPERSEDED / WITHDRAWN": "border-zinc-400 bg-zinc-200 text-zinc-700",
};

/** A one-line explanation of which gate is open or closed. */
export const STATE_EXPLANATION: Record<ContractViewerState, string> = {
  "CATALOGUED / UNASSIGNED":
    "In the organisation catalogue and applicable to no contract. Not evidence anywhere.",
  "APPLICABLE - PROJECTION PENDING":
    "Legally applicable, but its derived projection is not current, so it cannot answer yet.",
  "APPLICABLE - EVIDENCE READY":
    "Applicable, projection current, content available, and you are authorised.",
  "APPLICABLE - CONTENT BLOCKED":
    "Legally applicable, and its content is unavailable. Both are true at once.",
  "SUPERSEDED / WITHDRAWN":
    "No longer in force for this contract. Retained for historical questions.",
};

export function StateBadge({ state }: { state: ContractViewerState }) {
  return (
    <span
      data-testid="state-badge"
      title={STATE_EXPLANATION[state]}
      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${TONE[state]}`}
    >
      {state}
    </span>
  );
}

export default StateBadge;
