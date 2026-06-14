import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "../LoginPage";
import {
  getCurrentUserProfile,
  loginWithPassword,
} from "@/services/session-api";

const navigateMock = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom"
  );
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("@/services/session-api", () => ({
  loginWithPassword: vi.fn(),
  getCurrentUserProfile: vi.fn(),
}));

vi.mock("@/hooks/use-toast", () => ({
  useToast: () => ({
    toast: vi.fn(),
  }),
}));

vi.mock("@/lib/error-logger", () => ({
  extractErrorMessage: vi.fn((_error: unknown, fallback: string) => fallback),
  logError: vi.fn(),
}));

describe("LoginPage login process", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.removeItem("accessToken");
    window.localStorage.removeItem("user_id");
    window.localStorage.removeItem("user_roles");
    window.localStorage.removeItem("org_id");
    window.localStorage.removeItem("proj_id");
  });

  it("logs in superadmin, verifies /me roles, clears legacy auth storage, and navigates to overview", async () => {
    vi.mocked(getCurrentUserProfile)
      .mockRejectedValueOnce(new Error("No active session"))
      .mockResolvedValueOnce({
        id: "superadmin-id",
        email: "superadmin@example.com",
        roles: ["superadmin"],
        organization_id: null,
        projects: [],
      });
    vi.mocked(loginWithPassword).mockResolvedValue({
      access_token: "fresh-token",
      token_type: "bearer",
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <LoginPage />
      </MemoryRouter>
    );

    await user.type(screen.getByLabelText(/^email$/i), "superadmin@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "password");
    await user.click(screen.getByRole("button", { name: /login/i }));

    await waitFor(() => {
      expect(loginWithPassword).toHaveBeenCalledWith({
        email: "superadmin@example.com",
        password: "password",
      });
    });
    await waitFor(() => {
      expect(getCurrentUserProfile).toHaveBeenCalledTimes(2);
      expect(navigateMock).toHaveBeenCalledWith("/overview");
    });

    expect(window.localStorage.removeItem).toHaveBeenCalledWith("accessToken");
    expect(window.localStorage.removeItem).toHaveBeenCalledWith("user_roles");
    expect(window.localStorage.removeItem).toHaveBeenCalledWith("org_id");
    expect(window.localStorage.removeItem).toHaveBeenCalledWith("proj_id");
  });
});
