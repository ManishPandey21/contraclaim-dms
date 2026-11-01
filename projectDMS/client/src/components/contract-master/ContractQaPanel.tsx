/**
 * Contract Q&A — modes, the four outcomes, and provenance-gated persistence.
 *
 * T29. This completes the component set in this folder; its test was written
 * first and had been sitting without an implementation.
 *
 * Three things it refuses to do, each of which is the easy version:
 *
 * * **Default a mode.** "Current state" is the obvious default and the wrong
 *   one: a caller who forgot to say *when* would get a confident answer to a
 *   question they did not ask. `Historical` will not default its date either.
 * * **Flatten the outcomes.** Valid empty, degraded, authority failure and an
 *   invalid request are four different answers. Rendering them all as "no
 *   results" is what let a source outage read as "the contract is silent on
 *   this point" — the failure mode this whole programme exists to remove.
 * * **Decide provenance locally.** Save, export and cite follow the server's
 *   capability answer. Having results on screen is not evidence that the run
 *   was recorded, and a client that inferred otherwise would offer to persist
 *   an artefact that cannot be reconstructed.
 *
 * A degraded answer stays visible with a banner above it (HITL decision UI-03);
 * hiding the answer would over-correct, since the surviving candidates are still
 * canonically authorised.
 */

import React, { useCallback, useState } from "react";

import {
  ContractMasterApiError,
  type EvidenceResponse,
  type EvidenceSearchCommand,
  type QueryMode,
} from "@/services/contract-master-v1-api";

export interface ProvenanceCapabilities {
  can_save: boolean;
  can_export: boolean;
  can_cite: boolean;
}

export interface ContractQaPanelProps {
  projectId: string;
  contractId: string;
  search: (command: EvidenceSearchCommand) => Promise<EvidenceResponse>;
  provenanceCapabilities: ProvenanceCapabilities;
  /**
   * Where the question was launched from. A browse/catalogue entry that is not
   * evidence-ready cannot ask anything: a catalogue listing says an instrument
   * exists, not that it applies.
   */
  launchedFrom?: { source: "browse" | "evidence"; evidenceReady: boolean };
}

type PanelState =
  | { kind: "idle" }
  | { kind: "answered"; response: EvidenceResponse }
  | { kind: "authority_failure"; detail: string }
  | { kind: "invalid_request"; detail: string };

