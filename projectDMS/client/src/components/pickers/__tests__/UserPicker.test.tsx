import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UserPicker from "@/components/pickers/UserPicker";
import { containsRawId } from "@/lib/plain-language";

const pickers = vi.hoisted(() => ({ listPickerUsers: vi.fn() }));
vi.mock("@/services/pickers-api", () => pickers);

const COUNSEL_ID = "65f0a1b2c3d4e5f6a7b8c9d0";

beforeEach(() => {
  vi.clearAllMocks();
  pickers.listPickerUsers.mockResolvedValue({
    items: [
      { id: COUNSEL_ID, name: "Asha Rao" },
      { id: "user-author", name: "Vikram Shah" },
    ],
    truncated: false,
  });
});

describe("UserPicker", () => {
  it("asks the scoped endpoint for this project and authority, and shows names only", async () => {
    const onChange = vi.fn();
    const { container } = render(
      <UserPicker projectId="proj-A1" permission="dms.claim.determine" value="" onChange={onChange} label="Approver" />
    );
    expect(await screen.findByRole("option", { name: "Asha Rao" })).toBeInTheDocument();
    expect(pickers.listPickerUsers).toHaveBeenCalledWith({ projectId: "proj-A1", permission: "dms.claim.determine", q: "" });
    expect(containsRawId(container.textContent || "")).toBe(false);

    await userEvent.selectOptions(screen.getByLabelText("Approver"), "Asha Rao");
    expect(onChange).toHaveBeenCalledWith(COUNSEL_ID, { id: COUNSEL_ID, name: "Asha Rao" });
  });

  it("does not offer excluded people, such as the author of the work under review", async () => {
    render(
      <UserPicker projectId="proj-A1" permission="dms.claim.determine" value="" onChange={vi.fn()} label="Approver" excludeIds={["user-author"]} />
    );
    await screen.findByRole("option", { name: "Asha Rao" });
    expect(screen.queryByRole("option", { name: "Vikram Shah" })).not.toBeInTheDocument();
  });

  it("asks for a project before loading anyone", () => {
    render(<UserPicker projectId="" permission="dms.claim.determine" value="" onChange={vi.fn()} label="Approver" />);
    expect(pickers.listPickerUsers).not.toHaveBeenCalled();
    expect(screen.getByRole("option", { name: "Choose a project first" })).toBeInTheDocument();
    expect(screen.getByLabelText("Approver")).toBeDisabled();
  });

  it("explains an empty list and offers a search when the list is cut short", async () => {
    pickers.listPickerUsers.mockResolvedValueOnce({ items: [], truncated: false });
    const { rerender } = render(
      <UserPicker projectId="proj-A1" permission="dms.claim.determine" value="" onChange={vi.fn()} label="Approver" />
    );
    expect(await screen.findByText(/No one on this project holds this authority yet/)).toBeInTheDocument();

    pickers.listPickerUsers.mockResolvedValue({ items: [{ id: "u1", name: "Asha Rao" }], truncated: true });
    rerender(<UserPicker projectId="proj-A2" permission="dms.claim.determine" value="" onChange={vi.fn()} label="Approver" />);
    const box = await screen.findByLabelText("Search approver");
    await userEvent.type(box, "Ash{Enter}");
    await waitFor(() =>
      expect(pickers.listPickerUsers).toHaveBeenLastCalledWith({ projectId: "proj-A2", permission: "dms.claim.determine", q: "Ash" })
    );
  });

  it("renders a refusal from its user_message, never raw ids", async () => {
    pickers.listPickerUsers.mockRejectedValueOnce({
      response: { status: 403, data: { detail: `Not authorized for ${COUNSEL_ID}` } },
    });
    render(<UserPicker projectId="proj-A1" permission="dms.claim.determine" value="" onChange={vi.fn()} label="Approver" />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not authorized for a record");
    expect(containsRawId(alert.textContent || "")).toBe(false);
  });
});
