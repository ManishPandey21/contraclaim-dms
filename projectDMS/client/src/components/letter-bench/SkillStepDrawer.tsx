import React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useSkillStep } from "@/hooks/useLetterBench";
import type { SkillStep } from "@/types/letterBench";

const ANALYSIS_PARTS: Array<[string, string]> = [
  ["key_facts_asserted", "C. Key facts asserted"],
  ["contractual_basis_claimed", "D. Contractual basis claimed"],
  ["relief_sought", "E. Relief or action sought"],
  ["strengths_and_weaknesses", "F. Strengths and weaknesses"],
  ["implications_and_risks", "G. Contractual implications and risks"],
  ["recommended_response_strategy", "H. Recommended response strategy"],
];

const VARIATION_CLASS: Record<string, string> = {
  instructed_variation: "Instructed variation",
  value_engineering: "Value engineering",
  constructive_variation: "Constructive variation",
  not_a_variation: "Not a variation",
  insufficient_information: "Insufficient information",
};

const VALUATION_LIMB: Record<string, string> = {
  contract_rates: "First limb: contract rates",
  derived_rates: "Second limb: rate derived from contract rates",
  cost_plus: "Third limb: cost plus reasonable profit",
  daywork: "Daywork (only when instructed)",
  not_determined: "Not determined",
};

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <section className="space-y-1.5">
    <h3 className="text-sm font-semibold text-foreground">{title}</h3>
    <div className="text-sm leading-relaxed text-foreground">{children}</div>
  </section>
);

const Bullets: React.FC<{ items: string[]; empty?: string }> = ({ items, empty = "Nothing recorded." }) =>
  items.length ? (
    <ul className="ml-4 list-disc space-y-1">
      {items.map((item, index) => (
        <li key={`${index}-${item.slice(0, 20)}`}>{item}</li>
      ))}
    </ul>
  ) : (
    <p className="text-muted-foreground">{empty}</p>
  );

/** Renders a step's structured output by its schema, in the order the skill prescribes. */
export const StepOutput: React.FC<{ step: SkillStep }> = ({ step }) => {
  const output = step.output ?? {};
  if (step.output_schema_key === "letter_analysis.v1") {
    const particulars = (output.basic_particulars ?? {}) as Record<string, string | null>;
    return (
      <div className="space-y-5">
        <Section title="Short summary">
          <Bullets items={asList(output.summary_lines)} />
        </Section>
        <Section title="A. Basic particulars">
          <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1">
            {["from", "to", "date", "reference", "subject"].map((key) => (
              <React.Fragment key={key}>
                <dt className="capitalize text-muted-foreground">{key}</dt>
                <dd>{particulars[key] || "Not identified"}</dd>
              </React.Fragment>
            ))}
          </dl>
        </Section>
        <Section title="B. Purpose and nature">
          <p>{asText(output.purpose_and_nature) || "Not identified."}</p>
        </Section>
        {ANALYSIS_PARTS.map(([key, title]) => (
          <Section key={key} title={title}>
            <Bullets items={asList(output[key])} />
          </Section>
        ))}
        {asText(output.immediate_action) && (
          <div role="note" className="rounded-md border-2 border-amber-600 bg-amber-50 p-3 text-sm dark:bg-amber-950">
            <p className="font-semibold">Immediate action required</p>
            <p>{asText(output.immediate_action)}</p>
          </div>
        )}
      </div>
    );
  }
  if (step.output_schema_key === "variation_evaluation.v1") {
    const limb = asText(output.valuation_limb);
    return (
      <div className="space-y-5">
        <Section title="1. Description and classification">
          <p>{asText(output.description) || "Not described."}</p>
          <p className="mt-1 font-medium">
            {VARIATION_CLASS[asText(output.classification)] ?? asText(output.classification)}
          </p>
        </Section>
        <Section title="2. Contractual basis">
          <Bullets items={asList(output.contractual_basis)} />
        </Section>
        <Section title="3. Valuation and hierarchy">
          <p className="font-medium">{VALUATION_LIMB[limb] ?? limb}</p>
          <p>{asText(output.valuation_justification)}</p>
          {asText(output.valuation_amount) && <p>Amount: {asText(output.valuation_amount)}</p>}
        </Section>
        <Section title="4. Time impact">
          <p>{asText(output.time_impact) || "Not assessed."}</p>
        </Section>
        <Section title="5. Recommended wording">
          <p>{asText(output.recommended_wording) || "None proposed."}</p>
        </Section>
        <Section title="Residual risks">
          <Bullets items={asList(output.residual_risks)} />
        </Section>
        {asList(output.missing_information).length > 0 && (
          <Section title="Information required">
            <Bullets items={asList(output.missing_information)} />
          </Section>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-5">
      <Section title="Reply paragraph">
        <p className="whitespace-pre-wrap">{asText(output.text)}</p>
      </Section>
      {asText(output.internal_note) && (
        <Section title="Internal note (not for issue)">
          <p>{asText(output.internal_note)}</p>
        </Section>
      )}
      <Section title="Residual risks">
        <Bullets items={asList(output.residual_risks)} />
      </Section>
    </div>
  );
};

interface SkillStepDrawerProps {
  letterId: string;
  stepId: string | null;
  readOnly: boolean;
  onClose: () => void;
  onRerun: (step: SkillStep) => void;
}

export const SkillStepDrawer: React.FC<SkillStepDrawerProps> = ({
  letterId,
  stepId,
  readOnly,
  onClose,
  onRerun,
}) => {
  const { data: step, isLoading, isError } = useSkillStep(letterId, stepId);
  return (
    <Sheet open={Boolean(stepId)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{step?.skill_name ?? "Skill output"}</SheetTitle>
          <SheetDescription>
            {step
              ? `Version ${step.skill_version} · hash ${step.skill_hash.slice(0, 12)}${
                  step.target_assertion_ids.length ? ` · on ${step.target_assertion_ids.join(", ")}` : ""
                }${step.role ? ` · drafting as ${step.role}` : ""}`
              : "What this skill produced"}
          </SheetDescription>
        </SheetHeader>
        <div className="mt-4 space-y-4">
          {isLoading && <Skeleton className="h-64 w-full" />}
          {isError && <p className="text-sm text-destructive">This output could not be loaded.</p>}
          {step && (
            <>
              {step.stale && (
                <p role="status" className="rounded-md border border-amber-600 bg-amber-50 p-2 text-sm dark:bg-amber-950">
                  This output is out of date: something it depends on has changed since it ran.
                </p>
              )}
              {step.degraded && (
                <p role="status" className="flex gap-2 rounded-md border border-amber-600 bg-amber-50 p-2 text-sm dark:bg-amber-950">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>
                    The language model was unavailable, so this is the rule-based result. Run it again when the
                    service is back.
                  </span>
                </p>
              )}
              <StepOutput step={step} />
              {!readOnly && (
                <Button variant="outline" onClick={() => onRerun(step)}>
                  <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
                  Run again
                </Button>
              )}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
};

export default SkillStepDrawer;
