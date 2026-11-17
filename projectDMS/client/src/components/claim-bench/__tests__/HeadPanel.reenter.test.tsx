import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import HeadPanel from "@/components/claim-bench/HeadPanel";
import { ELEMENT_KEYS, type ClaimHead } from "@/types/claimBench";

// 4B-19 (D-10): a figure the claim record held in a form that could not be
// carried onto the board is shown as recorded, and a person settles it.

const head = (over: Partial<ClaimHead> = {}): ClaimHead => ({
  head_id: "H1",
  title: "Variation to the depot slab",
  kind: "time_and_cost",
  event_summary: null,
  clause_refs: [],
  amount_claimed: null,
  eot_days_claimed: null,
  elements: Object.fromEntries(ELEMENT_KEYS.map((key) => [key, { source_ids: [] }])) as ClaimHead["elements"],
  residual_risks: [],
  missing_information: [],
  test_stale: false,
  ...over,
});

const show = (item: ClaimHead, readOnly = false) => {
  const onPatch = vi.fn();
  render(
    <HeadPanel
      head={item}
      currency="INR"
      readOnly={readOnly}
      busy={false}
      testing={false}
      canTest
      onPatch={onPatch}
      onDecide={vi.fn()}
      onTest={vi.fn()}
      onRemove={vi.fn()}
    />,
  );
  return onPatch;
};

describe("a legacy figure that needs re-entry", () => {
  it("shows what the record holds, beside the empty field, and nothing when there is none", () => {
    show(
      head({
        figures_to_reenter: [
          { field: "amount_claimed", recorded: "12,50,000" },
          { field: "eot_days_claimed", recorded: "30.5" },
        ],
      }),
    );
    const notes = screen.getAllByRole("note");
    expect(notes).toHaveLength(2);
    expect(notes.map((note) => note.textContent).join(" ")).toContain("“12,50,000”");
    expect(notes.map((note) => note.textContent).join(" ")).toContain("“30.5”");
    expect((screen.getByLabelText("Amount claimed") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Days claimed") as HTMLInputElement).value).toBe("");
  });

  it("shows no note on a head with nothing to re-enter", () => {
    show(head({ amount_claimed: 5, figures_to_reenter: [] }));
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("lets the person say the head has no such figure, for that figure only", () => {
    const onPatch = show(
      head({
        figures_to_reenter: [
          { field: "amount_claimed", recorded: "12,50,000" },
          { field: "eot_days_claimed", recorded: "30.5" },
        ],
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "This head has no days" }));
    expect(onPatch).toHaveBeenCalledWith({ figures_not_needed: ["eot_days_claimed"] });
  });

  it("sends the entered figure when the person types it and saves", () => {
    const onPatch = show(head({ figures_to_reenter: [{ field: "amount_claimed", recorded: "12,50,000" }] }));
    fireEvent.change(screen.getByLabelText("Amount claimed"), { target: { value: "1250000" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onPatch.mock.calls[0][0]).toMatchObject({ amount_claimed: 1250000 });
  });

  it("offers no action on a read-only board", () => {
    show(head({ figures_to_reenter: [{ field: "amount_claimed", recorded: "12,50,000" }] }), true);
    const note = screen.getByRole("note");
    expect((within(note).getByRole("button") as HTMLButtonElement).disabled).toBe(true);
  });
});
