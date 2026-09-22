import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getHindrance: vi.fn(),
  getHindranceHistory: vi.fn(),
  listHindranceLinks: vi.fn(),
  archiveHindrance: vi.fn(),
  restoreHindrance: vi.fn(),
  resyncHindranceTimeline: vi.fn(),
  linkHindrance: vi.fn(),
  unlinkHindrance: vi.fn(),
  listProgrammeMilestones: vi.fn(),
  updateHindrance: vi.fn(),
  createHindrance: vi.fn(),
}));
const keyDates = vi.hoisted(() => ({ getMilestones: vi.fn(), getKeyDateWorkflow: vi.fn() }));
const rbac = vi.hoisted(() => ({ granted: new Set<string>() }));
/** The navbar selection the page is held to (owner decision 2026-09-22). */
const tenant = vi.hoisted(() => ({ value: { loading: false, selectedProjectId: "proj-A1", selectedOrganizationId: "org-A" } }));

vi.mock("@/services/hindrance-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/hindrance-api")>()),
  ...api,
}));
vi.mock("@/services/key-dates-api", () => keyDates);
vi.mock("@/contexts/TenantContext", () => ({ useTenant: () => tenant.value }));
vi.mock("@/hooks/useRBAC", () => ({
  default: () => ({ can: (permission: string) => rbac.granted.has(permission), loading: false, roles: [], permissions: new Set() }),
}));
vi.mock("@/components/document-links/EntityDocumentLinks", () => ({
  default: (props: any) => (
    <div
      data-testid="hindrance-evidence"
      data-target-type={props.targetType}
      data-target-id={props.targetId}
      data-project-id={props.projectId}
      data-default-role={props.defaultRole}
      data-can-manage={String(props.canManage)}
      data-roles={props.roles.map((role: { value: string }) => role.value).join(",")}
    />
  ),
}));

import HindranceDetailPage from "../HindranceDetailPage";

const entry = (overrides: Record<string, unknown> = {}) => ({
  id: "h-1",
  organization_id: "org-A",
  project_id: "proj-A1",
  event_type: "hindrance",
  hindrance_ref: "HIN-0001",
  title: "Site access blocked at Station S2",
  description: "Police barricading",
  category: "site_access",
  start_date: "2026-02-10T00:00:00",
  end_date: null,
  responsibility: "employer",
  responsible_party: "Employer",
  affected_party: "Contractor",
  impact_description: "Crane stood down",
  location: "Station S2",
  critical_path_impact: false,
  status: "open",
  linked_document_ids: [],
  archived_at: null,
  timeline_sync_status: "synced",
  created_at: "2026-02-10T08:00:00",
  created_by: "u-1",
  ...overrides,
});

const keyDateLink = {
  id: "link-1",
  delay_event_id: "h-1",
  organization_id: "org-A",
  project_id: "proj-A1",
  target_type: "key_date",
  target_id: "kd-A1",
  relationship_role: "impacts_key_date",
  revision: 1,
  target: { label: "KD-03", title: "Access to Station S2", status: "pending", route: "/key-dates/kd-A1" },
  target_available: true,
  target_restricted: false,
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/hindrances/h-1"]}>
      <Routes><Route path="/hindrances/:id" element={<HindranceDetailPage />} /></Routes>
    </MemoryRouter>,
  );
}

