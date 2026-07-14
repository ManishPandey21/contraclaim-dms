import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ArbitrationCaseWorkspacePage from "../ArbitrationCaseWorkspacePage";

const getArbitrationCaseMock = vi.fn();
const getArbitrationCaseDashboardMock = vi.fn();
const listMatrixRowsMock = vi.fn();
const listArbitrationCasesMock = vi.fn();
const getArbitrationReadinessMock = vi.fn();
const runArbitrationAgentMock = vi.fn();

vi.mock("@/services/arbitration-cases-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/arbitration-cases-api")>();
  return {
    ...actual,
    getArbitrationCase: (...args: unknown[]) => getArbitrationCaseMock(...args),
    getArbitrationCaseDashboard: (...args: unknown[]) => getArbitrationCaseDashboardMock(...args),
    listMatrixRows: (...args: unknown[]) => listMatrixRowsMock(...args),
    listArbitrationCases: (...args: unknown[]) => listArbitrationCasesMock(...args),
    getArbitrationReadiness: (...args: unknown[]) => getArbitrationReadinessMock(...args),
    runArbitrationAgent: (...args: unknown[]) => runArbitrationAgentMock(...args),
  };
});

vi.mock("@/services/arbitration-drafting-api", () => ({
  createArbitrationDraft: vi.fn(),
  prepareArbitrationDraftFromCase: vi.fn(),
}));

vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getProjects: vi.fn().mockResolvedValue([]) },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const CASE = {
  _id: "case-1",
  organization_id: "org-1",
  project_id: "project-1",
  title: "EOT arbitration case",
  party_perspective: "claimant",
  status: "matrix_preparation",
  readiness_score: 40,
  readiness_blockers: [],
};

const READINESS = {
  case_id: "case-1",
  readiness_score: 40,
  status: "blocked",
  blockers: [],
  checks: [],
};

const DASHBOARD = {
  case: CASE,
  matrix_counts: { "document-index": 1 },
  approved_counts: { "document-index": 1 },
  drafts: [],
  readiness: READINESS,
  latest_agent_runs: [],
};

const DOCUMENT_ROWS = [
  {
    _id: "row-1",
    case_id: "case-1",
    title: "Delay notice",
    source_id: "doc-1",
    exhibit_id: "C-1",
    approval_status: "approved",
  },
];

const renderWorkspace = () =>
  render(
    <MemoryRouter initialEntries={["/arbitration/cases/case-1/matrices"]}>
      <Routes>
        <Route path="/arbitration/cases/:caseId/matrices" element={<ArbitrationCaseWorkspacePage />} />
      </Routes>
    </MemoryRouter>,
  );

describe("ArbitrationCaseWorkspacePage (matrices section)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getArbitrationCaseMock.mockResolvedValue(CASE);
    getArbitrationCaseDashboardMock.mockResolvedValue(DASHBOARD);
    listMatrixRowsMock.mockResolvedValue(DOCUMENT_ROWS);
    listArbitrationCasesMock.mockResolvedValue([CASE]);
    getArbitrationReadinessMock.mockResolvedValue(READINESS);
    runArbitrationAgentMock.mockResolvedValue({ agent_type: "claim-identification", created_records: [] });
  });

  it("passes agent options (interest rate, mode) into agent runs from the dashboard", async () => {
    render(
      <MemoryRouter initialEntries={["/arbitration/cases/case-1"]}>
        <Routes>
          <Route path="/arbitration/cases/:caseId" element={<ArbitrationCaseWorkspacePage />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(getArbitrationCaseMock).toHaveBeenCalledWith("case-1"));

    await userEvent.type(screen.getByRole("spinbutton", { name: "Interest rate percent per annum" }), "12");
    await userEvent.type(screen.getByRole("spinbutton", { name: "Interest period in days" }), "365");
    await userEvent.click(screen.getByRole("button", { name: /Claims/ }));

    await waitFor(() =>
      expect(runArbitrationAgentMock).toHaveBeenCalledWith("case-1", "claim-identification", {
        options: { interest_rate: 12, interest_period_days: 365 },
      }),
    );
  });

  it("loads the case workspace and renders every matrix tab including Jurisdiction & Limitation", async () => {
    renderWorkspace();

    await waitFor(() => {
      expect(getArbitrationCaseMock).toHaveBeenCalledWith("case-1");
      expect(listMatrixRowsMock).toHaveBeenCalledWith("case-1", "document-index");
    });

    // All matrix tabs render, including the ARB-102 jurisdiction matrix.
    for (const label of [
      "Document Index",
      "Clause Matrix",
      "Issue Matrix",
      "Claim Matrix",
      "Defence Matrix",
      "Counterclaim Matrix",
      "Rejoinder Matrix",
      "Quantum Annexures",
      "Notice Compliance",
      "Jurisdiction & Limitation",
      "Expert Alignment",
      "Chronology Matrix",
    ]) {
      expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
    }

    // Seeded document-index row is displayed.
    expect(await screen.findByText(/Delay notice/)).toBeInTheDocument();
  });

  it("switches to the jurisdiction matrix tab and reloads rows for that matrix", async () => {
    renderWorkspace();
    await waitFor(() => expect(listMatrixRowsMock).toHaveBeenCalledWith("case-1", "document-index"));

    listMatrixRowsMock.mockResolvedValue([]);
    await userEvent.click(screen.getByRole("tab", { name: "Jurisdiction & Limitation" }));

    await waitFor(() => {
      expect(listMatrixRowsMock).toHaveBeenCalledWith("case-1", "jurisdiction-matrix");
    });

    // The jurisdiction-specific add-row fields appear.
    expect(screen.getByText(/Check type/)).toBeInTheDocument();
    expect(screen.getByText("Limitation status")).toBeInTheDocument();
  });
});
