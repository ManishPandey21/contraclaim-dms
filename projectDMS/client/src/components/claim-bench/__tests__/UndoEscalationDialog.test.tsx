import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ undoEscalation: vi.fn() }));
vi.mock("@/services/claim-escalation-api", () => api);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import UndoEscalationDialog from "@/components/claim-bench/UndoEscalationDialog";

const refusal = (code: string, message: string) =>
  Object.assign(new Error("Request failed with status code 409"), {
    isAxiosError: true,
    response: { status: 409, data: { detail: { code, user_message: message, detail: null } } },
  });

describe("UndoEscalationDialog (4B-37, 4B-40)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("asks for a reason and undoes the escalation", async () => {
    api.undoEscalation.mockResolvedValue({ claim_id: "c1", case_id: "case-1", status: "submitted", rows_archived: 4, freeze_lifted: true, case_discarded: false });
    const onUndone = vi.fn();
    render(<UndoEscalationDialog claimId="c1" caseId="case-1" open onOpenChange={vi.fn()} onUndone={onUndone} />);
    const undo = screen.getByRole("button", { name: "Undo escalation" });
    expect(undo).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Why is the escalation being undone?"), "Escalated by mistake");
    await userEvent.click(undo);
    await waitFor(() => expect(api.undoEscalation).toHaveBeenCalledWith("c1", "case-1", "Escalated by mistake"));
    expect(onUndone).toHaveBeenCalled();
  });

  it("shows the server's reason when the case already has a pleading version", async () => {
    api.undoEscalation.mockRejectedValue(
      refusal("escalation_undo_after_pleading", "A pleading version exists in this case, so the escalation can no longer be undone.")
    );
    const onUndone = vi.fn();
    render(<UndoEscalationDialog claimId="c1" caseId="case-1" open onOpenChange={vi.fn()} onUndone={onUndone} />);
    await userEvent.type(screen.getByLabelText("Why is the escalation being undone?"), "Escalated by mistake");
    await userEvent.click(screen.getByRole("button", { name: "Undo escalation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A pleading version exists in this case");
    expect(onUndone).not.toHaveBeenCalled();
  });
});
