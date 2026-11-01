import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type React from "react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EntityDocumentLinks from "@/components/document-links/EntityDocumentLinks";

const relationshipApi = vi.hoisted(() => ({
  listEntityDocumentLinks: vi.fn(), searchLinkableDocuments: vi.fn(),
  batchLinkDocuments: vi.fn(), removeDocumentLink: vi.fn(),
}));
const toastApi = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("@/services/document-relationships-api", () => relationshipApi);
vi.mock("sonner", () => ({ toast: toastApi }));

const roles = [{ value: "notice", label: "Notice" }];
const renderLinks = (props: Partial<React.ComponentProps<typeof EntityDocumentLinks>> = {}) => render(
  <MemoryRouter>
    <EntityDocumentLinks targetType="claim" targetId="claim-1" organizationId="org-1"
      projectId="project-1" roles={roles} defaultRole="notice" canManage {...props} />
  </MemoryRouter>,
);

describe("EntityDocumentLinks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([]);
    relationshipApi.searchLinkableDocuments.mockResolvedValue([
      { _id: "doc-1", filename: "One.pdf" }, { _id: "doc-2", filename: "Two.pdf" },
    ]);
    relationshipApi.batchLinkDocuments.mockResolvedValue([
      { _id: "link-1", document_id: "doc-1", relationship_role: "notice", _revision: 1,
        document: { _id: "doc-1", filename: "One.pdf" } },
      { _id: "link-2", document_id: "doc-2", relationship_role: "notice", _revision: 1,
        document: { _id: "doc-2", filename: "Two.pdf" } },
    ]);
  });

  it("batch-links multiple selected Documents with one request", async () => {
    const user = userEvent.setup();
    renderLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "pdf");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByLabelText("Select One.pdf"));
    await user.click(screen.getByLabelText("Select Two.pdf"));
    await user.click(screen.getByRole("button", { name: "Link selected (2)" }));
    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith(
      "claim", "claim-1", [
        { document_id: "doc-1", relationship_role: "notice" },
        { document_id: "doc-2", relationship_role: "notice" },
      ],
    ));
  });

  it("renders view-only and frozen controls without mutation actions", async () => {
    const { rerender } = renderLinks({ canManage: false });
    expect(await screen.findByText(/view-only access to document links/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Search Documents")).not.toBeInTheDocument();
    rerender(<MemoryRouter><EntityDocumentLinks targetType="claim" targetId="claim-1"
      roles={roles} defaultRole="notice" canManage frozen /></MemoryRouter>);
    expect(await screen.findByText("Evidence is frozen for this record.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Search Documents")).not.toBeInTheDocument();
  });

  it("marks already-linked search results and prevents duplicate selection", async () => {
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([
      { _id: "link-1", document_id: "doc-1", relationship_role: "notice", _revision: 1,
        document: { _id: "doc-1", filename: "One.pdf" } },
    ]);
    const user = userEvent.setup();
    renderLinks();
    await screen.findByText("One.pdf");
    await user.type(screen.getByLabelText("Search Documents"), "pdf");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Already linked")).toBeInTheDocument();
    expect(screen.getByLabelText("Select One.pdf")).toBeDisabled();
  });

  it("ignores a stale link response after the target changes", async () => {
    let resolveLink: (value: unknown[]) => void = () => undefined;
    relationshipApi.batchLinkDocuments.mockReturnValue(
      new Promise((resolve) => { resolveLink = resolve; }),
    );
    const user = userEvent.setup();
    const rendered = renderLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "pdf");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: "Link One.pdf" }));
    rendered.rerender(
      <MemoryRouter><EntityDocumentLinks targetType="claim" targetId="claim-2"
        roles={roles} defaultRole="notice" canManage /></MemoryRouter>,
    );
    resolveLink([{ _id: "stale", document_id: "doc-1", relationship_role: "notice", _revision: 1,
      document: { _id: "doc-1", filename: "One.pdf" } }]);
    await waitFor(() => expect(relationshipApi.listEntityDocumentLinks).toHaveBeenCalledWith("claim", "claim-2"));
    expect(screen.queryByText("One.pdf")).not.toBeInTheDocument();
  });

  it("loads the next search page with a server-derived offset", async () => {
    const firstPage = Array.from({ length: 25 }, (_, index) => ({
      _id: `doc-${index}`, filename: `Page ${index}.pdf`,
    }));
    relationshipApi.searchLinkableDocuments
      .mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce([{ _id: "doc-25", filename: "Page 25.pdf" }]);
    const user = userEvent.setup();
    renderLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "page");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: "Load more" }));
    await waitFor(() => expect(relationshipApi.searchLinkableDocuments).toHaveBeenLastCalledWith({
      q: "page", organization_id: "org-1", project_id: "project-1", limit: 25, skip: 25,
    }));
    expect(await screen.findByText("Page 25.pdf")).toBeInTheDocument();
  });

  it("ignores an out-of-order list response from the previous target", async () => {
    let resolveOld: (value: unknown[]) => void = () => undefined;
    relationshipApi.listEntityDocumentLinks
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce([]);
    const rendered = renderLinks();
    rendered.rerender(
      <MemoryRouter><EntityDocumentLinks targetType="claim" targetId="claim-2"
        roles={roles} defaultRole="notice" canManage /></MemoryRouter>,
    );
    await waitFor(() => expect(relationshipApi.listEntityDocumentLinks).toHaveBeenCalledWith("claim", "claim-2"));
    resolveOld([{ _id: "old-link", document_id: "old-doc", relationship_role: "notice", _revision: 1,
      document: { _id: "old-doc", filename: "Old Claim.pdf" } }]);
    await screen.findByText("No linked Documents yet.");
    expect(screen.queryByText("Old Claim.pdf")).not.toBeInTheDocument();
  });

  it("ignores an out-of-order search response from an older query", async () => {
    let resolveOld: (value: unknown[]) => void = () => undefined;
    relationshipApi.searchLinkableDocuments
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce([{ _id: "new-doc", filename: "New result.pdf" }]);
    const user = userEvent.setup();
    renderLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "old");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.clear(screen.getByLabelText("Search Documents"));
    await user.type(screen.getByLabelText("Search Documents"), "new");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("New result.pdf")).toBeInTheDocument();
    resolveOld([{ _id: "old-doc", filename: "Old result.pdf" }]);
    await waitFor(() => expect(screen.queryByText("Old result.pdf")).not.toBeInTheDocument());
    expect(screen.getByText("New result.pdf")).toBeInTheDocument();
  });

  it("does not repopulate results when a pending query is cleared", async () => {
    let resolveSearch: (value: unknown[]) => void = () => undefined;
    relationshipApi.searchLinkableDocuments.mockReturnValue(
      new Promise((resolve) => { resolveSearch = resolve; }),
    );
    const user = userEvent.setup();
    renderLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "old");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.clear(screen.getByLabelText("Search Documents"));
    resolveSearch([{ _id: "old-doc", filename: "Old result.pdf" }]);
    await waitFor(() => expect(screen.queryByText("Old result.pdf")).not.toBeInTheDocument());
  });

  it("clears stale pagination when the query is cleared", async () => {
    relationshipApi.searchLinkableDocuments.mockResolvedValueOnce(
      Array.from({ length: 25 }, (_, index) => ({
        _id: `doc-${index}`, filename: `Result ${index}.pdf`,
      })),
    );
    const user = userEvent.setup();
    renderLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "result");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByRole("button", { name: "Load more" })).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Search Documents"));
    await waitFor(() => expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument());
  });

  it("does not toast an obsolete search failure after a newer query succeeds", async () => {
    let rejectOld: (reason: Error) => void = () => undefined;
    relationshipApi.searchLinkableDocuments
      .mockReturnValueOnce(new Promise((_, reject) => { rejectOld = reject; }))
      .mockResolvedValueOnce([{ _id: "new-doc", filename: "New result.pdf" }]);
    const user = userEvent.setup();
    renderLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "old");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.clear(screen.getByLabelText("Search Documents"));
    await user.type(screen.getByLabelText("Search Documents"), "new");
    await screen.findByText("New result.pdf");
    rejectOld(new Error("obsolete"));
    await waitFor(() => expect(screen.getByText("New result.pdf")).toBeInTheDocument());
    expect(toastApi.error).not.toHaveBeenCalled();
  });

  it("reloads canonical links after a failed link conflict", async () => {
    relationshipApi.batchLinkDocuments.mockRejectedValueOnce(new Error("conflict"));
    const user = userEvent.setup();
    renderLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "pdf");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: "Link One.pdf" }));

    await waitFor(() => expect(toastApi.error).toHaveBeenCalledWith("Failed to link Documents"));
    expect(relationshipApi.listEntityDocumentLinks).toHaveBeenCalledTimes(2);
  });

  it("shows a retry control when loading linked Documents fails", async () => {
    relationshipApi.listEntityDocumentLinks
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderLinks();

    expect(await screen.findByText("Linked Documents could not be loaded.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry linked Documents" }));
    expect(await screen.findByText("No linked Documents yet.")).toBeInTheDocument();
    expect(relationshipApi.listEntityDocumentLinks).toHaveBeenCalledTimes(2);
  });

  it("shows useful search metadata and a direct Document view action", async () => {
    relationshipApi.searchLinkableDocuments.mockResolvedValueOnce([
      { _id: "doc-1", filename: "One.pdf", status: "approved", categories: ["invoice"],
        createdAt: "2026-08-20T00:00:00Z" },
    ]);
    const user = userEvent.setup();
    renderLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "one");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText(/approved.*invoice.*20 Aug 2026/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View One.pdf" })).toHaveAttribute(
      "href", "/documentviewer/doc-1",
    );
  });

  it("unlinks one supporter with revision and reload-safe reason", async () => {
    relationshipApi.listEntityDocumentLinks.mockResolvedValueOnce([
      { _id: "link-1", document_id: "doc-1", relationship_role: "notice", _revision: 3,
        document: { _id: "doc-1", filename: "One.pdf" } },
      { _id: "link-2", document_id: "doc-2", relationship_role: "notice", _revision: 1,
        document: { _id: "doc-2", filename: "Two.pdf" } },
    ]);
    relationshipApi.removeDocumentLink.mockResolvedValueOnce({});
    const user = userEvent.setup();
    renderLinks();
    await user.click(await screen.findByRole("button", { name: "Unlink One.pdf" }));

    await waitFor(() => expect(relationshipApi.removeDocumentLink).toHaveBeenCalledWith(
      "link-1", 3, "Removed from Claim",
    ));
    expect(screen.queryByText("One.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("Two.pdf")).toBeInTheDocument();
  });
});
