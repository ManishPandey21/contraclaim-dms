import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const tenant = { selectedOrganizationId: "org-A", selectedProjectId: "project-1" };

vi.mock("@/contexts/TenantContext", () => ({
  useOptionalTenant: () => tenant,
}));

const fetchAvailability = vi.fn();
const fetchSignals = vi.fn();

vi.mock("@/services/claim-bench-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/claim-bench-api")>();
  return {
    ...actual,
    fetchClaimBenchAvailability: (...args: unknown[]) => fetchAvailability(...args),
    fetchClaimSignals: (...args: unknown[]) => fetchSignals(...args),
  };
});

import { claimBenchKeys, useClaimBenchAvailability, useClaimSignals } from "@/hooks/useClaimBench";
import { useClaimBenchOn } from "@/hooks/useClaimBenchOn";

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe("Claim Bench queries follow the navbar selection (4B-03)", () => {
  beforeEach(() => {
    tenant.selectedOrganizationId = "org-A";
    tenant.selectedProjectId = "project-1";
    fetchAvailability.mockReset();
    fetchSignals.mockReset();
  });

  it("keys availability by organisation and signals by organisation and project", () => {
    expect(claimBenchKeys.availability("org-A")).not.toEqual(claimBenchKeys.availability("org-B"));
    expect(claimBenchKeys.signals("org-A", "p1")).not.toEqual(claimBenchKeys.signals("org-A", "p2"));
    // The prefixes still match every selection, so one invalidation clears them all.
    expect(claimBenchKeys.signals("org-A", "p1").slice(0, 2)).toEqual([...claimBenchKeys.signalsAll]);
    expect(claimBenchKeys.availability("org-A").slice(0, 2)).toEqual([...claimBenchKeys.availabilityAll]);
  });

  it("asks the server again when the organisation changes instead of serving the cached answer", async () => {
    fetchAvailability.mockResolvedValueOnce({ enabled: true, rollout_mode: "canary" });
    fetchAvailability.mockResolvedValueOnce({ enabled: false, rollout_mode: "canary" });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result, rerender } = renderHook(() => useClaimBenchAvailability(), { wrapper: wrapper(client) });
    await waitFor(() => expect(result.current.data?.enabled).toBe(true));

    tenant.selectedOrganizationId = "org-B";
    rerender();
    await waitFor(() => expect(result.current.data?.enabled).toBe(false));
    expect(fetchAvailability).toHaveBeenCalledTimes(2);
  });

  it("lists signals per project", async () => {
    fetchSignals.mockResolvedValue([]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = renderHook(() => useClaimSignals(true), { wrapper: wrapper(client) });
    await waitFor(() => expect(fetchSignals).toHaveBeenCalledTimes(1));
    tenant.selectedProjectId = "project-2";
    rerender();
    await waitFor(() => expect(fetchSignals).toHaveBeenCalledTimes(2));
  });

  it("useClaimBenchOn re-reads the flag for a new organisation and is off until it answers", async () => {
    fetchAvailability.mockResolvedValueOnce({ enabled: true, rollout_mode: "canary" });
    let resolveSecond: (value: { enabled: boolean; rollout_mode: string }) => void = () => undefined;
    fetchAvailability.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSecond = resolve;
      })
    );
    const { result, rerender } = renderHook(() => useClaimBenchOn());
    await waitFor(() => expect(result.current).toBe(true));

    tenant.selectedOrganizationId = "org-B";
    rerender();
    await waitFor(() => expect(result.current).toBe(false));
    resolveSecond({ enabled: false, rollout_mode: "canary" });
    await waitFor(() => expect(fetchAvailability).toHaveBeenCalledTimes(2));
    expect(result.current).toBe(false);
  });
});
