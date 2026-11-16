import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ listArbitrationDraftVersions: vi.fn() }));
vi.mock("@/services/arbitration-drafting-api", () => api);

import DraftVersionPicker from "@/components/pickers/DraftVersionPicker";
import { containsRawId } from "@/lib/plain-language";

describe("DraftVersionPicker (4B-40)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listArbitrationDraftVersions.mockResolvedValue([
      { _id: "65f0c0ffee0000000000aa01", draft_id: "d1", version: 1, full_markdown: "", validation_status: "needs_review" },
      { _id: "65f0c0ffee0000000000aa02", draft_id: "d1", version: 2, full_markdown: "", validation_status: "passed" },
    ]);
  });

  it("offers versions by number and state, newest first, and returns the chosen version's id", async () => {
    const onChange = vi.fn();
    render(<DraftVersionPicker draftId="d1" value="" onChange={onChange} label="Opponent pleading version" />);
    const picker = screen.getByLabelText("Opponent pleading version");
    await waitFor(() => expect(screen.getByRole("option", { name: "Version 2 · Passed" })).toBeInTheDocument());
    const names = screen.getAllByRole("option").map((option) => option.textContent ?? "");
    expect(names).toEqual(["Choose a version", "Version 2 · Passed", "Version 1 · Needs review"]);
    expect(names.some(containsRawId)).toBe(false);
    await userEvent.selectOptions(picker, "Version 1 · Needs review");
    expect(onChange).toHaveBeenCalledWith("65f0c0ffee0000000000aa01");
  });

  it("waits for a pleading to be chosen", () => {
    render(<DraftVersionPicker draftId="" value="" onChange={vi.fn()} label="Opponent pleading version" />);
    expect(screen.getByLabelText("Opponent pleading version")).toBeDisabled();
    expect(screen.getByRole("option", { name: "Choose the pleading first" })).toBeInTheDocument();
    expect(api.listArbitrationDraftVersions).not.toHaveBeenCalled();
  });
});
