import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import LetterSummaryPage from "../LetterSummaryPage";

// Basic clipboard mock to avoid errors if triggered
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

// Current test document payload; fetch mock reads from this
let currentDoc: any = null;

// Mock global fetch for the two endpoints the page calls:
// - GET /api/documents/:id
// - GET /api/documents/:id/comments
beforeEach(() => {
  currentDoc = null;
  (window.localStorage as any).clear();
});

global.fetch = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  // Documents details
  const docMatch = url.match(/\/api\/documents\/([^/]+)$/);
  if (docMatch) {
    if (!currentDoc) {
      return new Response(JSON.stringify({}), { status: 404 });
    }
    return new Response(JSON.stringify(currentDoc), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  // Comments
  const commentsMatch = url.match(/\/api\/documents\/([^/]+)\/comments$/);
  if (commentsMatch) {
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  // Fallback
  return new Response(JSON.stringify({}), { status: 404 });
}) as any;

function renderAt(letterId: string) {
  return render(
    <MemoryRouter initialEntries={[`/letters/summary/${letterId}`]}>
      <Routes>
        <Route path="/letters/summary/:id" element={<LetterSummaryPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("LetterSummaryPage - References tab", () => {
  it("renders references when provided as an array of strings", async () => {
    currentDoc = {
      id: "test-array",
      letterNo: "LET-001",
      date: "2024-01-01",
      subject: "Subject A",
      from_: "Alice",
      to: "Bob",
      reference: ["Ref A", "Ref B", "Ref C"],
      keywords: [],
      contractual_clauses: [],
    };

    renderAt("test-array");

    // Wait for document to load (subject appears)
    await waitFor(() => {
      expect(screen.getByText("Subject A")).toBeInTheDocument();
    });

    // Switch to References tab
    const refTab = screen.getByRole("tab", { name: /references/i });
    await userEvent.click(refTab);

    // Expect list items for each reference
    await waitFor(() => {
      expect(screen.getByText("Ref A")).toBeInTheDocument();
      expect(screen.getByText("Ref B")).toBeInTheDocument();
      expect(screen.getByText("Ref C")).toBeInTheDocument();
    });
  });

  it("parses and renders references when provided as a newline/bullet string", async () => {
    currentDoc = {
      id: "test-string",
      letterNo: "LET-002",
      date: "2024-01-02",
      subject: "Subject B",
      from_: "Carol",
      to: "Dave",
      reference: "Line 1\n- Line 2\n3) Line 3",
      keywords: [],
      contractual_clauses: [],
    };

    renderAt("test-string");

    // Wait for document to load
    await waitFor(() => {
      expect(screen.getByText("Subject B")).toBeInTheDocument();
    });

    // Switch to References tab
    const refTab = screen.getByRole("tab", { name: /references/i });
    await userEvent.click(refTab);

    // Expect parsed bullet items (prefixes stripped)
    await waitFor(() => {
      expect(screen.getByText("Line 1")).toBeInTheDocument();
      expect(screen.getByText("Line 2")).toBeInTheDocument();
      expect(screen.getByText("Line 3")).toBeInTheDocument();
    });
  });

  it("shows empty state when no references exist", async () => {
    currentDoc = {
      id: "test-empty",
      letterNo: "LET-003",
      date: "2024-01-03",
      subject: "Subject C",
      from_: "Erin",
      to: "Frank",
      keywords: [],
      contractual_clauses: [],
    };

    renderAt("test-empty");

    // Wait for document to load
    await waitFor(() => {
      expect(screen.getByText("Subject C")).toBeInTheDocument();
    });

    // Switch to References tab
    const refTab = screen.getByRole("tab", { name: /references/i });
    await userEvent.click(refTab);

    // Expect empty state text
    await waitFor(() => {
      expect(screen.getByText(/No reference available/i)).toBeInTheDocument();
    });
  });
});
