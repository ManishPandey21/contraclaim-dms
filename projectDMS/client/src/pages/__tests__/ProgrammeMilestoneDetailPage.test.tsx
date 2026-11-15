import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getProgrammeMilestone: vi.fn() }));
const rbac = vi.hoisted(() => ({ granted: new Set<string>() }));
/** The navbar selection the page is held to (CL-3B). */
const tenant = vi.hoisted(() => ({ value: { loading: false, selectedProjectId: "proj-A1", selectedOrganizationId: "org-A" } }));

vi.mock("@/services/programme-milestones-api", () => api);
vi.mock("@/contexts/TenantContext", () => ({ useTenant: () => tenant.value }));
vi.mock("@/hooks/useRBAC", () => ({
  default: () => ({ can: (permission: string) => rbac.granted.has(permission), loading: false, roles: [], permissions: new Set() }),
}));
vi.mock("@/components/document-links/EntityDocumentLinks", () => ({
  default: (props: any) => (
    <div
      data-testid="programme-evidence"
      data-target-type={props.targetType}
      data-target-id={props.targetId}
      data-project-id={props.projectId}
      data-default-role={props.defaultRole}
      data-can-manage={String(props.canManage)}
      data-roles={props.roles.map((role: { value: string }) => role.value).join(",")}
    />
  ),
}));

import ProgrammeMilestoneDetailPage from "../ProgrammeMilestoneDetailPage";

const milestone = (overrides: Record<string, unknown> = {}) => ({
  id: "pm-1",
  organization_id: "org-A",
  project_id: "proj-A1",
  milestone_ref: "PM-110",
  title: "Pier P4 piling",
  description: null,
  milestone_type: "programme_activity",
  status: "in_progress",
  planned_date: "2026-03-01T00:00:00",
  forecast_date: null,
  actual_date: null,
  package: null,
  discipline: null,
  location: "Pier P4",
  ...overrides,
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/programme-milestones/pm-1"]}>
      <Routes>
        <Route path="/programme-milestones/:id" element={<ProgrammeMilestoneDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const scopeError = (status: number, code: string) => ({ response: { status, data: { detail: { code, message: code } } } });

describe("ProgrammeMilestoneDetailPage (CL-3B)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    rbac.granted = new Set(["dms.evidence_graph.view", "dms.evidence_graph.manage"]);
    tenant.value = { loading: false, selectedProjectId: "proj-A1", selectedOrganizationId: "org-A" };
    api.getProgrammeMilestone.mockResolvedValue(milestone());
  });

  it("shows the milestone and its shared Documents section", async () => {
    renderPage();
    expect(await screen.findByText("Pier P4 piling")).toBeInTheDocument();
    expect(screen.getByText("PM-110")).toBeInTheDocument();
    const evidence = screen.getByTestId("programme-evidence");
    expect(evidence).toHaveAttribute("data-target-type", "programme_milestone");
    expect(evidence).toHaveAttribute("data-target-id", "pm-1");
    expect(evidence).toHaveAttribute("data-project-id", "proj-A1");
    expect(evidence).toHaveAttribute("data-default-role", "progress_evidence");
    expect(evidence).toHaveAttribute("data-roles", "progress_evidence,programme_record,correspondence,supporting_document");
    expect(evidence).toHaveAttribute("data-can-manage", "true");
    expect(api.getProgrammeMilestone).toHaveBeenCalledWith("pm-1");
  });

  it("is view-only without the manage permission", async () => {
    rbac.granted = new Set(["dms.evidence_graph.view"]);
    renderPage();
    expect(await screen.findByTestId("programme-evidence")).toHaveAttribute("data-can-manage", "false");
  });

  it.each([
    [scopeError(400, "selection_required"), "Select a project in the navbar to open this programme milestone."],
    [scopeError(403, "context_forbidden"), "not available in the project selected in the navbar"],
    [{ response: { status: 403, data: { detail: "Forbidden" } } }, "You do not have access to this programme milestone."],
    [{ response: { status: 404, data: { detail: "Not found" } } }, "This programme milestone does not exist."],
  ])("explains a refusal instead of showing a record (%#)", async (error, message) => {
    api.getProgrammeMilestone.mockRejectedValue(error);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByTestId("programme-evidence")).not.toBeInTheDocument();
  });

  it("never shows a milestone of another project than the selection", async () => {
    api.getProgrammeMilestone.mockResolvedValue(milestone({ project_id: "proj-A2" }));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("not available in the project selected in the navbar");
    expect(screen.queryByText("Pier P4 piling")).not.toBeInTheDocument();
  });

  it("waits for the tenant scope before loading", async () => {
    tenant.value = { loading: true, selectedProjectId: "", selectedOrganizationId: "" };
    renderPage();
    await waitFor(() => expect(screen.getByText(/Loading programme milestone/)).toBeInTheDocument());
    expect(api.getProgrammeMilestone).not.toHaveBeenCalled();
  });
});
