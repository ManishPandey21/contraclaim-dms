import React, { useEffect, useState } from "react";

import { Label } from "@/components/ui/label";
import { fetchClaimContractOptions, type ClaimContractOptions } from "@/services/claims-api";

interface Props {
  projectId: string;
  /** The contract chosen in the form ("" when none). */
  value: string;
  onChange: (contractId: string) => void;
  /** Tells the form whether a choice is required before saving. */
  onOptions?: (options: ClaimContractOptions | null) => void;
  /** Set when editing a claim that already has a contract: it is shown and cannot be changed. */
  fixed?: { contractId: string; label: string } | null;
}

/**
 * Which contract a claim is made under (4B-18, D-23).
 *
 * One contract on the project: it is shown, not asked for. Several: the person
 * chooses, and nothing is guessed. None they can read: the claim says
 * "No contract identified".
 */
const ClaimContractField: React.FC<Props> = ({ projectId, value, onChange, onOptions, fixed }) => {
  const [options, setOptions] = useState<ClaimContractOptions | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setOptions(null);
    setFailed(false);
    if (!projectId || fixed) {
      onOptions?.(null);
      return;
    }
    fetchClaimContractOptions(projectId)
      .then((loaded) => {
        if (!live) return;
        setOptions(loaded);
        onOptions?.(loaded);
      })
      .catch(() => {
        if (!live) return;
        setFailed(true);
        onOptions?.(null);
      });
    return () => {
      live = false;
    };
    // `onOptions` is a callback from the form; re-running on its identity would refetch on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, fixed?.contractId]);

  if (fixed) {
    return (
      <div>
        <Label>Contract</Label>
        <p className="py-2 text-sm">{fixed.label}</p>
        <p className="text-xs text-muted-foreground">
          Fixed for this claim: the records linked to it belong to this contract.
        </p>
      </div>
    );
  }
  if (!projectId) return null;
  if (failed) {
    return (
      <div>
        <Label>Contract</Label>
        <p role="alert" className="py-2 text-sm text-destructive">
          The project's contracts could not be loaded. Close this form and try again.
        </p>
      </div>
    );
  }
  if (!options) {
    return (
      <div>
        <Label>Contract</Label>
        <p className="py-2 text-sm text-muted-foreground">Loading the project's contracts…</p>
      </div>
    );
  }
  if (options.options.length === 0) {
    return (
      <div>
        <Label>Contract</Label>
        <p className="py-2 text-sm font-medium">No contract identified</p>
        <p className="text-xs text-muted-foreground">
          This project has no contract you can read. The claim is saved without one; it can be given one later.
        </p>
      </div>
    );
  }
  if (!options.must_choose) {
    return (
      <div>
        <Label>Contract</Label>
        <p className="py-2 text-sm">{options.options[0].label}</p>
        <p className="text-xs text-muted-foreground">The project's only contract.</p>
      </div>
    );
  }
  return (
    <div>
      <Label htmlFor="claim-contract">Contract</Label>
      <select
        id="claim-contract"
        className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
        value={value}
        aria-describedby="claim-contract-help"
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose the contract</option>
        {options.options.map((item) => (
          <option key={item.contract_id} value={item.contract_id}>
            {item.label}
          </option>
        ))}
      </select>
      <p id="claim-contract-help" className="text-xs text-muted-foreground">
        This project has more than one contract. Records from another contract cannot be linked to the claim.
      </p>
    </div>
  );
};

export default ClaimContractField;
