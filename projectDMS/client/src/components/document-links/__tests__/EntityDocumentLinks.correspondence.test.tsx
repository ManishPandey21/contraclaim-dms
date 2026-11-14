import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type React from "react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EntityDocumentLinks from "@/components/document-links/EntityDocumentLinks";
import { VARIATION_DOCUMENT_RELATIONSHIP_ROLES } from "@/services/document-relationships-api";

const relationshipApi = vi.hoisted(() => ({
  listEntityDocumentLinks: vi.fn(), searchLinkableDocuments: vi.fn(),
  batchLinkDocuments: vi.fn(), removeDocumentLink: vi.fn(),
}));
const toastApi = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("@/services/document-relationships-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/document-relationships-api")>()),
  ...relationshipApi,
}));
vi.mock("sonner", () => ({ toast: toastApi }));

/** A Document as `/document-search` returns it (Document model fields). */
const incomingLetter = {
  _id: "65f0c0ffee0000000000aa01", organization_id: "org-1", project_id: "project-1",
  filename: "ENG-VO-014.pdf", subject: "Instruction on added works", letterNo: "ENG/VO/014",
  date: "2026-09-01T00:00:00", from: "Engineer", to: "Contractor", uploadType: "incoming",
  status: "active",
};

/** A DocumentRelationshipView as `/entities/.../document-links` returns it. */
const linkRow = {
  _id: "link-1", organization_id: "org-1", project_id: "project-1", target_type: "variation",
  target_id: "var-1", document_id: incomingLetter._id, relationship_role: "correspondence",
  source: "user", _revision: 1, removed_at: null, frozen_at: null, document: incomingLetter,
  target_label: "VO-001", target_route: "/variations?variation_id=var-1",
};

const renderVariationLinks = (props: Partial<React.ComponentProps<typeof EntityDocumentLinks>> = {}) => render(
  <MemoryRouter>
    <EntityDocumentLinks targetType="variation" targetId="var-1" organizationId="org-1"
      projectId="project-1" roles={VARIATION_DOCUMENT_RELATIONSHIP_ROLES} defaultRole="correspondence"
      canManage {...props} />
  </MemoryRouter>,
);

const lastSearch = () => relationshipApi.searchLinkableDocuments.mock.calls.at(-1)?.[0];

