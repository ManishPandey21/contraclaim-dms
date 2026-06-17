import React, { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Loader2, Quote, Sparkles } from "lucide-react";
import { toast } from "sonner";
import {
  assessClaim,
  ClaimAssessment,
  getClaimAssessments,
} from "@/services/claims-api";

interface Props {
  claimId: string | null;
  claimTitle?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function citationLabel(c: ClaimAssessment["citations"][number]): string {
  const clause = c.clause_number ? `Clause ${c.clause_number}` : null;
  return [clause, c.clause_title, c.document_title || c.file_name]
    .filter(Boolean)
    .join(" · ") || "Source";
}

const ClaimAssessmentDialog: React.FC<Props> = ({ claimId, claimTitle, open, onOpenChange }) => {
  const [current, setCurrent] = useState<ClaimAssessment | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    if (!claimId) return;
    setLoading(true);
    try {
      const list = await getClaimAssessments(claimId);
      setCurrent(list[0] ?? null);
    } catch {
      toast.error("Failed to load assessments");
    } finally {
      setLoading(false);
    }
  }, [claimId]);

  useEffect(() => {
    if (open) {
      setCurrent(null);
      void load();
    }
  }, [open, load]);

  const run = async () => {
    if (!claimId) return;
    setRunning(true);
    try {
      setCurrent(await assessClaim(claimId));
      toast.success("Assessment generated");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Assessment failed");
    } finally {
      setRunning(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-indigo-500" />
            Clause-grounded assessment
          </DialogTitle>
          <DialogDescription>{claimTitle || claimId}</DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading…
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {current
                  ? `Last assessed ${current.created_at ? new Date(current.created_at).toLocaleString() : ""}`
                  : "No assessment yet. Generate one grounded in the project contract."}
              </p>
              <Button onClick={run} disabled={running}>
                {running ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Sparkles className="mr-2 h-4 w-4" />
                )}
                {current ? "Regenerate" : "Generate assessment"}
              </Button>
            </div>

            {current && (
              <>
                <div className="whitespace-pre-wrap rounded-md border bg-muted/30 p-3 text-sm">
                  {current.answer || "Information not found."}
                </div>

                <div>
                  <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase text-muted-foreground">
                    <Quote className="h-3 w-3" />
                    Source ledger ({current.citations.length})
                  </div>
                  {current.citations.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No clauses cited — treat the answer as unsupported.
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {current.citations.map((c, i) => (
                        <li key={i} className="rounded-md border p-2 text-sm">
                          <div className="flex items-center gap-2">
                            <Badge variant="outline">{i + 1}</Badge>
                            <span className="font-medium">{citationLabel(c)}</span>
                            {c.page != null && (
                              <span className="text-xs text-muted-foreground">p.{c.page}</span>
                            )}
                          </div>
                          {c.snippet && (
                            <p className="mt-1 text-xs text-muted-foreground">{c.snippet}</p>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default ClaimAssessmentDialog;
