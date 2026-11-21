import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { benchKeys, errorMessage, useBenchActions } from "@/hooks/useLetterBench";
import type { BenchResponse } from "@/types/letterBench";
import { assertion, bench } from "./fixtures";

const api = vi.hoisted(() => ({
  fetchBench: vi.fn(),
  patchAssertion: vi.fn(),
  lockPositions: vi.fn(),
  composeReply: vi.fn(),
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), message: vi.fn() }));

vi.mock("@/services/letter-bench-api", async () => {
  const actual = await vi.importActual<typeof import("@/services/letter-bench-api")>("@/services/letter-bench-api");
  return { ...actual, ...api };
});
vi.mock("sonner", () => ({ toast }));

function conflict() {
  return Object.assign(new Error("409"), {
    isAxiosError: true,
    response: { status: 409, data: { detail: "The Bench changed since you loaded it." } },
  });
}

function setup(initial: BenchResponse) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(benchKeys.bench("L1"), initial);
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const view = renderHook(() => useBenchActions("L1"), { wrapper });
  const cached = () => client.getQueryData<BenchResponse>(benchKeys.bench("L1"))!;
  return { ...view, cached };
}

function served(version: number, verdict: "none" | "partial" | null = null): BenchResponse {
  return bench({ board_version: version, assertions: [assertion({ verdict, state: verdict ? "decided" : "open" })] });
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  Object.values(toast).forEach((mock) => mock.mockReset());
});

describe("useBenchActions", () => {
  it("shows a verdict at once, sends the server's board_version, then takes the server's answer", async () => {
    let release: (value: BenchResponse) => void = () => undefined;
    api.patchAssertion.mockReturnValue(new Promise<BenchResponse>((resolve) => (release = resolve)));
    const { result, cached } = setup(served(5));

    act(() => result.current.patch.mutate({ assertionId: "A1", patch: { verdict: "partial" } }));
    await waitFor(() => expect(cached().board.assertions[0].verdict).toBe("partial"));
    expect(cached().board.board_version).toBe(5); // optimistic state never invents a version
    await waitFor(() => expect(api.patchAssertion).toHaveBeenCalledWith("L1", "A1", 5, { verdict: "partial" }));

    await act(async () => release(served(6, "partial")));
    await waitFor(() => expect(cached().board.board_version).toBe(6));
  });

  it("on a version conflict refetches and replays the user's action once", async () => {
    api.patchAssertion.mockRejectedValueOnce(conflict()).mockResolvedValueOnce(served(9, "none"));
    api.fetchBench.mockResolvedValue(served(8));
    const { result, cached } = setup(served(5));

    await act(async () => {
      await result.current.patch.mutateAsync({ assertionId: "A1", patch: { verdict: "none" } });
    });
    expect(api.patchAssertion.mock.calls.map((call) => call[2])).toEqual([5, 8]);
    expect(api.fetchBench).toHaveBeenCalledTimes(1);
    expect(cached().board.board_version).toBe(9);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("rolls the optimistic change back and says why when the server refuses", async () => {
    api.patchAssertion.mockRejectedValue(
      Object.assign(new Error("422"), { isAxiosError: true, response: { status: 422, data: { detail: "Rephrase it." } } })
    );
    api.fetchBench.mockResolvedValue(served(5));
    const { result, cached } = setup(served(5));

    await act(async () => {
      await result.current.patch.mutateAsync({ assertionId: "A1", patch: { verdict: "none" } }).catch(() => undefined);
    });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Rephrase it."));
    expect(cached().board.assertions[0].verdict).toBeNull();
    expect(api.patchAssertion).toHaveBeenCalledTimes(1); // a refusal is not replayed
  });

  it("gives up after one replay rather than looping", async () => {
    api.patchAssertion.mockRejectedValue(conflict());
    api.fetchBench.mockResolvedValue(served(8));
    const { result } = setup(served(5));
    await act(async () => {
      await result.current.patch.mutateAsync({ assertionId: "A1", patch: { verdict: "none" } }).catch(() => undefined);
    });
    expect(api.patchAssertion).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("The Bench changed since you loaded it."));
  });

  it("does not compose on a replay if the fresh board is no longer ready", async () => {
    api.composeReply.mockRejectedValue(conflict());
    api.fetchBench.mockResolvedValue(served(8)); // gate.can_compose is false
    const { result } = setup(bench({}, { can_compose: true }));
    await act(async () => {
      await result.current.compose.mutateAsync().catch(() => undefined);
    });
    expect(api.composeReply).toHaveBeenCalledTimes(1);
  });

  it("serialises quick edits so the second uses the version the first produced", async () => {
    api.patchAssertion.mockImplementation(async (_letter: string, _id: string, version: number) => served(version + 1, "none"));
    const { result } = setup(served(5));
    await act(async () => {
      const first = result.current.patch.mutateAsync({ assertionId: "A1", patch: { verdict: "none" } });
      const second = result.current.patch.mutateAsync({ assertionId: "A1", patch: { user_direction: "Out of time." } });
      await Promise.all([first, second]);
    });
    expect(api.patchAssertion.mock.calls.map((call) => call[2])).toEqual([5, 6]);
  });
});

describe("errorMessage", () => {
  it("prefers the server's own words", () => {
    expect(errorMessage(conflict(), "fallback")).toBe("The Bench changed since you loaded it.");
    expect(errorMessage(new Error("boom"), "fallback")).toBe("boom");
    expect(errorMessage(null, "fallback")).toBe("fallback");
  });

  it("reads the plain message of a structured refusal", () => {
    const refusal = Object.assign(new Error("Request failed with status code 403"), {
      isAxiosError: true,
      response: {
        status: 403,
        data: { detail: { code: "notice_self_verification", user_message: "Someone else must verify it.", detail: null } },
      },
    });
    expect(errorMessage(refusal, "fallback")).toBe("Someone else must verify it.");
  });
});
