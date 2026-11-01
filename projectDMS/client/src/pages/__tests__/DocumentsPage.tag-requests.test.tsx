import type { AxiosAdapter, InternalAxiosRequestConfig } from "axios";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/services/api";

const tenant = vi.hoisted(() => ({ value: null as null | Record<string, unknown> }));
vi.mock("@/contexts/TenantContext", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/contexts/TenantContext")>()),
  useOptionalTenant: () => tenant.value,
}));

vi.mock("@/services/plan-settings-api", () => ({
  getEffectivePlanServices: vi.fn(async () => ({ effective: { organizations: {}, projects: {} } })),
}));

import DocumentsPage from "../DocumentsPage";

/**
 * The Documents page must spend a bounded number of Tags reads per mount,
 * whatever the size of the tag catalogue: one `GET /tags` and at most one
 * batched subtag lookup for the rows on screen. It used to fetch
 * `/tags/{id}/subtags` for every tag in the catalogue, which exhausted the
 * per-user Tags read budget and locked the Tags page out with 429.
 */

type Doc = { id: string; tag: string; subTag: string };

const catalogue = (size: number) =>
  Array.from({ length: size }, (_, i) => ({ _id: `tag-${i}`, name: `Tag ${i}` }));

const documentRow = ({ id, tag, subTag }: Doc) => ({
  _id: id,
  filename: `${id}.pdf`,
  filetype: "application/pdf",
  filesize: 1024,
  uploadType: "incoming",
  letterNo: `L-${id}`,
  date: "2026-09-01",
  subject: `Subject ${id}`,
  from_: "Engineer",
  to: "Contractor",
  tags: tag ? [tag] : [],
  subTags: subTag ? [subTag] : [],
  status: "Received",
  organization_id: "org-a",
  project_id: "proj-a1",
  project_name: "A1",
  createdAt: "2026-09-01T00:00:00Z",
});

const json = (body: unknown) =>
  ({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) }) as Response;

type Batch = { tagIds: string[]; resolve: (subtags: unknown[]) => void };

let fetchedUrls: string[] = [];
let batches: Batch[] = [];
let batchLog: string[][] = [];
let batchAutoReply: ((tagIds: string[]) => unknown[]) | null = null;
const originalAdapter = api.defaults.adapter;

function serve({ tags, pages }: { tags: unknown[]; pages: Record<number, Doc[]>; total?: number }) {
  const total = Object.values(pages).reduce((sum, rows) => sum + rows.length, 0);
  vi.spyOn(global, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    fetchedUrls.push(url);
    if (url.includes("/documents?")) {
      const skip = Number(new URL(url, "http://x").searchParams.get("skip") || 0);
      const rows = pages[Math.floor(skip / 25) + 1] || [];
      return json({ documents: rows.map(documentRow), total: Math.max(total, 26) });
    }
    if (/\/tags(\?|$)/.test(url)) return json({ tags, total: tags.length });
    return json([]);
  });
  // Axios requests (the batch lookup goes through the shared API client).
  const adapter: AxiosAdapter = (config: InternalAxiosRequestConfig) =>
    new Promise((resolve) => {
      const url = String(config.url);
      fetchedUrls.push(url);
      if (url === "/tags/subtags/batch") {
        const tagIds = [...(config.params?.tag_ids ?? [])];
        batchLog.push(tagIds);
        const reply = (subtags: unknown[]) =>
          resolve({ data: { subtags, truncated: false }, status: 200, statusText: "OK", headers: {}, config });
        if (batchAutoReply) reply(batchAutoReply(tagIds));
        else batches.push({ tagIds, resolve: reply });
        return;
      }
      resolve({ data: {}, status: 200, statusText: "OK", headers: {}, config });
    });
  api.defaults.adapter = adapter;
}

const count = (predicate: (url: string) => boolean) => fetchedUrls.filter(predicate).length;
const catalogueReads = () => count((url) => /\/tags(\?|$)/.test(url));
const batchReads = () => count((url) => url.endsWith("/tags/subtags/batch"));
const perTagReads = () => count((url) => /\/tags\/[^/]+\/subtags/.test(url) && !url.includes("/tags/subtags/"));

