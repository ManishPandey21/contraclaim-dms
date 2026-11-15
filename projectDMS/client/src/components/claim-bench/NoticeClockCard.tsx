import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock, FileSearch } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { claimBenchKeys } from "@/hooks/useClaimBench";
import { errorMessage } from "@/hooks/useLetterBench";
import { NOTICE_BADGE_VARIANT, describeNotice } from "@/lib/claims-helpers";
import { formatDateIN } from "@/lib/format-in";
import {
  fetchNoticeCandidates,
  setNoticeRequirement,
  verifyNoticeRequirement,
} from "@/services/claim-bench-api";
import type { NoticeAssessment } from "@/services/claims-api";
import type { NoticePeriodCandidate, NoticeRequirement, NoticeRequirementInput } from "@/types/claimBench";

interface Props {
  claimId: string;
  claimType: string;
  projectId?: string | null;
  assessment?: NoticeAssessment | null;
  requirement?: NoticeRequirement | null;
  readOnly: boolean;
}

const TONE_BORDER = {
  neutral: "border-slate-300",
  ok: "border-green-700",
  warn: "border-amber-600",
  danger: "border-red-700",
} as const;

/**
 * The notice clock for a claim, and where its period comes from. A period is
 * the contract's only once a person has verified it against the quoted clause;
 * until then the card says so in words, not only in colour.
 */
