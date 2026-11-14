import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listHindrances: vi.fn(),
  createHindrance: vi.fn(),
  updateHindrance: vi.fn(),
}));
const tenant = vi.hoisted(() => ({
  value: {
    selectedOrganizationId: "org-A",
    selectedProjectId: "proj-A1",
    selectedProject: { _id: "proj-A1", name: "Metro Package A1" },
    loading: false,
  } as Record<string, unknown>,
}));
const rbac = vi.hoisted(() => ({ granted: new Set<string>() }));

vi.mock("@/services/hindrance-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/hindrance-api")>()),
  ...api,
}));
vi.mock("@/contexts/TenantContext", () => ({ useTenant: () => tenant.value }));
vi.mock("@/hooks/useRBAC", () => ({
  default: () => ({ can: (permission: string) => rbac.granted.has(permission), loading: false, roles: [], permissions: new Set() }),
}));

import HindranceRegisterPage from "../HindranceRegisterPage";

const row = (overrides: Record<string, unknown> = {}) => ({
  id: "h-1",
  organization_id: "org-A",
  project_id: "proj-A1",
  event_type: "hindrance",
  hindrance_ref: "HIN-0001",
  title: "Site access blocked at Station S2",
  category: "site_access",
  start_date: "2026-02-10T00:00:00",
  end_date: "2026-02-14T00:00:00",
  responsibility: "employer",
  affected_party: "Contractor",
  location: "Station S2",
  critical_path_impact: true,
  status: "open",
  linked_document_ids: [],
  created_at: "2026-02-10T08:00:00",
  updated_at: "2026-02-11T08:00:00",
  timeline_sync_status: "synced",
  ...overrides,
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/hindrances"]}>
      <Routes>
        <Route path="/hindrances" element={<HindranceRegisterPage />} />
        <Route path="/hindrances/:id" element={<p>Detail page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const lastParams = () => api.listHindrances.mock.calls[api.listHindrances.mock.calls.length - 1][0];

describe("HindranceRegisterPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    rbac.granted = new Set(["dms.hindrance.view", "dms.hindrance.create"]);
    tenant.value = {
      selectedOrganizationId: "org-A",
      selectedProjectId: "proj-A1",
      selectedProject: { _id: "proj-A1", name: "Metro Package A1" },
      loading: false,
    };
    api.listHindrances.mockResolvedValue({ items: [row()], total: 1, skip: 0, limit: 25 });
  });

  it("renders the register columns scoped to the navbar selection", async () => {
    renderPage();

    expect(await screen.findByText("HIN-0001")).toBeInTheDocument();
    for (const header of [
      "Reference", "Type", "Title", "Category", "Start date", "End / resolution", "Responsibility",
      "Affected party", "Location", "Critical path", "Status", "Updated",
    ]) {
      expect(screen.getByRole("columnheader", { name: new RegExp(header) })).toBeInTheDocument();
    }
    const cells = within(screen.getAllByRole("row")[1]);
    expect(cells.getByText("Site access")).toBeInTheDocument();
    expect(cells.getByText("10 Feb 2026")).toBeInTheDocument();
    expect(cells.getByText("Employer")).toBeInTheDocument();
    expect(cells.getByText("Yes")).toBeInTheDocument();
    expect(cells.getByText("Open")).toBeInTheDocument();
    expect(lastParams()).toMatchObject({ organization_id: "org-A", project_id: "proj-A1", sort: "start_date", order: "desc", skip: 0 });
    expect(lastParams().include_archived).toBeUndefined();
  });

  it("sends filters, debounced search, sort and archived toggle to the API", async () => {
    renderPage();
    await screen.findByText("HIN-0001");

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "constraint" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "resolved" } });
    fireEvent.change(screen.getByLabelText("Responsibility"), { target: { value: "neutral" } });
    fireEvent.change(screen.getByLabelText("Started on or after"), { target: { value: "2026-02-01" } });
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "gate 3" } });
    fireEvent.click(screen.getByLabelText("Include archived"));
    fireEvent.click(screen.getByRole("button", { name: /^Title/ }));

    await waitFor(() =>
      expect(lastParams()).toMatchObject({
        event_type: "constraint",
        status: "resolved",
        responsibility: "neutral",
        start_from: "2026-02-01T00:00:00",
        q: "gate 3",
        include_archived: true,
        sort: "title",
        order: "asc",
      }),
    );
  });

  it("paginates with the server total", async () => {
    api.listHindrances.mockResolvedValue({ items: [row()], total: 60, skip: 0, limit: 25 });
    renderPage();
    expect(await screen.findByText("1–25 of 60")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(lastParams().skip).toBe(25));
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();

    // Regression: the search debounce fires once after mount; it must not
    // reset the page the user has since moved to.
    await new Promise((resolve) => setTimeout(resolve, 450));
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    expect(lastParams().skip).toBe(25);
  });

  it("distinguishes an empty register from a filter with no results", async () => {
    api.listHindrances.mockResolvedValue({ items: [], total: 0, skip: 0, limit: 25 });
    renderPage();
    expect(await screen.findByText("No hindrances or constraints recorded yet.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "hindrance" } });

    expect(await screen.findByText("No entries match these filters.")).toBeInTheDocument();
  });

  it("shows a permission-denied state for a 403 instead of an empty register", async () => {
    api.listHindrances.mockRejectedValue({ response: { status: 403 } });
    renderPage();

    expect(await screen.findByText("You do not have access to this register.")).toBeInTheDocument();
    expect(screen.queryByText("No hindrances or constraints recorded yet.")).not.toBeInTheDocument();
  });

  it("fails visibly on an API error and retries", async () => {
    api.listHindrances.mockRejectedValueOnce({ response: { status: 500 } });
    renderPage();

    expect(await screen.findByText("The register could not be loaded.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));

    expect(await screen.findByText("HIN-0001")).toBeInTheDocument();
  });

  it("with no project selected, lists across scope but will not create", async () => {
    tenant.value = { ...tenant.value, selectedProjectId: "", selectedProject: null };
    renderPage();
    await screen.findByText("HIN-0001");

    expect(screen.getByRole("status")).toHaveTextContent("No project is selected");
    expect(screen.getByRole("button", { name: /Record entry/ })).toBeDisabled();
    expect(lastParams().project_id).toBeUndefined();
  });

  it("hides creation from a viewer without the create permission", async () => {
    rbac.granted = new Set(["dms.hindrance.view"]);
    renderPage();
    await screen.findByText("HIN-0001");

    expect(screen.queryByRole("button", { name: /Record entry/ })).not.toBeInTheDocument();
  });

  it("records an entry in the selected project and refreshes the register", async () => {
    api.createHindrance.mockResolvedValue(row({ id: "h-2", hindrance_ref: "HIN-0002", title: "New record" }));
    renderPage();
    await screen.findByText("HIN-0001");
    const callsBefore = api.listHindrances.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: /Record entry/ }));
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.change(dialog.getByLabelText("Title *"), { target: { value: "New record" } });
    fireEvent.change(dialog.getByLabelText("Start / occurrence date *"), { target: { value: "2026-03-01" } });
    fireEvent.change(dialog.getByLabelText("Category"), { target: { value: "drawing_approval" } });
    fireEvent.click(dialog.getByRole("button", { name: "Record entry" }));

    await waitFor(() => expect(api.createHindrance).toHaveBeenCalledTimes(1));
    expect(api.createHindrance.mock.calls[0][0]).toMatchObject({
      organization_id: "org-A",
      project_id: "proj-A1",
      event_type: "hindrance",
      title: "New record",
      start_date: "2026-03-01T00:00:00",
      category: "drawing_approval",
      critical_path_impact: false,
      claimed_days: null,
    });
    await waitFor(() => expect(api.listHindrances.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  it("opens the detail page from a row", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Site access blocked at Station S2"));

    expect(await screen.findByText("Detail page")).toBeInTheDocument();
  });
});
