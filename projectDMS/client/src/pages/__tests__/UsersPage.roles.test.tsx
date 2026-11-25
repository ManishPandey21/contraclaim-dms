import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 4B-08 (C-19): the Users form edits one access role. It used to save a one-item
 * role list, which removed every other role the user held, a reviewer role
 * included. Saving an edit now keeps the rest of the list.
 */
const api = vi.hoisted(() => ({
  getUsers: vi.fn(),
  getOrganizations: vi.fn(),
  getProjects: vi.fn(),
  updateUser: vi.fn(),
  createUser: vi.fn(),
  deleteUser: vi.fn(),
  lockUser: vi.fn(),
  unlockUser: vi.fn(),
}));
vi.mock("@/services/enhanced-api", () => ({ enhancedApi: api }));
vi.mock("@/hooks/useRBAC", () => ({ default: () => ({ can: () => true, loading: false }) }));
vi.mock("@/hooks/useStepUp", () => ({ useStepUp: () => ({ requestToken: vi.fn(), StepUpDialog: () => null }) }));
vi.mock("@/hooks/use-toast", () => ({ useToast: () => ({ toast: vi.fn() }) }));
// The row menu renders its items directly: the test is about the form, not the menu.
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
}));

import UsersPage from "../UsersPage";

const reviewer = {
  id: "u-1",
  username: "asha.rao",
  first_name: "Asha",
  last_name: "Rao",
  email: "asha@example.com",
  roles: ["projectuser", "quantum_reviewer", "panel_counsel"],
  organization_id: "org-a",
  projects: ["proj-1"],
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getUsers.mockResolvedValue([reviewer]);
  api.getOrganizations.mockResolvedValue([{ id: "org-a", name: "Org A" }]);
  api.getProjects.mockResolvedValue([{ id: "proj-1", name: "Metro Line 3", organization_id: "org-a" }]);
  api.updateUser.mockImplementation(async (_id: string, body: Record<string, unknown>) => ({ ...reviewer, ...body }));
});

describe("UsersPage edit", () => {
  it("saves the user's other roles along with the access role", async () => {
    const user = userEvent.setup();
    render(<UsersPage />);
    await screen.findByText("asha@example.com");
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(await screen.findByRole("button", { name: "Update User" }));

    await waitFor(() => expect(api.updateUser).toHaveBeenCalledTimes(1));
    const [id, body] = api.updateUser.mock.calls[0];
    expect(id).toBe("u-1");
    expect(body.roles).toEqual(["projectuser", "quantum_reviewer", "panel_counsel"]);
  });
});
