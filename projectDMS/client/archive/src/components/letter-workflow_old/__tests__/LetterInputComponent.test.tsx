import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

describe("LetterInputComponent", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Mock users
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
          data: [
            {
              id: "R1",
              requested_from: "U1",
              details: "Please provide site progress details.",
              due_date: "2025-10-10T00:00:00.000Z",
              created_at: "2025-09-01T00:00:00.000Z",
              status: "open",
            },
          ],
        };
      }
      return { data: [] };
    });
    (api.post as any).mockResolvedValue({ data: { ok: true } });
  });

  it("shows Letter Number from reference or 'New Draft' fallback", async () => {
    render(
      <LetterInputComponent
        letter={
          {
            ...letterBase,
            reference: { referenceNumber: "LET-1234" },
          } as any
        }
        onInput={() => {}}
        onCancel={() => {}}
      />
    );
    await waitFor(() => expect(api.get as any).toHaveBeenCalledWith("/users"));
    expect(screen.getByText("Letter Number")).toBeInTheDocument();
    expect(screen.getByText("LET-1234")).toBeInTheDocument();

    // No reference => New Draft
    render(
      <LetterInputComponent
        letter={{ ...letterBase, id: "L-2" } as any}
        onInput={() => {}}
        onCancel={() => {}}
      />
    );
    await waitFor(() => expect(api.get as any).toHaveBeenCalledWith("/users"));
    expect(screen.getAllByText("Letter Number")[1]).toBeInTheDocument();
    expect(screen.getByText("New Draft")).toBeInTheDocument();
  });

  it("loads and renders pending input requests, and submits response to move to Draft", async () => {
    const onInput = vi.fn();

    render(
      <LetterInputComponent
        letter={
          {
            ...letterBase,
            reference: { referenceNumber: "LET-555" },
          } as any
        }
        onInput={onInput}
        onCancel={() => {}}
      />
    );

    // Pending request appears
    await waitFor(() =>
      expect(api.get as any).toHaveBeenCalledWith(
        expect.stringContaining("/users")
      )
    );
    await waitFor(() =>
      expect(api.get as any).toHaveBeenCalledWith(
        expect.stringContaining("/input-requests/letter/L-1"),
        expect.any(Object)
      )
    );
    expect(screen.getByText("Input Requests")).toBeInTheDocument();
    expect(
      screen.getByText("Please provide site progress details.")
    ).toBeInTheDocument();

    // Provide response
    await userEvent.type(
      screen.getByLabelText(/Your Response/i),
      "Progress: 80%"
    );
    await userEvent.click(
      screen.getByRole("button", {
        name: /Provide Input & Continue to Draft/i,
      })
    );

    await waitFor(() =>
      expect(api.post as any).toHaveBeenCalledWith(
        "/input-requests/R1/respond",
        {
          message: "Progress: 80%",
        }
      )
    );

    await waitFor(() => expect(onInput).toHaveBeenCalled());
    const updatedLetter = (onInput as any).mock.calls[0][0];
    expect(updatedLetter.status).toBe("Draft");
    expect(updatedLetter.content).toBe("Progress: 80%");
    expect(updatedLetter.inputRequests[0].response).toBe("Progress: 80%");
  });
});
