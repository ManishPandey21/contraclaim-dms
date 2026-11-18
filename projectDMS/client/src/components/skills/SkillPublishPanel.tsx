import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CircleDashed } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { errorMessage } from "@/hooks/useLetterBench";
import { formatDateIN } from "@/lib/format-in";
import {
  REQUIREMENT_STATE_TEXT,
  publishBlockers,
  requirementStatuses,
  reviewerRoleLabel,
  runReport,
} from "@/lib/skill-approvals";
import { enhancedApi } from "@/services/enhanced-api";
import {
  fetchSkillApprovals,
  fetchSkillEvalRuns,
  fetchSkillVersionText,
  publishSkillVersion,
  recordSkillApproval,
  revokeSkillApproval,
  runSkillEvaluation,
} from "@/services/letter-bench-api";
import { getCurrentUserProfile } from "@/services/session-api";
import type { SkillEvalRun, SkillVersionSummary } from "@/types/letterBench";

interface Props {
  version: Pick<SkillVersionSummary, "skill_key" | "version" | "name" | "content_hash" | "required_approvals">;
  onClose: () => void;
  onPublished: () => void;
  /**
   * The reviewer's view: read the text, record or withdraw your own review.
   * Nothing a publisher does is offered. `myRoles` are the reviewer roles the
   * signed-in person holds at platform scope; only those reviews can be given.
   */
  reviewOnly?: boolean;
  myRoles?: string[];
}

const percent = (value?: number | null) => (value == null ? "—" : `${Math.round(value * 100)} %`);

const personName = (u: { first_name?: string; last_name?: string; username?: string; email?: string }) =>
  [u.first_name, u.last_name].filter(Boolean).join(" ").trim() || u.username || u.email || "Unnamed user";

/**
 * Review and publish one draft skill version.
 *
 * A version that names required approvals is published by three different
 * people: whoever ran the evaluation, the reviewers, and the publisher. The
 * server enforces all of it; this panel shows where things stand and why
 * Publish is or is not available.
 */
