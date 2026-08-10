import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PERMISSION_CONTRACT_VERSION } from "@/config/rolePermissions";

const mocks = vi.hoisted(() => ({
  getCurrentEntitlements: vi.fn(),
}));

vi.mock("@/services/plan-settings-api", () => ({
  getCurrentEntitlements: mocks.getCurrentEntitlements,
}));

import useEntitlements from "../useEntitlements";


const entitled = {
  contract_version: PERMISSION_CONTRACT_VERSION,
  scope_mode: "organization",
  organization_id: "org-A",
  project_id: null,
  source: "organization",
  plan_code: "dms_pro",
  features: {
    "feature.dms.enabled": true,
    "feature.dms.claims": true,
  },
  dms_enabled: true,
  drafting_enabled: false,
  unavailable_reason: null,
};


describe("useEntitlements", () => {
  beforeEach(() => {
    mocks.getCurrentEntitlements.mockReset();
    mocks.getCurrentEntitlements.mockResolvedValue(entitled);
  });

  it("refreshes entitlement state when active tenant context changes", async () => {
    const { result } = renderHook(() => useEntitlements());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hasFeature("feature.dms.claims")).toBe(true);
    expect(mocks.getCurrentEntitlements).toHaveBeenCalledTimes(1);

    act(() => window.dispatchEvent(new Event("tenant-context-changed")));
    await waitFor(() => expect(mocks.getCurrentEntitlements).toHaveBeenCalledTimes(2));
  });

  it("keeps the protected shell mounted during a tenant entitlement refresh", async () => {
    let resolveRefresh!: (value: typeof entitled) => void;
    const pendingRefresh = new Promise<typeof entitled>((resolve) => {
      resolveRefresh = resolve;
    });
    mocks.getCurrentEntitlements
      .mockResolvedValueOnce(entitled)
      .mockReturnValueOnce(pendingRefresh);

    const { result } = renderHook(() => useEntitlements());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => window.dispatchEvent(new Event("tenant-context-changed")));
    await waitFor(() => expect(mocks.getCurrentEntitlements).toHaveBeenCalledTimes(2));

    // A background scope refresh must retain the last verified snapshot and
    // must not make ProtectedRoute replace MainLayout with its loading shell.
    expect(result.current.loading).toBe(false);
    expect(result.current.entitlements).toEqual(entitled);

    await act(async () => resolveRefresh(entitled));
    await waitFor(() => expect(result.current.loading).toBe(false));
  });

  it("fails closed when generated frontend and backend contracts differ", async () => {
    mocks.getCurrentEntitlements.mockResolvedValue({
      ...entitled,
      contract_version: "stale-contract",
    });
    const { result } = renderHook(() => useEntitlements());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("permission_contract_version_mismatch");
    expect(result.current.hasFeature("feature.dms.enabled")).toBe(false);
  });

  it("normalizes structured API errors without opening access", async () => {
    mocks.getCurrentEntitlements.mockRejectedValue({
      response: { data: { detail: { message: "scope denied" } } },
    });
    const { result } = renderHook(() => useEntitlements());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("scope denied");
    expect(result.current.hasFeature("feature.dms.enabled")).toBe(false);
  });
});
