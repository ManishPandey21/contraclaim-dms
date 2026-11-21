import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getCurrentUserProfile: vi.fn(),
  getRoles: vi.fn(),
}));

vi.mock("@/services/session-api", () => ({
  __esModule: true,
  getCurrentUserProfile: mocks.getCurrentUserProfile,
}));

// The role catalogue must never be read (T-2); the mock proves it.
vi.mock("@/services/enhanced-api", () => ({
  __esModule: true,
  enhancedApi: { getRoles: mocks.getRoles },
}));

import { useRBAC } from "../useRBAC";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const EXPLICIT = [
  "dms.claim.determine",
  "dms.claim.strategy.read",
  "dms.claim.evidence.confirm_contract",
  "dms.claim.signal.view",
];

describe("useRBAC", () => {
  beforeEach(() => {
    mocks.getCurrentUserProfile.mockReset();
    mocks.getRoles.mockReset();
    mocks.getRoles.mockResolvedValue([
      { _id: "projectuser", permissions: ["dms.document.view", "dms.document.delete"] },
    ]);
  });

  it("uses the server's effective list and never the role catalogue", async () => {
    mocks.getCurrentUserProfile.mockResolvedValue({
      roles: ["projectadmin"],
      permissions: ["dms.document.view", "dms.document.upload"],
    });
    const { result } = renderHook(() => useRBAC());
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.can("dms.document.view")).toBe(true);
    expect(result.current.can("dms.document.upload")).toBe(true);
    expect(result.current.can("dms.document.delete")).toBe(false);
    expect(mocks.getRoles).not.toHaveBeenCalled();
  });

  it("treats an empty list from the server as authoritative: no permissions, no fallback", async () => {
    mocks.getCurrentUserProfile.mockResolvedValue({ roles: ["projectuser"], permissions: [] });
    const { result } = renderHook(() => useRBAC());
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.can("dms.document.view")).toBe(false);
    expect(result.current.can("dms.document.delete")).toBe(false);
    // Not even users:read by role name: the server lists it where its rule grants it.
    expect(result.current.can("users:read")).toBe(false);
    expect(mocks.getRoles).not.toHaveBeenCalled();
  });

  it("denies everything while loading", async () => {
    const pending = deferred<unknown>();
    mocks.getCurrentUserProfile.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useRBAC());
    expect(result.current.status).toBe("loading");
    expect(result.current.loading).toBe(true);
    expect(result.current.can("dms.document.view")).toBe(false);
    expect(result.current.can("users:read")).toBe(false);
    await act(async () => pending.resolve({ roles: [], permissions: [] }));
  });

  it("reports an error and denies everything when the profile cannot be read", async () => {
    mocks.getCurrentUserProfile.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useRBAC());
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBe("network down");
    expect(result.current.can("dms.document.view")).toBe(false);
    expect(result.current.roles).toEqual([]);
    expect(mocks.getRoles).not.toHaveBeenCalled();
  });

  it("matches the server list exactly: no client-side alias fan-out", async () => {
    // The server lists every check a held name passes; the client must not add more.
    mocks.getCurrentUserProfile.mockResolvedValue({
      roles: ["projectuser"],
      permissions: ["dms.claim.manage", "projects:update"],
    });
    const { result } = renderHook(() => useRBAC());
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.can("dms.claim.manage")).toBe(true);
    expect(result.current.can("projects:update")).toBe(true);
    // Reachable only by two client-side hops through projects:update.
    expect(result.current.can("dms.task.manage")).toBe(false);
    expect(result.current.can("dms.keydate.manage")).toBe(false);
  });

  describe("Super Admin", () => {
    it("passes every ordinary check but never an explicit-grant permission", async () => {
      mocks.getCurrentUserProfile.mockResolvedValue({ roles: ["superadmin"], permissions: ["*"] });
      const { result } = renderHook(() => useRBAC());
      await waitFor(() => expect(result.current.status).toBe("ready"));
      expect(result.current.can("dms.document.view")).toBe(true);
      expect(result.current.can("billing.plan.manage")).toBe(true);
      for (const permission of EXPLICIT) {
        expect(result.current.can(permission)).toBe(false);
      }
    });

    it("holds an explicit-grant permission only when the server names it", async () => {
      mocks.getCurrentUserProfile.mockResolvedValue({
        roles: ["superadmin"],
        permissions: ["*", "dms.claim.strategy.read"],
      });
      const { result } = renderHook(() => useRBAC());
      await waitFor(() => expect(result.current.status).toBe("ready"));
      expect(result.current.can("dms.claim.strategy.read")).toBe(true);
      expect(result.current.can("dms.claim.determine")).toBe(false);
    });
  });

  describe("explicit-grant permissions", () => {
    it("are never passed by a wildcard", async () => {
      mocks.getCurrentUserProfile.mockResolvedValue({ roles: ["orgadmin"], permissions: ["*"] });
      const { result } = renderHook(() => useRBAC());
      await waitFor(() => expect(result.current.status).toBe("ready"));
      expect(result.current.can("dms.document.view")).toBe(true);
      for (const permission of EXPLICIT) {
        expect(result.current.can(permission)).toBe(false);
      }
    });

    it("are passed when the server lists them for an ordinary user", async () => {
      mocks.getCurrentUserProfile.mockResolvedValue({
        roles: ["projectuser"],
        permissions: ["dms.claim.determine", "dms.claim.view"],
      });
      const { result } = renderHook(() => useRBAC());
      await waitFor(() => expect(result.current.status).toBe("ready"));
      expect(result.current.can("dms.claim.determine")).toBe(true);
      expect(result.current.can("dms.claim.strategy.read")).toBe(false);
    });
  });

  it("decides from the server list alone, never from profile role names", async () => {
    // A stored superadmin reference whose role is deactivated: the server does not answer "*".
    mocks.getCurrentUserProfile.mockResolvedValue({ roles: ["superadmin", "orguser"], permissions: ["users:read"] });
    const { result } = renderHook(() => useRBAC());
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.can("users:read")).toBe(true);
    expect(result.current.can("dms.document.view")).toBe(false);
    expect(result.current.can("billing.plan.manage")).toBe(false);
  });

  describe("invalidation", () => {
    it("forgets the previous answer and denies until the server answers after an account change", async () => {
      mocks.getCurrentUserProfile.mockResolvedValueOnce({ roles: ["orgadmin"], permissions: ["dms.document.view"] });
      const { result } = renderHook(() => useRBAC());
      await waitFor(() => expect(result.current.can("dms.document.view")).toBe(true));

      const next = deferred<unknown>();
      mocks.getCurrentUserProfile.mockReturnValueOnce(next.promise);
      act(() => {
        window.dispatchEvent(new CustomEvent("auth-state-changed"));
      });
      expect(result.current.status).toBe("loading");
      expect(result.current.can("dms.document.view")).toBe(false);
      expect(mocks.getCurrentUserProfile).toHaveBeenLastCalledWith({ force: true });

      await act(async () => next.resolve({ roles: ["projectuser"], permissions: [] }));
      expect(result.current.status).toBe("ready");
      expect(result.current.roles).toEqual(["projectuser"]);
      expect(result.current.can("dms.document.view")).toBe(false);
    });

    it.each(["profile-updated", "tenant-scope-changed"])(
      "re-reads the server in the background on %s, keeping the current answer meanwhile",
      async (event) => {
        mocks.getCurrentUserProfile.mockResolvedValueOnce({ roles: ["orgadmin"], permissions: ["dms.document.view"] });
        const { result } = renderHook(() => useRBAC());
        await waitFor(() => expect(result.current.can("dms.document.view")).toBe(true));

        const next = deferred<unknown>();
        mocks.getCurrentUserProfile.mockReturnValueOnce(next.promise);
        act(() => {
          window.dispatchEvent(new CustomEvent(event));
        });
        // Not cleared: clearing unmounts the layout behind ProtectedRoute (review, Critical).
        expect(result.current.status).toBe("ready");
        expect(mocks.getCurrentUserProfile).toHaveBeenLastCalledWith({ force: true });

        await act(async () => next.resolve({ roles: ["orgadmin"], permissions: [] }));
        expect(result.current.can("dms.document.view")).toBe(false);
      },
    );

    it("keeps the last answer when a background re-read fails", async () => {
      mocks.getCurrentUserProfile.mockResolvedValueOnce({ roles: ["orgadmin"], permissions: ["dms.document.view"] });
      const { result } = renderHook(() => useRBAC());
      await waitFor(() => expect(result.current.can("dms.document.view")).toBe(true));
      mocks.getCurrentUserProfile.mockRejectedValueOnce(new Error("timeout"));
      await act(async () => {
        window.dispatchEvent(new CustomEvent("tenant-scope-changed"));
      });
      expect(result.current.status).toBe("ready");
      expect(result.current.error).toBe("timeout");
      expect(result.current.can("dms.document.view")).toBe(true);
      // An account change that fails still denies everything.
      mocks.getCurrentUserProfile.mockRejectedValueOnce(new Error("down"));
      await act(async () => {
        window.dispatchEvent(new CustomEvent("auth-state-changed"));
      });
      expect(result.current.status).toBe("error");
      expect(result.current.can("dms.document.view")).toBe(false);
    });

    it("never lets an older answer overwrite a newer one (account switch)", async () => {
      const first = deferred<unknown>();
      const second = deferred<unknown>();
      mocks.getCurrentUserProfile.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
      const { result } = renderHook(() => useRBAC());
      act(() => {
        window.dispatchEvent(new CustomEvent("auth-state-changed"));
      });
      await act(async () => second.resolve({ roles: ["projectuser"], permissions: [] }));
      // The previous account's answer arrives late: ignored.
      await act(async () => first.resolve({ roles: ["superadmin"], permissions: ["*"] }));
      expect(result.current.roles).toEqual(["projectuser"]);
      expect(result.current.can("dms.document.view")).toBe(false);
    });

    it("stops listening after unmount", async () => {
      mocks.getCurrentUserProfile.mockResolvedValue({ roles: [], permissions: [] });
      const { result, unmount } = renderHook(() => useRBAC());
      await waitFor(() => expect(result.current.status).toBe("ready"));
      unmount();
      const calls = mocks.getCurrentUserProfile.mock.calls.length;
      window.dispatchEvent(new CustomEvent("auth-state-changed"));
      expect(mocks.getCurrentUserProfile.mock.calls.length).toBe(calls);
    });
  });
});
