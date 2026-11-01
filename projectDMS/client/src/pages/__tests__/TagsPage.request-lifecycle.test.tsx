import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Tag, TagsListResult } from "@/services/tags-api";

const api = vi.hoisted(() => ({
  listTags: vi.fn(),
  listSubTags: vi.fn(),
  createTag: vi.fn(),
  updateTag: vi.fn(),
  deleteTag: vi.fn(),
  createSubTag: vi.fn(),
  updateSubTag: vi.fn(),
  deleteSubTag: vi.fn(),
}));
const tenant = vi.hoisted(() => ({
  value: { loading: false, selectedOrganizationId: "org-A", selectedProjectId: "proj-A1" } as Record<
    string,
    unknown
  >,
}));

vi.mock("@/services/tags-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/tags-api")>()),
  ...api,
}));
vi.mock("@/contexts/TenantContext", () => ({ useTenant: () => tenant.value }));
vi.mock("@/lib/error-logger", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/error-logger")>()),
  logError: vi.fn(),
}));

import TagsPage, { TAG_SEARCH_DEBOUNCE_MS } from "../TagsPage";

const tag = (name: string, id = name): Tag => ({
  _id: id,
  name,
  organization_id: "org-A",
  created_by: "u-1",
  created_at: "",
  updated_at: "",
});

const page = (tags: Tag[], total = tags.length, pageNo = 1): TagsListResult => ({
  tags,
  total,
  page: pageNo,
  limit: 10,
  has_next: pageNo * 10 < total,
  has_prev: pageNo > 1,
});

const httpError = (status: number, headers: Record<string, string> = {}) =>
  Object.assign(new Error(`HTTP ${status}`), {
    isAxiosError: true,
    response: { status, headers, data: { detail: status === 429 ? "Rate limit exceeded" : "boom" } },
  });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function renderPage(key = "org-A:proj-A1") {
  return render(
    <MemoryRouter>
      <div key={key}>
        <TagsPage />
      </div>
    </MemoryRouter>,
  );
}

const searchBox = () => screen.getByPlaceholderText("Search tags");
const lastSearch = () => api.listTags.mock.calls[api.listTags.mock.calls.length - 1][0];

