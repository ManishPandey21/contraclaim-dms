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

    await user.type(
      screen.getByLabelText(/^work email$/i),
      "superadmin@example.com"
    );
    await user.type(screen.getByLabelText(/^password$/i), "password");
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));

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

  it("renders the Quiet authority shell with mobile-first and desktop layout guards", async () => {
    vi.mocked(getCurrentUserProfile).mockRejectedValue(
      new Error("No active session")
    );

    render(
      <MemoryRouter initialEntries={["/login"]}>
        <LoginPage />
      </MemoryRouter>
    );

    expect(screen.getByTestId("login-page")).toHaveClass(
      "min-h-[100svh]",
      "overflow-x-hidden"
    );
    expect(screen.getByTestId("login-background")).toHaveClass(
      "bg-gradient-to-br",
      "from-brand-soft",
      "via-paper",
      "to-paper"
    );
    expect(screen.getByTestId("login-background")).not.toHaveAttribute("style");
    expect(screen.getByTestId("login-responsive-layout")).toHaveClass(
      "grid",
      "content-center",
      "gap-5",
      "lg:grid-cols-[minmax(0,1.08fr)_minmax(360px,0.92fr)]"
    );
    expect(screen.getByTestId("login-card")).toHaveClass(
      "w-full",
      "max-w-md",
      "!bg-[#f7f8fa]"
    );
    expect(screen.getByTestId("login-card-heading")).toHaveClass(
      "w-full",
      "space-y-1.5"
    );
    expect(
      screen.getByRole("heading", {
        name: /welcome back to your contract record/i,
      })
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "ContraClaim" })).toHaveAttribute(
      "src",
      "/contraclaim2.png"
    );
    expect(screen.getByRole("img", { name: "ContraClaim" })).toHaveClass(
      "h-8",
      "sm:h-9",
      "w-auto"
    );
    expect(
      screen.getByRole("button", { name: /^sign in$/i })
    ).toHaveClass("bg-ink", "hover:bg-ink/90");
    expect(screen.getByLabelText(/^work email$/i)).toHaveClass("login-input");
    expect(screen.getByLabelText(/^password$/i)).toHaveClass("login-input");
    expect(
      screen.getByText(
        /claims, correspondence and evidence—connected in one defensible workspace/i
      )
    ).toHaveClass("hidden", "sm:block");

    await waitFor(() => {
      expect(getCurrentUserProfile).toHaveBeenCalledTimes(1);
    });
  });

  it("keeps password visibility accessible without changing the submitted value", async () => {
    vi.mocked(getCurrentUserProfile).mockRejectedValue(
      new Error("No active session")
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <LoginPage />
      </MemoryRouter>
    );

    const passwordInput = screen.getByLabelText(/^password$/i);
    await user.type(passwordInput, "password");
    expect(passwordInput).toHaveAttribute("type", "password");

    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(passwordInput).toHaveAttribute("type", "text");
    expect(passwordInput).toHaveValue("password");
    expect(
      screen.getByRole("button", { name: "Hide password" })
    ).toBeInTheDocument();
  });
});
