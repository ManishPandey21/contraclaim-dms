import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Gate 3 bullet 8: the skills page (/skills, /skill-reviews, /letters/skills all
 * render SkillAdminPage) when its API fails. A publisher whose list fails sees a
 * plain message and no table; a failing review queue does not take the page down.
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
vi.mock("@/services/enhanced-api", () => ({ enhancedApi: { getUsers: vi.fn().mockResolvedValue([]) } }));
vi.mock("@/services/session-api", () => ({ getCurrentUserProfile: vi.fn().mockResolvedValue({ id: "publisher-1" }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import SkillAdminPage from "../SkillAdminPage";

const failure = (status: number, detail: unknown) =>
  Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data: { detail } },
  });

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
  api.fetchSkillReviewQueue.mockResolvedValue({ my_roles: [], items: [] });
  api.fetchSkillAdminList.mockResolvedValue({ skills: [], versions: [] });
});

describe("SkillAdminPage error states", () => {
  it("shows a message and no versions table when the list fails", async () => {
    api.fetchSkillAdminList.mockRejectedValue(failure(500, "Internal Server Error"));
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent?.trim()).not.toBe("");
    expect(screen.queryByRole("heading", { name: "Versions" })).not.toBeInTheDocument();
  });

  it("shows a refusal as a message to a user who is neither publisher nor reviewer", async () => {
    api.fetchSkillAdminList.mockRejectedValue(failure(403, "Not authorized: missing_permission"));
    renderPage();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("shows the message when both the list and the review queue fail", async () => {
    api.fetchSkillAdminList.mockRejectedValue(failure(503, "Service unavailable"));
    api.fetchSkillReviewQueue.mockRejectedValue(failure(503, "Service unavailable"));
    renderPage();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Waiting for your review" })).not.toBeInTheDocument();
  });

  it("keeps the publisher's list when only the review queue fails", async () => {
    api.fetchSkillReviewQueue.mockRejectedValue(failure(500, "Internal Server Error"));
    renderPage();
    expect(await screen.findByRole("heading", { name: "Versions" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
