import React, { useEffect, useState } from "react";

import { getArbitrationDraftCitations, type CitationState, type DraftCitations } from "@/services/arbitration-drafting-api";

interface Props {
  draftId: string;
  /** Reloads when the draft gets a new version. */
  version?: number;
}

const STATE_LABEL: Record<CitationState, string> = {
  verified: "Verified",
  unverified: "Unverified",
  unresolved: "Unresolved",
};

// Text and a shape, not colour alone.
const STATE_CLASS: Record<CitationState, string> = {
  verified: "border-green-700 bg-green-50 text-green-900",
  unverified: "border-amber-600 bg-amber-50 text-amber-950",
  unresolved: "border-red-700 bg-red-50 text-red-900",
};
const STATE_MARK: Record<CitationState, string> = { verified: "✓", unverified: "?", unresolved: "✕" };

/**
 * The citations of a pleading draft, each as verified, unverified or
 * unresolved, with everything that stands between the draft and approval. The
 * figures are the server's, from the computation the approval gate itself uses.
 */
const DraftCitationPanel: React.FC<Props> = ({ draftId, version }) => {
  const [data, setData] = useState<DraftCitations | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    setFailed(false);
    getArbitrationDraftCitations(draftId)
      .then((result) => {
        if (active) setData(result);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [draftId, version]);

  if (failed) return <p role="alert">Citation states could not be loaded.</p>;
  if (!data) return <p className="text-muted-foreground">Checking citations…</p>;

  const blockers = [
    ...data.approval_blockers,
    ...data.missing_evidence.map((item) => `Evidence required: ${item}`),
  ];
  return (
    <section aria-label="Citation states" className="space-y-3">
      <dl className="grid grid-cols-3 gap-2 text-center">
        {(Object.keys(STATE_LABEL) as CitationState[]).map((state) => (
          <div key={state} className={`rounded-md border p-2 ${STATE_CLASS[state]}`}>
            <dt className="text-xs">
              <span aria-hidden="true">{STATE_MARK[state]} </span>
              {STATE_LABEL[state]}
            </dt>
            <dd className="text-lg font-semibold">{data.counts[state]}</dd>
          </div>
        ))}
      </dl>
      {data.version === null ? (
        <p className="text-xs text-muted-foreground">No version yet. Citations are checked when one is generated or saved.</p>
      ) : data.counts.cited === 0 ? (
        <p className="text-xs text-muted-foreground">Version {data.version} cites no sources.</p>
      ) : (
        <ul className="flex flex-wrap gap-1" aria-label="Citations in this version">
          {data.items.map((item) => (
            <li
              key={item.source_key}
              title={item.reason ?? item.label ?? undefined}
              className={`rounded-full border px-2 py-0.5 text-xs ${STATE_CLASS[item.state]}`}
            >
              <span aria-hidden="true">{STATE_MARK[item.state]} </span>
              {item.source_key}
              <span className="sr-only">: {STATE_LABEL[item.state]}</span>
            </li>
          ))}
        </ul>
      )}
      {data.can_be_approved ? (
        <p className="text-xs text-green-900">
          Nothing in the evidence blocks approval of version {data.version}. The workflow gates still apply.
        </p>
      ) : (
        <div role="status" className="space-y-1 rounded-md border border-red-700 bg-red-50 p-2 text-xs text-red-950">
          <p className="font-medium">This version cannot be approved or exported for filing</p>
          {data.cannot_be_approved_reason && <p>{data.cannot_be_approved_reason}</p>}
          {blockers.length > 0 && (
            <ul className="list-disc pl-4">
              {blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
};

export default DraftCitationPanel;
