import React, { useRef } from "react";
import { Check, CircleHelp, CircleSlash, X } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  VERDICT_DESCRIPTIONS,
  VERDICT_LABELS,
  VERDICT_ORDER,
  type Verdict,
} from "@/types/letterBench";
import { VERDICT_TONE } from "./verdictStyles";

// Each verdict carries a word and an icon; colour only reinforces them.
const VERDICT_ICON: Record<Verdict, React.ComponentType<{ className?: string }>> = {
  entitlement: Check,
  partial: CircleSlash,
  none: X,
  need_information: CircleHelp,
};

interface VerdictChipsProps {
  assertionId: string;
  value?: Verdict | null;
  proposed?: Verdict | null;
  disabled?: boolean;
  onChange: (verdict: Verdict) => void;
}

/**
 * The letters skill's four-way classification as a radio group. Each option
 * carries its name in text, so the choice never depends on colour.
 *
 * Arrow keys move focus only; Enter or Space chooses. Choosing is a saved
 * decision that can unlock positions and put a paragraph out of date, so it
 * must not happen as a side effect of moving through the options.
 */
export const VerdictChips: React.FC<VerdictChipsProps> = ({
  assertionId,
  value,
  proposed,
  disabled,
  onChange,
}) => {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const focusIndex = Math.max(
    0,
    VERDICT_ORDER.indexOf((value ?? proposed ?? VERDICT_ORDER[0]) as Verdict)
  );

  const onKeyDown = (event: React.KeyboardEvent, index: number) => {
    const step =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + VERDICT_ORDER.length) % VERDICT_ORDER.length;
    refs.current[next]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={`Position on ${assertionId}`}
      className="flex flex-wrap gap-1.5"
    >
      {VERDICT_ORDER.map((verdict, index) => {
        const checked = value === verdict;
        const isProposed = proposed === verdict;
        const Icon = VERDICT_ICON[verdict];
        return (
          <button
            key={verdict}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={`${VERDICT_LABELS[verdict]}: ${VERDICT_DESCRIPTIONS[verdict]}${
              isProposed ? " (proposed)" : ""
            }`}
            title={VERDICT_DESCRIPTIONS[verdict]}
            tabIndex={index === focusIndex ? 0 : -1}
            disabled={disabled}
            onClick={() => {
              if (!checked) onChange(verdict);
            }}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              "inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-sm font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              "disabled:cursor-not-allowed disabled:opacity-60",
              checked
                ? cn(VERDICT_TONE[verdict].chip, "shadow-sm")
                : "border-border bg-background text-muted-foreground hover:border-foreground/40 hover:text-foreground",
              !checked && isProposed && "border-dashed border-foreground/60 text-foreground"
            )}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
            {VERDICT_LABELS[verdict]}
            {isProposed && !checked && (
              <span className="rounded bg-muted px-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                proposed
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
};

export default VerdictChips;
