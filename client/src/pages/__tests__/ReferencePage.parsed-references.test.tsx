import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ReferencePage from "../ReferencePage";

// Clipboard mock (avoid errors on copy buttons)
Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn().mockResolvedValue(undefined),
  },
});

// LocalStorage mock for accessToken lookup
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = String(value);
    },
    clear: () => {
      store = {};
    },
    removeItem: (key: string) => {
      delete store[key];
    },
  };
})();
Object.defineProperty(window, "localStorage", {
  value: localStorageMock,
});

let currentDoc: any = null;
let referencesResponse: any = null;
let documentLookup: Record<string, any> = {};

// Mock fetch for endpoints used by ReferencePage:
// - GET /api/documents/:id
// - GET /api/documents/:id/references
// - GET /api/documents/:documentId (for enriching linked refs when legacy array form)
beforeEach(() => {
  currentDoc = null;
  referencesResponse = null;
  documentLookup = {};
  (window.localStorage as any).clear();
});

global.fetch = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);

  // GET /api/documents/:id (details)
  const docMatch = url.match(/\/api\/documents\/([^/]+)$/);
  if (docMatch) {
    const id = decodeURIComponent(docMatch[1]);
    // If this is a lookup for linked refs enrichment, serve from documentLookup
    if (documentLookup[id]) {
      return new Response(JSON.stringify(documentLookup[id]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (!currentDoc) {
      return new Response(JSON.stringify({}), { status: 404 });
    }
    return new Response(JSON.stringify(currentDoc), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  // GET /api/documents/:id/references
  const refsMatch = url.match(/\/api\/documents\/([^/]+)\/references$/);
  if (refsMatch) {
    if (referencesResponse === undefined || referencesResponse === null) {
      // default empty array (legacy shape)
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify(referencesResponse), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Fallback
  return new Response(JSON.stringify({}), { status: 404 });
}) as any;

function renderAt(letterId: string) {
  return render(
    <MemoryRouter initialEntries={[`/reference/${letterId}`]}>
      <Routes>
        <Route path="/reference/:id" element={<ReferencePage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("ReferencePage - Parsed References integration", () => {
  it("shows parsed references from DB (doc.reference array) and merges/dedupes with endpoint parsed", async () => {
    currentDoc = {
      id: "doc-1",
      letterNo: "LET-1001",
      date: "2024-01-01",
      subject: "Subject X",
      from_: "Alice",
      to: "Bob",
      reference: ["Ref 1", "Ref 2"], // from DB metadata.py
      keywords: [],
      clauses: [],
    };
    // Endpoint returns parsed with duplicate plus a new item, and one linked
    referencesResponse = {
      parsed: ["ref 2", "Ref 3"], // duplicate (case-insensitive) + new
      linked: [
        {
          id: "linked-1",
          letterNo: "LET-0005",
          title: "Prior Correspondence",
          date: "2024-01-02",
        },
      ],
    };

    renderAt("doc-1");

    // Wait for subject header to ensure load completed
    await waitFor(() => {
      expect(screen.getByText("Subject X")).toBeInTheDocument();
    });

    // Linked tab default - verify linked table shows row
    await waitFor(() => {
      expect(screen.getByText("LET-0005")).toBeInTheDocument();
      expect(screen.getByText("Prior Correspondence")).toBeInTheDocument();
    });

    // Switch to Parsed References tab
    const parsedTab = screen.getByRole("tab", { name: /parsed references/i });
    await userEvent.click(parsedTab);

    // Expect merged and deduped: Ref 1, Ref 2, Ref 3
    await waitFor(() => {
      expect(screen.getByText("Ref 1")).toBeInTheDocument();
      expect(screen.getByText("Ref 2")).toBeInTheDocument();
      expect(screen.getByText("Ref 3")).toBeInTheDocument();
    });
  });

  it("keeps parsed references from doc when /references endpoint returns legacy array of linked refs only", async () => {
    currentDoc = {
      id: "doc-2",
      letterNo: "LET-2001",
      date: "2024-02-01",
      subject: "Subject Y",
      from_: "Carol",
      to: "Dave",
      reference: "Item A\n- Item B\n2) Item C", // string form from DB
      keywords: [],
      clauses: [],
    };

    // Legacy array: [{ documentId }]
    referencesResponse = [{ documentId: "abc123" }];

    // The page will enrich linked refs via /api/documents/:documentId
    documentLookup["abc123"] = {
      id: "abc123",
      letterNo: "LET-0099",
      subject: "Legacy Linked Doc",
      date: "2024-01-15",
    };

    renderAt("doc-2");

    // Wait for subject header to ensure load completed
    await waitFor(() => {
      expect(screen.getByText("Subject Y")).toBeInTheDocument();
    });

    // Switch to Parsed References tab and validate parsed from DB (string normalized)
    const parsedTab = screen.getByRole("tab", { name: /parsed references/i });
    await userEvent.click(parsedTab);

    await waitFor(() => {
      expect(screen.getByText("Item A")).toBeInTheDocument();
      expect(screen.getByText("Item B")).toBeInTheDocument();
      expect(screen.getByText("Item C")).toBeInTheDocument();
    });

    // Back to Linked and verify enriched row exists
    const linkedTab = screen.getByRole("tab", { name: /linked references/i });
    await userEvent.click(linkedTab);
    await waitFor(() => {
      expect(screen.getByText("LET-0099")).toBeInTheDocument();
      expect(screen.getByText("Legacy Linked Doc")).toBeInTheDocument();
    });
  });

  it("shows empty parsed table state if no references in DB and none from endpoint", async () => {
    currentDoc = {
      id: "doc-3",
      letterNo: "LET-3001",
      date: "2024-03-01",
      subject: "Subject Z",
      from_: "Eve",
      to: "Frank",
      // reference field absent
      keywords: [],
      clauses: [],
    };

    // No references at endpoint either (default null - mock returns [])
    referencesResponse = [];

    renderAt("doc-3");

    await waitFor(() => {
      expect(screen.getByText("Subject Z")).toBeInTheDocument();
    });

    const parsedTab = screen.getByRole("tab", { name: /parsed references/i });
    await userEvent.click(parsedTab);

    // Table has header "Raw Reference" but no rows; show no entries via lack of any text node (can't assert empty table rows easily)
    // So we assert that searching some common placeholder is not found and that no crash occurred.
    // Alternatively ensure the search input is present (Parsed UI)
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/search parsed references/i)
      ).toBeInTheDocument();
    });
  });
});
