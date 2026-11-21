import React, { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/hooks/useLetterBench";
import { undoEscalation } from "@/services/claim-escalation-api";

interface Props {
  claimId: string;
  caseId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onUndone: () => void;
}

/**
 * Undo a claim's escalation (D-18, 4B-37), while the case has no pleading version. The
 * server decides whether it is still allowed and says why not; this asks for the reason.
 */
const UndoEscalationDialog: React.FC<Props> = ({ claimId, caseId, open, onOpenChange, onUndone }) => {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  const undo = async () => {
    setBusy(true);
    setRefusal(null);
    try {
      const done = await undoEscalation(claimId, caseId, reason.trim());
      toast.success(
        done.case_discarded
          ? "Escalation undone. The case it created held nothing else and was removed."
          : "Escalation undone. Its rows in the case are archived."
      );
      setReason("");
      onOpenChange(false);
      onUndone();
    } catch (error) {
      setRefusal(errorMessage(error, "The escalation could not be undone."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Undo the escalation</DialogTitle>
          <DialogDescription>
            Possible until the case has its first pleading version. The claim goes back to the status it had, its
            evidence is unfrozen, and the rows it added to the case are archived with your reason.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="undo-escalation-reason">Why is the escalation being undone?</Label>
          <Textarea id="undo-escalation-reason" rows={3} value={reason} onChange={(event) => setReason(event.target.value)} />
          <p className="text-xs text-muted-foreground">Kept in the claim's and the case's audit trail. At least 10 characters.</p>
          {refusal && (
            <p role="alert" className="text-sm text-destructive">
              {refusal}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={undo} disabled={busy || reason.trim().length < 10}>
            Undo escalation
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default UndoEscalationDialog;
