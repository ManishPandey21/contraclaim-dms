import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SkillPublishPanel from "@/components/skills/SkillPublishPanel";
import { runReport } from "@/lib/skill-approvals";
import type { SkillEvalRun } from "@/types/letterBench";

const api = vi.hoisted(() => ({
  fetchSkillApprovals: vi.fn(),
  fetchSkillVersionText: vi.fn(),
  fetchSkillEvalRuns: vi.fn(),
  runSkillEvaluation: vi.fn(),
  recordSkillApproval: vi.fn(),
  revokeSkillApproval: vi.fn(),
  publishSkillVersion: vi.fn(),
}));
vi.mock("@/services/letter-bench-api", () => api);
vi.mock("@/services/enhanced-api", () => ({ enhancedApi: { getUsers: vi.fn().mockResolvedValue([]) } }));
vi.mock("@/services/session-api", () => ({ getCurrentUserProfile: vi.fn().mockResolvedValue({ id: "publisher-1" }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const run = (over: Partial<SkillEvalRun> = {}): SkillEvalRun => ({
  _id: "run-1",
  skill_key: "indian-infra-variations",
  version: "1.2.0",
  content_hash: "c".repeat(64),
  policy_hash: "p".repeat(64),
  suite_key: "variations-golden",
  suite_hash: "0123456789abcdef".repeat(4),
  model: "gpt-4o",
  prompt_key: "k",
  recorded_by: "evaluator-1",
  threshold: 0.85,
  case_count: 12,
  passed: true,
  failures: [],
  scores: { element_agreement: 0.91, element_agreement_by_type: {}, classification_agreement: 0.9, degraded_rate: 0, schema_validity: 1 },
  matters: [],
  cost: { model_calls: 12, prompt_characters: 0, output_characters: 0 },
  started_at: "2026-11-18T00:00:00Z",
  suite_ready: true,
  suite_problems: [],
  ...over,
});

const version = { skill_key: "indian-infra-variations", version: "1.2.0", name: "Variations", content_hash: "c".repeat(64), required_approvals: [] };

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SkillPublishPanel version={version} onClose={vi.fn()} onPublished={vi.fn()} />
    </QueryClientProvider>
  );
}

beforeEach(() => vi.clearAllMocks());

describe("SkillPublishPanel (4B-41)", () => {
  it("derives the bound report from a run exactly as the server does", () => {
    expect(runReport(run())).toEqual({
      suite: "variations-golden@0123456789abcdef#run:run-1",
      score: 0.91,
      threshold: 0.85,
      case_count: 12,
      recorded_by: "evaluator-1",
    });
    expect(runReport(run({ passed: false })).score).toBe(0);
  });

  it("runs the evaluation on the server and publishes by naming the run; no result is typed", async () => {
    api.fetchSkillEvalRuns.mockResolvedValueOnce([]).mockResolvedValue([run()]);
    api.runSkillEvaluation.mockResolvedValue(run());
    api.publishSkillVersion.mockResolvedValue({});
    mount();
    expect(await screen.findByText(/No evaluation has been run on this version/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Score/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Run evaluation" }));
    await waitFor(() => expect(api.runSkillEvaluation).toHaveBeenCalledWith("indian-infra-variations", "1.2.0"));
    expect(await screen.findByText("Passed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(api.publishSkillVersion).toHaveBeenCalledWith("indian-infra-variations", "1.2.0", "run-1"));
  });

  it("will not publish on a passing run whose golden set is synthetic (4B-42)", async () => {
    api.fetchSkillEvalRuns.mockResolvedValue([
      run({ suite_ready: false, suite_problems: ["15 matter(s) are synthetic: a synthetic golden set supports no approval"] }),
    ]);
    mount();
    expect(await screen.findByText("Passed")).toBeInTheDocument();
    expect(screen.getByText(/15 matter\(s\) are synthetic/)).toBeInTheDocument();
    expect(screen.getByText(/cannot support an approval \(synthetic or incomplete\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("will not publish on a run that did not pass, and shows why", async () => {
    api.fetchSkillEvalRuns.mockResolvedValue([
      run({ passed: false, failures: ["hard gate - M03: clauses: cited but not in the matter: 8.4"] }),
    ]);
    mount();
    expect(await screen.findByText("Did not pass")).toBeInTheDocument();
    expect(screen.getByText(/cited but not in the matter: 8.4/)).toBeInTheDocument();
    expect(screen.getByText("The chosen evaluation run did not pass.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });
});
