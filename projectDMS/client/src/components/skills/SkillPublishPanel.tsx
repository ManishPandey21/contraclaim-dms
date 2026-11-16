import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CircleDashed } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errorMessage } from "@/hooks/useLetterBench";
import { formatDateIN } from "@/lib/format-in";
import {
  REQUIREMENT_STATE_TEXT,
  publishBlockers,
  requirementStatuses,
  reviewerRoleLabel,
} from "@/lib/skill-approvals";
import { enhancedApi } from "@/services/enhanced-api";
import {
  fetchSkillApprovals,
  publishSkillVersion,
  recordSkillApproval,
  revokeSkillApproval,
} from "@/services/letter-bench-api";
import { getCurrentUserProfile } from "@/services/session-api";
import type { SkillEvalReport, SkillVersionSummary } from "@/types/letterBench";

interface Props {
  version: SkillVersionSummary;
  onClose: () => void;
  onPublished: () => void;
}

const FIELDS = [
  ["suite", "Evaluation suite", "text"],
  ["case_count", "Cases run", "number"],
  ["score", "Score (0 to 1)", "number"],
  ["threshold", "Threshold (0 to 1)", "number"],
] as const;

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
const SkillPublishPanel: React.FC<Props> = ({ version, onClose, onPublished }) => {
  const client = useQueryClient();
  const approvalsKey = ["skills", "approvals", version.skill_key, version.version] as const;
  const needsApprovals = (version.required_approvals?.length ?? 0) > 0;
  const [form, setForm] = useState({ suite: "", score: "", threshold: "0.85", case_count: "", recorded_by: "" });

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
  const names = useMemo(() => {
    const map = new Map<string, string>();
    for (const user of people.data ?? []) map.set(String(user.id || user._id || ""), personName(user));
    return map;
  }, [people.data]);
  const nameOf = (id?: string | null) => (id ? names.get(id) || id : "");
  const myId = me.data?.id ? String(me.data.id) : null;

  const complete = form.suite.trim() !== "" && form.score !== "" && form.case_count !== "" && form.threshold !== "";
  const report: SkillEvalReport | null = complete
    ? {
        suite: form.suite.trim(),
        score: Number(form.score),
        threshold: Number(form.threshold),
        case_count: Number(form.case_count),
        recorded_by: form.recorded_by.trim() || null,
      }
    : null;
  const passing = report !== null && report.case_count > 0 && report.score >= report.threshold;

  const statuses = requirementStatuses(
    approvals.data?.required ?? version.required_approvals ?? [],
    approvals.data?.approvals ?? [],
    approvals.data?.content_hash ?? version.content_hash,
    report,
    approvals.data?.policy_hash ?? null
  );
  const blockers = needsApprovals ? publishBlockers(statuses, report, myId) : [];
  if (report && !passing) blockers.unshift("The evaluation is below its threshold, or no cases were run.");

  const refreshApprovals = () => client.invalidateQueries({ queryKey: approvalsKey });
  const approve = useMutation({
    mutationFn: (requirement: { kind: "technical" | "legal"; reviewer_role: string }) =>
      recordSkillApproval(version.skill_key, version.version, { ...requirement, eval_report: report! }),
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
  const publish = useMutation({
    mutationFn: () => publishSkillVersion(version.skill_key, version.version, report!),
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
          {needsApprovals ? "Review and publish" : "Publish"} {version.name} {version.version}
        </h2>
        <p className="text-sm text-muted-foreground">
          {needsApprovals
            ? "This skill shapes contractual and legal output. It is published only when each review below has been given against this exact text and evaluation result, by people other than the one who publishes it."
            : "Record the result of the golden-set evaluation. A version below its threshold cannot be published."}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {FIELDS.map(([key, label, type]) => (
          <div key={key} className="space-y-1">
            <label htmlFor={`eval-${key}`} className="text-xs font-medium">
              {label}
            </label>
            <Input
              id={`eval-${key}`}
              type={type}
              step={type === "number" ? "0.01" : undefined}
              value={form[key]}
              onChange={(event) => setForm((current) => ({ ...current, [key]: event.target.value }))}
            />
          </div>
        ))}
        {needsApprovals && (
          <div className="space-y-1 sm:col-span-2">
            <label htmlFor="eval-recorded-by" className="text-xs font-medium">
              Evaluation run by
            </label>
            {(people.data?.length ?? 0) > 0 ? (
              <select
                id="eval-recorded-by"
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={form.recorded_by}
                onChange={(event) => setForm((current) => ({ ...current, recorded_by: event.target.value }))}
              >
                <option value="">Choose who ran it</option>
                {[...names.entries()]
                  .filter(([id]) => id)
                  .sort((a, b) => a[1].localeCompare(b[1]))
                  .map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
              </select>
            ) : (
              <Input
                id="eval-recorded-by"
                value={form.recorded_by}
                placeholder="User id of the person who ran the evaluation"
                onChange={(event) => setForm((current) => ({ ...current, recorded_by: event.target.value }))}
              />
            )}
            <p className="text-xs text-muted-foreground">
              The evaluator cannot approve or publish the version they evaluated.
            </p>
          </div>
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
                    {status.state !== "ok" && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={!passing || !report?.recorded_by || approve.isPending}
                        title={
                          !passing || !report?.recorded_by
                            ? "Enter a passing evaluation and who ran it first"
                            : `Recorded in your name. You must hold the ${reviewerRoleLabel(requirement.reviewer_role)} role.`
                        }
                        onClick={() => approve.mutate(requirement)}
                      >
                        Record my approval
                      </Button>
                    )}
                    {status.state === "ok" && approval && (
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
        <Button disabled={!report || blockers.length > 0 || publish.isPending} onClick={() => publish.mutate()}>
          Publish
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </section>
  );
};

export default SkillPublishPanel;
