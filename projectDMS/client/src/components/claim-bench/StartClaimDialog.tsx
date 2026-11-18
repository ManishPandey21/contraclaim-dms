import React, { useEffect, useState } from "react";

import ClaimContractField from "@/components/claims/ClaimContractField";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/plain-language";
import { startClaim, type ClaimContractOptions, type ClaimStartResult } from "@/services/claims-api";

/** What was answered, handed to the full form when the person asks for more details. */
export interface StartAnswers {
  whatHappened: string;
  awareOn: string;
  contractId: string;
}

interface Props {
  open: boolean;
  projectId: string;
  onOpenChange: (open: boolean) => void;
  onStarted: (result: ClaimStartResult) => void;
  onMoreDetails: (answers: StartAnswers) => void;
}

const today = () => new Date().toISOString().slice(0, 10);

/**
 * A claim starts from three questions (4B-20, D-24): what happened, when you became
 * aware, and which contract. That creates the claim, as a draft, and its board with
 * the first head; everything else is worked on the board. "More details" opens the
 * full form with what has been answered so far.
 */
const StartClaimDialog: React.FC<Props> = ({ open, projectId, onOpenChange, onStarted, onMoreDetails }) => {
  const [whatHappened, setWhatHappened] = useState("");
  const [awareOn, setAwareOn] = useState("");
  const [contractId, setContractId] = useState("");
  const [options, setOptions] = useState<ClaimContractOptions | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setWhatHappened("");
      setAwareOn("");
      setContractId("");
      setError(null);
    }
  }, [open]);

  const missing: string[] = [];
  if (whatHappened.trim().length < 10) missing.push("say what happened (at least 10 characters)");
  if (!awareOn) missing.push("give the date you became aware");
  else if (awareOn > today()) missing.push("the date you became aware cannot be in the future");
  if (options?.must_choose && !contractId) missing.push("choose the contract");

  const start = async () => {
    if (missing.length) return;
    setSaving(true);
    setError(null);
    try {
      onStarted(
        await startClaim({
          project_id: projectId,
          what_happened: whatHappened.trim(),
          aware_on: awareOn,
          contract_id: contractId || undefined,
        })
      );
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Start a claim</DialogTitle>
          <DialogDescription>
            Three questions. The claim is saved as a draft and opens on its board, where the rest is worked.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="start-what-happened">What happened?</Label>
            <Textarea
              id="start-what-happened"
              rows={4}
              value={whatHappened}
              onChange={(event) => setWhatHappened(event.target.value)}
              placeholder="e.g. Access to the depot site was handed over on 20 March, eighteen days late."
            />
          </div>
          <div>
            <Label htmlFor="start-aware-on">When did you become aware of it?</Label>
            <Input
              id="start-aware-on"
              type="date"
              max={today()}
              value={awareOn}
              onChange={(event) => setAwareOn(event.target.value)}
            />
            <p className="pt-1 text-xs text-muted-foreground">Notice periods are normally counted from this date.</p>
          </div>
          <ClaimContractField projectId={projectId} value={contractId} onChange={setContractId} onOptions={setOptions} />
          {missing.length > 0 && (whatHappened || awareOn) && (
            <p className="text-xs text-muted-foreground">To start the claim, {missing.join("; ")}.</p>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onMoreDetails({ whatHappened: whatHappened.trim(), awareOn, contractId })}
            disabled={saving}
          >
            More details
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
              Cancel
            </Button>
            <Button type="button" onClick={() => void start()} disabled={saving || missing.length > 0}>
              {saving ? "Starting..." : "Start claim"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default StartClaimDialog;
