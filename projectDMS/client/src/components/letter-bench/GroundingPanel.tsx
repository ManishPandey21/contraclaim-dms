import React from "react";
import { Link } from "react-router-dom";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import type { GroundingStatement, GroundingStatus } from "@/types/letterBench";
import { BLOCKING_GROUNDING, unresolvedStatements } from "./benchLogic";

const STATUS: Record<GroundingStatus, { label: string; className: string }> = {
  unsupported: { label: "Unsupported", className: "border-red-700 bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-100" },
  contradicted: { label: "Contradicted", className: "border-red-700 bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-100" },
  partially_supported: {
    label: "Partly supported",
    className: "border-amber-600 bg-amber-50 text-amber-950 dark:bg-amber-950 dark:text-amber-100",
  },
  supported: { label: "Supported", className: "border-emerald-700 bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100" },
  user_confirmed: { label: "Confirmed by reviewer", className: "border-sky-700 bg-sky-50 text-sky-900 dark:bg-sky-950 dark:text-sky-100" },
  not_applicable: { label: "Not a factual claim", className: "border-border bg-muted text-foreground" },
};

const BLOCKING = BLOCKING_GROUNDING;

const StatementRow: React.FC<{ statement: GroundingStatement }> = ({ statement }) => {
  const tone = STATUS[statement.status];
  return (
    <li className="rounded border bg-card p-2.5 text-sm" data-testid="grounding-statement">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className={cn("rounded border px-1.5 py-0.5 text-[11px] font-medium", tone.className)}>{tone.label}</span>
        <span className="text-[11px] text-muted-foreground">{statement.classification.replace(/_/g, " ")}</span>
      </div>
      <p className="font-serif leading-6 text-foreground">{statement.text}</p>
      {statement.contradiction && (
        <p className="mt-1 text-xs text-red-800 dark:text-red-200">Source says otherwise: {statement.contradiction}</p>
      )}
      {statement.passage && (
        <blockquote className="mt-1.5 border-l-2 pl-2 text-xs text-muted-foreground">
          {statement.passage}
          <span className="mt-0.5 block not-italic">
            {statement.document_id ? (
              <Link to={`/documentviewer/${encodeURIComponent(statement.document_id)}`} className="underline underline-offset-2">
                {statement.source_label || "Open source document"}
              </Link>
            ) : (
              statement.source_label
            )}
            {statement.location ? ` · ${statement.location}` : ""}
          </span>
        </blockquote>
      )}
      {statement.confirmation_basis && (
        <p className="mt-1 text-xs text-sky-900 dark:text-sky-100">Basis recorded: {statement.confirmation_basis}</p>
      )}
      {BLOCKING.has(statement.status) && (
        <p className="mt-1 text-xs text-muted-foreground">
          Replace the wording in its paragraph, or edit the paragraph and record the basis on which you confirm it,
          then compose again.
        </p>
      )}
    </li>
  );
};

/**
 * What backs each factual statement in the composed reply. Unsupported or
 * contradicted statements, and unresolved [CONFIRM: ...] placeholders, hold
 * approval of the draft until they are resolved and the reply is composed again.
 */
export const GroundingPanel: React.FC<{ grounding?: GroundingStatement[] }> = ({ grounding }) => {
  const statements = (grounding ?? []).filter((s) => s.material || s.status !== "not_applicable");
  if (!grounding || !grounding.length) return null;
  const open = unresolvedStatements(grounding);
  return (
    <section aria-label="Factual grounding" className="mx-4 mb-4 rounded-md border p-3">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        {open.length ? (
          <ShieldAlert className="h-4 w-4 text-red-700" aria-hidden="true" />
        ) : (
          <ShieldCheck className="h-4 w-4 text-emerald-700" aria-hidden="true" />
        )}
        Factual grounding
      </h3>
      <p className="mt-0.5 text-xs text-muted-foreground">
        {open.length
          ? `${open.length} statement(s) are not backed by the sources. The draft cannot be approved until they are resolved.`
          : "Every factual statement is backed by a source, a user input or a recorded confirmation."}
      </p>
      {statements.length > 0 && (
        <ul className="mt-2 space-y-2">
          {[...statements]
            .sort((a, b) => Number(BLOCKING.has(b.status)) - Number(BLOCKING.has(a.status)))
            .map((statement, index) => (
              <StatementRow key={`${index}-${statement.text.slice(0, 24)}`} statement={statement} />
            ))}
        </ul>
      )}
    </section>
  );
};

export default GroundingPanel;