const SkillPublishPanel: React.FC<Props> = ({ version, onClose, onPublished, reviewOnly = false, myRoles }) => {
  const client = useQueryClient();
  const approvalsKey = ["skills", "approvals", version.skill_key, version.version] as const;
  const needsApprovals = (version.required_approvals?.length ?? 0) > 0;
  const runsKey = ["skills", "eval-runs", version.skill_key, version.version] as const;
  const [chosenRunId, setChosenRunId] = useState<string | null>(null);

  const runs = useQuery({
    queryKey: runsKey,
    queryFn: () => fetchSkillEvalRuns(version.skill_key, version.version),
    retry: false,
  });
  const approvals = useQuery({
    queryKey: approvalsKey,
    queryFn: () => fetchSkillApprovals(version.skill_key, version.version),
    enabled: needsApprovals,
  });
  const me = useQuery({ queryKey: ["session", "profile"], queryFn: () => getCurrentUserProfile(), staleTime: 60_000 });
  const people = useQuery({
    queryKey: ["users", "names"],
    queryFn: () => enhancedApi.getUsers(),
    staleTime: 60_000,
    retry: false,
  });
  const text = useQuery({
    queryKey: ["skills", "text", version.skill_key, version.version, version.content_hash],
    queryFn: () => fetchSkillVersionText(version.skill_key, version.version),
    enabled: reviewOnly,
    retry: false,
  });
  const mayGive = (role: string) => !reviewOnly || (myRoles ?? []).includes(role);
  const names = useMemo(() => {
    const map = new Map<string, string>();
    for (const user of people.data ?? []) map.set(String(user.id || user._id || ""), personName(user));
    return map;
  }, [people.data]);
  const nameOf = (id?: string | null) => (id ? names.get(id) || id : "");
  const myId = me.data?.id ? String(me.data.id) : null;

  // The run under review: the one chosen, else the newest. Runs are made on the server (4B-41).
  const run: SkillEvalRun | null =
    runs.data?.find((item) => item._id === chosenRunId) ?? runs.data?.[0] ?? null;
  const report = run ? runReport(run) : null;
  // A run counts only on a golden set that can support an approval (4B-42); the server refuses the rest.
  const passing = Boolean(run?.passed && run?.suite_ready);

  const statuses = requirementStatuses(
    approvals.data?.required ?? version.required_approvals ?? [],
    approvals.data?.approvals ?? [],
    approvals.data?.content_hash ?? version.content_hash,
    report,
    approvals.data?.policy_hash ?? null
  );
  const blockers = needsApprovals && !reviewOnly ? publishBlockers(statuses, report, myId) : [];
  if (run && !run.passed) blockers.unshift("The chosen evaluation run did not pass.");
  else if (run && !run.suite_ready) {
    blockers.unshift("The chosen run used a golden set that cannot support an approval (synthetic or incomplete).");
  }

  const refreshApprovals = () => client.invalidateQueries({ queryKey: approvalsKey });
  const approve = useMutation({
    mutationFn: (requirement: { kind: "technical" | "legal"; reviewer_role: string }) =>
      recordSkillApproval(version.skill_key, version.version, { ...requirement, eval_run_id: run!._id }),
    onSuccess: () => {
      toast.success("Approval recorded");
      void refreshApprovals();
    },
    onError: (failure) => toast.error(errorMessage(failure, "The approval could not be recorded")),
  });
  const revoke = useMutation({
    mutationFn: (input: { id: string; reason: string }) => revokeSkillApproval(input.id, input.reason),
    onSuccess: () => {
      toast.success("Approval revoked");
      void refreshApprovals();
    },
    onError: (failure) => toast.error(errorMessage(failure, "The approval could not be revoked")),
  });
  const evaluate = useMutation({
    mutationFn: () => runSkillEvaluation(version.skill_key, version.version),
    onSuccess: (made) => {
      toast.success(made.passed ? "Evaluation passed" : "Evaluation run: it did not pass");
      setChosenRunId(made._id);
      void client.invalidateQueries({ queryKey: runsKey });
    },
    onError: (failure) => toast.error(errorMessage(failure, "The evaluation could not be run")),
  });
  const publish = useMutation({
    mutationFn: () => publishSkillVersion(version.skill_key, version.version, run!._id),
    onSuccess: () => {
      toast.success("Published");
      onPublished();
    },
    onError: (failure) => toast.error(errorMessage(failure, "The version could not be published")),
  });

  return (
    <section aria-labelledby="publish-heading" className="space-y-4 rounded-lg border p-4">
      <div>
        <h2 id="publish-heading" className="text-sm font-semibold">
          {reviewOnly ? "Review" : needsApprovals ? "Review and publish" : "Publish"} {version.name} {version.version}
        </h2>
        <p className="text-sm text-muted-foreground">
          {reviewOnly
            ? "Read the text below and the evaluation run, then record your review against that run. Your review covers this exact text and that run only. Publishing is done by someone else."
            : needsApprovals
            ? "This skill shapes contractual and legal output. It is published only when each review below has been given against this exact text and evaluation result, by people other than the one who publishes it."
            : "Run the golden-set evaluation on the server. A version whose run did not pass cannot be published."}
        </p>
      </div>

      {reviewOnly && (
        <div className="space-y-1">
          <h3 className="text-xs font-semibold uppercase text-muted-foreground">Text under review</h3>
          {text.isLoading && <p className="text-sm text-muted-foreground">Loading the text…</p>}
          {text.isError && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(text.error, "The text could not be loaded.")}
            </p>
          )}
          {text.data && (
            <pre
              tabIndex={0}
              aria-label={`Text of ${version.name} ${version.version}`}
              className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/30 p-3 text-xs"
            >
              {text.data.skill_md}
            </pre>
          )}
        </div>
      )}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase text-muted-foreground">Evaluation runs</h3>
          {!reviewOnly && (
            <Button size="sm" variant="outline" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
              {evaluate.isPending ? "Running…" : "Run evaluation"}
            </Button>
          )}
        </div>
        {runs.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(runs.error, "Evaluation runs could not be loaded.")}
          </p>
        )}
        {runs.data?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No evaluation has been run on this version. The run is made on the server against the golden set; whoever
            runs it cannot approve or publish this version.
          </p>
        )}
        {(runs.data?.length ?? 0) > 0 && (
          <ul className="divide-y rounded-md border" aria-label="Evaluation runs">
            {runs.data!.map((item) => (
              <li key={item._id} className="space-y-1 p-3 text-sm">
                <label className="flex items-start gap-2">
                  <input
                    type="radio"
                    name={`eval-run-${version.skill_key}-${version.version}`}
                    className="mt-1"
                    checked={run?._id === item._id}
                    onChange={() => setChosenRunId(item._id)}
                    aria-label={`Run of ${formatDateIN(item.started_at)} by ${nameOf(item.recorded_by)}`}
                  />
                  <span>
                    <span className={item.passed ? "font-medium text-green-700" : "font-medium text-amber-700"}>
                      {item.passed ? "Passed" : "Did not pass"}
                    </span>{" "}
                    · {formatDateIN(item.started_at)} by {nameOf(item.recorded_by)} · {item.case_count} matters ·
                    elements {percent(item.scores.element_agreement)} (needs {percent(item.threshold)}) · classification{" "}
                    {percent(item.scores.classification_agreement)} · degraded {percent(item.scores.degraded_rate)}
                    <span className="block text-xs text-muted-foreground">
                      Golden set {item.suite_key} ({item.suite_hash.slice(0, 12)}), model {item.model}, text{" "}
                      {item.content_hash.slice(0, 12)}, {item.cost.model_calls} model calls
                    </span>
                  </span>
                </label>
                {!item.suite_ready && (
                  <p className="pl-6 text-xs text-amber-800">
                    Golden set cannot support an approval: {(item.suite_problems ?? []).join("; ") || "not checked"}
                  </p>
                )}
                {item.failures.length > 0 && (
                  <ul className="list-disc pl-10 text-xs text-amber-800">
                    {item.failures.slice(0, 8).map((failure) => (
                      <li key={failure}>{failure}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {needsApprovals && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase text-muted-foreground">Required reviews</h3>
          {approvals.isError && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(approvals.error, "Approvals could not be loaded.")}
            </p>
          )}
          <ul className="divide-y rounded-md border">
            {statuses.map((status) => {
              const { requirement, approval } = status;
              const Icon = status.state === "ok" ? CheckCircle2 : status.state === "missing" ? CircleDashed : CircleAlert;
              const tone =
                status.state === "ok" ? "text-green-700" : status.state === "missing" ? "text-muted-foreground" : "text-amber-700";
              return (
                <li key={`${requirement.kind}-${requirement.reviewer_role}`} className="flex flex-wrap items-start justify-between gap-3 p-3 text-sm">
                  <div className="flex items-start gap-2">
                    <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${tone}`} aria-hidden="true" />
                    <div>
                      <p className="font-medium">
                        {requirement.kind === "legal" ? "Legal approval" : "Technical review"} ·{" "}
                        {reviewerRoleLabel(requirement.reviewer_role)}
                      </p>
                      <p className={tone}>{REQUIREMENT_STATE_TEXT[status.state]}</p>
                      {approval && (
                        <p className="text-xs text-muted-foreground">
                          {nameOf(approval.reviewer_user_id)} on {formatDateIN(approval.decided_at)}, against{" "}
                          {approval.eval_suite}
                          {approval.eval_score != null && approval.eval_threshold != null
                            ? ` (${approval.eval_score.toFixed(2)} of ${approval.eval_threshold.toFixed(2)} needed, ${approval.eval_case_count} cases)`
                            : ""}
                          , text {approval.content_hash.slice(0, 12)}
                          {approval.revoked_at
                            ? `. Revoked ${formatDateIN(approval.revoked_at)} by ${nameOf(approval.revoked_by)}: ${approval.revoke_reason || "no reason given"}`
                            : ""}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    {status.state !== "ok" && !mayGive(requirement.reviewer_role) && (
                      <span className="text-xs text-muted-foreground">For another reviewer</span>
                    )}
                    {status.state !== "ok" && mayGive(requirement.reviewer_role) && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={!passing || approve.isPending}
                        title={
                          !passing
                            ? "Choose a passing evaluation run first"
                            : `Recorded in your name. You must hold the ${reviewerRoleLabel(requirement.reviewer_role)} role.`
                        }
                        onClick={() => approve.mutate(requirement)}
                      >
                        Record my approval
                      </Button>
                    )}
                    {status.state === "ok" &&
                      approval &&
                      (!reviewOnly || approval.reviewer_user_id === myId || mayGive(requirement.reviewer_role)) && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={revoke.isPending}
                        onClick={() => {
                          const reason = window.prompt("Why is this approval being withdrawn?");
                          const id = approval._id || approval.id;
                          if (reason && reason.trim() && id) revoke.mutate({ id, reason: reason.trim() });
                        }}
                      >
                        Revoke
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {blockers.length > 0 && (
        <div role="status" className="rounded-md border border-amber-600 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-medium">Not ready to publish</p>
          <ul className="list-disc pl-5">
            {blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex gap-2">
        {!reviewOnly && (
          <Button disabled={!report || blockers.length > 0 || publish.isPending} onClick={() => publish.mutate()}>
            Publish
          </Button>
        )}
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </section>
  );
};

export default SkillPublishPanel;
