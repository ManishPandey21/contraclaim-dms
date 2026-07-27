import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import LockedParagraphsPanel from "../LockedParagraphsPanel";
import type { DraftRunResponse } from "@/types/letterDrafting";


const run = {
  run_id: "run-1",
  letter_id: "letter-1",
  draft_type: "reply",
  mode: "draft",
  letter_category: "general",
  status: "completed",
  role: "contractor",
  draft_artifact: {
    draft_letter:
      "Subject: Notice\n\nThe approved contractual position remains unchanged.\n\nPlease respond within seven days.",
    source_integrity_notes: "Supported.",
  },
  frozen_sections: [],
} satisfies DraftRunResponse;


describe("Freeze and edit sections", () => {
  it("submits frozen indices separately from editable revision indices", async () => {
    const user = userEvent.setup();
    const onFreeze = vi.fn().mockResolvedValue(undefined);
    const onRevise = vi.fn().mockResolvedValue(undefined);

    render(
      <LockedParagraphsPanel
        run={run}
        onFreeze={onFreeze}
        onRevise={onRevise}
      />,
    );

    await user.click(screen.getAllByRole("button", { name: "Freeze" })[0]);
    await user.click(screen.getByRole("button", { name: "Save frozen sections" }));

    expect(onFreeze).toHaveBeenCalledTimes(1);
    expect(onFreeze.mock.calls[0][0]).toEqual([0]);

    await user.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    await user.selectOptions(screen.getByLabelText("Section edit action"), "clarity_structure");
    await user.click(screen.getByRole("button", { name: "Revise 1 selected" }));

    expect(onRevise).toHaveBeenCalledTimes(1);
    expect(onRevise.mock.calls[0].slice(0, 2)).toEqual([[1], "clarity_structure"]);
  });

  it("does not allow a frozen section to be selected for editing", async () => {
    const user = userEvent.setup();
    render(
      <LockedParagraphsPanel
        run={{
          ...run,
          frozen_sections: [
            {
              section_index: 1,
              content: "The approved contractual position remains unchanged.",
              content_hash: "a".repeat(64),
            },
          ],
        }}
        onFreeze={vi.fn()}
        onRevise={vi.fn()}
      />,
    );

    const editButtons = screen.getAllByRole("button", { name: "Edit" });
    expect(editButtons[1]).toBeDisabled();
    await user.click(editButtons[1]);
    expect(screen.getByRole("button", { name: /Revise/ })).toBeDisabled();
  });
});