export function ContractQaPanel({
  projectId,
  contractId,
  search,
  provenanceCapabilities,
  launchedFrom,
}: ContractQaPanelProps) {
  const canLaunch = launchedFrom ? launchedFrom.evidenceReady : true;
  const [mode, setMode] = useState<QueryMode | "">("");
  const [eventDate, setEventDate] = useState("");
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<PanelState>({ kind: "idle" });

  const blocker =
    mode === ""
      ? "Choose a mode. There is no implicit current-state default."
      : mode === "historical" && !eventDate
        ? "A historical question needs a date; it does not default to today."
        : null;

  const ask = useCallback(async () => {
    if (blocker || mode === "" || !canLaunch) return;
    try {
      const response = await search({
        project_id: projectId,
        contract_id: contractId,
        query: question,
        query_mode: mode,
        ...(mode === "historical" ? { event_date: eventDate } : {}),
      });
      setState({ kind: "answered", response });
    } catch (error) {
      if (error instanceof ContractMasterApiError && error.isAuthorityFailure) {
        setState({ kind: "authority_failure", detail: error.detail });
      } else if (error instanceof ContractMasterApiError) {
        setState({ kind: "invalid_request", detail: error.detail });
      } else {
        setState({ kind: "invalid_request", detail: String(error) });
      }
    }
  }, [blocker, canLaunch, contractId, eventDate, mode, projectId, question, search]);

  return (
    <div>
      <div className="flex flex-wrap items-end gap-4 text-sm">
        <div>
          <label className="block font-medium" htmlFor="qa-question">
            Question
          </label>
          <input
            id="qa-question"
            className="mt-1 w-72 rounded border border-slate-300 px-2 py-1"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
        </div>
        <div>
          <label className="block font-medium" htmlFor="qa-mode">
            Mode
          </label>
          <select
            id="qa-mode"
            className="mt-1 rounded border border-slate-300 px-2 py-1"
            value={mode}
            onChange={(event) => setMode(event.target.value as QueryMode | "")}
          >
            <option value="">— choose —</option>
            <option value="current_state">Current state</option>
            <option value="historical">As at a date</option>
          </select>
        </div>
        {mode === "historical" ? (
          <div>
            <label className="block font-medium" htmlFor="qa-event-date">
              Event date
            </label>
            <input
              id="qa-event-date"
              type="date"
              className="mt-1 rounded border border-slate-300 px-2 py-1"
              value={eventDate}
              onChange={(event) => setEventDate(event.target.value)}
            />
          </div>
        ) : null}
        <button
          className="rounded bg-slate-900 px-3 py-1.5 text-white disabled:bg-slate-300"
          disabled={blocker !== null || !canLaunch}
          onClick={ask}
        >
          Ask
        </button>
      </div>

      {blocker ? <p className="mt-2 text-sm text-rose-700">{blocker}</p> : null}

      {!canLaunch ? (
        <p className="mt-2 text-sm text-rose-700" data-testid="launch-blocked">
          This is a catalogue/browse listing, not evidence: the instrument is not
          evidence-ready, so it cannot answer a question.
        </p>
      ) : null}

      {state.kind === "authority_failure" ? (
        <div
          className="mt-4 rounded border border-rose-300 bg-rose-50 p-3 text-sm"
          data-testid="authority-failure"
          role="alert"
        >
          <strong>Authority failure.</strong> Eligibility could not be resolved, so nothing was
          searched. This is not an empty answer. {state.detail}
        </div>
      ) : null}

      {state.kind === "invalid_request" ? (
        <div
          className="mt-4 rounded border border-amber-300 bg-amber-50 p-3 text-sm"
          data-testid="invalid-request"
        >
          {state.detail}
        </div>
      ) : null}

      {state.kind === "answered" && state.response.outcome === "valid_empty" ? (
        <div
          className="mt-4 rounded border border-slate-300 bg-slate-50 p-3 text-sm"
          data-testid="valid-empty"
        >
          <strong>No applicable evidence.</strong> This contract has nothing applicable at this
          mode.
          {/* No widening affordance, deliberately. */}
        </div>
      ) : null}

      {state.kind === "answered" && state.response.outcome !== "valid_empty" ? (
        <div className="mt-4" data-testid="results">
          {state.response.outcome === "degraded" ? (
            <div
              className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm"
              data-testid="degraded-banner"
              role="alert"
            >
              <strong>Answer generated with reduced evidence coverage.</strong> Unavailable:{" "}
              {state.response.degraded_sources.join(", ")}
            </div>
          ) : null}

          <p className="text-sm text-slate-700">
            {state.response.total_count} clause(s) matched.
          </p>

          <div className="mt-3 flex gap-2">
            <button
              className="rounded border border-slate-300 px-3 py-1 disabled:text-slate-400"
              disabled={!provenanceCapabilities.can_save}
            >
              Save
            </button>
            <button
              className="rounded border border-slate-300 px-3 py-1 disabled:text-slate-400"
              disabled={!provenanceCapabilities.can_export}
            >
              Export
            </button>
            <button
              className="rounded border border-slate-300 px-3 py-1 disabled:text-slate-400"
              disabled={!provenanceCapabilities.can_cite}
            >
              Cite
            </button>
          </div>

          {!provenanceCapabilities.can_save ? (
            <p className="mt-2 text-xs text-slate-600" data-testid="provenance-note">
              Save, export and cite are unavailable: the server did not record provenance for this
              answer.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default ContractQaPanel;
