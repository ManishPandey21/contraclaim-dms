import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OverlapCostBasis from "@/components/claim-bench/OverlapCostBasis";
import { containsRawId } from "@/lib/plain-language";
import type { OverlapView } from "@/types/claimBench";

const links = vi.hoisted(() => ({ listEntityDocumentLinks: vi.fn() }));
vi.mock("@/services/document-relationships-api", () => links);

const INVOICE = "65f0a1b2c3d4e5f6a7b8c9f1";

const overlap = (over: Partial<OverlapView> = {}): OverlapView => ({
  overlap_id: "ec:2026-03-20_2026-03-31:abc",
  start_date: "2026-03-20T00:00:00",
  end_date: "2026-03-31T00:00:00",
  days: 12,
  kind: "employer_contractor",
  employer_event_ids: ["E1"],
  contractor_event_ids: ["C1"],
  finding: { treatment: "not_true_concurrency", basis: "Not true concurrency on the programme." },
  cost_established: false,
  cost_status: "Time only until the contractual basis, causation and cost evidence are recorded and reviewed.",
  ...over,
});

const mount = (view: OverlapView, onRecord = vi.fn(), onReview = vi.fn()) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <OverlapCostBasis claimId="c1" overlap={view} disabled={false} onRecord={onRecord} onReview={onReview} />
    </QueryClientProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  links.listEntityDocumentLinks.mockResolvedValue([
    { document_id: INVOICE, document: { subject: "Plant hire invoice" } },
  ]);
});

describe("OverlapCostBasis", () => {
  it("says the overlap is time only and records a basis with evidence from the claim's documents", async () => {
    const onRecord = vi.fn();
    mount(overlap(), onRecord);
    expect(screen.getByText("Time only")).toBeInTheDocument();
    expect(screen.getByText(/Time only until the contractual basis/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Record cost basis" }));
    const save = screen.getByRole("button", { name: "Record cost basis" });
    expect(save).toBeDisabled(); // nothing entered yet
    await userEvent.type(screen.getByLabelText("Contractual basis for cost"), "Sub-Clause 8.4 with 8.5 cost.");
    await userEvent.type(screen.getByLabelText(/Causation/), "Plant stood idle for want of access.");
    await userEvent.click(await screen.findByLabelText("Plant hire invoice"));
    await userEvent.click(save);
    expect(onRecord).toHaveBeenCalledWith({
      contractual_basis: "Sub-Clause 8.4 with 8.5 cost.",
      causation: "Plant stood idle for want of access.",
      evidence_document_ids: [INVOICE],
    });
  });

  it("offers a review once recorded, explains who may give it, and shows evidence by name", async () => {
    const onReview = vi.fn();
    const { container } = mount(
      overlap({
        finding: {
          treatment: "not_true_concurrency",
          basis: "Not true concurrency on the programme.",
          cost_basis: { contractual_basis: "Sub-Clause 8.4.", causation: "Idle plant.", evidence_document_ids: [INVOICE], recorded_by: "u1" },
        },
      }),
      vi.fn(),
      onReview
    );
    expect(await screen.findByText(/Plant hire invoice/)).toBeInTheDocument();
    expect(screen.getByText(/someone other than the person who recorded it/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review cost basis" }));
    expect(onReview).toHaveBeenCalled();
    expect(containsRawId(container.textContent || "")).toBe(false);
  });

  it("shows an established cost basis as carrying cost, with no review button", () => {
    mount(
      overlap({
        cost_established: true,
        cost_status: null,
        finding: {
          treatment: "not_true_concurrency",
          basis: "Not true concurrency on the programme.",
          cost_basis: { contractual_basis: "Sub-Clause 8.4.", causation: "Idle plant.", evidence_document_ids: [INVOICE], recorded_by: "u1", reviewed_by: "u2" },
        },
      })
    );
    expect(screen.getByText("Carries cost: basis reviewed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review cost basis" })).not.toBeInTheDocument();
  });
});