describe("HindranceDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    tenant.value = { loading: false, selectedProjectId: "proj-A1", selectedOrganizationId: "org-A" };
    rbac.granted = new Set(["dms.hindrance.view", "dms.hindrance.edit", "dms.hindrance.archive"]);
    api.getHindrance.mockResolvedValue(entry());
    api.listHindranceLinks.mockResolvedValue([keyDateLink]);
    api.getHindranceHistory.mockResolvedValue([
      { id: "a1", action: "delay_events.created", actor_id: "u-1", changed_fields: [], created_at: "2026-02-10T08:00:00" },
      { id: "a2", action: "delay_events.updated", actor_id: "u-1", changed_fields: ["title"], created_at: "2026-02-11T08:00:00" },
    ]);
    keyDates.getMilestones.mockResolvedValue([
      { id: "kd-A1", milestone_ref: "KD-03", title: "Access to Station S2" },
      { id: "kd-A2", milestone_ref: "KD-04", title: "Depot" },
    ]);
  });

  it("renders every section of the record", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: /Site access blocked at Station S2/ })).toBeInTheDocument();
    for (const title of [
      "Overview", "Dates & responsibility", "Impact", "Evidence & documents", "Affected activities",
      "Affected key dates", "EOT submissions", "Timeline & history", "Record",
    ]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("Crane stood down")).toBeInTheDocument();
    expect(await screen.findByText("KD-03")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /KD-03/ })).toHaveAttribute("href", "/key-dates/kd-A1");
    expect(screen.getByText("Changed: title")).toBeInTheDocument();
  });

  it("wires evidence to the canonical relationship target with the register's roles", async () => {
    renderPage();
    const evidence = await screen.findByTestId("hindrance-evidence");

    expect(evidence).toHaveAttribute("data-target-type", "delay_event");
    expect(evidence).toHaveAttribute("data-target-id", "h-1");
    expect(evidence).toHaveAttribute("data-project-id", "proj-A1");
    expect(evidence).toHaveAttribute("data-default-role", "site_record");
    expect(evidence).toHaveAttribute("data-can-manage", "true");
    expect(evidence.getAttribute("data-roles")).toContain("photograph");
  });

  it("archives only with a reason and then becomes read-only", async () => {
    api.archiveHindrance.mockResolvedValue(entry({ archived_at: "2026-02-12T08:00:00", archive_reason: "Raised in error" }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Archive/ }));

    const dialog = within(await screen.findByRole("alertdialog"));
    expect(dialog.getByRole("button", { name: "Archive" })).toBeDisabled();
    fireEvent.change(dialog.getByLabelText("Reason *"), { target: { value: "Raised in error" } });
    fireEvent.click(dialog.getByRole("button", { name: "Archive" }));

    await waitFor(() => expect(api.archiveHindrance).toHaveBeenCalledWith("h-1", "Raised in error"));
    expect(await screen.findByText(/Archived entries are read-only/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Restore/ })).toBeInTheDocument();
    expect(screen.getByTestId("hindrance-evidence")).toHaveAttribute("data-can-manage", "false");
  });

  it("makes a failed timeline projection visible and retries it", async () => {
    api.getHindrance.mockResolvedValue(entry({ timeline_sync_status: "failed", timeline_sync_error: "RuntimeError: timeline projection failed" }));
    api.resyncHindranceTimeline.mockResolvedValue(entry({ timeline_sync_status: "synced" }));
    renderPage();

    expect(await screen.findByText(/not on the Contract Timeline yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Retry timeline sync/ }));

    await waitFor(() => expect(api.resyncHindranceTimeline).toHaveBeenCalledWith("h-1"));
    await waitFor(() => expect(screen.queryByText(/not on the Contract Timeline yet/)).not.toBeInTheDocument());
  });

  it("links and unlinks a key date", async () => {
    api.linkHindrance.mockResolvedValue({ ...keyDateLink, id: "link-2", target_id: "kd-A2" });
    api.unlinkHindrance.mockResolvedValue({ ...keyDateLink, removed_at: "2026-02-12T08:00:00" });
    renderPage();
    await screen.findByText("KD-03");

    fireEvent.click(screen.getByRole("button", { name: "Link key date" }));
    fireEvent.click(await screen.findByRole("button", { name: "Link KD-04" }));
    await waitFor(() =>
      expect(api.linkHindrance).toHaveBeenCalledWith("h-1", { target_type: "key_date", target_id: "kd-A2" }),
    );
    expect(keyDates.getMilestones).toHaveBeenCalledWith({ project_id: "proj-A1" });

    fireEvent.click(screen.getByRole("button", { name: "Unlink KD-03" }));
    await waitFor(() => expect(api.unlinkHindrance).toHaveBeenCalledWith("h-1", "link-1", expect.any(String)));
    await waitFor(() => expect(api.listHindranceLinks.mock.calls.length).toBeGreaterThanOrEqual(3));
  });

  it("is view-only without edit and archive permissions", async () => {
    rbac.granted = new Set(["dms.hindrance.view"]);
    renderPage();
    await screen.findByText("KD-03");

    expect(screen.queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Archive/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Link key date" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Unlink KD-03" })).not.toBeInTheDocument();
    expect(screen.getByTestId("hindrance-evidence")).toHaveAttribute("data-can-manage", "false");
  });

  it.each([
    [403, "You do not have access to this register entry."],
    [404, "This register entry does not exist."],
    [500, "The register entry could not be loaded."],
  ])("shows a distinct state for HTTP %s", async (status, message) => {
    api.getHindrance.mockRejectedValue({ response: { status } });
    renderPage();

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to register/ })).toHaveAttribute("href", "/hindrances");
  });

  it("refuses an entry from another project than the one selected, even if the API returned it", async () => {
    tenant.value = { ...tenant.value, selectedProjectId: "proj-A2" };
    renderPage();

    expect(await screen.findByText("This register entry is not available in the project selected in the navbar.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Site access blocked/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
    expect(api.listHindranceLinks).not.toHaveBeenCalled();
  });

  it.each([
    ["context_forbidden", 403, "This register entry is not available in the project selected in the navbar."],
    ["selection_required", 400, "Select a project in the navbar to open this register entry."],
  ])("shows the backend's %s scope refusal as its own state", async (code, status, message) => {
    api.getHindrance.mockRejectedValue({ response: { status, data: { detail: { code, message: "x" } } } });
    renderPage();

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
  });

  it("waits for the tenant scope before loading, then loads under it", async () => {
    tenant.value = { ...tenant.value, loading: true };
    const view = renderPage();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.getHindrance).not.toHaveBeenCalled();

    tenant.value = { ...tenant.value, loading: false };
    view.rerender(
      <MemoryRouter initialEntries={["/hindrances/h-1"]}>
        <Routes><Route path="/hindrances/:id" element={<HindranceDetailPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { name: /Site access blocked/ })).toBeInTheDocument();
  });

  it("drops the entry when the navbar switches to another project", async () => {
    const view = renderPage();
    expect(await screen.findByRole("heading", { name: /Site access blocked/ })).toBeInTheDocument();

    tenant.value = { ...tenant.value, selectedProjectId: "proj-A2" };
    view.rerender(
      <MemoryRouter initialEntries={["/hindrances/h-1"]}>
        <Routes><Route path="/hindrances/:id" element={<HindranceDetailPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("This register entry is not available in the project selected in the navbar.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Site access blocked/ })).not.toBeInTheDocument();
  });

  it("says so when history cannot be loaded instead of claiming there is none", async () => {
    api.getHindranceHistory.mockRejectedValue({ response: { status: 500 } });
    renderPage();

    expect(await screen.findByText("History could not be loaded.")).toBeInTheDocument();
    expect(screen.queryByText("No history recorded.")).not.toBeInTheDocument();
  });

  it("shows a deleted or restricted relationship target honestly", async () => {
    api.listHindranceLinks.mockResolvedValue([
      { ...keyDateLink, id: "gone", target: null, target_available: false },
      { ...keyDateLink, id: "hidden", target_id: "kd-X", target: null, target_restricted: true },
    ]);
    renderPage();

    expect(await screen.findByText("The linked record no longer exists")).toBeInTheDocument();
    expect(screen.getByText("You do not have access to this record")).toBeInTheDocument();
  });
});