const subtagsFor = (tagIds: string[]) =>
  tagIds.map((tagId) => ({ _id: `sub-of-${tagId}`, name: `Subtag of ${tagId}`, tag_id: tagId }));

function renderPage(key = "org-a:proj-a1") {
  return render(
    <MemoryRouter initialEntries={["/documents"]}>
      <div key={key}>
        <DocumentsPage />
      </div>
    </MemoryRouter>,
  );
}

const rowsUsing = (...tags: string[]): Doc[] =>
  tags.map((tag, i) => ({ id: `doc-${i}-${tag}`, tag, subTag: tag ? `sub-of-${tag}` : "" }));

describe("DocumentsPage tag reads", () => {
  beforeEach(() => {
    fetchedUrls = [];
    batches = [];
    batchLog = [];
    batchAutoReply = subtagsFor;
    tenant.value = null;
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    api.defaults.adapter = originalAdapter;
  });

  it.each([5, 50, 500])(
    "makes exactly two tag reads with a %i-tag catalogue: one catalogue, one batch",
    async (size) => {
      serve({ tags: catalogue(size), pages: { 1: rowsUsing("tag-1", "tag-3", "tag-3", "tag-4") } });
      renderPage();

      expect(await screen.findByText("Subtag of tag-4", {}, { timeout: 5000 })).toBeInTheDocument();
      await new Promise((resolve) => setTimeout(resolve, 100));
      expect(catalogueReads()).toBe(1);
      expect(batchReads()).toBe(1);
      expect(perTagReads()).toBe(0);
      expect(catalogueReads() + batchReads() + perTagReads()).toBe(2);
    },
  );

  it("asks the batch for each displayed tag once, sorted and de-duplicated", async () => {
    batchAutoReply = null;
    serve({ tags: catalogue(50), pages: { 1: rowsUsing("tag-7", "tag-2", "tag-7", "tag-20") } });
    renderPage();

    await waitFor(() => expect(batches).toHaveLength(1));
    expect(batches[0].tagIds).toEqual(["tag-2", "tag-20", "tag-7"]);
  });

  it("makes no batch request when no displayed row has a tag", async () => {
    serve({ tags: catalogue(50), pages: { 1: rowsUsing("", "") } });
    renderPage();

    expect(await screen.findByText("Subject doc-0-")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(catalogueReads()).toBe(1);
    expect(batchReads()).toBe(0);
  });

  it("resolves a legacy tag stored by name to its canonical id", async () => {
    batchAutoReply = null;
    serve({
      tags: catalogue(50),
      pages: { 1: [{ id: "legacy", tag: "Tag 5", subTag: "Legacy subtag name" }] },
    });
    renderPage();

    await waitFor(() => expect(batches).toHaveLength(1));
    expect(batches[0].tagIds).toEqual(["tag-5"]);
    await act(async () => batches[0].resolve([]));
    // A subtag stored by name simply renders as that name.
    expect(screen.getAllByText("Legacy subtag name").length).toBeGreaterThan(0);
  });

  it("renders a subtag stored by id as its human-readable name", async () => {
    serve({ tags: catalogue(50), pages: { 1: rowsUsing("tag-3") } });
    renderPage();

    expect(await screen.findByText("Subtag of tag-3")).toBeInTheDocument();
    expect(screen.queryByText("sub-of-tag-3")).not.toBeInTheDocument();
  });

  it("a page change with a new tag set makes one new batch request", async () => {
    serve({ tags: catalogue(50), pages: { 1: rowsUsing("tag-1", "tag-2"), 2: rowsUsing("tag-8") } });
    renderPage();
    expect(await screen.findByText("Subtag of tag-2")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Next").closest("button")!);

    expect(await screen.findByText("Subtag of tag-8")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(batchReads()).toBe(2);
    expect(catalogueReads()).toBe(1);
    expect(perTagReads()).toBe(0);
  });

  it("a late answer for the previous page never replaces the current page's names", async () => {
    batchAutoReply = null;
    serve({ tags: catalogue(50), pages: { 1: rowsUsing("tag-1"), 2: rowsUsing("tag-8") } });
    renderPage();
    await waitFor(() => expect(batches).toHaveLength(1));

    fireEvent.click(screen.getByText("Next").closest("button")!);
    await waitFor(() => expect(batches).toHaveLength(2));

    await act(async () => batches[1].resolve(subtagsFor(["tag-8"])));
    expect(await screen.findByText("Subtag of tag-8")).toBeInTheDocument();

    // Page 1's lookup answers last, with a different name for the same id.
    await act(async () =>
      batches[0].resolve([{ _id: "sub-of-tag-8", name: "STALE NAME", tag_id: "tag-1" }]),
    );
    expect(screen.queryByText("STALE NAME")).not.toBeInTheDocument();
    expect(screen.getByText("Subtag of tag-8")).toBeInTheDocument();
  });

  it("a scope remount repeats the bounded pair, never the per-tag loop", async () => {
    serve({ tags: catalogue(50), pages: { 1: rowsUsing("tag-2", "tag-7") } });
    const view = renderPage("org-a:proj-a1");
    expect(await screen.findByText("Subtag of tag-7")).toBeInTheDocument();

    view.rerender(
      <MemoryRouter initialEntries={["/documents"]}>
        <div key="org-a:proj-a2">
          <DocumentsPage />
        </div>
      </MemoryRouter>,
    );
    await waitFor(() => expect(batchReads()).toBe(2));
    await new Promise((resolve) => setTimeout(resolve, 100));

    expect(catalogueReads()).toBe(2);
    expect(batchReads()).toBe(2);
    expect(perTagReads()).toBe(0);
  });

  it("waits for the navbar scope before reading the catalogue, then reads it once", async () => {
    tenant.value = { loading: true, selectedOrganizationId: "", selectedProjectId: "" };
    serve({ tags: catalogue(50), pages: { 1: rowsUsing("tag-2") } });
    const view = renderPage();
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(catalogueReads()).toBe(0);
    expect(batchReads()).toBe(0);

    tenant.value = { loading: false, selectedOrganizationId: "org-a", selectedProjectId: "proj-a1" };
    view.rerender(
      <MemoryRouter initialEntries={["/documents"]}>
        <div key="org-a:proj-a1">
          <DocumentsPage />
        </div>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Subtag of tag-2")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(catalogueReads()).toBe(1);
    expect(batchReads()).toBe(1);
  });

  it("still names subtags of an ObjectId tag missing from the loaded catalogue page", async () => {
    const outside = "65f1c0ffee0ddba11c0ffee0";
    serve({ tags: catalogue(50), pages: { 1: rowsUsing(outside) } });
    renderPage();

    expect(await screen.findByText(`Subtag of ${outside}`)).toBeInTheDocument();
    expect(batchReads()).toBe(1);
  });

  it("paging back to tags already loaded costs no further read", async () => {
    serve({ tags: catalogue(50), pages: { 1: rowsUsing("tag-1", "tag-2"), 2: rowsUsing("tag-2", "tag-8") } });
    renderPage();
    expect(await screen.findByText("Subtag of tag-2", {}, { timeout: 5000 })).toBeInTheDocument();

    fireEvent.click(screen.getByText("Next").closest("button")!);
    expect(await screen.findByText("Subtag of tag-8", {}, { timeout: 5000 })).toBeInTheDocument();
    // Page 2 asked only for the tag page 1 had not loaded.
    expect(batchLog.at(-1)).toEqual(["tag-8"]);

    fireEvent.click(screen.getByText("Previous").closest("button")!);
    expect(await screen.findByText("Subtag of tag-1", {}, { timeout: 5000 })).toBeInTheDocument();
    await waitFor(() => expect(fetchedUrls.filter((u) => u.includes("skip=0")).length).toBe(2));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(batchReads()).toBe(2);
  });
});
