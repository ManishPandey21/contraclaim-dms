import { describe, expect, it } from "vitest";
import {
  publishBlockers,
  requirementStatuses,
  reviewerRoleLabel,
  sameEvaluation,
} from "../skill-approvals";
import type { SkillApproval, SkillApprovalRequirement, SkillEvalReport } from "@/types/letterBench";

const HASH = "a".repeat(64);
const REQUIRED: SkillApprovalRequirement[] = [
  { kind: "technical", reviewer_role: "chief_contract_expert" },
  { kind: "legal", reviewer_role: "senior_legal_approver" },
];
const REPORT: SkillEvalReport = {
  suite: "claims-golden-v1",
  score: 0.92,
  threshold: 0.85,
  case_count: 12,
  recorded_by: "evaluator",
};

const approval = (over: Partial<SkillApproval>): SkillApproval => ({
  skill_key: "indian-infra-claims",
  version: "1.0.0",
  content_hash: HASH,
  eval_suite: "claims-golden-v1",
  eval_report_hash: "b".repeat(64),
  eval_score: 0.92,
  eval_threshold: 0.85,
  eval_case_count: 12,
  kind: "legal",
  reviewer_role: "senior_legal_approver",
  reviewer_user_id: "counsel",
  decided_at: "2026-11-15T10:00:00Z",
  ...over,
});
const technical = approval({ kind: "technical", reviewer_role: "chief_contract_expert", reviewer_user_id: "expert" });

const states = (approvals: SkillApproval[], report: SkillEvalReport | null = REPORT, hash = HASH) =>
  requirementStatuses(REQUIRED, approvals, hash, report).map((s) => s.state);

describe("requirementStatuses", () => {
  it("is missing until given, then ok", () => {
    expect(states([])).toEqual(["missing", "missing"]);
    expect(states([technical, approval({})])).toEqual(["ok", "ok"]);
  });

  it("does not count an approval given for other text", () => {
    expect(states([technical, approval({})], REPORT, "c".repeat(64))).toEqual(["stale_content", "stale_content"]);
  });

  it("does not count an approval given for another evaluation result", () => {
    expect(states([technical, approval({})], { ...REPORT, score: 0.99 })).toEqual([
      "stale_evaluation",
      "stale_evaluation",
    ]);
    expect(sameEvaluation(approval({}), { ...REPORT, case_count: 13 })).toBe(false);
  });

  it("reports a revoked approval as revoked, and prefers a live one", () => {
    const revoked = approval({ revoked_at: "2026-11-15T12:00:00Z" });
    expect(states([technical, revoked])).toEqual(["ok", "revoked"]);
    expect(states([technical, revoked, approval({ reviewer_user_id: "counsel-2", decided_at: "2026-11-15T13:00:00Z" })])).toEqual([
      "ok",
      "ok",
    ]);
  });

  it("does not let one role's approval satisfy another's", () => {
    expect(states([approval({ kind: "legal", reviewer_role: "chief_contract_expert" })])).toEqual(["missing", "missing"]);
  });
});

describe("publishBlockers", () => {
  const ready = requirementStatuses(REQUIRED, [technical, approval({})], HASH, REPORT);

  it("is empty when everything is in place and the publisher is a third person", () => {
    expect(publishBlockers(ready, REPORT, "publisher")).toEqual([]);
  });

  it("names each missing review", () => {
    const blockers = publishBlockers(requirementStatuses(REQUIRED, [technical], HASH, REPORT), REPORT, "publisher");
    expect(blockers).toEqual(["Legal approval by Senior Legal Approver: not given."]);
  });

  it("stops the evaluator and the legal signatory from publishing", () => {
    expect(publishBlockers(ready, REPORT, "evaluator")).toContain("You ran the evaluation, so someone else must publish.");
    expect(publishBlockers(ready, REPORT, "counsel")).toContain("You gave the legal approval, so someone else must publish.");
    expect(publishBlockers(ready, REPORT, "expert")).toEqual([]);
  });

  it("asks for the evaluator's name and for the evaluation itself", () => {
    expect(publishBlockers(ready, { ...REPORT, recorded_by: null }, "publisher")).toContain(
      "Name the person who ran the evaluation.",
    );
    expect(publishBlockers(ready, null, "publisher")).toEqual(["Enter the evaluation result the reviewers approved."]);
  });

  it("has nothing to say for a pack that needs no approvals", () => {
    expect(publishBlockers([], null, "publisher")).toEqual([]);
  });
});

describe("reviewerRoleLabel", () => {
  it("names known roles and tidies unknown ones", () => {
    expect(reviewerRoleLabel("senior_legal_approver")).toBe("Senior Legal Approver");
    expect(reviewerRoleLabel("quantum_reviewer")).toBe("Quantum Reviewer");
  });
});
