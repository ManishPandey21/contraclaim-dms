import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EvidenceContractConfirmations from "@/components/claim-bench/EvidenceContractConfirmations";

const api = vi.hoisted(() => ({ confirmEvidenceContract: vi.fn() }));
vi.mock("@/services/claim-findings-api", () => api);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const ITEMS = [
  { claim_id: "c1", kind: "hindrance", record_id: "HN", label: "Hindrance HR-7", state: "unidentified" as const },
  { claim_id: "c1", kind: "document", record_id: "d1", label: "Document L-12", state: "unverified" as const },
];

describe("Confirming which contract evidence belongs to (OD-1)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("asks how the person knows, sends the record and the reason, then reloads", async () => {
    api.confirmEvidenceContract.mockResolvedValue({});
    const reload = vi.fn();
    render(<EvidenceContractConfirmations items={ITEMS} onConfirmed={reload} />);
    expect(screen.getByText(/does not say which contract it belongs to/)).toBeInTheDocument();
    expect(screen.getByText(/which the Contract Master does not hold/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Confirm…" })[0]);
    const send = screen.getByRole("button", { name: "Confirm for this contract" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/How do you know/), "Main works site, chainage 12+400.");
    await userEvent.click(send);
    await waitFor(() =>
      expect(api.confirmEvidenceContract).toHaveBeenCalledWith("c1", {
        record_kind: "hindrance",
        record_id: "HN",
        reason: "Main works site, chainage 12+400.",
      })
    );
    expect(reload).toHaveBeenCalled();
  });

  it("offers no confirmation when read-only, and nothing at all when nothing waits", () => {
    const first = render(<EvidenceContractConfirmations items={ITEMS} readOnly onConfirmed={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Confirm…" })).not.toBeInTheDocument();
    first.unmount();
    const { container } = render(<EvidenceContractConfirmations items={[]} onConfirmed={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
