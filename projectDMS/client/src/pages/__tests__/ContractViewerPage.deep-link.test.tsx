import { render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * CL-2 item 10: the Contract Document relationship target deep-links to
 * `/contracts/viewer/{document_id}` (ContractDocumentEntityAdapter._route). This
 * pins that the viewer consumes that route id - it loads exactly that Document's
 * metadata and file - rather than ignoring it.
 */

const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  getDocument: vi.fn(),
  authenticatedFetch: vi.fn(),
}));

vi.mock("@/services/api", () => ({ api: { get: mocks.apiGet, post: vi.fn() } }));
vi.mock("@/services/enhanced-api", () => ({ enhancedApi: { getDocument: mocks.getDocument } }));
vi.mock("@/services/http", () => ({ authenticatedFetch: mocks.authenticatedFetch }));
vi.mock("@/services/contracts-api", () => ({ reindexContract: vi.fn() }));
vi.mock("@/components/contracts/ClauseIndexTab", () => ({ default: () => null }));

import ContractViewerPage from "@/pages/ContractViewerPage";

const DOCUMENT_ID = "65f0c0ffee0000000000cd01";

describe("ContractViewerPage deep link", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.apiGet.mockResolvedValue({ data: [] });
    mocks.getDocument.mockResolvedValue({ _id: DOCUMENT_ID, filename: "Particular Conditions.pdf" });
    mocks.authenticatedFetch.mockResolvedValue({
      ok: true, status: 200, blob: async () => new Blob(["%PDF-1.7"], { type: "application/pdf" }),
      text: async () => "",
    });
    if (!URL.createObjectURL) {
      (URL as unknown as { createObjectURL: () => string }).createObjectURL = () => "blob:contract";
    }
    if (!URL.revokeObjectURL) {
      (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => undefined;
    }
  });

  it("loads the Document named in /contracts/viewer/{document_id}", async () => {
    render(
      <MemoryRouter initialEntries={[`/contracts/viewer/${DOCUMENT_ID}`]}>
        <Routes><Route path="/contracts/viewer/:id" element={<ContractViewerPage />} /></Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(mocks.getDocument).toHaveBeenCalledWith(DOCUMENT_ID));
    await waitFor(() => expect(mocks.authenticatedFetch).toHaveBeenCalledWith(
      expect.stringContaining(`/contracts/${DOCUMENT_ID}/download`),
    ));
  });
});
