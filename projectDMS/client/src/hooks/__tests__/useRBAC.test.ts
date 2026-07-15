import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getCurrentUserProfile: vi.fn(),
  getRoles: vi.fn(),
}));

vi.mock("@/services/session-api", () => ({
  __esModule: true,
  getCurrentUserProfile: mocks.getCurrentUserProfile,
}));

vi.mock("@/services/enhanced-api", () => ({
  __esModule: true,
  enhancedApi: {
    getRoles: mocks.getRoles,
  },
}));

import { useRBAC } from "../useRBAC";

describe("useRBAC", () => {
  beforeEach(() => {
    mocks.getCurrentUserProfile.mockReset();
    mocks.getRoles.mockReset();
  });

  it("uses effective /me permissions before falling back to the role catalog", async () => {
    mocks.getCurrentUserProfile.mockResolvedValue({
      roles: ["projectadmin"],
      permissions: ["dms.document.view", "dms.document.upload"],
    });
    mocks.getRoles.mockRejectedValue(new Error("forbidden"));

    const { result } = renderHook(() => useRBAC());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.can("dms.document.view")).toBe(true);
    expect(result.current.can("dms.document.upload")).toBe(true);
    expect(mocks.getRoles).not.toHaveBeenCalled();
  });
});
