import React, { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/hooks/useLetterBench";
import { confirmEvidenceContract, type EvidenceContractItem } from "@/services/claim-findings-api";

const STATE_TEXT: Record<EvidenceContractItem["state"], string> = {
  unidentified: "does not say which contract it belongs to",
  unverified: "names the claim's contract, which the Contract Master does not hold",
};

interface Props {
  items: EvidenceContractItem[];
  /** The claim the items belong to, when every item is of one claim. */
  claimId?: string;
  readOnly?: boolean;
  /** Called once a record is confirmed, so the caller reloads what it shows. */
  onConfirmed: () => void;
}

/**
 * OD-1 (option B): before the submission is exported or the claim escalated, a person who
 * may edit the claim confirms each record whose contract is not shown belongs to the
 * claim's contract, and says how they know. The record itself is never changed.
 */
const EvidenceContractConfirmations: React.FC<Props> = ({ items, claimId, readOnly, onConfirmed }) => {
  const [open, setOpen] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  if (items.length === 0) return null;
  const key = (item: EvidenceContractItem) => `${item.claim_id ?? claimId}:${item.kind}:${item.record_id}`;

  const confirm = async (item: EvidenceContractItem) => {
    setBusy(true);
    try {
      await confirmEvidenceContract(item.claim_id ?? claimId ?? "", {
        record_kind: item.kind,
        record_id: item.record_id,
        reason: reason.trim(),
      });
      toast.success("Confirmed for the claim's contract");
      setOpen(null);
      setReason("");
      onConfirmed();
    } catch (error) {
      toast.error(errorMessage(error, "The record could not be confirmed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="evidence-confirm-heading" className="space-y-2 rounded-md border border-amber-600 bg-amber-50 p-3 text-amber-950">
      <h3 id="evidence-confirm-heading" className="text-sm font-semibold">
        Confirm which contract this evidence belongs to
      </h3>
      <p className="text-xs">
        Each record below must be confirmed by a person before the submission is exported or the claim is escalated.
        The record itself is not changed; who confirmed it, when and why is kept.
      </p>
      <ul className="space-y-2 text-sm">
        {items.map((item) => (
          <li key={key(item)} className="space-y-1">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>
                <span className="font-medium">{item.label}</span> {STATE_TEXT[item.state]}.
              </span>
              {!readOnly && open !== key(item) && (
                <Button size="sm" variant="outline" onClick={() => { setOpen(key(item)); setReason(""); }}>
                  Confirm…
                </Button>
              )}
            </div>
            {open === key(item) && (
              <div className="space-y-1">
                <Label htmlFor={`confirm-${key(item)}`} className="text-xs">
                  How do you know it belongs to the claim's contract?
                </Label>
                <Textarea id={`confirm-${key(item)}`} rows={2} value={reason} onChange={(event) => setReason(event.target.value)} />
                <div className="flex gap-2">
                  <Button size="sm" disabled={busy || reason.trim().length < 10} onClick={() => confirm(item)}>
                    Confirm for this contract
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setOpen(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
};

export default EvidenceContractConfirmations;
