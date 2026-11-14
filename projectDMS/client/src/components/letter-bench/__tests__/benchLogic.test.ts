import { describe, expect, it } from "vitest";

import {
  applyOptimisticPatch,
  assertionAtOffset,
  buildSegments,
  deadlineText,
  deadlineTone,
  decidedCount,
  fillHeight,
  isBenchBusy,
  linkTargetLabel,
  moveChainLink,
  offsetWithin,
  paletteSkills,
  paragraphBadge,
  removeChainLink,
  reorderChain,
  stepsForLink,
  unlocatedAssertions,
} from "../benchLogic";
import { CHAIN, TEXT, assertion, bench, link, paragraph, skill } from "./fixtures";

describe("letter segments", () => {
  it("splits the letter into plain and highlighted runs that rebuild the text exactly", () => {
    const segments = buildSegments(TEXT, bench().board.assertions);
    expect(segments.map((s) => s.assertion?.assertion_id ?? "-")).toEqual(["-", "A1", "-", "A2", "-"]);
    expect(segments.map((s) => s.text).join("")).toBe(TEXT);
    expect(segments[1].text).toBe(TEXT.slice(10, 40));
  });

  it("drops an overlapping span instead of duplicating text, and lists unplaced points", () => {
    const overlapping = [
      assertion(),
      assertion({ assertion_id: "A2", span: { start: 30, end: 60 } }),
      assertion({ assertion_id: "A3", span: null }),
      assertion({ assertion_id: "A4", span: { start: 80, end: 999 } }),
    ];
    const segments = buildSegments(TEXT, overlapping);
    expect(segments.map((s) => s.text).join("")).toBe(TEXT);
    expect(segments.filter((s) => s.assertion).map((s) => s.assertion!.assertion_id)).toEqual(["A1"]);
    expect(unlocatedAssertions(TEXT, overlapping).map((a) => a.assertion_id)).toEqual(["A3", "A4"]);
  });

  it("finds the assertion a caret sits inside, with the split offset", () => {
    const assertions = bench().board.assertions;
    expect(assertionAtOffset(assertions, 20)).toMatchObject({ relative: 10, assertion: { assertion_id: "A1" } });
    expect(assertionAtOffset(assertions, 10)).toBeNull(); // at the boundary there is nothing to split
    expect(assertionAtOffset(assertions, 5)).toBeNull();
  });

  it("maps a DOM point to a letter offset, ignoring the number badges", () => {
    const root = document.createElement("div");
    root.innerHTML = 'Dear Sir, <mark><span data-bench-skip="">A1</span>the Site</mark> was late';
    const markText = root.querySelector("mark")!.lastChild!;
    expect(offsetWithin(root, markText, 4)).toBe(14);
    expect(offsetWithin(root, root.lastChild!, 4)).toBe(22);
    expect(offsetWithin(root, document.createTextNode("elsewhere"), 0)).toBeNull();
  });
});

describe("optimistic verdict update", () => {
  it("applies the verdict at once without touching board_version", () => {
    const before = bench();
    const after = applyOptimisticPatch(before, "A1", { verdict: "partial" });
    expect(after.board.assertions[0]).toMatchObject({ verdict: "partial", verdict_source: "user", state: "decided" });
    expect(after.board.board_version).toBe(before.board.board_version);
    expect(after.gate.undecided).toBe(1);
    expect(before.board.assertions[0].verdict).toBeNull(); // the cached object is not mutated
  });

  it("marks a confirmed proposal as the proposal, and an override as the user's", () => {
    expect(applyOptimisticPatch(bench(), "A1", { verdict: "none" }).board.assertions[0].verdict_source).toBe("ai");
    expect(applyOptimisticPatch(bench(), "A1", { verdict: "entitlement" }).board.assertions[0].verdict_source).toBe("user");
  });

  it("stales only the paragraph that answers the changed assertion and blocks compose", () => {
    const composed = bench(
      { stage: "composed", reply_paragraphs: [paragraph(), paragraph({ paragraph_id: "P-A2", assertion_id: "A2" })] },
      { can_compose: true }
    );
    const after = applyOptimisticPatch(composed, "A2", { verdict: "none" });
    expect(after.board.reply_paragraphs.map((p) => p.stale)).toEqual([false, true]);
    expect(after.board.stage).toBe("positions");
    expect(after.gate.can_compose).toBe(false);
  });

  it("leaves structural edits and no-op edits to the server", () => {
    const before = bench();
    expect(applyOptimisticPatch(before, "A1", { split_at: 5 })).toBe(before);
    expect(applyOptimisticPatch(before, "A1", { remove: true })).toBe(before);
    expect(applyOptimisticPatch(before, "A9", { verdict: "none" })).toBe(before);
  });
});

