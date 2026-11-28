import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLetterBenchStore } from "@/stores/useLetterBenchStore";
import { AssertionCard } from "../AssertionCard";
import { IncomingLetterCanvas } from "../IncomingLetterCanvas";
import { PositionGateBar } from "../PositionGateBar";
import { ReplyComposer } from "../ReplyComposer";
import { SkillRail } from "../SkillRail";
import { VerdictChips } from "../VerdictChips";
import { CHAIN, TEXT, assertion, bench, link, paragraph } from "./fixtures";

beforeEach(() => useLetterBenchStore.getState().reset());

describe("VerdictChips", () => {
  it("is a radio group whose options carry their names in text", () => {
    render(<VerdictChips assertionId="A1" value="none" proposed="partial" onChange={vi.fn()} />);
    const group = screen.getByRole("radiogroup", { name: "Position on A1" });
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((radio) => radio.textContent)).toEqual([
      "Entitlement",
      "Partialproposed",
      "None",
      "Need information",
    ]);
    expect(within(group).getByRole("radio", { name: /^None/ })).toHaveAttribute("aria-checked", "true");
    expect(within(group).getByRole("radio", { name: /^Partial.*\(proposed\)/ })).toHaveAttribute("aria-checked", "false");
  });

  it("keeps one tab stop; arrows move focus without deciding, a press decides", () => {
    const onChange = vi.fn();
    render(<VerdictChips assertionId="A1" value="entitlement" onChange={onChange} />);
    const radios = screen.getAllByRole("radio");
    expect(radios.map((radio) => radio.tabIndex)).toEqual([0, -1, -1, -1]);
    fireEvent.keyDown(radios[0], { key: "ArrowLeft" });
    expect(radios[3]).toHaveFocus(); // wraps
    fireEvent.keyDown(radios[3], { key: "ArrowRight" });
    expect(radios[0]).toHaveFocus();
    // Moving through the options is not a decision: nothing was saved.
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(radios[2]); // Enter or Space on a button is a click
    expect(onChange).toHaveBeenCalledWith("none");
    fireEvent.click(radios[0]); // already chosen: no redundant save
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("does not change anything when read-only", () => {
    const onChange = vi.fn();
    render(<VerdictChips assertionId="A1" value="none" disabled onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: /^Partial/ }));
    fireEvent.keyDown(screen.getByRole("radio", { name: /^None/ }), { key: "ArrowRight" });
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("SkillRail", () => {
  const handlers = () => ({
    onOpen: vi.fn(),
    onAdd: vi.fn(),
    onRemove: vi.fn(),
    onMove: vi.fn(),
    onDrop: vi.fn(),
    onRun: vi.fn(),
  });

  it("names each step with its version, target and status, and says why one is unavailable", () => {
    render(<SkillRail chain={CHAIN} readOnly={false} {...handlers()} />);
    const rail = screen.getByRole("navigation", { name: "Skill chain" });
    expect(within(rail).getAllByRole("listitem")).toHaveLength(4);
    expect(
      within(rail).getByRole("button", { name: "Step 2: Variations version 1.0.0, on A2. Done" })
    ).toBeInTheDocument();
    expect(
      within(rail).getByRole("button", {
        name: "Step 3: Extension of time review, on Whole letter. Unavailable. Not installed",
      })
    ).toBeInTheDocument();
  });

  it("reorders and removes evaluation links from the keyboard; analysis and reply are fixed", () => {
    const h = handlers();
    render(<SkillRail chain={CHAIN} readOnly={false} {...h} />);
    expect(screen.getByRole("button", { name: "Move Variations earlier" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Move Variations later" }));
    expect(h.onMove).toHaveBeenCalledWith(CHAIN[1], 1);
    fireEvent.click(screen.getByRole("button", { name: "Remove Variations from the chain" }));
    expect(h.onRemove).toHaveBeenCalledWith(CHAIN[1]);
    expect(screen.queryByRole("button", { name: /Move Letter Analysis/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove Draft reply/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add a skill to the chain" }));
    expect(h.onAdd).toHaveBeenCalled();
  });

  it("offers Run only for a link that is out of date, failed or not yet run", () => {
    const h = handlers();
    const chain = [CHAIN[0], link({ status: "stale" }), CHAIN[3]];
    render(<SkillRail chain={chain} readOnly={false} {...h} />);
    fireEvent.click(screen.getByRole("button", { name: "Run Variations" }));
    expect(h.onRun).toHaveBeenCalledWith(chain[1]);
    expect(screen.getByRole("button", { name: /Variations.*Out of date/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run Draft reply" })).not.toBeInTheDocument();
  });

  it("shows no editing controls to a reviewer", () => {
    render(<SkillRail chain={CHAIN} readOnly {...handlers()} />);
    expect(screen.queryByRole("button", { name: /Remove|Move|Add a skill|^Run/ })).not.toBeInTheDocument();
  });
});

describe("AssertionCard", () => {
  const props = (overrides = {}) => ({
    assertion: assertion({
      clause_refs: ["8.4"],
      clause_checks: [{ clause_number: "8.4", state: "not_found" as const }],
      needs: ["BOQ rates for item 4.12"],
      verdict: "partial" as const,
    }),
    steps: [],
    nextAssertionId: "A2",
    selected: false,
    active: false,
    readOnly: false,
    onSelect: vi.fn(),
    onHover: vi.fn(),
    onVerdict: vi.fn(),
    onDirection: vi.fn(),
    onRouteSkill: vi.fn(),
    onOpenStep: vi.fn(),
    onMergeNext: vi.fn(),
    onRemove: vi.fn(),
    ...overrides,
  });

  it("states the clause check in plain language and lists what is needed", () => {
    render(<AssertionCard {...props()} />);
    const card = screen.getByTestId("assertion-card-A1");
    expect(card).toHaveTextContent("not found in this contract");
    expect(card).not.toHaveTextContent(/exists=|=None|null|unknown/);
    expect(card).toHaveTextContent("BOQ rates for item 4.12");
    expect(card).toHaveTextContent("You overrode the proposal");
    expect(screen.getByRole("heading", { name: "Assertion A1, position Partial" })).toBeInTheDocument();
  });

  it("saves a direction on Enter or blur, and only when it changed", () => {
    const p = props();
    render(<AssertionCard {...p} />);
    const input = screen.getByLabelText("Direction for the reply to A1");
    fireEvent.blur(input);
    expect(p.onDirection).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "Notice is out of time." } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(p.onDirection).toHaveBeenCalledWith("Notice is out of time.");
  });

  it("reports a verdict choice", () => {
    const p = props();
    render(<AssertionCard {...p} />);
    fireEvent.click(screen.getByRole("radio", { name: /^None/ }));
    expect(p.onVerdict).toHaveBeenCalledWith("none");
  });

  it("shows the current evaluation, not an older superseded one", () => {
    const step = (id: string, stale: boolean) => ({
      step_id: id, skill_key: "indian-infra-variations", skill_version: "1.0.0", skill_hash: "h", skill_name: "Variations",
      target_assertion_ids: ["A1"], output_schema_key: "variation_evaluation.v1", status: "completed" as const, stale, degraded: false,
    });
    const p = props({ routedLink: link({ target_assertion_ids: ["A1"] }), steps: [step("old", true), step("current", false)] });
    render(<AssertionCard {...p} />);
    expect(screen.getByTestId("assertion-card-A1")).not.toHaveTextContent("out of date");
    fireEvent.click(screen.getByRole("button", { name: "View evaluation" }));
    expect(p.onOpenStep).toHaveBeenCalledWith("current");
  });

  it("links to the evaluation a routed skill produced and flags it when out of date", () => {
    const p = props({
      routedLink: link({ target_assertion_ids: ["A1"] }),
      steps: [
        { step_id: "s-var", skill_key: "indian-infra-variations", skill_version: "1.0.0", skill_hash: "h", skill_name: "Variations", target_assertion_ids: ["A1"], output_schema_key: "variation_evaluation.v1", status: "completed", stale: true, degraded: false },
      ],
    });
    render(<AssertionCard {...p} />);
    expect(screen.getByTestId("assertion-card-A1")).toHaveTextContent("out of date");
    fireEvent.click(screen.getByRole("button", { name: "View evaluation" }));
    expect(p.onOpenStep).toHaveBeenCalledWith("s-var");
  });

  it("is inert for a reviewer", () => {
    render(<AssertionCard {...props({ readOnly: true })} />);
    expect(screen.getByLabelText("Direction for the reply to A1")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "More actions for A1" })).not.toBeInTheDocument();
  });
});

describe("IncomingLetterCanvas", () => {
  const assertions = bench().board.assertions;

  it("renders the letter once, with each assertion as a labelled, focusable highlight", () => {
    render(
      <IncomingLetterCanvas text={TEXT} assertions={assertions} readOnly={false} busy={false} onCreate={vi.fn()} onSplit={vi.fn()} />
    );
    const canvas = screen.getByTestId("letter-canvas");
    const marks = canvas.querySelectorAll("mark");
    expect(marks).toHaveLength(2);
    canvas.querySelectorAll("[data-bench-skip]").forEach((badge) => badge.remove());
    expect(canvas.textContent).toBe(TEXT);
  });

  it("selects an assertion by click or keyboard and shares it through the store", () => {
    render(
      <IncomingLetterCanvas text={TEXT} assertions={assertions} readOnly={false} busy={false} onCreate={vi.fn()} onSplit={vi.fn()} />
    );
    const second = screen.getByRole("button", { name: /^Assertion A2, undecided/ });
    fireEvent.keyDown(second, { key: "Enter" });
    expect(useLetterBenchStore.getState().selectedAssertionId).toBe("A2");
    expect(second).toHaveAttribute("aria-pressed", "true");
    fireEvent.focus(screen.getByRole("button", { name: /^Assertion A1/ }));
    expect(useLetterBenchStore.getState().activeAssertionId).toBe("A1");
  });

  it("offers to add a selection as an assertion, or to split when it starts inside one", () => {
    const onCreate = vi.fn();
    const onSplit = vi.fn();
    const { rerender } = render(
      <IncomingLetterCanvas text={TEXT} assertions={assertions} readOnly={false} busy={false} onCreate={onCreate} onSplit={onSplit} />
    );
    useLetterBenchStore.getState().setPendingSelection({ start: 75, end: 91, text: "Yours faithfully" });
    rerender(
      <IncomingLetterCanvas text={TEXT} assertions={assertions} readOnly={false} busy={false} onCreate={onCreate} onSplit={onSplit} />
    );
    fireEvent.click(screen.getByRole("button", { name: "Add as assertion" }));
    expect(onCreate).toHaveBeenCalledWith({ start: 75, end: 91 });
    expect(useLetterBenchStore.getState().pendingSelection).toBeNull();

    useLetterBenchStore.getState().setPendingSelection({ start: 20, end: 24, text: "Site", insideAssertionId: "A1", splitAt: 10 });
    rerender(
      <IncomingLetterCanvas text={TEXT} assertions={assertions} readOnly={false} busy={false} onCreate={onCreate} onSplit={onSplit} />
    );
    fireEvent.click(screen.getByRole("button", { name: "Split A1 here" }));
    expect(onSplit).toHaveBeenCalledWith("A1", 10);
  });

  it("says so when there is no text, instead of showing an empty page", () => {
    render(<IncomingLetterCanvas text="" assertions={[]} readOnly={false} busy={false} onCreate={vi.fn()} onSplit={vi.fn()} />);
    expect(screen.getByText("No text could be read from this document.")).toBeInTheDocument();
  });
});

describe("PositionGateBar", () => {
  const handlers = () => ({ onLock: vi.fn(), onCompose: vi.fn(), onSubmit: vi.fn() });

  it("blocks locking while a position is open and says what is missing", () => {
    const h = handlers();
    render(<PositionGateBar bench={bench({}, { blockers: ["2 positions still need your call"] })} busy={false} pending={false} {...h} />);
    expect(screen.getByRole("button", { name: "Lock positions" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("2 positions still need your call");
    expect(screen.getByRole("progressbar", { name: "Positions set" })).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByTestId("positions-count")).toHaveTextContent("0 of 2 set");
  });

  it("moves from lock to compose to submit as the board advances", () => {
    const h = handlers();
    const decided = [assertion({ verdict: "none" }), assertion({ assertion_id: "A2", verdict: "partial" })];
    const { rerender } = render(
      <PositionGateBar bench={bench({ assertions: decided }, { can_lock: true })} busy={false} pending={false} {...h} />
    );
    fireEvent.click(screen.getByRole("button", { name: "Lock positions" }));
    expect(h.onLock).toHaveBeenCalled();

    rerender(
      <PositionGateBar bench={bench({ assertions: decided, stage: "locked" }, { can_compose: true })} busy={false} pending={false} {...h} />
    );
    fireEvent.click(screen.getByRole("button", { name: "Compose reply" }));
    expect(h.onCompose).toHaveBeenCalled();

    rerender(
      <PositionGateBar
        bench={bench({ assertions: decided, stage: "composed", draft_run_id: "run1", reply_paragraphs: [paragraph()] })}
        busy={false}
        pending={false}
        {...h}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));
    expect(h.onSubmit).toHaveBeenCalled();
  });

  it("offers no action to a reviewer", () => {
    const readOnly = { ...bench({}, { can_lock: true }), read_only: true };
    render(<PositionGateBar bench={readOnly} busy={false} pending={false} {...handlers()} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("ReplyComposer", () => {
  const assertions = [assertion({ verdict: "none" }), assertion({ assertion_id: "A2", verdict: "partial" })];
  const paragraphs = [
    paragraph({ paragraph_id: "P-opening", assertion_id: null, part: "opening", text: "We refer to your letter." }),
    paragraph(),
    paragraph({ paragraph_id: "P-A2", assertion_id: "A2", text: "Accepted in part.", stale: true }),
  ];

  it("ties each point paragraph to its assertion and badges only the stale one", () => {
    render(<ReplyComposer paragraphs={paragraphs} assertions={assertions} readOnly={false} composing={false} onSave={vi.fn()} onRerun={vi.fn()} />);
    expect(screen.getByLabelText("Reply to A1 · None")).toHaveValue("Not accepted.");
    expect(screen.getByTestId("reply-paragraph-P-A2")).toHaveTextContent("Out of date");
    expect(screen.getByTestId("reply-paragraph-P-A1")).not.toHaveTextContent("Out of date");
    expect(screen.getByLabelText("Opening")).toHaveValue("We refer to your letter.");
    expect(screen.getAllByRole("button", { name: "Re-run" })).toHaveLength(1);
  });

  it("re-runs one paragraph and saves a hand edit only when it changed", () => {
    const onSave = vi.fn();
    const onRerun = vi.fn();
    render(<ReplyComposer paragraphs={paragraphs} assertions={assertions} readOnly={false} composing={false} onSave={onSave} onRerun={onRerun} />);
    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
    expect(onRerun).toHaveBeenCalledWith("A2");
    const editor = screen.getByLabelText("Reply to A1 · None");
    fireEvent.blur(editor);
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.change(editor, { target: { value: "Not accepted: notice out of time." } });
    fireEvent.blur(editor);
    expect(onSave).toHaveBeenCalledWith("P-A1", "Not accepted: notice out of time.");
  });

  it("lights up the assertion a paragraph answers when it is focused", () => {
    render(<ReplyComposer paragraphs={paragraphs} assertions={assertions} readOnly={false} composing={false} onSave={vi.fn()} onRerun={vi.fn()} />);
    fireEvent.focus(screen.getByLabelText("Reply to A2 · Partial"));
    expect(useLetterBenchStore.getState().activeAssertionId).toBe("A2");
    fireEvent.focus(screen.getByLabelText("Opening"));
    expect(useLetterBenchStore.getState().activeAssertionId).toBe("A2"); // framing has no assertion to light
  });

  it("explains the empty state", () => {
    render(<ReplyComposer paragraphs={[]} assertions={assertions} readOnly={false} composing={false} onSave={vi.fn()} onRerun={vi.fn()} />);
    expect(screen.getByText(/once positions are locked/)).toBeInTheDocument();
  });
});
