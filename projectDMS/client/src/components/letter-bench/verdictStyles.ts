import type { ChainLinkStatus, Verdict } from "@/types/letterBench";

export const VERDICT_TONE: Record<Verdict, { chip: string; mark: string; dot: string }> = {
  // chip = the selected state: solid fill with white text (at least 4.5:1).
  entitlement: {
    chip: "border-emerald-700 bg-emerald-700 text-white",
    mark: "bg-emerald-100/80 decoration-emerald-600 dark:bg-emerald-900/50",
    dot: "bg-emerald-700",
  },
  partial: {
    chip: "border-amber-700 bg-amber-700 text-white",
    mark: "bg-amber-100/80 decoration-amber-600 dark:bg-amber-900/50",
    dot: "bg-amber-700",
  },
  none: {
    chip: "border-rose-700 bg-rose-700 text-white",
    mark: "bg-rose-100/80 decoration-rose-600 dark:bg-rose-900/50",
    dot: "bg-rose-700",
  },
  need_information: {
    chip: "border-sky-700 bg-sky-700 text-white",
    mark: "bg-sky-100/80 decoration-sky-600 dark:bg-sky-900/50",
    dot: "bg-sky-700",
  },
};

export const UNDECIDED_MARK = "bg-yellow-100/70 decoration-slate-500 dark:bg-yellow-900/30";

export const LINK_STATUS_TONE: Record<ChainLinkStatus, string> = {
  pending: "border-dashed border-muted-foreground/40 text-muted-foreground",
  queued: "border-primary/40 text-foreground",
  running: "border-primary bg-primary/5 text-foreground",
  done: "border-emerald-600/50 bg-emerald-50/60 text-foreground dark:bg-emerald-950/40",
  stale: "border-amber-600 bg-amber-50 text-foreground dark:bg-amber-950/40",
  failed: "border-destructive bg-destructive/10 text-foreground",
  unavailable: "border-dashed border-muted-foreground/40 bg-muted/40 text-muted-foreground",
};
