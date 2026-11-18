import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 4B-09: a pure reviewer opens /skills. They hold a platform reviewer role and no
 * publishing permission, so the admin list is refused. They see their queue, read
 * the text, record their own review, and are offered nothing a publisher does.
 */
const api = vi.hoisted(() => ({
  fetchSkillAdminList: vi.fn(),
  fetchSkillReviewQueue: vi.fn(),
  fetchSkillVersionText: vi.fn(),
  fetchSkillApprovals: vi.fn(),
  recordSkillApproval: vi.fn(),
  revokeSkillApproval: vi.fn(),
  publishSkillVersion: vi.fn(),
  fetchSkillEvalRuns: vi.fn(),
  runSkillEvaluation: vi.fn(),
  importSkillBundle: vi.fn(),
  deprecateSkillVersion: vi.fn(),
}));
vi.mock("@/services/letter-bench-api", () => api);
vi.mock("@/services/enhanced-api", () => ({ enhancedApi: { getUsers: vi.fn().mockRejectedValue(new Error("403")) } }));
vi.mock("@/services/session-api", () => ({ getCurrentUserProfile: vi.fn().mockResolvedValue({ id: "counsel-1" }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import SkillAdminPage from "../SkillAdminPage";

const forbidden = Object.assign(new Error("Request failed with status code 403"), {
  isAxiosError: true,
  response: { status: 403, data: { detail: "Not authorized: missing_permission" } },
});

const queueItem = {
  skill_key: "indian-infra-claims",
  version: "1.1.0",
  name: "Claims method",
  kind: "evaluation",
  content_hash: "abc123def4567890",
  reviews: [
    { kind: "technical", reviewer_role: "chief_contract_expert", mine: false, given: false, given_by_me: false },
    { kind: "legal", reviewer_role: "senior_legal_approver", mine: true, given: false, given_by_me: false },
  ],
  waiting_on_me: true,
};

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <SkillAdminPage />
      </MemoryRouter>
    </QueryClientProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchSkillAdminList.mockRejectedValue(forbidden);
  api.fetchSkillReviewQueue.mockResolvedValue({ my_roles: ["senior_legal_approver"], items: [queueItem] });
  api.fetchSkillVersionText.mockResolvedValue({ skill_md: "# Method\n\nAssess each head against the notice clause." });
  api.fetchSkillApprovals.mockResolvedValue({
    skill_key: queueItem.skill_key,
    version: queueItem.version,
    content_hash: queueItem.content_hash,
    policy_hash: "p1",
    required: queueItem.reviews.map(({ kind, reviewer_role }) => ({ kind, reviewer_role })),
    approvals: [],
  });
  api.recordSkillApproval.mockResolvedValue({});
  api.fetchSkillEvalRuns.mockResolvedValue([
    {
      _id: "run-7", skill_key: "indian-infra-claims", version: "1.1.0", content_hash: "c".repeat(64),
      policy_hash: "p".repeat(64), suite_key: "claims-golden", suite_hash: "s".repeat(64), model: "gpt-4o",
      prompt_key: "claims.v1.head_test", prompt_version: 1, recorded_by: "evaluator-7", threshold: 0.85,
      case_count: 15, passed: true, failures: [],
      scores: { element_agreement: 0.92, element_agreement_by_type: {}, classification_agreement: 0.9, degraded_rate: 0, schema_validity: 1 },
      matters: [], cost: { model_calls: 15, prompt_characters: 0, output_characters: 0 }, started_at: "2026-11-18T00:00:00Z",
    },
  ]);
});

describe("SkillAdminPage for a reviewer", () => {
  it("shows the queue and none of the publisher's sections, with no error for the refused admin list", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "Waiting for your review" })).toBeInTheDocument();
    expect(screen.getByText("You hold: Senior Legal Approver.")).toBeInTheDocument();
    expect(screen.getByText(/Claims method/)).toBeInTheDocument();
    expect(screen.getByText(/Senior Legal Approver: not yet given/)).toBeInTheDocument();
    await waitFor(() => expect(api.fetchSkillAdminList).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Versions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Import a skill bundle" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Import as draft/ })).not.toBeInTheDocument();
  });

  it("lets the reviewer read the text and record only their own review; Publish is not offered", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Review…" }));

    expect(await screen.findByText(/Assess each head against the notice clause/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
    expect(screen.queryByText("Not ready to publish")).not.toBeInTheDocument();
    // One review is theirs to give; the other is marked as someone else's.
    expect(await screen.findAllByRole("button", { name: "Record my approval" })).toHaveLength(1);
    expect(screen.getByText("For another reviewer")).toBeInTheDocument();

    // The run is the server's; a reviewer cannot start one or type a result in (4B-41).
    expect(await screen.findByText(/Passed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run evaluation" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Score (0 to 1)")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Record my approval" }));

    await waitFor(() => expect(api.recordSkillApproval).toHaveBeenCalledTimes(1));
    const [key, version, body] = api.recordSkillApproval.mock.calls[0];
    expect([key, version]).toEqual(["indian-infra-claims", "1.1.0"]);
    expect(body).toMatchObject({ kind: "legal", reviewer_role: "senior_legal_approver" });
    expect(body).toEqual({ kind: "legal", reviewer_role: "senior_legal_approver", eval_run_id: "run-7" });
    expect(api.publishSkillVersion).not.toHaveBeenCalled();
  });

  it("says so when no draft is waiting", async () => {
    api.fetchSkillReviewQueue.mockResolvedValue({ my_roles: ["senior_legal_approver"], items: [] });
    renderPage();
    expect(await screen.findByText("No draft is asking for your review.")).toBeInTheDocument();
  });

  it("shows someone who is neither reviewer nor publisher the refusal, and no queue", async () => {
    api.fetchSkillReviewQueue.mockResolvedValue({ my_roles: [], items: [] });
    renderPage();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Waiting for your review" })).not.toBeInTheDocument();
  });
});
