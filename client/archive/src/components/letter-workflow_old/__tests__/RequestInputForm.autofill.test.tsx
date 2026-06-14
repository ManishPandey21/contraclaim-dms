import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import RequestInputForm from "../RequestInputForm";
import { api } from "@/services/api";

vi.mock("@/services/api", async () => {
  const actual = await vi.importActual<typeof import("@/services/api")>(
    "@/services/api"
  );
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
    },
  };
});

describe("RequestInputForm - auto-populate keypoints/summary", () => {
  const users = [{ id: "u1", name: "Alice", email: "a@example.com" }];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("loads document summary when documentId is provided and sets key points", async () => {
    // Arrange: mock document GET returning summary
    (api.get as any).mockImplementation((url: string) => {
      if (url === "/documents/doc-123") {
        return Promise.resolve({
          data: { summary: "Auto summary from document" },
        });
      }
      // suggested key points fallback (should not be called when document summary exists)
      if (url === "/input-requests/letter/let-1/suggested-key-points") {
        return Promise.resolve({ data: { key_points: "fallback points" } });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <RequestInputForm
        users={users as any}
        letterId="let-1"
        onRequestSent={() => {}}
        onCancel={() => {}}
        documentId="doc-123"
        onInputRequest={() => Promise.resolve()}
      />
    );

    // Assert loading feedback
    expect(await screen.findByText(/loading key points/i)).toBeInTheDocument();

    // Then key points should be populated from document summary
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Key Points (Auto)") as HTMLTextAreaElement)
          .value
      ).toContain("Auto summary from document")
    );
  });

  it("falls back to letter suggested key points when document has no summary", async () => {
    // Arrange: document GET returns no summary; fallback endpoint returns key points
    (api.get as any).mockImplementation((url: string) => {
      if (url === "/documents/doc-456") {
        return Promise.resolve({ data: { id: "doc-456" } });
      }
      if (url === "/input-requests/letter/let-2/suggested-key-points") {
        return Promise.resolve({
          data: { key_points: "suggested from letter" },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <RequestInputForm
        users={users as any}
        letterId="let-2"
        onRequestSent={() => {}}
        onCancel={() => {}}
        documentId="doc-456"
        onInputRequest={() => Promise.resolve()}
      />
    );

    await waitFor(() =>
      expect(
        (screen.getByLabelText("Key Points (Auto)") as HTMLTextAreaElement)
          .value
      ).toContain("suggested from letter")
    );
  });

  it("shows inline error when keypoints load fails", async () => {
    (api.get as any).mockRejectedValueOnce(new Error("boom"));

    render(
      <RequestInputForm
        users={users as any}
        letterId="let-3"
        onRequestSent={() => {}}
        onCancel={() => {}}
        documentId="doc-789"
        onInputRequest={() => Promise.resolve()}
      />
    );

    await waitFor(() =>
      expect(screen.getByText(/failed to load key points/i)).toBeInTheDocument()
    );
  });
});
