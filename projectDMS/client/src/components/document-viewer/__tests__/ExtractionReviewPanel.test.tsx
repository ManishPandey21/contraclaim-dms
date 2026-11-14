import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ExtractionReviewPanel from "@/components/document-viewer/ExtractionReviewPanel";
import type { ExtractionReview } from "@/services/extraction-review-api";

const reviewApi = vi.hoisted(() => ({ getExtractionReview: vi.fn(), decideExtractionPage: vi.fn() }));
const toastApi = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("@/services/extraction-review-api", () => reviewApi);
vi.mock("sonner", () => ({ toast: toastApi }));

const DOC = "65f0c0ffee0000000000bb05";

/** The GET /documents/{id}/extraction-review shape for the 4299 page-5 case. */
function review(overrides: Partial<ExtractionReview> = {}): ExtractionReview {
  return {
    document_id: DOC,
    filename: "GLM-4299.pdf",
    processing_status: "human_review_required",
    evidence_status: "published",
    can_review: true,
    review_state: "required",
    reviewable: true,
    extraction_run_id: "job-1",
    canonical_revision: 1,
    canonical_sha256: "c".repeat(64),
    blocking_pages: [5],
    unresolved_pages: [5],
    pages: [
      {
        page_number: 5,
        in_evidence: true,
        source: "ocr",
        status: "ocr_completed",
        page_class: "table_heavy",
        machine_quality_verdict: "fail",
        machine_needs_review: true,
        machine_failed_checks: [{ name: "numeric_integrity", verdict: "fail", detail: "row totals not checkable" }],
        unresolved: false,
        text_withheld: false,
        error: null,
        table_count: 1,
        tables: [[["Rework", "19,292,171"]]],
        page_checksum: "p".repeat(64),
        char_count: 40,
        text: "Guide Wall & D-wall Reworks Cost 19,292,171",
        approvable: true,
        decision: null,
      },
    ],
    ...overrides,
  };
}

function renderPanel(onShowPage = vi.fn(), onDecided = vi.fn(), currentPage: number | null = null) {
  return render(
    <ExtractionReviewPanel documentId={DOC} currentPage={currentPage} onShowPage={onShowPage} onDecided={onDecided} />
  );
}

describe("ExtractionReviewPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the machine verdict, reason, extracted text and tables of the flagged page", async () => {
    reviewApi.getExtractionReview.mockResolvedValue(review());
    renderPanel();
    expect(await screen.findByText("Page 5")).toBeInTheDocument();
    expect(screen.getByText("fail")).toBeInTheDocument();
    expect(screen.getByText(/numeric_integrity/)).toBeInTheDocument();
    expect(screen.getByLabelText("Extracted text of page 5")).toHaveTextContent("19,292,171");
    expect(screen.getByText(/Extracted tables \(1\)/)).toBeInTheDocument();
  });

  it("will not approve before the source page has been opened in the viewer", async () => {
    reviewApi.getExtractionReview.mockResolvedValue(review());
    const onShowPage = vi.fn();
    renderPanel(onShowPage);
    const approve = await screen.findByRole("button", { name: /Approve as-is/ });
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Show source page/ }));
    expect(onShowPage).toHaveBeenCalledWith(5);
  });

  it("approves with the revision and checksum the reviewer saw, then refreshes", async () => {
    reviewApi.getExtractionReview.mockResolvedValue(review());
    reviewApi.decideExtractionPage.mockResolvedValue({
      decision: { action: "approved_as_is" },
      idempotent: false,
      resolution: { resolved: true, state: "publication_pending" },
    });
    const onDecided = vi.fn();
    renderPanel(vi.fn(), onDecided, 5);
    const approve = await screen.findByRole("button", { name: /Approve as-is/ });
    await waitFor(() => expect(approve).toBeEnabled());
    await userEvent.type(screen.getByLabelText("Review comment for page 5"), "Matches the source");
    await userEvent.click(approve);
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    const [documentId, page, action, sent, comment] = reviewApi.decideExtractionPage.mock.calls[0];
    expect([documentId, page.page_number, action, sent.canonical_revision, comment]).toEqual([
      DOC, 5, "approve", 1, "Matches the source",
    ]);
    expect(toastApi.success).toHaveBeenCalledWith(expect.stringMatching(/being published/));
  });

  it("requires a comment to reject", async () => {
    reviewApi.getExtractionReview.mockResolvedValue(review());
    renderPanel(vi.fn(), vi.fn(), 5);
    const reject = await screen.findByRole("button", { name: /Reject/ });
    expect(reject).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Review comment for page 5"), "Totals do not match");
    expect(reject).toBeEnabled();
  });

  it("shows the server's refusal (stale evidence) instead of claiming success", async () => {
    reviewApi.getExtractionReview.mockResolvedValue(review());
    reviewApi.decideExtractionPage.mockRejectedValue({
      response: { status: 409, data: { detail: { message: "The document was reprocessed since this page was loaded" } } },
    });
    const onDecided = vi.fn();
    renderPanel(vi.fn(), onDecided, 5);
    const approve = await screen.findByRole("button", { name: /Approve as-is/ });
    await waitFor(() => expect(approve).toBeEnabled());
    await userEvent.click(approve);
    await waitFor(() =>
      expect(toastApi.error).toHaveBeenCalledWith("Review not saved", {
        description: "The document was reprocessed since this page was loaded",
      })
    );
    expect(onDecided).not.toHaveBeenCalled();
    expect(toastApi.success).not.toHaveBeenCalled();
  });

  it("offers no decision buttons to a viewer without the review permission", async () => {
    reviewApi.getExtractionReview.mockResolvedValue(review({ can_review: false }));
    renderPanel(vi.fn(), vi.fn(), 5);
    expect(await screen.findByText(/can view this review but not decide it/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve as-is/ })).toBeNull();
  });

  it("does not offer approval of a page with nothing extracted", async () => {
    const base = review();
    base.pages[0] = { ...base.pages[0], unresolved: true, approvable: false, text: "", char_count: 0 };
    reviewApi.getExtractionReview.mockResolvedValue(base);
    renderPanel(vi.fn(), vi.fn(), 5);
    expect(await screen.findByText(/cannot be\s+approved as-is/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve as-is/ })).toBeDisabled();
  });

  it("reports a resolved review and its decision without action buttons", async () => {
    const base = review({ review_state: "resolved", reviewable: false, processing_status: "completed" });
    base.pages[0] = {
      ...base.pages[0],
      decision: {
        id: "d1", page_number: 5, action: "approved_as_is", decision_version: 1, canonical_revision: 1,
        page_checksum: "p".repeat(64), comment: "ok", reviewed_by: "u1", reviewed_by_label: "reviewer@example.com",
        reviewed_at: null, machine_verdict: "fail", machine_needs_review: true,
      },
    };
    reviewApi.getExtractionReview.mockResolvedValue(base);
    renderPanel();
    expect(await screen.findByText("Review resolved")).toBeInTheDocument();
    expect(screen.getByText(/Approved as-is by reviewer@example.com/)).toBeInTheDocument();
    expect(screen.getByText("fail")).toBeInTheDocument(); // machine verdict still shown
    expect(screen.queryByRole("button", { name: /Approve as-is/ })).toBeNull();
  });
});
