/**
 * Contract Master state, as a section of the existing contract viewer.
 *
 * Six facts, rendered separately, because they are separate and any two of them
 * can disagree. The seventh — the viewer state — is a summary *of* them, not a
 * replacement for them.
 *
 * **Evidence readiness is never stored here.** Not in state that outlives a
 * fetch, not in localStorage, not in a cache keyed by instrument alone. Every
 * conjunct of readiness — applicability, projection currency, content
 * authority, authorisation — can change without this instrument being touched,
 * so a remembered "ready" is a claim the client has no way to keep true. It is
 * read from each response and re-read on refresh.
 *
 * **The cache key includes the project.** The same `contract_id` can exist in
 * two projects and mean different instruments; a key that omitted the project
 * would serve one project's answer to the other, which is a tenancy leak wearing
 * the clothes of a performance optimisation.
 *
 * **Scope is a fact, not a field.** After promotion it is immutable, so there is
 * no input, no select and no edit button — changing an instrument's scope is a
 * different legal statement, not a metadata edit.
 *
 * **A correction discloses its consequence first.** Advancing the revision
 * invalidates the current projection, and the instrument stops being usable as
 * evidence until reprojection finishes. Saying so afterwards would be telling
 * somebody what they had already done.
 */

import React, { useCallback, useEffect, useState } from "react";

import type { InstrumentDetail } from "@/services/contract-master-v1-api";
import { StateBadge } from "./StateBadge";
import { STATE_EXPLANATION } from "./stateVocabulary";

export interface ClassificationSuggestion {
  contract_document_type: string;
  basis: string;
}

export interface InstrumentStatePanelProps {
  contractDocumentId: string;
  projectId: string;
  fetchInstrument: (contractDocumentId: string, projectId: string) => Promise<InstrumentDetail>;
  /** A non-authoritative guess, if one exists. Never shown as the classification. */
  classificationSuggestion?: ClassificationSuggestion;
  onRetryProjection?: (contractDocumentId: string) => void;
}

function Dimension({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div data-testid={`dimension-${id}`} className="border-t border-slate-100 py-2">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium">{children}</dd>
    </div>
  );
}

export function InstrumentStatePanel({
  contractDocumentId,
  projectId,
  fetchInstrument,
  classificationSuggestion,
  onRetryProjection,
}: InstrumentStatePanelProps) {
  const [instrument, setInstrument] = useState<InstrumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Project first: the same instrument id in another project is another answer.
  const cacheKey = `contract-master:${projectId}:${contractDocumentId}`;

  const load = useCallback(async () => {
    try {
      setInstrument(await fetchInstrument(contractDocumentId, projectId));
      setError(null);
    } catch (cause) {
      setInstrument(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [contractDocumentId, projectId, fetchInstrument]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-900">
        {error}
      </p>
    );
  }
  if (!instrument) {
    return <p className="text-sm text-slate-600">Loading instrument state…</p>;
  }

  const projectionFailed = instrument.projection_status === "FAILED";

  return (
    <section className="space-y-3">
      <header className="flex flex-wrap items-center gap-3">
        <StateBadge state={instrument.viewer_state} />
        <span className="text-xs text-slate-600">
          {STATE_EXPLANATION[instrument.viewer_state]}
        </span>
        <button
          data-testid="refresh"
          className="ml-auto rounded border border-slate-300 px-2 py-1 text-xs"
          onClick={() => void load()}
        >
          Refresh
        </button>
      </header>

      <dl>
        <Dimension id="scope" label="Scope">
          {instrument.scope_level === "project"
            ? `Project — ${instrument.project_id}`
            : "Organisation-owned"}
          <span className="ml-2 text-xs font-normal text-slate-500">
            (authoritative; not editable)
          </span>
        </Dimension>

        <Dimension id="classification" label="Classification">
          {instrument.contract_document_type ?? "unclassified"}
          <span className="ml-2 text-xs font-normal text-slate-500">
            revision {instrument.classification_revision}
          </span>
        </Dimension>

        <Dimension id="applicability" label="Applicability">
          {instrument.applicability_count} contract(s)
        </Dimension>

        <Dimension id="projection" label="Projection">
          {instrument.projection_status}
          {instrument.projection_revision !== null ? (
            <span className="ml-2 text-xs font-normal text-slate-500">
              generation {instrument.projection_revision}
            </span>
          ) : null}
        </Dimension>

        <Dimension id="content" label="Content authority">
          {instrument.content_consumable ? "available" : "unavailable"}
        </Dimension>

        <Dimension id="readiness" label="Evidence readiness">
          {instrument.evidence_ready ? "Ready" : "Not ready"}
          <span className="ml-2 text-xs font-normal text-slate-500">
            (derived from the server on every load)
          </span>
        </Dimension>
      </dl>

      {classificationSuggestion ? (
        <p
          data-testid="classification-suggestion"
          className="rounded border border-dashed border-amber-400 bg-amber-50 p-3 text-sm text-amber-900"
        >
          Suggested: <strong>{classificationSuggestion.contract_document_type}</strong> — basis:{" "}
          {classificationSuggestion.basis}. This is a suggestion and is{" "}
          <strong>not confirmed</strong>; only an operator can resolve a classification.
        </p>
      ) : null}

      <p data-testid="correction-consequence" className="rounded bg-slate-50 p-3 text-sm text-slate-700">
        Correcting the classification advances its revision, which makes the current projection
        pending. This instrument will be unavailable as evidence until reprojection completes.
      </p>

      <div className="flex items-center gap-2">
        <button
          data-testid="use-as-evidence"
          className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
          disabled={!instrument.evidence_ready}
        >
          Use as evidence
        </button>
        {projectionFailed ? (
          <button
            className="rounded border border-amber-400 px-3 py-1.5 text-sm text-amber-900"
            onClick={() => onRetryProjection?.(contractDocumentId)}
          >
            Retry projection
          </button>
        ) : null}
      </div>

      <p data-testid="cache-key" className="sr-only">
        {cacheKey}
      </p>
    </section>
  );
}

export default InstrumentStatePanel;
