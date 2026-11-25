import React from "react";

import { deadlineState } from "@/lib/claim-findings-logic";
import { formatDateIN, formatINR } from "@/lib/format-in";
import type { BoardTrigger } from "@/types/letterBench";

const RESULT_WORDS: Record<string, string> = {
  full: "Made out in full",
  partial: "Made out in part",
  none: "Not made out",
  insufficient_information: "Cannot yet be determined",
};

/**
 * For a letter started from a determination (4B-31): the findings and the
 * information asked for, with each item's deadline, as the board carries them.
 * Renders nothing for any other letter.
 */
export const DeterminationContext: React.FC<{ trigger?: BoardTrigger | null; today?: string }> = ({
  trigger,
  today = new Date().toISOString().slice(0, 10),
}) => {
  if (!trigger || (trigger.type !== "determination" && trigger.type !== "information_request")) return null;
  const items = trigger.information_items ?? [];
  const heads = trigger.heads ?? [];
  return (
    <section aria-label="From the determination" className="space-y-2 border-b bg-muted/40 px-4 py-3 text-sm">
      <p className="font-medium">
        {trigger.type === "determination" ? "Findings carried from the determination" : "Information requested"}
        <span className="ml-2 text-xs font-normal text-muted-foreground">{trigger.label}</span>
      </p>
      {heads.length > 0 && (
        <ul className="space-y-1">
          {heads.map((head) => (
            <li key={head.head_id}>
              <span className="font-medium">{head.head_title || head.head_id}:</span>{" "}
              {RESULT_WORDS[head.result ?? ""] ?? "No finding recorded"}
              {head.days != null && ` · ${head.days} days`}
              {head.amount != null && ` · ${formatINR(head.amount)}`}
            </li>
          ))}
        </ul>
      )}
      {items.length > 0 && (
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-muted-foreground">
              <th className="py-1 pr-2 font-medium">Item</th>
              <th className="py-1 pr-2 font-medium">Head</th>
              <th className="py-1 pr-2 font-medium">Deadline</th>
              <th className="py-1 font-medium">If not supplied</th>
            </tr>
          </thead>
          <tbody>
            {items.map((entry, index) => {
              const state = deadlineState(entry.deadline, today);
              return (
                <tr key={`${entry.head_id}-${index}`} className="border-t">
                  <td className="py-1 pr-2">{entry.item}</td>
                  <td className="py-1 pr-2">{entry.head_title || entry.head_id}</td>
                  <td className="py-1 pr-2">
                    {entry.deadline ? formatDateIN(entry.deadline) : "No date set"}
                    {state === "overdue" && <span className="ml-1 font-medium text-red-700">(passed)</span>}
                    {state === "due" && <span className="ml-1 font-medium text-amber-700">(within a week)</span>}
                  </td>
                  <td className="py-1">{entry.consequence || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <p className="text-xs text-muted-foreground">
        Taken from the determination when this letter was started. A deadline is changed on the determination, not here.
      </p>
    </section>
  );
};
