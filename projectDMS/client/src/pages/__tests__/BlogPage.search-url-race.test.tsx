/**
 * The blog search box loses characters, deterministically reproduced.
 *
 * `BlogPage` kept the typed text in two independently authoritative places:
 * React state (`searchInput`, updated synchronously on every keystroke) and
 * the URL (`?q=`, updated through `setSearchParams`, which the router applies
 * on its own schedule). `useEffect(() => setSearchInput(query), [query])` then
 * copied the URL value back into the box unconditionally.
 *
 * While the router is still catching up, `query` holds an *older* value than
 * the box. Copying it back deletes whatever the user typed in between, and the
 * next keystroke appends to the truncated prefix - which is how "chronology"
 * arrived in the URL as "chrnology", "chronolgy" and "chroolgy". Twenty
 * consecutive runs of `BlogPage.test.tsx` against the unfixed component failed
 * fourteen times, always on the search assertion.
 *
 * That flake is a real defect, not a test artefact, so it is pinned here as a
 * deterministic case rather than left to timing. The router is replaced by a
 * queue the test drains by hand: `setSearchParams` enqueues a delivery instead
 * of applying it, so "an older update arrives after a newer keystroke" is an
 * ordering the test chooses rather than one it waits for. `fireEvent.change`
 * is used for the same reason - each keystroke stays a separate, observable
 * step. Realistic typing through `userEvent` is covered in `BlogPage.test.tsx`.
 */

import { useCallback, useReducer } from "react";
import type { SetURLSearchParams, URLSearchParamsInit } from "react-router-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, NavigationType } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

type Delivery = { params: URLSearchParams; type: NavigationType };

/** Every shape `setSearchParams` accepts, as a plain URLSearchParams. */
function toParams(init: URLSearchParamsInit): URLSearchParams {
  if (init instanceof URLSearchParams) return new URLSearchParams(init);
  if (typeof init === "string" || Array.isArray(init)) return new URLSearchParams(init);
  const params = new URLSearchParams();
  Object.entries(init).forEach(([key, value]) => {
    (Array.isArray(value) ? value : [value]).forEach((one) => params.append(key, one));
  });
  return params;
}

/** The router's state, and everything it has been asked to apply but has not. */
const router = {
  applied: new URLSearchParams(),
  appliedType: NavigationType.Pop,
  queue: [] as Delivery[],
  rerender: () => {},
};

function useControlledSearchParams(): [URLSearchParams, SetURLSearchParams] {
  const [, force] = useReducer((n: number) => n + 1, 0);
  router.rerender = force;

  const setSearchParams = useCallback<SetURLSearchParams>((nextInit, options) => {
    // React Router resolves an updater against the location it has COMMITTED,
    // not against a newer one the component has already asked for. That is the
    // whole reason two overlapping updates can lose each other's changes, so
    // the stub has to reproduce it rather than paper over it.
    const base = router.applied;
    const next =
      typeof nextInit === "function"
        ? toParams(nextInit(new URLSearchParams(base)))
        : toParams(nextInit);
    router.queue.push({
      params: next,
      type: options?.replace ? NavigationType.Replace : NavigationType.Push,
    });
  }, []);

  return [router.applied, setSearchParams];
}

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return {
    ...actual,
    useSearchParams: () => useControlledSearchParams(),
    useNavigationType: () => router.appliedType,
  };
});

const { default: BlogPage } = await import("../BlogPage");

/** Apply the oldest update the component asked for. */
function deliverOldestPendingUpdate() {
  const delivery = router.queue.shift();
  if (!delivery) throw new Error("nothing pending: the test proves nothing");
  act(() => {
    router.applied = delivery.params;
    router.appliedType = delivery.type;
    router.rerender();
  });
}

function deliverEveryPendingUpdate() {
  while (router.queue.length) deliverOldestPendingUpdate();
}

/** A browser Back or Forward: a change this component did not ask for. */
function navigateBack(search: string) {
  act(() => {
    router.applied = new URLSearchParams(search);
    router.appliedType = NavigationType.Pop;
    router.rerender();
  });
}

const searchBox = () => screen.getByRole("searchbox", { name: /search articles/i });

const appliedQuery = () => router.applied.get("q");

function renderBlog(search = "") {
  router.applied = new URLSearchParams(search);
  router.appliedType = NavigationType.Pop;
  router.queue = [];
  return render(
    <MemoryRouter initialEntries={["/blog"]}>
      <BlogPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  router.applied = new URLSearchParams();
  router.appliedType = NavigationType.Pop;
  router.queue = [];
});

describe("blog search when the URL lags behind the keyboard", () => {
  it("keeps a character typed while an older URL update was still pending", () => {
    renderBlog();

    fireEvent.change(searchBox(), { target: { value: "c" } });
    fireEvent.change(searchBox(), { target: { value: "ch" } });
    expect(searchBox()).toHaveValue("ch");
    expect(router.queue).toHaveLength(2);

    // The router now applies the FIRST request, ?q=c, while the box already
    // holds "ch". The unfixed component copies "c" back over it.
    deliverOldestPendingUpdate();

    expect(searchBox()).toHaveValue("ch");
  });

  it("still puts the whole search term in the URL once the router catches up", () => {
    renderBlog();

    for (const value of ["c", "ch", "chr", "chro", "chron"]) {
      fireEvent.change(searchBox(), { target: { value } });
    }
    deliverEveryPendingUpdate();

    expect(searchBox()).toHaveValue("chron");
    expect(appliedQuery()).toBe("chron");
  });

  it("does not strand the box out of sync after the lagging update lands", () => {
    renderBlog();

    fireEvent.change(searchBox(), { target: { value: "ch" } });
    fireEvent.change(searchBox(), { target: { value: "chr" } });
    deliverOldestPendingUpdate(); // stale "ch"
    fireEvent.change(searchBox(), { target: { value: "chro" } });
    deliverEveryPendingUpdate();

    expect(searchBox()).toHaveValue("chro");
    expect(appliedQuery()).toBe("chro");
  });

  it("does not reinstate a filter that an earlier, uncommitted update removed", () => {
    // Clicking "All topics" and then typing is two updates issued before the
    // first has committed. Both resolve against the same committed params, so
    // the second rewrites `category` back in and the page shows nothing at all
    // - the E2E case "filters by topic and by search term" going from one
    // article to zero.
    renderBlog("category=extension-of-time");

    fireEvent.click(screen.getByRole("button", { name: "All topics" }));
    fireEvent.change(searchBox(), { target: { value: "chronology" } });
    deliverEveryPendingUpdate();

    expect(appliedQuery()).toBe("chronology");
    expect(router.applied.get("category")).toBeNull();
  });

  it("still accepts a Back navigation, which the component did not ask for", () => {
    renderBlog("q=chronology");
    expect(searchBox()).toHaveValue("chronology");

    fireEvent.change(searchBox(), { target: { value: "chronologyX" } });
    deliverEveryPendingUpdate();
    expect(searchBox()).toHaveValue("chronologyX");

    navigateBack("q=chronology");

    expect(searchBox()).toHaveValue("chronology");
  });

  it("accepts a Back navigation even while one of its own updates is pending", () => {
    renderBlog("q=chr");

    fireEvent.change(searchBox(), { target: { value: "chro" } });
    expect(router.queue).toHaveLength(1);

    navigateBack("q=evidence");

    expect(searchBox()).toHaveValue("evidence");
  });
});
