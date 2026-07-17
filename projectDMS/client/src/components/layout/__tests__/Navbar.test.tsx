import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Navbar from "../Navbar";

const logout = vi.fn();

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ logout }),
}));

vi.mock("@/components/NotificationCenter", () => ({
  default: () => <button type="button">Notifications</button>,
}));

vi.mock("../TenantScopeBar", () => ({
  default: () => <div>Organisation and project selector</div>,
}));

describe("Navbar", () => {
  beforeEach(() => {
    logout.mockClear();
  });

  it("renders global controls without a page title", () => {
    render(<Navbar />);

    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Document Management System")
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Organisation and project selector")
    ).toBeInTheDocument();
    expect(
      screen.getByRole("searchbox", { name: "Global search" })
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Notifications" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Help" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Log out" })).toBeVisible();
  });

  it("logs the user out from the global logout action", () => {
    render(<Navbar />);

    fireEvent.click(screen.getByRole("button", { name: "Log out" }));

    expect(logout).toHaveBeenCalledOnce();
  });
});