const NoticeClockCard: React.FC<Props> = ({ claimId, claimType, projectId, assessment, requirement, readOnly }) => {
  const [open, setOpen] = useState(false);
  const summary = describeNotice(assessment);
  const requirementId = requirement?._id || requirement?.id;

  return (
    <section
      aria-labelledby="notice-clock-heading"
      className={`rounded-lg border-l-4 border bg-card p-4 ${TONE_BORDER[summary?.tone ?? "neutral"]}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="notice-clock-heading" className="flex items-center gap-2 text-sm font-semibold">
            {summary?.tone === "danger" || summary?.tone === "warn" ? (
              <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            ) : (
              <Clock className="h-4 w-4" aria-hidden="true" />
            )}
            Notice clock
            {summary && <Badge variant={NOTICE_BADGE_VARIANT[summary.tone]}>{summary.label}</Badge>}
          </h2>
          {summary ? (
            <p className="text-sm">{summary.sentence}</p>
          ) : (
            <p className="text-sm text-muted-foreground">
              No event or awareness date is recorded, so the notice period cannot be counted.
            </p>
          )}
          {assessment && (
            <p className="text-xs text-muted-foreground">
              Counted from {formatDateIN(assessment.basis_date)}; period ends {formatDateIN(assessment.due_date)}.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            {requirement ? (
              <>
                Period source: clause {requirement.clause_no}
                {requirement.document_type ? ` (${requirement.document_type})` : ""},{" "}
                {requirement.verification === "verified" ? (
                  <span className="font-medium text-green-800">
                    <CheckCircle2 className="mr-0.5 inline h-3 w-3" aria-hidden="true" />
                    verified {formatDateIN(requirement.verified_at)}
                  </span>
                ) : (
                  <span className="font-medium text-amber-800">not yet verified by a person</span>
                )}
              </>
            ) : (
              "Period source: no notice period has been recorded from this project's contract."
            )}
          </p>
        </div>
        {projectId && (
          <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
            <FileSearch className="mr-2 h-4 w-4" aria-hidden="true" />
            {requirement ? "Review notice period" : "Set from contract"}
          </Button>
        )}
      </div>
      {projectId && (
        <NoticePeriodDialog
          open={open}
          onOpenChange={setOpen}
          claimId={claimId}
          claimType={claimType}
          projectId={projectId}
          requirement={requirement ?? null}
          requirementId={requirementId}
          readOnly={readOnly}
        />
      )}
    </section>
  );
};

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  claimId: string;
  claimType: string;
  projectId: string;
  requirement: NoticeRequirement | null;
  requirementId?: string;
  readOnly: boolean;
}

const EMPTY = { period_days: "", clause_no: "", quote: "", trigger: "awareness" as "awareness" | "event" };

const NoticePeriodDialog: React.FC<DialogProps> = ({
  open,
  onOpenChange,
  claimId,
  claimType,
  projectId,
  requirement,
  requirementId,
  readOnly,
}) => {
  const client = useQueryClient();
  const [manual, setManual] = useState(EMPTY);
  const candidates = useQuery({
    queryKey: ["claim-bench", "notice-candidates", projectId, claimType, claimId],
    queryFn: () => fetchNoticeCandidates(claimType, claimId),
    enabled: open,
    retry: false,
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: claimBenchKeys.bench(claimId) });
    void client.invalidateQueries({ queryKey: ["claim-bench", "notice-candidates", projectId, claimType, claimId] });
  };
  const save = useMutation({
    mutationFn: (input: NoticeRequirementInput) => setNoticeRequirement(input),
    onSuccess: () => {
      toast.success("Recorded. It now needs to be verified against the clause.");
      setManual(EMPTY);
      refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "The notice period could not be recorded")),
  });
  const verify = useMutation({
    mutationFn: () => verifyNoticeRequirement(requirementId as string),
    onSuccess: () => {
      toast.success("Verified");
      refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "The notice period could not be verified")),
  });

  const choose = (candidate: NoticePeriodCandidate) =>
    save.mutate({
      project_id: projectId,
      claim_type: claimType,
      period_days: candidate.period_days,
      trigger: candidate.trigger,
      clause_no: candidate.clause_no,
      clause_title: candidate.clause_title,
      quote: candidate.quote,
      document_id: candidate.document_id,
      document_type: candidate.document_type,
      clause_uid: candidate.clause_uid,
      origin: "clause_index",
    });
  const days = Number(manual.period_days);
  const manualReady =
    Number.isInteger(days) && days >= 1 && days <= 365 && manual.clause_no.trim() !== "" && manual.quote.trim().length >= 10;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Notice period for this type of claim</DialogTitle>
          <DialogDescription>
            The period is taken from this project's own contract, with the wording it is read from. It applies to
            every claim of this type on the project.
          </DialogDescription>
        </DialogHeader>

        <section aria-labelledby="notice-current-heading" className="space-y-2 rounded-md border p-3">
          <h3 id="notice-current-heading" className="text-sm font-semibold">In force</h3>
          {requirement ? (
            <>
              <p className="text-sm">
                <span className="font-medium">{requirement.period_days} days</span> from{" "}
                {requirement.trigger === "awareness" ? "becoming aware of the event" : "the event"}, clause{" "}
                {requirement.clause_no}
                {requirement.document_type ? ` (${requirement.document_type})` : ""}.
              </p>
              <blockquote className="border-l-2 pl-3 text-sm italic text-muted-foreground">{requirement.quote}</blockquote>
              {requirement.verification === "verified" ? (
                <p className="text-sm font-medium text-green-800">
                  Verified against the clause on {formatDateIN(requirement.verified_at)}.
                </p>
              ) : (
                <div className="flex flex-wrap items-center gap-3">
                  <p className="text-sm text-amber-800">
                    Not yet verified. Until it is, claims show this period as unverified.
                  </p>
                  <Button
                    size="sm"
                    disabled={readOnly || verify.isPending || !requirementId}
                    title="Confirm that the quoted wording is the contract's and states this period. Needs claim-manage rights."
                    onClick={() => verify.mutate()}
                  >
                    I have checked it against the contract
                  </Button>
                </div>
              )}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              None recorded. Claims of this type are counted against an assumed or organisation-set period, and say so.
            </p>
          )}
        </section>

        <section aria-labelledby="notice-candidates-heading" className="space-y-2">
          <h3 id="notice-candidates-heading" className="text-sm font-semibold">Found in the contract's clause index</h3>
          {candidates.isLoading && <p className="text-sm text-muted-foreground">Reading the clause index…</p>}
          {candidates.isError && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(candidates.error, "The clause index could not be read. You may not have access to contract clauses.")}
            </p>
          )}
          {candidates.data && !candidates.data.clause_index_available && (
            <p className="text-sm text-muted-foreground">
              This project's contract is not clause-indexed, so nothing can be proposed. Enter the period below from
              the contract itself.
            </p>
          )}
          {candidates.data?.clause_index_available && candidates.data.candidates.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No sentence stating a notice period for a claim was found. Enter it below from the contract itself.
            </p>
          )}
          <ul className="space-y-2">
            {candidates.data?.candidates.map((candidate) => (
              <li
                key={`${candidate.clause_uid ?? candidate.clause_no}-${candidate.period_days}-${candidate.quote.length}`}
                className="space-y-1 rounded-md border p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium">
                    {candidate.period_days} days from{" "}
                    {candidate.trigger === "awareness" ? "becoming aware" : "the event"} · clause {candidate.clause_no}
                    {candidate.document_type ? ` · ${candidate.document_type}` : ""}
                  </p>
                  <div className="flex items-center gap-2">
                    {candidate.modifies_general_conditions && <Badge variant="warning">Modifies the general conditions</Badge>}
                    {candidate.cited_by_claim && <Badge variant="primary">Cited by this claim</Badge>}
                    <Button size="sm" variant="outline" disabled={readOnly || save.isPending} onClick={() => choose(candidate)}>
                      Use this
                    </Button>
                  </div>
                </div>
                <blockquote className="border-l-2 pl-3 text-sm italic text-muted-foreground">{candidate.quote}</blockquote>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="notice-manual-heading" className="space-y-2 rounded-md border p-3">
          <h3 id="notice-manual-heading" className="text-sm font-semibold">Enter it from the contract</h3>
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <Label htmlFor="notice-days">Period (days)</Label>
              <Input id="notice-days" inputMode="numeric" value={manual.period_days} onChange={(e) => setManual({ ...manual, period_days: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="notice-clause">Clause</Label>
              <Input id="notice-clause" value={manual.clause_no} placeholder="e.g. 20.1" onChange={(e) => setManual({ ...manual, clause_no: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="notice-trigger">Runs from</Label>
              <select
                id="notice-trigger"
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={manual.trigger}
                onChange={(e) => setManual({ ...manual, trigger: e.target.value as "awareness" | "event" })}
              >
                <option value="awareness">Becoming aware of the event</option>
                <option value="event">The event itself</option>
              </select>
            </div>
          </div>
          <div>
            <Label htmlFor="notice-quote">Contract wording that states the period</Label>
            <Textarea id="notice-quote" rows={3} value={manual.quote} onChange={(e) => setManual({ ...manual, quote: e.target.value })} />
            <p className="pt-1 text-xs text-muted-foreground">
              Copy the sentence as it appears in the contract. It must state the number of days entered above.
            </p>
          </div>
          <Button
            size="sm"
            disabled={readOnly || !manualReady || save.isPending}
            onClick={() =>
              save.mutate({
                project_id: projectId,
                claim_type: claimType,
                period_days: days,
                trigger: manual.trigger,
                clause_no: manual.clause_no.trim(),
                quote: manual.quote.trim(),
                origin: "entered",
              })
            }
          >
            Record this period
          </Button>
        </section>
      </DialogContent>
    </Dialog>
  );
};

export default NoticeClockCard;
