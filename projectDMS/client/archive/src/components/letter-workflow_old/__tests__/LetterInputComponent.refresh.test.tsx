import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import LetterInputComponent from "../LetterInputComponent";

vi.mock("@/services/api", () => {
  return {
    api: {
      get: vi.fn(),
      post: vi.fn(),
    },
  };
});

const { api } = await import("@/services/api");

const letterBase = {
  id: "L-1",
  title: "Test Letter",
  recipient: "Recipient A",
  subject: "Subject A",
  content: "",
  status: "Input",
  createdBy: { id: "U1", name: "Alice", email: "alice@example.com" },
  assignedTo: { id: "U2", name: "Bob", email: "bob@example.com" },
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
};

describe("LetterInputComponent - event-driven refresh of input requests", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reloads and shows newly created input request when input-request:created event is dispatched", async () => {
    // Mutable store to simulate backend list changes between loads
    let currentList: any[] = [];

    (api.get as any).mockImplementation(async (url: string) => {
      if (url === "/users") {
        return {
          data: [
            { id: "U1", username: "Alice", email: "alice@example.com" },
            { id: "U2", username: "Bob", email: "bob@example.com" },
          ],
        };
      }
      if (url.startsWith("/input-requests/letter/")) {
        return {
          data: currentList,
        };
      }
      return { data: [] };
    });

    // Initially no requests
    render(
      <LetterInputComponent
        letter={
          {
            ...letterBase,
            reference: { referenceNumber: "LET-999" },
          } as any
        }
        onInput={() => {}}
        onCancel={() => {}}
      />
    );

    // Users load
    await waitFor(() =>
      expect(api.get as any).toHaveBeenCalledWith(
        expect.stringContaining("/users")
      )
    );
    // Initial requests load (empty)
    await waitFor(() =>
      expect(api.get as any).toHaveBeenCalledWith(
        expect.stringContaining("/input-requests/letter/L-1"),
        expect.any(Object)
      )
    );

    // Now simulate a new request created on backend
    currentList = [
      {
        id: "R-new",
        requested_from: "U1",
        details: "Newly created request via event",
        due_date: "2025-10-11T00:00:00.000Z",
        created_at: "2025-09-02T00:00:00.000Z",
        status: "open",
      },
    ];

    // Dispatch the event the component listens for
    window.dispatchEvent(
      new CustomEvent("input-request:created", { detail: { letterId: "L-1" } })
    );

    // Component should re-fetch and render the new request
    await waitFor(() =>
      expect(
        screen.getByText("Newly created request via event")
      ).toBeInTheDocument()
    );
  });
});
