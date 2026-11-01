import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ listSubTags: vi.fn() }));
vi.mock("@/services/tags-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/tags-api")>()),
  ...api,
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

import { useSubtagOptions } from "../useSubtagOptions";

const sub = (id: string, tagId: string) => ({
  _id: id,
  name: `Name ${id}`,
  tag_id: tagId,
  created_at: "",
  updated_at: "",
});

const callsFor = (tagId: string) => api.listSubTags.mock.calls.filter(([id]) => id === tagId).length;

// The Document Viewer loads subtags for the selected tag. Re-selecting a tag
// must not spend another Tags read, including a tag that has no subtags.
describe("useSubtagOptions", () => {
  beforeEach(() => {
    api.listSubTags.mockReset();
    api.listSubTags.mockImplementation(async (tagId: string) =>
      tagId === "tag-a" ? [sub("a1", "tag-a")] : tagId === "tag-b" ? [sub("b1", "tag-b")] : [],
    );
  });

  it("selecting A, B, then A again loads A once and B once", async () => {
    const { result } = renderHook(() => useSubtagOptions());

    await act(() => result.current.fetchSubtags("tag-a"));
    await act(() => result.current.fetchSubtags("tag-b"));
    await act(() => result.current.fetchSubtags("tag-a"));

    expect(callsFor("tag-a")).toBe(1);
    expect(callsFor("tag-b")).toBe(1);
    expect(result.current.availableSubtags.map((option) => option.value).sort()).toEqual(["a1", "b1"]);
  });

  it("caches a tag with zero subtags instead of refetching it", async () => {
    const { result } = renderHook(() => useSubtagOptions());

    await act(() => result.current.fetchSubtags("tag-empty"));
    await act(() => result.current.fetchSubtags("tag-a"));
    await act(() => result.current.fetchSubtags("tag-empty"));
    await act(() => result.current.fetchSubtags("tag-empty"));

    expect(callsFor("tag-empty")).toBe(1);
  });

  it("concurrent selections of the same tag share one request", async () => {
    const { result } = renderHook(() => useSubtagOptions());

    await act(() => Promise.all([result.current.fetchSubtags("tag-a"), result.current.fetchSubtags("tag-a")]));

    expect(callsFor("tag-a")).toBe(1);
  });

  it("a failed load may be retried by selecting the tag again", async () => {
    api.listSubTags.mockRejectedValueOnce(new Error("429"));
    const { result } = renderHook(() => useSubtagOptions());

    await act(() => result.current.fetchSubtags("tag-a"));
    await act(() => result.current.fetchSubtags("tag-a"));

    expect(callsFor("tag-a")).toBe(2);
    expect(result.current.availableSubtags.map((option) => option.value)).toEqual(["a1"]);
  });

  it("clearing the options forgets what was loaded", async () => {
    const { result } = renderHook(() => useSubtagOptions());

    await act(() => result.current.fetchSubtags("tag-a"));
    act(() => result.current.resetSubtags());
    expect(result.current.availableSubtags).toEqual([]);
    await act(() => result.current.fetchSubtags("tag-a"));

    expect(callsFor("tag-a")).toBe(2);
  });

  it("a reset during an in-flight load ends loading, and the late answer writes nothing", async () => {
    let finish!: (value: unknown[]) => void;
    api.listSubTags.mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)));
    const { result } = renderHook(() => useSubtagOptions());

    let pending!: Promise<void>;
    act(() => {
      pending = result.current.fetchSubtags("tag-a");
    });
    expect(result.current.isLoadingSubtags).toBe(true);

    act(() => result.current.resetSubtags());
    expect(result.current.isLoadingSubtags).toBe(false);
    expect(result.current.availableSubtags).toEqual([]);

    await act(async () => {
      finish([sub("a1", "tag-a")]);
      await pending;
    });
    expect(result.current.availableSubtags).toEqual([]);
    expect(result.current.isLoadingSubtags).toBe(false);
  });

  it("a load that fails after a reset leaves the reset state and a newer claim alone", async () => {
    const { toast } = await import("sonner");
    vi.mocked(toast.error).mockClear();
    let fail!: (reason: unknown) => void;
    api.listSubTags.mockImplementationOnce(() => new Promise((_resolve, reject) => (fail = reject)));
    let finishNewer!: (value: unknown[]) => void;
    api.listSubTags.mockImplementationOnce(() => new Promise((resolve) => (finishNewer = resolve)));
    const { result } = renderHook(() => useSubtagOptions());

    let stale!: Promise<void>;
    act(() => {
      stale = result.current.fetchSubtags("tag-a");
    });
    act(() => result.current.resetSubtags());
    let newer!: Promise<void>;
    act(() => {
      newer = result.current.fetchSubtags("tag-a"); // after the reset: a fresh claim
    });

    await act(async () => {
      fail(new Error("429"));
      await stale;
    });
    // The stale failure neither ends the newer load nor releases its claim.
    expect(result.current.isLoadingSubtags).toBe(true);
    expect(toast.error).not.toHaveBeenCalled();
    await act(() => result.current.fetchSubtags("tag-a"));
    expect(callsFor("tag-a")).toBe(2);

    await act(async () => {
      finishNewer([sub("a1", "tag-a")]);
      await newer;
    });
    expect(result.current.isLoadingSubtags).toBe(false);
    expect(result.current.availableSubtags.map((option) => option.value)).toEqual(["a1"]);
  });
});
