import {
  ELEMENT_KEYS,
  type ClaimHead,
  type ElementKey,
  type ElementState,
  type HeadClassification,
  type HeadElement,
  type HeadKind,
  type Perspective,
} from "@/types/claimBench";

// Pure helpers for the Claim Bench. The server owns every rule; these only
// decide how the board is worded and summarised on screen.

export const ELEMENT_LABEL: Record<ElementKey, string> = {
  event: "Event",
  notice: "Notice",
  contractual_basis: "Contractual basis",
  causation: "Causation",
  quantum: "Quantum",
};

export const ELEMENT_QUESTION: Record<ElementKey, string> = {
  event: "What happened, and is it recorded?",
  notice: "Was notice given, and in time?",
  contractual_basis: "Which clause gives the entitlement?",
  causation: "Did the event cause the time or cost claimed?",
  quantum: "Are the days and amount built up from records?",
};

export const STATE_LABEL: Record<ElementState, string> = {
  supported: "Supported",
  weak: "Weak",
  missing: "Missing",
  not_applicable: "Not applicable",
};

export const STATE_OPTIONS: ElementState[] = ["supported", "weak", "missing", "not_applicable"];

export const KIND_LABEL: Record<HeadKind, string> = {
  time: "Time",
  cost: "Cost",
  time_and_cost: "Time and cost",
};

export const CLASSIFICATION_LABEL: Record<HeadClassification, string> = {
  excusable: "Excusable (time only)",
  compensable: "Compensable (time and cost)",
  contractor_risk: "Contractor's risk (no entitlement)",
  undetermined: "Not determined",
};

export const CLASSIFICATION_OPTIONS: HeadClassification[] = ["excusable", "compensable", "contractor_risk"];

export const PERSPECTIVE_LABEL: Record<Perspective, string> = {
  contractor: "Contractor",
  engineer: "Engineer",
  employer: "Employer",
};

export type LightTone = "supported" | "weak" | "missing" | "na" | "proposed" | "empty";

/**
 * Colour of one element's light. A decision shows solid; a proposal nobody has
 * decided on shows as "proposed", so a board never looks more settled than it is.
 */
export function lightTone(element?: HeadElement | null): LightTone {
  if (!element) return "empty";
  if (element.state) return element.state === "not_applicable" ? "na" : element.state;
  return element.proposed_state ? "proposed" : "empty";
}

export function lightDescription(key: ElementKey, element?: HeadElement | null): string {
  const label = ELEMENT_LABEL[key];
  if (element?.state) return `${label}: decided ${STATE_LABEL[element.state].toLowerCase()}`;
  if (element?.proposed_state) {
    return `${label}: proposed ${STATE_LABEL[element.proposed_state].toLowerCase()}, not yet decided`;
  }
  return `${label}: not tested`;
}

export function headProgress(head: ClaimHead): { decided: number; total: number; done: boolean } {
  const decided = ELEMENT_KEYS.filter((key) => head.elements[key]?.state).length;
  return { decided, total: ELEMENT_KEYS.length, done: decided === ELEMENT_KEYS.length && Boolean(head.classification) };
}

/** True when a person's call differs from what was proposed. Worth showing; never wrong. */
export function differsFromProposal(element?: HeadElement | null): boolean {
  return Boolean(element?.state && element.proposed_state && element.state !== element.proposed_state);
}

export function proposalSource(element?: HeadElement | null): string {
  if (!element?.proposed_state) return "";
  return element.proposed_by === "skill" ? "Proposed by the claims skill" : "From the records only";
}

/** What the last test of a head was, in one line. */
export function testSummary(head: ClaimHead): string {
  if (!head.step_id) return "Not tested yet";
  const bySkill = ELEMENT_KEYS.some((key) => head.elements[key]?.proposed_by === "skill");
  const what = bySkill ? "Tested with the claims skill" : "Checked against the records only";
  return head.test_stale ? `${what}, before the head was last edited` : what;
}

export function parseAmount(text: string): number | null | undefined {
  const cleaned = text.replace(/[,\s₹]/g, "");
  if (cleaned === "") return null;
  const value = Number(cleaned);
  return Number.isFinite(value) && value >= 0 ? value : undefined;
}

export function parseDays(text: string): number | null | undefined {
  const cleaned = text.trim();
  if (cleaned === "") return null;
  return /^\d{1,5}$/.test(cleaned) ? Number(cleaned) : undefined;
}
