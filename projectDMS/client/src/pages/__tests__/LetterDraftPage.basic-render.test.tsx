import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import LetterDraftPage from "../LetterDraftPage";

const apiGetMock = vi.hoisted(() => vi.fn());
const letterWorkflowMock = vi.hoisted(() => ({
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
}));
const langgraphDraftMock = vi.hoisted(() => ({
  data: null,
  loading: false,
  error: null,
  runDraft: vi.fn(),
  reset: vi.fn(),
}));
const letterDraftingMock = vi.hoisted(() => ({
  data: null,
  loading: false,
  error: null,
  run: vi.fn(),
  generateDraft: vi.fn(),
  preparePlan: vi.fn(),
  latestRun: vi.fn(),
  reviseRun: vi.fn(),
  validateRun: vi.fn(),
  critiqueRun: vi.fn(),
  approveRun: vi.fn(),
  exportRun: vi.fn(),
  issueRun: vi.fn(),
  reset: vi.fn(),
}));
const letterGraphRunsMock = vi.hoisted(() => ({
  data: null,
  loading: false,
  error: null,
  fetchRun: vi.fn().mockResolvedValue(null),
  setData: vi.fn(),
}));

vi.mock("@/services/api", () => ({
  api: {
    get: apiGetMock,
  },
}));

vi.mock("@/hooks/useLetterWorkflow", () => ({
  useLetterWorkflow: () => letterWorkflowMock,
}));

vi.mock("@/hooks/useLanggraphDraft", () => ({
  useLanggraphDraft: () => langgraphDraftMock,
}));

vi.mock("@/hooks/useLetterDrafting", () => ({
  useLetterDrafting: () => letterDraftingMock,
}));

vi.mock("@/hooks/useLetterGraphRuns", () => ({
  useLetterGraphRuns: () => letterGraphRunsMock,
}));

vi.mock("@/config/features", () => ({
  LANGGRAPH_ENABLED: false,
}));

vi.mock("@/components/letter-workflow/LetterDraftEditor", () => ({
  default: () => null,
}));

vi.mock("@/components/letter-workflow/LinkedDocumentSelector", () => ({
  LinkedDocumentSelector: () => null,
}));

vi.mock("@/components/letter-workflow/BackgroundSummary", () => ({
  default: () => null,
}));

vi.mock("@/components/letter-workflow/DraftSourcesPanel", () => ({
  default: () => null,
}));

vi.mock("@/components/letter-workflow/DraftEvidencePanel", () => ({
  DraftEvidencePanel: () => null,
}));

vi.mock("@/components/langgraph/PlanViewer", () => ({
  default: () => null,
}));

vi.mock("@/components/langgraph/GraphStatusBadge", () => ({
  default: () => null,
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
    letterWorkflowMock.handleLetterUpdate.mockReset();
    letterWorkflowMock.submitForReview.mockReset();
    letterWorkflowMock.fetchLetters.mockReset();
    langgraphDraftMock.runDraft.mockReset();
    langgraphDraftMock.reset.mockReset();
    letterDraftingMock.run.mockReset();
    letterDraftingMock.generateDraft.mockReset();
    letterDraftingMock.preparePlan.mockReset();
    letterDraftingMock.latestRun.mockReset();
    letterDraftingMock.reviseRun.mockReset();
    letterDraftingMock.validateRun.mockReset();
    letterDraftingMock.critiqueRun.mockReset();
    letterDraftingMock.approveRun.mockReset();
    letterDraftingMock.exportRun.mockReset();
    letterDraftingMock.issueRun.mockReset();
    letterDraftingMock.reset.mockReset();
    letterGraphRunsMock.fetchRun.mockReset();
    letterGraphRunsMock.fetchRun.mockResolvedValue(null);
    letterGraphRunsMock.setData.mockReset();
    (window.localStorage as any).clear();
  });

  it("fetches letter metadata and displays it", async () => {
    renderAt("abc123");

    expect(
      await screen.findByRole("heading", { name: "Test Letter Subject" })
    ).toBeInTheDocument();

    expect(screen.getByText("Draft Workspace")).toBeInTheDocument();
  });
});
