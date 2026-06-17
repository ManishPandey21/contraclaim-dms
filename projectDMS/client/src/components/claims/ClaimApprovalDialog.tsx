import React, { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CheckCircle2, Loader2, RotateCcw, Send, UserPlus } from "lucide-react";
import { toast } from "sonner";
import {
  ApprovalRecord,
  ApprovalState,
  approveClaim,
  assignClaimReviewer,
  getClaimApproval,
  returnClaim,
  submitClaimForReview,
} from "@/services/claims-api";

interface Props {
  claimId: string | null;
  claimTitle?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const STATE_LABEL: Record<ApprovalState, string> = {
  draft: "Draft",
  assigned: "Assigned",
  in_review: "In review",
  approved: "Approved",
  returned: "Returned",
};

const STATE_COLOR: Record<ApprovalState, string> = {
  draft: "bg-gray-500",
  assigned: "bg-blue-500",
  in_review: "bg-amber-500",
  approved: "bg-green-600",
  returned: "bg-red-500",
};

const ClaimApprovalDialog: React.FC<Props> = ({ claimId, claimTitle, open, onOpenChange }) => {
  const [record, setRecord] = useState<ApprovalRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reviewer, setReviewer] = useState("");
  const [comment, setComment] = useState("");

  const load = useCallback(async () => {
    if (!claimId) return;
    setLoading(true);
    try {
      setRecord(await getClaimApproval(claimId));
    } catch {
      toast.error("Failed to load approval state");
    } finally {
      setLoading(false);
    }
  }, [claimId]);

  useEffect(() => {
    if (open) {
      setReviewer("");
      setComment("");
      void load();
    }
  }, [open, load]);

  const run = async (fn: () => Promise<ApprovalRecord>, ok: string) => {
    setBusy(true);
    try {
      setRecord(await fn());
      toast.success(ok);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const state = record?.state;
  const canAssign = state === "draft" || state === "assigned" || state === "returned";
  const canSubmit = state === "assigned" || state === "returned";
  const canDecide = state === "in_review";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Approval workflow</DialogTitle>
          <DialogDescription>{claimTitle || claimId}</DialogDescription>
        </DialogHeader>

        {loading || !record ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading…
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">State:</span>
              <Badge className={STATE_COLOR[record.state]}>{STATE_LABEL[record.state]}</Badge>
              {record.reviewer_id && (
                <span className="text-sm text-muted-foreground">
                  · reviewer <span className="font-medium">{record.reviewer_id}</span>
                </span>
              )}
            </div>

            {canAssign && (
              <div className="space-y-2 rounded-md border p-3">
                <Label>Assign reviewer (user id)</Label>
                <div className="flex gap-2">
                  <Input
                    value={reviewer}
                    onChange={(e) => setReviewer(e.target.value)}
                    placeholder="reviewer user id"
                  />
                  <Button
                    disabled={busy || !reviewer.trim()}
                    onClick={() =>
                      run(() => assignClaimReviewer(record.resource_id, reviewer.trim()), "Reviewer assigned")
                    }
                  >
                    <UserPlus className="mr-2 h-4 w-4" />
                    Assign
                  </Button>
                </div>
              </div>
            )}

            {canSubmit && (
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => run(() => submitClaimForReview(record.resource_id), "Submitted for review")}
              >
                <Send className="mr-2 h-4 w-4" />
                Submit for review
              </Button>
            )}

            {canDecide && (
              <div className="space-y-2 rounded-md border p-3">
                <Label>Decision comment (optional)</Label>
                <Textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} />
                <div className="flex gap-2">
                  <Button
                    disabled={busy}
                    onClick={() => run(() => approveClaim(record.resource_id, comment || undefined), "Approved")}
                  >
                    <CheckCircle2 className="mr-2 h-4 w-4" />
                    Approve
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => run(() => returnClaim(record.resource_id, comment || undefined), "Returned")}
                  >
                    <RotateCcw className="mr-2 h-4 w-4" />
                    Return
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  The claim&apos;s drafter cannot approve or return their own work.
                </p>
              </div>
            )}

            {record.history.length > 0 && (
              <div className="space-y-1">
                <Label className="text-xs uppercase text-muted-foreground">History</Label>
                <ul className="space-y-1 text-sm">
                  {record.history.map((h, i) => (
                    <li key={i} className="flex justify-between gap-2">
                      <span>
                        <span className="font-medium">{h.action}</span>
                        {h.actor_id ? ` by ${h.actor_id}` : ""}
                        {h.comment ? ` — ${h.comment}` : ""}
                      </span>
                      <span className="text-muted-foreground">
                        {h.at ? new Date(h.at).toLocaleString() : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default ClaimApprovalDialog;