describe("EntityDocumentLinks correspondence selector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([]);
    relationshipApi.searchLinkableDocuments.mockResolvedValue([incomingLetter]);
    relationshipApi.batchLinkDocuments.mockResolvedValue([linkRow]);
    relationshipApi.removeDocumentLink.mockResolvedValue({ ...linkRow, removed_at: "2026-09-22T00:00:00" });
  });

  it("sends uploadType=correspondence for the correspondence role and offers no contract option", async () => {
    const user = userEvent.setup();
    renderVariationLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "ENG");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(lastSearch()).toEqual({
      q: "ENG", organization_id: "org-1", project_id: "project-1",
      uploadType: "correspondence", limit: 25, skip: 0,
    }));
    const direction = screen.getByLabelText("Direction");
    expect(within(direction).queryByRole("option", { name: "Contract" })).not.toBeInTheDocument();
    expect(screen.getByText(/only incoming and outgoing correspondence/i)).toBeInTheDocument();
  });

  it("narrows to one direction and forwards letter number and date filters", async () => {
    const user = userEvent.setup();
    renderVariationLinks();
    await screen.findByText("No linked Documents yet.");
    await user.selectOptions(screen.getByLabelText("Direction"), "outgoing");
    await user.type(screen.getByLabelText("Letter number"), "CON/VO/022");
    await user.type(screen.getByLabelText("Subject"), "quotation");
    fireEvent.change(screen.getByLabelText("Date from"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("Date to"), { target: { value: "2026-09-30" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    // A filter alone is a search: no free text is needed.
    await waitFor(() => expect(lastSearch()).toEqual({
      organization_id: "org-1", project_id: "project-1", uploadType: "outgoing",
      letterNo: "CON/VO/022", subject: "quotation", date_from: "2026-09-01", date_to: "2026-09-30", limit: 25, skip: 0,
    }));
  });

  it("does not over-filter supporting documents: no uploadType and contract selectable", async () => {
    const user = userEvent.setup();
    renderVariationLinks({ defaultRole: "supporting_document" });
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "conditions");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(lastSearch()).toEqual({
      q: "conditions", organization_id: "org-1", project_id: "project-1", limit: 25, skip: 0,
    }));
    await user.selectOptions(screen.getByLabelText("Direction"), "contract");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(lastSearch()?.uploadType).toBe("contract"));
  });

  it("switching to a correspondence role drops a contract direction", async () => {
    const user = userEvent.setup();
    renderVariationLinks({ defaultRole: "supporting_document" });
    await screen.findByText("No linked Documents yet.");
    await user.selectOptions(screen.getByLabelText("Direction"), "contract");
    await user.selectOptions(screen.getByLabelText("Relationship role"), "correspondence");
    await user.type(screen.getByLabelText("Search Documents"), "x");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(lastSearch()?.uploadType).toBe("correspondence"));
  });

  it("shows letter number, subject, date, direction and party for a result", async () => {
    const user = userEvent.setup();
    renderVariationLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "ENG");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    const result = await screen.findByTestId("document-search-result");
    expect(result).toHaveTextContent("ENG/VO/014");
    expect(result).toHaveTextContent("Instruction on added works");
    expect(result).toHaveTextContent("Incoming");
    expect(result).toHaveTextContent("Engineer → Contractor");
    expect(result).toHaveTextContent("1 Sept 2026");
  });

  it("links, renders the linked letter with its role and an open link, then unlinks", async () => {
    const user = userEvent.setup();
    renderVariationLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "ENG");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: "Link ENG-VO-014.pdf" }));

    expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith("variation", "var-1", [
      { document_id: incomingLetter._id, relationship_role: "correspondence" },
    ]);
    const row = await screen.findByTestId("linked-document");
    expect(row).toHaveTextContent("Correspondence");
    expect(within(row).getByRole("link")).toHaveAttribute("href", `/documentviewer/${incomingLetter._id}`);

    await user.click(within(row).getByRole("button", { name: "Unlink ENG-VO-014.pdf" }));
    await waitFor(() => expect(relationshipApi.removeDocumentLink).toHaveBeenCalledWith(
      "link-1", 1, "Removed from Variation",
    ));
    expect(await screen.findByText("No linked Documents yet.")).toBeInTheDocument();
  });

  it("renders several linked letters", async () => {
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([
      linkRow,
      { ...linkRow, _id: "link-2", document_id: "doc-out", relationship_role: "variation_submission",
        document: { ...incomingLetter, _id: "doc-out", filename: "CON-VO-022.pdf", uploadType: "outgoing" } },
    ]);
    renderVariationLinks();
    const rows = await screen.findAllByTestId("linked-document");
    expect(rows).toHaveLength(2);
    expect(rows[1]).toHaveTextContent("Variation submission");
    expect(rows[1]).toHaveTextContent("Outgoing");
  });

  it("rolls back to the server state when unlink fails", async () => {
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([linkRow]);
    relationshipApi.removeDocumentLink.mockRejectedValueOnce({ response: { status: 409 } });
    const user = userEvent.setup();
    renderVariationLinks();
    const row = await screen.findByTestId("linked-document");
    await user.click(within(row).getByRole("button", { name: "Unlink ENG-VO-014.pdf" }));
    await waitFor(() => expect(toastApi.error).toHaveBeenCalledWith(
      "Failed to unlink Document; reload before retrying",
    ));
    expect(relationshipApi.listEntityDocumentLinks).toHaveBeenCalledTimes(2);
    expect(await screen.findByTestId("linked-document")).toBeInTheDocument();
  });

  it("rolls back to the server state when link fails", async () => {
    relationshipApi.batchLinkDocuments.mockRejectedValueOnce({ response: { status: 422 } });
    const user = userEvent.setup();
    renderVariationLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "ENG");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: "Link ENG-VO-014.pdf" }));
    await waitFor(() => expect(toastApi.error).toHaveBeenCalledWith("Failed to link Documents"));
    expect(relationshipApi.listEntityDocumentLinks).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId("linked-document")).not.toBeInTheDocument();
  });

  it("view-only users see links but no link or unlink controls", async () => {
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([linkRow]);
    renderVariationLinks({ canManage: false });
    const row = await screen.findByTestId("linked-document");
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Search Documents")).not.toBeInTheDocument();
    expect(screen.getByText(/view-only access/i)).toBeInTheDocument();
  });
});

