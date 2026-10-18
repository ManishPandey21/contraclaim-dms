import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setActiveScope } from "@/services/active-scope";

const tenant = vi.hoisted(() => ({ value: null as null | Record<string, unknown> }));
vi.mock("@/contexts/TenantContext", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/contexts/TenantContext")>()),
  useOptionalTenant: () => tenant.value,
}));

vi.mock("@/services/plan-settings-api", () => ({
  getEffectivePlanServices: vi.fn(async () => ({ effective: { organizations: {}, projects: {} } })),
}));

import DocumentsPage from "../DocumentsPage";

/**
 * The Document Library searches, filters and sorts the WHOLE authorised dataset
 * on the server; the page renders exactly the page the server returns. Project
 * scope is the navbar selection (sent as X-Org-Id / X-Proj-Id by the HTTP
 * client), never a page-level project filter.
 */

const row = (id: string, overrides: Record<string, unknown> = {}) => ({
  _id: id,
  filename: `${id}.pdf`,
  filetype: "application/pdf",
  filesize: 1024,
  uploadType: "incoming",
  letterNo: `L-${id}`,
  date: "2026-09-01",
  subject: `Subject ${id}`,
  from_: "Engineer",
  to: "Contractor",
  tags: [],
  subTags: [],
  status: "Received",
  organization_id: "org-a",
  project_id: "proj-a1",
  project_name: "A1",
  createdAt: "2026-09-01T00:00:00Z",
  ...overrides,
});

const json = (body: unknown) =>
  ({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) }) as Response;

type Request = { url: URL; headers: Headers };
let requests: Request[] = [];
let serverRows: ReturnType<typeof row>[] = [];
let serverTotal = 0;

function serve() {
  vi.spyOn(global, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/documents?")) {
      requests.push({ url: new URL(url, "http://x"), headers: new Headers(init?.headers) });
      return json({ documents: serverRows, total: serverTotal });
    }
    if (/\/tags(\?|$)/.test(url)) {
      return json({ tags: [{ _id: "tag-claims", name: "Claims" }], total: 1 });
    }
    return json([]);
  });
}

const selectProject = (organizationId: string, projectId: string) => {
  tenant.value = { selectedOrganizationId: organizationId, selectedProjectId: projectId, loading: false };
  setActiveScope({ organizationId, projectId });
};

/** Mount as MainLayout does: keyed by the navbar selection, so a switch remounts. */
function renderPage(entry = "/documents") {
  const key = `${tenant.value?.selectedOrganizationId ?? ""}:${tenant.value?.selectedProjectId ?? ""}`;
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <div key={key}>
        <DocumentsPage />
      </div>
    </MemoryRouter>,
  );
}

const last = () => requests[requests.length - 1];
const lastParams = () => last().url.searchParams;
const searchBox = () => screen.getByPlaceholderText(/search by/i);
const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

