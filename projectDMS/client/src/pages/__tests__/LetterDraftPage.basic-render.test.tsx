import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import LetterDraftPage from "../LetterDraftPage";

const apiGetMock = vi.fn();

vi.mock("@/services/api", () => ({
  api: {
    get: apiGetMock,
  },
}));

vi.mock("@/hooks/useLanggraphDraft", () => ({
  useLanggraphDraft: () => ({
    data: null,
    loading: false,
    error: null,
    runDraft: vi.fn(),
    reset: vi.fn(),
  }),
}));

vi.mock("@/hooks/useLetterGraphRuns", () => ({
  useLetterGraphRuns: () => ({
    data: null,
    loading: false,
    error: null,
    fetchRun: vi.fn(),
    setData: vi.fn(),
  }),
}));

vi.mock("@/config/features", () => ({
  LANGGRAPH_ENABLED: false,
}));

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

function renderAt(letterId: string) {
  return render(
    <MemoryRouter initialEntries={[`/letters/${letterId}/draft`]}>
      <Routes>
        <Route path="/letters/:id/draft" element={<LetterDraftPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("LetterDraftPage without LangGraph enabled", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    (window.localStorage as any).clear();
  });

  it("fetches letter metadata and displays it", async () => {
    apiGetMock.mockResolvedValueOnce({
      data: {
        id: "abc123",
        subject: "Test Letter Subject",
        recipient: "Chief Engineer",
        status: "Draft",
      },
    });

    renderAt("abc123");

    await waitFor(() =>
      expect(apiGetMock).toHaveBeenCalledWith("/letters/abc123")
    );

    await waitFor(() =>
      expect(
        screen.getByText("Test Letter Subject", { exact: false })
      ).toBeInTheDocument()
    );

    expect(screen.getByText("LangGraph Draft Workspace")).toBeInTheDocument();
  });
});
