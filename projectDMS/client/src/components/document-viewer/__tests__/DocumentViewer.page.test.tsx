import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import DocumentViewer from "@/components/document-viewer/DocumentViewer";

const SIGNED = "https://bucket.s3.amazonaws.com/doc.pdf?X-Amz-Signature=abc";

vi.mock("@/services/http", () => ({ fetchApiFileBlob: vi.fn() }));

function renderAt(page?: number | null) {
  const document = { id: "doc-1", filename: "GLM-4299.pdf", filetype: "application/pdf", presigned_url: SIGNED };
  return render(
    <MemoryRouter initialEntries={["/documentviewer/doc-1"]}>
      <Routes>
        <Route
          path="/documentviewer/:id"
          element={<DocumentViewer document={document as any} page={page} />}
        />
      </Routes>
    </MemoryRouter>
  );
}

describe("DocumentViewer page deep link", () => {
  it("opens the native viewer at the requested page", async () => {
    renderAt(5);
    const frame = await screen.findByTitle("GLM-4299.pdf");
    expect(frame.getAttribute("src")).toBe(`${SIGNED}#page=5&toolbar=1&navpanes=0&view=FitH`);
  });

  it("keeps the previous fragment when no page is requested", async () => {
    renderAt(null);
    const frame = await screen.findByTitle("GLM-4299.pdf");
    expect(frame.getAttribute("src")).toBe(`${SIGNED}#toolbar=1&navpanes=0&view=FitH`);
  });
});