describe("skill chain", () => {
  it("reorders only evaluation links; analysis and the reply never move", () => {
    const moved = reorderChain(CHAIN, "l-var", 1);
    expect(moved.map((l) => l.link_id)).toEqual(["l-eot", "l-var"]);
    expect(reorderChain(CHAIN, "l-var", -1).map((l) => l.link_id)).toEqual(["l-var", "l-eot"]);
    expect(reorderChain(CHAIN, "l-an", 1).map((l) => l.link_id)).toEqual(["l-var", "l-eot"]);
    expect(moveChainLink(CHAIN, "l-eot", "l-var").map((l) => l.link_id)).toEqual(["l-eot", "l-var"]);
    expect(removeChainLink(CHAIN, "l-var").map((l) => l.link_id)).toEqual(["l-eot"]);
  });

  it("labels what a link applies to", () => {
    expect(linkTargetLabel(link())).toBe("A2");
    expect(linkTargetLabel(link({ target_assertion_ids: [] }))).toBe("Whole letter");
    expect(linkTargetLabel(CHAIN[0])).toBe("");
  });

  it("finds the steps behind a link", () => {
    const steps = [
      { step_id: "s1", skill_key: "indian-infra-variations", target_assertion_ids: ["A2"] },
      { step_id: "s2", skill_key: "indian-infra-variations", target_assertion_ids: ["A9"] },
      { step_id: "s3", skill_key: "other", target_assertion_ids: ["A2"] },
    ] as never[];
    expect(stepsForLink(link(), steps).map((s) => s.step_id)).toEqual(["s1"]);
  });
});

describe("skill palette", () => {
  const skills = [
    skill(),
    skill({ skill_key: "eot", name: "EOT review", available: false, unavailable_reason: "Not installed" }),
    skill({ skill_key: "employer-only", name: "Audit", roles_supported: ["employer"] }),
    skill({ skill_key: "letters", name: "Letters", kind: "drafting" }),
  ];

  it("offers evaluation skills only, selectable ones first", () => {
    const options = paletteSkills(skills, "employer");
    expect(options.map((o) => o.name)).toEqual(["Audit", "Variations", "EOT review"]);
    expect(options.map((o) => o.selectable)).toEqual([true, true, false]);
  });

  it("keeps a skill listed but disabled for a role it does not support, with the reason", () => {
    const audit = paletteSkills(skills, "contractor").find((o) => o.skill_key === "employer-only")!;
    expect(audit.selectable).toBe(false);
    expect(audit.reason).toBe("Not available when drafting as contractor");
    expect(paletteSkills(skills, "contractor").find((o) => o.skill_key === "eot")!.reason).toBe("Not installed");
  });
});

describe("status helpers", () => {
  it("knows when a skill is running so the page polls only then", () => {
    expect(isBenchBusy(undefined)).toBe(false);
    expect(isBenchBusy(bench())).toBe(false);
    expect(isBenchBusy(bench({ stage: "dissecting" }))).toBe(true);
    expect(isBenchBusy(bench({ composing: true }))).toBe(true);
    expect(isBenchBusy(bench({ chain: [link({ status: "running" })] }))).toBe(true);
  });

  it("badges a paragraph by what the user most needs to know", () => {
    expect(paragraphBadge(paragraph())).toBeNull();
    expect(paragraphBadge(paragraph({ degraded: true }))).toBe("template");
    expect(paragraphBadge(paragraph({ degraded: true, edited_by_user: true }))).toBe("edited");
    expect(paragraphBadge(paragraph({ stale: true, edited_by_user: true }))).toBe("stale");
  });

  it("states the deadline in words", () => {
    expect([deadlineText(9), deadlineText(1), deadlineText(0), deadlineText(-2), deadlineText(null)]).toEqual([
      "Reply due in 9 days",
      "Reply due in 1 day",
      "Reply due today",
      "Reply overdue by 2 days",
      "No reply date set",
    ]);
    expect([deadlineTone(-1), deadlineTone(3), deadlineTone(10), deadlineTone(11), deadlineTone(undefined)]).toEqual([
      "overdue",
      "urgent",
      "soon",
      "calm",
      "unknown",
    ]);
  });

  it("counts decided positions and sizes the Bench to the space it has", () => {
    expect(decidedCount([assertion({ verdict: "none" }), assertion()])).toBe(1);
    expect(fillHeight(720, 67)).toBe(653);
    expect(fillHeight(300, 67)).toBe(420);
  });
});
