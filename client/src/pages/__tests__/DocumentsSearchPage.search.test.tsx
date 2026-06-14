import React from "react";
import { describe, it, beforeEach, afterEach, vi, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DocumentsSearchPage from "../DocumentsSearchPage";
import { MemoryRouter } from "react-router-dom";

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

describe("DocumentsSearchPage - Clause/Text Search", () => {
  const user = userEvent.setup();
  let fetchSpy: any;

  beforeEach(() => {
    vi.useFakeTimers();
    fetchSpy = vi
      .spyOn(global, "fetch")
      .mockImplementation((input: any, init?: any) => {
        const url = typeof input === "string" ? input : input.url;
        // Projects list
        if (url.includes("/projects")) {
          return Promise.resolve(
            mkFetchResponse([
              { _id: "proj-1", name: "Project A", organization_id: "org-1" },
              { _id: "proj-2", name: "Project B", organization_id: "org-1" },
            ])
          );
        }
        // Orgs
        if (url.includes("/organizations")) {
          return Promise.resolve(
            mkFetchResponse([{ _id: "org-1", name: "Org 1" }])
          );
        }
        // Users (not needed here but page may call)
        if (url.includes("/users")) {
          return Promise.resolve(
            mkFetchResponse([{ id: "U1", username: "Alice" }])
          );
        }
        // Documents library (paged) - can return empty to avoid noise
        if (url.includes("/documents")) {
          // library list
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

  it("fetches project files when a project is selected, and searches within a selected single file", async () => {
    // Project doc listing
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-1", filename: "File 1.pdf" },
      { _id: "doc-2", filename: "File 2.pdf" },
    ]);
    (searchInDocument as any).mockResolvedValue({
      matches: [
        {
          page: 3,
          position: 120,
          context: "foo context",
          highlight: "foo HIGHLIGHT",
        },
      ],
      total: 1,
    });

    render(
      <MemoryRouter>
        <DocumentsSearchPage />
      </MemoryRouter>
    );

    // Wait for projects to load
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("/projects"),
        expect.anything()
      )
    );

    // Open Project Select and choose Project A
    await user.click(await screen.findByText("Select project"));
    await user.click(await screen.findByText("Project A"));

    // Persisted scope and get documents for project
    await waitFor(() => {
      expect(localStorage.getItem("proj_id")).toBe("proj-1");
    });
    await waitFor(() => {
      expect(enhancedApi.getDocuments).toHaveBeenCalledWith({
        project_id: "proj-1",
        limit: 500,
      });
    });

    // Open File Select and choose File 1
    await user.click(await screen.findByText("Select file"));
    await user.click(await screen.findByText("File 1.pdf"));

    // Type search text
    await user.type(
      await screen.findByPlaceholderText("Enter clause or text to search..."),
      "foo"
    );
    // Debounce
    vi.advanceTimersByTime(500);

    // Should search within selected doc only
    await waitFor(() => {
      expect(searchInDocument).toHaveBeenCalledWith("doc-1", "foo", {});
    });

    // Displays result
    await waitFor(() => {
      expect(screen.getByText("File 1.pdf")).toBeInTheDocument();
      expect(screen.getByText(/Page 3/)).toBeInTheDocument();
      expect(screen.getByText(/foo HIGHLIGHT|foo context/)).toBeInTheDocument();
    });
  });

  it('searches across "All Files" in the selected project via fullTextSearch', async () => {
    // Project doc listing
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-1", filename: "File 1.pdf" },
    ]);
    (fullTextSearch as any).mockResolvedValue({
      results: [
        { _id: "doc-x", filename: "Spec.pdf", highlights: ["bar match"] },
        { _id: "doc-y", filename: "BoQ.pdf", highlights: ["another bar"] },
      ],
      total: 2,
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

    // Choose All Files
    await user.click(await screen.findByText("Select file"));
    await user.click(await screen.findByText("All Files"));

    // Enter query
    await user.type(
      await screen.findByPlaceholderText("Enter clause or text to search..."),
      "bar"
    );
    vi.advanceTimersByTime(500);

    // Ensure fullTextSearch called with project scoping
    await waitFor(() => {
      expect(fullTextSearch).toHaveBeenCalledWith("bar", {
        projects: ["proj-1"],
        limit: 10,
        offset: 0,
        fuzzy: false,
      });
    });

    // Show result filenames and snippets
    await waitFor(() => {
      expect(screen.getByText("Spec.pdf")).toBeInTheDocument();
      expect(screen.getByText("BoQ.pdf")).toBeInTheDocument();
    });
  });
});
