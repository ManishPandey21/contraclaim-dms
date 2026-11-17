import type {
  Criticality,
  DelayEventView,
  DelayMethod,
  DelayView,
  OverlapTreatment,
  OverlapView,
  QuantumCategory,
  QuantumFormula,
  RecordRole,
  RiskOwner,
} from "@/types/claimBench";
import { parseAmount } from "@/lib/claim-bench-logic";

// Pure helpers for the Delay Lab and Quantum Builder screens. Every number
// shown comes from the server; these only lay it out and word it.

export const RISK_OWNER_LABEL: Record<RiskOwner, string> = {
  employer: "Employer's risk",
  contractor: "Contractor's risk",
  neutral: "Neutral",
};

export const CRITICALITY_LABEL: Record<Criticality, string> = {
  critical: "Critical",
  not_critical: "Not critical",
  unproven: "Not proven critical",
};

export const METHOD_LABEL: Record<DelayMethod, string> = {
  as_planned_vs_as_built: "As-planned vs as-built",
  windows: "Windows analysis",
};

export const UNAVAILABLE_METHOD_LABEL: Record<string, string> = {
  time_impact_analysis: "Time impact analysis",
  collapsed_as_built: "Collapsed as-built",
};

export const TREATMENT_LABEL: Record<OverlapTreatment, string> = {
  eot_granted_no_cost: "Time, no cost",
  eot_granted_with_cost: "Time and cost",
  apportioned: "Apportioned",
  no_eot: "No time",
  not_true_concurrency: "Not true concurrency",
};

export const TREATMENT_HELP: Record<OverlapTreatment, string> = {
  eot_granted_no_cost: "The overlap counts towards the extension; prolongation cost does not run for it.",
  eot_granted_with_cost: "The overlap counts towards the extension, and cost caused by the Employer delay alone is separated.",
  apportioned: "Part of the overlap counts towards the extension. Say how many days.",
  no_eot: "The overlap does not count towards the extension.",
  not_true_concurrency: "The Contractor's delay did not really run at the same time, or was not independently critical.",
};

export const CATEGORY_LABEL: Record<QuantumCategory, string> = {
  direct: "Direct cost",
  prolongation: "Prolongation",
  disruption: "Disruption",
  overhead_formula: "Overheads by formula",
  loss_of_profit: "Loss of profit",
  interest: "Interest",
};

/**
 * The figures each head-office overhead formula needs, as the server computes it
 * (backend services/quantum_formulas.py FORMULA_INPUTS). Each figure is taken
 * from a record linked to the claim. The delay is the line's own period.
 */
export const FORMULA_INPUTS: Record<Exclude<QuantumFormula, "none">, { key: string; label: string }[]> = {
  hudson: [
    { key: "tender_percentage", label: "Head-office overhead and profit in the tender (%)" },
    { key: "contract_sum", label: "Contract sum" },
    { key: "contract_period_days", label: "Contract period (days)" },
  ],
  emden: [
    { key: "overhead_and_profit", label: "Head-office overhead and profit in the accounts" },
    { key: "turnover", label: "Turnover in the same accounts" },
    { key: "contract_sum", label: "Contract sum" },
    { key: "contract_period_days", label: "Contract period (days)" },
  ],
  eichleay: [
    { key: "contract_billings", label: "Billings on this contract during actual performance, excluding this overhead claim" },
    { key: "total_billings", label: "Total company billings for the same period" },
    { key: "total_overhead", label: "Head-office overhead for the same period, excluding profit" },
    { key: "performance_days", label: "Days of actual performance (the same period)" },
  ],
};

export const FORMULA_LABEL: Record<QuantumFormula, string> = {
  none: "None",
  hudson: "Hudson",
  emden: "Emden",
  eichleay: "Eichleay",
};

export const RECORD_ROLE_LABEL: Record<RecordRole, string> = {
  support: "Supports the cost",
  actual_loss: "Shows actual loss",
  work_declined: "Shows work declined",
};

export const LANES: { key: RiskOwner | "undecided"; label: string }[] = [
  { key: "employer", label: "Employer" },
  { key: "contractor", label: "Contractor" },
  { key: "neutral", label: "Neutral" },
  { key: "undecided", label: "Not decided" },
];

