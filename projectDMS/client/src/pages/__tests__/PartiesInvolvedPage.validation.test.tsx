import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PartiesInvolvedPage from "../PartiesInvolvedPage";
import { enhancedApi } from "@/services/enhanced-api";

// The stakeholder form is the one form whose schema relied on a Zod default
// message (`name.max(50)`). Pin the message and the submitted payload so the
// Zod 4 upgrade (GHSA-mrhx-c7xc-m3w5) cannot change either.

vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: {
    getProjects: vi.fn(),
    getOrganizations: vi.fn(),
    getParties: vi.fn(),
    getProjectRepresentatives: vi.fn(),
    getOrganizationRepresentatives: vi.fn(),
    createParty: vi.fn(),
  },
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

async function openNewStakeholderDialog() {
  const user = userEvent.setup();
  render(<PartiesInvolvedPage />);
  await waitFor(() => expect(enhancedApi.getParties).toHaveBeenCalled());
  await user.click(screen.getByRole("tab", { name: /other stakeholders/i }));
  await user.click(await screen.findByRole("button", { name: /register new stakeholder/i }));
  return user;
}

describe("PartiesInvolvedPage stakeholder form validation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(enhancedApi.getProjects).mockResolvedValue([]);
    vi.mocked(enhancedApi.getOrganizations).mockResolvedValue([]);
    vi.mocked(enhancedApi.getParties).mockResolvedValue([]);
    vi.mocked(enhancedApi.createParty).mockImplementation(async (party) => ({ _id: "p1", ...party }) as never);
  });

  it("shows the same messages for a too-short and a too-long name and does not submit", async () => {
    const user = await openNewStakeholderDialog();
    const name = screen.getByPlaceholderText("Full Name");

    await user.type(name, "A");
    await user.click(screen.getByRole("button", { name: /^submit$/i }));
    expect(await screen.findByText("Name must be at least 2 characters")).toBeInTheDocument();

    await user.clear(name);
    await user.type(name, "x".repeat(51));
    await user.click(screen.getByRole("button", { name: /^submit$/i }));
    expect(await screen.findByText("String must contain at most 50 character(s)")).toBeInTheDocument();

    await user.clear(screen.getByPlaceholderText("Email"));
    await user.type(screen.getByPlaceholderText("Email"), "not-an-email");
    await user.click(screen.getByRole("button", { name: /^submit$/i }));
    expect(await screen.findByText("Invalid email address")).toBeInTheDocument();

    expect(enhancedApi.createParty).not.toHaveBeenCalled();
  });

  it("submits a valid stakeholder with empty optional fields mapped as before", async () => {
    const user = await openNewStakeholderDialog();

    await user.type(screen.getByPlaceholderText("Full Name"), "x".repeat(50));
    await user.click(screen.getByRole("button", { name: /^submit$/i }));

    await waitFor(() => expect(enhancedApi.createParty).toHaveBeenCalledTimes(1));
    expect(enhancedApi.createParty).toHaveBeenCalledWith({
      name: "x".repeat(50),
      type: "Individual",
      contactEmail: null,
      contactPhone: null,
      projects: [],
      organizationId: null,
      address: null,
      city: null,
      state: null,
      pinCode: null,
      country: null,
    });
  });
});
