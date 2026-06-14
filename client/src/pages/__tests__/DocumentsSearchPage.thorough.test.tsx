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

describe("DocumentsSearchPage - Thorough tests", () => {
  const user = userEvent.setup();
  let fetchSpy: any;

  beforeEach(() => {
    vi.useFakeTimers();
    fetchSpy = vi
      .spyOn(global, "fetch")
      .mockImplementation((input: any, init?: any) => {
        const url = typeof input === "string" ? input : input.url;
        // Organizations
        if (url.includes("/organizations")) {
          return Promise.resolve(
            mkFetchResponse([{ _id: "org-1", name: "Org 1" }])
          );
        }
        // Projects
        if (url.includes("/projects")) {
          return Promise.resolve(
            mkFetchResponse([
              { _id: "proj-1", name: "Project A", organization_id: "org-1" },
              { _id: "proj-2", name: "Project B", organization_id: "org-1" },
            ])
          );
        }
        // Users (page fetches)
        if (url.includes("/users")) {
          return Promise.resolve(
            mkFetchResponse([{ id: "U1", username: "Alice" }])
          );
        }
        // Library documents list (unrelated to search panel)
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

  it("paginates results in 'All Files' mode (next/prev buttons) and calls fullTextSearch with correct offsets", async () => {
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-1", filename: "File 1.pdf" },
      { _id: "doc-2", filename: "File 2.pdf" },
    ]);

    // Make fullTextSearch return 10 results per page, total 25
    (fullTextSearch as any)
      .mockResolvedValueOnce({
        results: Array.from({ length: 10 }).map((_, i) => ({
          _id: `r${i + 1}-p1`,
          filename: `Result ${i + 1} (p1)`,
          highlights: ["snippet p1"],
        })),
        total: 25,
      })
      .mockResolvedValueOnce({
        results: Array.from({ length: 10 }).map((_, i) => ({
          _id: `r${i + 1}-p2`,
          filename: `Result ${i + 1} (p2)`,
          highlights: ["snippet p2"],
        })),
        total: 25,
      })
      .mockResolvedValueOnce({
        results: Array.from({ length: 5 }).map((_, i) => ({
          _id: `r${i + 1}-p3`,
          filename: `Result ${i + 1} (p3)`,
          highlights: ["snippet p3"],
        })),
        total: 25,
      });

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
    await waitFor(() => expect(enhancedApi.getDocuments).toHaveBeenCalled());

    // Choose "All Files"
    await user.click(await screen.findByText("Select file"));
    await user.click(await screen.findByText("All Files"));

    // Enter query and debounce
    await user.type(
      await screen.findByPlaceholderText("Enter clause or text to search..."),
      "terms"
    );
    vi.advanceTimersByTime(500);

    // First page
    await waitFor(() => {
      expect(fullTextSearch).toHaveBeenCalledWith("terms", {
        projects: ["proj-1"],
        limit: 10,
        offset: 0,
        fuzzy: false,
      });
    });
    expect(await screen.findByText("Result 1 (p1)")).toBeInTheDocument();

    // Next page
    await user.click(screen.getByRole("button", { name: "" })); // Next chevron button has sr-only label "Next"
    // Click the next button by locating via title isn't present, use multiple buttons; select the second chevronRight occurrence.
    const chevrons = screen.getAllByRole("button");
    // The last pair are the pagination controls; click the last one
    await user.click(chevrons[chevrons.length - 1]);
    vi.advanceTimersByTime(100);

    await waitFor(() => {
      expect(fullTextSearch).toHaveBeenLastCalledWith("terms", {
        projects: ["proj-1"],
        limit: 10,
        offset: 10,
        fuzzy: false,
      });
    });
    expect(await screen.findByText("Result 1 (p2)")).toBeInTheDocument();

    // Next page (last)
    await user.click(chevrons[chevrons.length - 1]);
    vi.advanceTimersByTime(100);

    await waitFor(() => {
      expect(fullTextSearch).toHaveBeenLastCalledWith("terms", {
        projects: ["proj-1"],
        limit: 10,
        offset: 20,
        fuzzy: false,
      });
    });
    expect(await screen.findByText("Result 1 (p3)")).toBeInTheDocument();

    // Previous page
    // find previous chevron (first of the two)
    const chevrons2 = screen.getAllByRole("button");
    await user.click(chevrons2[chevrons2.length - 2]);
    vi.advanceTimersByTime(100);
    await waitFor(() => {
      expect(fullTextSearch).toHaveBeenLastCalledWith("terms", {
        projects: ["proj-1"],
        limit: 10,
        offset: 10,
        fuzzy: false,
      });
    });
  });

  it("handles backend errors for fullTextSearch and displays error state", async () => {
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-1", filename: "File 1.pdf" },
    ]);
    (fullTextSearch as any).mockRejectedValueOnce(new Error("Forbidden"));

    render(
      <MemoryRouter>
        <DocumentsSearchPage />
      </MemoryRouter>
    );

    await user.click(await screen.findByText("Select project"));
    await user.click(await screen.findByText("Project A"));
    await waitFor(() => expect(enhancedApi.getDocuments).toHaveBeenCalled());

    await user.click(await screen.findByText("Select file"));
    await user.click(await screen.findByText("All Files"));

    await user.type(
      await screen.findByPlaceholderText("Enter clause or text to search..."),
      "x"
    );
    vi.advanceTimersByTime(500);

    await waitFor(() => {
      expect(fullTextSearch).toHaveBeenCalled();
    });

    expect(await screen.findByText(/Error:/)).toBeInTheDocument();
  });

  it("handles backend errors for searchInDocument and displays error state", async () => {
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-1", filename: "File 1.pdf" },
    ]);
    (searchInDocument as any).mockRejectedValueOnce(new Error("Network error"));

    render(
      <MemoryRouter>
        <DocumentsSearchPage />
      </MemoryRouter>
    );

    await user.click(await screen.findByText("Select project"));
    await user.click(await screen.findByText("Project A"));
    await waitFor(() => expect(enhancedApi.getDocuments).toHaveBeenCalled());

    await user.click(await screen.findByText("Select file"));
    await user.click(await screen.findByText("File 1.pdf"));

    await user.type(
      await screen.findByPlaceholderText("Enter clause or text to search..."),
      "y"
    );
    vi.advanceTimersByTime(500);

    await waitFor(() => {
      expect(searchInDocument).toHaveBeenCalledWith("doc-1", "y", {});
    });

    expect(await screen.findByText(/Error:/)).toBeInTheDocument();
  });

  it("debounces rapid typing and dispatches only the final query for single-file search", async () => {
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-1", filename: "File 1.pdf" },
    ]);
    (searchInDocument as any).mockResolvedValue({
      matches: [{ page: 1, position: 10, context: "abc", highlight: "final" }],
      total: 1,
    });

    render(
      <MemoryRouter>
        <DocumentsSearchPage />
      </MemoryRouter>
    );

    await user.click(await screen.findByText("Select project"));
    await user.click(await screen.findByText("Project A"));
    await waitFor(() => expect(enhancedApi.getDocuments).toHaveBeenCalled());

    await user.click(await screen.findByText("Select file"));
    await user.click(await screen.findByText("File 1.pdf"));

    const input = await screen.findByPlaceholderText(
      "Enter clause or text to search..."
    );
    await user.type(input, "foo");
    vi.advanceTimersByTime(200); // before debounce fires
    await user.type(input, "bar");
    vi.advanceTimersByTime(500); // now final debounce

    await waitFor(() => {
      expect(searchInDocument).toHaveBeenCalledTimes(1);
      expect(searchInDocument).toHaveBeenCalledWith("doc-1", "foobar", {});
    });

    expect(await screen.findByText("File 1.pdf")).toBeInTheDocument();
  });

  it("renders long/special-character snippets safely", async () => {
    (enhancedApi.getDocuments as any).mockResolvedValueOnce([
      { _id: "doc-9", filename: "Spec.pdf" },
    ]);
    (fullTextSearch as any).mockResolvedValue({
      results: [
        {
          _id: "r1",
          filename: "Spec.pdf",
          highlights: ["Section 1.2 — Clause αβγ\nNewline & symbols <> \"'"],
        },
      ],
      total: 1,
    });

    render(
      <MemoryRouter>
        <DocumentsSearchPage />
      </MemoryRouter>
    );

    await user.click(await screen.findByText("Select project"));
    await user.click(await screen.findByText("Project A"));
    await waitFor(() => expect(enhancedApi.getDocuments).toHaveBeenCalled());

    await user.click(await screen.findByText("Select file"));
    await user.click(await screen.findByText("All Files"));

    await user.type(
      await screen.findByPlaceholderText("Enter clause or text to search..."),
      "spec"
    );
    vi.advanceTimersByTime(500);

    expect(await screen.findByText("Spec.pdf")).toBeInTheDocument();
    expect(await screen.findByText(/Section 1.2/)).toBeInTheDocument();
  });
});
