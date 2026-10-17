import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * EOT submission / determination targets deep-link to
 * `/key-dates?project_id=...&submission_id=...` (and `determination_id`). The
 * register must select that project (the workflow renders only for one project)
 * and hand the focused event to the workflow, not ignore the query.
 */

const api = vi.hoisted(() => ({
  getMilestones: vi.fn(), getKeyDateDashboard: vi.fn(),
}));

vi.mock("@/services/key-dates-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/key-dates-api")>()),
  ...api,
}));
vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getProjects: vi.fn().mockResolvedValue([{ _id: "project-1", name: "Metro" }]) },
}));
vi.mock("@/components/registers/CsvImportDialog", () => ({ default: () => null }));
vi.mock("@/components/key-dates/KeyDateRevisionWorkflow", () => ({
  default: (props: { projectId: string; focusSubmissionId?: string | null; focusDeterminationId?: string | null }) => (
    <div data-testid="workflow" data-project={props.projectId}
      data-submission={props.focusSubmissionId || ""} data-determination={props.focusDeterminationId || ""} />
  ),
}));

import KeyDateRegisterPage from "@/pages/KeyDateRegisterPage";

const renderAt = (url: string) => render(
  <MemoryRouter initialEntries={[url]}>
    <Routes><Route path="/key-dates" element={<KeyDateRegisterPage />} /></Routes>
  </MemoryRouter>,
);

describe("KeyDateRegisterPage EOT deep links", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getMilestones.mockResolvedValue([]);
    api.getKeyDateDashboard.mockResolvedValue({
      total: 0, achieved: 0, overdue: 0, due_30: 0, eot_under_review: 0, eot_approved: 0,
    });
  });

  it("selects the linked project and focuses the submission", async () => {
    renderAt("/key-dates?project_id=project-1&submission_id=sub-1");
    const workflow = await screen.findByTestId("workflow");
    expect(workflow).toHaveAttribute("data-project", "project-1");
    expect(workflow).toHaveAttribute("data-submission", "sub-1");
    expect(api.getMilestones).toHaveBeenCalledWith({ project_id: "project-1" });
  });

  it("focuses a determination", async () => {
    renderAt("/key-dates?project_id=project-1&determination_id=det-1");
    expect(await screen.findByTestId("workflow")).toHaveAttribute("data-determination", "det-1");
  });

  it("renders no workflow without a project", async () => {
    renderAt("/key-dates");
    await screen.findByText(/Register/);
    expect(screen.queryByTestId("workflow")).not.toBeInTheDocument();
  });
});
