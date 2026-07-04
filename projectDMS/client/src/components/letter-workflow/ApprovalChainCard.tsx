import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CheckCircle2, Circle, UserCheck } from "lucide-react";
import type { ApprovalStage, ApprovalStep } from "@/types/letterDrafting";

const STAGES: { stage: ApprovalStage; label: string }[] = [
  { stage: "drafter", label: "Drafter approval" },
  { stage: "reviewer", label: "Reviewer approval" },
  { stage: "final", label: "Final approval" },
];

interface Props {
  approvals: ApprovalStep[];
  disabled?: boolean;
  onApproveStage: (stage: ApprovalStage, comment?: string) => Promise<void> | void;
}

/**
 * Drafter -> Reviewer -> Final approval chain. Stages run in order with
 * separation of duties enforced server-side; only the final stage locks and
 * approves the draft.
 */
const ApprovalChainCard = ({ approvals, disabled, onApproveStage }: Props) => {
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const done = useMemo(() => {
    const map = new Map<ApprovalStage, ApprovalStep>();
    (approvals ?? []).forEach((step) => map.set(step.stage, step));
    return map;
  }, [approvals]);

  const nextStage = STAGES.find(({ stage }) => !done.has(stage))?.stage;

  const handleApprove = async () => {
    if (!nextStage) return;
    setSubmitting(true);
    try {
      await onApproveStage(nextStage, comment.trim() || undefined);
      setComment("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <UserCheck className="h-4 w-4" />
          Approval chain
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Drafter → Reviewer → Final. Each stage needs its own approver; the final
          stage locks the approved draft version.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <ol className="space-y-2">
          {STAGES.map(({ stage, label }) => {
            const step = done.get(stage);
            const isNext = stage === nextStage;
            return (
              <li key={stage} className="flex items-start gap-2 text-sm">
                {step ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                ) : (
                  <Circle
                    className={`mt-0.5 h-4 w-4 shrink-0 ${
                      isNext ? "text-blue-500" : "text-gray-300"
                    }`}
                  />
                )}
                <div>
                  <p className={step ? "font-medium" : isNext ? "font-medium" : "text-muted-foreground"}>
                    {label}
                    {isNext && !step ? " — next" : ""}
                  </p>
                  {step && (
                    <p className="text-xs text-muted-foreground">
                      by {step.approved_by ?? "unknown"}
                      {step.approved_at
                        ? ` on ${new Date(step.approved_at).toLocaleString()}`
                        : ""}
                      {step.comment ? ` — “${step.comment}”` : ""}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
        {nextStage ? (
          <div className="space-y-2">
            <input
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Approval comment (optional)"
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
            <Button
              size="sm"
              className="gap-2"
              onClick={handleApprove}
              disabled={disabled || submitting}
            >
              <CheckCircle2 className="h-4 w-4" />
              Approve as {nextStage}
            </Button>
          </div>
        ) : (
          <p className="text-sm font-medium text-emerald-700">
            Approval chain complete.
          </p>
        )}
      </CardContent>
    </Card>
  );
};

export default ApprovalChainCard;
