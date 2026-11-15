import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ExtractionReviewQueuePage from "@/pages/ExtractionReviewQueuePage";

const reviewApi = vi.hoisted(() => ({ listExtractionReviewQueue: vi.fn() }));
vi.mock("@/services/extraction-review-api", () => reviewApi);

function Where() {
  const location = useLocation();
  return <div data-testid="where">{`${location.pathname}${location.search}`}</div>;
}

function renderQueue() {
  return render(
    <MemoryRouter initialEntries={["/extraction-reviews"]}>
      <Routes>
        <Route path="/extraction-reviews" element={<ExtractionReviewQueuePage />} />
        <Route path="/documentviewer/:id" element={<Where />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("ExtractionReviewQueuePage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists review-required documents and opens the viewer at the first flagged page", async () => {
    reviewApi.listExtractionReviewQueue.mockResolvedValue({
      total: 1, skip: 0, limit: 100,
      items: [{
        document_id: "doc-4299", filename: "GLM-4299.pdf", subject: "Guide wall rework",
        letter_no: "GLM-SAM-KNPCC-05-UPMRC-OL-2024-4299", project_id: "proj-1",
        processing_status: "human_review_required", review_pages: [3, 5], first_review_page: 3,
        review_reason: "2 page(s) need review (failed the extraction quality check)",
        uploaded_at: "2026-10-12T18:17:00Z", updated_at: "2026-10-12T18:18:00Z",
      }],
    });
    renderQueue();
    expect(await screen.findByText("GLM-SAM-KNPCC-05-UPMRC-OL-2024-4299")).toBeInTheDocument();
    expect(screen.getByText("2 (3, 5)")).toBeInTheDocument();
    expect(screen.getByText(/failed the extraction quality check/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/documentviewer/doc-4299?tab=review&page=3");
  });

  it("shows an API failure instead of an empty queue", async () => {
    reviewApi.listExtractionReviewQueue.mockRejectedValue(new Error("Request failed with status code 503"));
    renderQueue();
    expect(await screen.findByText("Request failed with status code 503")).toBeInTheDocument();
    expect(screen.queryByText("No documents need extraction review.")).toBeNull();
  });

  it("says so when nothing needs review", async () => {
    reviewApi.listExtractionReviewQueue.mockResolvedValue({ total: 0, skip: 0, limit: 100, items: [] });
    renderQueue();
    expect(await screen.findByText("No documents need extraction review.")).toBeInTheDocument();
  });
});
