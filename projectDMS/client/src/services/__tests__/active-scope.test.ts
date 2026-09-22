import type { AxiosAdapter, InternalAxiosRequestConfig } from "axios";
import { beforeEach, describe, expect, it } from "vitest";

import { activeScopeHeaders, scopeErrorCode, setActiveScope } from "../active-scope";
import { createHttpClient } from "../http";

/** Resolve every request locally and remember the headers it would have sent. */
function capturingClient() {
  const sent: Record<string, string | undefined>[] = [];
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
    sent.push({
      org: config.headers.get("X-Org-Id") as string | undefined,
      project: config.headers.get("X-Proj-Id") as string | undefined,
    });
    return { data: {}, status: 200, statusText: "OK", headers: {}, config };
  };
  const client = createHttpClient();
  client.defaults.adapter = adapter;
  return { client, sent };
}

describe("active scope propagation", () => {
  beforeEach(() => setActiveScope({ organizationId: "", projectId: "" }));

  it("sends the current navbar selection on every API request", async () => {
    const { client, sent } = capturingClient();
    setActiveScope({ organizationId: "org-A", projectId: "proj-A1" });

    await client.get("/hindrances");
    await client.post("/hindrances", {});

    expect(sent).toEqual([
      { org: "org-A", project: "proj-A1" },
      { org: "org-A", project: "proj-A1" },
    ]);
  });

  it("switching project changes the very next request, and switching back restores it", async () => {
    const { client, sent } = capturingClient();
    setActiveScope({ organizationId: "org-A", projectId: "proj-A1" });
    await client.get("/hindrances");
    setActiveScope({ organizationId: "org-A", projectId: "proj-A2" });
    await client.get("/hindrances/h-1");
    setActiveScope({ organizationId: "org-A", projectId: "proj-A1" });
    await client.get("/hindrances/h-1");

    expect(sent.map((request) => request.project)).toEqual(["proj-A1", "proj-A2", "proj-A1"]);
  });

  it("sends no selection header when nothing is selected", async () => {
    const { client, sent } = capturingClient();
    await client.get("/hindrances");

    expect(sent).toEqual([{ org: undefined, project: undefined }]);
    expect(activeScopeHeaders()).toEqual({});
  });

  it("an explicitly set header is not overwritten", async () => {
    const { client, sent } = capturingClient();
    setActiveScope({ organizationId: "org-A", projectId: "proj-A1" });
    await client.get("/hindrances", { headers: { "X-Proj-Id": "proj-A2" } });

    expect(sent[0].project).toBe("proj-A2");
  });

  it("reads the backend's scope refusal codes and nothing else", () => {
    const refusal = (code: unknown) => ({ response: { data: { detail: { code } } } });
    expect(scopeErrorCode(refusal("context_forbidden"))).toBe("context_forbidden");
    expect(scopeErrorCode(refusal("selection_required"))).toBe("selection_required");
    expect(scopeErrorCode(refusal("other"))).toBeNull();
    expect(scopeErrorCode({ response: { data: { detail: "Not authorized" } } })).toBeNull();
  });
});
