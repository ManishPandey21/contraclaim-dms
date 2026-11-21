import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import ClaimSignalsStrip from "@/components/claim-bench/ClaimSignalsStrip";

const api = vi.hoisted(() => ({ raisePotentialClaim: vi.fn() }));
vi.mock("@/services/claim-bench-api", () => api);
const hooks = vi.hoisted(() => ({ signals: [] as unknown[] }));
vi.mock("@/hooks/useClaimBench", async (original) => ({
  ...(await original<object>()),
  useClaimBenchAvailability: () => ({ data: { enabled: true } }),
  useClaimSignals: () => ({ data: hooks.signals, isLoading: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("Claim signals (RU-1)", () => {
  it("offers no raise for a letter the viewer cannot open, and shows its neutral title", async () => {
    hooks.signals = ([
      { source_type: "letter_bench", source_id: "", label: "Incoming letter", reason: "The incoming letter was read as raising a claim.",
        title: "An incoming letter raises a possible claim (you cannot open the letter)", claim_type: "other" },
      { source_type: "letter_bench", source_id: "L-1", label: "Letter IN/7", reason: "The incoming letter was read as raising a claim.",
        title: "Access to Site", claim_type: "other" },
    ]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ClaimSignalsStrip onRaised={() => undefined} />
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByText(/you cannot open the letter/)).toBeInTheDocument();
    expect(screen.getByText("Someone who can open the letter must raise it.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Raise potential claim" })).toHaveLength(1);
  });
});
