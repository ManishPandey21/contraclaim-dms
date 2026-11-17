import React from "react";
import { Download, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import EvidenceContractConfirmations from "@/components/claim-bench/EvidenceContractConfirmations";
import { useSubmission } from "@/hooks/useClaimFindings";
import { formatDateIN } from "@/lib/format-in";
import type { ParagraphSource, SubmissionSection } from "@/types/claimFindings";

interface Props {
  claimId: string;
  readOnly: boolean;
}

const SOURCE_LABEL: Record<ParagraphSource["kind"], string> = {
  claim: "Claim record",
  head: "Head of claim",
  clause: "Contract clause",
  notice: "Notice",
  delay: "Delay assessment",
  quantum: "Quantum build-up",
  document: "Document",
  standard_wording: "Standard wording",
};

const Section: React.FC<{
  number: number;
  section: SubmissionSection;
  stale: boolean;
  disabled: boolean;
  onRegenerate: () => void;
}> = ({ number, section, stale, disabled, onRegenerate }) => (
  <section aria-labelledby={`section-${section.key}`} className="space-y-2">
    <header className="flex flex-wrap items-center justify-between gap-2 border-b pb-1">
      <h3 id={`section-${section.key}`} className="text-base font-semibold">
        {number}. {section.title}
      </h3>
      <div className="flex items-center gap-2">
        {stale && <Badge variant="warning">Record has changed</Badge>}
        <Badge variant="neutral">{section.produced_by === "skill" ? "Claims skill" : "Composed from the record"}</Badge>
        <Button variant="ghost" size="sm" disabled={disabled} onClick={onRegenerate} aria-label={`Re-compose ${section.title}`}>
          <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
          Re-compose
        </Button>
      </div>
    </header>
    {section.paragraphs.length === 0 && (
      <p className="text-sm text-muted-foreground">The record holds nothing for this section.</p>
    )}
    {section.paragraphs.map((paragraph, index) => (
      <details key={index} className="group rounded-md px-2 py-1 hover:bg-muted/40">
        <summary className="cursor-pointer list-none text-sm leading-relaxed">
          {paragraph.text}
          <span className="ml-2 text-xs text-muted-foreground group-open:hidden">
            {paragraph.sources.length} source{paragraph.sources.length === 1 ? "" : "s"}
          </span>
        </summary>
        <ul className="mt-1 list-disc pl-5 text-xs text-muted-foreground" aria-label="Where this paragraph comes from">
          {paragraph.sources.map((source, sourceIndex) => (
            <li key={sourceIndex}>
              {SOURCE_LABEL[source.kind]}: {source.label || source.ref}
            </li>
          ))}
        </ul>
      </details>
    ))}
    {section.internal_notes.length > 0 && (
      <aside className="rounded-md border border-amber-600 bg-amber-50 p-2 text-xs text-amber-950">
        <p className="font-medium">For you, not in the document</p>
        <ul className="list-disc pl-5">
          {section.internal_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </aside>
    )}
    {section.degraded && section.degraded_reason && section.produced_by !== "skill" && (
      <p className="text-xs text-muted-foreground">{section.degraded_reason}</p>
    )}
  </section>
);

/**
 * The claim submission as it would be issued, composed from the locked board.
 * Every paragraph shows where it comes from. Notes to the reader of this
 * screen are kept visibly apart from the document's own text.
 */
const SubmissionPanel: React.FC<Props> = ({ claimId, readOnly }) => {
  const { query, compose, download } = useSubmission(claimId);
  const view = query.data;
  if (query.isLoading) return <p className="p-4 text-sm text-muted-foreground">Loading the submission…</p>;
  if (!view) return <p role="alert" className="p-4 text-sm text-destructive">The submission could not be loaded.</p>;
  const busy = compose.isPending || download.isPending;
  const cannotCompose = readOnly || busy || !view.can_compose;

  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Claim submission</h2>
          <p className="text-sm text-muted-foreground">
            {view.exists
              ? `Composed ${formatDateIN(view.composed_at)} from the locked board.`
              : "Composed from the locked board: the heads as decided, clause wording as indexed, and the documents linked to the claim."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={cannotCompose} onClick={() => compose.mutate([])}>
            {view.exists ? (view.stale_sections ? "Re-compose what changed" : "Check against the record") : "Compose the submission"}
          </Button>
          {(["docx", "pdf"] as const).map((format) => (
            <Button key={format} variant="outline" size="sm" disabled={busy || !view.can_export} onClick={() => download.mutate(format)}>
              <Download className="mr-2 h-4 w-4" aria-hidden="true" />
              {format === "docx" ? "Word" : "PDF"}
            </Button>
          ))}
        </div>
      </header>

      {view.cannot_compose_reason && (
        <p role="status" className="rounded-md border bg-muted/40 p-3 text-sm">
          {view.cannot_compose_reason}
        </p>
      )}
      {view.exists && !view.cannot_compose_reason && view.cannot_export_reason && (
        <p role="status" className="rounded-md border border-amber-600 bg-amber-50 p-3 text-sm text-amber-950">
          {view.cannot_export_reason}
        </p>
      )}
      <EvidenceContractConfirmations
        items={view.unconfirmed_evidence ?? []}
        claimId={claimId}
        readOnly={readOnly}
        onConfirmed={() => query.refetch()}
      />
      {(view.evidence_notes ?? []).length > 0 && (view.unconfirmed_evidence ?? []).length === 0 && (
        <section aria-labelledby="submission-contract-notes" className="rounded-md border border-amber-600 bg-amber-50 p-3 text-amber-950">
          <h3 id="submission-contract-notes" className="text-sm font-semibold">
            Evidence whose contract is not confirmed
          </h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
            {(view.evidence_notes ?? []).map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      )}
      {view.exists && !view.approved_for_issue && (
        <p role="note" className="text-xs text-muted-foreground">
          The claim has not been approved, so an export is marked “Draft. Not approved for issue.” Approval is given on
          the claim's record page.
        </p>
      )}

      {view.exists && (
        <article aria-label="Claim submission" className="mx-auto max-w-3xl space-y-6 rounded-md border bg-background p-6">
          {view.sections.map((item, index) => (
            <Section
              key={item.section.key}
              number={index + 1}
              section={item.section}
              stale={item.stale}
              disabled={cannotCompose}
              onRegenerate={() => compose.mutate([item.section.key])}
            />
          ))}
          <section aria-labelledby="evidence-schedule" className="space-y-2">
            <h3 id="evidence-schedule" className="border-b pb-1 text-base font-semibold">
              Evidence schedule
            </h3>
            {view.evidence.length === 0 ? (
              <p className="text-sm text-muted-foreground">No document is linked to the claim.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted-foreground">
                    <th scope="col" className="py-1 pr-3 font-medium">No.</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Date</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Reference</th>
                    <th scope="col" className="py-1 font-medium">Document</th>
                  </tr>
                </thead>
                <tbody>
                  {view.evidence.map((item) => (
                    <tr key={item.document_id} className="border-t">
                      <td className="py-1 pr-3 font-mono text-xs">{item.number}</td>
                      <td className="py-1 pr-3">{item.date ? formatDateIN(item.date) : "Undated"}</td>
                      <td className="py-1 pr-3">{item.reference || ""}</td>
                      <td className="py-1">{item.title}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </article>
      )}
    </div>
  );
};

export default SubmissionPanel;
