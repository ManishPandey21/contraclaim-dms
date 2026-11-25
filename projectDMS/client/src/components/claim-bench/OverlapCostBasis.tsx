import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { listEntityDocumentLinks } from "@/services/document-relationships-api";
import type { OverlapView } from "@/types/claimBench";

interface Props {
  claimId: string;
  overlap: OverlapView;
  disabled: boolean;
  onRecord: (cost: { contractual_basis: string; causation: string; evidence_document_ids: string[] }) => void;
  onReview: () => void;
}

/**
 * The cost basis for an overlap decided as carrying cost (4B-22, D-16).
 *
 * Its days carry time at once, but cost only when the contractual basis, the
 * causation and the cost evidence are recorded and a different person holding
 * the claim's determination authority has reviewed them. The server decides who
 * may review; a refusal is shown as its message.
 */
const OverlapCostBasis: React.FC<Props> = ({ claimId, overlap, disabled, onRecord, onReview }) => {
  const recorded = overlap.finding?.cost_basis ?? null;
  const [editing, setEditing] = useState(false);
  const [basis, setBasis] = useState(recorded?.contractual_basis ?? "");
  const [causation, setCausation] = useState(recorded?.causation ?? "");
  const [evidence, setEvidence] = useState<string[]>(recorded?.evidence_document_ids ?? []);
  const documents = useQuery({
    queryKey: ["claim-bench", "claim-documents", claimId],
    queryFn: () => listEntityDocumentLinks("claim", claimId),
    enabled: editing || Boolean(recorded),
    retry: false,
  });
  const docs = (documents.data ?? []).map((link) => ({
    id: String(link.document_id),
    name: link.document?.subject || link.document?.filename || "Untitled document",
  }));
  const nameOf = (id: string) => docs.find((doc) => doc.id === id)?.name ?? "Restricted or removed document";
  const ready = basis.trim().length >= 10 && causation.trim().length >= 10 && evidence.length > 0;
  const id = `cost-${overlap.overlap_id}`;

  return (
    <section aria-label="Cost basis" className="space-y-2 rounded-md border border-dashed p-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium">Cost</p>
        {overlap.cost_established ? (
          <Badge variant="success">Carries cost: basis reviewed</Badge>
        ) : (
          <Badge variant="warning">Time only</Badge>
        )}
      </div>
      {overlap.cost_status && <p className="text-xs text-muted-foreground">{overlap.cost_status}</p>}
      {recorded && !editing && (
        <div className="space-y-1 text-xs">
          <p>
            <span className="font-medium">Contractual basis: </span>
            {recorded.contractual_basis}
          </p>
          <p>
            <span className="font-medium">Causation: </span>
            {recorded.causation}
          </p>
          <p>
            <span className="font-medium">Cost evidence: </span>
            {recorded.evidence_document_ids.map(nameOf).join("; ")}
          </p>
        </div>
      )}
      {editing ? (
        <div className="space-y-2">
          <div>
            <Label htmlFor={`${id}-basis`} className="text-xs">
              Contractual basis for cost
            </Label>
            <Textarea id={`${id}-basis`} rows={2} value={basis} disabled={disabled} onChange={(e) => setBasis(e.target.value)} />
          </div>
          <div>
            <Label htmlFor={`${id}-causation`} className="text-xs">
              Causation: why these costs arise from the Employer's delay alone
            </Label>
            <Textarea
              id={`${id}-causation`}
              rows={2}
              value={causation}
              disabled={disabled}
              onChange={(e) => setCausation(e.target.value)}
            />
          </div>
          <fieldset className="space-y-1">
            <legend className="text-xs">Cost evidence, from the documents linked to this claim</legend>
            {documents.isLoading && <p className="text-xs text-muted-foreground">Loading documents…</p>}
            {documents.data && docs.length === 0 && (
              <p className="text-xs text-muted-foreground">No documents are linked to this claim yet.</p>
            )}
            {docs.map((doc) => (
              <label key={doc.id} className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={evidence.includes(doc.id)}
                  disabled={disabled}
                  onChange={(e) =>
                    setEvidence((current) => (e.target.checked ? [...current, doc.id] : current.filter((item) => item !== doc.id)))
                  }
                />
                {doc.name}
              </label>
            ))}
          </fieldset>
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={disabled || !ready}
              onClick={() => {
                onRecord({ contractual_basis: basis.trim(), causation: causation.trim(), evidence_document_ids: evidence });
                setEditing(false);
              }}
            >
              Record cost basis
            </Button>
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={disabled} onClick={() => setEditing(true)}>
            {recorded ? "Change cost basis" : "Record cost basis"}
          </Button>
          {recorded && !recorded.reviewed_by && (
            <Button size="sm" variant="outline" disabled={disabled} onClick={onReview}>
              Review cost basis
            </Button>
          )}
        </div>
      )}
      {recorded && !recorded.reviewed_by && (
        <p className="text-xs text-muted-foreground">
          A review must come from someone other than the person who recorded it, holding the claim's determination
          authority.
        </p>
      )}
    </section>
  );
};

export default OverlapCostBasis;
