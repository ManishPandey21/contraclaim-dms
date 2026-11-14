// Pure helpers for the Letter Bench. No React, no I/O: unit-tested directly.
import type {
  Assertion,
  AssertionPatch,
  BenchResponse,
  ChainLink,
  ChainLinkInput,
  DraftRole,
  EffectiveSkill,
  ReplyParagraph,
  SkillStepSummary,
} from "@/types/letterBench";

export interface LetterSegment {
  key: string;
  text: string;
  start: number;
  assertion?: Assertion;
}

/** Split the letter into plain and highlighted runs. Overlaps keep the earlier assertion. */
export function buildSegments(text: string, assertions: Assertion[]): LetterSegment[] {
  const located = assertions
    .filter((a) => a.span && a.span.end <= text.length && a.span.end > a.span.start)
    .sort((a, b) => a.span!.start - b.span!.start);
  const segments: LetterSegment[] = [];
  let cursor = 0;
  for (const assertion of located) {
    const { start, end } = assertion.span!;
    if (start < cursor) continue;
    if (start > cursor) {
      segments.push({ key: `t-${cursor}`, text: text.slice(cursor, start), start: cursor });
    }
    segments.push({ key: `a-${assertion.assertion_id}`, text: text.slice(start, end), start, assertion });
    cursor = end;
  }
  if (cursor < text.length) {
    segments.push({ key: `t-${cursor}`, text: text.slice(cursor), start: cursor });
  }
  return segments;
}

/** Assertions that could not be placed on the letter text (shown in a list instead). */
export function unlocatedAssertions(text: string, assertions: Assertion[]): Assertion[] {
  return assertions.filter((a) => !a.span || a.span.end > text.length);
}

/** Which assertion, if any, contains this offset; with the offset relative to its start. */
export function assertionAtOffset(
  assertions: Assertion[],
  offset: number
): { assertion: Assertion; relative: number } | null {
  for (const assertion of assertions) {
    if (assertion.span && offset > assertion.span.start && offset < assertion.span.end) {
      return { assertion, relative: offset - assertion.span.start };
    }
  }
  return null;
}

/**
 * Apply a position edit to the cached board so the UI answers at once. Structural
 * edits (split, merge, remove) are left to the server response. board_version is
 * deliberately NOT bumped: it must stay the server's value for the next request.
 */
export function applyOptimisticPatch(
  bench: BenchResponse,
  assertionId: string,
  patch: AssertionPatch
): BenchResponse {
  if (patch.split_at !== undefined || patch.merge_with || patch.remove) return bench;
  let changed = false;
  const assertions = bench.board.assertions.map((assertion) => {
    if (assertion.assertion_id !== assertionId) return assertion;
    const next: Assertion = { ...assertion };
    if (patch.clear_verdict) {
      next.verdict = null;
      next.verdict_source = null;
    } else if (patch.verdict) {
      next.verdict = patch.verdict;
      next.verdict_source = patch.verdict === assertion.proposed_verdict ? "ai" : "user";
    }
    if (patch.user_direction !== undefined) {
      next.user_direction = patch.user_direction.trim() || null;
    }
    next.state = next.verdict ? "decided" : "open";
    changed =
      next.verdict !== assertion.verdict || next.user_direction !== assertion.user_direction;
    return next;
  });
  if (!changed) return bench;
  const undecided = assertions.filter((a) => !a.verdict).length;
  return {
    ...bench,
    board: {
      ...bench.board,
      assertions,
      stage: bench.board.stage === "dissecting" ? "dissecting" : "positions",
      reply_paragraphs: bench.board.reply_paragraphs.map((p) =>
        p.assertion_id === assertionId ? { ...p, stale: true } : p
      ),
    },
    gate: { ...bench.gate, undecided, can_compose: false },
  };
}

export function isBenchBusy(bench: BenchResponse | undefined): boolean {
  if (!bench) return false;
  return (
    bench.board.stage === "dissecting" ||
    bench.board.composing ||
    bench.board.chain.some((link) => link.status === "running" || link.status === "queued")
  );
}

export function decidedCount(assertions: Assertion[]): number {
  return assertions.filter((a) => Boolean(a.verdict)).length;
}

/** Skills a user may add to the chain for this role. Unavailable ones stay listed, disabled. */
export function paletteSkills(
  skills: EffectiveSkill[],
  role: DraftRole
): Array<EffectiveSkill & { selectable: boolean; reason?: string }> {
  return skills
    .filter((skill) => skill.kind === "evaluation")
    .map((skill) => {
      if (!skill.available) {
        return { ...skill, selectable: false, reason: skill.unavailable_reason ?? "Not installed" };
      }
      if (!skill.roles_supported.includes(role)) {
        return { ...skill, selectable: false, reason: `Not available when drafting as ${role}` };
      }
      return { ...skill, selectable: true };
    })
    .sort((a, b) => Number(b.selectable) - Number(a.selectable) || a.name.localeCompare(b.name));
}

/** The chain payload after moving one evaluation link. Analysis and the reply never move. */
export function reorderChain(chain: ChainLink[], linkId: string, direction: -1 | 1): ChainLinkInput[] {
  const middle = chain.filter((link) => link.kind === "evaluation");
  const index = middle.findIndex((link) => link.link_id === linkId);
  const target = index + direction;
  if (index === -1 || target < 0 || target >= middle.length) return toChainInput(middle);
  const next = [...middle];
  [next[index], next[target]] = [next[target], next[index]];
  return toChainInput(next);
}

