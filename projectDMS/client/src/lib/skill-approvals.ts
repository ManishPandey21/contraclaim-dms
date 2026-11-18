import type {
  SkillApproval,
  SkillApprovalRequirement,
  SkillEvalReport,
  SkillEvalRun,
} from "@/types/letterBench";

/**
 * The evaluation result an approval binds to, derived from a run exactly as the
 * server derives it (`SkillEvalRun.report`): the suite names the run, so an
 * approval given against one run does not count for another.
 */
export function runReport(run: SkillEvalRun): SkillEvalReport {
  return {
    suite: `${run.suite_key}@${run.suite_hash.slice(0, 16)}#run:${run._id}`,
    score: run.passed ? run.scores.element_agreement : 0,
    threshold: run.threshold,
    case_count: run.case_count,
    recorded_by: run.recorded_by,
  };
}

// Mirrors the server's publication gate so the screen can say, before anyone
// presses Publish, which review is missing and why. The server decides; this
// only explains.

export type RequirementState =
  | "ok"
  | "missing"
  | "revoked"
  | "stale_content"
  | "stale_policy"
  | "stale_evaluation";

/** The server refuses an evaluation that sets its own bar lower than this (SKILL_EVAL_MIN_THRESHOLD). */
export const MIN_EVAL_THRESHOLD = 0.85;

export interface RequirementStatus {
  requirement: SkillApprovalRequirement;
  state: RequirementState;
  approval: SkillApproval | null;
}

const ROLE_LABELS: Record<string, string> = {
  senior_legal_approver: "Senior Legal Approver",
  chief_contract_expert: "Chief Contract Expert",
  delay_planning_expert: "Delay / Planning Expert",
  arbitration_case_preparation_expert: "Arbitration Case Preparation Expert",
};

export function reviewerRoleLabel(role: string): string {
  return (
    ROLE_LABELS[role] ||
    role.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
  );
}

const close = (a?: number | null, b?: number | null) =>
  a != null && b != null && Math.abs(a - b) < 1e-6;

/** Was this approval given against the same evaluation result, run by the same person? */
export function sameEvaluation(approval: SkillApproval, report: SkillEvalReport): boolean {
  return (
    approval.eval_suite === report.suite &&
    close(approval.eval_score, report.score) &&
    close(approval.eval_threshold, report.threshold) &&
    approval.eval_case_count === report.case_count &&
    (approval.eval_recorded_by ?? "") === (report.recorded_by ?? "")
  );
}

export function requirementStatuses(
  required: SkillApprovalRequirement[],
  approvals: SkillApproval[],
  contentHash: string,
  report: SkillEvalReport | null,
  policyHash: string | null = null
): RequirementStatus[] {
  // Without the version's policy hash nothing can be shown as given: the server
  // would refuse it, so the screen must not promise it.
  const samePolicy = (item: SkillApproval) => policyHash !== null && item.policy_hash === policyHash;
  return required.map((requirement) => {
    const candidates = approvals
      .filter((item) => item.kind === requirement.kind && item.reviewer_role === requirement.reviewer_role)
      .sort((a, b) => b.decided_at.localeCompare(a.decided_at));
    const exact = candidates.filter(
      (item) =>
        item.content_hash === contentHash && samePolicy(item) && (report ? sameEvaluation(item, report) : true)
    );
    const live = exact.find((item) => !item.revoked_at);
    if (live) return { requirement, state: "ok", approval: live };
    if (exact.length) return { requirement, state: "revoked", approval: exact[0] };
    if (candidates.length) {
      const latest = candidates[0];
      const state: RequirementState =
        latest.content_hash !== contentHash
          ? "stale_content"
          : !samePolicy(latest)
            ? "stale_policy"
            : "stale_evaluation";
      return { requirement, state, approval: latest };
    }
    return { requirement, state: "missing", approval: null };
  });
}

export const REQUIREMENT_STATE_TEXT: Record<RequirementState, string> = {
  ok: "Given",
  missing: "Not given",
  revoked: "Revoked",
  stale_content: "Given for different skill text",
  stale_policy: "Given before the manifest changed",
  stale_evaluation: "Given for a different evaluation result",
};

/** Why Publish is not available, in the order a person would fix it. Empty when it is. */
export function publishBlockers(
  statuses: RequirementStatus[],
  report: SkillEvalReport | null,
  currentUserId: string | null
): string[] {
  if (statuses.length === 0) return [];
  const blockers: string[] = [];
  if (!report) {
    blockers.push("Choose the evaluation run the reviewers approved.");
    return blockers;
  }
  if (!report.recorded_by) blockers.push("Name the person who ran the evaluation.");
  if (report.threshold < MIN_EVAL_THRESHOLD) {
    blockers.push(`The evaluation threshold must be at least ${MIN_EVAL_THRESHOLD.toFixed(2)}.`);
  }
  for (const status of statuses) {
    if (status.state !== "ok") {
      blockers.push(
        `${status.requirement.kind === "legal" ? "Legal" : "Technical"} approval by ${reviewerRoleLabel(
          status.requirement.reviewer_role
        )}: ${REQUIREMENT_STATE_TEXT[status.state].toLowerCase()}.`
      );
    }
  }
  if (currentUserId && report.recorded_by === currentUserId) {
    blockers.push("You ran the evaluation, so someone else must publish.");
  }
  if (
    currentUserId &&
    statuses.some(
      (status) =>
        status.requirement.kind === "legal" &&
        status.state === "ok" &&
        status.approval?.reviewer_user_id === currentUserId
    )
  ) {
    blockers.push("You gave the legal approval, so someone else must publish.");
  }
  return blockers;
}
