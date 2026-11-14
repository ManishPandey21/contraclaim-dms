import React, { useEffect, useRef, useState } from "react";
import { AlertTriangle, ChevronDown, Combine, FileSearch, Sparkles, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { Assertion, ChainLink, SkillStepSummary, Verdict } from "@/types/letterBench";
import { VERDICT_LABELS } from "@/types/letterBench";
import { scrollWithinPane } from "./benchLogic";
import { VerdictChips } from "./VerdictChips";
import { VERDICT_TONE } from "./verdictStyles";

const CLAUSE_LABEL = {
  found: "found in this contract",
  not_found: "not found in this contract",
  not_checked: "not checked",
} as const;

interface AssertionCardProps {
  assertion: Assertion;
  /** The chain link (if any) whose skill is routed to this assertion. */
  routedLink?: ChainLink;
  steps: SkillStepSummary[];
  nextAssertionId?: string;
  selected: boolean;
  active: boolean;
  readOnly: boolean;
  onSelect: () => void;
  onHover: (active: boolean) => void;
  onVerdict: (verdict: Verdict) => void;
  onDirection: (text: string) => void;
  onRouteSkill: () => void;
  onOpenStep: (stepId: string) => void;
  onMergeNext: () => void;
  onRemove: () => void;
}

export const AssertionCard: React.FC<AssertionCardProps> = ({
  assertion,
  routedLink,
  steps,
  nextAssertionId,
  selected,
  active,
  readOnly,
  onSelect,
  onHover,
  onVerdict,
  onDirection,
  onRouteSkill,
  onOpenStep,
  onMergeNext,
  onRemove,
}) => {
  const [direction, setDirection] = useState(assertion.user_direction ?? "");
  const [expanded, setExpanded] = useState(false);
  const cardRef = useRef<HTMLElement>(null);
  const id = assertion.assertion_id;

  useEffect(() => {
    setDirection(assertion.user_direction ?? "");
  }, [assertion.user_direction]);

  useEffect(() => {
    if (selected) scrollWithinPane(cardRef.current);
  }, [selected]);

  const commitDirection = () => {
    if (direction.trim() !== (assertion.user_direction ?? "")) onDirection(direction);
  };

  const evaluation = steps.find(
    (step) =>
      step.target_assertion_ids.includes(id) &&
      step.output_schema_key !== "reply_paragraph.v1" &&
      step.output_schema_key !== "letter_analysis.v1"
  );
  const long = assertion.quoted_text.length > 220;
  const overridden =
    assertion.verdict &&
    assertion.proposed_verdict &&
    assertion.verdict !== assertion.proposed_verdict;

  return (
    <article
      ref={cardRef}
      aria-labelledby={`${id}-title`}
      data-testid={`assertion-card-${id}`}
      data-active={active || undefined}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      onFocusCapture={onSelect}
      className={cn(
        "rounded-lg border bg-card p-4 transition-shadow",
        selected ? "border-primary shadow-md ring-1 ring-primary/40" : "border-border",
        active && !selected && "border-foreground/40",
        !assertion.verdict && "border-l-4 border-l-yellow-500"
      )}
    >
      <header className="flex items-start gap-3">
        <button
          type="button"
          onClick={onSelect}
          aria-label={`Show ${id} in the letter`}
          className={cn(
            "flex h-7 min-w-9 shrink-0 items-center justify-center rounded-md px-1.5 text-xs font-bold text-white",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            assertion.verdict ? VERDICT_TONE[assertion.verdict].dot : "bg-slate-600"
          )}
        >
          {id}
        </button>
        <div className="min-w-0 flex-1">
          <h3 id={`${id}-title`} className="sr-only">
            Assertion {id}
            {assertion.verdict ? `, position ${VERDICT_LABELS[assertion.verdict]}` : ", undecided"}
          </h3>
          <blockquote
            className={cn(
              "border-l-2 border-muted-foreground/30 pl-3 text-sm leading-relaxed text-foreground",
              !expanded && long && "line-clamp-3"
            )}
          >
            {assertion.quoted_text}
          </blockquote>
          {long && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              className="mt-1 text-xs font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {expanded ? "Show less" : "Show all"}
            </button>
          )}
        </div>
        {!readOnly && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="h-8 px-2" aria-label={`More actions for ${id}`}>
                <ChevronDown className="h-4 w-4" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={onRouteSkill}>
                <Sparkles className="mr-2 h-4 w-4" aria-hidden="true" /> Apply a skill…
              </DropdownMenuItem>
              {nextAssertionId && (
                <DropdownMenuItem onSelect={onMergeNext}>
                  <Combine className="mr-2 h-4 w-4" aria-hidden="true" /> Merge with {nextAssertionId}
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={onRemove} className="text-destructive focus:text-destructive">
                <Trash2 className="mr-2 h-4 w-4" aria-hidden="true" /> Remove
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </header>

      <dl className="mt-3 space-y-1.5 text-xs">
        <div className="flex flex-wrap items-center gap-1.5">
          <dt className="font-medium text-muted-foreground">Relies on</dt>
          <dd className="flex flex-wrap gap-1.5">
            {assertion.clause_checks.length === 0 && (
              <span className="text-muted-foreground">no clause cited</span>
            )}
            {assertion.clause_checks.map((check) => (
              <span
                key={check.clause_number}
                title={check.note ?? check.title ?? undefined}
                className={cn(
                  "inline-flex items-center gap-1 rounded border px-1.5 py-0.5",
                  check.state === "found" && "border-emerald-600/40 text-foreground",
                  check.state === "not_found" && "border-rose-600/60 bg-rose-50 text-rose-900 dark:bg-rose-950 dark:text-rose-100",
                  check.state === "not_checked" && "border-dashed text-muted-foreground"
                )}
              >
                {check.state === "not_found" && <AlertTriangle className="h-3 w-3" aria-hidden="true" />}
                <span className="font-semibold">{check.clause_number}</span>
                <span>{CLAUSE_LABEL[check.state]}</span>
              </span>
            ))}
          </dd>
        </div>

        {(routedLink || evaluation) && (
          <div className="flex flex-wrap items-center gap-1.5">
            <dt className="font-medium text-muted-foreground">Skill</dt>
            <dd className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium text-foreground">{routedLink?.label ?? evaluation?.skill_name}</span>
              {(routedLink?.skill_version || evaluation?.skill_version) && (
                <span className="text-muted-foreground">v{routedLink?.skill_version ?? evaluation?.skill_version}</span>
              )}
              {routedLink?.status === "running" && <span className="text-primary">running…</span>}
              {evaluation?.stale && <span className="font-medium text-amber-700 dark:text-amber-300">out of date</span>}
              {evaluation && (
                <button
                  type="button"
                  onClick={() => onOpenStep(evaluation.step_id)}
                  className="inline-flex items-center gap-1 font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <FileSearch className="h-3 w-3" aria-hidden="true" /> View evaluation
                </button>
              )}
            </dd>
          </div>
        )}

        {assertion.needs.length > 0 && (
          <div>
            <dt className="font-medium text-muted-foreground">Needs</dt>
            <dd>
              <ul className="ml-4 list-disc text-foreground">
                {assertion.needs.map((need) => (
                  <li key={need}>{need}</li>
                ))}
              </ul>
            </dd>
          </div>
        )}

        {assertion.source_ids.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <dt className="font-medium text-muted-foreground">Sources</dt>
            <dd className="flex flex-wrap gap-1">
              {assertion.source_ids.map((source) => (
                <span key={source} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
                  {source}
                </span>
              ))}
            </dd>
          </div>
        )}
      </dl>

      <div className="mt-3 space-y-2">
        <VerdictChips
          assertionId={id}
          value={assertion.verdict}
          proposed={assertion.proposed_verdict}
          disabled={readOnly}
          onChange={onVerdict}
        />
        {assertion.proposed_reason && (
          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Why: </span>
            {assertion.proposed_reason}
            {overridden && (
              <span className="ml-1 rounded bg-muted px-1 font-medium text-foreground">You overrode the proposal</span>
            )}
          </p>
        )}
        <div>
          <label htmlFor={`${id}-direction`} className="sr-only">
            Direction for the reply to {id}
          </label>
          <Input
            id={`${id}-direction`}
            value={direction}
            maxLength={2000}
            disabled={readOnly}
            placeholder="Add a line of direction for this point (optional)"
            onChange={(event) => setDirection(event.target.value)}
            onBlur={commitDirection}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                commitDirection();
              }
            }}
            className="h-9 text-sm"
          />
        </div>
      </div>
    </article>
  );
};

export default AssertionCard;
