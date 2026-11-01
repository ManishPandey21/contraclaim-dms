import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EnclosuresPanel from "@/components/document-viewer/EnclosuresPanel";

const api = vi.hoisted(() => ({
  getDocumentEnclosures: vi.fn(),
  getDocumentChildren: vi.fn(),
  addDocumentEnclosure: vi.fn(),
}));
vi.mock("@/services/enhanced-api", () => ({ enhancedApi: api, default: api }));

const http = vi.hoisted(() => ({ authenticatedFetch: vi.fn() }));
vi.mock("@/services/http", () => http);

const LEGACY = {
  id: "enc-legacy",
  filename: "old-drawing.pdf",
  presigned_url: "https://bucket.s3.amazonaws.com/old.pdf?X-Amz-Signature=abc",
  filetype: "application/pdf",
  filesize: 2048,
  uploadedAt: "2026-01-05T10:00:00Z",
  uploadedBy: "user-1",
};

const CHILD_ENTRY = {
  id: "enc-child",
  filename: "site-photos.pdf",
  presigned_url: null,
  filetype: "application/pdf",
  filesize: 2.4 * 1024 * 1024,
  uploadedAt: "2026-10-20T10:00:00Z",
  uploadedBy: "user-1",
  child_document_id: "child-1",
};

function child(status: string, extra: Record<string, unknown> = {}) {
  return {
    _id: "child-1",
    filename: "site-photos.pdf",
    filetype: "application/pdf",
    filesize: CHILD_ENTRY.filesize,
    parent_document_id: "parent-1",
    relationship_type: "enclosure",
    processing_status: status,
    // The child quotes its parent's number; that must never become its identity.
    letterNo: "GLM-SAM-KNPCC-05-UPMRC-OL-2026-4951",
    ...extra,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderPanel(pollIntervalMs = 20) {
  return render(
    <MemoryRouter initialEntries={["/documentviewer/parent-1"]}>
      <Routes>
        <Route
          path="/documentviewer/parent-1"
          element={<EnclosuresPanel documentId="parent-1" pollIntervalMs={pollIntervalMs} />}
        />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>
  );
}

function rowFor(filename: string) {
  return screen.getByText(filename).closest("li") as HTMLElement;
}

describe("EnclosuresPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getDocumentEnclosures.mockResolvedValue([]);
    api.getDocumentChildren.mockResolvedValue([]);
  });

  it("renders the existing enclosure list", async () => {
    api.getDocumentEnclosures.mockResolvedValue([LEGACY, CHILD_ENTRY]);
    api.getDocumentChildren.mockResolvedValue([child("completed")]);
    renderPanel();
    expect(await screen.findByText("old-drawing.pdf")).toBeInTheDocument();
    expect(screen.getByText("site-photos.pdf")).toBeInTheDocument();
  });

  it("opens a historical enclosure's raw file, with no invented child status", async () => {
    api.getDocumentEnclosures.mockResolvedValue([LEGACY]);
    renderPanel();
    await screen.findByText("old-drawing.pdf");
    const row = rowFor("old-drawing.pdf");
    const open = within(row).getByRole("link", { name: "Open file old-drawing.pdf" });
    expect(open).toHaveAttribute("href", LEGACY.presigned_url);
    expect(within(row).queryByRole("link", { name: /Open document/ })).toBeNull();
    for (const label of ["Queued", "Processing", "Completed", "Review required", "Failed"]) {
      expect(within(row).queryByText(label)).toBeNull();
    }
  });

  it("keeps the legacy Delete for a historical enclosure", async () => {
    api.getDocumentEnclosures.mockResolvedValue([LEGACY]);
    http.authenticatedFetch.mockResolvedValue(new Response(null, { status: 204 }));
    renderPanel();
    await screen.findByText("old-drawing.pdf");
    const del = screen.getByRole("button", { name: "Delete enclosure old-drawing.pdf" });
    fireEvent.click(del);
    await waitFor(() =>
      expect(http.authenticatedFetch).toHaveBeenCalledWith(
        expect.stringContaining("/documents/parent-1/enclosures/enc-legacy"),
        { method: "DELETE" }
      )
    );
  });

  it("never offers Delete, Restore or Purge on a first-class child row (no orphaning)", async () => {
    api.getDocumentEnclosures.mockResolvedValue([CHILD_ENTRY, LEGACY]);
    api.getDocumentChildren.mockResolvedValue([child("completed")]);
    renderPanel();
    await screen.findByText("site-photos.pdf");
    const row = rowFor("site-photos.pdf");
    expect(within(row).queryByRole("button", { name: /Delete/i })).toBeNull();
    expect(within(row).queryByRole("button", { name: /Restore/i })).toBeNull();
    expect(within(row).queryByRole("button", { name: /Purge/i })).toBeNull();
    // The historical row beside it still has its legacy delete.
    expect(
      within(rowFor("old-drawing.pdf")).getByRole("button", { name: /Delete/i })
    ).toBeInTheDocument();
  });

  it("shows relationship, file type and size on a child row", async () => {
    api.getDocumentEnclosures.mockResolvedValue([CHILD_ENTRY]);
    api.getDocumentChildren.mockResolvedValue([child("completed")]);
    renderPanel();
    await screen.findByText("site-photos.pdf");
    expect(within(rowFor("site-photos.pdf")).getByText("Enclosure · PDF · 2.4 MB")).toBeInTheDocument();
  });

  it("never renders the child's letter number as its identity", async () => {
    api.getDocumentEnclosures.mockResolvedValue([CHILD_ENTRY]);
    api.getDocumentChildren.mockResolvedValue([child("completed")]);
    renderPanel();
    await screen.findByText("site-photos.pdf");
    expect(screen.queryByText(/GLM-SAM-KNPCC-05-UPMRC-OL-2026-4951/)).toBeNull();
    expect(screen.queryByText(/Letter No/i)).toBeNull();
  });

  it("navigates Open document to the child Document Viewer", async () => {
    api.getDocumentEnclosures.mockResolvedValue([CHILD_ENTRY]);
    api.getDocumentChildren.mockResolvedValue([child("completed")]);
    renderPanel();
    await screen.findByText("site-photos.pdf");
    const open = within(rowFor("site-photos.pdf")).getByRole("link", {
      name: "Open document site-photos.pdf",
    });
    fireEvent.click(open);
    expect((await screen.findByTestId("location")).textContent).toBe(
      "/documentviewer/child-1?tab=metadata"
    );
  });

  it.each([
    ["queued", "Queued"],
    ["retrying", "Retrying"],
    ["processing", "Processing"],
    ["completed", "Completed"],
    ["human_review_required", "Review required"],
    ["failed", "Failed"],
    ["stored_only", "Stored only"],
  ])("shows child status %s as text %s", async (status, label) => {
    api.getDocumentEnclosures.mockResolvedValue([CHILD_ENTRY]);
    api.getDocumentChildren.mockResolvedValue([child(status)]);
    renderPanel(60_000);
    await screen.findByText("site-photos.pdf");
    expect(within(rowFor("site-photos.pdf")).getByText(label)).toBeInTheDocument();
  });

  it("opens the existing review flow for a review-required child", async () => {
    api.getDocumentEnclosures.mockResolvedValue([CHILD_ENTRY]);
    api.getDocumentChildren.mockResolvedValue([child("human_review_required")]);
    renderPanel();
    await screen.findByText("Review required");
    fireEvent.click(screen.getByRole("link", { name: "Review site-photos.pdf" }));
    expect((await screen.findByTestId("location")).textContent).toBe(
      "/documentviewer/child-1?tab=review"
    );
  });

  it("renders an upload's child row from the response and keeps one row after a retry", async () => {
    const response = {
      enclosure: CHILD_ENTRY,
      message: "Enclosure added successfully",
      child_document_id: "child-1",
      child_created: true,
      relationship_type: "enclosure",
      version_id: "child-1",
      processing_job_id: "child-1",
      pipeline_version: "legacy_v0",
      processing_status: "queued",
    };
    api.addDocumentEnclosure.mockResolvedValue(response);
    // The refetch after upload returns the same entry the response carried.
    api.getDocumentEnclosures.mockResolvedValueOnce([]).mockResolvedValue([CHILD_ENTRY]);
    api.getDocumentChildren.mockResolvedValueOnce([]).mockResolvedValue([child("queued")]);
    const { container } = renderPanel(60_000);
    await screen.findByText(/No enclosures attached/);

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["%PDF-1.4"], "site-photos.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(await screen.findByText("site-photos.pdf")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();

    // An idempotent retry answers with the same entry ("Enclosure already attached").
    api.addDocumentEnclosure.mockResolvedValue({ ...response, child_created: false });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(api.addDocumentEnclosure).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByText("site-photos.pdf")).toHaveLength(1));
    expect(screen.queryByText("undefined")).toBeNull();
  });

  it("polls a child's status until it is terminal, then stops", async () => {
    api.getDocumentEnclosures.mockResolvedValue([CHILD_ENTRY]);
    api.getDocumentChildren
      .mockResolvedValueOnce([child("processing")])
      .mockResolvedValue([child("completed")]);
    renderPanel(20);
    expect(await screen.findByText("Processing")).toBeInTheDocument();
    expect(await screen.findByText("Completed")).toBeInTheDocument();
    const calls = api.getDocumentChildren.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(api.getDocumentChildren.mock.calls.length).toBe(calls);
  });

  it("shows an upload failure cleanly and keeps the list", async () => {
    api.getDocumentEnclosures.mockResolvedValue([LEGACY]);
    api.addDocumentEnclosure.mockRejectedValue(new Error("File type not allowed"));
    const { container } = renderPanel();
    await screen.findByText("old-drawing.pdf");
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["x"], "bad.exe", { type: "application/x-msdownload" })] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("File type not allowed");
    expect(screen.getByText("old-drawing.pdf")).toBeInTheDocument();
  });

  it("labels icon buttons and the dropzone, and truncates long names with a title", async () => {
    const longName = `${"very-long-enclosure-name-".repeat(6)}.pdf`;
    api.getDocumentEnclosures.mockResolvedValue([{ ...LEGACY, filename: longName }, CHILD_ENTRY]);
    api.getDocumentChildren.mockResolvedValue([child("completed")]);
    renderPanel();
    await screen.findByText("site-photos.pdf");
    expect(screen.getByRole("button", { name: "Refresh enclosures" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Delete enclosure ${longName}` })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upload enclosures/ })).toHaveAttribute("tabindex", "0");
    const name = screen.getByText(longName);
    expect(name).toHaveAttribute("title", longName);
    expect(name.className).toContain("truncate");
  });

  it("uses a stacked list, not a wide table, so a narrow panel does not overflow", async () => {
    api.getDocumentEnclosures.mockResolvedValue([LEGACY, CHILD_ENTRY]);
    api.getDocumentChildren.mockResolvedValue([child("completed")]);
    const { container } = renderPanel();
    await screen.findByText("site-photos.pdf");
    expect(container.querySelector("table")).toBeNull();
    for (const row of screen.getAllByRole("listitem")) {
      expect(row.className).toContain("min-w-0");
    }
  });
});
