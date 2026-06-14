import React from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "./test-wrapper.new";
import { PartiesInvolvedPageWrapper } from "@/pages/PartiesInvolvedPageWrapper.new";
import { mockData } from "./test-helpers";
import { mockApi, resetApiMocks } from "./mocks/api";

describe("Parties Integration", () => {
  beforeEach(() => {
    resetApiMocks();
  });

  it("renders the parties page with loading state", () => {
    render(<PartiesInvolvedPageWrapper />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("loads and displays parties", async () => {
    mockApi.get.mockResolvedValueOnce({ data: mockData.parties });

    render(<PartiesInvolvedPageWrapper />);

    await waitFor(() => {
      expect(screen.getByText(mockData.parties[0].name)).toBeInTheDocument();
    });
  });

  it("creates a new party", async () => {
    const newParty = mockData.generateParty({
      name: "New Test Organization",
    });

    mockApi.post.mockResolvedValueOnce({ data: newParty });
    mockApi.get.mockResolvedValueOnce({
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
    const partyToDelete = mockData.parties[0];
    const remainingParties = mockData.parties.slice(1);

    mockApi.get.mockResolvedValueOnce({ data: mockData.parties });
    mockApi.delete.mockResolvedValueOnce({ data: null });
    mockApi.get.mockResolvedValueOnce({ data: remainingParties });

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
    mockApi.get.mockRejectedValueOnce(new Error("Failed to fetch parties"));

    render(<PartiesInvolvedPageWrapper />);

    await waitFor(() => {
      expect(screen.getByText(/error/i)).toBeInTheDocument();
    });
  });

  it("filters parties by search term", async () => {
    mockApi.get.mockResolvedValueOnce({ data: mockData.parties });

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
