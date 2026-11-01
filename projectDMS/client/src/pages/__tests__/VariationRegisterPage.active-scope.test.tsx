import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VariationRegisterPage from "@/pages/VariationRegisterPage";

/**
 * CL-3A: the Variation Register follows the navbar selection (TenantContext),
 * like the Hindrance Register. The server holds every record-level request to
 * the selection; the page must never show one project's rows under another,
 * must file a new Variation in the selected project, and must explain a scope
 * refusal instead of showing a generic failure.
 */

const variationApi = vi.hoisted(() => ({
  getVariations: vi.fn(), getVariation: vi.fn(), getVariationSummary: vi.fn(),
  createVariation: vi.fn(), updateVariation: vi.fn(), deleteVariation: vi.fn(), exportVariations: vi.fn(),
}));
const tenant = vi.hoisted(() => ({
  value: {
    selectedOrganizationId: "org-A",
    selectedProjectId: "proj-A1",
    selectedProject: { _id: "proj-A1", name: "Metro A1" },
    loading: false,
  } as Record<string, unknown>,
}));
const toasts = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));

vi.mock("@/services/variations-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/variations-api")>()),
  ...variationApi,
}));
vi.mock("@/contexts/TenantContext", () => ({ useTenant: () => tenant.value }));
vi.mock("@/hooks/useRBAC", () => ({ default: () => ({ can: () => true }) }));
vi.mock("@/services/contract-master-api", () => ({ getContractMasterForProject: vi.fn().mockResolvedValue(null) }));
vi.mock("sonner", () => ({ toast: toasts }));

const row = (id: string, project: string) => ({
  id, _id: id, variation_number: id.toUpperCase(), variation_type: "positive", description: `Works ${id}`,
  status: "submitted", organization_id: "org-A", project_id: project, contract_id: "primary",
  currency_amounts: [], linked_document_ids: [],
});
const summary = {
  original_contract_value: 0, total_submitted_amount: 0, total_approved_amount: 0,
  cumulative_approved_variation: 0, revised_contract_value: 0, percentage_variation: 0,
  pending_variation_count: 0, approved_variation_count: 0, rejected_variation_count: 0,
};
const scopeError = (code: string, status: number) =>
  Object.assign(new Error(code), { response: { status, data: { detail: { code, message: code } } } });

const page = (url = "/variations") => (
  <MemoryRouter initialEntries={[url]}>
    <Routes><Route path="/variations" element={<VariationRegisterPage />} /></Routes>
  </MemoryRouter>
);

const select = (project: string | null) => {
  tenant.value = {
    selectedOrganizationId: "org-A",
    selectedProjectId: project || "",
    selectedProject: project ? { _id: project, name: `Metro ${project.slice(-2).toUpperCase()}` } : null,
    loading: false,
  };
};

describe("Variation Register active project scope", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    select("proj-A1");
    variationApi.getVariationSummary.mockResolvedValue(summary);
    variationApi.getVariations.mockImplementation(async (params: Record<string, string> = {}) =>
      params.project_id === "proj-A2" ? [row("vo-a2", "proj-A2")] : [row("vo-a1", "proj-A1")],
    );
  });

  it("lists the selected project only and says which project it is", async () => {
    render(page());
    expect(await screen.findByText("VO-A1")).toBeInTheDocument();
    expect(variationApi.getVariations).toHaveBeenCalledWith(expect.objectContaining({ project_id: "proj-A1" }));
    expect(variationApi.getVariationSummary).toHaveBeenCalledWith({ project_id: "proj-A1" });
    expect(screen.getByTestId("variation-scope")).toHaveTextContent("Project: Metro A1");
  });

  it("drops the previous project's rows on a switch and loads the new project", async () => {
    const view = render(page());
    expect(await screen.findByText("VO-A1")).toBeInTheDocument();

    let release: (rows: unknown[]) => void = () => undefined;
    variationApi.getVariations.mockImplementationOnce(
      () => new Promise((resolve) => { release = resolve; }),
    );
    select("proj-A2");
    view.rerender(page());
    // Before the new list arrives, no Project-A1 row is on screen.
    await waitFor(() => expect(screen.queryByText("VO-A1")).not.toBeInTheDocument());
    release([row("vo-a2", "proj-A2")]);
    expect(await screen.findByText("VO-A2")).toBeInTheDocument();
    expect(variationApi.getVariations).toHaveBeenLastCalledWith(expect.objectContaining({ project_id: "proj-A2" }));
  });

  it("files a new Variation in the selected project", async () => {
    variationApi.createVariation.mockResolvedValue(row("vo-new", "proj-A1"));
    const user = userEvent.setup();
    render(page());
    await user.click(await screen.findByRole("button", { name: /Add Variation/ }));
    expect(screen.getByLabelText("Project")).toHaveValue("Metro A1");
    await user.type(screen.getByPlaceholderText("VO-001"), "VO-NEW");
    await user.click(screen.getByRole("button", { name: /Create|Save/ }));
    await waitFor(() => expect(variationApi.createVariation).toHaveBeenCalled());
    expect(variationApi.createVariation.mock.calls[0][0]).toMatchObject({ project_id: "proj-A1" });
  });

  it("with no project selected lists without a project and offers no record actions", async () => {
    select(null);
    variationApi.getVariations.mockResolvedValue([row("vo-a1", "proj-A1"), row("vo-a2", "proj-A2")]);
    render(page());
    expect(await screen.findByText("VO-A2")).toBeInTheDocument();
    expect(variationApi.getVariations.mock.calls[0][0]).not.toHaveProperty("project_id");
    expect(screen.getByRole("button", { name: /Add Variation/ })).toBeDisabled();
    for (const button of screen.getAllByTitle("Edit")) expect(button).toBeDisabled();
    expect(screen.getByTestId("variation-scope")).toHaveTextContent(/Select a project in the navbar/);
  });

  it("explains a deep link refused by the selected project", async () => {
    variationApi.getVariation.mockRejectedValue(scopeError("context_forbidden", 403));
    render(page("/variations?variation_id=vo-a2"));
    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith("This variation is not in the project selected in the navbar."),
    );
    expect(screen.queryByText(/Correspondence — /)).not.toBeInTheDocument();
  });
});