const DAY = 86_400_000;
const utc = (iso: string) => Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10)));

export interface TimelineBar {
  id: string;
  label: string;
  lane: RiskOwner | "undecided";
  /** Left edge and width as a percentage of the timeline. */
  left: number;
  width: number;
  /** Counted in the figures: decided critical with a decided owner. */
  counted: boolean;
  openEnded: boolean;
}

export interface Timeline {
  start: string;
  end: string;
  totalDays: number;
  bars: TimelineBar[];
  overlaps: { id: string; left: number; width: number; decided: boolean }[];
}

/**
 * Lay events and overlaps on one time axis. An event goes in the lane of the
 * risk owner a person decided, and in "Not decided" until then, so the picture
 * never shows a proposal as a decision. Returns null when nothing can be drawn.
 */
export function buildTimeline(view: Pick<DelayView, "events" | "overlaps" | "as_of_date">): Timeline | null {
  const asOf = view.as_of_date ? view.as_of_date.slice(0, 10) : null;
  const spans = view.events
    .map((event) => ({ event, start: event.start_date.slice(0, 10), end: event.end_date?.slice(0, 10) ?? asOf }))
    .filter((item) => item.end !== null && utc(item.end as string) >= utc(item.start)) as {
    event: DelayEventView;
    start: string;
    end: string;
  }[];
  if (spans.length === 0) return null;
  const first = Math.min(...spans.map((item) => utc(item.start)));
  const last = Math.max(...spans.map((item) => utc(item.end)));
  const totalDays = Math.round((last - first) / DAY) + 1;
  const place = (start: string, end: string) => ({
    left: ((utc(start) - first) / DAY / totalDays) * 100,
    width: ((Math.round((utc(end) - utc(start)) / DAY) + 1) / totalDays) * 100,
  });
  return {
    start: new Date(first).toISOString().slice(0, 10),
    end: new Date(last).toISOString().slice(0, 10),
    totalDays,
    bars: spans.map(({ event, start, end }) => ({
      id: event.event_id,
      label: event.label,
      lane: event.decision.risk_owner ?? "undecided",
      counted: event.counted,
      openEnded: event.open_ended,
      ...place(start, end),
    })),
    overlaps: view.overlaps.map((overlap: OverlapView) => ({
      id: overlap.overlap_id,
      decided: Boolean(overlap.finding),
      ...place(overlap.start_date.slice(0, 10), overlap.end_date.slice(0, 10)),
    })),
  };
}

/** The extension in words. A range is stated as a range, never rounded to one figure. */
export function netEotText(net: DelayView["net"]): string {
  if (net.settled && net.days != null) return `${net.days} day${net.days === 1 ? "" : "s"}`;
  return `${net.low} to ${net.high} days`;
}

export function netEotExplanation(view: DelayView): string {
  const { net } = view;
  const parts = [`${net.employer_only_days} Employer critical days with no Contractor delay`];
  if (view.overlaps.length > 0) {
    const undecided = view.overlaps.filter((overlap) => !overlap.finding).length;
    parts.push(
      undecided > 0
        ? `${net.overlap_days_to_eot} from decided overlaps, ${undecided} overlap${undecided === 1 ? "" : "s"} still to decide`
        : `${net.overlap_days_to_eot} from overlaps as decided`
    );
  }
  if (net.neutral_days_included > 0) parts.push(`${net.neutral_days_included} neutral days`);
  if (net.previously_granted > 0) parts.push(`less ${net.previously_granted} already granted`);
  return parts.join("; ") + ".";
}

export function eventNeedsDecision(event: DelayEventView): boolean {
  return !event.decision.risk_owner || !event.decision.criticality;
}

/** The figures a formula line still needs, by label. Empty when the line can be sent. */
export const missingFormulaFigures = (
  category: QuantumCategory,
  formula: QuantumFormula,
  inputs: Record<string, { value: string } | undefined>
): string[] => {
  if (category !== "overhead_formula" || formula === "none") return [];
  return FORMULA_INPUTS[formula]
    .filter(({ key }) => {
      const value = parseAmount(inputs[key]?.value ?? "");
      return typeof value !== "number" || value <= 0;
    })
    .map(({ label }) => label);
};
