import type { AxiosAdapter, InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { setActiveScope } from "../active-scope";
import { api } from "../api";
import { listSubTags, listSubTagsBatch, listTags } from "../tags-api";

// The Tags page is fixed for rate limiting, not by loosening scope: every Tags
// read still travels with the navbar selection, and switching it changes the
// very next request.
describe("tags-api scope headers", () => {
  const sent: Array<{ url?: string; org?: string; project?: string; params?: unknown; uri?: string }> = [];
  const original = api.defaults.adapter;

  beforeEach(() => {
    sent.length = 0;
    const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
      sent.push({
        url: config.url,
        org: config.headers.get("X-Org-Id") as string | undefined,
        project: config.headers.get("X-Proj-Id") as string | undefined,
        params: config.params,
        uri: api.getUri(config),
      });
      const subtags = config.url === "/tags/subtags/batch"
        ? [{ _id: "s-1", name: "Late payment", tag_id: "tag-1" }, { id: "s-2", name: "Retention", tagId: "tag-2" }]
        : [];
      return { data: { tags: [], subtags, total: 0 }, status: 200, statusText: "OK", headers: {}, config };
    };
    api.defaults.adapter = adapter;
  });

  afterEach(() => {
    api.defaults.adapter = original;
    setActiveScope({ organizationId: "", projectId: "" });
  });

  it("sends the selected organisation and project, and follows a switch", async () => {
    setActiveScope({ organizationId: "org-A", projectId: "proj-A1" });
    await listTags({ search: "payment", page: 1, limit: 10 });
    await listSubTags("tag-1");

    setActiveScope({ organizationId: "org-B", projectId: "proj-B1" });
    await listTags({ page: 2, limit: 10 });

    expect(sent).toEqual([
      { url: "/tags", org: "org-A", project: "proj-A1", params: { skip: 0, limit: 10, search: "payment" } },
      { url: "/tags/tag-1/subtags", org: "org-A", project: "proj-A1", params: { skip: 0, limit: 200 } },
      { url: "/tags", org: "org-B", project: "proj-B1", params: { skip: 10, limit: 10 } },
    ].map((row, i) => ({ ...row, uri: sent[i].uri })));
  });

  it("batches subtags in one request with repeated tag_ids keys, de-duplicated", async () => {
    setActiveScope({ organizationId: "org-A", projectId: "proj-A1" });

    const subtags = await listSubTagsBatch(["tag-1", " tag-2 ", "tag-1", ""]);

    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ url: "/tags/subtags/batch", org: "org-A", project: "proj-A1" });
    expect(sent[0].uri).toMatch(/\/tags\/subtags\/batch\?tag_ids=tag-1&tag_ids=tag-2$/);
    expect(subtags.map((s) => [s._id, s.name, s.tag_id])).toEqual([
      ["s-1", "Late payment", "tag-1"],
      ["s-2", "Retention", "tag-2"],
    ]);
  });

  it("makes no request for an empty batch", async () => {
    expect(await listSubTagsBatch([])).toEqual([]);
    expect(await listSubTagsBatch(["", "  "])).toEqual([]);
    expect(sent).toHaveLength(0);
  });
});