export function moveChainLink(chain: ChainLink[], fromId: string, toId: string): ChainLinkInput[] {
  const middle = chain.filter((link) => link.kind === "evaluation");
  const from = middle.findIndex((link) => link.link_id === fromId);
  const to = middle.findIndex((link) => link.link_id === toId);
  if (from === -1 || to === -1 || from === to) return toChainInput(middle);
  const next = [...middle];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return toChainInput(next);
}

export function removeChainLink(chain: ChainLink[], linkId: string): ChainLinkInput[] {
  return toChainInput(chain.filter((link) => link.kind === "evaluation" && link.link_id !== linkId));
}

export function toChainInput(links: ChainLink[]): ChainLinkInput[] {
  return links.map((link) => ({
    link_id: link.link_id,
    skill_key: link.skill_key,
    target_assertion_ids: link.target_assertion_ids,
  }));
}

export type ParagraphBadge = "stale" | "template" | "edited" | null;

export function paragraphBadge(paragraph: ReplyParagraph): ParagraphBadge {
  if (paragraph.stale) return "stale";
  if (paragraph.edited_by_user) return "edited";
  if (paragraph.degraded) return "template";
  return null;
}

export function linkStatusLabel(link: ChainLink): string {
  switch (link.status) {
    case "pending":
      return "Not run yet";
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "done":
      return "Done";
    case "stale":
      return "Out of date";
    case "failed":
      return "Failed";
    case "unavailable":
      return "Unavailable";
  }
}

export function linkTargetLabel(link: ChainLink): string {
  if (link.kind !== "evaluation") return "";
  return link.target_assertion_ids.length ? link.target_assertion_ids.join(", ") : "Whole letter";
}

export function stepsForLink(link: ChainLink, steps: SkillStepSummary[]): SkillStepSummary[] {
  return steps.filter(
    (step) =>
      step.skill_key === link.skill_key &&
      (link.kind !== "evaluation" ||
        !link.target_assertion_ids.length ||
        step.target_assertion_ids.some((id) => link.target_assertion_ids.includes(id)))
  );
}

export function deadlineTone(days: number | null | undefined): "overdue" | "urgent" | "soon" | "calm" | "unknown" {
  if (days === null || days === undefined) return "unknown";
  if (days < 0) return "overdue";
  if (days <= 3) return "urgent";
  if (days <= 10) return "soon";
  return "calm";
}

export function deadlineText(days: number | null | undefined): string {
  if (days === null || days === undefined) return "No reply date set";
  if (days < 0) return `Reply overdue by ${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"}`;
  if (days === 0) return "Reply due today";
  return `Reply due in ${days} day${days === 1 ? "" : "s"}`;
}

/**
 * Character offset of a DOM point inside the letter canvas, counting only the
 * letter's own text. Nodes inside [data-bench-skip] (assertion number badges)
 * are not part of the letter and are skipped.
 */
export function offsetWithin(root: Node, node: Node, nodeOffset: number): number | null {
  if (!root.contains(node)) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (candidate) =>
      (candidate.parentElement?.closest("[data-bench-skip]") ?? null) !== null
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  });
  let total = 0;
  let current = walker.nextNode();
  while (current) {
    if (current === node) return total + nodeOffset;
    total += current.textContent?.length ?? 0;
    current = walker.nextNode();
  }
  // The point is an element boundary: count the text before the child at nodeOffset.
  if (node.nodeType === Node.ELEMENT_NODE) {
    const range = document.createRange();
    range.selectNodeContents(root);
    range.setEnd(node, nodeOffset);
    const fragment = range.cloneContents();
    fragment.querySelectorAll("[data-bench-skip]").forEach((el) => el.remove());
    return fragment.textContent?.length ?? 0;
  }
  return null;
}

/** Below this container width the three zones are shown as tabs. */
export const BENCH_WIDE_MIN = 960;
export const BENCH_MIN_HEIGHT = 420;

/** Height that fills the viewport below the element's top edge, never less than a usable minimum. */
export function fillHeight(viewportHeight: number, top: number): number {
  return Math.max(BENCH_MIN_HEIGHT, Math.round(viewportHeight - Math.max(0, top)));
}

/** On a phone the Bench takes the whole screen instead of the sliver the app shell leaves it. */
export const BENCH_FULLSCREEN_MAX = 640;

/**
 * Bring an element into view by scrolling ONLY its own Bench pane. Unlike
 * scrollIntoView this never scrolls the window, so the gate bar stays put.
 */
export function scrollWithinPane(element: HTMLElement | null | undefined): void {
  const pane = element?.closest<HTMLElement>("[data-bench-scroll]");
  if (!element || !pane) return;
  const paneBox = pane.getBoundingClientRect();
  const box = element.getBoundingClientRect();
  if (box.top >= paneBox.top && box.bottom <= paneBox.bottom) return;
  const target = pane.scrollTop + (box.top - paneBox.top) - Math.max(12, (paneBox.height - box.height) / 2);
  const reduce = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (typeof pane.scrollTo === "function") {
    pane.scrollTo({ top: Math.max(0, target), behavior: reduce ? "auto" : "smooth" });
  } else {
    pane.scrollTop = Math.max(0, target);
  }
}
