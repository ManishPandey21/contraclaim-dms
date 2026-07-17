import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  resolveSidebarIdentity,
  useSidebarIdentity,
} from "../useSidebarIdentity";

const { getProfile, getCurrentUserProfile } = vi.hoisted(() => ({
  getProfile: vi.fn(),
  getCurrentUserProfile: vi.fn(),
}));

vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getProfile },
}));
vi.mock("@/services/session-api", () => ({ getCurrentUserProfile }));

function IdentityProbe() {
  const identity = useSidebarIdentity();
  return (
    <div>
      <span>{identity.displayName}</span>
      <span>{identity.initials}</span>
    </div>
  );
}

describe("useSidebarIdentity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(window.localStorage.getItem).mockReturnValue(
      JSON.stringify({ full_name: "Arvind Tingloo" }),
    );
    getProfile.mockResolvedValue({
      first_name: "Manish",
      last_name: "Pandey",
      email: "superadmin@example.com",
    });
    getCurrentUserProfile.mockResolvedValue({
      first_name: "Manish",
      last_name: "Pandey",
      email: "superadmin@example.com",
    });
  });

  it("uses the authenticated profile instead of a stale local cache", async () => {
    render(<IdentityProbe />);

    expect(await screen.findByText("Manish Pandey")).toBeInTheDocument();
    expect(screen.queryByText("Arvind Tingloo")).not.toBeInTheDocument();
    expect(screen.getByText("MP")).toBeInTheDocument();
  });

  it("refreshes from the live profile after a profile update event", async () => {
    render(<IdentityProbe />);
    await screen.findByText("Manish Pandey");

    getProfile.mockResolvedValue({
      first_name: "Manish Kumar",
      last_name: "Pandey",
      email: "superadmin@example.com",
    });
    window.dispatchEvent(new CustomEvent("profile-updated"));

    await waitFor(() => {
      expect(screen.getByText("Manish Kumar Pandey")).toBeInTheDocument();
    });
  });

  it("falls back to the session name before using an email address", () => {
    expect(
      resolveSidebarIdentity(
        { email: "superadmin@example.com" },
        { full_name: "Manish Pandey", email: "superadmin@example.com" },
      ),
    ).toMatchObject({ displayName: "Manish Pandey", initials: "MP" });
  });
});
