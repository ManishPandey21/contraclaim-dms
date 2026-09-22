import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const chronologyApi = vi.hoisted(() => ({
  listChronologies: vi.fn(),
  listChronologyEvents: vi.fn(),
  getChronologyPleadingContext: vi.fn(),
  createChronology: vi.fn(),
  extractChronologyEvents: vi.fn(),
  verifyChronologyEvent: vi.fn(),
  rejectChronologyEvent: vi.fn(),
  attachChronologyToDraft: vi.fn(),
  chronologyExportUrl: vi.fn(() => "#"),
}));
const documentsApi = vi.hoisted(() => ({ listDocuments: vi.fn() }));
const rbac = vi.hoisted(() => ({ granted: new Set<string>() }));
const tenant = vi.hoisted(() => ({ value: { loading: false, selectedProjectId: "proj-A1", selectedOrganizationId: "org-A" } }));
const toastApi = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));

vi.mock("@/services/chronology-api", () => chronologyApi);
vi.mock("@/services/documents-api", () => documentsApi);
vi.mock("sonner", () => ({ toast: toastApi }));
vi.mock("@/contexts/TenantContext", () => ({ useTenant: () => tenant.value }));
vi.mock("@/hooks/useRBAC", () => ({
  default: () => ({ can: (permission: string) => rbac.granted.has(permission), loading: false, roles: [], permissions: new Set() }),
}));
vi.mock("@/components/document-links/EntityDocumentLinks", () => ({
  default: (props: any) => (
    <div
      data-testid="event-documents"
      data-target-type={props.targetType}
      data-target-id={props.targetId}
      data-can-manage={String(props.canManage)}
      data-roles={props.roles.map((role: { value: string }) => role.value).join(",")}
    />
  ),
}));

import ChronologyBuilderPage from "../ChronologyBuilderPage";

const chronology = (id: string, overrides: Record<string, unknown> = {}) => ({
  id, organization_id: "org-A", project_id: "proj-A1", title: `Chronology ${id}`, chronology_type: "eot_delay",
  party_perspective: "claimant", status: "review", selected_source_ids: [], selected_source_types: [], summary_counts: {},
  ...overrides,
});
const event = (id: string, chronologyId: string) => ({
  id, chronology_id: chronologyId, organization_id: "org-A", project_id: "proj-A1", title: `Event ${id}`,
  event_date: "2026-02-10T00:00:00", date_type: "exact", verification_status: "verified", event_classification: "notice",
  supports_party: "claimant", contract_clauses: [], issue_tags: [], claim_heads: [], event_link_ids: [],
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/chronology" element={<ChronologyBuilderPage />} />
        <Route path="/chronology/:chronologyId" element={<ChronologyBuilderPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ChronologyBuilderPage Documents and deep links (CL-3B)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    rbac.granted = new Set(["dms.chronology.view", "dms.chronology.edit"]);
    tenant.value = { loading: false, selectedProjectId: "proj-A1", selectedOrganizationId: "org-A" };
    chronologyApi.listChronologies.mockResolvedValue([chronology("chr-1"), chronology("chr-2")]);
    chronologyApi.listChronologyEvents.mockImplementation(async (id: string) =>
      id === "chr-2" ? [event("ev-21", "chr-2"), event("ev-22", "chr-2")] : [event("ev-11", "chr-1")]);
    chronologyApi.getChronologyPleadingContext.mockResolvedValue({ source_ledger: [] });
    documentsApi.listDocuments.mockResolvedValue({ documents: [] });
  });

  it("opens the deep-linked chronology and the event's Documents", async () => {
    renderAt("/chronology/chr-2?event_id=ev-22");
    await waitFor(() => expect(chronologyApi.listChronologyEvents).toHaveBeenCalledWith("chr-2", undefined));
    const cards = await screen.findAllByTestId("chronology-event");
    const focused = cards.find((card) => card.getAttribute("data-event-id") === "ev-22")!;
    const documents = within(focused).getByTestId("event-documents");
    expect(documents).toHaveAttribute("data-target-type", "chronology_event");
    expect(documents).toHaveAttribute("data-target-id", "ev-22");
    expect(documents).toHaveAttribute("data-roles", "correspondence,supporting_document");
    expect(documents).toHaveAttribute("data-can-manage", "true");
    // Only the linked event is opened.
    const other = cards.find((card) => card.getAttribute("data-event-id") === "ev-21")!;
    expect(within(other).queryByTestId("event-documents")).not.toBeInTheDocument();
  });

  it("opens an event's Documents on demand, read-only without edit", async () => {
    rbac.granted = new Set(["dms.chronology.view"]);
    renderAt("/chronology");
    const card = (await screen.findAllByTestId("chronology-event"))[0];
    fireEvent.click(within(card).getByRole("button", { name: "Documents" }));
    expect(within(card).getByTestId("event-documents")).toHaveAttribute("data-can-manage", "false");
  });

  it("keeps an archived chronology's evidence read-only", async () => {
    chronologyApi.listChronologies.mockResolvedValue([chronology("chr-1", { status: "archived" })]);
    renderAt("/chronology");
    const archivedCard = (await screen.findAllByTestId("chronology-event"))[0];
    fireEvent.click(within(archivedCard).getByRole("button", { name: "Documents" }));
    expect(within(archivedCard).getByTestId("event-documents")).toHaveAttribute("data-can-manage", "false");
  });

  it("follows the navbar selection and asks for one when none is selected", async () => {
    tenant.value = { loading: false, selectedProjectId: "", selectedOrganizationId: "org-A" };
    renderAt("/chronology");
    expect(await screen.findByRole("status")).toHaveTextContent("Select a project in the navbar");
    expect(chronologyApi.listChronologies).not.toHaveBeenCalled();
  });

  it("says why when the server refuses the selection", async () => {
    chronologyApi.listChronologies.mockRejectedValue({
      response: { status: 403, data: { detail: { code: "context_forbidden", message: "no" } } },
    });
    renderAt("/chronology");
    await waitFor(() => expect(toastApi.error).toHaveBeenCalledWith(
      "This chronology is not in the project selected in the navbar.",
    ));
    expect(screen.queryByTestId("chronology-event")).not.toBeInTheDocument();
  });
});
