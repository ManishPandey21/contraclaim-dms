/**
 * The badge for a contract instrument's frozen viewer state.
 *
 * The state vocabulary itself — the tones and the one-line explanations — lives
 * in ./stateVocabulary, so this module exports components only.
 */

import React from "react";

import type { ContractViewerState } from "@/services/contract-master-v1-api";

import { STATE_EXPLANATION, TONE } from "./stateVocabulary";

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
