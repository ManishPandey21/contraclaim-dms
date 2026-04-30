import React from "react";
import { describe, it, beforeEach, afterEach, vi, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DocumentsSearchPage from "../DocumentsSearchPage";
import { MemoryRouter } from "react-router-dom";

vi.setConfig({ testTimeout: 15000 });

vi.mock("@/services/enhanced-api", () => {
  return {
    default: {
      getDocuments: vi.fn(),
    },
  };
});

vi.mock("@/services/search-api", () => {
  return {
    searchInDocument: vi.fn(),
    fullTextSearch: vi.fn(),
  };
});

const enhancedApi = (await import("@/services/enhanced-api")).default as any;
const { searchInDocument, fullTextSearch } = await import(
  "@/services/search-api"
);

const mkFetchResponse = (data: any, ok = true, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("DocumentsSearchPage - Require explicit file selection", () => {
  const user = userEvent.setup();
  let fetchSpy: any;

  beforeEach(() => {
    vi.useFakeTimers();

    fetchSpy = vi.spyOn(global, "fetch").mockImplementation((input: any) => {
      const url = typeof input === "string" ? input : input.url;
      if (url.includes("/projects")) {
        return Promise.resolve(
          mkFetchResponse([
            { _id: "proj-1", name: "Project A", organization_id: "org-1" },
            { _id: "proj-2", name: "Project B", organization_id: "org-1" },
          ])
        );
      }
      if (url.includes("/organizations")) {
        return Promise.resolve(
          mkFetchResponse([{ _id: "org-1", name: "Org 1" }])
        );
      }
      if (url.includes("/users")) {
        return Promise.resolve(
          mkFetchResponse([{ id: "U1", username: "Alice" }])
        );
      }
      if (url.includes("/documents?")) {
        return Promise.resolve(mkFetchResponse({ documents: [], total: 0 }));
      }
      return Promise.resolve(mkFetchResponse({}));
    });

    (enhancedApi.getDocuments as any).mockReset();
    (searchInDocument as any).mockReset();
    (fullTextSearch as any).mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    vi.useRealTimers();
  });

  it("does not dispatch search until a file or 'All Files' is selected", async () => {
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-1", filename: "File 1.pdf" },
      { _id: "doc-2", filename: "File 2.pdf" },
    ]);

    render(
      <MemoryRouter>
        <DocumentsSearchPage />
      </MemoryRouter>
    );

    // Select project
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("/projects"),
        expect.anything()
      )
    );
    await user.click(await screen.findByText("Select project"));
    await user.click(await screen.findByText("Project A"));

    // File dropdown should have been populated
    await waitFor(() =>
      expect(enhancedApi.getDocuments).toHaveBeenCalledWith({
        project_id: "proj-1",
        limit: 500,
      })
    );

    // Input should be disabled until a file is chosen
    const input = await screen.findByPlaceholderText(
      "Enter clause or text to search..."
    );
    expect(input).toBeDisabled();

    // Even if we try to type, no searches should be dispatched
    await user.type(input, "foo");
    vi.advanceTimersByTime(500);
    expect(searchInDocument).not.toHaveBeenCalled();
    expect(fullTextSearch).not.toHaveBeenCalled();

    // Helper guidance should be shown
    expect(
      await screen.findByText(
        /Choose a file or select .*All Files.* to enable searching/i
      )
    ).toBeInTheDocument();
  });

  it("dispatches fullTextSearch when 'All Files' is selected", async () => {
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-1", filename: "File 1.pdf" },
      { _id: "doc-2", filename: "File 2.pdf" },
    ]);
    (fullTextSearch as any).mockResolvedValue({
      results: [{ _id: "r1", filename: "Spec.pdf", highlights: ["match"] }],
      total: 1,
    });

    render(
      <MemoryRouter>
        <DocumentsSearchPage />
      </MemoryRouter>
    );

    // Select project
    await user.click(await screen.findByText("Select project"));
    await user.click(await screen.findByText("Project A"));
    await waitFor(() => expect(enhancedApi.getDocuments).toHaveBeenCalled());

    // Select All Files
    await user.click(await screen.findByText("Select file"));
    await user.click(await screen.findByText("All Files"));

    // Input should be enabled now
    const input = await screen.findByPlaceholderText(
      "Enter clause or text to search..."
    );
    expect(input).not.toBeDisabled();

    await user.type(input, "bar");
    vi.advanceTimersByTime(500);

    await waitFor(() => {
      expect(fullTextSearch).toHaveBeenCalledWith("bar", {
        projects: ["proj-1"],
        limit: 10,
        offset: 0,
        fuzzy: false,
      });
    });
  });

  it("dispatches searchInDocument when a specific file is selected", async () => {
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-1", filename: "File 1.pdf" },
      { _id: "doc-2", filename: "File 2.pdf" },
    ]);
    (searchInDocument as any).mockResolvedValue({
      matches: [{ page: 2, position: 42, context: "ctx", highlight: "hit" }],
      total: 1,
    });

    render(
      <MemoryRouter>
        <DocumentsSearchPage />
      </MemoryRouter>
    );

    // Select project
    await user.click(await screen.findByText("Select project"));
    await user.click(await screen.findByText("Project A"));
    await waitFor(() => expect(enhancedApi.getDocuments).toHaveBeenCalled());

    // Select specific file
    await user.click(await screen.findByText("Select file"));
    await user.click(await screen.findByText("File 1.pdf"));

    const input = await screen.findByPlaceholderText(
      "Enter clause or text to search..."
    );
    await user.type(input, "baz");
    vi.advanceTimersByTime(500);

    await waitFor(() => {
      expect(searchInDocument).toHaveBeenCalledWith("doc-1", "baz", {});
    });
  });
});