describe("TagsPage request lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    tenant.value = { loading: false, selectedOrganizationId: "org-A", selectedProjectId: "proj-A1" };
    api.listTags.mockResolvedValue(page([tag("Payment"), tag("Variation")]));
  });

  it("makes exactly one request on initial load", async () => {
    renderPage();

    expect(await screen.findByText("Payment")).toBeInTheDocument();
    // Past the debounce too: its mount-time timer must not fetch again.
    await sleep(TAG_SEARCH_DEBOUNCE_MS + 100);
    expect(api.listTags).toHaveBeenCalledTimes(1);
    expect(api.listTags).toHaveBeenCalledWith({ search: "", page: 1, limit: 10 });
  });

  it("waits for the navbar scope before loading, then loads once", async () => {
    tenant.value = { ...tenant.value, loading: true };
    const view = renderPage();
    await sleep(50);
    expect(api.listTags).not.toHaveBeenCalled();

    tenant.value = { ...tenant.value, loading: false };
    view.rerender(
      <MemoryRouter>
        <div key="org-A:proj-A1">
          <TagsPage />
        </div>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Payment")).toBeInTheDocument();
    expect(api.listTags).toHaveBeenCalledTimes(1);
  });

  it("debounces rapid typing of 'payment' into one search request", async () => {
    renderPage();
    await screen.findByText("Payment");
    api.listTags.mockClear();
    api.listTags.mockResolvedValue(page([tag("Payment")]));

    let typed = "";
    for (const char of "payment") {
      typed += char;
      fireEvent.change(searchBox(), { target: { value: typed } });
      await sleep(40); // a fast typist: well inside the debounce window
    }
    expect(api.listTags).not.toHaveBeenCalled();

    await waitFor(() => expect(api.listTags).toHaveBeenCalledTimes(1));
    expect(lastSearch()).toEqual({ search: "payment", page: 1, limit: 10 });
    await sleep(TAG_SEARCH_DEBOUNCE_MS + 100);
    expect(api.listTags).toHaveBeenCalledTimes(1);
  });

  it("never lets an older search response replace the newer one", async () => {
    renderPage();
    await screen.findByText("Payment");

    const pay = deferred<TagsListResult>();
    const payment = deferred<TagsListResult>();
    api.listTags.mockImplementation(({ search }: { search: string }) =>
      search === "pay" ? pay.promise : payment.promise,
    );

    fireEvent.change(searchBox(), { target: { value: "pay" } });
    await waitFor(() => expect(lastSearch().search).toBe("pay"));
    fireEvent.change(searchBox(), { target: { value: "payment" } });
    await waitFor(() => expect(lastSearch().search).toBe("payment"));

    await act(async () => {
      payment.resolve(page([tag("Payment terms", "t-new")]));
    });
    expect(await screen.findByText("Payment terms")).toBeInTheDocument();

    // The stale "pay" response arrives last and must be discarded.
    await act(async () => {
      pay.resolve(page([tag("Paying agent", "t-old")]));
    });
    expect(screen.queryByText("Paying agent")).not.toBeInTheDocument();
    expect(screen.getByText("Payment terms")).toBeInTheDocument();
  });

  it("shows an error state, not 'No Tags Yet', when the load is rate limited", async () => {
    api.listTags.mockRejectedValue(httpError(429));
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Unable to load tags");
    expect(alert).toHaveTextContent("Too many tag requests. Please try again shortly.");
    expect(screen.queryByText("No Tags Yet")).not.toBeInTheDocument();
    expect(screen.queryByText("No tags to show")).not.toBeInTheDocument();
    // No automatic retry into a full bucket.
    await sleep(TAG_SEARCH_DEBOUNCE_MS + 100);
    expect(api.listTags).toHaveBeenCalledTimes(1);
  });

  it("Retry issues exactly one new request", async () => {
    api.listTags.mockRejectedValueOnce(httpError(429));
    renderPage();
    await screen.findByRole("alert");

    api.listTags.mockResolvedValueOnce(page([tag("Payment")]));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Payment")).toBeInTheDocument();
    expect(api.listTags).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("honours Retry-After: says when, and holds Retry until it passes", async () => {
    api.listTags.mockRejectedValueOnce(httpError(429, { "retry-after": "1" }));
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("You can retry in about 1 second.");
    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toBeDisabled();
    fireEvent.click(retry);
    expect(api.listTags).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(retry).toBeEnabled(), { timeout: 2000 });
    api.listTags.mockResolvedValueOnce(page([tag("Payment")]));
    fireEvent.click(retry);
    expect(await screen.findByText("Payment")).toBeInTheDocument();
    expect(api.listTags).toHaveBeenCalledTimes(2);
  });

  it("keeps the last loaded tags when a refresh fails", async () => {
    renderPage();
    await screen.findByText("Payment");

    api.listTags.mockRejectedValue(httpError(500));
    fireEvent.change(searchBox(), { target: { value: "var" } });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Showing the tags from the last successful load.");
    expect(screen.getByText("Payment")).toBeInTheDocument();
    expect(screen.queryByText("No Tags Yet")).not.toBeInTheDocument();
  });

  it("renders 'No Tags Yet' only for a successful load with zero tags", async () => {
    api.listTags.mockResolvedValue(page([], 0));
    renderPage();

    expect(await screen.findByText("No Tags Yet")).toBeInTheDocument();
    expect(screen.getByText("No tags to show")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("loads the next page at once, without the search debounce", async () => {
    api.listTags.mockResolvedValue(page([tag("Payment")], 25));
    renderPage();
    await screen.findByText("Payment");
    api.listTags.mockClear();
    api.listTags.mockResolvedValue(page([tag("Retention")], 25, 2));

    fireEvent.click(screen.getByRole("button", { name: /Next/ }));

    await waitFor(() => expect(api.listTags).toHaveBeenCalledTimes(1), {
      timeout: TAG_SEARCH_DEBOUNCE_MS - 100,
    });
    expect(lastSearch()).toEqual({ search: "", page: 2, limit: 10 });
    expect(await screen.findByText("Retention")).toBeInTheDocument();
  });

  it("a delete that resolves after a page change refreshes the page now shown", async () => {
    api.listTags.mockImplementation(({ page: pageNo }: { page: number }) =>
      Promise.resolve(
        pageNo === 2 ? page([tag("Retention")], 25, 2) : page([tag("Payment")], 25, 1),
      ),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const removal = deferred<void>();
    api.deleteTag.mockReturnValue(removal.promise);
    renderPage();
    await screen.findByText("Payment");

    fireEvent.click(screen.getAllByRole("button").find((b) => b.querySelector(".lucide-trash"))!);
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(await screen.findByText("Retention")).toBeInTheDocument();

    api.listTags.mockClear();
    await act(async () => {
      removal.resolve();
    });

    await waitFor(() => expect(api.listTags).toHaveBeenCalledTimes(1));
    expect(lastSearch()).toEqual({ search: "", page: 2, limit: 10 });
    expect(await screen.findByText("Retention")).toBeInTheDocument();
    expect(screen.queryByText("Payment")).not.toBeInTheDocument();
  });

  it("expanding a tag with no subtags again does not refetch them", async () => {
    api.listTags.mockResolvedValue(page([tag("Empty", "t-empty")]));
    api.listSubTags.mockResolvedValue([]);
    const { container } = renderPage();
    await screen.findByText("Empty");

    const toggle = () =>
      fireEvent.click(
        container.querySelector(".lucide-chevron-right, .lucide-chevron-down")!.closest("button")!,
      );
    toggle(); // expand: loads (zero subtags)
    await waitFor(() => expect(container.querySelector(".lucide-chevron-down")).not.toBeNull());
    toggle(); // collapse
    await waitFor(() => expect(container.querySelector(".lucide-chevron-right")).not.toBeNull());
    toggle(); // expand again: already loaded
    await waitFor(() => expect(container.querySelector(".lucide-chevron-down")).not.toBeNull());

    expect(api.listSubTags).toHaveBeenCalledTimes(1);
  });

  it("a scope switch remounts the page and shows only the new scope's tags", async () => {
    // MainLayout keys the routed page by organisation:project; the API client
    // attaches that selection as X-Org-Id / X-Proj-Id (see tags-api test).
    api.listTags.mockResolvedValue(page([tag("Org A tag", "a-1")]));
    const view = renderPage("org-A:proj-A1");
    expect(await screen.findByText("Org A tag")).toBeInTheDocument();

    api.listTags.mockResolvedValue(page([tag("Org B tag", "b-1")]));
    tenant.value = { loading: false, selectedOrganizationId: "org-B", selectedProjectId: "proj-B1" };
    view.rerender(
      <MemoryRouter>
        <div key="org-B:proj-B1">
          <TagsPage />
        </div>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Org B tag")).toBeInTheDocument();
    expect(screen.queryByText("Org A tag")).not.toBeInTheDocument();
    expect(api.listTags).toHaveBeenCalledTimes(2);
  });
});
