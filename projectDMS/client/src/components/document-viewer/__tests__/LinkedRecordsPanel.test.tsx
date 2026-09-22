import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LinkedRecordsPanel from "@/components/document-viewer/LinkedRecordsPanel";

const relationshipApi = vi.hoisted(() => ({ listDocumentEntityLinks: vi.fn() }));
vi.mock("@/services/document-relationships-api", () => relationshipApi);

describe("LinkedRecordsPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders authorized reverse links", async () => {
    relationshipApi.listDocumentEntityLinks.mockResolvedValue([
      {
        _id: "link-1", target_type: "claim", target_id: "claim-1",
        target_label: "CLM-001", target_route: "/claims/claim-1",
        relationship_role: "notice",
      },
    ]);
    render(<MemoryRouter><LinkedRecordsPanel documentId="doc-1" /></MemoryRouter>);
    expect(await screen.findByText("CLM-001")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/claims/claim-1");
  });

  it("names a Hindrance & Constraint Register entry and links to it", async () => {
    relationshipApi.listDocumentEntityLinks.mockResolvedValue([
      {
        _id: "link-2", target_type: "delay_event", target_id: "h-1",
        target_label: "HIN-0001", target_route: "/hindrances/h-1",
        relationship_role: "site_record",
      },
    ]);
    render(<MemoryRouter><LinkedRecordsPanel documentId="doc-1" /></MemoryRouter>);
    expect(await screen.findByText("HIN-0001")).toBeInTheDocument();
    // CL-3A: labels come from the shared lib/relationship-roles helpers.
    expect(screen.getByText(/Hindrance \/ Constraint · Site record/)).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/hindrances/h-1");
  });

  it("fails visibly and retries instead of claiming there are no links", async () => {
    relationshipApi.listDocumentEntityLinks
      .mockRejectedValueOnce(new Error("denied"))
      .mockResolvedValueOnce([]);
    render(<MemoryRouter><LinkedRecordsPanel documentId="doc-1" /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.queryByText("No linked records.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(relationshipApi.listDocumentEntityLinks).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("No linked records.")).toBeInTheDocument();
  });
});
