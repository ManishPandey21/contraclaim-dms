import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setActiveScope } from "../active-scope";

/**
 * CL-4A: the Documents register, Document Viewer and Dashboard reach the API
 * through `authenticatedFetch` / the global fetch wrapper, not the axios client.
 * Before CL-4A those requests carried no selection, so a backend that holds a
 * record to the navbar project would have answered 400 to a correct UI.
 */
describe("active scope on the fetch transports", () => {
  const sent: { url: string; org: string | null; project: string | null }[] = [];

  beforeEach(async () => {
    sent.length = 0;
    vi.resetModules();
    delete (window as { __contraclaimCsrfFetchInstalled?: boolean }).__contraclaimCsrfFetchInstalled;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const headers = new Headers(init?.headers);
        sent.push({ url: String(input), org: headers.get("X-Org-Id"), project: headers.get("X-Proj-Id") });
        return new Response("{}", { status: 200 });
      }),
    );
    window.fetch = globalThis.fetch;
    setActiveScope({ organizationId: "", projectId: "" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("authenticatedFetch sends the current selection, and a switch changes the next request", async () => {
    const { authenticatedFetch } = await import("../http");
    const { setActiveScope: select } = await import("../active-scope");
    select({ organizationId: "org-A", projectId: "proj-A1" });
    await authenticatedFetch("/api/documents?limit=20");
    select({ organizationId: "org-A", projectId: "proj-A2" });
    await authenticatedFetch("/api/documents/doc-1");

    expect(sent.map(({ org, project }) => ({ org, project }))).toEqual([
      { org: "org-A", project: "proj-A1" },
      { org: "org-A", project: "proj-A2" },
    ]);
  });

  it("the global fetch wrapper sends the selection on API requests only", async () => {
    const { installCsrfFetchInterceptor } = await import("../http");
    const { setActiveScope: select } = await import("../active-scope");
    installCsrfFetchInterceptor();
    select({ organizationId: "org-A", projectId: "proj-A1" });
    await window.fetch("/api/dashboard/stats");
    await window.fetch("https://storage.example.com/bucket/object?X-Amz-Signature=abc");

    expect(sent[0]).toMatchObject({ org: "org-A", project: "proj-A1" });
    expect(sent[1]).toMatchObject({ org: null, project: null });
  });

  it("sends no selection when nothing is selected, and never overwrites an explicit header", async () => {
    const { authenticatedFetch } = await import("../http");
    const { setActiveScope: select } = await import("../active-scope");
    await authenticatedFetch("/api/documents");
    select({ organizationId: "org-A", projectId: "proj-A1" });
    await authenticatedFetch("/api/documents", { headers: { "X-Proj-Id": "proj-A2" } });

    expect(sent[0]).toMatchObject({ org: null, project: null });
    expect(sent[1]).toMatchObject({ project: "proj-A2" });
  });

  it("a stored file is fetched from storage without the selection or credentials", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(input), init });
        if (String(input).startsWith("/api/")) {
          return new Response(JSON.stringify({ url: "https://storage.example.com/o?X-Amz-Signature=s" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        return new Response("PDF", { status: 200, headers: { "content-type": "application/pdf" } });
      }),
    );
    const { fetchApiFileBlob } = await import("../http");
    const { setActiveScope: select } = await import("../active-scope");
    select({ organizationId: "org-A", projectId: "proj-A1" });

    const blob = await fetchApiFileBlob("/api/documents/doc-1/download");

    expect(blob.size).toBe(3); // "PDF" from storage (jsdom Blob has no .text())
    expect(calls[0].url).toBe("/api/documents/doc-1/download?redirect=false");
    expect(new Headers(calls[0].init?.headers).get("X-Proj-Id")).toBe("proj-A1");
    expect(calls[1].url).toBe("https://storage.example.com/o?X-Amz-Signature=s");
    expect(new Headers(calls[1].init?.headers).get("X-Proj-Id")).toBeNull();
    expect(calls[1].init?.credentials).toBe("omit");
  });

  it("a local file streams from the API, and a scope refusal surfaces its status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("doc-1")
          ? new Response("BYTES", { status: 200, headers: { "content-type": "application/pdf" } })
          : new Response(JSON.stringify({ detail: { code: "context_forbidden" } }), { status: 403 }),
      ),
    );
    const { fetchApiFileBlob } = await import("../http");

    expect((await fetchApiFileBlob("/api/documents/doc-1/download")).size).toBe(5); // "BYTES"
    await expect(fetchApiFileBlob("/api/documents/doc-2/download")).rejects.toMatchObject({ status: 403 });
  });
});
