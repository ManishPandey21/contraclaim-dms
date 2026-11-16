import React from "react";

import { gateSteps, type GateState } from "@/lib/workflow-gates";
import type { ArbitrationWorkflowState } from "@/services/arbitration-drafting-api";

const MARK: Record<GateState, string> = { done: "✓", current: "●", working: "…", upcoming: "○", stopped: "✕" };
const STATE_TEXT: Record<GateState, string> = {
  done: "Done",
  current: "Waiting for you or a colleague",
  working: "In progress",
  upcoming: "Not yet",
  stopped: "Stopped",
};
const STATE_CLASS: Record<GateState, string> = {
  done: "border-green-700 bg-green-50 text-green-900",
  current: "border-blue-700 bg-blue-50 text-blue-950 font-medium",
  working: "border-slate-400 bg-slate-50 text-slate-900",
  upcoming: "border-slate-200 text-muted-foreground",
  stopped: "border-red-700 bg-red-50 text-red-900",
};

interface Props {
  workflow: Pick<ArbitrationWorkflowState, "status" | "current_node" | "required_human_role" | "approval_receipt_ids">;
}

/**
 * The eight approval gates of a pleading workflow, in order, with the one the run is at
 * and who acts there (plan 4B-40). States are words and a mark, not colour alone.
 */
const WorkflowGateStepper: React.FC<Props> = ({ workflow }) => {
  const steps = gateSteps(workflow);
  const now = steps.find((step) => step.state === "current" || step.state === "working" || step.state === "stopped");
  return (
    <section aria-label="Approval gates" className="space-y-2">
      <p className="text-sm" role="status">
        {now
          ? now.state === "stopped"
            ? `Stopped at: ${now.label}.`
            : now.state === "working"
              ? `Next: ${now.label}. The system is preparing it.`
              : `Now: ${now.label}. Who acts: ${now.who}.`
          : "All eight gates are done."}
      </p>
      <ol className="space-y-1">
        {steps.map((step, index) => (
          <li
            key={step.gate}
            aria-current={step === now ? "step" : undefined}
            className={`flex items-center justify-between rounded border px-2 py-1 text-xs ${STATE_CLASS[step.state]}`}
          >
            <span>
              <span aria-hidden="true">{MARK[step.state]} </span>
              {index + 1}. {step.label}
            </span>
            <span>{STATE_TEXT[step.state]}</span>
          </li>
        ))}
      </ol>
    </section>
  );
};

export default WorkflowGateStepper;