describe("Document Library filters", () => {
  beforeEach(() => {
    requests = [];
    serverRows = [row("a"), row("b")];
    serverTotal = 2;
    localStorage.clear();
    selectProject("org-a", "proj-a1");
    serve();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    tenant.value = null;
    setActiveScope({});
  });

  it("renders no page-level Project filter", async () => {
    // Organisation selected, "All Projects" in the navbar: the old page offered
    // its own project picker exactly here.
    selectProject("org-a", "");
    renderPage();
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(screen.queryByText("All Projects")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /project/i })).not.toBeInTheDocument();
  });

  it("sends no project filter: the navbar selection travels as scope headers", async () => {
    // A stale or hand-edited project_id in the URL is not a second project state.
    renderPage("/documents?project_id=proj-other");
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(lastParams().has("project_id")).toBe(false);
    expect(last().headers.get("X-Proj-Id")).toBe("proj-a1");
    expect(last().headers.get("X-Org-Id")).toBe("org-a");
  });

  it("refetches page 1 of the new project when the navbar project changes", async () => {
    const first = renderPage("/documents?page=3");
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(lastParams().get("skip")).toBe("50"); // a deep link to page 3 is honoured
    first.unmount();

    selectProject("org-a", "proj-a2");
    renderPage("/documents?page=3");
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(lastParams().get("skip")).toBe("0");
    expect(last().headers.get("X-Proj-Id")).toBe("proj-a2");
  });

  it("keeps the page when the page remounts under the same navbar project", async () => {
    renderPage("/documents?page=2").unmount();
    renderPage("/documents?page=2");
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(lastParams().get("skip")).toBe("25");
  });

  it("sends every filter, the search and the sort to the server", async () => {
    renderPage(
      "/documents?search=17.4&status=Received&tags=tag-claims&date_from=2025-01-01&date_to=2026-12-31&uploadType=incoming",
    );
    await waitFor(() => expect(requests).toHaveLength(1));
    const params = lastParams();
    expect(params.get("search")).toBe("17.4");
    expect(params.get("status")).toBe("Received");
    expect(params.get("tags")).toBe("tag-claims");
    expect(params.get("date_from")).toBe("2025-01-01");
    expect(params.get("date_to")).toBe("2026-12-31");
    expect(params.get("uploadType")).toBe("incoming");
    expect(params.get("sort_by")).toBe("date");
    expect(params.get("sort_order")).toBe("desc");
    expect(params.get("skip")).toBe("0");
    expect(params.get("limit")).toBe("25");
  });

  it("debounces the search into one request carrying the whole term", async () => {
    renderPage();
    await waitFor(() => expect(requests).toHaveLength(1));

    for (const value of ["1", "17", "17.", "17.4"]) {
      fireEvent.change(searchBox(), { target: { value } });
    }
    expect(searchBox()).toHaveValue("17.4");
    await settle();
    expect(requests).toHaveLength(1); // nothing sent per keystroke

    await waitFor(() => expect(requests).toHaveLength(2), { timeout: 2000 });
    expect(lastParams().get("search")).toBe("17.4");
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(requests).toHaveLength(2);
  });

  it("returns to page 1 when the search changes", async () => {
    renderPage("/documents?page=3");
    await waitFor(() => expect(requests).toHaveLength(1));
    fireEvent.change(searchBox(), { target: { value: "claim" } });
    await waitFor(() => expect(lastParams().get("search")).toBe("claim"), { timeout: 2000 });
    expect(lastParams().get("skip")).toBe("0");
  });

  it("returns to page 1 when a filter changes", async () => {
    renderPage("/documents?page=3");
    await waitFor(() => expect(requests).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("Date from"), { target: { value: "2026-01-01" } });
    await waitFor(() => expect(lastParams().get("date_from")).toBe("2026-01-01"));
    expect(lastParams().get("skip")).toBe("0");
  });

  it("sorts on the server and returns to page 1", async () => {
    renderPage("/documents?page=3");
    await waitFor(() => expect(requests).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: /^status/i }));
    await waitFor(() => expect(lastParams().get("sort_by")).toBe("status"));
    expect(lastParams().get("sort_order")).toBe("asc");
    expect(lastParams().get("skip")).toBe("0");
  });

  it("renders the server's rows in the server's order, unfiltered by the page", async () => {
    // The server matched "17.4" on fields the table does not show (e.g. the
    // filename), and ordered the rows itself: the page must not re-filter or
    // re-sort them.
    serverRows = [
      row("older", { subject: "First by server", date: "2024-01-01", filename: "17.4.pdf" }),
      row("newer", { subject: "Second by server", date: "2026-12-01", filename: "17.4-b.pdf" }),
    ];
    renderPage("/documents?search=17.4");
    const first = await screen.findByText("First by server");
    const second = screen.getByText("Second by server");
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("uses the filtered total from the server for the page count", async () => {
    serverRows = Array.from({ length: 25 }, (_, i) => row(`r${i}`));
    serverTotal = 135;
    renderPage("/documents?status=Received");
    expect(await screen.findByText(/of 135 documents/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "6" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "7" })).not.toBeInTheDocument();
  });

  it("has an icon-only, labelled reset control with a tooltip", async () => {
    // The shared setup's ResizeObserver mock cannot be constructed with `new`,
    // which the tooltip's positioning layer does.
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    renderPage("/documents?search=claim");
    await waitFor(() => expect(requests).toHaveLength(1));
    const reset = screen.getByRole("button", { name: "Reset filters" });
    expect(reset).toHaveAttribute("aria-label", "Reset filters");
    expect(reset.textContent?.trim()).toBe("");
    act(() => reset.focus());
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Reset filters");
  });

  it("reset clears only the page filters and keeps the navbar scope", async () => {
    renderPage(
      "/documents?search=claim&status=Received&tags=tag-claims&date_from=2026-01-01&date_to=2026-02-01&uploadType=outgoing&page=3",
    );
    await waitFor(() => expect(requests).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));

    await waitFor(() => expect(requests).toHaveLength(2));
    const params = lastParams();
    for (const name of ["search", "status", "tags", "date_from", "date_to", "uploadType", "project_id"]) {
      expect(params.has(name), name).toBe(false);
    }
    expect(params.get("skip")).toBe("0");
    expect(searchBox()).toHaveValue("");
    expect(last().headers.get("X-Proj-Id")).toBe("proj-a1");
    expect(tenant.value).toMatchObject({ selectedOrganizationId: "org-a", selectedProjectId: "proj-a1" });
  });

  it("reset is disabled when no page filter is active", async () => {
    renderPage();
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(screen.getByRole("button", { name: "Reset filters" })).toBeDisabled();
    // With the Project filter gone, an "All Projects" navbar view is not a filter.
    const card = screen.getByText("Document Library").closest("div")!.parentElement!;
    expect(within(card).queryByText("Reset")).not.toBeInTheDocument();
  });
});