/**
 * jsdom has no layout, so these pin the width contract the browser regression
 * (e2e/correspondence-linking.spec.ts) measures: nothing in the picker may take
 * its width from its content. A long filename once set the width of the
 * Variation dialog's grid track and spilled past the dialog and the viewport.
 */
describe("EntityDocumentLinks width contract", () => {
  const longName =
    "Kanpur-LET-JVTI-CPM-00550-E01-Variation statement no.01 for the Utility (Sewer & GRP Water Pipe line).pdf";
  const longLetter = { ...incomingLetter, filename: longName, letterNo: "Kanpur-LET-JVTI-CPM-00550-E01" };

  beforeEach(() => {
    vi.clearAllMocks();
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([{ ...linkRow, document: longLetter }]);
    relationshipApi.searchLinkableDocuments.mockResolvedValue([longLetter]);
  });

  it("lets its grid or flex parent shrink it below its content width", async () => {
    const { container } = renderVariationLinks();
    await screen.findByTestId("linked-document");
    expect(container.firstElementChild).toHaveClass("min-w-0");
  });

  it("clamps a long linked filename and keeps the full name reachable", async () => {
    renderVariationLinks();
    const row = await screen.findByTestId("linked-document");
    const link = within(row).getByRole("link");
    expect(link).toHaveAttribute("title", longName);
    expect(link).toHaveClass("min-w-0", "flex-1");
    const name = within(row).getByText(longName);
    expect(name).toHaveClass("line-clamp-2");
    expect(name.className).toContain("[overflow-wrap:anywhere]");
    expect(within(row).getByRole("button", { name: `Unlink ${longName}` })).toBeInTheDocument();
  });

  it("lays the filters out by available width, with every control able to shrink", async () => {
    renderVariationLinks();
    await screen.findByTestId("linked-document");
    const controls = ["Direction", "Letter number", "Subject", "Date from", "Date to"]
      .map((label) => screen.getByLabelText(label));
    const grid = controls[0].closest("label")?.parentElement;
    expect(grid?.className).toContain("grid-cols-[repeat(auto-fit,minmax(min(100%,10rem),1fr))]");
    // Viewport breakpoints would size the grid for the page, not for the dialog.
    expect(grid?.className).not.toMatch(/\b(sm|md|lg):grid-cols-/);
    for (const control of controls) {
      expect(control.parentElement).toBe(control.closest("label"));
      expect(control.closest("label")?.parentElement).toBe(grid);
      expect(control).toHaveClass("w-full", "min-w-0");
      expect(control.closest("label")).toHaveClass("min-w-0");
    }
    expect(screen.getByLabelText("Search Documents")).toHaveClass("min-w-0", "flex-1");
    expect(screen.getByRole("button", { name: "Search" })).toHaveClass("shrink-0");
  });

  it("keeps a long search result's actions beside a clamped name and links it", async () => {
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([]);
    relationshipApi.batchLinkDocuments.mockResolvedValue([{ ...linkRow, document: longLetter }]);
    const user = userEvent.setup();
    renderVariationLinks();
    await screen.findByText("No linked Documents yet.");
    await user.type(screen.getByLabelText("Search Documents"), "Kanpur");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    const result = await screen.findByTestId("document-search-result");
    expect(result).toHaveClass("min-w-0");
    const name = within(result).getByText(longName);
    expect(name).toHaveClass("line-clamp-2");
    expect(name.closest("label")).toHaveAttribute("title", longName);
    expect(name.closest("label")).toHaveClass("min-w-0", "flex-1");
    const linkButton = within(result).getByRole("button", { name: `Link ${longName}` });
    expect(linkButton.parentElement).toHaveClass("shrink-0");

    await user.click(linkButton);
    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith(
      "variation", "var-1", [{ document_id: longLetter._id, relationship_role: "correspondence" }],
    ));
    expect(await screen.findByTestId("linked-document")).toHaveTextContent(longName);
  });
});
