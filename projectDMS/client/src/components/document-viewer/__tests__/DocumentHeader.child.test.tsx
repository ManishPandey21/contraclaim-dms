import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import DocumentHeader from "@/components/document-viewer/DocumentHeader";

vi.mock("@/services/http", () => ({ fetchApiFileBlob: vi.fn(), authenticatedFetch: vi.fn() }));
vi.mock("@/services/email-service", () => ({ emailService: {} }));

function renderHeader(document: Record<string, unknown>) {
  return render(
    <MemoryRouter>
      <DocumentHeader
        document={document as any}
        showMetadata
        setShowMetadata={() => {}}
        isShareDialogOpen={false}
        setIsShareDialogOpen={() => {}}
        isLinkReferenceDialogOpen={false}
        setIsLinkReferenceDialogOpen={() => {}}
        relatedDocuments={[]}
      />
    </MemoryRouter>
  );
}

describe("DocumentHeader identity badge", () => {
  it("labels a child by its relationship, never as an incoming/outgoing letter", () => {
    renderHeader({
      _id: "child-1",
      filename: "site-photos.pdf",
      status: "Draft",
      // The viewer normalises a missing direction to "outgoing"; a child must not show it.
      uploadType: "outgoing",
      parent_document_id: "parent-1",
      relationship_type: "enclosure",
    });
    expect(screen.getByText("Enclosure")).toBeInTheDocument();
    expect(screen.queryByText("outgoing")).toBeNull();
    expect(screen.queryByText("incoming")).toBeNull();
  });

  it("keeps the direction badge for a top-level letter", () => {
    renderHeader({ _id: "doc-1", filename: "GLM-4299.pdf", status: "Draft", uploadType: "incoming" });
    expect(screen.getByText("incoming")).toBeInTheDocument();
  });
});
