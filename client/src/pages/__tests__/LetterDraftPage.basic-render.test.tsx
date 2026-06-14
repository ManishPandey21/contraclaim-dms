import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import LetterDraftPage from "../LetterDraftPage";

const apiGetMock = vi.hoisted(() => vi.fn());

vi.mock("@/services/api", () => ({
  api: {
    get: apiGetMock,
  },
}));

vi.mock("@/hooks/useLetterWorkflow", () => ({
  useLetterWorkflow: () => ({
    letters: [
      {
        id: "abc123",
        title: "Test Letter Subject",
        subject: "Test Letter Subject",
        recipient: "Chief Engineer",
        status: "Draft",
        strategicPlan: "Use the approved strategy.",
        strategy_plan: "Use the approved strategy.",
        content: "Draft content",
      },
    ],
    users: [],
    handleLetterUpdate: vi.fn(),
    submitForReview: vi.fn(),
    fetchLetters: vi.fn(),
  }),
}));

vi.mock("@/hooks/useLanggraphDraft", () => ({
  useLanggraphDraft: () => ({
    data: null,
    loading: false,
    error: null,
    runDraft: vi.fn(),
    reset: vi.fn(),
  }),
}));

vi.mock("@/hooks/useLetterDrafting", () => ({
  useLetterDrafting: () => ({
    data: null,
    loading: false,
    error: null,
    run: vi.fn(),
    generateDraft: vi.fn(),
    preparePlan: vi.fn(),
    latestRun: vi.fn(),
    reviseRun: vi.fn(),
    approveRun: vi.fn(),
    exportRun: vi.fn(),
    issueRun: vi.fn(),
    reset: vi.fn(),
  }),
}));

vi.mock("@/hooks/useLetterGraphRuns", () => ({
  useLetterGraphRuns: () => ({
    data: null,
    loading: false,
    error: null,
    fetchRun: vi.fn().mockResolvedValue(null),
    setData: vi.fn(),
  }),
}));

vi.mock("@/config/features", () => ({
  LANGGRAPH_ENABLED: false,
}));

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = String(value);
    },
    clear: () => {
      store = {};
    },
    removeItem: (key: string) => {
      delete store[key];
    },
  };
})();

Object.defineProperty(window, "localStorage", {
  value: localStorageMock,
});

function renderAt(letterId: string) {
  return render(
    <MemoryRouter initialEntries={[`/letters/${letterId}/draft`]}>
      <Routes>
        <Route path="/letters/:id/draft" element={<LetterDraftPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("LetterDraftPage without LangGraph enabled", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    (window.localStorage as any).clear();
  });

  it("fetches letter metadata and displays it", async () => {
    renderAt("abc123");

    await waitFor(() =>
      expect(
        screen.getByText("Test Letter Subject", { exact: false })
      ).toBeInTheDocument()
    );

    expect(screen.getByText("Draft Workspace")).toBeInTheDocument();
  });
});
