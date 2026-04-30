import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "./test-wrapper.new";
import { PartiesInvolvedPageWrapper } from "@/pages/PartiesInvolvedPageWrapper.new";
import { mockData } from "./test-helpers";

// Mock the API calls
vi.mock("@/services/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

describe("Parties Integration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the parties page with loading state", () => {
    render(<PartiesInvolvedPageWrapper />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("loads and displays parties", async () => {
    const { api } = await import("@/services/api");
    vi.mocked(api.get).mockResolvedValueOnce({ data: mockData.parties });

    render(<PartiesInvolvedPageWrapper />);

    await waitFor(() => {
      expect(screen.getByText(mockData.parties[0].name)).toBeInTheDocument();
    });
  });

  it("creates a new party", async () => {
    const { api } = await import("@/services/api");
    const newParty = mockData.generateParty({
      name: "New Test Organization",
    });

    vi.mocked(api.post).mockResolvedValueOnce({ data: newParty });
    vi.mocked(api.get).mockResolvedValueOnce({
      data: [...mockData.parties, newParty],
    });

    render(<PartiesInvolvedPageWrapper />);

    // Click add button
    fireEvent.click(screen.getByText(/add/i));

    // Fill form
    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: newParty.name },
    });

    // Submit form
    fireEvent.click(screen.getByText(/save/i));

    await waitFor(() => {
      expect(screen.getByText(newParty.name)).toBeInTheDocument();
    });
  });

  it("deletes a party", async () => {
    const { api } = await import("@/services/api");
    const partyToDelete = mockData.parties[0];
    const remainingParties = mockData.parties.slice(1);

    vi.mocked(api.get).mockResolvedValueOnce({ data: mockData.parties });
    vi.mocked(api.delete).mockResolvedValueOnce({ data: null });
    vi.mocked(api.get).mockResolvedValueOnce({ data: remainingParties });

    render(<PartiesInvolvedPageWrapper />);

    await waitFor(() => {
      expect(screen.getByText(partyToDelete.name)).toBeInTheDocument();
    });

    // Click delete button
    fireEvent.click(screen.getByTestId(`delete-party-${partyToDelete.id}`));

    // Confirm deletion
    fireEvent.click(screen.getByText(/confirm/i));

    await waitFor(() => {
      expect(screen.queryByText(partyToDelete.name)).not.toBeInTheDocument();
    });
  });

  it("handles API errors gracefully", async () => {
    const { api } = await import("@/services/api");
    vi.mocked(api.get).mockRejectedValueOnce(
      new Error("Failed to fetch parties")
    );

    render(<PartiesInvolvedPageWrapper />);

    await waitFor(() => {
      expect(screen.getByText(/error/i)).toBeInTheDocument();
    });
  });

  it("filters parties by search term", async () => {
    const { api } = await import("@/services/api");
    vi.mocked(api.get).mockResolvedValueOnce({ data: mockData.parties });

    render(<PartiesInvolvedPageWrapper />);

    await waitFor(() => {
      expect(screen.getByText(mockData.parties[0].name)).toBeInTheDocument();
    });

    // Type in search box
    fireEvent.change(screen.getByPlaceholderText(/search/i), {
      target: { value: mockData.parties[0].name },
    });

    // Should only show matching party
    expect(screen.getByText(mockData.parties[0].name)).toBeInTheDocument();
    expect(
      screen.queryByText(mockData.parties[1].name)
    ).not.toBeInTheDocument();
  });
});
