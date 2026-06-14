import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { PartiesInvolvedPageWrapper } from "@/pages/PartiesInvolvedPageWrapper.new";
import { PartiesProvider } from "@/contexts/PartiesContext.new";
import { enhancedApi as api } from "@/services/enhanced-api";
import { Party, Representative, Concern } from "@/types/api";

// Mock the API service
vi.mock("@/services/api", () => ({
  api: {
    getParties: vi.fn(),
    createParty: vi.fn(),
    updateParty: vi.fn(),
    deleteParty: vi.fn(),
    addRepresentative: vi.fn(),
    deleteRepresentative: vi.fn(),
    getConcerns: vi.fn(),
    createConcern: vi.fn(),
    deleteConcern: vi.fn(),
  },
}));

// Mock data
const mockParties: Party[] = [
  {
    id: "1",
    name: "Test Organization",
    type: "Organization",
    contactEmail: "test@org.com",
    contactPhone: "1234567890",
    createdAt: new Date().toISOString(),
    representatives: [],
    projects: [],
  },
];

const mockRepresentative: Representative = {
  id: "1",
  partyId: "1",
  name: "John Doe",
  email: "john@org.com",
  contactNumber: "1234567890",
  designation: "Manager",
  isPrimary: true,
};

const mockConcern: Concern = {
  id: "1",
  name: "Test Concern",
  description: "Test Description",
  partyId: "1",
  emails: ["test@org.com"],
  createdAt: new Date().toISOString(),
};

describe.skip("Parties Integration", () => {
  beforeEach(() => {
    // Reset all mocks before each test
    vi.clearAllMocks();

    // Setup default mock responses
    (api.getParties as any).mockResolvedValue(mockParties);
    (api.getConcerns as any).mockResolvedValue([mockConcern]);
  });

  it("renders the parties page with loading state", async () => {
    render(<PartiesInvolvedPageWrapper />);

    // Should show loading state initially
    expect(screen.getByText(/loading/i)).toBeInTheDocument();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.getByText("Parties Involved")).toBeInTheDocument();
    });
  });

  it("loads and displays parties", async () => {
    render(<PartiesInvolvedPageWrapper />);

    await waitFor(() => {
      expect(screen.getByText(mockParties[0].name)).toBeInTheDocument();
    });

    expect(api.getParties).toHaveBeenCalledTimes(1);
  });

  it("creates a new party", async () => {
    const newParty = {
      id: "2",
      name: "New Organization",
      type: "Organization",
      contactEmail: "new@org.com",
      contactPhone: "0987654321",
      createdAt: new Date().toISOString(),
      representatives: [],
      projects: [],
    };

    (api.createParty as any).mockResolvedValueOnce(newParty);

    render(<PartiesInvolvedPageWrapper />);

    // Wait for page to load
    await waitFor(() => {
      expect(screen.getByText("New Party")).toBeInTheDocument();
    });

    // Click new party button
    fireEvent.click(screen.getByText("New Party"));

    // Fill out form
    await userEvent.type(screen.getByLabelText(/name/i), newParty.name);
    await userEvent.type(
      screen.getByLabelText(/email/i),
      newParty.contactEmail
    );
    await userEvent.type(
      screen.getByLabelText(/phone/i),
      newParty.contactPhone
    );

    // Submit form
    fireEvent.click(screen.getByText("Create Party"));

    // Verify API call
    await waitFor(() => {
      expect(api.createParty).toHaveBeenCalledWith(
        expect.objectContaining({
          name: newParty.name,
          contactEmail: newParty.contactEmail,
          contactPhone: newParty.contactPhone,
        })
      );
    });

    // Verify success message
    expect(
      await screen.findByText(/party created successfully/i)
    ).toBeInTheDocument();
  });

  it("deletes a party", async () => {
    (api.deleteParty as any).mockResolvedValueOnce({
      message: "Party deleted successfully",
    });

    render(<PartiesInvolvedPageWrapper />);

    // Wait for parties to load
    await waitFor(() => {
      expect(screen.getByText(mockParties[0].name)).toBeInTheDocument();
    });

    // Find and click delete button
    const deleteButton = screen
      .getAllByRole("button")
      .find(
        (button) =>
          button.querySelector("svg")?.getAttribute("data-icon") === "trash"
      );
    expect(deleteButton).toBeTruthy();
    fireEvent.click(deleteButton!);

    // Confirm deletion
    const confirmButton = await screen.findByText(/confirm/i);
    fireEvent.click(confirmButton);

    // Verify API call
    await waitFor(() => {
      expect(api.deleteParty).toHaveBeenCalledWith(mockParties[0].id);
    });

    // Verify success message
    expect(
      await screen.findByText(/party deleted successfully/i)
    ).toBeInTheDocument();
  });

  it("handles API errors gracefully", async () => {
    // Mock API error
    (api.getParties as any).mockRejectedValueOnce(
      new Error("Failed to fetch parties")
    );

    render(<PartiesInvolvedPageWrapper />);

    // Verify error message
    expect(
      await screen.findByText(/failed to fetch parties/i)
    ).toBeInTheDocument();
  });

  it("filters parties by search term", async () => {
    render(<PartiesInvolvedPageWrapper />);

    // Wait for parties to load
    await waitFor(() => {
      expect(screen.getByText(mockParties[0].name)).toBeInTheDocument();
    });

    // Type in search box
    const searchInput = screen.getByPlaceholderText(/search parties/i);
    await userEvent.type(searchInput, "Test");

    // Verify filtered results
    expect(screen.getByText(mockParties[0].name)).toBeInTheDocument();

    // Search for non-existent party
    await userEvent.clear(searchInput);
    await userEvent.type(searchInput, "NonExistent");

    // Verify no results
    expect(screen.queryByText(mockParties[0].name)).not.toBeInTheDocument();
  });
});
